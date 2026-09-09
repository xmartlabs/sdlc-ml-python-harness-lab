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

# File extensions the runner image can execute, for a repository that has no
# project marker at its root (a script collection, a monorepo subfolder).
_SUPPORTED_EXTENSIONS = {
    "py": "Python",
    "js": "Node.js",
    "mjs": "Node.js",
    "cjs": "Node.js",
    "jsx": "Node.js",
    "ts": "Node.js",
    "tsx": "Node.js",
}
# Everything else the census names so the report can say "mostly .cbl" instead
# of "unknown". Order does not matter; the count does.
_KNOWN_EXTENSIONS = {
    **_SUPPORTED_EXTENSIONS,
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


def runtime_verdict(paths: list[str]) -> dict[str, Any]:
    """Can the runner image build and test this tree? Facts, no judgement.

    Marker files decide first, because they are what the setup commands act
    on. With no marker, the census decides: a tree that is mostly ``.py`` is a
    Python repository even without a ``pyproject.toml``, and a tree that is
    mostly ``.cbl`` is one the runner cannot execute whatever the markers say.
    """
    present = detect_ecosystems(paths)
    supported = sorted(name for name, ok in present.items() if ok)
    unsupported = sorted(name for name, ok in present.items() if not ok)
    census = language_census(paths)
    dominant = census[0]["language"] if census else ""
    by_files = False
    if not present and dominant in _SUPPORTED_EXTENSIONS.values():
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


def decide_next_step(runtime: dict[str, Any], present: bool) -> tuple[str, str]:
    """Where the wizard goes after the report, and why — from facts only.

    Runtime is checked first: a repository the runner cannot build stops here
    however good its harness is, because nothing the lab measures afterwards
    would be about the agent. Then the harness: with nothing written for an
    agent there is no harness to evaluate, only a baseline to compare against.
    """
    if not runtime["runner_can_execute"]:
        named = ", ".join(runtime["unsupported"]) or runtime["dominant_language"]
        if named:
            return (
                NEXT_STEP_UNSUPPORTED,
                f"Detected {named}. The runner image ships only Python and Node "
                "toolchains, so no case can be executed against this repository "
                "until the runner image is extended. The audit ends here.",
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
) -> dict[str, Any]:
    """Everything the auditor is shown, plus the lab's own verdicts.

    Pure over ``paths`` and ``read`` so the rules can be argued with in a test
    against a handful of strings rather than only observed against a clone.
    """
    governance = classify(paths)
    hooks = hooks_evidence(paths, read)
    runtime = runtime_verdict(paths)
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
    return collect_evidence(
        target, commit, paths, lambda p: read_file(mirror, commit, p)
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
    '- "use_cases": lista de 3 a 5 objetos {"title", "why", "validation_hint"}: '
    "tareas concretas y acotadas que un agente podría hacer EN ESTE repositorio y "
    "que servirían para medir si el harness ayuda. title corto; why una frase; "
    "validation_hint el comando o test que diría si salió bien. Si el runtime no es "
    "ejecutable por el laboratorio, igual proponé casos pero decilo en why.\n\n"
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
            lines += ["## Casos de uso posibles", ""]
            for index, row in enumerate(use_cases, start=1):
                hint = (
                    f" _Validación:_ `{row['validation_hint']}`"
                    if row["validation_hint"]
                    else ""
                )
                lines.append(f"{index}. **{row['title']}** — {row['why']}{hint}")
            lines.append("")
    # The lab's own line, printed last and from the evidence: the model above
    # described the repository, this decides what the wizard does next.
    execute = "sí" if runtime.get("runner_can_execute") else "no"
    lines += [
        "## Veredicto del laboratorio",
        "",
        f"- Ecosistemas detectados: {ecosystems}. El runner puede ejecutarlo: **{execute}**.",
        f"- Harness presente: **{'sí' if evidence.get('harness_present') else 'no'}**.",
        f"- **{_NEXT_STEP_LABEL.get(evidence.get('next_step', ''), '?')}.** "
        f"{evidence.get('next_step_reason', '')}",
        "",
    ]
    return "\n".join(lines)


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
