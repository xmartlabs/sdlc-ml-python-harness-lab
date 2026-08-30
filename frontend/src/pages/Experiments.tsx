import { useEffect, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { Spinner } from "../components/Spinner";
import type {
  AggregateRecord,
  ConditionSummary,
  DeltaRecord,
  ExperimentRecord,
  ExperimentSummary
} from "../types";

const MODE_LABEL: Record<string, string> = {
  harness_evaluation: "Harness evaluation",
  cross_repo: "Cross-repo",
  discovery: "Discovery"
};

/** What each mode does and does not license — shown, not assumed known. */
const MODE_CAVEAT: Record<string, string> = {
  harness_evaluation:
    "Repository, case and model held fixed; the harness varies. The only mode that supports a causal claim — and only with repetitions.",
  cross_repo:
    "Harness and model held fixed; the repository varies. Read per repository: scores from different codebases are not equivalent conditions and must never be pooled.",
  discovery:
    "No control. Exploratory evidence, not experimental evidence — this mode describes, it does not demonstrate."
};

function formatNumber(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return Number(value.toPrecision(digits)).toString();
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`;
}

/** A mean is never shown without its spread. */
function spread(aggregate: AggregateRecord | undefined): string {
  if (!aggregate || aggregate.mean === null) {
    return "—";
  }
  if (aggregate.stdev === null) {
    return formatNumber(aggregate.mean);
  }
  return `${formatNumber(aggregate.mean)} ± ${formatNumber(aggregate.stdev, 2)}`;
}

function deltaLabel(delta: DeltaRecord): string {
  if (delta.absolute === null) {
    return "—";
  }
  const sign = delta.absolute > 0 ? "+" : "";
  return `${sign}${formatNumber(delta.absolute)}`;
}

/**
 * Improvement is read from the metric's declared direction, never from the sign.
 * A drop in `cost_usd` is a win; a drop in `test_pass_rate` is not.
 */
function deltaClass(delta: DeltaRecord): string {
  if (delta.absolute === null || delta.direction === "neutral") {
    return "";
  }
  const better =
    delta.direction === "higher_is_better" ? delta.absolute > 0 : delta.absolute < 0;
  return better ? "delta-better" : "delta-worse";
}

const MOVEMENT_MARK: Record<string, string> = {
  fixed: "✅",
  broken: "❌",
  unchanged: "=",
  incomplete: "·"
};

function ConditionTable({ conditions }: { conditions: ConditionSummary[] }) {
  const metricNames = [
    ...new Set(conditions.flatMap((condition) => Object.keys(condition.metrics)))
  ].sort();

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Metric</th>
          {conditions.map((condition) => (
            <th key={condition.label}>
              {condition.label}
              {condition.role === "control" && <span className="badge"> control</span>}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>
            <strong>Success rate</strong>
            <div className="muted small">fact — validation verdict</div>
          </td>
          {conditions.map((condition) => (
            <td key={condition.label}>
              {formatPercent(condition.success_rate)}
              <div className="muted small">
                {condition.scored_runs}/{condition.runs} scored
              </div>
            </td>
          ))}
        </tr>
        {metricNames.map((name) => (
          <tr key={name}>
            <td className="mono small">{name}</td>
            {conditions.map((condition) => (
              <td className="mono small" key={condition.label}>
                {spread(condition.metrics[name])}
                {condition.metrics[name]?.missing ? (
                  <div className="muted small">
                    {condition.metrics[name].missing} missing
                  </div>
                ) : null}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ExperimentDetail({ summary }: { summary: ExperimentSummary }) {
  return (
    <div className="stack">
      <div className="card">
        <h2>{summary.question}</h2>
        <p className="muted">
          {MODE_LABEL[summary.mode] ?? summary.mode} · {summary.repetitions} repetition
          {summary.repetitions === 1 ? "" : "s"} per arm
        </p>
        <p className="muted small">{MODE_CAVEAT[summary.mode]}</p>
        {summary.conditions.flatMap((condition) => condition.warnings).length > 0 && (
          <ul className="warning-list">
            {[
              ...new Set(summary.conditions.flatMap((condition) => condition.warnings))
            ].map((warning) => (
              <li key={warning}>⚠ {warning}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h3>Conditions</h3>
        <ConditionTable conditions={summary.conditions} />
      </div>

      {summary.comparisons.map((comparison) => (
        <div className="card" key={`${comparison.control}-${comparison.treatment}`}>
          <h3>
            {comparison.treatment} vs {comparison.control}
          </h3>
          <p className="muted">
            Success rate {formatPercent(comparison.success.control)} →{" "}
            {formatPercent(comparison.success.treatment)}
            {comparison.success.points !== null && (
              <>
                {" "}
                (<strong>{comparison.success.points > 0 ? "+" : ""}
                {comparison.success.points.toFixed(0)} pp</strong>)
              </>
            )}
          </p>

          <table className="data-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th>Δ</th>
                <th>Beyond noise?</th>
              </tr>
            </thead>
            <tbody>
              {comparison.deltas.map((delta) => (
                <tr key={delta.name}>
                  <td className="mono small">{delta.name}</td>
                  <td className="mono small">{formatNumber(delta.control)}</td>
                  <td className="mono small">{formatNumber(delta.treatment)}</td>
                  <td className={`mono small ${deltaClass(delta)}`}>
                    {deltaLabel(delta)}
                  </td>
                  <td className="small">
                    {delta.exceeds_spread === null
                      ? "unknown"
                      : delta.exceeds_spread
                        ? "yes"
                        : "no — within run-to-run variance"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h4>Case matrix</h4>
          <p className="muted small">
            Where the treatment helps, and where it does not. An average of "+8
            points" can be "fixed four cases, broke two" — only this says which.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Case</th>
                <th>{comparison.control}</th>
                <th>{comparison.treatment}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {comparison.case_matrix.map((row) => (
                <tr key={row.case_id}>
                  <td className="mono small">{row.case_id}</td>
                  <td>{row.control === null ? "·" : row.control ? "✅" : "❌"}</td>
                  <td>{row.treatment === null ? "·" : row.treatment ? "✅" : "❌"}</td>
                  <td className="small">
                    {MOVEMENT_MARK[row.movement]} {row.movement}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function Experiments() {
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [summary, setSummary] = useState<ExperimentSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listExperiments()
      .then((rows) => {
        setExperiments(rows);
        setError(null);
      })
      .catch((loadError: unknown) =>
        setError(
          loadError instanceof Error ? loadError.message : "Unable to load experiments"
        )
      )
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    if (selected === null) {
      setSummary(null);
      return;
    }
    api
      .summarizeExperiment(selected)
      .then(setSummary)
      .catch((summaryError: unknown) =>
        setError(
          summaryError instanceof Error
            ? summaryError.message
            : "Unable to summarize experiment"
        )
      );
  }, [selected]);

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>Experiments</h1>
          <p className="muted">
            A stated question, declared arms, and the runs that answer it.
          </p>
        </div>
      </header>

      <HelpPanel title="What an experiment is, and what a batch is not">
        <p>
          An experiment states its <strong>question</strong> before anything runs, and
          declares its arms up front — one control, the rest treatments. The lab
          checks the design against the mode and refuses one that cannot answer the
          question it claims to: a comparison that varies both the harness and the
          repository produces a number that cannot be attributed to either.
        </p>
        <p className="muted">
          A batch is still the right tool for an ad-hoc sweep. The difference is that
          an experiment makes a claim, so it has to earn it.
        </p>
      </HelpPanel>

      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="card">
        <h2>All experiments</h2>
        {isLoading ? (
          <Spinner />
        ) : experiments.length === 0 ? (
          <EmptyState
            description="Create one from a finding, or with POST /api/experiments. An experiment needs a question, a mode, cases and at least one control condition."
            title="No experiments yet"
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Id</th>
                <th>Question</th>
                <th>Mode</th>
                <th>Arms</th>
                <th>Runs</th>
                <th>Reps</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {experiments.map((experiment) => (
                <tr key={experiment.experiment_id}>
                  <td>
                    <strong>{experiment.experiment_id}</strong>
                    <div className="muted small">{experiment.status}</div>
                  </td>
                  <td className="small">{experiment.question}</td>
                  <td className="small">
                    {MODE_LABEL[experiment.mode] ?? experiment.mode}
                  </td>
                  <td>{experiment.condition_count ?? "—"}</td>
                  <td>{experiment.run_count ?? "—"}</td>
                  <td>
                    {experiment.repetitions}
                    {experiment.repetitions < 5 && (
                      <div className="muted small">exploratory</div>
                    )}
                  </td>
                  <td>
                    <button
                      onClick={() =>
                        setSelected(
                          selected === experiment.experiment_id
                            ? null
                            : experiment.experiment_id
                        )
                      }
                      type="button"
                    >
                      {selected === experiment.experiment_id ? "Hide" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {summary !== null && <ExperimentDetail summary={summary} />}
    </section>
  );
}
