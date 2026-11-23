"""Run the PR1 contract fixture or verify its replay; no coding agent is launched."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness_lab_app.gameplay.contracts import Action, EpisodeConfig, PolicyReply
from harness_lab_app.gameplay.fixture import LineWorld
from harness_lab_app.gameplay.lmstudio import LMStudioPolicy
from harness_lab_app.gameplay.replay import verify_replay
from harness_lab_app.gameplay.runner import run_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", default="gameplay-fixture")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-ticks", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--model", help="Exact id of an already loaded LM Studio model")
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    args = parser.parse_args()
    if args.verify:
        replay = verify_replay(args.verify, LineWorld())
    else:
        config = EpisodeConfig(
            episode_id=args.episode_id,
            seed=args.seed,
            max_ticks=args.max_ticks,
            decision_timeout_seconds=args.timeout,
        )

        def scripted(observation, timeout):  # noqa: ARG001 - shared policy boundary
            return PolicyReply(
                action_json=Action(
                    observation_id=observation.observation_id, controls={"move": 1.0}
                ).model_dump_json()
            )

        policy = (
            LMStudioPolicy.connect(args.base_url, args.model)
            if args.model
            else scripted
        )
        output = args.output or Path("data/runs") / config.episode_id / "gameplay"
        replay = run_episode(
            LineWorld(),
            policy,
            config,
            output,
            policy_provenance=policy.provenance
            if isinstance(policy, LMStudioPolicy)
            else {"provider": "scripted-fixture"},
        )
    print(json.dumps(replay.result.model_dump(mode="json"), sort_keys=True))
    return 0 if replay.result.status in ("completed", "budget_exhausted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
