import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api";
import { SWE_CASE_TYPES, caseTestCounts, shortInstanceLabel } from "../caseLabels";
import { EmptyState } from "../components/EmptyState";
import { HelpPanel } from "../components/HelpPanel";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconSparkles } from "../components/icons";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import type {
  CaseCreatePayload,
  CaseRecord,
  DroppedCommand,
  ModelOption,
  SwebenchProInstance
} from "../types";

type CasesProps = {
  cases: CaseRecord[];
  onCreateCase: (payload: CaseCreatePayload) => Promise<CaseRecord>;
  onImportSwebench: (instanceId: string) => Promise<CaseRecord>;
  onImportSwebenchPro: (instanceId: string) => Promise<CaseRecord>;
  onSeedTetris: () => void;
  onSelectCase: (caseId: string) => void;
  selectedCaseId: string | null;
};

const CASE_TEMPLATE_VALUES = [
  "single_prompt",
  "multi_step",
  "build_from_scratch",
  "bugfix",
  "repo_task"
] as const;

type CaseFilter = "all" | "swe-bench" | "other";

const MESSAGES = defineMessages({
  en: {
    templateLabels: {
      single_prompt: "Single prompt",
      multi_step: "Multi-step",
      build_from_scratch: "Build from scratch",
      bugfix: "Bugfix",
      repo_task: "Repo task"
    } as Record<(typeof CASE_TEMPLATE_VALUES)[number], string>,
    swePro: "SWE-bench Pro",
    sweBench: "SWE-bench",
    testCounts: (f2p: number, p2p: number) => ` · F2P ${f2p} / P2P ${p2p}`,
    eyebrow: "Cases",
    heading: "Benchmark cases",
    lede: "Create, inspect, and sync case definitions from one place.",
    seedBenchmarkCases: "Seed benchmark cases",
    seedBenchmarkCasesTitle: "Import or refresh every benchmarks/*/task.yaml case",
    importSwebenchTitle: "Import SWE-bench instance",
    importSwebenchSubtitle: "SWE-bench Lite / Verified (HuggingFace)",
    importSwebenchIntro: (cliCmd: ReactNode, selftestCmd: ReactNode) => (
      <>
        Fetches one instance from SWE-bench Lite / Verified and stores it as a case (DB only, no
        YAML on disk). Equivalent CLI: {cliCmd}. Prove the harness first with {selftestCmd}.
      </>
    ),
    instanceId: "Instance ID",
    importing: "Importing...",
    importInstance: "Import instance",
    imported: (caseId: string) => `Imported ${caseId} — ready to run.`,
    importProTitle: "Import SWE-bench Pro instance",
    importProSubtitle: "Hard cases · Python subset (ScaleAI)",
    importProIntro: (cliCmd: ReactNode, selftestCmd: ReactNode) => (
      <>
        Long-horizon, multi-file tasks evaluated inside prebuilt per-instance Docker
        images. Browse the catalog and import — no need to know the instance id.
        Equivalent CLI: {cliCmd}; prove the lane first with {selftestCmd}.
      </>
    ),
    loadingCatalog: "Loading catalog...",
    browseInstances: "Browse instances",
    all: (count: number) => `All (${count})`,
    importedBadge: "Imported",
    import: "Import",
    instance: "Instance",
    tests: "Tests",
    f2pP2p: (f2p: number, p2p: number) => `F2P ${f2p} / P2P ${p2p}`,
    importedPro: (caseId: string) => `Imported ${caseId} — ready to run.`,
    newCase: "New case",
    defaultCaseName: "New case",
    defaultPromptBody: "Describe the task here.",
    defaultMultiStepPrompt:
      "Step 1: Explain the task.\nStep 2: Implement the fix.\nStep 3: Verify the result.",
    draftWithAi: "Draft it with AI (optional)",
    aiSubtitle: "Shapes the whole case — type, id, instruction, steps and validation",
    describeCase: "Describe the case you want, in plain language",
    describeCasePlaceholder:
      "e.g. A CLI that reads a CSV of transactions and writes a monthly summary as JSON, with tests for empty files and malformed rows.",
    model: "Model",
    transportDefault: "Transport default (recommended)",
    drafting: "Drafting…",
    generateCase: "Generate the case",
    noRunnableCommand: (retried: boolean) =>
      `The draft has no runnable validation command${
        retried ? " after one corrective retry" : ""
      }. Without one the case cannot score itself and every run waits for a ` +
      "manual score — write one below, or describe the check you want and " +
      "generate again.",
    draftedCase: (draftType: string, stepCount: number, kept: number, retried: boolean) =>
      `Drafted a ${draftType.replace(/_/g, " ")} case${
        stepCount > 0 ? `, a ${1 + stepCount}-step plan` : ""
      } with ${kept} validation command(s)${
        retried ? " after one corrective retry" : ""
      }. Review every field, then Create case.`,
    unableToGenerate: "Unable to generate the case draft.",
    leftOut: "Left out of the draft — the lab would not run these:",
    template: "Template",
    caseId: "Case ID",
    name: "Name",
    maxSteps: "Max steps",
    description: "Description",
    promptBody: "Prompt body",
    stepPromptsLabel: "Step prompts — one per line, driving step 2 onward",
    validationCommandsLabel: "Validation commands — one per line",
    scoringRubricLabel: "Scoring rubric — required once the case has a command",
    scoringRubricPlaceholder:
      "10 = every validation command passes and the maze, scoring and ghost chase all work. 5 = it renders and moves but scoring or collisions are missing. 0 = nothing runs.",
    noValidationHint: (code: ReactNode) => (
      <>
        A case with no validation command cannot score itself: every run of it
        stops in {code} until a person grades it.
      </>
    ),
    notCreated: "The case was not created:",
    unableToCreate: "Unable to create the case.",
    saving: "Saving...",
    createCase: "Create case",
    caseList: "Case list",
    sweBenchFilter: (count: number) => `SWE-bench (${count})`,
    otherFilter: (count: number) => `Other (${count})`,
    noSweBenchCases:
      "No SWE-bench cases yet. Import one above (they live in the DB only, so a DB reset removes them).",
    noCasesSeedOrCreate: "Seed the benchmark cases or create a new one to begin.",
    helpTitle: "Four ways to make a case",
    helpBody: (newCase: ReactNode) => (
      <>
        <p>
          A case is a task plus the checks that say whether the agent did it. Draft one
          from an idea with {newCase}, import a SWE-bench issue below, draft one from a
          repository on <em>Evaluate → Audit a repository</em>, or write a{" "}
          <code>task.yaml</code> under <code>benchmarks/</code>.
        </p>
        <p>
          A case is only as good as its check: a command that cannot fail, or fails for
          reasons that are not the agent's, makes every result built on it noise. Run a
          new case once with a strong model before trusting it.
        </p>
      </>
    ),
    noCasesTitle: "No cases here.",
    caseHeader: "Case",
    type: "Type",
    sync: "Sync"
  },
  es: {
    templateLabels: {
      single_prompt: "Prompt único",
      multi_step: "Multi-paso",
      build_from_scratch: "Construir desde cero",
      bugfix: "Corrección de bug",
      repo_task: "Tarea de repositorio"
    } as Record<(typeof CASE_TEMPLATE_VALUES)[number], string>,
    swePro: "SWE-bench Pro",
    sweBench: "SWE-bench",
    testCounts: (f2p: number, p2p: number) => ` · F2P ${f2p} / P2P ${p2p}`,
    eyebrow: "Casos",
    heading: "Casos de benchmark",
    lede: "Creá, inspeccioná y sincronizá definiciones de casos desde un solo lugar.",
    seedBenchmarkCases: "Sembrar casos de benchmark",
    seedBenchmarkCasesTitle: "Importar o actualizar cada caso de benchmarks/*/task.yaml",
    importSwebenchTitle: "Importar instancia de SWE-bench",
    importSwebenchSubtitle: "SWE-bench Lite / Verified (HuggingFace)",
    importSwebenchIntro: (cliCmd: ReactNode, selftestCmd: ReactNode) => (
      <>
        Trae una instancia de SWE-bench Lite / Verified y la guarda como caso (solo
        en la base de datos, sin YAML en disco). CLI equivalente: {cliCmd}. Probá el
        arnés primero con {selftestCmd}.
      </>
    ),
    instanceId: "ID de instancia",
    importing: "Importando...",
    importInstance: "Importar instancia",
    imported: (caseId: string) => `Se importó ${caseId} — listo para correr.`,
    importProTitle: "Importar instancia de SWE-bench Pro",
    importProSubtitle: "Casos difíciles · subconjunto Python (ScaleAI)",
    importProIntro: (cliCmd: ReactNode, selftestCmd: ReactNode) => (
      <>
        Tareas largas y multi-archivo evaluadas dentro de imágenes Docker
        pre-armadas por instancia. Explorá el catálogo e importá — no hace falta
        saber el id de la instancia. CLI equivalente: {cliCmd}; probá el carril
        primero con {selftestCmd}.
      </>
    ),
    loadingCatalog: "Cargando catálogo...",
    browseInstances: "Explorar instancias",
    all: (count: number) => `Todas (${count})`,
    importedBadge: "Importado",
    import: "Importar",
    instance: "Instancia",
    tests: "Tests",
    f2pP2p: (f2p: number, p2p: number) => `F2P ${f2p} / P2P ${p2p}`,
    importedPro: (caseId: string) => `Se importó ${caseId} — listo para correr.`,
    newCase: "Nuevo caso",
    defaultCaseName: "Nuevo caso",
    defaultPromptBody: "Describí la tarea acá.",
    defaultMultiStepPrompt:
      "Paso 1: Explicá la tarea.\nPaso 2: Implementá la corrección.\nPaso 3: Verificá el resultado.",
    draftWithAi: "Redactalo con IA (opcional)",
    aiSubtitle: "Define todo el caso — tipo, id, instrucción, pasos y validación",
    describeCase: "Describí el caso que querés, en lenguaje simple",
    describeCasePlaceholder:
      "ej. Un CLI que lee un CSV de transacciones y escribe un resumen mensual en JSON, con tests para archivos vacíos y filas mal formadas.",
    model: "Modelo",
    transportDefault: "Transporte por defecto (recomendado)",
    drafting: "Redactando…",
    generateCase: "Generar el caso",
    noRunnableCommand: (retried: boolean) =>
      `El borrador no tiene ningún comando de validación ejecutable${
        retried ? " después de un reintento correctivo" : ""
      }. Sin uno el caso no puede puntuarse solo y cada corrida queda esperando ` +
      "un puntaje manual — escribí uno abajo, o describí el chequeo que querés " +
      "y generá de nuevo.",
    draftedCase: (draftType: string, stepCount: number, kept: number, retried: boolean) =>
      `Se redactó un caso ${draftType.replace(/_/g, " ")}${
        stepCount > 0 ? `, un plan de ${1 + stepCount} pasos` : ""
      } con ${kept} comando(s) de validación${
        retried ? " después de un reintento correctivo" : ""
      }. Revisá cada campo y luego Crear caso.`,
    unableToGenerate: "No se pudo generar el borrador del caso.",
    leftOut: "Dejado fuera del borrador — el lab no correría esto:",
    template: "Plantilla",
    caseId: "ID del caso",
    name: "Nombre",
    maxSteps: "Pasos máximos",
    description: "Descripción",
    promptBody: "Cuerpo del prompt",
    stepPromptsLabel: "Prompts de paso — uno por línea, maneja el paso 2 en adelante",
    validationCommandsLabel: "Comandos de validación — uno por línea",
    scoringRubricLabel: "Rúbrica de puntuación — obligatoria si el caso tiene un comando",
    scoringRubricPlaceholder:
      "10 = pasan todos los comandos de validación y el laberinto, el puntaje y la persecución de fantasmas funcionan. 5 = renderiza y se mueve pero falta puntaje o colisiones. 0 = no corre nada.",
    noValidationHint: (code: ReactNode) => (
      <>
        Un caso sin comando de validación no puede puntuarse solo: cada corrida
        queda en {code} hasta que una persona lo califique.
      </>
    ),
    notCreated: "No se creó el caso:",
    unableToCreate: "No se pudo crear el caso.",
    saving: "Guardando...",
    createCase: "Crear caso",
    caseList: "Lista de casos",
    sweBenchFilter: (count: number) => `SWE-bench (${count})`,
    otherFilter: (count: number) => `Otros (${count})`,
    noSweBenchCases:
      "Todavía no hay casos de SWE-bench. Importá uno arriba (viven solo en la base de datos, así que un reset de la DB los elimina).",
    noCasesSeedOrCreate: "Sembrá los casos de benchmark o creá uno nuevo para empezar.",
    helpTitle: "Cuatro formas de crear un caso",
    helpBody: (newCase: ReactNode) => (
      <>
        <p>
          Un caso es una tarea más los chequeos que dicen si el agente la hizo. Generá
          uno desde una idea con {newCase}, importá un issue de SWE-bench abajo, generá
          uno desde un repositorio en <em>Evaluar → Auditar un repositorio</em>, o
          escribí un <code>task.yaml</code> en <code>benchmarks/</code>.
        </p>
        <p>
          Un caso vale lo que vale su chequeo: un comando que no puede fallar, o que
          falla por motivos ajenos al agente, convierte en ruido todo resultado que se
          apoye en él. Corré un caso nuevo una vez con un modelo fuerte antes de
          confiar en él.
        </p>
      </>
    ),
    noCasesTitle: "No hay casos acá.",
    caseHeader: "Caso",
    type: "Tipo",
    sync: "Sincronización"
  }
});

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function caseTypeBadge(item: CaseRecord) {
  const m = pickMessages(MESSAGES);
  if (SWE_CASE_TYPES.has(item.type)) {
    const tests = caseTestCounts(item);
    const counts = tests ? m.testCounts(tests.f2p, tests.p2p) : "";
    const label = item.type === "swe_pro_instance" ? m.swePro : m.sweBench;
    return <span className="badge ok">{label}{counts}</span>;
  }
  return <span>{item.type}</span>;
}

