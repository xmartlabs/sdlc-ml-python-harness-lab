import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import type {
  EnvSettingItem,
  EnvSettings,
  GatewayModelRecord,
  GatewayTestResult,
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

export function Settings({ theme, onThemeChange }: SettingsProps) {
  const [env, setEnv] = useState<EnvSettings | null>(null);
  const [facts, setFacts] = useState<RuntimeFacts | null>(null);
  const [models, setModels] = useState<GatewayModelRecord[]>([]);
  const [routable, setRoutable] = useState<Record<string, boolean>>({});
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
        for (const model of catalog.models) {
          flags[model.id.replace(/^gateway\//, "")] = model.routable !== false;
        }
        setRoutable(flags);
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadEnv();
    loadModels();
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
          <h2>Model catalog</h2>
          <button disabled={busy} onClick={handleRefreshModels} type="button">
            Refresh from gateway
          </button>
        </div>
        <p className="muted">
          Enabled models are routed to opencode at run time through a config overlay, so a
          model you enable here works immediately and <code>opencode.json</code> stays
          untouched.
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
            {models.map((model) => (
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
                </span>
              </div>
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
          </div>
        )}
      </section>
    </section>
  );
}
