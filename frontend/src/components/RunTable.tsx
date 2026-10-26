import { useEffect, useMemo, useState } from "react";
import type { MouseEvent } from "react";
import { NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  caseDisplayName,
  caseTestCounts,
  runAttemptSuffix,
  shortInstanceLabel
} from "../caseLabels";
import { formatDuration, formatRelative, formatRunDate, parseApiDate } from "../dates";
import { defineMessages, pickMessages, useMessages } from "../i18n";
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

const SORT_KEYS: SortKey[] = [
  "run",
  "harness",
  "model",
  "state",
  "score",
  "created",
  "improvements"
];

// Ordering for the improvement-cycle column: work still to do sorts first.
const IMPROVEMENTS_RANK: Record<string, number> = { yes: 0, pending: 1, no: 2 };

const MESSAGES = defineMessages({
  en: {
    columns: {
      run: "Run",
      harness: "Harness",
      model: "Model",
      state: "State",
      score: "Score",
      created: "Created",
      improvements: "Improvements"
    },
    actions: "Actions",
    improvementsBadge: {
      yes: { label: "yes", title: "Improvement proposals were generated." },
      no: {
        label: "no",
        title: "Synthesis ran and proposed no changes (clean run / gate closed)."
      },
      pending: {
        label: "pending",
        title: "No synthesis yet — open the run to generate its proposals."
      }
    },
    tooltip: {
      created: (date: string) => `Created: ${date}`,
      lastRun: (start: string, end: string) => `Last run: ${start} → ${end}`,
      stillRunning: "still running",
      lastRunNotStarted: "Last run: not started yet",
      queuedFor: (duration: string) => `Queued for: ${duration}`,
      execution: (duration: string) => `Execution: ${duration}`,
      run: (runId: string) => `Run: ${runId}`,
      case: (caseId: string) => `Case: ${caseId}`,
      tests: (f2p: number, p2p: number) => `Tests: F2P ${f2p} / P2P ${p2p}`,
      command: (command: string) => `Command: ${command}`
    },
    runList: "Run list",
    runsOfTotal: (shown: number, total: number) => `${shown} of ${total} runs`,
    runsTotal: (total: number) => `${total} total`,
    showingOnly: "Showing only",
    unscoredFilterLabel: "finished runs with no score",
    failedFilterLabel: "failed runs",
    showAllRuns: "Show all runs",
    search: "Search",
    searchPlaceholder: "Case title, run id, model…",
    harness: "Harness",
    state: "State",
    all: "All",
    perPage: "Per page",
    clearFilters: "Clear filters",
    emptyNoRuns: {
      title: "No runs yet.",
      description: "Create a run from the web app once the API is online."
    },
    emptyNoMatch: {
      title: "Nothing to show.",
      description: "No run matches the current search and filters."
    },
    sortBy: (label: string) => `Sort by ${label.toLowerCase()}`,
    batchBadge: "batch",
    armTitle: (label: string, experimentId: string) => `Arm "${label}" of experiment ${experimentId}.`,
    unlabelled: "unlabelled",
    noResult: "no result",
    noResultTitle:
      "Nothing about the model or the harness can be read from this run — retry it.",
    checksFailed: "checks failed",
    checksFailedTitle:
      "The automated checks ran and failed — the produced code does not build or does not run. Open the run to see which check failed.",
    checksPassed: "checks passed",
    checksPassedTitle: "Automated checks passed.",
    cancel: "Cancel",
    runAgain: "Run again",
    runAgainTitleExperiment: (experimentId: string) =>
      `Repeats this configuration and stays in ${experimentId}, as another repetition of the same arm.`,
    runAgainTitlePlain: "Repeats this configuration as a new run.",
    deleteLabel: "Delete",
    deleteConfirm: (runId: string) => `Delete run "${runId}"? This cannot be undone.`,
    paginationOf: (from: number, to: number, total: number) => `${from}–${to} of ${total}`,
    first: "« First",
    prev: "‹ Prev",
    pageOf: (current: number, total: number) => `Page ${current} / ${total}`,
    next: "Next ›",
    last: "Last »"
  },
  es: {
    columns: {
      run: "Corrida",
      harness: "Arnés",
      model: "Modelo",
      state: "Estado",
      score: "Puntaje",
      created: "Creada",
      improvements: "Mejoras"
    },
    actions: "Acciones",
    improvementsBadge: {
      yes: { label: "sí", title: "Se generaron propuestas de mejora." },
      no: {
        label: "no",
        title: "La síntesis corrió y no propuso cambios (corrida limpia / gate cerrado)."
      },
      pending: {
        label: "pendiente",
        title: "Todavía no hay síntesis — abrí la corrida para generar sus propuestas."
      }
    },
    tooltip: {
      created: (date: string) => `Creada: ${date}`,
      lastRun: (start: string, end: string) => `Última corrida: ${start} → ${end}`,
      stillRunning: "todavía corriendo",
      lastRunNotStarted: "Última corrida: todavía no empezó",
      queuedFor: (duration: string) => `En cola: ${duration}`,
      execution: (duration: string) => `Ejecución: ${duration}`,
      run: (runId: string) => `Corrida: ${runId}`,
      case: (caseId: string) => `Caso: ${caseId}`,
      tests: (f2p: number, p2p: number) => `Tests: F2P ${f2p} / P2P ${p2p}`,
      command: (command: string) => `Comando: ${command}`
    },
    runList: "Lista de corridas",
    runsOfTotal: (shown: number, total: number) => `${shown} de ${total} corridas`,
    runsTotal: (total: number) => `${total} en total`,
    showingOnly: "Mostrando solo",
    unscoredFilterLabel: "corridas terminadas sin puntaje",
    failedFilterLabel: "corridas falladas",
    showAllRuns: "Mostrar todas las corridas",
    search: "Buscar",
    searchPlaceholder: "Título del caso, id de corrida, modelo…",
    harness: "Arnés",
    state: "Estado",
    all: "Todas",
    perPage: "Por página",
    clearFilters: "Limpiar filtros",
    emptyNoRuns: {
      title: "Todavía no hay corridas.",
      description: "Creá una corrida desde la app web una vez que la API esté en línea."
    },
    emptyNoMatch: {
      title: "Nada para mostrar.",
      description: "Ninguna corrida coincide con la búsqueda y los filtros actuales."
    },
    sortBy: (label: string) => `Ordenar por ${label.toLowerCase()}`,
    batchBadge: "lote",
    armTitle: (label: string, experimentId: string) => `Brazo "${label}" del experimento ${experimentId}.`,
    unlabelled: "sin etiqueta",
    noResult: "sin resultado",
    noResultTitle:
      "De esta corrida no se puede leer nada sobre el modelo o el arnés — reintentala.",
    checksFailed: "chequeos fallaron",
    checksFailedTitle:
      "Los chequeos automáticos corrieron y fallaron — el código producido no compila o no corre. Abrí la corrida para ver qué chequeo falló.",
    checksPassed: "chequeos pasaron",
    checksPassedTitle: "Los chequeos automáticos pasaron.",
    cancel: "Cancelar",
    runAgain: "Correr de nuevo",
    runAgainTitleExperiment: (experimentId: string) =>
      `Repite esta configuración y se queda en ${experimentId}, como otra repetición del mismo brazo.`,
    runAgainTitlePlain: "Repite esta configuración como una nueva corrida.",
    deleteLabel: "Eliminar",
    deleteConfirm: (runId: string) => `¿Eliminar la corrida "${runId}"? Esto no se puede deshacer.`,
    paginationOf: (from: number, to: number, total: number) => `${from}–${to} de ${total}`,
    first: "« Primera",
    prev: "‹ Anterior",
    pageOf: (current: number, total: number) => `Página ${current} / ${total}`,
    next: "Siguiente ›",
    last: "Última »"
  }
});

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
  const m = pickMessages(MESSAGES);
  if (status === "yes") {
    return { className: "badge ok", ...m.improvementsBadge.yes };
  }
  if (status === "no") {
    return { className: "badge neutral", ...m.improvementsBadge.no };
  }
  return { className: "badge warn", ...m.improvementsBadge.pending };
}

