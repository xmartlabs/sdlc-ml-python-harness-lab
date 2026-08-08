from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness_lab_app.opencode_overlay import (
    CATALOG_FILE,
    EFFECTIVE_CONFIG_FILE,
    ENV_CONFIG_CONTENT,
    ENV_CONFIG_PATH,
    build_config,
    build_overlay,
    compute_delta,
    overlay_hash,
    read_catalog_models,
    read_tracked_gateway,
    routable_model_ids,
)

TRACKED = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "AI Gateway",
    "options": {"baseURL": "{env:GATEWAY_BASE_URL}"},
    "models": {"gpt-5.5": {"name": "GPT-5.5"}},
}


def _repo(tmp_path: Path, catalog: dict | None = None) -> Path:
    (tmp_path / "opencode.json").write_text(
        json.dumps({"provider": {"gateway": TRACKED}})
    )
    if catalog is not None:
        path = tmp_path / CATALOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"models": catalog}))
    return tmp_path


def test_reads_the_tracked_gateway_block(tmp_path):
    assert read_tracked_gateway(_repo(tmp_path)) == TRACKED


def test_a_missing_or_broken_config_is_not_an_error(tmp_path):
    assert read_tracked_gateway(tmp_path) == {}
    (tmp_path / "opencode.json").write_text("{ not json")
    assert read_tracked_gateway(tmp_path) == {}
    assert read_catalog_models(tmp_path) == {}


def test_delta_ignores_entries_the_tracked_map_already_expresses():
    tracked = {"gpt-5.5": {"name": "GPT-5.5"}}
    catalog = {"gpt-5.5": {"name": "GPT-5.5"}, "new-model": {"name": "New"}}
    assert compute_delta(tracked, catalog) == {"new-model": {"name": "New"}}


def test_delta_catches_a_changed_label():
    delta = compute_delta({"a": {"name": "Old"}}, {"a": {"name": "New"}})
    assert delta == {"a": {"name": "New"}}


def test_overlay_hash_is_none_when_empty_and_stable_otherwise():
    assert overlay_hash({}) is None
    first = overlay_hash({"a": {"name": "A"}, "b": {"name": "B"}})
    second = overlay_hash({"b": {"name": "B"}, "a": {"name": "A"}})
    assert first == second


def test_build_config_reproduces_the_whole_provider_block():
    config = build_config(TRACKED, {"new-model": {"name": "New"}})
    gateway = config["provider"]["gateway"]

    assert gateway["npm"] == TRACKED["npm"]
    assert gateway["options"] == TRACKED["options"]
    # Tracked ids survive alongside the overlay, so the config is still correct
    # if opencode replaces at the provider level instead of merging deeply.
    assert set(gateway["models"]) == {"gpt-5.5", "new-model"}


def test_no_catalog_means_no_overlay_at_all(tmp_path):
    env, digest = build_overlay(_repo(tmp_path))
    assert env == {}
    assert digest is None
    assert not (tmp_path / EFFECTIVE_CONFIG_FILE).exists()


def test_a_catalog_matching_the_tracked_map_produces_no_overlay(tmp_path):
    repo = _repo(tmp_path, {"gpt-5.5": {"name": "GPT-5.5"}})
    assert build_overlay(repo) == ({}, None)


def test_a_new_model_produces_both_env_vars_and_a_file(tmp_path):
    repo = _repo(tmp_path, {"gpt-5.5": {"name": "GPT-5.5"}, "brand-new": {"name": "B"}})
    env, digest = build_overlay(repo)

    assert set(env) == {ENV_CONFIG_PATH, ENV_CONFIG_CONTENT}
    assert digest is not None
    assert env[ENV_CONFIG_PATH] == str(repo / EFFECTIVE_CONFIG_FILE)

    inline = json.loads(env[ENV_CONFIG_CONTENT])
    on_disk = json.loads((repo / EFFECTIVE_CONFIG_FILE).read_text())
    assert inline == on_disk
    assert "brand-new" in inline["provider"]["gateway"]["models"]


def test_routable_ids_union_tracked_and_catalog(tmp_path):
    repo = _repo(tmp_path, {"brand-new": {"name": "B"}})
    assert routable_model_ids(repo) == {"gpt-5.5", "brand-new"}


@pytest.mark.skipif(
    subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    ).returncode
    != 0,
    reason="not a git work tree",
)
def test_the_overlay_never_dirties_the_tracked_tree():
    """The acceptance criterion for the whole overlay model.

    Everything the web configures must land under data/ (gitignored). If this
    fails, a web-driven configuration change has leaked into a tracked file.
    """
    root = Path(__file__).resolve().parents[2]
    before = subprocess.run(
        ["git", "status", "--porcelain", "--", "opencode.json"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout

    build_overlay(root)

    after = subprocess.run(
        ["git", "status", "--porcelain", "--", "opencode.json"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    ).stdout
    assert after == before
