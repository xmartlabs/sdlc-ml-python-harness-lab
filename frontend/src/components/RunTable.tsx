import { useEffect, useMemo, useState } from "react";
import type { MouseEvent } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import {
  caseDisplayName,
  caseTestCounts,
  runAttemptSuffix,
  shortInstanceLabel
} from "../caseLabels";
import { formatDuration, formatRelative, formatRunDate, parseApiDate } from "../dates";
import { EmptyState } from "./EmptyState";
import { ErrorBanner } from "./ErrorBanner";
import { StateBadge } from "./StateBadge";
import { CANCELLABLE_STATES } from "../pages/RunDetail";
import type { CaseRecord, RunRecord } from "../types";

type RunTableProps = {
  cases: CaseRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onCloneRun: (runId: string) => Promise<RunRecord>;
  onDeleteRun: (runId: string, force?: boolean) => Promise<void>;
  runs: RunRecord[];
};

type SortKey = "run" | "harness" | "model" | "state" | "score" | "created" | "improvements";
type SortDirection = "asc" | "desc";

// One flattened row: everything the table sorts, filters, or renders, computed
// once per run instead of per comparison.
type RunRow = {
  attempt: string;
  caseId: string;
  created: number;
  createdTooltip: string;
  harness: string;
  improvements: RunRecord["improvements"];
  model: string;
  run: RunRecord;
  shortCase: string;
  tests: { f2p: number; p2p: number } | null;
  title: string;
  tooltip: string;
};

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 25;

const SORT_COLUMNS: { key: SortKey; label: string }[] = [
  { key: "run", label: "Run" },
  { key: "harness", label: "Harness" },
  { key: "model", label: "Model" },
  { key: "state", label: "State" },
  { key: "score", label: "Score" },
  { key: "created", label: "Created" },
  { key: "improvements", label: "Improvements" }
];

// Ordering for the improvement-cycle column: work still to do sorts first.
const IMPROVEMENTS_RANK: Record<string, number> = { yes: 0, pending: 1, no: 2 };

// Pull a flag's value out of the stored CLI command — the run list shows
// harness/model as columns so conditions can be scanned without reading the
// full command line.
function commandFlag(command: string[], flag: string): string {
  const index = command.indexOf(flag);
  return index >= 0 && index + 1 < command.length ? command[index + 1] : "";
}

// At-a-glance improvement-synthesis status per run (from improvements.json):
// "yes" = proposed changes, "no" = ran but clean, "pending" = still to generate.
function improvementsBadge(status: RunRecord["improvements"]) {
  if (status === "yes") {
    return { className: "badge ok", label: "yes", title: "Improvement proposals were generated." };
  }
  if (status === "no") {
    return {
      className: "badge neutral",
      label: "no",
      title: "Synthesis ran and proposed no changes (clean run / gate closed)."
    };
  }
  return {
    className: "badge warn",
    label: "pending",
    title: "No synthesis yet — open the run to generate its proposals."
  };
}

// The Created cell shows the run's age; its tooltip carries the exact creation
// timestamp plus the last execution window (start → end and how long it took),
// which is what you actually compare runs by.
function createdTooltip(run: RunRecord): string {
  const lines = [`Created: ${formatRunDate(run.created_at)}`];
  if (run.started_at) {
    const duration = formatDuration(run.started_at, run.ended_at);
    const ended = run.ended_at ? formatRunDate(run.ended_at) : "still running";
    lines.push(`Last run: ${formatRunDate(run.started_at)} → ${ended}`);
    if (duration) {
      lines.push(`Duration: ${duration}`);
    }
  } else {
    lines.push("Last run: not started yet");
  }
  return lines.join("\n");
}

