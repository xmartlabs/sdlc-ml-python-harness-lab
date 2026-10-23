import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { InfoTooltip } from "../components/InfoTooltip";
import { ModelPicker } from "../components/ModelPicker";
import { Spinner } from "../components/Spinner";
import { StateBadge } from "../components/StateBadge";
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconPlus,
  IconSparkles,
  IconX
} from "../components/icons";
import { defineMessages, useMessages } from "../i18n";
import { HARNESS_NATIVE_MODELS, HARNESS_OPTIONS, HOST_HARNESSES } from "../harnesses";
import type {
  CaseRecord,
  DroppedCommand,
  ExperimentSummary,
  HarnessSkill,
  ModelOption,
  RunRecord,
  StagedSkillRecord
} from "../types";



const STEPS = [
  { key: "subject" },
  { key: "success" },
  { key: "arms" },
  { key: "watch" },
  { key: "read" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

type Arm = { harness: string; executorModel: string };

const MAX_STEP_OPTIONS = [5, 10, 20, 40];
const REPETITION_OPTIONS = [1, 3, 5, 10];

const MESSAGES = defineMessages({
  en: {
    stepLabel: {
      subject: "Bring your tool",
      success: "Declare what success is",
      arms: "Choose the arms",
      watch: "Watch it, and go in",
      read: "Read the result"
    } as Record<StepKey, string>,
    title: "Validate your tool",
    lede:
      "You built something — an agent behind an API, an MCP server, a service " +
      "wrapper — and a skill that drives it. This measures how reliably it does " +
      "its job under each harness and model. It says nothing about the harness: " +
      "nothing here is ablated.",
    noticeCaseSelected: (caseId: string) => `Case '${caseId}' selected.`,
    noticeUsingExistingCase: (caseId: string) => `Using existing case '${caseId}'.`,
    noticeCaseUpdated: (caseId: string) => `Case '${caseId}' updated.`,
    noticeCaseCreated: (caseId: string) => `Case '${caseId}' created.`,
    noticeStaged: (name: string) => `Staged '${name}'. It will be added to every arm's run.`,
    noticeImported: (name: string, sourceUrl: string) =>
      `Imported '${name}' from ${sourceUrl}.`,
    noticeDesignCreated: (experimentId: string) =>
      `Design '${experimentId}' created. Nothing has run yet — read ` +
      `the notes below, then launch.`,
    noticeQueued: (count: number) => `Queued ${count} run(s).`,
    errorLoadSkills: "Unable to load staged skills.",
    errorSomethingFailed: "Something failed",
    errorMultipleSkills: (choices: string) =>
      `That source holds several skills. Pick one on the Skills page: ${choices}`,
    errorNoFreeCaseId: "Unable to find a free case id for this subject.",
    errorNoFreeExperimentId: "Unable to find a free experiment id.",
    errorGenerateCaseDraft: "Unable to generate the case draft.",
    aiErrorNoCommand: (retried: string) =>
      `The draft has no runnable validation command${retried}. Write one below, ` +
      "or describe the check you want and generate again.",
    aiNoticeDraftGenerated: (kept: number, retried: string) =>
      `Draft generated with ${kept} validation command(s)${retried}. ` +
      "Review and edit the fields below before saving.",
    retriedSuffix: " (after one corrective retry)",
    runsQueuedCallout: (link: string) => (
      <>
        The runs are queued, so this is a record of what ran, not a form. To test
        something different, <Link to="/evaluate/subject" reloadDocument>{link}</Link>.
      </>
    ),
    startNewValidation: "start a new validation",
    skillsPanel: {
      heading: "Which skill drives your tool?",
      skillsWorkbench: "Skills workbench",
      intro:
        "The skill is what the agent reads to reach your tool. Staged skills are " +
        "added via extra skills, while harness skills are governed tools. Select " +
        "one or multiple skills under test.",
      tabAll: (count: number) => `All (${count})`,
      tabStaged: (count: number) => `Staged (${count})`,
      tabHarness: (count: number) => `Harness (${count})`,
      filterAriaLabel: "Filter skills",
      filterPlaceholder: "Filter skills by name or description…",
      noSkillsTitle: "No skills found.",
      noSkillsDescription: "No matching skills found. Adjust your search or filter tab.",
      invokeSelected: (tooltip: ReactNode) => (
        <>
          Ask the agent to use the selected skill(s) at the start of every step.{" "}
          {tooltip}
        </>
      ),
      invokeSelectedTooltip:
        "On by default: a skill that is merely available is invoked only when " +
        "the case's instruction or the skill's own triggers call for it, so the " +
        "run may never exercise it. Turn this off to measure whether the agent " +
        "reaches for the skill unasked.",
      isolateGoverned: (tooltip: ReactNode) => (
        <>
          Isolate the run: disable every harness skill that is not selected above.{" "}
          {tooltip}
        </>
      ),
      isolateGovernedTooltip:
        "Every governed harness skill you did not select above is added to this " +
        "run's disabled list, so only the skill(s) you picked can be reached. " +
        "Leave this off to test your skill alongside the rest of the harness as " +
        "usual; turn it on to isolate the measurement to just this skill.",
      alreadyGoverned: (tooltip: ReactNode) => (
        <>
          My skill is already part of the governed harness — every run loads it
          already.{" "}
          {tooltip}
        </>
      ),
      alreadyGovernedTooltip:
        "Check this only if your skill ships as part of the governed harness " +
        "itself, not as a staged extra skill, so every run loads it regardless " +
        "of what you pick above. It only affects this form: it lets you continue " +
        "past this step without selecting a skill from the list. It does not " +
        "change what is sent to the run."
    },
    writeOne: {
      heading: "Write one here",
      skillName: "Skill name",
      skillMd: "SKILL.md",
      stageButton: "Stage this skill",
      staging: "Staging…"
    },
    importOne: {
      heading: "Or import one",
      repoUrl: "Repository or archive URL",
      note:
        "Fetched, validated and staged — never promoted into the harness by this " +
        "step.",
      importButton: "Import",
      importing: "Importing…"
    },
    notReadyYet: "Not ready yet:",
    successPanel: {
      heading: "What is being tested, and what counts as done?",
      useExisting: (count: number) => `Use an existing case (${count})`,
      createNew: "Create a new case",
      pickExistingIntro:
        "Pick a case the lab already holds — SWE-bench, a benchmark, or a saved " +
        "case — to validate your tool or skill against a known standard.",
      filterCasesAriaLabel: "Filter cases",
      filterCasesPlaceholder: "Search by id, name or tag (e.g. swe-bench, python, bugfix)…",
      noCasesTitle: "No cases found.",
      noCasesDescription: "No existing case matches the search.",
      noDescription: "No description",
      selectedCase: (caseId: string) => (
        <>
          Selected case: <code>{caseId}</code>
        </>
      ),
      selectedCaseMeta: (subject: string, maxSteps: number, variant: string) => (
        <>
          {subject} · Max steps: {maxSteps} · Variant: <code>{variant}</code>
        </>
      ),
      useThisCase: "Use this case and continue"
    },
    aiAssist: {
      title: "Draft it with AI (optional)",
      contextWith: (skills: string) => `Context: ${skills}`,
      contextWithout: "No skills selected in step 1",
      describeLabel: "Describe what you want to test, in plain language",
      describePlaceholder:
        "e.g. Check that the agent uses notion-export to save page 123 to notes.txt, " +
        "and that the file exists and is not empty.",
      modelLabel: "Model",
      defaultSuffix: " (default)",
      generate: "Generate the case",
      drafting: "Drafting…",
      leftOutTitle: "Left out of the draft — the lab would not run these:"
    },
    caseForm: {
      subject: "Subject",
      subjectPlaceholder: "notion-export skill + local export API",
      instruction: "The instruction the agent gets",
      instructionPlaceholder:
        "Read the linked page and save its contents to notes.txt in the workspace.",
      validationCommands: "Validation commands — one per line",
      validationPlaceholder: "test -s {workspace}/notes.txt\npython3 check_export.py {workspace}",
      maxSteps: "Max steps",
      explanation:
        "The command is the whole measurement: it runs after the agent stops, and " +
        "its exit code is the pass. A command that cannot fail — a smoke check that " +
        "only proves the thing started — produces 100% and measures nothing.",
      notExecutable: "Not executable, so these would be dropped in silence:",
      saving: "Saving…",
      updateCase: "Update the case",
      createCase: "Create the case"
    },
    armsPanel: {
      heading: "Where should it run?",
      noBaseline: "One arm per harness/model — no baseline",
      intro:
        "Every arm is a peer. There is no control here, because nothing is being " +
        "compared against a version of the harness with your tool removed — each arm " +
        "gets its own pass rate and is read on its own.",
      harness: "Harness",
      runsNativeAuth: (harness: string) => `Runs the ${harness} CLI with its native auth. `,
      claudeAuthNote:
        "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or " +
        "host mode (make app-host).",
      hostModeNote: "Needs host mode — run make app-host.",
      executorModel: "Executor model",
      remove: "Remove",
      addArm: "Add an arm",
      repetitions: "Repetitions per arm",
      armsSummary: (arms: number, repetitions: number, runs: number) =>
        `${arms} arm(s) x ${repetitions} repetition(s) = ${runs} run(s). A single run ` +
        'of an agent is an anecdote; the repetitions are what make "most of the ' +
        'time" a rate.',
      queueing: "Queueing…",
      launch: (count: number) => `Launch ${count} run(s)`,
      creating: "Creating…",
      createDesign: "Create the design"
    },
    watchPanel: {
      heading: "The runs",
      openExperiment: "Open the experiment",
      intro:
        "The pass column is the validation command's verdict, computed for you. Open " +
        "a run's sandbox to see what the agent actually did — that is the part no " +
        "score can carry, and it is why a scored run is still worth looking at.",
      waitingForRuns: "Waiting for the runs to appear",
      run: "Run",
      arm: "Arm",
      state: "State",
      passed: "Passed",
      yourJudgement: "Your judgement",
      pass: "pass",
      fail: "fail",
      openSandbox: "Open the sandbox",
      scored: (score: number) => ` · scored ${score}/10`,
      reading: "Reading…",
      readResult: "Read the result"
    },
    readPanel: {
      resultKicker: "Result",
      evidence: (evidence: string) => `Evidence: ${evidence}`,
      onePerArm: "One rate per arm",
      arm: "Arm",
      passed: "Passed",
      scoredRuns: "Scored runs",
      costMean: "Cost (mean)",
      notAveraged:
        "These are not averaged together. A combined number would describe a " +
        "configuration nobody runs.",
      perCase: "Per case",
      passedOfScored: "Passed of scored, per arm",
      case: "Case",
      successesOfScored: (successes: number, scored: number) => `${successes} of ${scored}`,
      openFullExperiment: "Open the full experiment",
      backToRuns: "Back to the runs"
    },
    helpPanel: {
      title: "What this mode can and cannot say",
      body1:
        "It reports how often your subject completed its cases under each harness and " +
        "model. It cannot tell you whether the harness helped: no arm here has the " +
        "harness removed, so there is nothing to attribute a difference to.",
      body2: (link: string) => (
        <>
          To ask that question instead, run a <Link to="/evaluate/new">{link}</Link>,
          which ablates a skill and compares the arm with it against the arm without.
        </>
      ),
      harnessEvaluation: "harness evaluation"
    },
    blocking: {
      chooseSkill:
        "Choose at least one skill that drives your tool — or say it is already " +
        "part of the harness.",
      pickExistingCase: "Pick an existing case from the list, or create a new one.",
      nameSubject: "Name the subject. The result sentence has to say what passed.",
      writeInstruction: "Write the instruction the agent is given.",
      addValidationCommand:
        "Add at least one validation command that exits non-zero when the " +
        "subject fails. Without one the runs execute and report nothing.",
      declareArm: "Declare at least one arm.",
      duplicateArms: "Two arms are identical — each arm must be its own configuration.",
      pickModel:
        "Pick a model for every arm. A headless run needs one, and the arm's " +
        "label names it."
    }
  },
  es: {
    stepLabel: {
      subject: "Traé tu herramienta",
      success: "Declará qué es el éxito",
      arms: "Elegí los brazos",
      watch: "Miralo, y entrá",
      read: "Leé el resultado"
    } as Record<StepKey, string>,
    title: "Validá tu herramienta",
    lede:
      "Construiste algo — un agente detrás de una API, un servidor MCP, un " +
      "wrapper de servicio — y un skill que lo maneja. Esto mide con qué " +
      "confiabilidad hace su trabajo bajo cada arnés y modelo. No dice nada " +
      "sobre el arnés: nada acá está ablacionado.",
    noticeCaseSelected: (caseId: string) => `Caso '${caseId}' seleccionado.`,
    noticeUsingExistingCase: (caseId: string) => `Usando el caso existente '${caseId}'.`,
    noticeCaseUpdated: (caseId: string) => `Caso '${caseId}' actualizado.`,
    noticeCaseCreated: (caseId: string) => `Caso '${caseId}' creado.`,
    noticeStaged: (name: string) =>
      `'${name}' agregado a staging. Se incluirá en la corrida de cada brazo.`,
    noticeImported: (name: string, sourceUrl: string) =>
      `'${name}' importado desde ${sourceUrl}.`,
    noticeDesignCreated: (experimentId: string) =>
      `Diseño '${experimentId}' creado. Todavía no corrió nada — leé las notas ` +
      "de abajo y después lanzá.",
    noticeQueued: (count: number) => `${count} corrida(s) encoladas.`,
    errorLoadSkills: "No se pudieron cargar los skills en staging.",
    errorSomethingFailed: "Algo falló",
    errorMultipleSkills: (choices: string) =>
      `Esa fuente contiene varios skills. Elegí uno en la página de Skills: ${choices}`,
    errorNoFreeCaseId: "No se pudo encontrar un id de caso libre para esta herramienta.",
    errorNoFreeExperimentId: "No se pudo encontrar un id de experimento libre.",
    errorGenerateCaseDraft: "No se pudo generar el borrador del caso.",
    aiErrorNoCommand: (retried: string) =>
      `El borrador no tiene ningún comando de validación ejecutable${retried}. Escribí ` +
      "uno abajo, o describí la verificación que querés y generá de nuevo.",
    aiNoticeDraftGenerated: (kept: number, retried: string) =>
      `Borrador generado con ${kept} comando(s) de validación${retried}. ` +
      "Revisá y editá los campos de abajo antes de guardar.",
    retriedSuffix: " (después de un reintento correctivo)",
    runsQueuedCallout: (link: string) => (
      <>
        Las corridas ya están encoladas, así que esto es un registro de lo que
        corrió, no un formulario. Para probar algo distinto,{" "}
        <Link to="/evaluate/subject" reloadDocument>{link}</Link>.
      </>
    ),
    startNewValidation: "iniciá una nueva validación",
    skillsPanel: {
      heading: "¿Qué skill maneja tu herramienta?",
      skillsWorkbench: "Banco de skills",
      intro:
        "El skill es lo que el agente lee para llegar a tu herramienta. Los " +
        "skills en staging se agregan como skills extra, mientras que los " +
        "skills del arnés son herramientas gobernadas. Seleccioná uno o varios " +
        "skills a validar.",
      tabAll: (count: number) => `Todos (${count})`,
      tabStaged: (count: number) => `Staging (${count})`,
      tabHarness: (count: number) => `Arnés (${count})`,
      filterAriaLabel: "Filtrar skills",
      filterPlaceholder: "Filtrar skills por nombre o descripción…",
      noSkillsTitle: "No se encontraron skills.",
      noSkillsDescription: "Ningún skill coincide. Ajustá la búsqueda o la pestaña.",
      invokeSelected: (tooltip: ReactNode) => (
        <>
          Pedirle al agente que use el/los skill(s) seleccionados al empezar cada
          paso.{" "}
          {tooltip}
        </>
      ),
      invokeSelectedTooltip:
        "Activado por defecto: un skill que solo está disponible se invoca nada " +
        "más cuando la instrucción del caso o los propios disparadores del skill " +
        "lo piden, así que la corrida puede nunca ejercitarlo. Desactivá esto para " +
        "medir si el agente recurre al skill sin que se lo pidan.",
      isolateGoverned: (tooltip: ReactNode) => (
        <>
          Aislar la corrida: deshabilitar todo skill del arnés que no esté
          seleccionado arriba.{" "}
          {tooltip}
        </>
      ),
      isolateGovernedTooltip:
        "Todo skill gobernado del arnés que no seleccionaste arriba se agrega a " +
        "la lista de deshabilitados de esta corrida, así que solo el/los skill(s) " +
        "que elegiste puede(n) usarse. Dejalo apagado para probar tu skill junto " +
        "con el resto del arnés como de costumbre; prendelo para aislar la " +
        "medición a solo este skill.",
      alreadyGoverned: (tooltip: ReactNode) => (
        <>
          Mi skill ya forma parte del arnés gobernado — cada corrida ya lo carga.{" "}
          {tooltip}
        </>
      ),
      alreadyGovernedTooltip:
        "Marcá esto solo si tu skill viene incluido en el propio arnés gobernado, " +
        "no como skill extra en staging, así que cada corrida lo carga sin " +
        "importar lo que elijas arriba. Solo afecta este formulario: te deja " +
        "seguir más allá de este paso sin elegir un skill de la lista. No cambia " +
        "lo que se envía a la corrida."
    },
    writeOne: {
      heading: "Escribí uno acá",
      skillName: "Nombre del skill",
      skillMd: "SKILL.md",
      stageButton: "Poner este skill en staging",
      staging: "Subiendo a staging…"
    },
    importOne: {
      heading: "O importá uno",
      repoUrl: "URL del repositorio o archivo",
      note:
        "Obtenido, validado y puesto en staging — esto nunca lo promueve al " +
        "arnés.",
      importButton: "Importar",
      importing: "Importando…"
    },
    notReadyYet: "Todavía no está listo:",
    successPanel: {
      heading: "¿Qué se está probando, y qué cuenta como hecho?",
      useExisting: (count: number) => `Usar un caso existente (${count})`,
      createNew: "Crear un caso nuevo",
      pickExistingIntro:
        "Elegí un caso que el lab ya tiene — SWE-bench, un benchmark, o un caso " +
        "guardado — para validar tu herramienta o skill contra un estándar " +
        "conocido.",
      filterCasesAriaLabel: "Filtrar casos",
      filterCasesPlaceholder: "Buscá por id, nombre o etiqueta (por ej. swe-bench, python, bugfix)…",
      noCasesTitle: "No se encontraron casos.",
      noCasesDescription: "Ningún caso existente coincide con la búsqueda.",
      noDescription: "Sin descripción",
      selectedCase: (caseId: string) => (
        <>
          Caso seleccionado: <code>{caseId}</code>
        </>
      ),
      selectedCaseMeta: (subject: string, maxSteps: number, variant: string) => (
        <>
          {subject} · Pasos máximos: {maxSteps} · Variante: <code>{variant}</code>
        </>
      ),
      useThisCase: "Usar este caso y continuar"
    },
    aiAssist: {
      title: "Redactalo con IA (opcional)",
      contextWith: (skills: string) => `Contexto: ${skills}`,
      contextWithout: "No hay skills seleccionados en el paso 1",
      describeLabel: "Describí qué querés probar, en lenguaje simple",
      describePlaceholder:
        "por ej. Verificá que el agente use notion-export para guardar la página " +
        "123 en notes.txt, y que el archivo exista y no esté vacío.",
      modelLabel: "Modelo",
      defaultSuffix: " (por defecto)",
      generate: "Generar el caso",
      drafting: "Redactando…",
      leftOutTitle: "Dejado afuera del borrador — el lab no correría esto:"
    },
    caseForm: {
      subject: "Herramienta",
      subjectPlaceholder: "skill notion-export + API de exportación local",
      instruction: "La instrucción que recibe el agente",
      instructionPlaceholder:
        "Leé la página enlazada y guardá su contenido en notes.txt dentro del workspace.",
      validationCommands: "Comandos de validación — uno por línea",
      validationPlaceholder: "test -s {workspace}/notes.txt\npython3 check_export.py {workspace}",
      maxSteps: "Pasos máximos",
      explanation:
        "El comando es toda la medición: corre después de que el agente se " +
        "detiene, y su código de salida es el pase. Un comando que no puede " +
        "fallar — una verificación superficial que solo prueba que algo arrancó — " +
        "da 100% y no mide nada.",
      notExecutable: "No es ejecutable, así que esto se descartaría en silencio:",
      saving: "Guardando…",
      updateCase: "Actualizar el caso",
      createCase: "Crear el caso"
    },
    armsPanel: {
      heading: "¿Dónde debería correr?",
      noBaseline: "Un brazo por arnés/modelo — sin línea de base",
      intro:
        "Cada brazo es un par. Acá no hay control, porque no se está comparando " +
        "contra una versión del arnés con tu herramienta sacada — cada brazo " +
        "tiene su propia tasa de éxito y se lee por separado.",
      harness: "Arnés",
      runsNativeAuth: (harness: string) => `Corre la CLI de ${harness} con su auth nativa. `,
      claudeAuthNote:
        "Funciona en Docker (CLAUDE_CODE_OAUTH_TOKEN o ANTHROPIC_API_KEY en .env) " +
        "o en modo host (make app-host).",
      hostModeNote: "Necesita modo host — corré make app-host.",
      executorModel: "Modelo ejecutor",
      remove: "Quitar",
      addArm: "Agregar un brazo",
      repetitions: "Repeticiones por brazo",
      armsSummary: (arms: number, repetitions: number, runs: number) =>
        `${arms} brazo(s) x ${repetitions} repetición(es) = ${runs} corrida(s). Una ` +
        "sola corrida de un agente es una anécdota; las repeticiones son lo que " +
        'convierte "la mayoría de las veces" en una tasa.',
      queueing: "Encolando…",
      launch: (count: number) => `Lanzar ${count} corrida(s)`,
      creating: "Creando…",
      createDesign: "Crear el diseño"
    },
    watchPanel: {
      heading: "Las corridas",
      openExperiment: "Abrir el experimento",
      intro:
        "La columna de pase es el veredicto del comando de validación, calculado " +
        "por vos. Abrí el sandbox de una corrida para ver qué hizo realmente el " +
        "agente — esa es la parte que ningún puntaje puede transmitir, y por eso " +
        "vale la pena mirar una corrida igual aunque tenga puntaje.",
      waitingForRuns: "Esperando que aparezcan las corridas",
      run: "Corrida",
      arm: "Brazo",
      state: "Estado",
      passed: "Pasó",
      yourJudgement: "Tu evaluación",
      pass: "pasó",
      fail: "falló",
      openSandbox: "Abrir el sandbox",
      scored: (score: number) => ` · puntuado ${score}/10`,
      reading: "Leyendo…",
      readResult: "Leé el resultado"
    },
    readPanel: {
      resultKicker: "Resultado",
      evidence: (evidence: string) => `Evidencia: ${evidence}`,
      onePerArm: "Una tasa por brazo",
      arm: "Brazo",
      passed: "Pasó",
      scoredRuns: "Corridas puntuadas",
      costMean: "Costo (media)",
      notAveraged:
        "Esto no se promedia junto. Un número combinado describiría una " +
        "configuración que nadie corre.",
      perCase: "Por caso",
      passedOfScored: "Pasó de puntuadas, por brazo",
      case: "Caso",
      successesOfScored: (successes: number, scored: number) => `${successes} de ${scored}`,
      openFullExperiment: "Abrir el experimento completo",
      backToRuns: "Volver a las corridas"
    },
    helpPanel: {
      title: "Qué puede y qué no puede decir este modo",
      body1:
        "Informa con qué frecuencia tu herramienta completó sus casos bajo cada " +
        "arnés y modelo. No puede decirte si el arnés ayudó: ningún brazo acá " +
        "tiene el arnés sacado, así que no hay nada a lo que atribuirle una " +
        "diferencia.",
      body2: (link: string) => (
        <>
          Para hacer esa pregunta en cambio, corré una{" "}
          <Link to="/evaluate/new">{link}</Link>, que ablaciona un skill y compara
          el brazo con él contra el brazo sin él.
        </>
      ),
      harnessEvaluation: "evaluación de arnés"
    },
    blocking: {
      chooseSkill:
        "Elegí al menos un skill que maneje tu herramienta — o indicá que ya es " +
        "parte del arnés.",
      pickExistingCase: "Elegí un caso existente de la lista, o creá uno nuevo.",
      nameSubject: "Nombrá la herramienta. La oración del resultado tiene que decir qué pasó.",
      writeInstruction: "Escribí la instrucción que recibe el agente.",
      addValidationCommand:
        "Agregá al menos un comando de validación que salga con código distinto " +
        "de cero cuando la herramienta falla. Sin uno, las corridas se ejecutan y " +
        "no informan nada.",
      declareArm: "Declará al menos un brazo.",
      duplicateArms: "Dos brazos son idénticos — cada brazo debe tener su propia configuración.",
      pickModel:
        "Elegí un modelo para cada brazo. Una corrida sin interfaz necesita uno, " +
        "y la etiqueta del brazo lo nombra."
    }
  }
});

/**
 * Client-side mirror of `validation._looks_executable`.
 *
 * The server is still the authority — `create_experiment` refuses a case whose
 * validation is prose, and this cannot weaken that. It exists so the refusal
 * arrives while the field is still on screen, instead of three screens later.
 */
function looksExecutable(command: string): boolean {
  const stripped = command.trim();
  if (!stripped) {
    return false;
  }
  if (stripped.includes("{workspace}") || stripped.includes("{run_id}")) {
    return true;
  }
  if (["&&", "||", "|", ";"].some((operator) => stripped.includes(operator))) {
    return true;
  }
  const first = stripped.split(/\s+/)[0] ?? "";
  const known = new Set([
    "python",
    "python3",
    "pytest",
    "sh",
    "bash",
    "make",
    "npm",
    "node",
    "uv",
    "timeout",
    "go",
    "cargo",
    "ruff",
    "mypy"
  ]);
  return (
    first.includes("=") || known.has(first) || first.startsWith("./") || first.includes("/")
  );
}

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "subject"
  );
}

