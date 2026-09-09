"""The LLM reading of a repository's harness, before the lab runs anything.

Step 2 of the guided audit used to be a checklist: rules file present or not,
N skills, N agents. True, and not what somebody deciding whether to spend agent
time on a repository wants to know. They want to be told, in a paragraph: this
repository ships a harness made of these parts, it is written in this language,
and here is what could be tried on it — or that there is nothing here to
evaluate, or that the lab cannot run it at all.

Two layers, kept apart on purpose:

- **Evidence** is deterministic. It is read out of the checkout at the pinned
  commit — the same blobless mirror readiness uses — and it carries the lab's
  own verdicts: which ecosystems the repository declares and whether the runner
  image can execute them, and whether any harness file exists at all. Those
  decide ``next_step``. A model never gets to open a door a fact has closed.
- **Findings** are the model's. It is handed the evidence and asked for a fixed
  JSON shape: a summary, the harness parts and what each is worth, the
  language, the standards, the gaps, and a short list of use cases. The report
  is rendered from that JSON, so a reader always gets the same sections in the
  same order, and an answer that did not parse is stored as what it is.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select

if TYPE_CHECKING:
    from collections.abc import Callable

from harness_lab_app.models import RepositoryAudit, TargetRepository, utc_now
from harness_lab_app.readiness_service import RULES_FILES, classify, detect_ecosystems
from harness_lab_app.runner_capabilities import (
    RunnerCapability,
    capabilities_as_dicts,
    clean_version,
    compare_runtime_version,
    declared_capabilities,
    declared_runtime_for_binary,
    runner_capabilities,
    runtime_for_command,
)


class RepositoryAuditError(ValueError):
    """The audit could not be produced as asked."""


# --------------------------------------------------------------------------
# Evidence — what the lab can state about the tree without a model
# --------------------------------------------------------------------------

# Caps on what the model is shown. The point is to describe the harness, not to
# hand over the repository: a rules file is read whole-ish, a skill by its
# opening (name, description, when to use), an agent by its head.
_RULES_CHARS = 4000
_MAX_RULES_FILES = 3
_SKILL_CHARS = 700
_MAX_SKILLS = 25
_AGENT_CHARS = 400
_MAX_AGENTS = 12
_README_CHARS = 2500
_MANIFEST_CHARS = 1500
_MAX_LISTED = 40

# Skill directories, in the order the lab's own tooling prefers them.
_SKILL_ROOTS = (".claude/skills/", ".github/skills/", ".agents/skills/")
_AGENT_ROOTS = (".claude/agents/", ".github/agents/")

# Where an agent harness declares hooks: Claude Code's settings, the scripts
# they usually call, and the ordinary git-side hooks a team may run instead.
_CLAUDE_SETTINGS = (".claude/settings.json",)
_CLAUDE_HOOK_DIR = ".claude/hooks/"
_PRE_COMMIT = ".pre-commit-config.yaml"
_HUSKY_DIR = ".husky/"

_MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_./-]*):(?!=)", re.MULTILINE)

# Every extension the census names, so the report can say "mostly .cbl" instead
# of "unknown". Order does not matter; the count does.
_KNOWN_EXTENSIONS = {
    "py": "Python",
    "js": "Node.js",
    "mjs": "Node.js",
    "cjs": "Node.js",
    "jsx": "Node.js",
    "ts": "Node.js",
    "tsx": "Node.js",
    "go": "Go",
    "rs": "Rust",
    "java": "Java",
    "kt": "Kotlin",
    "rb": "Ruby",
    "php": "PHP",
    "cs": "C#",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "cc": "C++",
    "swift": "Swift",
    "scala": "Scala",
    "cbl": "COBOL",
    "cob": "COBOL",
    "cpy": "COBOL",
    "pl": "Perl",
    "r": "R",
    "sh": "Shell",
    "ex": "Elixir",
    "exs": "Elixir",
    "dart": "Dart",
    "lua": "Lua",
    "sql": "SQL",
}

# Which runtime a language needs in order to be executed at all. This is the
# only place a language is tied to a capability: whether the runner *has* that
# runtime is the capability table's answer, not this module's. Adding Go is
# declaring it in `runner_capabilities.DECLARED_RUNTIMES`, not editing an
# "unsupported" list here — the list no longer exists.
_LANGUAGE_RUNTIME = {
    "Python": "python",
    "Node.js": "node",
    "Shell": "bash",
    "Go": "go",
    "Rust": "rust",
    "Java": "java",
    "Kotlin": "java",
    "Ruby": "ruby",
    "PHP": "php",
    "C#": "dotnet",
}


def supported_extensions(
    capabilities: dict[str, RunnerCapability] | None = None,
) -> dict[str, str]:
    """Extensions the runner can execute, for a tree with no project marker.

    Derived from what the runner offers rather than hardcoded, so a Shell
    collection stopped being unreadable the moment bash was declared — no line
    of this module names Shell as a special case.
    """
    table = capabilities if capabilities is not None else declared_capabilities()
    runnable = {name for name, capability in table.items() if capability.available}
    return {
        extension: language
        for extension, language in _KNOWN_EXTENSIONS.items()
        if _LANGUAGE_RUNTIME.get(language) in runnable
    }


_IGNORED_SEGMENTS = ("node_modules/", "vendor/", "dist/", "build/", ".venv/")

NEXT_STEP_CONTINUE = "continue"
NEXT_STEP_NO_HARNESS = "no_harness"
NEXT_STEP_UNSUPPORTED = "unsupported_runtime"


def language_census(paths: list[str]) -> list[dict[str, Any]]:
    """Source files by language, most common first.

    A marker file says what a repository *declares*; this says what it is made
    of. The two disagree often enough to be worth showing both — a Python
    ``pyproject.toml`` over a tree that is mostly COBOL copybooks is a wrapper,
    not a Python project.
    """
    counts: Counter[str] = Counter()
    for path in paths:
        if any(segment in path for segment in _IGNORED_SEGMENTS):
            continue
        _, _, extension = path.rpartition(".")
        if "/" in extension or not extension:
            continue
        language = _KNOWN_EXTENSIONS.get(extension.lower())
        if language:
            counts[language] += 1
    return [
        {"language": language, "files": count}
        for language, count in counts.most_common(8)
    ]


def runtime_verdict(
    paths: list[str], *, capabilities: dict[str, RunnerCapability] | None = None
) -> dict[str, Any]:
    """Can the runner image build and test this tree? Facts, no judgement.

    Marker files decide first, because they are what the setup commands act
    on. With no marker, the census decides against the runner's capability
    table: a tree that is mostly ``.py`` is a Python repository even without a
    ``pyproject.toml``, a tree that is mostly ``.sh`` is one the runner can
    execute because it ships bash, and a tree that is mostly ``.cbl`` is one it
    cannot, whatever the markers say.

    This answers a question about the *repository*, which is a coarser question
    than the one that matters: a repository whose dominant language the runner
    cannot execute may still propose cases it can. That is what
    :func:`plan_cases` decides, case by case, once cases exist.
    """
    table = capabilities if capabilities is not None else declared_capabilities()
    present = detect_ecosystems(paths)
    supported = sorted(name for name, ok in present.items() if ok)
    unsupported = sorted(name for name, ok in present.items() if not ok)
    census = language_census(paths)
    dominant = census[0]["language"] if census else ""
    by_files = False
    if not present and dominant in supported_extensions(table).values():
        supported = [dominant]
        by_files = True
    return {
        "ecosystems": sorted(present) or ([dominant] if dominant else []),
        "supported": supported,
        "unsupported": unsupported,
        "runner_can_execute": bool(supported),
        "detected_from_files_only": by_files,
        "dominant_language": dominant,
        "census": census,
        "capabilities": capabilities_as_dicts(table),
    }


def hooks_evidence(paths: list[str], read: Callable[[str], str]) -> dict[str, Any]:
    """Every place a hook could be declared, and what was found there."""
    path_set = set(paths)
    events: list[str] = []
    for name in _CLAUDE_SETTINGS:
        if name not in path_set:
            continue
        try:
            data = json.loads(read(name) or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        if isinstance(hooks, dict):
            events.extend(sorted(str(key) for key in hooks))
    scripts = sorted(
        path[len(_CLAUDE_HOOK_DIR) :]
        for path in paths
        if path.startswith(_CLAUDE_HOOK_DIR) and not path.endswith("/")
    )
    husky = sorted(
        path[len(_HUSKY_DIR) :]
        for path in paths
        if path.startswith(_HUSKY_DIR) and "/" not in path[len(_HUSKY_DIR) :]
    )
    return {
        "claude_settings_events": events,
        "claude_hook_scripts": scripts[:_MAX_LISTED],
        "pre_commit": _PRE_COMMIT in path_set,
        "husky": husky[:_MAX_LISTED],
    }


def _skill_paths(paths: list[str]) -> list[str]:
    found = [
        path
        for path in paths
        if path.endswith("/SKILL.md") and path.startswith(_SKILL_ROOTS)
    ]
    return sorted(found)[:_MAX_SKILLS]


def _agent_paths(paths: list[str]) -> list[str]:
    found = [
        path
        for path in paths
        if path.endswith(".md")
        and path.startswith(_AGENT_ROOTS)
        and path.count("/") == 2
    ]
    return sorted(found)[:_MAX_AGENTS]


def _capped(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…(truncated)"


def _makefile_targets(text: str) -> list[str]:
    seen: list[str] = []
    for match in _MAKE_TARGET.finditer(text or ""):
        name = match.group(1)
        if name.startswith(".") or name in seen:
            continue
        seen.append(name)
        if len(seen) >= _MAX_LISTED:
            break
    return seen


def _package_scripts(text: str) -> dict[str, str]:
    try:
        data = json.loads(text or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v)[:120] for k, v in list(scripts.items())[:_MAX_LISTED]}


def _first_readme(paths: list[str]) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        if name in paths:
            return name
    return ""


def harness_present(governance: dict[str, list[str]], hooks: dict[str, Any]) -> bool:
    """Does anything here address an agent at all?

    CI and pre-commit are engineering standards a team may have had for years;
    they say nothing about how an agent is expected to work here. A harness is
    the part written *for the agent*: a rules file, skills, agent definitions,
    slash commands, or hooks the agent runtime fires.
    """
    return bool(
        governance.get("rules_files")
        or governance.get("skills")
        or governance.get("agents")
        or governance.get("commands")
        or hooks.get("claude_settings_events")
        or hooks.get("claude_hook_scripts")
    )


def _runner_summary(runtime: dict[str, Any]) -> str:
    """What the runner offers, named, so a refusal says what it measured."""
    names = [
        str(item.get("runtime"))
        for item in runtime.get("capabilities") or []
        if item.get("available") and item.get("runtime") not in {"sh", "git"}
    ]
    return ", ".join(names) or "python, node, bash"


def decide_next_step(runtime: dict[str, Any], present: bool) -> tuple[str, str]:
    """Where the wizard goes after the report, and why — from facts only.

    This is the *repository-level* answer, and it is provisional in one
    direction only. It runs before the auditor proposes anything, so all it can
    say is whether the tree itself is made of something the runner executes.
    A repository that fails it may still propose cases that need no execution
    at all, or cases in a second language it does ship — which is why the
    refusal below says the audit ends *unless* such cases appear, and why
    :func:`apply_case_plan` is allowed to lift exactly this verdict once real
    cases exist. Nothing may push the verdict the other way.

    Then the harness: with nothing written for an agent there is no harness to
    evaluate, only a baseline to compare against.
    """
    if not runtime["runner_can_execute"]:
        named = ", ".join(runtime["unsupported"]) or runtime["dominant_language"]
        offered = _runner_summary(runtime)
        if named:
            return (
                NEXT_STEP_UNSUPPORTED,
                f"Detected {named}. The runner image ships {offered}, so no "
                "command written in this repository's own language can be "
                "executed until the image is extended. Unless the audit "
                "proposes cases that need no execution, the audit ends here.",
            )
        return (
            NEXT_STEP_UNSUPPORTED,
            "No project marker (pyproject.toml, package.json, …) and no source "
            "files the runner could execute were found, so the lab cannot run a "
            "case here. The audit ends here.",
        )
    if not present:
        return (
            NEXT_STEP_NO_HARNESS,
            "Nothing in this repository is written for an agent: no rules file "
            "(CLAUDE.md / AGENTS.md), no skills, no agent definitions, no "
            "commands, no hooks. There is no harness to evaluate — only a "
            "baseline the lab's own harness could be compared against.",
        )
    languages = "/".join(runtime["supported"])
    return (
        NEXT_STEP_CONTINUE,
        f"{languages} project the runner can execute, with a harness in place. "
        "Cases can be drafted, mined from its history or reused from the "
        "catalog, and run as the repository is or against the lab harness.",
    )


def collect_evidence(
    target: TargetRepository,
    commit: str,
    paths: list[str],
    read: Callable[[str], str],
    *,
    capabilities: dict[str, RunnerCapability] | None = None,
) -> dict[str, Any]:
    """Everything the auditor is shown, plus the lab's own verdicts.

    Pure over ``paths`` and ``read`` so the rules can be argued with in a test
    against a handful of strings rather than only observed against a clone.
    ``capabilities`` defaults to what the runner image *declares*, which needs
    no container and no docker socket, so the verdict a test computes is the
    verdict the worker computes.
    """
    governance = classify(paths)
    hooks = hooks_evidence(paths, read)
    runtime = runtime_verdict(paths, capabilities=capabilities)
    present = harness_present(governance, hooks)
    next_step, reason = decide_next_step(runtime, present)
    path_set = set(paths)

    rules: dict[str, str] = {}
    for name in RULES_FILES:
        if name in path_set and len(rules) < _MAX_RULES_FILES:
            rules[name] = _capped(read(name), _RULES_CHARS)
    skills = {path: _capped(read(path), _SKILL_CHARS) for path in _skill_paths(paths)}
    agents = {path: _capped(read(path), _AGENT_CHARS) for path in _agent_paths(paths)}

    manifests: dict[str, str] = {}
    for name in ("pyproject.toml", "package.json", "setup.cfg", "tox.ini"):
        if name in path_set:
            manifests[name] = _capped(read(name), _MANIFEST_CHARS)
    readme_name = _first_readme(paths)

    return {
        "target_id": target.target_id,
        "name": target.name,
        "repository_url": target.repository_url,
        "commit": commit,
        "tracked_files": len(paths),
        "runtime": runtime,
        "harness_present": present,
        "next_step": next_step,
        "next_step_reason": reason,
        "governance": governance,
        "hooks": hooks,
        "rules_files": rules,
        "skills": skills,
        "agents": agents,
        "commands": governance.get("commands", [])[:_MAX_LISTED],
        "ci_workflows": governance.get("ci_workflows", [])[:_MAX_LISTED],
        "adrs": governance.get("adrs", [])[-10:],
        "memory": governance.get("memory", [])[:_MAX_LISTED],
        "governance_docs": governance.get("governance_docs", [])[:_MAX_LISTED],
        "makefile_targets": _makefile_targets(read("Makefile"))
        if "Makefile" in path_set
        else [],
        "package_scripts": _package_scripts(read("package.json"))
        if "package.json" in path_set
        else {},
        "manifests": manifests,
        "readme": _capped(read(readme_name), _README_CHARS) if readme_name else "",
        "setup_commands": list(target.setup_commands or []),
        "validation_commands": list(target.validation_commands or []),
    }


def gather_evidence(target: TargetRepository) -> dict[str, Any]:
    """Read the pinned tree through the blobless mirror and build the evidence.

    The pin is required, not defaulted: an audit of a moving branch describes
    a tree nobody can get back to, and the wizard's own step order guarantees
    the pin exists before this step opens.
    """
    from harness_lab_app.case_derivation import sync_mirror
    from harness_lab_app.config import get_settings
    from harness_lab_app.readiness_service import list_paths, read_file
    from harness_lab_app.target_service import clone_source

    commit = (target.pinned_commit or "").strip()
    if not commit:
        raise RepositoryAuditError(
            "The repository is not pinned. Pin its checkout first — the audit "
            "reads one commit, not a branch."
        )
    mirror = sync_mirror(
        clone_source(target), get_settings().target_mirrors_dir, target.target_id
    )
    paths = list_paths(mirror, commit)
    # Worker-side: ask the image itself, so the report can name real versions.
    # A probe that cannot run falls back to the declared table, which is what
    # classification used anyway — the verdict never depends on the probe.
    return collect_evidence(
        target,
        commit,
        paths,
        lambda p: read_file(mirror, commit, p),
        capabilities=runner_capabilities(),
    )


# --------------------------------------------------------------------------
# The model's half — a fixed JSON shape, rendered by the lab
# --------------------------------------------------------------------------

COMPONENT_KINDS = (
    "rules_file",
    "skill",
    "agent",
    "command",
    "hook",
    "ci",
    "docs",
    "other",
)
HARNESS_VERDICTS = ("complete", "partial", "none")
_MAX_USE_CASES = 6

REPOSITORY_AUDIT_SYSTEM_PROMPT = (
    "Sos el auditor de un laboratorio que evalúa harnesses de agentes de código "
    "(reglas para el agente como CLAUDE.md/AGENTS.md, skills, subagentes, comandos, "
    "hooks, gobernanza). Te dan la evidencia de UN repositorio leída a un commit "
    "fijo: qué archivos existen, el contenido recortado de las reglas, el "
    "encabezado de cada skill y agente, los hooks declarados, la CI, el Makefile, "
    "los manifiestos de build y el README. Tu trabajo es describir, en español y "
    "sin rodeos, qué harness tiene este repositorio y qué se podría probar en él.\n\n"
    "Respondé ÚNICAMENTE con un objeto JSON (sin texto antes ni después, sin "
    "bloque de código) con exactamente estas claves:\n"
    '- "summary": 2 a 4 frases. Qué es el repositorio, en qué lenguaje, y si tiene '
    "un harness y de qué está compuesto. Directo: 'Este repositorio cuenta con…'.\n"
    '- "harness_verdict": "complete" | "partial" | "none". complete = reglas para el '
    "agente + skills o agentes + algún mecanismo de verificación (hooks, comandos "
    "de validación, CI) que el agente pueda usar; partial = algo de eso; none = nada "
    "escrito para un agente.\n"
    '- "harness_components": lista de objetos {"kind", "name", "assessment"}. kind '
    'es uno de "rules_file", "skill", "agent", "command", "hook", "ci", "docs", '
    '"other". name es el archivo o nombre tal cual aparece en la evidencia. '
    "assessment es UNA frase sobre qué aporta o qué le falta. Agrupá skills o "
    "agentes muy parecidos en una entrada si son más de diez.\n"
    '- "language_notes": 1 a 2 frases sobre el lenguaje, el toolchain y cómo se '
    "testea (según Makefile, scripts, manifiestos).\n"
    '- "standards": lista corta de strings — convenciones y estándares de uso que '
    "las reglas imponen (formato, tests obligatorios, flujo de trabajo, idioma, "
    "arquitectura). Vacía si no hay.\n"
    '- "gaps": lista corta de strings — qué le falta al harness para ser evaluable '
    "o efectivo (sin comando de validación, skills sin descripción, reglas sin "
    "criterio de done, etc.). Vacía si no ves nada.\n"
    '- "use_cases": lista de 3 a 5 objetos {"title", "why", "validation_hint", '
    '"required_runtime_version"}: tareas concretas y acotadas que un agente podría '
    "hacer EN ESTE repositorio y que servirían para medir si el harness ayuda. "
    "title corto; why una frase; validation_hint el comando o test que diría si "
    "salió bien — si lo que decide el caso es una revisión y no un comando "
    "(que exista un AGENTS.md, que un kit tenga README, que se respete una regla), "
    "escribí esa comprobación en prosa y no inventes un comando. "
    "required_runtime_version solo si el repositorio declara una versión concreta "
    'del runtime (por ejemplo "3.2" para bash); si no la declara, "". Si el runtime '
    "no es ejecutable por el laboratorio, igual proponé casos pero decilo en why.\n\n"
    "Reglas: usá SOLO la evidencia dada — no inventes archivos, skills ni comandos "
    "que no aparezcan; nombrá las cosas exactamente como aparecen; sé breve, nada "
    "de párrafos largos; no incluyas secretos, tokens ni rutas absolutas."
)


def render_audit_prompt(evidence: dict[str, Any]) -> str:
    """The user message: the evidence as JSON the model can quote from."""
    return (
        "Evidencia del repositorio (JSON). Todo lo que afirmes tiene que salir "
        "de acá.\n\n```json\n"
        + json.dumps(evidence, indent=1, ensure_ascii=False, default=str)
        + "\n```"
    )


def _strings(raw: object, limit: int = 12) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()][:limit]


def _components(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "other").strip()
        rows.append(
            {
                "kind": kind if kind in COMPONENT_KINDS else "other",
                "name": str(item.get("name") or "").strip()[:200],
                "assessment": str(item.get("assessment") or "").strip()[:400],
            }
        )
    return [row for row in rows if row["name"]][:_MAX_LISTED]


def _use_cases(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:160]
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "why": str(item.get("why") or "").strip()[:400],
                "validation_hint": str(item.get("validation_hint") or "").strip()[:300],
                # Clamped to a shape, not trusted: a model-supplied version can
                # only ever degrade a verdict (exact to partial), never promote
                # a blocked case to a runnable one.
                "required_runtime_version": clean_version(
                    item.get("required_runtime_version")
                ),
            }
        )
    return rows[:_MAX_USE_CASES]


def _extract_json(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]
    return cleaned.strip()


def parse_findings(raw: str) -> dict[str, Any]:
    """The model's answer, coerced to the shape the report renders.

    An answer that is not JSON is not thrown away and not guessed at: the
    findings come back empty with a ``parse_error`` and the raw text is kept
    for the markdown, so a reader sees what the model actually said.
    """
    try:
        parsed = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        return {"parse_error": f"the auditor did not answer with JSON: {exc}"}
    if not isinstance(parsed, dict):
        return {"parse_error": "the auditor did not answer with a JSON object"}
    verdict = str(parsed.get("harness_verdict") or "").strip().lower()
    return {
        "summary": str(parsed.get("summary") or "").strip()[:2000],
        "harness_verdict": verdict if verdict in HARNESS_VERDICTS else "unknown",
        "harness_components": _components(parsed.get("harness_components")),
        "language_notes": str(parsed.get("language_notes") or "").strip()[:800],
        "standards": _strings(parsed.get("standards")),
        "gaps": _strings(parsed.get("gaps")),
        "use_cases": _use_cases(parsed.get("use_cases")),
    }


# --------------------------------------------------------------------------
# Capability matching — one case at a time, against what the runner has
# --------------------------------------------------------------------------

CASE_RUNNABLE = "runnable"
CASE_BLOCKED = "blocked"
CASE_STATIC = "static"

COVERAGE_NONE = "none"
COVERAGE_PARTIAL = "partial"
COVERAGE_FULL = "full"


def _is_available(capabilities: dict[str, RunnerCapability], runtime: str) -> bool:
    """Whether the runner offers this runtime. Absent and present-but-off are
    the same answer to the only question the classifier asks."""
    capability = capabilities.get(runtime)
    return capability is not None and capability.available


def classify_case(
    use_case: dict[str, Any], capabilities: dict[str, RunnerCapability]
) -> dict[str, Any]:
    """One proposed case, matched against the runner's capabilities.

    Three outcomes, and the order they are decided in is the whole point:

    - **blocked** — the hint names a runtime the runner does not have. Decided
      first, and from the leading binary alone, because a command like
      ``docker build .`` does not look executable to the run-time heuristic:
      calling it static would file it as "settled by reading", which promises a
      check nothing will ever run. Blocked is a statement about this case, not
      about the repository — its siblings are classified on their own terms and
      still run.
    - **static** — the case is settled by reading, not by running. It has no
      executable command at all, so no runtime can block it. This is why a
      repository written in Shell can still be evaluated on "does the right
      AGENTS.md apply here": the dominant language of the tree is irrelevant to
      a case that never executes anything.
    - **runnable** — the command names a runtime the runner has.

    Whether a hint is executable is decided by ``validation.is_executable_command``
    — the same function that decides, at run time, whether a validation entry is
    a command or a prose note. Using a second, kinder heuristic here would let
    the audit promise a check the runner then silently skips.
    """
    from harness_lab_app.validation import is_executable_command

    hint = str(use_case.get("validation_hint") or "").strip()
    required_version = clean_version(use_case.get("required_runtime_version"))
    row = {
        "title": use_case.get("title", ""),
        "why": use_case.get("why", ""),
        "validation_hint": hint,
        "runtime": "",
        "runtime_version": "",
        "required_runtime_version": required_version,
        "compatibility": "unknown",
        "status": CASE_STATIC,
        "reason": "",
    }

    def _blocked(runtime: str) -> dict[str, Any]:
        row["runtime"] = runtime
        row["status"] = CASE_BLOCKED
        row["reason"] = (
            f"Necesita `{runtime}`, que el runner no tiene. Los demás casos de "
            "este repositorio se evalúan igual."
        )
        return row

    named = declared_runtime_for_binary(hint)
    if named and not _is_available(capabilities, named):
        return _blocked(named)

    if not is_executable_command(hint):
        row["reason"] = (
            "Se resuelve leyendo el repositorio, no ejecutando nada: no depende "
            "de ningún runtime del runner."
        )
        return row

    runtime = runtime_for_command(hint)
    row["runtime"] = runtime
    if not _is_available(capabilities, runtime):
        return _blocked(runtime)
    capability = capabilities[runtime]

    row["status"] = CASE_RUNNABLE
    row["runtime_version"] = capability.version
    row["compatibility"] = compare_runtime_version(required_version, capability.version)
    if not required_version:
        row["reason"] = f"El runner ejecuta `{runtime}`."
    elif row["compatibility"] == "exact":
        row["reason"] = (
            f"El runner ejecuta `{runtime} {capability.version}`, que es la "
            f"versión que el repositorio declara ({required_version})."
        )
    elif row["compatibility"] == "partial":
        # The one thing this must never say is "certified".
        row["reason"] = (
            f"Ejecutable sí, pero el repositorio apunta a `{runtime} "
            f"{required_version}` y el runner tiene `{capability.version}`. "
            "El caso corre; la compatibilidad con el runtime destino es parcial."
        )
    else:
        row["reason"] = (
            f"El runner ejecuta `{runtime}`, pero no pudo medir su versión, así "
            f"que la compatibilidad con `{runtime} {required_version}` queda sin "
            "determinar."
        )
    return row


def execution_coverage(rows: list[dict[str, Any]]) -> str:
    """How much of what was proposed the lab can actually settle.

    ``none`` when nothing can be settled at all — neither by running nor by
    reading. ``full`` when nothing is blocked. ``partial`` otherwise, which is
    the honest answer for a repository whose cases the lab can only half cover.
    """
    runnable = sum(row["status"] == CASE_RUNNABLE for row in rows)
    static = sum(row["status"] == CASE_STATIC for row in rows)
    blocked = sum(row["status"] == CASE_BLOCKED for row in rows)
    if runnable + static == 0:
        return COVERAGE_NONE
    return COVERAGE_FULL if blocked == 0 else COVERAGE_PARTIAL


def plan_cases(
    use_cases: list[dict[str, Any]],
    capabilities: dict[str, RunnerCapability] | None = None,
) -> dict[str, Any]:
    """Classify every proposed case and total up what the lab can cover."""
    table = capabilities if capabilities is not None else declared_capabilities()
    rows = [classify_case(use_case, table) for use_case in use_cases or []]
    return {
        "cases": rows,
        "runnable": sum(row["status"] == CASE_RUNNABLE for row in rows),
        "blocked": sum(row["status"] == CASE_BLOCKED for row in rows),
        "static": sum(row["status"] == CASE_STATIC for row in rows),
        "coverage": execution_coverage(rows),
    }


def apply_case_plan(evidence: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Fold the per-case plan back into the evidence, in one direction only.

    The repository-level gate runs before any case exists, so it can be wrong
    in exactly one way: it can stop a repository whose cases the lab could in
    fact have evaluated. This corrects that, and only that. A repository that
    already reached ``continue`` is never demoted here, and neither is one with
    no harness — a model that proposes five beautiful cases for a repository
    containing nothing written for an agent has not created a harness to
    measure. Facts close doors; the plan may only reopen the one door the facts
    closed for a reason that turned out not to apply (ADR-0035).
    """
    updated = {**evidence, "case_plan": plan}
    if evidence.get("next_step") != NEXT_STEP_UNSUPPORTED:
        return updated
    if plan["runnable"] + plan["static"] == 0:
        return updated
    parts = []
    if plan["runnable"]:
        parts.append(f"{plan['runnable']} ejecutable(s) por el runner")
    if plan["static"]:
        parts.append(f"{plan['static']} que se resuelven sin ejecutar nada")
    reason = (
        f"El lenguaje dominante del repositorio no es ejecutable por el runner, "
        f"pero de los casos propuestos hay {' y '.join(parts)}. "
        "El lenguaje dominante no decide si el repositorio se puede evaluar: "
        "cada caso se evalúa contra las capacidades del runner por separado."
    )
    if plan["blocked"]:
        reason += f" Quedan {plan['blocked']} bloqueado(s), que no corren."
    updated["next_step"] = NEXT_STEP_CONTINUE
    updated["next_step_reason"] = reason
    return updated


