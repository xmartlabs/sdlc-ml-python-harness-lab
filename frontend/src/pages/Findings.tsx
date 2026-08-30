import { useEffect, useState } from "react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { Spinner } from "../components/Spinner";
import type { FindingRecord } from "../types";

const SEVERITY_CLASS: Record<string, string> = {
  high: "badge error",
  medium: "badge",
  low: "badge neutral"
};

const STATUS_HINT: Record<string, string> = {
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

export function Findings() {
  const [findings, setFindings] = useState<FindingRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);

  const load = async () => {
    setIsLoading(true);
    try {
      setFindings(await api.listFindings());
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
          A run is evidence. A finding is a claim about what that evidence means, and
          it is only worth making if it can be <strong>tested</strong> — which is why
          each one carries a recommendation and an intervention, and a slot for the
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

      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {warnings.length > 0 && (
        <ul className="warning-list">
          {warnings.map((warning) => (
            <li key={warning}>⚠ {warning}</li>
          ))}
        </ul>
      )}

      <div className="card">
        <h2>Record a finding</h2>
        <div className="form-grid">
          <label>
            <span>Id</span>
            <input
              onChange={(event) =>
                setForm({ ...form, finding_id: event.target.value })
              }
              placeholder="migrations-edited"
              value={form.finding_id}
            />
          </label>
          <label>
            <span>Title</span>
            <input
              onChange={(event) => setForm({ ...form, title: event.target.value })}
              placeholder="Agents edit legacy migration files"
              value={form.title}
            />
          </label>
          <label>
            <span>Severity</span>
            <select
              onChange={(event) =>
                setForm({ ...form, severity: event.target.value as FormState["severity"] })
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
                setForm({ ...form, target_repo_id: event.target.value })
              }
              placeholder="company-payments"
              value={form.target_repo_id}
            />
          </label>
          <label>
            <span>Evidence — run ids</span>
            <input
              onChange={(event) => setForm({ ...form, evidence: event.target.value })}
              placeholder="run-a, run-b"
              value={form.evidence}
            />
          </label>
          <label>
            <span>Affected cases</span>
            <input
              onChange={(event) => setForm({ ...form, cases: event.target.value })}
              placeholder="add-endpoint"
              value={form.cases}
            />
          </label>
        </div>
        <label className="stacked-field">
          <span>What was observed</span>
          <textarea
            onChange={(event) => setForm({ ...form, body: event.target.value })}
            rows={3}
            value={form.body}
          />
        </label>
        <label className="stacked-field">
          <span>Recommendation — what should change</span>
          <textarea
            onChange={(event) =>
              setForm({ ...form, recommendation: event.target.value })
            }
            rows={2}
            value={form.recommendation}
          />
        </label>
        <label className="stacked-field">
          <span>Intervention — the change that would test it</span>
          <input
            onChange={(event) => setForm({ ...form, intervention: event.target.value })}
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
            about the harness is refused. Review it, then{" "}
            <code>POST /api/experiments</code>.
          </p>
          <pre className="mono small">{JSON.stringify(draft, null, 2)}</pre>
          <button onClick={() => setDraft(null)} type="button">
            Close
          </button>
        </div>
      )}
    </section>
  );
}
