import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { ArtifactsPanel } from "../components/ArtifactsPanel";
import { ErrorBanner } from "../components/ErrorBanner";
import { HarnessCatalog } from "../components/HarnessCatalog";
import { ScoresPanel } from "../components/ScoresPanel";
import { LogViewer } from "../components/LogViewer";
import { Markdown } from "../components/Markdown";
import { SandboxPanel } from "../components/SandboxPanel";
import { StateBadge } from "../components/StateBadge";
import { formatDuration, formatRunDate } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type {
  AttributionRecord,
  AuditRecord,
  ImprovementsDoc,
  LangfuseStatus,
  ProposalIssue,
  RunContext,
  RunEventRecord,
  RunMetadata,
  RunRecord,
  SaveImprovementsResult,
  ValidationRecord
} from "../types";

type RunDetailProps = {
  run: RunRecord | null;
  // Every loaded run, used only to list this one's continuations.
  runs?: RunRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
  onContinueRun?: (runId: string, prompt: string, maxSteps?: number) => Promise<RunRecord>;
};

const POLL_INTERVAL_MS = 1500;
export const CANCELLABLE_STATES: RunRecord["state"][] = ["pending", "running"];
export const RETRYABLE_STATES: RunRecord["state"][] = ["failed", "cancelled"];
// A run can be continued once it has stopped and its workspace is final
// (ADR-0026). Mirrors CONTINUABLE_STATES in run_service.py.
export const CONTINUABLE_STATES: RunRecord["state"][] = [
  "completed",
  "awaiting_manual_score",
  "failed",
  "cancelled"
];
// Offered alongside whatever budget the parent used, which is already in the list.
const CONTINUE_STEP_OPTIONS = [1, 3, 6, 10];

// Harnesses whose CLI emits final summaries, not per-tool logs — so file/tool/
// governance attribution can't be parsed from them. claude runs now capture
// stream-json events (first-class tool calls), so a claude run only falls back
// to the summary-only note when its attribution carries no signals (old runs).
const SUMMARY_ONLY_HARNESSES = new Set(["claude", "codex"]);

