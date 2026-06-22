# Runtime Portability and Model Tiers

> Status: **Both runtimes are LIVE.** Claude Code emits a `model:` alias into each
> `.claude/agents/<name>.md` by tier (planning/review on the flagship, execution on
> Sonnet to save tokens). **OpenCode is operational too**: `opencode.json` ships
> env-driven providers for Ollama / LM Studio (self-hosted) plus built-in OpenAI and
> OpenCode Zen, configured entirely through `.env` — no IPs or keys are committed.

## Why tiers, not model ids

Agents (`.github/agents/`) and skills reference a **tier**
(`planner` / `executor` / `fast`), never a concrete model id. Switching runtime is
then a one-table change: the same governed agent maps to `claude-opus` on Claude
Code and to a self-hosted `qwen`/`llama` on OpenCode.

| Tier       | Role in the SDLC                         | min context | Claude Code (live alias) | OpenCode self-hosted (intended) |
|------------|------------------------------------------|-------------|--------------------------|---------------------------------|
| `planner`  | orchestrator, planner, reviewer          | large       | `opus`                   | `qwen2.5-coder:32b`             |
| `executor` | implementer, tester                      | medium      | `sonnet`                 | `qwen2.5-coder:14b`             |
| `fast`     | documenter, quick edits                  | small       | `haiku`                  | `llama3.1:8b`                   |

The Claude column uses **aliases** (`opus`/`sonnet`/`haiku`), not pinned ids, so each
agent always resolves to whatever that tier's current model is — "el que esté
disponible". The mapping lives in `src/ml_python_base/skills_sync/agents.py`
(`_CLAUDE_TIER_MODEL`). The exact local models are placeholders — pick what your
hardware runs. Keep the **tier names** stable; only the right-hand mapping changes
per runtime.

## OpenCode config (operational, env-driven)

`opencode.json` ships with the providers wired and **every host/model behind
`{env:...}` interpolation**, so the template carries no IPs or keys — you set them
in `.env` (gitignored). Providers:

- `ollama` / `lmstudio` — custom OpenAI-compatible providers; `baseURL` comes from
  `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL`.
- `nvidia` — NVIDIA NIM (free, cloud, OpenAI-compatible) for a big planning model;
  `apiKey` from `NVIDIA_API_KEY`.
- `openai` and `opencode` (OpenCode Zen) — built-in; authenticate with
  `opencode auth login` (keychain) or `OPENAI_API_KEY`.

Tiering is two levels via `model` (main) + `small_model` (lightweight), both from
`.env` (`OPENCODE_MODEL`, `OPENCODE_SMALL_MODEL`). A single local GPU rarely runs
three large models at once, so per-agent 3-tier splitting is intentionally not
forced here; switch the active model anytime with `/models` in the TUI.

### Plan with a big cloud model, execute self-hosted

OpenCode ships two **primary agents** you cycle with `Tab`: `plan` (read-only,
for designing) and `build` (full tools, for executing). `opencode.json` pins a
different model to each, both from `.env`:

```jsonc
// opencode.json (already wired)
"agent": {
  "plan":  { "model": "{env:OPENCODE_MODEL_PLAN}" },  // big cloud model
  "build": { "model": "{env:OPENCODE_MODEL}" }         // self-hosted
}
```

```bash
# .env  (default: no extra API key — Zen free model for planning)
OPENCODE_MODEL_PLAN=opencode/nemotron-3-ultra-free            # plan: Zen free Nemotron Ultra
OPENCODE_MODEL=lmstudio/qwen/qwen3-coder-30b                  # build: your LM Studio box
```

The default planner is **OpenCode Zen** (`opencode/*-free`), which only needs
`opencode auth login` once — no NVIDIA key, nothing breaks out of the box. Other free
Zen options: `opencode/deepseek-v4-flash-free`, `opencode/mimo-v2.5-free`,
`opencode/north-mini-code-free`.

Workflow: open `make opencode`, stay in **Plan** (the free big model) to design the
change, then `Tab` to **Build** (your local Qwen Coder) to implement it — big-model
planning, zero-cost local execution.

Prefer NVIDIA NIM instead? Set `OPENCODE_MODEL_PLAN=nvidia/nvidia/nemotron-3-super-120b-a12b`
and `NVIDIA_API_KEY=nvapi-...` (free key from build.nvidia.com). The id is
`<provider-id>/<api-model-id>`, hence the doubled `nvidia/nvidia/...`.

> Day-to-day operating guide (when to `Tab`, where skills run): see
> [`docs/opencode-workflow.md`](../docs/opencode-workflow.md).

### Setup from the CLI

```bash
brew install anomalyco/tap/opencode   # recommended tap (most up to date)
cp .env.example .env                  # then edit OLLAMA_BASE_URL / LMSTUDIO_BASE_URL / OPENCODE_MODEL
make opencode-doctor                  # check install + that the endpoints answer
make opencode                         # launch the TUI with .env loaded
```

