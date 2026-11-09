# Tune your local model: a guided page for running models on your own machine

- **Status:** Implemented (2026-11-09), all three stages, on `feat/local-optimizator`. See "What implementation taught" at the end.
- **Branch:** `feat/local-optimizator`
- **Related:** [ADR-0020](../../adr/0020-context-window-preflight-and-alias-merge.md)
  (context-window probe),
  [ADR-0063](../../adr/0063-the-context-limit-opencode-is-told-is-read-off-the-serving-host.md)
  (served window), [ADR-0069](../../adr/0069-a-model-is-judged-by-the-tier-it-does-reliably.md)
  (small models), [`guide-small-models.md`](../../guide-small-models.md),
  [`local-model-runtime-config.md`](../../local-model-runtime-config.md)

## Context

Developers who want to try models on their own laptop or a LAN box hit the same wall:
they choose a context length, GPU offload and batch sizes by guesswork. They also cannot
tell whether a slow or failing run comes from the model, the configuration, or the
hardware. Most losses of small local models in agent loops are configuration failures,
not reasoning failures (`guide-small-models.md`). A run of a real case through opencode
takes 20–45 minutes, so a wrong configuration is expensive to discover.

The lab already holds most of the pieces:
- direct-host clients for LM Studio and Ollama (`lmstudio_direct`, `ollama_direct`,
  `direct_hosts`);
- the context-window probe (`gateway_models.probe_model_context`);
- the served-window record on every run (`metadata.context_windows`);
- the transport-failure classifier and the `InfoTooltip` component.

What is missing is a place that teaches the parameters, estimates whether a
configuration fits, applies it, and measures it.

Two server facts shape the design (checked against LM Studio 0.4.21 on 2026-11-08):

- **LM Studio** `GET /api/v1/models` reports, per downloaded model and without loading it:
  - `size_bytes`, `quantization.name` and `bits_per_weight`;
  - `architecture`, `params_string` and `max_context_length`;
  - capabilities (tool use, reasoning);
  - for each loaded instance, its applied `config`: context length, eval and physical
    batch, flash attention, KV-cache offload and parallel slots.

  `POST /api/v1/models/load` accepts `context_length`, `eval_batch_size`,
  `flash_attention`, `offload_kv_cache_to_gpu` and `num_experts`. With
  `echo_load_config: true` it returns the configuration actually applied.
  `POST /api/v1/models/unload` takes an `instance_id`. Completions on `/api/v0` return
  `stats.tokens_per_second` and `stats.time_to_first_token`.

  LM Studio does **not** report layer count, attention heads or head dimension, and
  no server reports the machine's total RAM or VRAM.
