---
name: create_mle_agent_package
description: Use when designing a reusable pip-installable MLE agent package with governed scaffolding, runtime adapters, and validation plans.
---

# Skill: create_mle_agent_package

## Purpose

Generate a reusable, pip-installable MLE agent package aligned with this repository governance, suitable for FastAPI, serverless runtimes, or chatbot adapters.

When `prediction_type` is `llm` or `hybrid`, default to a provider-agnostic LangChain-style architecture where model selection is driven by a model catalog and a `model_core` selector instead of hard-coding OpenAI, Google, Ollama, or other vendors inside chains, workflows, or business logic.

This skill produces a deterministic specification and file plan only. It must not generate full business implementation when the request scope is only package scaffolding/specification.

## Governance References (Mandatory)

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`
- `.github/automation.md`

## Required Input

- `agent_name` (string, snake_case package name).
- `prediction_type` (enum: `classification` | `regression` | `llm` | `hybrid`).
- `runtime_target` (enum: `fastapi` | `lambda` | `cloud_function` | `hybrid`).
- `include_chat_adapter` (boolean).
- `include_data_endpoint` (boolean).
- `include_healthcheck` (boolean).
- `enabled_model_providers` (list of enum: `openai` | `google` | `ollama` | `lmstudio` | `bedrock`).
- `default_model_core` (string, optional; canonical provider-agnostic model selector such as `qwen3:8b` or `gemini-2.5-flash`).
- `include_embeddings_factory` (boolean, default `true` when `prediction_type` is `llm` or `hybrid`).
- `include_chain_factory` (boolean, default `true` when `prediction_type` is `llm` or `hybrid`).
- `include_workflow_scaffold` (boolean, default `true` when `prediction_type` is `llm` or `hybrid`).

## Output Format

- `package_name`: resolved from `agent_name`.
- `runtime_profile`: resolved from `runtime_target`.
- `artifact_tree`: deterministic ordered list of paths to create.
- `constraints_report`: pass/fail checklist for architecture and standards.
- `test_plan`: minimal deterministic unit test list.
- `runbook`: install/run/test commands.
- `provider_strategy`: catalog-driven summary of enabled providers, default `model_core`, and fallback behavior.
- `usage_examples`: minimal deterministic examples for LLM creation, chain creation, and workflow creation when `prediction_type` is `llm` or `hybrid`.

### Required Artifact Tree

Always include (ordered):

- `src/{agent_name}/__init__.py`
- `src/{agent_name}/agent.py`
- `src/{agent_name}/config.py`
- `src/{agent_name}/observability.py`
- `src/{agent_name}/graph/__init__.py`
- `src/{agent_name}/graph/state.py`
- `src/{agent_name}/graph/nodes.py`
- `src/{agent_name}/graph/builder.py`
- `src/{agent_name}/model/__init__.py`
- `src/{agent_name}/model/catalog.py`
- `src/{agent_name}/model/llm.py`
- `src/{agent_name}/model/readiness.py`
- `src/{agent_name}/prompts/__init__.py`
- `src/{agent_name}/prompts/defaults.py`
- `src/{agent_name}/prompts/loader.py`
- `src/{agent_name}/tools/__init__.py`
- `tests/test_{agent_name}_config.py`
- `tests/test_{agent_name}_graph.py`
- `tests/test_{agent_name}_api.py` (only when `runtime_target` includes `fastapi`)
- `docs/{agent_name}-implementation.md`

Conditional artifacts:

- Add adapter module(s) under `src/{agent_name}/adapters/` only when requested by runtime flags.
- Add chat adapter module only when `include_chat_adapter=true`.
- Add data endpoint schema/router only when `include_data_endpoint=true` and runtime includes `fastapi`.
- Add healthcheck endpoint only when `include_healthcheck=true` and runtime includes `fastapi`.
- Add `src/{agent_name}/model/embeddings.py` when `include_embeddings_factory=true`.
- Add `src/{agent_name}/chains/__init__.py` and `src/{agent_name}/chains/factory.py` when `include_chain_factory=true`.
- Add `src/{agent_name}/workflows/__init__.py` and `src/{agent_name}/workflows/builder.py` when `include_workflow_scaffold=true`.
- Add `tests/test_{agent_name}_catalog.py` and `tests/test_{agent_name}_llm_factory.py` when `prediction_type` is `llm` or `hybrid`.
- Add `tests/test_{agent_name}_chains.py` when `include_chain_factory=true`.
- Add `tests/test_{agent_name}_workflows.py` when `include_workflow_scaffold=true`.

## Provider-Agnostic LLM Pattern (Mandatory For `llm` And `hybrid`)

When the package includes LLM behavior, the generated specification must require this structure:

1. `config.py`
   - Pydantic settings with normalized provider validation.
   - Canonical `model_core`-style selector support.
   - Resolved properties such as `active_model_provider`, `active_model_name`, and embedding equivalents.
2. `model/catalog.py`
   - Declarative `ModelSpec`-style registry for chat and embedding models.
   - Resolver functions that map aliases or provider-native IDs into stable model specs.
   - Support for both paid APIs and local inference targets such as Ollama.
3. `model/readiness.py`
   - Readiness helpers such as `config_from_model_core()` and `check_provider_ready()`.
   - Readiness checks must verify environment prerequisites without coupling the rest of the package to a single vendor.
4. `model/llm.py`
   - `build_llm(config)` provider factory with lazy imports.
   - Provider-specific SDK imports must be isolated inside the factory.
5. `chains/factory.py` when enabled
   - `build_chain(config, ...)` or equivalent provider-agnostic chain builder.
   - Chains depend on `build_llm()` and prompts, never on vendor SDKs directly.
6. `workflows/builder.py` when enabled
   - `build_workflow(config, ...)` or equivalent orchestration builder.
   - Workflow assembly must be switchable across enabled `model_core` targets.

The design goal is to let a caller enable a model through a single selector and reuse the same chain or workflow shape regardless of whether the runtime uses OpenAI, Google, Bedrock, Ollama, or LM Studio-compatible endpoints.

## Required Usage Example Pattern

When `prediction_type` is `llm` or `hybrid`, the output must include a minimal example similar to the following in `docs/{agent_name}-implementation.md`:

```python
MODEL_CORE = "qwen3:8b"
# MODEL_CORE = "claude-3-5-sonnet"
# MODEL_CORE = "gpt-4o-mini"
# MODEL_CORE = "gemini-2.5-flash"

