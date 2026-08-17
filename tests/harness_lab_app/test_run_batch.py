"""Batch run creation: cross-product, all-or-nothing validation, dedup, cap."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine, select

from harness_lab_app.models import Case, CaseType, Run, RunConfig
from harness_lab_app.run_service import (
    RunBatchTooLargeError,
    RunPreflightError,
    RunService,
    _batch_size,
    _expand_batch,
    _preflight_key,
    _resolve_variants,
)
from harness_lab_app.schemas import RunBatchCreate

# Every case in these tests defines these variants unless stated otherwise.
ALL_VARIANTS = ["naive", "sdlc", "conductor", "x", "y"]


def make_session(cases: dict[str, list[str]] | None = None) -> Session:
    """In-memory DB seeded with the cases a batch is allowed to reference."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    seeded = cases if cases is not None else {}
    for case_id, variants in seeded.items():
        session.add(
            Case(
                case_id=case_id,
                name=case_id,
                type=CaseType.build_from_scratch,
                prompt_variants={name: f"prompt for {name}" for name in variants},
            )
        )
    session.commit()
    return session


def _seeded(*case_ids: str, variants: list[str] | None = None) -> Session:
    return make_session({c: list(variants or ALL_VARIANTS) for c in case_ids})


def _batch(**over) -> RunBatchCreate:
    base = {
        "case_ids": ["tetris-v1"],
        "prompt_variants": ["sdlc"],
        "executor_models": ["stub-executor"],
    }
    base.update(over)
    return RunBatchCreate(**base)


def _variants(data: RunBatchCreate) -> dict[str, list[str]]:
    """Per-case variants for a batch whose cases define every variant."""
    return {case_id: list(data.prompt_variants) for case_id in data.case_ids}


# --- expansion ---------------------------------------------------------------


def test_expand_multiplies_every_axis():
    data = _batch(
        case_ids=["a", "b"],
        prompt_variants=["naive", "sdlc"],
        harnesses=["opencode", "claude"],
        executor_models=["m1"],
        planner_models=[None, "p1"],
        repetitions=3,
    )
    configs = _expand_batch(data, _variants(data))

    assert len(configs) == 2 * 2 * 2 * 1 * 2 * 3


def test_batch_size_matches_expansion_without_building_it():
    """The cap is enforced on this count, so it must not drift from reality."""
    data = _batch(
        case_ids=["a", "b"],
        prompt_variants=["naive", "sdlc"],
        harnesses=["opencode", "claude"],
        planner_models=[None, "p1"],
        repetitions=3,
    )
    variants = _variants(data)

    assert _batch_size(data, variants) == len(_expand_batch(data, variants))


def test_expand_single_entry_axes_yields_one_config():
    """One entry per list must reproduce the single-run request exactly."""
    data = _batch(max_steps=7)
    configs = _expand_batch(data, _variants(data))

    assert len(configs) == 1
    config = configs[0]
    assert config.case_id == "tetris-v1"
    assert config.prompt_variant == "sdlc"
    assert config.harness == "opencode"
    assert config.executor_model == "stub-executor"
    assert config.max_steps == 7


def test_expand_never_resumes():
    """Resume targets one workspace; N fresh trials must each start clean."""
    data = _batch(case_ids=["a", "b"], repetitions=2)
    configs = _expand_batch(data, _variants(data))

    assert all(config.resume is False for config in configs)


def test_expand_orders_case_outermost_repetition_innermost():
    data = _batch(case_ids=["a", "b"], repetitions=2)
    configs = _expand_batch(data, _variants(data))

    assert [config.case_id for config in configs] == ["a", "a", "b", "b"]


# --- per-case prompt variants ------------------------------------------------


def test_case_is_only_paired_with_variants_it_defines():
    """The variant axis is the union across cases; the product must not be.

    An unsupported pair is not rejected downstream: it reaches bench_run.py and
    exits after the container is already up.
    """
    data = _batch(case_ids=["rich", "poor"], prompt_variants=["naive", "conductor"])
    defined = {"rich": ["naive", "conductor"], "poor": ["naive"]}

    resolved = _resolve_variants(data, defined)
    configs = _expand_batch(data, resolved)

    assert resolved == {"rich": ["naive", "conductor"], "poor": ["naive"]}
    assert [(c.case_id, c.prompt_variant) for c in configs] == [
        ("rich", "naive"),
        ("rich", "conductor"),
        ("poor", "naive"),
    ]


def test_resolve_variants_preserves_selection_order():
    data = _batch(case_ids=["a"], prompt_variants=["conductor", "naive"])

    assert _resolve_variants(data, {"a": ["naive", "conductor"]}) == {
        "a": ["conductor", "naive"]
    }


def test_unknown_case_is_rejected():
    data = _batch(case_ids=["ghost"])

    with pytest.raises(RunPreflightError, match="Unknown case 'ghost'"):
        _resolve_variants(data, {})


def test_case_matching_no_selected_variant_is_rejected():
    """Filtering may drop combinations; dropping a whole case is not filtering."""
    data = _batch(case_ids=["a", "b"], prompt_variants=["conductor"])

    with pytest.raises(RunPreflightError, match="Case 'b' defines none"):
        _resolve_variants(data, {"a": ["conductor"], "b": ["naive"]})


def test_batch_filters_variants_end_to_end():
    with _seeded() as session:
        session.add(
            Case(
                case_id="poor",
                name="poor",
                type=CaseType.build_from_scratch,
                prompt_variants={"naive": "p"},
            )
        )
        session.add(
            Case(
                case_id="rich",
                name="rich",
                type=CaseType.build_from_scratch,
                prompt_variants={"naive": "p", "conductor": "p"},
            )
        )
        session.commit()

        _, runs = RunService(session).create_batch(
            _batch(case_ids=["rich", "poor"], prompt_variants=["naive", "conductor"])
        )

    assert len(runs) == 3


