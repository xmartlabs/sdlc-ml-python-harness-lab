import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Layout } from "./components/Layout";
import { RunDetailRoute } from "./components/RunDetailRoute";
import { Cases } from "./pages/Cases";
import { Compare } from "./pages/Compare";
import { Dashboard } from "./pages/Dashboard";
import { Guide } from "./pages/Guide";
import { Improvements } from "./pages/Improvements";
import { Runs } from "./pages/Runs";
import { Settings } from "./pages/Settings";
import { Skills } from "./pages/Skills";
import { applyTheme, getStoredTheme } from "./theme";
import type { ThemeMode } from "./theme";
import type { CaseCreatePayload, CaseRecord, RunCreatePayload, RunRecord } from "./types";

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
  const [cases, setCases] = useState<CaseRecord[]>(fallbackCases);
  const [runs, setRuns] = useState<RunRecord[]>(fallbackRuns);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(previewCase.case_id);
  const [apiStatus, setApiStatus] = useState("Preview mode");
  const [theme, setTheme] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

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

  const importSwebench = async (instanceId: string) => {
    const imported = await api.importSwebench(instanceId);
    setCases((current) => [imported, ...current.filter((item) => item.case_id !== imported.case_id)]);
    setSelectedCaseId(imported.case_id);
    return imported;
  };

  const importSwebenchPro = async (instanceId: string) => {
    const imported = await api.importSwebenchPro(instanceId);
    setCases((current) => [imported, ...current.filter((item) => item.case_id !== imported.case_id)]);
    setSelectedCaseId(imported.case_id);
    return imported;
  };

  const createRun = async (payload: RunCreatePayload) => {
    const created = await api.createRun(payload);
    setRuns((current) => [created, ...current.filter((item) => item.run_id !== created.run_id)]);
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

  const cloneRun = async (runId: string) => {
    const created = await api.cloneRun(runId);
    setRuns((current) => [created, ...current.filter((run) => run.run_id !== created.run_id)]);
    return created;
  };

  const deleteRun = async (runId: string, force = false) => {
    await api.deleteRun(runId, force);
    setRuns((current) => current.filter((run) => run.run_id !== runId));
  };

  return (
    <Routes>
      <Route element={<Layout onUpdateRun={updateRun} runs={runs} />}>
        <Route element={<Dashboard apiStatus={apiStatus} cases={cases} runs={runs} />} path="/" />
        <Route
          element={
            <Runs
              cases={cases}
              onCancelRun={cancelRun}
              onCloneRun={cloneRun}
              onCreateRun={createRun}
              onDeleteRun={deleteRun}
              runs={runs}
            />
          }
          path="/runs"
        />
        <Route
          element={
            <RunDetailRoute
              onCancelRun={cancelRun}
              onRetryRun={retryRun}
              onScoreRun={scoreRun}
              runs={runs}
            />
          }
          path="/runs/:runId"
        />
        <Route
          element={
            <>
              <Cases
                cases={cases.length > 0 ? cases : [previewCase]}
                onCreateCase={createCase}
                onImportSwebench={importSwebench}
                onImportSwebenchPro={importSwebenchPro}
                onSeedTetris={seedTetris}
                onSelectCase={setSelectedCaseId}
                selectedCaseId={selectedCaseId}
              />
              {selectedCase !== null ? (
                <section className="panel">
                  <div className="panel-head">
                    <h2>Selected case</h2>
                    <span className="muted">{selectedCase.sync_state}</span>
                  </div>
                  <p>{selectedCase.description}</p>
                </section>
              ) : null}
            </>
          }
          path="/cases"
        />
        <Route element={<Skills />} path="/skills" />
        <Route element={<Improvements />} path="/improvements" />
        <Route element={<Compare runs={runs} />} path="/compare" />
        <Route element={<Guide />} path="/guide" />
        <Route element={<Settings onThemeChange={setTheme} theme={theme} />} path="/settings" />
        <Route element={<Navigate replace to="/" />} path="*" />
      </Route>
    </Routes>
  );
}
