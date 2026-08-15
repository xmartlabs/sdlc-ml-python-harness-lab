from __future__ import annotations

import contextlib
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import desc
from sqlmodel import Session, select

from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.models import (
    Artifact,
    Attribution,
    Audit,
    Run,
    RunConfig,
    RunEvent,
    RunState,
    Sandbox,
    Validation,
    utc_now,
)
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunConfigCreate, RunCreate

if TYPE_CHECKING:
    from collections.abc import Callable


# Tokens that mean "no real gateway credential configured yet".
_PLACEHOLDER_TOKENS = {"", "sk-local-change-me"}


class RunAlreadyExistsError(ValueError):
    """Raised when creating a run whose run_id is already in the database."""


class RunPreflightError(ValueError):
    """Raised when a run's config cannot possibly execute (invalid before enqueue)."""


class RunDeleteConflictError(ValueError):
    """Raised when deleting a `running` run without force (cancel it first)."""


def _resume_config(config: RunConfig) -> RunConfigCreate:
    return RunConfigCreate(
        case_id=config.case_id,
        prompt_variant=config.prompt_variant,
        harness=config.harness,
        planner_model=config.planner_model,
        executor_model=config.executor_model,
        score_mode=config.score_mode,
        max_steps=config.max_steps,
        resume=True,
        dry_run=config.dry_run,
    )


