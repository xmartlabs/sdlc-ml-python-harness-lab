import { Fragment, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { EXAMPLE_LMSTUDIO_URL, EXAMPLE_OLLAMA_URL } from "../constants";
import { ErrorBanner } from "../components/ErrorBanner";
import { LanguageToggle } from "../components/LanguageToggle";
import { defineMessages, useMessages } from "../i18n";
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

const MESSAGES = defineMessages({
  en: {
    groups: {
      models: {
        title: "Model defaults",
        hint: "Run models (OPENCODE_*) and the lab's own audit / judge / summary models"
      },
      runtimes: {
        title: "Local runtimes",
        hint: "Direct LM Studio / Ollama hosts the picker reads without a gateway"
      },
      credentials: {
        title: "Harness credentials",
        hint: "Claude and codex harness lanes. A Claude credential is also the fallback LLM transport for audits and judges when no gateway is set."
      },
      tracing: { title: "Tracing", hint: "Langfuse" }
    },
    notSet: "not set",
    leaveBlankToKeep: (value: string) => `${value} — leave blank to keep`,
    gatewayConnection: "Gateway connection",
    testConnection: "Test connection",
    testResultOk: (count: number | undefined, sample: string) =>
      `Reachable — ${count} models served (${sample}…).`,
    testResultFail: (detail: string | undefined) => `Not reachable — ${detail}`,
    lmstudioTitle: "LM Studio (direct, no gateway)",
    lmstudioReading: "Reading…",
    lmstudioRefresh: "Refresh from LM Studio",
    lmstudioIntro: (badge: ReactNode) => (
      <>
        Read straight from <code>LMSTUDIO_BASE_URL</code>, so these are the models the
        server is really serving under the names it really registers — no gateway alias
        can rename or reroute them. Loaded models appear in the run picker under “LM
        Studio (direct, no gateway)”, and their context window is the one reported here,
        not a guess. {badge}
      </>
    ),
    loading: "Loading…",
    lmstudioUnreachableTitle: "LM Studio is not reachable.",
    lmstudioUnreachableDescription: (error: string) =>
      `${error} Set LMSTUDIO_BASE_URL under “Local runtimes” to the server address ` +
      `including /v1 (for example ${EXAMPLE_LMSTUDIO_URL}) and make sure ` +
      "LM Studio is serving on the network, not only on localhost.",
    lmstudioDefaultError: "LM Studio did not answer.",
    lmstudioNoModelsTitle: "No models served.",
    lmstudioNoModelsDescription:
      "The server answered but serves no models. Load one in LM Studio, then press “Refresh from LM Studio”.",
    tableModel: "Model",
    tableLoadedContext: "Loaded context",
    tableState: "State",
    tableDetails: "Details",
    tokens: (count: number) => `${count.toLocaleString()} tokens`,
    ofMax: (count: number) => `of ${count.toLocaleString()} max`,
    loaded: "loaded",
    windowTooSmall: "window too small",
    windowTooSmallHint:
      "The harness system prompt alone is ~13.7k tokens; runs need at least 32k.",
    ollamaTitle: "Ollama (direct, no gateway)",
    ollamaRefresh: "Refresh from Ollama",
    ollamaIntro: (badge: ReactNode) => (
      <>
        Read straight from <code>OLLAMA_BASE_URL</code> — the second direct host. A
        <em> running</em> model reports the context window the server is serving;
        anything else gets the host&apos;s <code>OLLAMA_CONTEXT_LENGTH</code>, which
        defaults to 4096 and cannot be read remotely. That default is below the
        harness system prompt (~13.7k tokens), so raise it on the server before
        running anything here. {badge}
      </>
    ),
    ollamaUnreachableTitle: "Ollama is not reachable.",
    ollamaUnreachableDescription: (error: string) =>
      `${error} Set OLLAMA_BASE_URL under “Local runtimes” to the server address ` +
      `including /v1 (for example ${EXAMPLE_OLLAMA_URL}). If Ollama ` +
      "runs on another machine, it must listen on the network — set " +
      "OLLAMA_HOST=0.0.0.0 there.",
    ollamaDefaultError: "Ollama did not answer.",
    ollamaNoModelsTitle: "No models installed.",
    ollamaNoModelsDescription:
      "The server answered but has no models. Pull one with `ollama pull`, then press “Refresh from Ollama”.",
    tableServedContext: "Served context",
    running: "running",
    notLoaded: "not loaded",
    windowTooSmallOllamaHint:
      "The harness system prompt alone is ~13.7k tokens; runs need at least 32k. Raise OLLAMA_CONTEXT_LENGTH on the server.",
    catalogTitle: "Model catalog",
    catalogRefresh: "Refresh from gateway",
    catalogIntro:
      "Enabled models are routed to opencode at run time through a config overlay, so a model you enable here works immediately and ",
    catalogIntroTail:
      " stays untouched. Ids the gateway resolves to the same backend model are merged, so the run picker lists each model once. Use “Check context” to measure the window a backend is really serving before spending a run on it.",
    catalogEmptyTitle: "No models adopted yet.",
    catalogEmptyDescription: "Press “Refresh from gateway” to adopt whatever LiteLLM serves right now.",
    tableLabel: "Display label",
    tableActions: "Actions",
    enabled: "enabled",
    disabled: "disabled",
    notServed: "not served",
    notRoutable: "not routable",
    backendUnavailable: "backend unavailable",
    default: "default",
    mergedInto: (target: string) => `merged into ${target}`,
    disable: "Disable",
    enable: "Enable",
    makeDefault: "Make default",
    checking: "Checking…",
    checkContext: "Check context",
    saving: "Saving...",
    saveChanges: "Save changes",
    noChanges: "No changes",
    discard: "Discard",
    appearance: "Appearance",
    persistedLocally: "Persisted locally",
    theme: "Theme",
    light: "Light",
    dark: "Dark",
    language: "Language",
    runtimeFacts: "Runtime facts",
    readOnlyRestart: "Read-only — a change needs a restart",
    repository: "Repository",
    database: "Database",
    executionBackend: "Execution backend",
    runnerImage: "Runner image",
    sandboxImage: "Sandbox image",
    redis: "Redis",
    runTimeout: "Run timeout",
    stallTimeout: "Stall timeout",
    withoutOutput: (seconds: number) => `${seconds}s without output`,
    modelWait: "Model wait",
    awaitingResponse: (seconds: number) => `${seconds}s awaiting a response`,
    endpointErrors: "Endpoint errors",
    inARow: (count: number) => `${count} in a row`,
    disabledFlag: "disabled",
    eyebrow: "Settings",
    heading: "Transport, models and runtime",
    lede: (code: ReactNode) => (
      <>
        Set up the AI gateway or a Claude credential, adopt the models it serves,
        and edit the connection settings in {code} — no shell, no restart.
      </>
    ),
    nothingChanged: "Nothing changed.",
    saved: (written: string, path: string) => `Saved ${written} to ${path}. `,
    noRestartNeeded: "The next request picks the values up — no restart needed.",
    previousBackedUp: (backup: string) => ` Previous file backed up to ${backup}.`,
    gatewayServes: (total: number) => `Gateway serves ${total} models — `,
    addedModels: (list: string) => `added ${list}`,
    noNewModels: "no new models",
    noLongerServed: (list: string) => `; no longer served: ${list}`
  },
  es: {
    groups: {
      models: {
        title: "Modelos por defecto",
        hint: "Modelos de corrida (OPENCODE_*) y los propios modelos de auditoría / juez / resumen del lab"
      },
      runtimes: {
        title: "Runtimes locales",
        hint: "Hosts directos de LM Studio / Ollama que el selector lee sin pasar por un gateway"
      },
      credentials: {
        title: "Credenciales del arnés",
        hint: "Carriles de arnés de Claude y codex. Una credencial de Claude también es el transporte LLM de respaldo para auditorías y jueces cuando no hay gateway configurado."
      },
      tracing: { title: "Trazas", hint: "Langfuse" }
    },
    notSet: "sin definir",
    leaveBlankToKeep: (value: string) => `${value} — dejá en blanco para mantenerlo`,
    gatewayConnection: "Conexión al gateway",
    testConnection: "Probar conexión",
    testResultOk: (count: number | undefined, sample: string) =>
      `Accesible — ${count} modelos servidos (${sample}…).`,
    testResultFail: (detail: string | undefined) => `No accesible — ${detail}`,
    lmstudioTitle: "LM Studio (directo, sin gateway)",
    lmstudioReading: "Leyendo…",
    lmstudioRefresh: "Actualizar desde LM Studio",
    lmstudioIntro: (badge: ReactNode) => (
      <>
        Lee directo de <code>LMSTUDIO_BASE_URL</code>, así que estos son los modelos
        que el servidor realmente sirve bajo los nombres que realmente registra — ningún
        alias de gateway puede renombrarlos ni redirigirlos. Los modelos cargados
        aparecen en el selector de corridas bajo “LM Studio (directo, sin gateway)”, y
        su ventana de contexto es la reportada acá, no una estimación. {badge}
      </>
    ),
    loading: "Cargando…",
    lmstudioUnreachableTitle: "LM Studio no es accesible.",
    lmstudioUnreachableDescription: (error: string) =>
      `${error} Poné LMSTUDIO_BASE_URL en “Runtimes locales” con la dirección del ` +
      `servidor incluyendo /v1 (por ejemplo ${EXAMPLE_LMSTUDIO_URL}) y asegurate de ` +
      "que LM Studio esté sirviendo en la red, no solo en localhost.",
    lmstudioDefaultError: "LM Studio no respondió.",
    lmstudioNoModelsTitle: "No hay modelos servidos.",
    lmstudioNoModelsDescription:
      "El servidor respondió pero no sirve ningún modelo. Cargá uno en LM Studio y presioná “Actualizar desde LM Studio”.",
    tableModel: "Modelo",
    tableLoadedContext: "Contexto cargado",
    tableState: "Estado",
    tableDetails: "Detalles",
    tokens: (count: number) => `${count.toLocaleString()} tokens`,
    ofMax: (count: number) => `de ${count.toLocaleString()} máx`,
    loaded: "cargado",
    windowTooSmall: "ventana muy chica",
    windowTooSmallHint:
      "El system prompt del arnés solo ya son ~13.7k tokens; las corridas necesitan al menos 32k.",
    ollamaTitle: "Ollama (directo, sin gateway)",
    ollamaRefresh: "Actualizar desde Ollama",
    ollamaIntro: (badge: ReactNode) => (
      <>
        Lee directo de <code>OLLAMA_BASE_URL</code> — el segundo host directo. Un
        modelo <em>en ejecución</em> reporta la ventana de contexto que el servidor
        está sirviendo; cualquier otro usa el <code>OLLAMA_CONTEXT_LENGTH</code> del
        host, que por defecto es 4096 y no se puede leer en forma remota. Ese valor
        por defecto está por debajo del system prompt del arnés (~13.7k tokens), así
        que subilo en el servidor antes de correr algo acá. {badge}
      </>
    ),
    ollamaUnreachableTitle: "Ollama no es accesible.",
    ollamaUnreachableDescription: (error: string) =>
      `${error} Poné OLLAMA_BASE_URL en “Runtimes locales” con la dirección del ` +
      `servidor incluyendo /v1 (por ejemplo ${EXAMPLE_OLLAMA_URL}). Si Ollama ` +
      "corre en otra máquina, tiene que escuchar en la red — configurá " +
      "OLLAMA_HOST=0.0.0.0 ahí.",
    ollamaDefaultError: "Ollama no respondió.",
    ollamaNoModelsTitle: "No hay modelos instalados.",
    ollamaNoModelsDescription:
      "El servidor respondió pero no tiene modelos. Bajá uno con `ollama pull` y presioná “Actualizar desde Ollama”.",
    tableServedContext: "Contexto servido",
    running: "en ejecución",
    notLoaded: "no cargado",
    windowTooSmallOllamaHint:
      "El system prompt del arnés solo ya son ~13.7k tokens; las corridas necesitan al menos 32k. Subí OLLAMA_CONTEXT_LENGTH en el servidor.",
    catalogTitle: "Catálogo de modelos",
    catalogRefresh: "Actualizar desde el gateway",
    catalogIntro:
      "Los modelos habilitados se enrutan a opencode en tiempo de corrida mediante un overlay de configuración, así que un modelo que habilités acá funciona de inmediato y ",
    catalogIntroTail:
      " queda intacto. Los ids que el gateway resuelve al mismo modelo de backend se fusionan, así el selector de corridas lista cada modelo una sola vez. Usá “Chequear contexto” para medir la ventana que un backend realmente sirve antes de gastar una corrida en él.",
    catalogEmptyTitle: "Todavía no se adoptó ningún modelo.",
    catalogEmptyDescription: "Presioná “Actualizar desde el gateway” para adoptar lo que sea que LiteLLM esté sirviendo ahora.",
    tableLabel: "Etiqueta visible",
    tableActions: "Acciones",
    enabled: "habilitado",
    disabled: "deshabilitado",
    notServed: "no servido",
    notRoutable: "no enrutable",
    backendUnavailable: "backend no disponible",
    default: "por defecto",
    mergedInto: (target: string) => `fusionado en ${target}`,
    disable: "Deshabilitar",
    enable: "Habilitar",
    makeDefault: "Hacer por defecto",
    checking: "Chequeando…",
    checkContext: "Chequear contexto",
    saving: "Guardando...",
    saveChanges: "Guardar cambios",
    noChanges: "Sin cambios",
    discard: "Descartar",
    appearance: "Apariencia",
    persistedLocally: "Se guarda localmente",
    theme: "Tema",
    light: "Claro",
    dark: "Oscuro",
    language: "Idioma",
    runtimeFacts: "Datos del runtime",
    readOnlyRestart: "Solo lectura — un cambio necesita reiniciar",
    repository: "Repositorio",
    database: "Base de datos",
    executionBackend: "Backend de ejecución",
    runnerImage: "Imagen del runner",
    sandboxImage: "Imagen del sandbox",
    redis: "Redis",
    runTimeout: "Timeout de corrida",
    stallTimeout: "Timeout de estancamiento",
    withoutOutput: (seconds: number) => `${seconds}s sin salida`,
    modelWait: "Espera del modelo",
    awaitingResponse: (seconds: number) => `${seconds}s esperando una respuesta`,
    endpointErrors: "Errores de endpoint",
    inARow: (count: number) => `${count} seguidos`,
    disabledFlag: "deshabilitado",
    eyebrow: "Configuración",
    heading: "Transporte, modelos y runtime",
    lede: (code: ReactNode) => (
      <>
        Configurá el gateway de IA o una credencial de Claude, adoptá los modelos que
        sirve, y editá la configuración de conexión en {code} — sin shell, sin
        reiniciar.
      </>
    ),
    nothingChanged: "No cambió nada.",
    saved: (written: string, path: string) => `Se guardó ${written} en ${path}. `,
    noRestartNeeded: "El próximo pedido ya toma los valores — no hace falta reiniciar.",
    previousBackedUp: (backup: string) => ` Se respaldó el archivo anterior en ${backup}.`,
    gatewayServes: (total: number) => `El gateway sirve ${total} modelos — `,
    addedModels: (list: string) => `agregados ${list}`,
    noNewModels: "sin modelos nuevos",
    noLongerServed: (list: string) => `; ya no servidos: ${list}`
  }
});

type GroupKey = keyof (typeof MESSAGES)["en"]["groups"];

// Order of the groups returned by GET /api/settings/env.
const GROUP_ORDER: GroupKey[] = ["models", "runtimes", "credentials", "tracing"];

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
  const m = useMessages(MESSAGES);
  const placeholder = item.is_secret
    ? item.is_set
      ? m.leaveBlankToKeep(item.value)
      : m.notSet
    : m.notSet;
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

const CONTEXT_BADGE_MESSAGES = defineMessages({
  en: {
    tooSmall: (tokens: string, required: number) => `context ${tokens} < ${required}`,
    tight: (tokens: number | null) => `context ${tokens} — tight for agents`,
    sufficient: (required: number) => `context ≥ ${required}`,
    unknown: "context unknown"
  },
  es: {
    tooSmall: (tokens: string, required: number) => `contexto ${tokens} < ${required}`,
    tight: (tokens: number | null) => `contexto ${tokens} — justa para agentes`,
    sufficient: (required: number) => `contexto ≥ ${required}`,
    unknown: "contexto desconocido"
  }
});

/** Verdict of the last context measurement for one model, or nothing yet. */
function ContextBadge({ result }: { result: ContextProbeResult | undefined }) {
  const m = useMessages(CONTEXT_BADGE_MESSAGES);
  if (!result) {
    return null;
  }
  if (result.status === "insufficient") {
    return (
      <span className="badge error" title={result.detail}>
        {m.tooSmall(String(result.context_tokens ?? "?"), result.required_tokens)}
      </span>
    );
  }
  if (result.status === "ok") {
    return result.advisory ? (
      <span className="badge warn" title={result.advisory}>
        {m.tight(result.context_tokens)}
      </span>
    ) : (
      <span className="badge ok" title={result.detail}>
        {m.sufficient(result.required_tokens)}
      </span>
    );
  }
  return (
    <span className="badge warn" title={result.detail}>
      {m.unknown}
    </span>
  );
}

export function Settings({ theme, onThemeChange }: SettingsProps) {
  const m = useMessages(MESSAGES);
  const [env, setEnv] = useState<EnvSettings | null>(null);
  const [facts, setFacts] = useState<RuntimeFacts | null>(null);
  const [models, setModels] = useState<GatewayModelRecord[]>([]);
  const [routable, setRoutable] = useState<Record<string, boolean>>({});
  // Why a card's serving machine cannot answer, keyed by bare model id. Empty
  // for every card the lab cannot decide on, so absence means "no verdict",
  // not "healthy".
  const [unreachable, setUnreachable] = useState<Record<string, string>>({});
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
        const down: Record<string, string> = {};
        for (const model of catalog.models) {
          const bare = model.id.replace(/^gateway\//, "");
          flags[bare] = model.routable !== false;
          if (model.reachable === false) {
            down[bare] = model.unreachable_reason ?? "backend unavailable";
          }
          for (const alias of model.aliases ?? []) {
            merged[alias.replace(/^gateway\//, "")] = bare;
          }
        }
        setRoutable(flags);
        setMergedInto(merged);
        setUnreachable(down);
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
            ? m.nothingChanged
            : m.saved(result.written.join(", "), result.env_path) +
              m.noRestartNeeded +
              (result.backup ? m.previousBackedUp(result.backup) : "")
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
        const added = result.added.length ? m.addedModels(result.added.join(", ")) : m.noNewModels;
        const stale = result.stale.length ? m.noLongerServed(result.stale.join(", ")) : "";
        setNotice(`${m.gatewayServes(result.total)}${added}${stale}.`);
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
          setNotice(`${modelId}: ${result.advisory || result.detail}`);
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
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.heading}</h1>
          <p className="lede">{m.lede(<code>.env</code>)}</p>
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
          <h2>{m.gatewayConnection}</h2>
          <span className="muted">{env?.env_path ?? ".env"}</span>
        </div>
        <div className="form-grid">
          {(byGroup.gateway ?? []).map((item) => (
            <EnvField draft={draft} item={item} key={item.key} onChange={handleChange} />
          ))}
        </div>
        <div className="panel-actions">
          <button disabled={busy} onClick={handleTest} type="button">
            {m.testConnection}
          </button>
        </div>
        {testResult ? (
          <p className="muted">
            {testResult.ok
              ? m.testResultOk(testResult.model_count, (testResult.sample ?? []).join(", "))
              : m.testResultFail(testResult.detail)}
          </p>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.lmstudioTitle}</h2>
          <button disabled={lmstudioBusy} onClick={() => loadLmstudio(true)} type="button">
            {lmstudioBusy ? m.lmstudioReading : m.lmstudioRefresh}
          </button>
        </div>
        <p className="muted">
          {m.lmstudioIntro(
            lmstudio?.base_url ? <code>{lmstudio.base_url}</code> : ""
          )}
        </p>
        {lmstudio === null ? (
          <p className="muted">{m.loading}</p>
        ) : lmstudio.reachable === false ? (
          <EmptyState
            description={m.lmstudioUnreachableDescription(
              lmstudio.error ?? m.lmstudioDefaultError
            )}
            title={m.lmstudioUnreachableTitle}
          />
        ) : lmstudio.models.length === 0 ? (
          <EmptyState
            description={m.lmstudioNoModelsDescription}
            title={m.lmstudioNoModelsTitle}
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>{m.tableModel}</span>
              <span>{m.tableLoadedContext}</span>
              <span>{m.tableState}</span>
              <span>{m.tableDetails}</span>
            </div>
            {lmstudio.models.map((model) => (
              <div className="table-row" key={model.id}>
                <span className="harness-name">
                  <strong>{model.bare_id}</strong>
                  <small>{model.id}</small>
                </span>
                <span>
                  {model.loaded_context ? m.tokens(model.loaded_context) : "—"}
                  {model.max_context ? (
                    <small className="muted"> {m.ofMax(model.max_context)}</small>
                  ) : null}
                </span>
                <span className="model-flags">
                  <span className={model.loaded ? "badge ok" : "badge warn"}>
                    {model.loaded ? m.loaded : model.state}
                  </span>
                  {model.loaded && (model.loaded_context ?? 0) < 32768 ? (
                    <span className="badge error" title={m.windowTooSmallHint}>
                      {m.windowTooSmall}
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
          <h2>{m.ollamaTitle}</h2>
          <button disabled={ollamaBusy} onClick={() => loadOllama(true)} type="button">
            {ollamaBusy ? m.lmstudioReading : m.ollamaRefresh}
          </button>
        </div>
        <p className="muted">
          {m.ollamaIntro(ollama?.base_url ? <code>{ollama.base_url}</code> : "")}
        </p>
        {ollama === null ? (
          <p className="muted">{m.loading}</p>
        ) : ollama.reachable === false ? (
          <EmptyState
            description={m.ollamaUnreachableDescription(ollama.error ?? m.ollamaDefaultError)}
            title={m.ollamaUnreachableTitle}
          />
        ) : ollama.models.length === 0 ? (
          <EmptyState
            description={m.ollamaNoModelsDescription}
            title={m.ollamaNoModelsTitle}
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>{m.tableModel}</span>
              <span>{m.tableServedContext}</span>
              <span>{m.tableState}</span>
              <span>{m.tableDetails}</span>
            </div>
            {ollama.models.map((model) => (
              <div className="table-row" key={model.id}>
                <span className="harness-name">
                  <strong>{model.bare_id}</strong>
                  <small>{model.id}</small>
                </span>
                <span>
                  {model.context_length ? m.tokens(model.context_length) : "—"}
                </span>
                <span className="model-flags">
                  <span className={model.running ? "badge ok" : "badge"}>
                    {model.running ? m.running : m.notLoaded}
                  </span>
                  {model.running &&
                  model.context_length !== null &&
                  model.context_length < 32768 ? (
                    <span className="badge error" title={m.windowTooSmallOllamaHint}>
                      {m.windowTooSmall}
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
          <h2>{m.catalogTitle}</h2>
          <button disabled={busy} onClick={handleRefreshModels} type="button">
            {m.catalogRefresh}
          </button>
        </div>
        <p className="muted">
          {m.catalogIntro}
          <code>opencode.json</code>
          {m.catalogIntroTail}
        </p>
        {models.length === 0 ? (
          <EmptyState
            description={m.catalogEmptyDescription}
            title={m.catalogEmptyTitle}
          />
        ) : (
          <div className="table table--models">
            <div className="table-row table-head">
              <span>{m.tableModel}</span>
              <span>{m.tableLabel}</span>
              <span>{m.tableState}</span>
              <span>{m.tableActions}</span>
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
                      {model.enabled ? m.enabled : m.disabled}
                    </span>
                    {model.live ? null : <span className="badge warn">{m.notServed}</span>}
                    {routable[model.model_id] === false ? (
                      <span className="badge error">{m.notRoutable}</span>
                    ) : null}
                    {unreachable[model.model_id] ? (
                      <span className="badge error" title={unreachable[model.model_id]}>
                        {m.backendUnavailable}
                      </span>
                    ) : null}
                    {model.is_default ? <span className="badge neutral">{m.default}</span> : null}
                    {mergedInto[model.model_id] ? (
                      <span className="badge neutral">
                        {m.mergedInto(mergedInto[model.model_id])}
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
                      {model.enabled ? m.disable : m.enable}
                    </button>
                    {model.is_default ? null : (
                      <button
                        disabled={busy || !model.enabled}
                        onClick={() => patchModel(model.model_id, { is_default: true })}
                        type="button"
                      >
                        {m.makeDefault}
                      </button>
                    )}
                    <button
                      disabled={checking !== null}
                      onClick={() => handleCheckContext(model.model_id)}
                      type="button"
                    >
                      {checking === model.model_id ? m.checking : m.checkContext}
                    </button>
                  </span>
                </div>
              </Fragment>
            ))}
          </div>
        )}
      </section>

      {GROUP_ORDER.map((groupId) =>
        (byGroup[groupId] ?? []).length === 0 ? null : (
          <section className="panel" key={groupId}>
            <div className="panel-head">
              <h2>{m.groups[groupId].title}</h2>
              <span className="muted">{m.groups[groupId].hint}</span>
            </div>
            <div className="form-grid">
              {byGroup[groupId].map((item) => (
                <EnvField draft={draft} item={item} key={item.key} onChange={handleChange} />
              ))}
            </div>
          </section>
        )
      )}

      <section className="panel">
        <div className="panel-actions">
          <button className="primary" disabled={busy || !dirty} onClick={handleSave} type="button">
            {busy ? m.saving : dirty ? m.saveChanges : m.noChanges}
          </button>
          <button disabled={busy || !dirty} onClick={() => setDraft({})} type="button">
            {m.discard}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.appearance}</h2>
          <span className="muted">{m.persistedLocally}</span>
        </div>
        <div className="settings-list">
          <div className="theme-toggle-row">
            <span>{m.theme}</span>
            <div className="theme-toggle" role="group">
              <button
                aria-pressed={theme === "light"}
                className={theme === "light" ? "active" : ""}
                onClick={() => onThemeChange("light")}
                type="button"
              >
                {m.light}
              </button>
              <button
                aria-pressed={theme === "dark"}
                className={theme === "dark" ? "active" : ""}
                onClick={() => onThemeChange("dark")}
                type="button"
              >
                {m.dark}
              </button>
            </div>
          </div>
          <div className="theme-toggle-row">
            <span>{m.language}</span>
            <LanguageToggle />
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.runtimeFacts}</h2>
          <span className="muted">{m.readOnlyRestart}</span>
        </div>
        {facts === null ? (
          <p className="muted">{m.loading}</p>
        ) : (
          <div className="settings-list">
            <div>
              <span>{m.repository}</span>
              <strong>{facts.repo_root}</strong>
            </div>
            <div>
              <span>{m.database}</span>
              <strong>{facts.database_url}</strong>
            </div>
            <div>
              <span>{m.executionBackend}</span>
              <strong>{facts.execution_backend}</strong>
            </div>
            <div>
              <span>{m.runnerImage}</span>
              <strong>{facts.runner_image}</strong>
            </div>
            <div>
              <span>{m.sandboxImage}</span>
              <strong>{facts.sandbox_image}</strong>
            </div>
            <div>
              <span>{m.redis}</span>
              <strong>{facts.redis_url}</strong>
            </div>
            <div>
              <span>{m.runTimeout}</span>
              <strong>{facts.run_timeout_seconds}s</strong>
            </div>
            <div>
              <span>{m.stallTimeout}</span>
              <strong>
                {facts.run_stall_seconds
                  ? m.withoutOutput(facts.run_stall_seconds)
                  : m.disabledFlag}
              </strong>
            </div>
            <div>
              <span>{m.modelWait}</span>
              <strong>
                {facts.run_model_wait_seconds
                  ? m.awaitingResponse(facts.run_model_wait_seconds)
                  : m.disabledFlag}
              </strong>
            </div>
            <div>
              <span>{m.endpointErrors}</span>
              <strong>
                {facts.run_max_stream_errors
                  ? m.inARow(facts.run_max_stream_errors)
                  : m.disabledFlag}
              </strong>
            </div>
          </div>
        )}
      </section>
    </section>
  );
}