minimal_config = config_from_model_core(MODEL_CORE, temperature=0.0)
ready, reason = check_provider_ready(minimal_config)

print(f"model_core={MODEL_CORE}")
print(f"provider={minimal_config.active_model_provider}")
print(f"chat_model={minimal_config.active_model_name}")
print(f"ready={ready} -> {reason}")

if ready:
    minimal_llm = build_llm(minimal_config)
    print(
        f"LLM created: {type(minimal_llm).__name__} "
        f"(model_core={MODEL_CORE}, provider={minimal_config.active_model_provider})"
    )

    minimal_chain = build_chain(minimal_config)
    test_resp = minimal_chain.invoke("Respond with exactly: OK")
    test_text = getattr(test_resp, "content", str(test_resp))
    print(f"Sanity check: {test_text[:80].strip()!r}")
else:
    print("Sanity check skipped until that provider is available in this environment.")
```

If workflow scaffolding is enabled, include a second short example showing `build_workflow(minimal_config)` or the equivalent orchestration entrypoint.

## Architectural Constraints (Hard Rules)

1. Core (`graph`, `model`, `prompts`, `tools`, `agent`) must not depend on FastAPI or external transport SDKs.
2. Adapters may depend only on service/application entry points, never on infrastructure internals.
3. No business logic inside adapters (only translation, validation, orchestration wiring).
4. All imports must be absolute.
5. All public interfaces must have type hints.
6. If FastAPI is included, request/response schemas must use Pydantic.
7. Respect layer direction from `.github/architecture.md`:
   - domain/application independent from infrastructure.
   - infrastructure depends on contracts, not the opposite.
8. Provider-specific SDK imports must live only in provider factory modules such as `model/llm.py` and `model/embeddings.py`.
9. Chains and workflows must depend on provider-agnostic builders and config contracts, never directly on `langchain_openai`, `langchain_google_genai`, `langchain_aws`, `langchain_ollama`, or equivalent SDK packages.
10. Model aliasing and provider resolution must be centralized in `model/catalog.py`; do not spread provider or model-ID conditionals across nodes, prompts, chains, or workflows.

## Quality Guardrails

- Generate minimal unit tests for config loading, graph construction, and selected adapter wiring.
- Include English docstrings in all public modules and functions.
- Include structured logging hooks in service layer (`observability.py`, orchestration boundaries).
- Include one minimal usage snippet in docs showing package import and invocation path.
- Keep output deterministic: same input must yield same artifact tree and checklist.
- For `llm` or `hybrid` packages, generate minimal tests for model catalog resolution, `config_from_model_core()`, `check_provider_ready()`, and `build_llm()`.
- For chain-enabled packages, include at least one deterministic chain-construction test that swaps `model_core` without changing chain assembly logic.
- For workflow-enabled packages, include at least one deterministic workflow-construction test that proves the orchestration builder is provider-agnostic.
- Prefer `make install` or `uv`-based setup instructions over raw `pip` commands in the runbook.

## Execution Steps (Runbook)

1. Install locally:
   - `make install`
   - `uv sync` (only when a lower-level environment step is explicitly required)
2. If FastAPI runtime is included, run locally:
   - `uvicorn api:app --reload`
3. Run tests with project workflow:
   - `make test`

## Determinism and Reproducibility Rules

- Sort generated artifact paths lexicographically within each section.
- Keep stable naming conventions from `agent_name` without random suffixes.
- Do not infer optional components unless explicitly enabled by input flags.
- Emit a fixed-order compliance checklist (architecture, boundaries, standards, automation).
- For `llm` or `hybrid` packages, keep the provider strategy deterministic by listing enabled providers and stable catalog aliases in a fixed order.

## Failure Conditions (Must Fail)

Return `FAIL` and stop generation when any of the following is detected:

- Core module imports FastAPI, cloud runtime SDK, or adapter modules.
- Adapter includes business rules instead of transport translation/wiring.
- Relative imports are present.
- Missing type hints in public interfaces.
- FastAPI runtime selected but schemas are not Pydantic-based.
- Requested artifact tree violates repository boundaries (`src/`, `tests/`, `docs/`).
- Required governance checks are skipped.
- LLM-enabled scaffolding does not include a catalog-driven provider abstraction.
- Chain or workflow layers are hard-wired to a single provider or concrete provider SDK.
- No readiness helper is specified for distinguishing missing credentials or unavailable local inference endpoints from valid configurations.
- No deterministic `model_core` example is provided in docs for LLM-enabled packages.

## Compliance Checklist Template

- `architecture_governance`: PASS/FAIL
- `standards_governance`: PASS/FAIL
- `domain_boundaries_governance`: PASS/FAIL
- `automation_governance`: PASS/FAIL
- `determinism_reproducibility`: PASS/FAIL

## Execution Rules

1. Apply all governance files before proposing artifacts.
2. Generate only scoped outputs required by input flags.
3. Prefer minimal structure that satisfies architecture and testability.
4. Keep all code artifacts and technical docs in English.
5. Require a PASS checklist before declaring completion.
