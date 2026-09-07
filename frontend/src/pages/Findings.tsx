import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconCheck, IconInfo, IconX } from "../components/icons";
import type { FindingRecord, SuggestionRecord } from "../types";

const SEVERITY_CLASS: Record<string, string> = {
  high: "badge error",
  medium: "badge",
  low: "badge neutral"
};

const STATUS_HINT: Record<string, string> = {
  suggested: "The lab noticed a pattern. Nobody has accepted it yet.",
  open: "Observed. Nothing has tested it yet.",
  proposed: "An intervention is proposed but not measured.",
  experiment_running: "An experiment is under way.",
  validated: "An experiment supported the recommendation.",
  refuted: "An experiment did not support it — kept on the record.",
  dismissed: "Set aside without testing."
};

type FormState = {
  finding_id: string;
  title: string;
  body: string;
  severity: "low" | "medium" | "high";
  target_repo_id: string;
  evidence: string;
  cases: string;
  recommendation: string;
  intervention: string;
};

const EMPTY: FormState = {
  finding_id: "",
  title: "",
  body: "",
  severity: "medium",
  target_repo_id: "",
  evidence: "",
  cases: "",
  recommendation: "",
  intervention: ""
};

function toList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * The review queue for patterns the lab noticed (§14).
 *
 * Deliberately not a findings list. Nothing here is a claim the lab makes:
 * every row is a pattern read out of the corpus, waiting for a person to put
 * their name on it. The author field is required before Accept or Reject
 * enables, because a finding nobody owns is an assertion nobody has to defend.
 */
