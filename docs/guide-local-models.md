# Tune your local model

How to find a configuration that fits your machine and works for agent tasks, using
the **Local models → Tune your local model** page. It also explains what each server
setting does, because the page's tooltips are short and this is where they point.

Design: [`superpowers/specs/2026-11-08-local-model-tuning-design.md`](superpowers/specs/2026-11-08-local-model-tuning-design.md).
Decision: [ADR-0074](adr/0074-the-lab-applies-a-local-configuration-and-verifies-what-was-served.md).
To measure *how reliably* a model does real work once it runs, continue with
[Small & self-hosted models](guide-small-models.md).

## What the page does

1. **Your machine.** Pick one of your saved machines (listed first, under *My
   machines*), a reference machine, or enter your own figures. The lab runs
   in Docker and the server may be on another computer, so it cannot read your
   hardware: you tell it. The page shows the command that prints the exact numbers.
2. **Your server.** LM Studio or Ollama, on `localhost` or a LAN address. *Test
   connection* lists the models you have downloaded without loading any of them.
3. **Your model and configuration.** Choose the settings. The memory bar estimates
   whether they fit before anything is loaded.
4. **Load and test.** The lab loads the model with your settings, reads back what the
   server *actually* applied, and runs four short tests (a few minutes).
5. **Result.** One verdict per test, a plain reading of what it means for an agent run,
   and a profile you can save, compare and hand to *New evaluation*.

The page never loads or unloads a model while a lab run uses that server without
asking you first. If you continue anyway, the affected runs get an event saying so, so
a slow or failed run can be explained when the experiment is read.

## The settings, in plain words

| Setting | What it is | Why it matters | Typical values |
|---|---|---|---|
| **Context length** | How many tokens the model can see at once: instructions, files, tool output and its own answer. | The harness system prompt alone is ~14k tokens. Below 32k an agent run fails on the first step; 64k leaves room to work. Each doubling roughly doubles the KV cache memory. | 32k minimum, 64k comfortable |
| **GPU offload** | The share of the model's layers that run on the GPU. | Layers left on the CPU are several times slower. Lower it only when the model does not fit otherwise. | 100% |
| **Flash attention** | A faster way to compute attention. | Faster prompt reading and a smaller compute buffer. It does **not** shrink the KV cache. | On, when the model supports it |
| **KV cache on GPU** | Where the model keeps what it has already read. | On GPU it is fast but uses VRAM. In system RAM it frees VRAM for a larger context, at a speed cost. | On, unless the context does not fit |
| **KV cache type** | The precision of that cache. | `q8_0` halves the KV memory with a small quality loss. `q4_0` quarters it with a noticeable one. | `f16`, or `q8_0` for long contexts |
| **Eval batch size** | How many prompt tokens are processed per step while reading. | Bigger is faster prompt reading and a larger compute buffer. | 512 – 2048 |
| **Experts** (MoE only) | How many experts each token is routed through. | The model was trained with a fixed number; changing it trades quality for speed. | The model's default |

### Where memory goes

`total ≈ weights + KV cache + overhead`