/**
 * What a failure was, in two words, and whether it says anything about the run.
 *
 * The distinction that matters for a benchmark: a run the *lab* broke measured
 * nothing, and reading it as a weak result would poison the comparison. A
 * missing runner image, a restarted worker and a wedged agent are three
 * different things, and the list used to render all of them as the bare word
 * "failed" with the explanation buried inside the run.
 *
 * `lab` marks the ones where no conclusion about the model or the harness is
 * available — retry them rather than counting them.
 */
type FailureSummary = { label: string; lab: boolean; detail: string };

function summarizeFailure(run: RunRecord): FailureSummary | null {
  const detail = (run.failure_reason ?? "").trim();
  if (!detail || !run.failure_kind) {
    return null;
  }
  return {
    // The label and the kind both come from the server so this list, the
    // leaderboard's exclusion rule and the API can never disagree about what
    // a given failure was.
    label: run.failure_label ?? run.failure_kind,
    lab: run.failure_kind === "lab",
    detail
  };
}

// The Created cell shows the run's age; its tooltip carries the exact creation
// timestamp plus the last execution window (start → end and how long it took),
// which is what you actually compare runs by.
function createdTooltip(run: RunRecord): string {
  const m = pickMessages(MESSAGES);
  const lines = [m.tooltip.created(formatRunDate(run.created_at))];
  if (run.started_at) {
    const queued = formatDuration(run.created_at, run.started_at);
    const executed = formatDuration(run.started_at, run.ended_at);
    const ended = run.ended_at ? formatRunDate(run.ended_at) : m.tooltip.stillRunning;
    lines.push(m.tooltip.lastRun(formatRunDate(run.started_at), ended));
    // Queue time is not the harness being slow — with a single worker a batch
    // queues behind itself — so it is reported apart from execution time
    // instead of being folded into one "duration".
    if (queued) {
      lines.push(m.tooltip.queuedFor(queued));
    }
    if (executed) {
      lines.push(m.tooltip.execution(executed));
    }
  } else {
    lines.push(m.tooltip.lastRunNotStarted);
  }
  return lines.join("\n");
}

