from __future__ import annotations

from pathlib import Path

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import Case, CaseStatus, CaseSyncState, CaseType
from harness_lab_app.schemas import CaseCreate


def test_case_models_and_schemas_expose_expected_defaults():
    case = Case(case_id="prompt-1", name="Prompt 1", type=CaseType.single_prompt)
    create = CaseCreate(
        case_id="prompt-1", name="Prompt 1", type=CaseType.single_prompt
    )

    assert CaseType.single_prompt.value == "single_prompt"
    assert case.sync_state == CaseSyncState.not_synced
    assert case.prompt_variants == {}
    assert case.metadata_json == {}
    assert create.max_steps == 1


def test_case_service_create_clone_and_deactivate(session):
    service = CaseService(session)
    created = service.create_case(
        CaseCreate(
            case_id="sample-case",
            name="Sample Case",
            type=CaseType.single_prompt,
            prompt_variants={"default": "Write code"},
        )
    )
    clone = service.clone_case("sample-case", "sample-case-copy", "Sample Case Copy")
    deactivated = service.deactivate_case("sample-case-copy")

    assert created.case_id == "sample-case"
    assert clone.case_id == "sample-case-copy"
    assert clone.sync_state == CaseSyncState.not_synced
    assert deactivated.status == CaseStatus.inactive


def test_seed_tetris_case_uses_benchmark_yaml(session):
    service = CaseService(session)
    tetris = service.seed_tetris_case()

    assert tetris.case_id == "tetris-v1"
    assert "incremental" in tetris.prompt_variants
    assert tetris.sync_state == CaseSyncState.imported_from_yaml


def test_list_case_run_ids_reads_runs_yaml(session):
    service = CaseService(session)

    run_ids = service.list_case_run_ids("tetris-v1")

    assert "tetris-v1-K-001" in run_ids
    assert len(run_ids) > 0


def test_sync_case_to_yaml_writes_task_file(tmp_path: Path, session, monkeypatch):
    from harness_lab_app import (
        case_service as case_service_module,
        config as config_module,
    )

    monkeypatch.setattr(
        case_service_module,
        "get_settings",
        lambda: config_module.Settings(repo_root=tmp_path),
    )
    service = CaseService(session)
    service.create_case(
        CaseCreate(
            case_id="custom-case",
            name="Custom Case",
            type=CaseType.repo_task,
            description="A custom case",
            prompt_variants={"default": "Do the thing"},
            validation_commands=["pytest -q"],
            max_steps=4,
        )
    )

    task_path = service.sync_case_to_yaml("custom-case")

    assert task_path.exists()
    assert task_path.read_text().startswith("task_id: custom-case")
