import { useEffect, useMemo, useState } from "react";
import type { MouseEvent } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { caseOptionLabel } from "../caseLabels";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { StateBadge } from "../components/StateBadge";
import { CANCELLABLE_STATES } from "./RunDetail";
import type {
  CaseRecord,
  HarnessSkill,
  ModelOption,
  RunCreatePayload,
  RunRecord,
  StagedSkillRecord
} from "../types";

type RunsProps = {
  cases: CaseRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onCloneRun: (runId: string) => Promise<RunRecord>;
  onCreateRun: (payload: RunCreatePayload) => Promise<RunRecord>;
  onDeleteRun: (runId: string, force?: boolean) => Promise<void>;
  runs: RunRecord[];
};

const HARNESS_OPTIONS = ["opencode", "codex", "claude", "antigravity"] as const;
// Harnesses that run via the host CLI (native auth) instead of the runner
// container — they need the host worker (subprocess backend).
const HOST_HARNESSES = new Set(["codex", "claude"]);
const FALLBACK_EXECUTOR_MODEL = "gateway/lmstudio-qwen3.6-35b-build";
const MAX_STEPS_OPTIONS = [3, 6, 10] as const;

// Native model menus for the host-CLI harnesses. Values are passed straight to
// the CLI's --model, so keep them to ids the CLI accepts. Edit these as new
// versions ship — this is the systematic-lab knob for host harnesses.
const HARNESS_NATIVE_MODELS: Record<string, ModelOption[]> = {
  claude: [
    { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
    { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5" }
  ],
  // Only the confirmed CLI default is listed — add your codex model ids here.
  codex: [{ id: "codex-mini-latest", label: "Codex Mini (latest)" }]
};

// The model menu for a harness: its native CLI models, else the live gateway list.
function modelsForHarness(harness: string, gatewayModels: ModelOption[]): ModelOption[] {
  return HARNESS_NATIVE_MODELS[harness] ?? gatewayModels;
}

// At-a-glance improvement-synthesis status per run (from improvements.json):
// "sí" = proposed changes, "no" = ran but clean, "pendiente" = still to generate.
function improvementsBadge(status: RunRecord["improvements"]) {
  if (status === "yes") {
    return { className: "badge ok", label: "sí", title: "Generó sugerencias de mejora." };
  }
  if (status === "no") {
    return {
      className: "badge neutral",
      label: "no",
      title: "Se generó la síntesis y no propuso cambios (run limpio / gate cerrado)."
    };
  }
  return {
    className: "badge warn",
    label: "pendiente",
    title: "Sin síntesis todavía — entrá al run para generar sus proposals."
  };
}

// Ensure the currently-selected model always renders as an option even if the
// live gateway list does not contain it (e.g. a preset value, or the API is
// offline in preview mode), so the select never shows a blank selection.
function withCurrent(options: ModelOption[], current: string): ModelOption[] {
  if (!current || options.some((option) => option.id === current)) {
    return options;
  }
  return [...options, { id: current, label: current }];
}

export function Runs({
  cases,
  onCancelRun,
  onCloneRun,
  onCreateRun,
  onDeleteRun,
  runs
}: RunsProps) {
  const { runId: activeRunId } = useParams();
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
  // Remember the gateway default so switching back to opencode restores it.
  const [gatewayDefault, setGatewayDefault] = useState(FALLBACK_EXECUTOR_MODEL);
  const scoreMode = "manual";
  const [maxSteps, setMaxSteps] = useState(10);
  const [resume, setResume] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [caseRunIds, setCaseRunIds] = useState<string[]>([]);
  const [rowActionError, setRowActionError] = useState<string | null>(null);
  const [pendingRowId, setPendingRowId] = useState<string | null>(null);
  const [harnessSkills, setHarnessSkills] = useState<HarnessSkill[]>([]);
  const [disabledSkills, setDisabledSkills] = useState<Set<string>>(new Set());
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [extraSkills, setExtraSkills] = useState<Set<string>>(new Set());
  const [skillsOpen, setSkillsOpen] = useState(false);

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
      .listModels()
      .then((catalog) => {
        if (!active) {
          return;
        }
        setModelOptions(catalog.models);
        setModelSource(catalog.source);
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

  const plannerOptions = useMemo(
    () => withCurrent(modelOptions, plannerModel),
    [modelOptions, plannerModel]
  );
  const executorOptions = useMemo(
    () => withCurrent(modelsForHarness(harness, modelOptions), executorModel),
    [harness, modelOptions, executorModel]
  );

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

  const promptVariantNames = Object.keys(selectedCase?.prompt_variants ?? { default: "" });
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

  const handleCreateRun = async () => {
    if (selectedCase === null) {
      return;
    }

    setCreateError(null);
    setIsSaving(true);
    try {
      const created = await onCreateRun({
        run_id: runId.trim() || undefined,
        label: label.trim() || undefined,
        config: {
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
          skills_extra: [...extraSkills].sort()
        }
      });
      navigate(`/runs/${created.run_id}`);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Unable to create run");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancelRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onCancelRun(targetRunId)
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const handleCloneRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onCloneRun(targetRunId)
      .then((created) => navigate(`/runs/${created.run_id}`))
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const handleDeleteRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    if (!window.confirm(`Delete run "${targetRunId}"? This cannot be undone.`)) {
      return;
    }
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onDeleteRun(targetRunId)
      .then(() => {
        if (activeRunId === targetRunId) {
          navigate("/runs");
        }
      })
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
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
          <li><strong>Executor / Planner model</strong> come live from your LiteLLM
            gateway. Added a model there? Run <code>make sync-gateway-models</code>
            and it shows up here.</li>
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
        </div>
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
          <label>
            <span>Planner model</span>
            <select value={plannerModel} onChange={(event) => setPlannerModel(event.target.value)}>
              <option value="">None</option>
              {plannerOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Executor model</span>
            <select value={executorModel} onChange={(event) => setExecutorModel(event.target.value)}>
              {executorOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
            <small className="muted">
              {HARNESS_NATIVE_MODELS[harness]
                ? `Native ${harness} CLI models (passed to --model). Edit the list in Runs.tsx as versions ship.`
                : modelSource === "opencode.json"
                  ? "Gateway unreachable — showing opencode.json models."
                  : modelSource === "gateway"
                    ? "Live from your gateway (LiteLLM). Run make sync-gateway-models after adding models."
                    : "Loading models from the gateway…"}
            </small>
          </label>
          <label>
            <span>Score mode</span>
            <select value={scoreMode} disabled>
              <option value="manual">Manual</option>
            </select>
            <small className="muted">
              Interactive is CLI-only; web runs finish in awaiting_manual_score and are scored from run detail.
            </small>
          </label>
          <label>
            <span>Max steps</span>
            <select value={maxSteps} onChange={(event) => setMaxSteps(Number(event.target.value))}>
              {MAX_STEPS_OPTIONS.map((item) => (
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
                  </>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
        <div className="panel-actions">
          <button className="primary" disabled={isSaving || cases.length === 0} onClick={handleCreateRun} type="button">
            {isSaving ? "Creating..." : "Create run"}
          </button>
        </div>
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
            Web flow always uses manual scoring to avoid worker blocking; submit score in run detail when state is
            awaiting_manual_score.
          </p>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Run list</h2>
          <span className="muted">{runs.length} total</span>
        </div>
        {rowActionError ? <ErrorBanner message={rowActionError} onDismiss={() => setRowActionError(null)} /> : null}
        {runs.length === 0 ? (
          <EmptyState
            description="Create a run from the web app once the API is online."
            title="No runs yet."
          />
        ) : (
          <div className="table table--runs">
            <div className="table-row table-head">
              <span>Run</span>
              <span>State</span>
              <span>Score</span>
              <span>Improvements</span>
              <span>Actions</span>
            </div>
            {runs.map((run) => {
              const isActive = activeRunId === run.run_id;
              const isPending = pendingRowId === run.run_id;
              const improvements = improvementsBadge(run.improvements);
              return (
                <div className={isActive ? "table-row runs-row active" : "table-row runs-row"} key={run.run_id}>
                  <NavLink className="runs-row-link" to={`/runs/${run.run_id}`}>
                    <span>
                      <strong>{run.run_id}</strong>
                      <small>{run.command.join(" ")}</small>
                    </span>
                    <StateBadge state={run.state} />
                    <span>{run.score ?? "—"}</span>
                    <span>
                      <span className={improvements.className} title={improvements.title}>
                        {improvements.label}
                      </span>
                    </span>
                  </NavLink>
                  <div className="row-actions">
                    <button
                      disabled={!CANCELLABLE_STATES.includes(run.state) || isPending}
                      onClick={(event) => handleCancelRow(event, run.run_id)}
                      type="button"
                    >
                      Cancel
                    </button>
                    <button disabled={isPending} onClick={(event) => handleCloneRow(event, run.run_id)} type="button">
                      Run again
                    </button>
                    <button disabled={isPending} onClick={(event) => handleDeleteRow(event, run.run_id)} type="button">
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </section>
  );
}
