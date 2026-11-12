"""Per-step reward: credit each step of a run with the progress it made.

Inspired by reinforcement learning (ADR-0076). An agent run is an episode; each
harness invocation is a step; the case's own objective checks are the reward
signal. Instead of grading only the final state ("did it pass, and how many
steps did it take"), the lab grades the workspace **after every step** and
assigns each step the change it caused:

    progress_t = conclusive checks passing after step t / conclusive checks
    reward_t   = progress_t - progress_{t-1}            (progress_0 = 0)

The rewards telescope: their sum is the final progress, so the per-step view
never contradicts the run's outcome — it explains where the outcome came from.
Cost enters as a separate, unweighted series rather than a ``- λ·cost`` term:
the lab does not pick a price for a token on anyone's behalf, and the summary
metrics below (``cost_to_resolve``, ``post_resolution_cost_share``) are what
the λ-weighted return would trade off.

What this module is careful **not** to claim:

* **Not causal.** ``credit`` lists what a step consulted (a skill, a governance
  document) beside the reward that step earned. That is co-occurrence — the
  agent might have made the same progress without it. Causality comes only from
  ablation: run the same case without the item and compare (``pruning`` in
  :mod:`harness_lab_app.statistics`). Every credit row says ``kind:
  "co_occurrence"`` so no reader mistakes it for an effect.
* **Not shown to the agent.** The checks run on snapshots, after the run, in the
  worker — the agent never sees its reward, so measuring does not change what is
  measured (and the reward cannot be gamed by reading it).

Everything here is pure: no DB, no subprocess, no clock. The runner writes the
snapshots and per-step cost; the validation service runs the checks; this module
turns those facts into a trajectory.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

__all__ = [
    "CO_OCCURRENCE",
    "StepOutcome",
    "Trajectory",
    "build_trajectory",
    "claude_step_cost",
    "consulted_items",
    "corpus_credit",
    "progress_of",
    "read_step_meta",
    "shell_read_items",
    "snapshot_dirs",
    "step_record_files",
    "swe_progress",
]

# Progress at or above this counts as resolved: every conclusive check passed.
RESOLVED = 1.0
# A float tolerance for "did progress move", so 0.30000000000000004 is not a step.
_EPSILON = 1e-9
CO_OCCURRENCE = "co_occurrence"

_SNAPSHOT_RE = re.compile(r"^step-(\d+)\.snapshot$")
_META_RE = re.compile(r"^step-(\d+)\.meta\.json$")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def progress_of(objective: Mapping[str, Any] | None) -> float | None:
    """Share of conclusive checks that passed, or None when nothing measured.

    The same rule ``test_pass_rate`` uses: a check that timed out or could not
    launch measured the sandbox, not the agent, and stays out of the
    denominator (ADR-0048).
    """
    if not objective:
        return None
    conclusive = [
        check
        for check in objective.get("checks") or []
        if check.get("conclusive") is not False
    ]
    if not conclusive:
        return None
    return sum(1 for check in conclusive if check.get("passed")) / len(conclusive)


def swe_progress(swe: Mapping[str, Any]) -> float | None:
    """Progress on the SWE lane from the runner's live F2P/P2P verdict.

    The issue is the FAIL_TO_PASS tests; PASS_TO_PASS are what must not break.
    So progress is the share of F2P fixed, scaled by the share of P2P kept: a
    step that fixes nothing scores 0 however many P2P still pass (a raw
    ``passed/total`` read 18/19 = 95% for an untouched issue), and a step that
    breaks P2P loses progress — a regression. 1.0 only when resolved.
    """
    f2p_total = int(swe.get("f2p_total") or 0)
    if f2p_total <= 0:
        return None
    if swe.get("resolved"):
        return 1.0
    fixed = int(swe.get("f2p_passed") or 0) / f2p_total
    p2p_total = int(swe.get("p2p_total") or 0)
    kept = int(swe.get("p2p_passed") or 0) / p2p_total if p2p_total else 1.0
    # Not resolved means something is still wrong; never report it as done.
    return min(fixed * kept, 0.99)


def snapshot_dirs(run_dir: Path) -> list[tuple[int, Path]]:
    """The run's per-step workspace snapshots, in step order."""
    steps = run_dir / "steps"
    if not steps.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in steps.iterdir():
        match = _SNAPSHOT_RE.match(path.name)
        if match and path.is_dir():
            found.append((int(match.group(1)), path))
    return sorted(found)