function SuggestionQueue({
  suggestions,
  isLoading,
  onReload,
  onError
}: {
  suggestions: SuggestionRecord[];
  isLoading: boolean;
  onReload: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const act = async (key: string, accept: boolean) => {
    setBusy(key);
    setNotice(null);
    try {
      if (accept) {
        const overrides = edits[key]?.trim()
          ? { recommendation: edits[key].trim() }
          : undefined;
        const result = await api.acceptSuggestion(key, {
          author: author.trim(),
          overrides
        });
        setNotice(
          `Accepted as finding '${result.finding.finding_id}'. It is now on the ` +
            "record under your name — turn it into an experiment to settle it."
        );
      } else {
        await api.rejectSuggestion(key, { author: author.trim() });
        setNotice(
          "Rejected and written down, so the same pattern will not be " +
            "suggested again next week."
        );
      }
      await onReload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "Unable to record that");
    } finally {
      setBusy(null);
    }
  };

  if (isLoading) {
    return (
      <div className="card">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>Suggested findings</h2>
        <p className="muted">
          Patterns read out of the run corpus — regressions, cases that have never
          passed, harness items the auditor keeps judging unused. None of them is a
          finding until somebody accepts it.
        </p>
        <label className="field">
          <span>Your name</span>
          <input
            onChange={(event) => setAuthor(event.target.value)}
            placeholder="Who is signing this?"
            value={author}
          />
        </label>
        {author.trim() ? null : (
          <p className="muted small">
            <IconInfo size={14} /> Accepting or rejecting needs a name on it.
          </p>
        )}
        {notice ? <p className="callout callout--ok">{notice}</p> : null}
      </div>

      {suggestions.length === 0 ? (
        <EmptyState
          description="Either the corpus shows no patterns worth raising, or every one of them has already been accepted or rejected."
          title="Nothing waiting for review."
        />
      ) : (
        suggestions.map((suggestion) => (
          <article
            className={`card suggestion suggestion--${suggestion.severity}`}
            key={suggestion.key}
          >
            <div className="suggestion-head">
              <div>
                <span className="badge neutral">suggested</span>{" "}
                <span className={SEVERITY_CLASS[suggestion.severity]}>
                  {suggestion.severity}
                </span>
                <h3>{suggestion.title}</h3>
              </div>
              <span className="muted small mono">{suggestion.source}</span>
            </div>
            <p>{suggestion.body}</p>
            <label className="field">
              <span>Recommendation (edit before accepting)</span>
              <textarea
                onChange={(event) =>
                  setEdits({
                    ...edits,
                    [suggestion.key]: event.target.value
                  })
                }
                rows={2}
                value={edits[suggestion.key] ?? suggestion.recommendation}
              />
            </label>
            <p className="muted small">
              Evidence: {suggestion.evidence_run_ids.length} run(s)
              {suggestion.affected_case_ids.length > 0
                ? ` · cases: ${suggestion.affected_case_ids.join(", ")}`
                : ""}
              {suggestion.target_repo_id ? ` · ${suggestion.target_repo_id}` : ""}
            </p>
            <div className="button-row">
              <button
                className="primary"
                disabled={!author.trim() || busy === suggestion.key}
                onClick={() => void act(suggestion.key, true)}
                type="button"
              >
                <IconCheck size={14} /> Accept as finding
              </button>
              <button
                className="secondary"
                disabled={!author.trim() || busy === suggestion.key}
                onClick={() => void act(suggestion.key, false)}
                type="button"
              >
                <IconX size={14} /> Reject
              </button>
            </div>
          </article>
        ))
      )}
    </div>
  );
}

export function Findings() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "suggested" ? "suggested" : "record";
  const [findings, setFindings] = useState<FindingRecord[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);

  const load = async () => {
    setIsLoading(true);
    try {
      const [recorded, suggested] = await Promise.all([
        api.listFindings(),
        api.listSuggestions()
      ]);
      setFindings(recorded);
      setSuggestions(suggested.suggestions);
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "Unable to load findings"
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleCreate = async () => {
    setError(null);
    setWarnings([]);
    try {
      const created = await api.createFinding({
        finding_id: form.finding_id.trim(),
        title: form.title.trim(),
        body: form.body,
        severity: form.severity,
        target_repo_id: form.target_repo_id.trim(),
        evidence_run_ids: toList(form.evidence),
        affected_case_ids: toList(form.cases),
        recommendation: form.recommendation,
        intervention: form.intervention
      });
      setWarnings(created.warnings ?? []);
      setForm(EMPTY);
      await load();
    } catch (createError) {
      setError(
        createError instanceof Error ? createError.message : "Unable to save finding"
      );
    }
  };

  const handleDraft = async (findingId: string) => {
    setError(null);
    try {
      setDraft(await api.draftExperimentFromFinding(findingId));
    } catch (draftError) {
      setError(
        draftError instanceof Error ? draftError.message : "Unable to draft experiment"
      );
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>Findings</h1>
          <p className="muted">
            What the runs showed, what to do about it, and whether that was tested.
          </p>
        </div>
      </header>

      <HelpPanel title="A finding is a claim, not a note">
        <p>
          A run is evidence. A finding is a claim about what that evidence means, and it
          is only worth making if it can be <strong>tested</strong> — which is why each
          one carries a recommendation and an intervention, and a slot for the
          experiment that settles it:
        </p>
        <p className="mono small">
          Audit → Finding → Hypothesis → Experiment → Evidence → Recommendation
        </p>
        <p className="muted">
          A finding that cites no runs is an observation, and is labelled that way. A
          finding an experiment <em>refuted</em> keeps its record — that is the part
          worth not losing.
        </p>
      </HelpPanel>

      {error !== null && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}
      {warnings.length > 0 && (
        <ul className="warning-list">
          {warnings.map((warning) => (
            <li key={warning}>
              <IconAlert size={14} /> {warning}
            </li>
          ))}
        </ul>
      )}

      <div className="tab-bar" role="tablist">
        <button
          aria-selected={tab === "record"}
          className={tab === "record" ? "tab tab--active" : "tab"}
          onClick={() => setParams({})}
          role="tab"
          type="button"
        >
          Findings of record ({findings.length})
        </button>
        <button
          aria-selected={tab === "suggested"}
          className={tab === "suggested" ? "tab tab--active" : "tab"}
          onClick={() => setParams({ tab: "suggested" })}
          role="tab"
          type="button"
        >
          Suggested ({suggestions.length})
        </button>
      </div>

      {tab === "suggested" ? (
        <SuggestionQueue
          isLoading={isLoading}
          onError={setError}
          onReload={load}
          suggestions={suggestions}
        />
      ) : (
        <>
          <div className="card">
            <h2>Record a finding</h2>
            <div className="form-grid">
              <label>
                <span>Id</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      finding_id: event.target.value
                    })
                  }
                  placeholder="migrations-edited"
                  value={form.finding_id}
                />
              </label>
              <label>
                <span>Title</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      title: event.target.value
                    })
                  }
                  placeholder="Agents edit legacy migration files"
                  value={form.title}
                />
              </label>
              <label>
                <span>Severity</span>
                <select
                  onChange={(event) =>
                    setForm({
                      ...form,
                      severity: event.target.value as FormState["severity"]
                    })
                  }
                  value={form.severity}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                </select>
              </label>
              <label>
                <span>Repository</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      target_repo_id: event.target.value
                    })
                  }
                  placeholder="company-payments"
                  value={form.target_repo_id}
                />
              </label>
              <label>
                <span>Evidence — run ids</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      evidence: event.target.value
                    })
                  }
                  placeholder="run-a, run-b"
                  value={form.evidence}
                />
              </label>
              <label>
                <span>Affected cases</span>
                <input
                  onChange={(event) =>
                    setForm({
                      ...form,
                      cases: event.target.value
                    })
                  }
                  placeholder="add-endpoint"
                  value={form.cases}
                />
              </label>
            </div>
            <label className="stacked-field">
              <span>What was observed</span>
              <textarea
                onChange={(event) =>
                  setForm({
                    ...form,
                    body: event.target.value
                  })
                }
                rows={3}
                value={form.body}
              />
            </label>
            <label className="stacked-field">
              <span>Recommendation — what should change</span>
              <textarea
                onChange={(event) =>
                  setForm({
                    ...form,
                    recommendation: event.target.value
                  })
                }
                rows={2}
                value={form.recommendation}
              />
            </label>
            <label className="stacked-field">
              <span>Intervention — the change that would test it</span>
              <input
                onChange={(event) =>
                  setForm({
                    ...form,
                    intervention: event.target.value
                  })
                }
                placeholder="migration-safety skill"
                value={form.intervention}
              />
            </label>
            <button
              className="primary"
              disabled={form.finding_id.trim() === "" || form.title.trim() === ""}
              onClick={() => void handleCreate()}
              type="button"
            >
              Record finding
            </button>
          </div>

          <div className="card">
            <h2>Findings</h2>
            {isLoading ? (
              <Spinner />
            ) : findings.length === 0 ? (
              <EmptyState
                description="Findings turn a pile of runs into something a team can act on."
                title="Nothing recorded yet"
              />
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Finding</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Evidence</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {findings.map((finding) => (
                    <tr key={finding.finding_id}>
                      <td>
                        <strong>{finding.title}</strong>
                        <div className="muted small">
                          {finding.finding_id}
                          {finding.target_repo_id && ` · ${finding.target_repo_id}`}
                        </div>
                        {finding.recommendation && (
                          <div className="small">→ {finding.recommendation}</div>
                        )}
                      </td>
                      <td>
                        <span className={SEVERITY_CLASS[finding.severity]}>
                          {finding.severity}
                        </span>
                      </td>
                      <td className="small">
                        {finding.status}
                        <div className="muted small">{STATUS_HINT[finding.status]}</div>
                        {finding.experiment_id && (
                          <div className="mono small">{finding.experiment_id}</div>
                        )}
                      </td>
                      <td className="small">
                        {finding.evidence_run_ids.length > 0 ? (
                          `${finding.evidence_run_ids.length} run(s)`
                        ) : (
                          <span className="muted">observation only</span>
                        )}
                      </td>
                      <td>
                        <button
                          onClick={() => void handleDraft(finding.finding_id)}
                          type="button"
                        >
                          Draft experiment
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {draft !== null && (
            <div className="card">
              <h2>Draft experiment</h2>
              <p className="muted">
                A starting point, not a created experiment: the design still needs the
                harness axis the intervention changes, and a design that varies nothing
                about the harness is refused. Review it, then build the experiment in{" "}
                <Link to="/evaluate/new">New evaluation</Link>.
              </p>
              <pre className="mono small">{JSON.stringify(draft, null, 2)}</pre>
              <div className="button-row">
                <Link className="primary" to="/evaluate/new">
                  Build it in the wizard
                </Link>
                <button onClick={() => setDraft(null)} type="button">
                  Close
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
