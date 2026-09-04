import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
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
  { key: "subject", label: "Bring your tool" },
  { key: "success", label: "Declare what success is" },
  { key: "arms", label: "Choose the arms" },
  { key: "watch", label: "Watch it, and go in" },
  { key: "read", label: "Read the result" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

type Arm = { harness: string; executorModel: string };

const MAX_STEP_OPTIONS = [5, 10, 20, 40];
const REPETITION_OPTIONS = [1, 3, 5, 10];

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
    setNotice(`Case '${c.case_id}' selected.`);
  };

  const advanceWithExistingCase = () => {
    if (!caseId) return;
    setNotice(`Using existing case '${caseId}'.`);
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
      .catch(() => setError("Unable to load staged skills."));
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
      setError(workError instanceof Error ? workError.message : "Something failed");
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
      setNotice(`Staged '${created.name}'. It will be added to every arm's run.`);
    });

  const importSkill = () =>
    run("import", async () => {
      const preview = await api.previewStagedSkillImport(importUrl, "", "");
      if (!preview.preview) {
        throw new Error(
          `That source holds several skills. Pick one on the Skills page: ${preview.choices
            .map((choice) => choice.name)
            .join(", ")}`
        );
      }
      const imported = await api.importStagedSkill(importUrl, "", preview.name, false);
      setStagedSkills((current) => [...current, imported]);
      setSkillsExtra((current) => [...current, imported.name]);
      setImportUrl("");
      setNotice(`Imported '${imported.name}' from ${preview.source_url}.`);
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
        const retried = draft.attempts > 1 ? " (after one corrective retry)" : "";
        if (kept === 0) {
          setAiError(
            "The draft has no runnable validation command" +
              retried +
              ". Write one below, or describe the check you want and generate again."
          );
        } else {
          setAiNotice(
            `Draft generated with ${kept} validation command(s)${retried}. ` +
              "Review and edit the fields below before saving."
          );
        }
      } catch (err) {
        setAiError(err instanceof Error ? err.message : "Unable to generate the case draft.");
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
        setNotice(`Case '${updated.case_id}' updated.`);
        setStep("arms");
        return;
      }
      for (let attempt = 0; attempt < 5; attempt += 1) {
        const suffix = attempt === 0 ? "" : `-${attempt + 1}`;
        const candidate = `${slugify(subject).slice(0, 48) || "subject"}${suffix}`;
        try {
          const created = await api.createCase(payload(candidate));
          setCaseId(created.case_id);
          setNotice(`Case '${created.case_id}' created.`);
          setStep("arms");
          return;
        } catch (createError) {
          if (!isDuplicateId(createError)) {
            throw createError;
          }
        }
      }
      throw new Error("Unable to find a free case id for this subject.");
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
          setNotice(
            `Design '${created.experiment_id}' created. Nothing has run yet — read ` +
              `the notes below, then launch.`
          );
          return;
        } catch (createError) {
          if (!isDuplicateId(createError)) {
            throw createError;
          }
          lastError = createError;
        }
      }
      throw lastError instanceof Error
        ? lastError
        : new Error("Unable to find a free experiment id.");
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
      setNotice(`Queued ${result.count} run(s).`);
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
      problems.subject.push(
        "Choose at least one skill that drives your tool — or say it is already " +
          "part of the harness."
      );
    }
    if (caseMode === "existing") {
      if (!caseId) {
        problems.success.push("Pick an existing case from the list, or create a new one.");
      }
    } else {
      if (!subject.trim()) {
        problems.success.push("Name the subject. The result sentence has to say what passed.");
      }
      if (!prompt.trim()) {
        problems.success.push("Write the instruction the agent is given.");
      }
      if (executable.length === 0) {
        problems.success.push(
          "Add at least one validation command that exits non-zero when the " +
            "subject fails. Without one the runs execute and report nothing."
        );
      }
    }
    if (arms.length === 0) {
      problems.arms.push("Declare at least one arm.");
    }
    if (new Set(arms.map(armLabel)).size !== arms.length) {
      problems.arms.push("Two arms are identical — each arm must be its own configuration.");
    }
    if (arms.some((arm) => !arm.executorModel.trim())) {
      problems.arms.push(
        "Pick a model for every arm. A headless run needs one, and the arm's " +
          "label names it."
      );
    }
    return problems;
  }, [alreadyGoverned, arms, caseId, caseMode, executable.length, prompt, skillsExtra.length, subject]);


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
          <h1>Validate your tool</h1>
          <p className="lede">
            You built something — an agent behind an API, an MCP server, a service
            wrapper — and a skill that drives it. This measures how reliably it does
            its job under each harness and model. It says nothing about the harness:
            nothing here is ablated.
          </p>
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
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner message={error} /> : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}
      {launched && step !== "watch" && step !== "read" ? (
        <p className="callout">
          The runs are queued, so this is a record of what ran, not a form. To test
          something different, <Link to="/evaluate/subject" reloadDocument>start a new validation</Link>.
        </p>
      ) : null}

      {step === "subject" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>Which skill drives your tool?</h2>
              <Link className="button-link" to="/evidence/skills">
                Skills workbench
              </Link>
            </div>
            <p className="muted">
              The skill is what the agent reads to reach your tool. Staged skills are
              added via extra skills, while harness skills are governed tools. Select
              one or multiple skills under test.
            </p>

            <div className="filter-stack">
              <div className="tab-bar">
                <button
                  className={`tab ${filterTab === "all" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("all")}
                  type="button"
                >
                  All ({allSkills.length})
                </button>
                <button
                  className={`tab ${filterTab === "staged" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("staged")}
                  type="button"
                >
                  Staged ({stagedSkills.length})
                </button>
                <button
                  className={`tab ${filterTab === "harness" ? "tab--active" : ""}`}
                  onClick={() => setFilterTab("harness")}
                  type="button"
                >
                  Harness ({governedSkills.length})
                </button>
              </div>

              <input
                aria-label="Filter skills"
                onChange={(event) => setSearchSkill(event.target.value)}
                placeholder="Filter skills by name or description…"
                type="search"
                value={searchSkill}
              />
            </div>

            {visibleSkills.length === 0 ? (
              <EmptyState
                description="No matching skills found. Adjust your search or filter tab."
                title="No skills found."
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
                  Ask the agent to use the selected skill(s) at the start of every
                  step.{" "}
                  <small className="muted">
                    On by default: a skill that is only available is invoked only
                    when the case&rsquo;s instruction or the skill&rsquo;s own
                    triggers call for it. Turn off to measure whether the agent
                    reaches for it unasked.
                  </small>
                </span>
              </label>

              <label className="checkbox">
                <input
                  checked={isolateGoverned}
                  onChange={(event) => setIsolateGoverned(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  Isolate the run: disable every harness skill that is not selected above.
                </span>
              </label>

              <label className="checkbox">
                <input
                  checked={alreadyGoverned}
                  onChange={(event) => setAlreadyGoverned(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  My skill is already part of the governed harness — every run loads it
                  already.
                </span>
              </label>
            </div>
          </div>


          <div className="panel">
            <div className="panel-head">
              <h2>Write one here</h2>
            </div>
            <div className="form-grid">
              <label>
                <span>Skill name</span>
                <input
                  onChange={(event) => setNewSkillName(event.target.value)}
                  placeholder="notion-export"
                  value={newSkillName}
                />
              </label>
              <label className="full">
                <span>SKILL.md</span>
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
                {busy === "author" ? "Staging…" : "Stage this skill"}
              </button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Or import one</h2>
            </div>
            <div className="form-grid">
              <label className="full">
                <span>Repository or archive URL</span>
                <input
                  onChange={(event) => setImportUrl(event.target.value)}
                  placeholder="https://github.com/owner/repo"
                  value={importUrl}
                />
              </label>
            </div>
            <p className="muted">
              Fetched, validated and staged — never promoted into the harness by this
              step.
            </p>
            <div className="panel-actions">
              <button
                className="secondary"
                disabled={busy === "import" || !importUrl.trim()}
                onClick={() => void importSkill()}
                type="button"
              >
                {busy === "import" ? "Importing…" : "Import"}
              </button>
            </div>
          </div>
        </>
      ) : null}

      {step === "success" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>What is being tested, and what counts as done?</h2>
            {existingCases.length > 0 ? (
              <div className="tab-bar tab-bar--inline">
                <button
                  className={`tab ${caseMode === "existing" ? "tab--active" : ""}`}
                  onClick={() => setCaseMode("existing")}
                  type="button"
                >
                  Use an existing case ({existingCases.length})
                </button>
                <button
                  className={`tab ${caseMode === "new" ? "tab--active" : ""}`}
                  onClick={() => setCaseMode("new")}
                  type="button"
                >
                  Create a new case
                </button>
              </div>
            ) : null}
          </div>

          {caseMode === "existing" ? (
            <div className="filter-stack">
              <p className="muted">
                Pick a case the lab already holds — SWE-bench, a benchmark, or a saved
                case — to validate your tool or skill against a known standard.
              </p>

              <input
                aria-label="Filter cases"
                onChange={(event) => setCaseSearch(event.target.value)}
                placeholder="Search by id, name or tag (e.g. swe-bench, python, bugfix)…"
                type="search"
                value={caseSearch}
              />

              {filteredCases.length === 0 ? (
                <EmptyState
                  description="No existing case matches the search."
                  title="No cases found."
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
                            <code>{c.case_id}</code> — {c.description || "No description"}
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
                    <strong>
                      Selected case: <code>{caseId}</code>
                    </strong>
                    <span className="muted small">
                      {subject} · Max steps: {maxSteps} · Variant: <code>{promptVariant}</code>
                    </span>
                  </div>
                  <button
                    className="primary"
                    disabled={launched}
                    onClick={advanceWithExistingCase}
                    type="button"
                  >
                    Use this case and continue <IconArrowRight size={14} />
                  </button>
                </div>
              ) : null}

              {blocking.success.length > 0 ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <div>
                    <strong>Not ready yet:</strong>
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
                    <IconSparkles size={16} /> Draft it with AI (optional)
                  </strong>
                  {skillsExtra.length > 0 ? (
                    <span className="muted small">Context: {skillsExtra.join(", ")}</span>
                  ) : (
                    <span className="muted small">No skills selected in step 1</span>
                  )}
                </div>

                <label>
                  <span>Describe what you want to test, in plain language</span>
                  <textarea
                    onChange={(event) => setAiDescription(event.target.value)}
                    placeholder="e.g. Check that the agent uses notion-export to save page 123 to notes.txt, and that the file exists and is not empty."
                    rows={2}
                    value={aiDescription}
                  />
                </label>

                <div className="assist-card-row">
                  <label>
                    <span>Model</span>
                    <select
                      onChange={(event) => setAiModel(event.target.value)}
                      value={aiModel || gatewayDefault}
                    >
                      {modelOptions.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label || opt.id} {opt.id === gatewayDefault ? "(default)" : ""}
                        </option>
                      ))}
                      {!modelOptions.some((opt) => opt.id === gatewayDefault) && gatewayDefault ? (
                        <option value={gatewayDefault}>{gatewayDefault} (default)</option>
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
                      <Spinner label="Drafting…" />
                    ) : (
                      <>
                        <IconSparkles size={14} /> Generate the case
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
                      <strong>
                        Left out of the draft — the lab would not run these:
                      </strong>
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
                <label className="full">
                  <span>Subject</span>
                  <input
                    onChange={(event) => setSubject(event.target.value)}
                    placeholder="notion-export skill + local export API"
                    value={subject}
                  />
                </label>
                <label className="full">
                  <span>The instruction the agent gets</span>
                  <textarea
                    onChange={(event) => setPrompt(event.target.value)}
                    placeholder="Read the linked page and save its contents to notes.txt in the workspace."
                    rows={4}
                    value={prompt}
                  />
                </label>
                <label className="full">
                  <span>Validation commands — one per line</span>
                  <textarea
                    onChange={(event) => setCommandText(event.target.value)}
                    placeholder={"test -s {workspace}/notes.txt\npython3 check_export.py {workspace}"}
                    rows={5}
                    value={commandText}
                  />
                </label>
                <label>
                  <span>Max steps</span>
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
              <p className="muted">
                The command is the whole measurement: it runs after the agent stops, and
                its exit code is the pass. A command that cannot fail — a smoke check that
                only proves the thing started — produces 100% and measures nothing.
              </p>
              {rejected.length > 0 ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <div>
                    <strong>Not executable, so these would be dropped in silence:</strong>
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
                    <strong>Not ready yet:</strong>
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
                  {busy === "case" ? "Saving…" : caseId ? "Update the case" : "Create the case"}{" "}
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
            <h2>Where should it run?</h2>
            <span className="muted">One arm per harness/model — no baseline</span>
          </div>
          <p className="muted">
            Every arm is a peer. There is no control here, because nothing is being
            compared against a version of the harness with your tool removed — each arm
            gets its own pass rate and is read on its own.
          </p>
          <ul className="pick-list arm-list">
            {arms.map((arm, index) => (
              <li className="panel subtle" key={`${arm.harness}-${index}`}>
                <div className="form-grid">
                  <label>
                    <span>Harness</span>
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
                        Runs the {arm.harness} CLI with its native auth.{" "}
                        {arm.harness === "claude"
                          ? "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host)."
                          : "Needs host mode — run make app-host."}
                      </span>
                    ) : null}
                  </label>
                  <div className="full">
                    <ModelPicker
                      label="Executor model"
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
                      <IconX size={14} /> Remove
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
              <IconPlus size={14} /> Add an arm
            </button>
          </div>
          <div className="form-grid">
            <label>
              <span>Repetitions per arm</span>
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
            {arms.length} arm(s) x {repetitions} repetition(s) = {arms.length * repetitions}{" "}
            run(s). A single run of an agent is an anecdote; the repetitions are what
            make "most of the time" a rate.
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
                  ? "Queueing…"
                  : `Launch ${arms.length * repetitions} run(s)`}
              </button>
            ) : (
              <button
                className="primary"
                disabled={busy === "design" || blocking.arms.length > 0}
                onClick={() => void createDesign()}
                type="button"
              >
                {busy === "design" ? "Creating…" : "Create the design"}
              </button>
            )}
          </div>
        </div>
      ) : null}

      {step === "watch" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>The runs</h2>
            <Link className="button-link" to={`/evaluate/experiments?experiment=${experimentId}`}>
              Open the experiment
            </Link>
          </div>
          <p className="muted">
            The pass column is the validation command's verdict, computed for you. Open
            a run's sandbox to see what the agent actually did — that is the part no
            score can carry, and it is why a scored run is still worth looking at.
          </p>
          {runs.length === 0 ? (
            <Spinner label="Waiting for the runs to appear" />
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Arm</th>
                  <th>State</th>
                  <th>Passed</th>
                  <th>Your judgement</th>
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
                          ? "pass"
                          : "fail"}
                    </td>
                    <td>
                      <Link to={`/advanced/runs/${item.run_id}?tab=terminal`}>
                        Open the sandbox
                      </Link>
                      {item.score === null || item.score === undefined
                        ? null
                        : ` · scored ${item.score}/10`}
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
              {busy === "summary" ? "Reading…" : "Read the result"}
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
                  <p className="verdict-kicker">Result</p>
                  <h2>{summary.verdict.headline}</h2>
                  <p className="muted">{summary.verdict.claim}</p>
                </div>
                <span className="badge neutral verdict-strength">
                  {summary.verdict.strength}
                </span>
              </div>
              <p className="verdict-evidence muted">Evidence: {summary.verdict.evidence}</p>
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
              <h2>One rate per arm</h2>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Arm</th>
                  <th>Passed</th>
                  <th>Scored runs</th>
                  <th>Cost (mean)</th>
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
            <p className="muted">
              These are not averaged together. A combined number would describe a
              configuration nobody runs.
            </p>
          </div>

          {summary.case_outcomes ? (
            <div className="panel">
              <div className="panel-head">
                <h2>Per case</h2>
                <span className="muted">Passed of scored, per arm</span>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Case</th>
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
                              : `${cell.successes} of ${cell.scored_runs}`}
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
              Open the full experiment
            </button>
            <button className="ghost" onClick={() => setStep("watch")} type="button">
              Back to the runs
            </button>
          </div>
        </>
      ) : null}

      <HelpPanel docHref="docs/guide-skill-validation.md" title="What this mode can and cannot say">
        <p>
          It reports how often your subject completed its cases under each harness and
          model. It cannot tell you whether the harness helped: no arm here has the
          harness removed, so there is nothing to attribute a difference to.
        </p>
        <p>
          To ask that question instead, run a{" "}
          <Link to="/evaluate/new">harness evaluation</Link>, which ablates a skill and
          compares the arm with it against the arm without.
        </p>
      </HelpPanel>
    </section>
  );
}