def test_batch_rejects_unknown_case_and_persists_nothing():
    with _seeded("a") as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError, match="Unknown case"):
            service.create_batch(_batch(case_ids=["a", "ghost"]))

        assert session.exec(select(Run)).all() == []


# --- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "axis", ["case_ids", "prompt_variants", "harnesses", "executor_models"]
)
def test_empty_axis_is_rejected(axis):
    """An empty axis would collapse the product to zero runs and report success."""
    with pytest.raises(ValidationError):
        _batch(**{axis: []})


def test_repetitions_below_one_is_rejected():
    with pytest.raises(ValidationError):
        _batch(repetitions=0)


def test_batch_over_cap_is_rejected_and_persists_nothing(monkeypatch):
    from harness_lab_app import run_service as module

    monkeypatch.setattr(
        module, "get_settings", lambda: type("S", (), {"max_batch_runs": 4})()
    )
    with _seeded("a", "b", "c") as session:
        service = RunService(session)
        with pytest.raises(RunBatchTooLargeError) as excinfo:
            service.create_batch(_batch(case_ids=["a", "b", "c"], repetitions=2))

        assert "6 runs" in str(excinfo.value)
        assert "limit of 4" in str(excinfo.value)
        assert session.exec(select(Run)).all() == []


def test_cap_is_checked_before_the_product_is_built(monkeypatch):
    """`repetitions` has no upper bound: expanding first is a free CPU burn.

    An unauthenticated caller could ask for millions of runs and make the
    server allocate every one of them before the 422.
    """
    from harness_lab_app import run_service as module

    monkeypatch.setattr(
        module, "get_settings", lambda: type("S", (), {"max_batch_runs": 50})()
    )

    def _explode(*_args, **_kwargs):
        raise AssertionError("expanded before the cap was checked")

    monkeypatch.setattr(module, "_expand_batch", _explode)
    with (
        _seeded("tetris-v1") as session,
        pytest.raises(RunBatchTooLargeError),
    ):
        RunService(session).create_batch(_batch(repetitions=3_000_000))


def test_preflight_failure_persists_nothing(monkeypatch):
    """A batch is one intent: rejecting combination N must not leave 1..N-1 queued."""
    from harness_lab_app import run_service as module

    monkeypatch.setattr(
        module, "read_env_file_value", lambda *_a, **_k: "sk-local-change-me"
    )
    with _seeded("a", "b", "c") as session:
        service = RunService(session)
        with pytest.raises(RunPreflightError):
            service.create_batch(
                _batch(
                    case_ids=["a", "b", "c"],
                    # The good executor would pass; the gateway one fails on the
                    # placeholder token. Neither may be persisted.
                    executor_models=["stub-executor", "gateway/some-model"],
                )
            )

        assert session.exec(select(Run)).all() == []
        assert session.exec(select(RunConfig)).all() == []


# --- preflight deduplication -------------------------------------------------


def test_preflight_key_ignores_case_and_prompt():
    """Preflight reads neither, so varying them must not fragment the cache."""
    data = _batch(case_ids=["a", "b"], prompt_variants=["x", "y"])
    configs = _expand_batch(data, _variants(data))

    assert len({_preflight_key(config) for config in configs}) == 1


def test_preflight_runs_once_per_distinct_config():
    """Preflight makes network calls; a 12-run batch over 2 executors does 2."""
    calls = []
    with _seeded("a", "b", "c") as session:
        service = RunService(session)
        service.preflight = calls.append  # type: ignore[method-assign]

        batch_id, runs = service.create_batch(
            _batch(
                case_ids=["a", "b", "c"],
                executor_models=["m1", "m2"],
                repetitions=2,
            )
        )

    assert len(runs) == 12
    assert len(calls) == 2
    assert {config.executor_model for config in calls} == {"m1", "m2"}
    assert batch_id.startswith("batch-")


# --- persistence -------------------------------------------------------------


def test_created_runs_share_batch_id_and_have_distinct_ids():
    with _seeded("a", "b") as session:
        service = RunService(session)
        batch_id, runs = service.create_batch(
            _batch(case_ids=["a", "b"], repetitions=2)
        )

        assert len(runs) == 4
        assert {run.batch_id for run in runs} == {batch_id}
        assert len({run.run_id for run in runs}) == 4


def test_list_runs_filters_by_batch_id():
    with _seeded("a", "b") as session:
        service = RunService(session)
        batch_id, _ = service.create_batch(_batch(case_ids=["a"], repetitions=2))
        service.create_batch(_batch(case_ids=["b"], repetitions=3))

        assert len(service.list_runs(batch_id=batch_id)) == 2
        assert len(service.list_runs()) == 5


def test_every_run_is_published_once():
    published: list[str] = []
    with _seeded("a", "b") as session:
        service = RunService(session, enqueue=published.append)
        _, runs = service.create_batch(_batch(case_ids=["a", "b"], repetitions=2))

    assert published == [run.run_id for run in runs]


def test_single_run_creation_leaves_batch_id_unset():
    """The single-run path must stay untouched by batching."""
    from harness_lab_app.schemas import RunConfigCreate, RunCreate

    with make_session() as session:
        run = RunService(session).create_run(
            RunCreate(
                run_id="solo-1",
                config=RunConfigCreate(
                    case_id="tetris-v1",
                    prompt_variant="sdlc",
                    executor_model="stub-executor",
                ),
            )
        )

    assert run.batch_id is None