- **Ollama** accepts `options.num_ctx`, `num_gpu` and `num_batch` per request.
  `/api/show` reports the architecture numbers, and `/api/ps` reports the size held
  in VRAM. Flash attention and the KV-cache type are server environment settings
  (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`), not request options.

All tasks share one Celery queue (`runs`) with concurrency 1. Anything put on that
queue waits behind an experiment that may run for eight hours.

## Decisions taken in the brainstorm

| Question | Decision |
|---|---|
| Who applies the configuration? | **Hybrid.** The lab estimates and recommends. *Load with this configuration* applies it where the server allows. Otherwise the page says where to change it by hand. The served configuration is always read back and compared with the request. |
| What tests run after loading? | **Quick tests plus an agent check**, a few minutes in total: memory fit, read and write speed at several context sizes, the real window, and a short tool-calling check. A button hands the configuration to New evaluation for a real case. |
| Are results kept? | **Saved profiles.** Each test becomes a profile (machine, server, model, configuration, results). Two profiles of the same machine and model can be compared side by side. One can be marked as recommended. Results from different machines are never compared. |
| Which servers? | **LM Studio and Ollama from the start.** Ollama settings that only its server environment controls are explained, not applied. |
| How is the hardware known? | **Pick a reference machine, plus a command to check it.** A dropdown of typical machines preloads the values. A one-line command per OS shows the exact figure. The machine is saved under a name. |
| What happens when the server is busy with a lab run? | **A soft warning, never a block,** and only when an active run uses the same host (see "Host guard"). Continuing anyway leaves an event on the affected runs. |

## Where it lives

A new sidebar group, **Local models**, between *Evidence* and *Advanced*, with one
page: **Tune your local model** (`/local/tune`, `LocalModels.tsx`). It does not
belong under *Evaluate*: nothing here is an evaluation. Settings configures the lab
itself, and this page is for learning and preparation.

Rejected placements:
- extending Settings → *Check context*: Settings is already 1,000 lines, and a guided,
  explanatory flow does not fit a configuration page;
- a separate tool outside the lab: it would lose the agent check, the served-window
  record, the profiles and the hand-off to New evaluation.

## The user journey

A five-step guided page. Every parameter carries an `InfoTooltip` with what it is, why
it matters, and its usual range. Longer explanations link to `docs/guide-local-models.md`.

1. **Your machine.**
   - Pick from a list of reference machines (for example *MacBook Pro M3 · 36 GB
     unified*, *PC · RTX 4090 24 GB + 64 GB RAM*, *Other…*). The pick preloads memory
     type (unified, or GPU + system RAM), GPU or unified GB, and system RAM GB.
   - A copyable command per OS shows the exact figures: `system_profiler
     SPHardwareDataType` on macOS, `nvidia-smi` plus `free -g` on Linux, and
     `nvidia-smi` plus Task Manager on Windows.
   - Enter how much to leave free for the OS and other apps (default 4 GB on unified
     memory, 1 GB of VRAM on a discrete GPU).
   - Save the machine under a name.
2. **Your server.**
   - Choose LM Studio or Ollama, and a URL (localhost, or a LAN IP and port). Defaults
     come from `LMSTUDIO_BASE_URL` / `OLLAMA_BASE_URL`.
   - **Test connection** lists the downloaded models with size, quantization, maximum
     context and capabilities. It also shows which models are loaded and with what
     configuration. Nothing is loaded.
3. **Your model and configuration.**
   - Pick a model. Dropdowns carry reference defaults:
     - context length (8k, 16k, 32k, 50k, 64k, 128k, capped at the model's maximum);
     - GPU offload (all layers, or a fraction);
     - flash attention;
     - KV cache on GPU;
     - eval batch size;
     - number of experts (MoE models only).
   - A **memory bar** updates live: weights + KV cache + overhead, against the
     available memory. It turns amber when the margin is under 10% and red when the
     configuration does not fit. It names the largest lever ("128k context adds
     12 GB of KV cache; try 64k, or keep the KV cache in system RAM").
   - The estimate always shows its confidence (see "Memory estimate").
4. **Load and test.**
   - **Load with this configuration** applies the configuration on LM Studio through
     the API. On Ollama it attaches the options to every request.
   - The page reads back the served configuration and shows a *requested / served*
     difference ("you asked for 64k, LM Studio loaded 50k").
   - It then runs the tests (see "Tests"), each with its own progress line.
   - The lab never unloads a model it did not load without asking first ("loading
     this needs bonsai-27b unloaded — continue?").
5. **Result.**
   - Three verdicts: **Fits**, **Speed** and **Agent**, each with a plain-language
     reading. For example: "With the harness system prompt (~14k tokens), each agent
     step spends ~9 s reading context. A ~90-step tetris run: about 25–45 min."
   - An optional field records what the OS shows as memory in use, so the user
     compares estimate against reality.
   - **Save as profile**, **Compare** with another profile of the same machine and
     model, **Mark as recommended**, and **Use in New evaluation**, which opens the
     wizard with this model selected.

## Host guard

Loading, unloading and testing all act on a server that a lab run may be using.

- Before **Load**, **Unload** or **Run tests**, the lab looks for runs in `pending` or
  `running` whose executor or planner model is served by **the same host**.
  - A `lmstudio/…` or `ollama/…` model maps to its direct base URL.
  - A `gateway/…` model maps to the `api_base` of its LiteLLM card, through the
    routing table `gateway_models` already fetches.
  - Hosts are compared after `rewrite_localhost` normalisation.
  - Cloud models (Claude, OpenAI, and gateway cards with no LAN `api_base`) never
    match.
- **No match:** no prompt.
- **Match:** a modal states which runs are in progress and how many are queued. It
  warns that loading or unloading can evict their model or starve them of memory,
  making them fail or slow down, and that slowed or failed runs distort the
  experiment's comparison. Two buttons: **Cancel** and **Continue anyway**.
- **Continue anyway** writes a `RunEvent` (level `warning`) on each running run of
  that host: "Serving host reconfigured during this run from Tune your local model
  (<action>, <time>)". An ignored warning is therefore visible when the experiment is
  read later.
- **Run tests** shows the same modal with milder copy: the tests share the GPU, so
  both the tests and the run slow down. It writes the same event, with action
  `tests`.

The same guard fixes a related problem in New evaluation (found on 2026-11-07).
*Check configuration* with a `gateway/…` executor sends a real completion and an
~32k-token context probe. Those requests make LM Studio load a model that was not
loaded (the default `gateway/lmstudio-qwen3.6-35b-build`, ~22 GB). When the executor's
host has the model unloaded, *Check configuration* now asks first: "This will load
Qwen 35B (~22 GB) on 192.168.0.223 — continue?". The probe still runs when no host
can be resolved, as it does today.

## Memory estimate

`total = weights + kv_cache + overhead`

- **Weights:** `size_bytes` from the server (the file size is a good proxy for the
  loaded weights of a GGUF). With partial GPU offload, it is split by the offloaded
  fraction between GPU and system RAM.
- **KV cache:**
  `2 × layers × kv_heads × head_dim × context × bytes_per_element × (parallel slots)`.
  Flash attention does not shrink the KV cache in llama.cpp; it shrinks the compute
  buffer. The estimate models that buffer separately, and the tooltip says so.
- **Overhead:** compute buffer plus runtime, a per-backend constant (~0.5–1.5 GB),
  scaled by the eval batch size.

Confidence has three levels, and the UI shows the level:

| Level | When | Source of `layers / kv_heads / head_dim` |
|---|---|---|
| **Exact** | Ollama | `/api/show` model info |
| **Good** | Known architecture | A table in `local_tuning/catalog.py` for llama, qwen2/3, qwen35 (hybrid: only its attention layers keep KV), gemma, gpt-oss, mistral. For hybrid and sliding-window architectures, the table stores the effective per-token KV. |
| **Rough** | Unknown architecture | Derived from the parameter count. Shown as a range, with "check Task Manager / Activity Monitor". |

Unified memory (Apple silicon) has one pool: weights, KV and the OS all compete for it,
and macOS caps GPU-wired memory at about 65–75% of RAM by default. The available figure
for a Mac is `unified_gb × gpu_wired_fraction − reserve`, and the tooltip explains the
cap and the `iogpu.wired_limit_mb` setting that raises it.

Acceptance check: for `prism-ml/bonsai-27b` (Q1_0, 4.7 GB, 50,432 context, flash
attention, KV on GPU), the estimate must land within ±20% of the 8.9 GB dedicated GPU
memory observed on 2026-11-07.

## Tests

Each test has a timeout, and only one test session runs per host at a time.

1. **Fit.** The memory estimate against the machine, before loading. After loading,
   the served configuration replaces the requested one in the estimate.
2. **Speed.** For prompt sizes of 2k, 8k, 16k and the configured window (capped at
   the served window), send one completion with a 128-token answer. Record
   time-to-first-token (prompt processing) and output tokens per second. LM Studio
   reports both in `stats`; on Ollama they come from `prompt_eval_duration` and
   `eval_duration`. Report prompt throughput (tokens/s read) and generation
   throughput (tokens/s written). Translate them into "seconds per agent step at the
   harness system prompt size".
3. **Window.** Reuse `probe_model_context` against the direct host to confirm the
   served window holds the ~14k-token harness prompt plus working room. Below 32k is
   a red verdict, below 64k amber: the same thresholds the run gate uses.
4. **Agent.** Three short requests with a tool schema (a `read_file`, a `write_file`
   and a no-tool question). Pass when the model emits a well-formed tool call for the
   first two, with valid JSON arguments naming a declared tool, and answers the third
   without inventing a call. Failures are classified with the existing
   transport-failure vocabulary (malformed call, unknown tool, no call). A model
   without tool-use capability is reported as such, not as a failure.

Tests do not go through Celery. They run in the API process as a background job with a
status the page polls, so an experiment occupying the `runs` queue cannot starve them.

## Architecture

A new package `src/harness_lab_app/local_tuning/`, layered per `.github/architecture.md`:

| Unit | Layer | Responsibility | Depends on |
|---|---|---|---|
| `memory_estimate.py` | domain | Pure estimate: a breakdown (weights, KV, overhead, GPU vs RAM split), a verdict (fits / tight / does not fit), the confidence, and the largest lever. | — |
| `catalog.py` | domain | Reference machines, parameter options and defaults, the architecture table, and tooltip texts (EN/ES). Data only. | — |
| `servers.py` | infrastructure | One adapter per server behind one `LocalServer` protocol: `list_models`, `served_config`, `load(config)`, `unload(instance_id)`, `complete(prompt, *, tools, max_tokens)`. LM Studio uses `/api/v1/models[/load\|/unload]` and `/api/v0/chat/completions`. Ollama uses `/api/tags`, `/api/ps`, `/api/show` and `/api/chat` with `options`. Reuses `direct_hosts` for HTTP and errors. | `direct_hosts` |
| `probes.py` | application | The four tests, each returning a typed result with its plain-language reading. | `servers`, `memory_estimate` |
| `host_guard.py` | application | Maps active runs to serving hosts. Builds the warning, and writes the `RunEvent`s on *Continue anyway*. | `models`, `gateway_models` |
| `profile_service.py` | application | Save, list, compare (same machine and model only), mark recommended. | `models` |

API routes under `/api/local/…`: machines CRUD, `servers/test`, `models`, `estimate`,
`guard`, `load`, `unload`, `tests` (start) and `tests/{id}` (status), and profiles
CRUD plus compare and recommend.

**Data**, two new tables. They are created by `SQLModel.metadata.create_all`, like the
others; no compat migration is needed for new tables.

- `LocalMachine`: `id`, `name`, `memory_kind` (`unified` | `discrete`), `gpu_gb`,
  `ram_gb`, `reserve_gb`, `notes`, `created_at`.
- `TuningProfile`: `id`, `machine_id`, `server_kind`, `server_url`, `model_id`,
  `requested_config` (JSON), `served_config` (JSON), `estimate` (JSON), `results`
  (JSON: fit, speed, window, agent), `verdicts` (JSON), `observed_memory_gb`,
  `server_version`, `recommended` (bool), `created_at`.

**Frontend:**
- `pages/LocalModels.tsx`, with one component per step (`MachineStep`, `ServerStep`,
  `ConfigStep`, `TestStep`, `ResultStep`);
- `components/MemoryBar.tsx`, `components/ServedConfigDiff.tsx` and
  `components/HostGuardModal.tsx`, the last also used by New evaluation's *Check
  configuration*;
- types in `types.ts` and calls in `api.ts`;
- every string in EN and ES through `defineMessages`.

**Docs:** `docs/guide-local-models.md`, the parameter explainer the tooltips link to,
plus a short entry in `guide-web-ui.md` and an ADR for the hybrid apply-and-verify
decision and the host guard.

## Error handling

- **Host unreachable:** name the usual causes (server off, LM Studio not serving on the
  local network, firewall, wrong port) and show the URL that was tried.
- **Load refused:** show the server's own message. Suggest the largest lever from the
  estimate.
- **Served configuration differs from the request:** a warning with the diff, never an
  error. The served values drive the estimate and the tests from then on.
- **An Ollama setting that cannot be applied per request** (flash attention, KV cache
  type): shown as "set `OLLAMA_FLASH_ATTENTION=1` on the server and restart it",
  never silently ignored.
- **Test timeout:** that test is marked inconclusive, the rest still run.
- **A test session already running on that host:** the start is refused, and the
  running one is shown.

## Testing

- `memory_estimate`: unit tests against known answers. They include the bonsai-27b
  acceptance check above, a dense llama-architecture model, a MoE model and a
  unified-memory Mac with its wired-memory cap. The rough level must return a range
  that contains the exact value for at least the catalogued architectures.
- `servers`: adapter tests against recorded LM Studio 0.4 and Ollama responses
  (fixtures), with no network. They cover load echo, the served-config read-back,
  unload, completion stats and error payloads.
- `probes`: speed, window and agent verdicts from fixture completions, including a
  malformed tool call, an unknown tool and a model without tool use.
- `host_guard`: active runs seeded in the DB, one per kind (direct LM Studio on the
  host, gateway card pointed at the host, gateway card pointed elsewhere, Claude, a
  completed run). Only the first two match. *Continue anyway* writes one event per
  running run.
- `profile_service`: compare refuses different machines or models.
- API route tests for the guard and the profile flow. Frontend: `tsc` and the
  existing component test pattern for `MemoryBar` thresholds.
- `make check` green at the end of each stage.

## Stages

Each stage is usable on its own.

| Stage | Delivers | Risk |
|---|---|---|
| **1. Explore and learn** | Machine, server connection, model list, configuration with tooltips, memory bar, read-back of the served configuration of loaded models, `guide-local-models.md`. Read-only: never loads, unloads or tests. | None: it does not touch the server. |
| **2. Load and test** | Load/unload (LM Studio API, Ollama per request), the requested/served diff, the host guard with its `RunEvent`, the four tests with plain-language verdicts, and the guard on New evaluation's *Check configuration*. | Medium: it acts on a shared server. |
| **3. Profiles** | Save, compare, mark recommended, *Use in New evaluation*. | Low. |

## Out of scope

- Unattended configuration sweeps.
- Downloading models or choosing quantizations.
- Other servers (vLLM, the llama.cpp server).
- Automatic hardware detection.
- Comparing or recommending across machines.
- Running full cases inside this page: *Use in New evaluation* hands that off.

## Open questions for the plan

- The reference-machine list and the architecture table need real figures. Source
  them from vendor specifications and the models' `config.json`, and record the
  source next to each entry.
- Whether `/api/v1` completions also return `stats` on LM Studio 0.4, or whether the
  speed test must stay on `/api/v0`. Check against the live server before
  implementing `servers.py`.

## What implementation taught (2026-11-09)

Verified live against LM Studio 0.4.21 serving `prism-ml/bonsai-27b` on an RTX 24 GB:

- **Each LM Studio load adds an instance; it never reconfigures one.** Two test loads
  left three copies of bonsai in VRAM. `/load` now reuses a loaded instance whose
  served config matches. When the config differs, it answers 409
  `{loaded_instances, message}`, and `replace: true` unloads those instances first.
  Unload targets the `instance_id` the lab loaded. Without it, unload refuses (409)
  when the model has more than one instance, unless `force: true`.
- **`parallel` does not multiply the KV cache.** llama.cpp splits `n_ctx` across slots.
  LM Studio served `parallel: 4` at the 8.9 GB observed, so the estimate counts the
  context once.
- **LM Studio reports `qwen35moe`**, and the 35B-A3B fell to the rough estimate (47 GB
  of KV). The architecture table covers it now. A rough MoE estimate scales with the
  *active* parameters (floor 8B). The rough heuristic is recalibrated to
  `0.04 × params^0.55` MB/token.
- **Reasoning models spend a small token budget thinking** (`content` empty,
  `reasoning_content` full). The agent test uses 1,024 tokens.
- **Every Ollama completion carries the load options.** Without them Ollama reloads at
  its default 4k context, and the window test would read a stale value.
- **The guard reports `routing_unknown`** when the gateway's `/model/info` does not
  answer. Gateway runs on that host could not be checked, and the modal says so instead
  of reading "clear".
- **Server URLs are validated:** `http`/`https` only; no query, fragment or userinfo;
  only a root, `/v1` or `/api` path. Reflected server errors are truncated to 300
  characters.
- The architecture-table numbers for `qwen35`, `gemma` and `gpt-oss` are still
  approximations. bonsai (qwen35 27B) lands at 9.3 GB estimated against 8.9 GB observed.
