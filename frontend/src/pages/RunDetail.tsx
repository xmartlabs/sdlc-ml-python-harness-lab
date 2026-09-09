import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { ArtifactsPanel } from "../components/ArtifactsPanel";
import { ErrorBanner } from "../components/ErrorBanner";
import { HarnessCatalog } from "../components/HarnessCatalog";
import { ScoresPanel } from "../components/ScoresPanel";
import { LogViewer } from "../components/LogViewer";
import { Markdown } from "../components/Markdown";
import { SandboxPanel } from "../components/SandboxPanel";
import { StateBadge } from "../components/StateBadge";
import { formatDuration, formatRunDate } from "../dates";
import type {
  AttributionRecord,
  AuditRecord,
  ImprovementsDoc,
  LangfuseStatus,
  ProposalIssue,
  RunContext,
  RunEventRecord,
  RunMetadata,
  RunRecord,
  SaveImprovementsResult,
  ValidationRecord
} from "../types";

type RunDetailProps = {
  run: RunRecord | null;
  // Every loaded run, used only to list this one's continuations.
  runs?: RunRecord[];
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
  onContinueRun?: (runId: string, prompt: string, maxSteps?: number) => Promise<RunRecord>;
};

const POLL_INTERVAL_MS = 1500;
export const CANCELLABLE_STATES: RunRecord["state"][] = ["pending", "running"];
export const RETRYABLE_STATES: RunRecord["state"][] = ["failed", "cancelled"];
// A run can be continued once it has stopped and its workspace is final
// (ADR-0026). Mirrors CONTINUABLE_STATES in run_service.py.
export const CONTINUABLE_STATES: RunRecord["state"][] = [
  "completed",
  "awaiting_manual_score",
  "failed",
  "cancelled"
];
// Offered alongside whatever budget the parent used, which is already in the list.
const CONTINUE_STEP_OPTIONS = [1, 3, 6, 10];

// Harnesses whose CLI emits final summaries, not per-tool logs — so file/tool/
// governance attribution can't be parsed from them. claude runs now capture
// stream-json events (first-class tool calls), so a claude run only falls back
// to the summary-only note when its attribution carries no signals (old runs).
const SUMMARY_ONLY_HARNESSES = new Set(["claude", "codex"]);

const HARNESS_SOURCE_BLURB: Record<string, string> = {
  native:
    "The agent saw the target repository's own setup. The lab injected nothing, " +
    "so this is the control for any governance comparison.",
  injected:
    "The lab's harness was copied into the target workspace. Whatever this run " +
    "did with governance, it did with the lab's, not the repository's.",
  lab_root:
    "The agent ran inside the lab repository itself — no external target " +
    "workspace was materialised."
};

/**
 * What condition this run was, above the outcome and far above the logs (§16).
 *
 * A run is only interpretable as a member of a design. Reading stdout without
 * knowing which arm produced it, against which commit, with whose governance,
 * is how a pile of runs gets mistaken for a measurement.
 */
function RunConditionStrip({ context }: { context: RunContext }) {
  return (
    <div className="panel run-condition">
      <div className="panel-head">
        <h3>Condition</h3>
        {context.experiment_id ? (
          <Link to={`/evaluate/experiments?experiment=${context.experiment_id}`}>
            {context.experiment_id} ↗
          </Link>
        ) : (
          <span className="badge neutral">Ad-hoc run — not part of an experiment</span>
        )}
      </div>
      {context.experiment_question ? (
        <p className="muted">{context.experiment_question}</p>
      ) : null}
      <div className="detail-grid">
        <div>
          <span>Arm</span>
          <strong>{context.condition_label || "—"}</strong>
        </div>
        <div>
          <span>Case</span>
          <strong>{context.case_name || context.case_id || "—"}</strong>
        </div>
        <div>
          <span>Repository</span>
          <strong>{context.target_repo_id || "the lab itself"}</strong>
        </div>
        <div>
          <span>Commit</span>
          <strong className="mono">
            {context.target_commit ? context.target_commit.slice(0, 10) : "—"}
          </strong>
        </div>
        <div>
          <span>Harness</span>
          <strong>{context.harness}</strong>
        </div>
        <div>
          <span>Governance</span>
          <strong>{context.harness_source}</strong>
        </div>
        <div>
          <span>Executor model</span>
          <strong>{context.executor_model || "—"}</strong>
        </div>
        <div>
          <span>Step budget</span>
          <strong>{context.max_steps}</strong>
        </div>
      </div>
      <p className="muted small">
        {HARNESS_SOURCE_BLURB[context.harness_source] ?? ""}
      </p>
      {context.validation_commands.length > 0 ? (
        <p className="muted small">
          Validated by: <code>{context.validation_commands.join(" && ")}</code>
        </p>
      ) : (
        <p className="muted small">
          No validation gate — this run can only ever be scored by hand.
        </p>
      )}
    </div>
  );
}

