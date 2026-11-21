import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../api";
import { AdhocRunReview } from "../components/AdhocRunReview";
import { ErrorBanner } from "../components/ErrorBanner";
import { StateBadge } from "../components/StateBadge";
import { Spinner } from "../components/Spinner";
import { armFor } from "../components/guide/guideData";
import { HarnessModelChooser } from "../components/guide/HarnessModelChooser";
import { formatRunDate } from "../dates";
import { defineMessages, useMessages } from "../i18n";
import type { AdhocRunRecord, AdhocTaskDetail as AdhocTaskDetailType } from "../types";

/**
 * One ad hoc task (ADR-0095): what it asks for, the folder it runs against,
 * every run of it side by side, and the review panel for whichever run is
 * selected.
 */

const LIVE_RUN_STATES = new Set(["pending", "running", "scoring", "auditing"]);
const LIVE_APPLY_STATES = new Set(["applying", "undoing"]);
const POLL_MS = 2000;

const MESSAGES = defineMessages({
  en: {
    loading: "Loading task…",
    notFound: "Task not found.",
    prompt: "Prompt",
    folder: "Folder",
    baseCommit: "Base commit",
    headMoved: (n: number) =>
      `Your repo moved ${n} commits since this task was created: applying may conflict.`,
    runWith: "Run with…",
    launch: "Run",
    launching: "Launching…",
    egress: {
      cloud: (provider: string) => `Your code will be sent to ${provider}.`,
      local: "Runs on your local model: nothing leaves this machine."
    },
    runs: {
      title: "Runs",
      colHarness: "Harness",
      colModel: "Model",
      colEffort: "Effort",
      colState: "State",
      colTokens: "Tokens",
      colCost: "Cost",
      colMinutes: "Minutes",
      colFiles: "Files",
      colDecision: "Decision",
      colApply: "Apply",
      none: "—",
      unknownCost: "unknown (subscription)",
      localCost: "$0 (local)",
      estimated: (value: string) => `~$${value} estimated`,
      decisions: {
        pending: "pending",
        approved: "approved",
        rejected: "rejected",
        discarded: "discarded"
      },
      applyStates: {
        none: "—",
        applying: "applying",
        applied: "applied",
        undoing: "undoing",
        undone: "undone",
        failed: "failed"
      }
    },
    errors: {
      load: "Could not load this task.",
      launch: "Could not launch the run."
    }
  },
  es: {
    loading: "Cargando tarea…",
    notFound: "No se encontró la tarea.",
    prompt: "Prompt",
    folder: "Carpeta",
    baseCommit: "Commit base",
    headMoved: (n: number) =>
      `Tu repo avanzó ${n} commits desde que se creó esta tarea: aplicar puede generar conflictos.`,
    runWith: "Correr con…",
    launch: "Correr",
    launching: "Lanzando…",
    egress: {
      cloud: (provider: string) => `Tu código se va a enviar a ${provider}.`,
      local: "Corre en tu modelo local: nada sale de esta máquina."
    },
    runs: {
      title: "Corridas",
      colHarness: "Arnés",
      colModel: "Modelo",
      colEffort: "Esfuerzo",
      colState: "Estado",
      colTokens: "Tokens",
      colCost: "Costo",
      colMinutes: "Minutos",
      colFiles: "Archivos",
      colDecision: "Decisión",
      colApply: "Aplicación",
      none: "—",
      unknownCost: "desconocido (suscripción)",
      localCost: "$0 (local)",
      estimated: (value: string) => `~$${value} estimado`,
      decisions: {
        pending: "pendiente",
        approved: "aprobada",
        rejected: "rechazada",
        discarded: "descartada"
      },
      applyStates: {
        none: "—",
        applying: "aplicando",
        applied: "aplicada",
        undoing: "deshaciendo",
        undone: "deshecha",
        failed: "falló"
      }
    },
    errors: {
      load: "No se pudo cargar esta tarea.",
      launch: "No se pudo lanzar la corrida."
    }
  }
});

function isCloudModel(model: string): boolean {
  return !/^(lmstudio|ollama)\//.test(model);
}

function costCell(run: AdhocRunRecord, m: (typeof MESSAGES)["en"]): string {
  if (run.cost_usd != null) return `$${run.cost_usd.toFixed(4)}`;
  if (run.estimated_cost_usd != null) return m.runs.estimated(run.estimated_cost_usd.toFixed(2));
  if (run.billing_mode === "subscription") return m.runs.unknownCost;
  if (run.cost_source === "local" || run.cost_source === "self_hosted") return m.runs.localCost;
  return m.runs.none;
}

