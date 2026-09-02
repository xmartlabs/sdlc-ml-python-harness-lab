import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { HelpPanel } from "../components/HelpPanel";
import { StateBadge } from "../components/StateBadge";
import { IconAlert } from "../components/icons";
import type { RunRecord, ScoreRecord, ValidationRecord } from "../types";

type CompareProps = {
  runs: RunRecord[];
};

// The runner command is built by BenchRunAdapter with a stable flag layout, so
// case / prompt / models can be recovered from it without a config endpoint.
type RunFlags = {
  caseId: string;
  prompt: string;
  plannerModel: string;
  executorModel: string;
  maxSteps: string;
  ablation: number;
};

function flagValue(command: string[], flag: string): string {
  const idx = command.indexOf(flag);
  return idx >= 0 && idx + 1 < command.length ? command[idx + 1] : "";
}

function parseFlags(command: string[]): RunFlags {
  const ablation =
    command.filter((c) => c === "--disable-skill" || c === "--extra-skill").length;
  return {
    caseId: flagValue(command, "--case") || "—",
    prompt: flagValue(command, "--prompt") || "—",
    plannerModel: flagValue(command, "--planner-model") || "none",
    executorModel: flagValue(command, "--executor-model") || "—",
    maxSteps: flagValue(command, "--max-steps") || "—",
    ablation,
  };
}

function fmt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "number" ? String(value) : value;
}

type Column = {
  run: RunRecord;
  flags: RunFlags;
  validation: ValidationRecord | null;
  // Registry scores, keyed by name. These carry direction and provenance, so a
  // metric the comparison has never seen still knows which way is better.
  scores: Record<string, ScoreRecord>;
};

// One comparison row: how to read the display + optional numeric for best-cell.
type Metric = {
  label: string;
  display: (col: Column) => string;
  numeric?: (col: Column) => number | null;
  better?: "high" | "low";
  // Set for registry rows: a fact, an observation and a judgement are three
  // different kinds of claim, and a comparison table is exactly where that
  // distinction is easiest to lose.
  provenance?: string;
};

const METRICS: Metric[] = [
  { label: "Case", display: (c) => c.flags.caseId },
  { label: "Executor model", display: (c) => c.flags.executorModel },
  { label: "Planner model", display: (c) => c.flags.plannerModel },
  { label: "Prompt variant", display: (c) => c.flags.prompt },
  { label: "Max steps", display: (c) => c.flags.maxSteps },
  {
    label: "Ablation",
    display: (c) => (c.flags.ablation ? `${c.flags.ablation} skill(s)` : "none"),
  },
  {
    label: "Objective",
    display: (c) =>
      c.validation?.objective?.summary ??
      (c.validation?.objective?.passed === true
        ? "pass"
        : c.validation?.objective?.passed === false
          ? "fail"
          : "—"),
    numeric: (c) =>
      c.validation?.objective?.passed === true
        ? 1
        : c.validation?.objective?.passed === false
          ? 0
          : null,
    better: "high",
  },
  {
    label: "Final score",
    display: (c) => fmt(c.run.score),
    numeric: (c) => c.run.score ?? null,
    better: "high",
  },
  {
    label: "Composite",
    display: (c) => fmt(c.validation?.composite),
    numeric: (c) => c.validation?.composite ?? null,
    better: "high",
  },
  {
    label: "Tokens",
    display: (c) => fmt(c.validation?.cost?.total_tokens),
    numeric: (c) => c.validation?.cost?.total_tokens ?? null,
    better: "low",
  },
  {
    label: "Cost (USD)",
    display: (c) => fmt(c.validation?.cost?.cost_usd),
    numeric: (c) => c.validation?.cost?.cost_usd ?? null,
    better: "low",
  },
  {
    label: "Time (min)",
    display: (c) => fmt(c.validation?.cost?.time_minutes),
    numeric: (c) => c.validation?.cost?.time_minutes ?? null,
    better: "low",
  },
  {
    label: "Steps",
    display: (c) => fmt(c.validation?.effort?.steps),
    numeric: (c) => c.validation?.effort?.steps ?? null,
    better: "low",
  },
  {
    label: "Interventions",
    display: (c) => fmt(c.run.interventions),
    numeric: (c) => c.run.interventions ?? null,
    better: "low",
  },
];

