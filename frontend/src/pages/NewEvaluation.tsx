import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api";
import {
  defaultNativeModel,
  HARNESS_NATIVE_MODELS,
  HARNESS_OPTIONS,
  HOST_HARNESSES,
  useNativeModelIndex
} from "../harnesses";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { HostGuardModal } from "../components/HostGuardModal";
import { ModelPicker } from "../components/ModelPicker";
import { PromptPreview } from "../components/PromptPreview";
import { IconAlert, IconArrowRight } from "../components/icons";
import { defineMessages, useMessages } from "../i18n";
import type {
  CaseRecord,
  ExperimentMode,
  HarnessSkill,
  LocalModelLoadCheck,
  ModelOption,
  RunConfigPayload,
  RunValidationResult,
  StagedSkillRecord,
  TargetRecord
} from "../types";


/**
 * What a person is actually changing when they evaluate a harness change.
 *
 * Each factor maps to one condition override field. Only one is offered at a
 * time on purpose: an experiment that varies two harness factors at once cannot
 * attribute a difference to either, and the backend will say so (ADR-0028).
 */
const FACTORS = [
  {
    key: "harness_source",
    control: { harness_source: "native" },
    controlLabel: "Repository's own setup",
    treatmentLabel: "Lab harness injected"
  },
  {
    key: "full_harness",
    controlLabel: "Bare — empty workspace, governance masked, skills hidden",
    treatmentLabel: "Full harness"
  },
  {
    key: "governance",
    controlLabel: "Bare — an empty workspace, nothing loaded",
    treatmentLabel: "Governance body, skills hidden"
  },
  {
    key: "skills_extra",
    controlLabel: "Without the skills",
    treatmentLabel: "With the skills"
  },
  {
    key: "skills_disabled",
    controlLabel: "With the skills",
    treatmentLabel: "Without the skills"
  },
  {
    key: "prompt",
    controlLabel: "Plain prompt",
    treatmentLabel: "Harness prompt"
  },
  {
    key: "activation",
    controlLabel: "Harness prompt, skills available",
    treatmentLabel: "Harness prompt, skills invoked"
  },
  {
    key: "harness",
    controlLabel: "Current harness",
    treatmentLabel: "Alternative harness"
  }
] as const;

type FactorKey = (typeof FACTORS)[number]["key"];

