"""Cross-run worklist of improvement proposals and combined group issues.

``SavedProposal`` rows are snapshots of ``data/runs/<id>/improvements.json``
entries (written by ``scripts/lab_improve.py``); the file stays the source of
truth for synthesis, the DB is the UI index the Improvements page selects
from. ``GroupIssue`` records each combined sanitized issue generated from a
selection, so proposals can be marked ``issued`` and traced back.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.models import GroupIssue, SavedProposal, utc_now

if TYPE_CHECKING:
    from pathlib import Path

# Proposal fields refreshed on re-save (status/group_issue_id are preserved —
# an already-issued proposal stays issued).
_SNAPSHOT_FIELDS = ("body", "kind", "target", "repo_hint", "repo")


class ImprovementError(ValueError):
    """Invalid saved-proposal operation (missing row, bad selection)."""


class ImprovementService:
    def __init__(self, session: Session):
        self.session = session
        self.runs_dir: Path = get_settings().data_runs_dir

    # -- saving ---------------------------------------------------------------

    def save_from_run(self, run_id: str) -> dict:
        """Upsert the run's improvements.json proposals into the worklist.

        Idempotent on (run_id, title): existing rows are refreshed in place,
        new ones inserted. Raises FileNotFoundError when the run has no
        improvements.json yet (the API maps it to a 404).
        """
        path = self.runs_dir / run_id / "improvements.json"
        doc = json.loads(path.read_text())
        context = doc.get("context") or {}
        generator_model = doc.get("generator_model") or ""

        existing = {
            row.title: row
            for row in self.session.exec(
                select(SavedProposal).where(SavedProposal.run_id == run_id)
            )
        }
        saved = updated = 0
        for proposal in doc.get("proposals") or []:
            title = (proposal.get("title") or "").strip()
            if not title:
                continue
            row = existing.get(title)
            if row is None:
                row = SavedProposal(
                    run_id=run_id,
                    title=title,
                    case=context.get("case") or "",
                    harness=context.get("harness") or "",
                    executor_model=context.get("executor_model") or "",
                    generator_model=generator_model,
                )
                saved += 1
            else:
                updated += 1
            for field in _SNAPSHOT_FIELDS:
                setattr(row, field, proposal.get(field) or "")
            row.created_issue_url = proposal.get("created_issue_url") or None
            row.updated_at = utc_now()
            self.session.add(row)
        self.session.commit()
        total = len(
            self.session.exec(
                select(SavedProposal).where(SavedProposal.run_id == run_id)
            ).all()
        )
        return {"run_id": run_id, "saved": saved, "updated": updated, "total": total}

    # -- queries --------------------------------------------------------------

    def list_proposals(
        self,
        repo: str | None = None,
        kind: str | None = None,
        case: str | None = None,
        status: str | None = None,
    ) -> list[SavedProposal]:
        statement = select(SavedProposal)
        if repo:
            statement = statement.where(SavedProposal.repo == repo)
        if kind:
            statement = statement.where(SavedProposal.kind == kind)
        if case:
            statement = statement.where(SavedProposal.case == case)
        if status:
            statement = statement.where(SavedProposal.status == status)
        statement = statement.order_by(SavedProposal.created_at.desc())  # type: ignore[attr-defined]
        return list(self.session.exec(statement))

    def get_proposals(self, ids: list[int]) -> list[SavedProposal]:
        rows = list(
            self.session.exec(select(SavedProposal).where(SavedProposal.id.in_(ids)))  # type: ignore[union-attr]
        )
        missing = set(ids) - {row.id for row in rows}
        if missing:
            raise ImprovementError(
                f"Unknown proposal id(s): {', '.join(str(i) for i in sorted(missing))}"
            )
        by_id = {row.id: row for row in rows}
        return [by_id[i] for i in ids]

    def list_group_issues(self) -> list[GroupIssue]:
        statement = select(GroupIssue).order_by(GroupIssue.created_at.desc())  # type: ignore[attr-defined]
        return list(self.session.exec(statement))

    # -- lifecycle ------------------------------------------------------------

    def record_group_issue(
        self,
        proposals: list[SavedProposal],
        *,
        repository: str,
        title: str,
        markdown: str,
        form_json: dict,
        model: str,
    ) -> GroupIssue:
        issue = GroupIssue(
            repository=repository,
            title=title,
            markdown=markdown,
            form_json=form_json,
            proposal_ids=[p.id for p in proposals if p.id is not None],
            source_run_ids=sorted({p.run_id for p in proposals}),
            model=model,
        )
        self.session.add(issue)
        self.session.flush()  # allocate issue.id before linking proposals
        for proposal in proposals:
            proposal.status = "issued"
            proposal.group_issue_id = issue.id
            proposal.updated_at = utc_now()
            self.session.add(proposal)
        self.session.commit()
        self.session.refresh(issue)
        return issue

    def delete_proposal(self, proposal_id: int) -> None:
        row = self.session.get(SavedProposal, proposal_id)
        if row is None:
            raise ImprovementError(f"Unknown proposal id: {proposal_id}")
        self.session.delete(row)
        self.session.commit()