function harnessFromCommand(command: string[] | undefined): string {
  if (!command) return "opencode";
  const i = command.indexOf("--harness");
  return i >= 0 && i + 1 < command.length ? command[i + 1] : "opencode";
}

export function RunDetail({
  run,
  runs = [],
  onCancelRun,
  onRetryRun,
  onScoreRun,
  onContinueRun
}: RunDetailProps) {
  const navigate = useNavigate();
  const harness = harnessFromCommand(run?.command);
  const [events, setEvents] = useState<RunEventRecord[]>([]);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [continuePrompt, setContinuePrompt] = useState("");
  const [continueSteps, setContinueSteps] = useState(3);
  const [scoreValue, setScoreValue] = useState(0);
  const [interventionsValue, setInterventionsValue] = useState(0);
  const [notesValue, setNotesValue] = useState("");
  const [runContext, setRunContext] = useState<RunContext | null>(null);
  const [validation, setValidation] = useState<ValidationRecord | null>(null);
  const [attribution, setAttribution] = useState<AttributionRecord | null>(null);
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [langfuse, setLangfuse] = useState<LangfuseStatus | null>(null);
  const [langfuseNotice, setLangfuseNotice] = useState<string | null>(null);
  const [langfuseSending, setLangfuseSending] = useState(false);
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
  // The sandbox is reachable by link, not only by clicking through: a wizard
  // that tells you to go and look inside the workspace has to be able to point
  // at it. Read once, at mount — after that the tab is yours.
  const [searchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const [tab, setTab] = useState<"detail" | "terminal" | "desktop">(
    requestedTab === "terminal" || requestedTab === "desktop" ? requestedTab : "detail"
  );
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
    setLangfuse(null);
    setLangfuseNotice(null);
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
    // Available the moment the run exists — it describes the design, not the
    // outcome — so it is fetched once and never retried.
    api
      .getRunContext(run.run_id)
      .then((result) => {
        if (active) {
          setRunContext(result);
        }
      })
      .catch(() => {
        // Pre-context runs and deleted configs: the strip stays hidden rather
        // than guessing at a condition nobody recorded.
      });
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
      .getLangfuse(run.run_id)
      .then((result) => {
        if (active) {
          setLangfuse(result);
        }
      })
      .catch(() => {
        // Tracing status is decoration; a failure just hides the link.
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
        <Link className="run-detail-back" to="/advanced/runs">
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

  const canContinue = onContinueRun !== undefined && CONTINUABLE_STATES.includes(run.state);
  const children = runs.filter((item) => item.parent_run_id === run.run_id);

  const handleContinue = () => {
    const prompt = continuePrompt.trim();
    if (onContinueRun === undefined || prompt === "") {
      return;
    }
    setActionPending(true);
    setActionError(null);
    onContinueRun(run.run_id, prompt, continueSteps)
      .then((created) => {
        setContinuePrompt("");
        navigate(`/advanced/runs/${created.run_id}`);
      })
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

  // null means "nothing was measured". That is two different situations: a case
  // with no objective criterion at all (manual scoring), or checks that ran and
  // could not conclude — a timeout is a fact about the lab, not about the code.
  const hasChecks = (validation?.objective?.checks?.length ?? 0) > 0;

  const objectiveBadge = (passed: boolean | null | undefined) => {
    if (isSwebench) {
      if (passed === true) return <span className="badge ok">SWE-bench: RESOLVED</span>;
      if (passed === false) return <span className="badge error">SWE-bench: NOT RESOLVED</span>;
    }
    if (passed === true) return <span className="badge ok">objective: pass</span>;
    if (passed === false) return <span className="badge error">objective: fail</span>;
    if (hasChecks) return <span className="badge warn">objective: inconclusive</span>;
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

  const handleLangfuseExport = () => {
    if (run === null) return;
    setLangfuseSending(true);
    setLangfuseNotice(null);
    api
      .exportLangfuse(run.run_id)
      .then((result) => {
        setLangfuseNotice(
          result.ok
            ? `Sent ${result.spans} spans to session ${result.session_id}.`
            : result.reason
        );
        if (result.session_url) {
          setLangfuse((current) =>
            current === null ? current : { ...current, session_url: result.session_url }
          );
        }
      })
      .catch((error: unknown) => {
        setLangfuseNotice(error instanceof ApiError ? error.message : "Export failed.");
      })
      .finally(() => setLangfuseSending(false));
  };

  const countEntries = (counts: Record<string, number>) =>
    Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${name}×${count}`)
      .join("  ");

  return (
    <section className="run-detail-page">
      <Link className="run-detail-back" to="/advanced/runs">
        ← Runs
      </Link>
      <div className="run-detail-header">
        <p className="eyebrow">Run detail</p>
        <h2>{run.run_id}</h2>
        <StateBadge state={run.state} />
        {run.parent_run_id || children.length > 0 ? (
          <p className="muted run-lineage">
            {run.parent_run_id ? (
              <>
                Continues <Link to={`/advanced/runs/${run.parent_run_id}`}>{run.parent_run_id}</Link>
              </>
            ) : null}
            {run.parent_run_id && children.length > 0 ? " · " : null}
            {children.length > 0 ? (
              <>
                Continued by{" "}
                {children.map((child, index) => (
                  <span key={child.run_id}>
                    {index > 0 ? ", " : ""}
                    <Link to={`/advanced/runs/${child.run_id}`}>{child.run_id}</Link>
                  </span>
                ))}
              </>
            ) : null}
          </p>
        ) : null}
        <div className="run-actions">
          <button disabled={!canCancel || actionPending} onClick={handleCancel} type="button">
            Cancel
          </button>
          <button disabled={!canRetry || actionPending} onClick={handleRetry} type="button">
            Retry
          </button>
          {/* Only shown when tracing is on: a lab without Langfuse gets no dead buttons. */}
          {langfuse?.enabled ? (
            <>
              <button disabled={langfuseSending} onClick={handleLangfuseExport} type="button">
                {langfuseSending ? "Sending…" : "Send traces"}
              </button>
              {langfuse.session_url ? (
                <a
                  className="run-langfuse-link"
                  href={langfuse.session_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  View in Langfuse ↗
                </a>
              ) : null}
            </>
          ) : null}
        </div>
        {langfuse?.enabled ? (
          <p className="muted run-langfuse-note">
            Session <code>{langfuse.session_id}</code>
            {langfuse.session_url ? null : " — set LANGFUSE_PROJECT_URL in Settings for a direct link."}
            {langfuseNotice ? ` · ${langfuseNotice}` : null}
          </p>
        ) : null}
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
      {runContext ? <RunConditionStrip context={runContext} /> : null}
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
          <strong>{formatRunDate(run.started_at)}</strong>
        </div>
        <div>
          <span>Ended</span>
          <strong>{formatRunDate(run.ended_at)}</strong>
        </div>
        {/*
          Wall clock between created and ended answers "how long did I wait",
          never "how slow was the model". The worker runs at concurrency 1, so
          a batch queues behind itself and a run can sit for hours before its
          container starts. Keeping the two apart is what makes a slow harness
          distinguishable from a busy one.
        */}
        <div>
          <span>Queued for</span>
          <strong>{formatDuration(run.created_at, run.started_at) || "—"}</strong>
        </div>
        <div>
          <span>Execution</span>
          <strong>
            {formatDuration(run.started_at, run.ended_at) ||
              (run.started_at ? "running" : "—")}
          </strong>
        </div>
        <div>
          <span>Failure</span>
          <strong>{run.failure_reason ?? "None"}</strong>
        </div>
      </div>
      {run.state === "awaiting_manual_score" || run.state === "completed" ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>Manual score</h3>
            <span className="muted">
              {isSwebench
                ? "SWE-bench runs auto-score from validation — use Re-validate below instead"
                : run.state === "completed"
                  ? // A run with objective validation completes on its own, so this
                    // form would never appear for it — and the human judgement of
                    // *how* it did is exactly what the objective verdict cannot say.
                    "Already scored objectively — add your judgement of the work"
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
      {canContinue ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>Continue</h3>
            <span className="muted">
              Starts a child run from this workspace — this run is not modified
            </span>
          </div>
          <div className="form-grid score-form-grid">
            <label className="full">
              <span>Follow-up prompt</span>
              <textarea
                onChange={(event) => setContinuePrompt(event.target.value)}
                placeholder="What should the agent do next, starting from what is already there?"
                rows={4}
                value={continuePrompt}
              />
            </label>
            <label>
              <span>Max steps</span>
              <select
                onChange={(event) => setContinueSteps(Number(event.target.value))}
                value={continueSteps}
              >
                {CONTINUE_STEP_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="muted">
            A continuation counts as a human intervention: the child is hidden from the
            dashboard by default so it is not compared against one-shot runs.
          </p>
          <div className="panel-actions score-panel-actions">
            <button
              className="primary"
              disabled={actionPending || continuePrompt.trim() === ""}
              onClick={handleContinue}
              type="button"
            >
              {actionPending ? "Queueing..." : "Continue run"}
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
                {validation.objective.checks.map((check, index) => {
                  const inconclusive = check.conclusive === false;
                  const tone = inconclusive ? "warn" : check.passed ? "info" : "error";
                  const mark = inconclusive ? "⊘" : check.passed ? "✓" : "✗";
                  return (
                    <div className={`run-console-line ${tone}`} key={index}>
                      <span title={inconclusive ? "did not measure" : undefined}>{mark}</span>
                      <span>
                        {check.name} (exit {check.exit_code ?? "?"}) {check.detail}
                      </span>
                    </div>
                  );
                })}
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
            Objective validation runs the case&apos;s — or the target repository&apos;s —
            validation commands against the archived workspace in a runner container.
            The functional level is the subjective dimension; the composite is gated by
            the objective floor.
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
              is observable; judge behaviour from the LLM audit below.
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
            {/*
              The denominator's provenance. "2/9" against a client's own
              documents and "2/9" against the lab's are different measurements
              that would otherwise render identically — and a native run scored
              against the lab's rules would be scored against rules it never
              received (ADR-0036).
            */}
            {attribution.harness_usage?.governance_source ? (
              <p className="muted small">
                Denominator: the{" "}
                <strong>
                  {attribution.harness_usage.governance_source === "target_workspace"
                    ? "target repository's own"
                    : attribution.harness_usage.governance_source === "bare"
                      ? "empty"
                      : "lab repository's"}
                </strong>{" "}
                governance surface, under{" "}
                <code>harness_source={attribution.harness_usage.harness_source}</code>
                {attribution.harness_usage.governance_fingerprint
                  ? ` · fingerprint ${attribution.harness_usage.governance_fingerprint.slice(0, 12)}`
                  : ""}
                .
              </p>
            ) : null}
            {attribution.harness_usage?.governance_source === "bare" ? (
              <p className="muted small">
                A bare run had no governance to read: the runner masked the lab's
                CLAUDE.md, hooks, subagents and <code>.github/</code>, so read
                coverage is not a measure of anything here (ADR-0049).
              </p>
            ) : null}
            {attribution.harness_usage?.governance_warning ? (
              <p className="callout callout--warning small">
                {attribution.harness_usage.governance_warning}
              </p>
            ) : null}
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
              placeholder="auditor model — empty uses the transport default"
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
            {/*
              What the auditor was allowed to judge against, stated before its
              judgement. ADR-0036: a `native` run is evaluated against the
              target repository's own governance, an `injected` one against the
              harness the lab actually put there. Never against rules the run
              did not receive.
            */}
            {audit.governance_json && "fingerprint" in audit.governance_json ? (
              <details className="governance-surface">
                <summary>
                  Judged against{" "}
                  {audit.governance_json.source === "target_workspace"
                    ? "the target repository's own governance"
                    : "the lab repository's governance"}{" "}
                  · {audit.governance_json.file_count} file(s) ·{" "}
                  <code>{audit.governance_json.fingerprint.slice(0, 12)}</code>
                </summary>
                <p className="muted small">
                  Rulebook: <code>{audit.governance_json.rules_file}</code> ·{" "}
                  <code>harness_source={audit.governance_json.harness_source}</code>
                  {audit.governance_json.truncated
                    ? " · file list truncated for the fingerprint"
                    : ""}
                </p>
                {audit.governance_json.warning ? (
                  <p className="callout callout--warning small">
                    {audit.governance_json.warning}
                  </p>
                ) : null}
                <ul className="check-items">
                  {audit.governance_json.files.map((file) => (
                    <li key={file}>
                      <code>{file}</code>
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="muted small">
                This audit predates per-run governance surfaces, so what it was
                judged against was not recorded. Re-run the audit to pin it.
              </p>
            )}
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
              aria-label="Synthesis strictness"
              onChange={(e) => setImproveStrictness(e.target.value)}
              title="Filter strictness: strict keeps only proposals with strong evidence; lenient admits more."
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
                ? "Synthesising..."
                : improvements
                  ? "Re-synthesize"
                  : "Propose improvements"}
            </button>
            {improvements !== null && improvements.proposals.length > 0 ? (
              <button
                disabled={savingProposals}
                onClick={handleSaveProposals}
                title="Save these proposals to the cross-run table (Improvements) so they can be grouped into one combined issue."
                type="button"
              >
                {savingProposals ? "Saving…" : "Save"}
              </button>
            ) : null}
            {saveResult !== null ? (
              <Link
                className="badge ok save-feedback"
                title="Open Improvements with the saved proposals"
                to="/advanced/improvements"
              >
                Saved {saveResult.saved + saveResult.updated} · view table ↗
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
                    This run was within the expected range for this case and model —{" "}
                    {improvements.gate.reason} You can force the synthesis with{" "}
                    <code>make improve RUN={run.run_id} IMPROVE_FORCE=1</code>.
                  </p>
                ) : (
                  <p className="muted">
                    The synthesis found nothing actionable to improve from this audit.
                  </p>
                )}
                {/* Close the loop: 0 proposals still persists improvements.json,
                    so the Runs list already reflects this run as "no" — say so,
                    since there is nothing to save into the cross-run worklist. */}
                <p className="muted">
                  No proposals to save. This run is already recorded as{" "}
                  <span className="badge neutral">no</span> in the{" "}
                  <Link to="/advanced/runs">Runs</Link> list (Improvements column).
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
          <h3>Sanitised issue (ml-python-base)</h3>
          <div className="run-actions">
            <button disabled={proposalLoading} onClick={handleGenerateIssue} type="button">
              {proposalLoading ? "Generating…" : "Generate sanitised issue (ml-python-base)"}
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
              ? "Synthesising a sanitised issue from the audit (this runs an LLM on the server and can take 15 to 40 seconds)."
              : "Generate a sanitised GitHub issue for ml-python-base from this run's audit. Run the audit first if it is not ready yet."}
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
                Open prefilled issue on GitHub ↗
              </a>
            </div>
            <p className="muted" style={{ fontSize: "0.85rem" }}>
              The link opens the <code>{proposalIssue.form.template}</code> form on{" "}
              {proposalIssue.repository} with every field already filled in. Confirm the two
              fields marked <strong>recommended</strong> (channel and SemVer) and tick the
              sanitisation boxes before creating it. Each field can also be copied on its
              own if you would rather paste by hand.
            </p>
            <div className="proposal-fields" style={{ display: "grid", gap: "0.75rem" }}>
              {proposalIssue.form.fields.map((field) => (
                <div key={field.id}>
                  <div className="panel-head">
                    <h4 style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                      {field.label}
                      {field.recommended ? (
                        <span className="badge ok">recommended</span>
                      ) : null}
                    </h4>
                    <button
                      onClick={() => handleCopyField(field.id, field.value)}
                      type="button"
                    >
                      {copiedField === field.id ? "Copied" : "Copy"}
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
              <summary>View as a single markdown block (manual copy and paste)</summary>
              <div className="panel-head" style={{ marginTop: "0.5rem" }}>
                <span className="muted">Issue for {proposalIssue.repository}</span>
                <button onClick={handleCopyIssue} type="button">
                  {proposalCopied ? "Copied" : "Copy all"}
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
        <ScoresPanel runId={run.run_id} />
        <ArtifactsPanel runId={run.run_id} />
      </div>
      </div>
        </>
      ) : null}
    </section>
  );
}
