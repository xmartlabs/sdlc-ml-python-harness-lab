"""Synthetic source roles test selection; no tested-model data or inference."""

import json
import shutil

import pytest

from harness_lab_app.gameplay.comparison import ComparisonStore
from harness_lab_app.gameplay.contracts import EpisodeConfig
from harness_lab_app.gameplay.racing import RacingEnvironment, baseline_policy
from harness_lab_app.gameplay.recordings import make_selection
from harness_lab_app.gameplay.runner import run_episode

pytestmark = pytest.mark.skipif(
    not shutil.which("node"), reason="Node required; Docker E2E covers production path"
)


def record(store, digit, role, seed=7):
    ident = "compare-" + digit * 20
    with RacingEnvironment(runtime_url="") as env:
        run_episode(
            env,
            baseline_policy(env),
            EpisodeConfig(
                episode_id=ident,
                seed=seed,
                tick_rate=60,
                ticks_per_action=12,
                max_ticks=7200,
            ),
            store.root / "runs" / ident,
            clock=lambda: 0.0,
            policy_provenance=role,
        )
    return ident


def test_all_source_combinations_verify_without_changing_official(tmp_path):
    store = ComparisonStore(tmp_path)
    store.write({"condition": {}, "competitors": [], "state": "frozen-official"})
    human = record(store, "1", {"controller": "human-recorded-controls"})
    human2 = record(store, "2", {"controller": "human-recorded-controls"})
    ai = record(
        store,
        "3",
        {
            "controller": "model-fixture",
            "requested_model": "TEST FIXTURE, NOT A REAL MODEL RUN",
        },
    )
    ai2 = record(
        store, "4", {"controller": "model-fixture", "requested_model": "TEST FIXTURE 2"}
    )
    for ids in [[human, human2], [human, ai], [ai, ai2], [human, human2, ai, ai2]]:
        result = make_selection(store, ids)
        assert [c["episode_id"] for c in result["competitors"]] == ids
        assert result["session_id"]
    assert store.current()["state"] == "frozen-official"
    assert (
        json.loads((tmp_path / "selection.json").read_text())["session_id"]
        == result["session_id"]
    )


def test_corruption_and_mismatched_seed_are_rejected(tmp_path):
    store = ComparisonStore(tmp_path)
    first = record(store, "a", {"controller": "human-recorded-controls"})
    other = record(store, "b", {"controller": "human-recorded-controls"}, seed=8)
    with pytest.raises(ValueError, match="same"):
        make_selection(store, [first, other])
    path = store.path(first)
    data = json.loads(path.read_text())
    data["frames"][10]["observation"]["state"]["x"] += 1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        make_selection(store, [first])
    assert not (tmp_path / "selection.json").exists()
