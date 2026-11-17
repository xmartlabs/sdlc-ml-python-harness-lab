"""Read per-step rewards out of the store: one run's curve, or the whole corpus."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from harness_lab_app.models import Run, RunConfig, Validation
from harness_lab_app.step_reward import corpus_credit, grouped_credit

# The dimensions a census can be sliced by, and the RunConfig column each reads.
CENSUS_DIMENSIONS: dict[str, str] = {
    "model": "executor_model",
    "case": "case_id",
    "harness": "harness",
}
UNKNOWN_GROUP = "(unknown)"


class StepRewardService:
    def __init__(self, session: Session):
        self.session = session

    def trajectory(self, run_id: str) -> dict[str, Any] | None:
        """The run's per-step reward, or None when it left no step record."""
        validation = self.session.exec(
            select(Validation).where(Validation.run_id == run_id)
        ).first()
        if validation is None:
            return None
        per_step = (validation.objective or {}).get("per_step")
        return per_step if isinstance(per_step, dict) else None

    def corpus(self, run_ids: list[str] | None = None) -> dict[str, Any]:
        """Credit per skill and governance doc across every measured run.

        Skills in the governed catalog that no run ever opened are listed with
        zero runs — they are the first pruning candidates.
        """
        from harness_lab_app.skills_catalog import known_skill_names

        statement = select(Validation)
        if run_ids is not None:
            statement = statement.where(Validation.run_id.in_(run_ids))  # type: ignore[attr-defined]
        trajectories = [
            (row.objective or {}).get("per_step")
            for row in self.session.exec(statement).all()
        ]
        measured = [
            t for t in trajectories if isinstance(t, dict) and t.get("measured")
        ]
        available = [f"skill:{name}" for name in sorted(known_skill_names())]
        return {
            "runs_measured": len(measured),
            "items": corpus_credit(measured, available=available),
            "note": (
                "Co-occurrence, not cause: reward earned by the steps that "
                "consulted each item. Ablate an item (make prune) to learn "
                "whether it can be removed."
            ),
        }

    def census(self, by: str = "model") -> dict[str, Any]:
        """Skill usage per model, case or harness CLI, beside each skill's size.

        The first step of harness pruning (ADR-0085): which skills are never
        opened, for whom, and what they cost to carry. Co-occurrence still — it
        orders what to ablate, it never removes anything.
        """
        from harness_lab_app.skills_catalog import skill_footprints

        if by not in CENSUS_DIMENSIONS:
            raise ValueError(
                f"census dimension must be one of {sorted(CENSUS_DIMENSIONS)}"
            )
        column = CENSUS_DIMENSIONS[by]
        rows = self.session.exec(
            select(Validation, RunConfig)
            .join(Run, Run.run_id == Validation.run_id)  # type: ignore[arg-type]
            .join(RunConfig, RunConfig.id == Run.run_config_id)  # type: ignore[arg-type]
        ).all()
        keyed = []
        for validation, config in rows:
            trajectory = (validation.objective or {}).get("per_step")
            if not isinstance(trajectory, dict):
                continue
            keyed.append((str(getattr(config, column) or UNKNOWN_GROUP), trajectory))
        footprints = skill_footprints()
        available = [f"skill:{name}" for name in sorted(footprints)]
        return {
            "by": by,
            "groups": grouped_credit(keyed, available=available),
            "footprints": footprints,
            "note": (
                "Co-occurrence, not cause. A skill never opened by one model may "
                "still matter to another; ablate per tier (make prune) before "
                "removing it from that tier's profile."
            ),
        }
