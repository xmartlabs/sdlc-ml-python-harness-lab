"""Machines and saved profiles for *Tune your local model* (spec "Architecture").

``ProfileService`` is the only writer of ``LocalMachine`` / ``TuningProfile``:
plain CRUD plus the two rules the spec calls out — marking a profile
*recommended* clears the flag on its siblings (same machine + model), and
``compare`` refuses across machines or models because speed is a property of
the hardware, not the model (see the ``TuningProfile`` docstring in
``models.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from harness_lab_app.models import LocalMachine, TuningProfile

if TYPE_CHECKING:
    from sqlmodel import Session


class ProfileService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Machines -----------------------------------------------------------

    def list_machines(self) -> list[LocalMachine]:
        return list(self.session.exec(select(LocalMachine)).all())

    def create_machine(self, data: dict) -> LocalMachine:
        machine = LocalMachine(**data)
        self.session.add(machine)
        self.session.commit()
        self.session.refresh(machine)
        return machine

    def get_machine(self, machine_id: int) -> LocalMachine | None:
        return self.session.get(LocalMachine, machine_id)

    def update_machine(self, machine_id: int, data: dict) -> LocalMachine | None:
        machine = self.session.get(LocalMachine, machine_id)
        if machine is None:
            return None
        for key, value in data.items():
            if hasattr(machine, key):
                setattr(machine, key, value)
        self.session.add(machine)
        self.session.commit()
        self.session.refresh(machine)
        return machine

    def delete_machine(self, machine_id: int) -> bool:
        machine = self.session.get(LocalMachine, machine_id)
        if machine is None:
            return False
        self.session.delete(machine)
        self.session.commit()
        return True

    # --- Profiles -------------------------------------------------------------

    def list_profiles(
        self, machine_id: int | None = None, model_id: str | None = None
    ) -> list[TuningProfile]:
        statement = select(TuningProfile)
        if machine_id is not None:
            statement = statement.where(TuningProfile.machine_id == machine_id)
        if model_id is not None:
            statement = statement.where(TuningProfile.model_id == model_id)
        return list(self.session.exec(statement).all())

    def create_profile(self, data: dict) -> TuningProfile:
        profile = TuningProfile(**data)
        self.session.add(profile)
        self.session.commit()
        self.session.refresh(profile)
        if profile.recommended:
            self._clear_other_recommended(profile)
        return profile

    def get_profile(self, profile_id: int) -> TuningProfile | None:
        return self.session.get(TuningProfile, profile_id)

    def update_profile(
        self,
        profile_id: int,
        *,
        label: str | None = None,
        recommended: bool | None = None,
        observed_memory_gb: float | None = None,
    ) -> TuningProfile | None:
        profile = self.session.get(TuningProfile, profile_id)
        if profile is None:
            return None
        if label is not None:
            profile.label = label
        if observed_memory_gb is not None:
            profile.observed_memory_gb = observed_memory_gb
        if recommended is not None:
            profile.recommended = recommended
        self.session.add(profile)
        self.session.commit()
        self.session.refresh(profile)
        if recommended:
            self._clear_other_recommended(profile)
        return profile

    def _clear_other_recommended(self, profile: TuningProfile) -> None:
        """Marking a profile recommended clears the flag on its siblings.

        Siblings are the other profiles of the *same* machine and model only:
        "recommended" is a statement about a configuration for one model on
        one machine, never a lab-wide ranking.
        """
        siblings = self.session.exec(
            select(TuningProfile)
            .where(TuningProfile.machine_id == profile.machine_id)
            .where(TuningProfile.model_id == profile.model_id)
            .where(TuningProfile.id != profile.id)
        ).all()
        changed = False
        for sibling in siblings:
            if sibling.recommended:
                sibling.recommended = False
                self.session.add(sibling)
                changed = True
        if changed:
            self.session.commit()

    def delete_profile(self, profile_id: int) -> bool:
        profile = self.session.get(TuningProfile, profile_id)
        if profile is None:
            return False
        self.session.delete(profile)
        self.session.commit()
        return True

    def compare(self, a_id: int, b_id: int) -> dict:
        a = self.session.get(TuningProfile, a_id)
        b = self.session.get(TuningProfile, b_id)
        if a is None or b is None:
            return {
                "a": None,
                "b": None,
                "comparable": False,
                "reason": "One of the profiles does not exist.",
            }
        if a.machine_id != b.machine_id:
            return {
                "a": a,
                "b": b,
                "comparable": False,
                "reason": (
                    "These profiles were measured on different machines. Speed "
                    "and memory fit are properties of the hardware, so they "
                    "cannot be compared across machines."
                ),
            }
        if a.model_id != b.model_id:
            return {
                "a": a,
                "b": b,
                "comparable": False,
                "reason": "These profiles are for different models and cannot be compared.",
            }
        from harness_lab_app.local_tuning.history import compare_metrics, summarize

        comparison = compare_metrics(
            summarize(a.results or [], a.served_config, a.estimate),
            summarize(b.results or [], b.served_config, b.estimate),
        )
        return {
            "a": a,
            "b": b,
            "comparable": True,
            "reason": comparison["summary"],
            "comparison": comparison,
        }

    def history(self, machine_id: int, model_id: str | None = None) -> list[dict]:
        """Every saved profile of a machine, newest first, with its summary numbers."""
        from harness_lab_app.local_tuning.history import summarize

        rows: list[dict] = []
        for profile in self.list_profiles(machine_id=machine_id, model_id=model_id):
            rows.append(
                {
                    "profile": profile,
                    "summary": summarize(
                        profile.results or [], profile.served_config, profile.estimate
                    ),
                }
            )
        rows.sort(key=lambda row: row["profile"].created_at or "", reverse=True)
        return rows
