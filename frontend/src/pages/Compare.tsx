import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { HelpPanel } from "../components/HelpPanel";
import { StateBadge } from "../components/StateBadge";
import type { RunRecord, ValidationRecord } from "../types";

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
};

// One comparison row: how to read the display + optional numeric for best-cell.
type Metric = {
  label: string;
  display: (col: Column) => string;
  numeric?: (col: Column) => number | null;
  better?: "high" | "low";
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

  const columns: Column[] = useMemo(
    () =>
      selectedIds
        .map((id) => runs.find((r) => r.run_id === id))
        .filter((r): r is RunRecord => Boolean(r))
        .map((run) => ({
          run,
          flags: parseFlags(run.command),
          validation: validations[run.run_id] ?? null,
        })),
    [selectedIds, runs, validations]
  );


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
          <p className="eyebrow">Compare</p>
          <h1>Run comparison</h1>
          <p className="lede">
            Line up runs of the same case across models or harness conditions —
            objective score, cost, and effort side by side.
          </p>
        </div>
      </header>

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
                {METRICS.map((metric) => {
                  const winner = bestIndex(metric, columns);
                  return (
                    <tr key={metric.label}>
                      <th scope="row">{metric.label}</th>
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

          <p className="muted">
            Looking for the aggregated ranking by condition (% resolved, harness,
            version)? It moved to the <Link to="/">Dashboard</Link>.
          </p>
        </>
      )}
    </section>
  );
}