Inside the TUI: `/connect` to add a cloud provider (OpenAI / Zen) and paste its key,
`/models` to switch the active model. `make opencode` loads `.env` for you; for direct
`opencode` use, load it yourself (`direnv`, or `set -a; . ./.env; set +a`) so the
`{env:...}` values resolve.

> LM Studio model ids: the placeholder `local-model` under `provider.lmstudio.models`
> in `opencode.json` must match the id LM Studio serves (Developer/Server tab).

## Graceful degradation for small context windows

Local models usually have smaller context windows than Claude. The lifecycle is
designed to survive this:

- **Fresh context per phase.** The `orchestrator` delegates each SDLC phase to a
  separate agent, so no single context must hold the whole task — the biggest
  token saver when moving to local models.
- **`context_budget` on agents.** Agents declare `small` / `medium` / `large`. A
  projector may drop or down-tier `large`-only agents when the active runtime's
  models cannot satisfy `min context`.
- **Skill degradation.** Long skills can document a shorter "degraded mode" path
  for small-window runtimes. `plan_and_execute_feature` and `brainstorm_quick`
  carry an explicit weak-model path.

### Self-hosted that won't converge: checklist

A weak self-hosted model that loops forever and never produces a working result
(the classic symptom: very long interaction, no runnable artifact) is almost
always one of these — check in order:

1. **Served context window too small.** A model served at 4k-8k cannot hold
   governance + skills + the plan + history. opencode cannot fix this; raise it at
   the server (LM Studio Context Length, Ollama `num_ctx`) to >= 16k-32k. This is
   the #1 cause.
2. **Temperature too high.** Weak coder models wander at default temp; the `plan`
   and `build` agents in `opencode.json` ship at `0.2` (a number, not
   `{env:...}`-interpolable).
3. **Planner↔executor gap.** A strong planner writing for a weak executor produces
   steps the executor can't chew. Self-hosted: use the same coder model for plan
   and build, or a planner only slightly stronger.
4. **Task too big for one execution turn.** Slice into the smallest
   independently-verifiable increment and aim for a runnable milestone first.

Levers 1-3 live in `.env.example` / `opencode.json`; lever 4 is
[`docs/task-sizing.md`](../docs/task-sizing.md).

### A different symptom: `Response too long` (output overrun)

The checklist above fixes the **input** loop (never converges, long interaction). A separate
failure shows up mainly under **GitHub Copilot agent mode**: the model tries to regenerate a
whole file in one shot, the response exceeds Copilot's ceiling, and the turn fails with
`Response too long`. This is an **output** problem — no instruction-file rule or Copilot
setting caps output. The only reliable control point is your model gateway:

- If you front the local model with a LiteLLM gateway, set `max_tokens: 2048` per model in
  `config.yaml`. A capped response cannot overrun, so the failure becomes impossible and the
  cap forces small, targeted edits.

Full mechanical setup (gateway cap, served context for Copilot's ~9k-token prompt, VS Code
settings, why soft rules alone fail): [`docs/local-model-runtime-config.md`](../docs/local-model-runtime-config.md).

### Recommended preset for local vibe coding (`local_model_32k`)

A concrete instance of the tiers above for spikes driven via Copilot / OpenCode / LM Studio
(generic tier support stays intact):

- **Gateway output cap (mechanical, the important one):** `max_tokens: 2048` on the local
  model — makes whole-file rewrites and `Response too long` impossible.
- **Served context:** 32k minimum; 64k if the GPU allows (Copilot's own prompt eats ~9k,
  leaving ~20-22k usable at 32k).
- **Temperature:** 0.2.
- **One model for plan and build** (or a planner only slightly stronger) — mind the
  planner↔executor gap.
- **Driver:** OpenCode (`make opencode`) routed through the ai-gateway — **not** Copilot agent
  mode (its ~9k-token prompt saturates a 32k window). Build = `gateway/...-build` (local),
  plan = strong cloud; usage shows in Langfuse. See `docs/local-model-runtime-config.md`.

Operating rules: [`LOCAL_AGENT.md`](../LOCAL_AGENT.md). Sizing:
[`docs/task-sizing.md`](../docs/task-sizing.md). Mechanical setup:
[`docs/local-model-runtime-config.md`](../docs/local-model-runtime-config.md).

## Activation checklist

1. Install and start Ollama / LM Studio on your GPU host(s); pull the models you set
   in `.env` (`ollama pull qwen2.5-coder:32b`, or load a model in LM Studio).
2. `cp .env.example .env` and set `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL` /
   `OPENCODE_MODEL` / `OPENCODE_SMALL_MODEL`. For LM Studio, also list its served
   model id under `provider.lmstudio.models` in `opencode.json`.
3. `make opencode-doctor` — confirms opencode is installed and each endpoint answers.
4. `make opencode` — run a small task through OpenCode and confirm each SDLC gate
   (`make check`) still passes on the local model.
