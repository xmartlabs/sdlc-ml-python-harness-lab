import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { CapabilityPanel } from "../components/CapabilityPanel";
import { FactorialPanel } from "../components/FactorialPanel";
import { HelpPanel } from "../components/HelpPanel";
import { Markdown } from "../components/Markdown";
import { ModelPicker } from "../components/ModelPicker";
import { Spinner } from "../components/Spinner";
import {
  IconAlert,
  IconCheck,
  IconMinus,
  IconTrendDown,
  IconTrendUp,
  IconX
} from "../components/icons";
import {
  defaultNativeModel,
  HARNESS_NATIVE_MODELS,
  HARNESS_OPTIONS,
  HOST_HARNESSES,
  useNativeModelIndex
} from "../harnesses";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import type {
  AggregateRecord,
  CaseOutcomeCell,
  ConditionSummary,
  DeltaRecord,
  ExperimentAuditRecord,
  ExperimentRecord,
  ExperimentSummary,
  LaunchConfigSummary,
  ModelOption,
  RepetitionPlan,
  RunConfigPayload,
  RunValidationResult,
  SuccessDelta,
  ValidityBlock,
  VerdictBlock,
  VerdictHighlight
} from "../types";

const MESSAGES = defineMessages({
  en: {
    modeLabel: {
      harness_evaluation: "Harness evaluation",
      cross_repo: "Cross-repo",
      discovery: "Discovery",
      subject_validation: "Subject validation",
      harness_by_model: "Harness x model"
    } as Record<string, string>,
    /** What each mode does and does not license — shown, not assumed known. */
    modeCaveat: {
      harness_evaluation:
        "Repository, case and model held fixed; the harness varies. The only mode that supports a causal claim — and only with repetitions.",
      cross_repo:
        "Harness and model held fixed; the repository varies. Read per repository: scores from different codebases are not equivalent conditions and must never be pooled.",
      discovery:
        "No control. Exploratory evidence, not experimental evidence — this mode describes, it does not demonstrate.",
      subject_validation:
        "No control, nothing ablated. Reports how reliably the declared subject passed under each harness/model arm — one rate per arm, never pooled, and nothing about the harness.",
      harness_by_model:
        "Every harness level runs on every model. Read the harness effect per model and how it changes from the first model to the last; no two cells are compared on their own, because that would mix the model with the harness."
    } as Record<string, string>,
    notValidated: "Not validated",
    passedValidation: "Passed validation",
    failedValidation: "Failed validation",
    better: "better",
    worse: "worse",
    perCasePerArm: "Per case, per arm",
    perCasePerArmHint:
      "Passed of scored, for every cell. Each arm is read down its own column — the columns are not combined.",
    case: "Case",
    validityTitle: {
      treatment_not_activated: "The treatment never happened",
      infrastructure_failures: "These runs measured the connection",
      no_headroom: "No headroom to detect an improvement",
      objective_unmeasured: "The objective was never measured",
      floor_effect: "Nothing resolved on either side",
      mixed_treatment: "The arm mixes treated and untreated runs",
      design_objection: "The design was objected to when it was created"
    } as Record<string, string>,
    comparisonNotValid: "Comparison not valid",
    readWithCare: "Read with care",
    verdict: "Verdict",
    measured: (observed: string) => `Measured: ${observed}`,
    fixed: (count: number) => `${count} fixed`,
    broken: (count: number) => `${count} broken`,
    unchanged: (count: number) => `${count} unchanged`,
    incomplete: (count: number) => `${count} incomplete`,
    evidence: (evidence: string) => `Evidence: ${evidence}`,
    repetitionHintBasisObserved: (rate: string) =>
      `planned from the noisiest arm's observed rate (${rate})`,
    repetitionHintBasisAssumed:
      "planned from a 50% rate, the worst case, until every arm has 10 scored runs",
    repetitionHint: (
      differencePoints: number,
      repetitionsPerCase: number,
      runsPerArm: number,
      current: number,
      basis: string
    ) => (
      <span>
        <strong>Planning hint:</strong> to detect a {differencePoints}-point
        difference in success (two-sided 5%, 80% power), plan about{" "}
        <strong>{repetitionsPerCase}</strong> repetition
        {repetitionsPerCase === 1 ? "" : "s"} per case per arm (
        {runsPerArm} runs per arm); this design has {current}. It is{" "}
        {basis}, and it ignores what pairing by case saves, so it errs high.
        Advice for the next design, not a judgement on this one.
      </span>
    ),
    metric: "Metric",
    control: "control",
    successRate: "Success rate",
    successRateFact: "fact — validation verdict, 95% interval",
    scored: (scoredRuns: number, runs: number) => `${scoredRuns}/${runs} scored`,
    successesOfScored: (successes: number, scoredRuns: number) =>
      `${successes} of ${scoredRuns}`,
    wilsonTitle:
      "95% Wilson interval: the range of pass rates these runs are compatible with. Descriptive, not a test.",
    passK: "pass^k",
    passKHint: "every one of k tries passes, averaged over cases",
    passAtKTitle: "pass@k: at least one of k tries passes. The gap to pass^k is how much retrying hides.",
    missing: (count: number) => `${count} missing`,
    runThisExperiment: "Run this experiment",
    lockedIntro:
      "Launched and locked: every further round reuses the configuration the existing runs already carry, so one experiment stays one measurement. Want a different model or budget? That is a different measurement — create a new experiment.",
    harness: "Harness",
    executorModel: "Executor model",
    harnessDefault: "harness default",
    plannerModel: "Planner model",
    none: "none",
    promptVariant: "Prompt variant",
    promptVariants: "Prompt variants",
    stepBudget: "Step budget",
    skillsAdded: "Skills added",
    skillsAskedFor: "Skills asked for",
    skillsRemoved: "Skills removed",
    watchThemInRuns: (link: ReactNode) => <>Watch them in {link}.</>,
    runsLink: "Runs",
    queueing: "Queueing…",
    queueAnotherRound: "Queue another round — same configuration",
    noCasesToLaunch: (newEvaluation: ReactNode) => (
      <>
        This experiment has no cases, so there is nothing to launch. Create a
        new one from {newEvaluation} or from the repository&apos;s workbench;
        experiments are immutable once declared.
      </>
    ),
    newEvaluationLink: "New evaluation",
    draftIntro:
      "This experiment is a draft — it has no real runs yet. Shared settings for every arm; the design supplies the rest, and a field the experiment varies is overridden per arm, so nothing picked here can leak into the comparison as a confound.",
    runsHostedAuth: (harness: string) => `Runs the ${harness} CLI with its native auth.`,
    claudeAuthHelp:
      "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host).",
    otherHostHelp: "Needs host mode — run make app-host.",
    promptVariantsFixed: (variants: string) =>
      `Fixed by the design: ${variants} — every arm runs each of them.`,
    promptVariantsUnion:
      "Union of the variants the experiment's cases define. A case without the chosen variant contributes no runs for it.",
    stepBudgetHint: "Defaults to the largest budget any selected case declares.",
    nativeModelsHint: (harness: string) =>
      `Native ${harness} CLI models (passed to --model).`,
    heldFixedHint:
      "Held fixed across every arm — the experiment varies the harness, never the model.",
    dryRun: "Dry run — build the commands without executing them",
    ready: "Ready",
    blocked: "Blocked",
    checking: "Checking…",
    checkConfiguration: "Check configuration",
    launchRuns: "Launch runs",
    unableToCheck: "Unable to check",
    unableToLaunch: "Unable to launch",
    queuedNotice: (count: number) =>
      `Queued ${count} run(s) — every case in every arm, repeated as the design declares. Results fill in here as they finish.`,
    reading: "Reading",
    readingEllipsis: "Reading…",
    readAgain: "Read again",
    readThisExperiment: "Read this experiment",
    readyToReadTitle:
      "An LLM reads the summary's evidence and writes what was compared, what the result per arm, what each arm read and invoked, and why the comparison can or cannot be trusted — in plain language.",
    notYetReadableTitle: "Available once every run has stopped.",
    noReadingYetFinished:
      "No reading yet. Ask for one to get the verdict, the result per arm, what each arm read and invoked, and why the comparison can or cannot be trusted — in plain language.",
    noReadingYetUnfinished: "The reading becomes available once every run has stopped.",
    readingWith: (model: string) => `Reading with ${model}…`,
    theDefaultModel: "the default model",
    readingFailed: "The reading failed",
    unableToAudit: "Unable to audit",
    hideEvidence: "hide the evidence it was given",
    showEvidence: "show the evidence it was given",
    abandon: "Abandon",
    abandonTitle:
      "Hide from the list. Its runs stay in the corpus — this is 'stop showing me this', not 'this never happened'.",
    deleteTitleWithRuns: (count: number) =>
      `Erase this experiment and its ${count} run(s) — artifacts and scores leave every dashboard.`,
    deleteTitleDraft: "Erase this draft.",
    delete: "Delete",
    repetitionsSuffix: (count: number) => `${count} repetition${count === 1 ? "" : "s"} per arm`,
    subjectUnderTest: (subject: ReactNode) => <>Subject under test: {subject}</>,
    conditions: "Conditions",
    deltaTitle: "Δ",
    beyondNoise: "Beyond noise?",
    unknown: "unknown",
    yes: "yes",
    noWithinVariance: "no — within run-to-run variance",
    caseMatrix: "Case matrix",
    caseMatrixHint:
      'Where the treatment helps, and where it does not. An average of "+8 points" can be "fixed four cases, broke two" — only this says which.',
    successRateMove: (control: string, treatment: string) =>
      `Success rate ${control} → ${treatment}`,
    pp: " pp",
    pp95Title: "95% interval for the delta, resampled by case and then by repetition. Descriptive, not a test.",
    interval95Label: ", 95% interval ",
    crossesZero:
      "The interval includes zero: these runs are also compatible with no effect on success. More repetitions narrow it; the interval describes the runs, it does not test them.",
    vs: (treatment: string, control: string) => `${treatment} vs ${control}`,
    movementLabel: {
      fixed: "fixed",
      broken: "broken",
      unchanged: "unchanged",
      incomplete: "incomplete"
    } as Record<string, string>,
    experimentsTitle: "Experiments",
    experimentsLede: "A stated question, declared arms, and the runs that answer it.",
    helpTitle: "What an experiment is, and what a batch is not",
    helpParagraph1: (
      <>
        An experiment states its <strong>question</strong> before anything runs, and
        declares its arms up front — a control and its treatments, or, for subject
        validation, peer arms with no control. The lab
        checks the design against the mode and refuses one that cannot answer the
        question it claims to: a comparison that varies both the harness and the
        repository produces a number that cannot be attributed to either.
      </>
    ),
    helpParagraph2:
      "A batch is still the right tool for an ad-hoc sweep. The difference is that an experiment makes a claim, so it has to earn it.",
    familyLabel: (familyId: string) => (
      <>
        Family <code>{familyId}</code>{" "}
      </>
    ),
    showAll: "show all",
    allExperiments: "All experiments",
    showAbandoned: (count: number) => `Show abandoned (${count})`,
    evaluateHarnessChange: "Evaluate a harness change",
    auditRepository: "Audit a repository",
    crossRepoEvaluation: "Cross-repo evaluation",
    noExperimentsDescription:
      "Start by choosing what you want to learn. An experiment needs a question, a mode, cases and at least one arm.",
    noExperimentsTitle: "No experiments yet.",
    id: "Id",
    question: "Question",
    mode: "Mode",
    arms: "Arms",
    runs: "Runs",
    reps: "Reps",
    familyButton: (familyId: string) => `family ${familyId}`,
    familyButtonTitle: "Read this experiment with the rest of its ladder",
    exploratory: "exploratory",
    judgement: "judgement",
    hide: "Hide",
    open: "Open",
    unableToLoadExperiments: "Unable to load experiments",
    unableToSummarize: "Unable to summarize experiment",
    unableToAbandon: "Unable to abandon",
    unableToDelete: "Unable to delete",
    deleteConfirmWithRuns: (experimentId: string, count: number) =>
      `Delete "${experimentId}" AND its ${count} run(s)? Their artifacts and scores disappear from every dashboard and aggregate. This cannot be undone.`,
    deleteConfirmDraft: (experimentId: string) =>
      `Delete the draft "${experimentId}"? This cannot be undone.`,
    experimentStatus: {
      draft: "draft",
      running: "running",
      complete: "complete",
      abandoned: "abandoned"
    } as Record<string, string>
  },
  es: {
    modeLabel: {
      harness_evaluation: "Evaluación de arnés",
      cross_repo: "Entre repositorios",
      discovery: "Exploración",
      subject_validation: "Validación de sujeto",
      harness_by_model: "Arnés x modelo"
    } as Record<string, string>,
    modeCaveat: {
      harness_evaluation:
        "Repositorio, caso y modelo se mantienen fijos; el arnés varía. El único modo que admite una afirmación causal — y solo con repeticiones.",
      cross_repo:
        "Arnés y modelo se mantienen fijos; el repositorio varía. Se lee por repositorio: los puntajes de códigos distintos no son condiciones equivalentes y nunca deben agruparse.",
      discovery:
        "Sin control. Evidencia exploratoria, no experimental — este modo describe, no demuestra.",
      subject_validation:
        "Sin control, nada ablacionado. Reporta cuán confiablemente pasó el sujeto declarado bajo cada brazo de arnés/modelo — una tasa por brazo, nunca agrupadas, y nada sobre el arnés.",
      harness_by_model:
        "Cada nivel de arnés corre en cada modelo. Se lee el efecto del arnés por modelo y cómo cambia del primer modelo al último; no se comparan dos celdas por sí solas, porque eso mezclaría el modelo con el arnés."
    } as Record<string, string>,
    notValidated: "No validado",
    passedValidation: "Pasó la validación",
    failedValidation: "Falló la validación",
    better: "mejor",
    worse: "peor",
    perCasePerArm: "Por caso, por brazo",
    perCasePerArmHint:
      "Pasados sobre puntuados, para cada celda. Cada brazo se lee por su propia columna — las columnas no se combinan.",
    case: "Caso",
    validityTitle: {
      treatment_not_activated: "El tratamiento nunca ocurrió",
      infrastructure_failures: "Estas corridas midieron la conexión",
      no_headroom: "Sin margen para detectar una mejora",
      objective_unmeasured: "El objetivo nunca se midió",
      floor_effect: "Nada se resolvió en ninguno de los dos lados",
      mixed_treatment: "El brazo mezcla corridas tratadas y no tratadas",
      design_objection: "El diseño fue objetado cuando se creó"
    } as Record<string, string>,
    comparisonNotValid: "La comparación no es válida",
    readWithCare: "Leé con cuidado",
    verdict: "Veredicto",
    measured: (observed: string) => `Medido: ${observed}`,
    fixed: (count: number) => `${count} arreglado${count === 1 ? "" : "s"}`,
    broken: (count: number) => `${count} roto${count === 1 ? "" : "s"}`,
    unchanged: (count: number) => `${count} sin cambios`,
    incomplete: (count: number) => `${count} incompleto${count === 1 ? "" : "s"}`,
    evidence: (evidence: string) => `Evidencia: ${evidence}`,
    repetitionHintBasisObserved: (rate: string) =>
      `planificado a partir de la tasa observada del brazo más ruidoso (${rate})`,
    repetitionHintBasisAssumed:
      "planificado a partir de una tasa del 50%, el peor caso, hasta que cada brazo tenga 10 corridas puntuadas",
    repetitionHint: (
      differencePoints: number,
      repetitionsPerCase: number,
      runsPerArm: number,
      current: number,
      basis: string
    ) => (
      <span>
        <strong>Sugerencia de planificación:</strong> para detectar una
        diferencia de {differencePoints} puntos en el éxito (dos colas 5%,
        potencia 80%), planificá unas <strong>{repetitionsPerCase}</strong>{" "}
        repetición{repetitionsPerCase === 1 ? "" : "es"} por caso y por brazo
        ({runsPerArm} corridas por brazo); este diseño tiene {current}. Está{" "}
        {basis}, e ignora lo que ahorra el pareo por caso, así que se equivoca
        por exceso. Es un consejo para el próximo diseño, no un juicio sobre
        este.
      </span>
    ),
    metric: "Métrica",
    control: "control",
    successRate: "Tasa de éxito",
    successRateFact: "hecho — veredicto de validación, intervalo del 95%",
    scored: (scoredRuns: number, runs: number) => `${scoredRuns}/${runs} puntuadas`,
    successesOfScored: (successes: number, scoredRuns: number) =>
      `${successes} de ${scoredRuns}`,
    wilsonTitle:
      "Intervalo de Wilson del 95%: el rango de tasas de éxito con el que estas corridas son compatibles. Descriptivo, no una prueba.",
    passK: "pass^k",
    passKHint: "todos los k intentos pasan, promediado sobre los casos",
    passAtKTitle:
      "pass@k: al menos uno de k intentos pasa. La brecha con pass^k es cuánto esconde el reintentar.",
    missing: (count: number) => `${count} faltante${count === 1 ? "" : "s"}`,
    runThisExperiment: "Correr este experimento",
    lockedIntro:
      "Lanzado y bloqueado: cada ronda adicional reusa la configuración que ya llevan las corridas existentes, así que un experimento sigue siendo una sola medición. ¿Querés otro modelo o presupuesto? Esa es otra medición — creá un nuevo experimento.",
    harness: "Arnés",
    executorModel: "Modelo ejecutor",
    harnessDefault: "por defecto del arnés",
    plannerModel: "Modelo planificador",
    none: "ninguno",
    promptVariant: "Variante de prompt",
    promptVariants: "Variantes de prompt",
    stepBudget: "Presupuesto de pasos",
    skillsAdded: "Skills agregadas",
    skillsAskedFor: "Skills pedidas",
    skillsRemoved: "Skills quitadas",
    watchThemInRuns: (link: ReactNode) => <>Mirálas en {link}.</>,
    runsLink: "Corridas",
    queueing: "Encolando…",
    queueAnotherRound: "Encolar otra ronda — misma configuración",
    noCasesToLaunch: (newEvaluation: ReactNode) => (
      <>
        Este experimento no tiene casos, así que no hay nada para lanzar. Creá
        uno nuevo desde {newEvaluation} o desde el banco de trabajo del
        repositorio; los experimentos son inmutables una vez declarados.
      </>
    ),
    newEvaluationLink: "Nueva evaluación",
    draftIntro:
      "Este experimento es un borrador — todavía no tiene corridas reales. Configuración compartida para cada brazo; el diseño provee el resto, y un campo que el experimento varía se reemplaza por brazo, así que nada elegido acá puede filtrarse a la comparación como un confundidor.",
    runsHostedAuth: (harness: string) => `Corre la CLI de ${harness} con su autenticación nativa.`,
    claudeAuthHelp:
      "Funciona en Docker (CLAUDE_CODE_OAUTH_TOKEN o ANTHROPIC_API_KEY en .env) o en modo host (make app-host).",
    otherHostHelp: "Necesita modo host — corré make app-host.",
    promptVariantsFixed: (variants: string) =>
      `Fijado por el diseño: ${variants} — cada brazo corre cada una de ellas.`,
    promptVariantsUnion:
      "Unión de las variantes que definen los casos del experimento. Un caso sin la variante elegida no aporta corridas para ella.",
    stepBudgetHint: "Por defecto, el presupuesto más grande que declare algún caso elegido.",
    nativeModelsHint: (harness: string) =>
      `Modelos nativos de la CLI de ${harness} (pasados a --model).`,
    heldFixedHint:
      "Se mantiene fijo en cada brazo — el experimento varía el arnés, nunca el modelo.",
    dryRun: "Ensayo en seco — arma los comandos sin ejecutarlos",
    ready: "Listo",
    blocked: "Bloqueado",
    checking: "Revisando…",
    checkConfiguration: "Revisar configuración",
    launchRuns: "Lanzar corridas",
    unableToCheck: "No se pudo revisar",
    unableToLaunch: "No se pudo lanzar",
    queuedNotice: (count: number) =>
      `Se encolaron ${count} corrida(s) — cada caso en cada brazo, repetido como declara el diseño. Los resultados se van completando acá a medida que terminan.`,
    reading: "Lectura",
    readingEllipsis: "Leyendo…",
    readAgain: "Leer de nuevo",
    readThisExperiment: "Leer este experimento",
    readyToReadTitle:
      "Un LLM lee la evidencia del resumen y escribe qué se comparó, el resultado por brazo, qué leyó e invocó cada brazo, y por qué la comparación se puede o no confiar — en lenguaje claro.",
    notYetReadableTitle: "Disponible cuando todas las corridas hayan terminado.",
    noReadingYetFinished:
      "Todavía no hay lectura. Pedí una para obtener el veredicto, el resultado por brazo, qué leyó e invocó cada brazo, y por qué se puede o no confiar en la comparación — en lenguaje claro.",
    noReadingYetUnfinished: "La lectura estará disponible cuando todas las corridas hayan terminado.",
    readingWith: (model: string) => `Leyendo con ${model}…`,
    theDefaultModel: "el modelo por defecto",
    readingFailed: "La lectura falló",
    unableToAudit: "No se pudo auditar",
    hideEvidence: "ocultar la evidencia que recibió",
    showEvidence: "mostrar la evidencia que recibió",
    abandon: "Abandonar",
    abandonTitle:
      "Ocultar de la lista. Sus corridas se mantienen en el corpus — esto es 'dejá de mostrarme esto', no 'esto nunca pasó'.",
    deleteTitleWithRuns: (count: number) =>
      `Borrar este experimento y sus ${count} corrida(s) — los artefactos y puntajes desaparecen de todos los tableros.`,
    deleteTitleDraft: "Borrar este borrador.",
    delete: "Borrar",
    repetitionsSuffix: (count: number) => `${count} repetición${count === 1 ? "" : "es"} por brazo`,
    subjectUnderTest: (subject: ReactNode) => <>Sujeto bajo prueba: {subject}</>,
    conditions: "Condiciones",
    deltaTitle: "Δ",
    beyondNoise: "¿Más allá del ruido?",
    unknown: "desconocido",
    yes: "sí",
    noWithinVariance: "no — dentro de la varianza entre corridas",
    caseMatrix: "Matriz de casos",
    caseMatrixHint:
      'Dónde ayuda el tratamiento y dónde no. Un promedio de "+8 puntos" puede ser "arregló cuatro casos, rompió dos" — solo esto dice cuáles.',
    successRateMove: (control: string, treatment: string) =>
      `Tasa de éxito ${control} → ${treatment}`,
    pp: " pp",
    pp95Title:
      "Intervalo del 95% para la diferencia, remuestreado por caso y luego por repetición. Descriptivo, no una prueba.",
    interval95Label: ", intervalo del 95% ",
    crossesZero:
      "El intervalo incluye el cero: estas corridas también son compatibles con ningún efecto sobre el éxito. Más repeticiones lo achican; el intervalo describe las corridas, no las pone a prueba.",
    vs: (treatment: string, control: string) => `${treatment} vs ${control}`,
    movementLabel: {
      fixed: "arreglado",
      broken: "roto",
      unchanged: "sin cambios",
      incomplete: "incompleto"
    } as Record<string, string>,
    experimentsTitle: "Experimentos",
    experimentsLede: "Una pregunta declarada, brazos declarados, y las corridas que la responden.",
    helpTitle: "Qué es un experimento, y qué no es un batch",
    helpParagraph1: (
      <>
        Un experimento declara su <strong>pregunta</strong> antes de que nada
        corra, y declara sus brazos de entrada — un control y sus
        tratamientos, o, para validación de sujeto, brazos pares sin control.
        El lab revisa el diseño contra el modo y rechaza uno que no pueda
        responder la pregunta que dice responder: una comparación que varía
        tanto el arnés como el repositorio produce un número que no se puede
        atribuir a ninguno de los dos.
      </>
    ),
    helpParagraph2:
      "Un batch sigue siendo la herramienta correcta para un barrido ad-hoc. La diferencia es que un experimento hace una afirmación, así que tiene que ganársela.",
    familyLabel: (familyId: string) => (
      <>
        Familia <code>{familyId}</code>{" "}
      </>
    ),
    showAll: "mostrar todas",
    allExperiments: "Todos los experimentos",
    showAbandoned: (count: number) => `Mostrar abandonados (${count})`,
    evaluateHarnessChange: "Evaluar un cambio de arnés",
    auditRepository: "Auditar un repositorio",
    crossRepoEvaluation: "Evaluación entre repositorios",
    noExperimentsDescription:
      "Empezá eligiendo qué querés aprender. Un experimento necesita una pregunta, un modo, casos y al menos un brazo.",
    noExperimentsTitle: "Todavía no hay experimentos.",
    id: "Id",
    question: "Pregunta",
    mode: "Modo",
    arms: "Brazos",
    runs: "Corridas",
    reps: "Reps",
    familyButton: (familyId: string) => `familia ${familyId}`,
    familyButtonTitle: "Leer este experimento junto con el resto de su escalera",
    exploratory: "exploratorio",
    judgement: "juicio",
    hide: "Ocultar",
    open: "Abrir",
    unableToLoadExperiments: "No se pudieron cargar los experimentos",
    unableToSummarize: "No se pudo resumir el experimento",
    unableToAbandon: "No se pudo abandonar",
    unableToDelete: "No se pudo borrar",
    deleteConfirmWithRuns: (experimentId: string, count: number) =>
      `¿Borrar "${experimentId}" Y sus ${count} corrida(s)? Sus artefactos y puntajes desaparecen de todos los tableros y agregados. Esto no se puede deshacer.`,
    deleteConfirmDraft: (experimentId: string) =>
      `¿Borrar el borrador "${experimentId}"? Esto no se puede deshacer.`,
    experimentStatus: {
      draft: "borrador",
      running: "corriendo",
      complete: "completo",
      abandoned: "abandonado"
    } as Record<string, string>
  }
});