// Human-readable identity for a run row: the case title leads, the attempt id
// and the short instance id follow, and the tooltip keeps the exact run id,
// test counts, and the full command a hover away.
function buildRow(run: RunRecord, caseById: Map<string, CaseRecord>): RunRow {
  const m = pickMessages(MESSAGES);
  const caseId = commandFlag(run.command, "--case");
  const caseRecord = caseById.get(caseId);
  const title = caseDisplayName(caseRecord, caseId) || run.run_id;
  const tests = caseTestCounts(caseRecord);
  const tooltip = [
    title,
    m.tooltip.run(run.run_id),
    caseId ? m.tooltip.case(caseId) : null,
    tests ? m.tooltip.tests(tests.f2p, tests.p2p) : null,
    m.tooltip.command(run.command.join(" "))
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
  const m = useMessages(MESSAGES);
  const { runId: activeRunId } = useParams();
  // The home page's attention items promise a filtered list — "4 experiment
  // runs failed" leading to every run ever would be the kind of CTA that
  // teaches people to stop clicking them.
  const [searchParams, setSearchParams] = useSearchParams();
  const attentionFilter = searchParams.get("filter") ?? "";
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
      if (attentionFilter === "failed" && row.run.state !== "failed") {
        return false;
      }
      // "Unscored" means finished with no manual/objective score recorded —
      // exactly the runs missing from every aggregate on the home page.
      if (
        attentionFilter === "unscored" &&
        !(row.run.state === "completed" && row.run.score === null)
      ) {
        return false;
      }
      if (!needle) {
        return true;
      }
      // batch_id is in the haystack so pasting one from the queue confirmation
      // narrows the table to exactly that batch — no extra filter control for
      // what is an opaque, copy-pasted id.
      return [
        row.title,
        row.run.run_id,
        row.caseId,
        row.harness,
        row.model,
        row.run.batch_id ?? ""
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [attentionFilter, harnessFilter, query, rows, stateFilter]);

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
      .then((created) => navigate(`/advanced/runs/${created.run_id}`))
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const handleDeleteRow = (event: MouseEvent, targetRunId: string) => {
    event.preventDefault();
    if (!window.confirm(m.deleteConfirm(targetRunId))) {
      return;
    }
    setRowActionError(null);
    setPendingRowId(targetRunId);
    onDeleteRun(targetRunId)
      .then(() => {
        if (activeRunId === targetRunId) {
          navigate("/advanced/runs");
        }
      })
      .catch((error: Error) => setRowActionError(error.message))
      .finally(() => setPendingRowId(null));
  };

  const isFiltered =
    query.trim() !== "" ||
    harnessFilter !== "all" ||
    stateFilter !== "all" ||
    attentionFilter !== "";

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.runList}</h2>
        <span className="muted">
          {isFiltered ? m.runsOfTotal(sorted.length, runs.length) : m.runsTotal(runs.length)}
        </span>
      </div>

      {/* An arrived-from-home filter is visible and removable — a list quietly
          hiding rows is worse than one that shows too many. */}
      {attentionFilter ? (
        <p className="callout">
          {m.showingOnly}{" "}
          <strong>
            {attentionFilter === "unscored" ? m.unscoredFilterLabel : m.failedFilterLabel}
          </strong>
          .{" "}
          <button
            className="button-link"
            onClick={() => setSearchParams({})}
            type="button"
          >
            {m.showAllRuns}
          </button>
        </p>
      ) : null}

      {runs.length > 0 ? (
        <div className="table-toolbar">
          <label className="toolbar-field toolbar-search">
            <span className="sidebar-label">{m.search}</span>
            <input
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder={m.searchPlaceholder}
              value={query}
            />
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">{m.harness}</span>
            <select
              onChange={(event) => {
                setHarnessFilter(event.target.value);
                setPage(1);
              }}
              value={harnessFilter}
            >
              <option value="all">{m.all}</option>
              {harnessOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">{m.state}</span>
            <select
              onChange={(event) => {
                setStateFilter(event.target.value);
                setPage(1);
              }}
              value={stateFilter}
            >
              <option value="all">{m.all}</option>
              {stateOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="toolbar-field">
            <span className="sidebar-label">{m.perPage}</span>
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
              {m.clearFilters}
            </button>
          ) : null}
        </div>
      ) : null}

      {rowActionError ? (
        <ErrorBanner message={rowActionError} onDismiss={() => setRowActionError(null)} />
      ) : null}

      {runs.length === 0 ? (
        <EmptyState description={m.emptyNoRuns.description} title={m.emptyNoRuns.title} />
      ) : sorted.length === 0 ? (
        <EmptyState description={m.emptyNoMatch.description} title={m.emptyNoMatch.title} />
      ) : (
        <>
          <div className="table table--runs">
            <div className="table-row table-head">
              {SORT_KEYS.map((key) => (
                <span key={key}>
                  <button
                    className={sortKey === key ? "sort-header active" : "sort-header"}
                    onClick={() => handleSort(key)}
                    title={m.sortBy(m.columns[key])}
                    type="button"
                  >
                    {m.columns[key]}
                    <span className="sort-arrow">
                      {sortKey === key ? (sortDirection === "asc" ? "▲" : "▼") : "↕"}
                    </span>
                  </button>
                </span>
              ))}
              <span>{m.actions}</span>
            </div>
            {visible.map((row) => {
              const run = row.run;
              const isActive = activeRunId === run.run_id;
              const isPending = pendingRowId === run.run_id;
              const improvements = improvementsBadge(row.improvements);
              const failure = summarizeFailure(run);
              return (
                <div
                  className={isActive ? "table-row runs-row active" : "table-row runs-row"}
                  key={run.run_id}
                >
                  <NavLink className="runs-row-link" to={`/advanced/runs/${run.run_id}`}>
                    <span className="run-cell" title={row.tooltip}>
                      <strong className="run-title">{row.title}</strong>
                      <small className="run-meta">
                        {row.attempt ? <span className="badge neutral">{row.attempt}</span> : null}
                        {row.run.batch_id ? (
                          <span className="badge neutral" title={row.run.batch_id}>
                            {m.batchBadge}
                          </span>
                        ) : null}
                        {run.experiment_id ? (
                          <span
                            className="badge neutral"
                            title={m.armTitle(run.condition_label || m.unlabelled, run.experiment_id)}
                          >
                            {run.condition_label || run.experiment_id}
                          </span>
                        ) : null}
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
                    <span className="run-state-cell">
                      <StateBadge state={run.state} />
                      {failure ? (
                        <span
                          className={failure.lab ? "badge warn" : "badge error"}
                          title={failure.detail}
                        >
                          {failure.label}
                        </span>
                      ) : null}
                      {failure?.lab ? (
                        <small className="muted" title={m.noResultTitle}>
                          {m.noResult}
                        </small>
                      ) : null}
                      {run.objective_passed === false ? (
                        <span className="badge error" title={m.checksFailedTitle}>
                          {m.checksFailed}
                        </span>
                      ) : null}
                      {run.objective_passed === true ? (
                        <span className="badge ok" title={m.checksPassedTitle}>
                          {m.checksPassed}
                        </span>
                      ) : null}
                    </span>
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
                      {m.cancel}
                    </button>
                    <button
                      disabled={isPending}
                      onClick={(event) => handleCloneRow(event, run.run_id)}
                      title={
                        run.experiment_id
                          ? m.runAgainTitleExperiment(run.experiment_id)
                          : m.runAgainTitlePlain
                      }
                      type="button"
                    >
                      {m.runAgain}
                    </button>
                    <button
                      disabled={isPending}
                      onClick={(event) => handleDeleteRow(event, run.run_id)}
                      type="button"
                    >
                      {m.deleteLabel}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="table-pagination">
            <span className="muted">
              {m.paginationOf(pageStart + 1, Math.min(pageStart + pageSize, sorted.length), sorted.length)}
            </span>
            <div className="pagination-controls">
              <button disabled={currentPage === 1} onClick={() => setPage(1)} type="button">
                {m.first}
              </button>
              <button
                disabled={currentPage === 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                type="button"
              >
                {m.prev}
              </button>
              <span className="muted">{m.pageOf(currentPage, pageCount)}</span>
              <button
                disabled={currentPage === pageCount}
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                type="button"
              >
                {m.next}
              </button>
              <button
                disabled={currentPage === pageCount}
                onClick={() => setPage(pageCount)}
                type="button"
              >
                {m.last}
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
