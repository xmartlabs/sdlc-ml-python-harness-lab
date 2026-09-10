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
    from collections.abc import Callable, Sequence

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
    "summary is present, weight it heavily — but a check marked INCONCLUSIVE "
    "timed out, was killed, or never ran, and is evidence about the lab rather "
    "than the agent: ignore it and judge the diff. Keep the rationale to 1-3 "
    "sentences, and never include secrets, tokens, or local filesystem paths."
)

RUBRIC_SYSTEM_PROMPT = (
    "You are grading an autonomous coding agent's output against a rubric "
    "supplied by the person who wrote the task. The rubric is the ONLY scale "
    "you may use: do not substitute your own idea of quality, and do not "
    "reward effort the rubric does not ask for. Output ONLY a JSON object: "
    '{"score": <integer 0-10>, "evidence": <string>, "rationale": <string>}. '
    "Rubric levels are cumulative unless the rubric says otherwise: award the "
    "HIGHEST level whose requirement is evidenced, and every level below it "
    "must hold too. "
    "Each rubric level may carry an evidence tag that says what can establish "
    "it. [check]: only an automated check can — award it only when a PASS "
    "check establishes it, never from the diff or the report. [diff]: the code "
    "itself can — award it only when you can quote the lines that implement "
    "it, and put that quotation in the evidence field. [manual]: only a human "
    "running the program can — NEVER award it; stop at the highest level "
    "below it that you can establish. An untagged level is treated as [diff]. "
    "Automated checks are marked PASS, FAIL or INCONCLUSIVE. A FAIL is a fact "
    "about the code and refutes every level it contradicts. An INCONCLUSIVE "
    "check timed out, was killed, or never ran: it is a fact about the lab, not "
    "about the agent, so it neither confirms nor refutes anything — do not read "
    "it as a failure, and do not award a [check] level on it. "
    "Checks and the file list are authoritative on EXISTENCE: a PASS check that "
    "executed a path proves that file exists and behaves as the check asserts, "
    "and a file in the agent's file list was written even if the diff omits it "
    "(the diff can miss newly created files). Never conclude a file is missing "
    "when a PASS check ran it or the file list names it. A check's OUTPUT "
    "describes the repository the agent was working in; grade the agent's "
    "work, not the repository's pre-existing behaviour that the output reports. "
    "Treat the agent's claims as claims, not results: a report asserting a "
    "feature that the diff does not implement earns nothing. When the evidence "
    "cannot distinguish two levels, take the lower one and say why. Name the "
    "level you awarded and the evidence for it in 1-3 sentences. Never include "
    "secrets, tokens, or local filesystem paths."
)

# Keep the prompt bounded: the diff is the signal, the rest is context.
_DIFF_CLIP = 24000
_TEXT_CLIP = 8000
_RUBRIC_CLIP = 12000
_FILES_CLIP = 60


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
    result = {"score": score, "rationale": str(obj.get("rationale", "")).strip()}
    # The quoted code behind a [diff] level. Optional: the SWE judge and older
    # replies do not carry it, and a missing citation is not a parse failure.
    evidence = str(obj.get("evidence", "")).strip()
    if evidence:
        result["evidence"] = evidence
    return result


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


def build_rubric_message(
    *,
    task: str,
    rubric: str,
    diff: str,
    objective_summary: str = "",
    report: str = "",
    files_modified: Sequence[str] = (),
) -> str:
    """Render rubric + task + evidence as one prompt. Pure assembly.

    The rubric goes first and the evidence after it, so the scale is fixed
    before the model reads anything that might tempt it to invent one.
    """
    blocks = [
        "Score the agent's work against the rubric below.",
        f"## Rubric\n\n{rubric.strip()[:_RUBRIC_CLIP]}",
        f"## Task given to the agent\n\n{task.strip()[:_TEXT_CLIP]}",
    ]
    if objective_summary.strip():
        blocks.append(
            "## Automated checks already run (facts, not opinions — but an "
            "INCONCLUSIVE check is a fact about the lab, not about the code)"
            f"\n\n{objective_summary.strip()[:_TEXT_CLIP]}"
        )
    if files_modified:
        listed = "\n".join(f"- {path}" for path in list(files_modified)[:_FILES_CLIP])
        blocks.append(
            "## Files the agent wrote or modified (from the run's tool log — "
            f"facts; the diff below may omit newly created ones)\n\n{listed}"
        )
    diff = diff.strip()
    blocks.append(
        f"## Agent diff\n\n{diff[:_DIFF_CLIP] if diff else '(no diff produced)'}"
    )
    if report.strip():
        blocks.append(
            "## Agent report (the agent's own claims — verify against the diff)"
            f"\n\n{report.strip()[:_TEXT_CLIP]}"
        )
    return "\n\n".join(blocks)


def judge_rubric_score(
    *,
    task: str,
    rubric: str,
    diff: str,
    gateway: Callable[[str, str, str], str],
    objective_summary: str = "",
    report: str = "",
    files_modified: Sequence[str] = (),
    model: str = DEFAULT_JUDGE_MODEL,
) -> dict:
    """One gateway call → ``{score, rationale}``. Raises on an unparseable reply."""
    user = build_rubric_message(
        task=task,
        rubric=rubric,
        diff=diff,
        objective_summary=objective_summary,
        report=report,
        files_modified=files_modified,
    )
    return parse_judge_score(gateway(model, RUBRIC_SYSTEM_PROMPT, user))