const MESSAGES = defineMessages({
  en: {
    backToRuns: "← Runs",
    selectRun: "Select a run to view status, artifacts, score, and audit.",
    condition: {
      title: "Condition",
      adHoc: "Ad-hoc run — not part of an experiment",
      arm: "Arm",
      case: "Case",
      repository: "Repository",
      repositoryFallback: "the lab itself",
      commit: "Commit",
      harness: "Harness",
      governance: "Governance",
      executorModel: "Executor model",
      stepBudget: "Step budget",
      sourceBlurb: {
        native:
          "The agent saw the target repository's own setup. The lab injected nothing, " +
          "so this is the control for any governance comparison.",
        injected:
          "The lab's harness was copied into the target workspace. Whatever this run " +
          "did with governance, it did with the lab's, not the repository's.",
        lab_root:
          "The agent ran inside the lab repository itself — no external target " +
          "workspace was materialised."
      },
      validatedBy: (commands: ReactNode) => <>Validated by: {commands}</>,
      noValidationGate: "No validation gate — this run can only ever be scored by hand."
    },
    header: {
      eyebrow: "Run detail",
      continues: (parent: ReactNode) => <>Continues {parent}</>,
      continuedBy: (children: ReactNode) => <>Continued by {children}</>,
      cancel: "Cancel",
      retry: "Retry",
      sending: "Sending…",
      sendTraces: "Send traces",
      viewInLangfuse: "View in Langfuse ↗",
      session: (id: ReactNode) => <>Session {id}</>,
      noDirectLink: " — set LANGFUSE_PROJECT_URL in Settings for a direct link.",
      sentSpans: (spans: number, sessionId: string) => `Sent ${spans} spans to session ${sessionId}.`,
      exportFailed: "Export failed."
    },
    tabs: { detail: "Detail", terminal: "Terminal", desktop: "Desktop" },
    summary: {
      score: "Score",
      interventions: "Interventions",
      exitCode: "Exit code",
      started: "Started",
      ended: "Ended",
      queuedFor: "Queued for",
      execution: "Execution",
      failure: "Failure",
      none: "None",
      running: "running"
    },
    manualScore: {
      title: "Manual score",
      swebenchNote: "SWE-bench runs auto-score from validation — use Re-validate below instead",
      alreadyScored: "Already scored objectively — add your judgement of the work",
      requiredToComplete: "Required to complete run",
      scoreLabel: "Score (0-10)",
      interventionsLabel: "Interventions",
      notesLabel: "Notes",
      saving: "Saving...",
      submitScore: "Submit score"
    },
    continue: {
      title: "Continue",
      subtitle: "Starts a child run from this workspace — this run is not modified",
      followUpPrompt: "Follow-up prompt",
      placeholder: "What should the agent do next, starting from what is already there?",
      maxSteps: "Max steps",
      note:
        "A continuation counts as a human intervention: the child is hidden from the " +
        "dashboard by default so it is not compared against one-shot runs.",
      queueing: "Queueing...",
      continueRun: "Continue run"
    },
    validation: {
      title: "Validation & score",
      sweBenchResolved: "SWE-bench: RESOLVED",
      sweBenchNotResolved: "SWE-bench: NOT RESOLVED",
      objectivePass: "objective: pass",
      objectiveFail: "objective: fail",
      objectiveInconclusive: "objective: inconclusive",
      objectiveManual: "objective: manual",
      revalidate: "Re-validate",
      noValidationYet:
        "No validation yet. Objective validation runs automatically when a run " +
        "finishes; use Re-validate to run it now.",
      composite: "Composite",
      functional: "Functional",
      objective: "Objective",
      tokens: "Tokens",
      costUsd: "Cost (USD)",
      steps: "Steps",
      interventions: "Interventions",
      timeMin: "Time (min)",
      llmJudged: "LLM-judged",
      autoTests: "auto (tests)",
      manual: "manual",
      didNotMeasure: (reason: string | null) => `did not measure${reason ? `: ${reason}` : ""}`,
      checkLine: (name: ReactNode, exitCode: ReactNode, detail: ReactNode) => (
        <>
          {name} (exit {exitCode}) {detail}
        </>
      ),
      sweBenchExplainer:
        "SWE-bench scoring is objective and binary: resolved iff every FAIL_TO_PASS " +
        "test passes and no PASS_TO_PASS test regresses, evaluated by reinstalling " +
        "the archived workspace and running the subset (swebench_validate.py). The " +
        "run is auto-scored from this verdict — no manual score needed.",
      objectiveExplainer:
        "Objective validation runs the case's — or the target repository's — " +
        "validation commands against the archived workspace in a runner container. " +
        "The functional level is the subjective dimension; the composite is gated by " +
        "the objective floor."
    },
    attribution: {
      title: "Harness attribution",
      governanceBadge: (value: ReactNode) => <>governance {value}</>,
      reindex: "Re-index",
      summaryOnlyExplainer: (harness: ReactNode) => (
        <>
          Tool-level attribution isn&apos;t available for the <strong>{harness}</strong>{" "}
          harness: its CLI reports a final summary per step, not the per-tool logs
          (<code>permission=edit</code>, <code>read</code>, <code>bash</code>…) that
          opencode emits. Counts would all read 0 — not because the agent did
          nothing, but because the harness doesn&apos;t expose them. Only the step count
          is observable; judge behaviour from the LLM audit below.
        </>
      ),
      stepsParsed: "Steps parsed",
      noAttributionYet:
        "No attribution yet. It is computed automatically after a run from the " +
        "opencode step logs; use Re-index to compute it now.",
      tools: "Tools",
      agentModes: "Agent modes",
      governanceRead: "Governance read",
      skillsConsulted: "Skills consulted",
      bash: "Bash",
      governanceReadList: (docs: string) => `Governance read: ${docs}`,
      noGovernanceRead: "No governance docs were read in this run.",
      governanceSourceLabel: {
        target_workspace: "target repository's own",
        bare: "empty",
        lab: "lab repository's"
      },
      denominator: (label: ReactNode, harnessSource: ReactNode, fingerprintSuffix: string) => (
        <>
          Denominator: the <strong>{label}</strong> governance surface, under {harnessSource}
          {fingerprintSuffix}.
        </>
      ),
      bareWarning:
        "A bare run had no governance to read: the runner masked the lab's " +
        "CLAUDE.md, hooks, subagents and .github/, so read coverage is not a " +
        "measure of anything here (ADR-0049)."
    },
    audit: {
      title: "LLM audit",
      auditorPlaceholder: "auditor model — empty uses the transport default",
      auditing: "Auditing...",
      runAudit: "Run audit",
      noAuditYet:
        "No audit yet. A strong LLM reads this run's logs, diff, usage and score " +
        "and writes a qualitative diagnostic (skill/rule usage, efficiency, SDLC " +
        "adherence, failure points). Runs in the worker; this may take a minute.",
      auditingWith: (model: ReactNode) => <>Auditing with {model}…</>,
      defaultModel: "default model",
      auditFailed: "Audit failed",
      judgedAgainstSourceLabel: {
        target_workspace: "the target repository's own governance",
        lab: "the lab repository's governance"
      },
      judgedAgainst: (sourceLabel: ReactNode, fileCount: number, fingerprint: ReactNode) => (
        <>
          Judged against {sourceLabel} · {fileCount} file(s) · {fingerprint}
        </>
      ),
      rulebook: (rulesFile: ReactNode, harnessSource: ReactNode, truncatedSuffix: string) => (
        <>
          Rulebook: {rulesFile} · {harnessSource}
          {truncatedSuffix}
        </>
      ),
      rulebookTruncated: " · file list truncated for the fingerprint",
      predatesGovernance:
        "This audit predates per-run governance surfaces, so what it was judged " +
        "against was not recorded. Re-run the audit to pin it."
    },
    improvements: {
      title: "Improvement proposals",
      proposalsCount: (count: number) => `${count} proposal(s)`,
      strictnessAriaLabel: "Synthesis strictness",
      strictnessTitle:
        "Filter strictness: strict keeps only proposals with strong evidence; lenient admits more.",
      strictOptions: { strict: "strict", balanced: "balanced", lenient: "lenient" },
      synthesising: "Synthesising...",
      resynthesize: "Re-synthesize",
      proposeImprovements: "Propose improvements",
      auditFirstTitle: "Run the LLM audit first — proposals are synthesized from it.",
      saveTitle:
        "Save these proposals to the cross-run table (Improvements) so they can be " +
        "grouped into one combined issue.",
      saving: "Saving…",
      save: "Save",
      openSavedTitle: "Open Improvements with the saved proposals",
      savedCount: (n: number) => `Saved ${n} · view table ↗`,
      synthesisDidNotProduce: "Synthesis did not produce proposals — check the run logs.",
      runAuditFirstForIssues:
        "Turn this audit's findings into concrete GitHub issue proposals for the " +
        "template/harness repos (LLM synthesis in the worker).",
      runAuditFirstForProposals: "Run the LLM audit first; proposals are synthesized from its findings.",
      withinExpectedRange: (reason: ReactNode, cmd: ReactNode) => (
        <>
          This run was within the expected range for this case and model — {reason} You can
          force the synthesis with {cmd}.
        </>
      ),
      nothingActionable: "The synthesis found nothing actionable to improve from this audit.",
      runsLinkLabel: "Runs",
      noProposalsToSave: (runsLink: ReactNode) => (
        <>
          No proposals to save. This run is already recorded as{" "}
          <span className="badge neutral">no</span> in the {runsLink} list (Improvements column).
        </>
      ),
      issueLink: "issue ↗",
      notFiled: "not filed",
      reviewEditNote: (path: ReactNode, ghCode: ReactNode, cmd: ReactNode) => (
        <>
          Review/edit {path} (repo routing, wording), then file the issues from the host (needs{" "}
          {ghCode} auth): {cmd}
        </>
      )
    },
    sanitisedIssue: {
      title: "Sanitised issue (ml-python-base)",
      generating: "Generating…",
      generateButton: "Generate sanitised issue (ml-python-base)",
      proposalPending:
        "Synthesising a sanitised issue from the audit (this runs an LLM on the server " +
        "and can take 15 to 40 seconds).",
      generatePrompt:
        "Generate a sanitised GitHub issue for ml-python-base from this run's audit. " +
        "Run the audit first if it is not ready yet.",
      openPrefilled: "Open prefilled issue on GitHub ↗",
      linkExplainer: (template: ReactNode, repository: ReactNode) => (
        <>
          The link opens the {template} form on {repository} with every field already filled
          in. Confirm the two fields marked <strong>recommended</strong> (channel and SemVer)
          and tick the sanitisation boxes before creating it. Each field can also be copied on
          its own if you would rather paste by hand.
        </>
      ),
      recommendedBadge: "recommended",
      copyField: "Copy",
      copiedField: "Copied",
      viewAsMarkdown: "View as a single markdown block (manual copy and paste)",
      issueForRepo: (repository: ReactNode) => <>Issue for {repository}</>,
      copyAll: "Copy all"
    },
    catalog: { title: "Harness catalog", subtitle: "available · observed · LLM verdict" },
    commandPreview: "Command preview",
    logs: { title: "Logs", live: "live", history: "history" },
    artifacts: { title: "Artifacts" }
  },
  es: {
    backToRuns: "← Corridas",
    selectRun: "Seleccioná una corrida para ver su estado, artefactos, puntaje y auditoría.",
    condition: {
      title: "Condición",
      adHoc: "Corrida suelta — no forma parte de un experimento",
      arm: "Brazo",
      case: "Caso",
      repository: "Repositorio",
      repositoryFallback: "el lab mismo",
      commit: "Commit",
      harness: "Arnés",
      governance: "Gobernanza",
      executorModel: "Modelo ejecutor",
      stepBudget: "Presupuesto de pasos",
      sourceBlurb: {
        native:
          "El agente vio la configuración propia del repositorio destino. El lab no " +
          "inyectó nada, así que esta es la referencia para cualquier comparación de " +
          "gobernanza.",
        injected:
          "El arnés del lab se copió al workspace destino. Lo que esta corrida haya " +
          "hecho con la gobernanza, lo hizo con la del lab, no la del repositorio.",
        lab_root:
          "El agente corrió dentro del propio repositorio del lab — no se materializó " +
          "ningún workspace destino externo."
      },
      validatedBy: (commands: ReactNode) => <>Validado por: {commands}</>,
      noValidationGate: "Sin puerta de validación — esta corrida solo puede puntuarse a mano."
    },
    header: {
      eyebrow: "Detalle de la corrida",
      continues: (parent: ReactNode) => <>Continúa {parent}</>,
      continuedBy: (children: ReactNode) => <>Continuada por {children}</>,
      cancel: "Cancelar",
      retry: "Reintentar",
      sending: "Enviando…",
      sendTraces: "Enviar trazas",
      viewInLangfuse: "Ver en Langfuse ↗",
      session: (id: ReactNode) => <>Sesión {id}</>,
      noDirectLink: " — configurá LANGFUSE_PROJECT_URL en Configuración para un enlace directo.",
      sentSpans: (spans: number, sessionId: string) => `Se enviaron ${spans} spans a la sesión ${sessionId}.`,
      exportFailed: "Falló la exportación."
    },
    tabs: { detail: "Detalle", terminal: "Terminal", desktop: "Escritorio" },
    summary: {
      score: "Puntaje",
      interventions: "Intervenciones",
      exitCode: "Código de salida",
      started: "Iniciada",
      ended: "Finalizada",
      queuedFor: "En cola",
      execution: "Ejecución",
      failure: "Falla",
      none: "Ninguna",
      running: "en curso"
    },
    manualScore: {
      title: "Puntaje manual",
      swebenchNote:
        "Las corridas de SWE-bench se puntúan automáticamente a partir de la validación " +
        "— usá Re-validar más abajo",
      alreadyScored: "Ya tiene puntaje objetivo — agregá tu juicio sobre el trabajo",
      requiredToComplete: "Necesario para completar la corrida",
      scoreLabel: "Puntaje (0-10)",
      interventionsLabel: "Intervenciones",
      notesLabel: "Notas",
      saving: "Guardando...",
      submitScore: "Enviar puntaje"
    },
    continue: {
      title: "Continuar",
      subtitle: "Inicia una corrida hija desde este workspace — esta corrida no se modifica",
      followUpPrompt: "Prompt de seguimiento",
      placeholder: "¿Qué debería hacer el agente a continuación, partiendo de lo que ya existe?",
      maxSteps: "Pasos máximos",
      note:
        "Una continuación cuenta como intervención humana: la corrida hija queda oculta " +
        "del dashboard por defecto para no compararla contra corridas de un solo intento.",
      queueing: "Encolando...",
      continueRun: "Continuar corrida"
    },
    validation: {
      title: "Validación y puntaje",
      sweBenchResolved: "SWE-bench: RESUELTO",
      sweBenchNotResolved: "SWE-bench: NO RESUELTO",
      objectivePass: "objetivo: pasó",
      objectiveFail: "objetivo: falló",
      objectiveInconclusive: "objetivo: no concluyente",
      objectiveManual: "objetivo: manual",
      revalidate: "Re-validar",
      noValidationYet:
        "Todavía no hay validación. La validación objetiva corre automáticamente cuando " +
        "una corrida termina; usá Re-validar para correrla ahora.",
      composite: "Compuesto",
      functional: "Funcional",
      objective: "Objetivo",
      tokens: "Tokens",
      costUsd: "Costo (USD)",
      steps: "Pasos",
      interventions: "Intervenciones",
      timeMin: "Tiempo (min)",
      llmJudged: "Juzgado por LLM",
      autoTests: "automático (tests)",
      manual: "manual",
      didNotMeasure: (reason: string | null) => `no se midió${reason ? `: ${reason}` : ""}`,
      checkLine: (name: ReactNode, exitCode: ReactNode, detail: ReactNode) => (
        <>
          {name} (salida {exitCode}) {detail}
        </>
      ),
      sweBenchExplainer:
        "El puntaje de SWE-bench es objetivo y binario: resuelto si y solo si todos los " +
        "tests FAIL_TO_PASS pasan y ningún test PASS_TO_PASS retrocede, evaluado " +
        "reinstalando el workspace archivado y corriendo el subconjunto " +
        "(swebench_validate.py). La corrida se puntúa automáticamente a partir de este " +
        "veredicto — no hace falta puntaje manual.",
      objectiveExplainer:
        "La validación objetiva corre los comandos de validación del caso — o del " +
        "repositorio destino — contra el workspace archivado en un contenedor runner. " +
        "El nivel funcional es la dimensión subjetiva; el compuesto queda limitado por " +
        "el piso objetivo."
    },
    attribution: {
      title: "Atribución del arnés",
      governanceBadge: (value: ReactNode) => <>gobernanza {value}</>,
      reindex: "Re-indexar",
      summaryOnlyExplainer: (harness: ReactNode) => (
        <>
          La atribución a nivel de herramienta no está disponible para el arnés{" "}
          <strong>{harness}</strong>: su CLI reporta un resumen final por paso, no los logs
          por herramienta (<code>permission=edit</code>, <code>read</code>,{" "}
          <code>bash</code>…) que emite opencode. Los conteos darían todos 0 — no porque el
          agente no hizo nada, sino porque el arnés no los expone. Solo se puede observar la
          cantidad de pasos; juzgá el comportamiento con la auditoría LLM de abajo.
        </>
      ),
      stepsParsed: "Pasos parseados",
      noAttributionYet:
        "Todavía no hay atribución. Se calcula automáticamente después de una corrida a " +
        "partir de los logs de pasos de opencode; usá Re-indexar para calcularla ahora.",
      tools: "Herramientas",
      agentModes: "Modos de agente",
      governanceRead: "Gobernanza leída",
      skillsConsulted: "Skills consultadas",
      bash: "Bash",
      governanceReadList: (docs: string) => `Gobernanza leída: ${docs}`,
      noGovernanceRead: "No se leyó ningún documento de gobernanza en esta corrida.",
      governanceSourceLabel: {
        target_workspace: "propia del repositorio destino",
        bare: "vacía",
        lab: "del repositorio del lab"
      },
      denominator: (label: ReactNode, harnessSource: ReactNode, fingerprintSuffix: string) => (
        <>
          Denominador: la superficie de gobernanza <strong>{label}</strong>, bajo{" "}
          {harnessSource}
          {fingerprintSuffix}.
        </>
      ),
      bareWarning:
        "Una corrida bare no tuvo gobernanza para leer: el runner ocultó el CLAUDE.md, " +
        "los hooks, los subagentes y .github/ del lab, así que la cobertura de lectura " +
        "no mide nada acá (ADR-0049)."
    },
    audit: {
      title: "Auditoría LLM",
      auditorPlaceholder: "modelo auditor — vacío usa el default del transporte",
      auditing: "Auditando...",
      runAudit: "Correr auditoría",
      noAuditYet:
        "Todavía no hay auditoría. Un LLM potente lee los logs, el diff, el uso y el " +
        "puntaje de esta corrida y escribe un diagnóstico cualitativo (uso de " +
        "skills/reglas, eficiencia, adherencia al SDLC, puntos de falla). Corre en el " +
        "worker; puede tardar un minuto.",
      auditingWith: (model: ReactNode) => <>Auditando con {model}…</>,
      defaultModel: "modelo por defecto",
      auditFailed: "Falló la auditoría",
      judgedAgainstSourceLabel: {
        target_workspace: "la gobernanza propia del repositorio destino",
        lab: "la gobernanza del repositorio del lab"
      },
      judgedAgainst: (sourceLabel: ReactNode, fileCount: number, fingerprint: ReactNode) => (
        <>
          Juzgada contra {sourceLabel} · {fileCount} archivo(s) · {fingerprint}
        </>
      ),
      rulebook: (rulesFile: ReactNode, harnessSource: ReactNode, truncatedSuffix: string) => (
        <>
          Reglas: {rulesFile} · {harnessSource}
          {truncatedSuffix}
        </>
      ),
      rulebookTruncated: " · lista de archivos truncada para el fingerprint",
      predatesGovernance:
        "Esta auditoría es anterior a las superficies de gobernanza por corrida, así " +
        "que no quedó registrado contra qué se juzgó. Volvé a correr la auditoría para " +
        "fijarlo."
    },
    improvements: {
      title: "Propuestas de mejora",
      proposalsCount: (count: number) => `${count} propuesta(s)`,
      strictnessAriaLabel: "Rigurosidad de la síntesis",
      strictnessTitle:
        "Rigurosidad del filtro: estricto deja solo propuestas con evidencia fuerte; " +
        "flexible admite más.",
      strictOptions: { strict: "estricto", balanced: "equilibrado", lenient: "flexible" },
      synthesising: "Sintetizando...",
      resynthesize: "Re-sintetizar",
      proposeImprovements: "Proponer mejoras",
      auditFirstTitle: "Corré primero la auditoría LLM — las propuestas se sintetizan a partir de ella.",
      saveTitle:
        "Guardá estas propuestas en la tabla entre corridas (Mejoras) para poder " +
        "agruparlas en un solo issue combinado.",
      saving: "Guardando…",
      save: "Guardar",
      openSavedTitle: "Abrir Mejoras con las propuestas guardadas",
      savedCount: (n: number) => `Guardadas ${n} · ver tabla ↗`,
      synthesisDidNotProduce: "La síntesis no produjo propuestas — revisá los logs de la corrida.",
      runAuditFirstForIssues:
        "Convertí los hallazgos de esta auditoría en propuestas concretas de issues de " +
        "GitHub para los repos de template/harness (síntesis LLM en el worker).",
      runAuditFirstForProposals:
        "Corré primero la auditoría LLM; las propuestas se sintetizan a partir de sus hallazgos.",
      withinExpectedRange: (reason: ReactNode, cmd: ReactNode) => (
        <>
          Esta corrida estuvo dentro del rango esperado para este caso y modelo — {reason}{" "}
          Podés forzar la síntesis con {cmd}.
        </>
      ),
      nothingActionable: "La síntesis no encontró nada concreto para mejorar a partir de esta auditoría.",
      runsLinkLabel: "Corridas",
      noProposalsToSave: (runsLink: ReactNode) => (
        <>
          No hay propuestas para guardar. Esta corrida ya está registrada como{" "}
          <span className="badge neutral">no</span> en la lista de {runsLink} (columna
          Mejoras).
        </>
      ),
      issueLink: "issue ↗",
      notFiled: "sin crear",
      reviewEditNote: (path: ReactNode, ghCode: ReactNode, cmd: ReactNode) => (
        <>
          Revisá/editá {path} (ruteo de repos, redacción), y después creá los issues desde
          el host (necesita auth de {ghCode}): {cmd}
        </>
      )
    },
    sanitisedIssue: {
      title: "Issue saneado (ml-python-base)",
      generating: "Generando…",
      generateButton: "Generar issue saneado (ml-python-base)",
      proposalPending:
        "Sintetizando un issue saneado a partir de la auditoría (esto corre un LLM en el " +
        "servidor y puede tardar entre 15 y 40 segundos).",
      generatePrompt:
        "Generá un issue saneado de GitHub para ml-python-base a partir de la auditoría " +
        "de esta corrida. Corré primero la auditoría si todavía no está lista.",
      openPrefilled: "Abrir issue precargado en GitHub ↗",
      linkExplainer: (template: ReactNode, repository: ReactNode) => (
        <>
          El enlace abre el formulario {template} en {repository} con todos los campos ya
          completados. Confirmá los dos campos marcados como <strong>recomendado</strong>{" "}
          (canal y SemVer) y marcá las casillas de saneamiento antes de crearlo. Cada campo
          también se puede copiar por separado si preferís pegarlo a mano.
        </>
      ),
      recommendedBadge: "recomendado",
      copyField: "Copiar",
      copiedField: "Copiado",
      viewAsMarkdown: "Ver como un solo bloque markdown (copiar y pegar a mano)",
      issueForRepo: (repository: ReactNode) => <>Issue para {repository}</>,
      copyAll: "Copiar todo"
    },
    catalog: { title: "Catálogo del arnés", subtitle: "disponible · observado · veredicto LLM" },
    commandPreview: "Vista previa del comando",
    logs: { title: "Logs", live: "en vivo", history: "historial" },
    artifacts: { title: "Artefactos" }
  }
});

