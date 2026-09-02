"""Patterns the lab noticed, offered to a person — never findings by themselves.

A finding is a claim someone in this lab is prepared to defend to a client
(ADR-0035). Nothing here produces one. These functions read the run corpus and
say "this looks worth a look", with the evidence attached and a stable key, and
that is where their authority stops: a suggestion becomes a finding only when a
person accepts it and their name goes on it.

Every suggestion carries:

``key``
    Stable across recomputation, so a suggestion a person has already accepted
    or rejected never comes back. It is stored on the resulting finding's
    ``origin``.
``evidence_run_ids`` / ``affected_case_ids``
    What the pattern was read from. A suggestion with neither would be a hunch
    the lab dressed up as an observation.
``recommendation``
    Phrased as something testable, because the next step in the loop is an
    experiment and a suggestion that cannot become one is a dead end.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# A case has to fail more than once before a failure is a pattern rather than a
# bad afternoon.
REPEATED_FAILURES = 2
# Below this share of the harness judged useful, the harness itself is worth
# questioning rather than the agent.
LOW_ADHERENCE = 0.5
# Below this share of a run's own governance surface actually read, the
# documents are not reaching the agent.
LOW_COVERAGE = 0.34


def _suggestion(
    key: str,
    title: str,
    body: str,
    *,
    severity: str = "medium",
    recommendation: str = "",
    intervention: str = "",
    target_repo_id: str = "",
    evidence_run_ids: list[str] | None = None,
    affected_case_ids: list[str] | None = None,
    source: str = "",
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "body": body,
        "severity": severity,
        "recommendation": recommendation,
        "intervention": intervention,
        "target_repo_id": target_repo_id,
        "evidence_run_ids": evidence_run_ids or [],
        "affected_case_ids": affected_case_ids or [],
        "source": source,
        # Said on every one of them, in the payload rather than only in the UI.
        "status": "suggested",
    }


def _by_case(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("resolved") is None or not row.get("case_id"):
            continue
        # A continued run was steered by a human between steps, so it does not
        # describe the harness's own behaviour on the case.
        if row.get("chain_depth"):
            continue
        grouped[str(row["case_id"])].append(row)
    return {
        case_id: sorted(case_rows, key=lambda row: row.get("created_at") or "")
        for case_id, case_rows in grouped.items()
    }


def from_regressions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A case that used to pass and now does not."""
    out = []
    for case_id, ordered in _by_case(rows).items():
        if len(ordered) < 2:
            continue
        if ordered[-1]["resolved"] or not any(row["resolved"] for row in ordered[:-1]):
            continue
        passed = [row for row in ordered[:-1] if row["resolved"]]
        out.append(
            _suggestion(
                f"regression:{case_id}",
                f"'{case_id}' regressed",
                (
                    f"This case passed {len(passed)} earlier run(s) and fails on "
                    "the most recent one. Something the harness used to handle, "
                    "it no longer handles."
                ),
                severity="high",
                recommendation=(
                    "Identify what changed between the last passing run and the "
                    "failing one, then run both harness states against this case."
                ),
                intervention="Revert or repair the harness change that broke it.",
                target_repo_id=str(ordered[-1].get("target_repo_id") or ""),
                evidence_run_ids=[
                    str(passed[-1]["run_id"]),
                    str(ordered[-1]["run_id"]),
                ],
                affected_case_ids=[case_id],
                source="regression",
            )
        )
    return out


def from_repeated_failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A case that has never passed. Not a regression — an open problem."""
    out = []
    for case_id, ordered in _by_case(rows).items():
        if len(ordered) < REPEATED_FAILURES or any(row["resolved"] for row in ordered):
            continue
        out.append(
            _suggestion(
                f"repeated-failure:{case_id}",
                f"'{case_id}' has never passed",
                (
                    f"All {len(ordered)} validated run(s) of this case failed. "
                    "Either the task is beyond the current harness or the case's "
                    "own validation is wrong — both are worth knowing."
                ),
                recommendation=(
                    "Read one failing run end to end before treating this as a "
                    "harness gap: confirm the case's validation commands pass on "
                    "a correct solution."
                ),
                evidence_run_ids=[str(row["run_id"]) for row in ordered[-3:]],
                affected_case_ids=[case_id],
                source="repeated_failure",
            )
        )
    return out


def from_score_signal(signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Harness-level patterns the score corpus shows in aggregate.

    These read **judgements**, not facts: `skill_adherence` is an auditor's
    opinion about whether the harness was used. Suggestions built on it say so,
    because a person deciding whether to accept one needs to know what kind of
    claim they are underwriting.
    """
    out = []
    adherence = signal.get("adherence") or {}
    mean = adherence.get("mean")
    n = adherence.get("runs") or 0
    if mean is not None and n >= 3 and mean < LOW_ADHERENCE:
        out.append(
            _suggestion(
                "low-adherence",
                "Most of the harness is not being used",
                (
                    f"Across {n} audited run(s), the auditor judged {mean:.0%} of "
                    "the available harness items to have been genuinely used. "
                    "This is an LLM judgement, not an observation of invocation."
                ),
                recommendation=(
                    "Remove or rewrite the items judged redundant, then compare "
                    "the trimmed harness against the current one."
                ),
                intervention="Trim the harness to the items that earn their place.",
                source="score_corpus",
            )
        )

    redundant = signal.get("redundant") or {}
    if (redundant.get("mean") or 0) >= 1 and (redundant.get("runs") or 0) >= 3:
        out.append(
            _suggestion(
                "redundant-items",
                "Harness items are consistently judged redundant",
                (
                    f"An average of {redundant['mean']:.1f} item(s) per run were "
                    f"judged redundant across {redundant['runs']} audited run(s)."
                ),
                severity="low",
                recommendation=(
                    "List the items named redundant most often and test whether "
                    "removing them changes anything."
                ),
                source="score_corpus",
            )
        )
    return out


def build(
    rows: list[dict[str, Any]],
    *,
    score_signal: dict[str, Any] | None = None,
    ruled_on: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Every suggestion the corpus supports, minus the ones already ruled on.

    ``ruled_on`` is the set of suggestion keys that already produced a finding —
    accepted or rejected. Recomputing a suggestion somebody has already dealt
    with is how a review queue stops being read.
    """
    seen = ruled_on or set()
    found = [
        *from_regressions(rows),
        *from_repeated_failures(rows),
        *from_score_signal(score_signal or {}),
    ]
    rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        (item for item in found if item["key"] not in seen),
        key=lambda item: (rank.get(item["severity"], 9), item["key"]),
    )
