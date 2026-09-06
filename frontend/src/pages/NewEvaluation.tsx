import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api";
import {
  HARNESS_NATIVE_MODELS,
  HARNESS_OPTIONS,
  HOST_HARNESSES
} from "../harnesses";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { ModelPicker } from "../components/ModelPicker";
import { PromptPreview } from "../components/PromptPreview";
import { IconAlert, IconArrowRight } from "../components/icons";
import type {
  CaseRecord,
  ExperimentMode,
  HarnessSkill,
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
    label: "Governance source",
    blurb:
      "The repository's own governance versus the harness this lab injects. The honest baseline for 'does our harness help this team?'.",
    control: { harness_source: "native" },
    controlLabel: "Repository's own setup",
    treatmentLabel: "Lab harness injected"
  },
  {
    key: "full_harness",
    label: "Full harness",
    blurb:
      "The whole harness as one treatment: the governance-loaded prompt plus " +
      "every skill, against a bare arm with the skills hidden and a plain " +
      "prompt. A win says the harness earns its keep as a unit — ablate " +
      "factors afterwards to see which parts do the work.",
    controlLabel: "Bare — plain prompt, skills hidden",
    treatmentLabel: "Full harness"
  },
  {
    key: "skills_extra",
    label: "Add skills",
    blurb:
      "Baseline versus baseline plus one or more staged skills — skills being " +
      "trialled from the Skills page, not yet part of the harness. One skill is " +
      "the narrowest test; several test the bundle as a unit.",
    controlLabel: "Without the skills",
    treatmentLabel: "With the skills"
  },
  {
    key: "skills_disabled",
    label: "Remove skills",
    blurb:
      "Ablation of skills already in the harness: if removing them changes " +
      "nothing, they were not earning their place in the context window. This " +
      "is how a governed skill's value is measured — it is already present, so " +
      "the comparison is with it versus without it.",
    controlLabel: "With the skills",
    treatmentLabel: "Without the skills"
  },
  {
    key: "harness",
    label: "Swap the harness",
    blurb: "The same case and model driven by a different agent CLI.",
    controlLabel: "Current harness",
    treatmentLabel: "Alternative harness"
  }
] as const;

type FactorKey = (typeof FACTORS)[number]["key"];

/** Backend ceiling on an experiment slug; suffixes have to fit inside it. */
const MAX_SLUG = 64;

