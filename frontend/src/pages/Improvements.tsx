import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { formatRunDate } from "../dates";
import { IssueFormView } from "../components/IssueFormView";
import { defineMessages, useMessages } from "../i18n";
import type { GroupIssueResult, SavedProposalRecord } from "../types";

const ALL = "all";

const MESSAGES = defineMessages({
  en: {
    eyebrow: "Improvements",
    title: "Saved proposals",
    lede: (
      <>
        Proposals saved from individual runs (the &quot;Save&quot; button in the Improvement
        proposals panel). Select one or several — they may come from different runs — and
        generate a single combined, sanitised issue for ml-python-base.
      </>
    ),
    emptyTitle: "No saved proposals yet.",
    emptyDescription:
      'Run a case, generate its improvement proposals, and use the panel\'s "Save" button to bring them here.',
    worklist: "Worklist",
    selectedCount: (selected: number, total: number) => `${selected} of ${total} selected`,
    proposalCount: (n: number) => `${n} proposal${n === 1 ? "" : "s"}`,
    filterRepo: "Filter by repository",
    repoAll: "repo: all",
    filterKind: "Filter by kind",
    kindAll: "kind: all",
    filterCase: "Filter by case",
    caseAll: "case: all",
    filterHarness: "Filter by harness",
    harnessAll: "harness: all",
    harnessTooltip:
      "The tool that drove the harness — it decides whether an issue touches CLAUDE.md, opencode, or the shared .github/ layer.",
    recurrentTooltip: (min: number) =>
      `Only themes proposed from ${min} or more distinct runs (same kind and target).`,
    recurrentOnly: "recurrent only",
    selectAtLeastOne: "Select at least one proposal with the checkboxes.",
    generating: "Generating…",
    generateCombined: (n: number) => `Generate combined issue (${n})`,
    synthesising:
      "Synthesising a combined, sanitised issue (this runs an LLM on the server and can take 15 to 40 seconds).",
    selectAllVisible: "Select all visible",
    selectProposal: (title: string) => `Select ${title}`,
    colProposal: "Proposal",
    colKindTarget: "Kind · target",
    colRepo: "Repo",
    colHarness: "Harness",
    colRunCase: "Run · case",
    colDateTemplate: "Date · template",
    colStatus: "Status",
    recurrentBadgeTooltip: (count: number) =>
      `The same kind and target were proposed from ${count} distinct runs — repeated evidence, a good issue candidate.`,
    recurrentBadge: (count: number) => `recurrent ×${count}`,
    harnessBadgeTooltip: (harness: string) =>
      `Harness: ${harness} — a generated issue would target its adapter, or .github/ if the selection mixes harnesses.`,
    templateTooltip: (parts: (string | null)[]) =>
      parts.filter(Boolean).join(" · ") || "No template metadata",
    templateLabel: (template: string) => `template: ${template}`,
    fingerprintLabel: (fingerprint: string) => `fingerprint: ${fingerprint}`,
    status: { saved: "saved", issued: "issued" },
    issueLink: "issue ↗",
    remove: "Remove",
    noMatch: "No proposal matches the current filters.",
    combinedHeading: "Combined issue (ml-python-base)",
    combinedCount: (proposals: number, runs: number) =>
      `${proposals} proposal${proposals === 1 ? "" : "s"} · ${runs} run${runs === 1 ? "" : "s"}`
  },
  es: {
    eyebrow: "Mejoras",
    title: "Propuestas guardadas",
    lede: (
      <>
        Propuestas guardadas desde corridas individuales (el botón &quot;Guardar&quot; del
        panel de propuestas de mejora). Elegí una o varias — pueden venir de corridas
        distintas — y generá un único issue combinado y saneado para ml-python-base.
      </>
    ),
    emptyTitle: "Todavía no hay propuestas guardadas.",
    emptyDescription:
      'Corré un caso, generá sus propuestas de mejora, y usá el botón "Guardar" del panel para traerlas acá.',
    worklist: "Lista de trabajo",
    selectedCount: (selected: number, total: number) => `${selected} de ${total} seleccionadas`,
    proposalCount: (n: number) => `${n} propuesta${n === 1 ? "" : "s"}`,
    filterRepo: "Filtrar por repositorio",
    repoAll: "repo: todos",
    filterKind: "Filtrar por tipo",
    kindAll: "tipo: todos",
    filterCase: "Filtrar por caso",
    caseAll: "caso: todos",
    filterHarness: "Filtrar por arnés",
    harnessAll: "arnés: todos",
    harnessTooltip:
      "La herramienta que condujo el arnés — decide si un issue toca CLAUDE.md, opencode, o la capa compartida .github/.",
    recurrentTooltip: (min: number) =>
      `Solo temas propuestos desde ${min} o más corridas distintas (mismo tipo y objetivo).`,
    recurrentOnly: "solo recurrentes",
    selectAtLeastOne: "Seleccioná al menos una propuesta con los checkboxes.",
    generating: "Generando…",
    generateCombined: (n: number) => `Generar issue combinado (${n})`,
    synthesising:
      "Sintetizando un issue combinado y saneado (esto corre un LLM en el servidor y puede tardar entre 15 y 40 segundos).",
    selectAllVisible: "Seleccionar todas las visibles",
    selectProposal: (title: string) => `Seleccionar ${title}`,
    colProposal: "Propuesta",
    colKindTarget: "Tipo · objetivo",
    colRepo: "Repo",
    colHarness: "Arnés",
    colRunCase: "Corrida · caso",
    colDateTemplate: "Fecha · plantilla",
    colStatus: "Estado",
    recurrentBadgeTooltip: (count: number) =>
      `El mismo tipo y objetivo se propusieron desde ${count} corridas distintas — evidencia repetida, buen candidato a issue.`,
    recurrentBadge: (count: number) => `recurrente ×${count}`,
    harnessBadgeTooltip: (harness: string) =>
      `Arnés: ${harness} — un issue generado apuntaría a su adaptador, o a .github/ si la selección mezcla arneses.`,
    templateTooltip: (parts: (string | null)[]) =>
      parts.filter(Boolean).join(" · ") || "Sin metadatos de plantilla",
    templateLabel: (template: string) => `plantilla: ${template}`,
    fingerprintLabel: (fingerprint: string) => `huella: ${fingerprint}`,
    status: { saved: "guardada", issued: "emitida" },
    issueLink: "issue ↗",
    remove: "Quitar",
    noMatch: "Ninguna propuesta coincide con los filtros actuales.",
    combinedHeading: "Issue combinado (ml-python-base)",
    combinedCount: (proposals: number, runs: number) =>
      `${proposals} propuesta${proposals === 1 ? "" : "s"} · ${runs} corrida${runs === 1 ? "" : "s"}`
  }
});

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
  const m = useMessages(MESSAGES);
  const [proposals, setProposals] = useState<SavedProposalRecord[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [repoFilter, setRepoFilter] = useState(ALL);
  const [kindFilter, setKindFilter] = useState(ALL);
  const [caseFilter, setCaseFilter] = useState(ALL);
  const [harnessFilter, setHarnessFilter] = useState(ALL);
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
        (harnessFilter === ALL || p.harness === harnessFilter) &&
        (!onlyRecurrent ||
          (runsPerTheme.get(recurrenceKey(p))?.size ?? 1) >= RECURRENCE_MIN_RUNS)
    );
  }, [
    proposals,
    repoFilter,
    kindFilter,
    caseFilter,
    harnessFilter,
    onlyRecurrent,
    runsPerTheme
  ]);

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
            `The sanitiser rejected the result (possible leak of private data). Deselect the offending proposal and try again. Detail: ${error.message}`
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
          <p className="eyebrow">{m.eyebrow}</p>
          <h1>{m.title}</h1>
          <p className="lede">{m.lede}</p>
        </div>
      </header>
      {loadError ? <ErrorBanner message={loadError} onDismiss={() => setLoadError(null)} /> : null}
      {proposals !== null && proposals.length === 0 ? (
        <EmptyState title={m.emptyTitle} description={m.emptyDescription} />
      ) : null}
      {proposals !== null && proposals.length > 0 ? (
        <section className="panel">
          <div className="panel-head">
            <h2>{m.worklist}</h2>
            <span className="muted">
              {selected.size > 0
                ? m.selectedCount(selected.size, visible.length)
                : m.proposalCount(visible.length)}
            </span>
          </div>
          <div className="improvements-toolbar">
            <select
              aria-label={m.filterRepo}
              onChange={(e) => setRepoFilter(e.target.value)}
              value={repoFilter}
            >
              <option value={ALL}>{m.repoAll}</option>
              {distinct(proposals.map((p) => p.repo)).map((repo) => (
                <option key={repo} value={repo}>
                  {repo.split("/").pop()}
                </option>
              ))}
            </select>
            <select
              aria-label={m.filterKind}
              onChange={(e) => setKindFilter(e.target.value)}
              value={kindFilter}
            >
              <option value={ALL}>{m.kindAll}</option>
              {distinct(proposals.map((p) => p.kind)).map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </select>
            <select
              aria-label={m.filterCase}
              onChange={(e) => setCaseFilter(e.target.value)}
              value={caseFilter}
            >
              <option value={ALL}>{m.caseAll}</option>
              {distinct(proposals.map((p) => p.case)).map((caseId) => (
                <option key={caseId} value={caseId}>
                  {caseId}
                </option>
              ))}
            </select>
            <select
              aria-label={m.filterHarness}
              onChange={(e) => setHarnessFilter(e.target.value)}
              title={m.harnessTooltip}
              value={harnessFilter}
            >
              <option value={ALL}>{m.harnessAll}</option>
              {distinct(proposals.map((p) => p.harness)).map((harness) => (
                <option key={harness} value={harness}>
                  {harness}
                </option>
              ))}
            </select>
            <label className="switch" title={m.recurrentTooltip(RECURRENCE_MIN_RUNS)}>
              <input
                checked={onlyRecurrent}
                onChange={(e) => setOnlyRecurrent(e.target.checked)}
                type="checkbox"
              />
              <span>{m.recurrentOnly}</span>
            </label>
            <span className="toolbar-spacer" />
            <button
              className="primary"
              disabled={generating || selected.size === 0}
              onClick={handleGenerate}
              title={selected.size === 0 ? m.selectAtLeastOne : undefined}
              type="button"
            >
              {generating ? m.generating : m.generateCombined(selected.size)}
            </button>
          </div>
          {generating ? <p className="muted">{m.synthesising}</p> : null}
          <div className="table-scroll">
            <div className="table table--improvements">
              <div className="table-row table-head">
                <span>
                  <input
                    aria-label={m.selectAllVisible}
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    type="checkbox"
                  />
                </span>
                <span>{m.colProposal}</span>
                <span>{m.colKindTarget}</span>
                <span>{m.colRepo}</span>
                <span>{m.colHarness}</span>
                <span>{m.colRunCase}</span>
                <span>{m.colDateTemplate}</span>
                <span>{m.colStatus}</span>
                <span />
              </div>
              {visible.map((proposal) => (
                <div className="table-row" key={proposal.id}>
                  <span>
                    <input
                      aria-label={m.selectProposal(proposal.title)}
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
                        title={m.recurrentBadgeTooltip(runCount(proposal))}
                      >
                        {m.recurrentBadge(runCount(proposal))}
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
                  <span>
                    {proposal.harness ? (
                      <span
                        className="badge neutral"
                        title={m.harnessBadgeTooltip(proposal.harness)}
                      >
                        {proposal.harness}
                      </span>
                    ) : (
                      <small className="muted">—</small>
                    )}
                  </span>
                  <span className="cell-stack">
                    <Link
                      className="cell-truncate"
                      title={proposal.run_id}
                      to={`/advanced/runs/${proposal.run_id}`}
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
                      title={m.templateTooltip([
                        proposal.template_version
                          ? m.templateLabel(proposal.template_version)
                          : null,
                        proposal.template_fingerprint
                          ? m.fingerprintLabel(proposal.template_fingerprint)
                          : null
                      ])}
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
                      {m.status[proposal.status]}
                    </span>
                    {proposal.created_issue_url ? (
                      <small>
                        <a
                          href={proposal.created_issue_url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {m.issueLink}
                        </a>
                      </small>
                    ) : null}
                  </span>
                  <span className="row-actions">
                    <button onClick={() => handleDelete(proposal.id)} type="button">
                      {m.remove}
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
          {visible.length === 0 ? <p className="muted">{m.noMatch}</p> : null}
        </section>
      ) : null}
      {generateError ? (
        <ErrorBanner message={generateError} onDismiss={() => setGenerateError(null)} />
      ) : null}
      {groupIssue ? (
        <section className="panel">
          <div className="panel-head">
            <h2>{m.combinedHeading}</h2>
            <span className="muted">
              {m.combinedCount(groupIssue.proposal_ids.length, groupIssue.source_run_ids.length)}
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