function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return Number(value.toPrecision(digits)).toString();
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

/**
 * "[23–88]" — a rate's 95% interval in percent, or nothing when there is none
 * (ADR-0064). Shown beside the rate, never instead of it.
 */
function rateInterval(
  low: number | null | undefined,
  high: number | null | undefined
): string | null {
  if (low === null || low === undefined || high === null || high === undefined) {
    return null;
  }
  return `[${(low * 100).toFixed(0)}–${(high * 100).toFixed(0)}]`;
}

/** "+12", "-8" and "0" for a bound in points — never "-0". */
function signedPoints(value: number): string {
  const rounded = Math.round(value);
  if (rounded === 0) {
    return "0";
  }
  return rounded > 0 ? `+${rounded}` : `${rounded}`;
}

/** "[-12, +52] pp" for a success delta, or nothing for an older backend. */
function deltaInterval(success: SuccessDelta): string | null {
  const { delta_ci_low: low, delta_ci_high: high } = success;
  if (low === null || low === undefined || high === null || high === undefined) {
    return null;
  }
  return `[${signedPoints(low)}, ${signedPoints(high)}] pp`;
}

/** A mean is never shown without its spread. */
function spread(aggregate: AggregateRecord | undefined): string {
  if (!aggregate || aggregate.mean === null) {
    return "—";
  }
  if (aggregate.stdev === null) {
    return formatNumber(aggregate.mean);
  }
  return `${formatNumber(aggregate.mean)} ± ${formatNumber(aggregate.stdev, 2)}`;
}

