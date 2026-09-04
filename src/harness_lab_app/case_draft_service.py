"""AI-assisted draft generation for validation cases.

Translates a user's natural language scenario description and active skill context
into a structured validation case (subject, instruction, validation commands, max_steps).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.executors import rewrite_localhost
from harness_lab_app.gateway_models import GATEWAY_USER_AGENT, normalize_model_name
from harness_lab_app.schemas import (
    CaseDraftRequest,
    CaseDraftResponse,
    DroppedCommand,
)
from harness_lab_app.validation import is_executable_command

DEFAULT_DRAFT_MODEL = "gpt-5.4-nano"

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
    '- "max_steps": integer step budget (5, 10, 15, 20, or 25, default 15)\n\n'
    "Only return valid JSON without markdown code fences."
)


def _gateway() -> tuple[str, str] | None:
    base = (
        os.environ.get("GATEWAY_BASE_URL")
        or read_env_file_value("GATEWAY_BASE_URL")
        or ""
    ).strip()
    token = (
        os.environ.get("GATEWAY_TOKEN") or read_env_file_value("GATEWAY_TOKEN") or ""
    ).strip()
    if not base or not token:
        return None
    if Path("/.dockerenv").exists():
        base = rewrite_localhost(base, get_settings().docker_host_alias)
    return base.rstrip("/"), token


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


def _call_gateway_chat(payload: dict) -> str:
    gateway = _gateway()
    if gateway is None:
        raise ValueError(
            "Gateway not configured (GATEWAY_BASE_URL and GATEWAY_TOKEN required)."
        )
    base, token = gateway
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": GATEWAY_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:  # nosec B310
        data = json.loads(resp.read())
    return (data["choices"][0]["message"]["content"] or "").strip()


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


def generate_case_draft(req: CaseDraftRequest) -> CaseDraftResponse:
    """Draft a case from a scenario, keeping only commands the lab would run.

    The model gets one corrective turn if its first answer leaves no runnable
    command; the response always carries what was dropped and why, so an
    empty ``validation_commands`` is never silent.
    """
    if not req.description.strip():
        raise ValueError("Description cannot be empty.")

    chosen_model = (
        req.model
        or os.environ.get("CASE_DRAFT_MODEL")
        or read_env_file_value("CASE_DRAFT_MODEL")
        or os.environ.get("GATEWAY_DEFAULT_MODEL")
        or DEFAULT_DRAFT_MODEL
    )

    skill_context = _load_skill_context(req.selected_skills)
    user_prompt = f"User Test Scenario Description:\n{req.description.strip()}"
    if skill_context:
        user_prompt += f"\n\n{skill_context}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    payload = {
        "model": normalize_model_name(chosen_model),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }

    attempts = 0
    parsed: dict = {}
    commands: list[str] = []
    dropped: list[DroppedCommand] = []
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        raw_response = _call_gateway_chat(payload)
        parsed = _parse_draft(raw_response)
        commands, dropped = screen_commands(_raw_commands(parsed))
        if commands or not dropped:
            break
        # Nothing runnable, but the model did try: show it exactly what died
        # and let it rewrite, keeping its own answer in the transcript.
        messages.append({"role": "assistant", "content": raw_response})
        messages.append({"role": "user", "content": _correction_prompt(dropped)})

    subject = str(parsed.get("subject") or "Validation case").strip()
    instruction = str(parsed.get("instruction") or req.description).strip()

    raw_steps = parsed.get("max_steps")
    max_steps = 10
    if isinstance(raw_steps, int) and 1 <= raw_steps <= 100:
        max_steps = raw_steps

    return CaseDraftResponse(
        subject=subject,
        instruction=instruction,
        validation_commands=commands,
        max_steps=max_steps,
        dropped_commands=dropped,
        attempts=attempts,
    )
