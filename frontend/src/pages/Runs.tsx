import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { CaseRecord, RunCreatePayload, RunRecord } from "../types";

type RunsProps = {
  cases: CaseRecord[];
  onCreateRun: (payload: RunCreatePayload) => Promise<RunRecord>;
  runs: RunRecord[];
  onSelectRun: (runId: string) => void;
  selectedRunId: string | null;
};

const HARNESS_OPTIONS = ["opencode", "antigravity"] as const;
const PLANNER_MODEL_OPTIONS = [
  "",
  "gateway/nim-nemotron-super-120b",
  "gateway/gpt-5.5"
] as const;
const EXECUTOR_MODEL_OPTIONS = [
  "gateway/lmstudio-qwen3.6-35b-build",
  "gateway/nim-nemotron-super-120b",
  "gateway/gpt-5.5",
  "openai/gpt-4o-mini",
  "openai/moonshotai/kimi-k2.6"
] as const;
const MAX_STEPS_OPTIONS = [3, 6, 10] as const;

export function Runs({
  cases,
  onCreateRun,
  runs,
  onSelectRun,
  selectedRunId
}: RunsProps) {
  const [runId, setRunId] = useState("new-run");
  const [caseId, setCaseId] = useState(cases[0]?.case_id ?? "tetris-v1");
    const [promptVariant, setPromptVariant] = useState("sdlc");
  const [harness, setHarness] = useState("opencode");
  const [plannerModel, setPlannerModel] = useState("");
  const [executorModel, setExecutorModel] = useState("gateway/lmstudio-qwen3.6-35b-build");
  const [scoreMode, setScoreMode] = useState("manual");
  const [maxSteps, setMaxSteps] = useState(10);
  const [resume, setResume] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [caseRunIds, setCaseRunIds] = useState<string[]>([]);

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
        if (!active) {
          return;
        }
        setCaseRunIds(payload.run_ids);
        if (payload.run_ids.length > 0 && !payload.run_ids.includes(runId)) {
          const preferred = payload.run_ids.find((item) => item === `${caseId}-K-001`);
          setRunId(preferred ?? payload.run_ids[0]);
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

  const commandPreview = useMemo(() => {
    const parts = [
      "make bench-run",
      `CASE=${caseId}`,
      `RUN=${runId}`,
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
     return parts.join(" \\\n+  ");
    return parts.join(" \\\n+  ");
  }, [caseId, dryRun, executorModel, maxSteps, plannerModel, promptVariant, resume, runId]);

  const applySdlcQwenPreset = () => {
    setCaseId("tetris-v1");
    setPromptVariant("sdlc");
    setHarness("opencode");
    setPlannerModel("");
    setExecutorModel("gateway/lmstudio-qwen3.6-35b-build");
    setMaxSteps(10);
    setScoreMode("manual");
  };

  const handleCreateRun = async () => {
    if (selectedCase === null) {
      return;
    }

    setCreateError(null);
    setIsSaving(true);
    try {
      const created = await onCreateRun({
        run_id: runId,
        config: {
          case_id: caseId,
          prompt_variant: promptVariant,
          harness,
          planner_model: plannerModel || null,
          executor_model: executorModel || null,
          score_mode: scoreMode,
          max_steps: maxSteps,
          resume,
          dry_run: dryRun
        }
      });
      onSelectRun(created.run_id);
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
            <span>Run template</span>
            <select
              disabled={caseRunIds.length === 0}
              value={caseRunIds.includes(runId) ? runId : ""}
              onChange={(event) => setRunId(event.target.value)}
            >
              {caseRunIds.length === 0 ? <option value="">No run templates found</option> : null}
              {caseRunIds.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Run ID</span>
            <input value={runId} onChange={(event) => setRunId(event.target.value)} />
            <small className="muted">Use a run_id present in benchmarks/{caseId}/runs.yaml.</small>
          </label>
          <label>
            <span>Case</span>
            <select value={caseId} onChange={(event) => setCaseId(event.target.value)}>
              {cases.map((item) => (
                <option key={item.case_id} value={item.case_id}>
                  {item.case_id} · {item.name}
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
            <select value={harness} onChange={(event) => setHarness(event.target.value)}>
              {HARNESS_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Planner model</span>
            <select value={plannerModel} onChange={(event) => setPlannerModel(event.target.value)}>
              {PLANNER_MODEL_OPTIONS.map((item) => (
                <option key={item || "none"} value={item}>
                  {item || "None"}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Executor model</span>
            <select value={executorModel} onChange={(event) => setExecutorModel(event.target.value)}>
              {EXECUTOR_MODEL_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Score mode</span>
            <select value={scoreMode} onChange={(event) => setScoreMode(event.target.value)}>
              <option value="manual">Manual</option>
              <option value="auto">Auto</option>
            </select>
            <small className="muted">Interactive is CLI-only; web flow uses manual scoring.</small>
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
        <div className="panel-actions">
          <button className="primary" disabled={isSaving || cases.length === 0} onClick={handleCreateRun} type="button">
            {isSaving ? "Creating..." : "Create run"}
          </button>
        </div>
        {createError ? <p className="muted error">{createError}</p> : null}
        <div>
          <p className="sidebar-label">Equivalent CLI command</p>
          <code className="command-preview">{commandPreview}</code>
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
        {runs.length === 0 ? (
          <div className="empty-state">
            <strong>No runs yet.</strong>
            <p>Create a run from the web app once the API is online.</p>
          </div>
        ) : (
          <div className="table">
            <div className="table-row table-head">
              <span>Run</span>
              <span>State</span>
              <span>Score</span>
            </div>
            {runs.map((run) => (
              <button
                className={selectedRunId === run.run_id ? "table-row selectable active" : "table-row selectable"}
                key={run.run_id}
                onClick={() => onSelectRun(run.run_id)}
                type="button"
              >
                <span>
                  <strong>{run.run_id}</strong>
                  <small>{run.command.join(" ")}</small>
                </span>
                <span className="badge">{run.state}</span>
                <span>{run.score ?? "—"}</span>
              </button>
            ))}
          </div>
        )}
      </section>
    </section>
  );
}
