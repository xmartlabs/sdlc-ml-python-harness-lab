from __future__ import annotations

from typing import TYPE_CHECKING

from harness_lab_app.config import get_settings

if TYPE_CHECKING:
    from harness_lab_app.schemas import RunConfigCreate


class BenchRunAdapter:
    def build_command(self, run_id: str, config: RunConfigCreate) -> list[str]:
        settings = get_settings()
        command = (
            [settings.runner_python, "scripts/bench_run.py"]
            if settings.runner_python
            else ["uv", "run", "python", "scripts/bench_run.py"]
        )
        command.extend(
            [
                "--case",
                config.case_id,
                "--run",
                run_id,
                "--prompt",
                config.prompt_variant,
                "--harness",
                config.harness,
                "--score-mode",
                config.score_mode,
                "--max-steps",
                str(config.max_steps),
            ]
        )
        if config.planner_model:
            command.extend(["--planner-model", config.planner_model])
        if config.executor_model:
            command.extend(["--executor-model", config.executor_model])
        # Sorted → deterministic command for identical configs (stable clone/retry).
        for skill in sorted(set(config.skills_disabled)):
            command.extend(["--disable-skill", skill])
        for skill in sorted(set(config.skills_extra)):
            command.extend(["--extra-skill", skill])
        if config.dry_run:
            command.append("--dry-run")
        if config.resume:
            command.append("--resume")
        # Web runs are managed: the API owns scoring, so the runner must reset a
        # stale workspace on start and archive+clean it after a manual-scored run
        # (ADR-0006). CLI invocations omit --fresh and keep the interactive guard.
        command.append("--fresh")
        return command
