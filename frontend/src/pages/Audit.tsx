import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconArrowRight } from "../components/icons";
import type {
  CaseCandidate,
  CaseCandidates,
  ReadinessReport,
  SuggestionRecord,
  TargetRecord
} from "../types";

const STEPS = [
  { key: "repository", label: "Select repository" },
  { key: "readiness", label: "Repository readiness" },
  { key: "cases", label: "Derive cases" },
  { key: "discovery", label: "Run discovery" },
  { key: "findings", label: "Review findings" },
  { key: "experiment", label: "Turn a finding into an experiment" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

function candidateToCase(candidate: CaseCandidate, target: TargetRecord) {
  return {
    case_id: candidate.suggested_case_id,
    name: candidate.subject.slice(0, 100),
    type: "single_prompt",
    description: `Derived from ${target.name} at ${candidate.commit.slice(0, 8)}.`,
    tags: ["derived", target.target_id],
    prompt_variants: { default: candidate.suggested_prompt },
    step_prompts: [],
    success_criteria: {},
    scoring_rubric: "",
    constraints: [],
    // The repository's own tests, not a rubric the lab invented for it.
    validation_commands: target.validation_commands,
    max_steps: 40
  };
}

/**
 * Mode C as a workflow rather than a button.
 *
 * The six steps are the audit an engineer would actually perform on a client
 * repository, in order, with the lab's own claims kept narrow at every one of
 * them: readiness is *detected*, cases are *proposed*, findings are *suggested*.
 * Nothing here writes a claim into the record without a person accepting it.
 */
export function Audit() {
  const navigate = useNavigate();
  const [step, setStep] = useState<StepKey>("repository");
  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [targetId, setTargetId] = useState("");
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<CaseCandidates | null>(null);
  const [accepted, setAccepted] = useState<string[]>([]);
  // The ids of the cases actually created — what the discovery experiment
  // runs. Kept apart from `accepted` (commit hashes for the checklist UI).
  const [acceptedCaseIds, setAcceptedCaseIds] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionRecord[]>([]);
  const [author, setAuthor] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    api
      .listTargets()
      .then(setTargets)
      .catch(() => setError("Unable to load repositories."));
  }, []);

  const target = useMemo(
    () => targets.find((item) => item.target_id === targetId) ?? null,
    [targetId, targets]
  );

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (workError) {
      setError(workError instanceof Error ? workError.message : "Something failed");
    } finally {
      setBusy("");
    }
  };

  const loadReadiness = (id: string) =>
    run("readiness", async () => {
      setReadiness(await api.getTargetReadiness(id));
      setStep("readiness");
    });

  const adoptInferred = (payload: {
    validation_commands?: string[];
    setup_commands?: string[];
  }) =>
    run("adopt", async () => {
      await api.updateTarget(targetId, payload);
      setNotice(
        "Adopted. Future runs against this repository validate with its own " +
          "commands — edit them any time under Evidence → Repositories."
      );
      await loadReadiness(targetId);
    });

  const generateReport = () =>
    run("report", async () => {
      const payload = await api.getAuditReport(targetId);
      setReport(payload.markdown);
    });

  const derive = () =>
    run("derive", async () => {
      const derived = await api.deriveCases(targetId, 15);
      setCandidates(derived);
      // Candidates a past audit already turned into cases start ticked, so
      // the button that would only ever answer "already exists" is never
      // offered in the first place.
      try {
        const known = new Set((await api.listCases()).map((item) => item.case_id));
        const existing = derived.candidates.filter((candidate) =>
          known.has(candidate.suggested_case_id)
        );
        if (existing.length > 0) {
          setAccepted((current) => [
            ...new Set([...current, ...existing.map((c) => c.commit)])
          ]);
          setAcceptedCaseIds((current) => [
            ...new Set([...current, ...existing.map((c) => c.suggested_case_id)])
          ]);
        }
      } catch {
        // Catalog unlistable — the accept button handles duplicates anyway.
      }
      setStep("cases");
    });

  const acceptCandidate = (candidate: CaseCandidate) =>
    run(candidate.commit, async () => {
      if (!target) {
        return;
      }
      const payload = candidateToCase(candidate, target);
      try {
        const created = await api.createCase(payload);
        setAcceptedCaseIds((current) => [...current, created.case_id]);
      } catch (createError) {
        // A past audit already accepted this candidate. That is the outcome
        // the button was after, so it reads as success — the case simply
        // joins the discovery set like any other.
        const alreadyThere =
          (createError instanceof ApiError && createError.status === 409) ||
          (createError instanceof Error &&
            createError.message.includes("already exists"));
        if (!alreadyThere) {
          throw createError;
        }
        setAcceptedCaseIds((current) =>
          current.includes(payload.case_id) ? current : [...current, payload.case_id]
        );
        setNotice(
          `'${payload.case_id}' already existed from an earlier audit — reusing it.`
        );
      }
      setAccepted((current) => [...current, candidate.commit]);
    });

  const startDiscovery = () =>
    run("discovery", async () => {
      // This session's accepted cases, plus every case previously derived from
      // this repository (tagged at creation) — so re-entering the audit after
      // a reload still finds the cases a past session created.
      const caseIds = new Set(acceptedCaseIds);
      try {
        for (const item of await api.listCases()) {
          if ((item.tags ?? []).includes(targetId)) {
            caseIds.add(item.case_id);
          }
        }
      } catch {
        // Cases unlistable — proceed with what this session accepted.
      }
      if (caseIds.size === 0) {
        setError(
          "No cases yet for this repository. Accept at least one derived case " +
            "in the previous step — a discovery run with no cases measures nothing."
        );
        setStep("cases");
        return;
      }
      // A previous audit may already have created `audit-<target>`; each
      // discovery round is its own experiment, so suffix rather than collide.
      const suffix = Date.now().toString(36).slice(-4);
      const experiment = await api.createExperiment({
        experiment_id: `audit-${targetId}-${suffix}`.slice(0, 64),
        name: `Audit: ${target?.name ?? targetId}`,
        question: `How does ${target?.name ?? targetId} currently behave under an agent?`,
        mode: "discovery",
        case_ids: [...caseIds].sort(),
        repetitions: 2,
        conditions: [
          {
            label: "as-is",
            role: "treatment",
            description: "The repository exactly as the team has it today.",
            overrides: { target_repo_id: targetId, harness_source: "native" }
          }
        ],
        notes:
          "Exploratory discovery run. Produces observations and suggested findings; " +
          "it cannot support a causal claim."
      });
      navigate(`/evaluate/experiments?experiment=${experiment.experiment_id}`);
    });

  const loadSuggestions = () =>
    run("suggestions", async () => {
      const payload = await api.listSuggestions();
      setSuggestions(payload.suggestions);
      setStep("findings");
    });

  const accept = (suggestion: SuggestionRecord) =>
    run(suggestion.key, async () => {
      const result = await api.acceptSuggestion(suggestion.key, { author });
      setSuggestions((current) =>
        current.filter((item) => item.key !== suggestion.key)
      );
      setNotice(
        `Accepted as finding '${result.finding.finding_id}'. It is now on the record ` +
          `under your name.`
      );
      setStep("experiment");
    });

  const reject = (suggestion: SuggestionRecord) =>
    run(suggestion.key, async () => {
      await api.rejectSuggestion(suggestion.key, {
        author,
        reason: "Reviewed during the repository audit and not taken forward."
      });
      setSuggestions((current) =>
        current.filter((item) => item.key !== suggestion.key)
      );
    });

  const stepIndex = STEPS.findIndex((item) => item.key === step);

  return (
    <section className="page audit">
      <header className="page-header">
        <div>
          <h1>Audit a repository</h1>
          <p className="lede">
            Exploratory by design. This produces cases, observations and suggested
            findings — never a causal verdict about what caused what.
          </p>
        </div>
      </header>

      <ol className="stepper">
        {STEPS.map((item, index) => (
          <li
            className={[
              "stepper-item",
              item.key === step ? "stepper-item--current" : "",
              index < stepIndex ? "stepper-item--done" : ""
            ]
              .filter(Boolean)
              .join(" ")}
            key={item.key}
          >
            <button onClick={() => setStep(item.key)} type="button">
              <span className="stepper-index">{index + 1}</span>
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner message={error} /> : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}

      {step === "repository" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Which repository are we auditing?</h2>
            <Link className="button-link" to="/evidence/repositories">
              Register a new one
            </Link>
          </div>
          {targets.length === 0 ? (
            <EmptyState
              action={
                <Link className="primary" to="/evidence/repositories">
                  Add a repository
                </Link>
              }
              description="An audit reads a real repository at a real commit. Register the one you want to look at first."
              title="No repositories registered yet."
            />
          ) : (
            <ul className="pick-list">
              {targets.map((item) => (
                <li key={item.target_id}>
                  <button
                    className={targetId === item.target_id ? "primary" : "secondary"}
                    onClick={() => {
                      setTargetId(item.target_id);
                      void loadReadiness(item.target_id);
                    }}
                    type="button"
                  >
                    <strong>{item.name}</strong>
                    <span className="muted">{item.repository_url}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {step === "readiness" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>What this repository already has</h2>
            {readiness ? (
              <span className={`badge ${readiness.ready ? "ok" : "warn"}`}>
                {readiness.ready ? "Ready to run" : "Not runnable yet"}
              </span>
            ) : null}
          </div>
          {busy === "readiness" ? <Spinner label="Reading the repository" /> : null}
          {readiness ? (
            <>
              <ReadinessReportView
                adopting={busy === "adopt"}
                onAdopt={adoptInferred}
                report={readiness}
              />
              <div className="toolbar">
                <button
                  className="primary"
                  disabled={busy !== ""}
                  onClick={() => void derive()}
                  type="button"
                >
                  {busy === "derive" ? "Reading history…" : "Derive cases from history"}
                  <IconArrowRight size={15} />
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {step === "cases" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Candidate cases</h2>
            {candidates ? (
              <span className="badge neutral">
                {candidates.candidates.length} of {candidates.scanned} commits scanned
              </span>
            ) : null}
          </div>
          <p className="muted">
            Proposed from the repository's own history — real changes this team already
            made. Read each one before accepting it; a case nobody vetted measures
            nothing you can defend.
          </p>
          {candidates && candidates.candidates.length === 0 ? (
            <EmptyState
              description="No commit in the scanned range paired a source change with a test change, which is what makes a commit reproducible as a case."
              title="Nothing derivable here."
            />
          ) : null}
          <ul className="candidate-list">
            {candidates?.candidates.map((candidate) => (
              <li className="candidate" key={candidate.commit}>
                <div>
                  <strong>{candidate.subject}</strong>
                  <p className="muted">
                    <code>{candidate.commit.slice(0, 8)}</code> ·{" "}
                    {candidate.source_files.length} source ·{" "}
                    {candidate.test_files.length} test
                  </p>
                  <pre className="candidate-prompt">{candidate.suggested_prompt}</pre>
                </div>
                {accepted.includes(candidate.commit) ? (
                  <span className="badge ok">Case in the catalog</span>
                ) : (
                  <button
                    className="secondary"
                    disabled={busy !== ""}
                    onClick={() => void acceptCandidate(candidate)}
                    type="button"
                  >
                    Create this case
                  </button>
                )}
              </li>
            ))}
          </ul>
          <div className="toolbar">
            <button className="primary" onClick={() => setStep("discovery")} type="button">
              Continue to discovery
              <IconArrowRight size={15} />
            </button>
          </div>
        </div>
      ) : null}

      {step === "discovery" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Run discovery</h2>
          </div>
          <p className="callout callout--warning">
            <IconAlert size={15} />
            Discovery observes the repository as the team has it — native governance,
            nothing injected. It has no control arm, so it can describe what happens
            but never why.
          </p>
          <p className="muted">
            This creates a discovery experiment against{" "}
            <strong>{target?.name ?? targetId}</strong> carrying every case derived
            from it — the ones you accepted here and any from earlier audits. You
            launch it from its page, where the model and budget are chosen.
          </p>
          <div className="toolbar">
            <button
              className="primary"
              disabled={busy !== "" || !targetId}
              onClick={() => void startDiscovery()}
              type="button"
            >
              {busy === "discovery" ? "Creating…" : "Create discovery experiment"}
              <IconArrowRight size={15} />
            </button>
            <button className="secondary" onClick={() => void loadSuggestions()} type="button">
              Skip to findings
            </button>
          </div>
        </div>
      ) : null}

      {step === "findings" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Suggested findings</h2>
            <button
              className="secondary"
              disabled={busy !== ""}
              onClick={() => void loadSuggestions()}
              type="button"
            >
              Refresh
            </button>
          </div>
          <p className="muted">
            Patterns the lab noticed in the run corpus. None of them is a finding until
            somebody accepts it and their name goes on it.
          </p>
          <label className="field">
            <span>Your name (required to accept or reject)</span>
            <input
              onChange={(event) => setAuthor(event.target.value)}
              placeholder="Who is signing off on this?"
              value={author}
            />
          </label>
          {busy === "suggestions" ? <Spinner label="Reading the corpus" /> : null}
          {suggestions.length === 0 && busy === "" ? (
            <EmptyState
              description="Either the corpus shows no repeated pattern yet, or every suggestion has already been accepted or rejected."
              title="Nothing suggested right now."
            />
          ) : null}
          <ul className="suggestion-list">
            {suggestions.map((suggestion) => (
              <li className="suggestion" key={suggestion.key}>
                <div className="suggestion-head">
                  <span className="badge warn">Suggested</span>
                  <span className={`badge ${suggestion.severity === "high" ? "error" : ""}`}>
                    {suggestion.severity}
                  </span>
                  <strong>{suggestion.title}</strong>
                </div>
                <p>{suggestion.body}</p>
                {suggestion.recommendation ? (
                  <p className="muted">
                    <strong>Recommended: </strong>
                    {suggestion.recommendation}
                  </p>
                ) : null}
                {suggestion.evidence_run_ids.length > 0 ? (
                  <p className="muted">
                    Evidence: {suggestion.evidence_run_ids.length} run(s)
                  </p>
                ) : null}
                <div className="toolbar">
                  <button
                    className="primary"
                    disabled={!author.trim() || busy !== ""}
                    onClick={() => void accept(suggestion)}
                    type="button"
                  >
                    Accept as finding
                  </button>
                  <button
                    className="secondary"
                    disabled={!author.trim() || busy !== ""}
                    onClick={() => void reject(suggestion)}
                    type="button"
                  >
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {step === "experiment" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Turn a finding into an experiment</h2>
          </div>
          <p className="muted">
            An accepted finding is a claim someone is prepared to defend. The way to
            settle it is to test it: open the finding and draft the experiment its
            recommendation implies, then edit the design before creating it.
          </p>
          <div className="toolbar">
            <Link className="primary" to="/evidence/findings">
              Open findings
              <IconArrowRight size={15} />
            </Link>
            <button
              className="secondary"
              disabled={busy !== "" || !targetId}
              onClick={() => void generateReport()}
              type="button"
            >
              {busy === "report" ? "Assembling…" : "Generate client report"}
            </button>
          </div>
          {report ? (
            <div className="card">
              <div className="panel-head">
                <h3>Client report (markdown)</h3>
                <button
                  className="secondary"
                  onClick={() => {
                    void navigator.clipboard.writeText(report);
                    setNotice("Report copied to the clipboard.");
                  }}
                  type="button"
                >
                  Copy
                </button>
              </div>
              <p className="muted small">
                Assembled live from the evidence — readiness, derived cases,
                experiments and signed findings. Regenerate after more runs and
                it simply says more. It never claims what was not measured.
              </p>
              <pre className="candidate-prompt" style={{ maxHeight: 480 }}>
                {report}
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