- **Weights** are about the size of the downloaded file. A 4-bit 27B model is ~17 GB.
- **KV cache** grows with the context length. Some architectures keep it only on part
  of their layers (Qwen 3.5+ hybrid models, Gemma's sliding window, gpt-oss), so they
  need far less than an older model of the same size. That is why the estimate reads
  the model's architecture.
- **Overhead** is the compute buffer and the runtime, typically 0.5–1.5 GB.

The estimate states its confidence:
- **exact** when the server reports the architecture (Ollama);
- **good** when the architecture is in the lab's table;
- **rough** (a range) otherwise.

After loading, write down what Task Manager or Activity Monitor shows. Comparing the
two is how you learn where your memory goes.

### Apple silicon

A Mac has one memory pool shared by the CPU, the GPU and the OS. By default macOS lets
the GPU wire about 65–75% of it. A 36 GB Mac therefore has ~23 GB for a model after a
4 GB reserve, not 36. You can raise the cap with
`sudo sysctl iogpu.wired_limit_mb=<MB>` (it resets on reboot); leave the OS enough to
breathe.

## NVIDIA Jetson (Orin Nano) and other devices you run yourself

Pick **Jetson Orin Nano Super · 8 GB shared (MAXN)** in step 1. The 8 GB are LPDDR5
shared by the CPU and the GPU, unified like a Mac's memory but without macOS's wired
limit, so the whole pool counts. Bandwidth is 102 GB/s in Super/MAXN mode (JetPack
6.1+) and 68 GB/s at the original clocks.

**Evaluate only.** On a device where you start the model yourself (`ollama run …` on
the Jetson, a box someone else runs), choose *I load it myself — evaluate only* in step
4. The lab reads what the server is running and tests exactly that. It never loads,
reloads or reconfigures the model, and asks you to start it when it is not loaded.
On Ollama this matters twice over: a request whose context or batch differs from the
running model makes Ollama reload it, so in this mode every test request repeats the
served context and leaves the batch unset.

Measured on a Jetson Orin Nano Super (Ollama 0.24.0, 2026-11-09): `qwen3:4b` Q4_K_M at
a 16k context sat fully in GPU memory (5.4 GB; estimate 5.8 GB). It read ~560–620
tok/s, wrote ~14–16 tok/s and passed the agent check 3/3. The window failed: 16k is
below the harness's 32k floor.

**Check that the model runs on the GPU.** Ollama reports how much of a loaded model is
in GPU memory. When that is 0, the fit test fails with *running on the CPU*: the server
is not using CUDA. Run `ollama ps` on the device; the PROCESSOR column should read GPU.

## LM Studio and Ollama differ

- **LM Studio (0.4+)** loads through its API with context length, eval batch, flash
  attention, KV-cache offload and experts, and echoes the configuration it applied.
  **GPU offload is set in the app's load panel**; the API does not take it. The same
  goes for the **KV cache type**: tick *K Cache Quantization* and *V Cache
  Quantization* in the load panel, pick the type you chose in the lab and reload the
  model. Otherwise it stays `f16`, and since LM Studio does not echo it back the lab
  cannot detect the mismatch. Turn on
  *Serve on Local Network* to reach it from another machine.
- **Ollama** takes context length, batch and GPU layers *per request*, so the lab sends
  them with every call. Flash attention and the KV cache type are server settings:
  set `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` in the server's
  environment and restart it. Set `OLLAMA_HOST=0.0.0.0` to reach it from the LAN.

Whatever you ask for, the page shows **requested / served**. A server may cap or round
a value (LM Studio, for one, can load a smaller context than requested). The served
value is the one that counts.

## The tests

| Test | What it checks | Verdict |
|---|---|---|
| **Fit** | The memory estimate against your machine, using the served configuration once loaded. | Red when it does not fit, amber under a 10% margin. |
| **Speed** | Prompt reading (tokens/s) and writing (tokens/s) at 2k, 8k, 16k and your window. | Turned into seconds per agent step at the harness prompt size: green under 20 s, amber under 60 s. |
| **Window** | That a ~31k-token prompt really fits in what the server serves. | Red below 32k, amber below 64k (the run gate's thresholds). |
| **Agent** | Three short requests with tools: read a file, write a file, answer without tools. | Green at 3/3. Failures are named: no call, unknown tool, malformed arguments, spurious call. |

Reasoning models (Qwen 3.x, gpt-oss) think before they answer. The agent test gives
them room (1,024 tokens). An empty answer in your own tests usually means the token
budget ran out while the model was still thinking.

## History and the Results page

With a saved machine (step 1), every finished test session is kept automatically. It is
named after what was served, e.g. `bonsai-27b · 128k · FA · batch 512`, and you can
rename it. Step 5 lists this machine and model's history. Tick two rows and **Compare**
shows each metric side by side with a winner. Differences under 5% are treated as
run-to-run noise: two runs of one configuration measured 75.1 and 75.2 tok/s.

**Local models → Results** is the quick overview, with no case to run:

- **Speed tests:** every saved test ranked by seconds per agent step, with read and
  write tok/s, agent cases passed, served context and verdicts. Speed is a property of
  the machine, so compare rows of the same machine. The page filters by machine.
- **Case runs with local models:** lab runs whose executor is a local model, one row
  per model, harness, case and **arm**. Arms are never pooled: a bare and a
  full-harness arm are two conditions. The row shows runs, runs that passed, the share
  of graded tests passed and the median duration. Local case runs record no token
  speed, so a model's tok/s comes from its speed tests.

## Is it worth tuning further?

Writing tokens reads every active weight once per token, so a GPU's **memory
bandwidth** sets a ceiling: `tok/s ≈ bandwidth ÷ model size`. For a MoE model, use the
active share of its size. Add your GPU's bandwidth in step 1 (the presets fill it in),
and the speed test reports how close you are:

| Share of the ceiling | Reading |
|---|---|
| 60 % or more | **Near the ceiling.** Published llama.cpp results reach 64–81 % on consumer NVIDIA GPUs, so settings have little left to give. A smaller quantization or a faster GPU is what moves it. |
| 35–60 % | **Some headroom.** Check that every layer is on the GPU, flash attention is on, and nothing else uses the GPU. |
| under 35 % | **Far below.** Something is likely off: layers on the CPU, the model spilling into shared memory, or another model on the GPU. |
| 1–2 bit quants | **Compute-bound.** Unpacking a Q1/Q2 model costs compute, so the bandwidth ceiling overstates what is reachable. Compare against a 4-bit quantization instead. |

*Local models → Results* also shows the published llama-bench reference (Llama 2 7B
Q4_0) for common GPUs and Apple chips, with its sources. It compares GPUs, not models.

**Prompt reading is measured with prompts no cache can reuse.** llama.cpp reuses a
cached prompt prefix: a repeated 17k-token prompt "read" at 113,000 tok/s, against
~1,180 for a fresh one. Every test prompt therefore starts with a unique tag.

## Then measure it on real work

A profile that passes all four tests is ready for a case. *Use in New evaluation*
opens the wizard with that model selected. A local run of a real case takes 20–45
minutes, so start with one case and a few repetitions, and read
[Small & self-hosted models](guide-small-models.md) before spending a night on it.
