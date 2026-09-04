import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { formatRunDate } from "../dates";
import type {
  CaseRecord,
  CheckoutState,
  ConditionRecord,
  ExperimentRecord,
  GateCheckRecord,
  HarnessSkill,
  ReadinessReport,
  StagedSkillRecord,
  TargetRecord
} from "../types";

type TabKey = "checkout" | "readiness" | "cases" | "experiments" | "report";

const TABS: { key: TabKey; label: string }[] = [
  { key: "checkout", label: "Checkout" },
  { key: "readiness", label: "Readiness" },
  { key: "cases", label: "Cases" },
  { key: "experiments", label: "Experiments" },
  { key: "report", label: "Report" }
];

/** An experiment read whole, because only the detail endpoint carries arms. */
type ExperimentDetail = ExperimentRecord & { conditions: ConditionRecord[] };

type PresetKey = "" | "skills" | "harness";

/**
 * The harness both arms run under.
 *
 * Neither preset varies it — the harness is what is held fixed while the skill
 * set or the governance source moves — so it is a constant here rather than a
 * fourth field in a form that already asks enough.
 */
const LAUNCH_HARNESS = "opencode";

/** Poll interval for a gate check, in milliseconds. */
const GATE_POLL_MS = 4000;

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 52) || "repository"
  );
}

function isDuplicateId(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.status === 409) ||
    (error instanceof Error && error.message.includes("already exists"))
  );
}

function readTargetRepoId(overrides: Record<string, unknown>): string {
  const value = overrides.target_repo_id;
  return typeof value === "string" ? value : "";
}

