import { Fragment, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { EXAMPLE_LMSTUDIO_URL, EXAMPLE_OLLAMA_URL } from "../constants";
import { ErrorBanner } from "../components/ErrorBanner";
import type {
  ContextProbeResult,
  EnvSettingItem,
  EnvSettings,
  GatewayModelRecord,
  GatewayTestResult,
  LMStudioCatalog,
  OllamaCatalog,
  RuntimeFacts
} from "../types";
import type { ThemeMode } from "../theme";

type SettingsProps = {
  theme: ThemeMode;
  onThemeChange: (theme: ThemeMode) => void;
};

// Order and titles for the groups returned by GET /api/settings/env.
const GROUPS: { id: string; title: string; hint: string }[] = [
  {
    id: "models",
    title: "Model defaults",
    hint: "Used when a run does not name a model"
  },
  {
    id: "runtimes",
    title: "Local runtimes",
    hint: "Self-hosted backends behind the gateway"
  },
  {
    id: "credentials",
    title: "Harness credentials",
    hint: "Native CLI lanes (claude, codex)"
  },
  { id: "tracing", title: "Tracing", hint: "Langfuse" }
];

function fieldValue(item: EnvSettingItem, draft: Record<string, string>): string {
  if (item.key in draft) {
    return draft[item.key];
  }
  // Secrets are only ever returned masked, so the input starts empty: an
  // untouched field is never sent, which is what keeps the stored value.
  return item.is_secret ? "" : item.value;
}

function EnvField({
  item,
  draft,
  onChange
}: {
  item: EnvSettingItem;
  draft: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  const placeholder = item.is_secret
    ? item.is_set
      ? `${item.value} — leave blank to keep`
      : "not set"
    : "not set";
  return (
    <label>
      <span>{item.label}</span>
      <input
        onChange={(event) => onChange(item.key, event.target.value)}
        placeholder={placeholder}
        type={item.is_secret ? "password" : "text"}
        value={fieldValue(item, draft)}
      />
      <small className="muted">
        <code>{item.key}</code>
        {item.help_text ? ` — ${item.help_text}` : ""}
      </small>
    </label>
  );
}

/** Verdict of the last context measurement for one model, or nothing yet. */
function ContextBadge({ result }: { result: ContextProbeResult | undefined }) {
  if (!result) {
    return null;
  }
  if (result.status === "insufficient") {
    return (
      <span className="badge error" title={result.detail}>
        context {result.context_tokens ?? "?"} &lt; {result.required_tokens}
      </span>
    );
  }
  if (result.status === "ok") {
    return (
      <span className="badge ok" title={result.detail}>
        context ≥ {result.required_tokens}
      </span>
    );
  }
  return (
    <span className="badge warn" title={result.detail}>
      context unknown
    </span>
  );
}

export function Settings({ theme, onThemeChange }: SettingsProps) {
  const [env, setEnv] = useState<EnvSettings | null>(null);
  const [facts, setFacts] = useState<RuntimeFacts | null>(null);
  const [models, setModels] = useState<GatewayModelRecord[]>([]);
  const [routable, setRoutable] = useState<Record<string, boolean>>({});
  // model_id -> canonical id it was merged into, for ids the gateway router
  // resolves to a backend model another id already covers.
  const [mergedInto, setMergedInto] = useState<Record<string, string>>({});
  const [contextChecks, setContextChecks] = useState<Record<string, ContextProbeResult>>(
    {}
  );
  const [checking, setChecking] = useState<string | null>(null);
  // Models read straight from the LM Studio host, with no gateway in between.
  const [lmstudio, setLmstudio] = useState<LMStudioCatalog | null>(null);
  const [lmstudioBusy, setLmstudioBusy] = useState(false);
  const [ollama, setOllama] = useState<OllamaCatalog | null>(null);
  const [ollamaBusy, setOllamaBusy] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<GatewayTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadEnv = () => {
    api
      .readEnvSettings()
      .then((data) => {
        setEnv(data);
        setDraft({});
      })
      .catch((err: Error) => setError(err.message));
  };

  const loadModels = () => {
    api.listModelCatalog().then(setModels).catch(() => {});
    api
      .listModels()
      .then((catalog) => {
        const flags: Record<string, boolean> = {};
        const merged: Record<string, string> = {};
        for (const model of catalog.models) {
          const bare = model.id.replace(/^gateway\//, "");
          flags[bare] = model.routable !== false;
          for (const alias of model.aliases ?? []) {
            merged[alias.replace(/^gateway\//, "")] = bare;
          }
        }
        setRoutable(flags);
        setMergedInto(merged);
      })
      .catch(() => {});
  };

  /**
   * Ask the LM Studio host itself which models it serves.
   *
   * `refresh` bypasses the short server-side cache — the point of this panel is
   * that after loading or unloading a model in LM Studio the list here matches
   * reality on the next click, without a gateway or a catalog refresh.
   */
  const loadLmstudio = (refresh = false) => {
    setLmstudioBusy(true);
    api
      .listLmstudioModels(refresh)
      .then(setLmstudio)
      .catch((err: Error) =>
        setLmstudio({ base_url: "", reachable: false, error: err.message, models: [] })
      )
      .finally(() => setLmstudioBusy(false));
  };

  /** The same, for the second direct host. */
  const loadOllama = (refresh = false) => {
    setOllamaBusy(true);
    api
      .listOllamaModels(refresh)
      .then(setOllama)
      .catch((err: Error) =>
        setOllama({ base_url: "", reachable: false, error: err.message, models: [] })
      )
      .finally(() => setOllamaBusy(false));
  };

  useEffect(() => {
    loadEnv();
    loadModels();
    loadLmstudio();
    loadOllama();
    api.readRuntimeFacts().then(setFacts).catch(() => {});
  }, []);

  const byGroup = useMemo(() => {
    const grouped: Record<string, EnvSettingItem[]> = {};
    for (const item of env?.items ?? []) {
      (grouped[item.group] ??= []).push(item);
    }
    return grouped;
  }, [env]);

  const handleChange = (key: string, value: string) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setNotice(null);
  };

  const handleSave = () => {
    if (Object.keys(draft).length === 0) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    api
      .writeEnvSettings(draft)
      .then((result) => {
        setEnv({
          items: result.items,
          warnings: result.warnings,
          env_path: result.env_path
        });
        setDraft({});
        setNotice(
          result.written.length === 0
            ? "Nothing changed."
            : `Saved ${result.written.join(", ")} to ${result.env_path}. ` +
              "The next request picks the values up — no restart needed." +
              (result.backup ? ` Previous file backed up to ${result.backup}.` : "")
        );
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleTest = () => {
    setBusy(true);
    setError(null);
    setTestResult(null);
    const baseItem = env?.items.find((item) => item.key === "GATEWAY_BASE_URL");
    api
      .testGateway(draft.GATEWAY_BASE_URL ?? baseItem?.value ?? "", draft.GATEWAY_TOKEN ?? "")
      .then(setTestResult)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const handleRefreshModels = () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    api
      .refreshModelCatalog()
      .then((result) => {
        loadModels();
        const added = result.added.length ? `added ${result.added.join(", ")}` : "no new models";
        const stale = result.stale.length ? `; no longer served: ${result.stale.join(", ")}` : "";
        setNotice(`Gateway serves ${result.total} models — ${added}${stale}.`);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const patchModel = (
    modelId: string,
    changes: { label?: string; enabled?: boolean; is_default?: boolean }
  ) => {
    setBusy(true);
    setError(null);
    api
      .updateGatewayModel(modelId, changes)
      .then(() => loadModels())
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  /**
   * Measure the context window the backend is *actually* serving for a model.
   *
   * LM Studio loads a model on demand with its default window, which can be far
   * smaller than the one configured by hand — and the harness system prompt
   * alone is ~13.7k tokens, so an 8k auto-load kills every step of a run
   * instantly. The only honest answer comes from sending a real oversized
   * prompt, so this takes as long as the model needs to load and reply.
   */
  const handleCheckContext = (modelId: string) => {
    setChecking(modelId);
    setError(null);
    setNotice(null);
    api
      .checkModelContext(modelId)
      .then((result) => {
        setContextChecks((current) => ({ ...current, [modelId]: result }));
        // An insufficient window is a hard failure the user must act on, so it
        // gets the error banner; anything else is informational.
        if (result.status === "insufficient") {
          setError(`${modelId}: ${result.detail}`);
        } else {
          setNotice(`${modelId}: ${result.detail}`);
        }
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setChecking(null));
  };

  const commitLabel = (model: GatewayModelRecord) => {
    const next = labels[model.model_id];
    if (next === undefined || next === model.label) {
      return;
    }
    patchModel(model.model_id, { label: next });
  };

  const dirty = Object.keys(draft).length > 0;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Gateway, models and runtime</h1>
          <p className="lede">
            Configure the LLM gateway, adopt models it serves, and edit the connection
            settings in <code>.env</code> — no CLI, no commit.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {notice ? <p className="muted">{notice}</p> : null}
      {(env?.warnings ?? []).map((warning) => (
        <p className="muted" key={warning}>
          ⚠ {warning}
        </p>
      ))}

      <section className="panel">
        <div className="panel-head">
          <h2>Gateway connection</h2>
          <span className="muted">{env?.env_path ?? ".env"}</span>
        </div>
        <div className="form-grid">
          {(byGroup.gateway ?? []).map((item) => (
            <EnvField draft={draft} item={item} key={item.key} onChange={handleChange} />
          ))}
        </div>
        <div className="panel-actions">
          <button disabled={busy} onClick={handleTest} type="button">
            Test connection
          </button>
        </div>
        {testResult ? (
          <p className="muted">
            {testResult.ok
              ? `Reachable — ${testResult.model_count} models served (${(
                  testResult.sample ?? []
                ).join(", ")}…).`
              : `Not reachable — ${testResult.detail}`}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>LM Studio (direct, no gateway)</h2>
          <button disabled={lmstudioBusy} onClick={() => loadLmstudio(true)} type="button">
            {lmstudioBusy ? "Reading…" : "Refresh from LM Studio"}
          </button>
        </div>
        <p className="muted">
          Read straight from <code>LMSTUDIO_BASE_URL</code>, so these are the models the
          server is really serving under the names it really registers — no gateway alias
          can rename or reroute them. Loaded models appear in the run picker under “LM
          Studio (direct, no gateway)”, and their context window is the one reported here,
          not a guess. {lmstudio?.base_url ? <code>{lmstudio.base_url}</code> : null}
        </p>
        {lmstudio === null ? (
          <p className="muted">Loading…</p>
        ) : lmstudio.reachable === false ? (
          <EmptyState
            description={
              (lmstudio.error ?? "LM Studio did not answer.") +
              " Set LMSTUDIO_BASE_URL under “Local runtimes” to the server address " +
              `including /v1 (for example ${EXAMPLE_LMSTUDIO_URL}) and make sure ` +
              "LM Studio is serving on the network, not only on localhost."
            }
            title="LM Studio is not reachable."
          />
        ) : lmstudio.models.length === 0 ? (
          <EmptyState
            description="The server answered but serves no models. Load one in LM Studio, then press “Refresh from LM Studio”."
            title="No models served."
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>Model</span>
              <span>Loaded context</span>
              <span>State</span>
              <span>Details</span>
            </div>
            {lmstudio.models.map((model) => (
              <div className="table-row" key={model.id}>
                <span className="harness-name">
                  <strong>{model.bare_id}</strong>
                  <small>{model.id}</small>
                </span>
                <span>
                  {model.loaded_context
                    ? `${model.loaded_context.toLocaleString()} tokens`
                    : "—"}
                  {model.max_context ? (
                    <small className="muted"> of {model.max_context.toLocaleString()} max</small>
                  ) : null}
                </span>
                <span className="model-flags">
                  <span className={model.loaded ? "badge ok" : "badge warn"}>
                    {model.loaded ? "loaded" : model.state}
                  </span>
                  {model.loaded && (model.loaded_context ?? 0) < 32768 ? (
                    <span
                      className="badge error"
                      title="The harness system prompt alone is ~13.7k tokens; runs need at least 32k."
                    >
                      window too small
                    </span>
                  ) : null}
                </span>
                <span className="muted">
                  {[model.type, model.arch, model.quantization].filter(Boolean).join(" · ") ||
                    "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Ollama (direct, no gateway)</h2>
          <button disabled={ollamaBusy} onClick={() => loadOllama(true)} type="button">
            {ollamaBusy ? "Reading…" : "Refresh from Ollama"}
          </button>
        </div>
        <p className="muted">
          Read straight from <code>OLLAMA_BASE_URL</code> — the second direct host. A
          <em> running</em> model reports the context window the server is serving;
          anything else gets the host's <code>OLLAMA_CONTEXT_LENGTH</code>, which
          defaults to 4096 and cannot be read remotely. That default is below the
          harness system prompt (~13.7k tokens), so raise it on the server before
          running anything here. {ollama?.base_url ? <code>{ollama.base_url}</code> : null}
        </p>
        {ollama === null ? (
          <p className="muted">Loading…</p>
        ) : ollama.reachable === false ? (
          <EmptyState
            description={
              (ollama.error ?? "Ollama did not answer.") +
              " Set OLLAMA_BASE_URL under “Local runtimes” to the server address " +
              `including /v1 (for example ${EXAMPLE_OLLAMA_URL}). If Ollama ` +
              "runs on another machine, it must listen on the network — set " +
              "OLLAMA_HOST=0.0.0.0 there."
            }
            title="Ollama is not reachable."
          />
        ) : ollama.models.length === 0 ? (
          <EmptyState
            description="The server answered but has no models. Pull one with `ollama pull`, then press “Refresh from Ollama”."
            title="No models installed."
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>Model</span>
              <span>Served context</span>
              <span>State</span>
              <span>Details</span>
            </div>
            {ollama.models.map((model) => (
              <div className="table-row" key={model.id}>
                <span className="harness-name">
                  <strong>{model.bare_id}</strong>
                  <small>{model.id}</small>
                </span>
                <span>
                  {model.context_length
                    ? `${model.context_length.toLocaleString()} tokens`
                    : "—"}
                </span>
                <span className="model-flags">
                  <span className={model.running ? "badge ok" : "badge"}>
                    {model.running ? "running" : "not loaded"}
                  </span>
                  {model.running &&
                  model.context_length !== null &&
                  model.context_length < 32768 ? (
                    <span
                      className="badge error"
                      title="The harness system prompt alone is ~13.7k tokens; runs need at least 32k. Raise OLLAMA_CONTEXT_LENGTH on the server."
                    >
                      window too small
                    </span>
                  ) : null}
                </span>
                <span className="muted">
                  {[model.parameter_size, model.family, model.quantization]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Model catalog</h2>
          <button disabled={busy} onClick={handleRefreshModels} type="button">
            Refresh from gateway
          </button>
        </div>
        <p className="muted">
          Enabled models are routed to opencode at run time through a config overlay, so a
          model you enable here works immediately and <code>opencode.json</code> stays
          untouched. Ids the gateway resolves to the same backend model are merged, so the
          run picker lists each model once. Use “Check context” to measure the window a
          backend is really serving before spending a run on it.
        </p>
        {models.length === 0 ? (
          <EmptyState
            description="Press “Refresh from gateway” to adopt whatever LiteLLM serves right now."
            title="No models adopted yet."
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>Model</span>
              <span>Display label</span>
              <span>State</span>
              <span>Actions</span>
            </div>
            {models.map((model, index) => (
              <Fragment key={model.model_id}>
                {model.group && model.group !== models[index - 1]?.group ? (
                  <div className="table-row table-section" key={`section-${model.group}`}>
                    <span className="muted">
                      <strong>{model.group}</strong>
                    </span>
                  </div>
                ) : null}
                <div className="table-row" key={model.model_id}>
                  <span className="harness-name">
                    <strong>{model.model_id}</strong>
                    <small>gateway/{model.model_id}</small>
                  </span>
                  <span>
                    <input
                      disabled={busy}
                      onBlur={() => commitLabel(model)}
                      onChange={(event) =>
                        setLabels((current) => ({
                          ...current,
                          [model.model_id]: event.target.value
                        }))
                      }
                      value={labels[model.model_id] ?? model.label}
                    />
                  </span>
                  <span className="model-flags">
                    <span className={model.enabled ? "badge ok" : "badge"}>
                      {model.enabled ? "enabled" : "disabled"}
                    </span>
                    {model.live ? null : <span className="badge warn">not served</span>}
                    {routable[model.model_id] === false ? (
                      <span className="badge error">not routable</span>
                    ) : null}
                    {model.is_default ? <span className="badge neutral">default</span> : null}
                    {mergedInto[model.model_id] ? (
                      <span className="badge neutral">
                        merged into {mergedInto[model.model_id]}
                      </span>
                    ) : null}
                    <ContextBadge result={contextChecks[model.model_id]} />
                  </span>
                  <span className="row-actions">
                    <button
                      disabled={busy}
                      onClick={() => patchModel(model.model_id, { enabled: !model.enabled })}
                      type="button"
                    >
                      {model.enabled ? "Disable" : "Enable"}
                    </button>
                    {model.is_default ? null : (
                      <button
                        disabled={busy || !model.enabled}
                        onClick={() => patchModel(model.model_id, { is_default: true })}
                        type="button"
                      >
                        Make default
                      </button>
                    )}
                    <button
                      disabled={checking !== null}
                      onClick={() => handleCheckContext(model.model_id)}
                      type="button"
                    >
                      {checking === model.model_id ? "Checking…" : "Check context"}
                    </button>
                  </span>
                </div>
              </Fragment>
            ))}
          </div>
        )}
      </section>

      {GROUPS.map((group) =>
        (byGroup[group.id] ?? []).length === 0 ? null : (
          <section className="panel" key={group.id}>
            <div className="panel-head">
              <h2>{group.title}</h2>
              <span className="muted">{group.hint}</span>
            </div>
            <div className="form-grid">
              {byGroup[group.id].map((item) => (
                <EnvField draft={draft} item={item} key={item.key} onChange={handleChange} />
              ))}
            </div>
          </section>
        )
      )}

      <section className="panel">
        <div className="panel-actions">
          <button className="primary" disabled={busy || !dirty} onClick={handleSave} type="button">
            {busy ? "Saving..." : dirty ? "Save changes" : "No changes"}
          </button>
          <button disabled={busy || !dirty} onClick={() => setDraft({})} type="button">
            Discard
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Appearance</h2>
          <span className="muted">Persisted locally</span>
        </div>
        <div className="settings-list">
          <div className="theme-toggle-row">
            <span>Theme</span>
            <div className="theme-toggle" role="group">
              <button
                aria-pressed={theme === "light"}
                className={theme === "light" ? "active" : ""}
                onClick={() => onThemeChange("light")}
                type="button"
              >
                Light
              </button>
              <button
                aria-pressed={theme === "dark"}
                className={theme === "dark" ? "active" : ""}
                onClick={() => onThemeChange("dark")}
                type="button"
              >
                Dark
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Runtime facts</h2>
          <span className="muted">Read-only — a change needs a restart</span>
        </div>
        {facts === null ? (
          <p className="muted">Loading…</p>
        ) : (
          <div className="settings-list">
            <div>
              <span>Repository</span>
              <strong>{facts.repo_root}</strong>
            </div>
            <div>
              <span>Database</span>
              <strong>{facts.database_url}</strong>
            </div>
            <div>
              <span>Execution backend</span>
              <strong>{facts.execution_backend}</strong>
            </div>
            <div>
              <span>Runner image</span>
              <strong>{facts.runner_image}</strong>
            </div>
            <div>
              <span>Sandbox image</span>
              <strong>{facts.sandbox_image}</strong>
            </div>
            <div>
              <span>Redis</span>
              <strong>{facts.redis_url}</strong>
            </div>
            <div>
              <span>Run timeout</span>
              <strong>{facts.run_timeout_seconds}s</strong>
            </div>
            <div>
              <span>Stall timeout</span>
              <strong>
                {facts.run_stall_seconds
                  ? `${facts.run_stall_seconds}s without output`
                  : "disabled"}
              </strong>
            </div>
            <div>
              <span>Model wait</span>
              <strong>
                {facts.run_model_wait_seconds
                  ? `${facts.run_model_wait_seconds}s awaiting a response`
                  : "disabled"}
              </strong>
            </div>
            <div>
              <span>Endpoint errors</span>
              <strong>
                {facts.run_max_stream_errors
                  ? `${facts.run_max_stream_errors} in a row`
                  : "disabled"}
              </strong>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
