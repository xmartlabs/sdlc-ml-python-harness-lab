import { Link, useParams } from "react-router-dom";

import { RunDetail } from "../pages/RunDetail";
import type { RunRecord } from "../types";

type RunDetailRouteProps = {
  runs: RunRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
};

export function RunDetailRoute({ runs, onCancelRun, onRetryRun, onScoreRun }: RunDetailRouteProps) {
  const { runId } = useParams();
  const run = runId ? runs.find((item) => item.run_id === runId) ?? null : null;

  if (run === null) {
    return (
      <section className="run-detail-page">
        <Link className="run-detail-back" to="/runs">
          ← Runs
        </Link>
        <div className="empty-state">
          <h2>Run not found</h2>
          <p className="muted">
            {runId ? `No run "${runId}" is loaded.` : "No run selected."} It may have
            been deleted, or the list is still loading.
          </p>
        </div>
      </section>
    );
  }

  return (
    <RunDetail
      onCancelRun={onCancelRun}
      onRetryRun={onRetryRun}
      onScoreRun={onScoreRun}
      run={run}
    />
  );
}