_VERDICT_LABEL = {
    "complete": "completo",
    "partial": "parcial",
    "none": "sin harness",
    "unknown": "sin veredicto",
}
_KIND_LABEL = {
    "rules_file": "reglas",
    "skill": "skill",
    "agent": "agente",
    "command": "comando",
    "hook": "hook",
    "ci": "CI",
    "docs": "docs",
    "other": "otro",
}
_CASE_STATUS_LABEL = {
    CASE_RUNNABLE: "Ejecutable por el runner",
    CASE_BLOCKED: "Bloqueado",
    CASE_STATIC: "Estático (sin ejecución)",
}
_COVERAGE_LABEL = {
    COVERAGE_NONE: "ninguna",
    COVERAGE_PARTIAL: "parcial",
    COVERAGE_FULL: "total",
}
_NEXT_STEP_LABEL = {
    NEXT_STEP_CONTINUE: "Se puede continuar",
    NEXT_STEP_NO_HARNESS: "No hay harness que evaluar",
    NEXT_STEP_UNSUPPORTED: "El laboratorio no puede ejecutarlo",
}


def render_markdown(
    findings: dict[str, Any], evidence: dict[str, Any], *, model: str, raw: str = ""
) -> str:
    """The report a person reads, always in the same order.

    Rendered by the lab rather than written free-form by the model so two
    audits a month apart line up section by section, and so the lab's own
    verdict — runtime and next step — is printed from the evidence, not from
    whatever the model felt like saying about it.
    """
    runtime = evidence.get("runtime") or {}
    ecosystems = ", ".join(runtime.get("ecosystems") or []) or "no detectado"
    lines = [
        f"# Auditoría de harness — {evidence.get('name') or evidence.get('target_id')}",
        "",
        f"`{evidence.get('repository_url', '')}` en `{str(evidence.get('commit', ''))[:12]}`"
        f" · modelo `{model}` · {datetime.now(UTC).date().isoformat()}",
        "",
    ]
    if findings.get("parse_error"):
        lines += [
            "## Resumen",
            "",
            f"_El auditor no devolvió la estructura pedida ({findings['parse_error']})._ "
            "Lo que dijo, tal cual:",
            "",
            raw.strip() or "(vacío)",
            "",
        ]
    else:
        lines += ["## Resumen", "", findings.get("summary") or "(sin resumen)", ""]
        verdict = _VERDICT_LABEL.get(findings.get("harness_verdict", "unknown"), "?")
        lines += [f"## Harness detectado — {verdict}", ""]
        components = findings.get("harness_components") or []
        if components:
            for row in components:
                kind = _KIND_LABEL.get(row["kind"], row["kind"])
                lines.append(f"- **{kind}** `{row['name']}` — {row['assessment']}")
        else:
            lines.append("No se detectó ningún componente escrito para un agente.")
        lines.append("")
        lines += ["## Lenguaje y runtime", ""]
        if findings.get("language_notes"):
            lines += [findings["language_notes"], ""]
        standards = findings.get("standards") or []
        if standards:
            lines += ["## Estándares de uso", ""]
            lines += [f"- {item}" for item in standards]
            lines.append("")
        gaps = findings.get("gaps") or []
        if gaps:
            lines += ["## Brechas", ""]
            lines += [f"- {item}" for item in gaps]
            lines.append("")
        use_cases = findings.get("use_cases") or []
        if use_cases:
            planned = (evidence.get("case_plan") or {}).get("cases") or []
            lines += ["## Casos de uso posibles", ""]
            for index, row in enumerate(use_cases, start=1):
                hint = (
                    f" _Validación:_ `{row['validation_hint']}`"
                    if row["validation_hint"]
                    else ""
                )
                lines.append(f"{index}. **{row['title']}** — {row['why']}{hint}")
                # The lab's verdict on this one case, printed under it: the
                # model proposed it, a deterministic match decided whether the
                # runner can settle it.
                case = planned[index - 1] if index - 1 < len(planned) else None
                if case:
                    label = _CASE_STATUS_LABEL.get(case["status"], case["status"])
                    lines.append(f"   - _{label}._ {case['reason']}")
            lines.append("")
    # The lab's own line, printed last and from the evidence: the model above
    # described the repository, this decides what the wizard does next.
    execute = "sí" if runtime.get("runner_can_execute") else "no"
    lines += [
        "## Veredicto del laboratorio",
        "",
        f"- Ecosistemas detectados: {ecosystems}. El runner puede ejecutarlo: **{execute}**.",
        f"- Capacidades del runner: {_capabilities_line(runtime)}.",
        f"- Harness presente: **{'sí' if evidence.get('harness_present') else 'no'}**.",
    ]
    plan = evidence.get("case_plan") or {}
    if plan.get("cases"):
        coverage = _COVERAGE_LABEL.get(plan.get("coverage", ""), "?")
        lines.append(
            f"- Cobertura de ejecución: **{coverage}** — "
            f"{plan['runnable']} ejecutable(s), {plan['static']} "
            f"estático(s), {plan['blocked']} bloqueado(s)."
        )
    lines += [
        f"- **{_NEXT_STEP_LABEL.get(evidence.get('next_step', ''), '?')}.** "
        f"{evidence.get('next_step_reason', '')}",
        "",
    ]
    return "\n".join(lines)


