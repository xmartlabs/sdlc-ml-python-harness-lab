from __future__ import annotations

import json
from pathlib import Path

from harness_lab_app import provenance
from harness_lab_app.provenance import (
    build_condition,
    build_harness_fingerprint,
    build_model_condition,
    compute_condition_hash,
    hash_file,
    hash_text,
    read_skill_hashes,
)


def _write_harness(root: Path, *, plan_hash: str = "aaa") -> None:
    """Create a minimal fake harness tree (skills-lock, agents, docs, hooks)."""
    (root / "skills-lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "brainstorming": {"computedHash": "bba479"},
                    "writing-plans": {"computedHash": plan_hash},
                },
            }
        )
    )
    agents = root / ".github" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "implementer.md").write_text("implementer agent")
    (agents / "README.md").write_text("ignored")
    github = root / ".github"
    (github / "architecture.md").write_text("arch")
    (github / "standards.md").write_text("std")
    (github / "domain-boundaries.md").write_text("dom")
    (root / "OPENCODE.md").write_text("opencode")
    (root / "LOCAL_AGENT.md").write_text("local")
    claude = root / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / "settings.json").write_text('{"hooks": {}}')


def test_hash_text_is_deterministic_and_prefixed():
    assert hash_text("abc") == hash_text("abc")
    assert hash_text("abc") != hash_text("abd")
    assert hash_text("abc").startswith("sha256:")


def test_hash_file_returns_none_for_missing(tmp_path: Path):
    assert hash_file(tmp_path / "nope.txt") is None
    target = tmp_path / "f.txt"
    target.write_text("hi")
    assert hash_file(target) == hash_file(target)


def test_read_skill_hashes_maps_name_to_computed_hash(tmp_path: Path):
    _write_harness(tmp_path)
    skills = read_skill_hashes(tmp_path / "skills-lock.json")
    assert skills == {"brainstorming": "bba479", "writing-plans": "aaa"}


def test_read_skill_hashes_missing_file_is_empty(tmp_path: Path):
    assert read_skill_hashes(tmp_path / "skills-lock.json") == {}


def test_harness_fingerprint_is_deterministic(monkeypatch, tmp_path: Path):
    # Tool versions vary by machine; pin them so the hash is stable in the test.
    monkeypatch.setattr(provenance, "_tool_version", lambda _cmd: "opencode 1.0")
    monkeypatch.setattr(provenance, "_conductor_plugin_version", lambda _root: "2.0")
    _write_harness(tmp_path)

    fp1 = build_harness_fingerprint(tmp_path)
    fp2 = build_harness_fingerprint(tmp_path)

    assert fp1 == fp2
    assert fp1["harness_hash"].startswith("sha256:")
    assert fp1["skills"]["writing-plans"] == "aaa"
    assert "implementer" in fp1["agents"]
    assert "README" not in fp1["agents"]
    assert set(fp1["governance_docs"]) == {
        "architecture.md",
        "standards.md",
        "domain-boundaries.md",
        "OPENCODE.md",
        "LOCAL_AGENT.md",
    }


def test_harness_hash_changes_when_a_skill_changes(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(provenance, "_tool_version", lambda _cmd: "opencode 1.0")
    monkeypatch.setattr(provenance, "_conductor_plugin_version", lambda _root: "2.0")

    _write_harness(tmp_path, plan_hash="aaa")
    baseline = build_harness_fingerprint(tmp_path)["harness_hash"]

    _write_harness(tmp_path, plan_hash="zzz")  # ablate one skill
    ablated = build_harness_fingerprint(tmp_path)["harness_hash"]

    assert baseline != ablated


def test_build_model_condition_extracts_gateway_alias():
    run = {"temperature": 0.2, "executor_context": 32768}
    model = build_model_condition(run, "gateway/lmstudio-qwen3.6-35b-build", None)
    assert model["temperature"] == 0.2
    assert model["context_tokens"] == 32768
    assert model["gateway_alias"] == "lmstudio-qwen3.6-35b-build"


def test_condition_hash_is_stable_and_factor_sensitive():
    model = {
        "executor_model": "gateway/x",
        "planner_model": None,
        "temperature": 0.2,
        "context_tokens": 32768,
    }
    base = compute_condition_hash("sha256:h", model, "sha256:p", "sha256:c")
    assert base == compute_condition_hash("sha256:h", model, "sha256:p", "sha256:c")
    # Changing any factor changes the arm identity.
    assert base != compute_condition_hash("sha256:H", model, "sha256:p", "sha256:c")
    assert base != compute_condition_hash("sha256:h", model, "sha256:P", "sha256:c")
    hotter = {**model, "temperature": 0.9}
    assert base != compute_condition_hash("sha256:h", hotter, "sha256:p", "sha256:c")


def test_condition_hash_ignores_provenance_only_fields(monkeypatch, tmp_path: Path):
    # Two builds with different git state but identical harness/model/prompt/case
    # must share the same condition_hash (arm identity), even though the
    # provenance fields differ.
    monkeypatch.setattr(provenance, "_tool_version", lambda _cmd: "opencode 1.0")
    monkeypatch.setattr(provenance, "_conductor_plugin_version", lambda _root: "2.0")
    _write_harness(tmp_path)
    prompt = tmp_path / "prompt_sdlc.md"
    prompt.write_text("do the thing")
    task = tmp_path / "task.yaml"
    task.write_text("task_id: tetris-v1")
    run = {"temperature": 0.2, "executor_context": 32768}

    monkeypatch.setattr(provenance, "_git", lambda _root, *_a: "commit-A")
    monkeypatch.setattr(provenance, "_harness_git_dirty", lambda _root, **_kw: False)
    cond_a = build_condition(
        repo_root=tmp_path,
        run=run,
        executor_model="gateway/x",
        planner_model=None,
        prompt_source=prompt,
        task_path=task,
    )

    monkeypatch.setattr(provenance, "_git", lambda _root, *_a: "commit-B")
    monkeypatch.setattr(provenance, "_harness_git_dirty", lambda _root, **_kw: True)
    cond_b = build_condition(
        repo_root=tmp_path,
        run=run,
        executor_model="gateway/x",
        planner_model=None,
        prompt_source=prompt,
        task_path=task,
    )

    assert cond_a["condition_hash"] == cond_b["condition_hash"]
    assert cond_a["lab_git_commit"] != cond_b["lab_git_commit"]
    assert cond_a["harness_git_dirty"] != cond_b["harness_git_dirty"]


def test_enrich_metadata_never_raises_and_adds_condition(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(provenance, "_tool_version", lambda _cmd: None)
    monkeypatch.setattr(provenance, "_conductor_plugin_version", lambda _root: None)
    _write_harness(tmp_path)
    meta = {"run_id": "tetris-v1-K-001"}

    provenance.enrich_metadata(
        meta,
        repo_root=tmp_path,
        run={"temperature": 0.2},
        executor_model="gateway/x",
        planner_model=None,
        prompt_source=None,
        task_path=None,
    )

    assert "condition_hash" in meta
    assert "harness_fingerprint" in meta
    assert meta["run_id"] == "tetris-v1-K-001"
