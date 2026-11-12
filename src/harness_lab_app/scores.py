"""The score registry: every metric the lab records, declared and versioned.

Three problems this exists to prevent, all of which are silent.

**A metric changing meaning under a stable name.** When a judge, a rubric or a
formula changes, `step_efficiency` from before and after are different
quantities. Pooling them produces a trend line that is an artefact of the change.
Every definition therefore carries a ``version``, and a score is stored under
``name@version``.

**Facts read as opinions, and opinions read as facts.** `test_pass_rate = 1.0`
is a command's exit status. `skill_usefulness = 0.81` is a model's opinion.
`skill_file_read = true` is something parsed out of a log. They are all numbers
on a screen, and only the declared ``provenance`` keeps them distinguishable.

**A number whose direction nobody remembers.** ``direction`` says whether more is
better, so a comparison surface can never render an improvement as a regression.
"""

from __future__ import annotations

from dataclasses import dataclass

# Score categories. They are kept separate all the way to the UI: collapsing
# them into one number is exactly what the roadmap forbids.
OUTCOME = "outcome"
EFFICIENCY = "efficiency"
TRAJECTORY = "trajectory"
BEHAVIORAL = "behavioral"
COMPOSITE = "composite"

# Where a value came from, which is what licenses how much weight it can carry.
FACT = "fact"  # a command exited, a file exists — reproducible without judgement
OBSERVATION = "observation"  # parsed from artifacts; true of the record, not the world
JUDGEMENT = "judgement"  # a model's opinion, defensible but not checkable

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
NEUTRAL = "neutral"

NUMERIC = "numeric"
BOOLEAN = "boolean"
CATEGORICAL = "categorical"


@dataclass(frozen=True)
class ScoreDefinition:
    name: str
    category: str
    kind: str
    provenance: str
    direction: str
    version: str
    description: str
    source: str = ""
    minimum: float | None = None
    maximum: float | None = None

    @property
    def key(self) -> str:
        """The identity a stored score is filed under."""
        return f"{self.name}@{self.version}"


def _d(**kwargs) -> ScoreDefinition:
    return ScoreDefinition(**kwargs)


# --- Level 1: deterministic outcome ---------------------------------------
_OUTCOME = [
    _d(
        name="task_validation_pass",
        category=OUTCOME,
        kind=BOOLEAN,
        provenance=FACT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deterministic",
        description="Every objective validation command exited 0.",
    ),
    _d(
        name="test_pass_rate",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=FACT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deterministic",
        minimum=0.0,
        maximum=1.0,
        description="Fraction of validation checks that passed.",
    ),
    _d(
        name="rubric_score",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="llm_judge",
        minimum=0.0,
        maximum=10.0,
        description=(
            "The LLM judge's level on the case's own rubric (0-10). An opinion "
            "on the record so it can be compared across arms and caught when "
            "it disagrees with itself — never the success metric. Absent when "
            "a human scored the run: that is a different kind of judgement."
        ),
    ),
    _d(
        name="expected_files_modified",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deterministic",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Fraction of the case's expected files the diff actually touched. "
            "Observation, not fact: it is true of the recorded diff, and a "
            "correct solution may legitimately land elsewhere."
        ),
    ),
    _d(
        name="forbidden_files_modified",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="deterministic",
        minimum=0.0,
        description=(
            "Count of forbidden paths the diff touched. A solution that edits "
            "one is wrong even when the tests pass."
        ),
    ),
]

# --- Level 2: efficiency ---------------------------------------------------
_EFFICIENCY = [
    _d(
        name="total_tokens",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Uncached input plus output tokens across the run. Excludes the "
            "prompt cache, which is where a harness is actually paid for — "
            "read `billed_tokens` for that."
        ),
    ),
    _d(
        name="billed_tokens",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Every token the provider billed: input, output, and the prompt "
            "cache written and re-read on each turn. The number that moves "
            "when a harness puts 25 skills in the system prompt."
        ),
    ),
    _d(
        name="cache_read_tokens",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description="Prompt-cache tokens re-read across the run's turns.",
    ),
    _d(
        name="output_tokens",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Tokens the model generated across the run, reasoning included. "
            "On a self-hosted model this is what costs time: decode is the "
            "slow half of inference."
        ),
    ),
    _d(
        name="cost_usd",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Reported spend for the run. 0 for a self-hosted model on opencode "
            "— measured as free, not unknown."
        ),
    ),
    _d(
        name="agent_steps",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Harness restarts, not work: one invocation that ran fifty turns "
            "counts 1. Read it with agent_turns and the run's stop_reason — "
            "alone it reads a stop-criterion difference as an agent one."
        ),
    ),
    _d(
        name="agent_turns",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description="Assistant turns across every invocation — work actually done.",
    ),
    _d(
        name="agent_tool_calls",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description="Tool invocations across every step of the run.",
    ),
    _d(
        name="wall_time_minutes",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Wall-clock time of every harness invocation, summed (the step "
            "clock). The same clock for every harness and arm; provisioning "
            "and scoring are outside it."
        ),
    ),
    _d(
        name="model_minutes",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Minutes the model spent answering, summed over its turns "
            "(opencode). The gap to wall_time_minutes is tool execution and "
            "harness overhead."
        ),
    ),
    _d(
        name="diff_lines_changed",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=NEUTRAL,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description=(
            "Lines added plus removed. Neutral on purpose — neither a large nor "
            "a small diff is good in itself."
        ),
    ),
    _d(
        name="files_changed",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=NEUTRAL,
        version="v1",
        source="efficiency",
        minimum=0.0,
        description="Files the diff touched.",
    ),
]

