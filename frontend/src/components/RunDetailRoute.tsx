import { Link, useParams } from "react-router-dom";

import { defineMessages, useMessages } from "../i18n";
import { RunDetail } from "../pages/RunDetail";
import type { RunRecord } from "../types";

const MESSAGES = defineMessages({
  en: {
    backToRuns: "← Runs",
    notFoundTitle: "Run not found",
    noRunLoaded: (runId: string) => `No run "${runId}" is loaded.`,
    noRunSelected: "No run selected.",
    maybeDeletedOrLoading: " It may have been deleted, or the list is still loading."
  },
  es: {
    backToRuns: "← Corridas",
    notFoundTitle: "Corrida no encontrada",
    noRunLoaded: (runId: string) => `No hay ninguna corrida "${runId}" cargada.`,
    noRunSelected: "No se seleccionó ninguna corrida.",
    maybeDeletedOrLoading: " Puede haber sido eliminada, o la lista todavía está cargando."
  }
});

type RunDetailRouteProps = {
  runs: RunRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
  onContinueRun: (runId: string, prompt: string, maxSteps?: number) => Promise<RunRecord>;
};

export function RunDetailRoute({
  runs,
  onCancelRun,
  onRetryRun,
  onScoreRun,
  onContinueRun
}: RunDetailRouteProps) {
  const { runId } = useParams();
  const m = useMessages(MESSAGES);
  const run = runId ? runs.find((item) => item.run_id === runId) ?? null : null;

  if (run === null) {
    return (
      <section className="run-detail-page">
        <Link className="run-detail-back" to="/advanced/runs">
          {m.backToRuns}
        </Link>
        <div className="empty-state">
          <h2>{m.notFoundTitle}</h2>
          <p className="muted">
            {runId ? m.noRunLoaded(runId) : m.noRunSelected}
            {m.maybeDeletedOrLoading}
          </p>
        </div>
      </section>
    );
  }

  return (
    <RunDetail
      onCancelRun={onCancelRun}
      onContinueRun={onContinueRun}
      onRetryRun={onRetryRun}
      onScoreRun={onScoreRun}
      run={run}
      runs={runs}
    />
  );
}
