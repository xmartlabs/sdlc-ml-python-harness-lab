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


def test_seeding_imports_every_authored_benchmark(session):
    service = CaseService(session)
    seeded = service.seed_benchmark_cases()

    ids = sorted(case.case_id for case in seeded)
    assert "tetris-v2" in ids
    assert "cli-textstats-v1" in ids
    # The retired pilot never comes back through the seed.
    assert "tetris-v1" not in ids
    assert all(case.sync_state == CaseSyncState.imported_from_yaml for case in seeded)
    assert all(
        sorted(case.prompt_variants) == ["default", "harness"] for case in seeded
    )


def test_the_frontend_case_grades_every_browser_test_on_its_own(session):
    """todo-web-v1 (ADR-0067): tamper guard first, then the entry point, then one
    command per shipped test — so test_pass_rate has the suite's resolution."""
    import re

    from harness_lab_app.case_validation import check_commands

    case = next(
        c
        for c in CaseService(session).seed_benchmark_cases()
        if c.case_id == "todo-web-v1"
    )
    commands = case.validation_commands
    assert commands[0].startswith(
        "git -C /app diff --quiet HEAD -- benchmarks/todo-web-v1/tests"
    )
    shipped = re.findall(
        r"^def (test_\w+)\(",
        Path("benchmarks/todo-web-v1/tests/test_todo.py").read_text(),
        re.M,
    )
    graded = [c.rsplit("::", 1)[1] for c in commands if "::" in c]
    assert graded == shipped
    assert check_commands(commands) == []
    assert "frontend" in case.tags and "browser" in case.tags


def test_reseeding_refreshes_stale_prompt_variants(session):
    """A re-seed rebuilds the variant map from task.yaml (ADR-0047).

    Without this the database keeps offering variants the benchmark no longer
    declares, and the wizard hands the operator a prompt that resolves to
    nothing.
    """
    service = CaseService(session)
    tetris = next(c for c in service.seed_benchmark_cases() if c.case_id == "tetris-v2")
    tetris.prompt_variants = {"naive": "benchmarks/tetris-v2/prompt_naive.md"}
    tetris.name = "Tetris (renamed by the operator)"
    session.add(tetris)
    session.commit()

    refreshed = next(
        c for c in service.seed_benchmark_cases() if c.case_id == "tetris-v2"
    )

    assert sorted(refreshed.prompt_variants) == ["default", "harness"]
    # Operator-owned display fields survive the refresh.
    assert refreshed.name == "Tetris (renamed by the operator)"


def test_list_case_run_ids_reads_runs_yaml(session, tmp_path, monkeypatch):
    """A benchmark that ships a runs.yaml registry exposes its ids; one that
    does not (every active case today) exposes none."""
    from types import SimpleNamespace

    from harness_lab_app import case_service as module

    (tmp_path / "curated-v1").mkdir()
    (tmp_path / "curated-v1" / "runs.yaml").write_text(
        "runs:\n  - run_id: curated-v1-K-001\n  - run_id: curated-v1-K-002\n"
    )
    monkeypatch.setattr(
        module, "get_settings", lambda: SimpleNamespace(benchmarks_dir=tmp_path)
    )
    service = CaseService(session)

    assert service.list_case_run_ids("curated-v1") == [
        "curated-v1-K-001",
        "curated-v1-K-002",
    ]
    assert service.list_case_run_ids("tetris-v2") == []


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
            scoring_rubric="10 when the suite passes, 0 when it does not.",
            max_steps=4,
        )
    )

    task_path = service.sync_case_to_yaml("custom-case")

    assert task_path.exists()
    assert task_path.read_text().startswith("task_id: custom-case")


def test_update_case_keeps_a_db_only_case_unsynced(session):
    """`modified_since_sync` is a drift warning about a YAML file on disk.

    A case created in the browser has no such file, so every edit would raise a
    false alarm about drift from nothing.
    """
    service = CaseService(session)
    service.create_case(
        CaseCreate(
            case_id="ui-case",
            name="UI case",
            type=CaseType.multi_step,
            prompt_variants={"default": "Build it."},
        )
    )

    updated = service.update_case(
        "ui-case",
        CaseCreate(
            case_id="ui-case",
            name="UI case renamed",
            type=CaseType.multi_step,
            prompt_variants={"default": "Build it better."},
        ),
    )

    assert updated.name == "UI case renamed"
    assert updated.sync_state == CaseSyncState.not_synced


def test_update_case_flags_drift_once_a_case_has_been_synced(session):
    service = CaseService(session)
    case = service.create_case(
        CaseCreate(
            case_id="synced-case",
            name="Synced case",
            type=CaseType.repo_task,
            prompt_variants={"default": "Do the thing."},
        )
    )
    case.sync_state = CaseSyncState.synced
    session.add(case)
    session.commit()

    updated = service.update_case(
        "synced-case",
        CaseCreate(
            case_id="synced-case",
            name="Synced case",
            type=CaseType.repo_task,
            prompt_variants={"default": "Do the other thing."},
        ),
    )

    assert updated.sync_state == CaseSyncState.modified_since_sync


def test_creating_a_duplicate_case_is_a_named_refusal(session):
    """'Already exists' is an answer, not a 500.

    The audit flow re-offers candidates a past session already accepted, so a
    duplicate create is an expected event — the caller must be able to tell it
    apart from a real failure and treat it as idempotent success.
    """
    import pytest

    from harness_lab_app.case_service import CaseExistsError, CaseService
    from harness_lab_app.schemas import CaseCreate

    service = CaseService(session)
    payload = CaseCreate(
        case_id="derived-once",
        name="Derived once",
        type="single_prompt",
        description="",
        tags=[],
        prompt_variants={"default": "Do the thing."},
        step_prompts=[],
        success_criteria={},
        scoring_rubric="",
        constraints=[],
        validation_commands=[],
        max_steps=10,
    )
    service.create_case(payload)

    with pytest.raises(CaseExistsError, match="derived-once"):
        service.create_case(payload)


