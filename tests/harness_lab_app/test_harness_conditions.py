"""Harness conditions: native vs injected on an external target (ADR-0031).

The property these tests exist to protect is the one that makes the comparison a
comparison at all — the two arms must produce **different** `condition_hash`
values. Fingerprinting both against the lab would collapse them into one arm and
the difference would disappear without any test failing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness_lab_app.case_service import CaseService
from harness_lab_app.models import CaseType, HarnessSource
from harness_lab_app.provenance import (
    build_workspace_harness_fingerprint,
    compute_condition_hash,
)
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.schemas import (
    CaseCreate,
    RunBatchCreate,
    RunConfigCreate,
    RunCreate,
    TargetRepositoryCreate,
)
from harness_lab_app.target_service import TargetService
from ml_python_base.skills_sync.config import DEFAULT_REGISTRY_PATH, load_registry
from ml_python_base.skills_sync.discovery import discover_skills
from ml_python_base.skills_sync.projection import BACKUP_SUFFIX, project_harness

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_LAB = Path(__file__).resolve().parents[2]
_SHA = "c" * 40


def _skip_if_claude_md_masked():
    """Skip test if CLAUDE.md is masked/empty (read-only filesystem in Docker)."""
    claude_md = _LAB / "CLAUDE.md"
    if not claude_md.read_text():
        pytest.skip("CLAUDE.md is empty/masked in this environment")


@pytest.fixture(scope="module")
def registry():
    return load_registry(_LAB / DEFAULT_REGISTRY_PATH)


# --------------------------------------------------------------------------
# Projection into a foreign root
# --------------------------------------------------------------------------


def test_projection_makes_the_target_self_contained(registry, tmp_path):
    _skip_if_claude_md_masked()

    target = tmp_path / "repo"
    target.mkdir()

    result = project_harness(
        _LAB,
        target,
        registry,
        tool_ids=("claude",),
        governance_files=(".github/architecture.md",),
    )

    skills_dir = target / ".claude" / "skills"
    assert result.skills, "the lab has governed skills to project"
    assert skills_dir.is_dir()
    # Copies, never symlinks: a symlink would point at a path the archived
    # workspace cannot reach once the lab has moved on.
    assert all(not child.is_symlink() for child in skills_dir.iterdir())
    assert (target / "CLAUDE.md").is_file()
    assert (target / ".github" / "architecture.md").is_file()
    assert "BEGIN GENERATED SKILLS" in (target / "CLAUDE.md").read_text()


def test_projection_backs_up_governance_it_displaces(registry, tmp_path):
    _skip_if_claude_md_masked()

    target = tmp_path / "repo"
    target.mkdir()
    (target / "CLAUDE.md").write_text("# Their own conventions\n")

    result = project_harness(_LAB, target, registry, tool_ids=("claude",))

    assert "CLAUDE.md" in result.collisions
    backup = target / f"CLAUDE.md{BACKUP_SUFFIX}"
    assert backup.read_text() == "# Their own conventions\n"
    assert "BEGIN GENERATED SKILLS" in (target / "CLAUDE.md").read_text()


def test_projection_carries_the_runs_ablation(registry, tmp_path):
    """An ablated arm must stay ablated inside the target."""
    _skip_if_claude_md_masked()

    target = tmp_path / "repo"
    target.mkdir()
    governed = discover_skills(_LAB)
    dropped = governed[0].name
    reduced = [skill for skill in governed if skill.name != dropped]

    project_harness(_LAB, target, registry, skills=reduced, tool_ids=("claude",))

    projected = {child.name for child in (target / ".claude" / "skills").iterdir()}
    assert dropped not in projected
    assert len(projected) == len(governed) - 1
    assert dropped not in (target / "CLAUDE.md").read_text()


def test_projection_only_touches_the_tools_a_run_uses(registry, tmp_path):
    _skip_if_claude_md_masked()

    target = tmp_path / "repo"
    target.mkdir()

    project_harness(_LAB, target, registry, tool_ids=("opencode",))

    assert (target / ".opencode" / "skills").is_dir()
    # A surface no agent in this run reads is still a file the agent can see.
    assert not (target / ".claude").exists()


# --------------------------------------------------------------------------
# The fingerprint must distinguish the arms
# --------------------------------------------------------------------------


def test_native_and_injected_do_not_share_a_fingerprint(registry, tmp_path):
    _skip_if_claude_md_masked()

    bare = tmp_path / "native"
    bare.mkdir()
    (bare / "app.py").write_text("print('x')\n")

    injected = tmp_path / "injected"
    injected.mkdir()
    (injected / "app.py").write_text("print('x')\n")
    project_harness(_LAB, injected, registry, tool_ids=("claude",))

    native_fp = build_workspace_harness_fingerprint(bare)
    injected_fp = build_workspace_harness_fingerprint(injected)

    assert native_fp["harness_hash"] != injected_fp["harness_hash"]
    assert native_fp["native_skills"] == {}
    assert ".claude/skills" in injected_fp["native_skills"]


def test_a_target_with_its_own_governance_is_its_own_condition(tmp_path):
    """Two `native` arms are only the same arm if the repos are the same."""
    bare = tmp_path / "bare"
    bare.mkdir()
    opinionated = tmp_path / "opinionated"
    opinionated.mkdir()
    (opinionated / "CLAUDE.md").write_text("# We always write tests first\n")

    assert (
        build_workspace_harness_fingerprint(bare)["harness_hash"]
        != build_workspace_harness_fingerprint(opinionated)["harness_hash"]
    )


def test_harness_source_enters_the_hash_but_lab_root_never_does():
    def digest(source=""):
        return compute_condition_hash(
            "harness-hash",
            {"executor_model": "m", "planner_model": None},
            "prompt-hash",
            "case-hash",
            harness_source=source,
        )

    baseline = digest()
    assert digest("native") != digest("injected") != baseline
    assert digest("native") != baseline
    # A bare arm is its own condition, distinct from both in-target ones and
    # from the lab default (ADR-0049) — otherwise it would share an arm with
    # whichever it collided with and the comparison would vanish.
    assert len({digest("bare"), digest("native"), digest("injected"), baseline}) == 4
    # The default must hash exactly as it did before harness conditions existed.
    assert digest("lab_root") == digest("") == baseline


# --------------------------------------------------------------------------
# Run creation
# --------------------------------------------------------------------------


@pytest.fixture
def lab(session, monkeypatch):
    CaseService(session).create_case(
        CaseCreate(
            case_id="add-endpoint",
            name="Add endpoint",
            type=CaseType.repo_task,
            prompt_variants={"default": "Add an endpoint"},
        )
    )
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="payments",
            name="Payments",
            repository_url="https://github.com/acme/payments.git",
        )
    )
    service = RunService(session)
    monkeypatch.setattr(service, "_check_runner_image", lambda: None)
    monkeypatch.setattr(service, "_publish", lambda _run_id: None)
    monkeypatch.setattr(
        "harness_lab_app.run_service.resolve_ref", lambda _url, _ref: _SHA
    )
    return service


def _config(**over) -> RunConfigCreate:
    base = {
        "case_id": "add-endpoint",
        "prompt_variant": "default",
        "executor_model": "stub-model",
    }
    base.update(over)
    return RunConfigCreate(**base)


@pytest.mark.parametrize("source", [HarnessSource.native, HarnessSource.injected])
def test_an_in_target_condition_requires_a_target(lab, source):
    with pytest.raises(RunPreflightError, match="target is required"):
        lab.create_run(RunCreate(config=_config(harness_source=source)))


def test_bare_refuses_a_target(lab):
    """`bare` and `native` are different answers to "no lab harness"."""
    with pytest.raises(RunPreflightError, match="without a repository"):
        lab.create_run(
            RunCreate(
                config=_config(
                    target_repo_id="payments", harness_source=HarnessSource.bare
                )
            )
        )


def test_bare_is_creatable_without_a_target(lab):
    run = lab.create_run(RunCreate(config=_config(harness_source=HarnessSource.bare)))
    assert lab.get_run_config(run.run_config_id).harness_source == HarnessSource.bare


def test_bare_needs_the_docker_backend(lab, monkeypatch):
    """On the host lane nothing masks the lab, so the arm would not be bare."""
    monkeypatch.setattr(
        "harness_lab_app.run_service.get_settings",
        lambda: SimpleNamespace(execution_backend="subprocess"),
    )
    with pytest.raises(RunPreflightError, match="docker backend"):
        lab.create_run(RunCreate(config=_config(harness_source=HarnessSource.bare)))


def test_the_pair_is_creatable_on_one_repository(lab):
    control = lab.create_run(
        RunCreate(
            config=_config(
                target_repo_id="payments", harness_source=HarnessSource.native
            )
        )
    )
    treatment = lab.create_run(
        RunCreate(
            config=_config(
                target_repo_id="payments", harness_source=HarnessSource.injected
            )
        )
    )

    control_config = lab.get_run_config(control.run_config_id)
    treatment_config = lab.get_run_config(treatment.run_config_id)
    assert control_config.harness_source == HarnessSource.native
    assert treatment_config.harness_source == HarnessSource.injected
    # Same repository, same commit — the harness is the only difference.
    assert control_config.target_commit == treatment_config.target_commit


def test_a_run_defaults_to_the_historical_condition(lab):
    run = lab.create_run(RunCreate(config=_config()))
    assert lab.get_run_config(run.run_config_id).harness_source == (
        HarnessSource.lab_root
    )


def test_cloning_preserves_the_harness_condition(lab):
    original = lab.create_run(
        RunCreate(
            config=_config(
                target_repo_id="payments", harness_source=HarnessSource.injected
            )
        )
    )
    clone = lab.clone_run(original.run_id)
    assert lab.get_run_config(clone.run_config_id).harness_source == (
        HarnessSource.injected
    )


def test_a_batch_can_run_both_arms_over_several_repositories(lab, session):
    TargetService(session).create_target(
        TargetRepositoryCreate(
            target_id="inventory",
            name="Inventory",
            repository_url="https://github.com/acme/inventory.git",
        )
    )

    _batch_id, runs = lab.create_batch(
        RunBatchCreate(
            case_ids=["add-endpoint"],
            prompt_variants=["default"],
            executor_models=["stub-model"],
            target_repo_ids=["payments", "inventory"],
            harness_sources=[HarnessSource.native, HarnessSource.injected],
            repetitions=3,
        )
    )

    assert len(runs) == 12  # 2 repos x 2 harness conditions x 3 repetitions
    configs = [lab.get_run_config(run.run_config_id) for run in runs]
    pairs = {(c.target_repo_id, c.harness_source) for c in configs}
    assert pairs == {
        ("payments", HarnessSource.native),
        ("payments", HarnessSource.injected),
        ("inventory", HarnessSource.native),
        ("inventory", HarnessSource.injected),
    }


def test_the_case_spec_tells_the_runner_which_condition_to_build(
    lab, session, tmp_path, monkeypatch
):
    from harness_lab_app import tasks

    run = lab.create_run(
        RunCreate(
            config=_config(
                target_repo_id="payments", harness_source=HarnessSource.injected
            )
        )
    )
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=tmp_path, repo_root=tmp_path),
    )
    spec = tasks._target_spec(session, run)
    assert spec["harness_source"] == "injected"


def test_case_spec_stages_harness_source_where_the_runner_reads_it(
    session, tmp_path, monkeypatch
):
    """The staged spec must carry harness_source at the TOP level.

    bench_run.py reads `case_spec.get("harness_source")` — root level. The
    worker once staged it only inside the target block, so every declared
    `native` run silently fell back to `lab_root`: the agent ran with the
    lab's governance loaded, and the audit judged it against documents the
    condition said it never had. The two runs of the first deepseek audit are
    the fossil record of this bug.
    """
    import json
    from types import SimpleNamespace

    from harness_lab_app import tasks
    from harness_lab_app.case_service import CaseService
    from harness_lab_app.schemas import CaseCreate

    CaseService(session).create_case(
        CaseCreate(
            case_id="native-spec-case",
            name="Native spec case",
            type="repo_task",
            description="",
            tags=[],
            prompt_variants={"default": "Do the task."},
            step_prompts=[],
            success_criteria={},
            scoring_rubric="",
            constraints=[],
            validation_commands=[],
            max_steps=10,
        )
    )
    monkey_dir = tmp_path / "runs"
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=monkey_dir, repo_root=tmp_path),
    )
    tasks._write_case_spec(
        session,
        "native-spec-case-K-001",
        "native-spec-case",
        "default",
        target={
            "target_id": "acme",
            "repo_url": "https://example.invalid/acme.git",
            "ref": "main",
            "commit": "abc123",
            "setup": [],
            "harness_source": "native",
        },
    )
    spec = json.loads(
        (monkey_dir / "native-spec-case-K-001" / "case_spec.json").read_text()
    )

    # Root level — the runner's contract — and still inside target for readers
    # of the provisioning block.
    assert spec["harness_source"] == "native"
    assert spec["target"]["harness_source"] == "native"


def test_case_spec_carries_the_condition_of_a_targetless_run(
    session, tmp_path, monkeypatch
):
    """A run with no repository has a condition too (ADR-0049).

    Staging `harness_source` only alongside a target block silently degraded
    every bare arm to `lab_root` in the runner: the agent would have run from
    the lab root with the whole harness loaded, which is the one thing a bare
    arm exists not to do.
    """
    import json

    from harness_lab_app import tasks

    CaseService(session).create_case(
        CaseCreate(
            case_id="bare-spec-case",
            name="Bare spec case",
            type=CaseType.build_from_scratch,
            prompt_variants={"default": "Do the task."},
        )
    )
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs_dir, repo_root=tmp_path),
    )
    tasks._write_case_spec(
        session,
        "bare-spec-case-K-001",
        "bare-spec-case",
        "default",
        harness_source="bare",
    )
    spec = json.loads(
        (runs_dir / "bare-spec-case-K-001" / "case_spec.json").read_text()
    )

    assert spec["harness_source"] == "bare"
    assert "target" not in spec


# ---------------------------------------------------------------------------
# Staging the adapter files an injected run's own mask hides (ADR-0049)
# ---------------------------------------------------------------------------


def test_an_injected_run_gets_its_adapter_files_staged(monkeypatch, tmp_path):
    """The projection needs the bytes the runner masks, so the worker stages them.

    The agent's cwd sits under the lab root, so `CLAUDE.md` has to be masked to
    an empty stub there. The `injected` projection splices its generated-skills
    region into that same file. The worker is not masked, so it is the one that
    can put the real bytes somewhere the runner still reaches.
    """
    from harness_lab_app import config, tasks
    from harness_lab_app.executors import GOVERNANCE_MASK_ANCESTORS
    from harness_lab_app.models import HarnessSource

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    (tmp_path / "adapters").mkdir()
    (tmp_path / "adapters" / "registry.toml").write_text(
        '[governance]\nfiles = []\nautomation = ""\norchestration = ""\n\n'
        '[[tool]]\nid = "claude"\nadapter_file = "CLAUDE.md"\n'
        '\n[[tool]]\nid = "antigravity"\nadapter_file = ".agents/rules/GEMINI.md"\n'
    )
    (tmp_path / "CLAUDE.md").write_text("# Real rules\n")
    (tmp_path / ".agents" / "rules").mkdir(parents=True)
    (tmp_path / ".agents" / "rules" / "GEMINI.md").write_text("# Gemini\n")

    staged = tasks._stage_harness_adapters(
        "run-1", HarnessSource.injected, GOVERNANCE_MASK_ANCESTORS
    )

    assert sorted(staged) == [".agents/rules/GEMINI.md", "CLAUDE.md"]
    root = settings.data_runs_dir / "run-1" / "harness_source"
    assert (root / "CLAUDE.md").read_text() == "# Real rules\n"
    # A nested adapter path keeps its shape, so `adapter_root / relative` finds it.
    assert (root / ".agents" / "rules" / "GEMINI.md").read_text() == "# Gemini\n"


def test_a_native_run_stages_nothing(monkeypatch, tmp_path):
    """Only the injected arm projects, so only it needs the unmasked bytes."""
    from harness_lab_app import config, tasks
    from harness_lab_app.executors import GOVERNANCE_MASK_ANCESTORS
    from harness_lab_app.models import HarnessSource

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    assert (
        tasks._stage_harness_adapters(
            "run-2", HarnessSource.native, GOVERNANCE_MASK_ANCESTORS
        )
        == []
    )
    assert not (settings.data_runs_dir / "run-2" / "harness_source").exists()


def test_staging_never_breaks_a_run(monkeypatch, tmp_path):
    """A missing registry is provisioning's failure to report, not the worker's.

    Raising here would kill the run before the runner could say anything useful
    about why the harness could not be built.
    """
    from harness_lab_app import config, tasks
    from harness_lab_app.executors import GOVERNANCE_MASK_ANCESTORS
    from harness_lab_app.models import HarnessSource

    settings = config.Settings(repo_root=tmp_path)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    assert (
        tasks._stage_harness_adapters(
            "run-3", HarnessSource.injected, GOVERNANCE_MASK_ANCESTORS
        )
        == []
    )


def test_a_starter_case_reaches_the_runner_through_the_spec(
    session, tmp_path, monkeypatch
):
    """task.yaml `starter` → Case.metadata_json → case_spec.json `starter_dir`, the
    only path by which a bug-fix case's broken files reach the workspace (ADR-0068)."""
    import json

    from harness_lab_app import tasks
    from harness_lab_app.case_service import CaseService

    case = next(
        c
        for c in CaseService(session).seed_benchmark_cases()
        if c.case_id == "frontend-bugfix-v1"
    )
    assert case.metadata_json["starter_dir"] == "benchmarks/frontend-bugfix-v1/starter"
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: SimpleNamespace(data_runs_dir=runs_dir, repo_root=tmp_path),
    )
    tasks._write_case_spec(session, "fb-K-001", "frontend-bugfix-v1", "default")
    spec = json.loads((runs_dir / "fb-K-001" / "case_spec.json").read_text())
    assert spec["starter_dir"] == "benchmarks/frontend-bugfix-v1/starter"
    assert spec["repo"] == ""
