# ADR-0099: A local model is a door of its own

- **Status:** Accepted
- **Date:** 2026-11-23
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0090](0090-the-navigation-is-grouped-by-capability.md),
  [ADR-0093](0093-the-lab-is-driven-by-guided-flows.md)
- **Related:** [ADR-0074](0074-the-lab-applies-a-local-configuration-and-verifies-what-was-served.md),
  [ADR-0095](0095-an-adhoc-task-runs-on-a-copy-and-is-applied-on-request.md),
  guide [guide-local-models.md](../guide-local-models.md)

## Context

A question people now arrive with is *"I am about to travel — can the model on my
laptop do real work without internet?"*. The lab could already answer it, but the
answer was hard to reach and stopped halfway:

1. *Tune your local model* sat two levels down, inside *Models and harness*. Home's
   fifth door and the sidebar's *Try* group offered *Your repo*, whose content
   already had two other ways in from Home (the *Start in 30 seconds* band and the
   *Your team's repo* journey).
2. Step 1 read as a hardware form to fill in exactly, while its only job is a
   memory estimate. Step 2 assumed a server was already running and said nothing
   about installing LM Studio or Ollama, nor about Ollama's 4k default context,
   which is below the ~14k-token harness prompt.
3. The address typed in step 2 served the tests only. Agent runs read
   `LMSTUDIO_BASE_URL` / `OLLAMA_BASE_URL` from settings, so a model could be tuned
   on one server and run on another without the page saying so.
4. Step 5 ended on *Use in New evaluation*, the lab's most technical form, with no
   guided way to try the model on a real task.

## Decision

- **Local model replaces Your repo** as Home's fifth door and as a sidebar entry
  in *Try*, both opening `/local/tune`. The entry owns `/local/`, so it stays
  highlighted on *Local results* too; *Models and harness* no longer owns it. The
  *Your repo* hub keeps its route, `/your-repo`, and is reached from Home's band
  and journey.
- **Step 1 says it is a pre-configuration:** pick your machine or the closest one
  and go on. The exact-figures commands and *Save this machine* fold under an
  optional disclosure.
- **Step 2 teaches the server it asks for.** Until a connection test succeeds it
  shows, for the chosen server, three steps, one copyable command and links to the
  official download, server guide and tool-capable model pages. It does not copy
  install instructions that change with every release. It names the two things an
  agent needs (tool use, a 32k+ context) and Ollama's 4k default.
- **One server for tests and runs: the one that answered.** A successful
  connection test whose address differs from the one runs use writes it, in its
  `/v1` form, through the existing settings endpoint, refreshes the model list and
  says which address it replaced. Most operators run the server on `localhost` or
  a LAN box of their own, and the lab's `.env` holds whoever configured it first;
  a tested address that runs then ignored is what made a step-5 run fail on a
  model the page had just passed (2026-11-23). An explicit-click version shipped
  first and was replaced the same day for that reason. When the write fails,
  step 2 offers *Use this server for runs* to retry, and step 5 shows it *instead
  of* the run links.
- **Step 5 ends on real work, with OpenCode and the model already chosen:** the
  first clinic case (`clinic-slots-t1`) in the *Models* flow, or an ad hoc task on
  a folder of the operator's. Both pages accept the arm as a query parameter
  (`?first=` and `?arm=`, in the `harness|model` form the choosers use). A failed
  agent test is flagged before either link. *New evaluation* remains as the
  advanced option.

## Consequences

- The laptop-offline question has a direct way in, and the path from installing a
  server to a graded run on a real case no longer needs the operator to know about
  settings keys or the run form.
- *Your repo* loses its sidebar entry; anyone who used it from there now goes
  through Home or the URL. The hub itself is unchanged.
- The setup box links to third-party pages that can move. The links were checked
  on 2026-11-23 and live in one constant (`SERVER_LINKS` in `LocalModels.tsx`).
- Testing a server on this page changes the server every later run uses, including
  other operators' runs on a shared lab. The page says so when it happens (old and
  new address), and the setting stays editable in *System → Settings*.
- The run preflight's "unknown model" refusal names the host it asked and the
  setting that chose it, for LM Studio and Ollama alike.
- Every test is kept: *Run tests* files it under a saved machine, saving the
  step-1 figures as one when none is picked (the backend keeps history only for a
  saved machine, and step 1 no longer asks anyone to save). *Saved results*
  (`/local/results`) is linked from the top of the page and from step 5, so past
  tests are one click away without running a new one.