_STEP_LOG_RE = re.compile(r"^step-(\d+)\.log$")
_STEP_EVENTS_RE = re.compile(r"^step-(\d+)\.events\.jsonl$")


def step_record_files(run_dir: Path) -> dict[int, dict[str, Path]]:
    """Each step's own log and claude events file, by step number.

    A multi-step run writes ``steps/step-N.log`` (+ ``.events.jsonl``). A
    single-step run writes neither: its one step is ``opencode.log`` (+
    ``opencode.events.jsonl``) — the same fallback every other step reader
    uses — even though it now has a ``steps/`` directory for its snapshot.
    """
    found: dict[int, dict[str, Path]] = {}
    steps = run_dir / "steps"
    if steps.is_dir():
        for path in steps.iterdir():
            for kind, pattern in (("log", _STEP_LOG_RE), ("events", _STEP_EVENTS_RE)):
                match = pattern.match(path.name)
                if match:
                    found.setdefault(int(match.group(1)), {})[kind] = path
    if not found:
        single = {
            kind: path
            for kind, path in (
                ("log", run_dir / "opencode.log"),
                ("events", run_dir / "opencode.events.jsonl"),
            )
            if path.is_file()
        }
        if single:
            found[1] = single
    return found


def read_step_meta(run_dir: Path) -> dict[int, dict[str, Any]]:
    """Per-step facts the runner wrote (cost, tokens, turns), by step number."""
    steps = run_dir / "steps"
    if not steps.is_dir():
        return {}
    meta: dict[int, dict[str, Any]] = {}
    for path in steps.iterdir():
        match = _META_RE.match(path.name)
        if not match:
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            meta[int(match.group(1))] = data
    return meta