// Human-readable identity for a run row: the case title leads, the attempt id
// and the short instance id follow, and the tooltip keeps the exact run id,
// test counts, and the full command a hover away.
function buildRow(run: RunRecord, caseById: Map<string, CaseRecord>): RunRow {
  const caseId = commandFlag(run.command, "--case");
  const caseRecord = caseById.get(caseId);
  const title = caseDisplayName(caseRecord, caseId) || run.run_id;
  const tests = caseTestCounts(caseRecord);
  const tooltip = [
    title,
    `Run: ${run.run_id}`,
    caseId ? `Case: ${caseId}` : null,
    tests ? `Tests: F2P ${tests.f2p} / P2P ${tests.p2p}` : null,
    `Command: ${run.command.join(" ")}`
  ]
    .filter(Boolean)
    .join("\n");
  return {
    attempt: runAttemptSuffix(run.run_id, caseId),
    caseId,
    created: parseApiDate(run.created_at)?.getTime() ?? 0,
    createdTooltip: createdTooltip(run),
    harness: commandFlag(run.command, "--harness"),
    improvements: run.improvements,
    model: commandFlag(run.command, "--executor-model").replace(/^gateway\//, ""),
    run,
    shortCase: caseId ? shortInstanceLabel(caseId) : "",
    tests,
    title,
    tooltip
  };
}

function compareRows(a: RunRow, b: RunRow, key: SortKey): number {
  switch (key) {
    case "created":
      return a.created - b.created;
    case "score":
      // Unscored runs sort last in both directions by staying at the extreme.
      return (a.run.score ?? -1) - (b.run.score ?? -1);
    case "state":
      return a.run.state.localeCompare(b.run.state);
    case "harness":
      return a.harness.localeCompare(b.harness);
    case "model":
      return a.model.localeCompare(b.model);
    case "improvements":
      return (
        (IMPROVEMENTS_RANK[a.improvements ?? "pending"] ?? 1) -
        (IMPROVEMENTS_RANK[b.improvements ?? "pending"] ?? 1)
      );
    default:
      return a.title.localeCompare(b.title) || a.run.run_id.localeCompare(b.run.run_id);
  }
}

export function RunTable({ cases, onCancelRun, onCloneRun, onDeleteRun, runs }: RunTableProps) {
  const { runId: activeRunId } = useParams();
  const navigate = useNavigate();
  const [rowActionError, setRowActionError] = useState<string | null>(null);
  const [pendingRowId, setPendingRowId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [harnessFilter, setHarnessFilter] = useState("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("created");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [page, setPage] = useState(1);

  // The run list only knows a run's case through its command, so index the
  // catalog once and look the case up by id.
  const caseById = useMemo(() => new Map(cases.map((item) => [item.case_id, item])), [cases]);
  const rows = useMemo(() => runs.map((run) => buildRow(run, caseById)), [caseById, runs]);

  const harnessOptions = useMemo(
    () => [...new Set(rows.map((row) => row.harness).filter(Boolean))].sort(),
    [rows]
  );
  const stateOptions = useMemo(
    () => [...new Set(rows.map((row) => row.run.state))].sort(),
    [rows]
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (harnessFilter !== "all" && row.harness !== harnessFilter) {
        return false;
      }
      if (stateFilter !== "all" && row.run.state !== stateFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return [row.title, row.run.run_id, row.caseId, row.harness, row.model]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [harnessFilter, query, rows, stateFilter]);

  const sorted = useMemo(() => {
    const factor = sortDirection === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => compareRows(a, b, sortKey) * factor);
  }, [filtered, sortDirection, sortKey]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  // Filtering down (or a page-size change) can leave the current page empty.
  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * pageSize;
  const visible = sorted.slice(pageStart, pageStart + pageSize);

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    // Dates and scores are most useful highest-first; text columns A→Z.
    setSortDirection(key === "created" || key === "score" ? "desc" : "asc");
  };

  const resetFilters = () => {
    setQuery("");
    setHarnessFilter("all");
    setStateFilter("all");
    setPage(1);
  };

  const handleCancelRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onCancelRun(targetRunId)
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const handleCloneRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onCloneRun(targetRunId)
      .then((created) => navigate(`/runs/${created.run_id}`))
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const handleDeleteRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    if (!window.confirm(`Delete run "${targetRunId}"? This cannot be undone.`)) {
      return;
    }
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onDeleteRun(targetRunId)
      .then(() => {
        if (activeRunId === targetRunId) {
          navigate("/runs");
        }
      })
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const isFiltered = query.trim() !== "" || harnessFilter !== "all" || stateFilter !== "all";

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Run list</h2>
        <span className="muted">
          {isFiltered ? `${sorted.length} of ${runs.length} runs` : `${runs.length} total`}
        </span>
      </div>

      {runs.length > 0 ? (
        <div className="table-toolbar">
          <label className="toolbar-field toolbar-search">
            <span className="sidebar-label">Search</span>
            <input
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Case title, run id, model…"
              value={query}
            />
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">Harness</span>
            <select
              onChange={(event) => {
                setHarnessFilter(event.target.value);
                setPage(1);
              }}
              value={harnessFilter}
            >
              <option value="all">All</option>
              {harnessOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">State</span>
            <select
              onChange={(event) => {
                setStateFilter(event.target.value);
                setPage(1);
              }}
              value={stateFilter}
            >
              <option value="all">All</option>
              {stateOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">Per page</span>
            <select
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
              value={pageSize}
            >
              {PAGE_SIZE_OPTIONS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          {isFiltered ? (
            <button onClick={resetFilters} type="button">
              Clear filters
            </button>
          ) : null}
        </div>
      ) : null}

      {rowActionError ? (
        <ErrorBanner message={rowActionError} onDismiss={() => setRowActionError(null)} />
      ) : null}

      {runs.length === 0 ? (
        <EmptyState
          description="Create a run from the web app once the API is online."
          title="No runs yet."
        />
      ) : sorted.length === 0 ? (
        <EmptyState
          description="No run matches the current search and filters."
          title="Nothing to show."
        />
      ) : (
        <>
          <div className="table table--runs">
            <div className="table-row table-head">
              {SORT_COLUMNS.map((column) => (
                <span key={column.key}>
                  <button
                    className={sortKey === column.key ? "sort-header active" : "sort-header"}
                    onClick={() => handleSort(column.key)}
                    title={`Sort by ${column.label.toLowerCase()}`}
                    type="button"
                  >
                    {column.label}
                    <span className="sort-arrow">
                      {sortKey === column.key ? (sortDirection === "asc" ? "▲" : "▼") : "↕"}
                    </span>
                  </button>
                </span>
              ))}
              <span>Actions</span>
            </div>
            {visible.map((row) => {
              const run = row.run;
              const isActive = activeRunId === run.run_id;
              const isPending = pendingRowId === run.run_id;
              const improvements = improvementsBadge(row.improvements);
              return (
                <div
                  className={isActive ? "table-row runs-row active" : "table-row runs-row"}
                  key={run.run_id}
                >
                  <NavLink className="runs-row-link" to={`/runs/${run.run_id}`}>
                    <span className="run-cell" title={row.tooltip}>
                      <strong className="run-title">{row.title}</strong>
                      <small className="run-meta">
                        {row.attempt ? <span className="badge neutral">{row.attempt}</span> : null}
                        {row.shortCase ? <span className="cell-truncate">{row.shortCase}</span> : null}
                        {row.tests ? (
                          <span className="run-tests">
                            F2P {row.tests.f2p} / P2P {row.tests.p2p}
                          </span>
                        ) : null}
                      </small>
                    </span>
                    <span>
                      {row.harness ? (
                        <span className="badge neutral">{row.harness}</span>
                      ) : (
                        <small className="muted">—</small>
                      )}
                    </span>
                    <span>
                      {row.model ? (
                        <small className="cell-truncate" title={row.model}>
                          {row.model}
                        </small>
                      ) : (
                        <small className="muted">—</small>
                      )}
                    </span>
                    <StateBadge state={run.state} />
                    <span>{run.score ?? "—"}</span>
                    <span>
                      <small className="run-created" title={row.createdTooltip}>
                        {formatRelative(run.created_at)}
                      </small>
                    </span>
                    <span>
                      <span className={improvements.className} title={improvements.title}>
                        {improvements.label}
                      </span>
                    </span>
                  </NavLink>
                  <div className="row-actions">
                    <button
                      disabled={!CANCELLABLE_STATES.includes(run.state) || isPending}
                      onClick={(event) => handleCancelRow(event, run.run_id)}
                      type="button"
                    >
                      Cancel
                    </button>
                    <button
                      disabled={isPending}
                      onClick={(event) => handleCloneRow(event, run.run_id)}
                      type="button"
                    >
                      Run again
                    </button>
                    <button
                      disabled={isPending}
                      onClick={(event) => handleDeleteRow(event, run.run_id)}
                      type="button"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="table-pagination">
            <span className="muted">
              {pageStart + 1}–{Math.min(pageStart + pageSize, sorted.length)} of {sorted.length}
            </span>
            <div className="pagination-controls">
              <button disabled={currentPage === 1} onClick={() => setPage(1)} type="button">
                « First
              </button>
              <button
                disabled={currentPage === 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                type="button"
              >
                ‹ Prev
              </button>
              <span className="muted">
                Page {currentPage} / {pageCount}
              </span>
              <button
                disabled={currentPage === pageCount}
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                type="button"
              >
                Next ›
              </button>
              <button
                disabled={currentPage === pageCount}
                onClick={() => setPage(pageCount)}
                type="button"
              >
                Last »
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