const MESSAGES = defineMessages({
  en: {
    factorLabel: {
      harness_source: "Governance source",
      full_harness: "Full harness",
      governance: "Governance body",
      skills_extra: "Add skills",
      skills_disabled: "Remove skills",
      prompt: "Harness prompt",
      activation: "Activation",
      harness: "Swap the harness"
    } as Record<FactorKey, string>,
    factorBlurb: {
      harness_source:
        "The repository's own governance versus the harness this lab injects. The honest baseline for 'does our harness help this team?'.",
      full_harness:
        "The whole harness as one treatment: the governance-loaded prompt plus " +
        "every skill, against a bare arm with the skills hidden and a plain " +
        "prompt. A win says the harness earns its keep as a unit — ablate " +
        "factors afterwards to see which parts do the work.",
      governance:
        "The rulebook alone: CLAUDE.md, the SessionStart hook, the subagents, " +
        "the slash commands, the .github/ governance and the MCP servers — " +
        "against nothing at all. Both arms hide every skill and run the same " +
        "prompt, so a difference is about the governance and not about the " +
        "catalog. Lab venue only.",
      skills_extra:
        "Baseline versus baseline plus one or more staged skills — skills being " +
        "trialled from the Skills page, not yet part of the harness. One skill is " +
        "the narrowest test; several test the bundle as a unit.",
      skills_disabled:
        "Ablation of skills already in the harness: if removing them changes " +
        "nothing, they were not earning their place in the context window. This " +
        "is how a governed skill's value is measured — it is already present, so " +
        "the comparison is with it versus without it.",
      prompt:
        "The preamble alone: the plain task against the task with the harness " +
        "instruction prepended (read .github/, use these skills), with the " +
        "governance body and the whole catalog present on both arms. Rung 3 of " +
        "the ladder — what the wording adds once everything is already there.",
      activation:
        "Being asked outright. Both arms carry the harness prompt and the whole " +
        "catalog; the treatment also gets the activation block that names every " +
        "skill and requires the agent to invoke it at the start of each step. " +
        "Rung 4 of the ladder — the difference between a harness on disk and a " +
        "harness in use (ADR-0041).",
      harness: "The same case and model driven by a different agent CLI."
    } as Record<FactorKey, string>,
    needsTarget: " — needs an external repository",
    needsLab: " — lab venue only",
    unableToLoadData: "Unable to load repositories and cases.",
    unableToCheck: "Unable to check",
    suiteNeedsControlAndTreatment: "A suite comparison needs one control arm and one treatment.",
    filedAs: (experimentId: string) =>
      `An experiment already asks this question, so this one was filed as ` +
      `'${experimentId}'. The earlier one is untouched.`,
    unableToFindFreeId: "Unable to find a free experiment id.",
    unableToCreateExperiment: "Unable to create experiment",
    experimentCreatedLaunchRefused: (experimentId: string, reason: string) =>
      `Experiment '${experimentId}' was created, but launching was refused: ${reason}`,
    unknownError: "unknown error",
    // Cross-repo arms
    referenceRepo: (id: string) => `Reference repository — the others are read against ${id}.`,
    sameHarnessAgainst: (id: string) => `The same harness, unchanged, against ${id}.`,
    // harness_source
    nativeGovernanceDescription: "The repository's own governance, with nothing injected.",
    injectedGovernanceDescription: "The lab harness injected into the workspace.",
    // harness factor
    harnessInUseToday: "The harness in use today.",
    alternativeUnderTest: "The alternative under test.",
    // full_harness
    invokedListMany: (count: number) => `its ${count} governed skills`,
    invokedNote: (list: string) =>
      ` The agent is asked at the start of every step to invoke ${list} and follow them.`,
    bareDescriptionLab: (bareVariant: string) =>
      `Empty workspace, the lab's governance masked, plain prompt ('${bareVariant}'), every governed skill hidden.`,
    bareDescriptionSamePrompt: (bareVariant: string) =>
      `Same prompt ('${bareVariant}') with every governed skill hidden.`,
    bareDescriptionTargetPrompt: (bareVariant: string) =>
      `Plain prompt ('${bareVariant}') with every governed skill hidden.`,
    unknownVariant: "?",
    fullHarnessDescription: (harnessVariant: string, invokedNote: string) =>
      `Governance prompt ('${harnessVariant}') with the whole skill ` +
      "catalog available." +
      invokedNote,
    // prompt / activation
    plainPromptDescription: (plain: string) =>
      `Plain prompt ('${plain}'), governance body and every governed skill available.`,
    harnessPromptDescription: (governed: string) =>
      `Harness prompt ('${governed}'), same governance body and catalog.`,
    harnessPromptNotAskedDescription: (governed: string) =>
      `Harness prompt ('${governed}') with every governed skill available, not asked for.`,
    activatedDescription: (governed: string, count: number) =>
      `Harness prompt ('${governed}') plus the activation block naming its ` +
      `${count} governed skills at the start of every step.`,
    // governance
    bareGovernanceDescription:
      "An empty workspace with the lab's governance masked in the " +
      "container: no CLAUDE.md, no hooks, no subagents, no .github/, " +
      "no MCP servers, and no skills.",
    governanceOnlyDescription:
      "The lab's governance as the agent normally meets it — CLAUDE.md, " +
      "the SessionStart hook, the subagents, the slash commands and " +
      ".github/ — with the same skills hidden on both sides.",
    // skills_extra / skills_disabled
    governedHarnessToday: (count: number) =>
      `the governed harness as it is today (CLAUDE.md + ${count} governed skills)`,
    governedHarnessTodayNoSkills: "the governed harness as it is today",
    theSelectedSkills: "the selected skills",
    theStagedSkills: "the staged skills",
    askedToInvoke: ", and the agent is asked at the start of every step to invoke it and follow it.",
    ablationStillAvailable: (governed: string, listed: string) =>
      `${governed}, with ${listed} still available`,
    ablationWithoutStaged: (governed: string, listed: string) => `${governed}, without ${listed}.`,
    ablationRemoved: (listed: string) => `The same harness with ${listed} removed.`,
    additionAvailable: (listed: string) => `The same harness with ${listed} added as an available skill`,
    availableNotForced:
      ". Available, not forced: the agent invokes it only if " +
      "the case's instruction or the skill's own triggers call for it.",
    // suggested questions
    crossRepoQuestion1:
      "Does this harness hold up across repositories, or does it degrade outside the one it was built in?",
    crossRepoQuestion2:
      "Do the same cases score the same way in every repository, or is one repository carrying the result?",
    fullHarnessQuestion1: "Does the full harness improve outcomes over the bare model on these cases?",
    fullHarnessQuestion2:
      "Running the same prompt with and without the harness, does the governed skill catalog change the outcome?",
    fullHarnessQuestionEnforced: (listed: string) =>
      `When the agent is told to use ${listed}, does the harness beat the bare run on the same prompt?`,
    fullHarnessQuestionNotEnforced:
      "Is the harness worth what it costs here — better outcomes for the extra steps and tokens?",
    theGovernedSkills: "the governed skills",
    thisStagedSkill: "this staged skill",
    thisSkill: "this skill",
    skillsExtraQuestion1: (listed: string) => `Does adding ${listed} improve outcomes?`,
    skillsExtraQuestion2: (listed: string) =>
      `Does ${listed} earn a place in the harness, or is the baseline already good enough here?`,
    skillsDisabledQuestion1: (listed: string) => `Does removing ${listed} change outcomes at all?`,
    skillsDisabledQuestion2: (listed: string) =>
      `If we dropped ${listed} from the harness, would these cases notice?`,
    harnessFactorQuestion1: (treatment: string, control: string) =>
      `Does ${treatment} beat ${control} on the same cases and model?`,
    harnessFactorQuestion2: (control: string, treatment: string) =>
      `Do ${control} and ${treatment} differ in what they finish, or only in how they get there?`,
    defaultQuestion1: "Does the injected lab harness improve over the repository's own setup?",
    defaultQuestion2:
      "For a team that already has its own governance, is our harness an improvement or only a difference?",
    // blocking
    writeQuestion: "Write the question this experiment answers.",
    chooseAtLeastOneCase: "Choose at least one case.",
    sharedVariantProblem: (families: string) =>
      "The selected cases share no prompt variant, so any choice would hand " +
      "some of them an empty prompt. Keep one prompt family: " +
      `${families}.`,
    crossRepoNeedsTwo: "Cross-repo needs at least two repositories to vary.",
    chooseRepositoryForArms: "Choose the repository every arm will run against.",
    harnessSourceNeedsTarget:
      "Native versus injected governance needs an external repository — " +
      "inside the lab there is no other governance to compare against. " +
      "To compare the lab's own governance against nothing, use " +
      "'Governance body'.",
    labOnlyFactor: (label: string) =>
      `'${label}' is a rung of the lab's own harness ladder, which ` +
      "only the lab venue can stage. Against an external repository use " +
      "'Governance source'.",
    chooseAtLeastOneSkill: "Choose at least one skill for the treatment to vary.",
    suiteCannotCarryPair:
      "The regression suite fixes its own prompts, so it cannot carry the " +
      "bare-versus-governed pair. Pick cases by hand, or choose another factor.",
    identicalArmsNoSkills:
      "Both arms use the same prompt variant and there are no governed " +
      "skills to hide, so the two arms would be identical.",
    chooseSkillsForActivation:
      "Choose the governed skills to name in the activation block, or " +
      "clear 'Ask the agent to use the harness'.",
    // advisories
    forcedSinglePrompt: "The selected cases define a single prompt, so both arms run it. ",
    bothArmsRunPrompt: (forced: string, bareVariant: string) =>
      `${forced}Both arms run '${bareVariant}', so what moves is `,
    governanceAndCatalogFixed:
      "the governance body plus the skill catalog, with the prompt " +
      "held fixed. That is a sound comparison — it is the harness " +
      "minus its prompt — but read the result as a claim about what " +
      "the agent could load, not about the wording it was given.",
    catalogAloneAblation:
      "the skill catalog alone rather than the harness as a unit. " +
      "That is a sound ablation — it is what the 'Remove skills' " +
      "factor states directly — but read the result as a claim about " +
      "the skills, not about the whole harness.",
    partialAblationNote:
      "Inside this lab both arms still carry the governance body of " +
      "CLAUDE.md, the SessionStart hook and the .github/ governance — only " +
      "the generated skills catalog differs. The control arm is not a bare " +
      "model. For a bare-versus-governed comparison use 'Governance body' " +
      "(the rulebook against nothing) or 'Full harness' (everything " +
      "against nothing).",
    bundleTogether: (count: number, moved: string) =>
      `${count} skills are ${moved} together, so a difference ` +
      "belongs to the bundle and cannot be attributed to any one of them. " +
      "To evaluate one skill, select that skill alone.",
    removedFromTreatment: "removed from the treatment arm",
    stagedInTreatment: "staged in the treatment arm",
    leftOutAvailable: (_isPlural: boolean) => "available to both arms and tested by neither.",
    leftOutAbsent: (_isPlural: boolean) => "absent from both arms and tested by neither.",
    leftOutSuffix: (names: string, isPlural: boolean, rest: string) =>
      ` ${names} ${isPlural ? "are" : "is"} left out: ${rest} If that is the skill you mean to evaluate, the selection is inverted.`,
    armsDifferInPrompt: (bareVariant: string, harnessVariant: string, governanceClause: string) =>
      `The arms differ in prompt ('${bareVariant}' vs '${harnessVariant}')` +
      governanceClause +
      " and in skill catalog. That is the harness as a unit, and a " +
      "difference cannot be attributed to any one part. For a claim about " +
      "the skills alone use 'Add skills' or 'Remove skills'; for the " +
      "rulebook alone use 'Governance body'; to run one prompt on both " +
      "arms, set them equal above.",
    inGovernanceBody: ", in governance body",
    verbIs: "is",
    verbAre: "are",
    variantsDroppedNote: (dropped: string, verbIsAre: string, offered: string) =>
      `${dropped} ` +
      `${verbIsAre} declared by some of the ` +
      "selected cases but not all of them, so the prompt pickers offer " +
      `only ${offered}. ` +
      "An experiment runs one design across every case it selects.",
    bareArmToldToFollow: (promptVariant: string) =>
      `Both arms run '${promptVariant}', which directs the agent through ` +
      "the lab's governance — the bare arm cannot read any of it. Pick the " +
      "plain variant so the comparison is about what each arm had, not " +
      "about one arm being told to use what it did not.",
    // form
    title: "New evaluation",
    lede: "The design is built from your answers, so the experiment can answer the question it declares.",
    evaluateHarnessChange: "Evaluate a harness change",
    testAcrossRepositories: "Test across repositories",
    whatAreYouTryingToFindOut: "What are you trying to find out?",
    requiredQuestion: "required — the experiment is built around this question",
    placeholderCrossRepo:
      "Does our review skill hold up outside the repository it was written for?",
    placeholderHarnessEval: "Does the planning skill reduce steps without losing correctness?",
    useASuggestedQuestion: "Use a suggested question: ",
    whatVaries: "What varies",
    whereCasesRun: "Where the cases run (held fixed across both arms)",
    thisLab: (
      <>
        <strong>This lab</strong> — the harness under test is the lab&apos;s own
        governance, the synced <code>ml-python-base</code> release. This is the
        continuous-improvement loop: change a skill, measure it, release,
        re-measure.
      </>
    ),
    externalRepo: (
      <>
        <strong>An external repository</strong> — the agent works inside
        someone else&apos;s codebase, provisioned from the pinned local checkout
        for each run.
      </>
    ),
    repository: "Repository",
    selectRepository: "Select a repository",
    noRepositoriesRegistered:
      "No repositories registered yet — add one under Evidence → Repositories, or keep the evaluation in this lab.",
    factorUnderTest: "Factor under test",
    bareArmPrompt: "Bare arm prompt",
    bareArmPromptHint:
      "The plain task prompt — no governance instructions, and every governed skill hidden for this arm.",
    fullHarnessArmPrompt: "Full-harness arm prompt",
    fullHarnessArmPromptHint: (count: number) =>
      `The governance-loaded variant, with the whole skill catalog (${count}) available.`,
    pickCasesFirst:
      "Pick cases below first — the bare and governed prompts come from the variants those cases define.",
    noSharedPromptNarrow:
      "The selected cases share no prompt variant, so there is no pair that runs on all of them. Narrow the selection to one prompt family.",
    activationLegend: "Activation on the full-harness arm",
    askAgentToUseHarness: "Ask the agent to use the harness",
    askAgentToUseHarnessHint: (
      <>
        <strong>On by default, with the whole catalog selected.</strong> A
        prompt written in this UI says nothing about governance, so the
        full-harness arm may never open a skill and the pair measures only
        what sat on disk. This names the skills below in an activation block
        at the start of every step of that arm; the bare arm never gets it —
        it cannot see those skills at all. Untick individual skills to narrow
        the block, or turn the whole thing off only to ask the different
        question: whether the agent reaches for them unprompted.
      </>
    ),
    skillsNamedTogether: (count: number) =>
      `${count} skills named — they are asked for together, so a win belongs to the harness as a whole and not to any one of them.`,
    stagedSkillsToAdd: "Staged skills to add",
    governedSkillsToRemove: "Governed skills to remove",
    nothingStagedToOffer: (
      <>
        Nothing staged to offer. <code>skills_extra</code> only accepts staged
        skills — import or draft one under Evidence → Skills first. To measure
        a skill that is already part of the harness, use{" "}
        <strong>Remove skills</strong> instead: it is already present, so its
        value is measured by taking it away.
      </>
    ),
    enforceSkillUse: "Enforce skill use",
    enforceSkillUseHint: (
      <>
        Tell the agent, at the start of every step, to invoke the selected
        skill(s) and follow them for the whole task. Off = the skill is only
        available and the agent decides. On = the test of &ldquo;does it help
        when asked for&rdquo;; Off = &ldquo;does it help unasked&rdquo;.
      </>
    ),
    skillsSelectedBundle: (count: number) => (
      <>
        {count} skills selected — the treatment is the <strong>bundle</strong>.
        A win says the bundle helps; it cannot be attributed to any single
        member. To rank them, run one-skill-at-a-time experiments afterwards.
      </>
    ),
    controlHarness: "Control harness",
    treatmentHarness: "Treatment harness",
    harnessHeldFixedAcrossRepos: "Harness (held fixed across every repository)",
    repositories: "Repositories",
    noRepositoriesAddOne: "No repositories registered yet. Add one under Evidence → Repositories.",
    resultsStayPerRepo: (
      <>
        Results stay per repository. A pooled score across different codebases
        would treat them as one condition, which they are not. The first
        repository you pick becomes the <strong>reference</strong> the others
        are read against — a reference, not a baseline: nothing here supports
        a causal claim.
      </>
    ),
    casesAndRepetitions: "Cases and repetitions",
    fromTheSuite: "from the suite",
    useRegressionSuite: "Use the regression suite — validating a harness release against the canonical cases",
    suite: "Suite",
    suiteDeclaresDesign: (
      <>
        The suite declares which cases are representative and how often each
        is worth repeating, so neither is chosen here. What comes back is an
        ordinary harness-evaluation experiment: the same design validation,
        the same case matrix, the same{" "}
        <strong>fixed / broken / unchanged</strong> reading. There is no
        second runner.
      </>
    ),
    casesLegend: "Cases (every arm runs all of them)",
    repetitionsPerCasePerArm: "Repetitions per case, per arm",
    repetitionChoices: {
      exploratory: "1 — exploratory",
      judgement: "2 — judgement",
      validation: "5 — validation"
    },
    agentsStochastic:
      "Agents are stochastic. One repetition per arm is exploratory and gets no directional headline; two is a judgement; five is a validation.",
    howItRuns: "How it runs",
    sharedByEveryArm:
      "Shared by every arm — the factor above is the only thing that differs between them, so nothing picked here can leak into the comparison as a confound.",
    designVariesHarness: "The design varies the harness, so each arm uses its own. Pick a model both CLIs can address, or leave the default.",
    harnessHeldFixedAcrossArms: "Harness (held fixed across both arms)",
    runsHarnessCli: (harness: string) => `Runs the ${harness} CLI with its native auth. `,
    claudeAuthHint:
      "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host).",
    hostModeHint: "Needs host mode — run make app-host.",
    executorModelLabel: "Executor model",
    executorModelHeldFixed: "Held fixed across every arm — the experiment varies the factor above, never the model.",
    plannerModelLabel: "Planner model",
    promptVariantLabel: "Prompt variant",
    suiteDeclaresOwnPrompt: "The suite declares its own.",
    setPerArmAbove: "Set per arm above — bare versus governed is the treatment.",
    variantsEverySelectedCase:
      "Variants every selected case defines — one case short of a variant is enough for the launch to be refused.",
    stepBudget: "Step budget",
    stepBudgetHint: "Bumped to the largest budget any selected case declares.",
    dryRunLabel: "Dry run — build the commands without executing them",
    designAboutToCreate: "The design you are about to create",
    arm: (count: number) => `${count} arm${count === 1 ? "" : "s"}`,
    suiteCasesAndRepetitions: " × the suite's cases and repetitions",
    caseCountTimes: (count: number) => ` × ${count} case${count === 1 ? "" : "s"} × `,
    repetitionsEqualsRuns: (repetitions: number, runs: number) =>
      `${repetitions} = ${runs} run${runs === 1 ? "" : "s"}`,
    roleLabel: { control: "control", treatment: "treatment" } as Record<string, string>,
    notReadyYet: "Not ready yet — the buttons below unlock when this is resolved:",
    ready: "Ready",
    blocked: "Blocked",
    checkSuiteResolvedAtLaunch: "The suite's cases are resolved at launch — the same checks run there.",
    pickAtLeastOneCaseFirst: "Pick at least one case first.",
    checking: "Checking…",
    checkConfiguration: "Check configuration",
    loadCheckTitle: (model: string, size: string, host: string) =>
      `Checking this configuration will load ${model} (~${size} GB) on ${host}.`,
    loadCheckContinue: "Continue",
    loadCheckCancel: "Cancel",
    working: "Working…",
    createAndLaunchRuns: "Create & launch runs",
    createWithoutLaunching: "Create without launching",
    experimentExists: (link: ReactNode) => (
      <>The experiment exists — {link} to adjust the configuration and launch from there.</>
    ),
    openIt: "open it",
    whyFormShaped: "Why the form is shaped like this",
    whyFormShapedBody1:
      "An experiment that varies two things at once produces a number nobody can attribute. Rather than let you build one and reject it afterwards, the form only offers one factor at a time and pins everything else.",
    whyFormShapedBody2: (
      <>
        <strong>Create &amp; launch runs</strong> queues every run of the
        design at once — arms × cases × repetitions — with the model, prompt
        and budget set here, then lands you on the experiment as results fill
        in. Create without launching if you want to review the arms first; the
        experiment&apos;s own page can launch it later.
      </>
    )
  },
  es: {
    factorLabel: {
      harness_source: "Origen de la gobernanza",
      full_harness: "Arnés completo",
      governance: "Cuerpo de gobernanza",
      skills_extra: "Agregar skills",
      skills_disabled: "Quitar skills",
      prompt: "Prompt del arnés",
      activation: "Activación",
      harness: "Cambiar el arnés"
    } as Record<FactorKey, string>,
    factorBlurb: {
      harness_source:
        "La propia gobernanza del repositorio contra el arnés que este lab inyecta. La comparación honesta para '¿nuestro arnés ayuda a este equipo?'.",
      full_harness:
        "El arnés entero como un solo tratamiento: el prompt con la gobernanza " +
        "cargada más todas las skills, contra un brazo bare con las skills " +
        "ocultas y un prompt plano. Una victoria dice que el arnés se gana su " +
        "lugar como unidad — ablacioná factores después para ver qué partes " +
        "hacen el trabajo.",
      governance:
        "El cuerpo de reglas solo: CLAUDE.md, el hook de SessionStart, los " +
        "subagentes, los slash commands, la gobernanza de .github/ y los " +
        "servidores MCP — contra nada. Ambos brazos ocultan todas las skills y " +
        "corren el mismo prompt, así que una diferencia es sobre la gobernanza " +
        "y no sobre el catálogo. Solo en el venue del lab.",
      skills_extra:
        "Línea base contra línea base más una o más skills en borrador — skills " +
        "en prueba desde la página de Skills, todavía no parte del arnés. Una " +
        "sola skill es la prueba más angosta; varias prueban el conjunto como " +
        "unidad.",
      skills_disabled:
        "Ablación de skills que ya están en el arnés: si quitarlas no cambia " +
        "nada, no se estaban ganando su lugar en la ventana de contexto. Así se " +
        "mide el valor de una skill gobernada — ya está presente, así que la " +
        "comparación es con ella versus sin ella.",
      prompt:
        "El preámbulo solo: la tarea plana contra la tarea con la instrucción " +
        "del arnés antepuesta (leé .github/, usá estas skills), con el cuerpo " +
        "de gobernanza y el catálogo entero presentes en ambos brazos. Escalón " +
        "3 de la escalera — lo que suma la redacción una vez que ya está todo " +
        "lo demás.",
      activation:
        "Que se lo pidan directamente. Ambos brazos llevan el prompt del arnés " +
        "y el catálogo entero; el tratamiento además recibe el bloque de " +
        "activación que nombra cada skill y exige que el agente la invoque al " +
        "empezar cada paso. Escalón 4 de la escalera — la diferencia entre un " +
        "arnés en disco y un arnés en uso (ADR-0041).",
      harness: "El mismo caso y modelo corridos por un CLI de agente distinto."
    } as Record<FactorKey, string>,
    needsTarget: " — necesita un repositorio externo",
    needsLab: " — solo venue del lab",
    unableToLoadData: "No se pudieron cargar los repositorios y casos.",
    unableToCheck: "No se pudo revisar",
    suiteNeedsControlAndTreatment:
      "Una comparación de suite necesita un brazo de control y uno de tratamiento.",
    filedAs: (experimentId: string) =>
      `Ya hay un experimento que hace esta pregunta, así que este se archivó ` +
      `como '${experimentId}'. El anterior quedó intacto.`,
    unableToFindFreeId: "No se pudo encontrar un id de experimento libre.",
    unableToCreateExperiment: "No se pudo crear el experimento",
    experimentCreatedLaunchRefused: (experimentId: string, reason: string) =>
      `Se creó el experimento '${experimentId}', pero el lanzamiento fue rechazado: ${reason}`,
    unknownError: "error desconocido",
    referenceRepo: (id: string) => `Repositorio de referencia — los demás se leen contra ${id}.`,
    sameHarnessAgainst: (id: string) => `El mismo arnés, sin cambios, contra ${id}.`,
    nativeGovernanceDescription: "La propia gobernanza del repositorio, sin nada inyectado.",
    injectedGovernanceDescription: "El arnés del lab inyectado en el workspace.",
    harnessInUseToday: "El arnés en uso hoy.",
    alternativeUnderTest: "La alternativa bajo prueba.",
    invokedListMany: (count: number) => `sus ${count} skills gobernadas`,
    invokedNote: (list: string) =>
      ` Al agente se le pide al empezar cada paso que invoque ${list} y las siga.`,
    bareDescriptionLab: (bareVariant: string) =>
      `Workspace vacío, la gobernanza del lab oculta, prompt plano ('${bareVariant}'), todas las skills gobernadas ocultas.`,
    bareDescriptionSamePrompt: (bareVariant: string) =>
      `Mismo prompt ('${bareVariant}') con todas las skills gobernadas ocultas.`,
    bareDescriptionTargetPrompt: (bareVariant: string) =>
      `Prompt plano ('${bareVariant}') con todas las skills gobernadas ocultas.`,
    unknownVariant: "?",
    fullHarnessDescription: (harnessVariant: string, invokedNote: string) =>
      `Prompt con gobernanza ('${harnessVariant}') con el catálogo de skills ` +
      "entero disponible." +
      invokedNote,
    plainPromptDescription: (plain: string) =>
      `Prompt plano ('${plain}'), cuerpo de gobernanza y todas las skills gobernadas disponibles.`,
    harnessPromptDescription: (governed: string) =>
      `Prompt del arnés ('${governed}'), el mismo cuerpo de gobernanza y catálogo.`,
    harnessPromptNotAskedDescription: (governed: string) =>
      `Prompt del arnés ('${governed}') con todas las skills gobernadas disponibles, sin pedirlas.`,
    activatedDescription: (governed: string, count: number) =>
      `Prompt del arnés ('${governed}') más el bloque de activación que nombra ` +
      `sus ${count} skills gobernadas al empezar cada paso.`,
    bareGovernanceDescription:
      "Un workspace vacío con la gobernanza del lab oculta en el " +
      "contenedor: sin CLAUDE.md, sin hooks, sin subagentes, sin .github/, " +
      "sin servidores MCP, y sin skills.",
    governanceOnlyDescription:
      "La gobernanza del lab tal como la encuentra el agente normalmente — " +
      "CLAUDE.md, el hook de SessionStart, los subagentes, los slash commands " +
      "y .github/ — con las mismas skills ocultas en ambos lados.",
    governedHarnessToday: (count: number) =>
      `el arnés gobernado tal como está hoy (CLAUDE.md + ${count} skills gobernadas)`,
    governedHarnessTodayNoSkills: "el arnés gobernado tal como está hoy",
    theSelectedSkills: "las skills seleccionadas",
    theStagedSkills: "las skills en borrador",
    askedToInvoke: ", y al agente se le pide al empezar cada paso que la invoque y la siga.",
    ablationStillAvailable: (governed: string, listed: string) =>
      `${governed}, con ${listed} todavía disponible`,
    ablationWithoutStaged: (governed: string, listed: string) => `${governed}, sin ${listed}.`,
    ablationRemoved: (listed: string) => `El mismo arnés con ${listed} quitadas.`,
    additionAvailable: (listed: string) => `El mismo arnés con ${listed} agregadas como skill disponible`,
    availableNotForced:
      ". Disponible, no forzada: el agente la invoca solo si " +
      "la instrucción del caso o los disparadores propios de la skill lo piden.",
    // preguntas sugeridas
    crossRepoQuestion1:
      "¿Este arnés se sostiene en varios repositorios, o se degrada fuera de aquel en el que se construyó?",
    crossRepoQuestion2:
      "¿Los mismos casos puntúan igual en cada repositorio, o un repositorio está cargando con el resultado?",
    fullHarnessQuestion1: "¿El arnés completo mejora los resultados sobre el modelo bare en estos casos?",
    fullHarnessQuestion2:
      "Corriendo el mismo prompt con y sin el arnés, ¿el catálogo de skills gobernado cambia el resultado?",
    fullHarnessQuestionEnforced: (listed: string) =>
      `Cuando se le dice al agente que use ${listed}, ¿el arnés le gana a la corrida bare con el mismo prompt?`,
    fullHarnessQuestionNotEnforced:
      "¿El arnés vale lo que cuesta acá — mejores resultados por los pasos y tokens extra?",
    theGovernedSkills: "las skills gobernadas",
    thisStagedSkill: "esta skill en borrador",
    thisSkill: "esta skill",
    skillsExtraQuestion1: (listed: string) => `¿Agregar ${listed} mejora los resultados?`,
    skillsExtraQuestion2: (listed: string) =>
      `¿${listed} se gana un lugar en el arnés, o la línea base ya es suficientemente buena acá?`,
    skillsDisabledQuestion1: (listed: string) => `¿Quitar ${listed} cambia algo en los resultados?`,
    skillsDisabledQuestion2: (listed: string) =>
      `Si sacáramos ${listed} del arnés, ¿estos casos lo notarían?`,
    harnessFactorQuestion1: (treatment: string, control: string) =>
      `¿${treatment} le gana a ${control} en los mismos casos y modelo?`,
    harnessFactorQuestion2: (control: string, treatment: string) =>
      `¿${control} y ${treatment} difieren en lo que terminan, o solo en cómo llegan ahí?`,
    defaultQuestion1: "¿El arnés del lab inyectado mejora sobre el propio setup del repositorio?",
    defaultQuestion2:
      "Para un equipo que ya tiene su propia gobernanza, ¿nuestro arnés es una mejora o solo una diferencia?",
    writeQuestion: "Escribí la pregunta que responde este experimento.",
    chooseAtLeastOneCase: "Elegí al menos un caso.",
    sharedVariantProblem: (families: string) =>
      "Los casos seleccionados no comparten ninguna variante de prompt, así que " +
      "cualquier elección le daría a algunos un prompt vacío. Mantené una " +
      `familia de prompt: ${families}.`,
    crossRepoNeedsTwo: "Cross-repo necesita al menos dos repositorios para variar.",
    chooseRepositoryForArms: "Elegí el repositorio contra el que correrá cada brazo.",
    harnessSourceNeedsTarget:
      "Gobernanza nativa versus inyectada necesita un repositorio externo — " +
      "dentro del lab no hay otra gobernanza contra la cual comparar. Para " +
      "comparar la propia gobernanza del lab contra nada, usá 'Cuerpo de " +
      "gobernanza'.",
    labOnlyFactor: (label: string) =>
      `'${label}' es un escalón de la propia escalera de arnés del lab, que ` +
      "solo el venue del lab puede montar. Contra un repositorio externo usá " +
      "'Origen de la gobernanza'.",
    chooseAtLeastOneSkill: "Elegí al menos una skill para que varíe el tratamiento.",
    suiteCannotCarryPair:
      "La suite de regresión fija sus propios prompts, así que no puede llevar " +
      "el par bare-versus-gobernado. Elegí casos a mano, o elegí otro factor.",
    identicalArmsNoSkills:
      "Ambos brazos usan la misma variante de prompt y no hay skills gobernadas " +
      "que ocultar, así que los dos brazos serían idénticos.",
    chooseSkillsForActivation:
      "Elegí las skills gobernadas para nombrar en el bloque de activación, o " +
      "destildá 'Pedirle al agente que use el arnés'.",
    forcedSinglePrompt: "Los casos seleccionados definen un solo prompt, así que ambos brazos lo corren. ",
    bothArmsRunPrompt: (forced: string, bareVariant: string) =>
      `${forced}Ambos brazos corren '${bareVariant}', así que lo que se mueve es `,
    governanceAndCatalogFixed:
      "el cuerpo de gobernanza más el catálogo de skills, con el prompt fijo. " +
      "Esa es una comparación sólida — es el arnés menos su prompt — pero leé " +
      "el resultado como una afirmación sobre lo que el agente podía cargar, " +
      "no sobre la redacción que recibió.",
    catalogAloneAblation:
      "el catálogo de skills solo, en lugar del arnés como unidad. Esa es una " +
      "ablación sólida — es lo que dice directamente el factor 'Quitar skills' " +
      "— pero leé el resultado como una afirmación sobre las skills, no sobre " +
      "el arnés entero.",
    partialAblationNote:
      "Dentro de este lab ambos brazos siguen llevando el cuerpo de gobernanza " +
      "de CLAUDE.md, el hook de SessionStart y la gobernanza de .github/ — solo " +
      "difiere el catálogo de skills generado. El brazo de control no es un " +
      "modelo bare. Para una comparación bare-versus-gobernado usá 'Cuerpo de " +
      "gobernanza' (el reglamento contra nada) o 'Arnés completo' (todo contra " +
      "nada).",
    bundleTogether: (count: number, moved: string) =>
      `${count} skills se ${moved} juntas, así que una diferencia ` +
      "pertenece al conjunto y no se puede atribuir a ninguna por separado. " +
      "Para evaluar una skill, seleccioná solo esa skill.",
    removedFromTreatment: "quitan del brazo de tratamiento",
    stagedInTreatment: "ponen en borrador en el brazo de tratamiento",
    leftOutAvailable: (isPlural: boolean) =>
      isPlural
        ? "disponibles para ambos brazos y no probadas por ninguno."
        : "disponible para ambos brazos y no probada por ninguno.",
    leftOutAbsent: (isPlural: boolean) =>
      isPlural
        ? "ausentes de ambos brazos y no probadas por ninguno."
        : "ausente de ambos brazos y no probada por ninguno.",
    leftOutSuffix: (names: string, isPlural: boolean, rest: string) =>
      ` ${names} ${isPlural ? "quedaron" : "quedó"} afuera: ${rest} Si esa es la skill que querés evaluar, la selección está invertida.`,
    armsDifferInPrompt: (bareVariant: string, harnessVariant: string, governanceClause: string) =>
      `Los brazos difieren en prompt ('${bareVariant}' vs '${harnessVariant}')` +
      governanceClause +
      " y en catálogo de skills. Eso es el arnés como unidad, y una diferencia " +
      "no se puede atribuir a ninguna parte por separado. Para una afirmación " +
      "sobre las skills solas usá 'Agregar skills' o 'Quitar skills'; para el " +
      "reglamento solo usá 'Cuerpo de gobernanza'; para correr un solo prompt " +
      "en ambos brazos, igualalos arriba.",
    inGovernanceBody: ", en cuerpo de gobernanza",
    verbIs: "está",
    verbAre: "están",
    variantsDroppedNote: (dropped: string, verbIsAre: string, offered: string) =>
      `${dropped} ` +
      `${verbIsAre} declaradas por algunos de los casos seleccionados pero no ` +
      "por todos, así que los selectores de prompt ofrecen " +
      `solo ${offered}. ` +
      "Un experimento corre un solo diseño en todos los casos que selecciona.",
    bareArmToldToFollow: (promptVariant: string) =>
      `Ambos brazos corren '${promptVariant}', que dirige al agente a través de ` +
      "la gobernanza del lab — el brazo bare no puede leer nada de eso. Elegí " +
      "la variante plana para que la comparación sea sobre lo que tenía cada " +
      "brazo, no sobre que a uno se le pidió usar lo que no tenía.",
    title: "Nueva evaluación",
    lede: "El diseño se arma a partir de tus respuestas, así el experimento puede responder la pregunta que declara.",
    evaluateHarnessChange: "Evaluar un cambio de arnés",
    testAcrossRepositories: "Probar en varios repositorios",
    whatAreYouTryingToFindOut: "¿Qué querés averiguar?",
    requiredQuestion: "obligatorio — el experimento se arma alrededor de esta pregunta",
    placeholderCrossRepo:
      "¿La skill de revisión se sostiene fuera del repositorio para el que se escribió?",
    placeholderHarnessEval: "¿La skill de planificación reduce los pasos sin perder corrección?",
    useASuggestedQuestion: "Usar una pregunta sugerida: ",
    whatVaries: "Qué varía",
    whereCasesRun: "Dónde corren los casos (fijo en ambos brazos)",
    thisLab: (
      <>
        <strong>Este lab</strong> — el arnés bajo prueba es la propia
        gobernanza del lab, el release sincronizado de <code>ml-python-base</code>.
        Este es el loop de mejora continua: cambiá una skill, medila, hacé el
        release, volvé a medir.
      </>
    ),
    externalRepo: (
      <>
        <strong>Un repositorio externo</strong> — el agente trabaja dentro del
        código de otro equipo, provisto desde el checkout local fijado para
        cada corrida.
      </>
    ),
    repository: "Repositorio",
    selectRepository: "Elegí un repositorio",
    noRepositoriesRegistered:
      "Todavía no hay repositorios registrados — agregá uno en Evidencia → Repositorios, o mantené la evaluación en este lab.",
    factorUnderTest: "Factor bajo prueba",
    bareArmPrompt: "Prompt del brazo bare",
    bareArmPromptHint:
      "El prompt plano de la tarea — sin instrucciones de gobernanza, y con todas las skills gobernadas ocultas para este brazo.",
    fullHarnessArmPrompt: "Prompt del brazo de arnés completo",
    fullHarnessArmPromptHint: (count: number) =>
      `La variante con la gobernanza cargada, con el catálogo de skills entero (${count}) disponible.`,
    pickCasesFirst:
      "Elegí casos abajo primero — los prompts bare y gobernado salen de las variantes que esos casos definen.",
    noSharedPromptNarrow:
      "Los casos seleccionados no comparten ninguna variante de prompt, así que no hay par que corra en todos. Acotá la selección a una sola familia de prompt.",
    activationLegend: "Activación en el brazo de arnés completo",
    askAgentToUseHarness: "Pedirle al agente que use el arnés",
    askAgentToUseHarnessHint: (
      <>
        <strong>Activado por defecto, con todo el catálogo seleccionado.</strong>{" "}
        Un prompt escrito en esta interfaz no dice nada sobre gobernanza, así
        que el brazo de arnés completo podría no abrir nunca una skill y el
        par solo mediría lo que había en disco. Esto nombra las skills de
        abajo en un bloque de activación al empezar cada paso de ese brazo; el
        brazo bare nunca lo recibe — no puede ver esas skills en absoluto.
        Destildá skills individuales para acotar el bloque, o apagá todo para
        hacer la pregunta distinta: si el agente las busca sin que se lo
        pidan.
      </>
    ),
    skillsNamedTogether: (count: number) =>
      `${count} skills nombradas — se piden juntas, así que una victoria le pertenece al arnés como un todo y no a ninguna en particular.`,
    stagedSkillsToAdd: "Skills en borrador para agregar",
    governedSkillsToRemove: "Skills gobernadas para quitar",
    nothingStagedToOffer: (
      <>
        Nada en borrador para ofrecer. <code>skills_extra</code> solo acepta
        skills en borrador — importá o escribí una en Evidencia → Skills
        primero. Para medir una skill que ya es parte del arnés, usá{" "}
        <strong>Quitar skills</strong> en su lugar: ya está presente, así que
        su valor se mide quitándola.
      </>
    ),
    enforceSkillUse: "Exigir el uso de la skill",
    enforceSkillUseHint: (
      <>
        Decirle al agente, al empezar cada paso, que invoque la(s) skill(s)
        seleccionada(s) y las siga durante toda la tarea. Apagado = la skill
        solo está disponible y el agente decide. Prendido = la prueba de
        &ldquo;¿ayuda cuando se la piden?&rdquo;; apagado = &ldquo;¿ayuda sin
        que se la pidan?&rdquo;.
      </>
    ),
    skillsSelectedBundle: (count: number) => (
      <>
        {count} skills seleccionadas — el tratamiento es el{" "}
        <strong>conjunto</strong>. Una victoria dice que el conjunto ayuda; no
        se puede atribuir a ninguna por separado. Para ordenarlas, corré
        experimentos de una skill por vez después.
      </>
    ),
    controlHarness: "Arnés de control",
    treatmentHarness: "Arnés de tratamiento",
    harnessHeldFixedAcrossRepos: "Arnés (fijo en todos los repositorios)",
    repositories: "Repositorios",
    noRepositoriesAddOne: "Todavía no hay repositorios registrados. Agregá uno en Evidencia → Repositorios.",
    resultsStayPerRepo: (
      <>
        Los resultados quedan por repositorio. Un puntaje agrupado entre
        distintos códigos los trataría como una sola condición, y no lo son.
        El primer repositorio que elegís se vuelve la{" "}
        <strong>referencia</strong> contra la que se leen los demás — una
        referencia, no una línea base: nada acá sostiene una afirmación
        causal.
      </>
    ),
    casesAndRepetitions: "Casos y repeticiones",
    fromTheSuite: "de la suite",
    useRegressionSuite: "Usar la suite de regresión — validar un release de arnés contra los casos canónicos",
    suite: "Suite",
    suiteDeclaresDesign: (
      <>
        La suite declara qué casos son representativos y cuánto vale la pena
        repetir cada uno, así que ninguno de los dos se elige acá. Lo que
        vuelve es un experimento de evaluación de arnés común: la misma
        validación de diseño, la misma matriz de casos, la misma lectura{" "}
        <strong>arreglado / roto / sin cambios</strong>. No hay un segundo
        runner.
      </>
    ),
    casesLegend: "Casos (cada brazo corre todos)",
    repetitionsPerCasePerArm: "Repeticiones por caso, por brazo",
    repetitionChoices: {
      exploratory: "1 — exploratorio",
      judgement: "2 — juicio",
      validation: "5 — validación"
    },
    agentsStochastic:
      "Los agentes son estocásticos. Una repetición por brazo es exploratoria y no tiene titular direccional; dos es un juicio; cinco es una validación.",
    howItRuns: "Cómo corre",
    sharedByEveryArm:
      "Compartido por todos los brazos — el factor de arriba es lo único que difiere entre ellos, así que nada elegido acá se filtra en la comparación como un confusor.",
    designVariesHarness: "El diseño varía el arnés, así que cada brazo usa el suyo. Elegí un modelo que ambos CLIs puedan usar, o dejá el default.",
    harnessHeldFixedAcrossArms: "Arnés (fijo en ambos brazos)",
    runsHarnessCli: (harness: string) => `Corre el CLI de ${harness} con su autenticación nativa. `,
    claudeAuthHint:
      "Funciona en Docker (CLAUDE_CODE_OAUTH_TOKEN o ANTHROPIC_API_KEY en .env) o en modo host (make app-host).",
    hostModeHint: "Necesita modo host — corré make app-host.",
    executorModelLabel: "Modelo ejecutor",
    executorModelHeldFixed: "Fijo en todos los brazos — el experimento varía el factor de arriba, nunca el modelo.",
    plannerModelLabel: "Modelo planificador",
    promptVariantLabel: "Variante de prompt",
    suiteDeclaresOwnPrompt: "La suite declara el suyo.",
    setPerArmAbove: "Se fija por brazo arriba — bare versus gobernado es el tratamiento.",
    variantsEverySelectedCase:
      "Variantes que define cada caso seleccionado — que a un caso le falte una variante alcanza para que se rechace el lanzamiento.",
    stepBudget: "Presupuesto de pasos",
    stepBudgetHint: "Subido al presupuesto más grande que declara algún caso seleccionado.",
    dryRunLabel: "Dry run — armar los comandos sin ejecutarlos",
    designAboutToCreate: "El diseño que estás por crear",
    arm: (count: number) => `${count} brazo${count === 1 ? "" : "s"}`,
    suiteCasesAndRepetitions: " × los casos y repeticiones de la suite",
    caseCountTimes: (count: number) => ` × ${count} caso${count === 1 ? "" : "s"} × `,
    repetitionsEqualsRuns: (repetitions: number, runs: number) =>
      `${repetitions} = ${runs} corrida${runs === 1 ? "" : "s"}`,
    roleLabel: { control: "control", treatment: "tratamiento" } as Record<string, string>,
    notReadyYet: "Todavía no está listo — los botones de abajo se habilitan cuando se resuelva esto:",
    ready: "Listo",
    blocked: "Bloqueado",
    checkSuiteResolvedAtLaunch: "Los casos de la suite se resuelven al lanzar — las mismas revisiones corren ahí.",
    pickAtLeastOneCaseFirst: "Elegí al menos un caso primero.",
    checking: "Revisando…",
    checkConfiguration: "Revisar configuración",
    loadCheckTitle: (model: string, size: string, host: string) =>
      `Revisar esta configuración va a cargar ${model} (~${size} GB) en ${host}.`,
    loadCheckContinue: "Continuar",
    loadCheckCancel: "Cancelar",
    working: "Trabajando…",
    createAndLaunchRuns: "Crear y lanzar corridas",
    createWithoutLaunching: "Crear sin lanzar",
    experimentExists: (link: ReactNode) => (
      <>El experimento existe — {link} para ajustar la configuración y lanzarlo desde ahí.</>
    ),
    openIt: "abrirlo",
    whyFormShaped: "Por qué el formulario está armado así",
    whyFormShapedBody1:
      "Un experimento que varía dos cosas a la vez produce un número que nadie puede atribuir. En lugar de dejarte armar uno y rechazarlo después, el formulario ofrece un solo factor a la vez y fija todo lo demás.",
    whyFormShapedBody2: (
      <>
        <strong>Crear y lanzar corridas</strong> encola todas las corridas del
        diseño de una — brazos × casos × repeticiones — con el modelo, prompt
        y presupuesto fijados acá, y después te deja en el experimento
        mientras se llenan los resultados. Creá sin lanzar si querés revisar
        los brazos primero; la propia página del experimento lo puede lanzar
        después.
      </>
    )
  }
});

