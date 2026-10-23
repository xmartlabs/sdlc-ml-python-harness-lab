import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useNativeModelIndex } from "../harnesses";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import type { ModelOption } from "../types";

type ModelSource = "gateway" | "direct";

type ModelPickerProps = {
  label: string;
  value: string;
  onChange: (modelId: string) => void;
  /** The whole mixed catalog; the picker does the filtering. */
  options: ModelOption[];
  /** Keep an explicit empty choice at the top of the list. */
  allowNone?: boolean;
  /**
   * What the empty choice means here. The planner's empty is "no planner at
   * all"; a host-CLI harness's empty is "whatever the CLI picks by itself",
   * which is a running model and not the absence of one.
   */
  noneLabel?: string;
  /**
   * Host-CLI harnesses (claude, codex) address their own models directly. When
   * this is set the tiers are meaningless, so the picker collapses to one flat
   * select over exactly these ids.
   */
  nativeOptions?: ModelOption[];
  /** Why a direct provider has nothing in it, keyed by provider id. */
  directErrors?: Record<string, string>;
  onReload?: () => void;
  reloading?: boolean;
  /** Caller-supplied helper text, rendered under the control. */
  children?: ReactNode;
};

const MESSAGES = defineMessages({
  en: {
    sourceLabels: { gateway: "AI Gateway", direct: "Direct (local)" } as Record<
      ModelSource,
      string
    >,
    none: "None",
    notServed: (model: string) =>
      `The gateway has no card for ${model}: runs would fail on their first request. Pick a model it serves, or add it to the gateway.`,
    reloading: "Reloading…",
    reload: "⟳ Reload",
    notLoaded: "Not loaded — a JIT auto-load would use the model's default window.",
    loadedWith: (ctx: string, max: string) => `Loaded with ${ctx}${max}.`,
    tokens: (count: string) => `${count} tokens`,
    unreportedWindow: "an unreported window",
    ofMax: (count: string) => ` of ${count} max`,
    notRoutable: "Not routable — enable it on the Settings page before running it.",
    backendUnavailable: (reason: string) => `Backend unavailable — ${reason}.`,
    unreachableReasonFallback: "the machine behind this card is not serving it",
    warningNotRoutable: " — not routable",
    warningBackendUnavailable: " — backend unavailable",
    noModelsFromSource: "No models available from this source.",
    hostUrlHelp: (currentPick: string) => (
      <>
        Set the host URL under Settings → Local runtimes (it must end in{" "}
        <code>/v1</code>), then press Reload. Your current pick — {currentPick} — is
        unchanged.
      </>
    )
  },
  es: {
    sourceLabels: { gateway: "AI Gateway", direct: "Directo (local)" } as Record<
      ModelSource,
      string
    >,
    none: "Ninguno",
    notServed: (model: string) =>
      `El gateway no tiene card para ${model}: las corridas fallarían en el primer pedido. Elegí un modelo que sirva, o agregalo al gateway.`,
    reloading: "Recargando…",
    reload: "⟳ Recargar",
    notLoaded: "No cargado — una autocarga JIT usaría la ventana por defecto del modelo.",
    loadedWith: (ctx: string, max: string) => `Cargado con ${ctx}${max}.`,
    tokens: (count: string) => `${count} tokens`,
    unreportedWindow: "una ventana no informada",
    ofMax: (count: string) => ` de ${count} máx.`,
    notRoutable: "No enrutable — habilitalo en la página de Configuración antes de usarlo.",
    backendUnavailable: (reason: string) => `Backend no disponible — ${reason}.`,
    unreachableReasonFallback: "la máquina detrás de esta ficha no lo está sirviendo",
    warningNotRoutable: " — no enrutable",
    warningBackendUnavailable: " — backend no disponible",
    noModelsFromSource: "No hay modelos disponibles desde esta fuente.",
    hostUrlHelp: (currentPick: string) => (
      <>
        Configurá la URL del host en Configuración → Runtimes locales (debe terminar en{" "}
        <code>/v1</code>), y después presioná Recargar. Tu elección actual — {currentPick}{" "}
        — no cambió.
      </>
    )
  }
});

// Display names for providers named only in an error, where no option exists to
// carry a `provider_label`.
const PROVIDER_NAMES: Record<string, string> = {
  lmstudio: "LM Studio",
  ollama: "Ollama"
};

// Vendor order for the provider tier. Local backends lead because they are what
// this lab runs on; "other" trails because it is the bucket for ids no rule
// matched. Providers with no models are omitted, so this is an order, not a menu.
const PROVIDER_ORDER = [
  "lmstudio",
  "ollama",
  "openai",
  "anthropic",
  "google",
  "nvidia",
  "other"
];

