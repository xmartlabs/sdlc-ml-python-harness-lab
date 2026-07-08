import { StateBadge } from "../components/StateBadge";
import { EmptyState } from "../components/EmptyState";
import type { RunRecord } from "../types";

type CompareProps = {
  runs: RunRecord[];
};

function RunColumn({ run, label }: { run: RunRecord | undefined; label: string }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{run ? run.run_id : label}</h2>
        {run ? <StateBadge state={run.state} /> : null}
      </div>
      {run ? (
        <>
          <p className="muted">{run.command.join(" ")}</p>
          <div className="detail-grid">
            <div>
              <span>Score</span>
              <strong>{run.score ?? "—"}</strong>
            </div>
            <div>
              <span>Interventions</span>
              <strong>{run.interventions ?? "—"}</strong>
            </div>
          </div>
        </>
      ) : (
        <p className="muted">Pick a run from the Runs page to compare it here.</p>
      )}
    </section>
  );
}

export function Compare({ runs }: CompareProps) {
  const [left, right] = runs;

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Compare</p>
          <h1>Two-run comparison</h1>
          <p className="lede">Compare outcome, state, and command shape across runs.</p>
        </div>
      </header>

      {runs.length === 0 ? (
        <EmptyState
          description="Create at least one run from the Runs page to start comparing outcomes."
          title="No runs to compare yet."
        />
      ) : (
        <div className="compare-grid">
          <RunColumn label="Run A" run={left} />
          <RunColumn label="Run B" run={right} />
        </div>
      )}
    </section>
  );
}