const MAX_SLUG = 64;

// Three named levels, sized for a budget a lab can actually spend: one run
// per arm explores, two is enough for a judgement the page will state, five is
// a validation. The backend's wording ladder matches (statistics.py). The
// label for each value lives in MESSAGES, read at render time.
const REPETITION_CHOICES = [
  { value: 1, key: "exploratory" as const },
  { value: 2, key: "judgement" as const },
  { value: 5, key: "validation" as const }
];

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, MAX_SLUG) || "experiment"
  );
}

/**
 * The same question asked of a different harness is a different experiment.
 *
 * Ids are derived from the question, so re-running one comparison against
 * another runtime used to collide and be refused with nothing to do about it
 * short of rewording the question. The counter keeps the id readable, keeps
 * the question intact, and leaves the earlier experiment untouched.
 */
function uniqueExperimentId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) {
    return base;
  }
  for (let counter = 2; counter <= 99; counter += 1) {
    const suffix = `-${counter}`;
    const candidate = base.slice(0, MAX_SLUG - suffix.length) + suffix;
    if (!taken.has(candidate)) {
      return candidate;
    }
  }
  const stamp = `-${Date.now().toString(36)}`;
  return base.slice(0, MAX_SLUG - stamp.length) + stamp;
}

/** Ids the backend already holds, so the form can pick a free one up front. */
async function takenExperimentIds(): Promise<Set<string>> {
  try {
    const existing = await api.listExperiments();
    return new Set(existing.map((item) => item.experiment_id));
  } catch {
    // Unlistable catalog — the create call still refuses duplicates and the
    // retry below recovers from it.
    return new Set<string>();
  }
}

