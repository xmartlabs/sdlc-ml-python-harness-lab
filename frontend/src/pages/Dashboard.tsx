import type { CaseRecord, RunRecord } from "../types";

type DashboardProps = {
  cases: CaseRecord[];
  runs: RunRecord[];
  apiStatus: string;
};

function countByState(runs: RunRecord[], state: RunRecord["state"]): number {
  return runs.filter((run) => run.state === state).length;
}

export function Dashboard({ cases, runs, apiStatus }: DashboardProps) {
  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Dashboard</p>
          <h1>Run workbench</h1>
          <p className="lede">Operate local benchmark runs from the existing harness.</p>
        </div>
        <div className="status-chip">{apiStatus}</div>
      </header>

      <div className="metrics">
        <div className="metric">
          <span>Cases</span>
          <strong>{cases.length}</strong>
        </div>
        <div className="metric">
          <span>Runs</span>
          <strong>{runs.length}</strong>
        </div>
        <div className="metric">
          <span>Running</span>
          <strong>{countByState(runs, "running")}</strong>
        </div>
        <div className="metric">
          <span>Completed</span>
          <strong>{countByState(runs, "completed")}</strong>
        </div>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Operational notes</h2>
          <span className="muted">Preview-ready layout</span>
        </div>
        <div className="table">
          <div className="table-row table-head">
            <span>Signal</span>
            <span>Value</span>
            <span>Status</span>
          </div>
          <div className="table-row">
            <span>API</span>
            <span>/api</span>
            <span className="badge neutral">{apiStatus}</span>
          </div>
          <div className="table-row">
            <span>Case source</span>
            <span>DB first</span>
            <span className="badge neutral">Manual sync</span>
          </div>
          <div className="table-row">
            <span>Artifact source</span>
            <span>data/runs/&lt;run_id&gt;</span>
            <span className="badge neutral">Indexed</span>
          </div>
        </div>
      </section>
    </section>
  );
}
