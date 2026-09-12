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
    controlLabel: "Bare — empty workspace, governance masked, skills hidden",
    treatmentLabel: "Full harness"
  },
  {
    key: "governance",
    label: "Governance body",
    blurb:
      "The rulebook alone: CLAUDE.md, the SessionStart hook, the subagents, " +
      "the slash commands, the .github/ governance and the MCP servers — " +
      "against nothing at all. Both arms hide every skill and run the same " +
      "prompt, so a difference is about the governance and not about the " +
      "catalog. Lab venue only.",
    controlLabel: "Bare — an empty workspace, nothing loaded",
    treatmentLabel: "Governance body, skills hidden"
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
    key: "prompt",
    label: "Harness prompt",
    blurb:
      "The preamble alone: the plain task against the task with the harness " +
      "instruction prepended (read .github/, use these skills), with the " +
      "governance body and the whole catalog present on both arms. Rung 3 of " +
      "the ladder — what the wording adds once everything is already there.",
    controlLabel: "Plain prompt",
    treatmentLabel: "Harness prompt"
  },
  {
    key: "activation",
    label: "Activation",
    blurb:
      "Being asked outright. Both arms carry the harness prompt and the whole " +
      "catalog; the treatment also gets the activation block that names every " +
      "skill and requires the agent to invoke it at the start of each step. " +
      "Rung 4 of the ladder — the difference between a harness on disk and a " +
      "harness in use (ADR-0041).",
    controlLabel: "Harness prompt, skills available",
    treatmentLabel: "Harness prompt, skills invoked"
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

// Three named levels, sized for a budget a lab can actually spend: one run
// per arm explores, two is enough for a judgement the page will state, five is
// a validation. The backend's wording ladder matches (statistics.py).
const REPETITION_CHOICES = [
  { value: 1, label: "1 — exploratory" },
  { value: 2, label: "2 — judgement" },
  { value: 5, label: "5 — validation" }
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
      return [
        "Does this harness hold up across repositories, or does it degrade outside the one it was built in?",
        "Do the same cases score the same way in every repository, or is one repository carrying the result?"
      ];
    }
    if (factorKey === "full_harness") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : "the governed skills";
      return [
        "Does the full harness improve outcomes over the bare model on these cases?",
        // The same-prompt design this factor now accepts: one plain prompt, run
        // with the catalog hidden and with it available. Worth offering as a
        // sentence, because it is the question a case written in this UI can
        // actually answer.
        "Running the same prompt with and without the harness, does the governed skill catalog change the outcome?",
        enforceSkills
          ? `When the agent is told to use ${listed}, does the harness beat the bare run on the same prompt?`
          : "Is the harness worth what it costs here — better outcomes for the extra steps and tokens?"
      ];
    }
    if (factorKey === "skills_extra") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : "this staged skill";
      return [
        `Does adding ${listed} improve outcomes?`,
        `Does ${listed} earn a place in the harness, or is the baseline already good enough here?`
      ];
    }
    if (factorKey === "skills_disabled") {
      const listed = skillNames.length > 0 ? skillNames.join(", ") : "this skill";
      return [
        `Does removing ${listed} change outcomes at all?`,
        `If we dropped ${listed} from the harness, would these cases notice?`
      ];
    }
    if (factorKey === "harness") {
      return [
        `Does ${treatmentHarness} beat ${controlHarness} on the same cases and model?`,
        `Do ${controlHarness} and ${treatmentHarness} differ in what they finish, or only in how they get there?`
      ];
    }
    return [
      "Does the injected lab harness improve over the repository's own setup?",
      "For a team that already has its own governance, is our harness an improvement or only a difference?"
    ];
  }, [controlHarness, enforceSkills, factorKey, mode, skillNames, treatmentHarness]);

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
          ? `its ${invoked.length} governed skills`
          : invoked.map((name) => `'${name}'`).join(", ");
      const invokedNote = invoked.length
        ? ` The agent is asked at the start of every step to invoke ${invokedList} and follow them.`
        : "";
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
              ? `Empty workspace, the lab's governance masked, plain prompt ('${bareVariant || "?"}'), every governed skill hidden.`
              : bareVariant && bareVariant === harnessVariant
                ? `Same prompt ('${bareVariant}') with every governed skill hidden.`
                : `Plain prompt ('${bareVariant || "?"}') with every governed skill hidden.`,
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
          description:
            `Governance prompt ('${harnessVariant || "?"}') with the whole skill ` +
            "catalog available." +
            invokedNote,
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
            description: `Plain prompt ('${plain}'), governance body and every governed skill available.`,
            overrides: { ...base, prompt_variant: plain, skills_disabled: [] }
          },
          {
            label: "harness-prompt",
            role: "treatment" as const,
            description: `Harness prompt ('${governed}'), same governance body and catalog.`,
            overrides: { ...base, prompt_variant: governed, skills_disabled: [] }
          }
        ];
      }
      return [
        {
          label: "harness-prompt",
          role: "control" as const,
          description: `Harness prompt ('${governed}') with every governed skill available, not asked for.`,
          overrides: { ...base, prompt_variant: governed, skills_disabled: [] }
        },
        {
          label: "activated",
          role: "treatment" as const,
          description:
            `Harness prompt ('${governed}') plus the activation block naming its ` +
            `${catalog.length} governed skills at the start of every step.`,
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
          description:
            "An empty workspace with the lab's governance masked in the " +
            "container: no CLAUDE.md, no hooks, no subagents, no .github/, " +
            "no MCP servers, and no skills.",
          overrides: { ...base, harness_source: "bare", skills_disabled: hidden }
        },
        {
          label: "governance-only",
          role: "treatment" as const,
          description:
            "The lab's governance as the agent normally meets it — CLAUDE.md, " +
            "the SessionStart hook, the subagents, the slash commands and " +
            ".github/ — with the same skills hidden on both sides.",
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
      problems.push(
        "The selected cases share no prompt variant, so any choice would hand " +
          "some of them an empty prompt. Keep one prompt family: " +
          `${variantFamilies.join("; ")}.`
      );
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
          "inside the lab there is no other governance to compare against. " +
          "To compare the lab's own governance against nothing, use " +
          "'Governance body'."
      );
    }
    if (
      mode === "harness_evaluation" &&
      venue === "target" &&
      (factorKey === "governance" ||
        factorKey === "prompt" ||
        factorKey === "activation")
    ) {
      problems.push(
        `'${factor.label}' is a rung of the lab's own harness ladder, which ` +
          "only the lab venue can stage. Against an external repository use " +
          "'Governance source'."
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
        problems.push(
          "Both arms use the same prompt variant and there are no governed " +
            "skills to hide, so the two arms would be identical."
        );
      }
      if (enforceSkills && skillNames.length === 0) {
        problems.push(
          "Choose the governed skills to name in the activation block, or " +
            "clear 'Ask the agent to use the harness'."
        );
      }
    }
    return problems;
  }, [
    caseIds,
    crossRepoIds,
    declaredVariants,
    factor,
    factorKey,
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
      const forced =
        variantOptions.length < 2
          ? "The selected cases define a single prompt, so both arms run it. "
          : "";
      notes.push(
        `${forced}Both arms run '${bareVariant}', so what moves is ` +
          (venue === "lab"
            ? "the governance body plus the skill catalog, with the prompt " +
              "held fixed. That is a sound comparison — it is the harness " +
              "minus its prompt — but read the result as a claim about what " +
              "the agent could load, not about the wording it was given."
            : "the skill catalog alone rather than the harness as a unit. " +
              "That is a sound ablation — it is what the 'Remove skills' " +
              "factor states directly — but read the result as a claim about " +
              "the skills, not about the whole harness.")
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
      notes.push(
        "Inside this lab both arms still carry the governance body of " +
          "CLAUDE.md, the SessionStart hook and the .github/ governance — only " +
          "the generated skills catalog differs. The control arm is not a bare " +
          "model. For a bare-versus-governed comparison use 'Governance body' " +
          "(the rulebook against nothing) or 'Full harness' (everything " +
          "against nothing)."
      );
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
        factorKey === "skills_disabled"
          ? "removed from the treatment arm"
          : "staged in the treatment arm";
      let note =
        `${skillNames.length} skills are ${moved} together, so a difference ` +
        "belongs to the bundle and cannot be attributed to any one of them. " +
        "To evaluate one skill, select that skill alone.";
      if (leftOut.length > 0 && leftOut.length <= 3) {
        note +=
          ` ${leftOut.map((name) => `'${name}'`).join(", ")} ` +
          `${leftOut.length === 1 ? "is" : "are"} left out: ` +
          (factorKey === "skills_disabled"
            ? "available to both arms and tested by neither."
            : "absent from both arms and tested by neither.") +
          " If that is the skill you mean to evaluate, the selection is inverted.";
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
        `The arms differ in prompt ('${bareVariant}' vs '${harnessVariant}')` +
          (venue === "lab" ? ", in governance body" : "") +
          " and in skill catalog. That is the harness as a unit, and a " +
          "difference cannot be attributed to any one part. For a claim about " +
          "the skills alone use 'Add skills' or 'Remove skills'; for the " +
          "rulebook alone use 'Governance body'; to run one prompt on both " +
          "arms, set them equal above."
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
          `${dropped.map((variant) => `'${variant}'`).join(", ")} ` +
            `${dropped.length === 1 ? "is" : "are"} declared by some of the ` +
            "selected cases but not all of them, so the prompt pickers offer " +
            `only ${variantOptions.map((variant) => `'${variant}'`).join(", ")}. ` +
            "An experiment runs one design across every case it selects."
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
      notes.push(
        `Both arms run '${promptVariant}', which directs the agent through ` +
          "the lab's governance — the bare arm cannot read any of it. Pick the " +
          "plain variant so the comparison is about what each arm had, not " +
          "about one arm being told to use what it did not."
      );
    }
    return notes;
  }, [
    bareVariant,
    declaredVariants,
    factorKey,
    harnessVariant,
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
          <div className="button-row">
            {suggestedQuestions.map((suggestion, index) => (
              <button
                className="secondary"
                key={suggestion}
                onClick={() => setQuestion(suggestion)}
                type="button"
              >
                {index === 0 ? "Use a suggested question: " : ""}
                &ldquo;{suggestion}&rdquo;
              </button>
            ))}
          </div>
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
                    {item.label}
                    {needsTarget ? " — needs an external repository" : ""}
                    {needsLab ? " — lab venue only" : ""}
                  </option>
                );
              })}
            </select>
          </label>
          <p className="muted">{factor.blurb}</p>
          {factorKey === "full_harness" ||
          factorKey === "prompt" ||
          factorKey === "activation" ? (
            variantOptions.length === 0 ? (
              <p className="muted small">
                {caseIds.length === 0 || declaredVariants.length === 0
                  ? "Pick cases below first — the bare and governed prompts come from the variants those cases define."
                  : "The selected cases share no prompt variant, so there is no pair that runs on all of them. Narrow the selection to one prompt family."}
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
          {factorKey === "full_harness" && skills.length > 0 ? (
            <fieldset className="field">
              <legend>Activation on the full-harness arm</legend>
              <label className="checkbox">
                <input
                  checked={enforceSkills}
                  onChange={(event) => setEnforceSkills(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  Ask the agent to use the harness{" "}
                  <small className="muted">
                    <strong>On by default, with the whole catalog selected.</strong>{" "}
                    A prompt written in this UI says nothing about governance, so
                    the full-harness arm may never open a skill and the pair
                    measures only what sat on disk. This names the skills below in
                    an activation block at the start of every step of that arm;
                    the bare arm never gets it — it cannot see those skills at
                    all. Untick individual skills to narrow the block, or turn the
                    whole thing off only to ask the different question: whether
                    the agent reaches for them unprompted.
                  </small>
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
                <p className="muted small">
                  {skillNames.length} skills named — they are asked for together,
                  so a win belongs to the harness as a whole and not to any one
                  of them.
                </p>
              ) : null}
            </fieldset>
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
          Agents are stochastic. One repetition per arm is exploratory and gets no
          directional headline; two is a judgement; five is a validation.
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
                Variants every selected case defines — one case short of a
                variant is enough for the launch to be refused.
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
