"""AI-assisted draft generation for validation cases.

Translates a user's natural language scenario description and active skill context
into a structured validation case (subject, instruction, validation commands, max_steps).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from sqlmodel import Session

from harness_lab_app import llm_transport
from harness_lab_app.config import read_env_file_value
from harness_lab_app.models import CaseType
from harness_lab_app.schemas import (
    CaseDraftRequest,
    CaseDraftResponse,
    DroppedCommand,
)
from harness_lab_app.validation import is_executable_command

# Drafting a case is a bounded generation, not a judgement over long logs, so
# it rides the summary tier of whichever transport is active (ADR-0043):
# gpt-5.4-nano on a gateway, claude-haiku-4-5 on the Claude CLI.
DRAFT_TIMEOUT_SECONDS = 60

#: Case types a person can author. The two SWE-bench types are imported from a
#: dataset with their own test lists, so the assistant must never mint one.
_AUTHORABLE_CASE_TYPES = frozenset(
    {
        CaseType.single_prompt,
        CaseType.multi_step,
        CaseType.build_from_scratch,
        CaseType.bugfix,
        CaseType.repo_task,
    }
)

# One corrective round-trip when the first answer leaves nothing runnable. A
# second retry rarely changes the outcome and doubles the wait.
MAX_ATTEMPTS = 2

REASON_MULTILINE = (
    "spans several lines; the case stores one shell command per line, so a "
    "heredoc or script body would be split and each line run on its own"
)
REASON_NOT_RUNNABLE = (
    "does not look like a runnable command: it must start with a known binary "
    "(python3, node, pytest, sh, make, npm…) or reference {workspace}"
)

_SYSTEM_PROMPT = (
    "You are an expert test designer for autonomous AI coding agents in a benchmark harness. "
    "Your job is to convert a user test scenario description into a concrete, rigorous, executable validation case.\n\n"
    "Guidelines:\n"
    "- NEVER generate trivial toy tasks (like 'sum two numbers', 'hello world', or printing text). Always design realistic, self-contained software engineering problems (e.g., building a data structure, parsing a format, writing a service utility, or implementing an API client with edge cases).\n"
    "- For meta/style/token-efficiency skills (like 'caveman', 'be-terse', TDD, etc.), design a substantial software task with automated unit tests. The goal is to measure whether the agent can successfully deliver working code and tests while operating under the skill's style or token constraints.\n"
    "- Validation commands MUST verify actual functional correctness and exit non-zero on failure. Prefer running test suites (e.g. `pytest {workspace}/test_*.py` or running a python script with assertions) rather than mere file existence checks like `test -s`.\n"
    "- The agent instruction should clearly describe expected inputs, outputs, file paths in `{workspace}`, and requirements.\n\n"
    "Hard rules for validation_commands (commands that break them are discarded):\n"
    "- Each command is exactly ONE line. It is executed verbatim with `sh -c`. No heredocs (`<<`), no multi-line scripts, no line breaks inside a command.\n"
    '- For a check with several statements, inline it: `python3 -c "import sys; ...; sys.exit(0 if ok else 1)"` or `node -e "const m=require(\'{workspace}/x.js\'); if(!m.f) process.exit(1)"`. Separate statements with `;`.\n'
    "- Every command must start with one of: python3, python, pytest, node, npm, npx, sh, bash, make, uv, go, cargo, timeout — or reference {workspace}.\n"
    "- Every command must reference {workspace} (the agent's working directory) when it touches files.\n"
    "- If the agent must produce a test file, have the instruction ask for it and run it (`pytest {workspace}/test_x.py -q`, `node {workspace}/x.test.js`) instead of writing the test inline.\n"
    "- Prefer 2 to 6 focused commands over one giant one; each must be able to fail.\n\n"
    "Return a strict JSON object with these exact keys:\n"
    '- "subject": concise string naming the tool/feature under test (e.g. "caveman: token-efficient Python module implementation")\n'
    '- "instruction": clear, precise, comprehensive prompt the agent will receive in its workspace to perform the task\n'
    '- "validation_commands": array of single-line shell commands that run after the agent stops and exit non-zero if the agent failed (e.g. ["pytest {workspace}/test_module.py -q", "python3 -c "import json,sys; d=json.load(open(\'{workspace}/out.json\')); sys.exit(0 if d.get(\'ok\') else 1)""])\n'
    '- "max_steps": integer step budget (5, 10, 15, 20, or 25, default 15)\n'
    '- "case_id": lowercase kebab-case identifier for this case, 3 to 40 characters, no spaces (e.g. "json-config-merger")\n'
    '- "description": one or two sentences saying what the case measures, for a person browsing the catalog. Not the instruction.\n'
    '- "case_type": one of "single_prompt" (one instruction, the agent works until done), "multi_step" (the work is driven turn by turn from a written plan), "build_from_scratch" (an empty workspace, the agent creates the project), "bugfix" (existing code is broken and must be repaired), "repo_task" (a change inside an existing repository)\n'
    '- "step_prompts": ONLY for "multi_step" — the prompt for step 2 onward, one string per step, in order. "instruction" is step 1, so do not repeat it here. Use [] for every other case type.\n\n'
    "Sizing rules that tie those together:\n"
    "- Choose the case type from the work, not from the wording of the request: an empty workspace is build_from_scratch, repairing existing behaviour is bugfix, and a task inside a repository you were shown is repo_task.\n"
    "- A multi_step case must have at least one entry in step_prompts, each a self-contained instruction for that turn, and max_steps must be at least 1 + len(step_prompts).\n"
    "- Every other case type must leave step_prompts empty; its budget is how "
    "many turns the agent may take, not a plan.\n\n"
    "When a 'Repository context' section is present:\n"
    "- The case runs INSIDE that repository: {workspace} is a clone of it at a fixed "
    "commit, not an empty directory. Never ask the agent to create the project from "
    "scratch, and name real paths from the layout you were shown.\n"
    "- Design a task that repository plausibly gets asked for — a small feature, a bug "
    "fix, a missing test — sized for the step budget.\n"
    "- Prefer the repository's own validation commands verbatim: they are what its team "
    "trusts, and a gate you invent measures your guess instead of their code. Add a "
    "focused command only for the behaviour the task introduces.\n\n"
    "Only return valid JSON without markdown code fences."
)


def _find_skill_content(name: str) -> str | None:
    """Attempt to find SKILL.md for a given skill in staging or governed paths."""
    candidates = [
        Path("data/skills_staging") / name / "SKILL.md",
        Path(".agents/skills") / name / "SKILL.md",
        Path(".github/skills") / name / "SKILL.md",
        Path(".github/skills-external") / name / "SKILL.md",
    ]
    for path in candidates:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                return content[:2500]
            except OSError:
                continue
    return None


def _load_skill_context(selected_skills: list[str]) -> str:
    if not selected_skills:
        return ""
    sections: list[str] = []
    for skill_name in selected_skills:
        body = _find_skill_content(skill_name)
        if body:
            sections.append(f"### Skill: {skill_name}\n{body}")
        else:
            sections.append(f"### Skill: {skill_name}\n(No SKILL.md found on disk)")
    return "\n\nAvailable Skills Context:\n" + "\n\n".join(sections)


def _call_model(model: str, system: str, user: str) -> str:
    """One draft completion over the lab's LLM transport.

    Gateway first, Claude CLI second (ADR-0043). A missing transport is a
    configuration error the wizard can act on, so it surfaces as ``ValueError``
    like every other refusal in this module — never as a silent empty draft.
    """
    try:
        return llm_transport.chat(
            model, system, user, timeout=DRAFT_TIMEOUT_SECONDS, temperature=0.2
        ).strip()
    except llm_transport.TransportNotConfigured as exc:
        raise ValueError(
            "No LLM transport is configured for the case assistant: set "
            "GATEWAY_BASE_URL + GATEWAY_TOKEN, or CLAUDE_CODE_OAUTH_TOKEN / "
            "ANTHROPIC_API_KEY (Health → LLM transport shows which is active)."
        ) from exc
    except llm_transport.TransportError as exc:
        raise ValueError(f"Case draft request failed: {exc}") from exc


def default_draft_model() -> str:
    """The model the assistant uses when the request names none."""
    return (
        os.environ.get("CASE_DRAFT_MODEL")
        or read_env_file_value("CASE_DRAFT_MODEL")
        or os.environ.get("GATEWAY_DEFAULT_MODEL")
        or llm_transport.default_summary_model()
    )


def _sanitize_json(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _raw_commands(parsed: dict) -> list[str]:
    raw = parsed.get("validation_commands")
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str) and raw.strip():
        # A single string is one command, not a script: keep its lines together
        # so a pasted heredoc is screened as the multi-line entry it is.
        return [raw.strip()]
    return []


def screen_commands(raw: list[str]) -> tuple[list[str], list[DroppedCommand]]:
    """Split the model's commands into what the lab would run and what it would drop.

    Mirrors the two ways a command dies downstream. The case stores one shell
    command per line and the wizard's textarea is split on newlines, so a
    multi-line entry (a heredoc, a script body) does not survive as a unit —
    its first line runs alone (`node - <<'EOF'` with an empty stdin exits 0,
    a pass that measures nothing) and the rest is dropped one line at a time.
    And ``build_validation_spec`` skips anything ``is_executable_command``
    rejects, without saying so. Both are refused here instead, with a reason
    the person can act on while the field is still on screen.
    """
    accepted: list[str] = []
    dropped: list[DroppedCommand] = []
    for command in raw:
        if "\n" in command:
            dropped.append(DroppedCommand(command=command, reason=REASON_MULTILINE))
        elif not is_executable_command(command):
            dropped.append(DroppedCommand(command=command, reason=REASON_NOT_RUNNABLE))
        else:
            accepted.append(command)
    return accepted, dropped


def _correction_prompt(dropped: list[DroppedCommand]) -> str:
    listed = "\n".join(f"- {item.reason}:\n{item.command}" for item in dropped)
    return (
        "Every validation command in your previous answer was rejected:\n"
        f"{listed}\n\n"
        "Rewrite the same checks so that each command is ONE line executed by "
        "`sh -c`: no heredocs, no line breaks; inline multi-statement checks with "
        '`python3 -c "..."` or `node -e "..."` and `;`. Every command must '
        "start with a known binary or reference {workspace}. Return the full JSON "
        "object again with all four keys."
    )


def _parse_draft(raw_response: str) -> dict:
    cleaned = _sanitize_json(raw_response)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM did not return a JSON object.")
    return parsed


def _load_repo_context(target_id: str) -> str:
    """Describe the target repository for the assistant, or say nothing.

    Best-effort by design: a repository that has not been cloned yet still gets
    a draft, it is simply a more generic one. Failing the draft because the
    context could not be read would make the assistant less useful exactly when
    the operator is still setting the repository up.
    """
    if not target_id.strip():
        return ""
    try:
        from harness_lab_app.checkout_service import repo_context
        from harness_lab_app.db import engine
        from harness_lab_app.target_service import TargetService

        with Session(engine) as session:
            target = TargetService(session).get_target(target_id.strip())
            return repo_context(target).as_prompt_block()
    except Exception:
        return ""


def _case_type(raw: object) -> str:
    """The model's case type, or the safe default.

    Only the hand-authored types are accepted. `swe_bench_instance` and
    `swe_pro_instance` are imported from a dataset and carry test lists the
    lab scores against; a drafted case wearing that type would claim an
    objective verdict nothing computes.
    """
    value = str(raw or "").strip()
    return value if value in _AUTHORABLE_CASE_TYPES else CaseType.single_prompt


def _step_prompts(raw: object, case_type: str) -> list[str]:
    """Per-step prompts, kept only where the runner reads them.

    `step_prompts` drives step 2 onward of a `multi_step` case. On any other
    type the runner never opens the list, so keeping it would show a plan in
    the editor that nothing executes.
    """
    if case_type != CaseType.multi_step or not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _max_steps(raw: object) -> int:
    return raw if isinstance(raw, int) and 1 <= raw <= 100 else 10


def _case_id(raw: object, subject: str, taken: list[str]) -> str:
    """A slug that is free in the catalog.

    The person can still rename it, but they should not have to discover the
    collision by pressing Create and reading a 409.
    """
    slug = _slugify(str(raw or "")) or _slugify(subject) or "drafted-case"
    used = {item.strip() for item in taken if item.strip()}
    if slug not in used:
        return slug
    for suffix in range(2, 100):
        candidate = f"{slug}-{suffix}"
        if candidate not in used:
            return candidate
    return slug


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:40].strip("-")


def generate_case_draft(req: CaseDraftRequest) -> CaseDraftResponse:
    """Draft a case from a scenario, keeping only commands the lab would run.

    The model gets one corrective turn if its first answer leaves no runnable
    command; the response always carries what was dropped and why, so an
    empty ``validation_commands`` is never silent.
    """
    if not req.description.strip():
        raise ValueError("Description cannot be empty.")

    chosen_model = req.model or default_draft_model()

    skill_context = _load_skill_context(req.selected_skills)
    user_prompt = f"User Test Scenario Description:\n{req.description.strip()}"
    if skill_context:
        user_prompt += f"\n\n{skill_context}"
    repo_block = _load_repo_context(req.target_id)
    if repo_block:
        user_prompt += f"\n\n{repo_block}"

    attempts = 0
    parsed: dict = {}
    commands: list[str] = []
    dropped: list[DroppedCommand] = []
    prompt = user_prompt
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        raw_response = _call_model(chosen_model, _SYSTEM_PROMPT, prompt)
        parsed = _parse_draft(raw_response)
        commands, dropped = screen_commands(_raw_commands(parsed))
        if commands or not dropped:
            break
        # Nothing runnable, but the model did try: show it exactly what died
        # and let it rewrite. The transport carries one (system, user) pair, so
        # the model's own answer travels inside the corrective prompt.
        prompt = (
            f"{user_prompt}\n\nYour previous answer:\n{raw_response}\n\n"
            f"{_correction_prompt(dropped)}"
        )

    subject = str(parsed.get("subject") or "Validation case").strip()
    instruction = str(parsed.get("instruction") or req.description).strip()
    case_type = _case_type(parsed.get("case_type"))
    step_prompts = _step_prompts(parsed.get("step_prompts"), case_type)
    # The plan decides the floor: a budget below 1 + len(step_prompts) would
    # stop the run before the last step it was given a prompt for.
    max_steps = max(_max_steps(parsed.get("max_steps")), 1 + len(step_prompts))

    return CaseDraftResponse(
        subject=subject,
        instruction=instruction,
        validation_commands=commands,
        max_steps=max_steps,
        dropped_commands=dropped,
        attempts=attempts,
        case_id=_case_id(parsed.get("case_id"), subject, req.existing_case_ids),
        description=str(parsed.get("description") or "").strip(),
        case_type=case_type,
        step_prompts=step_prompts,
    )
