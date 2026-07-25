"""LLM-as-judge functional scoring for objective cases without a runnable verdict.

Used when a SWE-bench Pro run's automated tests could not deliver a conclusive
pass/fail (no validation image, inconclusive result): an LLM reads the task, the
diff the agent produced, and any partial test summary, and returns a 0-10
functional score with a short rationale (the same model that audits the run).

Pure prompt assembly + tolerant parsing; the gateway is an injectable callable so
the scoring stays testable without a network call (mirrors ``drafting``).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# The auditor's default model — "the same LLM that does the analysis".
DEFAULT_JUDGE_MODEL = "gpt-5.4-mini"

JUDGE_SYSTEM_PROMPT = (
    "You are grading whether an autonomous coding agent RESOLVED a software "
    "engineering task. You are given the task (problem statement / prompt), the "
    "diff the agent produced, and — when available — a partial automated-test "
    "summary. Output ONLY a JSON object: "
    '{"score": <integer 0-10>, "rationale": <string>}. '
    "Scoring guide: 10 = the change fully and correctly resolves the stated "
    "problem; 7-9 = correct approach with minor gaps; 4-6 = partial progress, the "
    "core issue unresolved; 1-3 = wrong direction or barely started; 0 = no "
    "relevant change. Judge only the code change against the task — never invent "
    "tests or credit intentions not present in the diff. When a partial test "
    "summary is present, weight it heavily. Keep the rationale to 1-3 sentences, "
    "and never include secrets, tokens, or local filesystem paths."
)

# Keep the prompt bounded: the diff is the signal, the rest is context.
_DIFF_CLIP = 24000
_TEXT_CLIP = 8000


def build_judge_message(
    *,
    problem_statement: str,
    diff: str,
    objective_summary: str = "",
    report: str = "",
) -> str:
    """Render the task + diff (+ optional test summary / report) as one prompt.

    Pure assembly, testable without a gateway.
    """
    blocks = [
        "Grade whether the agent resolved the task.",
        f"## Problem statement\n\n{problem_statement.strip()[:_TEXT_CLIP]}",
    ]
    if objective_summary.strip():
        blocks.append(
            "## Automated test summary (partial / may be unavailable)\n\n"
            f"{objective_summary.strip()[:_TEXT_CLIP]}"
        )
    diff = diff.strip()
    body = diff[:_DIFF_CLIP] if diff else "(no diff produced)"
    blocks.append(f"## Agent diff\n\n{body}")
    if report.strip():
        blocks.append(f"## Agent report\n\n{report.strip()[:_TEXT_CLIP]}")
    return "\n\n".join(blocks)


def parse_judge_score(raw: str) -> dict:
    """Tolerantly extract ``{score: int 0-10, rationale: str}`` from an LLM reply."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("judge response contained no JSON object")
    obj = json.loads(text[start : end + 1])
    score = max(0, min(10, round(float(obj["score"]))))
    return {"score": score, "rationale": str(obj.get("rationale", "")).strip()}


def judge_functional_score(
    *,
    problem_statement: str,
    diff: str,
    gateway: Callable[[str, str, str], str],
    objective_summary: str = "",
    report: str = "",
    model: str = DEFAULT_JUDGE_MODEL,
) -> dict:
    """One gateway call → ``{score, rationale}``. Raises on an unparseable reply."""
    user = build_judge_message(
        problem_statement=problem_statement,
        diff=diff,
        objective_summary=objective_summary,
        report=report,
    )
    return parse_judge_score(gateway(model, JUDGE_SYSTEM_PROMPT, user))
