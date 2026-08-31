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


def make_lab_root(tmp_path):
    """A stand-in for the lab's own repository governance."""
    lab = tmp_path / "lab"
    (lab / ".github").mkdir(parents=True)
    (lab / ".github" / "standards.md").write_text("lab standards\n")
    (lab / ".github" / "architecture.md").write_text("lab architecture\n")
    (lab / "CLAUDE.md").write_text("lab rules\n")
    return lab


def patch_settings(monkeypatch, runs_dir, lab_root):
    monkeypatch.setattr(
        svc_module,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs_dir, repo_root=lab_root),
    )


def test_index_run_persists_attribution(tmp_path, monkeypatch):
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
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
    patch_settings(monkeypatch, tmp_path, make_lab_root(tmp_path))
    engine = make_engine()
    with Session(engine) as session:
        assert AttributionService(session).get_attribution("nope") is None


def write_run(runs_dir, run_id, *, metadata, log_lines, workspace=None):
    run_dir = runs_dir / run_id
    steps = run_dir / "steps"
    steps.mkdir(parents=True)
    (steps / "step-1.log").write_text("\n".join(log_lines))
    (run_dir / "metadata.json").write_text(json.dumps(metadata))
    if workspace:
        for relative, body in workspace.items():
            path = run_dir / "workspace" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    return run_dir


def test_native_run_is_measured_against_the_target_governance(tmp_path, monkeypatch):
    """A native run never received the lab's documents (ADR-0031).

    Its read coverage must be a ratio over the *target's* rulebook. Scoring it
    against `.github/standards.md` would mark a client's repository down for not
    following conventions the lab never gave it.
    """
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
    write_run(
        runs_dir,
        "native-1",
        metadata={"harness": "claude", "harness_source": "native"},
        log_lines=["message=evaluated permission=read pattern=AGENTS.md"],
        workspace={"AGENTS.md": "target rules\n", "docs/adr/0001-x.md": "adr\n"},
    )

    engine = make_engine()
    with Session(engine) as session:
        usage = AttributionService(session).index_run("native-1").harness_usage

    available = usage["governance_docs_available"]
    assert "AGENTS.md" in available
    # The lab's own governance is absent from the denominator entirely.
    assert ".github/standards.md" not in available
    assert ".github/architecture.md" not in available
    # ADRs are catalogue, not a standing rulebook, so they never inflate it.
    assert "docs/adr/0001-x.md" not in available
    assert usage["governance_docs_read"] == ["AGENTS.md"]
    assert usage["governance_coverage"] == "1/1"
    assert usage["governance_source"] == "target_workspace"
    assert usage["harness_source"] == "native"
    assert usage["governance_fingerprint"]
    assert "governance_warning" not in usage


def test_injected_run_is_measured_against_the_injected_harness(tmp_path, monkeypatch):
    """`injected` is the same workspace *after* the lab projected its harness in.

    The agent saw both the target's file and the lab's, so both belong in the
    denominator — read from the archived workspace, not from the lab's checkout.
    """
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
    write_run(
        runs_dir,
        "injected-1",
        metadata={"harness": "claude", "harness_source": "injected"},
        log_lines=[
            "message=evaluated permission=read pattern=CLAUDE.md",
            "message=evaluated permission=read pattern=.github/standards.md",
        ],
        workspace={
            "AGENTS.md": "target rules\n",
            "CLAUDE.md": "injected rules\n",
            ".github/standards.md": "injected standards\n",
        },
    )

    engine = make_engine()
    with Session(engine) as session:
        usage = AttributionService(session).index_run("injected-1").harness_usage

    assert usage["governance_source"] == "target_workspace"
    assert usage["harness_source"] == "injected"
    assert sorted(usage["governance_docs_available"]) == [
        ".github/standards.md",
        "AGENTS.md",
        "CLAUDE.md",
    ]
    assert usage["governance_coverage"] == "2/3"


def test_two_conditions_of_one_run_pair_get_different_fingerprints(
    tmp_path, monkeypatch
):
    """The fingerprint is over content, so it separates conditions."""
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
    write_run(
        runs_dir,
        "a",
        metadata={"harness": "claude", "harness_source": "native"},
        log_lines=[],
        workspace={"CLAUDE.md": "target rules\n"},
    )
    write_run(
        runs_dir,
        "b",
        metadata={"harness": "claude", "harness_source": "injected"},
        log_lines=[],
        workspace={"CLAUDE.md": "lab rules, projected in\n"},
    )

    engine = make_engine()
    with Session(engine) as session:
        service = AttributionService(session)
        first = service.index_run("a").harness_usage["governance_fingerprint"]
        second = service.index_run("b").harness_usage["governance_fingerprint"]
    assert first and second and first != second


def test_missing_workspace_degrades_with_a_warning(tmp_path, monkeypatch):
    """A native run whose workspace was cleaned up is a degraded record.

    It falls back to the lab's governance because there is nothing else to read,
    but it must say so rather than present the fallback as the truth.
    """
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
    write_run(
        runs_dir,
        "orphan",
        metadata={"harness": "claude", "harness_source": "native"},
        log_lines=["message=evaluated permission=read pattern=CLAUDE.md"],
    )

    engine = make_engine()
    with Session(engine) as session:
        usage = AttributionService(session).index_run("orphan").harness_usage

    assert usage["governance_source"] == "lab_root"
    assert "archived" in usage["governance_warning"]


def test_lab_root_run_uses_the_labs_own_governance(tmp_path, monkeypatch):
    runs_dir = tmp_path / "data" / "runs"
    patch_settings(monkeypatch, runs_dir, make_lab_root(tmp_path))
    write_run(
        runs_dir,
        "local-1",
        metadata={"harness": "claude", "harness_source": "lab_root"},
        log_lines=["message=evaluated permission=read pattern=CLAUDE.md"],
    )

    engine = make_engine()
    with Session(engine) as session:
        usage = AttributionService(session).index_run("local-1").harness_usage

    assert usage["governance_source"] == "lab_root"
    assert usage["harness_source"] == "lab_root"
    assert sorted(usage["governance_docs_available"]) == [
        ".github/architecture.md",
        ".github/standards.md",
        "CLAUDE.md",
    ]
    assert usage["governance_coverage"] == "1/3"
    assert "governance_warning" not in usage
