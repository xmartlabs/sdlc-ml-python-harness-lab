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
from harness_lab_app.schemas import CaseDraftRequest, CaseDraftResponse

DEFAULT_DRAFT_MODEL = "gpt-5.4-nano"

_SYSTEM_PROMPT = (
    "You are an expert test designer for autonomous AI coding agents in a benchmark harness. "
    "Your job is to convert a user test scenario description into a concrete, rigorous, executable validation case.\n\n"
    "Guidelines:\n"
    "- NEVER generate trivial toy tasks (like 'sum two numbers', 'hello world', or printing text). Always design realistic, self-contained software engineering problems (e.g., building a data structure, parsing a format, writing a service utility, or implementing an API client with edge cases).\n"
    "- For meta/style/token-efficiency skills (like 'caveman', 'be-terse', TDD, etc.), design a substantial software task with automated unit tests. The goal is to measure whether the agent can successfully deliver working code and tests while operating under the skill's style or token constraints.\n"
    "- Validation commands MUST verify actual functional correctness and exit non-zero on failure. Prefer running test suites (e.g. `pytest {workspace}/test_*.py` or running a python script with assertions) rather than mere file existence checks like `test -s`.\n"
    "- The agent instruction should clearly describe expected inputs, outputs, file paths in `{workspace}`, and requirements.\n\n"
    "Return a strict JSON object with these exact keys:\n"
    '- "subject": concise string naming the tool/feature under test (e.g. "caveman: token-efficient Python module implementation")\n'
    '- "instruction": clear, precise, comprehensive prompt the agent will receive in its workspace to perform the task\n'
    '- "validation_commands": array of non-empty bash commands that run after the agent stops and exit non-zero if the agent failed. Use {workspace} for workspace paths (e.g. ["pytest {workspace}/test_module.py"])\n'
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


def generate_case_draft(req: CaseDraftRequest) -> CaseDraftResponse:
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

    payload = {
        "model": normalize_model_name(chosen_model),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    raw_response = _call_gateway_chat(payload)
    cleaned = _sanitize_json(raw_response)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc

    subject = str(parsed.get("subject") or "Validation case").strip()
    instruction = str(parsed.get("instruction") or req.description).strip()

    raw_commands = parsed.get("validation_commands")
    commands: list[str] = []
    if isinstance(raw_commands, list):
        commands = [str(c).strip() for c in raw_commands if str(c).strip()]
    elif isinstance(raw_commands, str) and raw_commands.strip():
        commands = [line.strip() for line in raw_commands.split("\n") if line.strip()]

    raw_steps = parsed.get("max_steps")
    max_steps = 10
    if isinstance(raw_steps, int) and 1 <= raw_steps <= 100:
        max_steps = raw_steps

    return CaseDraftResponse(
        subject=subject,
        instruction=instruction,
        validation_commands=commands,
        max_steps=max_steps,
    )
