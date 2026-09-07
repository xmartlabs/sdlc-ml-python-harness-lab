import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { HelpPanel } from "../components/HelpPanel";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import type {
  CaseCandidate,
  CaseCandidates,
  ReadinessReport,
  TargetRecord,
  TargetResolveResult
} from "../types";

/** Multi-line textareas hold one shell command per line. */
function toLines(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

type FormState = {
  target_id: string;
  name: string;
  repository_url: string;
  description: string;
  default_ref: string;
  setup: string;
  validation: string;
  language: string;
  team: string;
};

const EMPTY_FORM: FormState = {
  target_id: "",
  name: "",
  repository_url: "",
  description: "",
  default_ref: "main",
  setup: "",
  validation: "",
  language: "",
  team: ""
};

function formFrom(target: TargetRecord): FormState {
  return {
    target_id: target.target_id,
    name: target.name,
    repository_url: target.repository_url,
    description: target.description,
    default_ref: target.default_ref,
    setup: target.setup_commands.join("\n"),
    validation: target.validation_commands.join("\n"),
    language: target.language,
    team: target.team
  };
}

export function Repositories() {
  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  // Per-target pin check: what the default ref points at right now.
  const [pins, setPins] = useState<Record<string, TargetResolveResult | string>>({});
  const [checking, setChecking] = useState<string | null>(null);
  // Derived case candidates: proposals mined from the repo's own history.
  const [derived, setDerived] = useState<CaseCandidates | null>(null);
  const [deriving, setDeriving] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Read on demand rather than on load: it clones and reads the repository,
  // which is far too expensive to do for every row of a table nobody asked
  // a question of yet.
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [reading, setReading] = useState<string | null>(null);
  // Pre-creation inspection of the repository the form is describing. Read-only
  // (nothing is saved) and non-blocking: it warns, it does not stop the Add.
  const [inspection, setInspection] = useState<ReadinessReport | null>(null);
  const [inspecting, setInspecting] = useState(false);

  const load = async (archived = showArchived) => {
    setIsLoading(true);
    try {
      setTargets(await api.listTargets(archived));
      setError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "Unable to load repositories"
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load(showArchived);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchived]);

  // Archived repositories are still listed when the box is ticked, but a mode
  // that varies the repository can only vary the ones that still run.
  const activeTargets = targets.filter((target) => target.status !== "archived");

  const handleSubmit = async () => {
    setIsSaving(true);
    setError(null);
    try {
      const payload = {
        target_id: form.target_id.trim(),
        name: form.name.trim() || form.target_id.trim(),
        repository_url: form.repository_url.trim(),
        description: form.description,
        default_ref: form.default_ref.trim() || "main",
        setup_commands: toLines(form.setup),
        validation_commands: toLines(form.validation),
        language: form.language.trim(),
        team: form.team.trim()
      };
      if (editingId) {
        await api.updateTarget(editingId, payload);
      } else {
        await api.createTarget(payload);
      }
      setForm(EMPTY_FORM);
      setEditingId(null);
      setInspection(null);
      await load();
    } catch (saveError) {
      setError(
        saveError instanceof Error ? saveError.message : "Unable to save repository"
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleInspect = async () => {
    setInspecting(true);
    setError(null);
    setInspection(null);
    try {
      setInspection(
        await api.inspectTarget({
          repository_url: form.repository_url.trim(),
          default_ref: form.default_ref.trim() || "main",
          setup_commands: toLines(form.setup),
          validation_commands: toLines(form.validation)
        })
      );
    } catch (inspectError) {
      setError(
        inspectError instanceof Error
          ? inspectError.message
          : "Unable to inspect the repository"
      );
    } finally {
      setInspecting(false);
    }
  };

  const handleArchive = async (targetId: string) => {
    setError(null);
    try {
      await api.archiveTarget(targetId);
      await load();
    } catch (archiveError) {
      setError(
        archiveError instanceof Error
          ? archiveError.message
          : "Unable to archive repository"
      );
    }
  };

  const handleCheck = async (target: TargetRecord) => {
    setChecking(target.target_id);
    try {
      const resolved = await api.resolveTargetRef(target.target_id, target.default_ref);
      setPins((current) => ({ ...current, [target.target_id]: resolved }));
    } catch (checkError) {
      setPins((current) => ({
        ...current,
        [target.target_id]:
          checkError instanceof Error ? checkError.message : "Could not resolve"
      }));
    } finally {
      setChecking(null);
    }
  };

  const handleReadiness = async (targetId: string) => {
    setReading(targetId);
    setError(null);
    try {
      setReadiness(await api.getTargetReadiness(targetId));
    } catch (readError) {
      setError(
        readError instanceof Error ? readError.message : "Unable to read the repository"
      );
    } finally {
      setReading(null);
    }
  };

  const handleDerive = async (target: TargetRecord) => {
    setDeriving(target.target_id);
    setError(null);
    setDerived(null);
    try {
      setDerived(await api.deriveCases(target.target_id));
    } catch (deriveError) {
      setError(
        deriveError instanceof Error
          ? deriveError.message
          : "Unable to read the repository's history"
      );
    } finally {
      setDeriving(null);
    }
  };

  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <h1>Repositories</h1>
          <p className="muted">
            The repositories a case can be run against. A case is paired with a
            repository at the run, so the same case can be evaluated across several
            of them.
          </p>
        </div>
        {/*
          Cross-repo lives here rather than on the home page: it is the one mode
          that cannot be started without the thing this page holds. Offered on
          the front page it was a door that did not open; offered here it appears
          exactly when the second repository does.
        */}
        <div className="page-header-action">
          {activeTargets.length >= 2 ? (
            <Link className="primary" to="/evaluate/new?mode=cross_repo">
              Test the harness across these repositories
            </Link>
          ) : (
            <p className="muted">
              Cross-repo evaluation needs at least two active repositories to vary —{" "}
              {activeTargets.length === 0 ? "none" : "one"} registered so far.
            </p>
          )}
        </div>
      </header>

      <HelpPanel title="How a repository is pinned">
        <p>
          The checkout lives under <code>data/checkouts/</code>, because that is the
          only folder a run container can see. You put it there: clone it yourself with
          the command this page shows, attach a clone that is already in that folder
          under any name, or have the lab copy a repository you already have on this
          machine. The lab never fetches a remote on your behalf. It then{" "}
          <strong>pins the commit</strong> that checkout is on, and every run, readiness
          read and derived case works from that pinned commit until you re-pin it.
          Re-running later is the same experiment, not whatever was pushed since.
        </p>
        <p className="muted">
          <strong>Setup</strong> runs before the agent (for example{" "}
          <code>uv sync</code>). <strong>Validation</strong> are the repository's own
          gates, and they replace the case's when a run has a target — the repo knows
          how it is tested better than a portable case does.
        </p>
      </HelpPanel>

      {error !== null && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      <div className="card">
        <h2>{editingId ? `Edit ${editingId}` : "Add a repository"}</h2>
        <div className="form-grid">
          <label>
            <span>Id</span>
            <input
              disabled={editingId !== null}
              onChange={(event) =>
                setForm({ ...form, target_id: event.target.value })
              }
              placeholder="company-payments"
              value={form.target_id}
            />
          </label>
          <label>
            <span>Name</span>
            <input
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="Payments API"
              value={form.name}
            />
          </label>
          <label>
            <span>Repository URL</span>
            <input
              onChange={(event) =>
                setForm({ ...form, repository_url: event.target.value })
              }
              placeholder="https://github.com/company/payments-api.git"
              value={form.repository_url}
            />
          </label>
          <label>
            <span>Default ref</span>
            <input
              onChange={(event) =>
                setForm({ ...form, default_ref: event.target.value })
              }
              placeholder="main"
              value={form.default_ref}
            />
          </label>
          <label>
            <span>Language</span>
            <input
              onChange={(event) => setForm({ ...form, language: event.target.value })}
              placeholder="python"
              value={form.language}
            />
          </label>
          <label>
            <span>Team</span>
            <input
              onChange={(event) => setForm({ ...form, team: event.target.value })}
              placeholder="payments"
              value={form.team}
            />
          </label>
        </div>
        <label className="stacked-field">
          <span>Setup commands — one per line, run before the agent</span>
          <textarea
            onChange={(event) => setForm({ ...form, setup: event.target.value })}
            placeholder={"uv sync"}
            rows={3}
            value={form.setup}
          />
        </label>
        <label className="stacked-field">
          <span>Validation commands — one per line, the repo's own gates</span>
          <textarea
            onChange={(event) => setForm({ ...form, validation: event.target.value })}
            placeholder={"make lint\nmake test"}
            rows={3}
            value={form.validation}
          />
        </label>
        <div className="row gap">
          <button
            className="primary"
            disabled={
              isSaving ||
              form.target_id.trim() === "" ||
              form.repository_url.trim() === ""
            }
            onClick={() => void handleSubmit()}
            type="button"
          >
            {isSaving ? "Saving…" : editingId ? "Save changes" : "Add repository"}
          </button>
          <button
            disabled={inspecting || form.repository_url.trim() === ""}
            onClick={() => void handleInspect()}
            type="button"
          >
            {inspecting ? "Inspecting…" : "Inspect repository"}
          </button>
          {editingId !== null && (
            <button
              onClick={() => {
                setEditingId(null);
                setForm(EMPTY_FORM);
                setInspection(null);
              }}
              type="button"
            >
              Cancel
            </button>
          )}
        </div>
        <p className="muted small">
          Inspect reads the repository before you save it — it checks the runtime is
          one the runner image can build (Python or Node), and that your setup and
          validation commands name a toolchain the repository actually has. It only
          warns; you can still add the repository.
        </p>

        {inspection !== null &&
          (() => {
            const runtime = inspection.checks.find(
              (check) => check.label === "Runtime compatibility"
            );
            const bad = runtime !== undefined && runtime.status !== "ok";
            return (
              <div className="stack">
                {runtime !== undefined && (
                  <div className={`callout ${bad ? "callout--warning" : "callout--ok"}`}>
                    <div>
                      <strong>
                        {bad
                          ? "This repository is likely not to work in the lab"
                          : "Runtime looks compatible"}
                      </strong>
                      <p>{runtime.detail}</p>
                      {runtime.items.length > 0 && (
                        <ul className="check-items">
                          {runtime.items.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>
                )}
                <ReadinessReportView report={inspection} />
                <div className="row">
                  <button onClick={() => setInspection(null)} type="button">
                    Dismiss
                  </button>
                </div>
              </div>
            );
          })()}
      </div>

      <div className="card">
        <div className="row space-between">
          <h2>Registered repositories</h2>
          <label className="row gap">
            <input
              checked={showArchived}
              onChange={(event) => setShowArchived(event.target.checked)}
              type="checkbox"
            />
            <span>Show archived</span>
          </label>
        </div>

        {isLoading ? (
          <Spinner />
        ) : targets.length === 0 ? (
          <EmptyState
            description="Add one above to run a case against an external repository."
            title="No target repositories yet"
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Id</th>
                <th>Repository</th>
                <th>Default ref</th>
                <th>Resolves to</th>
                <th>Gates</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {targets.map((target) => {
                const pin = pins[target.target_id];
                return (
                  <tr key={target.target_id}>
                    <td>
                      <strong>{target.target_id}</strong>
                      {target.status === "archived" && (
                        <span className="badge"> archived</span>
                      )}
                      <div className="muted small">{target.name}</div>
                    </td>
                    <td className="mono small">{target.repository_url}</td>
                    <td className="mono small">{target.default_ref}</td>
                    <td className="mono small">
                      {checking === target.target_id ? (
                        "checking…"
                      ) : pin === undefined ? (
                        <button onClick={() => void handleCheck(target)} type="button">
                          Check
                        </button>
                      ) : typeof pin === "string" ? (
                        <span className="error-text">{pin}</span>
                      ) : (
                        <span title={pin.commit}>{pin.commit.slice(0, 10)}</span>
                      )}
                    </td>
                    <td className="small">
                      {target.validation_commands.length > 0
                        ? `${target.validation_commands.length} command(s)`
                        : "case default"}
                    </td>
                    <td className="row gap">
                      {/* The workbench, where this repository's checkout, cases
                          and experiments accumulate. */}
                      <Link
                        className="button-link"
                        to={`/evidence/repositories/${target.target_id}`}
                      >
                        Open
                      </Link>
                      <button
                        onClick={() => {
                          setEditingId(target.target_id);
                          setForm(formFrom(target));
                        }}
                        type="button"
                      >
                        Edit
                      </button>
                      <button
                        disabled={reading !== null}
                        onClick={() => void handleReadiness(target.target_id)}
                        type="button"
                      >
                        {reading === target.target_id ? "Reading…" : "Readiness"}
                      </button>
                      <button
                        disabled={deriving !== null}
                        onClick={() => void handleDerive(target)}
                        type="button"
                      >
                        {deriving === target.target_id ? "Reading…" : "Derive cases"}
                      </button>
                      {target.status === "active" && (
                        <button
                          onClick={() => void handleArchive(target.target_id)}
                          type="button"
                        >
                          Archive
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {readiness !== null && (
        <div className="card">
          <div className="row space-between">
            <h2>Readiness — {readiness.target_id}</h2>
            <span className={`badge ${readiness.ready ? "ok" : "warn"}`}>
              {readiness.ready ? "Ready to run" : "Not runnable yet"}
            </span>
          </div>
          <p className="muted small">
            "Ready" is a narrow claim: a run against this repository would execute
            and be scored. It says nothing about how good the repository's own AI
            governance is.
          </p>
          <ReadinessReportView
            adopting={reading !== null}
            onAdopt={(payload) => {
              void (async () => {
                setReading(readiness.target_id);
                try {
                  await api.updateTarget(readiness.target_id, payload);
                  setTargets(await api.listTargets());
                  setReadiness(await api.getTargetReadiness(readiness.target_id));
                } catch (adoptError) {
                  setError(
                    adoptError instanceof Error
                      ? adoptError.message
                      : "Unable to adopt the inferred commands"
                  );
                } finally {
                  setReading(null);
                }
              })();
            }}
            report={readiness}
          />
          <button onClick={() => setReadiness(null)} type="button">
            Close
          </button>
        </div>
      )}

      {derived !== null && (
        <div className="card">
          <h2>
            Candidate cases from {derived.target_id} ({derived.candidates.length})
          </h2>
          <p className="muted">
            Mined from commits that changed source <strong>and</strong> its tests
            together — tasks this team performs and already knows how to verify.
            Nothing is saved: read them, keep the ones the team would recognise as
            their work, and create those as cases.
          </p>
          <p className="muted small">
            Each candidate carries the commit it came from, which is what lets you
            show later that the case predates the results instead of having been
            written to fit them. Freeze the case and its rubric{" "}
            <strong>before</strong> running either harness condition.
          </p>
          {derived.candidates.length === 0 ? (
            <EmptyState
              description="No commit in the scanned history changed source and tests together. Widen the scan, or the repository may not keep tests in recognisable paths."
              title="No candidates found"
            />
          ) : (
            <ul className="candidate-list">
              {derived.candidates.map((candidate: CaseCandidate) => (
                <li key={candidate.commit}>
                  <button
                    className="skills-toggle"
                    onClick={() =>
                      setExpanded(
                        expanded === candidate.commit ? null : candidate.commit
                      )
                    }
                    type="button"
                  >
                    {expanded === candidate.commit ? "▾" : "▸"} {candidate.subject}
                    <span className="muted small">
                      {" "}
                      {candidate.commit.slice(0, 7)} · {candidate.source_files.length}{" "}
                      file(s) · {candidate.test_files.length} test(s)
                    </span>
                  </button>
                  {expanded === candidate.commit && (
                    <div className="stack">
                      <label className="stacked-field">
                        <span>Suggested case id</span>
                        <input readOnly value={candidate.suggested_case_id} />
                      </label>
                      <label className="stacked-field">
                        <span>Suggested prompt — edit before using</span>
                        <textarea
                          readOnly
                          rows={10}
                          value={candidate.suggested_prompt}
                        />
                      </label>
                      <p className="muted small">
                        Create it from this repository&apos;s workbench (Open →
                        Cases), so it carries <code>origin_target_id</code>.
                      </p>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