def claude_step_cost(lines: Iterable[str]) -> dict[str, Any]:
    """One claude step's cost, from the terminal ``result`` event of its stream.

    Per-turn usage inside the stream is a streaming snapshot that undercounts
    (ADR-0039); the ``result`` event is the step's total of record.
    """
    result: dict[str, Any] | None = None
    for raw in lines:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    if result is None:
        return {}
    usage = result.get("usage") or {}
    billed = sum(
        int(usage.get(key) or 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
    cost = result.get("total_cost_usd")
    return {
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "billed_tokens": billed,
        "output_tokens": int(usage.get("output_tokens") or 0),
        "turns": result.get("num_turns"),
    }


# A shell command that prints a file: `cat .github/standards.md`, `head -80 x`,
# `sed -n 1,40p x`, `less x`. Agents read the harness this way as often as with a
# Read tool, and the attribution parsers only see the tool.
_SHELL_READ_RE = re.compile(r"\b(?:cat|head|tail|sed|less|more|bat|nl)\b[^\n|;&]*")
_SKILL_FILE_RE = re.compile(r"skills(?:-external)?/(?P<name>[\w.-]+)/SKILL\.md")


def shell_read_items(
    commands: Iterable[str], governance: Sequence[str] = ()
) -> set[str]:
    """Harness items a step printed with a shell read, not a Read tool.

    A heuristic, and labelled as one: a governance path or a ``SKILL.md`` that
    appears in the arguments of ``cat``/``head``/``sed``/... is counted as read.
    """
    items: set[str] = set()
    for command in commands:
        for match in _SHELL_READ_RE.finditer(command):
            fragment = match.group(0)
            for path in governance:
                if path in fragment:
                    items.add(f"doc:{path}")
            for skill in _SKILL_FILE_RE.finditer(fragment):
                items.add(f"skill:{skill.group('name')}")
    return items


def consulted_items(
    parsed: Mapping[str, Any],
    governance: Sequence[str] = (),
    shell_commands: Iterable[str] = (),
) -> list[str]:
    """What a step opened from the harness: its skills and governance documents.

    ``parsed`` is one step's output of :func:`attribution.parse_logs` or
    :func:`attribution.parse_claude_events`; ``shell_commands`` are the step's
    raw shell commands, scanned for reads the parsers do not see (see
    :func:`shell_read_items`). Prefixed so a skill and a document with the same
    name can never merge in the credit table.
    """
    surface = set(governance)
    items = {f"skill:{name}" for name in parsed.get("skills_consulted") or []}
    items |= {
        f"doc:{path}" for path in parsed.get("files_read") or [] if path in surface
    }
    items |= shell_read_items(shell_commands, governance)
    return sorted(items)


# --------------------------------------------------------------------------
# The trajectory
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StepOutcome:
    step: int
    progress: float | None
    # Change in progress this step caused; None when the step was not measured.
    reward: float | None
    cost_usd: float | None = None
    billed_tokens: int | None = None
    passed: int | None = None
    total: int | None = None
    consulted: tuple[str, ...] = ()


@dataclass(frozen=True)
class Trajectory:
    steps: list[StepOutcome]
    final_progress: float | None
    best_progress: float | None
    # First step at which every conclusive check passed; None if never.
    steps_to_resolve: int | None
    # First step at which the run reached its best progress (resolved or not).
    steps_to_best: int | None
    cost_to_resolve_usd: float | None
    post_resolution_cost_share: float | None
    post_resolution_token_share: float | None
    post_best_cost_share: float | None
    progress_auc: float | None
    step_regressions: int
    unmeasured_steps: int
    credit: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.steps_to_resolve is not None

    @property
    def measured(self) -> bool:
        return self.final_progress is not None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resolved"] = self.resolved
        data["measured"] = self.measured
        for step in data["steps"]:
            step["consulted"] = list(step["consulted"])
        return data


def _share_after(values: Sequence[float | None], index: int) -> float | None:
    """Share of the total spent after step ``index`` (0-based), or None.

    None when any step's figure is unknown, or the total is zero: a share of a
    partly measured total, or of nothing, is not a number anyone should read.
    """
    if not values or any(value is None for value in values):
        return None
    known = [float(value) for value in values if value is not None]
    total = sum(known)
    if total <= 0:
        return None
    return sum(known[index + 1 :]) / total


def _credit(steps: Sequence[StepOutcome]) -> list[dict[str, Any]]:
    """Reward earned by the steps that consulted each item. Co-occurrence only."""
    rows: dict[str, dict[str, Any]] = {}
    for step in steps:
        for item in step.consulted:
            row = rows.setdefault(
                item,
                {
                    "item": item,
                    "kind": CO_OCCURRENCE,
                    "steps": [],
                    "reward_when_consulted": 0.0,
                    "measured_steps": 0,
                },
            )
            row["steps"].append(step.step)
            if step.reward is not None:
                row["reward_when_consulted"] += step.reward
                row["measured_steps"] += 1
    for row in rows.values():
        row["reward_when_consulted"] = round(row["reward_when_consulted"], 6)
    return sorted(
        rows.values(), key=lambda row: (-row["reward_when_consulted"], row["item"])
    )


def corpus_credit(
    trajectories: Iterable[Mapping[str, Any]], available: Iterable[str] = ()
) -> list[dict[str, Any]]:
    """Credit per harness item across many runs — the pruning shortlist.

    Sums each run's ``credit`` rows. Items listed in ``available`` that no run
    ever consulted are included with zero runs: those are the cheapest pruning
    candidates, because they cost context and were never opened. Ordered from
    least to most evidence of use, so the shortlist is the head of the list.

    Co-occurrence, still: this picks what to ablate first, it does not decide
    what to remove — :mod:`harness_lab_app.pruning` does, from an ablation.
    """
    rows: dict[str, dict[str, Any]] = {
        item: {"item": item, "runs": 0, "steps": 0, "reward_when_consulted": 0.0}
        for item in available
    }
    total_runs = 0
    for trajectory in trajectories:
        if not trajectory.get("measured"):
            continue
        total_runs += 1
        for credit in trajectory.get("credit") or []:
            row = rows.setdefault(
                credit["item"],
                {
                    "item": credit["item"],
                    "runs": 0,
                    "steps": 0,
                    "reward_when_consulted": 0.0,
                },
            )
            row["runs"] += 1
            row["steps"] += len(credit.get("steps") or [])
            row["reward_when_consulted"] += float(
                credit.get("reward_when_consulted") or 0.0
            )
    for row in rows.values():
        row["kind"] = CO_OCCURRENCE
        row["share_of_runs"] = row["runs"] / total_runs if total_runs else 0.0
        row["reward_when_consulted"] = round(row["reward_when_consulted"], 6)
    return sorted(
        rows.values(),
        key=lambda row: (row["runs"], row["reward_when_consulted"], row["item"]),
    )


def build_trajectory(steps: Sequence[Mapping[str, Any]]) -> Trajectory:
    """Turn per-step facts into rewards and the run's summary metrics.

    Each entry: ``step``, ``progress`` (0-1 or None), and optionally
    ``cost_usd``, ``billed_tokens``, ``passed``, ``total``, ``consulted``.
    An unmeasured step (``progress`` None) earns no reward and leaves the last
    known progress standing, so the next measured step is credited with the
    change since the last measurement — nothing is invented for the gap.
    """
    ordered = sorted(steps, key=lambda row: int(row["step"]))
    outcomes: list[StepOutcome] = []
    last = 0.0
    regressions = 0
    unmeasured = 0
    for row in ordered:
        progress = row.get("progress")
        reward: float | None = None
        if progress is None:
            unmeasured += 1
        else:
            reward = float(progress) - last
            if reward < -_EPSILON:
                regressions += 1
            last = float(progress)
        cost = row.get("cost_usd")
        tokens = row.get("billed_tokens")
        outcomes.append(
            StepOutcome(
                step=int(row["step"]),
                progress=None if progress is None else float(progress),
                reward=None if reward is None else round(reward, 6),
                cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                billed_tokens=int(tokens) if isinstance(tokens, (int, float)) else None,
                passed=row.get("passed"),
                total=row.get("total"),
                consulted=tuple(row.get("consulted") or ()),
            )
        )

    measured = [step for step in outcomes if step.progress is not None]
    if not measured:
        return Trajectory(
            steps=outcomes,
            final_progress=None,
            best_progress=None,
            steps_to_resolve=None,
            steps_to_best=None,
            cost_to_resolve_usd=None,
            post_resolution_cost_share=None,
            post_resolution_token_share=None,
            post_best_cost_share=None,
            progress_auc=None,
            step_regressions=regressions,
            unmeasured_steps=unmeasured,
            credit=_credit(outcomes),
        )

    final = measured[-1].progress
    best = max(step.progress for step in measured if step.progress is not None)
    resolve_index = next(
        (
            index
            for index, step in enumerate(outcomes)
            if step.progress is not None and step.progress >= RESOLVED - _EPSILON
        ),
        None,
    )
    best_index = next(
        index
        for index, step in enumerate(outcomes)
        if step.progress is not None and step.progress >= best - _EPSILON
    )
    costs = [step.cost_usd for step in outcomes]
    tokens = [
        float(step.billed_tokens) if step.billed_tokens is not None else None
        for step in outcomes
    ]

    cost_to_resolve: float | None = None
    if resolve_index is not None:
        upto = costs[: resolve_index + 1]
        if all(value is not None for value in upto):
            cost_to_resolve = round(sum(float(v) for v in upto if v is not None), 6)

    # Area under the progress curve, one unit per step, carrying the last known
    # value across unmeasured steps. 1.0 means solved in the first step and
    # kept; arriving later, or regressing, costs area.
    carried = 0.0
    area = 0.0
    for step in outcomes:
        if step.progress is not None:
            carried = step.progress
        area += carried
    auc = area / len(outcomes)

    def _rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 6)

    return Trajectory(
        steps=outcomes,
        final_progress=final,
        best_progress=best,
        steps_to_resolve=(
            outcomes[resolve_index].step if resolve_index is not None else None
        ),
        steps_to_best=outcomes[best_index].step,
        cost_to_resolve_usd=cost_to_resolve,
        post_resolution_cost_share=(
            _rounded(_share_after(costs, resolve_index))
            if resolve_index is not None
            else None
        ),
        post_resolution_token_share=(
            _rounded(_share_after(tokens, resolve_index))
            if resolve_index is not None
            else None
        ),
        post_best_cost_share=_rounded(_share_after(costs, best_index)),
        progress_auc=round(auc, 6),
        step_regressions=regressions,
        unmeasured_steps=unmeasured,
        credit=_credit(outcomes),
    )