def _capabilities_line(runtime: dict[str, Any]) -> str:
    """The runner's runtimes as the report shows them, versions when measured."""
    parts = []
    for item in runtime.get("capabilities") or []:
        if not item.get("available") or item.get("runtime") in {"sh", "git"}:
            continue
        version = str(item.get("version") or "")
        parts.append(f"`{item['runtime']}{' ' + version if version else ''}` ✓")
    return " ".join(parts) or "no determinadas"


# --------------------------------------------------------------------------
# Persistence — one row per target, replaced on re-audit
# --------------------------------------------------------------------------


class RepositoryAuditService:
    def __init__(self, session: Session):
        self.session = session

    def get(self, target_id: str) -> RepositoryAudit | None:
        return self.session.exec(
            select(RepositoryAudit).where(RepositoryAudit.target_id == target_id)
        ).first()

    def _upsert(self, target_id: str) -> RepositoryAudit:
        audit = self.get(target_id)
        if audit is None:
            audit = RepositoryAudit(target_id=target_id)
            self.session.add(audit)
        return audit

    def mark_running(
        self, target_id: str, auditor_model: str = "", commit: str = ""
    ) -> RepositoryAudit:
        audit = self._upsert(target_id)
        audit.status = "running"
        audit.auditor_model = auditor_model
        audit.commit = commit or audit.commit
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_result(
        self,
        target_id: str,
        *,
        markdown: str,
        findings: dict[str, Any],
        evidence: dict[str, Any],
        model: str,
    ) -> RepositoryAudit:
        audit = self._upsert(target_id)
        audit.status = "done"
        audit.markdown = markdown
        audit.findings_json = findings
        audit.evidence_json = evidence
        audit.commit = str(evidence.get("commit") or audit.commit)
        audit.auditor_model = model
        audit.error = None
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit

    def store_error(self, target_id: str, error: str) -> RepositoryAudit:
        audit = self._upsert(target_id)
        audit.status = "failed"
        audit.error = error[:2000]
        audit.updated_at = utc_now()
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(audit)
        return audit