function isDuplicateId(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.status === 409) ||
    (error instanceof Error && error.message.includes("already exists"))
  );
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${Math.round(value * 100)}%`;
}

function armLabel(arm: Arm): string {
  return arm.executorModel ? `${arm.harness}/${arm.executorModel}` : arm.harness;
}

/**
 * Subject validation as a workflow: bring a tool, say what success is, run it
 * across harnesses, look inside, read one rate per arm (ADR-0040).
 *
 * The five steps gate, which is what separates this from `Audit`'s freely
 * clickable stepper. Each gate blocks the one thing this mode cannot survive
 * without: a subject that is present in the runs, a case that says what success
 * is in a command, and at least one declared arm. The second is the load-bearing
 * one — without an executable validation command the runs execute, cost money
 * and report nothing, and nowhere in that chain does anything say so.
 *
 * It is deliberately not a fourth branch of `NewEvaluation`: that page is one
 * flat form for designs that compare arms, and every control on it presumes a
 * control arm this mode does not have.
 */
export function SubjectValidation() {
  const m = useMessages(MESSAGES);
  const navigate = useNavigate();
  const [step, setStep] = useState<StepKey>("subject");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  // Step 1 — the subject.
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [governedSkills, setGovernedSkills] = useState<HarnessSkill[]>([]);
  const [filterTab, setFilterTab] = useState<"all" | "staged" | "harness">("all");
  const [searchSkill, setSearchSkill] = useState("");
  const [isolateGoverned, setIsolateGoverned] = useState(false);
  const [skillsExtra, setSkillsExtra] = useState<string[]>([]);
  // The escape hatch for a skill that is already governed: it is in every run
  // by definition, so requiring it to be staged would lock out the case the
  // staging area exists to graduate.
  const [alreadyGoverned, setAlreadyGoverned] = useState(false);
  // On by default: the wizard's question is "does my skill work", and a skill
  // that is merely available may never be reached for in a short case.
  const [invokeSelected, setInvokeSelected] = useState(true);
  const [newSkillName, setNewSkillName] = useState("");
  const [newSkillBody, setNewSkillBody] = useState("");
  const [importUrl, setImportUrl] = useState("");

  // Step 2 — what success is.
  const [existingCases, setExistingCases] = useState<CaseRecord[]>([]);
  const [caseMode, setCaseMode] = useState<"existing" | "new">("existing");
  const [caseSearch, setCaseSearch] = useState("");
  const [promptVariant, setPromptVariant] = useState("default");
  const [subject, setSubject] = useState("");
  const [prompt, setPrompt] = useState("");
  const [commandText, setCommandText] = useState("");
  const [maxSteps, setMaxSteps] = useState(10);
  const [caseId, setCaseId] = useState("");

  // Step 2 — AI Case Assistant.
  const [aiDescription, setAiDescription] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiNotice, setAiNotice] = useState("");
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiDropped, setAiDropped] = useState<DroppedCommand[]>([]);

  // Step 3 — the arms.
  const [arms, setArms] = useState<Arm[]>([{ harness: "opencode", executorModel: "" }]);
  const [repetitions, setRepetitions] = useState(3);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [gatewayDefault, setGatewayDefault] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  // Once runs are queued the design is what ran, and steps 1–3 stop being
  // editable: changing them would leave this page describing runs that never
  // happened.
  const [launched, setLaunched] = useState(false);

  // Steps 4 and 5 — the evidence.
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);

  const allSkills = useMemo(() => {
    const staged = stagedSkills.map((s) => ({
      name: s.name,
      description: s.description || s.status,
      source: "staged" as const
    }));
    const governed = governedSkills.map((s) => ({
      name: s.name,
      description: s.description,
      source: "harness" as const
    }));
    return [...staged, ...governed];
  }, [stagedSkills, governedSkills]);

  const visibleSkills = useMemo(() => {
    let list = allSkills;
    if (filterTab === "staged") {
      list = list.filter((s) => s.source === "staged");
    } else if (filterTab === "harness") {
      list = list.filter((s) => s.source === "harness");
    }
    const q = searchSkill.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (s) => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
      );
    }
    return list;
  }, [allSkills, filterTab, searchSkill]);

  const filteredCases = useMemo(() => {
    const q = caseSearch.trim().toLowerCase();
    if (!q) return existingCases;
    return existingCases.filter((c) => {
      const matchId = c.case_id.toLowerCase().includes(q);
      const matchName = (c.name || "").toLowerCase().includes(q);
      const matchDesc = (c.description || "").toLowerCase().includes(q);
      const matchTags = (c.tags || []).some((t) => t.toLowerCase().includes(q));
      return matchId || matchName || matchDesc || matchTags;
    });
  }, [existingCases, caseSearch]);

  const selectExistingCase = (c: CaseRecord) => {
    setCaseId(c.case_id);
    setSubject(c.name || c.case_id);
    const variants = c.prompt_variants ? Object.keys(c.prompt_variants) : [];
    const variantKey = variants.includes("default")
      ? "default"
      : variants[0] || "default";
    setPromptVariant(variantKey);
    const firstPrompt =
      (c.prompt_variants && c.prompt_variants[variantKey]) ||
      c.description ||
      "";
    setPrompt(firstPrompt);
    setCommandText((c.validation_commands || []).join("\n"));
    if (c.max_steps) {
      setMaxSteps(c.max_steps);
    }
    setNotice(m.noticeCaseSelected(c.case_id));
  };

  const advanceWithExistingCase = () => {
    if (!caseId) return;
    setNotice(m.noticeUsingExistingCase(caseId));
    setStep("arms");
  };

  const commands = useMemo(
    () => commandText.split("\n").map((line) => line.trim()).filter(Boolean),
    [commandText]
  );
  const executable = useMemo(() => commands.filter(looksExecutable), [commands]);
  const rejected = useMemo(
    () => commands.filter((command) => !looksExecutable(command)),
    [commands]
  );

  useEffect(() => {
    api
      .listStagedSkills()
      .then(setStagedSkills)
      .catch(() => setError(m.errorLoadSkills));
    api
      .listHarnessSkills()
      .then(setGovernedSkills)
      .catch(() => {});
    api
      .listCases()
      .then((cases) => {
        setExistingCases(cases);
        if (cases.length === 0) {
          setCaseMode("new");
        }
      })
      .catch(() => {});

    api
      .listModels(false)
      .then((catalog) => {
        setModelOptions(catalog.models);
        const fallback = catalog.default_executor ?? "";
        setGatewayDefault(fallback);
        // A headless run needs a model — preflight refuses one without. The
        // opening arm is a gateway harness with no model until the catalog
        // says what the default is.
        setArms((current) =>
          current.map((arm) =>
            arm.executorModel || HARNESS_NATIVE_MODELS[arm.harness]
              ? arm
              : { ...arm, executorModel: fallback }
          )
        );
      })
      .catch(() => {
        // Gateway offline: the pickers fall back to their own empty state, and
        // a harness with native models does not need it at all.
      });
  }, []);

  // Poll only while the runs are the thing on screen. An experiment whose runs
  // have all landed stops polling by itself.
  useEffect(() => {
    if (step !== "watch" || !experimentId) {
      return;
    }
    let cancelled = false;
    const load = () => {
      api
        .listRuns()
        .then((all) => {
          if (!cancelled) {
            setRuns(all.filter((run) => run.experiment_id === experimentId));
          }
        })
        .catch(() => {
          // Transient: the next tick asks again.
        });
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [experimentId, step]);

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (workError) {
      setError(workError instanceof Error ? workError.message : m.errorSomethingFailed);
    } finally {
      setBusy("");
    }
  };

  const toggleSkill = (name: string) =>
    setSkillsExtra((current) =>
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name]
    );

  /**
   * Any change to the arms or the repetitions after a draft exists discards
   * the draft: the launch button must never queue a design other than the one
   * on screen. After launch the design is frozen instead.
   */
  const editArms = (update: (current: Arm[]) => Arm[]) => {
    if (launched) {
      return;
    }
    setArms(update);
    setExperimentId("");
    setWarnings([]);
  };
  const editRepetitions = (value: number) => {
    if (launched) {
      return;
    }
    setRepetitions(value);
    setExperimentId("");
    setWarnings([]);
  };

  const authorSkill = () =>
    run("author", async () => {
      const name = slugify(newSkillName);
      const created = await api.createStagedSkill(
        name,
        `Subject under validation: ${newSkillName.trim() || name}`,
        newSkillBody
      );
      setStagedSkills((current) => [...current, created]);
      setSkillsExtra((current) => [...current, created.name]);
      setNewSkillName("");
      setNewSkillBody("");
      setNotice(m.noticeStaged(created.name));
    });

  const importSkill = () =>
    run("import", async () => {
      const preview = await api.previewStagedSkillImport(importUrl, "", "");
      if (!preview.preview) {
        throw new Error(
          m.errorMultipleSkills(preview.choices.map((choice) => choice.name).join(", "))
        );
      }
      const imported = await api.importStagedSkill(importUrl, "", preview.name, false);
      setStagedSkills((current) => [...current, imported]);
      setSkillsExtra((current) => [...current, imported.name]);
      setImportUrl("");
      setNotice(m.noticeImported(imported.name, preview.source_url));
    });


  const generateCaseWithAi = () =>
    run("case-draft", async () => {
      setAiError(null);
      setAiNotice("");
      setAiDropped([]);
      try {
        const draft = await api.generateCaseDraft({
          description: aiDescription,
          model: aiModel || gatewayDefault || undefined,
          selected_skills: skillsExtra
        });
        if (draft.subject) {
          setSubject(draft.subject);
        }
        if (draft.instruction) {
          setPrompt(draft.instruction);
        }
        // The server already screened the commands: what is left is one shell
        // command per line, which is the only shape this textarea preserves.
        setCommandText((draft.validation_commands ?? []).join("\n"));
        if (draft.max_steps) {
          setMaxSteps(draft.max_steps);
        }
        setAiDropped(draft.dropped_commands ?? []);
        const kept = draft.validation_commands?.length ?? 0;
        const retried = draft.attempts > 1 ? m.retriedSuffix : "";
        if (kept === 0) {
          setAiError(m.aiErrorNoCommand(retried));
        } else {
          setAiNotice(m.aiNoticeDraftGenerated(kept, retried));
        }
      } catch (err) {
        setAiError(err instanceof Error ? err.message : m.errorGenerateCaseDraft);
      }
    });

  /**
   * Create the case under the first free id derived from the subject — or,
   * when this step is revisited, update the case already created rather than
   * leaving a trail of near-duplicates behind.
   */
  const createCase = () =>
    run("case", async () => {
      const payload = (caseIdCandidate: string) => ({
        case_id: caseIdCandidate,
        name: subject.slice(0, 100),
        type: "single_prompt" as const,
        description: `What '${subject}' is asked to do, and the command that says whether it did.`,
        tags: ["subject-validation"],
        prompt_variants: { default: prompt },
        step_prompts: [],
        success_criteria: {},
        scoring_rubric: "",
        constraints: [],
        validation_commands: executable,
        max_steps: maxSteps
      });
      if (caseId) {
        const updated = await api.updateCase(caseId, payload(caseId));
        setNotice(m.noticeCaseUpdated(updated.case_id));
        setStep("arms");
        return;
      }
      for (let attempt = 0; attempt < 5; attempt += 1) {
        const suffix = attempt === 0 ? "" : `-${attempt + 1}`;
        const candidate = `${slugify(subject).slice(0, 48) || "subject"}${suffix}`;
        try {
          const created = await api.createCase(payload(candidate));
          setCaseId(created.case_id);
          setNotice(m.noticeCaseCreated(created.case_id));
          setStep("arms");
          return;
        } catch (createError) {
          if (!isDuplicateId(createError)) {
            throw createError;
          }
        }
      }
      throw new Error(m.errorNoFreeCaseId);
    });

  /**
   * Create the design as a draft, so the backend's own warnings — the
   * repetition strength above all — are read before anything is queued.
   */
  const createDesign = () =>
    run("design", async () => {
      const base = slugify(`does-${subject}-work`);
      let lastError: unknown = null;
      for (let attempt = 0; attempt < 5; attempt += 1) {
        const candidate = attempt === 0 ? base : `${base}-${attempt + 1}`;
        try {
          const created = await api.createExperiment({
            experiment_id: candidate,
            name: subject.slice(0, 80),
            question: `Does ${subject} do its job reliably across harnesses and models?`,
            mode: "subject_validation",
            subject,
            case_ids: [caseId],
            repetitions,
            conditions: arms.map((arm) => ({
              label: armLabel(arm),
              role: "treatment",
              description: `${subject} under ${armLabel(arm)}.`,
              overrides: {
                harness: arm.harness,
                executor_model: arm.executorModel || null
              }
            }))
          });
          setExperimentId(created.experiment_id);
          setWarnings(created.warnings ?? []);
          setNotice(m.noticeDesignCreated(created.experiment_id));
          return;
        } catch (createError) {
          if (!isDuplicateId(createError)) {
            throw createError;
          }
          lastError = createError;
        }
      }
      throw lastError instanceof Error ? lastError : new Error(m.errorNoFreeExperimentId);
    });


  const launch = () =>
    run("launch", async () => {
      const stagedSelected = skillsExtra.filter((name) =>
        stagedSkills.some((s) => s.name === name)
      );
      const unselectedGoverned = isolateGoverned
        ? governedSkills
            .map((s) => s.name)
            .filter((name) => !skillsExtra.includes(name))
        : [];

      const result = await api.launchExperiment(experimentId, {
        case_id: caseId,
        prompt_variant: promptVariant || "default",
        harness: arms[0]?.harness ?? "opencode",
        planner_model: null,
        executor_model: arms[0]?.executorModel || null,
        score_mode: "manual",
        max_steps: maxSteps,
        resume: false,
        dry_run: false,
        // The subject travels with every arm: it is what is held constant while
        // the harness and the model vary around it.
        skills_extra: stagedSelected,
        skills_disabled: unselectedGoverned,
        // Every selection, governed ones included: a governed skill is
        // available too, and only staged ones need `skills_extra` to be.
        skills_invoke: invokeSelected ? [...skillsExtra] : []
      });
      setNotice(m.noticeQueued(result.count));
      setLaunched(true);
      setStep("watch");
    });

  const loadSummary = () =>
    run("summary", async () => {
      setSummary(await api.summarizeExperiment(experimentId));
      setStep("read");
    });

  const blocking = useMemo(() => {
    const problems: Record<StepKey, string[]> = {
      subject: [],
      success: [],
      arms: [],
      watch: [],
      read: []
    };
    if (skillsExtra.length === 0 && !alreadyGoverned) {
      problems.subject.push(m.blocking.chooseSkill);
    }
    if (caseMode === "existing") {
      if (!caseId) {
        problems.success.push(m.blocking.pickExistingCase);
      }
    } else {
      if (!subject.trim()) {
        problems.success.push(m.blocking.nameSubject);
      }
      if (!prompt.trim()) {
        problems.success.push(m.blocking.writeInstruction);
      }
      if (executable.length === 0) {
        problems.success.push(m.blocking.addValidationCommand);
      }
    }
    if (arms.length === 0) {
      problems.arms.push(m.blocking.declareArm);
    }
    if (new Set(arms.map(armLabel)).size !== arms.length) {
      problems.arms.push(m.blocking.duplicateArms);
    }
    if (arms.some((arm) => !arm.executorModel.trim())) {
      problems.arms.push(m.blocking.pickModel);
    }
    return problems;
  }, [
    alreadyGoverned,
    arms,
    caseId,
    caseMode,
    executable.length,
    m,
    prompt,
    skillsExtra.length,
    subject
  ]);


  const stepIndex = STEPS.findIndex((item) => item.key === step);
  const reached = (key: StepKey) => {
    // A step is reachable once every gate before it is clear. Going back is
    // always allowed — this refuses to skip forward, not to re-read.
    const index = STEPS.findIndex((item) => item.key === key);
    if (index <= stepIndex) {
      return true;
    }
    // The last two steps read runs, so they need runs to exist.
    if ((key === "watch" || key === "read") && !launched) {
      return false;
    }
    return STEPS.slice(0, index).every((item) => blocking[item.key].length === 0);
  };
  const goTo = (key: StepKey) => {
    if (key === "read" && !summary) {
      void loadSummary();
      return;
    }
    setStep(key);
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

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
            <button
              disabled={!reached(item.key)}
              onClick={() => goTo(item.key)}
              type="button"
            >
              <span className="stepper-index">{index + 1}</span>
              <span>{m.stepLabel[item.key]}</span>
            </button>
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner message={error} /> : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}
      {launched && step !== "watch" && step !== "read" ? (
        <p className="callout">{m.runsQueuedCallout(m.startNewValidation)}</p>
      ) : null}

      {step === "subject" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>{m.skillsPanel.heading}</h2>
              <Link className="button-link" to="/evidence/skills">
                {m.skillsPanel.skillsWorkbench}
              </Link>
            </div>
            <p className="muted">{m.skillsPanel.intro}</p>

            <div className="filter-stack">
              <div className="tab-bar">
                <button
                  className={`tab ${filterTab === "all" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("all")}
                  type="button"
                >
                  {m.skillsPanel.tabAll(allSkills.length)}
                </button>
                <button
                  className={`tab ${filterTab === "staged" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("staged")}
                  type="button"
                >
                  {m.skillsPanel.tabStaged(stagedSkills.length)}
                </button>
                <button
                  className={`tab ${filterTab === "harness" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("harness")}
                  type="button"
                >
                  {m.skillsPanel.tabHarness(governedSkills.length)}
                </button>
              </div>

              <input
                aria-label={m.skillsPanel.filterAriaLabel}
                onChange={(event) => setSearchSkill(event.target.value)}
                placeholder={m.skillsPanel.filterPlaceholder}
                type="search"
                value={searchSkill}
              />
            </div>

            {visibleSkills.length === 0 ? (
              <EmptyState
                description={m.skillsPanel.noSkillsDescription}
                title={m.skillsPanel.noSkillsTitle}
              />
            ) : (
              <ul className="pick-list">
                {visibleSkills.map((skill) => (
                  <li key={`${skill.source}-${skill.name}`}>
                    <button
                      className={skillsExtra.includes(skill.name) ? "primary" : "secondary"}
                      onClick={() => toggleSkill(skill.name)}
                      type="button"
                    >
                      <strong>
                        {skillsExtra.includes(skill.name) ? <IconCheck size={14} /> : null}
                        {skill.name}
                        <span className="badge pick-tag">{skill.source}</span>
                      </strong>
                      <span className="muted">{skill.description}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <div className="checkbox-stack">
              <label className="checkbox">
                <input
                  checked={invokeSelected}
                  onChange={(event) => setInvokeSelected(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  {m.skillsPanel.invokeSelected(
                    <InfoTooltip text={m.skillsPanel.invokeSelectedTooltip} />
                  )}
                </span>
              </label>

              <label className="checkbox">
                <input
                  checked={isolateGoverned}
                  onChange={(event) => setIsolateGoverned(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  {m.skillsPanel.isolateGoverned(
                    <InfoTooltip text={m.skillsPanel.isolateGovernedTooltip} />
                  )}
                </span>
              </label>

              <label className="checkbox">
                <input
                  checked={alreadyGoverned}
                  onChange={(event) => setAlreadyGoverned(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  {m.skillsPanel.alreadyGoverned(
                    <InfoTooltip text={m.skillsPanel.alreadyGovernedTooltip} />
                  )}
                </span>
              </label>
            </div>
          </div>


          <div className="panel">
            <div className="panel-head">
              <h2>{m.writeOne.heading}</h2>
            </div>
            <div className="form-grid">
              <label>
                <span>{m.writeOne.skillName}</span>
                <input
                  onChange={(event) => setNewSkillName(event.target.value)}
                  placeholder="notion-export"
                  value={newSkillName}
                />
              </label>
              <label className="full">
                <span>{m.writeOne.skillMd}</span>
                <textarea
                  onChange={(event) => setNewSkillBody(event.target.value)}
                  placeholder={"# notion-export\n\nCall POST http://localhost:8900/export with…"}
                  rows={8}
                  value={newSkillBody}
                />
              </label>
            </div>
            <div className="panel-actions">
              <button
                className="secondary"
                disabled={busy === "author" || !newSkillName.trim() || !newSkillBody.trim()}
                onClick={() => void authorSkill()}
                type="button"
              >
                {busy === "author" ? m.writeOne.staging : m.writeOne.stageButton}
              </button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>{m.importOne.heading}</h2>
            </div>
            <div className="form-grid">
              <label className="full">
                <span>{m.importOne.repoUrl}</span>
                <input
                  onChange={(event) => setImportUrl(event.target.value)}
                  placeholder="https://github.com/owner/repo"
                  value={importUrl}
                />
              </label>
            </div>
            <p className="muted">{m.importOne.note}</p>
            <div className="panel-actions">
              <button
                className="secondary"
                disabled={busy === "import" || !importUrl.trim()}
                onClick={() => void importSkill()}
                type="button"
              >
                {busy === "import" ? m.importOne.importing : m.importOne.importButton}
              </button>
            </div>
          </div>
        </>
      ) : null}

      {step === "success" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.successPanel.heading}</h2>
            {existingCases.length > 0 ? (
              <div className="tab-bar tab-bar--inline">
                <button
                  className={`tab ${caseMode === "existing" ? "tab--active" : ""}`}
                  onClick={() => setCaseMode("existing")}
                  type="button"
                >
                  {m.successPanel.useExisting(existingCases.length)}
                </button>
                <button
                  className={`tab ${caseMode === "new" ? "tab--active" : ""}`}
                  onClick={() => setCaseMode("new")}
                  type="button"
                >
                  {m.successPanel.createNew}
                </button>
              </div>
            ) : null}
          </div>

          {caseMode === "existing" ? (
            <div className="filter-stack">
              <p className="muted">{m.successPanel.pickExistingIntro}</p>

              <input
                aria-label={m.successPanel.filterCasesAriaLabel}
                onChange={(event) => setCaseSearch(event.target.value)}
                placeholder={m.successPanel.filterCasesPlaceholder}
                type="search"
                value={caseSearch}
              />

              {filteredCases.length === 0 ? (
                <EmptyState
                  description={m.successPanel.noCasesDescription}
                  title={m.successPanel.noCasesTitle}
                />
              ) : (
                <ul className="pick-list">
                  {filteredCases.map((c) => {
                    const isSelected = caseId === c.case_id;
                    return (
                      <li key={c.case_id}>
                        <button
                          className={isSelected ? "primary" : "secondary"}
                          onClick={() => selectExistingCase(c)}
                          type="button"
                        >
                          <strong>
                            {isSelected ? <IconCheck size={14} /> : null}
                            {c.name || c.case_id}
                            <span className="badge pick-tag">{c.type}</span>
                            {(c.tags || []).map((tag) => (
                              <span className="badge pick-tag pick-tag--soft" key={tag}>
                                {tag}
                              </span>
                            ))}
                          </strong>
                          <span className="muted">
                            <code>{c.case_id}</code> — {c.description || m.successPanel.noDescription}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}

              {caseId ? (
                <div className="panel subtle selection-summary">
                  <div>
                    <strong>{m.successPanel.selectedCase(caseId)}</strong>
                    <span className="muted small">
                      {m.successPanel.selectedCaseMeta(subject, maxSteps, promptVariant)}
                    </span>
                  </div>
                  <button
                    className="primary"
                    disabled={launched}
                    onClick={advanceWithExistingCase}
                    type="button"
                  >
                    {m.successPanel.useThisCase} <IconArrowRight size={14} />
                  </button>
                </div>
              ) : null}

              {blocking.success.length > 0 ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <div>
                    <strong>{m.notReadyYet}</strong>
                    <ul className="blocking-list">
                      {blocking.success.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <>
              {/* AI case assistant: optional, fills the form below. */}
              <div className="assist-card">
                <div className="assist-card-head">
                  <strong className="assist-card-title">
                    <IconSparkles size={16} /> {m.aiAssist.title}
                  </strong>
                  {skillsExtra.length > 0 ? (
                    <span className="muted small">
                      {m.aiAssist.contextWith(skillsExtra.join(", "))}
                    </span>
                  ) : (
                    <span className="muted small">{m.aiAssist.contextWithout}</span>
                  )}
                </div>

                <label>
                  <span>{m.aiAssist.describeLabel}</span>
                  <textarea
                    onChange={(event) => setAiDescription(event.target.value)}
                    placeholder={m.aiAssist.describePlaceholder}
                    rows={2}
                    value={aiDescription}
                  />
                </label>

                <div className="assist-card-row">
                  <label>
                    <span>{m.aiAssist.modelLabel}</span>
                    <select
                      onChange={(event) => setAiModel(event.target.value)}
                      value={aiModel || gatewayDefault}
                    >
                      {modelOptions.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label || opt.id}{" "}
                          {opt.id === gatewayDefault ? m.aiAssist.defaultSuffix : ""}
                        </option>
                      ))}
                      {!modelOptions.some((opt) => opt.id === gatewayDefault) && gatewayDefault ? (
                        <option value={gatewayDefault}>
                          {gatewayDefault}
                          {m.aiAssist.defaultSuffix}
                        </option>
                      ) : null}
                    </select>
                  </label>

                  <button
                    className="primary"
                    disabled={busy === "case-draft" || !aiDescription.trim()}
                    onClick={() => void generateCaseWithAi()}
                    type="button"
                  >
                    {busy === "case-draft" ? (
                      <Spinner label={m.aiAssist.drafting} />
                    ) : (
                      <>
                        <IconSparkles size={14} /> {m.aiAssist.generate}
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
                      <strong>{m.aiAssist.leftOutTitle}</strong>
                      <ul className="blocking-list dropped-list">
                        {aiDropped.map((item) => (
                          <li key={item.command}>
                            <code>{item.command.split("\n")[0]}</code>
                            {item.command.includes("\n") ? " …" : ""}
                            {/* item.reason is backend-composed and stays in English. */}
                            <span className="muted small"> — {item.reason}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="form-grid">
                <label className="full">
                  <span>{m.caseForm.subject}</span>
                  <input
                    onChange={(event) => setSubject(event.target.value)}
                    placeholder={m.caseForm.subjectPlaceholder}
                    value={subject}
                  />
                </label>
                <label className="full">
                  <span>{m.caseForm.instruction}</span>
                  <textarea
                    onChange={(event) => setPrompt(event.target.value)}
                    placeholder={m.caseForm.instructionPlaceholder}
                    rows={4}
                    value={prompt}
                  />
                </label>
                <label className="full">
                  <span>{m.caseForm.validationCommands}</span>
                  <textarea
                    onChange={(event) => setCommandText(event.target.value)}
                    placeholder={m.caseForm.validationPlaceholder}
                    rows={5}
                    value={commandText}
                  />
                </label>
                <label>
                  <span>{m.caseForm.maxSteps}</span>
                  <select
                    onChange={(event) => setMaxSteps(Number(event.target.value))}
                    value={maxSteps}
                  >
                    {MAX_STEP_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="muted">{m.caseForm.explanation}</p>
              {rejected.length > 0 ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <div>
                    <strong>{m.caseForm.notExecutable}</strong>
                    <ul className="blocking-list">
                      {rejected.map((command) => (
                        <li key={command}>{command}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}
              {blocking.success.length > 0 ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <div>
                    <strong>{m.notReadyYet}</strong>
                    <ul className="blocking-list">
                      {blocking.success.map((problem) => (
                        <li key={problem}>{problem}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : null}
              <div className="panel-actions">
                <button
                  className="primary"
                  disabled={busy === "case" || launched || blocking.success.length > 0}
                  onClick={() => void createCase()}
                  type="button"
                >
                  {busy === "case"
                    ? m.caseForm.saving
                    : caseId
                      ? m.caseForm.updateCase
                      : m.caseForm.createCase}{" "}
                  <IconArrowRight size={14} />
                </button>
              </div>
            </>
          )}
        </div>
      ) : null}


      {step === "arms" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.armsPanel.heading}</h2>
            <span className="muted">{m.armsPanel.noBaseline}</span>
          </div>
          <p className="muted">{m.armsPanel.intro}</p>
          <ul className="pick-list arm-list">
            {arms.map((arm, index) => (
              <li className="panel subtle" key={`${arm.harness}-${index}`}>
                <div className="form-grid">
                  <label>
                    <span>{m.armsPanel.harness}</span>
                    <select
                      onChange={(event) => {
                        const harness = event.target.value;
                        const native = HARNESS_NATIVE_MODELS[harness];
                        editArms((current) =>
                          current.map((item, position) =>
                            position === index
                              ? {
                                  harness,
                                  executorModel: native ? native[0].id : gatewayDefault
                                }
                              : item
                          )
                        );
                      }}
                      value={arm.harness}
                    >
                      {HARNESS_OPTIONS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                    {HOST_HARNESSES.has(arm.harness) ? (
                      <span className="muted small">
                        {m.armsPanel.runsNativeAuth(arm.harness)}
                        {arm.harness === "claude"
                          ? m.armsPanel.claudeAuthNote
                          : m.armsPanel.hostModeNote}
                      </span>
                    ) : null}
                  </label>
                  <div className="full">
                    <ModelPicker
                      label={m.armsPanel.executorModel}
                      nativeOptions={HARNESS_NATIVE_MODELS[arm.harness]}
                      onChange={(modelId) =>
                        editArms((current) =>
                          current.map((item, position) =>
                            position === index ? { ...item, executorModel: modelId } : item
                          )
                        )
                      }
                      options={modelOptions}
                      value={arm.executorModel}
                    />
                  </div>
                </div>
                <div className="panel-actions">
                  <span className="badge neutral">{armLabel(arm)}</span>
                  {arms.length > 1 ? (
                    <button
                      className="ghost"
                      disabled={launched}
                      onClick={() =>
                        editArms((current) =>
                          current.filter((_item, position) => position !== index)
                        )
                      }
                      type="button"
                    >
                      <IconX size={14} /> {m.armsPanel.remove}
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
          <div className="panel-actions">
            <button
              className="secondary"
              disabled={launched}
              onClick={() =>
                editArms((current) => [
                  ...current,
                  { harness: "claude", executorModel: HARNESS_NATIVE_MODELS.claude[0].id }
                ])
              }
              type="button"
            >
              <IconPlus size={14} /> {m.armsPanel.addArm}
            </button>
          </div>
          <div className="form-grid">
            <label>
              <span>{m.armsPanel.repetitions}</span>
              <select
                disabled={launched}
                onChange={(event) => editRepetitions(Number(event.target.value))}
                value={repetitions}
              >
                {REPETITION_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="muted">
            {m.armsPanel.armsSummary(arms.length, repetitions, arms.length * repetitions)}
          </p>
          {blocking.arms.length > 0 ? (
            <ul className="blocking-list">
              {blocking.arms.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          ) : null}
          {warnings.length > 0 ? (
            <ul className="blocking-list blocking-list--warning">
              {/* warnings are backend-composed and stay in English. */}
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
          <div className="panel-actions">
            {experimentId ? (
              <button
                className="primary"
                disabled={busy === "launch" || launched}
                onClick={() => void launch()}
                type="button"
              >
                {busy === "launch"
                  ? m.armsPanel.queueing
                  : m.armsPanel.launch(arms.length * repetitions)}
              </button>
            ) : (
              <button
                className="primary"
                disabled={busy === "design" || blocking.arms.length > 0}
                onClick={() => void createDesign()}
                type="button"
              >
                {busy === "design" ? m.armsPanel.creating : m.armsPanel.createDesign}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {step === "watch" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.watchPanel.heading}</h2>
            <Link className="button-link" to={`/evaluate/experiments?experiment=${experimentId}`}>
              {m.watchPanel.openExperiment}
            </Link>
          </div>
          <p className="muted">{m.watchPanel.intro}</p>
          {runs.length === 0 ? (
            <Spinner label={m.watchPanel.waitingForRuns} />
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>{m.watchPanel.run}</th>
                  <th>{m.watchPanel.arm}</th>
                  <th>{m.watchPanel.state}</th>
                  <th>{m.watchPanel.passed}</th>
                  <th>{m.watchPanel.yourJudgement}</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((item) => (
                  <tr key={item.run_id}>
                    <td>
                      <Link to={`/advanced/runs/${item.run_id}`}>{item.run_id}</Link>
                    </td>
                    <td>{item.condition_label ?? "—"}</td>
                    <td>
                      <StateBadge state={item.state} />
                    </td>
                    <td>
                      {item.objective_passed === null || item.objective_passed === undefined
                        ? "—"
                        : item.objective_passed
                          ? m.watchPanel.pass
                          : m.watchPanel.fail}
                    </td>
                    <td>
                      <Link to={`/advanced/runs/${item.run_id}?tab=terminal`}>
                        {m.watchPanel.openSandbox}
                      </Link>
                      {item.score === null || item.score === undefined
                        ? null
                        : m.watchPanel.scored(item.score)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="panel-actions">
            <button
              className="primary"
              disabled={busy === "summary" || !experimentId}
              onClick={() => void loadSummary()}
              type="button"
            >
              {busy === "summary" ? m.watchPanel.reading : m.watchPanel.readResult}
            </button>
          </div>
        </div>
      ) : null}

      {step === "read" && summary ? (
        <>
          {summary.verdict ? (
            <div className="card verdict verdict--neutral">
              <div className="verdict-head">
                <div>
                  {/* summary.verdict.* is backend-composed and stays in English. */}
                  <p className="verdict-kicker">{m.readPanel.resultKicker}</p>
                  <h2>{summary.verdict.headline}</h2>
                  <p className="muted">{summary.verdict.claim}</p>
                </div>
                <span className="badge neutral verdict-strength">
                  {summary.verdict.strength}
                </span>
              </div>
              <p className="verdict-evidence muted">
                {m.readPanel.evidence(summary.verdict.evidence)}
              </p>
              {summary.verdict.caveats.length > 0 ? (
                <ul className="warning-list">
                  {summary.verdict.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <div className="panel">
            <div className="panel-head">
              <h2>{m.readPanel.onePerArm}</h2>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>{m.readPanel.arm}</th>
                  <th>{m.readPanel.passed}</th>
                  <th>{m.readPanel.scoredRuns}</th>
                  <th>{m.readPanel.costMean}</th>
                </tr>
              </thead>
              <tbody>
                {summary.conditions.map((condition) => (
                  <tr key={condition.label}>
                    <td>{condition.label}</td>
                    <td>{formatPercent(condition.success_rate)}</td>
                    <td>
                      {condition.scored_runs}/{condition.runs}
                    </td>
                    <td>
                      {condition.metrics.cost_usd?.mean === null ||
                      condition.metrics.cost_usd?.mean === undefined
                        ? "—"
                        : `$${condition.metrics.cost_usd.mean.toFixed(2)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">{m.readPanel.notAveraged}</p>
          </div>

          {summary.case_outcomes ? (
            <div className="panel">
              <div className="panel-head">
                <h2>{m.readPanel.perCase}</h2>
                <span className="muted">{m.readPanel.passedOfScored}</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{m.readPanel.case}</th>
                    {Object.keys(summary.case_outcomes).map((label) => (
                      <th key={label}>{label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ...new Set(
                      Object.values(summary.case_outcomes).flatMap((byCase) =>
                        Object.keys(byCase)
                      )
                    )
                  ].map((caseKey) => (
                    <tr key={caseKey}>
                      <td>{caseKey}</td>
                      {Object.entries(summary.case_outcomes ?? {}).map(([label, byCase]) => {
                        const cell = byCase[caseKey];
                        return (
                          <td key={label}>
                            {!cell || cell.scored_runs === 0
                              ? "—"
                              : m.readPanel.successesOfScored(cell.successes, cell.scored_runs)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          <div className="panel-actions">
            <button
              className="secondary"
              onClick={() => navigate(`/evaluate/experiments?experiment=${experimentId}`)}
              type="button"
            >
              {m.readPanel.openFullExperiment}
            </button>
            <button className="ghost" onClick={() => setStep("watch")} type="button">
              {m.readPanel.backToRuns}
            </button>
          </div>
        </>
      ) : null}

      <HelpPanel docHref="docs/guide-skill-validation.md" title={m.helpPanel.title}>
        <p>{m.helpPanel.body1}</p>
        <p>{m.helpPanel.body2(m.helpPanel.harnessEvaluation)}</p>
      </HelpPanel>
    </section>
  );
}
