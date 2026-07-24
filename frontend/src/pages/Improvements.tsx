import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { IssueFormView } from "../components/IssueFormView";
import type { GroupIssueResult, SavedProposalRecord } from "../types";

const ALL = "all";

// A proposal theme is "recurrent" when the same kind+target was proposed from
// at least this many distinct runs — repeated evidence, not one-off noise.
const RECURRENCE_MIN_RUNS = 2;

function recurrenceKey(p: SavedProposalRecord): string {
  return `${p.kind}|${p.target}`.toLowerCase().trim();
}

function distinct(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort();
}

// SWE-bench Pro run ids run past 80 chars; keep the informative head and the
// distinguishing "-K-NNN" tail, full id stays available via the title tooltip.
function shortRunId(runId: string): string {
  if (runId.length <= 28) {
    return runId;
  }
  return `${runId.slice(0, 16)}…${runId.slice(-9)}`;
}

function formatRunDate(iso: string | null): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

// harness_fingerprint.harness_hash → short tag ("f9db0a83") for scanning
// template iterations; the full hash stays in the tooltip.
function shortFingerprint(fingerprint: string): string {
  if (!fingerprint) {
    return "—";
  }
  return fingerprint.replace(/^sha256:/, "").slice(0, 8);
}

// Cross-run worklist: proposals saved from each run's "Guardar" button. Select
// any subset (across runs/cases) and synthesize ONE combined sanitized issue
// for ml-python-base — the group counterpart of the per-run card in RunDetail.
export function Improvements() {
  const [proposals, setProposals] = useState<SavedProposalRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [repoFilter, setRepoFilter] = useState(ALL);
  const [kindFilter, setKindFilter] = useState(ALL);
  const [caseFilter, setCaseFilter] = useState(ALL);
  const [onlyRecurrent, setOnlyRecurrent] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [groupIssue, setGroupIssue] = useState<GroupIssueResult | null>(null);

  const refresh = () => {
    api
      .listSavedProposals()
      .then((rows) => {
        setProposals(rows);
        setLoadError(null);
      })
      .catch((error: Error) => setLoadError(error.message));
  };

  useEffect(refresh, []);

  // Distinct runs per kind+target theme, across ALL saved proposals (not the
  // filtered view) — recurrence is a global signal.
  const runsPerTheme = useMemo(() => {
    const themes = new Map<string, Set<string>>();
    for (const p of proposals ?? []) {
      const key = recurrenceKey(p);
      const runs = themes.get(key) ?? new Set<string>();
      runs.add(p.run_id);
      themes.set(key, runs);
    }
    return themes;
  }, [proposals]);

  const runCount = (p: SavedProposalRecord) =>
    runsPerTheme.get(recurrenceKey(p))?.size ?? 1;

  const visible = useMemo(() => {
    if (proposals === null) {
      return [];
    }
    return proposals.filter(
      (p) =>
        (repoFilter === ALL || p.repo === repoFilter) &&
        (kindFilter === ALL || p.kind === kindFilter) &&
        (caseFilter === ALL || p.case === caseFilter) &&
        (!onlyRecurrent ||
          (runsPerTheme.get(recurrenceKey(p))?.size ?? 1) >= RECURRENCE_MIN_RUNS)
    );
  }, [proposals, repoFilter, kindFilter, caseFilter, onlyRecurrent, runsPerTheme]);

  const toggle = (id: number) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const allVisibleSelected =
    visible.length > 0 && visible.every((p) => selected.has(p.id));

  const toggleAllVisible = () => {
    setSelected((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        visible.forEach((p) => next.delete(p.id));
      } else {
        visible.forEach((p) => next.add(p.id));
      }
      return next;
    });
  };

  const handleDelete = (id: number) => {
    api
      .deleteSavedProposal(id)
      .then(() => {
        setSelected((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
        refresh();
      })
      .catch((error: Error) => setLoadError(error.message));
  };

  const handleGenerate = () => {
    setGenerating(true);
    setGenerateError(null);
    setGroupIssue(null);
    api
      .createGroupIssue([...selected])
      .then((result) => {
        setGroupIssue(result);
        setSelected(new Set());
        refresh();
      })
      .catch((error: Error) => {
        if (error instanceof ApiError && error.status === 422) {
          setGenerateError(
            `El sanitizador rechazó el resultado (posible fuga de datos privados). Deseleccioná la proposal problemática y reintentá. Detalle: ${error.message}`
          );
        } else {
          setGenerateError(error.message);
        }
      })
      .finally(() => setGenerating(false));
  };

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Improvements</p>
          <h1>Saved proposals</h1>
          <p className="lede">
            Proposals guardadas desde cada run (botón "Guardar" en Improvement
            proposals). Seleccioná una o varias — incluso de runs distintos — y generá
            UN issue sanitizado combinado para ml-python-base.
          </p>
        </div>
      </header>
      {loadError ? <ErrorBanner message={loadError} onDismiss={() => setLoadError(null)} /> : null}
      {proposals !== null && proposals.length === 0 ? (
        <EmptyState
          title="No hay proposals guardadas"
          description='Corré un run, generá sus Improvement proposals y usá el botón "Guardar" del panel para traerlas acá.'
        />
      ) : null}
      {proposals !== null && proposals.length > 0 ? (
        <section className="panel">
          <div className="panel-head">
            <h2>Worklist</h2>
            <span className="muted">
              {selected.size > 0
                ? `${selected.size} seleccionada(s) de ${visible.length}`
                : `${visible.length} proposal(s)`}
            </span>
          </div>
          <div className="improvements-toolbar">
            <select
              aria-label="Filtrar por repo"
              onChange={(e) => setRepoFilter(e.target.value)}
              value={repoFilter}
            >
              <option value={ALL}>repo: todas</option>
              {distinct(proposals.map((p) => p.repo)).map((repo) => (
                <option key={repo} value={repo}>
                  {repo.split("/").pop()}
                </option>
              ))}
            </select>
            <select
              aria-label="Filtrar por kind"
              onChange={(e) => setKindFilter(e.target.value)}
              value={kindFilter}
            >
              <option value={ALL}>kind: todos</option>
              {distinct(proposals.map((p) => p.kind)).map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </select>
            <select
              aria-label="Filtrar por case"
              onChange={(e) => setCaseFilter(e.target.value)}
              value={caseFilter}
            >
              <option value={ALL}>case: todos</option>
              {distinct(proposals.map((p) => p.case)).map((caseId) => (
                <option key={caseId} value={caseId}>
                  {caseId}
                </option>
              ))}
            </select>
            <label
              className="switch"
              title={`Solo temas propuestos desde ≥${RECURRENCE_MIN_RUNS} runs distintos (mismo kind+target).`}
            >
              <input
                checked={onlyRecurrent}
                onChange={(e) => setOnlyRecurrent(e.target.checked)}
                type="checkbox"
              />
              <span>solo recurrentes</span>
            </label>
            <span className="toolbar-spacer" />
            <button
              className="primary"
              disabled={generating || selected.size === 0}
              onClick={handleGenerate}
              title={
                selected.size === 0
                  ? "Seleccioná al menos una proposal con los checkboxes."
                  : undefined
              }
              type="button"
            >
              {generating
                ? "Generando…"
                : `Generar issue combinado (${selected.size})`}
            </button>
          </div>
          {generating ? (
            <p className="muted">
              Sintetizando un issue sanitizado combinado (corre un LLM en el servidor;
              puede tardar entre 15 y 40 segundos).
            </p>
          ) : null}
          <div className="table-scroll">
            <div className="table table--improvements">
              <div className="table-row table-head">
                <span>
                  <input
                    aria-label="Seleccionar todas las visibles"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    type="checkbox"
                  />
                </span>
                <span>Proposal</span>
                <span>Kind · target</span>
                <span>Repo</span>
                <span>Run · case</span>
                <span>Fecha · template</span>
                <span>Status</span>
                <span />
              </div>
              {visible.map((proposal) => (
                <div className="table-row" key={proposal.id}>
                  <span>
                    <input
                      aria-label={`Seleccionar ${proposal.title}`}
                      checked={selected.has(proposal.id)}
                      onChange={() => toggle(proposal.id)}
                      type="checkbox"
                    />
                  </span>
                  <span className="cell-stack">
                    <strong>{proposal.title}</strong>
                    <small className="cell-excerpt">{proposal.body.slice(0, 220)}</small>
                  </span>
                  <span className="cell-stack">
                    {proposal.kind ? (
                      <span className="badge neutral">{proposal.kind}</span>
                    ) : null}
                    {runCount(proposal) >= RECURRENCE_MIN_RUNS ? (
                      <span
                        className="badge ok"
                        title={`Mismo kind+target propuesto desde ${runCount(proposal)} runs distintos — evidencia repetida, buen candidato a issue.`}
                      >
                        recurrente ×{runCount(proposal)}
                      </span>
                    ) : null}
                    {proposal.target ? (
                      <small className="cell-truncate" title={proposal.target}>
                        {proposal.target}
                      </small>
                    ) : null}
                  </span>
                  <span>
                    <span className="badge neutral" title={proposal.repo}>
                      {proposal.repo.split("/").pop()}
                    </span>
                  </span>
                  <span className="cell-stack">
                    <Link
                      className="cell-truncate"
                      title={proposal.run_id}
                      to={`/runs/${proposal.run_id}`}
                    >
                      {shortRunId(proposal.run_id)}
                    </Link>
                    <small className="cell-truncate" title={proposal.case}>
                      {proposal.case}
                    </small>
                  </span>
                  <span className="cell-stack">
                    <span className="cell-date">{formatRunDate(proposal.run_created_at)}</span>
                    <small
                      className="cell-fingerprint"
                      title={
                        [
                          proposal.template_version
                            ? `template: ${proposal.template_version}`
                            : null,
                          proposal.template_fingerprint
                            ? `fingerprint: ${proposal.template_fingerprint}`
                            : null
                        ]
                          .filter(Boolean)
                          .join(" · ") || "Sin datos del template"
                      }
                    >
                      {proposal.template_version ||
                        shortFingerprint(proposal.template_fingerprint)}
                    </small>
                  </span>
                  <span className="cell-stack">
                    <span
                      className={
                        proposal.status === "issued" ? "badge ok" : "badge neutral"
                      }
                    >
                      {proposal.status}
                    </span>
                    {proposal.created_issue_url ? (
                      <small>
                        <a
                          href={proposal.created_issue_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          issue ↗
                        </a>
                      </small>
                    ) : null}
                  </span>
                  <span className="row-actions">
                    <button onClick={() => handleDelete(proposal.id)} type="button">
                      Quitar
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
          {visible.length === 0 ? (
            <p className="muted">Ninguna proposal coincide con los filtros.</p>
          ) : null}
        </section>
      ) : null}
      {generateError ? (
        <ErrorBanner message={generateError} onDismiss={() => setGenerateError(null)} />
      ) : null}
      {groupIssue ? (
        <section className="panel">
          <div className="panel-head">
            <h2>Issue combinado (ml-python-base)</h2>
            <span className="muted">
              {groupIssue.proposal_ids.length} proposal(s) ·{" "}
              {groupIssue.source_run_ids.length} run(s)
            </span>
          </div>
          <IssueFormView
            form={groupIssue.form}
            markdown={groupIssue.markdown}
            repository={groupIssue.repository}
          />
        </section>
      ) : null}
    </section>
  );
}