def test_the_clinic_ladder_declares_three_tiers(session):
    """Tier tags drive the capability frontier (ADR-0069); a missing or duplicated
    tier would silently shorten the ladder."""
    seeded = {c.case_id: c for c in CaseService(session).seed_benchmark_cases()}
    tiers = {
        case_id: next(t for t in seeded[case_id].tags if t.startswith("tier:"))
        for case_id in ("clinic-slots-t1", "clinic-booking-t2", "clinic-rules-t3")
    }
    assert tiers == {
        "clinic-slots-t1": "tier:1",
        "clinic-booking-t2": "tier:2",
        "clinic-rules-t3": "tier:3",
    }


def test_a_task_yaml_declares_its_graders_dependencies(tmp_path):
    """ADR-0071: the packages the graded tests import are case data, stored like
    the starter, refreshed on re-seed and dropped when the yaml drops them."""
    import yaml

    from harness_lab_app.case_service import CaseService

    task = tmp_path / "task.yaml"
    body = {
        "task_id": "deps-case",
        "description": "d",
        "prompts": {"default": "p"},
        "validation_commands": ["pytest {workspace}/t.py -q"],
        "validation_requirements": ["pandas==2.2.3", "joblib==1.4.2"],
    }
    task.write_text(yaml.safe_dump(body))
    from sqlmodel import Session, SQLModel, create_engine
    from sqlmodel.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        case = CaseService(session).import_task_yaml(task)
        assert case.metadata_json["validation_requirements"] == [
            "pandas==2.2.3",
            "joblib==1.4.2",
        ]


def test_a_graded_case_hands_its_tests_an_absolute_workspace():
    """Every graded command `cd`s into the workspace first, so a `{workspace}` path
    relative to the lab root no longer resolves after it. The clinic tests only
    worked because `python -m pytest` happens to put the cwd on sys.path; the iris
    tests, which run `main.py` by path, did not (2026-10-24)."""
    import re

    import yaml

    root = Path(__file__).resolve().parents[2] / "benchmarks"
    offenders = []
    for task in sorted(root.glob("*/task.yaml")):
        for command in (
            yaml.safe_load(task.read_text()).get("validation_commands") or []
        ):
            if re.search(r"\b[A-Z_]+_WORKSPACE=\{workspace\}", str(command)):
                offenders.append(task.parent.name)
                break
    assert offenders == [], offenders


def test_a_case_with_validation_requirements_reads_back_through_the_api_schema():
    """The list lives in metadata_json; a `dict[str, str]` schema would refuse to
    serialize every case that declares it."""
    from harness_lab_app.schemas import CaseRead

    record = CaseRead(
        case_id="c",
        name="c",
        type="build_from_scratch",
        status="active",
        sync_state="imported_from_yaml",
        metadata_json={"validation_requirements": ["pandas==3.1.0"]},
    )
    assert record.metadata_json["validation_requirements"] == ["pandas==3.1.0"]


def test_editing_a_case_keeps_what_its_metadata_holds(session):
    """The case editor sends no `metadata_json`; an edit replaced it with `{}`.

    That silently dropped `validation_requirements` (ADR-0071), `starter_dir`
    and a SWE case's instance data the first time anyone fixed a typo.
    """
    service = CaseService(session)
    service.create_case(
        CaseCreate(
            case_id="graded-case",
            name="Graded case",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Build it."},
            metadata_json={
                "validation_requirements": ["numpy==2.4.6"],
                "starter_dir": "benchmarks/x/starter",
            },
        )
    )

    updated = service.update_case(
        "graded-case",
        CaseCreate(
            case_id="graded-case",
            name="Graded case, renamed",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Build it."},
        ),
    )

    assert updated.name == "Graded case, renamed"
    assert updated.metadata_json == {
        "validation_requirements": ["numpy==2.4.6"],
        "starter_dir": "benchmarks/x/starter",
    }


def test_an_edit_that_sends_requirements_replaces_only_those(session):
    service = CaseService(session)
    service.create_case(
        CaseCreate(
            case_id="graded-case",
            name="Graded case",
            type=CaseType.build_from_scratch,
            metadata_json={
                "validation_requirements": ["numpy==2.4.6"],
                "starter_dir": "benchmarks/x/starter",
            },
        )
    )

    updated = service.update_case(
        "graded-case",
        CaseCreate(
            case_id="graded-case",
            name="Graded case",
            type=CaseType.build_from_scratch,
            metadata_json={"validation_requirements": ["pandas==3.1.0"]},
        ),
    )
    assert updated.metadata_json == {
        "validation_requirements": ["pandas==3.1.0"],
        "starter_dir": "benchmarks/x/starter",
    }

    cleared = service.update_case(
        "graded-case",
        CaseCreate(
            case_id="graded-case",
            name="Graded case",
            type=CaseType.build_from_scratch,
            metadata_json={"validation_requirements": []},
        ),
    )
    assert cleared.metadata_json == {"starter_dir": "benchmarks/x/starter"}


def test_a_new_case_does_not_store_an_empty_requirements_list(session):
    """The Cases form always sends the key; empty means "none", not a value."""
    case = CaseService(session).create_case(
        CaseCreate(
            case_id="plain-case",
            name="Plain case",
            type=CaseType.single_prompt,
            metadata_json={"validation_requirements": []},
        )
    )
    assert case.metadata_json == {}
