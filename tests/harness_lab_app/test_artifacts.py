from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.artifact_service import ArtifactService


def make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_index_run_artifacts(tmp_path: Path):
    run_dir = tmp_path / "data" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text('{"run_id":"run-1"}')
    (run_dir / "diff.patch").write_text("diff --git a/main.py b/main.py")

    with make_session() as session:
        service = ArtifactService(session, data_runs_dir=tmp_path / "data" / "runs")
        artifacts = service.index_run("run-1")

    assert [artifact.name for artifact in artifacts] == ["diff.patch", "metadata.json"]


def test_index_run_skips_workspace_and_sandbox_trees(tmp_path: Path):
    run_dir = tmp_path / "data" / "runs" / "run-2"
    (run_dir / "steps").mkdir(parents=True)
    (run_dir / "metadata.json").write_text('{"run_id":"run-2"}')
    (run_dir / "steps" / "step-01.md").write_text("step one")
    # The agent's checkout and the sandbox copy of it: tens of thousands of
    # files that no consumer of the index ever reads.
    (run_dir / "workspace" / "src" / "deep").mkdir(parents=True)
    (run_dir / "workspace" / "src" / "deep" / "module.py").write_text("x = 1")
    (run_dir / "sandbox-desktop" / "src").mkdir(parents=True)
    (run_dir / "sandbox-desktop" / "src" / "module.py").write_text("x = 1")

    with make_session() as session:
        service = ArtifactService(session, data_runs_dir=tmp_path / "data" / "runs")
        artifacts = service.index_run("run-2")

    assert [artifact.name for artifact in artifacts] == [
        "metadata.json",
        str(Path("steps") / "step-01.md"),
    ]
