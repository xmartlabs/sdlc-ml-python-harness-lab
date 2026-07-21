from __future__ import annotations

import json
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from harness_lab_app import attribution_service as svc_module
from harness_lab_app.attribution_service import AttributionService


def make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_index_run_persists_attribution(tmp_path, monkeypatch):
    runs_dir = tmp_path / "data" / "runs"
    monkeypatch.setattr(
        svc_module, "get_settings", lambda: SimpleNamespace(data_runs_dir=runs_dir)
    )
    run_dir = runs_dir / "r1"
    steps = run_dir / "steps"
    steps.mkdir(parents=True)
    (steps / "step-1.log").write_text(
        "\n".join(
            [
                "message=evaluated permission=read pattern=.github/standards.md",
                "message=evaluated permission=edit pattern=workspace/r1/main.py",
                'message=evaluated permission=bash pattern="python main.py"',
            ]
        )
    )
    (run_dir / "metadata.json").write_text(
        json.dumps({"skills_bundle": ["writing-plans", "brainstorming"]})
    )

    engine = make_engine()
    with Session(engine) as session:
        attribution = AttributionService(session).index_run("r1")

        assert attribution.tools["read"] == 1
        assert attribution.tools["edit"] == 1
        assert attribution.tools["bash"] == 1
        assert (
            ".github/standards.md" in attribution.harness_usage["governance_docs_read"]
        )
        assert attribution.harness_usage["skills_available"] == [
            "brainstorming",
            "writing-plans",
        ]
        assert attribution.steps_parsed == 1
        assert "workspace/r1/main.py" in attribution.files["edited"]

        # Re-indexing updates the same row (idempotent upsert).
        again = AttributionService(session).index_run("r1")
        assert again.id == attribution.id


def test_get_attribution_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path),
    )
    engine = make_engine()
    with Session(engine) as session:
        assert AttributionService(session).get_attribution("nope") is None
