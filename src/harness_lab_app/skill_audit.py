"""The LLM as auditor of one skill: what it covers, what it misses, a v2 draft.

The experiment audit reads a whole experiment and comments on the harness. This
reads the same evidence (``experiment_audit_service.build_evidence``: every
run's failed checks, per arm) next to one skill's own text, and answers the
question that leads to a rewrite: the checks the case kept failing while the
skill was in use — does the skill name them, and if it does, why did the agent
not act on it? It returns a diagnosis and a full **draft** of a v2.

Nothing is written. Staging the draft as ``<name>-v2`` is a separate, explicit
step (ADR-0092), and the v2 earns nothing until it wins a v1-vs-v2 trial.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlmodel import Session

#: Characters of SKILL.md and of evidence handed to the auditor. A skill is a
#: few KB; the evidence is clipped per run by ``build_evidence`` already.
SKILL_CLIP = 12_000
EVIDENCE_CLIP = 24_000

SYSTEM = """You audit one skill — instructions an AI coding agent loads to do a task.
You are given the skill's SKILL.md and the evidence of an experiment where it was
used: for each run, the case's checks that failed. Answer in JSON only:
{
  "covers": ["what the skill already asks for that the evidence shows working"],
  "misses": [{"check": "a failed check or defect class", "why": "the skill does not name it | names it but too weakly | names it but the agent skipped it", "fix": "one concrete change to the skill"}],
  "summary": "two sentences: the main reason the skill did not change the outcome, and the bet the v2 makes",
  "v2": "the complete rewritten SKILL.md, frontmatter included, same name with -v2"
}
Rules: base every miss on a failed check in the evidence; never invent defects or
quote the case's hidden answer key; keep what works; prefer demanding evidence
per item over adding more advice; keep the v2 no longer than the original plus
30%."""


class SkillAuditError(ValueError):
    """The skill or its evidence cannot be audited as asked."""


class SkillNotFoundError(SkillAuditError):
    """No staged or governed skill has this name."""


@dataclass(frozen=True)
class SkillAudit:
    skill: str
    experiment_id: str
    model: str
    covers: list[str]
    misses: list[dict[str, str]]
    summary: str
    v2: str
    runs_read: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "experiment_id": self.experiment_id,
            "model": self.model,
            "covers": self.covers,
            "misses": self.misses,
            "summary": self.summary,
            "v2": self.v2,
            "v2_name": v2_name(self.skill),
            "runs_read": self.runs_read,
        }


def v2_name(skill: str) -> str:
    return f"{skill}-v2"


def skill_text(session: Session, name: str) -> str:
    """SKILL.md of a staged skill, or of a governed one; SkillAuditError if neither."""
    from harness_lab_app.config import get_settings
    from harness_lab_app.staged_skill_service import (
        StagedSkillError,
        StagedSkillService,
    )
    from ml_python_base.skills_sync.discovery import discover_skills

    try:
        staged = StagedSkillService(session).get_skill(name)
        if staged.content.strip():
            return staged.content
    except StagedSkillError:
        pass
    for skill in discover_skills(get_settings().repo_root):
        if skill.name != name:
            continue
        path = skill.source_path
        file = path / "SKILL.md" if path.is_dir() else path
        if file.is_file():
            return file.read_text(encoding="utf-8")
    raise SkillNotFoundError(f"No staged or governed skill named '{name}'.")


def _parse(raw: str) -> dict[str, Any]:
    """The auditor's JSON, tolerating a fenced block around it."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SkillAuditError("The auditor did not return JSON.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SkillAuditError(f"The auditor returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not str(data.get("v2", "")).strip():
        raise SkillAuditError("The auditor's answer has no v2 draft.")
    return data


def audit_skill(
    session: Session,
    skill: str,
    experiment_id: str,
    *,
    model: str = "",
    call: Callable[[str, str, str], tuple[str, str]] | None = None,
) -> SkillAudit:
    """Diagnose ``skill`` from ``experiment_id``'s evidence and draft a v2.

    ``call(model, system, user) -> (text, model_used)`` defaults to the shared
    LLM transport; tests pass a stub. Raises ``SkillAuditError`` when the
    experiment has no finished run that carried the skill.
    """
    from harness_lab_app.experiment_audit_service import (
        build_evidence,
        call_with_retry,
    )

    evidence = build_evidence(session, experiment_id)
    arms = [
        arm
        for arm in evidence.get("arms", [])
        if skill in (arm.get("overrides", {}).get("skills_extra") or [])
        or skill in (arm.get("overrides", {}).get("skills_invoke") or [])
    ]
    if not arms:
        raise SkillAuditError(
            f"No arm of '{experiment_id}' carried '{skill}'. Audit an experiment "
            "where the skill was asked for."
        )
    labels = {arm["label"] for arm in arms}
    runs = [run for run in evidence.get("runs", []) if run.get("arm") in labels]
    if not runs:
        raise SkillAuditError(
            f"'{experiment_id}' has no finished run with '{skill}' yet."
        )
    text = skill_text(session, skill)[:SKILL_CLIP]
    user = (
        f"# SKILL.md of {skill}\n\n{text}\n\n"
        f"# Evidence: runs that carried {skill}\n\n"
        + json.dumps({"arms": arms, "runs": runs}, indent=1, default=str)[
            :EVIDENCE_CLIP
        ]
    )
    if call is None:
        from harness_lab_app.llm_transport import chat_reply, default_audit_model

        chosen = model or default_audit_model()

        def call(m: str, system: str, prompt: str) -> tuple[str, str]:
            reply = chat_reply(
                m,
                system,
                prompt,
                trace_kind="skill_audit",
                trace_metadata={"skill": skill},
            )
            return reply.text, reply.model

        model = chosen
    holder: dict[str, str] = {}

    def once() -> str:
        answer, used = call(model, SYSTEM, user)
        holder["model"] = used
        return answer

    data = _parse(call_with_retry(once))
    return SkillAudit(
        skill=skill,
        experiment_id=experiment_id,
        model=holder.get("model", model),
        covers=[str(item) for item in data.get("covers") or []],
        misses=[
            {key: str(miss.get(key, "")) for key in ("check", "why", "fix")}
            for miss in data.get("misses") or []
            if isinstance(miss, dict)
        ],
        summary=str(data.get("summary", "")),
        v2=str(data["v2"]),
        runs_read=len(runs),
    )
