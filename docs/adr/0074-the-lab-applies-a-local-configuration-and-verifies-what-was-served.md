# ADR-0074: The lab applies a local configuration and verifies what was served

- **Status:** Accepted
- **Date:** 2026-11-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0020](0020-context-window-preflight-and-alias-merge.md),
  [ADR-0063](0063-the-context-limit-opencode-is-told-is-read-off-the-serving-host.md),
  [ADR-0069](0069-a-model-is-judged-by-the-tier-it-does-reliably.md),
  spec [`2026-11-08-local-model-tuning-design.md`](../superpowers/specs/2026-11-08-local-model-tuning-design.md),
  guide [`guide-local-models.md`](../guide-local-models.md)

## Context

Developers trying local models (LM Studio, Ollama) on their own machines chose the
context length, GPU offload and batch sizes by guesswork. A wrong configuration
surfaced 20–45 minutes into a run, as a slow or failed agent loop. The lab already
probed served context windows (ADR-0020, ADR-0063) but had no place to choose,
apply and test a configuration.

Two facts constrain the design:
- **Server APIs differ, and both can serve something other than what was asked.**
  LM Studio 0.4 loads with parameters and echoes the applied configuration. Ollama
  takes some options per request and others only as server environment variables.
- **The serving host is shared.** On 2026-11-07, New evaluation's *Check
  configuration* sent real completions through the gateway to its default executor,
  and LM Studio loaded a 22 GB model next to the one an experiment was using. Nothing
  warned about it, and nothing recorded it.

## Decision

1. **Hybrid apply-and-verify.** The lab estimates memory and recommends a
   configuration. It applies the configuration where the server's API allows it, and
   names the manual step where the API does not. After every load it reads back the
   served configuration and shows *requested / served*. The served values, never the
   requested ones, drive the estimate and the tests.
2. **The memory estimate states its confidence**: *exact* (server-reported
   architecture), *good* (catalogued architecture) or *rough* (a range derived from the
   parameter count). Hybrid and sliding-window architectures keep a KV cache on only
   part of their layers, so a size-only formula would overstate them about twofold.
3. **A host guard, soft by design.** Before the page loads, unloads or tests on a host,
   it lists the lab runs (running or queued) whose model is served by that host:
   - a direct `lmstudio/` or `ollama/` model maps to its base URL;
   - a gateway alias maps to its card's `api_base`;
   - cloud models never match.

   The user may continue anyway. That choice writes a warning event on each running
   run, so the experiment carries the explanation. The same guard asks before *Check
   configuration* would load a model that is not loaded.
4. **Tests run in the API process, not on the Celery `runs` queue.** That queue has
   concurrency 1 and may be busy for hours with an experiment. One test session per
   host at a time.
5. **Profiles are compared only within one machine and one model.** Speed is a
   property of the hardware.

## Consequences

- The lab now writes to a model server (load and unload) for the first time. The guard
  and the recorded event are what keep that from silently distorting an experiment.
- The architecture table must be maintained as model families appear. An unknown
  architecture degrades to a *rough* range, never to a wrong exact number.
- Ollama's flash attention and KV-cache type stay outside the lab's control, and the
  page says so instead of pretending to apply them.
- The page teaches as much as it measures. The parameter explanations live in
  `guide-local-models.md`, and tooltips point there.

## Alternatives considered

- **Measure only, never load.** Rejected: the user still has to translate a
  recommendation into a server UI by hand, which is where the mistakes happen.
- **The lab loads and sweeps configurations unattended.** Rejected for now: a sweep
  ties up a shared GPU for hours and couples the lab tightly to server versions.
- **Extend Settings → Check context.** Rejected: Settings configures the lab. A guided,
  explanatory flow needs its own page.
- **Block every action while a run uses the host.** Rejected: most runs use cloud
  models and are unaffected, and a hard block teaches users to stop the lab instead.
