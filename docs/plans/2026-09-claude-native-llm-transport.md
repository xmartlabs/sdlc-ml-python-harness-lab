# Claude-Native LLM Transport (subscription-only lab) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `subagent-driven-development` (recommended) or `executing-plans` to implement
> this plan task by task. Use TDD for behavior changes. Do not create commits,
> tags, pushes, releases, issues, pull requests, or branch integrations unless a
> human explicitly requests that operation.

**Goal:** Make a Claude subscription the *only* credential a new engineer needs
to run this lab end to end. Today `CLAUDE_CODE_OAUTH_TOKEN` buys the `claude`
harness lane and nothing else: the behavioural audit, the improvement synthesis,
the SWE-bench Pro judge, the case-summary polish and two of the three DeepEval
metrics all require an OpenAI-compatible gateway. This plan adds a second
transport for those in-process LLM calls that drives the already-installed,
already-authenticated Claude Code CLI in headless mode, so the same subscription
that pays for runs also pays for judging.

**Architecture:** One transport seam, four existing call sites collapsed onto
it. A new stdlib-only `harness_lab_app.llm_transport` module owns "send a
(system, user) pair to a model and return the text", and resolves *how* at call
time: gateway when `GATEWAY_BASE_URL`/`GATEWAY_TOKEN` are set (unchanged, still
the preferred path), Claude CLI when they are not but a Claude credential is,
and an explicit "no judge configured" outcome when neither exists. No caller
learns about transports; the existing graceful-skip semantics are preserved
exactly (a measurement that did not happen is still not a zero).

**Tech stack:** Python 3.11 stdlib (`urllib`, `subprocess`, `json`), the Claude
Code CLI (`@anthropic-ai/claude-code`, already pinned in `Dockerfile.runner`),
Docker Compose, pytest, Make.

**Plan status:** **implemented** on `feat/lab-v3`, 2026-09-04. All six
milestones landed (the case assistant, a fifth call site missed in the first
pass, was moved onto the transport the same day); the decision is recorded in
[ADR-0043](../adr/0043-one-llm-transport-gateway-first-claude-cli-second.md).
Validation evidence is in the Milestone 6 section below.

**Plan owner repository:** this repository.

---

## Why this plan exists

A teammate cloning this repo with only a Claude subscription can benchmark the
`claude` harness and nothing else. The preflight lets them through —
`src/harness_lab_app/run_service.py:363-370` returns as soon as
`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is present, before any gateway
check — and then every LLM-judged feature fails or silently degrades at the
moment they try to use it.

The reason is uniform: **every in-process LLM call in the lab speaks
OpenAI-shaped HTTP.** Each site independently builds
`POST {GATEWAY_BASE_URL}/chat/completions` with `Authorization: Bearer
{GATEWAY_TOKEN}` and reads `choices[0].message.content`.

| Feature | Call site | Behaviour with no gateway |
|---|---|---|
| Behavioural audit | `scripts/bench_audit.py:473` (`call_gateway`) | `sys.exit("❌ GATEWAY_BASE_URL or GATEWAY_TOKEN not set")` |
| Improvement proposals / sanitized issue | `scripts/lab_improve.py:41,321` (imports `call_gateway`) | same exit |
| SWE-bench Pro judge | `src/harness_lab_app/tasks.py:955` (`_worker_gateway`), used at `:873`, `:931` | `RuntimeError("gateway not configured")`, falls back to manual scoring |
| Case-summary polish on import | `src/harness_lab_app/case_summary.py:34` (`_gateway`) | returns `None`, raw issue text kept |
| `task_completion`, `step_efficiency` | `src/harness_lab_app/deepeval_adapter.py:164,190` | metrics skipped with "No judge model is configured" |
| AI case draft ("Draft it with AI" in the wizards) | `src/harness_lab_app/case_draft_service.py` (`_call_gateway_chat`) | `ValueError("Gateway not configured")` → HTTP 400 in the wizard |

`tool_correctness` is unaffected — it is deterministic and needs no judge.

### Why a Claude subscription token cannot simply be pasted into `GATEWAY_TOKEN`

`sk-ant-oat…` (from `claude setup-token`) is a **Claude Code CLI credential**,
not an API-HTTP one. Using it over raw HTTP requires the native
`POST /v1/messages` endpoint plus an `anthropic-beta: oauth-2025-04-20` header,
a request shape this lab never emits. No combination of environment variables
closes that gap.

An **Anthropic console API key** (`sk-ant-api…`, paid credits) *does* work
today with no code change, because Anthropic ships an OpenAI-compatible layer at
`https://api.anthropic.com/v1/` accepting `Authorization: Bearer` and returning
`choices[0].message.content` — exactly the lab's shape. That is a valid
workaround (see Alternatives) but it is not "subscription only": it is a second
paid credential.