# --- Level 2b: per-step reward (RL-inspired, ADR-0076) ---------------------
# The case's own checks run on the workspace after every step, so each step is
# credited with the progress it made. Facts, not judgements: the same commands
# as `test_pass_rate`, pointed at a snapshot. Absent — never zero — for a run
# that left no step snapshots.
_STEP_REWARD = [
    _d(
        name="steps_to_resolve",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="step_reward",
        minimum=1.0,
        description=(
            "First step after which every conclusive check passed. Absent when "
            "the run never resolved. Unlike agent_steps it does not count the "
            "steps the loop kept running after the work was done."
        ),
    ),
    _d(
        name="cost_to_resolve_usd",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="step_reward",
        minimum=0.0,
        description=(
            "Spend up to and including the resolving step. The price of the "
            "solution, separated from the price of the stop criterion — two "
            "arms that both pass every check still differ here."
        ),
    ),
    _d(
        name="post_resolution_cost_share",
        category=EFFICIENCY,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="step_reward",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Share of the run's spend that came after it was already resolved: "
            "re-reading, extra tests, scope creep. Falls back to billed tokens "
            "on a self-hosted model, where the spend is measured as free."
        ),
    ),
    _d(
        name="progress_auc",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=FACT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="step_reward",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Mean progress over the run's steps (area under the progress "
            "curve). 1.0 is solved in step one and kept; arriving later or "
            "breaking passing checks lowers it. The RL return without discount."
        ),
    ),
    _d(
        name="step_regressions",
        category=OUTCOME,
        kind=NUMERIC,
        provenance=FACT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="step_reward",
        minimum=0.0,
        description=(
            "Steps after which fewer checks passed than before — work that "
            "broke work. Invisible to every end-of-run score."
        ),
    ),
]

# --- Level 4: harness behaviour (the LLM audit, made structural) ----------
_BEHAVIORAL = [
    _d(
        name="skill_invoked",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="skill_usage",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Share of the skills this run is about (staged, asked for, or "
            "ablated elsewhere in the experiment) that the agent actually "
            "opened — a Skill call or a read of their SKILL.md in the step "
            "record. Available is not invoked: a with-skills arm at 0 measured "
            "the menu, not the skill."
        ),
    ),
    _d(
        name="skill_invocations",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=NEUTRAL,
        version="v1",
        source="skill_usage",
        minimum=0.0,
        description=(
            "How many times the skills under test were invoked in this run. "
            "Neutral: once is enough, many may mean the agent kept re-reading."
        ),
    ),
    _d(
        name="skill_adherence",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="harness_audit",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Share of available harness items the auditor judged genuinely used. "
            "A judgement: the auditor reads logs, it does not observe invocation."
        ),
    ),
    _d(
        name="harness_items_useful",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="harness_audit",
        minimum=0.0,
        description="Harness items the auditor judged useful in this run.",
    ),
    _d(
        name="harness_items_redundant",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=LOWER_IS_BETTER,
        version="v1",
        source="harness_audit",
        minimum=0.0,
        description="Harness items the auditor judged redundant or unused.",
    ),
    _d(
        name="governance_read_coverage",
        category=BEHAVIORAL,
        kind=NUMERIC,
        provenance=OBSERVATION,
        direction=HIGHER_IS_BETTER,
        # v2 changed the denominator: it is now the governance surface the run
        # actually had (ADR-0031), not a fixed list of the lab's own documents.
        # A `native` run is no longer marked down for not reading files that
        # were never in its workspace, so v1 and v2 values are not comparable.
        version="v2",
        source="attribution",
        minimum=0.0,
        maximum=1.0,
        description=(
            "Share of the run's own governance documents the agent actually "
            "opened, parsed from step logs. The denominator is that run's "
            "resolved governance surface. A lower bound: context-injected "
            "content leaves no read event."
        ),
    ),
]

# --- Level 3: trajectory (an optional adapter, never the platform) --------
# Declared here even though the adapter may not be installed: a definition is a
# contract about what the name means, and it must not appear or vanish depending
# on which extras a deployment happens to have.
_TRAJECTORY = [
    _d(
        name="task_completion",
        category=TRAJECTORY,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deepeval",
        minimum=0.0,
        maximum=1.0,
        description="Whether the agent's trajectory accomplished the stated task.",
    ),
    _d(
        name="step_efficiency",
        category=TRAJECTORY,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deepeval",
        minimum=0.0,
        maximum=1.0,
        description="Whether the steps taken were necessary to reach the outcome.",
    ),
    _d(
        name="tool_correctness",
        category=TRAJECTORY,
        kind=NUMERIC,
        provenance=JUDGEMENT,
        direction=HIGHER_IS_BETTER,
        version="v1",
        source="deepeval",
        minimum=0.0,
        maximum=1.0,
        description="Whether the tools called were the right ones for each step.",
    ),
]

REGISTRY: dict[str, ScoreDefinition] = {
    definition.name: definition
    for definition in (
        *_OUTCOME,
        *_EFFICIENCY,
        *_STEP_REWARD,
        *_TRAJECTORY,
        *_BEHAVIORAL,
    )
}


def get_definition(name: str) -> ScoreDefinition:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown score '{name}'. Every score must be declared in the "
            "registry before it can be recorded — an undeclared metric has no "
            "direction, no provenance and no version, so nothing downstream can "
            "read it correctly."
        ) from None


def definitions_by_category() -> dict[str, list[ScoreDefinition]]:
    grouped: dict[str, list[ScoreDefinition]] = {}
    for definition in REGISTRY.values():
        grouped.setdefault(definition.category, []).append(definition)
    for items in grouped.values():
        items.sort(key=lambda item: item.name)
    return grouped
