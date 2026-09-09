import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { caseOptionLabel } from "../caseLabels";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { ModelPicker } from "../components/ModelPicker";
import {
  HARNESS_NATIVE_MODELS,
  HARNESS_OPTIONS,
  HOST_HARNESSES
} from "../harnesses";
import { RunTable } from "../components/RunTable";
import type {
  CaseRecord,
  HarnessSkill,
  ModelOption,
  RunBatchPayload,
  RunBatchResult,
  RunConfigPayload,
  RunCreatePayload,
  RunRecord,
  HarnessSource,
  RunValidationResult,
  StagedSkillRecord,
  TargetRecord,
  TargetResolveResult
} from "../types";

type RunsProps = {
  cases: CaseRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onCloneRun: (runId: string) => Promise<RunRecord>;
  onCreateRun: (payload: RunCreatePayload) => Promise<RunRecord>;
  onCreateRunBatch: (payload: RunBatchPayload) => Promise<RunBatchResult>;
  onDeleteRun: (runId: string, force?: boolean) => Promise<void>;
  runs: RunRecord[];
};

/** Add or remove `value` in a Set, returning a new Set (never mutating state). */
function toggleInSet<T extends string>(current: Set<T>, value: T): Set<T> {
  const next = new Set(current);
  if (next.has(value)) {
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}


const FALLBACK_EXECUTOR_MODEL = "gateway/lmstudio-qwen3.6-35b-build";
// Offered budgets. The selected case's own max_steps is merged in, so a case
// authored with 1 or 4 steps is selectable rather than silently rounded up.
const MAX_STEPS_OPTIONS = [3, 6, 10] as const;
const DEFAULT_MAX_STEPS = 10;

export function Runs({
  cases,
  onCancelRun,
  onCloneRun,
  onCreateRun,
  onCreateRunBatch,
  onDeleteRun,
  runs
}: RunsProps) {
  const navigate = useNavigate();
  const [runId, setRunId] = useState("");
  const [label, setLabel] = useState("");
  const [caseId, setCaseId] = useState(cases[0]?.case_id ?? "tetris-v1");
  const [promptVariant, setPromptVariant] = useState("sdlc");
  const [harness, setHarness] = useState("opencode");
  const [plannerModel, setPlannerModel] = useState("");
  const [executorModel, setExecutorModel] = useState(FALLBACK_EXECUTOR_MODEL);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelSource, setModelSource] = useState<string | null>(null);
  // Why each direct host has no models, keyed by provider id. One may be up
  // while the other is down, so this is per provider rather than one flag.
  const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
  // Remember the gateway default so switching back to opencode restores it.
  const [gatewayDefault, setGatewayDefault] = useState(FALLBACK_EXECUTOR_MODEL);
  const scoreMode = "manual";
  const [maxSteps, setMaxSteps] = useState(DEFAULT_MAX_STEPS);
  const [resume, setResume] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [validation, setValidation] = useState<RunValidationResult | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [caseRunIds, setCaseRunIds] = useState<string[]>([]);
  const [harnessSkills, setHarnessSkills] = useState<HarnessSkill[]>([]);
  const [disabledSkills, setDisabledSkills] = useState<Set<string>>(new Set());
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [extraSkills, setExtraSkills] = useState<Set<string>>(new Set());
  // Off = available only; on = the agent is told to invoke them every step.
  const [invokeExtra, setInvokeExtra] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [reloadingModels, setReloadingModels] = useState(false);
  // Target repository (ADR-0029). Empty = the run works in the lab's own
  // workspace, which is what every run did before targets existed.
  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [targetRepoId, setTargetRepoId] = useState("");
  const [targetRef, setTargetRef] = useState("");
  const [targetPin, setTargetPin] = useState<TargetResolveResult | string | null>(
    null
  );
  const [checkingPin, setCheckingPin] = useState(false);
  const [batchTargets, setBatchTargets] = useState<Set<string>>(new Set());
  // The harness condition (ADR-0031). Only meaningful with a target selected.
  const [harnessSource, setHarnessSource] = useState<HarnessSource>("lab_root");
  const [batchHarnessSources, setBatchHarnessSources] = useState<Set<HarnessSource>>(
    new Set()
  );

  // --- Batch mode ----------------------------------------------------------
  // Off by default, and while off every control above behaves exactly as it did
  // before batching existed. Turning it on promotes four of the single selects
  // into additive sets seeded from the current selection, so the batch always
  // starts as "what I have now" and is widened from there.
  const [batchMode, setBatchMode] = useState(false);
  const [batchCases, setBatchCases] = useState<Set<string>>(new Set());
  const [batchPrompts, setBatchPrompts] = useState<Set<string>>(new Set());
  const [batchHarnesses, setBatchHarnesses] = useState<Set<string>>(new Set());
  const [batchExecutors, setBatchExecutors] = useState<Set<string>>(new Set());
  const [repetitions, setRepetitions] = useState(1);
  const [batchResult, setBatchResult] = useState<RunBatchResult | null>(null);
  // Read from the server rather than mirrored as a constant here: a client-side
  // copy of the cap would silently disagree with the value that actually
  // enforces it. Null until the fetch lands (or if it fails) — the button stays
  // enabled and the server remains the authority either way.
  const [maxBatchRuns, setMaxBatchRuns] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    api
      .readRuntimeFacts()
      .then((facts) => {
        if (active) {
          setMaxBatchRuns(facts.max_batch_runs);
        }
      })
      .catch(() => {
        // Offline / older API: leave the cap unknown and let the server decide.
      });
    return () => {
      active = false;
    };
  }, []);

  const enterBatchMode = () => {
    setBatchCases(new Set([caseId]));
    setBatchPrompts(new Set([promptVariant]));
    setBatchHarnesses(new Set([harness]));
    setBatchExecutors(new Set(executorModel ? [executorModel] : []));
    setRepetitions(1);
    setBatchMode(true);
  };

  /**
   * Re-read the model menu, optionally dropping the server-side caches.
   *
   * The gateway list and the direct LM Studio list live behind separate caches
   * with different lifetimes, so a model loaded a moment ago can be missing
   * from one half of the menu. `refresh` clears both — that is the whole job of
   * the Reload button.
   */
  const loadModels = (refresh = false) => {
    setReloadingModels(refresh);
    return api
      .listModels(refresh)
      .then((catalog) => {
        setModelOptions(catalog.models);
        setModelSource(catalog.source);
        setDirectErrors(catalog.direct_errors ?? {});
        return catalog;
      })
      .finally(() => setReloadingModels(false));
  };

  useEffect(() => {
    let active = true;
    api
      .listHarnessSkills()
      .then((skills) => {
        if (active) {
          setHarnessSkills(skills);
        }
      })
      .catch(() => {
        // No catalog (API offline) — the section stays hidden.
      });
    api
      .listStagedSkills()
      .then((skills) => {
        if (active) {
          setStagedSkills(skills.filter((skill) => skill.status !== "archived"));
        }
      })
      .catch(() => {
        // No staged skills endpoint / API offline — group stays hidden.
      });
    api
      .listTargets()
      .then((rows) => {
        if (active) {
          setTargets(rows);
        }
      })
      .catch(() => {
        // No targets endpoint / API offline — the selector stays hidden and
        // the form behaves exactly as it did before targets existed.
      });
    loadModels()
      .then((catalog) => {
        if (!active) {
          return;
        }
        if (catalog.default_executor) {
          setGatewayDefault(catalog.default_executor);
          // Adopt the gateway's default only while the form is untouched and the
          // harness uses gateway models; never clobber a value the user picked.
          if (!HARNESS_NATIVE_MODELS[harness]) {
            setExecutorModel((current) =>
              current === FALLBACK_EXECUTOR_MODEL ? catalog.default_executor : current
            );
          }
        }
      })
      .catch(() => {
        // Gateway/API offline — the selects fall back to the current value.
      });
    return () => {
      active = false;
    };
  }, []);

  // Switching harness swaps the executor menu (native CLI models vs gateway), so
  // reset the executor to that harness's default to avoid a stale/invalid pick.
  const handleHarnessChange = (next: string) => {
    setHarness(next);
    const native = HARNESS_NATIVE_MODELS[next];
    setExecutorModel(native ? native[0].id : gatewayDefault);
  };

  const toggleExtraSkill = (name: string) => {
    setExtraSkills((current) => {
      const next = new Set(current);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const toggleSkill = (name: string) => {
    setDisabledSkills((current) => {
      const next = new Set(current);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const selectedCase = useMemo(
    () => cases.find((item) => item.case_id === caseId) ?? cases[0] ?? null,
    [caseId, cases]
  );

  // The case says how many steps its task takes; the form used to ignore it and
  // queue every run at 10, so a one-step case advertised a ten-step budget it
  // was never going to use.
  useEffect(() => {
    setMaxSteps(selectedCase?.max_steps ?? DEFAULT_MAX_STEPS);
  }, [selectedCase?.case_id, selectedCase?.max_steps]);

  const maxStepsOptions = useMemo(
    () => Array.from(new Set([...MAX_STEPS_OPTIONS, maxSteps])).sort((a, b) => a - b),
    [maxSteps]
  );

  const promptVariantNames = Object.keys(selectedCase?.prompt_variants ?? { default: "" });

  /**
   * Prompt variants offered in batch mode: the union across the selected cases.
   *
   * Union rather than intersection because cases legitimately define different
   * variant sets, and an intersection is empty as soon as one case is unusual —
   * which would silently make the whole axis unselectable. The server pairs
   * each case only with the variants it defines, so the run count has to be
   * summed per case rather than multiplied by the size of this axis.
   */
  const batchPromptNames = useMemo(() => {
    const names = new Set<string>();
    for (const item of cases) {
      if (!batchCases.has(item.case_id)) {
        continue;
      }
      for (const variant of Object.keys(item.prompt_variants ?? {})) {
        names.add(variant);
      }
    }
    return [...names].sort();
  }, [batchCases, cases]);
  useEffect(() => {
    if (promptVariantNames.length === 0) {
      return;
    }
    if (promptVariantNames.includes(promptVariant)) {
      return;
    }
    const preferred =
      caseId === "tetris-v1" && promptVariantNames.includes("sdlc")
        ? "sdlc"
        : promptVariantNames[0];
    setPromptVariant(preferred);
  }, [caseId, promptVariant, promptVariantNames]);

  useEffect(() => {
    let active = true;
    api
      .listCaseRunIds(caseId)
      .then((payload) => {
        if (active) {
          setCaseRunIds(payload.run_ids);
        }
      })
      .catch(() => {
        if (active) {
          setCaseRunIds([]);
        }
      });

    return () => {
      active = false;
    };
  }, [caseId]);

  const effectiveRunLabel = label.trim() || runId.trim();

  const commandPreview = useMemo(() => {
    const parts = [
      "make bench-run",
      `CASE=${caseId}`,
      `RUN=${effectiveRunLabel || `<auto: ${caseId}-K-NNN>`}`,
      `PROMPT=${promptVariant}`,
      `EXECUTOR_MODEL=${executorModel}`,
      `MAX_STEPS=${maxSteps}`,
      "SCORE_MODE=manual"
    ];
    if (plannerModel) {
      parts.push(`PLANNER_MODEL=${plannerModel}`);
    }
    if (dryRun) {
      parts.push("DRY_RUN=1");
    }
    if (resume) {
      parts.push("RESUME=1");
    }
    return parts.join(" \\\n  ");
  }, [caseId, dryRun, effectiveRunLabel, executorModel, maxSteps, plannerModel, promptVariant, resume]);

  const applySdlcQwenPreset = () => {
    setCaseId("tetris-v1");
    setPromptVariant("sdlc");
    setHarness("opencode");
    setPlannerModel("");
    setExecutorModel(FALLBACK_EXECUTOR_MODEL);
    setMaxSteps(10);
  };

  // A verdict only describes the configuration that produced it, so any edit
  // voids it rather than leaving a stale green tick above the Create button.
  useEffect(() => {
    setValidation(null);
  }, [
    caseId,
    promptVariant,
    harness,
    plannerModel,
    executorModel,
    maxSteps,
    resume,
    dryRun,
    disabledSkills,
    extraSkills,
    invokeExtra,
    targetRepoId,
    targetRef,
    harnessSource
  ]);

  // The pin describes one (repository, ref) pair; changing either makes the
  // displayed commit describe a configuration the form no longer holds.
  useEffect(() => {
    setTargetPin(null);
  }, [targetRepoId, targetRef]);

  const buildConfig = (): RunConfigPayload => ({
    case_id: caseId,
    prompt_variant: promptVariant,
    harness,
    planner_model: plannerModel || null,
    executor_model: executorModel || null,
    score_mode: scoreMode,
    max_steps: maxSteps,
    resume,
    dry_run: dryRun,
    skills_disabled: [...disabledSkills].sort(),
    skills_extra: [...extraSkills].sort(),
    skills_invoke: invokeExtra ? [...extraSkills].sort() : [],
    target_repo_id: targetRepoId,
    target_ref: targetRef.trim(),
    harness_source: harnessSource
  });

  // Same checks Create run applies, minus the write. The slow part (measuring
  // the executor's served context window) is memoized server-side, so creating
  // right after a green check costs nothing extra.
  const handleValidateRun = async () => {
    if (selectedCase === null) {
      return;
    }
    setCreateError(null);
    setValidation(null);
    setIsValidating(true);
    try {
      setValidation(await api.preflightRunConfig(buildConfig()));
    } catch (error) {
      setCreateError(
        error instanceof Error ? error.message : "Unable to validate configuration"
      );
    } finally {
      setIsValidating(false);
    }
  };

  // An empty executor set means "do not vary the model", which the API spells
  // `[null]` — so it still contributes a factor of one, never zero.
  const batchExecutorAxis: (string | null)[] =
    batchExecutors.size > 0 ? [...batchExecutors] : [null];

  // Mirrors the server: a case only runs the selected variants it defines, so
  // the variant axis is summed per case instead of multiplied once. Selecting
  // a case that shares no variant with the selection is rejected outright
  // rather than silently dropped, so name those cases here.
  const selectedCasesInBatch = cases.filter((item) => batchCases.has(item.case_id));
  const variantsPerCase = selectedCasesInBatch.map((item) => ({
    caseId: item.case_id,
    count: [...batchPrompts].filter((name) =>
      Object.hasOwn(item.prompt_variants ?? {}, name),
    ).length,
  }));
  const casesWithoutVariant = variantsPerCase
    .filter((entry) => entry.count === 0)
    .map((entry) => entry.caseId);

  // Same shape as the executor axis: no selection means "do not vary the
  // repository", which the API spells as a single empty string.
  const batchTargetAxis: string[] =
    batchTargets.size > 0 ? [...batchTargets] : [""];

  const batchHarnessSourceAxis: HarnessSource[] =
    batchHarnessSources.size > 0 ? [...batchHarnessSources] : ["lab_root"];

  const batchRunCount =
    variantsPerCase.reduce((total, entry) => total + entry.count, 0) *
    batchHarnesses.size *
    batchExecutorAxis.length *
    batchTargetAxis.length *
    batchHarnessSourceAxis.length *
    Math.max(repetitions, 0);

  const overBatchCap = maxBatchRuns !== null && batchRunCount > maxBatchRuns;

  const handleCreateBatch = async () => {
    setCreateError(null);
    setValidation(null);
    setBatchResult(null);
    setIsSaving(true);
    try {
      setBatchResult(
        await onCreateRunBatch({
          case_ids: [...batchCases],
          prompt_variants: [...batchPrompts],
          harnesses: [...batchHarnesses],
          executor_models: batchExecutorAxis,
          planner_models: [plannerModel || null],
          repetitions,
          score_mode: scoreMode,
          max_steps: maxSteps,
          dry_run: dryRun,
          skills_disabled: [...disabledSkills].sort(),
          skills_extra: [...extraSkills].sort(),
          skills_invoke: invokeExtra ? [...extraSkills].sort() : [],
          target_repo_ids: batchTargetAxis,
          target_ref: targetRef.trim(),
          harness_sources: batchHarnessSourceAxis
        })
      );
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Unable to queue batch");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCreateRun = async () => {
    if (selectedCase === null) {
      return;
    }

    setCreateError(null);
    setValidation(null);
    setIsSaving(true);
    try {
      const created = await onCreateRun({
        run_id: runId.trim() || undefined,
        label: label.trim() || undefined,
        config: buildConfig()
      });
      navigate(`/advanced/runs/${created.run_id}`);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Unable to create run");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Runs</p>
          <h1>Recent runs</h1>
          <p className="lede">Follow queue state, outcomes, and generated command previews.</p>
        </div>
      </header>

      <HelpPanel title="How to launch and compare a run" docHref="docs/guide-web-ui.md">
        <ol className="skills-howto">
          <li>Pick a <strong>Case</strong> (the task), then a <strong>Prompt
            variant</strong> and a <strong>Harness</strong>.</li>
          <li><strong>Executor / Planner model</strong> — Gateway models come live
            from LiteLLM (press Reload here, or Refresh from gateway in Settings);
            Direct models are read off your LM Studio / Ollama host; claude and codex
            use their native ids.</li>
          <li>Optionally toggle <strong>Skills</strong> to A/B the harness for this
            run only (a distinct arm).</li>
          <li><strong>Create run</strong>, watch it stream, then score it (SWE-bench
            auto-scores). Open <strong>Compare</strong> to line runs up across
            models.</li>
        </ol>
      </HelpPanel>

      <section className="panel">
        <div className="panel-head">
          <h2>New run</h2>
          <span className="muted">{selectedCase ? selectedCase.name : "No case selected"}</span>
        </div>
        <div className="panel-actions run-presets">
          <button className="primary" onClick={applySdlcQwenPreset} type="button">
            Preset: Full SDLC + Qwen
          </button>
          <button
            onClick={() => (batchMode ? setBatchMode(false) : enterBatchMode())}
            type="button"
          >
            {batchMode ? "Leave batch mode" : "Batch mode"}
          </button>
        </div>
        {batchMode ? (
          <div className="batch-section">
            <p className="muted">
              Each list below is a dimension: every combination of the selected cases,
              prompts, harnesses and executor models is queued, repeated{" "}
              <strong>{repetitions}</strong>{" "}
              {repetitions === 1 ? "time" : "times"}. Planner model, max steps, dry run
              and the skill ablation are shared by every run in the batch.
            </p>
            <div className="form-grid">
              <fieldset className="batch-axis">
                <legend>Cases ({batchCases.size})</legend>
                <div className="batch-options">
                  {cases.map((item) => (
                    <label className="skill-checkbox" key={item.case_id}>
                      <input
                        checked={batchCases.has(item.case_id)}
                        onChange={() =>
                          setBatchCases((current) => toggleInSet(current, item.case_id))
                        }
                        type="checkbox"
                      />
                      <span>{caseOptionLabel(item)}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="batch-axis">
                <legend>Prompt variants ({batchPrompts.size})</legend>
                <div className="batch-options">
                  {batchPromptNames.map((variant) => (
                    <label className="skill-checkbox" key={variant}>
                      <input
                        checked={batchPrompts.has(variant)}
                        onChange={() =>
                          setBatchPrompts((current) => toggleInSet(current, variant))
                        }
                        type="checkbox"
                      />
                      <span>{variant}</span>
                    </label>
                  ))}
                </div>
                <small className="muted">
                  Union of the variants defined by the selected cases. A case without a
                  selected variant contributes no runs for it.
                </small>
              </fieldset>
              <fieldset className="batch-axis">
                <legend>Harnesses ({batchHarnesses.size})</legend>
                <div className="batch-options">
                  {HARNESS_OPTIONS.map((item) => (
                    <label className="skill-checkbox" key={item}>
                      <input
                        checked={batchHarnesses.has(item)}
                        onChange={() =>
                          setBatchHarnesses((current) => toggleInSet(current, item))
                        }
                        type="checkbox"
                      />
                      <span>{item}</span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="batch-axis">
                <legend>Executor models ({batchExecutors.size})</legend>
                <div className="batch-options">
                  {modelOptions.map((option) => (
                    <label className="skill-checkbox" key={option.id}>
                      <input
                        checked={batchExecutors.has(option.id)}
                        onChange={() =>
                          setBatchExecutors((current) => toggleInSet(current, option.id))
                        }
                        type="checkbox"
                      />
                      <span>{option.label}</span>
                    </label>
                  ))}
                </div>
                <small className="muted">
                  None selected means "do not vary the model" — the harness default is
                  used for every run.
                </small>
              </fieldset>
              {targets.length > 0 ? (
                <fieldset className="batch-axis">
                  <legend>Target repositories ({batchTargets.size})</legend>
                  <div className="batch-options">
                    {targets.map((target) => (
                      <label className="skill-checkbox" key={target.target_id}>
                        <input
                          checked={batchTargets.has(target.target_id)}
                          onChange={() =>
                            setBatchTargets((current) =>
                              toggleInSet(current, target.target_id)
                            )
                          }
                          type="checkbox"
                        />
                        <span>{target.target_id}</span>
                      </label>
                    ))}
                  </div>
                  <small className="muted">
                    Vary the repository to test whether the harness holds outside the
                    repo it was designed in. Scores across different repositories are
                    not comparable as though conditions were identical — read them per
                    repository, not pooled.
                  </small>
                </fieldset>
              ) : null}
              <fieldset className="batch-axis">
                  <legend>Harness ({batchHarnessSources.size})</legend>
                  <div className="batch-options">
                    {(
                      targets.length > 0
                        ? (["lab_root", "native", "injected"] as HarnessSource[])
                        : (["lab_root", "bare"] as HarnessSource[])
                    ).map(
                      (source) => (
                        <label className="skill-checkbox" key={source}>
                          <input
                            checked={batchHarnessSources.has(source)}
                            onChange={() =>
                              setBatchHarnessSources((current) =>
                                toggleInSet(current, source)
                              )
                            }
                            type="checkbox"
                          />
                          <span>{source}</span>
                        </label>
                      )
                    )}
                  </div>
                  <small className="muted">
                    {targets.length > 0 ? (
                      <>
                        Selecting <code>native</code> and <code>injected</code>{" "}
                        together on one repository is the harness A/B: same repo, same
                        case, same model, harness varied. Both require a target
                        repository.
                      </>
                    ) : (
                      <>
                        Selecting <code>bare</code> and <code>lab_root</code> together
                        is the bare-versus-governed A/B: one arm sees the prompt and
                        nothing else, the other sees the whole harness.
                      </>
                    )}
                  </small>
                </fieldset>
              <label>
                <span>Repetitions</span>
                <input
                  min={1}
                  onChange={(event) =>
                    setRepetitions(Math.max(1, Number(event.target.value) || 1))
                  }
                  type="number"
                  value={repetitions}
                />
                <small className="muted">
                  Trials per combination. Repeat to separate a real difference from
                  run-to-run variance.
                </small>
              </label>
            </div>
          </div>
        ) : null}
        <div className="form-grid">
          <label>
            <span>Run template (optional)</span>
            <select
              disabled={caseRunIds.length === 0}
              value={caseRunIds.includes(runId) ? runId : ""}
              onChange={(event) => setRunId(event.target.value)}
            >
              <option value="">None — leave Run ID blank for auto</option>
              {caseRunIds.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Run ID (optional)</span>
            <input
              onChange={(event) => setRunId(event.target.value)}
              placeholder={`auto (${caseId}-K-NNN)`}
              value={runId}
            />
            <small className="muted">Leave blank to auto-generate the next {caseId}-K-NNN id.</small>
          </label>
          <label>
            <span>Custom label (optional)</span>
            <input
              onChange={(event) => setLabel(event.target.value)}
              placeholder="e.g. sdlc-qwen-smoke"
              value={label}
            />
            <small className="muted">Overrides Run ID as the persisted run identifier when set.</small>
          </label>
          <label>
            <span>Case</span>
            <select value={caseId} onChange={(event) => setCaseId(event.target.value)}>
              {cases.map((item) => (
                <option key={item.case_id} value={item.case_id}>
                  {caseOptionLabel(item)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Prompt variant</span>
            <select value={promptVariant} onChange={(event) => setPromptVariant(event.target.value)}>
              {promptVariantNames.map((variant) => (
                <option key={variant} value={variant}>
                  {variant}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Harness</span>
            <select value={harness} onChange={(event) => handleHarnessChange(event.target.value)}>
              {HARNESS_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            {HOST_HARNESSES.has(harness) ? (
              <small className="muted">
                Runs the {harness} CLI with its native auth; pick the model below.{" "}
                {harness === "claude"
                  ? "Works in Docker (put CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host)."
                  : "Needs host mode — run make app-host."}
              </small>
            ) : null}
          </label>
          <ModelPicker
            allowNone
            label="Planner model"
            directErrors={directErrors}
            onChange={setPlannerModel}
            options={modelOptions}
            value={plannerModel}
          />
          <ModelPicker
            label="Executor model"
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
                ? `Native ${harness} CLI models (passed to --model). Edit the list in harnesses.ts as versions ship.`
                : modelSource === "opencode.json"
                  ? "Gateway unreachable — showing opencode.json models."
                  : modelSource === "gateway"
                    ? "AI Gateway models are routed through LiteLLM; Direct models are read off your LM Studio host, where an alias cannot rename them in transit."
                    : "Loading models…"}
            </small>
          </ModelPicker>
          <label>
            <span>Score mode</span>
            <select value={scoreMode} disabled>
              <option value="manual">Manual</option>
            </select>
            <small className="muted">
              Interactive scoring is CLI-only. A case with a validation command scores
              itself, a case with a rubric is LLM-judged; otherwise the run stops in
              awaiting_manual_score and you score it from the run page.
            </small>
          </label>
          <label>
            <span>Max steps</span>
            <select value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value))}>
              {maxStepsOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="switch">
            <span>Resume</span>
            <input checked={resume} onChange={(event) => setResume(event.target.checked)} type="checkbox" />
          </label>
          <label className="switch">
            <span>Dry run</span>
            <input checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} type="checkbox" />
          </label>
          {targets.length > 0 ? (
            <>
              <label>
                <span>Target repository</span>
                <select
                  disabled={batchMode}
                  onChange={(event) => setTargetRepoId(event.target.value)}
                  value={targetRepoId}
                >
                  <option value="">None — run in the lab workspace</option>
                  {targets.map((target) => (
                    <option key={target.target_id} value={target.target_id}>
                      {target.target_id}
                    </option>
                  ))}
                </select>
                <small className="muted">
                  {batchMode
                    ? "Varied as a batch axis below."
                    : "The repository the agent works in. Provisioned from the pinned local checkout for each run."}
                </small>
              </label>
              {targetRepoId !== "" || batchTargets.size > 0 ? (
                <label>
                  <span>Ref</span>
                  <input
                    onChange={(event) => setTargetRef(event.target.value)}
                    placeholder={
                      targets.find((item) => item.target_id === targetRepoId)
                        ?.default_ref ?? "main"
                    }
                    value={targetRef}
                  />
                  <small className="muted">
                    {targetPin === null ? (
                      <button
                        disabled={checkingPin || targetRepoId === ""}
                        onClick={() => {
                          setCheckingPin(true);
                          api
                            .resolveTargetRef(targetRepoId, targetRef.trim())
                            .then((resolved) => setTargetPin(resolved))
                            .catch((error: unknown) =>
                              setTargetPin(
                                error instanceof Error
                                  ? error.message
                                  : "Could not resolve"
                              )
                            )
                            .finally(() => setCheckingPin(false));
                        }}
                        type="button"
                      >
                        {checkingPin ? "Checking…" : "Check commit"}
                      </button>
                    ) : typeof targetPin === "string" ? (
                      <span className="error-text">{targetPin}</span>
                    ) : (
                      <>Pins to {targetPin.commit.slice(0, 10)}</>
                    )}
                  </small>
                </label>
              ) : null}
              {!batchMode ? (
                <label>
                  <span>Governance source</span>
                  <select
                    onChange={(event) =>
                      setHarnessSource(event.target.value as HarnessSource)
                    }
                    value={harnessSource}
                  >
                    <option value="lab_root">Lab harness (from the lab repo)</option>
                    {/* The in-target pair needs a repository to be inside;
                        `bare` needs the absence of one (ADR-0049). */}
                    {targetRepoId === "" ? (
                      <option value="bare">Bare — nothing, not even governance</option>
                    ) : (
                      <>
                        <option value="native">
                          Native — the target's own conventions
                        </option>
                        <option value="injected">
                          Injected — our harness inside the target
                        </option>
                      </>
                    )}
                  </select>
                  <small className="muted">
                    {harnessSource === "lab_root"
                      ? "The agent works from the lab repo, so it sees CLAUDE.md, the hooks, the subagents and .github/."
                      : harnessSource === "bare"
                        ? "Control arm: an empty workspace with the lab's governance masked in the container — the agent sees the prompt and nothing else. Needs the docker backend."
                        : harnessSource === "native"
                          ? "Control arm: the agent works inside the repo with only what that repo ships."
                          : "Treatment arm: the agent works inside the repo with our harness projected into it."}
                  </small>
                </label>
              ) : null}
            </>
          ) : null}
        </div>
        {harnessSkills.length > 0 ? (
          <div className="skills-section">
            <button className="skills-toggle" onClick={() => setSkillsOpen((open) => !open)} type="button">
              {skillsOpen ? "▾" : "▸"} Skills — {harnessSkills.length - disabledSkills.size}/
              {harnessSkills.length} enabled
              {extraSkills.size > 0 ? ` + ${extraSkills.size} experimental` : ""}
              {disabledSkills.size > 0 || extraSkills.size > 0 ? (
                <span className="badge error">ablation</span>
              ) : null}
            </button>
            {skillsOpen ? (
              <>
                <p className="muted">
                  Unchecked skills are hidden from the agent for this run only (symlinks and
                  the adapter-file skill list are regenerated, then restored). A non-empty
                  ablation changes the run's condition_hash — that is the point: compare
                  against a full-harness baseline of the same case/model.
                </p>
                <div className="panel-actions">
                  <button onClick={() => setDisabledSkills(new Set())} type="button">
                    Enable all
                  </button>
                  <button
                    onClick={() => setDisabledSkills(new Set(harnessSkills.map((skill) => skill.name)))}
                    type="button"
                  >
                    Disable all
                  </button>
                </div>
                <div className="skills-grid">
                  {harnessSkills.map((skill) => (
                    <label className="skill-checkbox" key={skill.name} title={skill.description}>
                      <input
                        checked={!disabledSkills.has(skill.name)}
                        onChange={() => toggleSkill(skill.name)}
                        type="checkbox"
                      />
                      <span>
                        {skill.name}
                        <small className="muted"> {skill.kind}</small>
                      </span>
                    </label>
                  ))}
                </div>
                {stagedSkills.length > 0 ? (
                  <>
                    <p className="sidebar-label">Experimental (staged, off by default)</p>
                    <div className="skills-grid">
                      {stagedSkills.map((skill) => (
                        <label
                          className="skill-checkbox"
                          key={skill.name}
                          title={skill.description}
                        >
                          <input
                            checked={extraSkills.has(skill.name)}
                            onChange={() => toggleExtraSkill(skill.name)}
                            type="checkbox"
                          />
                          <span>
                            {skill.name}
                            <small className="muted"> {skill.status}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                    <label className="checkbox">
                      <input
                        checked={invokeExtra}
                        disabled={extraSkills.size === 0}
                        onChange={(event) => setInvokeExtra(event.target.checked)}
                        type="checkbox"
                      />
                      <span>
                        Ask the agent to use them
                        <small className="muted"> at the start of every step</small>
                      </span>
                    </label>
                  </>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
        <div className="panel-actions">
          <button
            disabled={isValidating || isSaving || cases.length === 0}
            onClick={handleValidateRun}
            type="button"
          >
            {isValidating ? "Validating..." : "Validate config"}
          </button>
          {batchMode ? (
            <button
              className="primary"
              disabled={
                isSaving ||
                batchRunCount === 0 ||
                overBatchCap ||
                casesWithoutVariant.length > 0
              }
              onClick={handleCreateBatch}
              type="button"
            >
              {isSaving
                ? "Queueing..."
                : `Queue ${batchRunCount} ${batchRunCount === 1 ? "run" : "runs"}`}
            </button>
          ) : (
            <button
              className="primary"
              disabled={isSaving || cases.length === 0}
              onClick={handleCreateRun}
              type="button"
            >
              {isSaving ? "Creating..." : "Create run"}
            </button>
          )}
        </div>
        {batchMode ? (
          <p className="muted">
            {batchRunCount === 0 ? (
              <span className="badge error">
                Select at least one case, prompt variant and harness.
              </span>
            ) : casesWithoutVariant.length > 0 ? (
              <span className="badge error">
                {casesWithoutVariant.join(", ")} defines none of the selected prompt
                variants. Select a variant it has, or deselect the case.
              </span>
            ) : overBatchCap ? (
              <span className="badge error">
                {batchRunCount} runs exceeds the limit of {maxBatchRuns}. Narrow a
                dimension, or raise HARNESS_LAB_MAX_BATCH_RUNS.
              </span>
            ) : (
              <>
                This queues <strong>{batchRunCount}</strong> containerised{" "}
                {batchRunCount === 1 ? "run" : "runs"}, each spending model budget.
                Each case runs only the selected variants it defines, so the count
                can be lower than the product of the axes. Nothing is created unless
                every combination passes preflight.
              </>
            )}
          </p>
        ) : null}
        {batchResult ? (
          <p className="muted">
            <span className="badge ok">queued</span> {batchResult.count}{" "}
            {batchResult.count === 1 ? "run" : "runs"} as{" "}
            <code>{batchResult.batch_id}</code>. They appear in the table below as the
            queue drains.
          </p>
        ) : null}
        {validation ? (
          <p className="muted">
            <span className={validation.ok ? "badge ok" : "badge error"}>
              {validation.ok ? "ready" : "blocked"}
            </span>{" "}
            {validation.detail}
          </p>
        ) : (
          <p className="muted">
            Validate config runs the same checks as Create run — case files, harness
            availability, model routing, and the context window the executor is actually
            serving — without creating anything. On a cold model the first check can take
            up to ~40s while the backend loads it.
          </p>
        )}
        {createError ? <ErrorBanner message={createError} onDismiss={() => setCreateError(null)} /> : null}
        <div>
          <p className="sidebar-label">Equivalent CLI command</p>
          <code className="command-preview">{commandPreview}</code>
          {selectedCase?.type === "swe_bench_instance" ? (
            <p className="muted">
              SWE-bench cases are provisioned by the web worker (case_spec.json + repo
              checkout); the CLI line above is a reference, not a runnable equivalent.
            </p>
          ) : null}
          <p className="muted">
            Interactive scoring is CLI-only. A case with a validation command scores
            itself, a case with a rubric is LLM-judged; otherwise the run stops in
            awaiting_manual_score and you score it from the run page.
          </p>
        </div>
      </section>

      <RunTable
        cases={cases}
        onCancelRun={onCancelRun}
        onCloneRun={onCloneRun}
        onDeleteRun={onDeleteRun}
        runs={runs}
      />
    </section>
  );
}
