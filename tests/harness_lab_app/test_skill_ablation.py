"""Per-run skill ablation: schema/adapter plumbing, provenance rules, and the
bench_run apply/restore round-trip on a temp repo fixture."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from harness_lab_app.provenance import compute_condition_hash
from harness_lab_app.run_service import RunPreflightError, RunService
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate, RunCreate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_run  # noqa: E402

RENDER_MARKERS = (
    "<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->",
    "<!-- END GENERATED SKILLS -->",
)


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _config(**overrides) -> RunConfigCreate:
    base = {
        "case_id": "tetris-v1",
        "prompt_variant": "sdlc",
        "executor_model": "stub-executor",
    }
    return RunConfigCreate(**{**base, **overrides})


# ---------------------------------------------------------------------------
# Adapter / schema / preflight
# ---------------------------------------------------------------------------


def test_adapter_emits_sorted_skill_flags():
    command = BenchRunAdapter().build_command(
        "run-1", _config(skills_disabled=["zeta", "alpha"], skills_extra=["nu"])
    )
    joined = " ".join(command)
    assert "--disable-skill alpha" in joined
    assert "--disable-skill zeta" in joined
    assert "--extra-skill nu" in joined
    assert joined.index("--disable-skill alpha") < joined.index("--disable-skill zeta")


def test_adapter_default_config_has_no_skill_flags():
    command = BenchRunAdapter().build_command("run-1", _config())
    assert "--disable-skill" not in command
    assert "--extra-skill" not in command


def test_create_run_persists_skills_disabled():
    with _session() as session:
        service = RunService(session)
        run = service.create_run(
            RunCreate(
                run_id="abl-1",
                config=_config(skills_disabled=["brainstorming"]),
            )
        )
        config = service.get_run_config(run.run_config_id)
        assert config.skills_disabled == ["brainstorming"]
        assert "--disable-skill" in run.command


def test_preflight_rejects_unknown_disabled_skill():
    with (
        _session() as session,
        pytest.raises(RunPreflightError, match="definitely-not-a-skill"),
    ):
        RunService(session).create_run(
            RunCreate(
                run_id="abl-2",
                config=_config(skills_disabled=["definitely-not-a-skill"]),
            )
        )


# ---------------------------------------------------------------------------
# Provenance: condition_hash ablation rules
# ---------------------------------------------------------------------------


def test_condition_hash_unchanged_for_empty_ablation():
    model = {"executor_model": "m", "planner_model": None}
    base = compute_condition_hash("h", model, "p", "c")
    assert compute_condition_hash("h", model, "p", "c", skills_disabled=()) == base
    assert (
        compute_condition_hash("h", model, "p", "c", skills_extra_hashes=None) == base
    )


def test_condition_hash_flips_on_ablation_and_is_order_insensitive():
    model = {"executor_model": "m", "planner_model": None}
    base = compute_condition_hash("h", model, "p", "c")
    ablated = compute_condition_hash(
        "h", model, "p", "c", skills_disabled=("beta", "alpha")
    )
    assert ablated != base
    assert (
        compute_condition_hash("h", model, "p", "c", skills_disabled=("alpha", "beta"))
        == ablated
    )
    with_extra = compute_condition_hash(
        "h", model, "p", "c", skills_extra_hashes={"gamma": "sha256:x"}
    )
    assert with_extra != base
    # Editing the staged skill's content flips the arm again.
    assert (
        compute_condition_hash(
            "h", model, "p", "c", skills_extra_hashes={"gamma": "sha256:y"}
        )
        != with_extra
    )


# ---------------------------------------------------------------------------
# bench_run apply/restore round-trip on a temp repo
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_repo(tmp_path, monkeypatch):
    """Minimal governed repo: real registry+templates, two skills, OPENCODE.md."""
    shutil.copytree(_REPO_ROOT / "adapters", tmp_path / "adapters")
    internal = tmp_path / ".github" / "skills"
    internal.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (internal / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: does {name}\n---\nBody of {name}.\n"
        )
    (tmp_path / ".github" / "skills-external").mkdir()
    begin, end = RENDER_MARKERS
    (tmp_path / "OPENCODE.md").write_text(f"# Adapter\n\n{begin}\n{end}\n")
    staging = tmp_path / "data" / "skills_staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(bench_run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bench_run, "SKILLS_STAGING_DIR", staging)
    return tmp_path


def test_apply_and_restore_round_trip(temp_repo):
    effective = bench_run.build_effective_skills(["beta"], [])
    bench_run.apply_skill_views(effective)

    opencode_skills = temp_repo / ".opencode" / "skills"
    assert (opencode_skills / "alpha").is_dir()
    assert not (opencode_skills / "beta").exists()
    adapter_text = (temp_repo / "OPENCODE.md").read_text()
    assert "alpha" in adapter_text
    assert "beta" not in adapter_text

    bench_run.restore_full_skill_views()
    assert (opencode_skills / "beta").is_dir()
    assert "beta" in (temp_repo / "OPENCODE.md").read_text()


def test_restore_at_start_heals_a_pre_ablated_tree(temp_repo):
    # Simulate a crashed previous ablation: beta missing from the native view.
    bench_run.apply_skill_views(bench_run.build_effective_skills(["beta"], []))
    assert not (temp_repo / ".opencode" / "skills" / "beta").exists()
    # The guard (restore before every run) brings the full view back.
    bench_run.restore_full_skill_views()
    assert (temp_repo / ".opencode" / "skills" / "beta").is_dir()


def test_extra_skill_is_linked_and_removed(temp_repo):
    staged = temp_repo / "data" / "skills_staging" / "gamma"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text(
        "---\nname: gamma\ndescription: staged gamma\n---\nStaged body.\n"
    )
    effective = bench_run.build_effective_skills([], ["gamma"])
    bench_run.apply_skill_views(effective)
    gamma_link = temp_repo / ".opencode" / "skills" / "gamma" / "SKILL.md"
    assert gamma_link.exists()
    assert "gamma" in (temp_repo / "OPENCODE.md").read_text()

    bench_run.restore_full_skill_views()
    assert not (temp_repo / ".opencode" / "skills" / "gamma").exists()
    assert "gamma" not in (temp_repo / "OPENCODE.md").read_text()


def test_build_effective_skills_fails_fast(temp_repo):
    with pytest.raises(SystemExit):
        bench_run.build_effective_skills(["ghost"], [])
    with pytest.raises(SystemExit):
        bench_run.build_effective_skills([], ["missing-staged"])
    # Collision with a governed skill is refused unless it is disabled.
    staged = temp_repo / "data" / "skills_staging" / "alpha"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("---\nname: alpha\n---\n")
    with pytest.raises(SystemExit):
        bench_run.build_effective_skills([], ["alpha"])
    # Disabled governed + staged replacement is a valid A/B move.
    effective = bench_run.build_effective_skills(["alpha"], ["alpha"])
    names = [skill.name for skill in effective]
    assert names.count("alpha") == 1


def test_staged_skill_hashes(temp_repo):
    staged = temp_repo / "data" / "skills_staging" / "delta"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("v1")
    first = bench_run.staged_skill_hashes(["delta"])
    assert "delta" in first
    (staged / "SKILL.md").write_text("v2")
    assert bench_run.staged_skill_hashes(["delta"]) != first
    assert bench_run.staged_skill_hashes(["absent"]) == {}


def test_staged_skill_hash_covers_the_whole_tree(temp_repo):
    """ADR-0017: a supporting file is part of the arm, not decoration.

    Hashing only SKILL.md let an edit to `scripts/` reuse a previous arm's
    ``condition_hash`` — two different harnesses binned as one.
    """
    staged = temp_repo / "data" / "skills_staging" / "delta"
    (staged / "scripts").mkdir(parents=True)
    (staged / "SKILL.md").write_text("v1")
    (staged / "scripts" / "run.sh").write_text("echo hi\n")
    before = bench_run.staged_skill_hashes(["delta"])

    (staged / "scripts" / "run.sh").write_text("echo bye\n")
    assert bench_run.staged_skill_hashes(["delta"]) != before

    # And adding a file is a change too.
    (staged / "references").mkdir()
    (staged / "references" / "notes.md").write_text("notes\n")
    assert bench_run.staged_skill_hashes(["delta"]) != before


def test_staged_skill_hash_algo_is_recorded():
    """The label that keeps the two generations of staged arms legible."""
    assert bench_run.STAGED_SKILL_HASH_ALGO == "folder_sha256_v2"


def test_build_effective_skills_refuses_an_escaping_symlink(temp_repo):
    """A staged skill is projected by symlink; an escaping link would leak
    arbitrary host files into the agent's context."""
    secret = temp_repo / "secret.txt"
    secret.write_text("token\n")
    staged = temp_repo / "data" / "skills_staging" / "epsilon"
    staged.mkdir(parents=True)
    (staged / "SKILL.md").write_text("---\nname: epsilon\n---\n")
    (staged / "leak.txt").symlink_to(secret)

    with pytest.raises(SystemExit, match="symlink"):
        bench_run.build_effective_skills([], ["epsilon"])


def test_build_effective_skills_allows_an_internal_symlink(temp_repo):
    staged = temp_repo / "data" / "skills_staging" / "zeta"
    (staged / "scripts").mkdir(parents=True)
    (staged / "SKILL.md").write_text("---\nname: zeta\n---\n")
    (staged / "scripts" / "run.sh").write_text("echo hi\n")
    (staged / "alias.sh").symlink_to(staged / "scripts" / "run.sh")

    effective = bench_run.build_effective_skills([], ["zeta"])
    assert "zeta" in [skill.name for skill in effective]