export function RepositoryDetail() {
  const { targetId = "" } = useParams();

  const [tab, setTab] = useState<TabKey>("checkout");
  const [target, setTarget] = useState<TargetRecord | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  const [checkout, setCheckout] = useState<CheckoutState | null>(null);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [gate, setGate] = useState<GateCheckRecord | null>(null);
  const [cases, setCases] = useState<CaseRecord[] | null>(null);
  const [expandedCase, setExpandedCase] = useState("");
  const [experiments, setExperiments] = useState<ExperimentDetail[] | null>(null);
  const [report, setReport] = useState("");

  // Preset form. One at a time: two open launchers on one screen is two ways
  // to answer a question the page asked once.
  const [preset, setPreset] = useState<PresetKey>("");
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [repetitions, setRepetitions] = useState(3);
  const [executorModel, setExecutorModel] = useState("");
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [enforceInvoke, setEnforceInvoke] = useState(true);
  const [stagedSkills, setStagedSkills] = useState<StagedSkillRecord[]>([]);
  const [governedSkills, setGovernedSkills] = useState<HarnessSkill[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (actionError) {
      setError(
        actionError instanceof Error ? actionError.message : "Something went wrong"
      );
    } finally {
      setBusy("");
    }
  };

  // Archived repositories are included: a workbench opened from a bookmark
  // should show the repository, not an empty page, whatever its status.
  useEffect(() => {
    let active = true;
    setIsLoading(true);
    Promise.all([api.listTargets(true), api.getCheckout(targetId)])
      .then(([targets, state]) => {
        if (!active) {
          return;
        }
        setTarget(targets.find((item) => item.target_id === targetId) ?? null);
        setCheckout(state);
      })
      .catch((loadError: unknown) => {
        if (active) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Unable to load this repository"
          );
        }
      })
      .finally(() => {
        if (active) {
          setIsLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [targetId]);

  // Each tab reads on first sight rather than on load: readiness clones and
  // reads the repository, and the experiment list costs one request per
  // experiment. Neither is worth paying for a tab nobody opened.
  useEffect(() => {
    if (tab === "readiness" && readiness === null) {
      void run("readiness", async () => {
        setReadiness(await api.getTargetReadiness(targetId));
        setGate(await api.getGateCheck(targetId));
      });
    }
    if (tab === "cases" && cases === null) {
      void run("cases", async () => {
        setCases(await api.listCases(targetId));
      });
    }
    if (tab === "experiments" && experiments === null) {
      void run("experiments", async () => {
        setExperiments(await loadExperiments());
        if (cases === null) {
          setCases(await api.listCases(targetId));
        }
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // A gate check runs in the background, so the page asks again until it ends.
  useEffect(() => {
    if (gate === null || gate.status !== "running") {
      return;
    }
    const timer = window.setInterval(() => {
      void api
        .getGateCheck(targetId)
        .then((next) => setGate(next))
        .catch(() => {
          // A single failed poll is not a failed check — try again next tick.
        });
    }, GATE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [gate, targetId]);

  /**
   * This repository's experiments.
   *
   * `GET /api/experiments` returns no conditions, and the repository lives in a
   * condition's overrides, so each experiment is read once to find out whether
   * it belongs here.
   */
  const loadExperiments = async (): Promise<ExperimentDetail[]> => {
    const listed = await api.listExperiments();
    const details = await Promise.allSettled(
      listed.map((item) => api.getExperiment(item.experiment_id))
    );
    return details
      .flatMap((result) => (result.status === "fulfilled" ? [result.value] : []))
      .filter((detail) =>
        detail.conditions.some(
          (condition) => readTargetRepoId(condition.overrides) === targetId
        )
      );
  };

  const loadSkills = () =>
    run("skills", async () => {
      const [staged, governed] = await Promise.all([
        api.listStagedSkills(),
        api.listHarnessSkills()
      ]);
      setStagedSkills(staged);
      setGovernedSkills(governed);
    });

  const rePin = () =>
    run("pin", async () => {
      const state = await api.pinCheckout(targetId);
      setCheckout(state);
      setTarget(
        (current) =>
          current && {
            ...current,
            pinned_commit: state.pinned_commit,
            pinned_at: state.pinned_at
          }
      );
      setNotice(
        `Pinned to ${state.pinned_commit.slice(0, 10)}. Everything measured from ` +
          `now on reads that commit.`
      );
    });

  const runGateCheck = () =>
    run("gate", async () => {
      setGate(await api.startGateCheck(targetId));
    });

  const generateReport = () =>
    run("report", async () => {
      const payload = await api.getAuditReport(targetId);
      setReport(payload.markdown);
    });

  const downloadReport = () => {
    const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${targetId}-report.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const pinnedCommit = target?.pinned_commit ?? checkout?.pinned_commit ?? "";

  const selectedCases = useMemo(
    () => (cases ?? []).filter((item) => selectedCaseIds.includes(item.case_id)),
    [cases, selectedCaseIds]
  );

  // Fund the largest budget any selected case declares: an arm that runs out of
  // steps failed for a reason that is not the treatment.
  const maxSteps = useMemo(
    () => Math.max(10, ...selectedCases.map((item) => item.max_steps ?? 0)),
    [selectedCases]
  );

  // The union across the selected cases, exactly as the run form takes it — a
  // case without the chosen variant simply contributes no runs for it.
  const promptVariant = useMemo(() => {
    const names = new Set<string>();
    for (const item of selectedCases) {
      for (const variant of Object.keys(item.prompt_variants ?? {})) {
        names.add(variant);
      }
    }
    if (names.has("default")) {
      return "default";
    }
    return [...names].sort()[0] ?? "baseline";
  }, [selectedCases]);

  const stagedSelected = selectedSkills.filter((name) =>
    stagedSkills.some((skill) => skill.name === name)
  );

  const toggle = (list: string[], value: string): string[] =>
    list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

  const openPreset = (next: PresetKey) => {
    setPreset(next);
    setWarnings([]);
    setNotice("");
    setError(null);
    if (next === "skills" && stagedSkills.length === 0 && governedSkills.length === 0) {
      void loadSkills();
    }
  };

  /** Why a preset cannot be launched yet, or an empty string when it can. */
  const blocking = useMemo(() => {
    if (pinnedCommit === "") {
      return (
        "This repository has no pinned commit. Pin it under Checkout first — two " +
        "arms run against a moving ref are not the same experiment twice."
      );
    }
    if (selectedCaseIds.length === 0) {
      return "Select at least one case. An experiment with no cases measures nothing.";
    }
    if (preset === "skills") {
      if (selectedSkills.length === 0) {
        return "Select at least one skill — that is the only thing the two arms differ on.";
      }
      if (stagedSelected.length === 0 && !enforceInvoke) {
        return (
          "Every selected skill is already governed and the arm is not asked to use " +
          "them, so both arms would be identical. Tick 'Enforce skill use', or add a " +
          "staged skill."
        );
      }
    }
    return "";
  }, [
    enforceInvoke,
    pinnedCommit,
    preset,
    selectedCaseIds,
    selectedSkills,
    stagedSelected
  ]);

  /** Create under the first free id: a name collision is not a failure. */
  const createUnderFreeId = async (
    base: string,
    build: (experimentId: string) => Record<string, unknown>
  ) => {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const candidate = attempt === 0 ? base : `${base}-${attempt + 1}`;
      try {
        return await api.createExperiment(build(candidate));
      } catch (createError) {
        if (!isDuplicateId(createError)) {
          throw createError;
        }
        lastError = createError;
      }
    }
    throw lastError instanceof Error
      ? lastError
      : new Error("Unable to find a free experiment id.");
  };

  const launchPreset = () =>
    run("launch", async () => {
      const name = target?.name ?? targetId;
      const shared = {
        target_repo_id: targetId,
        target_ref: pinnedCommit,
        harness_source: "native" as const
      };
      const isSkills = preset === "skills";
      const skillList = selectedSkills.join(", ");
      const conditions = isSkills
        ? [
            {
              label: "native",
              role: "control" as const,
              description: "The repository's own setup, with no extra skills.",
              overrides: { ...shared }
            },
            {
              label: "native + skills",
              role: "treatment" as const,
              description: enforceInvoke
                ? `The same setup with ${skillList} available and asked for.`
                : `The same setup with ${skillList} available.`,
              overrides: {
                ...shared,
                // Governed skills are already present; only staged ones have to
                // be added, so listing a governed name here would be a request
                // for a staged skill that does not exist.
                ...(stagedSelected.length > 0 ? { skills_extra: stagedSelected } : {}),
                ...(enforceInvoke ? { skills_invoke: [...selectedSkills] } : {})
              }
            }
          ]
        : [
            {
              label: "native",
              role: "control" as const,
              description: "The repository's own governance, with nothing injected.",
              overrides: { ...shared }
            },
            {
              label: "injected",
              role: "treatment" as const,
              description: "The lab harness injected into the same workspace.",
              overrides: { ...shared, harness_source: "injected" as const }
            }
          ];

      const question = isSkills
        ? `Does adding ${skillList} change outcomes on ${name}?`
        : `Does the lab harness improve on ${name}'s own setup?`;
      const base = slugify(
        isSkills ? `skills-${targetId}` : `harness-source-${targetId}`
      );

      const created = await createUnderFreeId(base, (experimentId) => ({
        experiment_id: experimentId,
        name: isSkills ? `Skills on ${name}` : `Harness source on ${name}`,
        question,
        mode: "harness_evaluation",
        case_ids: [...selectedCaseIds],
        repetitions,
        conditions,
        notes: `Both arms run against ${targetId} at ${pinnedCommit.slice(0, 10)}.`
      }));
      setWarnings(created.warnings ?? []);

      const result = await api.launchExperiment(created.experiment_id, {
        case_id: selectedCaseIds[0] ?? "",
        prompt_variant: promptVariant,
        harness: LAUNCH_HARNESS,
        planner_model: null,
        executor_model: executorModel.trim() || null,
        score_mode: "manual",
        max_steps: maxSteps,
        resume: false,
        dry_run: false
      });
      setNotice(
        `Queued ${result.count} run(s) under '${created.experiment_id}'.`
      );
      setPreset("");
      setExperiments(await loadExperiments());
    });

  if (isLoading) {
    return (
      <section className="page">
        <Spinner label="Reading this repository" />
      </section>
    );
  }

  if (target === null) {
    return (
      <section className="page">
        <EmptyState
          action={
            <Link className="button-link" to="/evidence/repositories">
              Back to repositories
            </Link>
          }
          description={`No repository is registered under '${targetId}'.`}
          title="Repository not found"
        />
      </section>
    );
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>{target.name || target.target_id}</h1>
          <p className="lede">
            <code className="mono">{target.repository_url}</code>
          </p>
          <p className="muted small">
            {pinnedCommit ? (
              <>
                Pinned at{" "}
                <span className="badge neutral" title={pinnedCommit}>
                  {pinnedCommit.slice(0, 10)}
                </span>{" "}
                — every case, run and report below reads that commit.
              </>
            ) : (
              <>
                <span className="badge warn">unpinned</span> Nothing here is
                comparable until this repository is pinned to a commit.
              </>
            )}
          </p>
        </div>
        <div className="page-header-action">
          <Link className="button-link" to="/evidence/repositories">
            All repositories
          </Link>
        </div>
      </header>

      <div className="tab-bar tab-bar--inline">
        {TABS.map((item) => (
          <button
            className={`tab ${tab === item.key ? "tab--active" : ""}`}
            key={item.key}
            onClick={() => {
              setTab(item.key);
              setNotice("");
            }}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>

      {error !== null ? (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      ) : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}

      {tab === "checkout" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Local checkout</h2>
            <span className={`badge ${checkout?.exists ? "ok" : "warn"}`}>
              {checkout?.exists ? "Cloned" : "Not cloned"}
            </span>
          </div>
          <p className="muted">
            You clone this repository in your own terminal so your credentials
            apply; the lab only reads the clone and never makes one itself.
          </p>

          {checkout === null ? (
            <Spinner label="Reading the checkout" />
          ) : (
            <>
              <div className="detail-grid">
                <div>
                  <span>Path</span>
                  <code className="mono small">{checkout.host_path || "—"}</code>
                </div>
                <div>
                  <span>HEAD</span>
                  <strong className="mono">
                    {checkout.head ? checkout.head.slice(0, 10) : "—"}
                  </strong>
                </div>
                <div>
                  <span>Branch</span>
                  <strong>{checkout.branch || "—"}</strong>
                </div>
                <div>
                  <span>Pinned commit</span>
                  <strong className="mono">
                    {checkout.pinned_commit
                      ? checkout.pinned_commit.slice(0, 10)
                      : "not pinned"}
                  </strong>
                </div>
                <div>
                  <span>Pinned at</span>
                  <strong>
                    {checkout.pinned_at ? formatRunDate(checkout.pinned_at) : "—"}
                  </strong>
                </div>
              </div>

              {checkout.error ? (
                <p className="callout callout--warning">{checkout.error}</p>
              ) : null}

              {checkout.dirty ? (
                <div className="callout callout--warning">
                  <div>
                    <strong>The working tree has uncommitted changes</strong>
                    <p>
                      {checkout.dirty_files} file(s) differ from{" "}
                      {checkout.head ? checkout.head.slice(0, 10) : "HEAD"}. A run
                      reads the pinned commit, not your edits, so what you see
                      locally is not what was measured.
                    </p>
                  </div>
                </div>
              ) : null}

              {checkout.pin_is_stale ? (
                <div className="callout">
                  <div>
                    <strong>HEAD has moved since the pin</strong>
                    <p>
                      Re-pinning changes what every future case, run and report
                      reads. Results already recorded keep the commit they used.
                    </p>
                    <div className="button-row">
                      <button
                        className="primary"
                        disabled={busy !== ""}
                        onClick={() => void rePin()}
                        type="button"
                      >
                        {busy === "pin" ? "Pinning…" : "Re-pin to HEAD"}
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}

              {!checkout.exists || !checkout.is_repo ? (
                <div className="card">
                  <div className="panel-head">
                    <h3>Clone it yourself</h3>
                    <button
                      className="secondary"
                      onClick={() => {
                        void navigator.clipboard.writeText(checkout.command);
                        setNotice("Clone command copied to the clipboard.");
                      }}
                      type="button"
                    >
                      Copy
                    </button>
                  </div>
                  <pre className="command-preview">{checkout.command}</pre>
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      {tab === "readiness" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>What this repository already has</h2>
              {readiness !== null ? (
                <span className={`badge ${readiness.ready ? "ok" : "warn"}`}>
                  {readiness.ready ? "Ready to run" : "Not runnable yet"}
                </span>
              ) : null}
            </div>
            {busy === "readiness" ? <Spinner label="Reading the repository" /> : null}
            {readiness !== null ? (
              <ReadinessReportView
                adopting={busy === "adopt"}
                onAdopt={(payload) => {
                  void run("adopt", async () => {
                    await api.updateTarget(targetId, payload);
                    setReadiness(await api.getTargetReadiness(targetId));
                    setNotice(
                      "Adopted. Runs against this repository now validate with its " +
                        "own commands."
                    );
                  });
                }}
                report={readiness}
              />
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Gate check</h2>
              <button
                className="primary"
                disabled={busy !== "" || gate?.status === "running"}
                onClick={() => void runGateCheck()}
                type="button"
              >
                {gate?.status === "running" ? "Running…" : "Run gate check"}
              </button>
            </div>
            <p className="muted">
              It runs the repository's own setup and validation commands at the
              pinned commit with no agent, so a red run afterwards is a fact about
              the agent rather than about the runner image.
            </p>

            {gate === null ? (
              <EmptyState
                description="Run one to find out whether this repository builds and passes on its own."
                title="No gate check yet"
              />
            ) : (
              <>
                <div className="detail-grid">
                  <div>
                    <span>Verdict</span>
                    <strong>
                      {gate.status === "running"
                        ? "Running"
                        : gate.passed === true
                          ? "Passed"
                          : gate.passed === false
                            ? `Failed at ${gate.failed_phase || "an unnamed phase"}`
                            : "No verdict"}
                    </strong>
                  </div>
                  <div>
                    <span>Commit</span>
                    <strong className="mono">
                      {gate.commit ? gate.commit.slice(0, 10) : "—"}
                    </strong>
                  </div>
                  <div>
                    <span>Last updated</span>
                    <strong>{formatRunDate(gate.updated_at)}</strong>
                  </div>
                </div>
                {gate.error ? (
                  <p className="callout callout--danger">{gate.error}</p>
                ) : null}
                {gate.status === "running" ? (
                  <Spinner label="Waiting for the gate to finish" />
                ) : null}
                <ul className="check-list">
                  {gate.checks_json.map((step) => (
                    <li
                      className={`check check--${step.passed ? "ok" : "missing"}`}
                      key={`${step.phase}-${step.command}`}
                    >
                      <div>
                        <strong>{step.phase}</strong>{" "}
                        <code className="mono small">{step.command}</code>
                        <p className="muted">
                          exit {step.exit_code ?? "—"} ·{" "}
                          {step.passed ? "passed" : "failed"}
                        </p>
                        {!step.passed && step.detail ? (
                          <pre className="command-preview">{step.detail}</pre>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </>
      ) : null}

      {tab === "cases" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Case library</h2>
            {cases !== null ? (
              <span className="badge neutral">{cases.length} case(s)</span>
            ) : null}
          </div>
          <p className="muted">
            Cases derived from this repository's own history. They are what every
            experiment below runs.
          </p>
          {busy === "cases" ? <Spinner label="Loading cases" /> : null}
          {cases !== null && cases.length === 0 ? (
            <EmptyState
              action={
                <Link className="button-link" to="/evaluate/audit">
                  Derive cases from history
                </Link>
              }
              description="Derive the first ones from the repository's commits in the audit."
              title="No cases from this repository yet"
            />
          ) : null}
          {cases !== null && cases.length > 0 ? (
            <ul className="candidate-list">
              {cases.map((item) => (
                <li key={item.case_id}>
                  <button
                    className="skills-toggle"
                    onClick={() =>
                      setExpandedCase(expandedCase === item.case_id ? "" : item.case_id)
                    }
                    type="button"
                  >
                    {expandedCase === item.case_id ? "▾" : "▸"}{" "}
                    {item.name || item.case_id}
                    <span className="muted small">
                      {" "}
                      <code>{item.case_id}</code> · {item.type} ·{" "}
                      {item.max_steps ?? "—"} step(s)
                    </span>
                  </button>
                  {expandedCase === item.case_id ? (
                    <div className="stack">
                      {(item.validation_commands ?? []).length === 0 ? (
                        <p className="muted small">
                          No validation command, so a run of this case has no
                          objective verdict — it can only be scored by hand.
                        </p>
                      ) : (
                        <ul className="check-items">
                          {(item.validation_commands ?? []).map((command) => (
                            <li key={command}>
                              <code>{command}</code>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {tab === "experiments" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>Launch an experiment</h2>
            </div>
            <p className="muted">
              Both presets hold the repository, the cases and the model fixed and
              vary one thing about the harness. Everything runs at the pinned
              commit.
            </p>
            <div className="toolbar">
              <button
                className={preset === "skills" ? "primary" : "secondary"}
                onClick={() => openPreset(preset === "skills" ? "" : "skills")}
                type="button"
              >
                Native vs native + skills
              </button>
              <button
                className={preset === "harness" ? "primary" : "secondary"}
                onClick={() => openPreset(preset === "harness" ? "" : "harness")}
                type="button"
              >
                Native vs the lab harness
              </button>
            </div>

            {preset !== "" ? (
              <div className="card">
                <h3>
                  {preset === "skills"
                    ? "Native vs native + skills"
                    : "Native vs the lab harness"}
                </h3>
                <p className="muted small">
                  {preset === "skills"
                    ? "Both arms use the repository's own governance. Only the treatment arm gets the selected skills."
                    : "Both arms use the same cases and model. The control keeps the repository's own governance; the treatment gets the lab harness injected into the workspace."}
                </p>

                <label className="field">
                  <span>Cases — {selectedCaseIds.length} selected</span>
                </label>
                {cases === null || cases.length === 0 ? (
                  <EmptyState
                    action={
                      <Link className="button-link" to="/evaluate/audit">
                        Add the first case
                      </Link>
                    }
                    description="An experiment needs at least one case from this repository."
                    title="No cases to run"
                  />
                ) : (
                  <ul className="pick-list">
                    {cases.map((item) => {
                      const isSelected = selectedCaseIds.includes(item.case_id);
                      return (
                        <li key={item.case_id}>
                          <button
                            className={isSelected ? "primary" : "secondary"}
                            onClick={() =>
                              setSelectedCaseIds((current) =>
                                toggle(current, item.case_id)
                              )
                            }
                            type="button"
                          >
                            <strong>
                              {item.name || item.case_id}
                              <span className="badge pick-tag">{item.type}</span>
                            </strong>
                            <span className="muted">
                              <code>{item.case_id}</code> —{" "}
                              {(item.validation_commands ?? []).length} validation
                              command(s)
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}

                {preset === "skills" ? (
                  <>
                    <label className="field">
                      <span>Skills — {selectedSkills.length} selected</span>
                    </label>
                    {busy === "skills" ? <Spinner label="Loading skills" /> : null}
                    <div className="checkbox-grid">
                      {stagedSkills.map((skill) => (
                        <label className="checkbox" key={`staged-${skill.name}`}>
                          <input
                            checked={selectedSkills.includes(skill.name)}
                            onChange={() =>
                              setSelectedSkills((current) => toggle(current, skill.name))
                            }
                            type="checkbox"
                          />
                          <span>
                            {skill.name}
                            <span className="badge pick-tag pick-tag--soft">staged</span>
                          </span>
                        </label>
                      ))}
                      {governedSkills.map((skill) => (
                        <label className="checkbox" key={`governed-${skill.name}`}>
                          <input
                            checked={selectedSkills.includes(skill.name)}
                            onChange={() =>
                              setSelectedSkills((current) => toggle(current, skill.name))
                            }
                            type="checkbox"
                          />
                          <span>
                            {skill.name}
                            <span className="badge pick-tag pick-tag--soft">
                              governed
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                    <label className="inline-check">
                      <input
                        checked={enforceInvoke}
                        onChange={(event) => setEnforceInvoke(event.target.checked)}
                        type="checkbox"
                      />
                      <span>Enforce skill use</span>
                    </label>
                    <p className="muted small">
                      Without it the treatment arm measures whether the skill was
                      available, not whether it was used.
                    </p>
                  </>
                ) : null}

                <div className="field-row">
                  <label className="field">
                    <span>Repetitions per arm</span>
                    <input
                      min={1}
                      onChange={(event) =>
                        setRepetitions(Math.max(1, Number(event.target.value)))
                      }
                      type="number"
                      value={repetitions}
                    />
                  </label>
                  <label className="field">
                    <span>Executor model — optional, harness default when empty</span>
                    <input
                      onChange={(event) => setExecutorModel(event.target.value)}
                      placeholder="anthropic/claude-sonnet-4-5"
                      value={executorModel}
                    />
                  </label>
                </div>

                {blocking ? <p className="callout callout--warning">{blocking}</p> : null}

                <div className="toolbar">
                  <button onClick={() => setPreset("")} type="button">
                    Cancel
                  </button>
                  <button
                    className="primary"
                    disabled={busy !== "" || blocking !== ""}
                    onClick={() => void launchPreset()}
                    type="button"
                  >
                    {busy === "launch" ? "Launching…" : "Create and launch"}
                  </button>
                </div>
              </div>
            ) : null}

            {warnings.length > 0 ? (
              <ul className="warning-list">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Experiments on this repository</h2>
              {experiments !== null ? (
                <span className="badge neutral">{experiments.length}</span>
              ) : null}
            </div>
            {busy === "experiments" ? <Spinner label="Reading experiments" /> : null}
            {experiments !== null && experiments.length === 0 ? (
              <EmptyState
                description="Launch one with a preset above."
                title="Nothing has been run against this repository yet"
              />
            ) : null}
            {experiments !== null && experiments.length > 0 ? (
              <ul className="pick-list">
                {experiments.map((item) => (
                  <li key={item.experiment_id}>
                    <Link
                      className="button-link"
                      to={`/evaluate/experiments?experiment=${item.experiment_id}`}
                    >
                      <strong>{item.name || item.experiment_id}</strong>
                      <span className="muted small">
                        {" "}
                        {item.status} · {item.conditions.length} arm(s) ·{" "}
                        {item.case_ids.length} case(s) · {item.repetitions} rep(s)
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </>
      ) : null}

      {tab === "report" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Client report</h2>
            <div className="toolbar">
              <button
                className="primary"
                disabled={busy !== ""}
                onClick={() => void generateReport()}
                type="button"
              >
                {busy === "report"
                  ? "Assembling…"
                  : report
                    ? "Regenerate"
                    : "Generate report"}
              </button>
              {report ? (
                <button onClick={downloadReport} type="button">
                  Download .md
                </button>
              ) : null}
            </div>
          </div>
          <p className="muted">
            Assembled live from the evidence this repository has produced —
            readiness, cases, experiments and signed findings. It never claims what
            was not measured.
          </p>
          {report ? (
            <pre className="candidate-prompt pre--scroll">{report}</pre>
          ) : (
            <EmptyState
              description="Generate one to read what the lab can currently say about this repository."
              title="No report yet"
            />
          )}
          <p className="muted small">
            Findings are recorded separately — see{" "}
            <Link to="/evidence/findings">Findings</Link>.
          </p>
        </div>
      ) : null}
    </section>
  );
}
