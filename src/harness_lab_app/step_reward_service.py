"""Read per-step rewards out of the store: one run's curve, or the whole corpus."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from harness_lab_app.models import Validation
from harness_lab_app.step_reward import corpus_credit


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