/**
 * What condition this run was, above the outcome and far above the logs (§16).
 *
 * A run is only interpretable as a member of a design. Reading stdout without
 * knowing which arm produced it, against which commit, with whose governance,
 * is how a pile of runs gets mistaken for a measurement.
 */
function RunConditionStrip({ context }: { context: RunContext }) {
  const m = useMessages(MESSAGES);
  return (
    <div className="panel run-condition">
      <div className="panel-head">
        <h3>{m.condition.title}</h3>
        {context.experiment_id ? (
          <Link to={`/evaluate/experiments?experiment=${context.experiment_id}`}>
            {context.experiment_id} ↗
          </Link>
        ) : (
          <span className="badge neutral">{m.condition.adHoc}</span>
        )}
      </div>
      {context.experiment_question ? (
        <p className="muted">{context.experiment_question}</p>
      ) : null}
      <div className="detail-grid">
        <div>
          <span>{m.condition.arm}</span>
          <strong>{context.condition_label || "—"}</strong>
        </div>
        <div>
          <span>{m.condition.case}</span>
          <strong>{context.case_name || context.case_id || "—"}</strong>
        </div>
        <div>
          <span>{m.condition.repository}</span>
          <strong>{context.target_repo_id || m.condition.repositoryFallback}</strong>
        </div>
        <div>
          <span>{m.condition.commit}</span>
          <strong className="mono">
            {context.target_commit ? context.target_commit.slice(0, 10) : "—"}
          </strong>
        </div>
        <div>
          <span>{m.condition.harness}</span>
          <strong>{context.harness}</strong>
        </div>
        <div>
          <span>{m.condition.governance}</span>
          <strong>{context.harness_source}</strong>
        </div>
        <div>
          <span>{m.condition.executorModel}</span>
          <strong>{context.executor_model || "—"}</strong>
        </div>
        <div>
          <span>{m.condition.stepBudget}</span>
          <strong>{context.max_steps}</strong>
        </div>
      </div>
      <p className="muted small">
        {m.condition.sourceBlurb[context.harness_source as keyof typeof m.condition.sourceBlurb] ?? ""}
      </p>
      {context.validation_commands.length > 0 ? (
        <p className="muted small">
          {m.condition.validatedBy(<code>{context.validation_commands.join(" && ")}</code>)}
        </p>
      ) : (
        <p className="muted small">{m.condition.noValidationGate}</p>
      )}
    </div>
  );
}

