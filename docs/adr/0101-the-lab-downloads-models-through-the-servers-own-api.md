# ADR-0101: The lab downloads models through the server's own API

- **Status:** Accepted
- **Date:** 2026-11-26
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0099](0099-a-local-model-is-a-door-of-its-own.md)
- **Related:** [ADR-0074](0074-the-lab-applies-a-local-configuration-and-verifies-what-was-served.md),
  guide [guide-local-models.md](../guide-local-models.md)

## Context

*Tune your local model* step 2 lists only the models the server already holds
(`/api/tags` on Ollama, `/api/v1/models` on LM Studio). On an NVIDIA Jetson set up
months ago, that meant two models and no way to try a third without opening a
terminal on the device, finding a tag on a website and guessing whether it would fit
in 8 GB. The page already knew the machine (step 1) and talked to the server
(step 2), so it was the natural place to find and fetch a model.

Three ways to fetch one were weighed:

- **A. Show a command to copy** (`ollama pull …`, `lms get …`). Cheap, but on a
  remote box the operator still needs a shell there, and the page sees no progress.
- **B. Ask the server to download it over the HTTP API the lab already uses.**
  Ollama has `POST /api/pull` (streamed NDJSON progress) and LM Studio 0.4+ has
  `POST /api/v1/models/download` with `GET …/download/status/{job_id}`. Both
  accept a Hugging Face reference as well as their own catalog names.
- **C. Run the command over SSH.** Needs stored credentials and remote shell
  execution, a large new attack surface, for something B does without either.

## Decision

- **B, with A as the fallback.** Step 2 gains *Get more models* once the server
  answers. A download is a job the API runs in a background thread
  (`local_tuning/pull_jobs.py`, the same shape as `test_sessions.py`), polled by
  the page; one download per host at a time. When a download fails, the row shows
  the equivalent shell command to copy.
- **Three ways to find a model:**
  - *Recommended*: a short curated list (`local_tuning/model_hub.py`) of models
    that run an agent on small machines, each file's size checked against its
    Hugging Face repo. Offline: nothing leaves the lab to show it.
  - *Search Hugging Face*: the public `/api/models` search filtered to GGUF, then
    each repo's file tree for its single-file quantizations. Results are cached
    for ten minutes per query.
  - *Exact name*: anything the server's own download takes (an Ollama tag, an LM
    Studio catalog key, `hf.co/owner/repo:QUANT`, a `https://huggingface.co/`
    link). Ollama's library has no public search API, so it is not scraped.
- **Every file carries what each server is asked for.** Ollama pulls the library
  tag when the curated entry names one, otherwise `hf.co/{repo}:{quant}`; LM
  Studio downloads the Hugging Face link with the quantization beside it.
- **Fit is shown, never enforced.** Each file is estimated with the lab's memory
  model at the step-3 default (32k context) on the step-1 machine, plus the
  largest context option that still fits. A Jetson with 8 GB sees *Fits up to
  16k* rather than a flat *too big*. A download is never blocked by the estimate:
  the model may be meant for another machine, and the estimate can be wrong.
- **A download does not trip the host guard.** It never loads a model, so lab runs
  on the host are reported beside the job, not as a confirmation dialog. Files
  over 10 GB ask for one more click.
- **Only model references reach the server.** The request is validated to a
  model-name character set, or a `https://huggingface.co/` link; any other scheme,
  whitespace or a leading dash is refused with 422, and the server URL goes
  through the existing allowlist. A first path segment that Ollama would read as a
  registry host (a dot, a port, or `localhost`) must be `hf.co`, `huggingface.co`
  or `registry.ollama.ai`, so a request cannot make a LAN server fetch from an
  arbitrary host.
- **A download is checked to load (Ollama).** When the pull completes and no
  other model is loaded, the lab loads it once at a 2k context and unloads it
  right away (`keep_alive: 0`). A file the server cannot run then fails in
  step 2 with a cause, not in step 4 with a blob path. The download itself
  stays *completed*: the file is on the server. When another model is loaded
  the check is skipped, so it never evicts a model someone is using. LM Studio
  is not checked: the key it gives a Hugging Face download is its own.
- **The server version is read and used.** The connection test reports
  Ollama's `/api/version`. A file with a known floor (a library tag's manifest
  `requires`, e.g. `gemma4` needs 0.30.9; a Hugging Face repo that ships a
  separate `mmproj` vision file needs 0.30, the release that moved GGUF models
  to llama.cpp's server) shows a warning on an older server before the
  download. Ollama's bare `unable to load model: <blob>` is explained the same
  way wherever it appears, including step 4's load.
- **The quantization list leads with the usual picks.** Q3_K_M, Q4_0, Q4_K_S,
  Q4_K_M, UD-Q4_K_XL, Q5_K_M, Q6_K, Q8_0 and MXFP4 come first; 1-2 bit,
  importance-matrix and full-precision files sit in a second group of the same
  select. A row opens on Q4_K_M when it fits, else the largest usual pick that
  does.
- **The page survives a long download.** Polling retries transient errors with
  backoff (a proxy 502 twenty minutes in is not fatal), stops when the panel
  unmounts, and releases the page when the API forgets the job. Search answers
  that arrive after a newer request are ignored. These rules live in
  `frontend/src/local/pullPolling.mjs`, covered by `npm run test:local`.

## Consequences

- Trying another model on a remote box no longer needs a shell on it, and the
  page says before the download whether it will fit.
- The *Search Hugging Face* tab needs the API container to reach
  `huggingface.co`. Without internet it says so; the recommended list and exact
  names still work.
- The job registry lives in the API process. A restart forgets the job, while the
  server keeps downloading; the model appears in the next listing.
- With authentication off (the lab's default banner), anyone who can reach the lab
  can start a multi-gigabyte download on a serving host, as they can already load
  and unload models there.
- The curated list goes stale as models are released. Its sizes and Ollama tags
  were checked on 2026-11-26 and live in one constant (`_CURATED_SPECS`).
- Found on the first real test (a Jetson with Ollama 0.24, 2026-11-26): a
  Gemma 4 GGUF from Hugging Face downloaded, then failed to load, and every
  Gemma 4 file read *Too big* because the estimate used Gemma 3 27B's shape.
  Gemma 4 now has its own shapes in the architecture table (few wide KV heads
  on its full-attention layers, narrower ones on its sliding-window layers, and
  no cache at all on the E models' KV-shared layers): E4B at 32k needs about
  0.6 GB of KV cache, not 3.4. Ollama's `/api/show` reads Gemma 4 flat, so the
  lab ignores that shape for any model whose sliding-window layers report their
  own key length.
- LM Studio older than 0.4 has no download API; the page says to update it or to
  download in LM Studio itself. A missing model is also a 404 there, so the
  "update" message is shown only for LM Studio's unknown-route answer.
