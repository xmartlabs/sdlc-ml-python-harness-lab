import type { RunRecord } from "../types";

type CompareProps = {
  runs: RunRecord[];
};

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

      <div className="compare-grid">
        <section className="panel">
          <div className="panel-head">
            <h2>{left ? left.run_id : "Run A"}</h2>
            <span className="muted">{left ? left.state : "No run selected"}</span>
          </div>
          <p className="muted">{left ? left.command.join(" ") : "Pick the first run to compare."}</p>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>{right ? right.run_id : "Run B"}</h2>
            <span className="muted">{right ? right.state : "No run selected"}</span>
          </div>
          <p className="muted">{right ? right.command.join(" ") : "Pick the second run to compare."}</p>
        </section>
      </div>
    </section>
  );
}