### Why the CLI route is viable

The lab **already** shells out to the Claude Code CLI in headless mode, paid for
by the subscription — `scripts/bench_run.py:1430` (`build_claude_cmd`):

```python
cmd = ["claude", "-p", "--dangerously-skip-permissions",
       "--output-format", "stream-json", "--verbose"]
```

The capability ("send Claude a prompt, get text back, billed to the plan") is
present in this repository. This plan exposes it to the judge/audit path.

---

## Constraints discovered during design (read before implementing)

1. **The transport is duplicated four times.** `bench_audit.call_gateway`,
   `tasks._worker_gateway`, `case_summary._gateway` and
   `deepeval_adapter._gateway_chat` are four near-identical copies of the same
   request (differing only in timeout — 180s / 120s / 60s — and in
   `deepeval_adapter` using `temperature: 0.0` where the others use `0.2`).
   Implementing the CLI fallback *before* extracting the seam means writing it
   four times. Milestone 1 exists for this reason and must land first.

2. **The Claude CLI is only installed in the runner image.**
   `Dockerfile.runner:72` (`npm install -g @anthropic-ai/claude-code`). The
   audit and the judges execute in the API/worker container built from
   `Dockerfile.api`, which does **not** have the binary. This is the one real
   infrastructure cost of the plan; Milestone 3 decides and implements it.

3. **Model defaults are all OpenAI-family** and will 404 against any Anthropic
   endpoint: `DEFAULT_AUDITOR_MODEL = "gpt-5.4-mini"`
   (`scripts/bench_audit.py:69`), `DEFAULT_JUDGE_MODEL = "gpt-5.4-mini"`
   (`src/harness_lab_app/harness_lifecycle/judge.py:22`),
   `DEFAULT_SUMMARY_MODEL = "gpt-5.4-nano"`
   (`src/harness_lab_app/case_summary.py:24`). Defaults must become
   transport-aware, not just documented.

4. **`DEEPEVAL_MODEL` is not web-editable.** It is absent from `_SPECS` in
   `src/harness_lab_app/settings_service.py:103-239`, so the documented cost
   lever (`DEEPEVAL_MODEL=off`) can only be set by hand-editing `.env`. In
   practice it is inherited from `AUDITOR_MODEL`, which *is* editable.

5. **Skips must stay skips.** `deepeval_adapter` is explicit that a metric which
   could not run returns no score rather than zero. A CLI transport that fails
   must produce the same `skipped_reason` path, never a 0.0.

---

## Overall progress

- [x] Milestone 1 — Extract the single LLM transport seam (no behaviour change)
- [x] Milestone 2 — Claude CLI transport behind that seam
- [x] Milestone 3 — Make the CLI reachable from the API/worker container
- [x] Milestone 4 — Transport-aware model defaults and resolution
- [x] Milestone 5 — Documentation, settings surface, and onboarding matrix
- [x] Milestone 6 — End-to-end validation on a subscription-only `.env`

---

## Milestone 1 — Extract the single LLM transport seam

Pure refactor. No new capability, no behaviour change; the diff should be
provably neutral.

- [x] Create `src/harness_lab_app/llm_transport.py`, stdlib-only for the same
      reason `gateway_models.py` is (scripts and the FastAPI app both import it;
      `scripts/bench_audit.py:41,50` already imports from the package, so this
      works from `scripts/` unchanged).
- [x] Public surface: `chat(model, system, user, *, timeout, temperature) -> str`
      and `available_transport() -> Literal["gateway", "claude_cli", "none"]`.