// Client-side mirror of the server's PROVIDER_RULES, used only for options that
// arrive without the fields — a preset id, or the whole catalog when the API is
// offline. Server values always win; this exists so an unclassified option still
// lands somewhere reachable instead of vanishing under a filter.
const PROVIDER_FALLBACK_RULES: [string[], string, string][] = [
  [["lmstudio-"], "lmstudio", "LM Studio"],
  [["ollama-"], "ollama", "Ollama"],
  [["gpt-", "codex-"], "openai", "OpenAI"],
  [["claude-", "bedrock-claude-"], "anthropic", "Claude"],
  [["gemini-"], "google", "Gemini"],
  [["nim-"], "nvidia", "NVIDIA"]
];

type Tiers = { source: ModelSource; provider: string; providerLabel: string };

function classify(option: ModelOption): Tiers {
  if (option.source && option.provider) {
    return {
      source: option.source,
      provider: option.provider,
      providerLabel: option.provider_label ?? option.provider
    };
  }
  if (option.id.startsWith("lmstudio/")) {
    return { source: "direct", provider: "lmstudio", providerLabel: "LM Studio" };
  }
  const bare = option.id.replace(/^gateway\//, "");
  for (const [prefixes, provider, providerLabel] of PROVIDER_FALLBACK_RULES) {
    if (prefixes.some((prefix) => bare.startsWith(prefix))) {
      return { source: "gateway", provider, providerLabel };
    }
  }
  return { source: "gateway", provider: "other", providerLabel: "Other" };
}

/** What the selected model means for a run, in one line. */
function metadataFor(option: ModelOption | undefined): string | null {
  const m = pickMessages(MESSAGES);
  if (!option) {
    return null;
  }
  if (option.source === "direct") {
    if (!option.loaded) {
      return m.notLoaded;
    }
    const ctx = option.loaded_context
      ? m.tokens(option.loaded_context.toLocaleString())
      : m.unreportedWindow;
    const max = option.max_context ? m.ofMax(option.max_context.toLocaleString()) : "";
    return m.loadedWith(ctx, max);
  }
  if (option.routable === false) {
    return m.notRoutable;
  }
  if (option.reachable === false) {
    return m.backendUnavailable(option.unreachable_reason ?? m.unreachableReasonFallback);
  }
  return null;
}

/**
 * The warning a model carries in the collapsed `<select>`, where only the
 * option text is visible.
 *
 * Two different failures, kept apart on purpose: an id this harness cannot
 * address is fixed on the Settings page, while a card whose serving machine is
 * off is fixed by starting that machine. Collapsing both into one word would
 * send the operator to the wrong place.
 */
function warningFor(option: ModelOption): string {
  const m = pickMessages(MESSAGES);
  if (option.routable === false) {
    return m.warningNotRoutable;
  }
  if (option.reachable === false) {
    return m.warningBackendUnavailable;
  }
  return "";
}

/**
 * Three-tier model chooser: source → provider → model.
 *
 * The flat menu it replaces listed a heading "LM Studio (local)" that held
 * *gateway aliases* while direct models sat under a second, similarly-named
 * heading, and every hosted vendor shared one "Cloud" bucket. Two different
 * questions were collapsed into one list: how the model is reached, and who
 * makes it. Splitting them into tiers means a wrong pick is a visible wrong
 * pick, not a name that reads right and routes somewhere else.
 */
export function ModelPicker({
  label,
  value,
  onChange,
  options,
  allowNone = false,
  noneLabel,
  nativeOptions,
  directErrors,
  onReload,
  reloading = false,
  children
}: ModelPickerProps) {
  const m = useMessages(MESSAGES);
  const nativeIndex = useNativeModelIndex();
  const [source, setSource] = useState<ModelSource>("gateway");
  const [provider, setProvider] = useState("lmstudio");

  const classified = useMemo(
    () => options.map((option) => ({ option, tiers: classify(option) })),
    [options]
  );

  // Adopt the tiers of whatever is selected. This is what keeps the control in
  // step with changes made elsewhere — the preset button, the gateway default
  // arriving from the API, a harness switch. It never calls onChange: the
  // selection drives the tiers here, and only the tier buttons below drive the
  // selection, so the two can never chase each other.
  useEffect(() => {
    const match = classified.find((entry) => entry.option.id === value);
    if (match) {
      setSource(match.tiers.source);
      setProvider(match.tiers.provider);
    }
  }, [value, classified]);

  const bySource = useMemo(
    () => ({
      gateway: classified.filter((entry) => entry.tiers.source === "gateway"),
      direct: classified.filter((entry) => entry.tiers.source === "direct")
    }),
    [classified]
  );

  // Providers present in the active source, in the fixed order, with counts.
  const providers = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const entry of bySource[source]) {
      const current = counts.get(entry.tiers.provider);
      counts.set(entry.tiers.provider, {
        label: entry.tiers.providerLabel,
        count: (current?.count ?? 0) + 1
      });
    }
    return PROVIDER_ORDER.filter((id) => counts.has(id)).map((id) => ({
      id,
      ...counts.get(id)!
    }));
  }, [bySource, source]);

  const activeProvider = providers.some((p) => p.id === provider)
    ? provider
    : (providers[0]?.id ?? provider);

  const visible = useMemo(() => {
    const filtered = bySource[source]
      .filter((entry) => entry.tiers.provider === activeProvider)
      .map((entry) => entry.option);
    // The selected id always renders, even when it belongs to another tier or
    // the catalog could not be loaded — a select that shows a blank selection
    // hides which model a run would actually use.
    if (value && !filtered.some((option) => option.id === value)) {
      const known = options.find((option) => option.id === value);
      return [...filtered, known ?? { id: value, label: value }];
    }
    return filtered;
  }, [bySource, source, activeProvider, value, options]);

  const selected = options.find((option) => option.id === value);
  const metadata = metadataFor(selected);
  const sourceIsEmpty = bySource[source].length === 0;
  // Why each direct host has nothing, in the fixed provider order. Shown in
  // place of the chips: a source that offers nothing has to say why, or the
  // only thing the user learns is that the button does not work.
  const errorLines = Object.entries(directErrors ?? {})
    .sort(
      (a, b) => PROVIDER_ORDER.indexOf(a[0]) - PROVIDER_ORDER.indexOf(b[0])
    )
    .map(([id, message]) => `${PROVIDER_NAMES[id] ?? id}: ${message}`);

  /** Move to a tier and take the first model in it, unless "None" is set. */
  const jumpTo = (nextSource: ModelSource, nextProvider?: string) => {
    setSource(nextSource);
    const pool = bySource[nextSource];
    const target =
      nextProvider ??
      PROVIDER_ORDER.find((id) => pool.some((e) => e.tiers.provider === id)) ??
      activeProvider;
    setProvider(target);
    if (allowNone && value === "") {
      return;
    }
    const first = pool.find((entry) => entry.tiers.provider === target);
    if (first && first.option.id !== value) {
      onChange(first.option.id);
    }
  };

  if (nativeOptions) {
    const notServed = nativeIndex[value]?.on_gateway === false;
    return (
      <label className="model-picker">
        <span>{label}</span>
        <select onChange={(event) => onChange(event.target.value)} value={value}>
          {allowNone ? <option value="">{noneLabel ?? m.none}</option> : null}
          {nativeOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {nativeIndex[option.id]?.label ?? option.label}
            </option>
          ))}
        </select>
        {notServed ? (
          <small className="callout callout--danger" role="alert">
            {m.notServed(value)}
          </small>
        ) : null}
        {children}
      </label>
    );
  }

  return (
    <div className="model-picker">
      <div className="model-picker-head">
        <span>{label}</span>
        {onReload ? (
          <button
            className="ghost"
            disabled={reloading}
            onClick={onReload}
            type="button"
          >
            {reloading ? m.reloading : m.reload}
          </button>
        ) : null}
      </div>

      {/* Never disabled: a source with nothing in it has to be openable, or
          the reason it is empty — the thing the user actually needs — has
          nowhere to appear. */}
      <div className="segmented" role="group">
        {(["gateway", "direct"] as ModelSource[]).map((id) => (
          <button
            aria-pressed={source === id}
            className={source === id ? "active" : ""}
            key={id}
            onClick={() => jumpTo(id)}
            type="button"
          >
            {m.sourceLabels[id]}
          </button>
        ))}
      </div>

      {sourceIsEmpty ? (
        <div className="picker-empty">
          {errorLines.length > 0 ? (
            errorLines.map((line) => (
              <small className="muted" key={line}>
                ⚠ {line}
              </small>
            ))
          ) : (
            <small className="muted">{m.noModelsFromSource}</small>
          )}
          <small className="muted">
            {m.hostUrlHelp(selected?.label ?? value ?? noneLabel ?? m.none)}
          </small>
        </div>
      ) : (
        <>
          <div className="chip-row" role="group">
            {providers.map((entry) => (
              <button
                aria-pressed={entry.id === activeProvider}
                className={`chip${entry.id === activeProvider ? " active" : ""}`}
                key={entry.id}
                onClick={() => jumpTo(source, entry.id)}
                type="button"
              >
                {entry.label} <span className="chip-count">{entry.count}</span>
              </button>
            ))}
          </div>

          <select onChange={(event) => onChange(event.target.value)} value={value}>
            {allowNone ? <option value="">{noneLabel ?? m.none}</option> : null}
            {visible.map((option) => (
              <option key={option.id} value={option.id}>
                {`${option.label}${warningFor(option)}`}
              </option>
            ))}
          </select>

          {metadata ? <small className="muted">{metadata}</small> : null}
        </>
      )}
      {children}
    </div>
  );
}