class RunService:
    def __init__(
        self,
        session: Session,
        enqueue: Callable[[str], None] | None = None,
        enqueue_cleanup: Callable[[str], None] | None = None,
    ):
        self.session = session
        self.adapter = BenchRunAdapter()
        # Queue publisher injected by the API layer (celery_app.enqueue_run);
        # None (tests, read paths) means runs are created but not published.
        self.enqueue = enqueue
        # Publisher for the worker-only container-cleanup task (delete flow);
        # None means container teardown is skipped (still removes DB + disk).
        self.enqueue_cleanup = enqueue_cleanup

    def _publish(self, run_id: str) -> None:
        if self.enqueue is None:
            return
        try:
            self.enqueue(run_id)
        except Exception as exc:
            # A swallowed enqueue error used to leave the run stuck in `pending`
            # forever with no visible reason. Surface it: mark the run failed so
            # the UI shows why it never started (broker down, etc.).
            self.session.add(
                RunEvent(
                    run_id=run_id,
                    level="error",
                    message=f"Failed to enqueue run (broker unavailable?): {exc!r}",
                )
            )
            run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
            if run is not None and run.state == RunState.pending:
                run.state = RunState.failed
                run.failure_reason = "Queue broker unavailable — run not started"
                run.ended_at = utc_now()
                self.session.add(run)
            self.session.commit()

    def list_runs(self) -> list[Run]:
        statement = select(Run).order_by(desc(cast("Any", Run.created_at)))
        return list(self.session.exec(statement))

    def get_run(self, run_id: str) -> Run:
        run = self.session.exec(select(Run).where(Run.run_id == run_id)).first()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    def get_run_config(self, run_config_id: int) -> RunConfig:
        config = self.session.exec(
            select(RunConfig).where(RunConfig.id == run_config_id)
        ).first()
        if config is None:
            raise ValueError(f"Run config not found: {run_config_id}")
        return config

    def preview_command(self, run_id: str, config: RunConfigCreate) -> list[str]:
        return self.adapter.build_command(run_id, config)

    def preflight(self, config: RunConfigCreate) -> None:
        """Reject configs that cannot possibly run, before persisting/enqueuing.

        Turns the old silent "fails open" behavior (a run that exits 0 having
        built nothing) into an actionable error at creation time (ADR-0006).
        Dry-run needs no model or gateway, so it is exempt.

        Public because the run form calls it on its own through
        ``POST /api/runs/preflight``: same checks, nothing persisted, so the
        answer is the one ``create_run`` would give.
        """
        if config.dry_run:
            return
        if config.harness in ("codex", "claude"):
            # These harnesses drive their own CLI with native auth (no gateway
            # model/token). Host/subprocess mode uses your host login directly.
            # See docs/harness-adapters.md.
            if get_settings().execution_backend == "subprocess":
                return
            # Docker mode: the CLI must be baked into the runner image with
            # credentials supplied via .env. claude is wired for Docker; codex is
            # host-only for now.
            if config.harness == "claude":
                token = read_env_file_value(
                    "CLAUDE_CODE_OAUTH_TOKEN"
                ) or read_env_file_value("ANTHROPIC_API_KEY")
                if token:
                    return
                raise RunPreflightError(
                    "The 'claude' harness in Docker needs credentials in .env: "
                    "CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`, uses your "
                    "subscription) or ANTHROPIC_API_KEY. Or run host mode "
                    "(make app-host)."
                )
            raise RunPreflightError(
                "The 'codex' harness runs the host CLI — start host mode "
                "(make app-host) or set HARNESS_LAB_EXECUTION_BACKEND=subprocess. "
                "(codex-in-Docker is not wired yet.)"
            )
        if config.score_mode != "none" and not (config.executor_model or "").strip():
            raise RunPreflightError(
                "Executor model is required for a headless run — pick an executor "
                "model before creating the run."
            )
        executor = (config.executor_model or "").strip()
        if executor.startswith("lmstudio/"):
            self._check_lmstudio_direct(executor)
        elif executor.startswith("ollama/"):
            self._check_ollama_direct(executor)
        elif executor.startswith("gateway/"):
            self._check_routable(executor)
            token = read_env_file_value("GATEWAY_TOKEN")
            if token in _PLACEHOLDER_TOKENS:
                raise RunPreflightError(
                    "GATEWAY_TOKEN is not set (still the placeholder value). Set a "
                    "real token in .env before running a gateway/ model."
                )
            detail = self._probe_gateway_executor(executor, token or "")
            if detail:
                raise RunPreflightError(
                    f"Executor model '{executor}' looks unreachable: {detail}. "
                    "This usually means the self-hosted server behind it "
                    "(LM Studio / Ollama / NIM) is off — start it (or pick "
                    "another model) and retry. Without this check the run would "
                    "burn every step on stream errors."
                )
            self._check_executor_context(executor, token or "")
        if config.skills_disabled:
            from harness_lab_app.skills_catalog import known_skill_names

            unknown = sorted(set(config.skills_disabled) - known_skill_names())
            if unknown:
                raise RunPreflightError(
                    f"Unknown skills in skills_disabled: {', '.join(unknown)}. "
                    "Use GET /api/harness/skills for the valid names."
                )
        if config.skills_extra:
            from harness_lab_app.staged_skill_service import StagedSkillService

            active = StagedSkillService(self.session).active_names()
            unknown_extra = sorted(set(config.skills_extra) - active)
            if unknown_extra:
                raise RunPreflightError(
                    f"Unknown/archived staged skills in skills_extra: "
                    f"{', '.join(unknown_extra)}. Create them on the Skills page "
                    "first."
                )

    def _probe_gateway_executor(self, executor: str, token: str) -> str | None:
        """Health-probe the gateway backend serving a ``gateway/…`` executor.

        Returns an error detail only when the backend is provably down (the
        K-004/K-005 failure mode: LM Studio host off → every LLM call streams
        errors). Ambiguity — slow JIT model loads, gateway hiccups — returns
        ``None`` so a runnable config is never blocked. Extracted for tests;
        HARNESS_LAB_DISABLE_GATEWAY_PROBE=1 skips it entirely (test suites and
        air-gapped setups must never make network calls from preflight).
        """
        if os.environ.get("HARNESS_LAB_DISABLE_GATEWAY_PROBE"):
            return None
        from harness_lab_app.executors import rewrite_localhost
        from harness_lab_app.gateway_models import probe_gateway_model

        base = read_env_file_value("GATEWAY_BASE_URL") or ""
        if not base:
            return None
        if Path("/.dockerenv").exists():
            base = rewrite_localhost(base, get_settings().docker_host_alias)
        return probe_gateway_model(executor, base_url=base, token=token)

    def _check_executor_context(self, executor: str, token: str) -> None:
        """Reject an executor whose context window cannot hold the harness.

        The K-020 failure mode: LM Studio JIT-auto-loaded the model with its
        default 8k window, the ~13.7k-token system prompt did not fit, and all
        4432 steps died on ``n_keep >= n_ctx`` — 2.5 hours for nothing. The
        probe answers in under a second in exactly that case, because the
        backend refuses an oversized prompt before evaluating it.

        Only a *provable* shortfall blocks the run; ``unknown`` (gateway down,
        timeout, model still loading) falls through, same contract as
        :meth:`_probe_gateway_executor`.
        """
        if os.environ.get("HARNESS_LAB_DISABLE_GATEWAY_PROBE"):
            return
        from harness_lab_app.executors import rewrite_localhost
        from harness_lab_app.gateway_models import probe_model_context_cached

        base = read_env_file_value("GATEWAY_BASE_URL") or ""
        if not base:
            return
        if Path("/.dockerenv").exists():
            base = rewrite_localhost(base, get_settings().docker_host_alias)
        result = probe_model_context_cached(
            executor, base_url=base, token=token, timeout=90.0
        )
        if result.status != "insufficient":
            return
        window = (
            f"{result.context_tokens} tokens"
            if result.context_tokens
            else "an unknown size"
        )
        raise RunPreflightError(
            f"Executor model '{executor}' is loaded with a context window of "
            f"{window}, below the {result.required_tokens} this harness needs. "
            "The system prompt alone is ~13.7k tokens, so every step would fail "
            "immediately. Reload the model with a larger context (in LM Studio, "
            "a JIT auto-load uses the model's small default — set it explicitly) "
            "and retry."
        )

    def _check_lmstudio_direct(self, executor: str) -> None:
        """Validate an ``lmstudio/<id>`` executor against the host, in one GET.

        Every question the gateway path answers by inference — does this name
        exist, is a model actually behind it, how big is its window — LM Studio
        answers directly in ``/api/v0/models``. So this check is allowed to be
        strict where the gateway ones fail open: there is no ambiguity left to
        protect, and the ambiguity is precisely what let run tetris-v1-K-031
        launch green against a model name the server rejects.

        Skipped under ``HARNESS_LAB_DISABLE_GATEWAY_PROBE`` alongside the other
        network checks, so the test suite never reaches for a host.
        """
        if os.environ.get("HARNESS_LAB_DISABLE_GATEWAY_PROBE"):
            return
        from harness_lab_app.gateway_models import MIN_CONTEXT_TOKENS
        from harness_lab_app.lmstudio_direct import (
            LMStudioUnreachable,
            bare_model_id,
            find_model,
            list_lmstudio_models_cached,
            lmstudio_root,
        )

        base = read_env_file_value("LMSTUDIO_BASE_URL") or ""
        if not base:
            raise RunPreflightError(
                "LMSTUDIO_BASE_URL is not set, so there is no server to send "
                "this model to. Set it on the Settings page (for example "
                "http://192.168.0.223:1234/v1) and retry."
            )
        if not base.rstrip("/").endswith("/v1"):
            # opencode passes this straight to an OpenAI-compatible client,
            # which appends /chat/completions to it. Without the /v1 segment
            # every request 404s at run time, and the model list this preflight
            # reads would still look perfect (it uses the native API beside it).
            raise RunPreflightError(
                f"LMSTUDIO_BASE_URL ('{base}') must end in /v1 — the harness "
                "talks to LM Studio through its OpenAI-compatible endpoint. "
                f"Set it to {base.rstrip('/')}/v1 on the Settings page."
            )
        if Path("/.dockerenv").exists():
            from harness_lab_app.executors import rewrite_localhost

            base = rewrite_localhost(base, get_settings().docker_host_alias)
        try:
            models = list_lmstudio_models_cached(base)
        except LMStudioUnreachable as exc:
            raise RunPreflightError(
                f"LM Studio at {lmstudio_root(base)} did not answer ({exc}). "
                "Start the server (Developer tab → Status: Running) or fix "
                "LMSTUDIO_BASE_URL on the Settings page."
            ) from exc

        bare = bare_model_id(executor)
        model = find_model(models, bare)
        if model is None:
            known = ", ".join(m.id for m in models[:8]) or "none"
            raise RunPreflightError(
                f"LM Studio does not know a model called '{bare}'. It currently "
                f"serves: {known}. Press 'Refresh models' — this list is read "
                "straight off the server, so a name in it always routes."
            )
        if not model.loaded:
            if model.state == "unknown":
                # Pre-/api/v0 build: the id is real but state is unknowable.
                # Blocking here would ground a working setup on missing
                # metadata, so this is the one direct case that falls through.
                return
            raise RunPreflightError(
                f"Model '{bare}' is not loaded in LM Studio. Load it there "
                "first and set its context length explicitly — a JIT auto-load "
                "uses the model's small default window (that is how run "
                "tetris-v1-K-020 lost 2.5 hours to an 8k context)."
            )
        window = model.loaded_context
        if window is not None and window < MIN_CONTEXT_TOKENS:
            raise RunPreflightError(
                f"Model '{bare}' is loaded with a {window}-token context, below "
                f"the {MIN_CONTEXT_TOKENS} this harness needs — the system "
                "prompt alone is ~13.7k tokens. Eject it, reload with a larger "
                "Context Length in LM Studio, and retry."
            )

    def _check_ollama_direct(self, executor: str) -> None:
        """Validate an ``ollama/<id>`` executor against the host it addresses.

        Strict about everything the host can answer — is it configured, is it
        up, does it have this model — and deliberately *not* strict about a cold
        model, which is where Ollama differs from LM Studio. LM Studio JIT-loads
        with the model's own default window, unknowable in advance; Ollama
        applies its server-wide ``OLLAMA_CONTEXT_LENGTH`` to whatever it loads.
        A cold Ollama model is therefore a warning in the picker label, not a
        block here, while a *running* one whose reported window is too small is
        refused exactly like an LM Studio model.
        """
        if os.environ.get("HARNESS_LAB_DISABLE_GATEWAY_PROBE"):
            return
        from harness_lab_app.direct_hosts import DirectHostUnreachable
        from harness_lab_app.gateway_models import MIN_CONTEXT_TOKENS
        from harness_lab_app.ollama_direct import (
            bare_model_id,
            find_model,
            list_ollama_models_cached,
            ollama_root,
        )

        base = read_env_file_value("OLLAMA_BASE_URL") or ""
        if not base:
            raise RunPreflightError(
                "OLLAMA_BASE_URL is not set, so there is no server to send this "
                "model to. Set it on the Settings page (for example "
                "http://192.168.0.155:11434/v1) and retry."
            )
        if not base.rstrip("/").endswith("/v1"):
            # Same trap as LM Studio: the native API this preflight reads lives
            # beside the OpenAI-compatible one, so the model list would look
            # perfect while every completion 404s at run time.
            raise RunPreflightError(
                f"OLLAMA_BASE_URL ('{base}') must end in /v1 — the harness talks "
                "to Ollama through its OpenAI-compatible endpoint. Set it to "
                f"{base.rstrip('/')}/v1 on the Settings page."
            )
        if Path("/.dockerenv").exists():
            from harness_lab_app.executors import rewrite_localhost

            base = rewrite_localhost(base, get_settings().docker_host_alias)
        try:
            models = list_ollama_models_cached(base)
        except DirectHostUnreachable as exc:
            raise RunPreflightError(
                f"Ollama at {ollama_root(base)} did not answer ({exc}). Start it "
                "(or fix OLLAMA_BASE_URL on the Settings page) and retry."
            ) from exc

        bare = bare_model_id(executor)
        model = find_model(models, bare)
        if model is None:
            known = ", ".join(m.id for m in models[:8]) or "none"
            raise RunPreflightError(
                f"Ollama does not have a model called '{bare}'. It currently "
                f"has: {known}. Pull it there, then press Reload — this list is "
                "read straight off the server, so a name in it always routes."
            )
        window = model.context_length
        if model.running and window is not None and window < MIN_CONTEXT_TOKENS:
            raise RunPreflightError(
                f"Model '{bare}' is running with a {window}-token context, below "
                f"the {MIN_CONTEXT_TOKENS} this harness needs — the system "
                "prompt alone is ~13.7k tokens. Ollama takes this from the "
                "server-wide OLLAMA_CONTEXT_LENGTH, so raise it there (for "
                f"example OLLAMA_CONTEXT_LENGTH={MIN_CONTEXT_TOKENS}), restart "
                "Ollama, and retry."
            )

    def _check_routable(self, executor: str) -> None:
        """Reject a ``gateway/`` model opencode has no provider entry for.

        The gateway serving a model is not enough: opencode resolves
        ``gateway/<id>`` against ``provider.gateway.models``, from the tracked
        ``opencode.json`` plus the enabled web catalog (the overlay). A model in
        neither would burn every step on a provider error.

        Skipped under ``HARNESS_LAB_DISABLE_GATEWAY_PROBE``, alongside the
        reachability probe: both validate the executor against configuration
        the test suite deliberately does not have.
        """
        if os.environ.get("HARNESS_LAB_DISABLE_GATEWAY_PROBE"):
            return
        from harness_lab_app.opencode_overlay import routable_model_ids

        routable = routable_model_ids(get_settings().repo_root)
        # An empty set means opencode.json is unreadable — fail open rather than
        # block every run on a parsing problem this check cannot diagnose.
        if not routable:
            return
        bare = executor[len("gateway/") :]
        if bare not in routable:
            raise RunPreflightError(
                f"Executor model '{executor}' is not routable: opencode has no "
                "provider entry for it. Open the Settings page, press "
                "'Refresh models' and make sure it is enabled."
            )

    def _next_run_id(self, case_id: str) -> str:
        """Return the next free `<case>-K-NNN` id (the web lane, ADR-0006).

        Unique across both the DB and the on-disk run/workspace dirs, so a
        generated run never collides with a stale workspace (the RESUME trap).
        """
        prefix = f"{case_id}-K-"
        pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        max_n = 0
        for run in self.session.exec(select(Run)).all():
            match = pattern.match(run.run_id)
            if match:
                max_n = max(max_n, int(match.group(1)))
        settings = get_settings()
        candidate = max_n + 1
        while (settings.data_runs_dir / f"{prefix}{candidate:03d}").exists() or (
            settings.workspace_dir / f"{prefix}{candidate:03d}"
        ).exists():
            candidate += 1
        return f"{prefix}{candidate:03d}"

    def _resolve_run_id(self, data: RunCreate) -> str:
        """Pick the run id: an explicit run_id/label (409 on dup), else generate."""
        explicit = (data.label or data.run_id or "").strip()
        if not explicit:
            return self._next_run_id(data.config.case_id)
        existing = self.session.exec(select(Run).where(Run.run_id == explicit)).first()
        if existing is not None:
            raise RunAlreadyExistsError(
                f"Run '{explicit}' already exists (state: {existing.state}). "
                "Use retry to re-run it, or pick a different run id."
            )
        return explicit

    def create_run(self, data: RunCreate) -> Run:
        self.preflight(data.config)
        run_id = self._resolve_run_id(data)
        config = RunConfig(**data.config.model_dump())
        self.session.add(config)
        self.session.commit()
        self.session.refresh(config)

        command = self.preview_command(run_id, data.config)
        run = Run(
            run_id=run_id,
            run_config_id=config.id or 0,
            state=RunState.pending,
            command=command,
        )
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run queued"))
        self.session.commit()
        self.session.refresh(run)
        self._publish(run.run_id)
        return run

    def clone_run(self, source_run_id: str) -> Run:
        """Re-queue a prior run's config under a fresh auto-generated id.

        Powers the "Run again" action: same arm, new trial with its own
        workspace. resume is reset to False so it starts clean.
        """
        source = self.get_run(source_run_id)
        config = self.get_run_config(source.run_config_id)
        new_config = RunConfigCreate(
            case_id=config.case_id,
            prompt_variant=config.prompt_variant,
            harness=config.harness,
            planner_model=config.planner_model,
            executor_model=config.executor_model,
            score_mode=config.score_mode,
            max_steps=config.max_steps,
            resume=False,
            dry_run=config.dry_run,
        )
        return self.create_run(RunCreate(config=new_config))

    def mark_running(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        run.state = RunState.running
        run.started_at = utc_now()
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run started"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_finished(
        self, run_id: str, exit_code: int, failure_reason: str | None = None
    ) -> Run:
        run = self.get_run(run_id)
        run.exit_code = exit_code
        run.failure_reason = failure_reason
        run.ended_at = utc_now()
        run.state = (
            RunState.awaiting_manual_score if exit_code == 0 else RunState.failed
        )
        self.session.add(run)
        self.session.add(
            RunEvent(
                run_id=run_id,
                level="info" if exit_code == 0 else "error",
                message=f"Run exited with {exit_code}",
            )
        )
        self.session.commit()
        self.session.refresh(run)
        return run

    def score_run(
        self, run_id: str, score: int, interventions: int, notes: str = ""
    ) -> Run:
        run = self.get_run(run_id)
        run.score = score
        run.interventions = interventions
        run.notes = notes
        run.state = RunState.completed
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message=f"Run scored {score}/10"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def mark_cancelled(self, run_id: str, exit_code: int) -> Run:
        run = self.get_run(run_id)
        run.exit_code = exit_code
        run.ended_at = utc_now()
        run.state = RunState.cancelled
        run.cancel_requested = False
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, level="info", message="Run cancelled"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def cancel_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run.state in {RunState.completed, RunState.failed, RunState.cancelled}:
            return run
        if run.state == RunState.running:
            run.cancel_requested = True
            self.session.add(run)
            self.session.add(
                RunEvent(run_id=run_id, level="info", message="Cancellation requested")
            )
        else:
            run.state = RunState.cancelled
            run.ended_at = utc_now()
            self.session.add(run)
            self.session.add(
                RunEvent(run_id=run_id, level="info", message="Run cancelled")
            )
        self.session.commit()
        self.session.refresh(run)
        return run

    def retry_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        config = self.get_run_config(run.run_config_id)
        # Persist resume on the config: the Celery task rebuilds the command
        # from RunConfig at execution time, so the flag must survive there.
        config.resume = True
        self.session.add(config)
        run.command = self.adapter.build_command(
            run_id,
            _resume_config(config),
        )
        run.state = RunState.pending
        run.exit_code = None
        run.failure_reason = None
        run.started_at = None
        run.ended_at = None
        run.cancel_requested = False
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, level="info", message="Run retried"))
        self.session.commit()
        self.session.refresh(run)
        self._publish(run_id)
        return run

    @staticmethod
    def _remove_run_dirs(run_id: str) -> None:
        """Remove on-disk run state, guarded to stay under the repo root.

        Paths are built only from settings + the validated run_id; a run_id that
        resolves outside the repo (e.g. containing ``..``) is refused, so the
        run_id is never treated as a free-text filesystem path (ADR-0007).
        """
        settings = get_settings()
        repo_root = settings.repo_root.resolve()
        for base in (settings.data_runs_dir, settings.workspace_dir):
            target = (base / run_id).resolve()
            if repo_root != target and repo_root not in target.parents:
                continue
            if target.exists():
                shutil.rmtree(target)

    def delete_run(self, run_id: str, *, force: bool = False) -> None:
        """Delete a run and all its state: DB rows, on-disk dirs, container.

        No DB cascades exist, so dependent rows are removed explicitly. A
        `running` run is refused unless force=True (cancel it first). Container
        teardown is enqueued to the worker, which owns the docker socket.
        """
        run = self.get_run(run_id)
        if run.state == RunState.running and not force:
            raise RunDeleteConflictError(
                f"Run '{run_id}' is running. Cancel it first, or delete with force."
            )
        for model in (Validation, Attribution, Audit, Sandbox, Artifact, RunEvent):
            rows = self.session.exec(select(model).where(model.run_id == run_id)).all()
            for row in rows:
                self.session.delete(row)
        config_id = run.run_config_id
        self.session.delete(run)
        config = self.session.exec(
            select(RunConfig).where(RunConfig.id == config_id)
        ).first()
        if config is not None:
            self.session.delete(config)
        self.session.commit()

        self._remove_run_dirs(run_id)

        if self.enqueue_cleanup is not None:
            # Best-effort: a leftover container is harmless once the run is gone.
            with contextlib.suppress(Exception):
                self.enqueue_cleanup(run_id)

    def list_events(self, run_id: str, ascending: bool = False) -> list[RunEvent]:
        order = (
            cast("Any", RunEvent.created_at)
            if ascending
            else desc(cast("Any", RunEvent.created_at))
        )
        statement = select(RunEvent).where(RunEvent.run_id == run_id).order_by(order)
        return list(self.session.exec(statement))
