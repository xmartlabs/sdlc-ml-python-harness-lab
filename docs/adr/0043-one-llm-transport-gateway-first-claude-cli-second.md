# ADR-0043: One LLM transport for the lab, gateway first and the Claude CLI second

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Marcos Soto
- **Related:** [ADR-0008](0008-llm-audit-in-web.md),
  [ADR-0012](0012-dynamic-gateway-model-list.md),
  [ADR-0014](0014-web-editable-env-allowlist.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md),
  `src/harness_lab_app/llm_transport.py`,
  `docs/plans/2026-09-claude-native-llm-transport.md`

## Context

The lab makes two very different kinds of LLM call. A **run** is an agent working
inside a container through a harness (`claude`, `opencode`, …), and the `claude`
lane already authenticates with a subscription token. Everything else — the
behavioural audit, the improvement synthesis, the SWE-bench Pro judge, the
case-summary polish, the AI case draft and two of the three DeepEval trajectory
metrics — is an **in-process call**: a single (system, user) pair sent from the API, the worker
or a script, expecting text back.

Every one of those in-process calls spoke OpenAI-shaped HTTP against
`GATEWAY_BASE_URL` + `GATEWAY_TOKEN`, and each of the five call sites built that
request itself — same URL suffix, same bearer header, same
`choices[0].message.content` read, written five times.

That had two costs. The duplication is the small one. The real one is the
onboarding cliff: an engineer whose only credential is a Claude subscription
passes the preflight (`run_service.py` accepts a Claude credential and returns
before any gateway check), benchmarks the `claude` harness successfully, and then
finds that every judged feature either exits, raises, or silently degrades. The
lab looked configured and was not. Buying a second, paid credential to *judge*
runs the subscription already paid to *execute* is a poor first experience and,
for a teammate trying the lab out, often not an option at all.

Meanwhile the Claude Code CLI is already installed and already authenticated in
this lab, and `claude -p --output-format json` with every tool disabled is a
perfectly ordinary text completion — no agent loop, no permission prompts, no
filesystem reach.

## Decision

**One transport module, and a fixed resolution order that puts the gateway
first.**

`harness_lab_app.llm_transport` owns "send a (system, user) pair to a model and
return the text". It resolves *how* at call time:

1. **Gateway** — when `GATEWAY_BASE_URL` and `GATEWAY_TOKEN` are both set.
   Unchanged wire format, unchanged model names, unchanged behaviour.
2. **Claude CLI** — when they are not, but `CLAUDE_CODE_OAUTH_TOKEN` or
   `ANTHROPIC_API_KEY` is set and `claude` is on `PATH`. The call is
   `claude -p --output-format json --tools "" --strict-mcp-config`, the prompt
   on **stdin**, the system prompt via `--append-system-prompt`, run in an empty
   temporary directory.
3. **None** — an explicit outcome, not an error swallowed somewhere.

Three consequences are load-bearing:

**The gateway always wins when it is configured.** That ordering is the entire
compatibility guarantee for a multi-model setup: adding a Claude credential to a
lab that has a gateway changes nothing about which model judges. It is asserted
by a test, not left to convention (`test_the_claude_cli_judges_only_when_there_is_no_gateway`).

**Model names are transport-specific, so defaults are too.** `gpt-5.4-mini`
means nothing to the CLI and `claude-sonnet-5` means nothing to a gateway that
does not proxy it. `default_audit_model()` and `default_summary_model()`
therefore answer per transport, and an explicitly configured `AUDITOR_MODEL` is
still passed through verbatim — a name the user chose is never rewritten.

**A failed call still is not a zero.** The transport raises; each caller
converts that raise into the outcome it already documented — the auditor exits
with its message, the SWE judge falls back to manual scoring, the case summary
returns `None`, a DeepEval metric records a skip reason, the case assistant
refuses with a message that names both credentials. Nothing invents a value,
which is the same rule ADR-0038 protects.

The CLI reports API failures in-band (exit code 1 *and* `"is_error": true` *and*
the error text sitting in `result` where an answer would be), so the parser
checks all three rather than trusting the exit code.

The CLI is installed into `Dockerfile.api` — the image the API and the worker
both run — for the same reason `Dockerfile.runner` has it. Credentials are not
exported into those containers, so the transport reads them the way every other
in-process caller already does (env, then the bind-mounted `.env`) and injects
the one it found into the subprocess environment explicitly.

## Consequences

**Good.** A Claude subscription is now sufficient to run the lab end to end. The
five duplicated HTTP bodies became one (the case assistant,
`case_draft_service.py`, was the last to move — it had been missed in the first
pass and left the "Draft it with AI" button gateway-only on a subscription lab). Adding a third transport later is a
function, not a fifth copy. `judge_transport()` is recorded in each judged
metric's provenance, so a score says which transport produced it.

**Costs.** `Dockerfile.api` now carries Node 20 and the Claude CLI, which is
weight the gateway-only path does not use. A CLI subprocess is slower per call
than an HTTP request and its latency is not tunable. Scores produced under
different transports are, strictly, produced by different judges — provenance
records this, but a comparison across them is still the reader's responsibility.

**Rejected alternatives.** *Pointing the existing gateway client at Anthropic's
OpenAI-compatible endpoint* works and stays documented as the no-code option,
but it requires a paid API key and so does not solve the subscription case.
*Making the CLI the only transport* would break the multi-model setup this lab
exists to compare. *Falling back from a failing gateway to the CLI* was rejected
deliberately: silently switching judges mid-experiment produces two incomparable
numbers under one name.
