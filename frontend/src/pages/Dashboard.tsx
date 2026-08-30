import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Link } from "react-router-dom";

import type {
  CaseRecord,
  LeaderboardRunRow,
  OfficialReferenceRow,
  OverviewReport,
  RunRecord,
  ScoreAggregate
} from "../types";

type DashboardProps = {
  cases: CaseRecord[];
  runs: RunRecord[];
  apiStatus: string;
};

const ALL = "all";

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(0)}%`;
}

function usd(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `$${value.toFixed(2)}`;
}

/**
 * A sparkline of weekly success rate.
 *
 * A week with no validated run is a **gap**, not a zero: drawing it on the
 * baseline would show a collapse that never happened.
 */
function Trend({ points }: { points: OverviewReport["trend"] }) {
  const measured = points.filter((point) => point.rate !== null);
  if (measured.length < 2) {
    return (
      <p className="muted small">
        Not enough weeks with a validated run to draw a trend yet.
      </p>
    );
  }
  const width = 240;
  const height = 44;
  const step = width / Math.max(points.length - 1, 1);
  return (
    <svg
      aria-label="Weekly success rate"
      height={height}
      role="img"
      viewBox={`0 0 ${width} ${height}`}
      width={width}
    >
      {points.map((point, index) =>
        point.rate === null ? null : (
          <circle
            cx={index * step}
            cy={height - point.rate * height}
            fill="currentColor"
            key={point.week_ending}
            r={2.5}
          >
            <title>
              {point.week_ending}: {pct(point.rate)} over {point.n} run(s)
            </title>
          </circle>
        )
      )}
      <polyline
        fill="none"
        points={points
          .map((point, index) =>
            point.rate === null
              ? null
              : `${index * step},${height - point.rate * height}`
          )
          .filter(Boolean)
          .join(" ")}
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </svg>
  );
}

/**
 * The score corpus, aggregated.
 *
 * The figures above come from `Validation`. These come from the registry, and
 * they answer a different question: not "did the runs pass?" but "is the
 * harness earning its keep?" — which is the question this lab exists for and
 * which nothing else on this page would ever surface.
 */
function HarnessSignal({ scores }: { scores: OverviewReport["scores"] }) {
  if (scores.runs === 0) {
    return (
      <section className="panel">
        <h2>Harness signal</h2>
        <p className="muted">
          No scores recorded yet. Runs are scored against the registry when they
          finish; older runs can be scored from their detail page.
        </p>
      </section>
    );
  }

  const adherence = scores.adherence;
  const useful = scores.useful?.mean ?? null;
  const redundant = scores.redundant?.mean ?? null;
  const forbidden = scores.forbidden;

  return (
    <section className="panel">
      <div className="row space-between">
        <h2>Harness signal</h2>
        <span className="muted small">over {scores.runs} scored run(s)</span>
      </div>

      <div className="metrics">
        <div className="metric">
          <span>Skill adherence</span>
          <strong>{adherence?.mean === null || adherence === null
            ? "—"
            : pct(adherence.mean)}</strong>
          <small className="muted">
            share of the harness judged used · judgement
          </small>
        </div>
        <div className="metric">
          <span>Items useful / run</span>
          <strong>{useful === null ? "—" : useful.toFixed(1)}</strong>
          <small className="muted">
            {redundant === null ? "" : `${redundant.toFixed(1)} judged redundant`}
          </small>
        </div>
        <div className="metric">
          <span>Diff size</span>
          <strong>
            {scores.diff_size?.median === null || !scores.diff_size
              ? "—"
              : `${scores.diff_size.median} lines`}
          </strong>
          <small className="muted">median · observation</small>
        </div>
        <div className="metric">
          <span>Boundary violations</span>
          <strong>{forbidden?.mean ? forbidden.maximum : 0}</strong>
          <small className="muted">forbidden paths touched, worst run</small>
        </div>
      </div>

      {adherence !== null && adherence.mean !== null && adherence.mean < 0.4 && (
        <p className="muted small">
          Agents are judged to use under half of the harness available to them.
          That is a finding about the <em>harness</em>, not the model: an item
          nothing ever reaches is cost without effect. Read the per-item verdicts
          in a run&apos;s <strong>Harness catalog</strong>, then ablate the ones
          that never earn their keep and measure the difference.
        </p>
      )}
    </section>
  );
}

function Overview({ report }: { report: OverviewReport }) {
  const unscored = report.scoring.terminal - report.scoring.scored;
  return (
    <>
      <div className="metrics">
        <div className="metric">
          <span>Harness version</span>
          <strong>{report.harness_version ?? "—"}</strong>
        </div>
        <div className="metric">
          <span>Success rate ({report.window_days}d)</span>
          <strong>{pct(report.success.rate)}</strong>
          <small className="muted">over {report.success.n} validated run(s)</small>
        </div>
        <div className="metric">
          <span>Regressions</span>
          <strong>{report.regressions.count}</strong>
          <small className="muted">
            of {report.regressions.eligible_cases} case(s) with a history
          </small>
        </div>
        <div className="metric">
          <span>Median cost</span>
          <strong>{usd(report.cost.median_usd)}</strong>
          <small className="muted">
            {usd(report.cost.cost_per_success)} per success
          </small>
        </div>
        <div className="metric">
          <span>Regression suite</span>
          <strong>{report.regression_suite.cases}</strong>
          <small className="muted">
            of {report.regression_suite.of_active} active case(s)
          </small>
        </div>
        <div className="metric">
          <span>Experiments</span>
          <strong>{report.experiments.recent}</strong>
          <small className="muted">
            this window · {report.experiments.total} total
          </small>
        </div>
      </div>

      <div className="row gap wrap">
        <section className="panel">
          <h2>Quality over time</h2>
          <Trend points={report.trend} />
          <p className="muted small">
            Weekly share of objectively-validated runs that passed. A week with no
            validated run is left blank rather than drawn as zero.
          </p>
        </section>

        <HarnessSignal scores={report.scores} />
      </div>

      <div className="row gap wrap">
        <section className="panel">
          <h2>Worth attention</h2>
          <ul className="warning-list">
            {report.success.note && <li>⚠ {report.success.note}</li>}
            {report.regressions.count > 0 && (
              <li>
                ⚠ Regressed since a previous pass:{" "}
                <span className="mono small">
                  {report.regressions.cases.join(", ")}
                </span>
              </li>
            )}
            {unscored > 0 && (
              <li>
                {unscored} finished run(s) have no scores yet — scoring is on demand,
                so they are absent from every experiment summary.
              </li>
            )}
            {report.findings.open > 0 && (
              <li>
                {report.findings.open} open <Link to="/findings">finding(s)</Link>{" "}
                with nothing testing them yet.
              </li>
            )}
            {report.regression_suite.cases === 0 && (
              <li>
                No case carries its own objective validation, so nothing here can
                gate a change to the harness.
              </li>
            )}
            {report.success.note === "" &&
              report.regressions.count === 0 &&
              unscored === 0 &&
              report.findings.open === 0 &&
              report.regression_suite.cases > 0 && <li>Nothing outstanding.</li>}
          </ul>
        </section>
      </div>
    </>
  );
}

function countByState(runs: RunRecord[], state: RunRecord["state"]): number {
  return runs.filter((run) => run.state === state).length;
}

function distinct(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort();
}

// One leaderboard entry = one experimental condition (or one official
// reference row). % resolved is per-attempt: resolved runs / objectively
// validated runs of the condition — manual-score rows carry avg score only.
type BoardRow = {
  key: string;
  official: boolean;
  label: string;
  detail: string;
  sourceUrl?: string;
  pct: number | null;
  resolvedCount: number;
  objectiveAttempts: number;
  attempts: number;
  avgScore: number | null;
  bestScore: number | null;
  avgCost: number | null;
};

function buildBoard(rows: LeaderboardRunRow[]): BoardRow[] {
  const groups = new Map<string, LeaderboardRunRow[]>();
  for (const row of rows) {
    const key = [
      row.harness,
      row.executor_model ?? "",
      row.prompt_variant,
      row.harness_version
    ].join("|");
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return [...groups.entries()].map(([key, members]) => {
    const objective = members.filter((m) => m.resolved !== null);
    const resolvedCount = objective.filter((m) => m.resolved === true).length;
    const scores = members
      .map((m) => m.score)
      .filter((s): s is number => typeof s === "number");
    const costs = members
      .map((m) => m.cost_usd)
      .filter((c): c is number => typeof c === "number");
    const first = members[0];
    return {
      key,
      official: false,
      label: `${first.harness} + ${first.executor_model ?? "?"}`,
      detail: [first.prompt_variant, first.harness_version && `harness ${first.harness_version}`]
        .filter(Boolean)
        .join(" · "),
      pct: objective.length > 0 ? (resolvedCount / objective.length) * 100 : null,
      resolvedCount,
      objectiveAttempts: objective.length,
      attempts: members.length,
      avgScore:
        scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : null,
      bestScore: scores.length > 0 ? Math.max(...scores) : null,
      avgCost: costs.length > 0 ? costs.reduce((a, b) => a + b, 0) / costs.length : null
    };
  });
}

function referenceToBoard(refs: OfficialReferenceRow[]): BoardRow[] {
  return refs.map((ref, index) => ({
    key: `official-${index}`,
    official: true,
    label: `${ref.agent} + ${ref.model}`,
    detail: [ref.dataset && `SWE-bench ${ref.dataset}`, ref.date]
      .filter(Boolean)
      .join(" · "),
    sourceUrl: ref.source_url,
    pct: ref.pct_resolved,
    resolvedCount: 0,
    objectiveAttempts: 0,
    attempts: 0,
    avgScore: null,
    bestScore: null,
    avgCost: null
  }));
}

function fmtPct(pct: number | null): string {
  return pct === null ? "—" : `${pct.toFixed(1)}%`;
}

function fmtNum(value: number | null, digits = 1): string {
  return value === null ? "—" : value.toFixed(digits);
}

export function Dashboard({ cases, runs, apiStatus }: DashboardProps) {
  const [rows, setRows] = useState<LeaderboardRunRow[]>([]);
  const [overview, setOverview] = useState<OverviewReport | null>(null);
  const [reference, setReference] = useState<OfficialReferenceRow[]>([]);
  const [laneFilter, setLaneFilter] = useState(ALL);
  const [harnessFilter, setHarnessFilter] = useState(ALL);
  const [modelFilter, setModelFilter] = useState(ALL);
  const [promptFilter, setPromptFilter] = useState(ALL);
  const [versionFilter, setVersionFilter] = useState(ALL);
  const [showOfficial, setShowOfficial] = useState(true);
  // Off by default: a continued run was steered by a human between steps, so
  // ranking it beside a one-shot attempt overstates the harness (ADR-0026).
  const [showContinuations, setShowContinuations] = useState(false);

  useEffect(() => {
    api
    api
      .getOverview()
      .then(setOverview)
      .catch(() => setOverview(null));
    api
      .getLeaderboard()
      .then(setRows)
      .catch(() => setRows([]));
    api
      .getLeaderboardReference()
      .then(setReference)
      .catch(() => setReference([]));
  }, []);

  const filtered = useMemo(
    () =>
      rows.filter(
        (r) =>
          (laneFilter === ALL || r.case_type === laneFilter) &&
          (harnessFilter === ALL || r.harness === harnessFilter) &&
          (modelFilter === ALL || r.executor_model === modelFilter) &&
          (promptFilter === ALL || r.prompt_variant === promptFilter) &&
          (versionFilter === ALL || r.harness_version === versionFilter) &&
          (showContinuations || (r.chain_depth ?? 0) === 0)
      ),
    [
      rows,
      laneFilter,
      harnessFilter,
      modelFilter,
      promptFilter,
      versionFilter,
      showContinuations
    ]
  );

  const board = useMemo(() => {
    const mine = buildBoard(filtered);
    const official = showOfficial ? referenceToBoard(reference) : [];
    return [...mine, ...official].sort((a, b) => {
      if (a.pct === null && b.pct === null) {
        return (b.avgScore ?? -1) - (a.avgScore ?? -1);
      }
      if (a.pct === null) return 1;
      if (b.pct === null) return -1;
      return b.pct - a.pct;
    });
  }, [filtered, reference, showOfficial]);

  const maxPct = Math.max(100, ...board.map((r) => r.pct ?? 0));

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Overview</p>
          <h1>Harness quality</h1>
          <p className="lede">
            Is the harness getting better, is anything regressing, and what does a
            success cost. Every figure carries its denominator — a rate over four
            runs and one over four hundred are different claims.
          </p>
        </div>
        <div className="status-chip">{apiStatus}</div>
      </header>

      {overview !== null ? (
        <Overview report={overview} />
      ) : (
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
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Leaderboard — ranking by condition</h2>
          <span className="muted">
            {board.filter((r) => !r.official).length} condition(s) ·{" "}
            {filtered.length} run(s)
          </span>
        </div>
        <div className="improvements-toolbar">
          <select
            aria-label="Filter by lane"
            onChange={(e) => setLaneFilter(e.target.value)}
            value={laneFilter}
          >
            <option value={ALL}>lane: all</option>
            {distinct(rows.map((r) => r.case_type)).map((lane) => (
              <option key={lane} value={lane}>
                {lane}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by harness"
            onChange={(e) => setHarnessFilter(e.target.value)}
            value={harnessFilter}
          >
            <option value={ALL}>harness: all</option>
            {distinct(rows.map((r) => r.harness)).map((harness) => (
              <option key={harness} value={harness}>
                {harness}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by model"
            onChange={(e) => setModelFilter(e.target.value)}
            value={modelFilter}
          >
            <option value={ALL}>model: all</option>
            {distinct(rows.map((r) => r.executor_model ?? "")).map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by prompt"
            onChange={(e) => setPromptFilter(e.target.value)}
            value={promptFilter}
          >
            <option value={ALL}>prompt: all</option>
            {distinct(rows.map((r) => r.prompt_variant)).map((prompt) => (
              <option key={prompt} value={prompt}>
                {prompt}
              </option>
            ))}
          </select>
          <select
            aria-label="Filter by harness version"
            onChange={(e) => setVersionFilter(e.target.value)}
            value={versionFilter}
          >
            <option value={ALL}>version: all</option>
            {distinct(rows.map((r) => r.harness_version)).map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
          <label className="switch" title="Reference rows from the official leaderboard (data/reference/swebench_official.json).">
            <input
              checked={showOfficial}
              onChange={(e) => setShowOfficial(e.target.checked)}
              type="checkbox"
            />
            <span>official reference</span>
          </label>
          <label
            className="switch"
            title="Runs continued from another run (ADR-0026). A human read the workspace and wrote a new prompt, so they are not comparable to one-shot attempts."
          >
            <input
              checked={showContinuations}
              onChange={(e) => setShowContinuations(e.target.checked)}
              type="checkbox"
            />
            <span>include continuations</span>
          </label>
        </div>
        {board.length === 0 ? (
          <p className="muted">
            No terminal runs yet — launch a case from the Runs page to populate the
            ranking.
          </p>
        ) : (
          <div className="table-scroll">
            <div className="table table--leaderboard">
              <div className="table-row table-head">
                <span>#</span>
                <span>Condition</span>
                <span>% resolved</span>
                <span>Runs</span>
                <span>Avg score</span>
                <span>Best</span>
                <span>Avg cost</span>
              </div>
              {board.map((row, index) => (
                <div
                  className={`table-row${row.official ? " leaderboard-official" : ""}`}
                  key={row.key}
                >
                  <span className="muted">{index + 1}</span>
                  <span className="cell-stack">
                    <strong>
                      {row.label}{" "}
                      {row.official ? (
                        <span className="badge neutral" title="Reference row from the official leaderboard — different subset and infrastructure, indicative comparison only.">
                          official
                        </span>
                      ) : null}
                    </strong>
                    <small className="muted">
                      {row.detail}
                      {row.official && row.sourceUrl ? (
                        <>
                          {" · "}
                          <a href={row.sourceUrl} rel="noreferrer" target="_blank">
                            source ↗
                          </a>
                        </>
                      ) : null}
                    </small>
                  </span>
                  <span className="cell-stack">
                    <span className="pct-bar" title={
                      row.official
                        ? "Official pass@1 over the 500 Verified instances"
                        : `${row.resolvedCount}/${row.objectiveAttempts} objectively-validated runs resolved`
                    }>
                      <span
                        className={`pct-bar-fill${row.official ? " official" : ""}`}
                        style={{ width: `${((row.pct ?? 0) / maxPct) * 100}%` }}
                      />
                      <span className="pct-bar-label">{fmtPct(row.pct)}</span>
                    </span>
                    {!row.official && row.objectiveAttempts > 0 ? (
                      <small className="muted">
                        {row.resolvedCount}/{row.objectiveAttempts} resolved
                      </small>
                    ) : null}
                  </span>
                  <span>{row.official ? "—" : row.attempts}</span>
                  <span>{fmtNum(row.avgScore)}</span>
                  <span>{row.bestScore === null ? "—" : row.bestScore}</span>
                  <span>{row.avgCost === null ? "—" : `$${row.avgCost.toFixed(3)}`}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        <p className="muted">
          ⚠️ Indicative comparison: your % runs over the lab&apos;s curated subset and
          your conditions; official rows are pass@1 over the 500 SWE-bench Verified
          instances. Update the reference by editing{" "}
          <code>data/reference/swebench_official.json</code>.
        </p>
      </section>
    </section>
  );
}
