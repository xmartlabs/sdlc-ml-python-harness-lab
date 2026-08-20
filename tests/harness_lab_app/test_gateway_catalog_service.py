from __future__ import annotations

import json

import pytest

from harness_lab_app import gateway_catalog_service as catalog_module
from harness_lab_app.config import Settings
from harness_lab_app.gateway_catalog_service import (
    GatewayCatalogError,
    GatewayCatalogService,
)
from harness_lab_app.opencode_overlay import CATALOG_FILE, build_overlay

TRACKED = {
    "npm": "@ai-sdk/openai-compatible",
    "models": {"gpt-5.5": {"name": "GPT-5.5 (curated)"}},
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"provider": {"gateway": TRACKED}})
    )
    monkeypatch.setattr(
        catalog_module, "get_settings", lambda: Settings(repo_root=tmp_path)
    )
    return tmp_path


def test_refresh_adopts_new_ids_with_the_tracked_label(repo, session):
    service = GatewayCatalogService(session)
    result = service.refresh(["gpt-5.5", "brand-new"])

    assert result["added"] == ["brand-new", "gpt-5.5"]
    assert result["gateway_reachable"] is True
    labels = {row.model_id: row.label for row in service.list_models()}
    # An id the tracked map already names keeps that human label.
    assert labels == {"gpt-5.5": "GPT-5.5 (curated)", "brand-new": "brand-new"}


def test_refresh_marks_vanished_ids_stale_without_deleting_them(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5", "gone"])
    service.update_model("gone", label="Hand-written label")

    result = service.refresh(["gpt-5.5"])

    assert result["stale"] == ["gone"]
    row = service.get_model("gone")
    assert row.live is False
    assert row.label == "Hand-written label"


def test_an_unreachable_gateway_marks_nothing_stale(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5"])

    result = service.refresh([])

    assert result == {
        "added": [],
        "stale": [],
        "total": 1,
        "gateway_reachable": False,
    }
    assert service.get_model("gpt-5.5").live is True


def test_refresh_is_idempotent(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5"])
    assert service.refresh(["gpt-5.5"])["added"] == []


def test_only_one_model_can_be_the_default(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["a", "b"])
    service.update_model("a", is_default=True)
    service.update_model("b", is_default=True)

    defaults = [row.model_id for row in service.list_models() if row.is_default]
    assert defaults == ["b"]


def test_disabled_models_stay_out_of_the_overlay(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5", "brand-new"])
    service.update_model("brand-new", enabled=False)

    assert set(service.overlay_models()) == {"gpt-5.5"}


def test_options_are_merged_into_the_overlay_entry(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["brand-new"])
    service.update_model("brand-new", label="New", options={"limit": {"context": 8}})

    assert service.overlay_models()["brand-new"] == {
        "name": "New",
        "limit": {"context": 8},
    }


def test_a_gateway_prefixed_id_resolves_to_the_bare_row(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["brand-new"])
    assert service.get_model("gateway/brand-new").model_id == "brand-new"


def test_an_unknown_model_is_rejected(repo, session):
    with pytest.raises(GatewayCatalogError):
        GatewayCatalogService(session).get_model("nope")


def test_the_catalog_file_feeds_the_overlay_end_to_end(repo, session):
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5", "brand-new"])

    written = json.loads((repo / CATALOG_FILE).read_text())
    assert set(written["models"]) == {"gpt-5.5", "brand-new"}

    env, digest = build_overlay(repo)
    assert digest is not None
    assert "brand-new" in env["OPENCODE_CONFIG_CONTENT"]


def test_a_catalog_equal_to_the_tracked_map_yields_no_overlay(repo, session):
    """The no-op case: labels identical to opencode.json must not flip an arm."""
    service = GatewayCatalogService(session)
    service.refresh(["gpt-5.5"])

    assert build_overlay(repo) == ({}, None)
