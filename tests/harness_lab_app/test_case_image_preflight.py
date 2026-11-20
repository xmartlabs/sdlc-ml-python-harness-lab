"""A case that brings its own runner image is refused before it is queued when
that image is not built (ADR-0078).

chess3d-v1-K-015 (2026-11-20): the shared runner image was there, the Godot one
was not — a stack reset had dropped it — so the preflight passed, the run was
queued, and it died in the executor a second later with "was not found
locally". The worker now reports which case images it has, and the preflight
reads that list the way it already reads the shared image.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from harness_lab_app.models import Case, CaseType
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import RunConfigCreate

GODOT = "harness-lab-runner-godot:latest"


def _service(
    monkeypatch, session: Session, case_images: list[str] | None
) -> RunService:
    import harness_lab_app.run_service as rs

    session.add(
        Case(
            case_id="chess3d-v1",
            name="chess3d-v1",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "d"},
            metadata_json={"runner_image": GODOT},
        )
    )
    session.commit()
    facts = {
        "backend": "docker",
        "docker_ok": True,
        "runner_image": "harness-lab-runner:latest",
        "runner_image_present": True,
    }
    if case_images is not None:
        facts["case_images_present"] = case_images
    monkeypatch.setattr(rs.get_settings(), "execution_backend", "docker", raising=False)
    monkeypatch.setattr(
        "harness_lab_app.health_service.worker_facts", lambda **_k: facts
    )
    return RunService(session)


def _config() -> RunConfigCreate:
    return RunConfigCreate(
        case_id="chess3d-v1",
        prompt_variant="default",
        harness="claude",
        executor_model="claude-opus-5-5",
    )


def test_a_case_whose_own_image_is_missing_is_refused_with_the_command(
    monkeypatch, session
):
    service = _service(monkeypatch, session, case_images=[])
    with pytest.raises(RunPreflightError) as refused:
        service._check_runner_image(_config())
    message = str(refused.value)
    assert GODOT in message
    assert "make images-3d" in message


def test_a_case_whose_own_image_is_built_passes(monkeypatch, session):
    service = _service(monkeypatch, session, case_images=[GODOT])
    service._check_runner_image(_config())


def test_an_older_worker_that_does_not_report_case_images_is_not_second_guessed(
    monkeypatch, session
):
    """Unknown is not missing: a worker without the field must not block runs."""
    service = _service(monkeypatch, session, case_images=None)
    service._check_runner_image(_config())


def test_a_case_without_its_own_image_is_unaffected(monkeypatch, session):
    service = _service(monkeypatch, session, case_images=[])
    config = _config()
    config.case_id = "tetris-v2"  # not in the database: the shared image only
    service._check_runner_image(config)
