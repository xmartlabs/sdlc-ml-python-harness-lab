"""Curated catalog of gateway models, refreshed from the live LiteLLM list.

The gateway is the *discovery* source and this table is the *routing* source.
``refresh`` reconciles the two: new ids are adopted (seeded from the tracked
``opencode.json`` label when there is one), ids that vanished are marked
``live=False`` rather than deleted, so a provider that is merely down does not
cost the user the label they curated.

Every write re-materializes ``data/app/gateway-models.json``, the gitignored
file :mod:`harness_lab_app.opencode_overlay` turns into opencode configuration
at run launch. That file — not this table — is what a run reads, so the runner
needs no database access.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlmodel import Session, select

from harness_lab_app.config import get_settings
from harness_lab_app.gateway_models import (
    GATEWAY_PREFIX,
    classify_backend,
    read_opencode_gateway_models,
)
from harness_lab_app.models import GatewayModel, utc_now
from harness_lab_app.opencode_overlay import CATALOG_FILE

if TYPE_CHECKING:
    from pathlib import Path


class GatewayCatalogError(ValueError):
    """Invalid catalog operation (unknown model, bad id)."""


class GatewayCatalogService:
    def __init__(self, session: Session):
        self.session = session
        self.repo_root: Path = get_settings().repo_root

    # -- queries ------------------------------------------------------------

    def list_models(self) -> list[GatewayModel]:
        """Catalog rows in the menu's own order: LM Studio → Cloud → Ollama.

        Sorting happens in Python rather than SQL because the backend of an id
        is derived (:func:`classify_backend`), not stored. ``model_id`` breaks
        ties so the order stays stable between calls.
        """
        rows = self.session.exec(select(GatewayModel).order_by(GatewayModel.model_id))
        return sorted(
            rows, key=lambda row: (classify_backend(row.model_id)[0], row.model_id)
        )

    def get_model(self, model_id: str) -> GatewayModel:
        bare = normalize_id(model_id)
        row = self.session.exec(
            select(GatewayModel).where(GatewayModel.model_id == bare)
        ).first()
        if row is None:
            raise GatewayCatalogError(f"Model not in the catalog: {bare}")
        return row

    # -- mutations ----------------------------------------------------------

    def refresh(self, live_ids: list[str]) -> dict:
        """Reconcile the catalog with the ids the gateway currently serves.

        An empty ``live_ids`` means the gateway was unreachable, not that every
        model vanished; nothing is marked stale in that case.
        """
        rows = {row.model_id: row for row in self.list_models()}
        tracked = read_opencode_gateway_models(self.repo_root)
        now = utc_now()
        added: list[str] = []

        for bare in live_ids:
            row = rows.get(bare)
            if row is None:
                entry = tracked.get(bare) or {}
                row = GatewayModel(
                    model_id=bare,
                    label=str(entry.get("name") or bare),
                    first_seen_at=now,
                )
                added.append(bare)
                rows[bare] = row
            row.live = True
            row.last_seen_at = now
            self.session.add(row)

        stale: list[str] = []
        if live_ids:
            serving = set(live_ids)
            for bare, row in rows.items():
                if bare not in serving and row.live:
                    row.live = False
                    stale.append(bare)
                    self.session.add(row)

        self.session.commit()
        self.write_overlay_file()
        return {
            "added": sorted(added),
            "stale": sorted(stale),
            "total": len(rows),
            "gateway_reachable": bool(live_ids),
        }

    def update_model(
        self,
        model_id: str,
        *,
        label: str | None = None,
        enabled: bool | None = None,
        is_default: bool | None = None,
        options: dict | None = None,
    ) -> GatewayModel:
        row = self.get_model(model_id)
        if label is not None:
            row.label = label.strip()
        if enabled is not None:
            row.enabled = enabled
        if options is not None:
            row.options_json = options
        if is_default:
            # At most one default: clearing the others here keeps the run form
            # from having to break the tie.
            for other in self.list_models():
                if other.model_id != row.model_id and other.is_default:
                    other.is_default = False
                    self.session.add(other)
            row.is_default = True
        elif is_default is False:
            row.is_default = False

        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        self.write_overlay_file()
        return row

    # -- overlay materialization -------------------------------------------

    def overlay_models(self) -> dict[str, dict]:
        """The enabled slice of the catalog, shaped as opencode model entries."""
        entries: dict[str, dict] = {}
        for row in self.list_models():
            if not row.enabled:
                continue
            entry: dict = {"name": row.label or row.model_id}
            entry.update(row.options_json or {})
            entries[row.model_id] = entry
        return dict(sorted(entries.items()))

    def write_overlay_file(self) -> Path:
        path = self.repo_root / CATALOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": utc_now().isoformat(),
            "models": self.overlay_models(),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


def normalize_id(model_id: str) -> str:
    """Accept either ``gateway/<id>`` or the bare id the catalog is keyed by."""
    bare = model_id.strip()
    if bare.startswith(GATEWAY_PREFIX):
        bare = bare[len(GATEWAY_PREFIX) :]
    if not bare:
        raise GatewayCatalogError("Model id must not be empty.")
    return bare
