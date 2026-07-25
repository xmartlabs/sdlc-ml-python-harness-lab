import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { ArtifactsPanel } from "../components/ArtifactsPanel";
import { ErrorBanner } from "../components/ErrorBanner";
import { HarnessCatalog } from "../components/HarnessCatalog";
import { LogViewer } from "../components/LogViewer";
import { Markdown } from "../components/Markdown";
import { SandboxPanel } from "../components/SandboxPanel";
import { StateBadge } from "../components/StateBadge";
import type {
  AttributionRecord,
  AuditRecord,
  ImprovementsDoc,
  ProposalIssue,
  RunEventRecord,
  RunMetadata,
  RunRecord,
  SaveImprovementsResult,
  ValidationRecord
} from "../types";

type RunDetailProps = {
  run: RunRecord | null;
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
};

const POLL_INTERVAL_MS = 1500;
export const CANCELLABLE_STATES: RunRecord["state"][] = ["pending", "running"];
export const RETRYABLE_STATES: RunRecord["state"][] = ["failed", "cancelled"];

// Harnesses whose CLI emits final summaries, not per-tool logs — so file/tool/
// governance attribution can't be parsed from them. claude runs now capture
// stream-json events (first-class tool calls), so a claude run only falls back
// to the summary-only note when its attribution carries no signals (old runs).
const SUMMARY_ONLY_HARNESSES = new Set(["claude", "codex"]);

function harnessFromCommand(command: string[] | undefined): string {
  if (!command) return "opencode";
  const i = command.indexOf("--harness");
  return i >= 0 && i + 1 < command.length ? command[i + 1] : "opencode";
}