function deltaLabel(delta: DeltaRecord): string {
  if (delta.absolute === null) {
    return "—";
  }
  const sign = delta.absolute > 0 ? "+" : "";
  return `${sign}${formatNumber(delta.absolute)}`;
}

/**
 * Improvement is read from the metric's declared direction, never from the sign.
 * A drop in `cost_usd` is a win; a drop in `test_pass_rate` is not.
 */
function deltaClass(delta: DeltaRecord): string {
  if (delta.absolute === null || delta.direction === "neutral") {
    return "";
  }
  const better =
    delta.direction === "higher_is_better" ? delta.absolute > 0 : delta.absolute < 0;
  return better ? "delta-better" : "delta-worse";
}

/** A pass/fail cell, as a shape rather than an emoji that renders differently everywhere. */
function Outcome({ value }: { value: boolean | null }) {
  const m = useMessages(MESSAGES);
  if (value === null) {
    return <span className="outcome outcome--unknown" title={m.notValidated}>·</span>;
  }
  return (
    <span
      className={`outcome outcome--${value ? "pass" : "fail"}`}
      title={value ? m.passedValidation : m.failedValidation}
    >
      {value ? <IconCheck size={15} /> : <IconX size={15} />}
    </span>
  );
}

const MOVEMENT_CLASS: Record<string, string> = {
  fixed: "movement movement--fixed",
  broken: "movement movement--broken",
  unchanged: "movement movement--unchanged",
  incomplete: "movement movement--incomplete"
};