export function Cases({
  cases,
  onCreateCase,
  onImportSwebench,
  onImportSwebenchPro,
  onSeedTetris,
  onSelectCase,
  selectedCaseId
}: CasesProps) {
  const m = useMessages(MESSAGES);
  const [template, setTemplate] = useState("single_prompt");
  const [name, setName] = useState(m.defaultCaseName);
  const [caseId, setCaseId] = useState("new-case");
  const [description, setDescription] = useState("");
  const [promptBody, setPromptBody] = useState(m.defaultPromptBody);
  const [maxSteps, setMaxSteps] = useState(1);
  // Required by the server whenever the case carries a validation command: a
  // case with commands and no rubric has nothing to grade against and parks in
  // awaiting_manual_score, so `check_case` refuses it with a 422.
  const [scoringRubric, setScoringRubric] = useState("");
  // One command per line, the shape the case stores and the runner executes.
  const [commandText, setCommandText] = useState("");
  const [stepPrompts, setStepPrompts] = useState<string[]>([]);
  const [aiDescription, setAiDescription] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiNotice, setAiNotice] = useState("");
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDropped, setAiDropped] = useState<DroppedCommand[]>([]);
  const [isDrafting, setIsDrafting] = useState(false);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [swebenchId, setSwebenchId] = useState("pallets__flask-4992");
  const [isImporting, setIsImporting] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [caseFilter, setCaseFilter] = useState<CaseFilter>("all");
  const [proCatalog, setProCatalog] = useState<SwebenchProInstance[] | null>(null);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(false);
  const [proRepoFilter, setProRepoFilter] = useState<string>("all");
  const [proImportingId, setProImportingId] = useState<string | null>(null);
  const [proMessage, setProMessage] = useState<string | null>(null);
  const [proError, setProError] = useState<string | null>(null);
  const [proExpandedId, setProExpandedId] = useState<string | null>(null);
  // The assistant's model menu. A catalog the lab cannot reach is not an
  // error here: the request falls back to the transport's own default model.
  useEffect(() => {
    let active = true;
    api
      .listModels(false)
      .then((catalog) => {
        if (!active) return;
        setModelOptions(catalog.models);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const templateLabel = useMemo(
    () =>
      (m.templateLabels as Record<string, string>)[template] ?? template,
    [template, m]
  );

  const handleTemplateChange = (nextTemplate: string) => {
    setTemplate(nextTemplate);
    if (!caseId || caseId === slugify(name)) {
      setCaseId(`${nextTemplate}-case`);
    }
    if (nextTemplate === "multi_step") {
      setPromptBody(m.defaultMultiStepPrompt);
      setMaxSteps(3);
    } else {
      setPromptBody(m.defaultPromptBody);
      setMaxSteps(1);
    }
  };

  const handleNameChange = (value: string) => {
    setName(value);
    if (caseId === "new-case" || caseId === slugify(name)) {
      setCaseId(slugify(value) || "new-case");
    }
  };

  const handleImportSwebench = async () => {
    setIsImporting(true);
    setImportMessage(null);
    setImportError(null);
    try {
      const imported = await onImportSwebench(swebenchId.trim());
      setImportMessage(m.imported(imported.case_id));
    } catch (error) {
      setImportError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsImporting(false);
    }
  };

  const visibleCases = useMemo(() => {
    if (caseFilter === "swe-bench") {
      return cases.filter((item) => SWE_CASE_TYPES.has(item.type));
    }
    if (caseFilter === "other") {
      return cases.filter((item) => !SWE_CASE_TYPES.has(item.type));
    }
    return cases;
  }, [cases, caseFilter]);

  const handleLoadProCatalog = async () => {
    setIsLoadingCatalog(true);
    setProError(null);
    try {
      setProCatalog(await api.listSwebenchProInstances());
    } catch (error) {
      setProError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoadingCatalog(false);
    }
  };

  const handleImportPro = async (instanceId: string) => {
    setProImportingId(instanceId);
    setProMessage(null);
    setProError(null);
    try {
      const imported = await onImportSwebenchPro(instanceId);
      setProMessage(m.importedPro(shortInstanceLabel(imported.case_id)));
      setProCatalog((current) =>
        current
          ? current.map((item) =>
              item.instance_id === instanceId ? { ...item, imported: true } : item
            )
          : current
      );
    } catch (error) {
      setProError(error instanceof Error ? error.message : String(error));
    } finally {
      setProImportingId(null);
    }
  };

  const proRepos = useMemo(() => {
    const repos = new Set((proCatalog ?? []).map((item) => item.repo));
    return ["all", ...Array.from(repos).sort()];
  }, [proCatalog]);

  const visibleProInstances = useMemo(() => {
    const items = proCatalog ?? [];
    return proRepoFilter === "all"
      ? items
      : items.filter((item) => item.repo === proRepoFilter);
  }, [proCatalog, proRepoFilter]);

  /**
   * Fill the form from one sentence.
   *
   * The assistant returns the whole case — type, id, name, description,
   * instruction, per-step prompts and validation commands — because this form
   * saves all of it in one press. Nothing is created here: every field lands
   * editable, so the draft is a starting point and not a decision.
   */
  const handleGenerate = async () => {
    setIsDrafting(true);
    setAiError(null);
    setAiNotice("");
    setAiDropped([]);
    try {
      const draft = await api.generateCaseDraft({
        description: aiDescription,
        model: aiModel || undefined,
        selected_skills: [],
        existing_case_ids: cases.map((item) => item.case_id)
      });
      const draftType = draft.case_type || "single_prompt";
      setTemplate(draftType);
      if (draft.case_id) setCaseId(draft.case_id);
      if (draft.subject) setName(draft.subject);
      if (draft.description) setDescription(draft.description);
      if (draft.instruction) setPromptBody(draft.instruction);
      if (draft.scoring_rubric) setScoringRubric(draft.scoring_rubric);
      setStepPrompts(draft.step_prompts ?? []);
      // The server screened these: what is left is one runnable command per
      // line, which is the only shape this textarea preserves.
      setCommandText((draft.validation_commands ?? []).join("\n"));
      if (draft.max_steps) setMaxSteps(draft.max_steps);
      setAiDropped(draft.dropped_commands ?? []);

      const kept = draft.validation_commands?.length ?? 0;
      const retried = draft.attempts > 1;
      const stepCount =
        draftType === "multi_step" ? (draft.step_prompts?.length ?? 0) : 0;
      if (kept === 0) {
        setAiError(m.noRunnableCommand(retried));
      } else {
        setAiNotice(m.draftedCase(draftType, stepCount, kept, retried));
      }
    } catch (error) {
      setAiError(error instanceof Error ? error.message : m.unableToGenerate);
    } finally {
      setIsDrafting(false);
    }
  };

  const handleCreate = async () => {
    setIsSaving(true);
    setSaveError(null);
    try {
      const created = await onCreateCase({
        case_id: caseId || slugify(name) || "new-case",
        name: name || m.defaultCaseName,
        type: template,
        description,
        tags: ["ui"],
        prompt_variants: { default: promptBody },
        // A drafted plan drives step 2 onward; typing the steps into the
        // prompt body keeps the old behaviour for a hand-written case.
        step_prompts:
          template === "multi_step"
            ? stepPrompts.length > 0
              ? stepPrompts
              : promptBody
                  .split("\n")
                  .map((item) => item.trim())
                  .filter(Boolean)
            : [],
        success_criteria: {},
        scoring_rubric: scoringRubric,
        constraints: [],
        validation_commands: commandText
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        max_steps: maxSteps
      });
      onSelectCase(created.case_id);
      setCaseId(`${slugify(created.name)}-copy`);
    } catch (error) {
      // Without this the server's refusal became an unhandled rejection and the
      // button read as dead: pressed, nothing created, nothing said.
      setSaveError(error instanceof Error ? error.message : m.unableToCreate);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.heading}</h1>
          <p className="lede">{m.lede}</p>
        </div>
        <div className="toolbar">
          <button
            className="primary"
            onClick={onSeedTetris}
            title={m.seedBenchmarkCasesTitle}
            type="button"
          >
            {m.seedBenchmarkCases}
          </button>
        </div>
      </header>

      <HelpPanel doc="create-case" title={m.helpTitle}>
        {m.helpBody(<strong>{m.newCase}</strong>)}
      </HelpPanel>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.importSwebenchTitle}</h2>
          <span className="muted">{m.importSwebenchSubtitle}</span>
        </div>
        <p className="muted">
          {m.importSwebenchIntro(
            <code>make swebench-import INSTANCE=&lt;id&gt;</code>,
            <code>make swebench-selftest INSTANCE=&lt;id&gt;</code>
          )}
        </p>
        <div className="form-grid">
          <label>
            <span>{m.instanceId}</span>
            <input
              placeholder="pallets__flask-4992"
              value={swebenchId}
              onChange={(event) => setSwebenchId(event.target.value)}
            />
          </label>
        </div>
        <div className="panel-actions">
          <button
            className="primary"
            disabled={isImporting || swebenchId.trim().length === 0}
            onClick={handleImportSwebench}
            type="button"
          >
            {isImporting ? m.importing : m.importInstance}
          </button>
          {importMessage ? <span className="badge ok">{importMessage}</span> : null}
          {importError ? <span className="badge error">{importError}</span> : null}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.importProTitle}</h2>
          <span className="muted">{m.importProSubtitle}</span>
        </div>
        <p className="muted">
          {m.importProIntro(
            <code>make swebench-pro-import INSTANCE=&lt;id&gt;</code>,
            <code>make swebench-pro-selftest INSTANCE=&lt;id&gt;</code>
          )}
        </p>
        {proCatalog === null ? (
          <div className="panel-actions">
            <button
              className="primary"
              disabled={isLoadingCatalog}
              onClick={handleLoadProCatalog}
              type="button"
            >
              {isLoadingCatalog ? m.loadingCatalog : m.browseInstances}
            </button>
            {proError ? <span className="badge error">{proError}</span> : null}
          </div>
        ) : (
          <>
            <div className="toolbar" style={{ marginBottom: "0.75rem" }}>
              {proRepos.map((repo) => (
                <button
                  className={proRepoFilter === repo ? "badge neutral" : "badge"}
                  key={repo}
                  onClick={() => setProRepoFilter(repo)}
                  type="button"
                >
                  {repo === "all"
                    ? m.all((proCatalog ?? []).length)
                    : `${repo} (${(proCatalog ?? []).filter((item) => item.repo === repo).length})`}
                </button>
              ))}
            </div>
            <div className="panel-actions" style={{ marginBottom: "0.5rem" }}>
              {proMessage ? <span className="badge ok">{proMessage}</span> : null}
              {proError ? <span className="badge error">{proError}</span> : null}
            </div>
            <div className="table" style={{ maxHeight: "28rem", overflowY: "auto" }}>
              <div className="table-row table-head">
                <span>{m.instance}</span>
                <span>{m.tests}</span>
                <span></span>
              </div>
              {visibleProInstances.map((item) => {
                const expanded = proExpandedId === item.instance_id;
                return (
                  <div className="table-row" key={item.instance_id}>
                    <span
                      onClick={() =>
                        setProExpandedId(expanded ? null : item.instance_id)
                      }
                      style={{ cursor: "pointer" }}
                      title={item.instance_id}
                    >
                      <strong style={{ display: "block" }}>
                        {item.title || shortInstanceLabel(item.instance_id)}
                      </strong>
                      <small className="muted" style={{ display: "block" }}>
                        {shortInstanceLabel(item.instance_id)}
                        {item.summary && !expanded
                          ? ` — ${item.summary.slice(0, 110)}…`
                          : ""}
                      </small>
                      {expanded ? (
                        <span style={{ display: "block", marginTop: "0.35rem" }}>
                          {item.summary ? (
                            <small style={{ display: "block", whiteSpace: "normal" }}>
                              {item.summary}
                            </small>
                          ) : null}
                          {item.issue_categories.length > 0 ? (
                            <span
                              style={{
                                display: "flex",
                                flexWrap: "wrap",
                                gap: "0.3rem",
                                marginTop: "0.35rem"
                              }}
                            >
                              {item.issue_categories.map((category) => (
                                <span className="badge neutral" key={category}>
                                  {category.replaceAll("_", " ")}
                                </span>
                              ))}
                            </span>
                          ) : null}
                        </span>
                      ) : null}
                    </span>
                    <span className="muted">{m.f2pP2p(item.f2p_count, item.p2p_count)}</span>
                    <span>
                      {item.imported ? (
                        <span className="badge ok">{m.importedBadge}</span>
                      ) : (
                        <button
                          className="badge"
                          disabled={proImportingId !== null}
                          onClick={() => handleImportPro(item.instance_id)}
                          type="button"
                        >
                          {proImportingId === item.instance_id ? m.importing : m.import}
                        </button>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.newCase}</h2>
          <span className="muted">{templateLabel}</span>
        </div>

        {/* AI case assistant: optional, fills every field below. */}
        <div className="assist-card">
          <div className="assist-card-head">
            <strong className="assist-card-title">
              <IconSparkles size={16} /> {m.draftWithAi}
            </strong>
            <span className="muted small">{m.aiSubtitle}</span>
          </div>

          <label>
            <span>{m.describeCase}</span>
            <textarea
              onChange={(event) => setAiDescription(event.target.value)}
              placeholder={m.describeCasePlaceholder}
              rows={2}
              value={aiDescription}
            />
          </label>

          <div className="assist-card-row">
            <label>
              {/* Empty means "let the transport pick": the gateway's summary-tier
                  model, or Claude Haiku on a subscription lab. The catalog's
                  default is an *executor* model, which drafts prose worse than
                  it writes code, so it is offered rather than preselected. */}
              <span>{m.model}</span>
              <select
                onChange={(event) => setAiModel(event.target.value)}
                value={aiModel}
              >
                <option value="">{m.transportDefault}</option>
                {modelOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label || option.id}
                  </option>
                ))}
              </select>
            </label>

            <button
              className="primary"
              disabled={isDrafting || !aiDescription.trim()}
              onClick={() => void handleGenerate()}
              type="button"
            >
              {isDrafting ? (
                <Spinner label={m.drafting} />
              ) : (
                <>
                  <IconSparkles size={14} /> {m.generateCase}
                </>
              )}
            </button>
          </div>

          {aiNotice ? <p className="callout callout--ok">{aiNotice}</p> : null}
          {aiError ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <span>{aiError}</span>
            </div>
          ) : null}
          {aiDropped.length > 0 ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <div>
                <strong>{m.leftOut}</strong>
                <ul className="blocking-list dropped-list">
                  {aiDropped.map((item) => (
                    <li key={item.command}>
                      <code>{item.command.split("\n")[0]}</code>
                      {item.command.includes("\n") ? " …" : ""}
                      <span className="muted small"> — {item.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}
        </div>

        <div className="form-grid">
          <label>
            <span>{m.template}</span>
            <select value={template} onChange={(event) => handleTemplateChange(event.target.value)}>
              {CASE_TEMPLATE_VALUES.map((value) => (
                <option key={value} value={value}>
                  {m.templateLabels[value]}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{m.caseId}</span>
            <input value={caseId} onChange={(event) => setCaseId(event.target.value)} />
          </label>
          <label>
            <span>{m.name}</span>
            <input value={name} onChange={(event) => handleNameChange(event.target.value)} />
          </label>
          <label>
            <span>{m.maxSteps}</span>
            <input
              min={1}
              type="number"
              value={maxSteps}
              onChange={(event) => setMaxSteps(Number(event.target.value))}
            />
          </label>
          <label className="full">
            <span>{m.description}</span>
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} />
          </label>
          <label className="full">
            <span>{m.promptBody}</span>
            <textarea value={promptBody} onChange={(event) => setPromptBody(event.target.value)} />
          </label>
          {template === "multi_step" && stepPrompts.length > 0 ? (
            <label className="full">
              <span>{m.stepPromptsLabel}</span>
              <textarea
                onChange={(event) =>
                  setStepPrompts(
                    event.target.value.split("\n").map((item) => item.trim())
                  )
                }
                rows={Math.min(stepPrompts.length + 1, 8)}
                value={stepPrompts.join("\n")}
              />
            </label>
          ) : null}
          <label className="full">
            <span>{m.validationCommandsLabel}</span>
            <textarea
              onChange={(event) => setCommandText(event.target.value)}
              placeholder="pytest {workspace}/test_summary.py -q"
              value={commandText}
            />
          </label>
          <label className="full">
            <span>{m.scoringRubricLabel}</span>
            <textarea
              onChange={(event) => setScoringRubric(event.target.value)}
              placeholder={m.scoringRubricPlaceholder}
              rows={4}
              value={scoringRubric}
            />
          </label>
        </div>
        <p className="muted small">{m.noValidationHint(<code>awaiting_manual_score</code>)}</p>
        {saveError ? (
          <div className="callout callout--warning">
            <IconAlert size={16} />
            <div>
              <strong>{m.notCreated}</strong>
              <ul className="blocking-list">
                {saveError.split("\n").map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
        <div className="panel-actions">
          <button
            className="primary"
            disabled={isSaving}
            onClick={() => void handleCreate()}
            type="button"
          >
            {isSaving ? m.saving : m.createCase}
          </button>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>{m.caseList}</h2>
          <div className="toolbar">
            {(
              [
                { value: "all", label: m.all(cases.length) },
                {
                  value: "swe-bench",
                  label: m.sweBenchFilter(
                    cases.filter((item) => SWE_CASE_TYPES.has(item.type)).length
                  )
                },
                {
                  value: "other",
                  label: m.otherFilter(
                    cases.filter((item) => !SWE_CASE_TYPES.has(item.type)).length
                  )
                }
              ] as { value: CaseFilter; label: string }[]
            ).map((option) => (
              <button
                className={caseFilter === option.value ? "badge neutral" : "badge"}
                key={option.value}
                onClick={() => setCaseFilter(option.value)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        {visibleCases.length === 0 ? (
          <EmptyState
            description={
              caseFilter === "swe-bench" ? m.noSweBenchCases : m.noCasesSeedOrCreate
            }
            title={m.noCasesTitle}
          />
        ) : (
          <div className="table">
            <div className="table-row table-head">
              <span>{m.caseHeader}</span>
              <span>{m.type}</span>
              <span>{m.sync}</span>
            </div>
            {visibleCases.map((item) => (
              <button
                className={selectedCaseId === item.case_id ? "table-row selectable active" : "table-row selectable"}
                key={item.case_id}
                onClick={() => onSelectCase(item.case_id)}
                type="button"
              >
                <span>
                  <strong style={{ display: "block" }}>
                    {item.name && item.name !== item.case_id
                      ? item.name
                      : item.case_id}
                  </strong>
                  <small className="muted" style={{ display: "block" }}>
                    {SWE_CASE_TYPES.has(item.type)
                      ? shortInstanceLabel(item.case_id)
                      : item.case_id}
                  </small>
                </span>
                {caseTypeBadge(item)}
                <span className="badge neutral">{item.sync_state}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
