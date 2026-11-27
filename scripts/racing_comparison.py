"""Run the approved local-only sequential racing comparison; no paid API fallback."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import time
import uuid
from pathlib import Path

from harness_lab_app.gameplay.comparison import (
    CONDITION,
    BenchmarkEnvironment,
    ComparisonStore,
    condition_fingerprint,
    outcome_label,
)
from harness_lab_app.gameplay.contracts import (
    Action,
    EpisodeConfig,
    Observation,
    PolicyReply,
)
from harness_lab_app.gameplay.lmstudio import LMStudioPolicy
from harness_lab_app.gameplay.racing import after_countdown, baseline_policy
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode
from harness_lab_app.local_tuning.contracts import LoadConfig
from harness_lab_app.local_tuning.servers import LMStudioServer

LOCAL_MODELS = [
    ("Gemma 4 E4B", "google/gemma-4-e4b"),
    ("Qwen 3.5 9B", "qwen/qwen3.5-9b"),
    ("Bonsai 27B", "prism-ml/bonsai-27b"),
    ("Qwen 3.8 27B", "qwen/qwen3.8-27b"),
]


def config(episode_id: str) -> EpisodeConfig:
    return EpisodeConfig(
        episode_id=episode_id,
        seed=CONDITION["seed"],
        tick_rate=CONDITION["tick_rate"],
        ticks_per_action=CONDITION["ticks_per_action"],
        max_ticks=CONDITION["max_ticks"],
        decision_timeout_seconds=CONDITION["decision_timeout_seconds"],
    )


def run_attempt(store, document, entry, policy, runtime):
    """Same game boundary for every provider; wall failures remain inconclusive."""
    started = time.monotonic()
    reason = ""
    latencies: list[float] = []
    tokens: dict[str, int | None] = {"input_tokens": None, "output_tokens": None}
    entry.update(
        state="running",
        started_at=time.time(),
        request_count=0,
        valid_actions=0,
        invalid_actions=0,
        policy=policy.provenance,
    )
    store.write(document)

    def decide(observation: Observation, timeout: float) -> PolicyReply:
        nonlocal reason
        elapsed = time.monotonic() - started
        if (store.root / "cancel").exists():
            reason = "cancelled"
            raise RuntimeError("Comparison cancelled")
        if elapsed >= CONDITION["wall_budget_seconds"]:
            reason = "wall_limit"
            raise RuntimeError("Comparison wall limit reached")
        entry.update(
            live_state=observation.state,
            metrics=observation.state.get("metrics", {}),
            wall_seconds=elapsed,
            thinking=observation.state.get("phase") != "countdown",
        )
        if observation.state.get("phase") == "countdown":
            store.write(document)
            return after_countdown(policy)(observation, timeout)
        entry["request_count"] += 1
        store.write(document)
        begin = time.monotonic()
        try:
            remaining_wall = CONDITION["wall_budget_seconds"] - elapsed
            try:
                reply = policy(observation, min(timeout, remaining_wall))
            except TimeoutError:
                if remaining_wall < timeout:
                    reason = "wall_limit"
                raise
        finally:
            latencies.append(time.monotonic() - begin)
            entry.update(
                wall_seconds=time.monotonic() - started,
                inference_seconds=sum(latencies),
                latency_p50_seconds=statistics.median(latencies),
                latency_p95_seconds=sorted(latencies)[
                    max(0, int(len(latencies) * 0.95 + 0.999) - 1)
                ],
            )
            store.write(document)
        for source, dest in [
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ]:
            value = reply.provenance.get(source)
            if isinstance(value, int):
                tokens[dest] = (tokens[dest] or 0) + value
        entry.update(tokens)
        try:
            action = Action.model_validate_json(reply.action_json)
            action.validate_for(observation)
            entry["valid_actions"] += 1
        except ValueError:
            entry["invalid_actions"] += 1
        entry["thinking"] = False
        store.write(document)
        return reply

    try:
        with BenchmarkEnvironment(runtime_url=runtime) as env:
            replay = run_episode(
                env,
                decide,
                config(entry["episode_id"]),
                store.root / "runs" / entry["episode_id"],
                policy_provenance={
                    **policy.provenance,
                    "comparison_condition": document["condition"],
                    "condition_fingerprint": document["condition_fingerprint"],
                },
            )
            snapshot = env.metrics()
        with BenchmarkEnvironment(runtime_url=runtime) as env:
            verify_replay(
                store.root / "runs" / entry["episode_id"] / "replay.json", env
            )
        entry.update(
            state="finalized",
            status=outcome_label(replay.result.status, snapshot, reason),
            result=replay.result.model_dump(mode="json"),
            metrics=snapshot,
            metrics_source="final"
            if replay.result.metrics
            else "partial snapshot, inconclusive",
            live_state=replay.frames[-1].observation.state
            if replay.frames
            else replay.initial.state,
            verified=True,
            thinking=False,
            wall_seconds=time.monotonic() - started,
        )
    except Exception as exc:
        entry.update(
            state="failed",
            status="inconclusive: infrastructure error",
            thinking=False,
            error=type(exc).__name__,
            wall_seconds=time.monotonic() - started,
        )
        store.write(document)
        raise
    store.write(document)
    print(
        json.dumps(
            {
                "model": entry["model"],
                "status": entry["status"],
                "metrics": entry["metrics"],
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("data/tmp/gameplay-demo/racing-comparison")
    )
    parser.add_argument("--runtime", default="http://127.0.0.1:18080")
    parser.add_argument("--lmstudio", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--codex-auth", type=Path)
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()
    store = ComparisonStore(args.root)
    with BenchmarkEnvironment(runtime_url=args.runtime) as env:
        env.reset(CONDITION["seed"])
        condition = {**CONDITION, "engine_sha256": env.provenance["engine_sha256"]}
        if args.calibrate:
            ident = "compare-" + uuid.uuid4().hex[:20]
            replay = run_episode(
                env,
                baseline_policy(env),
                config(ident),
                store.root / "calibration" / ident,
                clock=lambda: 0.0,
                policy_provenance={
                    "controller": "SCRIPTED calibration",
                    "model_call": False,
                },
            )
            print(replay.result.model_dump_json())
            if replay.result.metrics.get("finished") != 1:
                raise RuntimeError("Calibration failed")
            return
    document = store.current()
    if document.get("competitors"):
        if document.get("condition") != condition:
            raise RuntimeError(
                "Frozen condition changed; preserve old session and use a fresh root"
            )
    else:
        document = {
            "condition": condition,
            "condition_fingerprint": condition_fingerprint(condition),
            "state": "ready",
            "competitors": [
                {
                    "label": label,
                    "model": model,
                    "provider": "lmstudio-direct",
                    "state": "waiting",
                    "episode_id": "compare-" + uuid.uuid4().hex[:20],
                }
                for label, model in LOCAL_MODELS
            ]
            + [
                {
                    "label": "GPT 6.1 Sol",
                    "model": "gpt-6.1-sol",
                    "provider": "codex-subscription",
                    "state": "waiting",
                    "episode_id": "compare-" + uuid.uuid4().hex[:20],
                }
            ],
        }
        store.write(document)
    args.root.joinpath("driver.pid").write_text(str(os.getpid()))
    server = LMStudioServer(args.lmstudio.removesuffix("/v1"))
    owned = None
    document["state"] = "running"
    store.write(document)
    try:
        for entry in document["competitors"]:
            if entry["state"] == "finalized":
                continue
            if entry["state"] not in ("waiting",):
                raise RuntimeError(
                    "Interrupted attempt needs explicit journal recovery, not silent rerun"
                )
            if entry["provider"] == "lmstudio-direct":
                if any(m.loaded for m in server.list_models()):
                    raise RuntimeError(
                        "A model is already loaded; do not disrupt user-owned instances"
                    )
                entry["state"] = "loading"
                store.write(document)
                served = server.load(
                    entry["model"],
                    LoadConfig(
                        context_length=4096,
                        eval_batch_size=128,
                        parallel=1,
                        flash_attention=True,
                    ),
                )
                owned = (entry["model"], served.instance_id)
                args.root.joinpath("owned-model.json").write_text(json.dumps(owned))
                entry["state"] = "waiting"
                policy = LMStudioPolicy.connect(args.lmstudio, entry["model"])
            else:
                if args.codex_auth is None:
                    entry.update(
                        state="blocked",
                        status="GPT subscription auth directory not supplied",
                    )
                    store.write(document)
                    continue
                from harness_lab_app.gameplay.codex_policy import CodexGameplayPolicy

                policy = CodexGameplayPolicy(
                    args.codex_auth,
                    executable=shutil.which("codex") or "codex",
                    cli_version="0.171.0-alpha.13.2",
                )
            try:
                run_attempt(store, document, entry, policy, args.runtime)
            finally:
                if owned:
                    server.unload(owned[0], owned[1])
                    owned = None
                    args.root.joinpath("owned-model.json").write_text("null")
        document["state"] = (
            "completed"
            if all(e["state"] == "finalized" for e in document["competitors"])
            else "blocked"
        )
        store.write(document)
    except Exception as exc:
        document["state"] = "failed"
        document["failure_type"] = type(exc).__name__
        store.write(document)
        raise
    finally:
        if owned:
            server.unload(owned[0], owned[1])
            args.root.joinpath("owned-model.json").write_text("null")
        args.root.joinpath("driver.pid").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
