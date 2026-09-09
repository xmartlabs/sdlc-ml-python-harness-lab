from __future__ import annotations

import contextlib
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import desc
from sqlmodel import Session, select

from harness_lab_app.config import get_settings, read_env_file_value
from harness_lab_app.constants import EXAMPLE_LMSTUDIO_URL, EXAMPLE_OLLAMA_URL
from harness_lab_app.models import (
    IN_TARGET_HARNESS_SOURCES,
    Artifact,
    Attribution,
    Audit,
    Case,
    HarnessSource,
    Run,
    RunConfig,
    RunEvent,
    RunState,
    Sandbox,
    Score,
    TargetStatus,
    Validation,
    utc_now,
)
from harness_lab_app.runner_adapter import BenchRunAdapter
from harness_lab_app.schemas import RunBatchCreate, RunConfigCreate, RunCreate
from harness_lab_app.target_service import (
    TargetError,
    TargetService,
    clone_source,
    resolve_ref,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


# Tokens that mean "no real gateway credential configured yet".
_PLACEHOLDER_TOKENS = {"", "sk-local-change-me"}


class RunAlreadyExistsError(ValueError):
    """Raised when creating a run whose run_id is already in the database."""


class RunPreflightError(ValueError):
    """Raised when a run's config cannot possibly execute (invalid before enqueue)."""


class RunDeleteConflictError(ValueError):
    """Raised when deleting a `running` run without force (cancel it first)."""


class RunBatchTooLargeError(ValueError):
    """Raised when a batch's cross-product exceeds ``max_batch_runs``."""


class RunContinueError(ValueError):
    """Raised when a run cannot be continued (not terminal, or no workspace)."""


# States a run can be continued from: it has stopped, and whatever it built is
# final. `pending`/`running` are excluded because the workspace is still moving,
# and `scoring`/`auditing` because those are transient post-processing steps
# whose artifacts are not written yet.
CONTINUABLE_STATES = frozenset(
    {
        RunState.completed,
        RunState.awaiting_manual_score,
        RunState.failed,
        RunState.cancelled,
    }
)


def continuation_source_dir(parent_run_id: str) -> Path | None:
    """Where a continuation's workspace is seeded from, or None if nowhere.

    Two sources, in order. The archive under ``data/runs/<id>/workspace`` is the
    normal one: a `--fresh` run copies its workspace there and deletes the live
    tree. The live ``workspace/<id>`` is the fallback for a run that never got
    to archive — killed by the watchdog, cancelled mid-step — which is exactly
    the case worth rescuing rather than discarding.
    """
    settings = get_settings()
    archived = settings.data_runs_dir / parent_run_id / "workspace"
    if archived.is_dir():
        return archived
    live = settings.workspace_dir / parent_run_id
    if live.is_dir():
        return live
    return None


def _resolve_variants(
    data: RunBatchCreate, defined: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """Narrow the prompt-variant axis to what each case actually defines.

    The variant axis is offered as the *union* across the selected cases, so a
    plain cross-product pairs cases with variants they do not have. Those runs
    are not caught by preflight: they reach ``scripts/bench_run.py``, which
    exits only after a container has started — burning setup time to discover a
    combination that was known-invalid before the batch was queued.

    Selection order is preserved so the generated run ids stay predictable.

    Raises:
        RunPreflightError: a case is unknown, or defines none of the selected
            variants. Dropping some combinations of a case is the documented
            behaviour; dropping the case entirely is not what was asked for.
    """
    resolved: dict[str, list[str]] = {}
    for case_id in data.case_ids:
        available = defined.get(case_id)
        if available is None:
            raise RunPreflightError(
                f"Unknown case '{case_id}'. Use GET /api/cases for the valid ids."
            )
        allowed = [name for name in data.prompt_variants if name in available]
        if not allowed:
            raise RunPreflightError(
                f"Case '{case_id}' defines none of the selected prompt variants "
                f"({', '.join(data.prompt_variants)}). It has: "
                f"{', '.join(sorted(available)) or '(none)'}."
            )
        resolved[case_id] = allowed
    return resolved


def _batch_size(data: RunBatchCreate, variants: Mapping[str, Sequence[str]]) -> int:
    """Size of the cross-product, without building it.

    Counted arithmetically on purpose: the cap has to be enforceable before
    expansion allocates one ``RunConfigCreate`` per run. ``repetitions`` has no
    upper bound of its own, so expanding first made a single unauthenticated
    request able to spend seconds of CPU only to be rejected.
    """
    return (
        sum(len(names) for names in variants.values())
        * len(data.harnesses)
        * len(data.executor_models)
        * len(data.planner_models)
        * len(data.target_repo_ids)
        * len(data.harness_sources)
        * data.repetitions
    )


def _expand_batch(
    data: RunBatchCreate, variants: Mapping[str, Sequence[str]]
) -> list[RunConfigCreate]:
    """Cross-product of the axes, repeated ``repetitions`` times.

    ``variants`` carries the per-case prompt variants from ``_resolve_variants``
    rather than ``data.prompt_variants``, so a case is only paired with the
    variants it defines.

    Order is deliberate and stable: case is the outermost loop and repetition
    the innermost, so the generated ``<case>-K-NNN`` ids of one arm land
    consecutively and a partially-finished batch reads in a sensible order.
    """
    return [
        RunConfigCreate(
            case_id=case_id,
            prompt_variant=prompt_variant,
            harness=harness,
            executor_model=executor_model,
            planner_model=planner_model,
            score_mode=data.score_mode,
            max_steps=data.max_steps,
            dry_run=data.dry_run,
            resume=False,
            skills_disabled=list(data.skills_disabled),
            skills_extra=list(data.skills_extra),
            skills_invoke=list(data.skills_invoke),
            target_repo_id=target_repo_id,
            target_ref=data.target_ref,
            harness_source=harness_source,
        )
        for case_id in data.case_ids
        for prompt_variant in variants[case_id]
        for harness in data.harnesses
        for executor_model in data.executor_models
        for planner_model in data.planner_models
        for target_repo_id in data.target_repo_ids
        for harness_source in data.harness_sources
        for _ in range(data.repetitions)
    ]


def _preflight_key(config: RunConfigCreate) -> tuple:
    """The fields ``RunService.preflight`` actually reads.

    Deduplication key for batch validation. Two configs with the same key
    cannot get different preflight answers, so the second check is pure cost —
    and preflight's cost is network round-trips.

    Kept next to ``preflight`` on purpose: **if preflight starts reading another
    field, it must be added here**, or a batch will skip a check it should have
    made. ``planner_model`` and ``case_id`` are absent because preflight does
    not consult them; including them would only fragment the cache.
    """
    return (
        config.dry_run,
        config.harness,
        config.score_mode,
        config.executor_model,
        tuple(sorted(set(config.skills_disabled))),
        tuple(sorted(set(config.skills_extra))),
        tuple(sorted(set(config.skills_invoke))),
        config.target_repo_id,
        config.harness_source,
    )


def _resume_config(config: RunConfig) -> RunConfigCreate:
    """The stored config as a resumed one, for rebuilding a retry's command.

    The skills ablation is carried across on purpose: ``build_command`` turns it
    into ``--disable-skill`` / ``--extra-skill`` flags, so dropping it here would
    silently retry an ablation arm with the full harness.
    """
    return RunConfigCreate(
        case_id=config.case_id,
        prompt_variant=config.prompt_variant,
        harness=config.harness,
        planner_model=config.planner_model,
        executor_model=config.executor_model,
        target_repo_id=config.target_repo_id,
        target_ref=config.target_ref,
        harness_source=config.harness_source,
        score_mode=config.score_mode,
        max_steps=config.max_steps,
        resume=True,
        dry_run=config.dry_run,
        continuation_prompt=config.continuation_prompt,
        skills_disabled=list(config.skills_disabled),
        skills_extra=list(config.skills_extra),
        skills_invoke=list(config.skills_invoke),
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

    def list_runs(self, batch_id: str | None = None) -> list[Run]:
        statement = select(Run).order_by(desc(cast("Any", Run.created_at)))
        if batch_id:
            statement = statement.where(Run.batch_id == batch_id)
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

    def _check_runner_image(self) -> None:
        """Refuse a docker-backed run when the runner image is not built yet.

        Without this the run is persisted, queued, and dies in the executor
        milliseconds later with a container-level error — a failed run in the
        history for a condition the operator can fix with one button. The
        worker is the only process that can see the image (ADR-0002), and its
        answer is cached, so this costs nothing in the common case. Silence
        when no worker replies: that is the worker check's story to tell, and
        guessing here would block runs whenever the broadcast is slow.
        """
        from harness_lab_app.health_service import worker_facts

        facts = worker_facts()
        if not facts or facts.get("backend") != "docker":
            return
        if facts.get("docker_ok") and not facts.get("runner_image_present"):
            raise RunPreflightError(
                f"Runner image {facts.get('runner_image')!r} is not built, so "
                "the run would fail immediately. Open Health and use "
                "'Build runner image' — it builds on the worker and takes a "
                "few minutes."
            )

    def preflight(self, config: RunConfigCreate) -> None:
        """Reject configs that cannot possibly run, before persisting/enqueuing.

        Turns the old silent "fails open" behavior (a run that exits 0 having
        built nothing) into an actionable error at creation time (ADR-0006).
        Dry-run needs no model or gateway, so it is exempt.

        Public because the run form calls it on its own through
        ``POST /api/runs/preflight``: same checks, nothing persisted, so the
        answer is the one ``create_run`` would give.
        """
        self._check_target(config)
        if config.dry_run:
            return
        # A bare arm is bare only because the runner masks the lab's governance
        # at the container level (ADR-0049). On the host lane there is nothing
        # to mask with, and the agent would silently read the lab's CLAUDE.md
        # from the workspace's parent directory — a control arm that is a second
        # treatment arm, which is exactly what this condition exists to prevent.
        if (
            config.harness_source == HarnessSource.bare
            and get_settings().execution_backend != "docker"
        ):
            raise RunPreflightError(
                "harness_source='bare' needs the docker backend: the lab's "
                "governance is masked inside the runner container, and a host "
                "run would see it through the workspace's parent directory."
            )
        self._check_runner_image()
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
                # The detail carries its own diagnosis and fix (backend down,
                # bad alias, refused completion budget) — do not editorialize
                # over it here; an "unreachable" framing was wrong for two of
                # the three.
                raise RunPreflightError(
                    f"Executor model '{executor}' failed the gateway "
                    f"preflight: {detail}. Without this check the run would "
                    "burn every step on the same error."
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
        if config.skills_invoke:
            # Asking for a skill the agent cannot see would be a prompt about
            # nothing, and the run would count it as "asked and not invoked".
            from harness_lab_app.skills_catalog import known_skill_names

            available = (known_skill_names() - set(config.skills_disabled)) | set(
                config.skills_extra
            )
            unavailable = sorted(set(config.skills_invoke) - available)
            if unavailable:
                raise RunPreflightError(
                    f"skills_invoke names skills this run cannot see: "
                    f"{', '.join(unavailable)}. A skill to invoke must be staged "
                    "via skills_extra, or governed and not in skills_disabled."
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
        from harness_lab_app.opencode_overlay import declared_output_limit

        base = read_env_file_value("GATEWAY_BASE_URL") or ""
        if not base:
            return None
        if Path("/.dockerenv").exists():
            base = rewrite_localhost(base, get_settings().docker_host_alias)
        # Probe with the budget the run will send: the model's declared
        # `limit.output` when opencode.json states one, opencode's default
        # otherwise. Probing the default against a model that declares a lower
        # cap would reject the configuration this check tells users to write.
        return probe_gateway_model(
            executor,
            base_url=base,
            token=token,
            budget=declared_output_limit(get_settings().repo_root, executor),
        )

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
                f"{EXAMPLE_LMSTUDIO_URL}) and retry."
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
                f"{EXAMPLE_OLLAMA_URL}) and retry."
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

    def _next_continuation_run_id(self, parent_run_id: str) -> str:
        """Return the next free `<parent>-C-NNN` id (ADR-0026).

        Suffixing the parent keeps the chain readable everywhere a run id
        appears — directory listings, logs, the runs table — without a join.
        Uniqueness is checked against the DB *and* the on-disk dirs for the same
        reason as `_next_run_id`: a stale workspace must never be inherited by a
        run that did not create it.
        """
        prefix = f"{parent_run_id}-C-"
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

    def _check_target(self, config: RunConfigCreate) -> None:
        """Reject a target that cannot be used, without touching the network.

        Deliberately cheap and deliberately part of ``preflight``: an unknown or
        archived target is knowable from the database, so it must fail at
        creation rather than inside a container. The network call that resolves
        the ref is a separate step (``_resolve_target``) because a batch needs it
        once per repository, not once per run.
        """
        if config.target_repo_id and config.harness_source == HarnessSource.bare:
            raise RunPreflightError(
                "harness_source='bare' runs without a repository, so it cannot "
                "target one. Against a target the bare arm is 'native' — the "
                "repository's own conventions and nothing of the lab's."
            )
        if not config.target_repo_id:
            if config.harness_source in IN_TARGET_HARNESS_SOURCES:
                raise RunPreflightError(
                    f"harness_source='{config.harness_source.value}' runs the agent "
                    "inside a target repository, so a target is required. Pick one, "
                    "or use harness_source='lab_root'."
                )
            return
        try:
            target = TargetService(self.session).get_target(config.target_repo_id)
        except TargetError as exc:
            raise RunPreflightError(str(exc)) from exc
        if target.status != TargetStatus.active:
            raise RunPreflightError(
                f"Target repository '{target.target_id}' is archived. Reactivate "
                "it, or pick another target."
            )

    def _resolve_target(
        self, config: RunConfigCreate, *, pinned_commit: str = ""
    ) -> str:
        """Resolve the run's ref to an immutable commit SHA (ADR-0029).

        ``pinned_commit`` short-circuits the lookup and is what makes a clone or
        a retry honest: re-resolving ``main`` for "the same arm, another trial"
        would silently move the run to whatever was pushed since, producing two
        runs that claim one condition and ran two.
        """
        if not config.target_repo_id:
            return ""
        if pinned_commit:
            return pinned_commit
        target = TargetService(self.session).get_target(config.target_repo_id)
        ref = (config.target_ref or "").strip()
        if not ref and target.pinned_commit:
            # A pinned target answers without asking the network at all: the
            # operator already chose the commit their whole audit is about, and
            # re-resolving `main` here would quietly move it (ADR-0042).
            return target.pinned_commit
        ref = ref or (target.default_ref or "").strip()
        try:
            return resolve_ref(clone_source(target), ref)
        except TargetError as exc:
            raise RunPreflightError(
                f"Could not pin '{target.target_id}' at ref '{ref}': {exc}"
            ) from exc

    def create_run(self, data: RunCreate) -> Run:
        self.preflight(data.config)
        target_commit = self._resolve_target(data.config)
        run_id = self._resolve_run_id(data)
        run = self._persist_run(run_id, data.config, target_commit=target_commit)
        self._publish(run.run_id)
        return run

    def _persist_run(
        self,
        run_id: str,
        config: RunConfigCreate,
        batch_id: str | None = None,
        parent_run_id: str | None = None,
        target_commit: str = "",
        experiment_id: str | None = None,
        condition_label: str | None = None,
    ) -> Run:
        """Write the config + run rows. No preflight, no publish.

        Split out of ``create_run`` so batching can validate once and persist
        many, rather than re-running network preflight per generated run.

        ``target_commit`` is passed in rather than resolved here for the same
        reason: one ``git ls-remote`` per repository serves a whole batch, and
        every run of that batch must carry the *same* commit or the repetitions
        are not repetitions.

        ``experiment_id``/``condition_label`` attribute the run to an arm. They
        are written in the same transaction as the row rather than patched on
        afterwards, so a run is never briefly visible as unattributed.
        """
        row = RunConfig(**config.model_dump(), target_commit=target_commit)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)

        command = self.preview_command(run_id, config)
        run = Run(
            run_id=run_id,
            run_config_id=row.id or 0,
            state=RunState.pending,
            command=command,
            batch_id=batch_id,
            parent_run_id=parent_run_id,
            experiment_id=experiment_id,
            condition_label=condition_label,
        )
        self.session.add(run)
        self.session.add(RunEvent(run_id=run_id, message="Run queued"))
        self.session.commit()
        self.session.refresh(run)
        return run

    def _defined_variants(self, case_ids: Sequence[str]) -> dict[str, list[str]]:
        """Prompt variants each requested case declares, keyed by case id."""
        rows = self.session.exec(
            select(Case).where(cast("Any", Case.case_id).in_(list(case_ids)))
        ).all()
        return {row.case_id: list(row.prompt_variants) for row in rows}

    def create_batch(self, data: RunBatchCreate) -> tuple[str, list[Run]]:
        """Expand the cross-product into runs, validating everything first.

        Two properties this owes the caller, both learned from what the naive
        loop would do:

        *Nothing is created unless everything validates.* A batch is one
        intent. Persisting combinations 1-16 and then rejecting 17 leaves the
        user with a half-executed request they did not ask for and now have to
        unpick, while runs are already burning GPU time.

        *Preflight runs once per distinct configuration, not once per run.*
        ``preflight`` makes network calls (gateway reachability, context-window
        probe). A 24-run batch over two executors must issue two probes.

        The cap is checked on the counted size, before expansion allocates
        anything.
        """
        variants = _resolve_variants(data, self._defined_variants(data.case_ids))
        size = _batch_size(data, variants)
        limit = get_settings().max_batch_runs
        if size > limit:
            raise RunBatchTooLargeError(
                f"This request expands to {size} runs, over the limit of "
                f"{limit}. Narrow a dimension or raise "
                "HARNESS_LAB_MAX_BATCH_RUNS."
            )
        configs = _expand_batch(data, variants)

        seen: set[tuple] = set()
        for config in configs:
            key = _preflight_key(config)
            if key in seen:
                continue
            seen.add(key)
            self.preflight(config)

        # One `git ls-remote` per (repository, ref), not per run: every
        # repetition of an arm must carry the same commit, and re-resolving
        # per run would let a push mid-batch split one arm into two.
        pins: dict[tuple[str, str], str] = {}
        for config in configs:
            key = (config.target_repo_id, config.target_ref)
            if config.target_repo_id and key not in pins:
                pins[key] = self._resolve_target(config)

        batch_id = f"batch-{uuid4().hex[:12]}"
        runs = [
            self._persist_run(
                self._next_run_id(config.case_id),
                config,
                batch_id,
                target_commit=pins.get((config.target_repo_id, config.target_ref), ""),
            )
            for config in configs
        ]
        # Publish only after every row exists, so a batch is never half-queued.
        for run in runs:
            self._publish(run.run_id)
        return batch_id, runs

    def clone_run(self, source_run_id: str) -> Run:
        """Re-queue a prior run's config under a fresh auto-generated id.

        Powers the "Run again" action: same arm, new trial with its own
        workspace. resume is reset to False so it starts clean.

        A clone of an experiment's run stays *in* that experiment, under the
        same arm. Re-running a failed trial from the runs table is the most
        natural way to top an arm back up, and dropping the attribution there
        produced the worst possible outcome: runs that measured the design
        exactly, sat in the corpus, and counted for nothing. Attribution is
        carried rather than offered as a choice — the config is identical by
        construction, so there is no consistency question to ask.
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
            # Same arm, new trial: the ablation is part of the arm, so a clone
            # that dropped it would quietly compare a different condition.
            skills_disabled=list(config.skills_disabled),
            skills_extra=list(config.skills_extra),
            skills_invoke=list(config.skills_invoke),
            target_repo_id=config.target_repo_id,
            target_ref=config.target_ref,
            harness_source=config.harness_source,
        )
        # Same arm, new trial — so the *commit* is carried, not the ref. Cloning
        # a run pinned to `main` must not silently pick up whatever was pushed
        # since; that would be a different condition wearing the same label.
        self.preflight(new_config)
        run_id = self._next_run_id(new_config.case_id)
        run = self._persist_run(
            run_id,
            new_config,
            target_commit=config.target_commit,
            experiment_id=source.experiment_id,
            condition_label=source.condition_label,
        )
        self._publish(run.run_id)
        return run

    def list_children(self, run_id: str) -> list[Run]:
        """Runs continuing from this one, oldest first."""
        statement = (
            select(Run)
            .where(Run.parent_run_id == run_id)
            .order_by(cast("Any", Run.created_at))
        )
        return list(self.session.exec(statement))

    def continue_run(
        self, parent_run_id: str, prompt: str, max_steps: int | None = None
    ) -> Run:
        """Queue a child run seeded from a terminal run's workspace (ADR-0026).

        The parent is not touched. Its score, validation and provenance keep
        describing the execution that actually produced them; the follow-up
        instruction — a human intervention — belongs to a new run that carries
        its own artifacts and its own `condition_hash`.

        The child inherits the whole arm (harness, models, skills ablation) so
        the only deliberate difference is the prompt, and runs with
        ``resume=True`` so the runner keeps the seeded workspace instead of
        resetting it.

        Unlike ``clone_run`` it does *not* join the parent's experiment. A
        continuation is a human intervention: counting it as another repetition
        of the arm would score the operator's hint as if the agent had earned
        it. The asymmetry is the point — a clone repeats the condition, a
        continuation changes it.
        """
        parent = self.get_run(parent_run_id)
        if parent.state not in CONTINUABLE_STATES:
            raise RunContinueError(
                f"Run '{parent_run_id}' is {parent.state.value}, so its workspace "
                "is still moving. Wait for it to finish (or cancel it) before "
                "continuing."
            )
        if continuation_source_dir(parent_run_id) is None:
            raise RunContinueError(
                f"Run '{parent_run_id}' has no workspace left to continue from. "
                "It was deleted, or the run never produced one. Use 'Run again' "
                "to start a fresh trial."
            )
        prompt_text = prompt.strip()
        if not prompt_text:
            raise RunContinueError("A continuation needs a follow-up prompt.")

        config = self.get_run_config(parent.run_config_id)
        new_config = RunConfigCreate(
            case_id=config.case_id,
            prompt_variant=config.prompt_variant,
            harness=config.harness,
            planner_model=config.planner_model,
            executor_model=config.executor_model,
            score_mode=config.score_mode,
            max_steps=max_steps or config.max_steps,
            resume=True,
            dry_run=config.dry_run,
            continuation_prompt=prompt_text,
            skills_disabled=list(config.skills_disabled),
            skills_extra=list(config.skills_extra),
            skills_invoke=list(config.skills_invoke),
            target_repo_id=config.target_repo_id,
            target_ref=config.target_ref,
            harness_source=config.harness_source,
        )
        self.preflight(new_config)
        run_id = self._next_continuation_run_id(parent_run_id)
        # The child continues the parent's workspace, so it is by definition at
        # the parent's commit. Re-resolving the ref would record a commit the
        # seeded tree is not at.
        run = self._persist_run(
            run_id,
            new_config,
            parent_run_id=parent_run_id,
            target_commit=config.target_commit,
        )
        self.session.add(
            RunEvent(run_id=run_id, message=f"Continues run {parent_run_id}")
        )
        self.session.add(
            RunEvent(run_id=parent_run_id, message=f"Continued by run {run_id}")
        )
        self.session.commit()
        self.session.refresh(run)
        self._publish(run_id)
        return run

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
                message=self._finish_message(exit_code, failure_reason),
            )
        )
        self.session.commit()
        self.session.refresh(run)
        return run

    @staticmethod
    def _finish_message(exit_code: int, failure_reason: str | None) -> str:
        """The closing event of a run, as a reader should see it.

        When the watchdog stops a run it already knows why ("Run timed out
        after 7200s") and then kills the container, so the exit code that
        follows is the harness's own SIGKILL. Reporting the raw code on its own
        line made every watchdog stop end in a red `Run exited with 137`, which
        reads as a crash the lab did not have; the code is kept as a
        parenthetical for anyone debugging the executor.
        """
        if exit_code == 0 or not failure_reason:
            return f"Run exited with {exit_code}"
        return f"{failure_reason} (exit code {exit_code})"

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
        children = self.list_children(run_id)
        if children and not force:
            # Its children were seeded from this workspace; deleting it leaves
            # them pointing at a run that no longer exists and makes their
            # chain_depth unexplainable. Same reasoning as the running guard.
            names = ", ".join(child.run_id for child in children)
            raise RunDeleteConflictError(
                f"Run '{run_id}' has continuations ({names}). Delete those "
                "first, or delete with force."
            )
        # Score included: without it a deleted run left its scores behind, and
        # every aggregate kept being biased by a measurement whose run was gone.
        for model in (
            Validation,
            Attribution,
            Audit,
            Sandbox,
            Artifact,
            RunEvent,
            Score,
        ):
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
