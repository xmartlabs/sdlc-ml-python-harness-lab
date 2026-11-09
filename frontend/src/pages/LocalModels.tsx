import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import type { HostGuardAction } from "../components/HostGuardModal";
import { HostGuardModal } from "../components/HostGuardModal";
import { InfoTooltip } from "../components/InfoTooltip";
import { MemoryBar } from "../components/MemoryBar";
import { ServedConfigDiff } from "../components/ServedConfigDiff";
import { Spinner } from "../components/Spinner";
import { api, ApiError } from "../api";
import { formatRunDate } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type {
  LocalComparison,
  LocalGuardReport,
  LocalHeadroom,
  LocalHistoryRow,
  LocalLoadConfig,
  LocalLoadResult,
  LocalMachine,
  LocalMemoryEstimate,
  LocalModelInfo,
  LocalNotLoadedDetail,
  LocalReference,
  LocalServedConfig,
  LocalServerKind,
  LocalServerTestResult,
  LocalTestName,
  LocalTestResult,
  LocalTestSession,
  LocalTuningProfile,
  LocalUnloadForeignInstancesDetail,
  MachineSpec,
  RefMachine
} from "../types";

const STEPS = [
  { key: "machine" },
  { key: "server" },
  { key: "config" },
  { key: "test" },
  { key: "result" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

const GPU_OFFLOAD_OPTIONS = [1, 0.75, 0.5, 0.25];

/** Placeholder sent with `evaluate_only: true` — the backend ignores it and
 * reads the server's own served config instead. */
const EMPTY_SERVED_CONFIG: LocalServedConfig = {
  context_length: null,
  gpu_offload: null,
  flash_attention: null,
  offload_kv_cache_to_gpu: null,
  eval_batch_size: null,
  num_experts: null,
  parallel: null,
  vram_bytes: null,
  instance_id: null
};

const MESSAGES = defineMessages({
  en: {
    header: {
      title: "Tune your local model",
      lede:
        "Learn the parameters, estimate whether a configuration fits, apply it, and measure it — before spending 20–45 minutes on a real case."
    },
    help: {
      title: "How this page works",
      body:
        "Five steps: your machine, your server, your model and configuration, load and test, and the result. Nothing is loaded until step 4, and loading, unloading or testing always checks first whether a lab run is using the same host."
    },
    steps: {
      machine: "Your machine",
      server: "Your server",
      config: "Model & configuration",
      test: "Load & test",
      result: "Result"
    } as Record<StepKey, string>,
    machine: {
      heading: "Your machine",
      presetLabel: "Reference machine",
      presetCustom: "Other…",
      memoryKind: "Memory type",
      unified: "Unified (CPU and GPU share it)",
      discrete: "Discrete GPU + system RAM",
      gpuGb: "GPU / unified GB",
      ramGb: "System RAM GB",
      reserveGb: "Reserve for OS and apps (GB)",
      wiredFraction: "Share the GPU can use",
      wiredFractionHint:
        "Apple silicon: ~0.75 (macOS cap, raise with iogpu.wired_limit_mb). Jetson and DGX Spark: 1. Ryzen AI Max: 0.75 on Windows (96 of 128 GB), ~0.94 on Linux.",
      bandwidthGbs: "Memory bandwidth (GB/s)",
      commandsHeading: "Check the exact figures",
      macos: "macOS",
      linux: "Linux",
      windows: "Windows",
      copy: "Copy",
      copied: "Command copied.",
      saveLabel: "Save this machine as",
      saveNamePlaceholder: "e.g. My MacBook Pro",
      save: "Save machine",
      saved: "Machine saved.",
      savedMachines: "Saved machines",
      delete: "Delete",
      tooltips: {
        memoryKind:
          "Unified memory is one pool shared by the CPU, the GPU and the OS: Apple silicon, NVIDIA Jetson and DGX Spark, AMD Ryzen AI Max. A discrete GPU keeps its own VRAM, separate from system RAM.",
        reserve:
          "Memory the OS and other apps keep for themselves. Default 4 GB on unified memory, 1 GB of VRAM on a discrete GPU.",
        wiredFraction:
          "How much of the unified pool the GPU may use. macOS caps it (~0.75; raise with iogpu.wired_limit_mb). A Jetson or DGX Spark has no such cap (1). Ryzen AI Max sets it in firmware or the driver: 96 GB on Windows, ~120 GB on Linux.",
        bandwidthGbs:
          "The ceiling on how fast a model can write tokens: each token reads every active weight once. RTX 3090 936, RTX 4090 1008, M4 Max 546. Leave empty if unknown."
      }
    },
    server: {
      heading: "Your server",
      kind: "Server",
      url: "URL",
      urlPlaceholder: "http://192.168.0.10:1234",
      test: "Test connection",
      testing: "Testing…",
      unreachableHeading: "Could not reach the server",
      unreachableHint:
        "Usual causes: the server is off, it is not serving on the local network, a firewall, or the wrong port.",
      modelsHeading: "Downloaded models",
      name: "Name",
      params: "Params",
      quantization: "Quantization",
      size: "Size (GB)",
      maxContext: "Max context",
      tools: "Tools",
      loaded: "Loaded",
      servedContext: "Served context",
      yes: "Yes",
      no: "No"
    },
    config: {
      heading: "Model & configuration",
      pickModel: "Model",
      contextLength: "Context length",
      gpuOffload: "GPU offload",
      flashAttention: "Flash attention",
      kvCacheOnGpu: "KV cache on GPU",
      kvCacheType: "KV cache type",
      evalBatchSize: "Eval batch size",
      numExperts: "Number of active experts (MoE)",
      estimating: "Estimating…",
      ollamaNote:
        "Ollama: flash attention and the KV cache type are server environment settings (OLLAMA_FLASH_ATTENTION, OLLAMA_KV_CACHE_TYPE) — set them on the server and restart it. Context, GPU offload and eval batch are applied per request.",
      lmstudioNote: "LM Studio: GPU offload beyond this request is also adjustable by hand in the app.",
      tooltips: {
        contextLength:
          "Tokens the model can see at once. The harness system prompt alone is ~14k tokens, so agents need at least 32k, ideally 64k. Each doubling roughly doubles KV cache memory.",
        gpuOffload:
          "Fraction of layers placed on the GPU. Below 100% the rest runs on CPU, which is much slower.",
        flashAttention:
          "Faster attention and a smaller compute buffer. It does not shrink the KV cache.",
        kvCacheOnGpu:
          "Faster on GPU, but uses VRAM. Off moves the KV cache to system RAM, which is slower.",
        kvCacheType:
          "f16 is the baseline. q8_0 halves KV cache memory with a small quality loss; q4_0 saves more at a larger quality cost.",
        evalBatchSize:
          "Tokens processed per step while reading the prompt. Bigger is faster prefill, at the cost of a bigger compute buffer.",
        numExperts: "Active experts per token in a mixture-of-experts model."
      },
      evaluateOnlyNote: "Ignored in evaluate-only mode: the tests use what the server is running."
    },
    testStep: {
      heading: "Load & test",
      modeLoad: "Load from the lab",
      modeEvaluate: "I load it myself — evaluate only",
      servedConfigHeading: "What the server is running",
      load: "Load with this configuration",
      loading: "Loading…",
      unload: "Unload",
      unloading: "Unloading…",
      unloadAnyway: "Unload anyway",
      runTests: "Run tests",
      running: "Running…",
      noMachineNote: "Save the machine in step 1 to keep this test in the history.",
      refresh: "Refresh",
      testNames: {
        fit: "Fit",
        speed: "Speed",
        window: "Window",
        agent: "Agent"
      } as Record<LocalTestName, string>,
      inProgress: (name: string) => `Running ${name}…`,
      sessionLost:
        "The test session was lost — the API probably restarted. Run the tests again."
    },
    resultStep: {
      heading: "Result",
      servedConfigHeading: "Configuration tested",
      context: "Context",
      flashAttention: "Flash attention",
      kvOnGpu: "KV cache on GPU",
      evalBatch: "Eval batch",
      parallel: "Parallel",
      observedLabel: "Memory the OS shows (GB)",
      saveObserved: "Save",
      savedToHistory: (label: string) => `Saved to history as "${label}".`,
      renamePlaceholder: "New label",
      renameSave: "Save",
      saved: "Profile saved.",
      markRecommended: "Mark as recommended",
      recommended: "Recommended",
      compare: "Compare",
      notComparable: "Not comparable",
      useInNewEvaluation: "Use in New evaluation",
      promptTokens: "Prompt tokens",
      readSpeed: "Read tok/s",
      writeSpeed: "Write tok/s",
      ttft: "TTFT (s)",
      historyHeading: "History for this machine and model",
      noHistory: "No saved runs for this machine and model yet.",
      colLabel: "Label",
      colContext: "Context",
      colReadSpeed: "Read tok/s",
      colWriteSpeed: "Write tok/s",
      colSecondsPerStep: "s/step",
      colAgent: "Agent",
      colFit: "Fit",
      colWindow: "Window",
      colEstGb: "Est. GB",
      colDate: "Date",
      delete: "Delete",
      confirmDelete: "Delete this saved run? This cannot be undone.",
      compareSelection: "Pick exactly two rows to compare.",
      compareHeading: "Comparison",
      colMetric: "Metric",
      colDelta: "Δ%",
      colWinner: "Winner",
      winnerA: "A",
      winnerB: "B",
      winnerTie: "≈ tie",
      noiseNote: (pct: string) =>
        `Differences under ${pct}% are treated as run-to-run noise.`
    },
    headroom: {
      heading: "Headroom",
      ceiling: "Ceiling",
      measured: "Measured",
      typical: "Typical range",
      level: {
        near_ceiling: "Near the ceiling",
        some_headroom: "Some headroom",
        far_below: "Far below",
        compute_bound: "Compute-bound quant"
      } as Record<string, string>,
      noBandwidthHint:
        "Add your GPU's memory bandwidth in step 1 to see how close this is to the hardware's ceiling."
    },
    agentTable: {
      prompt: "Prompt",
      expected: "Expected tool",
      got: "Got",
      result: "Result"
    },
    verdictLabel: {
      ok: "Fits",
      tight: "Tight",
      fail: "Does not fit",
      inconclusive: "Inconclusive",
      not_applicable: "Not applicable"
    },
    unableToLoad: "Could not load the reference data.",
    unableToSave: "Could not save.",
    cancel: "Cancel",
    unloadForeign: {
      title: "Model has instances this session did not load",
      unloadAnyway: "Unload anyway"
    },
    alreadyLoaded: {
      title: "This model is already loaded",
      replace: "Replace it"
    }
  },
  es: {
    header: {
      title: "Optimizá tu modelo local",
      lede:
        "Aprendé los parámetros, estimá si una configuración entra, aplicala y medila — antes de gastar 20 a 45 minutos en un caso real."
    },
    help: {
      title: "Cómo funciona esta página",
      body:
        "Cinco pasos: tu máquina, tu servidor, tu modelo y configuración, cargar y probar, y el resultado. Nada se carga hasta el paso 4, y cargar, descargar o probar siempre revisa primero si una corrida del lab está usando el mismo host."
    },
    steps: {
      machine: "Tu máquina",
      server: "Tu servidor",
      config: "Modelo y configuración",
      test: "Cargar y probar",
      result: "Resultado"
    } as Record<StepKey, string>,
    machine: {
      heading: "Tu máquina",
      presetLabel: "Máquina de referencia",
      presetCustom: "Otra…",
      memoryKind: "Tipo de memoria",
      unified: "Unificada (CPU y GPU la comparten)",
      discrete: "GPU discreta + RAM del sistema",
      gpuGb: "GB de GPU / unificada",
      ramGb: "GB de RAM del sistema",
      reserveGb: "Reserva para el SO y otras apps (GB)",
      wiredFraction: "Parte que puede usar la GPU",
      wiredFractionHint:
        "Apple silicon: ~0,75 (límite de macOS, se sube con iogpu.wired_limit_mb). Jetson y DGX Spark: 1. Ryzen AI Max: 0,75 en Windows (96 de 128 GB), ~0,94 en Linux.",
      bandwidthGbs: "Ancho de banda de memoria (GB/s)",
      commandsHeading: "Revisá las cifras exactas",
      macos: "macOS",
      linux: "Linux",
      windows: "Windows",
      copy: "Copiar",
      copied: "Comando copiado.",
      saveLabel: "Guardar esta máquina como",
      saveNamePlaceholder: "ej. Mi MacBook Pro",
      save: "Guardar máquina",
      saved: "Máquina guardada.",
      savedMachines: "Máquinas guardadas",
      delete: "Eliminar",
      tooltips: {
        memoryKind:
          "La memoria unificada es un solo pool que comparten la CPU, la GPU y el SO: Apple silicon, NVIDIA Jetson y DGX Spark, AMD Ryzen AI Max. Una GPU discreta tiene su propia VRAM, separada de la RAM del sistema.",
        reserve:
          "Memoria que el SO y otras apps se reservan. Por defecto 4 GB en memoria unificada, 1 GB de VRAM en GPU discreta.",
        wiredFraction:
          "Cuánto del pool unificado puede usar la GPU. macOS lo limita (~0,75; se sube con iogpu.wired_limit_mb). Una Jetson o una DGX Spark no tienen ese límite (1). Ryzen AI Max lo fija el firmware o el driver: 96 GB en Windows, ~120 GB en Linux.",
        bandwidthGbs:
          "El límite de qué tan rápido un modelo puede escribir tokens: cada token lee cada peso activo una vez. RTX 3090 936, RTX 4090 1008, M4 Max 546. Dejá vacío si no lo sabés."
      }
    },
    server: {
      heading: "Tu servidor",
      kind: "Servidor",
      url: "URL",
      urlPlaceholder: "http://192.168.0.10:1234",
      test: "Probar conexión",
      testing: "Probando…",
      unreachableHeading: "No se pudo alcanzar el servidor",
      unreachableHint:
        "Causas habituales: el servidor está apagado, no sirve en la red local, un firewall, o el puerto equivocado.",
      modelsHeading: "Modelos descargados",
      name: "Nombre",
      params: "Params",
      quantization: "Cuantización",
      size: "Tamaño (GB)",
      maxContext: "Contexto máx.",
      tools: "Tools",
      loaded: "Cargado",
      servedContext: "Contexto servido",
      yes: "Sí",
      no: "No"
    },
    config: {
      heading: "Modelo y configuración",
      pickModel: "Modelo",
      contextLength: "Largo de contexto",
      gpuOffload: "Offload a GPU",
      flashAttention: "Flash attention",
      kvCacheOnGpu: "Caché KV en GPU",
      kvCacheType: "Tipo de caché KV",
      evalBatchSize: "Tamaño de eval batch",
      numExperts: "Cantidad de expertos activos (MoE)",
      estimating: "Estimando…",
      ollamaNote:
        "Ollama: flash attention y el tipo de caché KV son ajustes del entorno del servidor (OLLAMA_FLASH_ATTENTION, OLLAMA_KV_CACHE_TYPE) — configuralos en el servidor y reiniciá. Contexto, offload a GPU y eval batch se aplican por pedido.",
      lmstudioNote: "LM Studio: el offload a GPU más allá de este pedido también es ajustable a mano en la app.",
      tooltips: {
        contextLength:
          "Tokens que el modelo puede ver a la vez. Solo el system prompt del harness son ~14k tokens, así que los agentes necesitan al menos 32k, idealmente 64k. Cada duplicación duplica aproximadamente la memoria de caché KV.",
        gpuOffload:
          "Fracción de capas puestas en la GPU. Por debajo del 100% el resto corre en CPU, mucho más lento.",
        flashAttention:
          "Atención más rápida y un buffer de cómputo más chico. No reduce la caché KV.",
        kvCacheOnGpu:
          "Más rápida en GPU, pero usa VRAM. Apagada mueve la caché KV a la RAM del sistema, más lento.",
        kvCacheType:
          "f16 es la base. q8_0 reduce a la mitad la memoria de caché KV con poca pérdida de calidad; q4_0 ahorra más con más pérdida de calidad.",
        evalBatchSize:
          "Tokens procesados por paso al leer el prompt. Más grande es un prefill más rápido, a costa de un buffer de cómputo más grande.",
        numExperts: "Expertos activos por token en un modelo mixture-of-experts."
      },
      evaluateOnlyNote: "Ignorado en modo solo evaluar: las pruebas usan lo que el servidor está corriendo."
    },
    testStep: {
      heading: "Cargar y probar",
      modeLoad: "Cargar desde el lab",
      modeEvaluate: "Lo cargo yo — solo evaluar",
      servedConfigHeading: "Lo que el servidor está corriendo",
      load: "Cargar con esta configuración",
      loading: "Cargando…",
      unload: "Descargar",
      unloading: "Descargando…",
      unloadAnyway: "Descargar de todos modos",
      runTests: "Correr pruebas",
      running: "Corriendo…",
      noMachineNote: "Guardá la máquina en el paso 1 para conservar esta prueba en el historial.",
      refresh: "Actualizar",
      testNames: {
        fit: "Entra",
        speed: "Velocidad",
        window: "Ventana",
        agent: "Agente"
      } as Record<LocalTestName, string>,
      inProgress: (name: string) => `Corriendo ${name}…`,
      sessionLost:
        "Se perdió la sesión de pruebas — probablemente la API se reinició. Corré las pruebas de nuevo."
    },
    resultStep: {
      heading: "Resultado",
      servedConfigHeading: "Configuración probada",
      context: "Contexto",
      flashAttention: "Flash attention",
      kvOnGpu: "Caché KV en GPU",
      evalBatch: "Eval batch",
      parallel: "Paralelo",
      observedLabel: "Memoria que muestra el SO (GB)",
      saveObserved: "Guardar",
      savedToHistory: (label: string) => `Guardado en el historial como "${label}".`,
      renamePlaceholder: "Nueva etiqueta",
      renameSave: "Guardar",
      saved: "Perfil guardado.",
      markRecommended: "Marcar como recomendado",
      recommended: "Recomendado",
      compare: "Comparar",
      notComparable: "No comparables",
      useInNewEvaluation: "Usar en Nueva evaluación",
      promptTokens: "Tokens de prompt",
      readSpeed: "Tok/s lectura",
      writeSpeed: "Tok/s escritura",
      ttft: "TTFT (s)",
      historyHeading: "Historial para esta máquina y modelo",
      noHistory: "Todavía no hay corridas guardadas para esta máquina y modelo.",
      colLabel: "Etiqueta",
      colContext: "Contexto",
      colReadSpeed: "Tok/s lectura",
      colWriteSpeed: "Tok/s escritura",
      colSecondsPerStep: "s/paso",
      colAgent: "Agente",
      colFit: "Entra",
      colWindow: "Ventana",
      colEstGb: "GB est.",
      colDate: "Fecha",
      delete: "Eliminar",
      confirmDelete: "¿Eliminar esta corrida guardada? No se puede deshacer.",
      compareSelection: "Elegí exactamente dos filas para comparar.",
      compareHeading: "Comparación",
      colMetric: "Métrica",
      colDelta: "Δ%",
      colWinner: "Ganador",
      winnerA: "A",
      winnerB: "B",
      winnerTie: "≈ empate",
      noiseNote: (pct: string) =>
        `Las diferencias por debajo del ${pct}% se consideran ruido entre corridas.`
    },
    headroom: {
      heading: "Margen",
      ceiling: "Techo",
      measured: "Medido",
      typical: "Rango típico",
      level: {
        near_ceiling: "Cerca del techo",
        some_headroom: "Algo de margen",
        far_below: "Muy por debajo",
        compute_bound: "Cuantización limitada por cómputo"
      } as Record<string, string>,
      noBandwidthHint:
        "Agregá el ancho de banda de memoria de tu GPU en el paso 1 para ver qué tan cerca está esto del techo del hardware."
    },
    agentTable: {
      prompt: "Prompt",
      expected: "Tool esperada",
      got: "Obtenido",
      result: "Resultado"
    },
    verdictLabel: {
      ok: "Entra",
      tight: "Justa",
      fail: "No entra",
      inconclusive: "Inconcluso",
      not_applicable: "No aplica"
    },
    unableToLoad: "No se pudieron cargar los datos de referencia.",
    unableToSave: "No se pudo guardar.",
    cancel: "Cancelar",
    unloadForeign: {
      title: "El modelo tiene instancias que esta sesión no cargó",
      unloadAnyway: "Descargar de todos modos"
    },
    alreadyLoaded: {
      title: "Este modelo ya está cargado",
      replace: "Reemplazarlo"
    }
  }
});

const OS_COMMANDS: Record<"macos" | "linux" | "windows", string> = {
  macos: "system_profiler SPHardwareDataType | grep Memory",
  linux: "nvidia-smi --query-gpu=name,memory.total --format=csv && free -g",
  windows: "nvidia-smi"
};

function defaultLoadConfig(reference: LocalReference | null, model: LocalModelInfo | null): LocalLoadConfig {
  const base = reference?.default_load ?? {
    context_length: 32768,
    gpu_offload: 1,
    flash_attention: null,
    offload_kv_cache_to_gpu: null,
    eval_batch_size: null,
    num_experts: null,
    parallel: 1,
    kv_cache_bytes: 2
  };
  if (model?.max_context && base.context_length > model.max_context) {
    return { ...base, context_length: model.max_context };
  }
  return { ...base };
}

/**
 * The context-length select only ever offers `contextOptions` filtered to
 * `<= maxContext`, so a value picked from outside that set (the previous
 * model's, or the config default) would show one number while sending
 * another the select cannot even display. Clamping to the largest surviving
 * option — or to `maxContext` itself when it is smaller than every option —
 * keeps the displayed value and the sent value the same thing.
 */
function clampContextToOptions(
  contextOptions: number[],
  maxContext: number | null | undefined,
  requested: number
): number {
  const allowed = maxContext
    ? contextOptions.filter((option) => option <= maxContext)
    : contextOptions;
  if (allowed.length === 0) {
    return maxContext ?? requested;
  }
  if (allowed.includes(requested)) {
    return requested;
  }
  return Math.max(...allowed);
}

function verdictBadgeClass(verdict: string): string {
  if (verdict === "ok") return "badge ok";
  if (verdict === "tight") return "badge warn";
  if (verdict === "fail") return "badge error";
  return "badge neutral";
}

function headroomBadgeClass(level: string): string {
  if (level === "near_ceiling") return "badge ok";
  if (level === "some_headroom") return "badge warn";
  if (level === "far_below") return "badge error";
  return "badge muted";
}

/**
 * Five-step guided page: your machine, your server, model & configuration,
 * load & test, and the result. See
 * `docs/superpowers/specs/2026-11-08-local-model-tuning-design.md`.
 */
export function LocalModels() {
  const m = useMessages(MESSAGES);
  const navigate = useNavigate();

  const [step, setStep] = useState<StepKey>("machine");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [reference, setReference] = useState<LocalReference | null>(null);
  const [savedMachines, setSavedMachines] = useState<LocalMachine[]>([]);

  // --- Step 1: machine ------------------------------------------------
  const [presetId, setPresetId] = useState("");
  const [memoryKind, setMemoryKind] = useState<"unified" | "discrete">("unified");
  const [gpuGb, setGpuGb] = useState(36);
  const [ramGb, setRamGb] = useState(36);
  const [reserveGb, setReserveGb] = useState(4);
  const [wiredFraction, setWiredFraction] = useState(0.75);
  const [bandwidthGbs, setBandwidthGbs] = useState("");
  const [machineName, setMachineName] = useState("");

  // --- Step 2: server ---------------------------------------------------
  const [serverKind, setServerKind] = useState<LocalServerKind>("lmstudio");
  const [serverUrl, setServerUrl] = useState("");
  const [testingServer, setTestingServer] = useState(false);
  const [serverTest, setServerTest] = useState<LocalServerTestResult | null>(null);

  // --- Step 3: model & configuration -------------------------------------
  const [modelId, setModelId] = useState("");
  const [loadConfig, setLoadConfig] = useState<LocalLoadConfig>(defaultLoadConfig(null, null));
  const [estimate, setEstimate] = useState<LocalMemoryEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);

  // --- Step 4: load & test ------------------------------------------------
  // "I load it myself — evaluate only": the lab never loads/reconfigures the
  // model, it reads whatever the server already serves. Defaults to on for an
  // Ollama server or a Jetson preset — the usual "someone else runs it" cases.
  const [evaluateOnly, setEvaluateOnly] = useState(false);
  const [evaluateOnlyTouched, setEvaluateOnlyTouched] = useState(false);
  const [notLoaded, setNotLoaded] = useState<LocalNotLoadedDetail | null>(null);
  const [loadBusy, setLoadBusy] = useState(false);
  const [unloadBusy, setUnloadBusy] = useState(false);
  const [loadResult, setLoadResult] = useState<LocalLoadResult | null>(null);
  const [testSession, setTestSession] = useState<LocalTestSession | null>(null);
  const [testingBusy, setTestingBusy] = useState(false);
  const [guard, setGuard] = useState<LocalGuardReport | null>(null);
  const [guardAction, setGuardAction] = useState<HostGuardAction | null>(null);
  // Set when the model has instances this session did not load itself — an
  // unload needs `force: true` to proceed, offered as "Unload anyway".
  const [unloadForeign, setUnloadForeign] =
    useState<LocalUnloadForeignInstancesDetail | null>(null);
  // Set when the model is already loaded with another configuration: a second
  // LM Studio load would add a copy, so "Replace it" unloads the current one.
  const [alreadyLoaded, setAlreadyLoaded] = useState<{
    instances: string[];
    message: string;
    acknowledgeGuard: boolean;
  } | null>(null);
  // Consecutive failed polls of the running test session, reset on a success
  // and on every fresh session. 5 in a row gives up rather than polling a dead
  // session forever.
  const pollFailures = useRef(0);

  // --- Step 5: result -----------------------------------------------------
  const [observedMemoryGb, setObservedMemoryGb] = useState("");
  const [renameLabel, setRenameLabel] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [history, setHistory] = useState<LocalHistoryRow[]>([]);
  const [compareSelection, setCompareSelection] = useState<string[]>([]);
  const [compareResult, setCompareResult] = useState<{
    comparable: boolean;
    reason: string;
    comparison?: LocalComparison;
  } | null>(null);

  const estimateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Bumped on every estimate request; a response is applied only if it is
  // still the latest one in flight when it comes back.
  const estimateSeq = useRef(0);

  const machine: LocalMachine | undefined = savedMachines.find(
    (candidate) => candidate.name === machineName
  );

  const machineSpec: MachineSpec = useMemo(
    () => ({
      memory_kind: memoryKind,
      gpu_gb: gpuGb,
      ram_gb: ramGb,
      reserve_gb: reserveGb,
      gpu_wired_fraction: wiredFraction,
      bandwidth_gbs: bandwidthGbs ? Number(bandwidthGbs) : null
    }),
    [memoryKind, gpuGb, ramGb, reserveGb, wiredFraction, bandwidthGbs]
  );

  const selectedModel: LocalModelInfo | null =
    serverTest?.models.find((item) => item.id === modelId) ?? null;

  useEffect(() => {
    Promise.all([api.local.getReference(), api.local.listMachines()])
      .then(([ref, machines]) => {
        setReference(ref);
        setSavedMachines(machines);
        setServerUrl((current) => current || ref.defaults.lmstudio_url);
        setLoadConfig(defaultLoadConfig(ref, null));
      })
      .catch(() => setError(m.unableToLoad));
  }, []);

  // A model, server or server kind change means the operator left step 4
  // behind — the previous load, its guard state and any running test
  // belonged to a different host/model and must never be acted on again
  // (Unload / Run tests would otherwise target a model this session never
  // loaded).
  useEffect(() => {
    setLoadResult(null);
    setTestSession(null);
    setGuard(null);
    setGuardAction(null);
    setUnloadForeign(null);
    setNotLoaded(null);
    pollFailures.current = 0;
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId, serverUrl, serverKind]);

  useEffect(() => {
    if (step !== "test") {
      return;
    }
    if (!testSession || testSession.status !== "running") {
      return;
    }
    let cancelled = false;
    pollTimer.current = setTimeout(() => {
      void api.local
        .getTestSession(testSession.id)
        .then((updated) => {
          if (cancelled) return;
          pollFailures.current = 0;
          setTestSession(updated);
        })
        .catch((err: ApiError | Error) => {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 404) {
            setTestSession((current) =>
              current
                ? {
                    ...current,
                    status: "failed",
                    error: m.testStep.sessionLost
                  }
                : current
            );
            return;
          }
          pollFailures.current += 1;
          if (pollFailures.current >= 5) {
            setTestSession((current) =>
              current
                ? {
                    ...current,
                    status: "failed",
                    error: current.error || m.testStep.sessionLost
                  }
                : current
            );
          }
          // Keep polling below the failure cap — a transient network error is
          // not the same as a lost session, and backing off (rather than
          // stopping) gives a flaky connection a chance to recover.
        });
    }, 2000);
    return () => {
      cancelled = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [step, testSession]);

  // Live estimate, debounced 300ms: every change to the model or its
  // configuration schedules a fresh POST /estimate, cancelling the previous
  // timer rather than piling up requests while the operator is still typing.
  // `estimateSeq` guards against an out-of-order response: a slow request for
  // a stale configuration must never overwrite the result of a faster, later
  // one.
  useEffect(() => {
    if (estimateTimer.current) {
      clearTimeout(estimateTimer.current);
    }
    if (!selectedModel) {
      setEstimate(null);
      return;
    }
    estimateTimer.current = setTimeout(() => {
      const requestId = (estimateSeq.current += 1);
      setEstimating(true);
      api.local
        .estimate(selectedModel, loadConfig, machineSpec)
        .then((result) => {
          if (requestId === estimateSeq.current) {
            setEstimate(result);
          }
        })
        .catch(() => {
          if (requestId === estimateSeq.current) {
            setEstimate(null);
          }
        })
        .finally(() => {
          if (requestId === estimateSeq.current) {
            setEstimating(false);
          }
        });
    }, 300);
    return () => {
      if (estimateTimer.current) clearTimeout(estimateTimer.current);
    };
  }, [selectedModel, loadConfig, machineSpec]);

  // Clamp context_length to the select's own option set whenever the picked
  // model changes (or its max_context becomes known) — see
  // clampContextToOptions.
  useEffect(() => {
    if (!selectedModel || !reference) {
      return;
    }
    setLoadConfig((current) => {
      const clamped = clampContextToOptions(
        reference.context_options,
        selectedModel.max_context,
        current.context_length
      );
      return clamped === current.context_length ? current : { ...current, context_length: clamped };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel?.id, selectedModel?.max_context, reference]);

  // Default evaluate-only on for an Ollama server or a Jetson machine — the
  // usual case where something other than the lab is running the model.
  // Never overrides a choice the operator already made.
  useEffect(() => {
    if (evaluateOnlyTouched) return;
    setEvaluateOnly(serverKind === "ollama" || presetId.startsWith("jetson"));
  }, [serverKind, presetId, evaluateOnlyTouched]);

  const refreshHistory = () => {
    if (!machine || !modelId) {
      setHistory([]);
      return;
    }
    api.local
      .history(machine.id, modelId)
      .then(setHistory)
      .catch(() => setHistory([]));
  };

  useEffect(refreshHistory, [machine, modelId]);

  // The finished session was saved automatically (a machine was selected) —
  // refresh the history so the new row and its "Saved to history" line show up.
  useEffect(() => {
    if (testSession?.status === "done" && testSession.profile_id != null) {
      refreshHistory();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testSession?.status, testSession?.profile_id]);

  const applyPreset = (id: string) => {
    setPresetId(id);
    const preset: RefMachine | undefined = reference?.machines.find((item) => item.id === id);
    if (preset) {
      setMemoryKind(preset.memory_kind);
      setGpuGb(preset.gpu_gb);
      setRamGb(preset.ram_gb);
      setReserveGb(preset.reserve_gb);
      if (preset.bandwidth_gbs > 0) {
        setBandwidthGbs(String(preset.bandwidth_gbs));
      }
      if (preset.gpu_wired_fraction != null) {
        setWiredFraction(preset.gpu_wired_fraction);
      }
    }
  };

  const applySavedMachine = (name: string) => {
    setMachineName(name);
    const found = savedMachines.find((item) => item.name === name);
    if (found) {
      setMemoryKind(found.memory_kind);
      setGpuGb(found.gpu_gb);
      setRamGb(found.ram_gb);
      setReserveGb(found.reserve_gb);
      setWiredFraction(found.gpu_wired_fraction);
      setBandwidthGbs(found.bandwidth_gbs != null ? String(found.bandwidth_gbs) : "");
    }
  };

  const handleSaveMachine = () => {
    if (!machineName.trim()) {
      return;
    }
    setError(null);
    api.local
      .createMachine({
        name: machineName.trim(),
        memory_kind: memoryKind,
        gpu_gb: gpuGb,
        ram_gb: ramGb,
        reserve_gb: reserveGb,
        gpu_wired_fraction: wiredFraction,
        bandwidth_gbs: bandwidthGbs ? Number(bandwidthGbs) : null,
        notes: ""
      })
      .then((created) => {
        setSavedMachines((current) => [created, ...current.filter((item) => item.id !== created.id)]);
        setNotice(m.machine.saved);
      })
      .catch((err: Error) => setError(err.message));
  };

  const handleDeleteMachine = (id: string) => {
    api.local
      .deleteMachine(id)
      .then(() => setSavedMachines((current) => current.filter((item) => item.id !== id)))
      .catch((err: Error) => setError(err.message));
  };

  const handleTestServer = () => {
    setTestingServer(true);
    setError(null);
    setServerTest(null);
    api.local
      .testServer(serverKind, serverUrl.trim())
      .then((result) => {
        setServerTest(result);
        if (!result.reachable) {
          setError(result.error || m.server.unreachableHint);
        }
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setTestingServer(false));
  };

  const runGuardedAction = (action: HostGuardAction, run: (ack: boolean) => Promise<void>) => {
    api.local
      .guard(serverKind, serverUrl.trim())
      .then((report) => {
        if (report.clear) {
          return run(false);
        }
        setGuard(report);
        setGuardAction(action);
        return undefined;
      })
      .catch(() => run(false));
  };

  const handleLoad = (acknowledgeGuard = false, replace = false) => {
    setLoadBusy(true);
    setError(null);
    return api.local
      .load(serverKind, serverUrl.trim(), modelId, loadConfig, acknowledgeGuard, replace)
      .then((result) => {
        setLoadResult(result);
        setGuard(null);
        setGuardAction(null);
        setAlreadyLoaded(null);
      })
      .catch((err: ApiError | Error) => {
        if (err instanceof ApiError && err.status === 409) {
          const detail = err.detail as
            | { guard?: LocalGuardReport; loaded_instances?: string[]; message?: string }
            | undefined;
          if (detail?.guard) {
            setGuard(detail.guard);
            setGuardAction("load");
            return;
          }
          if (detail?.loaded_instances) {
            // LM Studio adds an instance per load; loading again would keep two
            // copies in memory, so the backend asks before replacing it.
            setGuard(null);
            setGuardAction(null);
            setAlreadyLoaded({
              instances: detail.loaded_instances,
              message: detail.message ?? "",
              acknowledgeGuard
            });
            return;
          }
        }
        // Not a guard conflict (or guard resolution failed outright) — the
        // guard modal must not keep floating over the error it is unrelated
        // to.
        setGuard(null);
        setGuardAction(null);
        setError(err.message);
      })
      .finally(() => setLoadBusy(false));
  };

  const handleUnload = (acknowledgeGuard = false, force = false) => {
    setUnloadBusy(true);
    setError(null);
    const instanceId = loadResult?.served.instance_id ?? null;
    return api.local
      .unload(serverKind, serverUrl.trim(), modelId, acknowledgeGuard, instanceId, force)
      .then(() => {
        setLoadResult(null);
        setGuard(null);
        setGuardAction(null);
        setUnloadForeign(null);
      })
      .catch((err: ApiError | Error) => {
        if (err instanceof ApiError && err.status === 409) {
          const detail = err.detail as
            | { guard?: LocalGuardReport; instances?: string[]; message?: string }
            | undefined;
          if (detail?.guard) {
            setGuard(detail.guard);
            setGuardAction("unload");
            return;
          }
          if (detail?.instances) {
            setUnloadForeign({ instances: detail.instances, message: detail.message ?? err.message });
            return;
          }
        }
        setGuard(null);
        setGuardAction(null);
        setError(err.message);
      })
      .finally(() => setUnloadBusy(false));
  };

  const handleRunTests = (acknowledgeGuard = false) => {
    if (!selectedModel || (!evaluateOnly && !loadResult)) {
      return Promise.resolve();
    }
    setTestingBusy(true);
    setError(null);
    setNotLoaded(null);
    return api.local
      .startTests(
        serverKind,
        serverUrl.trim(),
        selectedModel,
        loadConfig,
        machineSpec,
        loadResult?.served ?? EMPTY_SERVED_CONFIG,
        acknowledgeGuard,
        machine ? Number(machine.id) : null,
        estimate,
        evaluateOnly
      )
      .then((session) => {
        pollFailures.current = 0;
        setTestSession(session);
        setGuard(null);
        setGuardAction(null);
      })
      .catch((err: ApiError | Error) => {
        if (err instanceof ApiError && err.status === 409) {
          const detail = err.detail as
            | { guard?: LocalGuardReport; not_loaded?: string; message?: string }
            | undefined;
          if (detail?.guard) {
            setGuard(detail.guard);
            setGuardAction("tests");
            return;
          }
          if (detail?.not_loaded) {
            setNotLoaded({ not_loaded: detail.not_loaded, message: detail.message ?? err.message });
            return;
          }
        }
        setGuard(null);
        setGuardAction(null);
        setError(err.message);
      })
      .finally(() => setTestingBusy(false));
  };

  // The saved profile for this finished session, when a machine was selected
  // and the backend filed it automatically (ADR "local model tuning: history").
  const savedProfile: LocalTuningProfile | undefined =
    testSession?.profile_id != null
      ? history.find((row) => row.profile.id === testSession.profile_id)?.profile
      : undefined;

  const handleSaveObservedMemory = () => {
    if (!savedProfile) {
      return;
    }
    setError(null);
    api.local
      .updateProfile(savedProfile.id, {
        observed_memory_gb: observedMemoryGb ? Number(observedMemoryGb) : null
      })
      .then(() => {
        setNotice(m.resultStep.saved);
        refreshHistory();
      })
      .catch((err: Error) => setError(m.unableToSave + " " + err.message));
  };

  const handleRenameProfile = () => {
    if (!savedProfile || !renameLabel.trim()) {
      return;
    }
    setRenaming(true);
    api.local
      .updateProfile(savedProfile.id, { label: renameLabel.trim() })
      .then(() => {
        setRenameLabel("");
        refreshHistory();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setRenaming(false));
  };

  const handleMarkRecommended = (id: string) => {
    api.local
      .updateProfile(id, { recommended: true })
      .then(() => refreshHistory())
      .catch((err: Error) => setError(err.message));
  };

  const handleDeleteHistoryRow = (id: string) => {
    if (!window.confirm(m.resultStep.confirmDelete)) {
      return;
    }
    api.local
      .deleteProfile(id)
      .then(() => {
        setCompareSelection((current) => current.filter((item) => item !== id));
        refreshHistory();
      })
      .catch((err: Error) => setError(err.message));
  };

  const toggleCompareSelection = (id: string) => {
    setCompareResult(null);
    setCompareSelection((current) => {
      if (current.includes(id)) {
        return current.filter((item) => item !== id);
      }
      if (current.length >= 2) {
        return [current[1], id];
      }
      return [...current, id];
    });
  };

  const handleCompare = () => {
    if (compareSelection.length !== 2) {
      setError(m.resultStep.compareSelection);
      return;
    }
    setError(null);
    const [a, b] = compareSelection;
    api.local
      .compareProfiles(a, b)
      .then((result) =>
        setCompareResult({
          comparable: result.comparable,
          reason: result.reason,
          comparison: result.comparison
        })
      )
      .catch((err: Error) => setError(err.message));
  };

  const handleUseInNewEvaluation = () => {
    navigate(`/evaluate/new?executor=${serverKind}/${encodeURIComponent(modelId)}`);
  };

  const reached = (key: StepKey): boolean => {
    if (key === "machine") return true;
    if (key === "server") return true;
    if (key === "config") return Boolean(serverTest?.reachable);
    if (key === "test") return Boolean(selectedModel);
    if (key === "result") return Boolean(testSession);
    return false;
  };

  const stepIndex = STEPS.findIndex((item) => item.key === step);

  return (
    <section className="page local-tune">
      <header className="page-header">
        <div>
          <h1>{m.header.title}</h1>
          <p className="lede">{m.header.lede}</p>
        </div>
      </header>

      <HelpPanel title={m.help.title}>
        <p>{m.help.body}</p>
      </HelpPanel>

      <ol className="stepper">
        {STEPS.map((item, index) => (
          <li
            className={[
              "stepper-item",
              item.key === step ? "stepper-item--current" : "",
              index < stepIndex ? "stepper-item--done" : ""
            ]
              .filter(Boolean)
              .join(" ")}
            key={item.key}
          >
            <button disabled={!reached(item.key)} onClick={() => setStep(item.key)} type="button">
              <span className="stepper-index">{index + 1}</span>
              <span>{m.steps[item.key]}</span>
            </button>
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}

      {guard && guardAction ? (
        <HostGuardModal
          action={guardAction}
          guard={guard}
          onCancel={() => {
            setGuard(null);
            setGuardAction(null);
          }}
          onContinue={() => {
            if (guardAction === "load") return handleLoad(true);
            if (guardAction === "unload") return handleUnload(true);
            if (guardAction === "tests") return handleRunTests(true);
            return undefined;
          }}
        />
      ) : null}

      {alreadyLoaded ? (
        <div className="modal-overlay">
          <div aria-modal="true" className="modal-dialog" role="dialog">
            <h2>{m.alreadyLoaded.title}</h2>
            <p>{alreadyLoaded.message}</p>
            <ul>
              {alreadyLoaded.instances.map((instance) => (
                <li key={instance}>{instance}</li>
              ))}
            </ul>
            <div className="toolbar">
              <button className="secondary" onClick={() => setAlreadyLoaded(null)} type="button">
                {m.cancel}
              </button>
              <button
                className="primary"
                disabled={loadBusy}
                onClick={() => void handleLoad(alreadyLoaded.acknowledgeGuard, true)}
                type="button"
              >
                {m.alreadyLoaded.replace}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {unloadForeign ? (
        <div className="modal-overlay">
          <div aria-modal="true" className="modal-dialog" role="dialog">
            <h2>{m.unloadForeign.title}</h2>
            <p>{unloadForeign.message}</p>
            <ul>
              {unloadForeign.instances.map((instance) => (
                <li key={instance}>{instance}</li>
              ))}
            </ul>
            <div className="toolbar">
              <button className="secondary" onClick={() => setUnloadForeign(null)} type="button">
                {m.cancel}
              </button>
              <button
                className="primary"
                disabled={unloadBusy}
                onClick={() => void handleUnload(false, true)}
                type="button"
              >
                {unloadBusy ? m.testStep.unloading : m.unloadForeign.unloadAnyway}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {step === "machine" ? (
        <div className="local-tune-step">
          <div className="panel">
            <div className="panel-head">
              <h2>{m.machine.heading}</h2>
            </div>
            <div className="form-grid">
              <label className="field">
                <span>{m.machine.presetLabel}</span>
                <select onChange={(event) => applyPreset(event.target.value)} value={presetId}>
                  <option value="">{m.machine.presetCustom}</option>
                  {(reference?.machines ?? []).map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>
                  {m.machine.memoryKind} <InfoTooltip text={m.machine.tooltips.memoryKind} />
                </span>
                <select
                  onChange={(event) => setMemoryKind(event.target.value as "unified" | "discrete")}
                  value={memoryKind}
                >
                  <option value="unified">{m.machine.unified}</option>
                  <option value="discrete">{m.machine.discrete}</option>
                </select>
              </label>
              <label className="field">
                <span>{m.machine.gpuGb}</span>
                <input
                  min={0}
                  onChange={(event) => setGpuGb(Number(event.target.value))}
                  type="number"
                  value={gpuGb}
                />
              </label>
              <label className="field">
                <span>{m.machine.ramGb}</span>
                <input
                  min={0}
                  onChange={(event) => setRamGb(Number(event.target.value))}
                  type="number"
                  value={ramGb}
                />
              </label>
              <label className="field">
                <span>
                  {m.machine.reserveGb} <InfoTooltip text={m.machine.tooltips.reserve} />
                </span>
                <input
                  min={0}
                  onChange={(event) => setReserveGb(Number(event.target.value))}
                  type="number"
                  value={reserveGb}
                />
              </label>
              {memoryKind === "unified" ? (
                <label className="field">
                  <span>
                    {m.machine.wiredFraction} <InfoTooltip text={m.machine.tooltips.wiredFraction} />
                  </span>
                  <input
                    max={1}
                    min={0.1}
                    onChange={(event) => setWiredFraction(Number(event.target.value))}
                    step={0.05}
                    type="number"
                    value={wiredFraction}
                  />
                  <span className="muted small">{m.machine.wiredFractionHint}</span>
                </label>
              ) : null}
              <label className="field">
                <span>
                  {m.machine.bandwidthGbs} <InfoTooltip text={m.machine.tooltips.bandwidthGbs} />
                </span>
                <input
                  min={0}
                  onChange={(event) => setBandwidthGbs(event.target.value)}
                  type="number"
                  value={bandwidthGbs}
                />
              </label>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>{m.machine.commandsHeading}</h3>
            </div>
            <div className="form-grid">
              {(["macos", "linux", "windows"] as const).map((os) => (
                <div className="card" key={os}>
                  <h3>{m.machine[os]}</h3>
                  <pre className="command-preview">{OS_COMMANDS[os]}</pre>
                  <button
                    className="secondary"
                    onClick={() => {
                      void navigator.clipboard.writeText(OS_COMMANDS[os]);
                      setNotice(m.machine.copied);
                    }}
                    type="button"
                  >
                    {m.machine.copy}
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h3>{m.machine.saveLabel}</h3>
            </div>
            <div className="toolbar">
              <input
                onChange={(event) => setMachineName(event.target.value)}
                placeholder={m.machine.saveNamePlaceholder}
                value={machineName}
              />
              <button className="secondary" onClick={handleSaveMachine} type="button">
                {m.machine.save}
              </button>
            </div>
            {savedMachines.length > 0 ? (
              <>
                <h3>{m.machine.savedMachines}</h3>
                <ul>
                  {savedMachines.map((item) => (
                    <li key={item.id}>
                      <button
                        className="button-link"
                        onClick={() => applySavedMachine(item.name)}
                        type="button"
                      >
                        {item.name}
                      </button>{" "}
                      <span className="muted small">
                        ({item.memory_kind}, {item.gpu_gb} GB / {item.ram_gb} GB)
                      </span>{" "}
                      <button
                        className="button-link"
                        onClick={() => handleDeleteMachine(item.id)}
                        type="button"
                      >
                        {m.machine.delete}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>

          <div className="toolbar">
            <button className="primary" onClick={() => setStep("server")} type="button">
              {m.steps.server}
            </button>
          </div>
        </div>
      ) : null}

      {step === "server" ? (
        <div className="local-tune-step">
          <div className="panel">
            <div className="panel-head">
              <h2>{m.server.heading}</h2>
            </div>
            <div className="form-grid">
              <label className="field">
                <span>{m.server.kind}</span>
                <select
                  onChange={(event) => {
                    const kind = event.target.value as LocalServerKind;
                    setServerKind(kind);
                    setServerUrl(
                      kind === "lmstudio"
                        ? (reference?.defaults.lmstudio_url ?? "")
                        : (reference?.defaults.ollama_url ?? "")
                    );
                  }}
                  value={serverKind}
                >
                  <option value="lmstudio">LM Studio</option>
                  <option value="ollama">Ollama</option>
                </select>
              </label>
              <label className="field">
                <span>{m.server.url}</span>
                <input
                  onChange={(event) => setServerUrl(event.target.value)}
                  placeholder={m.server.urlPlaceholder}
                  value={serverUrl}
                />
              </label>
            </div>
            <div className="toolbar">
              <button
                className="secondary"
                disabled={testingServer || !serverUrl.trim()}
                onClick={handleTestServer}
                type="button"
              >
                {testingServer ? m.server.testing : m.server.test}
              </button>
            </div>
          </div>

          {serverTest && !serverTest.reachable ? (
            <div className="callout callout--danger">
              <div>
                <strong>{m.server.unreachableHeading}</strong>
                <p>{serverTest.error || m.server.unreachableHint}</p>
                <p className="muted small">{serverUrl}</p>
              </div>
            </div>
          ) : null}

          {serverTest && serverTest.reachable ? (
            <div className="panel">
              <div className="panel-head">
                <h3>{m.server.modelsHeading}</h3>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{m.server.name}</th>
                    <th>{m.server.params}</th>
                    <th>{m.server.quantization}</th>
                    <th>{m.server.size}</th>
                    <th>{m.server.maxContext}</th>
                    <th>{m.server.tools}</th>
                    <th>{m.server.loaded}</th>
                    <th>{m.server.servedContext}</th>
                  </tr>
                </thead>
                <tbody>
                  {serverTest.models.map((item) => (
                    <tr
                      className={item.id === modelId ? "selectable active" : "selectable"}
                      key={item.id}
                      onClick={() => setModelId(item.id)}
                    >
                      <td>{item.display_name || item.id}</td>
                      <td>{item.params}</td>
                      <td>{item.quantization}</td>
                      <td>
                        {item.size_bytes ? (item.size_bytes / 1e9).toFixed(1) : "—"}
                      </td>
                      <td>{item.max_context ?? "—"}</td>
                      <td>
                        {item.supports_tools === null
                          ? "—"
                          : item.supports_tools
                            ? m.server.yes
                            : m.server.no}
                      </td>
                      <td>{item.loaded ? m.server.yes : m.server.no}</td>
                      <td>{item.served?.context_length ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="toolbar">
            <button
              className="primary"
              disabled={!serverTest?.reachable}
              onClick={() => setStep("config")}
              type="button"
            >
              {m.steps.config}
            </button>
          </div>
        </div>
      ) : null}

      {step === "config" ? (
        <div className="local-tune-step">
          <div className="panel">
            <div className="panel-head">
              <h2>{m.config.heading}</h2>
            </div>
            <label className="field">
              <span>{m.config.pickModel}</span>
              <select onChange={(event) => setModelId(event.target.value)} value={modelId}>
                <option value="">{m.config.pickModel}</option>
                {(serverTest?.models ?? []).map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.display_name || item.id}
                  </option>
                ))}
              </select>
            </label>

            {evaluateOnly ? <p className="muted small">{m.config.evaluateOnlyNote}</p> : null}

            {selectedModel ? (
              <div className="form-grid">
                <label className="field">
                  <span>
                    {m.config.contextLength} <InfoTooltip text={m.config.tooltips.contextLength} />
                  </span>
                  <select
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        context_length: Number(event.target.value)
                      }))
                    }
                    value={loadConfig.context_length}
                  >
                    {(reference?.context_options ?? []).filter(
                      (option) => !selectedModel.max_context || option <= selectedModel.max_context
                    ).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>
                    {m.config.gpuOffload} <InfoTooltip text={m.config.tooltips.gpuOffload} />
                  </span>
                  <select
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        gpu_offload: Number(event.target.value)
                      }))
                    }
                    value={loadConfig.gpu_offload}
                  >
                    {GPU_OFFLOAD_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {Math.round(option * 100)}%
                      </option>
                    ))}
                  </select>
                </label>
                <label className="switch">
                  <span>
                    {m.config.flashAttention} <InfoTooltip text={m.config.tooltips.flashAttention} />
                  </span>
                  <input
                    checked={loadConfig.flash_attention ?? false}
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        flash_attention: event.target.checked
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="switch">
                  <span>
                    {m.config.kvCacheOnGpu} <InfoTooltip text={m.config.tooltips.kvCacheOnGpu} />
                  </span>
                  <input
                    checked={loadConfig.offload_kv_cache_to_gpu ?? false}
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        offload_kv_cache_to_gpu: event.target.checked
                      }))
                    }
                    type="checkbox"
                  />
                </label>
                <label className="field">
                  <span>
                    {m.config.kvCacheType} <InfoTooltip text={m.config.tooltips.kvCacheType} />
                  </span>
                  <select
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        kv_cache_bytes: Number(event.target.value)
                      }))
                    }
                    value={loadConfig.kv_cache_bytes}
                  >
                    {Object.entries(reference?.kv_cache_types ?? { f16: 2 }).map(([name, bytes]) => (
                      <option key={name} value={bytes}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>
                    {m.config.evalBatchSize} <InfoTooltip text={m.config.tooltips.evalBatchSize} />
                  </span>
                  <select
                    onChange={(event) =>
                      setLoadConfig((current) => ({
                        ...current,
                        eval_batch_size: Number(event.target.value)
                      }))
                    }
                    value={loadConfig.eval_batch_size ?? ""}
                  >
                    {(reference?.eval_batch_options ?? []).map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
                {selectedModel.is_moe ? (
                  <label className="field">
                    <span>
                      {m.config.numExperts} <InfoTooltip text={m.config.tooltips.numExperts} />
                    </span>
                    <input
                      min={1}
                      onChange={(event) =>
                        setLoadConfig((current) => ({
                          ...current,
                          num_experts: Number(event.target.value)
                        }))
                      }
                      type="number"
                      value={loadConfig.num_experts ?? 1}
                    />
                  </label>
                ) : null}
              </div>
            ) : null}

            <p className="muted small">
              {serverKind === "ollama" ? m.config.ollamaNote : m.config.lmstudioNote}
            </p>

            {estimating ? <Spinner label={m.config.estimating} /> : null}
            {estimate ? <MemoryBar estimate={estimate} /> : null}
          </div>

          <div className="toolbar">
            <button
              className="primary"
              disabled={!selectedModel}
              onClick={() => setStep("test")}
              type="button"
            >
              {m.steps.test}
            </button>
          </div>
        </div>
      ) : null}

      {step === "test" ? (
        <div className="local-tune-step">
          <div className="panel">
            <div className="panel-head">
              <h2>{m.testStep.heading}</h2>
            </div>

            <div className="form-grid">
              <label className="switch">
                <input
                  checked={!evaluateOnly}
                  name="test-mode"
                  onChange={() => {
                    setEvaluateOnly(false);
                    setEvaluateOnlyTouched(true);
                  }}
                  type="radio"
                />
                <span>{m.testStep.modeLoad}</span>
              </label>
              <label className="switch">
                <input
                  checked={evaluateOnly}
                  name="test-mode"
                  onChange={() => {
                    setEvaluateOnly(true);
                    setEvaluateOnlyTouched(true);
                  }}
                  type="radio"
                />
                <span>{m.testStep.modeEvaluate}</span>
              </label>
            </div>

            {!evaluateOnly ? (
              <>
                <div className="toolbar">
                  <button
                    className="primary"
                    disabled={loadBusy || !selectedModel}
                    onClick={() => runGuardedAction("load", handleLoad)}
                    type="button"
                  >
                    {loadBusy ? m.testStep.loading : m.testStep.load}
                  </button>
                  <button
                    className="secondary"
                    disabled={unloadBusy || !loadResult}
                    onClick={() => runGuardedAction("unload", handleUnload)}
                    type="button"
                  >
                    {unloadBusy ? m.testStep.unloading : m.testStep.unload}
                  </button>
                </div>

                {loadResult ? (
                  <ServedConfigDiff diff={loadResult.diff} unsupported={loadResult.unsupported} />
                ) : null}
              </>
            ) : (
              <div className="panel subtle">
                <h3>{m.testStep.servedConfigHeading}</h3>
                <ServedConfigSummary messages={m} served={selectedModel?.served ?? EMPTY_SERVED_CONFIG} />
              </div>
            )}

            {notLoaded ? (
              <div className="callout callout--danger">
                <p>{notLoaded.message}</p>
                <button
                  className="secondary"
                  disabled={testingServer}
                  onClick={handleTestServer}
                  type="button"
                >
                  {m.testStep.refresh}
                </button>
              </div>
            ) : null}

            <div className="toolbar">
              <button
                className="primary"
                disabled={testingBusy || !selectedModel || (!evaluateOnly && !loadResult)}
                onClick={() => runGuardedAction("tests", handleRunTests)}
                type="button"
              >
                {testingBusy ? m.testStep.running : m.testStep.runTests}
              </button>
            </div>
            {!machine ? <p className="muted small">{m.testStep.noMachineNote}</p> : null}

            {testSession ? (
              <div className="local-test-progress">
                {testSession.status === "running" ? (
                  <Spinner label={m.testStep.inProgress(testSession.current)} />
                ) : null}
                {testSession.results.map((result) => (
                  <p key={result.name}>
                    <span className={verdictBadgeClass(result.verdict)}>
                      {m.testStep.testNames[result.name]}
                    </span>{" "}
                    {result.reading}
                  </p>
                ))}
                {testSession.error ? <ErrorBanner message={testSession.error} /> : null}
              </div>
            ) : null}
          </div>

          <div className="toolbar">
            <button
              className="primary"
              disabled={!testSession || testSession.status === "running"}
              onClick={() => setStep("result")}
              type="button"
            >
              {m.steps.result}
            </button>
          </div>
        </div>
      ) : null}

      {step === "result" && testSession ? (
        <div className="local-tune-step">
          <div className="panel">
            <div className="panel-head">
              <h2>{m.resultStep.heading}</h2>
            </div>

            {loadResult ? (
              <div className="panel subtle">
                <h3>{m.resultStep.servedConfigHeading}</h3>
                <ServedConfigSummary messages={m} served={loadResult.served} />
              </div>
            ) : null}

            <div className="local-verdict-chips">
              {testSession.results.map((result) => (
                <span className={verdictBadgeClass(result.verdict)} key={result.name}>
                  {m.testStep.testNames[result.name]}: {m.verdictLabel[result.verdict]}
                </span>
              ))}
            </div>
            {testSession.results
              .filter((result) => result.name === "speed")
              .map((result) => (
                <div key={result.name}>
                  <SpeedTable messages={m} result={result} />
                  <HeadroomBlock
                    hasBandwidth={Boolean(machineSpec.bandwidth_gbs)}
                    headroom={(result.detail.headroom as LocalHeadroom | null) ?? null}
                    messages={m}
                  />
                </div>
              ))}
            {testSession.results
              .filter((result) => result.name === "agent")
              .map((result) => <AgentTable key={result.name} messages={m} result={result} />)}

            {savedProfile ? (
              <div className="panel subtle">
                <p className="callout callout--ok">{m.resultStep.savedToHistory(savedProfile.label)}</p>
                <div className="toolbar">
                  <input
                    onChange={(event) => setRenameLabel(event.target.value)}
                    placeholder={m.resultStep.renamePlaceholder}
                    value={renameLabel}
                  />
                  <button
                    className="secondary"
                    disabled={renaming || !renameLabel.trim()}
                    onClick={handleRenameProfile}
                    type="button"
                  >
                    {m.resultStep.renameSave}
                  </button>
                </div>
                <div className="toolbar">
                  <label className="field">
                    <span>{m.resultStep.observedLabel}</span>
                    <input
                      onChange={(event) => setObservedMemoryGb(event.target.value)}
                      type="number"
                      value={observedMemoryGb}
                    />
                  </label>
                  <button className="secondary" onClick={handleSaveObservedMemory} type="button">
                    {m.resultStep.saveObserved}
                  </button>
                </div>
              </div>
            ) : (
              <p className="muted small">{m.testStep.noMachineNote}</p>
            )}

            <div className="toolbar">
              <button className="secondary" onClick={handleUseInNewEvaluation} type="button">
                {m.resultStep.useInNewEvaluation}
              </button>
            </div>

            <div className="panel subtle">
              <h3>{m.resultStep.historyHeading}</h3>
              {history.length === 0 ? (
                <p className="muted small">{m.resultStep.noHistory}</p>
              ) : (
                <>
                  <div className="table-scroll">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th />
                          <th>{m.resultStep.colLabel}</th>
                          <th>{m.resultStep.colContext}</th>
                          <th>{m.resultStep.colReadSpeed}</th>
                          <th>{m.resultStep.colWriteSpeed}</th>
                          <th>{m.resultStep.colSecondsPerStep}</th>
                          <th>{m.resultStep.colAgent}</th>
                          <th>{m.resultStep.colFit}</th>
                          <th>{m.resultStep.colWindow}</th>
                          <th>{m.resultStep.colEstGb}</th>
                          <th>{m.resultStep.colDate}</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {history.map((row) => {
                          const fit = row.summary.verdicts.fit;
                          const window_ = row.summary.verdicts.window;
                          return (
                            <tr key={row.profile.id}>
                              <td>
                                <input
                                  checked={compareSelection.includes(row.profile.id)}
                                  onChange={() => toggleCompareSelection(row.profile.id)}
                                  type="checkbox"
                                />
                              </td>
                              <td>
                                {row.profile.label}{" "}
                                {row.profile.recommended ? (
                                  <span className="badge ok">{m.resultStep.recommended}</span>
                                ) : (
                                  <button
                                    className="button-link"
                                    onClick={() => handleMarkRecommended(row.profile.id)}
                                    type="button"
                                  >
                                    {m.resultStep.markRecommended}
                                  </button>
                                )}
                              </td>
                              <td>{row.summary.context_length ?? "—"}</td>
                              <td>{row.summary.read_tokens_per_second?.toFixed(1) ?? "—"}</td>
                              <td>{row.summary.write_tokens_per_second?.toFixed(1) ?? "—"}</td>
                              <td>{row.summary.seconds_per_step?.toFixed(2) ?? "—"}</td>
                              <td>
                                {row.summary.agent_passed ?? "—"}/{row.summary.agent_total ?? "—"}
                              </td>
                              <td>
                                {fit ? (
                                  <span className={verdictBadgeClass(fit)}>{m.verdictLabel[fit]}</span>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td>
                                {window_ ? (
                                  <span className={verdictBadgeClass(window_)}>
                                    {m.verdictLabel[window_]}
                                  </span>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td>{row.summary.estimated_gb?.toFixed(1) ?? "—"}</td>
                              <td>{formatRunDate(row.profile.created_at)}</td>
                              <td>
                                <button
                                  className="button-link"
                                  onClick={() => handleDeleteHistoryRow(row.profile.id)}
                                  type="button"
                                >
                                  {m.resultStep.delete}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <div className="toolbar">
                    <button className="secondary" onClick={handleCompare} type="button">
                      {m.resultStep.compare}
                    </button>
                  </div>
                </>
              )}

              {compareResult ? (
                compareResult.comparable && compareResult.comparison ? (
                  <CompareResultPanel comparison={compareResult.comparison} messages={m} />
                ) : (
                  <p className="muted small">
                    {`${m.resultStep.notComparable}: ${compareResult.reason}`}
                  </p>
                )
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function ServedConfigSummary({
  served,
  messages
}: {
  served: LocalServedConfig;
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
}) {
  const yesNo = (value: boolean | null) =>
    value === null ? "—" : value ? messages.server.yes : messages.server.no;
  return (
    <div className="form-grid">
      <div>
        <span className="muted small">{messages.resultStep.context}</span>{" "}
        {served.context_length ?? "—"}
      </div>
      <div>
        <span className="muted small">{messages.resultStep.flashAttention}</span>{" "}
        {yesNo(served.flash_attention)}
      </div>
      <div>
        <span className="muted small">{messages.resultStep.kvOnGpu}</span>{" "}
        {yesNo(served.offload_kv_cache_to_gpu)}
      </div>
      <div>
        <span className="muted small">{messages.resultStep.evalBatch}</span>{" "}
        {served.eval_batch_size ?? "—"}
      </div>
      <div>
        <span className="muted small">{messages.resultStep.parallel}</span>{" "}
        {served.parallel ?? "—"}
      </div>
    </div>
  );
}

function winnerLabel(
  winner: "a" | "b" | "tie" | "",
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>
): string {
  if (winner === "a") return messages.resultStep.winnerA;
  if (winner === "b") return messages.resultStep.winnerB;
  if (winner === "tie") return messages.resultStep.winnerTie;
  return "—";
}

function CompareResultPanel({
  comparison,
  messages
}: {
  comparison: LocalComparison;
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
}) {
  return (
    <div className="panel subtle">
      <h3>{messages.resultStep.compareHeading}</h3>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>{messages.resultStep.colMetric}</th>
              <th>A</th>
              <th>B</th>
              <th>{messages.resultStep.colDelta}</th>
              <th>{messages.resultStep.colWinner}</th>
            </tr>
          </thead>
          <tbody>
            {comparison.rows.map((row) => (
              <tr key={row.key}>
                <td>{row.label}</td>
                <td>{row.a ?? "—"}</td>
                <td>{row.b ?? "—"}</td>
                <td>{row.delta_pct !== null ? `${row.delta_pct}%` : "—"}</td>
                <td>
                  <span className={row.winner ? `badge ${row.winner === "tie" ? "neutral" : "ok"}` : "badge neutral"}>
                    {winnerLabel(row.winner, messages)}
                  </span>
                </td>
              </tr>
            ))}
            {comparison.verdicts.map((row) => (
              <tr key={row.name}>
                <td>{messages.testStep.testNames[row.name]}</td>
                <td>{row.a ? messages.verdictLabel[row.a] : "—"}</td>
                <td>{row.b ? messages.verdictLabel[row.b] : "—"}</td>
                <td>—</td>
                <td>
                  <span className={row.winner ? `badge ${row.winner === "tie" ? "neutral" : "ok"}` : "badge neutral"}>
                    {winnerLabel(row.winner, messages)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="callout callout--ok">{comparison.summary}</p>
      <p className="muted small">{messages.resultStep.noiseNote(String(comparison.noise_pct))}</p>
    </div>
  );
}

function SpeedTable({
  result,
  messages
}: {
  result: LocalTestResult;
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
}) {
  const points = Array.isArray(result.detail.points) ? (result.detail.points as Record<string, number | null>[]) : [];
  if (points.length === 0) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>{messages.resultStep.promptTokens}</th>
            <th>{messages.resultStep.readSpeed}</th>
            <th>{messages.resultStep.writeSpeed}</th>
            <th>{messages.resultStep.ttft}</th>
          </tr>
        </thead>
        <tbody>
          {points.map((point, index) => (
            <tr key={index}>
              <td>{point.prompt_tokens ?? "—"}</td>
              <td>{point.prompt_tokens_per_second?.toFixed(1) ?? "—"}</td>
              <td>{point.tokens_per_second?.toFixed(1) ?? "—"}</td>
              <td>{point.time_to_first_token_s?.toFixed(2) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeadroomBlock({
  headroom,
  hasBandwidth,
  messages
}: {
  headroom: LocalHeadroom | null;
  hasBandwidth: boolean;
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
}) {
  if (!headroom) {
    if (hasBandwidth) {
      return null;
    }
    return <p className="muted small">{messages.headroom.noBandwidthHint}</p>;
  }
  const measuredPct = Math.min(100, headroom.fraction * 100);
  const [typicalLow, typicalHigh] = headroom.typical_tps;
  const typicalLowPct = Math.min(100, (typicalLow / headroom.ceiling_tps) * 100);
  const typicalHighPct = Math.min(100, (typicalHigh / headroom.ceiling_tps) * 100);
  return (
    <div className="panel subtle">
      <h3>
        {messages.headroom.heading}{" "}
        <span className={headroomBadgeClass(headroom.level)}>
          {messages.headroom.level[headroom.level]}
        </span>
      </h3>
      <div className="headroom-track" role="img">
        <span
          className="headroom-typical-band"
          style={{
            left: `${typicalLowPct}%`,
            width: `${Math.max(0, typicalHighPct - typicalLowPct)}%`
          }}
          title={`${messages.headroom.typical}: ${typicalLow}-${typicalHigh}`}
        />
        <span
          className="headroom-measured"
          style={{ width: `${measuredPct}%` }}
          title={`${messages.headroom.measured}: ${headroom.fraction.toFixed(2)}`}
        />
      </div>
      <p className="muted small">
        {messages.headroom.ceiling}: {headroom.ceiling_tps} tok/s —{" "}
        {messages.headroom.typical}: {typicalLow}-{typicalHigh} tok/s
      </p>
      <p>{headroom.reading}</p>
    </div>
  );
}

function AgentTable({
  result,
  messages
}: {
  result: LocalTestResult;
  messages: ReturnType<typeof useMessages<(typeof MESSAGES)["en"]>>;
}) {
  const cases = Array.isArray(result.detail.cases) ? (result.detail.cases as Record<string, unknown>[]) : [];
  if (cases.length === 0) {
    return null;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>{messages.agentTable.prompt}</th>
            <th>{messages.agentTable.expected}</th>
            <th>{messages.agentTable.got}</th>
            <th>{messages.agentTable.result}</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((item, index) => {
            const failure = item.failure ? String(item.failure) : "";
            return (
              <tr key={index}>
                <td>{String(item.prompt ?? "")}</td>
                <td>{String(item.expected ?? "")}</td>
                <td>{String(item.got ?? "")}</td>
                <td>
                  <span className={failure ? "badge error" : "badge ok"}>
                    {failure || "✓"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
