import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api, SESSION_EXPIRED_EVENT } from "./api";
import { CaseEditor } from "./components/CaseEditor";
import { Layout } from "./components/Layout";
import { RunDetailRoute } from "./components/RunDetailRoute";
import { Cases } from "./pages/Cases";
import { Compare } from "./pages/Compare";
import { Experiments } from "./pages/Experiments";
import { Findings } from "./pages/Findings";
import { Repositories } from "./pages/Repositories";
import { Dashboard } from "./pages/Dashboard";
import { Guide } from "./pages/Guide";
import { Health } from "./pages/Health";
import { Improvements } from "./pages/Improvements";
import { Login } from "./pages/Login";
import { Runs } from "./pages/Runs";
import { Settings } from "./pages/Settings";
import { Skills } from "./pages/Skills";
import { applyTheme, getStoredTheme } from "./theme";
import type { ThemeMode } from "./theme";
import type {
  CaseCreatePayload,
  CaseRecord,
  RunBatchPayload,
  RunCreatePayload,
  RunRecord,
  SessionState
} from "./types";

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
  // `null` while the session probe is in flight, so the app never flashes the
  // login form at an already-authenticated operator.
  const [session, setSession] = useState<SessionState | null>(null);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    let active = true;

    api
      .readSession()
      .then((state) => {
        if (active) {
          setSession(state);
        }
      })
      .catch(() => {
        // API unreachable: fall back to preview mode rather than a dead form.
        if (active) {
          setSession({ authenticated: true, auth_enabled: false });
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const expire = () => setSession({ authenticated: false, auth_enabled: true });
    window.addEventListener(SESSION_EXPIRED_EVENT, expire);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, expire);
  }, []);

  useEffect(() => {
    let active = true;
    if (session === null || !session.authenticated) {
      return () => {
        active = false;
      };
    }

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
  }, [session]);

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

  const upsertCase = (item: CaseRecord) => {
    setCases((current) => [item, ...current.filter((row) => row.case_id !== item.case_id)]);
    setSelectedCaseId(item.case_id);
  };

  const removeCase = (caseId: string) => {
    setCases((current) => current.filter((row) => row.case_id !== caseId));
    setSelectedCaseId((current) => (current === caseId ? null : current));
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

  /**
   * Queue a whole cross-product, then re-read the list.
   *
   * Unlike `createRun` this does not splice the new rows in from the response:
   * a batch returns ids only, and a batch large enough to be worth the feature
   * is large enough that a refetch is cheaper than N optimistic inserts.
   */
  const createRunBatch = async (payload: RunBatchPayload) => {
    const result = await api.createRunBatch(payload);
    setRuns(await api.listRuns());
    return result;
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

  const continueRun = async (runId: string, prompt: string, maxSteps?: number) => {
    const created = await api.continueRun(runId, prompt, maxSteps);
    setRuns((current) => [created, ...current.filter((run) => run.run_id !== created.run_id)]);
    return created;
  };

  const deleteRun = async (runId: string, force = false) => {
    await api.deleteRun(runId, force);
    setRuns((current) => current.filter((run) => run.run_id !== runId));
  };

  if (session === null) {
    return null;
  }

  if (!session.authenticated) {
    return (
      <Login
        onAuthenticated={() => setSession({ authenticated: true, auth_enabled: true })}
      />
    );
  }

  return (
    <Routes>
      <Route
        element={
          <Layout
            authEnabled={session.auth_enabled}
            onLogout={() => {
              void api.logout().then(() => setSession({ authenticated: false, auth_enabled: true }));
            }}
            onUpdateRun={updateRun}
            runs={runs}
          />
        }
      >
        <Route element={<Dashboard apiStatus={apiStatus} cases={cases} runs={runs} />} path="/" />
        <Route
          element={
            <Runs
              cases={cases}
              onCancelRun={cancelRun}
              onCloneRun={cloneRun}
              onCreateRun={createRun}
              onCreateRunBatch={createRunBatch}
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
              onContinueRun={continueRun}
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
                <CaseEditor
                  caseRecord={selectedCase}
                  key={selectedCase.case_id}
                  onCloned={upsertCase}
                  onDeleted={removeCase}
                  onSaved={upsertCase}
                />
              ) : null}
            </>
          }
          path="/cases"
        />
        <Route element={<Skills />} path="/skills" />
        <Route element={<Improvements />} path="/improvements" />
        <Route element={<Experiments />} path="/experiments" />
        <Route element={<Findings />} path="/findings" />
        <Route element={<Repositories />} path="/repositories" />
        <Route element={<Compare runs={runs} />} path="/compare" />
        <Route element={<Guide />} path="/guide" />
        <Route element={<Health />} path="/health" />
        <Route element={<Settings onThemeChange={setTheme} theme={theme} />} path="/settings" />
        <Route element={<Navigate replace to="/" />} path="*" />
      </Route>
    </Routes>
  );
}