function harnessFromCommand(command: string[] | undefined): string {
  if (!command) return "opencode";
  const i = command.indexOf("--harness");
  return i >= 0 && i + 1 < command.length ? command[i + 1] : "opencode";
}

export function RunDetail({
  run,
  runs = [],
  onCancelRun,
  onRetryRun,
  onScoreRun,
  onContinueRun
}: RunDetailProps) {
  const navigate = useNavigate();
  const m = useMessages(MESSAGES);
  const harness = harnessFromCommand(run?.command);
  const [events, setEvents] = useState<RunEventRecord[]>([]);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [continuePrompt, setContinuePrompt] = useState("");
  const [continueSteps, setContinueSteps] = useState(3);
  const [scoreValue, setScoreValue] = useState(0);
  const [interventionsValue, setInterventionsValue] = useState(0);
  const [notesValue, setNotesValue] = useState("");
  const [runContext, setRunContext] = useState<RunContext | null>(null);
  const [validation, setValidation] = useState<ValidationRecord | null>(null);
  const [attribution, setAttribution] = useState<AttributionRecord | null>(null);
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [langfuse, setLangfuse] = useState<LangfuseStatus | null>(null);
  const [langfuseNotice, setLangfuseNotice] = useState<string | null>(null);
  const [langfuseSending, setLangfuseSending] = useState(false);
  const [metadata, setMetadata] = useState<RunMetadata | null>(null);
  const [auditModel, setAuditModel] = useState("");
  const [improvements, setImprovements] = useState<ImprovementsDoc | null>(null);
  const [improving, setImproving] = useState(false);
  const [improveError, setImproveError] = useState<string | null>(null);
  const [savingProposals, setSavingProposals] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveImprovementsResult | null>(null);
  const [improveStrictness, setImproveStrictness] = useState("balanced");
  const [proposalIssue, setProposalIssue] = useState<ProposalIssue | null>(null);
  const [proposalLoading, setProposalLoading] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [proposalNotice, setProposalNotice] = useState<string | null>(null);
  const [proposalCopied, setProposalCopied] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);
  // The sandbox is reachable by link, not only by clicking through: a wizard
  // that tells you to go and look inside the workspace has to be able to point
  // at it. Read once, at mount — after that the tab is yours.
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<"detail" | "terminal" | "desktop">(
    requestedTab === "terminal" || requestedTab === "desktop" ? requestedTab : "detail"
  );
  const consoleRef = useRef<HTMLDivElement | null>(null);
  // A claude run with events-based attribution has real tool counts; only fall
  // back to the summary-only explainer when there is nothing to show.
  const attributionHasSignals = Boolean(
    attribution &&
      (Object.values(attribution.tools ?? {}).some((count) => count > 0) ||
        (attribution.files?.read?.length ?? 0) > 0 ||
        (attribution.files?.edited?.length ?? 0) > 0)
  );
  const summaryOnlyHarness =
    SUMMARY_ONLY_HARNESSES.has(harness) && !attributionHasSignals;

  useEffect(() => {
    setEvents([]);
    setActionError(null);

    if (run === null) {
      return;
    }

    let active = true;

    const fetchLogs = () => {
      api
        .listRunLogs(run.run_id)
        .then((fetched) => {
          if (active) {
            setEvents(fetched);
          }
        })
        .catch(() => {
          // Log fetching is best-effort; keep showing the last known events.
        });
    };

    fetchLogs();

    if (run.state !== "running") {
      return () => {
        active = false;
      };
    }

    const interval = window.setInterval(fetchLogs, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [run?.run_id, run?.state]);

  useEffect(() => {
    // The scrollable element is the LogViewer's inner console, not the wrapper.
    const console = consoleRef.current?.querySelector(".run-console");
    if (console) {
      console.scrollTop = console.scrollHeight;
    }
  }, [events]);

  useEffect(() => {
    if (run === null) {
      return;
    }
    setScoreValue(run.score ?? 0);
    setInterventionsValue(run.interventions ?? 0);
    setNotesValue(run.notes ?? "");
  }, [run?.run_id, run?.score, run?.interventions, run?.notes]);

  useEffect(() => {
    setValidation(null);
    setAttribution(null);
    setAudit(null);
    setLangfuse(null);
    setLangfuseNotice(null);
    setMetadata(null);
    setImprovements(null);
    setImproveError(null);
    setProposalIssue(null);
    setProposalError(null);
    setProposalNotice(null);
    setProposalCopied(false);
    if (run === null) {
      return;
    }
    let active = true;
    const timers: number[] = [];
    // Validation/attribution are computed by the worker AFTER the state flips
    // to awaiting_manual_score (validation reinstalls the repo — can take a
    // minute). Retry a few times instead of freezing on "No validation yet".
    const fetchWithRetry = (
      fetcher: () => Promise<void>,
      tries: number,
      delayMs: number
    ) => {
      fetcher().catch(() => {
        if (active && tries > 0) {
          timers.push(window.setTimeout(() => fetchWithRetry(fetcher, tries - 1, delayMs), delayMs));
        }
      });
    };
    // Available the moment the run exists — it describes the design, not the
    // outcome — so it is fetched once and never retried.
    api
      .getRunContext(run.run_id)
      .then((result) => {
        if (active) {
          setRunContext(result);
        }
      })
      .catch(() => {
        // Pre-context runs and deleted configs: the strip stays hidden rather
        // than guessing at a condition nobody recorded.
      });
    fetchWithRetry(
      () =>
        api.getValidation(run.run_id).then((result) => {
          if (active) {
            setValidation(result);
          }
        }),
      run.state === "running" || run.state === "pending" ? 0 : 20,
      6000
    );
    fetchWithRetry(
      () =>
        api.getAttribution(run.run_id).then((result) => {
          if (active) {
            setAttribution(result);
          }
        }),
      run.state === "running" || run.state === "pending" ? 0 : 5,
      6000
    );
    api
      .getAudit(run.run_id)
      .then((result) => {
        if (active) {
          setAudit(result);
        }
      })
      .catch(() => {
        // No audit yet (404) is expected until one is requested.
      });
    api
      .getLangfuse(run.run_id)
      .then((result) => {
        if (active) {
          setLangfuse(result);
        }
      })
      .catch(() => {
        // Tracing status is decoration; a failure just hides the link.
      });
    api
      .getImprovements(run.run_id)
      .then((result) => {
        if (active) {
          setImprovements(result);
        }
      })
      .catch(() => {
        // No improvements yet (404) is expected until synthesis is requested.
      });
    const parseMetadata = (content: string) => {
      if (!active) {
        return;
      }
      try {
        setMetadata(JSON.parse(content) as RunMetadata);
      } catch {
        // Unreadable metadata — the catalog panel shows its empty hint.
      }
    };
    api
      .readArtifact(run.run_id, "metadata.json")
      .then((result) => parseMetadata(result.content))
      .catch(() => {
        // Not indexed yet (older runs) — index once, then retry the read.
        api
          .indexArtifacts(run.run_id)
          .then(() => api.readArtifact(run.run_id, "metadata.json"))
          .then((result) => parseMetadata(result.content))
          .catch(() => {
            // No metadata.json at all (e.g. dry-run) — leave the hint visible.
          });
      });
    return () => {
      active = false;
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [run?.run_id, run?.state]);

  if (run === null) {
    return (
      <section className="run-detail-page">
        <Link className="run-detail-back" to="/advanced/runs">
          {m.backToRuns}
        </Link>
        <p className="muted">{m.selectRun}</p>
      </section>
    );
  }

  const canCancel = CANCELLABLE_STATES.includes(run.state);
  const canRetry = RETRYABLE_STATES.includes(run.state);

  const handleCancel = () => {
    setActionPending(true);
    setActionError(null);
    onCancelRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRetry = () => {
    setActionPending(true);
    setActionError(null);
    onRetryRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const canContinue = onContinueRun !== undefined && CONTINUABLE_STATES.includes(run.state);
  const children = runs.filter((item) => item.parent_run_id === run.run_id);

  const handleContinue = () => {
    const prompt = continuePrompt.trim();
    if (onContinueRun === undefined || prompt === "") {
      return;
    }
    setActionPending(true);
    setActionError(null);
    onContinueRun(run.run_id, prompt, continueSteps)
      .then((created) => {
        setContinuePrompt("");
        navigate(`/advanced/runs/${created.run_id}`);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleScore = () => {
    const clampedScore = Math.max(0, Math.min(10, scoreValue));
    const normalizedInterventions = Math.max(0, interventionsValue);
    setActionPending(true);
    setActionError(null);
    onScoreRun(run.run_id, clampedScore, normalizedInterventions, notesValue)
      .then(() => api.getValidation(run.run_id).then(setValidation).catch(() => {}))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRevalidate = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .validateRun(runId)
      .then(() => {
        // Validation runs asynchronously in the worker; poll for the result.
        let tries = 0;
        const poll = () => {
          api
            .getValidation(runId)
            .then((result) => setValidation(result))
            .catch(() => {});
          tries += 1;
          if (tries < 8) {
            window.setTimeout(poll, 3000);
          }
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  // SWE-bench validations carry the official verdict in the summary line
  // ("resolved=True F2P=1/1 P2P=18/18") written by swebench_validate.py.
  const isSwebench = validation?.objective?.summary?.startsWith("resolved=") ?? false;

  // null means "nothing was measured". That is two different situations: a case
  // with no objective criterion at all (manual scoring), or checks that ran and
  // could not conclude — a timeout is a fact about the lab, not about the code.
  const hasChecks = (validation?.objective?.checks?.length ?? 0) > 0;

  const objectiveBadge = (passed: boolean | null | undefined) => {
    if (isSwebench) {
      if (passed === true) return <span className="badge ok">{m.validation.sweBenchResolved}</span>;
      if (passed === false) return <span className="badge error">{m.validation.sweBenchNotResolved}</span>;
    }
    if (passed === true) return <span className="badge ok">{m.validation.objectivePass}</span>;
    if (passed === false) return <span className="badge error">{m.validation.objectiveFail}</span>;
    if (hasChecks) return <span className="badge warn">{m.validation.objectiveInconclusive}</span>;
    return <span className="badge">{m.validation.objectiveManual}</span>;
  };

  const scoreSourceBadge = (source: string | undefined) => {
    if (source === "llm_judge") return <span className="badge">{m.validation.llmJudged}</span>;
    if (source === "objective") return <span className="badge">{m.validation.autoTests}</span>;
    if (source === "manual") return <span className="badge">{m.validation.manual}</span>;
    return null;
  };

  const handleIndexAttribution = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .indexAttribution(runId)
      .then((result) => setAttribution(result))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRunAudit = () => {
    const runId = run.run_id;
    const model = auditModel.trim() || undefined;
    setActionPending(true);
    setActionError(null);
    setAudit({
      run_id: runId,
      auditor_model: model ?? "",
      status: "running",
      markdown: "",
      error: null,
      created_at: "",
      updated_at: ""
    });
    api
      .runAudit(runId, model)
      .then(() => {
        // The audit is an LLM call in the worker; poll until it settles.
        let tries = 0;
        const poll = () => {
          api
            .getAudit(runId)
            .then((result) => {
              setAudit(result);
              if (result.status === "running" && tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            })
            .catch(() => {
              if (tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            });
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleImprove = () => {
    const runId = run.run_id;
    setImproving(true);
    setImproveError(null);
    api
      .runImprove(runId, auditModel.trim() || undefined, improveStrictness)
      .then(() => {
        // The synthesis is an LLM call in the worker; poll for the artifact.
        let tries = 0;
        const poll = () => {
          api
            .getImprovements(runId)
            .then((result) => {
              setImprovements(result);
              setImproving(false);
            })
            .catch(() => {
              if (tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              } else {
                setImproving(false);
                setImproveError(m.improvements.synthesisDidNotProduce);
              }
            });
        };
        window.setTimeout(poll, 4000);
      })
      .catch((error: Error) => {
        setImproving(false);
        setImproveError(error.message);
      });
  };

  const handleSaveProposals = () => {
    const runId = run.run_id;
    setSavingProposals(true);
    api
      .saveImprovements(runId)
      .then((result) => {
        setSaveResult(result);
        setImproveError(null);
      })
      .catch((error: Error) => setImproveError(error.message))
      .finally(() => setSavingProposals(false));
  };

  const handleGenerateIssue = () => {
    const runId = run.run_id;
    setProposalLoading(true);
    setProposalError(null);
    setProposalNotice(null);
    setProposalCopied(false);
    api
      .getProposalIssue(runId)
      .then((result) => {
        setProposalIssue(result);
      })
      .catch((error: Error) => {
        // A 409 means the prerequisite isn't ready (no finished audit) — surface it
        // as a soft, actionable notice rather than a red error banner.
        if (error instanceof ApiError && error.status === 409) {
          setProposalNotice(error.message);
        } else {
          setProposalError(error.message);
        }
      })
      .finally(() => setProposalLoading(false));
  };

  const handleCopyIssue = () => {
    if (!proposalIssue) {
      return;
    }
    navigator.clipboard
      .writeText(proposalIssue.markdown)
      .then(() => {
        setProposalCopied(true);
        window.setTimeout(() => setProposalCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard access denied — the markdown stays selectable in the <pre>.
      });
  };

  const handleCopyField = (key: string, text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopiedField(key);
        window.setTimeout(
          () => setCopiedField((current) => (current === key ? null : current)),
          2000
        );
      })
      .catch(() => {
        // Clipboard access denied — the value stays selectable in the field.
      });
  };

  const handleLangfuseExport = () => {
    if (run === null) return;
    setLangfuseSending(true);
    setLangfuseNotice(null);
    api
      .exportLangfuse(run.run_id)
      .then((result) => {
        setLangfuseNotice(
          result.ok ? m.header.sentSpans(result.spans, result.session_id) : result.reason
        );
        if (result.session_url) {
          setLangfuse((current) =>
            current === null ? current : { ...current, session_url: result.session_url }
          );
        }
      })
      .catch((error: unknown) => {
        setLangfuseNotice(error instanceof ApiError ? error.message : m.header.exportFailed);
      })
      .finally(() => setLangfuseSending(false));
  };

  const countEntries = (counts: Record<string, number>) =>
    Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${name}×${count}`)
      .join("  ");

  return (
    <section className="run-detail-page">
      <Link className="run-detail-back" to="/advanced/runs">
        {m.backToRuns}
      </Link>
      <div className="run-detail-header">
        <p className="eyebrow">{m.header.eyebrow}</p>
        <h2>{run.run_id}</h2>
        <StateBadge state={run.state} />
        {run.parent_run_id || children.length > 0 ? (
          <p className="muted run-lineage">
            {run.parent_run_id
              ? m.header.continues(
                  <Link to={`/advanced/runs/${run.parent_run_id}`}>{run.parent_run_id}</Link>
                )
              : null}
            {run.parent_run_id && children.length > 0 ? " · " : null}
            {children.length > 0
              ? m.header.continuedBy(
                  children.map((child, index) => (
                    <span key={child.run_id}>
                      {index > 0 ? ", " : ""}
                      <Link to={`/advanced/runs/${child.run_id}`}>{child.run_id}</Link>
                    </span>
                  ))
                )
              : null}
          </p>
        ) : null}
        <div className="run-actions">
          <button disabled={!canCancel || actionPending} onClick={handleCancel} type="button">
            {m.header.cancel}
          </button>
          <button disabled={!canRetry || actionPending} onClick={handleRetry} type="button">
            {m.header.retry}
          </button>
          {/* Only shown when tracing is on: a lab without Langfuse gets no dead buttons. */}
          {langfuse?.enabled ? (
            <>
              <button disabled={langfuseSending} onClick={handleLangfuseExport} type="button">
                {langfuseSending ? m.header.sending : m.header.sendTraces}
              </button>
              {langfuse.session_url ? (
                <a
                  className="run-langfuse-link"
                  href={langfuse.session_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  {m.header.viewInLangfuse}
                </a>
              ) : null}
            </>
          ) : null}
        </div>
        {langfuse?.enabled ? (
          <p className="muted run-langfuse-note">
            {m.header.session(<code>{langfuse.session_id}</code>)}
            {langfuse.session_url ? null : m.header.noDirectLink}
            {langfuseNotice ? ` · ${langfuseNotice}` : null}
          </p>
        ) : null}
        {actionError !== null ? <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} /> : null}
      </div>
      <div className="run-tabs">
        {(["detail", "terminal", "desktop"] as const).map((name) => (
          <button
            className={tab === name ? "run-tab active" : "run-tab"}
            key={name}
            onClick={() => setTab(name)}
            type="button"
          >
            {m.tabs[name]}
          </button>
        ))}
      </div>
      {tab !== "detail" ? <SandboxPanel key={tab} kind={tab} runId={run.run_id} /> : null}
      {tab === "detail" ? (
        <>
      {runContext ? <RunConditionStrip context={runContext} /> : null}
      <div className="detail-grid">
        <div>
          <span>{m.summary.score}</span>
          <strong>{run.score ?? "—"}</strong>
        </div>
        <div>
          <span>{m.summary.interventions}</span>
          <strong>{run.interventions ?? "—"}</strong>
        </div>
        <div>
          <span>{m.summary.exitCode}</span>
          <strong>{run.exit_code ?? "—"}</strong>
        </div>
        <div>
          <span>{m.summary.started}</span>
          <strong>{formatRunDate(run.started_at)}</strong>
        </div>
        <div>
          <span>{m.summary.ended}</span>
          <strong>{formatRunDate(run.ended_at)}</strong>
        </div>
        {/*
          Wall clock between created and ended answers "how long did I wait",
          never "how slow was the model". The worker runs at concurrency 1, so
          a batch queues behind itself and a run can sit for hours before its
          container starts. Keeping the two apart is what makes a slow harness
          distinguishable from a busy one.
        */}
        <div>
          <span>{m.summary.queuedFor}</span>
          <strong>{formatDuration(run.created_at, run.started_at) || "—"}</strong>
        </div>
        <div>
          <span>{m.summary.execution}</span>
          <strong>
            {formatDuration(run.started_at, run.ended_at) ||
              (run.started_at ? m.summary.running : "—")}
          </strong>
        </div>
        <div>
          <span>{m.summary.failure}</span>
          <strong>{run.failure_reason ?? m.summary.none}</strong>
        </div>
      </div>
      {run.state === "awaiting_manual_score" || run.state === "completed" ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>{m.manualScore.title}</h3>
            <span className="muted">
              {isSwebench
                ? m.manualScore.swebenchNote
                : run.state === "completed"
                  ? // A run with objective validation completes on its own, so this
                    // form would never appear for it — and the human judgement of
                    // *how* it did is exactly what the objective verdict cannot say.
                    m.manualScore.alreadyScored
                  : m.manualScore.requiredToComplete}
            </span>
          </div>
          <div className="form-grid score-form-grid">
            <label>
              <span>{m.manualScore.scoreLabel}</span>
              <input
                max={10}
                min={0}
                onChange={(event) => setScoreValue(Number(event.target.value))}
                type="number"
                value={scoreValue}
              />
            </label>
            <label>
              <span>{m.manualScore.interventionsLabel}</span>
              <input
                min={0}
                onChange={(event) => setInterventionsValue(Number(event.target.value))}
                type="number"
                value={interventionsValue}
              />
            </label>
            <label className="full">
              <span>{m.manualScore.notesLabel}</span>
              <textarea onChange={(event) => setNotesValue(event.target.value)} value={notesValue} />
            </label>
          </div>
          <div className="panel-actions score-panel-actions">
            <button className="primary" disabled={actionPending} onClick={handleScore} type="button">
              {actionPending ? m.manualScore.saving : m.manualScore.submitScore}
            </button>
          </div>
        </div>
      ) : null}
      {canContinue ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>{m.continue.title}</h3>
            <span className="muted">{m.continue.subtitle}</span>
          </div>
          <div className="form-grid score-form-grid">
            <label className="full">
              <span>{m.continue.followUpPrompt}</span>
              <textarea
                onChange={(event) => setContinuePrompt(event.target.value)}
                placeholder={m.continue.placeholder}
                rows={4}
                value={continuePrompt}
              />
            </label>
            <label>
              <span>{m.continue.maxSteps}</span>
              <select
                onChange={(event) => setContinueSteps(Number(event.target.value))}
                value={continueSteps}
              >
                {CONTINUE_STEP_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="muted">{m.continue.note}</p>
          <div className="panel-actions score-panel-actions">
            <button
              className="primary"
              disabled={actionPending || continuePrompt.trim() === ""}
              onClick={handleContinue}
              type="button"
            >
              {actionPending ? m.continue.queueing : m.continue.continueRun}
            </button>
          </div>
        </div>
      ) : null}
      <div className="run-detail-grid">
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.validation.title}</h3>
          <div className="run-actions">
            {validation ? objectiveBadge(validation.objective?.passed) : null}
            <button disabled={actionPending} onClick={handleRevalidate} type="button">
              {m.validation.revalidate}
            </button>
          </div>
        </div>
        {validation === null ? (
          <p className="muted">{m.validation.noValidationYet}</p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>{m.validation.composite}</span>
                <strong>{validation.composite ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.functional}</span>
                <strong>
                  {validation.subjective?.functional_score ?? "—"}{" "}
                  {scoreSourceBadge(validation.subjective?.score_source)}
                </strong>
              </div>
              <div>
                <span>{m.validation.objective}</span>
                <strong>{validation.objective?.summary ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.tokens}</span>
                <strong>{validation.cost?.total_tokens ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.costUsd}</span>
                <strong>{validation.cost?.cost_usd ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.steps}</span>
                <strong>{validation.effort?.steps ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.interventions}</span>
                <strong>{validation.effort?.interventions ?? "—"}</strong>
              </div>
              <div>
                <span>{m.validation.timeMin}</span>
                <strong>{validation.cost?.time_minutes ?? "—"}</strong>
              </div>
            </div>
            {validation.objective?.checks && validation.objective.checks.length > 0 ? (
              <div className="run-console">
                {validation.objective.checks.map((check, index) => {
                  const inconclusive = check.conclusive === false;
                  const tone = inconclusive ? "warn" : check.passed ? "info" : "error";
                  const mark = inconclusive ? "⊘" : check.passed ? "✓" : "✗";
                  return (
                    <div className={`run-console-line ${tone}`} key={index}>
                      <span
                        title={
                          inconclusive
                            ? m.validation.didNotMeasure(
                                check.inconclusive_reason
                                  ? check.inconclusive_reason.replace(/_/g, " ")
                                  : null
                              )
                            : undefined
                        }
                      >
                        {mark}
                      </span>
                      <span>
                        {m.validation.checkLine(check.name, check.exit_code ?? "?", check.detail)}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </>
        )}
        {isSwebench ? (
          <p className="muted">{m.validation.sweBenchExplainer}</p>
        ) : (
          <p className="muted">{m.validation.objectiveExplainer}</p>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.attribution.title}</h3>
          <div className="run-actions">
            {attribution && !summaryOnlyHarness ? (
              <span className="badge">
                {m.attribution.governanceBadge(attribution.harness_usage?.governance_coverage ?? "—")}
              </span>
            ) : null}
            {!summaryOnlyHarness ? (
              <button disabled={actionPending} onClick={handleIndexAttribution} type="button">
                {m.attribution.reindex}
              </button>
            ) : null}
          </div>
        </div>
        {summaryOnlyHarness ? (
          <>
            <p className="muted">{m.attribution.summaryOnlyExplainer(harness)}</p>
            <div className="detail-grid">
              <div>
                <span>{m.attribution.stepsParsed}</span>
                <strong>{attribution?.steps_parsed ?? "—"}</strong>
              </div>
            </div>
          </>
        ) : attribution === null ? (
          <p className="muted">{m.attribution.noAttributionYet}</p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>{m.attribution.tools}</span>
                <strong>{countEntries(attribution.tools) || "—"}</strong>
              </div>
              <div>
                <span>{m.attribution.agentModes}</span>
                <strong>{countEntries(attribution.agent_modes) || "—"}</strong>
              </div>
              <div>
                <span>{m.attribution.governanceRead}</span>
                <strong>{attribution.harness_usage?.governance_coverage ?? "—"}</strong>
              </div>
              <div>
                <span>{m.attribution.skillsConsulted}</span>
                <strong>
                  {(attribution.harness_usage?.skills_consulted?.length ?? 0)}/
                  {attribution.harness_usage?.skills_available?.length ?? 0}
                </strong>
              </div>
              <div>
                <span>{m.attribution.bash}</span>
                <strong>{countEntries(attribution.bash_commands) || "—"}</strong>
              </div>
              <div>
                <span>{m.attribution.stepsParsed}</span>
                <strong>{attribution.steps_parsed}</strong>
              </div>
            </div>
            {attribution.harness_usage?.governance_docs_read &&
            attribution.harness_usage.governance_docs_read.length > 0 ? (
              <p className="muted">
                {m.attribution.governanceReadList(
                  attribution.harness_usage.governance_docs_read.join(", ")
                )}
              </p>
            ) : (
              <p className="muted">{m.attribution.noGovernanceRead}</p>
            )}
            {/*
              The denominator's provenance. "2/9" against a client's own
              documents and "2/9" against the lab's are different measurements
              that would otherwise render identically — and a native run scored
              against the lab's rules would be scored against rules it never
              received (ADR-0036).
            */}
            {attribution.harness_usage?.governance_source ? (
              <p className="muted small">
                {m.attribution.denominator(
                  attribution.harness_usage.governance_source === "target_workspace"
                    ? m.attribution.governanceSourceLabel.target_workspace
                    : attribution.harness_usage.governance_source === "bare"
                      ? m.attribution.governanceSourceLabel.bare
                      : m.attribution.governanceSourceLabel.lab,
                  <code>harness_source={attribution.harness_usage.harness_source}</code>,
                  attribution.harness_usage.governance_fingerprint
                    ? ` · fingerprint ${attribution.harness_usage.governance_fingerprint.slice(0, 12)}`
                    : ""
                )}
              </p>
            ) : null}
            {attribution.harness_usage?.governance_source === "bare" ? (
              <p className="muted small">{m.attribution.bareWarning}</p>
            ) : null}
            {attribution.harness_usage?.governance_warning ? (
              <p className="callout callout--warning small">
                {attribution.harness_usage.governance_warning}
              </p>
            ) : null}
            <p className="muted">{attribution.harness_usage?.skills_note}</p>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.audit.title}</h3>
          <div className="run-actions">
            {audit ? <span className="badge">{audit.status}</span> : null}
            <input
              className="audit-model-input"
              onChange={(event) => setAuditModel(event.target.value)}
              placeholder={m.audit.auditorPlaceholder}
              type="text"
              value={auditModel}
            />
            <button
              disabled={actionPending || audit?.status === "running"}
              onClick={handleRunAudit}
              type="button"
            >
              {audit?.status === "running" ? m.audit.auditing : m.audit.runAudit}
            </button>
          </div>
        </div>
        {audit === null ? (
          <p className="muted">{m.audit.noAuditYet}</p>
        ) : audit.status === "running" ? (
          <p className="muted">{m.audit.auditingWith(audit.auditor_model || m.audit.defaultModel)}</p>
        ) : audit.status === "failed" ? (
          <ErrorBanner message={audit.error ?? m.audit.auditFailed} />
        ) : (
          <div className="audit-body">
            {/*
              What the auditor was allowed to judge against, stated before its
              judgement. ADR-0036: a `native` run is evaluated against the
              target repository's own governance, an `injected` one against the
              harness the lab actually put there. Never against rules the run
              did not receive.
            */}
            {audit.governance_json && "fingerprint" in audit.governance_json ? (
              <details className="governance-surface">
                <summary>
                  {m.audit.judgedAgainst(
                    audit.governance_json.source === "target_workspace"
                      ? m.audit.judgedAgainstSourceLabel.target_workspace
                      : m.audit.judgedAgainstSourceLabel.lab,
                    audit.governance_json.file_count,
                    <code>{audit.governance_json.fingerprint.slice(0, 12)}</code>
                  )}
                </summary>
                <p className="muted small">
                  {m.audit.rulebook(
                    <code>{audit.governance_json.rules_file}</code>,
                    <code>harness_source={audit.governance_json.harness_source}</code>,
                    audit.governance_json.truncated ? m.audit.rulebookTruncated : ""
                  )}
                </p>
                {audit.governance_json.warning ? (
                  <p className="callout callout--warning small">
                    {audit.governance_json.warning}
                  </p>
                ) : null}
                <ul className="check-items">
                  {audit.governance_json.files.map((file) => (
                    <li key={file}>
                      <code>{file}</code>
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="muted small">{m.audit.predatesGovernance}</p>
            )}
            <Markdown>{audit.markdown}</Markdown>
          </div>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.improvements.title}</h3>
          <div className="run-actions">
            {improvements ? (
              <span className="badge ok">{m.improvements.proposalsCount(improvements.proposals.length)}</span>
            ) : null}
            <select
              aria-label={m.improvements.strictnessAriaLabel}
              onChange={(e) => setImproveStrictness(e.target.value)}
              title={m.improvements.strictnessTitle}
              value={improveStrictness}
            >
              <option value="strict">{m.improvements.strictOptions.strict}</option>
              <option value="balanced">{m.improvements.strictOptions.balanced}</option>
              <option value="lenient">{m.improvements.strictOptions.lenient}</option>
            </select>
            <button
              disabled={improving || audit?.status !== "done"}
              onClick={handleImprove}
              title={audit?.status !== "done" ? m.improvements.auditFirstTitle : undefined}
              type="button"
            >
              {improving
                ? m.improvements.synthesising
                : improvements
                  ? m.improvements.resynthesize
                  : m.improvements.proposeImprovements}
            </button>
            {improvements !== null && improvements.proposals.length > 0 ? (
              <button
                disabled={savingProposals}
                onClick={handleSaveProposals}
                title={m.improvements.saveTitle}
                type="button"
              >
                {savingProposals ? m.improvements.saving : m.improvements.save}
              </button>
            ) : null}
            {saveResult !== null ? (
              <Link
                className="badge ok save-feedback"
                title={m.improvements.openSavedTitle}
                to="/advanced/improvements"
              >
                {m.improvements.savedCount(saveResult.saved + saveResult.updated)}
              </Link>
            ) : null}
          </div>
        </div>
        {improveError ? <ErrorBanner message={improveError} /> : null}
        {improvements === null ? (
          <p className="muted">
            {audit?.status === "done"
              ? m.improvements.runAuditFirstForIssues
              : m.improvements.runAuditFirstForProposals}
          </p>
        ) : (
          <>
            {improvements.proposals.length === 0 ? (
              <>
                {improvements.gate?.skipped ? (
                  <p className="muted">
                    {m.improvements.withinExpectedRange(
                      improvements.gate.reason,
                      <code>make improve RUN={run.run_id} IMPROVE_FORCE=1</code>
                    )}
                  </p>
                ) : (
                  <p className="muted">{m.improvements.nothingActionable}</p>
                )}
                {/* Close the loop: 0 proposals still persists improvements.json,
                    so the Runs list already reflects this run as "no" — say so,
                    since there is nothing to save into the cross-run worklist. */}
                <p className="muted">
                  {m.improvements.noProposalsToSave(
                    <Link to="/advanced/runs">{m.improvements.runsLinkLabel}</Link>
                  )}
                </p>
              </>
            ) : (
              <div className="table">
                {improvements.proposals.map((proposal, index) => (
                  <div className="table-row" key={`${proposal.title}-${index}`}>
                    <span>
                      <strong>{proposal.title}</strong>
                      <small>{proposal.body.slice(0, 220)}</small>
                    </span>
                    <span>
                      <span className="badge neutral">{proposal.repo.split("/").pop()}</span>
                      {proposal.kind ? <small> {proposal.kind}</small> : null}
                    </span>
                    <span>
                      {proposal.created_issue_url ? (
                        <a href={proposal.created_issue_url} rel="noreferrer" target="_blank">
                          {m.improvements.issueLink}
                        </a>
                      ) : (
                        <span className="muted">{m.improvements.notFiled}</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p className="muted">
              {m.improvements.reviewEditNote(
                <code>data/runs/{run.run_id}/improvements.json</code>,
                <code>gh</code>,
                <code>make improve RUN={run.run_id} CREATE=1</code>
              )}
            </p>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.sanitisedIssue.title}</h3>
          <div className="run-actions">
            <button disabled={proposalLoading} onClick={handleGenerateIssue} type="button">
              {proposalLoading ? m.sanitisedIssue.generating : m.sanitisedIssue.generateButton}
            </button>
          </div>
        </div>
        {proposalError ? <ErrorBanner message={proposalError} onDismiss={() => setProposalError(null)} /> : null}
        {proposalNotice ? (
          <p className="muted" style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start" }}>
            <span aria-hidden="true">ℹ️</span>
            <span>{proposalNotice}</span>
          </p>
        ) : null}
        {proposalIssue === null ? (
          <p className="muted">
            {proposalLoading
              ? m.sanitisedIssue.proposalPending
              : m.sanitisedIssue.generatePrompt}
          </p>
        ) : (
          <>
            <div className="run-actions" style={{ marginBottom: "0.5rem" }}>
              <a
                className="primary"
                href={proposalIssue.form.url}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "inline-block",
                  padding: "8px 14px",
                  borderRadius: "6px",
                  textDecoration: "none"
                }}
              >
                {m.sanitisedIssue.openPrefilled}
              </a>
            </div>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              {m.sanitisedIssue.linkExplainer(
                <code>{proposalIssue.form.template}</code>,
                proposalIssue.repository
              )}
            </p>
            <div className="proposal-fields" style={{ display: "grid", gap: "0.75rem" }}>
              {proposalIssue.form.fields.map((field) => (
                <div key={field.id}>
                  <div className="panel-head">
                    <h4 style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                      {field.label}
                      {field.recommended ? (
                        <span className="badge ok">{m.sanitisedIssue.recommendedBadge}</span>
                      ) : null}
                    </h4>
                    <button
                      onClick={() => handleCopyField(field.id, field.value)}
                      type="button"
                    >
                      {copiedField === field.id ? m.sanitisedIssue.copiedField : m.sanitisedIssue.copyField}
                    </button>
                  </div>
                  {field.reason ? (
                    <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
                      {field.reason}
                    </p>
                  ) : null}
                  <pre
                    style={{
                      fontFamily: "monospace",
                      whiteSpace: "pre-wrap",
                      margin: 0
                    }}
                  >
                    {field.value}
                  </pre>
                </div>
              ))}
            </div>
            <details style={{ marginTop: "1rem" }}>
              <summary>{m.sanitisedIssue.viewAsMarkdown}</summary>
              <div className="panel-head" style={{ marginTop: "0.5rem" }}>
                <span className="muted">{m.sanitisedIssue.issueForRepo(proposalIssue.repository)}</span>
                <button onClick={handleCopyIssue} type="button">
                  {proposalCopied ? m.sanitisedIssue.copiedField : m.sanitisedIssue.copyAll}
                </button>
              </div>
              <pre
                style={{
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  maxHeight: "24rem",
                  overflow: "auto"
                }}
              >
                {proposalIssue.markdown}
              </pre>
            </details>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.catalog.title}</h3>
          <span className="muted">{m.catalog.subtitle}</span>
        </div>
        <HarnessCatalog attribution={attribution} audit={audit} metadata={metadata} />
      </div>
      <div>
        <p className="sidebar-label">{m.commandPreview}</p>
        <code className="command-preview">{run.command.join(" ")}</code>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.logs.title}</h3>
          <span className="muted">{run.state === "running" ? m.logs.live : m.logs.history}</span>
        </div>
        <div ref={consoleRef}>
          <LogViewer events={events} />
        </div>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>{m.artifacts.title}</h3>
          <span className="muted">data/runs/{run.run_id}</span>
        </div>
        <ScoresPanel runId={run.run_id} />
        <ArtifactsPanel runId={run.run_id} />
      </div>
      </div>
        </>
      ) : null}
    </section>
  );
}
