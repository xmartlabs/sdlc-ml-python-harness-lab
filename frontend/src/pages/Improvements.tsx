import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { formatRunDate } from "../dates";
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
          <p className="eyebrow">Improvements</p>
          <h1>Saved proposals</h1>
          <p className="lede">
            Proposals saved from individual runs (the "Save" button in the Improvement
            proposals panel). Select one or several — they may come from different runs —
            and generate a single combined, sanitised issue for ml-python-base.
          </p>
        </div>
      </header>
      {loadError ? <ErrorBanner message={loadError} onDismiss={() => setLoadError(null)} /> : null}
      {proposals !== null && proposals.length === 0 ? (
        <EmptyState
          title="No saved proposals yet."
          description={
            "Run a case, generate its improvement proposals, and use the " +
            'panel\'s "Save" button to bring them here.'
          }
        />
      ) : null}
      {proposals !== null && proposals.length > 0 ? (
        <section className="panel">
          <div className="panel-head">
            <h2>Worklist</h2>
            <span className="muted">
              {selected.size > 0
                ? `${selected.size} of ${visible.length} selected`
                : `${visible.length} proposal(s)`}
            </span>
          </div>
          <div className="improvements-toolbar">
            <select
              aria-label="Filter by repository"
              onChange={(e) => setRepoFilter(e.target.value)}
              value={repoFilter}
            >
              <option value={ALL}>repo: all</option>
              {distinct(proposals.map((p) => p.repo)).map((repo) => (
                <option key={repo} value={repo}>
                  {repo.split("/").pop()}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by kind"
              onChange={(e) => setKindFilter(e.target.value)}
              value={kindFilter}
            >
              <option value={ALL}>kind: all</option>
              {distinct(proposals.map((p) => p.kind)).map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by case"
              onChange={(e) => setCaseFilter(e.target.value)}
              value={caseFilter}
            >
              <option value={ALL}>case: all</option>
              {distinct(proposals.map((p) => p.case)).map((caseId) => (
                <option key={caseId} value={caseId}>
                  {caseId}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by harness"
              onChange={(e) => setHarnessFilter(e.target.value)}
              title="The tool that drove the harness — it decides whether an issue touches CLAUDE.md, opencode, or the shared .github/ layer."
              value={harnessFilter}
            >
              <option value={ALL}>harness: all</option>
              {distinct(proposals.map((p) => p.harness)).map((harness) => (
                <option key={harness} value={harness}>
                  {harness}
                </option>
              ))}
            </select>
            <label
              className="switch"
              title={`Only themes proposed from ${RECURRENCE_MIN_RUNS} or more distinct runs (same kind and target).`}
            >
              <input
                checked={onlyRecurrent}
                onChange={(e) => setOnlyRecurrent(e.target.checked)}
                type="checkbox"
              />
              <span>recurrent only</span>
            </label>
            <span className="toolbar-spacer" />
            <button
              className="primary"
              disabled={generating || selected.size === 0}
              onClick={handleGenerate}
              title={
                selected.size === 0
                  ? "Select at least one proposal with the checkboxes."
                  : undefined
              }
              type="button"
            >
              {generating
                ? "Generating…"
                : `Generate combined issue (${selected.size})`}
            </button>
          </div>
          {generating ? (
            <p className="muted">
              Synthesising a combined, sanitised issue (this runs an LLM on the
              server and can take 15 to 40 seconds).
            </p>
          ) : null}
          <div className="table-scroll">
            <div className="table table--improvements">
              <div className="table-row table-head">
                <span>
                  <input
                    aria-label="Select all visible"
                    checked={allVisibleSelected}
                    onChange={toggleAllVisible}
                    type="checkbox"
                  />
                </span>
                <span>Proposal</span>
                <span>Kind · target</span>
                <span>Repo</span>
                <span>Harness</span>
                <span>Run · case</span>
                <span>Date · template</span>
                <span>Status</span>
                <span />
              </div>
              {visible.map((proposal) => (
                <div className="table-row" key={proposal.id}>
                  <span>
                    <input
                      aria-label={`Select ${proposal.title}`}
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
                        title={`The same kind and target were proposed from ${runCount(proposal)} distinct runs — repeated evidence, a good issue candidate.`}
                      >
                        recurrent ×{runCount(proposal)}
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
                        title={`Harness: ${proposal.harness} — a generated issue would target its adapter, or .github/ if the selection mixes harnesses.`}
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
                          .join(" · ") || "No template metadata"
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
                      Remove
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
          {visible.length === 0 ? (
            <p className="muted">No proposal matches the current filters.</p>
          ) : null}
        </section>
      ) : null}
      {generateError ? (
        <ErrorBanner message={generateError} onDismiss={() => setGenerateError(null)} />
      ) : null}
      {groupIssue ? (
        <section className="panel">
          <div className="panel-head">
            <h2>Combined issue (ml-python-base)</h2>
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
