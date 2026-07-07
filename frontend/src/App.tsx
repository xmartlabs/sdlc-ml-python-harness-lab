import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import { Shell } from "./components/Shell";
import { Cases } from "./pages/Cases";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";
import { Settings } from "./pages/Settings";
import type { CaseCreatePayload, CaseRecord, RunCreatePayload, RunRecord } from "./types";

type PageId = "dashboard" | "runs" | "cases" | "compare" | "settings";

const previewCase: CaseRecord = {
  case_id: "tetris-v1",
  name: "Tetris pilot case",
  type: "single_prompt",
  status: "active",
  sync_state: "imported_from_yaml",
  description: "Seeded local preview for the Tetris benchmark."
};

const previewRun: RunRecord = {
  run_id: "tetris-v1-A-001",
  state: "completed",
  command: ["uv", "run", "python", "scripts/bench_run.py", "--case", "tetris-v1"],
  score: 3,
  interventions: 1,
  failure_reason: null
};

const fallbackCases: CaseRecord[] = [previewCase];
const fallbackRuns: RunRecord[] = [previewRun];

export default function App() {
  const [page, setPage] = useState<PageId>("dashboard");
  const [cases, setCases] = useState<CaseRecord[]>(fallbackCases);
  const [runs, setRuns] = useState<RunRecord[]>(fallbackRuns);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(previewCase.case_id);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(previewRun.run_id);
  const [apiStatus, setApiStatus] = useState("Preview mode");

  useEffect(() => {
    let active = true;

    Promise.allSettled([api.health(), api.listCases(), api.listRuns()]).then(
      ([healthResult, casesResult, runsResult]) => {
        if (!active) {
          return;
        }

        if (healthResult.status === "fulfilled") {
          setApiStatus(healthResult.value.status);
        }

        if (casesResult.status === "fulfilled" && casesResult.value.length > 0) {
          setCases(casesResult.value);
          setSelectedCaseId((current) => current ?? casesResult.value[0]?.case_id ?? null);
        }

        if (runsResult.status === "fulfilled" && runsResult.value.length > 0) {
          setRuns(runsResult.value);
          setSelectedRunId((current) => current ?? runsResult.value[0]?.run_id ?? null);
        }

        if (
          healthResult.status === "rejected" &&
          casesResult.status === "rejected" &&
          runsResult.status === "rejected"
        ) {
          setApiStatus("Preview mode");
        }
      }
    );

    return () => {
      active = false;
    };
  }, []);

  const selectedRun = useMemo(
    () => runs.find((run) => run.run_id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );

  useEffect(() => {
    if (selectedRunId === null) {
      return;
    }
    const tracked = runs.find((run) => run.run_id === selectedRunId);
    if (!tracked || !["pending", "running", "awaiting_manual_score"].includes(tracked.state)) {
      return;
    }

    let active = true;
    const refresh = () => {
      api
        .getRun(selectedRunId)
        .then((updated) => {
          if (!active) {
            return;
          }
          updateRun(updated);
        })
        .catch(() => {
          // Keep last known state on transient API errors.
        });
    };

    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [runs, selectedRunId]);

  const selectedCase = useMemo(
    () => cases.find((item) => item.case_id === selectedCaseId) ?? cases[0] ?? null,
    [cases, selectedCaseId]
  );

  const seedTetris = () => {
    void api
      .seedTetris()
      .then((createdCase) => {
        setCases((current) => [createdCase, ...current.filter((item) => item.case_id !== createdCase.case_id)]);
        setSelectedCaseId(createdCase.case_id);
      })
      .catch(() => {
        setCases((current) => [previewCase, ...current.filter((item) => item.case_id !== previewCase.case_id)]);
        setSelectedCaseId(previewCase.case_id);
      });
  };

  const createCase = async (payload: CaseCreatePayload) => {
    const created = await api.createCase(payload);
    setCases((current) => [created, ...current.filter((item) => item.case_id !== created.case_id)]);
    setSelectedCaseId(created.case_id);
    return created;
  };

  const createRun = async (payload: RunCreatePayload) => {
    const created = await api.createRun(payload);
    setRuns((current) => [created, ...current.filter((item) => item.run_id !== created.run_id)]);
    setSelectedRunId(created.run_id);
    return created;
  };

  const updateRun = (updated: RunRecord) => {
    setRuns((current) =>
      current.map((run) => (run.run_id === updated.run_id ? updated : run))
    );
  };

  const cancelRun = async (runId: string) => {
    const updated = await api.cancelRun(runId);
    updateRun(updated);
    return updated;
  };

  const retryRun = async (runId: string) => {
    const updated = await api.retryRun(runId);
    updateRun(updated);
    return updated;
  };

  const scoreRun = async (runId: string, score: number, interventions: number, notes: string) => {
    const updated = await api.scoreRun(runId, score, interventions, notes);
    updateRun(updated);
    return updated;
  };

  const inspector = (
    <RunDetail onCancelRun={cancelRun} onRetryRun={retryRun} onScoreRun={scoreRun} run={selectedRun} />
  );

  let content: ReactNode;

  switch (page) {
    case "dashboard":
      content = <Dashboard apiStatus={apiStatus} cases={cases} runs={runs} />;
      break;
    case "runs":
      content = (
        <Runs
          cases={cases}
          onCreateRun={createRun}
          onSelectRun={setSelectedRunId}
          runs={runs}
          selectedRunId={selectedRunId}
        />
      );
      break;
    case "cases":
      content = (
        <Cases
          cases={cases.length > 0 ? cases : [previewCase]}
          onCreateCase={createCase}
          onSeedTetris={seedTetris}
          onSelectCase={setSelectedCaseId}
          selectedCaseId={selectedCaseId}
        />
      );
      break;
    case "compare":
      content = <Compare runs={runs} />;
      break;
    case "settings":
      content = <Settings />;
      break;
    default:
      content = <Dashboard apiStatus={apiStatus} cases={cases} runs={runs} />;
  }

  return (
    <Shell activePage={page} inspector={inspector} onNavigate={setPage}>
      {content}
      {selectedCase !== null && page === "cases" ? (
        <section className="panel">
          <div className="panel-head">
            <h2>Selected case</h2>
            <span className="muted">{selectedCase.sync_state}</span>
          </div>
          <p>{selectedCase.description}</p>
        </section>
      ) : null}
    </Shell>
  );
}