// Index of the winning column for a metric (or null when there is no clear win).
function bestIndex(metric: Metric, columns: Column[]): number | null {
  if (!metric.numeric || !metric.better) return null;
  let best: number | null = null;
  let bestVal = metric.better === "high" ? -Infinity : Infinity;
  const nums = columns.map((c) => metric.numeric!(c));
  const present = nums.filter((n): n is number => n !== null);
  if (present.length < 2 || new Set(present).size === 1) return null;
  nums.forEach((n, i) => {
    if (n === null) return;
    if ((metric.better === "high" && n > bestVal) || (metric.better === "low" && n < bestVal)) {
      bestVal = n;
      best = i;
    }
  });
  return best;
}

export function Compare({ runs }: CompareProps) {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [scoresByRun, setScoresByRun] = useState<
    Record<string, Record<string, ScoreRecord>>
  >({});
  // name → direction, from the registry. Not stored on a score row: direction
  // is a property of the definition, and duplicating it per value is how the
  // two drift apart.
  const [directions, setDirections] = useState<Record<string, string>>({});

  useEffect(() => {
    api
      .getScoreDefinitions()
      .then((payload) => {
        const map: Record<string, string> = {};
        for (const group of Object.values(payload.categories)) {
          for (const definition of group) {
            map[definition.name] = definition.direction;
          }
        }
        setDirections(map);
      })
      .catch(() => setDirections({}));
  }, []);
  const [validations, setValidations] = useState<Record<string, ValidationRecord | null>>({});

  // Default to the two most recent runs once the list arrives.
  useEffect(() => {
    if (selectedIds.length === 0 && runs.length > 0) {
      setSelectedIds(runs.slice(0, Math.min(2, runs.length)).map((r) => r.run_id));
    }
  }, [runs, selectedIds.length]);

  // Fetch validation for any selected run we have not loaded yet.
  useEffect(() => {
    let active = true;
    for (const id of selectedIds) {
      if (id in validations) continue;
      api
        .getValidation(id)
        .then((v) => active && setValidations((prev) => ({ ...prev, [id]: v })))
        .catch(() => active && setValidations((prev) => ({ ...prev, [id]: null })));
    }
    return () => {
      active = false;
    };
  }, [selectedIds, validations]);

  // Same, for the registry scores.
  useEffect(() => {
    let active = true;
    for (const id of selectedIds) {
      if (id in scoresByRun) continue;
      api
        .listRunScores(id)
        .then(
          (rows) =>
            active &&
            setScoresByRun((prev) => ({
              ...prev,
              [id]: Object.fromEntries(rows.map((row) => [row.name, row]))
            }))
        )
        .catch(
          () => active && setScoresByRun((prev) => ({ ...prev, [id]: {} }))
        );
    }
    return () => {
      active = false;
    };
  }, [selectedIds, scoresByRun]);

  const columns: Column[] = useMemo(
    () =>
      selectedIds
        .map((id) => runs.find((r) => r.run_id === id))
        .filter((r): r is RunRecord => Boolean(r))
        .map((run) => ({
          run,
          flags: parseFlags(run.command),
          validation: validations[run.run_id] ?? null,
          scores: scoresByRun[run.run_id] ?? {}
        })),
    [selectedIds, runs, validations, scoresByRun]
  );

  // Every registry metric present on any selected run, so a comparison never
  // silently drops a score one arm recorded and the other did not.
  const scoreRows: Metric[] = useMemo(() => {
    const seen = new Map<string, ScoreRecord>();
    for (const column of columns) {
      for (const score of Object.values(column.scores)) {
        if (!seen.has(score.name)) seen.set(score.name, score);
      }
    }
    return [...seen.values()]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((sample) => ({
        label: `${sample.name}`,
        provenance: sample.provenance,
        display: (column: Column) => {
          const score = column.scores[sample.name];
          if (!score) return "—";
          if (score.value_bool !== null) return score.value_bool ? "pass" : "fail";
          if (score.value_num !== null) {
            return String(Number(score.value_num.toPrecision(4)));
          }
          return score.value_text ?? "—";
        },
        numeric: (column: Column) => {
          const score = column.scores[sample.name];
          if (!score) return null;
          if (score.value_bool !== null) return score.value_bool ? 1 : 0;
          return score.value_num;
        },
        // Direction comes from the registry, never from the sign: a drop in
        // cost is a win, a drop in pass rate is not.
        better:
          directions[sample.name] === "higher_is_better"
            ? ("high" as const)
            : directions[sample.name] === "lower_is_better"
              ? ("low" as const)
              : undefined
      }));
  }, [columns, directions]);


  const setColumn = (index: number, runId: string) =>
    setSelectedIds((prev) => prev.map((id, i) => (i === index ? runId : id)));
  const addColumn = () => {
    const next = runs.find((r) => !selectedIds.includes(r.run_id)) ?? runs[0];
    if (next) setSelectedIds((prev) => [...prev, next.run_id]);
  };
  const removeColumn = (index: number) =>
    setSelectedIds((prev) => prev.filter((_, i) => i !== index));

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Advanced</p>
          <h1>Advanced Compare</h1>
          <p className="lede">
            Line up runs of the same case across models or harness conditions —
            objective score, cost, and effort side by side.
          </p>
        </div>
      </header>

      <div className="callout callout--warning">
        <IconAlert size={16} />
        <p>
          <strong>Ad-hoc comparison.</strong> No causal claim is implied unless the
          conditions satisfy an experiment design. These runs were not necessarily
          matched on repository, model or repetition count, so a difference here can
          come from anything that differed between them. To make a claim, run an{" "}
          <Link to="/evaluate/new">experiment</Link>.
        </p>
      </div>

      <HelpPanel title="How to read this comparison" docHref="docs/guide-web-ui.md">
        <ol className="skills-howto">
          <li>Pick 2–4 runs of the <strong>same case</strong> (different models or
            skill conditions) from the selectors.</li>
          <li>The metrics table highlights the best cell per row — higher is better
            for scores, lower for tokens / cost / time / steps.</li>
          <li><strong>Objective</strong> comes from automatic validation; SWE-bench
            resolves to pass/fail, other cases show their check summary.</li>
          <li>The aggregated <strong>Leaderboard</strong> (% resolved per harness +
            model + prompt + harness version) lives on the Dashboard.</li>
        </ol>
      </HelpPanel>

      {runs.length === 0 ? (
        <EmptyState
          description="Create at least one run from the Runs page to start comparing outcomes."
          title="No runs to compare yet."
        />
      ) : (
        <>
          <div className="compare-picker">
            {selectedIds.map((id, index) => (
              <div className="compare-picker-col" key={index}>
                <select value={id} onChange={(e) => setColumn(index, e.target.value)}>
                  {runs.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.run_id}
                    </option>
                  ))}
                </select>
                {selectedIds.length > 2 ? (
                  <button className="ghost" onClick={() => removeColumn(index)} type="button">
                    Remove
                  </button>
                ) : null}
              </div>
            ))}
            {selectedIds.length < 4 && selectedIds.length < runs.length ? (
              <button className="ghost" onClick={addColumn} type="button">
                + Add run
              </button>
            ) : null}
          </div>

          <div className="table-scroll">
            <table className="compare-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  {columns.map((col) => (
                    <th key={col.run.run_id}>
                      <div className="compare-col-head">
                        <span>{col.run.run_id}</span>
                        <StateBadge state={col.run.state} />
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...METRICS, ...scoreRows].map((metric) => {
                  const winner = bestIndex(metric, columns);
                  return (
                    <tr key={metric.label}>
                      <th scope="row">
                        {metric.label}
                        {metric.provenance && (
                          <div
                            className={`small provenance-${metric.provenance}`}
                          >
                            {metric.provenance}
                          </div>
                        )}
                      </th>
                      {columns.map((col, i) => (
                        <td className={winner === i ? "compare-best" : undefined} key={col.run.run_id}>
                          {metric.display(col)}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {scoreRows.length > 0 && (
            <p className="muted small">
              Rows below the divider come from the score registry. Their
              &ldquo;best&rdquo; cell follows each metric&apos;s <em>declared
              direction</em>, not the sign — a lower cost wins, a lower pass rate
              does not. A judgement is a model&apos;s opinion and is marked as
              one; it should not be read beside a fact as the same kind of claim.
            </p>
          )}

          <p className="muted">
            Looking for the aggregated ranking by condition (% resolved, harness,
            version)? It moved to the <Link to="/advanced/benchmarks">Benchmarks</Link>.
          </p>
        </>
      )}
    </section>
  );
}