- [x] Move the gateway request into it, preserving the details that were
      learned the hard way: the `GATEWAY_USER_AGENT` override (Cloudflare bans
      urllib's default UA — `gateway_models.py:37-41`), `normalize_model_name`,
      and the in-container `rewrite_localhost` → `docker_host_alias` rewrite.
- [x] Keep per-caller timeout and temperature as arguments; do not unify the
      values (180/120/60s and 0.2/0.0 are deliberate).
- [x] Repoint all four call sites at it: `bench_audit.call_gateway` becomes a
      thin wrapper (keep the name — `lab_improve.py:41` imports it),
      `tasks._worker_gateway`, `case_summary._gateway`,
      `deepeval_adapter._gateway_chat`.

**Validation:** `make check` clean; existing tests pass untouched, in particular
`tests/harness_lab_app/test_deepeval_adapter.py`; a diff review confirming no
behavioural delta.

## Milestone 2 — Claude CLI transport behind the seam

- [x] Add `_claude_cli_chat()` to `llm_transport`: `claude -p
      --output-format json [--model <id>]`, prompt on stdin, system prompt
      via `--append-system-prompt` (verify the flag against the installed CLI
      version before relying on it; fall back to prepending the system text to
      the user message if unavailable).
- [x] Resolution order in `available_transport()`: gateway if
      `GATEWAY_BASE_URL` **and** `GATEWAY_TOKEN` are set → else `claude_cli` if
      `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is set **and** the binary
      is on `PATH` → else `none`. The gateway keeps priority so no existing
      deployment changes behaviour.
- [x] Enforce a timeout and treat non-zero exit, empty stdout and unparseable
      JSON as transport failure — surfaced as the callers' existing failure
      modes (skip / fall back to manual / raise), never as a score.
- [x] Record the transport in provenance: `deepeval_adapter` already writes
      `judge_transport` (`gateway` | `provider-default`); extend the vocabulary
      with `claude-cli` so a score always says how it was produced.

**Validation:** unit tests with a stubbed `subprocess` covering success, non-zero
exit, timeout, empty output and malformed JSON; a test asserting gateway
priority when both credentials are present; a test asserting the DeepEval skip
path still yields `skipped_reason` and no value.

## Milestone 3 — Make the CLI reachable from the API/worker container

Two options; pick one and record the choice in an ADR.

- [x] **Option 3a (recommended):** install `@anthropic-ai/claude-code` in
      `Dockerfile.api`, mirroring `Dockerfile.runner:72`. One line; costs image
      size and a Node toolchain in the API image.
- [ ] **Option 3b (not taken):** spawn a short-lived runner container per judge call,
      reusing the existing execution backend. Cleaner isolation, materially
      slower per call, and more moving parts in the worker.
- [x] Confirm the credential actually reaches the chosen process: the API and
      worker containers do **not** export `.env` into their environment, which
      is why `deepeval_adapter._env_or_dotenv` falls back to
      `read_env_file_value` (`deepeval_adapter.py:84-99`). The CLI reads its
      token from the environment, so the transport must inject it explicitly
      into the subprocess env rather than assuming inheritance.
- [x] Write the ADR recording the seam, the resolution order and the 3a/3b
      decision. Landed as `0043-one-llm-transport-gateway-first-claude-cli-second.md`;
      the number `0010` this plan proposed was already taken.

**Validation:** `make app-up`, then a judge call executed inside the worker
container with `GATEWAY_*` unset and only `CLAUDE_CODE_OAUTH_TOKEN` present.

## Milestone 4 — Transport-aware model defaults

- [x] Make the three defaults resolve per transport instead of being constants:
      keep `gpt-5.4-mini` / `gpt-5.4-nano` for the gateway, and use Claude ids
      (`claude-sonnet-5` for audit/judge, `claude-haiku-4-5` for summaries) when
      the transport is `claude_cli`. Explicit `AUDITOR_MODEL` / `IMPROVE_MODEL`
      / `CASE_SUMMARY_MODEL` / `DEEPEVAL_MODEL` always win.
- [x] Preserve `judge_model()`'s documented resolution order
      (`deepeval_adapter.py:124-151`): `DEEPEVAL_MODEL` (with `off`/`none`
      disabling) → `AUDITOR_MODEL` → default *only when a transport exists* →
      `""`. The third step currently reads "when the gateway is configured";
      it becomes "when any transport is available".
- [x] Error messages must name the actual problem. A model id that cannot exist
      on the active transport should say so, not surface a bare 404.

**Validation:** unit tests pinning the resolved default per transport and the
override precedence; a test that `DEEPEVAL_MODEL=off` still disables judging on
the CLI transport.

## Milestone 5 — Documentation and settings surface

- [x] `docs/onboarding.md:29-53`: the Path A / Path B split currently presents
      the two credential paths as mutually exclusive and concludes "with only
      Path B you can benchmark the `claude` harness, but the audit → improvement
      loop needs the gateway." Replace the matrix with three columns
      (gateway-only, Claude-only, both) and drop the TL;DR once this ships.
- [x] Document the third option that already works with **no** code change, for
      anyone who has an Anthropic console key: `GATEWAY_BASE_URL=https://api.anthropic.com/v1`
      with `GATEWAY_TOKEN=sk-ant-api…` and the `*_MODEL` overrides. Note
      Anthropic's own caveat that the compatibility layer is intended for
      testing rather than production, and that the lab never sends `max_tokens`
      (worth a smoke test on first use).
- [x] `docs/known-limitations.md:129-135`: the DeepEval paragraph says the judge
      "resolves zero-config through the lab's own gateway" — update for the CLI
      transport.
- [x] Add `DEEPEVAL_MODEL` to `_SPECS` in `settings_service.py` (group
      `GROUP_MODELS`) so the documented cost lever is reachable from the web UI.
- [x] `.env.example`: add a commented subscription-only stanza.

**Validation:** `make check-sync`; a fresh read of `docs/onboarding.md` by
someone who has not seen this plan.

## Milestone 6 — End-to-end validation on a subscription-only `.env`

- [x] With **only** `CLAUDE_CODE_OAUTH_TOKEN` set (no `GATEWAY_*`, no
      `OPENAI_API_KEY`, no `ANTHROPIC_API_KEY`): run a case on the `claude`
      harness, press **Run audit**, generate an improvement proposal, score a
      SWE-bench Pro run, and confirm `task_completion` and `step_efficiency`
      carry values with `judge_transport: claude-cli`.
- [x] Confirm no regression on the gateway path: same sequence with `GATEWAY_*`
      set must still route through the gateway.
- [x] Record measured latency and plan-quota impact per judged run — the CLI
      spawns a process per call and consumes the same subscription quota as the
      runs themselves.
- [x] `/retro` into `memory/learnings.md`.

### Measured, 2026-09-04 (macOS host, Claude CLI 2.1.261)

Each row is a real call, not a mock. The subscription-only column ran against a
`.env` holding `CLAUDE_CODE_OAUTH_TOKEN` and nothing else.

| Call | Subscription-only (`claude_cli`) | Gateway |
|---|---|---|
| Transport resolved | `claude_cli`, judge model `claude-sonnet-5` | `gateway`, judge model `gpt-5.4-mini` |
| Case-summary polish | 5.5 s | 1.7 s |
| DeepEval trajectory set (3 metrics) | 20.6 s — `task_completion` 0.90, `step_efficiency` 1.00, `tool_correctness` 1.00 | 5.8 s — identical three scores |
| Full auditor prompt (2.3 KB system + 86 KB user, a real run's log and diff) | 38.4 s, 7.5 KB audit body | — |

Two things this exercise established that the unit tests could not:

*The auditor prompt must travel on stdin.* The real prompt measured 86 KB; that
is comfortably under the OS argv limit but close enough that a larger diff would
not be, and there is no reason to gamble on it.

*The gateway is roughly 3–4× faster per judged run.* The CLI pays a process
spawn and the subscription's own scheduling on every call. It is a fallback that
works, not a performance equal — which is another reason the gateway keeps
priority when both are configured.

**One bug this validation caught.** `_tool_correctness` constructed
`ToolCorrectnessMetric()` outside its `try`, and DeepEval builds an OpenAI client
in that constructor which raises on a missing `OPENAI_API_KEY`. On a
subscription-only lab that killed all three trajectory metrics before any of them
ran. The metric is deterministic and never calls a model, so it is now handed the
lab's own judge purely to satisfy the constructor, and the construction moved
inside the `try` so a future constructor failure is one skipped metric rather
than three.

---

## Alternatives considered

| Option | Teammate needs | Code change | Verdict |
|---|---|---|---|
| **A. LiteLLM virtual key** on the lab owner's existing gateway | nothing beyond their Claude token | none | **Works today.** Fastest unblock; the owner pays for audit/judge traffic (small next to run traffic) and can cap it with per-key budgets. Use this while this plan is unimplemented. |
| **B. Claude CLI transport** (this plan) | Claude subscription only | this plan | The durable fix; makes the repo shareable without owner-hosted infra. |
| **C. Anthropic OpenAI-compat endpoint** | Anthropic console API key with credits | none (env only) | Works today, but is a second paid credential — does not satisfy "subscription only". |
| **D. Teammate-local LiteLLM** | own provider key + a container | none | Only worth it if they also want to mix providers. |
| **E. Degraded mode** (`DEEPEVAL_MODEL=off`, no audits) | nothing | none | Loses the audit → improvement loop, which is much of the lab's value. |

## Open questions

- Does the installed Claude Code CLI version support `--append-system-prompt` in
  `-p` mode? If not, decide between prepending the system text and adopting
  `--system-prompt`. Verify against the pinned version, do not assume.
- Should CLI-transport judging be rate-limited or serialized? A batch run
  scoring many trials would spawn many CLI processes against one subscription.
- Is `judge_transport: claude-cli` enough provenance, or should the CLI version
  string travel with the score the way `deepeval_version` does?