export function RunDetail({ run, onCancelRun, onRetryRun, onScoreRun }: RunDetailProps) {
  const harness = harnessFromCommand(run?.command);
  const [events, setEvents] = useState<RunEventRecord[]>([]);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [scoreValue, setScoreValue] = useState(0);
  const [interventionsValue, setInterventionsValue] = useState(0);
  const [notesValue, setNotesValue] = useState("");
  const [validation, setValidation] = useState<ValidationRecord | null>(null);
  const [attribution, setAttribution] = useState<AttributionRecord | null>(null);
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [metadata, setMetadata] = useState<RunMetadata | null>(null);
  const [auditModel, setAuditModel] = useState("");
  const [improvements, setImprovements] = useState<ImprovementsDoc | null>(null);
  const [improving, setImproving] = useState(false);
  const [improveError, setImproveError] = useState<string | null>(null);
  const [savingProposals, setSavingProposals] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveImprovementsResult | null>(null);
  const [improveStrictness, setImproveStrictness] = useState("balanced");
  const [proposalIssue, setProposalIssue] = useState<ProposalIssue | null>(null);
  const [proposalLoading, setProposalLoading] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [proposalNotice, setProposalNotice] = useState<string | null>(null);
  const [proposalCopied, setProposalCopied] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [tab, setTab] = useState<"detail" | "terminal" | "desktop">("detail");
  const consoleRef = useRef<HTMLDivElement | null>(null);
  // A claude run with events-based attribution has real tool counts; only fall
  // back to the summary-only explainer when there is nothing to show.
  const attributionHasSignals = Boolean(
    attribution &&
      (Object.values(attribution.tools ?? {}).some((count) => count > 0) ||
        (attribution.files?.read?.length ?? 0) > 0 ||
        (attribution.files?.edited?.length ?? 0) > 0)
  );
  const summaryOnlyHarness =
    SUMMARY_ONLY_HARNESSES.has(harness) && !attributionHasSignals;

  useEffect(() => {
    setEvents([]);
    setActionError(null);

    if (run === null) {
      return;
    }

    let active = true;

    const fetchLogs = () => {
      api
        .listRunLogs(run.run_id)
        .then((fetched) => {
          if (active) {
            setEvents(fetched);
          }
        })
        .catch(() => {
          // Log fetching is best-effort; keep showing the last known events.
        });
    };

    fetchLogs();

    if (run.state !== "running") {
      return () => {
        active = false;
      };
    }

    const interval = window.setInterval(fetchLogs, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [run?.run_id, run?.state]);

  useEffect(() => {
    // The scrollable element is the LogViewer's inner console, not the wrapper.
    const console = consoleRef.current?.querySelector(".run-console");
    if (console) {
      console.scrollTop = console.scrollHeight;
    }
  }, [events]);

  useEffect(() => {
    if (run === null) {
      return;
    }
    setScoreValue(run.score ?? 0);
    setInterventionsValue(run.interventions ?? 0);
    setNotesValue(run.notes ?? "");
  }, [run?.run_id, run?.score, run?.interventions, run?.notes]);

  useEffect(() => {
    setValidation(null);
    setAttribution(null);
    setAudit(null);
    setMetadata(null);
    setImprovements(null);
    setImproveError(null);
    setProposalIssue(null);
    setProposalError(null);
    setProposalNotice(null);
    setProposalCopied(false);
    if (run === null) {
      return;
    }
    let active = true;
    const timers: number[] = [];
    // Validation/attribution are computed by the worker AFTER the state flips
    // to awaiting_manual_score (validation reinstalls the repo — can take a
    // minute). Retry a few times instead of freezing on "No validation yet".
    const fetchWithRetry = (
      fetcher: () => Promise<void>,
      tries: number,
      delayMs: number
    ) => {
      fetcher().catch(() => {
        if (active && tries > 0) {
          timers.push(window.setTimeout(() => fetchWithRetry(fetcher, tries - 1, delayMs), delayMs));
        }
      });
    };
    fetchWithRetry(
      () =>
        api.getValidation(run.run_id).then((result) => {
          if (active) {
            setValidation(result);
          }
        }),
      run.state === "running" || run.state === "pending" ? 0 : 20,
      6000
    );
    fetchWithRetry(
      () =>
        api.getAttribution(run.run_id).then((result) => {
          if (active) {
            setAttribution(result);
          }
        }),
      run.state === "running" || run.state === "pending" ? 0 : 5,
      6000
    );
    api
      .getAudit(run.run_id)
      .then((result) => {
        if (active) {
          setAudit(result);
        }
      })
      .catch(() => {
        // No audit yet (404) is expected until one is requested.
      });
    api
      .getImprovements(run.run_id)
      .then((result) => {
        if (active) {
          setImprovements(result);
        }
      })
      .catch(() => {
        // No improvements yet (404) is expected until synthesis is requested.
      });
    const parseMetadata = (content: string) => {
      if (!active) {
        return;
      }
      try {
        setMetadata(JSON.parse(content) as RunMetadata);
      } catch {
        // Unreadable metadata — the catalog panel shows its empty hint.
      }
    };
    api
      .readArtifact(run.run_id, "metadata.json")
      .then((result) => parseMetadata(result.content))
      .catch(() => {
        // Not indexed yet (older runs) — index once, then retry the read.
        api
          .indexArtifacts(run.run_id)
          .then(() => api.readArtifact(run.run_id, "metadata.json"))
          .then((result) => parseMetadata(result.content))
          .catch(() => {
            // No metadata.json at all (e.g. dry-run) — leave the hint visible.
          });
      });
    return () => {
      active = false;
      timers.forEach((timer) => window.clearTimeout(timer));
    };
  }, [run?.run_id, run?.state]);

  if (run === null) {
    return (
      <section className="run-detail-page">
        <Link className="run-detail-back" to="/runs">
          ← Runs
        </Link>
        <p className="muted">Select a run to view status, artifacts, score, and audit.</p>
      </section>
    );
  }

  const canCancel = CANCELLABLE_STATES.includes(run.state);
  const canRetry = RETRYABLE_STATES.includes(run.state);

  const handleCancel = () => {
    setActionPending(true);
    setActionError(null);
    onCancelRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRetry = () => {
    setActionPending(true);
    setActionError(null);
    onRetryRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleScore = () => {
    const clampedScore = Math.max(0, Math.min(10, scoreValue));
    const normalizedInterventions = Math.max(0, interventionsValue);
    setActionPending(true);
    setActionError(null);
    onScoreRun(run.run_id, clampedScore, normalizedInterventions, notesValue)
      .then(() => api.getValidation(run.run_id).then(setValidation).catch(() => {}))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRevalidate = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .validateRun(runId)
      .then(() => {
        // Validation runs asynchronously in the worker; poll for the result.
        let tries = 0;
        const poll = () => {
          api
            .getValidation(runId)
            .then((result) => setValidation(result))
            .catch(() => {});
          tries += 1;
          if (tries < 8) {
            window.setTimeout(poll, 3000);
          }
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  // SWE-bench validations carry the official verdict in the summary line
  // ("resolved=True F2P=1/1 P2P=18/18") written by swebench_validate.py.
  const isSwebench = validation?.objective?.summary?.startsWith("resolved=") ?? false;

  const objectiveBadge = (passed: boolean | null | undefined) => {
    if (isSwebench) {
      if (passed === true) return <span className="badge ok">SWE-bench: RESOLVED</span>;
      if (passed === false) return <span className="badge error">SWE-bench: NOT RESOLVED</span>;
    }
    if (passed === true) return <span className="badge ok">objective: pass</span>;
    if (passed === false) return <span className="badge error">objective: fail</span>;
    return <span className="badge">objective: manual</span>;
  };

  const scoreSourceBadge = (source: string | undefined) => {
    if (source === "llm_judge") return <span className="badge">LLM-judged</span>;
    if (source === "objective") return <span className="badge">auto (tests)</span>;
    if (source === "manual") return <span className="badge">manual</span>;
    return null;
  };

  const handleIndexAttribution = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .indexAttribution(runId)
      .then((result) => setAttribution(result))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRunAudit = () => {
    const runId = run.run_id;
    const model = auditModel.trim() || undefined;
    setActionPending(true);
    setActionError(null);
    setAudit({
      run_id: runId,
      auditor_model: model ?? "",
      status: "running",
      markdown: "",
      error: null,
      created_at: "",
      updated_at: ""
    });
    api
      .runAudit(runId, model)
      .then(() => {
        // The audit is an LLM call in the worker; poll until it settles.
        let tries = 0;
        const poll = () => {
          api
            .getAudit(runId)
            .then((result) => {
              setAudit(result);
              if (result.status === "running" && tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            })
            .catch(() => {
              if (tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            });
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleImprove = () => {
    const runId = run.run_id;
    setImproving(true);
    setImproveError(null);
    api
      .runImprove(runId, auditModel.trim() || undefined, improveStrictness)
      .then(() => {
        // The synthesis is an LLM call in the worker; poll for the artifact.
        let tries = 0;
        const poll = () => {
          api
            .getImprovements(runId)
            .then((result) => {
              setImprovements(result);
              setImproving(false);
            })
            .catch(() => {
              if (tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              } else {
                setImproving(false);
                setImproveError(
                  "Synthesis did not produce proposals — check the run logs."
                );
              }
            });
        };
        window.setTimeout(poll, 4000);
      })
      .catch((error: Error) => {
        setImproving(false);
        setImproveError(error.message);
      });
  };

  const handleSaveProposals = () => {
    const runId = run.run_id;
    setSavingProposals(true);
    api
      .saveImprovements(runId)
      .then((result) => {
        setSaveResult(result);
        setImproveError(null);
      })
      .catch((error: Error) => setImproveError(error.message))
      .finally(() => setSavingProposals(false));
  };

  const handleGenerateIssue = () => {
    const runId = run.run_id;
    setProposalLoading(true);
    setProposalError(null);
    setProposalNotice(null);
    setProposalCopied(false);
    api
      .getProposalIssue(runId)
      .then((result) => {
        setProposalIssue(result);
      })
      .catch((error: Error) => {
        // A 409 means the prerequisite isn't ready (no finished audit) — surface it
        // as a soft, actionable notice rather than a red error banner.
        if (error instanceof ApiError && error.status === 409) {
          setProposalNotice(error.message);
        } else {
          setProposalError(error.message);
        }
      })
      .finally(() => setProposalLoading(false));
  };

  const handleCopyIssue = () => {
    if (!proposalIssue) {
      return;
    }
    navigator.clipboard
      .writeText(proposalIssue.markdown)
      .then(() => {
        setProposalCopied(true);
        window.setTimeout(() => setProposalCopied(false), 2000);
      })
      .catch(() => {
        // Clipboard access denied — the markdown stays selectable in the <pre>.
      });
  };

  const handleCopyField = (key: string, text: string) => {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        setCopiedField(key);
        window.setTimeout(
          () => setCopiedField((current) => (current === key ? null : current)),
          2000
        );
      })
      .catch(() => {
        // Clipboard access denied — the value stays selectable in the field.
      });
  };

  const countEntries = (counts: Record<string, number>) =>
    Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${name}×${count}`)
      .join("  ");

  return (
    <section className="run-detail-page">
      <Link className="run-detail-back" to="/runs">
        ← Runs
      </Link>
      <div className="run-detail-header">
        <p className="eyebrow">Run detail</p>
        <h2>{run.run_id}</h2>
        <StateBadge state={run.state} />
        <div className="run-actions">
          <button disabled={!canCancel || actionPending} onClick={handleCancel} type="button">
            Cancel
          </button>
          <button disabled={!canRetry || actionPending} onClick={handleRetry} type="button">
            Retry
          </button>
        </div>
        {actionError !== null ? <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} /> : null}
      </div>
      <div className="run-tabs">
        {(["detail", "terminal", "desktop"] as const).map((name) => (
          <button
            className={tab === name ? "run-tab active" : "run-tab"}
            key={name}
            onClick={() => setTab(name)}
            type="button"
          >
            {name === "detail" ? "Detail" : name === "terminal" ? "Terminal" : "Desktop"}
          </button>
        ))}
      </div>
      {tab !== "detail" ? <SandboxPanel key={tab} kind={tab} runId={run.run_id} /> : null}
      {tab === "detail" ? (
        <>
      <div className="detail-grid">
        <div>
          <span>Score</span>
          <strong>{run.score ?? "—"}</strong>
        </div>
        <div>
          <span>Interventions</span>
          <strong>{run.interventions ?? "—"}</strong>
        </div>
        <div>
          <span>Exit code</span>
          <strong>{run.exit_code ?? "—"}</strong>
        </div>
        <div>
          <span>Started</span>
          <strong>{run.started_at ?? "—"}</strong>
        </div>
        <div>
          <span>Ended</span>
          <strong>{run.ended_at ?? "—"}</strong>
        </div>
        <div>
          <span>Failure</span>
          <strong>{run.failure_reason ?? "None"}</strong>
        </div>
      </div>
      {run.state === "awaiting_manual_score" ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>Manual score</h3>
            <span className="muted">
              {isSwebench
                ? "SWE-bench runs auto-score from validation — use Re-validate below instead"
                : "Required to complete run"}
            </span>
          </div>
          <div className="form-grid score-form-grid">
            <label>
              <span>Score (0-10)</span>
              <input
                max={10}
                min={0}
                onChange={(event) => setScoreValue(Number(event.target.value))}
                type="number"
                value={scoreValue}
              />
            </label>
            <label>
              <span>Interventions</span>
              <input
                min={0}
                onChange={(event) => setInterventionsValue(Number(event.target.value))}
                type="number"
                value={interventionsValue}
              />
            </label>
            <label className="full">
              <span>Notes</span>
              <textarea onChange={(event) => setNotesValue(event.target.value)} value={notesValue} />
            </label>
          </div>
          <div className="panel-actions score-panel-actions">
            <button className="primary" disabled={actionPending} onClick={handleScore} type="button">
              {actionPending ? "Saving..." : "Submit score"}
            </button>
          </div>
        </div>
      ) : null}
      <div className="run-detail-grid">
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Validation & score</h3>
          <div className="run-actions">
            {validation ? objectiveBadge(validation.objective?.passed) : null}
            <button disabled={actionPending} onClick={handleRevalidate} type="button">
              Re-validate
            </button>
          </div>
        </div>
        {validation === null ? (
          <p className="muted">
            No validation yet. Objective validation runs automatically when a run
            finishes; use Re-validate to run it now.
          </p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>Composite</span>
                <strong>{validation.composite ?? "—"}</strong>
              </div>
              <div>
                <span>Functional</span>
                <strong>
                  {validation.subjective?.functional_score ?? "—"}{" "}
                  {scoreSourceBadge(validation.subjective?.score_source)}
                </strong>
              </div>
              <div>
                <span>Objective</span>
                <strong>{validation.objective?.summary ?? "—"}</strong>
              </div>
              <div>
                <span>Tokens</span>
                <strong>{validation.cost?.total_tokens ?? "—"}</strong>
              </div>
              <div>
                <span>Cost (USD)</span>
                <strong>{validation.cost?.cost_usd ?? "—"}</strong>
              </div>
              <div>
                <span>Steps</span>
                <strong>{validation.effort?.steps ?? "—"}</strong>
              </div>
              <div>
                <span>Interventions</span>
                <strong>{validation.effort?.interventions ?? "—"}</strong>
              </div>
              <div>
                <span>Time (min)</span>
                <strong>{validation.cost?.time_minutes ?? "—"}</strong>
              </div>
            </div>
            {validation.objective?.checks && validation.objective.checks.length > 0 ? (
              <div className="run-console">
                {validation.objective.checks.map((check, index) => (
                  <div
                    className={`run-console-line ${check.passed ? "info" : "error"}`}
                    key={index}
                  >
                    <span>{check.passed ? "✓" : "✗"}</span>
                    <span>
                      {check.name} (exit {check.exit_code ?? "?"}) {check.detail}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        )}
        {isSwebench ? (
          <p className="muted">
            SWE-bench scoring is objective and binary: resolved iff every FAIL_TO_PASS
            test passes and no PASS_TO_PASS test regresses, evaluated by reinstalling
            the archived workspace and running the subset (swebench_validate.py). The
            run is auto-scored from this verdict — no manual score needed.
          </p>
        ) : (
          <p className="muted">
            Objective validation runs a headless smoke of workspace/{run.run_id}/ in the
            runner container (SDL dummy driver). The functional level below is the
            subjective dimension; the composite gates it by the objective floor.
          </p>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Harness attribution</h3>
          <div className="run-actions">
            {attribution && !summaryOnlyHarness ? (
              <span className="badge">
                governance {attribution.harness_usage?.governance_coverage ?? "—"}
              </span>
            ) : null}
            {!summaryOnlyHarness ? (
              <button disabled={actionPending} onClick={handleIndexAttribution} type="button">
                Re-index
              </button>
            ) : null}
          </div>
        </div>
        {summaryOnlyHarness ? (
          <>
            <p className="muted">
              Tool-level attribution isn't available for the <strong>{harness}</strong>{" "}
              harness: its CLI reports a final summary per step, not the per-tool logs
              (<code>permission=edit</code>, <code>read</code>, <code>bash</code>…) that
              opencode emits. Counts would all read 0 — not because the agent did
              nothing, but because the harness doesn't expose them. Only the step count
              is observable; judge behavior from the LLM audit below.
            </p>
            <div className="detail-grid">
              <div>
                <span>Steps parsed</span>
                <strong>{attribution?.steps_parsed ?? "—"}</strong>
              </div>
            </div>
          </>
        ) : attribution === null ? (
          <p className="muted">
            No attribution yet. It is computed automatically after a run from the
            opencode step logs; use Re-index to compute it now.
          </p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>Tools</span>
                <strong>{countEntries(attribution.tools) || "—"}</strong>
              </div>
              <div>
                <span>Agent modes</span>
                <strong>{countEntries(attribution.agent_modes) || "—"}</strong>
              </div>
              <div>
                <span>Governance read</span>
                <strong>{attribution.harness_usage?.governance_coverage ?? "—"}</strong>
              </div>
              <div>
                <span>Skills consulted</span>
                <strong>
                  {(attribution.harness_usage?.skills_consulted?.length ?? 0)}/
                  {attribution.harness_usage?.skills_available?.length ?? 0}
                </strong>
              </div>
              <div>
                <span>Bash</span>
                <strong>{countEntries(attribution.bash_commands) || "—"}</strong>
              </div>
              <div>
                <span>Steps parsed</span>
                <strong>{attribution.steps_parsed}</strong>
              </div>
            </div>
            {attribution.harness_usage?.governance_docs_read &&
            attribution.harness_usage.governance_docs_read.length > 0 ? (
              <p className="muted">
                Governance read: {attribution.harness_usage.governance_docs_read.join(", ")}
              </p>
            ) : (
              <p className="muted">No governance docs were read in this run.</p>
            )}
            <p className="muted">{attribution.harness_usage?.skills_note}</p>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>LLM audit</h3>
          <div className="run-actions">
            {audit ? <span className="badge">{audit.status}</span> : null}
            <input
              className="audit-model-input"
              onChange={(event) => setAuditModel(event.target.value)}
              placeholder="auditor model (optional)"
              type="text"
              value={auditModel}
            />
            <button
              disabled={actionPending || audit?.status === "running"}
              onClick={handleRunAudit}
              type="button"
            >
              {audit?.status === "running" ? "Auditing..." : "Run audit"}
            </button>
          </div>
        </div>
        {audit === null ? (
          <p className="muted">
            No audit yet. A strong LLM reads this run's logs, diff, usage and score
            and writes a qualitative diagnostic (skill/rule usage, efficiency, SDLC
            adherence, failure points). Runs in the worker; this may take a minute.
          </p>
        ) : audit.status === "running" ? (
          <p className="muted">Auditing with {audit.auditor_model || "default model"}…</p>
        ) : audit.status === "failed" ? (
          <ErrorBanner message={audit.error ?? "Audit failed"} />
        ) : (
          <div className="audit-body">
            <Markdown>{audit.markdown}</Markdown>
          </div>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Improvement proposals</h3>
          <div className="run-actions">
            {improvements ? (
              <span className="badge ok">{improvements.proposals.length} proposal(s)</span>
            ) : null}
            <select
              aria-label="Estrictez de la síntesis"
              onChange={(e) => setImproveStrictness(e.target.value)}
              title="Estrictez del filtro: strict = pocas proposals con evidencia fuerte; lenient = más permisivo."
              value={improveStrictness}
            >
              <option value="strict">strict</option>
              <option value="balanced">balanced</option>
              <option value="lenient">lenient</option>
            </select>
            <button
              disabled={improving || audit?.status !== "done"}
              onClick={handleImprove}
              title={
                audit?.status !== "done"
                  ? "Run the LLM audit first — proposals are synthesized from it."
                  : undefined
              }
              type="button"
            >
              {improving
                ? "Synthesizing..."
                : improvements
                  ? "Re-synthesize"
                  : "Propose improvements"}
            </button>
            {improvements !== null && improvements.proposals.length > 0 ? (
              <button
                disabled={savingProposals}
                onClick={handleSaveProposals}
                title="Guarda estas proposals en la tabla cross-run (menú Improvements) para agruparlas en un issue combinado."
                type="button"
              >
                {savingProposals ? "Guardando…" : "Guardar"}
              </button>
            ) : null}
            {saveResult !== null ? (
              <Link
                className="badge ok save-feedback"
                title="Abrir el menú Improvements con las proposals guardadas"
                to="/improvements"
              >
                Guardadas {saveResult.saved + saveResult.updated} · ver tabla ↗
              </Link>
            ) : null}
          </div>
        </div>
        {improveError ? <ErrorBanner message={improveError} /> : null}
        {improvements === null ? (
          <p className="muted">
            {audit?.status === "done"
              ? "Turn this audit's findings into concrete GitHub issue proposals for the template/harness repos (LLM synthesis in the worker)."
              : "Run the LLM audit first; proposals are synthesized from its findings."}
          </p>
        ) : (
          <>
            {improvements.proposals.length === 0 ? (
              <>
                {improvements.gate?.skipped ? (
                  <p className="muted">
                    ✅ La corrida estuvo dentro de lo esperado para este caso+modelo —{" "}
                    {improvements.gate.reason} Podés forzar la síntesis con{" "}
                    <code>make improve RUN={run.run_id} IMPROVE_FORCE=1</code>.
                  </p>
                ) : (
                  <p className="muted">
                    The synthesis found nothing actionable to improve from this audit.
                  </p>
                )}
                {/* Close the loop: 0 proposals still persists improvements.json,
                    so the Runs list already reflects this run as "no" — say so,
                    since there is nothing to "Guardar" into the cross-run worklist. */}
                <p className="muted">
                  Sin proposals para guardar. Este run ya quedó registrado como{" "}
                  <span className="badge neutral">no</span> en la lista de{" "}
                  <Link to="/runs">Runs</Link> (columna Improvements).
                </p>
              </>
            ) : (
              <div className="table">
                {improvements.proposals.map((proposal, index) => (
                  <div className="table-row" key={`${proposal.title}-${index}`}>
                    <span>
                      <strong>{proposal.title}</strong>
                      <small>{proposal.body.slice(0, 220)}</small>
                    </span>
                    <span>
                      <span className="badge neutral">{proposal.repo.split("/").pop()}</span>
                      {proposal.kind ? <small> {proposal.kind}</small> : null}
                    </span>
                    <span>
                      {proposal.created_issue_url ? (
                        <a href={proposal.created_issue_url} rel="noreferrer" target="_blank">
                          issue ↗
                        </a>
                      ) : (
                        <span className="muted">not filed</span>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p className="muted">
              Review/edit <code>data/runs/{run.run_id}/improvements.json</code> (repo
              routing, wording), then file the issues from the host (needs{" "}
              <code>gh</code> auth):{" "}
              <code>make improve RUN={run.run_id} CREATE=1</code>
            </p>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Issue sanitizado (ml-python-base)</h3>
          <div className="run-actions">
            <button disabled={proposalLoading} onClick={handleGenerateIssue} type="button">
              {proposalLoading ? "Generando…" : "Generar issue sanitizado (ml-python-base)"}
            </button>
          </div>
        </div>
        {proposalError ? <ErrorBanner message={proposalError} onDismiss={() => setProposalError(null)} /> : null}
        {proposalNotice ? (
          <p className="muted" style={{ display: "flex", gap: "0.5rem", alignItems: "flex-start" }}>
            <span aria-hidden="true">ℹ️</span>
            <span>{proposalNotice}</span>
          </p>
        ) : null}
        {proposalIssue === null ? (
          <p className="muted">
            {proposalLoading
              ? "Sintetizando un issue sanitizado a partir de la auditoría (corre un LLM en el servidor; puede tardar entre 15 y 40 segundos)."
              : "Genera un issue de GitHub sanitizado para ml-python-base a partir de la auditoría de este run. Corré la auditoría primero si todavía no está lista."}
          </p>
        ) : (
          <>
            <div className="run-actions" style={{ marginBottom: "0.5rem" }}>
              <a
                className="primary"
                href={proposalIssue.form.url}
                target="_blank"
                rel="noreferrer"
                style={{
                  display: "inline-block",
                  padding: "8px 14px",
                  borderRadius: "6px",
                  textDecoration: "none"
                }}
              >
                Abrir issue pre-cargado en GitHub ↗
              </a>
            </div>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              El link abre el form <code>{proposalIssue.form.template}</code> de{" "}
              {proposalIssue.repository} con todos los campos ya cargados. Confirmá los dos
              campos marcados <strong>recomendado</strong> (canal y SemVer) y tildá las
              casillas de sanitización antes de crear. Cada campo también se puede copiar
              suelto por si preferís pegar a mano.
            </p>
            <div className="proposal-fields" style={{ display: "grid", gap: "0.75rem" }}>
              {proposalIssue.form.fields.map((field) => (
                <div key={field.id}>
                  <div className="panel-head">
                    <h4 style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                      {field.label}
                      {field.recommended ? (
                        <span className="badge ok">recomendado</span>
                      ) : null}
                    </h4>
                    <button
                      onClick={() => handleCopyField(field.id, field.value)}
                      type="button"
                    >
                      {copiedField === field.id ? "Copiado ✓" : "Copiar"}
                    </button>
                  </div>
                  {field.reason ? (
                    <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
                      {field.reason}
                    </p>
                  ) : null}
                  <pre
                    style={{
                      fontFamily: "monospace",
                      whiteSpace: "pre-wrap",
                      margin: 0
                    }}
                  >
                    {field.value}
                  </pre>
                </div>
              ))}
            </div>
            <details style={{ marginTop: "1rem" }}>
              <summary>Ver como markdown único (copia y pega manual)</summary>
              <div className="panel-head" style={{ marginTop: "0.5rem" }}>
                <span className="muted">Issue para {proposalIssue.repository}</span>
                <button onClick={handleCopyIssue} type="button">
                  {proposalCopied ? "Copiado ✓" : "Copiar todo"}
                </button>
              </div>
              <pre
                style={{
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  maxHeight: "24rem",
                  overflow: "auto"
                }}
              >
                {proposalIssue.markdown}
              </pre>
            </details>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Harness catalog</h3>
          <span className="muted">available · observed · LLM verdict</span>
        </div>
        <HarnessCatalog attribution={attribution} audit={audit} metadata={metadata} />
      </div>
      <div>
        <p className="sidebar-label">Command preview</p>
        <code className="command-preview">{run.command.join(" ")}</code>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Logs</h3>
          <span className="muted">{run.state === "running" ? "live" : "history"}</span>
        </div>
        <div ref={consoleRef}>
          <LogViewer events={events} />
        </div>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Artifacts</h3>
          <span className="muted">data/runs/{run.run_id}</span>
        </div>
        <ArtifactsPanel runId={run.run_id} />
      </div>
      </div>
        </>
      ) : null}
    </section>
  );
}