/** A create refused only because the derived id was taken. */
function isDuplicateId(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.status === 409) ||
    (error instanceof Error && error.message.includes("already exists"))
  );
}

/**
 * Mode A and Mode B, as a form that cannot express an invalid design.
 *
 * The wizard builds the conditions rather than asking for them: Mode A pins
 * every arm to one repository and varies exactly one harness factor; Mode B
 * pins the harness and emits one arm per repository. The backend still
 * validates — this only means the operator rarely has to be told no.
 */
export function NewEvaluation() {
  const m = useMessages(MESSAGES);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const mode = (params.get("mode") ?? "harness_evaluation") as ExperimentMode;

  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [skills, setSkills] = useState<HarnessSkill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [question, setQuestion] = useState("");
  const [targetId, setTargetId] = useState("");
  const [caseIds, setCaseIds] = useState<string[]>([]);
  // §13: the regression suite is a *producer* of this same experiment, not a
  // second runner. Ticking this replaces the case picker with the suite's
  // declared design — its canonical cases and its per-case repetitions.
  // Where the cases run. "lab" is the historical default and the loop this
  // repository was built around: the harness under test is the lab's own
  // governance (the synced ml-python-base release), and the agent works from
  // the lab root. "target" is the external-repository case ADR-0031 added.
  const [venue, setVenue] = useState<"lab" | "target">("lab");
  const [useSuite, setUseSuite] = useState(false);
  const [suiteId, setSuiteId] = useState("harness-regression-v1");
  const [repetitions, setRepetitions] = useState(2);
  // Adding one skill is the narrowest useful test and the commonest question
  // asked of this lab's own harness, so it is where the form opens. Governance
  // source needs an external repository and would open the form blocked.
  const [factorKey, setFactorKey] = useState<FactorKey>("skills_extra");
  const [skillNames, setSkillNames] = useState<string[]>([]);
  // Off answers "does the skill help unasked" (only available, the agent
  // decides); on answers "does it help when asked for" (the agent is told to
  // invoke it at the start of every step).
  const [enforceSkills, setEnforceSkills] = useState(false);
  // The full-harness comparison's prompt pair: the bare and the governed
  // member of whichever prompt family the selected cases are from.
  const [bareVariant, setBareVariant] = useState("");
  const [harnessVariant, setHarnessVariant] = useState("");
  // Staged skills feed "add", governed skills feed "remove": skills_extra is
  // validated against the staged catalog and skills_disabled against the
  // governed one, so offering either list to the other factor builds an
  // experiment the launch preflight refuses.
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [controlHarness, setControlHarness] = useState("opencode");
  const [treatmentHarness, setTreatmentHarness] = useState("claude");
  const [crossRepoIds, setCrossRepoIds] = useState<string[]>([]);

  // How the runs execute — the same controls as the classic run form, so the
  // wizard is the whole journey: design, configuration, launch. One shared
  // harness serves Mode A and Mode B alike; when the *factor* is the harness,
  // the arms override it and the shared value is ignored.
  const [harness, setHarness] = useState("opencode");
  const [executorModel, setExecutorModel] = useState("");
  const [plannerModel, setPlannerModel] = useState("");
  const [gatewayDefault, setGatewayDefault] = useState("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
  const [reloadingModels, setReloadingModels] = useState(false);
  const [promptVariant, setPromptVariant] = useState("sdlc");
  const [maxSteps, setMaxSteps] = useState(10);
  const [dryRun, setDryRun] = useState(false);
  // Set when creation succeeded but the launch was refused: the experiment
  // exists, so the recovery is its page, not this form.
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [check, setCheck] = useState<RunValidationResult | null>(null);
  const [checking, setChecking] = useState(false);
  // Set when `GET /model-load-check` finds the model unloaded: the confirm
  // step before `handleCheck`'s own request would load it. Null the rest of
  // the time, including when the check could not resolve a host (fail open).
  const [pendingLoadCheck, setPendingLoadCheck] = useState<LocalModelLoadCheck | null>(null);

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

  // `?executor=` preselects the executor model — the hand-off from Tune your
  // local model's "Use in New evaluation". Read once on mount, before the
  // catalog's own default-executor adoption (which only fires on an empty
  // pick), so a link always wins over the gateway default.
  useEffect(() => {
    const requested = params.get("executor");
    if (requested) {
      setExecutorModel(requested);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void loadModels().catch(() => {
      // Gateway/API offline — the pickers show their empty-state guidance.
    });
    Promise.allSettled([
      api.listTargets(),
      api.listCases(),
      api.listHarnessSkills(),
      api.listStagedSkills()
    ])
      .then(([targetResult, caseResult, skillResult, stagedResult]) => {
        if (targetResult.status === "fulfilled") {
          setTargets(targetResult.value);
          setTargetId((current) => current || (targetResult.value[0]?.target_id ?? ""));
        }
        if (caseResult.status === "fulfilled") {
          setCases(caseResult.value);
        }
        if (skillResult.status === "fulfilled") {
          setSkills(skillResult.value);
        }
        if (stagedResult.status === "fulfilled") {
          setStagedSkills(
            stagedResult.value.filter((skill) => skill.status !== "archived")
          );
        }
      })
      .catch(() => setError(m.unableToLoadData));
  }, []);

  const selectedCases = useMemo(
    () => cases.filter((item) => caseIds.includes(item.case_id)),
    [cases, caseIds]
  );

  /** Whether a case declares this variant — the shape `promptEntriesFor` reads. */
  const declares = (item: CaseRecord, variant: string) =>
    typeof item.prompt_variants?.[variant] === "string";

  // Every variant *some* selected case declares. Not what the pickers offer —
  // it exists to explain what a mixed selection costs.
  const declaredVariants = useMemo(() => {
    const names = new Set<string>();
    for (const item of selectedCases) {
      for (const variant of Object.keys(item.prompt_variants ?? {})) {
        names.add(variant);
      }
    }
    return [...names].sort();
  }, [selectedCases]);

  // What the pickers offer: the variants EVERY selected case declares.
  //
  // The union is what batch mode uses, and it is right there — a batch drops
  // the pairs a case cannot run and carries on. An experiment cannot: it runs
  // one design across every case it selected, and `experiment_service.launch`
  // refuses the whole launch if any case lacks the variant an arm asks for.
  // Offering the union meant a lab case and three SWE-bench instances put
  // 'default'/'harness' in the pickers, and the refusal arrived six screens
  // later, after the experiment had already been created (ADR-0059).
  const variantOptions = useMemo(() => {
    if (selectedCases.length === 0) {
      return [];
    }
    return declaredVariants.filter((variant) =>
      selectedCases.every((item) => declares(item, variant))
    );
  }, [declaredVariants, selectedCases]);

  // The selected cases grouped by the prompt family they belong to, phrased for
  // an operator who has to fix the selection: which cases, declaring what.
  const variantFamilies = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const item of selectedCases) {
      const key = Object.keys(item.prompt_variants ?? {}).sort().join(", ");
      groups.set(key, [...(groups.get(key) ?? []), item.case_id]);
    }
    return [...groups.entries()].map(([variants, ids]) => {
      const who =
        ids.length > 2 ? `${ids.length} cases` : ids.map((id) => `'${id}'`).join(" and ");
      const verb = ids.length === 1 ? "declares" : "declare";
      return `${who} ${verb} ${variants || "no prompt at all"}`;
    });
  }, [selectedCases]);

  // Cases feeding the "View prompt" toggle next to bare/harness selects — one
  // entry per selected case that actually defines this variant, so a person
  // can see what they picked without leaving the form. `prompt_variants`
  // holds either the literal text or a `task.yaml`-style path, so the
  // content itself is fetched from the API, which resolves either form.
  const promptEntriesFor = (variant: string) =>
    selectedCases
      .filter((item) => declares(item, variant))
      .map((item) => ({ caseId: item.case_id, label: item.name }));

  const stepOptions = useMemo(() => {
    const budgets = new Set<number>([3, 6, 10, maxSteps]);
    for (const item of selectedCases) {
      if (item.max_steps) {
        budgets.add(item.max_steps);
      }
    }
    return [...budgets].sort((a, b) => a - b);
  }, [maxSteps, selectedCases]);

  // Keep the variant valid as the case selection changes, preferring the
  // un-doctored baseline of whichever family the cases are from. Alphabetical
  // order would land on "swe_conductor", which is a treatment prompt.
  useEffect(() => {
    if (variantOptions.length === 0 || variantOptions.includes(promptVariant)) {
      return;
    }
    const preferred = ["sdlc", "swe_default"].find((name) =>
      variantOptions.includes(name)
    );
    setPromptVariant(preferred ?? variantOptions[0]);
  }, [promptVariant, variantOptions]);

  // Fund the largest budget any selected case declares — an arm that runs out
  // of steps fails for a reason that is not the treatment. Upward only, so a
  // deliberate choice is never shrunk back.
  useEffect(() => {
    const declared = selectedCases.map((item) => item.max_steps ?? 0);
    if (declared.length > 0) {
      setMaxSteps((current) => Math.max(current, ...declared));
    }
  }, [selectedCases]);

  // Switching to the lab leaves no second governance surface to compare, so a
  // factor that needs one cannot stay selected.
  useEffect(() => {
    if (venue === "lab" && factorKey === "harness_source") {
      setFactorKey("skills_extra");
    }
    // And the mirror: a bare arm is a masked *lab* container, so there is no
    // bare condition to put inside somebody else's repository (ADR-0049).
    if (
      venue === "target" &&
      (factorKey === "governance" ||
        factorKey === "prompt" ||
        factorKey === "activation")
    ) {
      setFactorKey("harness_source");
    }
  }, [factorKey, venue]);

  // The two skill factors draw from different catalogs, so a selection cannot
  // survive switching between them.
  //
  // This effect — not the `useState` initialiser — is where the activation
  // default lives, because it overwrites the flag on every factor change.
  // Asking is the default wherever the treatment is a skill the arm *has*: an
  // arm that never invokes its skills measured availability, not the skill
  // (ADR-0041/0046). It stays off for `skills_disabled`, whose treatment is
  // removal, and for the factors that move no catalog at all.
  //
  // `full_harness` also seeds the selection with the whole catalog. Turning the
  // flag on over an empty grid was a dead end: the activation block is built
  // from `skillNames`, so it came out empty, and the only unblocking move the
  // form offered was clearing the flag again — which is how the treatment arm
  // of `...-on--4` reached its runs with `skills_invoke: []` and invoked none
  // of its twenty-five skills. What "full harness" names *is* the catalog, so
  // the catalog is the honest default; the operator can trim it below. The
  // catalog is fetched once on mount, hence the `skills` dependency: on the
  // first render it is still empty and there is nothing to seed.
  useEffect(() => {
    setSkillNames(
      factorKey === "full_harness" ? skills.map((skill) => skill.name).sort() : []
    );
    setEnforceSkills(factorKey === "full_harness" || factorKey === "skills_extra");
  }, [factorKey, skills]);

  // Pair the arms with the conventional members of the prompt family:
  // default→harness for cases on the two-variant shape (ADR-0047),
  // swe_default→swe_harness for imported SWE-bench instances. The legacy
  // naive→sdlc pairing is kept for cases that still ship a prompt family, but
  // it is listed last: those variants vary the task as well as the harness, so
  // it is the fallback, not the default. Falls back to first/last so an unusual
  // family still yields a usable pair the operator can correct.
  useEffect(() => {
    const usesPair =
      factorKey === "full_harness" ||
      factorKey === "prompt" ||
      factorKey === "activation";
    if (!usesPair || variantOptions.length === 0) {
      return;
    }
    const pick = (candidates: string[], fallback: string) =>
      candidates.find((name) => variantOptions.includes(name)) ?? fallback;
    setBareVariant((current) =>
      variantOptions.includes(current)
        ? current
        : pick(["default", "swe_default", "naive", "vibe"], variantOptions[0])
    );
    setHarnessVariant((current) =>
      variantOptions.includes(current)
        ? current
        : pick(
            ["harness", "swe_harness", "sdlc"],
            variantOptions[variantOptions.length - 1]
          )
    );
  }, [factorKey, variantOptions]);

  // Written FOR the operator, adopted BY the operator: the backend refuses a
  // question-less experiment on purpose (a batch with authority it has not
  // earned), so the wizard offers a starting sentence rather than silently
  // inventing one.
  const suggestedQuestions = useMemo(() => {
    if (mode === "cross_repo") {
      return [m.crossRepoQuestion1, m.crossRepoQuestion2];
    }
    if (factorKey === "full_harness") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : m.theGovernedSkills;
      return [
        m.fullHarnessQuestion1,
        // The same-prompt design this factor now accepts: one plain prompt, run
        // with the catalog hidden and with it available. Worth offering as a
        // sentence, because it is the question a case written in this UI can
        // actually answer.
        m.fullHarnessQuestion2,
        enforceSkills
          ? m.fullHarnessQuestionEnforced(listed)
          : m.fullHarnessQuestionNotEnforced
      ];
    }
    if (factorKey === "skills_extra") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : m.thisStagedSkill;
      return [m.skillsExtraQuestion1(listed), m.skillsExtraQuestion2(listed)];
    }
    if (factorKey === "skills_disabled") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : m.thisSkill;
      return [m.skillsDisabledQuestion1(listed), m.skillsDisabledQuestion2(listed)];
    }
    if (factorKey === "harness") {
      return [
        m.harnessFactorQuestion1(treatmentHarness, controlHarness),
        m.harnessFactorQuestion2(controlHarness, treatmentHarness)
      ];
    }
    return [m.defaultQuestion1, m.defaultQuestion2];
  }, [controlHarness, enforceSkills, factorKey, m, mode, skillNames, treatmentHarness]);

  const setMode = (next: ExperimentMode) => {
    setParams({ mode: next });
    setWarnings([]);
    setNotice(null);
    setError(null);
  };

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const conditions = useMemo(() => {
    if (mode === "cross_repo") {
      // One arm per repository, everything else identical — the only shape
      // that answers "does this harness generalize?".
      //
      // The first repository is marked control because the design validator
      // needs one arm the others are read against. It is a *reference*, not a
      // baseline: nothing here is a causal comparison, and the results stay
      // per repository however they are labelled.
      return crossRepoIds.map((id, index) => ({
        label: id,
        role: index === 0 ? ("control" as const) : ("treatment" as const),
        description: index === 0 ? m.referenceRepo(id) : m.sameHarnessAgainst(id),
        overrides: { target_repo_id: id, harness }
      }));
    }
    // In the lab, there is no target to pin: the run works from the lab root,
    // which is what `harness_source` defaults to and what every historical run
    // did. Sending an empty target_repo_id would be a different thing entirely.
    const base: Record<string, unknown> =
      venue === "target" && targetId ? { target_repo_id: targetId } : {};
    if (factorKey === "harness_source") {
      return [
        {
          label: "native",
          role: "control" as const,
          description: m.nativeGovernanceDescription,
          overrides: { ...base, harness_source: "native" }
        },
        {
          label: "injected",
          role: "treatment" as const,
          description: m.injectedGovernanceDescription,
          overrides: { ...base, harness_source: "injected" }
        }
      ];
    }
    if (factorKey === "harness") {
      return [
        {
          label: controlHarness,
          role: "control" as const,
          description: m.harnessInUseToday,
          overrides: { ...base, harness: controlHarness }
        },
        {
          label: treatmentHarness,
          role: "treatment" as const,
          description: m.alternativeUnderTest,
          overrides: { ...base, harness: treatmentHarness }
        }
      ];
    }
    if (factorKey === "full_harness") {
      // Two levers moved together on purpose — the governance prompt and the
      // skill catalog are one treatment, and the claim is about the harness
      // as a unit.
      //
      // The activation block only ever rides the treatment arm: the bare arm
      // hides every governed skill, and preflight refuses a run told to invoke
      // a skill it cannot see. It exists because a prompt written in this UI
      // says nothing about governance, so without it "full harness" means only
      // that the catalog was on disk.
      const invoked = enforceSkills ? [...skillNames].sort() : [];
      // Naming all twenty-five would bury the arm's description under quoted
      // strings — the same reason `statistics._skill_usage_issues` counts
      // instead of listing once a catalog is more than a handful.
      const invokedList =
        invoked.length > 3
          ? m.invokedListMany(invoked.length)
          : invoked.map((name) => `'${name}'`).join(", ");
      const invokedNote = invoked.length ? m.invokedNote(invokedList) : "";
      // Inside the lab the control arm is `bare`: an empty workspace with the
      // lab's governance masked in the container (ADR-0049). Without it a
      // "bare" arm still carried CLAUDE.md, the SessionStart hook, the
      // subagents and .github/ — the whole rulebook — and the experiment
      // measured the catalog while claiming to measure the harness.
      //
      // `skills_disabled` stays on it even though the masks make it redundant:
      // it is what names the catalog the *treatment* is about, through the
      // sibling-removes rule (ADR-0046), and what keeps the two arms distinct
      // in the condition hash.
      const bareGovernance = venue === "lab" ? { harness_source: "bare" } : {};
      return [
        {
          label: "bare",
          role: "control" as const,
          description:
            venue === "lab"
              ? m.bareDescriptionLab(bareVariant || m.unknownVariant)
              : bareVariant && bareVariant === harnessVariant
                ? m.bareDescriptionSamePrompt(bareVariant)
                : m.bareDescriptionTargetPrompt(bareVariant || m.unknownVariant),
          overrides: {
            ...base,
            ...bareGovernance,
            prompt_variant: bareVariant,
            skills_disabled: skills.map((skill) => skill.name).sort()
          }
        },
        {
          label: "full-harness",
          role: "treatment" as const,
          description: m.fullHarnessDescription(harnessVariant || m.unknownVariant, invokedNote),
          overrides: {
            ...base,
            prompt_variant: harnessVariant,
            skills_disabled: [],
            ...(invoked.length ? { skills_invoke: invoked } : {})
          }
        }
      ];
    }
    if (factorKey === "prompt" || factorKey === "activation") {
      // Rungs 3 and 4 of the ladder (ADR-0051). Both sit on `lab_root` with the
      // whole catalog visible; a bare arm cannot be their control, because it
      // has no prompt family and no catalog to hold fixed.
      const plain = bareVariant || "default";
      const governed = harnessVariant || "harness";
      const catalog = skills.map((skill) => skill.name).sort();
      if (factorKey === "prompt") {
        return [
          {
            label: "plain-prompt",
            role: "control" as const,
            description: m.plainPromptDescription(plain),
            overrides: { ...base, prompt_variant: plain, skills_disabled: [] }
          },
          {
            label: "harness-prompt",
            role: "treatment" as const,
            description: m.harnessPromptDescription(governed),
            overrides: { ...base, prompt_variant: governed, skills_disabled: [] }
          }
        ];
      }
      return [
        {
          label: "harness-prompt",
          role: "control" as const,
          description: m.harnessPromptNotAskedDescription(governed),
          overrides: { ...base, prompt_variant: governed, skills_disabled: [] }
        },
        {
          label: "activated",
          role: "treatment" as const,
          description: m.activatedDescription(governed, catalog.length),
          overrides: {
            ...base,
            prompt_variant: governed,
            skills_disabled: [],
            skills_invoke: catalog
          }
        }
      ];
    }
    if (factorKey === "governance") {
      // The rulebook on its own. Both arms hide every skill and inherit the
      // same prompt, so the only thing that moves is whether the lab's
      // governance is readable — which is the question "is our governance any
      // good?" asked without the catalog confounding it (ADR-0049).
      const hidden = skills.map((skill) => skill.name).sort();
      return [
        {
          label: "bare",
          role: "control" as const,
          description: m.bareGovernanceDescription,
          overrides: { ...base, harness_source: "bare", skills_disabled: hidden }
        },
        {
          label: "governance-only",
          role: "treatment" as const,
          description: m.governanceOnlyDescription,
          overrides: { ...base, skills_disabled: hidden }
        }
      ];
    }
    const isAblation = factorKey === "skills_disabled";
    const chosen = [...skillNames].sort();
    const listed = chosen.map((name) => `'${name}'`).join(", ");
    // The descriptions travel with the experiment and are what a reader sees
    // next to each arm later, so they say what "baseline" actually is: the
    // governed harness as it stands (CLAUDE.md plus every governed skill), not
    // a bare model. A staged skill is *available* to the treatment arm, not
    // forced — the agent invokes it only when the instruction or the skill's
    // own triggers call for it.
    const governedCount = skills.length;
    const governed = governedCount
      ? m.governedHarnessToday(governedCount)
      : m.governedHarnessTodayNoSkills;
    // Enforcing changes which arm gets `skills_invoke`: it is always the arm
    // that *has* the skill — the treatment when adding, the control when
    // removing — since an invoked skill must also be available in that run.
    const enforced = enforceSkills && chosen.length > 0;
    const asked = m.askedToInvoke;
    return [
      {
        label: "baseline",
        role: "control" as const,
        description: isAblation
          ? m.ablationStillAvailable(governed, listed || m.theSelectedSkills) +
            (enforced ? asked : ".")
          : m.ablationWithoutStaged(governed, listed || m.theStagedSkills),
        overrides: isAblation
          ? enforced
            ? { ...base, skills_invoke: chosen }
            : { ...base }
          : { ...base, skills_extra: [] }
      },
      {
        // One treatment arm whatever the count: several skills are a *bundle*,
        // and the claim the experiment can make is about the bundle.
        label: isAblation ? "without-skills" : "with-skills",
        role: "treatment" as const,
        description: isAblation
          ? m.ablationRemoved(listed || m.theSelectedSkills)
          : m.additionAvailable(listed || m.theSelectedSkills) +
            (enforced ? asked : m.availableNotForced),
        overrides:
          !isAblation && enforced
            ? { ...base, skills_extra: chosen, skills_invoke: chosen }
            : { ...base, [factorKey]: chosen }
      }
    ];
  }, [
    enforceSkills,
    bareVariant,
    controlHarness,
    m,
    skills,
    crossRepoIds,
    factorKey,
    harness,
    harnessVariant,
    mode,
    skillNames,
    skills,
    targetId,
    treatmentHarness,
    venue
  ]);

  const blocking = useMemo(() => {
    const problems: string[] = [];
    if (!question.trim()) {
      problems.push(m.writeQuestion);
    }
    if (caseIds.length === 0 && !useSuite) {
      problems.push(m.chooseAtLeastOneCase);
    }
    // Cases from two prompt families cannot share one design: whatever variant
    // is picked, the other family's cases would be handed an empty prompt and
    // the backend refuses the launch. Said here, while the selection is on
    // screen and fixable.
    if (
      !useSuite &&
      caseIds.length > 0 &&
      declaredVariants.length > 0 &&
      variantOptions.length === 0
    ) {
      problems.push(m.sharedVariantProblem(variantFamilies.join("; ")));
    }
    if (mode === "cross_repo" && crossRepoIds.length < 2) {
      problems.push(m.crossRepoNeedsTwo);
    }
    if (mode === "harness_evaluation" && venue === "target" && !targetId) {
      problems.push(m.chooseRepositoryForArms);
    }
    if (mode === "harness_evaluation" && venue === "lab" && factorKey === "harness_source") {
      problems.push(m.harnessSourceNeedsTarget);
    }
    if (
      mode === "harness_evaluation" &&
      venue === "target" &&
      (factorKey === "governance" ||
        factorKey === "prompt" ||
        factorKey === "activation")
    ) {
      problems.push(m.labOnlyFactor(m.factorLabel[factorKey]));
    }
    if (
      mode === "harness_evaluation" &&
      (factorKey === "skills_extra" || factorKey === "skills_disabled") &&
      skillNames.length === 0
    ) {
      problems.push(m.chooseAtLeastOneSkill);
    }
    if (mode === "harness_evaluation" && factorKey === "full_harness") {
      if (useSuite) {
        problems.push(m.suiteCannotCarryPair);
      } else if (
        venue === "target" &&
        bareVariant &&
        bareVariant === harnessVariant &&
        skills.length === 0
      ) {
        // Equal prompts alone are not degenerate: the bare arm still hides the
        // whole governed catalog, which is a treatment on its own, and a case
        // written in this UI defines exactly one prompt — the same-prompt pair
        // is its *only* shape, and the honest one, since such a prompt carries
        // no governance instruction for the bare arm to lose. The arms collapse
        // into a single condition only when there are no skills to hide.
        //
        // Scoped to the target venue: in the lab the control arm now also
        // carries `harness_source: bare`, so the two arms differ by the whole
        // governance surface no matter what the prompts and the catalog say.
        problems.push(m.identicalArmsNoSkills);
      }
      if (enforceSkills && skillNames.length === 0) {
        problems.push(m.chooseSkillsForActivation);
      }
    }
    return problems;
  }, [
    caseIds,
    crossRepoIds,
    declaredVariants,
    factorKey,
    m,
    mode,
    question,
    bareVariant,
    harnessVariant,
    skillNames,
    skills,
    enforceSkills,
    targetId,
    useSuite,
    variantFamilies,
    variantOptions,
    venue
  ]);

  // Not everything worth saying blocks creation. A design can be legitimate and
  // still measure something narrower than the factor's name suggests, and the
  // operator should read that before the results rather than after them.
  const advisories = useMemo(() => {
    const notes: string[] = [];
    const isSkillFactor =
      factorKey === "skills_extra" || factorKey === "skills_disabled";
    // A. Skills-only ablation: the arms share a prompt, so what moved is the
    // catalog and nothing else.
    if (
      mode === "harness_evaluation" &&
      factorKey === "full_harness" &&
      bareVariant &&
      bareVariant === harnessVariant &&
      skills.length > 0
    ) {
      const forced = variantOptions.length < 2 ? m.forcedSinglePrompt : "";
      notes.push(
        m.bothArmsRunPrompt(forced, bareVariant) +
          (venue === "lab" ? m.governanceAndCatalogFixed : m.catalogAloneAblation)
      );
    }
    // B. Partial ablation. This is a property of the *venue*, not of the prompt
    // pair: the sync engine rewrites only the generated skills region of
    // CLAUDE.md, so inside the lab the governance body, the SessionStart hook,
    // the subagents and .github/ survive in both arms however the prompts are
    // set. Gating this on "both arms run the same prompt" is what let three
    // experiments describe a control arm as bare when it never was.
    //
    // It no longer applies to `full_harness`: that factor's lab-venue control
    // is now `harness_source: bare`, whose container masks all of it (ADR-0049).
    // A skills-only factor still leaves the governance standing on both sides,
    // which is correct for what those factors measure — and worth saying.
    if (mode === "harness_evaluation" && venue === "lab" && isSkillFactor) {
      notes.push(m.partialAblationNote);
    }
    // B2. The skill under test is the one *selected*: the treatment removes
    // (or stages) it and the control keeps (or lacks) it, so whatever is left
    // unticked is held constant across both arms. A selection of "all but
    // one" reads like the inverse — as if the unticked skill were the subject
    // — and it is exactly the one skill the design never varies. That is how
    // an experiment meant to test `systematic_debugging` ran with it available
    // to both arms and asked for by neither (ADR-0061). Said here, before the
    // runs, because no auditor can recover the operator's intent afterwards.
    if (mode === "harness_evaluation" && isSkillFactor && skillNames.length > 1) {
      const catalog =
        factorKey === "skills_disabled"
          ? skills.map((skill) => skill.name)
          : stagedSkills.map((skill) => skill.name);
      const leftOut = catalog
        .filter((name) => !skillNames.includes(name))
        .sort();
      const moved =
        factorKey === "skills_disabled" ? m.removedFromTreatment : m.stagedInTreatment;
      let note = m.bundleTogether(skillNames.length, moved);
      if (leftOut.length > 0 && leftOut.length <= 3) {
        const isPlural = leftOut.length > 1;
        note += m.leftOutSuffix(
          leftOut.map((name) => `'${name}'`).join(", "),
          isPlural,
          factorKey === "skills_disabled" ? m.leftOutAvailable(isPlural) : m.leftOutAbsent(isPlural)
        );
      }
      notes.push(note);
    }
    // C. Prompt and catalog moved together. Legitimate — it is what the factor
    // means (ADR-0044) — and unattributable, which the reader must know before
    // the numbers rather than after them.
    if (
      mode === "harness_evaluation" &&
      factorKey === "full_harness" &&
      bareVariant &&
      harnessVariant &&
      bareVariant !== harnessVariant
    ) {
      notes.push(
        m.armsDifferInPrompt(
          bareVariant,
          harnessVariant,
          venue === "lab" ? m.inGovernanceBody : ""
        )
      );
    }
    // D. The pickers are narrower than the case list suggests. Not a fault in
    // the design — the intersection is what every case can actually run — but
    // the operator picked those cases for a reason and should see that one
    // family is deciding the prompt for all of them.
    if (!useSuite && variantOptions.length > 0) {
      const dropped = declaredVariants.filter(
        (variant) => !variantOptions.includes(variant)
      );
      if (dropped.length > 0) {
        notes.push(
          m.variantsDroppedNote(
            dropped.map((variant) => `'${variant}'`).join(", "),
            dropped.length === 1 ? m.verbIs : m.verbAre,
            variantOptions.map((variant) => `'${variant}'`).join(", ")
          )
        );
      }
    }
    // E. A bare arm told to follow what it cannot read. `governance` inherits
    // the launch prompt on both sides, so a harness-family variant reaches the
    // masked arm as an instruction about documents that are not there.
    if (
      mode === "harness_evaluation" &&
      factorKey === "governance" &&
      /^(harness|swe_harness|sdlc)/.test(promptVariant)
    ) {
      notes.push(m.bareArmToldToFollow(promptVariant));
    }
    return notes;
  }, [
    bareVariant,
    declaredVariants,
    factorKey,
    harnessVariant,
    m,
    mode,
    promptVariant,
    skillNames,
    skills,
    stagedSkills,
    useSuite,
    variantOptions,
    venue
  ]);

  // A verdict only describes the configuration that produced it, so any edit
  // voids it rather than leaving a stale green tick above the Launch button.
  //
  // "The configuration" is everything that shapes what will be launched, not
  // only the fields `buildLaunchConfig` sends to the preflight. The per-arm
  // prompts, the skills and the arm harnesses decide what the arms *are*, and
  // a verdict taken before one of them moved describes an experiment nobody is
  // about to run. Keep this list in step with the `conditions` memo.
  useEffect(() => {
    setCheck(null);
  }, [
    bareVariant,
    caseIds,
    controlHarness,
    crossRepoIds,
    dryRun,
    enforceSkills,
    executorModel,
    factorKey,
    harness,
    harnessVariant,
    maxSteps,
    mode,
    plannerModel,
    promptVariant,
    skillNames,
    skills,
    targetId,
    treatmentHarness,
    useSuite,
    venue
  ]);

  /**
   * ADR-0070-era fix (spec 2026-11-08-local-model-tuning, "Host guard"): the
   * preflight sends a real completion and a ~32k-token context probe, which
   * can make LM Studio auto-load a model nobody asked for — and if a lab run
   * already has that host busy, the load competes with it. Only a `gateway/…`
   * or `lmstudio/…` executor can be resolved to a direct host at all, so the
   * check is skipped for everything else; any failure of the check itself
   * (host unreachable, unknown model, backend offline) proceeds silently —
   * the probe runs today with no such guard, and fails no harder than before.
   */
  const runPreflight = async () => {
    setChecking(true);
    setError(null);
    setCheck(null);
    try {
      setCheck(await api.preflightRunConfig(buildLaunchConfig()));
    } catch (checkError) {
      setError(
        checkError instanceof Error ? checkError.message : m.unableToCheck
      );
    } finally {
      setChecking(false);
    }
  };

  const handleCheck = async () => {
    const needsLoadCheck =
      executorModel.startsWith("gateway/") ||
      executorModel.startsWith("lmstudio/") ||
      executorModel.startsWith("ollama/");
    if (needsLoadCheck) {
      // Set before the request goes out, not after it resolves — the button
      // shows a spinner for the whole round trip, including the
      // model-load-check itself, not just the preflight that may follow it.
      setChecking(true);
      try {
        const result = await api.local.modelLoadCheck(executorModel);
        if (result.loaded === false) {
          setPendingLoadCheck(result);
          setChecking(false);
          return;
        }
      } catch {
        // Fail open: proceed exactly as the page did before this check existed.
      }
    }
    await runPreflight();
  };

  const nativeIndex = useNativeModelIndex();
  const handleHarnessChange = (next: string) => {
    setHarness(next);
    // The first native model the gateway serves, never one it would refuse.
    setExecutorModel(defaultNativeModel(next, nativeIndex) ?? gatewayDefault);
  };

  const buildLaunchConfig = (): RunConfigPayload => ({
    case_id: caseIds[0] ?? "",
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

  /**
   * Create under the first free id derived from the question.
   *
   * The listing can be stale — another tab, another operator — so a refusal on
   * the id we picked is not an error to report but a collision to step past.
   */
  const createUnderFreeId = async <T extends { experiment_id: string }>(
    create: (experimentId: string) => Promise<T>
  ): Promise<T> => {
    const base = slugify(question);
    const taken = await takenExperimentIds();
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const experimentId = uniqueExperimentId(base, taken);
      try {
        const created = await create(experimentId);
        if (created.experiment_id !== base) {
          setNotice(m.filedAs(created.experiment_id));
        }
        return created;
      } catch (createError) {
        if (!isDuplicateId(createError)) {
          throw createError;
        }
        // Lost the race for that id; take the next counter and try again.
        taken.add(experimentId);
        lastError = createError;
      }
    }
    throw lastError instanceof Error ? lastError : new Error(m.unableToFindFreeId);
  };

  /** Create the experiment; with `launch`, queue every run of the design too. */
  const submit = async (launch: boolean) => {
    setIsSaving(true);
    setError(null);
    setNotice(null);
    setCreatedId(null);
    try {
      if (useSuite) {
        const control = conditions.find((item) => item.role === "control");
        const treatment = conditions.find((item) => item.role === "treatment");
        if (!control || !treatment) {
          throw new Error(m.suiteNeedsControlAndTreatment);
        }
        const fromSuite = await createUnderFreeId((experimentId) =>
          api.createExperimentFromSuite({
            experiment_id: experimentId,
            suite_id: suiteId,
            question,
            name: question.slice(0, 80),
            control: {
              label: control.label,
              description: control.description,
              overrides: control.overrides
            },
            treatment: {
              label: treatment.label,
              description: treatment.description,
              overrides: treatment.overrides
            }
          })
        );
        setWarnings(fromSuite.warnings ?? []);
        await finish(fromSuite.experiment_id, launch);
        return;
      }
      const created = await createUnderFreeId((experimentId) =>
        api.createExperiment({
          experiment_id: experimentId,
          name: question.slice(0, 80),
          question,
          mode,
          case_ids: caseIds,
          repetitions,
          conditions,
          // The objections the designer was shown and created anyway. Sent so
          // the results page can repeat them next to the verdict instead of
          // letting them die in this form (ADR-0046).
          design_notes: advisories
        })
      );
      setWarnings(created.warnings ?? []);
      await finish(created.experiment_id, launch);
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : m.unableToCreateExperiment
      );
    } finally {
      setIsSaving(false);
    }
  };

  /**
   * The step the old flow hid: creation used to drop you on the experiment
   * page to configure and launch a second time. Launching here, with the
   * configuration this form already collected, makes the wizard one journey —
   * and a refused launch still leaves a valid experiment to fix and launch
   * from its own page.
   */
  const finish = async (experimentId: string, launch: boolean) => {
    if (launch) {
      try {
        await api.launchExperiment(experimentId, buildLaunchConfig());
      } catch (launchError) {
        setCreatedId(experimentId);
        setError(
          m.experimentCreatedLaunchRefused(
            experimentId,
            launchError instanceof Error ? launchError.message : m.unknownError
          )
        );
        return;
      }
    }
    navigate(`/evaluate/experiments?experiment=${experimentId}`);
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} /> : null}

      <div className="mode-switch" role="tablist">
        <button
          aria-selected={mode === "harness_evaluation"}
          className={mode === "harness_evaluation" ? "primary" : "secondary"}
          onClick={() => setMode("harness_evaluation")}
          role="tab"
          type="button"
        >
          {m.evaluateHarnessChange}
        </button>
        <button
          aria-selected={mode === "cross_repo"}
          className={mode === "cross_repo" ? "primary" : "secondary"}
          onClick={() => setMode("cross_repo")}
          role="tab"
          type="button"
        >
          {m.testAcrossRepositories}
        </button>
      </div>

      <div className="panel">
        <label className="field">
          <span>
            {m.whatAreYouTryingToFindOut}{" "}
            {question.trim() ? null : (
              <em className="error-text small">{m.requiredQuestion}</em>
            )}
          </span>
          <input
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={
              mode === "cross_repo" ? m.placeholderCrossRepo : m.placeholderHarnessEval
            }
            value={question}
          />
        </label>
        {question.trim() ? null : (
          <div className="button-row">
            {suggestedQuestions.map((suggestion, index) => (
              <button
                className="secondary"
                key={suggestion}
                onClick={() => setQuestion(suggestion)}
                type="button"
              >
                {index === 0 ? m.useASuggestedQuestion : ""}
                &ldquo;{suggestion}&rdquo;
              </button>
            ))}
          </div>
        )}
      </div>

      {mode === "harness_evaluation" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.whatVaries}</h2>
          </div>
          <fieldset className="field">
            <legend>{m.whereCasesRun}</legend>
            <label className="checkbox">
              <input
                checked={venue === "lab"}
                name="venue"
                onChange={() => setVenue("lab")}
                type="radio"
              />
              <span>{m.thisLab}</span>
            </label>
            <label className="checkbox">
              <input
                checked={venue === "target"}
                name="venue"
                onChange={() => setVenue("target")}
                type="radio"
              />
              <span>{m.externalRepo}</span>
            </label>
          </fieldset>
          {venue === "target" ? (
            <label className="field">
              <span>{m.repository}</span>
              <select
                onChange={(event) => setTargetId(event.target.value)}
                value={targetId}
              >
                <option value="">{m.selectRepository}</option>
                {targets.map((target) => (
                  <option key={target.target_id} value={target.target_id}>
                    {target.name}
                  </option>
                ))}
              </select>
              {targets.length === 0 ? (
                <span className="muted small">{m.noRepositoriesRegistered}</span>
              ) : null}
            </label>
          ) : null}
          <label className="field">
            <span>{m.factorUnderTest}</span>
            <select
              onChange={(event) => setFactorKey(event.target.value as FactorKey)}
              value={factorKey}
            >
              {FACTORS.map((item) => {
                // Native vs injected compares two governance surfaces, and
                // inside the lab there is only one. The mirror: a bare arm is a
                // masked lab container, which no external repository can host.
                const needsTarget = item.key === "harness_source" && venue === "lab";
                const needsLab =
                  venue === "target" &&
                  (item.key === "governance" ||
                    item.key === "prompt" ||
                    item.key === "activation");
                return (
                  <option
                    disabled={needsTarget || needsLab}
                    key={item.key}
                    value={item.key}
                  >
                    {m.factorLabel[item.key]}
                    {needsTarget ? m.needsTarget : ""}
                    {needsLab ? m.needsLab : ""}
                  </option>
                );
              })}
            </select>
          </label>
          <p className="muted">{m.factorBlurb[factorKey]}</p>
          {factorKey === "full_harness" ||
          factorKey === "prompt" ||
          factorKey === "activation" ? (
            variantOptions.length === 0 ? (
              <p className="muted small">
                {caseIds.length === 0 || declaredVariants.length === 0
                  ? m.pickCasesFirst
                  : m.noSharedPromptNarrow}
              </p>
            ) : (
              <div className="field-row">
                <label className="field">
                  <span>{m.bareArmPrompt}</span>
                  <select
                    onChange={(event) => setBareVariant(event.target.value)}
                    value={bareVariant}
                  >
                    {variantOptions.map((variant) => (
                      <option key={variant} value={variant}>
                        {variant}
                      </option>
                    ))}
                  </select>
                  <span className="muted small">{m.bareArmPromptHint}</span>
                  <PromptPreview entries={promptEntriesFor(bareVariant)} variant={bareVariant} />
                </label>
                <label className="field">
                  <span>{m.fullHarnessArmPrompt}</span>
                  <select
                    onChange={(event) => setHarnessVariant(event.target.value)}
                    value={harnessVariant}
                  >
                    {variantOptions.map((variant) => (
                      <option key={variant} value={variant}>
                        {variant}
                      </option>
                    ))}
                  </select>
                  <span className="muted small">
                    {m.fullHarnessArmPromptHint(skills.length)}
                  </span>
                  <PromptPreview
                    entries={promptEntriesFor(harnessVariant)}
                    variant={harnessVariant}
                  />
                </label>
              </div>
            )
          ) : null}
          {factorKey === "full_harness" && skills.length > 0 ? (
            <fieldset className="field">
              <legend>{m.activationLegend}</legend>
              <label className="checkbox">
                <input
                  checked={enforceSkills}
                  onChange={(event) => setEnforceSkills(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  {m.askAgentToUseHarness}{" "}
                  <small className="muted">{m.askAgentToUseHarnessHint}</small>
                </span>
              </label>
              {enforceSkills ? (
                <div className="checkbox-grid">
                  {skills.map((skill) => (
                    <label className="checkbox" key={skill.name}>
                      <input
                        checked={skillNames.includes(skill.name)}
                        onChange={() =>
                          setSkillNames((current) => toggle(current, skill.name))
                        }
                        type="checkbox"
                      />
                      <span>
                        {skill.name} <small className="muted">{skill.kind}</small>
                      </span>
                    </label>
                  ))}
                </div>
              ) : null}
              {enforceSkills && skillNames.length > 1 ? (
                <p className="muted small">{m.skillsNamedTogether(skillNames.length)}</p>
              ) : null}
            </fieldset>
          ) : null}
          {factorKey === "skills_extra" || factorKey === "skills_disabled" ? (
            <fieldset className="field">
              <legend>
                {factorKey === "skills_extra"
                  ? m.stagedSkillsToAdd
                  : m.governedSkillsToRemove}
              </legend>
              {factorKey === "skills_extra" && stagedSkills.length === 0 ? (
                <p className="muted small">{m.nothingStagedToOffer}</p>
              ) : (
                <div className="checkbox-grid">
                  {(factorKey === "skills_extra"
                    ? stagedSkills.map((skill) => ({
                        name: skill.name,
                        hint: skill.status
                      }))
                    : skills.map((skill) => ({ name: skill.name, hint: skill.kind }))
                  ).map((skill) => (
                    <label className="checkbox" key={skill.name}>
                      <input
                        checked={skillNames.includes(skill.name)}
                        onChange={() =>
                          setSkillNames((current) => toggle(current, skill.name))
                        }
                        type="checkbox"
                      />
                      <span>
                        {skill.name} <small className="muted">{skill.hint}</small>
                      </span>
                    </label>
                  ))}
                </div>
              )}
              <label className="checkbox">
                <input
                  checked={enforceSkills}
                  disabled={skillNames.length === 0}
                  onChange={(event) => setEnforceSkills(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  {m.enforceSkillUse}{" "}
                  <small className="muted">{m.enforceSkillUseHint}</small>
                </span>
              </label>
              {skillNames.length > 1 ? (
                <p className="muted small">{m.skillsSelectedBundle(skillNames.length)}</p>
              ) : null}
            </fieldset>
          ) : null}
          {factorKey === "harness" ? (
            <div className="field-row">
              <label className="field">
                <span>{m.controlHarness}</span>
                <select
                  onChange={(event) => setControlHarness(event.target.value)}
                  value={controlHarness}
                >
                  {HARNESS_OPTIONS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>{m.treatmentHarness}</span>
                <select
                  onChange={(event) => setTreatmentHarness(event.target.value)}
                  value={treatmentHarness}
                >
                  {HARNESS_OPTIONS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="panel">
          <div className="panel-head">
            <h2>{m.whatVaries}</h2>
          </div>
          <label className="field">
            <span>{m.harnessHeldFixedAcrossRepos}</span>
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
          </label>
          <fieldset className="field">
            <legend>{m.repositories}</legend>
            {targets.length === 0 ? (
              <p className="muted">{m.noRepositoriesAddOne}</p>
            ) : null}
            <div className="checkbox-grid">
              {targets.map((target) => (
                <label className="checkbox" key={target.target_id}>
                  <input
                    checked={crossRepoIds.includes(target.target_id)}
                    onChange={() =>
                      setCrossRepoIds((current) => toggle(current, target.target_id))
                    }
                    type="checkbox"
                  />
                  <span>{target.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <p className="callout callout--warning">{m.resultsStayPerRepo}</p>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <h2>{m.casesAndRepetitions}</h2>
          {useSuite ? <span className="badge neutral">{m.fromTheSuite}</span> : null}
        </div>
        {mode === "harness_evaluation" ? (
          <label className="checkbox">
            <input
              checked={useSuite}
              onChange={(event) => setUseSuite(event.target.checked)}
              type="checkbox"
            />
            <span>{m.useRegressionSuite}</span>
          </label>
        ) : null}
        {useSuite ? (
          <>
            <label className="field">
              <span>{m.suite}</span>
              <input
                onChange={(event) => setSuiteId(event.target.value)}
                value={suiteId}
              />
            </label>
            <p className="muted">{m.suiteDeclaresDesign}</p>
          </>
        ) : (
        <>
        <fieldset className="field">
          <legend>{m.casesLegend}</legend>
          <div className="checkbox-grid">
            {cases.map((item) => (
              <label className="checkbox" key={item.case_id}>
                <input
                  checked={caseIds.includes(item.case_id)}
                  onChange={() => setCaseIds((current) => toggle(current, item.case_id))}
                  type="checkbox"
                />
                <span>{item.name || item.case_id}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <label className="field">
          <span>{m.repetitionsPerCasePerArm}</span>
          <select
            onChange={(event) => setRepetitions(Number(event.target.value))}
            value={repetitions}
          >
            {REPETITION_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {m.repetitionChoices[choice.key]}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">{m.agentsStochastic}</p>
        </>
        )}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{m.howItRuns}</h2>
        </div>
        <p className="muted">{m.sharedByEveryArm}</p>
        {mode === "harness_evaluation" && factorKey === "harness" ? (
          <p className="muted small">{m.designVariesHarness}</p>
        ) : mode === "harness_evaluation" ? (
          <label className="field">
            <span>{m.harnessHeldFixedAcrossArms}</span>
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
                {m.runsHarnessCli(harness)}
                {harness === "claude" ? m.claudeAuthHint : m.hostModeHint}
              </span>
            ) : null}
          </label>
        ) : null}
        <div className="field-row">
          <ModelPicker
            label={m.executorModelLabel}
            directErrors={directErrors}
            nativeOptions={
              mode === "harness_evaluation" && factorKey === "harness"
                ? undefined
                : HARNESS_NATIVE_MODELS[harness]
            }
            onChange={setExecutorModel}
            onReload={() => void loadModels(true)}
            options={modelOptions}
            reloading={reloadingModels}
            value={executorModel}
          >
            <small className="muted">{m.executorModelHeldFixed}</small>
          </ModelPicker>
          <ModelPicker
            allowNone
            label={m.plannerModelLabel}
            directErrors={directErrors}
            onChange={setPlannerModel}
            options={modelOptions}
            value={plannerModel}
          />
        </div>
        <div className="field-row">
          {useSuite ? (
            <div className="field">
              <span>{m.promptVariantLabel}</span>
              <p className="muted small">{m.suiteDeclaresOwnPrompt}</p>
            </div>
          ) : mode === "harness_evaluation" && factorKey === "full_harness" ? (
            <div className="field">
              <span>{m.promptVariantLabel}</span>
              <p className="muted small">{m.setPerArmAbove}</p>
            </div>
          ) : (
            <label className="field">
              <span>{m.promptVariantLabel}</span>
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
              <span className="muted small">{m.variantsEverySelectedCase}</span>
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
        <label className="checkbox">
          <input
            checked={dryRun}
            onChange={(event) => setDryRun(event.target.checked)}
            type="checkbox"
          />
          <span>{m.dryRunLabel}</span>
        </label>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{m.designAboutToCreate}</h2>
          <span className="badge neutral">
            {m.arm(conditions.length)}
            {useSuite ? (
              m.suiteCasesAndRepetitions
            ) : (
              <>
                {m.caseCountTimes(caseIds.length)}
                {m.repetitionsEqualsRuns(
                  repetitions,
                  conditions.length * caseIds.length * repetitions
                )}
              </>
            )}
          </span>
        </div>
        <ul className="design-preview">
          {conditions.map((condition) => (
            <li key={condition.label}>
              <span className={`badge ${condition.role === "control" ? "" : "neutral"}`}>
                {m.roleLabel[condition.role]}
              </span>
              <strong>{condition.label}</strong>
              <span className="muted">{condition.description}</span>
            </li>
          ))}
        </ul>
        {blocking.length > 0 ? (
          <div className="callout callout--warning">
            <IconAlert size={16} />
            <div>
              <strong>{m.notReadyYet}</strong>
              <ul className="blocking-list">
                {blocking.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}
        {advisories.length > 0 ? (
          <ul className="blocking-list blocking-list--warning">
            {advisories.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}
        {notice ? <p className="callout callout--ok">{notice}</p> : null}
        {warnings.length > 0 ? (
          <ul className="blocking-list blocking-list--warning">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
        {/*
          The preflight checks the shared run configuration — case, models,
          harness, budget — and knows nothing about the arms. A green "Ready"
          under an unresolved blocker therefore answers a question nobody
          asked, and reads as permission to launch. A refusal still shows: it
          carries the provider's own reason, which the blockers cannot.
        */}
        {check && !(check.ok && blocking.length > 0) ? (
          <p className={`callout ${check.ok ? "callout--ok" : "callout--warning"}`}>
            <strong>{check.ok ? m.ready : m.blocked}</strong> — {check.detail}
          </p>
        ) : null}
        {pendingLoadCheck ? (
          pendingLoadCheck.guard &&
          pendingLoadCheck.guard.running.length + pendingLoadCheck.guard.queued.length > 0 ? (
            <HostGuardModal
              action="check"
              guard={pendingLoadCheck.guard}
              onCancel={() => setPendingLoadCheck(null)}
              onContinue={() => {
                setPendingLoadCheck(null);
                return runPreflight();
              }}
            />
          ) : (
            <div className="modal-overlay">
              <div aria-modal="true" className="modal-dialog" role="dialog">
                <p>
                  {m.loadCheckTitle(
                    pendingLoadCheck.model_bare,
                    pendingLoadCheck.size_gb?.toFixed(1) ?? "?",
                    pendingLoadCheck.host ?? "?"
                  )}
                </p>
                <div className="toolbar">
                  <button
                    className="secondary"
                    onClick={() => setPendingLoadCheck(null)}
                    type="button"
                  >
                    {m.loadCheckCancel}
                  </button>
                  <button
                    className="primary"
                    onClick={() => {
                      setPendingLoadCheck(null);
                      void runPreflight();
                    }}
                    type="button"
                  >
                    {m.loadCheckContinue}
                  </button>
                </div>
              </div>
            </div>
          )
        ) : null}
        <div className="toolbar">
          <button
            className="secondary"
            disabled={checking || isSaving || useSuite || caseIds.length === 0}
            onClick={() => void handleCheck()}
            title={
              useSuite
                ? m.checkSuiteResolvedAtLaunch
                : caseIds.length === 0
                  ? m.pickAtLeastOneCaseFirst
                  : undefined
            }
            type="button"
          >
            {checking ? m.checking : m.checkConfiguration}
          </button>
          <button
            className="primary"
            disabled={blocking.length > 0 || isSaving}
            onClick={() => void submit(true)}
            title={blocking.length > 0 ? blocking.join(" ") : undefined}
            type="button"
          >
            {isSaving ? m.working : m.createAndLaunchRuns}
            <IconArrowRight size={15} />
          </button>
          <button
            className="secondary"
            disabled={blocking.length > 0 || isSaving}
            onClick={() => void submit(false)}
            title={blocking.length > 0 ? blocking.join(" ") : undefined}
            type="button"
          >
            {m.createWithoutLaunching}
          </button>
        </div>
        {createdId ? (
          <p className="muted small">
            {m.experimentExists(
              <Link to={`/evaluate/experiments?experiment=${createdId}`}>{m.openIt}</Link>
            )}
          </p>
        ) : null}
      </div>

      <HelpPanel doc="run" title={m.whyFormShaped}>
        <p>{m.whyFormShapedBody1}</p>
        <p>{m.whyFormShapedBody2}</p>
      </HelpPanel>
    </section>
  );
}