const REPETITION_CHOICES = [
  { value: 2, label: "2 — exploratory only" },
  { value: 5, label: "5 — useful comparison" },
  { value: 10, label: "10 — stronger evidence" }
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
  const [repetitions, setRepetitions] = useState(5);
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
      .catch(() => setError("Unable to load repositories and cases."));
  }, []);

  const factor = useMemo(
    () => FACTORS.find((item) => item.key === factorKey) ?? FACTORS[0],
    [factorKey]
  );

  const selectedCases = useMemo(
    () => cases.filter((item) => caseIds.includes(item.case_id)),
    [cases, caseIds]
  );

  // Union across the selected cases, exactly as batch mode does it: a case
  // without the chosen variant contributes no runs for it, but an intersection
  // goes empty as soon as one case is unusual.
  const variantOptions = useMemo(() => {
    const names = new Set<string>();
    for (const item of selectedCases) {
      for (const variant of Object.keys(item.prompt_variants ?? {})) {
        names.add(variant);
      }
    }
    return [...names].sort();
  }, [selectedCases]);

  // Cases feeding the "View prompt" toggle next to bare/harness selects — one
  // entry per selected case that actually defines this variant, so a person
  // can see what they picked without leaving the form. `prompt_variants`
  // holds either the literal text or a `task.yaml`-style path, so the
  // content itself is fetched from the API, which resolves either form.
  const promptEntriesFor = (variant: string) =>
    selectedCases
      .filter((item) => typeof item.prompt_variants?.[variant] === "string")
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
  }, [factorKey, venue]);

  // The two skill factors draw from different catalogs, so a selection cannot
  // survive switching between them.
  useEffect(() => {
    setSkillNames([]);
    setEnforceSkills(false);
  }, [factorKey]);

  // Pair the arms with the conventional members of the prompt family:
  // naive→sdlc for the lab's own cases, swe_default→swe_harness for imported
  // SWE-bench instances. Falls back to first/last so an unusual family still
  // yields a usable pair the operator can correct.
  useEffect(() => {
    if (factorKey !== "full_harness" || variantOptions.length === 0) {
      return;
    }
    const pick = (candidates: string[], fallback: string) =>
      candidates.find((name) => variantOptions.includes(name)) ?? fallback;
    setBareVariant((current) =>
      variantOptions.includes(current)
        ? current
        : pick(["naive", "swe_default", "vibe"], variantOptions[0])
    );
    setHarnessVariant((current) =>
      variantOptions.includes(current)
        ? current
        : pick(
            ["sdlc", "swe_harness"],
            variantOptions[variantOptions.length - 1]
          )
    );
  }, [factorKey, variantOptions]);

  // Written FOR the operator, adopted BY the operator: the backend refuses a
  // question-less experiment on purpose (a batch with authority it has not
  // earned), so the wizard offers a starting sentence rather than silently
  // inventing one.
  const suggestedQuestion = useMemo(() => {
    if (mode === "cross_repo") {
      return "Does this harness hold up across repositories, or does it degrade outside the one it was built in?";
    }
    if (factorKey === "full_harness") {
      return "Does the full harness improve outcomes over the bare model on these cases?";
    }
    if (factorKey === "skills_extra") {
      return skillNames.length > 0
        ? `Does adding ${skillNames.join(", ")} improve outcomes?`
        : "Does adding this staged skill improve outcomes?";
    }
    if (factorKey === "skills_disabled") {
      return skillNames.length > 0
        ? `Does removing ${skillNames.join(", ")} change outcomes at all?`
        : "Does removing this skill change outcomes at all?";
    }
    if (factorKey === "harness") {
      return `Does ${treatmentHarness} beat ${controlHarness} on the same cases and model?`;
    }
    return "Does the injected lab harness improve over the repository's own setup?";
  }, [controlHarness, factorKey, mode, skillNames, treatmentHarness]);

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
        description:
          index === 0
            ? `Reference repository — the others are read against ${id}.`
            : `The same harness, unchanged, against ${id}.`,
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
          description: "The repository's own governance, with nothing injected.",
          overrides: { ...base, harness_source: "native" }
        },
        {
          label: "injected",
          role: "treatment" as const,
          description: "The lab harness injected into the workspace.",
          overrides: { ...base, harness_source: "injected" }
        }
      ];
    }
    if (factorKey === "harness") {
      return [
        {
          label: controlHarness,
          role: "control" as const,
          description: "The harness in use today.",
          overrides: { ...base, harness: controlHarness }
        },
        {
          label: treatmentHarness,
          role: "treatment" as const,
          description: "The alternative under test.",
          overrides: { ...base, harness: treatmentHarness }
        }
      ];
    }
    if (factorKey === "full_harness") {
      // Two levers moved together on purpose — the governance prompt and the
      // skill catalog are one treatment, and the claim is about the harness
      // as a unit.
      return [
        {
          label: "bare",
          role: "control" as const,
          description:
            bareVariant && bareVariant === harnessVariant
              ? `Same prompt ('${bareVariant}') with every governed skill hidden.`
              : `Plain prompt ('${bareVariant || "?"}') with every governed skill hidden.`,
          overrides: {
            ...base,
            prompt_variant: bareVariant,
            skills_disabled: skills.map((skill) => skill.name).sort()
          }
        },
        {
          label: "full-harness",
          role: "treatment" as const,
          description: `Governance prompt ('${harnessVariant || "?"}') with the whole skill catalog available.`,
          overrides: { ...base, prompt_variant: harnessVariant, skills_disabled: [] }
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
      ? `the governed harness as it is today (CLAUDE.md + ${governedCount} governed skills)`
      : "the governed harness as it is today";
    // Enforcing changes which arm gets `skills_invoke`: it is always the arm
    // that *has* the skill — the treatment when adding, the control when
    // removing — since an invoked skill must also be available in that run.
    const enforced = enforceSkills && chosen.length > 0;
    const asked =
      ", and the agent is asked at the start of every step to invoke it and follow it.";
    return [
      {
        label: "baseline",
        role: "control" as const,
        description: isAblation
          ? `${governed}, with ${listed || "the selected skills"} still available` +
            (enforced ? asked : ".")
          : `${governed}, without ${listed || "the staged skills"}.`,
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
          ? `The same harness with ${listed || "the selected skills"} removed.`
          : `The same harness with ${listed || "the selected skills"} added as an ` +
            "available skill" +
            (enforced
              ? asked
              : ". Available, not forced: the agent invokes it only if " +
                "the case's instruction or the skill's own triggers call for it."),
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
      problems.push("Write the question this experiment answers.");
    }
    if (caseIds.length === 0 && !useSuite) {
      problems.push("Choose at least one case.");
    }
    if (mode === "cross_repo" && crossRepoIds.length < 2) {
      problems.push("Cross-repo needs at least two repositories to vary.");
    }
    if (mode === "harness_evaluation" && venue === "target" && !targetId) {
      problems.push("Choose the repository every arm will run against.");
    }
    if (mode === "harness_evaluation" && venue === "lab" && factorKey === "harness_source") {
      problems.push(
        "Native versus injected governance needs an external repository — " +
          "inside the lab there is no other governance to compare against."
      );
    }
    if (
      mode === "harness_evaluation" &&
      (factorKey === "skills_extra" || factorKey === "skills_disabled") &&
      skillNames.length === 0
    ) {
      problems.push("Choose at least one skill for the treatment to vary.");
    }
    if (mode === "harness_evaluation" && factorKey === "full_harness") {
      if (useSuite) {
        problems.push(
          "The regression suite fixes its own prompts, so it cannot carry the " +
            "bare-versus-governed pair. Pick cases by hand, or choose another factor."
        );
      } else if (caseIds.length > 0 && variantOptions.length < 2) {
        problems.push(
          "The selected cases define fewer than two prompt variants, so there " +
            "is no bare-versus-governed pair to compare."
        );
      } else if (
        bareVariant &&
        bareVariant === harnessVariant &&
        skills.length === 0
      ) {
        // Equal prompts alone are not degenerate: the bare arm still hides the
        // whole governed catalog, which is a treatment on its own. The arms
        // only collapse into one condition when there are no skills to hide.
        problems.push(
          "Both arms use the same prompt variant and there are no governed " +
            "skills to hide, so the two arms would be identical."
        );
      }
    }
    return problems;
  }, [
    caseIds,
    crossRepoIds,
    factorKey,
    mode,
    question,
    bareVariant,
    harnessVariant,
    skillNames,
    skills,
    targetId,
    useSuite,
    variantOptions,
    venue
  ]);

  // Not everything worth saying blocks creation. A design can be legitimate and
  // still measure something narrower than the factor's name suggests, and the
  // operator should read that before the results rather than after them.
  const advisories = useMemo(() => {
    const notes: string[] = [];
    if (
      mode === "harness_evaluation" &&
      factorKey === "full_harness" &&
      bareVariant &&
      bareVariant === harnessVariant &&
      skills.length > 0
    ) {
      notes.push(
        `Both arms run '${bareVariant}', so the treatment is the skill catalog ` +
          "alone rather than the harness as a unit. That is a sound ablation — " +
          "it is what the 'Remove skills' factor states directly — but read the " +
          "result as a claim about the skills, not about the whole harness."
      );
    }
    return notes;
  }, [bareVariant, factorKey, harnessVariant, mode, skills]);

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

  const handleCheck = async () => {
    setChecking(true);
    setError(null);
    setCheck(null);
    try {
      setCheck(await api.preflightRunConfig(buildLaunchConfig()));
    } catch (checkError) {
      setError(
        checkError instanceof Error ? checkError.message : "Unable to check"
      );
    } finally {
      setChecking(false);
    }
  };

  const handleHarnessChange = (next: string) => {
    setHarness(next);
    const native = HARNESS_NATIVE_MODELS[next];
    setExecutorModel(native ? native[0].id : gatewayDefault);
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
          setNotice(
            `An experiment already asks this question, so this one was filed as ` +
              `'${created.experiment_id}'. The earlier one is untouched.`
          );
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
    throw lastError instanceof Error
      ? lastError
      : new Error("Unable to find a free experiment id.");
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
          throw new Error("A suite comparison needs one control arm and one treatment.");
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
          conditions
        })
      );
      setWarnings(created.warnings ?? []);
      await finish(created.experiment_id, launch);
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : "Unable to create experiment"
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
          `Experiment '${experimentId}' was created, but launching was refused: ` +
            (launchError instanceof Error ? launchError.message : "unknown error")
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
          <h1>New evaluation</h1>
          <p className="lede">
            The design is built from your answers, so the experiment can answer the
            question it declares.
          </p>
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
          Evaluate a harness change
        </button>
        <button
          aria-selected={mode === "cross_repo"}
          className={mode === "cross_repo" ? "primary" : "secondary"}
          onClick={() => setMode("cross_repo")}
          role="tab"
          type="button"
        >
          Test across repositories
        </button>
      </div>

      <div className="panel">
        <label className="field">
          <span>
            What are you trying to find out?{" "}
            {question.trim() ? null : (
              <em className="error-text small">
                required — the experiment is built around this question
              </em>
            )}
          </span>
          <input
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={
              mode === "cross_repo"
                ? "Does our review skill hold up outside the repository it was written for?"
                : "Does the planning skill reduce steps without losing correctness?"
            }
            value={question}
          />
        </label>
        {question.trim() ? null : (
          <button
            className="secondary"
            onClick={() => setQuestion(suggestedQuestion)}
            type="button"
          >
            Use a suggested question: &ldquo;{suggestedQuestion}&rdquo;
          </button>
        )}
      </div>

      {mode === "harness_evaluation" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>What varies</h2>
          </div>
          <fieldset className="field">
            <legend>Where the cases run (held fixed across both arms)</legend>
            <label className="checkbox">
              <input
                checked={venue === "lab"}
                name="venue"
                onChange={() => setVenue("lab")}
                type="radio"
              />
              <span>
                <strong>This lab</strong> — the harness under test is the lab&apos;s
                own governance, the synced <code>ml-python-base</code> release. This
                is the continuous-improvement loop: change a skill, measure it,
                release, re-measure.
              </span>
            </label>
            <label className="checkbox">
              <input
                checked={venue === "target"}
                name="venue"
                onChange={() => setVenue("target")}
                type="radio"
              />
              <span>
                <strong>An external repository</strong> — the agent works inside
                someone else&apos;s codebase, provisioned from the pinned local
                checkout for each run.
              </span>
            </label>
          </fieldset>
          {venue === "target" ? (
            <label className="field">
              <span>Repository</span>
              <select
                onChange={(event) => setTargetId(event.target.value)}
                value={targetId}
              >
                <option value="">Select a repository</option>
                {targets.map((target) => (
                  <option key={target.target_id} value={target.target_id}>
                    {target.name}
                  </option>
                ))}
              </select>
              {targets.length === 0 ? (
                <span className="muted small">
                  No repositories registered yet — add one under Evidence →
                  Repositories, or keep the evaluation in this lab.
                </span>
              ) : null}
            </label>
          ) : null}
          <label className="field">
            <span>Factor under test</span>
            <select
              onChange={(event) => setFactorKey(event.target.value as FactorKey)}
              value={factorKey}
            >
              {FACTORS.map((item) => (
                <option
                  // Native vs injected compares two governance surfaces, and
                  // inside the lab there is only one.
                  disabled={item.key === "harness_source" && venue === "lab"}
                  key={item.key}
                  value={item.key}
                >
                  {item.label}
                  {item.key === "harness_source" && venue === "lab"
                    ? " — needs an external repository"
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">{factor.blurb}</p>
          {factorKey === "full_harness" ? (
            variantOptions.length === 0 ? (
              <p className="muted small">
                Pick cases below first — the bare and governed prompts come from
                the variants those cases define.
              </p>
            ) : (
              <div className="field-row">
                <label className="field">
                  <span>Bare arm prompt</span>
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
                  <span className="muted small">
                    The plain task prompt — no governance instructions, and every
                    governed skill hidden for this arm.
                  </span>
                  <PromptPreview entries={promptEntriesFor(bareVariant)} variant={bareVariant} />
                </label>
                <label className="field">
                  <span>Full-harness arm prompt</span>
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
                    The governance-loaded variant, with the whole skill catalog
                    ({skills.length}) available.
                  </span>
                  <PromptPreview
                    entries={promptEntriesFor(harnessVariant)}
                    variant={harnessVariant}
                  />
                </label>
              </div>
            )
          ) : null}
          {factorKey === "skills_extra" || factorKey === "skills_disabled" ? (
            <fieldset className="field">
              <legend>
                {factorKey === "skills_extra"
                  ? "Staged skills to add"
                  : "Governed skills to remove"}
              </legend>
              {factorKey === "skills_extra" && stagedSkills.length === 0 ? (
                <p className="muted small">
                  Nothing staged to offer. <code>skills_extra</code> only accepts
                  staged skills — import or draft one under Evidence → Skills
                  first. To measure a skill that is already part of the harness,
                  use <strong>Remove skills</strong> instead: it is already
                  present, so its value is measured by taking it away.
                </p>
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
                  Enforce skill use{" "}
                  <small className="muted">
                    Tell the agent, at the start of every step, to invoke the
                    selected skill(s) and follow them for the whole task. Off =
                    the skill is only available and the agent decides. On = the
                    test of &ldquo;does it help when asked for&rdquo;; Off =
                    &ldquo;does it help unasked&rdquo;.
                  </small>
                </span>
              </label>
              {skillNames.length > 1 ? (
                <p className="muted small">
                  {skillNames.length} skills selected — the treatment is the{" "}
                  <strong>bundle</strong>. A win says the bundle helps; it cannot
                  be attributed to any single member. To rank them, run
                  one-skill-at-a-time experiments afterwards.
                </p>
              ) : null}
            </fieldset>
          ) : null}
          {factorKey === "harness" ? (
            <div className="field-row">
              <label className="field">
                <span>Control harness</span>
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
                <span>Treatment harness</span>
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
            <h2>What varies</h2>
          </div>
          <label className="field">
            <span>Harness (held fixed across every repository)</span>
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
            <legend>Repositories</legend>
            {targets.length === 0 ? (
              <p className="muted">
                No repositories registered yet. Add one under Evidence → Repositories.
              </p>
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
          <p className="callout callout--warning">
            Results stay per repository. A pooled score across different codebases
            would treat them as one condition, which they are not. The first
            repository you pick becomes the <strong>reference</strong> the others are
            read against — a reference, not a baseline: nothing here supports a causal
            claim.
          </p>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <h2>Cases and repetitions</h2>
          {useSuite ? <span className="badge neutral">from the suite</span> : null}
        </div>
        {mode === "harness_evaluation" ? (
          <label className="checkbox">
            <input
              checked={useSuite}
              onChange={(event) => setUseSuite(event.target.checked)}
              type="checkbox"
            />
            <span>
              Use the regression suite — validating a harness release against the
              canonical cases
            </span>
          </label>
        ) : null}
        {useSuite ? (
          <>
            <label className="field">
              <span>Suite</span>
              <input
                onChange={(event) => setSuiteId(event.target.value)}
                value={suiteId}
              />
            </label>
            <p className="muted">
              The suite declares which cases are representative and how often each is
              worth repeating, so neither is chosen here. What comes back is an
              ordinary harness-evaluation experiment: the same design validation, the
              same case matrix, the same <strong>fixed / broken / unchanged</strong>{" "}
              reading. There is no second runner.
            </p>
          </>
        ) : (
        <>
        <fieldset className="field">
          <legend>Cases (every arm runs all of them)</legend>
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
          <span>Repetitions per case, per arm</span>
          <select
            onChange={(event) => setRepetitions(Number(event.target.value))}
            value={repetitions}
          >
            {REPETITION_CHOICES.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          Agents are stochastic. Below five repetitions per arm the lab will describe
          the result as exploratory, whatever the difference looks like.
        </p>
        </>
        )}
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>How it runs</h2>
        </div>
        <p className="muted">
          Shared by every arm — the factor above is the only thing that differs
          between them, so nothing picked here can leak into the comparison as a
          confound.
        </p>
        {mode === "harness_evaluation" && factorKey === "harness" ? (
          <p className="muted small">
            The design varies the harness, so each arm uses its own. Pick a model
            both CLIs can address, or leave the default.
          </p>
        ) : mode === "harness_evaluation" ? (
          <label className="field">
            <span>Harness (held fixed across both arms)</span>
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
                Runs the {harness} CLI with its native auth.{" "}
                {harness === "claude"
                  ? "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host)."
                  : "Needs host mode — run make app-host."}
              </span>
            ) : null}
          </label>
        ) : null}
        <div className="field-row">
          <ModelPicker
            label="Executor model"
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
            <small className="muted">
              Held fixed across every arm — the experiment varies the factor
              above, never the model.
            </small>
          </ModelPicker>
          <ModelPicker
            allowNone
            label="Planner model"
            directErrors={directErrors}
            onChange={setPlannerModel}
            options={modelOptions}
            value={plannerModel}
          />
        </div>
        <div className="field-row">
          {useSuite ? (
            <div className="field">
              <span>Prompt variant</span>
              <p className="muted small">The suite declares its own.</p>
            </div>
          ) : mode === "harness_evaluation" && factorKey === "full_harness" ? (
            <div className="field">
              <span>Prompt variant</span>
              <p className="muted small">
                Set per arm above — bare versus governed is the treatment.
              </p>
            </div>
          ) : (
            <label className="field">
              <span>Prompt variant</span>
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
              <span className="muted small">
                Union of the variants the selected cases define.
              </span>
            </label>
          )}
          <label className="field">
            <span>Step budget</span>
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
            <span className="muted small">
              Bumped to the largest budget any selected case declares.
            </span>
          </label>
        </div>
        <label className="checkbox">
          <input
            checked={dryRun}
            onChange={(event) => setDryRun(event.target.checked)}
            type="checkbox"
          />
          <span>Dry run — build the commands without executing them</span>
        </label>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>The design you are about to create</h2>
          <span className="badge neutral">
            {conditions.length} arm{conditions.length === 1 ? "" : "s"}
            {useSuite ? (
              <> × the suite&apos;s cases and repetitions</>
            ) : (
              <>
                {" "}
                × {caseIds.length} case{caseIds.length === 1 ? "" : "s"} ×{" "}
                {repetitions} = {conditions.length * caseIds.length * repetitions}{" "}
                run{conditions.length * caseIds.length * repetitions === 1 ? "" : "s"}
              </>
            )}
          </span>
        </div>
        <ul className="design-preview">
          {conditions.map((condition) => (
            <li key={condition.label}>
              <span className={`badge ${condition.role === "control" ? "" : "neutral"}`}>
                {condition.role}
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
              <strong>
                Not ready yet — the buttons below unlock when this is resolved:
              </strong>
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
            <strong>{check.ok ? "Ready" : "Blocked"}</strong> — {check.detail}
          </p>
        ) : null}
        <div className="toolbar">
          <button
            className="secondary"
            disabled={checking || isSaving || useSuite || caseIds.length === 0}
            onClick={() => void handleCheck()}
            title={
              useSuite
                ? "The suite's cases are resolved at launch — the same checks run there."
                : caseIds.length === 0
                  ? "Pick at least one case first."
                  : undefined
            }
            type="button"
          >
            {checking ? "Checking…" : "Check configuration"}
          </button>
          <button
            className="primary"
            disabled={blocking.length > 0 || isSaving}
            onClick={() => void submit(true)}
            title={blocking.length > 0 ? blocking.join(" ") : undefined}
            type="button"
          >
            {isSaving ? "Working…" : "Create & launch runs"}
            <IconArrowRight size={15} />
          </button>
          <button
            className="secondary"
            disabled={blocking.length > 0 || isSaving}
            onClick={() => void submit(false)}
            title={blocking.length > 0 ? blocking.join(" ") : undefined}
            type="button"
          >
            Create without launching
          </button>
        </div>
        {createdId ? (
          <p className="muted small">
            The experiment exists —{" "}
            <Link to={`/evaluate/experiments?experiment=${createdId}`}>
              open it
            </Link>{" "}
            to adjust the configuration and launch from there.
          </p>
        ) : null}
      </div>

      <HelpPanel title="Why the form is shaped like this">
        <p>
          An experiment that varies two things at once produces a number nobody can
          attribute. Rather than let you build one and reject it afterwards, the form
          only offers one factor at a time and pins everything else.
        </p>
        <p>
          <strong>Create &amp; launch runs</strong> queues every run of the design at
          once — arms × cases × repetitions — with the model, prompt and budget set
          here, then lands you on the experiment as results fill in. Create without
          launching if you want to review the arms first; the experiment&apos;s own
          page can launch it later.
        </p>
      </HelpPanel>
    </section>
  );
}