/**
 * The ten-second answer, at the top of the page.
 *
 * Everything here comes from the backend's own verdict: what the experiment is
 * allowed to claim follows from its mode and its repetition count, and that is
 * not a judgement a template should be making. The wording is deliberately
 * cautious — "likely improvement", "within observed variance" — because this
 * lab does not run hypothesis tests and must not sound as though it does.
 */
/** A rate reads as a percentage, a cost as money, everything else as a number. */
/**
 * ▲ / ▼ with a good/bad colour, read from the registry direction the backend
 * attaches. "10,712 → 7,223 tokens" is an improvement for a cost and a
 * regression for a rate; without this the reader supplied the direction.
 */
function highlightMove(highlight: VerdictHighlight) {
  const m = pickMessages(MESSAGES);
  const { control, treatment, direction } = highlight;
  if (
    control === null ||
    control === undefined ||
    treatment === null ||
    treatment === undefined ||
    !direction ||
    direction === "neutral" ||
    control === treatment
  ) {
    return null;
  }
  const up = treatment > control;
  const better = direction === "higher_is_better" ? up : !up;
  return (
    <span
      className={`highlight-move ${better ? "highlight-move--better" : "highlight-move--worse"}`}
      title={better ? m.better : m.worse}
    >
      {up ? "▲" : "▼"}
    </span>
  );
}

function formatHighlight(metric: string, value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (metric.endsWith("_rate") || metric.endsWith("_ratio")) {
    return formatPercent(value);
  }
  if (metric.includes("cost") || metric.endsWith("_usd")) {
    return `$${value.toFixed(2)}`;
  }
  return formatNumber(value);
}

/**
 * The per-arm, per-case grid for a mode with no control: counts per cell, with
 * the repetitions kept. There is no movement column because there is nothing
 * to move relative to.
 */