export function AdhocTaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const m = useMessages(MESSAGES);

  const [detail, setDetail] = useState<AdhocTaskDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [arm, setArm] = useState("");
  const [launching, setLaunching] = useState(false);
  const runPanelRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(() => {
    if (!taskId) return;
    api
      .getAdhocTask(taskId)
      .then((result) => {
        setDetail(result);
        setSelectedRunId((current) => current ?? result.runs[0]?.run_id ?? null);
      })
      .catch(() => setError(m.errors.load));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while anything on this task is still moving.
  useEffect(() => {
    if (!detail) return undefined;
    const live =
      detail.runs.some((run) => LIVE_RUN_STATES.has(run.state) || LIVE_APPLY_STATES.has(run.apply_state)) ?? false;
    if (!live) return undefined;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [detail, load]);

  const launch = async () => {
    if (!taskId || !arm) return;
    const picked = armFor(arm);
    setLaunching(true);
    setError(null);
    try {
      const run = await api.launchAdhocRun(taskId, {
        harness: picked.harness,
        executor_model: picked.model,
        executor_effort: picked.effort || undefined
      });
      setDetail((current) => (current ? { ...current, runs: [run, ...current.runs] } : current));
      setSelectedRunId(run.run_id);
    } catch (launchError) {
      setError(launchError instanceof Error ? launchError.message : m.errors.launch);
    } finally {
      setLaunching(false);
    }
  };

  const onRunChanged = (updated: AdhocRunRecord) => {
    setDetail((current) =>
      current
        ? { ...current, runs: current.runs.map((run) => (run.run_id === updated.run_id ? updated : run)) }
        : current
    );
  };

  if (error && !detail) {
    return <ErrorBanner message={error} />;
  }
  if (!detail) {
    return <Spinner />;
  }

  const { task, runs, source } = detail;
  const selectedRun = runs.find((run) => run.run_id === selectedRunId) ?? runs[0] ?? null;
  const pickedArm = arm ? armFor(arm) : null;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{task.title || task.task_id}</h1>
          <p className="mono small">
            {m.folder}: {task.host_path} · {task.branch} · {m.baseCommit} {task.base_commit.slice(0, 8)}
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {(source.head_moved_by ?? 0) > 0 ? (
        <div className="callout callout--warning">
          <p>{m.headMoved(source.head_moved_by ?? 0)}</p>
        </div>
      ) : null}

      <details className="card">
        <summary>{m.prompt}</summary>
        <p style={{ whiteSpace: "pre-wrap" }}>{task.prompt}</p>
      </details>

      <div className="card" ref={runPanelRef}>
        <h2>{m.runWith}</h2>
        <HarnessModelChooser onPick={setArm} value={arm} />
        {pickedArm ? (
          <p className="muted small">
            {isCloudModel(pickedArm.model)
              ? m.egress.cloud(pickedArm.harness)
              : m.egress.local}
          </p>
        ) : null}
        <div className="button-row">
          <button className="primary" disabled={!arm || launching} onClick={() => void launch()} type="button">
            {launching ? m.launching : m.launch}
          </button>
        </div>
      </div>

      <div className="card">
        <h2>{m.runs.title}</h2>
        {runs.length === 0 ? (
          <p className="muted">{m.runs.none}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{m.runs.colHarness}</th>
                <th>{m.runs.colModel}</th>
                <th>{m.runs.colEffort}</th>
                <th>{m.runs.colState}</th>
                <th>{m.runs.colTokens}</th>
                <th>{m.runs.colCost}</th>
                <th>{m.runs.colMinutes}</th>
                <th>{m.runs.colFiles}</th>
                <th>{m.runs.colDecision}</th>
                <th>{m.runs.colApply}</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  className={run.run_id === selectedRun?.run_id ? "selectable active" : "selectable"}
                  key={run.run_id}
                  onClick={() => setSelectedRunId(run.run_id)}
                  style={{ cursor: "pointer" }}
                >
                  <td>{run.harness}</td>
                  <td className="mono small">{run.executor_model}</td>
                  <td>{run.executor_effort || m.runs.none}</td>
                  <td>
                    <StateBadge state={run.state} />
                  </td>
                  <td>{run.billed_tokens != null ? run.billed_tokens.toLocaleString() : m.runs.none}</td>
                  <td>{costCell(run, m)}</td>
                  <td>{run.minutes != null ? run.minutes.toFixed(1) : m.runs.none}</td>
                  <td className="small">
                    {run.files_changed ?? 0} ({"+"}
                    {run.lines_added ?? 0}/{"-"}
                    {run.lines_removed ?? 0})
                  </td>
                  <td>
                    <span className="badge">{m.runs.decisions[run.decision]}</span>
                  </td>
                  <td>
                    <span className="badge">{m.runs.applyStates[run.apply_state]}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedRun ? (
        <AdhocRunReview
          onChanged={onRunChanged}
          onRunAgain={() => runPanelRef.current?.scrollIntoView({ behavior: "smooth" })}
          run={selectedRun}
        />
      ) : null}

      <p className="muted small">
        {formatRunDate(task.created_at)} · {formatRunDate(task.updated_at)}
      </p>
    </section>
  );
}