function CaseOutcomeGrid({
  outcomes
}: {
  outcomes: Record<string, Record<string, CaseOutcomeCell>>;
}) {
  const m = useMessages(MESSAGES);
  const labels = Object.keys(outcomes);
  const caseIds = [
    ...new Set(labels.flatMap((label) => Object.keys(outcomes[label] ?? {})))
  ];
  if (caseIds.length === 0) {
    return null;
  }
  return (
    <div className="card">
      <h3>{m.perCasePerArm}</h3>
      <p className="muted small">{m.perCasePerArmHint}</p>
      <table className="data-table">
        <thead>
          <tr>
            <th>{m.case}</th>
            {labels.map((label) => (
              <th key={label}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {caseIds.map((caseId) => (
            <tr key={caseId}>
              <td className="mono small">{caseId}</td>
              {labels.map((label) => {
                const cell = outcomes[label]?.[caseId];
                return (
                  <td key={label}>
                    {!cell || cell.scored_runs === 0
                      ? "—"
                      : m.successesOfScored(cell.successes, cell.scored_runs)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Read before the verdict (ADR-0046).
 *
 * Deliberately its own card above the verdict rather than another line in
 * `verdict.caveats`: a caveat printed under a confident headline is exactly
 * how this lab published three comparisons that could not have measured what
 * they named.
 */
function ValidityBanner({ validity }: { validity: ValidityBlock }) {
  const m = useMessages(MESSAGES);
  if (validity.status === "valid" || validity.issues.length === 0) return null;
  const invalid = validity.status === "invalid";
  return (
    <div className={`card validity ${invalid ? "validity--invalid" : "validity--degraded"}`}>
      <div className="row space-between">
        <p className="verdict-kicker">
          {invalid ? m.comparisonNotValid : m.readWithCare}
        </p>
        <span className={`badge ${invalid ? "error" : "warn"}`}>{validity.status}</span>
      </div>
      <ul className="validity-issues">
        {validity.issues.map((issue) => (
          <li key={`${issue.code}:${issue.arm ?? ""}:${issue.detail}`}>
            <strong>
              <IconAlert size={14} /> {m.validityTitle[issue.code] ?? issue.code}
            </strong>
            <p>{issue.detail}</p>
            {issue.remedy ? <p className="muted">{issue.remedy}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerdictCard({ verdict }: { verdict: VerdictBlock }) {
  const m = useMessages(MESSAGES);
  // `tone` is authoritative since ADR-0046; the headline sniff below stays one
  // release longer so a summary from an older backend still renders in colour.
  const headline = verdict.headline.toLowerCase();
  const tone =
    verdict.tone ??
    (headline.includes("improvement")
      ? "better"
      : headline.includes("regression")
        ? "worse"
        : "neutral");
  const Trend =
    tone === "better" ? IconTrendUp : tone === "worse" ? IconTrendDown : IconMinus;
  const toneClass =
    tone === "better"
      ? "verdict--better"
      : tone === "worse"
        ? "verdict--worse"
        : "verdict--neutral";

  return (
    <div className={`card verdict ${toneClass}`}>
      <div className="verdict-head">
        <span className="verdict-icon">
          <Trend size={22} />
        </span>
        <div>
          <p className="verdict-kicker">{m.verdict}</p>
          <h2>{verdict.headline}</h2>
          <p className="muted">{verdict.claim}</p>
          {verdict.observed ? (
            /* The measurement is demoted, never withheld (ADR-0041). */
            <p className="muted verdict-observed">{m.measured(verdict.observed)}</p>
          ) : null}
        </div>
        <span className="badge neutral verdict-strength">{verdict.strength}</span>
      </div>

      {verdict.highlights.length > 0 ? (
        <div className="verdict-highlights">
          {verdict.highlights.map((highlight) => (
            <div
              className="verdict-highlight"
              key={`${highlight.label}:${highlight.metric}`}
            >
              <span>{highlight.label}</span>
              <strong>
                {highlight.scored_runs === undefined ? (
                  <>
                    {formatHighlight(highlight.metric, highlight.control)} →{" "}
                    {formatHighlight(highlight.metric, highlight.treatment)}
                    {highlightMove(highlight)}
                  </>
                ) : (
                  /* One arm on its own: a rate with nothing to compare it
                     against, so it is shown with the counts behind it rather
                     than as an arrow between two numbers. */
                  <>
                    {formatHighlight(highlight.metric, highlight.rate)}{" "}
                    <span className="muted">
                      ({m.successesOfScored(highlight.successes ?? 0, highlight.scored_runs ?? 0)})
                    </span>
                  </>
                )}
              </strong>
            </div>
          ))}
        </div>
      ) : null}

      <div className="verdict-movements">
        <span className="movement movement--fixed">{m.fixed(verdict.movements.fixed)}</span>
        <span className="movement movement--broken">
          {m.broken(verdict.movements.broken)}
        </span>
        <span className="movement movement--unchanged">
          {m.unchanged(verdict.movements.unchanged)}
        </span>
        {verdict.movements.incomplete > 0 ? (
          <span className="movement movement--incomplete">
            {m.incomplete(verdict.movements.incomplete)}
          </span>
        ) : null}
      </div>

      <p className="verdict-evidence muted">{m.evidence(verdict.evidence)}</p>

      {verdict.caveats.length > 0 ? (
        <ul className="warning-list">
          {verdict.caveats.map((caveat) => (
            <li key={caveat}>
              <IconAlert size={14} /> {caveat}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * How many repetitions a 20-point move would need — advice for the next design,
 * never a verdict on this one (ADR-0064). Planned from a coin flip until every
 * arm has enough scored runs to plan from its own rate.
 */
function RepetitionHint({ plan, current }: { plan: RepetitionPlan; current: number }) {
  const m = useMessages(MESSAGES);
  const basis =
    plan.basis === "observed"
      ? m.repetitionHintBasisObserved(formatPercent(plan.base_rate))
      : m.repetitionHintBasisAssumed;
  return (
    <p className="callout">
      {m.repetitionHint(
        plan.difference_points,
        plan.repetitions_per_case,
        plan.runs_per_arm,
        current,
        basis
      )}
    </p>
  );
}

function ConditionTable({ conditions }: { conditions: ConditionSummary[] }) {
  const m = useMessages(MESSAGES);
  const metricNames = [
    ...new Set(conditions.flatMap((condition) => Object.keys(condition.metrics)))
  ].sort();

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>{m.metric}</th>
          {conditions.map((condition) => (
            <th key={condition.label}>
              {condition.label}
              {condition.role === "control" && <span className="badge"> {m.control}</span>}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <strong>{m.successRate}</strong>
            <div className="muted small">{m.successRateFact}</div>
          </td>
          {conditions.map((condition) => {
            const interval = rateInterval(
              condition.success_rate_ci_low,
              condition.success_rate_ci_high
            );
            return (
              <td key={condition.label}>
                {formatPercent(condition.success_rate)}
                {interval ? (
                  <span className="muted small" title={m.wilsonTitle}>
                    {" "}
                    {interval}
                  </span>
                ) : null}
                <div className="muted small">
                  {m.scored(condition.scored_runs, condition.runs)}
                </div>
              </td>
            );
          })}
        </tr>
        {conditions.some((condition) => condition.pass_k) ? (
          <tr>
            <td>
              <strong>{m.passK}</strong>
              <div className="muted small">{m.passKHint}</div>
            </td>
            {conditions.map((condition) => (
              <td key={condition.label}>
                {condition.pass_k ? (
                  <>
                    {formatPercent(condition.pass_k.pass_hat_k)}
                    <div className="muted small" title={m.passAtKTitle}>
                      k={condition.pass_k.k} · pass@k{" "}
                      {formatPercent(condition.pass_k.pass_at_k)}
                    </div>
                  </>
                ) : (
                  "—"
                )}
              </td>
            ))}
          </tr>
        ) : null}
        {metricNames.map((name) => (
          <tr key={name}>
            <td className="mono small">{name}</td>
            {conditions.map((condition) => (
              <td className="mono small" key={condition.label}>
                {spread(condition.metrics[name])}
                {condition.metrics[name]?.missing ? (
                  <div className="muted small">{m.missing(condition.metrics[name].missing)}</div>
                ) : null}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Turn a declared design into runs.
 *
 * The arms and repetitions are already fixed by the experiment — this only
 * supplies what every arm shares. Anything the design varies is overridden per
 * arm afterwards, so a model typed here never silently becomes a confound.
 */
/**
 * Turn a declared design into runs — with the run form's own controls.
 *
 * The first version of this panel asked for the model and harness as bare text
 * and hardcoded a score_mode the API refuses, so the one button on it produced
 * a validation error and nothing else. Everything here is now the same
 * machinery the Runs form uses: the three-tier model picker, the case's own
 * prompt variants, the preflight check, and the one score mode the web API
 * accepts.
 *
 * The arms and repetitions stay fixed by the experiment — this only supplies
 * what every arm shares. Anything the design varies is overridden per arm
 * afterwards, so a value picked here never becomes a confound.
 */
function LaunchPanel({
  experimentId,
  onLaunched
}: {
  experimentId: string;
  onLaunched: () => void;
}) {
  const m = useMessages(MESSAGES);
  const [caseIds, setCaseIds] = useState<string[]>([]);
  // Set once the experiment has real runs: the backend refuses a relaunch
  // that differs, so the panel stops asking and just offers another round.
  const [locked, setLocked] = useState<LaunchConfigSummary | null>(null);
  // Variants pinned by the design itself; when present the select disappears.
  const [designVariants, setDesignVariants] = useState<string[]>([]);
  // Union across the experiment's cases — same rule as batch mode: a case
  // without the chosen variant contributes no runs for it, but an intersection
  // would go empty as soon as one case is unusual.
  const [variantOptions, setVariantOptions] = useState<string[]>([]);
  const [promptVariant, setPromptVariant] = useState("sdlc");
  const [harness, setHarness] = useState("opencode");
  const [plannerModel, setPlannerModel] = useState("");
  const [executorModel, setExecutorModel] = useState("");
  const [gatewayDefault, setGatewayDefault] = useState("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
  const [reloadingModels, setReloadingModels] = useState(false);
  const [maxSteps, setMaxSteps] = useState(10);
  const [stepOptions, setStepOptions] = useState<number[]>([3, 6, 10]);
  const [dryRun, setDryRun] = useState(false);
  const [check, setCheck] = useState<RunValidationResult | null>(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadModels = (refresh = false) => {
    setReloadingModels(refresh);
    return api
      .listModels(refresh)
      .then((catalog) => {
        setModelOptions(catalog.models);
        setDirectErrors(catalog.direct_errors ?? {});
        if (catalog.default_executor) {
          setGatewayDefault(catalog.default_executor);
          // Adopt the default only while nothing was picked yet, and only for
          // gateway harnesses — never clobber a choice the operator made.
          setExecutorModel((current) =>
            current === "" && !HARNESS_NATIVE_MODELS[harness]
              ? catalog.default_executor
              : current
          );
        }
        return catalog;
      })
      .finally(() => setReloadingModels(false));
  };

  useEffect(() => {
    let active = true;
    void loadModels().catch(() => {
      // Gateway/API offline — the pickers show their empty-state guidance.
    });
    Promise.allSettled([api.getExperiment(experimentId), api.listCases()]).then(
      ([experimentResult, casesResult]) => {
        if (!active) {
          return;
        }
        const ids =
          experimentResult.status === "fulfilled"
            ? experimentResult.value.case_ids
            : [];
        setCaseIds(ids);
        if (experimentResult.status === "fulfilled") {
          setDesignVariants(experimentResult.value.prompt_variants ?? []);
          setLocked(experimentResult.value.launch_config ?? null);
        }
        if (casesResult.status === "fulfilled") {
          const mine = casesResult.value.filter((item) =>
            ids.includes(item.case_id)
          );
          const names = new Set<string>();
          const budgets = new Set<number>([3, 6, 10]);
          for (const item of mine) {
            for (const variant of Object.keys(item.prompt_variants ?? {})) {
              names.add(variant);
            }
            if (item.max_steps) {
              budgets.add(item.max_steps);
            }
          }
          const sortedNames = [...names].sort();
          setVariantOptions(sortedNames);
          // Prefer the un-doctored baseline of whichever family the cases are
          // from: "sdlc" for the lab's own cases, "swe_default" for imported
          // SWE-bench instances. Alphabetical order would land on
          // "swe_conductor", which is a treatment prompt, not a default.
          const preferred = ["sdlc", "swe_default"].find((name) =>
            sortedNames.includes(name)
          );
          if (sortedNames.length > 0) {
            setPromptVariant(preferred ?? sortedNames[0]);
          }
          const sortedBudgets = [...budgets].sort((a, b) => a - b);
          setStepOptions(sortedBudgets);
          // Default to the largest budget any of the cases declares: an
          // experiment mixing a 6-step canary with a 40-step task has to fund
          // the 40-step task, or its arm fails for reasons that are not the
          // treatment.
          setMaxSteps(sortedBudgets[sortedBudgets.length - 1]);
        }
      }
    );
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId]);

  // A verdict only describes the configuration that produced it, so any edit
  // voids it rather than leaving a stale green tick above the Launch button.
  useEffect(() => {
    setCheck(null);
  }, [promptVariant, harness, plannerModel, executorModel, maxSteps, dryRun]);

  const nativeIndex = useNativeModelIndex();
  const handleHarnessChange = (next: string) => {
    setHarness(next);
    // The first native model the gateway serves, never one it would refuse.
    setExecutorModel(defaultNativeModel(next, nativeIndex) ?? gatewayDefault);
  };

  const buildConfig = (caseId: string): RunConfigPayload => ({
    case_id: caseId,
    prompt_variant: promptVariant,
    harness,
    planner_model: plannerModel || null,
    executor_model: executorModel || null,
    // The one mode the web API accepts. Runs with objective validation score
    // themselves from it; the rest land in awaiting_manual_score.
    score_mode: "manual",
    max_steps: maxSteps,
    resume: false,
    dry_run: dryRun
  });

  const handleCheck = async () => {
    setChecking(true);
    setError(null);
    setCheck(null);
    try {
      setCheck(await api.preflightRunConfig(buildConfig(caseIds[0] ?? "")));
    } catch (checkError) {
      setError(
        checkError instanceof Error ? checkError.message : m.unableToCheck
      );
    } finally {
      setChecking(false);
    }
  };

  const launch = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.launchExperiment(
        experimentId,
        // A locked experiment relaunches with exactly what it already ran —
        // the backend refuses anything else, and asking again in a form is
        // how two measurements end up filed under one name.
        locked
          ? {
              case_id: caseIds[0] ?? "",
              prompt_variant: locked.prompt_variant,
              harness: locked.harness,
              planner_model: locked.planner_model,
              executor_model: locked.executor_model,
              score_mode: "manual",
              max_steps: locked.max_steps,
              resume: false,
              dry_run: false,
              // Dropping these turns a subject-validation relaunch into a
              // config mismatch the backend refuses.
              skills_disabled: locked.skills_disabled ?? [],
              skills_extra: locked.skills_extra ?? [],
              skills_invoke: locked.skills_invoke ?? []
            }
          : buildConfig(caseIds[0] ?? "")
      );
      setNotice(m.queuedNotice(result.count));
      onLaunched();
    } catch (launchError) {
      setError(
        launchError instanceof Error ? launchError.message : m.unableToLaunch
      );
    } finally {
      setBusy(false);
    }
  };

  if (locked) {
    return (
      <div className="card">
        <h3>{m.runThisExperiment}</h3>
        <p className="muted">{m.lockedIntro}</p>
        <div className="detail-grid">
          <div>
            <span>{m.harness}</span>
            <strong>{locked.harness}</strong>
          </div>
          <div>
            <span>{m.executorModel}</span>
            <strong>{locked.executor_model ?? m.harnessDefault}</strong>
          </div>
          <div>
            <span>{m.plannerModel}</span>
            <strong>{locked.planner_model ?? m.none}</strong>
          </div>
          <div>
            <span>{m.promptVariant}</span>
            <strong>{locked.prompt_variant}</strong>
          </div>
          <div>
            <span>{m.stepBudget}</span>
            <strong>{locked.max_steps}</strong>
          </div>
          {locked.skills_extra?.length ? (
            <div>
              <span>{m.skillsAdded}</span>
              <strong>{locked.skills_extra.join(", ")}</strong>
            </div>
          ) : null}
          {locked.skills_invoke?.length ? (
            <div>
              <span>{m.skillsAskedFor}</span>
              <strong>{locked.skills_invoke.join(", ")}</strong>
            </div>
          ) : null}
          {locked.skills_disabled?.length ? (
            <div>
              <span>{m.skillsRemoved}</span>
              <strong>{locked.skills_disabled.join(", ")}</strong>
            </div>
          ) : null}
        </div>
        {error ? (
          <ErrorBanner message={error} onDismiss={() => setError(null)} />
        ) : null}
        {notice ? (
          <p className="callout callout--ok">
            {notice}{" "}
            {m.watchThemInRuns(<Link to="/advanced/runs">{m.runsLink}</Link>)}
          </p>
        ) : null}
        <div className="button-row">
          <button
            className="primary"
            disabled={busy}
            onClick={() => void launch()}
            type="button"
          >
            {busy ? m.queueing : m.queueAnotherRound}
          </button>
        </div>
      </div>
    );
  }

  if (caseIds.length === 0) {
    return (
      <div className="card">
        <h3>{m.runThisExperiment}</h3>
        <p className="callout callout--warning">
          {m.noCasesToLaunch(
            <Link to="/evaluate/new">{m.newEvaluationLink}</Link>
          )}
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3>{m.runThisExperiment}</h3>
      <p className="muted">{m.draftIntro}</p>

      <div className="field-row">
        <label className="field">
          <span>{m.harness}</span>
          <select
            onChange={(event) => handleHarnessChange(event.target.value)}
            value={harness}
          >
            {HARNESS_OPTIONS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          {HOST_HARNESSES.has(harness) ? (
            <span className="muted small">
              {m.runsHostedAuth(harness)}{" "}
              {harness === "claude" ? m.claudeAuthHelp : m.otherHostHelp}
            </span>
          ) : null}
        </label>
        {designVariants.length > 0 ? (
          <div className="field">
            <span>{m.promptVariants}</span>
            <p className="muted small">{m.promptVariantsFixed(designVariants.join(", "))}</p>
          </div>
        ) : (
          <label className="field">
            <span>{m.promptVariant}</span>
            {variantOptions.length > 0 ? (
              <select
                onChange={(event) => setPromptVariant(event.target.value)}
                value={promptVariant}
              >
                {variantOptions.map((variant) => (
                  <option key={variant} value={variant}>
                    {variant}
                  </option>
                ))}
              </select>
            ) : (
              <input
                onChange={(event) => setPromptVariant(event.target.value)}
                value={promptVariant}
              />
            )}
            <span className="muted small">{m.promptVariantsUnion}</span>
          </label>
        )}
        <label className="field">
          <span>{m.stepBudget}</span>
          <select
            onChange={(event) => setMaxSteps(Number(event.target.value))}
            value={maxSteps}
          >
            {stepOptions.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <span className="muted small">{m.stepBudgetHint}</span>
        </label>
      </div>

      <div className="field-row">
        <ModelPicker
          label={m.executorModel}
          directErrors={directErrors}
          nativeOptions={HARNESS_NATIVE_MODELS[harness]}
          onChange={setExecutorModel}
          onReload={() => void loadModels(true)}
          options={modelOptions}
          reloading={reloadingModels}
          value={executorModel}
        >
          <small className="muted">
            {HARNESS_NATIVE_MODELS[harness]
              ? m.nativeModelsHint(harness)
              : m.heldFixedHint}
          </small>
        </ModelPicker>
        <ModelPicker
          allowNone
          label={m.plannerModel}
          directErrors={directErrors}
          onChange={setPlannerModel}
          options={modelOptions}
          value={plannerModel}
        />
      </div>

      <label className="checkbox">
        <input
          checked={dryRun}
          onChange={(event) => setDryRun(event.target.checked)}
          type="checkbox"
        />
        <span>{m.dryRun}</span>
      </label>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {check ? (
        <p className={`callout ${check.ok ? "callout--ok" : "callout--warning"}`}>
          <strong>{check.ok ? m.ready : m.blocked}</strong> — {check.detail}
        </p>
      ) : null}
      {notice ? (
        <p className="callout callout--ok">
          {notice}{" "}
          {m.watchThemInRuns(<Link to="/advanced/runs">{m.runsLink}</Link>)}
        </p>
      ) : null}

      <div className="button-row">
        <button
          className="secondary"
          disabled={checking || busy}
          onClick={() => void handleCheck()}
          type="button"
        >
          {checking ? m.checking : m.checkConfiguration}
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() => void launch()}
          type="button"
        >
          {busy ? m.queueing : m.launchRuns}
        </button>
      </div>
    </div>
  );
}

/**
 * The LLM reading of a finished experiment (ADR-0053).
 *
 * The cards above already hold every fact; this is the same evidence read
 * back as prose — what was compared, what came out, what each arm actually
 * used, whether the comparison can be trusted. Disabled until every run has
 * stopped: a reading of half an experiment would be persisted as though the
 * comparison existed.
 */
function ExperimentAuditPanel({
  experimentId,
  finished
}: {
  experimentId: string;
  finished: boolean;
}) {
  const m = useMessages(MESSAGES);
  const [audit, setAudit] = useState<ExperimentAuditRecord | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);

  useEffect(() => {
    setAudit(null);
    setError(null);
    api
      .getExperimentAudit(experimentId)
      .then(setAudit)
      .catch(() => setAudit(null));
  }, [experimentId]);

  // Poll while the worker is writing; the call is one gateway request, so a
  // few seconds between polls is enough.
  useEffect(() => {
    if (audit?.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => {
      api
        .getExperimentAudit(experimentId)
        .then(setAudit)
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [audit?.status, experimentId]);

  const handleRun = () => {
    setPending(true);
    setError(null);
    api
      .runExperimentAudit(experimentId)
      .then(() => api.getExperimentAudit(experimentId))
      .then(setAudit)
      .catch((runError: unknown) =>
        setError(runError instanceof Error ? runError.message : m.unableToAudit)
      )
      .finally(() => setPending(false));
  };

  return (
    <div className="card">
      <div className="row space-between">
        <h3>{m.reading}</h3>
        <div className="button-row">
          {audit ? <span className="badge">{audit.status}</span> : null}
          <button
            disabled={!finished || pending || audit?.status === "running"}
            onClick={handleRun}
            title={finished ? m.readyToReadTitle : m.notYetReadableTitle}
            type="button"
          >
            {audit?.status === "running"
              ? m.readingEllipsis
              : audit?.status === "done"
                ? m.readAgain
                : m.readThisExperiment}
          </button>
        </div>
      </div>
      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {audit === null ? (
        <p className="muted">
          {finished ? m.noReadingYetFinished : m.noReadingYetUnfinished}
        </p>
      ) : audit.status === "running" ? (
        <p className="muted">{m.readingWith(audit.auditor_model || m.theDefaultModel)}</p>
      ) : audit.status === "failed" ? (
        <ErrorBanner message={audit.error ?? m.readingFailed} />
      ) : (
        <>
          <Markdown>{audit.markdown}</Markdown>
          <p className="muted small">
            {audit.auditor_model} · {new Date(audit.updated_at).toLocaleString()} ·{" "}
            <button
              className="link"
              onClick={() => setShowEvidence((value) => !value)}
              type="button"
            >
              {showEvidence ? m.hideEvidence : m.showEvidence}
            </button>
          </p>
          {showEvidence && audit.evidence_json ? (
            <pre className="evidence-json">
              {JSON.stringify(audit.evidence_json, null, 1)}
            </pre>
          ) : null}
        </>
      )}
    </div>
  );
}

function ExperimentDetail({
  summary,
  record,
  onLaunched,
  onAbandon,
  onDelete
}: {
  summary: ExperimentSummary;
  record?: ExperimentRecord;
  onLaunched: () => void;
  onAbandon: (experimentId: string) => void;
  onDelete: (experimentId: string, runCount: number) => void;
}) {
  const m = useMessages(MESSAGES);
  const runCount = record?.run_count ?? 0;
  return (
    <div className="stack">
      {summary.validity ? <ValidityBanner validity={summary.validity} /> : null}
      {summary.verdict ? <VerdictCard verdict={summary.verdict} /> : null}
      <ExperimentAuditPanel
        experimentId={summary.experiment_id}
        finished={record?.finished ?? false}
      />
      <div className="card">
        <div className="row space-between">
          <h2>{summary.question}</h2>
          <div className="button-row">
            {record && record.status !== "abandoned" ? (
              <button
                className="secondary"
                onClick={() => onAbandon(summary.experiment_id)}
                title={m.abandonTitle}
                type="button"
              >
                {m.abandon}
              </button>
            ) : null}
            <button
              className="danger"
              onClick={() => onDelete(summary.experiment_id, runCount)}
              title={
                runCount > 0
                  ? m.deleteTitleWithRuns(runCount)
                  : m.deleteTitleDraft
              }
              type="button"
            >
              {m.delete}
            </button>
          </div>
        </div>
        <p className="muted">
          {m.modeLabel[summary.mode] ?? summary.mode} ·{" "}
          {m.repetitionsSuffix(summary.repetitions)}
        </p>
        <p className="muted small">{m.modeCaveat[summary.mode]}</p>
        {summary.subject ? (
          <p className="muted">
            {m.subjectUnderTest(<strong>{summary.subject}</strong>)}
          </p>
        ) : null}
        {summary.conditions.flatMap((condition) => condition.warnings).length > 0 && (
          <ul className="warning-list">
            {[
              ...new Set(summary.conditions.flatMap((condition) => condition.warnings))
            ].map((warning) => (
              <li key={warning}>
                <IconAlert size={14} /> {warning}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h3>{m.conditions}</h3>
        <ConditionTable conditions={summary.conditions} />
        {summary.repetition_plan ? (
          <RepetitionHint
            plan={summary.repetition_plan}
            current={summary.repetitions}
          />
        ) : null}
      </div>

      <LaunchPanel experimentId={summary.experiment_id} onLaunched={onLaunched} />

      {summary.mode === "subject_validation" && summary.case_outcomes ? (
        <CaseOutcomeGrid outcomes={summary.case_outcomes} />
      ) : null}

      {summary.factorial ? <FactorialPanel block={summary.factorial} /> : null}

      {summary.capability ? <CapabilityPanel block={summary.capability} /> : null}

      {summary.comparisons.map((comparison) => (
        <div className="card" key={`${comparison.control}-${comparison.treatment}`}>
          <h3>{m.vs(comparison.treatment, comparison.control)}</h3>
          <p className="muted">
            {m.successRateMove(
              formatPercent(comparison.success.control),
              formatPercent(comparison.success.treatment)
            )}
            {comparison.success.points !== null && (
              <>
                {" "}
                (<strong>{comparison.success.points > 0 ? "+" : ""}
                {comparison.success.points.toFixed(0)}{m.pp}</strong>
                {deltaInterval(comparison.success) ? (
                  <span title={m.pp95Title}>
                    {m.interval95Label}
                    {deltaInterval(comparison.success)}
                  </span>
                ) : null}
                )
              </>
            )}
          </p>
          {comparison.success.interval_crosses_zero ? (
            <p className="muted small">{m.crossesZero}</p>
          ) : null}

          <table className="data-table">
            <thead>
              <tr>
                <th>{m.metric}</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th>{m.deltaTitle}</th>
                <th>{m.beyondNoise}</th>
              </tr>
            </thead>
            <tbody>
              {comparison.deltas.map((delta) => (
                <tr key={delta.name}>
                  <td className="mono small">{delta.name}</td>
                  <td className="mono small">{formatNumber(delta.control)}</td>
                  <td className="mono small">{formatNumber(delta.treatment)}</td>
                  <td className={`mono small ${deltaClass(delta)}`}>
                    {deltaLabel(delta)}
                  </td>
                  <td className="small">
                    {delta.exceeds_spread === null
                      ? m.unknown
                      : delta.exceeds_spread
                        ? m.yes
                        : m.noWithinVariance}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4>{m.caseMatrix}</h4>
          <p className="muted small">{m.caseMatrixHint}</p>
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.case}</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {comparison.case_matrix.map((row) => (
                <tr key={row.case_id}>
                  <td className="mono small">{row.case_id}</td>
                  <td>
                    <Outcome value={row.control} />
                  </td>
                  <td>
                    <Outcome value={row.treatment} />
                  </td>
                  <td className="small">
                    <span className={MOVEMENT_CLASS[row.movement]}>
                      {m.movementLabel[row.movement]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function Experiments() {
  const m = useMessages(MESSAGES);
  const [params, setParams] = useSearchParams();
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  // A ladder is read as one family (ADR-0051): `?family=<id>` narrows the
  // list to its rungs, in the order they were created.
  const familyFilter = params.get("family") ?? "";
  // Deep-linkable: the wizard lands here on the experiment it just created.
  const [selected, setSelected] = useState<string | null>(params.get("experiment"));
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  // A form that created an experiment but could not launch it lands here with
  // the reason, so the launch is retried on that design rather than a copy.
  const handedOver = (useLocation().state as { launchError?: string } | null)?.launchError;
  const [launchNotice, setLaunchNotice] = useState<string | null>(handedOver ?? null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a launch so the summary refetches with the queued runs in it.
  const [refresh, setRefresh] = useState(0);
  // Abandoned experiments are clutter by definition — opt in to see them.
  const [showAbandoned, setShowAbandoned] = useState(false);

  useEffect(() => {
    api
      .listExperiments()
      .then((rows) => {
        setExperiments(rows);
        setError(null);
      })
      .catch((loadError: unknown) =>
        setError(
          loadError instanceof Error ? loadError.message : m.unableToLoadExperiments
        )
      )
      .finally(() => setIsLoading(false));
  }, [refresh]);

  useEffect(() => {
    if (selected === null) {
      setSummary(null);
      return;
    }
    api
      .summarizeExperiment(selected)
      .then(setSummary)
      .catch((summaryError: unknown) =>
        setError(
          summaryError instanceof Error ? summaryError.message : m.unableToSummarize
        )
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, refresh]);

  const handleAbandon = async (experimentId: string) => {
    try {
      await api.abandonExperiment(experimentId);
      setRefresh((value) => value + 1);
    } catch (abandonError) {
      setError(
        abandonError instanceof Error ? abandonError.message : m.unableToAbandon
      );
    }
  };

  const handleDelete = async (experimentId: string, runCount: number) => {
    const message =
      runCount > 0
        ? m.deleteConfirmWithRuns(experimentId, runCount)
        : m.deleteConfirmDraft(experimentId);
    if (!window.confirm(message)) {
      return;
    }
    try {
      await api.deleteExperiment(experimentId, runCount > 0);
      setSelected(null);
      setParams({});
      setRefresh((value) => value + 1);
    } catch (deleteError) {
      setError(
        deleteError instanceof Error ? deleteError.message : m.unableToDelete
      );
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>{m.experimentsTitle}</h1>
          <p className="muted">{m.experimentsLede}</p>
        </div>
      </header>

      <HelpPanel title={m.helpTitle}>
        <p>{m.helpParagraph1}</p>
        <p className="muted">{m.helpParagraph2}</p>
      </HelpPanel>

      {launchNotice !== null && (
        <ErrorBanner message={launchNotice} onDismiss={() => setLaunchNotice(null)} />
      )}
      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="card">
        <div className="row space-between">
          <h2>
            {familyFilter ? (
              <>
                {m.familyLabel(familyFilter)}
                <button
                  className="link"
                  onClick={() => setParams({})}
                  type="button"
                >
                  {m.showAll}
                </button>
              </>
            ) : (
              m.allExperiments
            )}
          </h2>
          {experiments.some((item) => item.status === "abandoned") ? (
            <label className="row gap">
              <input
                checked={showAbandoned}
                onChange={(event) => setShowAbandoned(event.target.checked)}
                type="checkbox"
              />
              <span>
                {m.showAbandoned(
                  experiments.filter((item) => item.status === "abandoned").length
                )}
              </span>
            </label>
          ) : null}
        </div>
        {isLoading ? (
          <Spinner />
        ) : experiments.length === 0 ? (
          <EmptyState
            action={
              <div className="empty-state-buttons">
                <Link className="primary" to="/evaluate/new?mode=harness_evaluation">
                  {m.evaluateHarnessChange}
                </Link>
                <Link className="secondary" to="/evaluate/audit">
                  {m.auditRepository}
                </Link>
                <Link className="secondary" to="/evaluate/new?mode=cross_repo">
                  {m.crossRepoEvaluation}
                </Link>
              </div>
            }
            description={m.noExperimentsDescription}
            title={m.noExperimentsTitle}
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.id}</th>
                <th>{m.question}</th>
                <th>{m.mode}</th>
                <th>{m.arms}</th>
                <th>{m.runs}</th>
                <th>{m.reps}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {experiments
                .filter(
                  (experiment) =>
                    showAbandoned || experiment.status !== "abandoned"
                )
                .filter(
                  (experiment) =>
                    !familyFilter || experiment.family_id === familyFilter
                )
                .map((experiment) => (
                <tr key={experiment.experiment_id}>
                  <td>
                    <strong>{experiment.experiment_id}</strong>
                    <div className="muted small">
                      {m.experimentStatus[experiment.status] ?? experiment.status}
                      {experiment.family_id && !familyFilter ? (
                        <>
                          {" · "}
                          <button
                            className="link"
                            onClick={() => setParams({ family: experiment.family_id ?? "" })}
                            title={m.familyButtonTitle}
                            type="button"
                          >
                            {m.familyButton(experiment.family_id)}
                          </button>
                        </>
                      ) : null}
                    </div>
                  </td>
                  <td className="small">{experiment.question}</td>
                  <td className="small">
                    {m.modeLabel[experiment.mode] ?? experiment.mode}
                  </td>
                  <td>{experiment.condition_count ?? "—"}</td>
                  <td>{experiment.run_count ?? "—"}</td>
                  <td>
                    {experiment.repetitions}
                    {experiment.repetitions < 2 ? (
                      <div className="muted small">{m.exploratory}</div>
                    ) : experiment.repetitions < 5 ? (
                      <div className="muted small">{m.judgement}</div>
                    ) : null}
                  </td>
                  <td>
                    <button
                      onClick={() => {
                        const next =
                          selected === experiment.experiment_id
                            ? null
                            : experiment.experiment_id;
                        setSelected(next);
                        setParams(next ? { experiment: next } : {});
                      }}
                      type="button"
                    >
                      {selected === experiment.experiment_id ? m.hide : m.open}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {summary !== null && (
        <ExperimentDetail
          onAbandon={(id) => void handleAbandon(id)}
          onDelete={(id, runCount) => void handleDelete(id, runCount)}
          onLaunched={() => setRefresh((value) => value + 1)}
          record={experiments.find(
            (item) => item.experiment_id === summary.experiment_id
          )}
          summary={summary}
        />
      )}
    </section>
  );
}
