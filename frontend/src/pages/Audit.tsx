import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconArrowRight, IconCheck, IconSparkles } from "../components/icons";
import { HARNESS_OPTIONS } from "../harnesses";
import type {
  CaseCandidate,
  CaseCandidates,
  CaseRecord,
  CheckoutState,
  DroppedCommand,
  GateCheckRecord,
  ReadinessReport,
  TargetRecord
} from "../types";

const STEPS = [
  { key: "clone", label: "Clone it yourself" },
  { key: "readiness", label: "What this repository already has" },
  { key: "case", label: "One case that passes" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

type CaseTab = "ai" | "history" | "manual" | "existing";

const MAX_STEP_OPTIONS = [5, 10, 20, 40];

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "case"
  );
}

function isDuplicateId(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.status === 409) ||
    (error instanceof Error && error.message.includes("already exists"))
  );
}

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
    max_steps: 40,
    origin_target_id: target.target_id
  };
}

/**
 * Onboarding a third-party repository, in the order the work actually happens.
 *
 * The lab does not clone the repository: it has no credentials, and neither
 * does a run container. The engineer clones it in their own terminal, where
 * their ssh agent and tokens already work, and the lab reads that checkout
 * (ADR-0042). Everything downstream hangs off the commit they pin, which is
 * why steps 2 and 3 stay shut until the pin exists.
 *
 * It ends at the first case that passes against the repository — not at a
 * verdict. Findings and discovery live on their own pages.
 */
export function Audit() {
  const [step, setStep] = useState<StepKey>("clone");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  // Step 1 — the repository and the engineer's own clone of it.
  const [targets, setTargets] = useState<TargetRecord[]>([]);
  const [targetId, setTargetId] = useState("");
  const [newTargetId, setNewTargetId] = useState("");
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newRef, setNewRef] = useState("main");
  const [checkout, setCheckout] = useState<CheckoutState | null>(null);
  const [copied, setCopied] = useState(false);

  // Step 2 — what the repository brings on its own.
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [gate, setGate] = useState<GateCheckRecord | null>(null);
  const [gateError, setGateError] = useState("");

  // Step 3 — one case, from whichever of the four sources fits.
  const [caseTab, setCaseTab] = useState<CaseTab>("ai");
  const [caseId, setCaseId] = useState("");
  const [manualCaseId, setManualCaseId] = useState("");
  const [caseName, setCaseName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [commandText, setCommandText] = useState("");
  const [maxSteps, setMaxSteps] = useState(10);
  const [aiDescription, setAiDescription] = useState("");
  const [aiDropped, setAiDropped] = useState<DroppedCommand[]>([]);
  const [candidates, setCandidates] = useState<CaseCandidates | null>(null);
  const [existingCases, setExistingCases] = useState<CaseRecord[]>([]);
  // claude runs on its CLI default, so it is the harness that needs the least
  // configuration to answer "does this case pass here at all?".
  const [harness, setHarness] = useState<(typeof HARNESS_OPTIONS)[number]>("claude");
  const [executorModel, setExecutorModel] = useState("");
  const [experimentId, setExperimentId] = useState("");

  const target = useMemo(
    () => targets.find((item) => item.target_id === targetId) ?? null,
    [targetId, targets]
  );
  const pinnedCommit = checkout?.pinned_commit ?? "";
  const cloneFound = checkout?.is_repo ?? false;
  const gateRunning = gate?.status === "running";
  const commands = useMemo(
    () =>
      commandText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean),
    [commandText]
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

  useEffect(() => {
    api
      .listTargets()
      .then(setTargets)
      .catch(() => setError("Unable to load repositories."));
  }, []);

  /**
   * Watch for the clone while this step is open and the clone is not there yet.
   *
   * The interval is torn down on every dependency change, not only on unmount:
   * a leaked one keeps hitting the API from a step nobody is looking at.
   */
  useEffect(() => {
    if (step !== "clone" || !targetId || cloneFound) {
      return;
    }
    let cancelled = false;
    const load = () => {
      api
        .getCheckout(targetId)
        .then((state) => {
          if (!cancelled) {
            setCheckout(state);
          }
        })
        .catch(() => {
          // Transient: the next tick asks again.
        });
    };
    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [cloneFound, step, targetId]);

  // A gate check runs in a worker, so the record is polled until it settles.
  useEffect(() => {
    if (step !== "readiness" || !targetId || !gateRunning) {
      return;
    }
    let cancelled = false;
    const load = () => {
      api
        .getGateCheck(targetId)
        .then((record) => {
          if (!cancelled && record) {
            setGate(record);
          }
        })
        .catch(() => {
          // Transient: the next tick asks again.
        });
    };
    const timer = window.setInterval(load, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [gateRunning, step, targetId]);

  // The catalog is only needed by the last step's fourth tab, so it is fetched
  // when that step opens rather than on every visit to the page.
  useEffect(() => {
    if (step !== "case") {
      return;
    }
    api
      .listCases()
      .then(setExistingCases)
      .catch(() => {
        // The other three tabs do not need the catalog.
      });
  }, [step]);

  const selectTarget = (id: string) =>
    run("checkout", async () => {
      setTargetId(id);
      setCheckout(await api.getCheckout(id));
    });

  const createTarget = () =>
    run("target", async () => {
      const id = newTargetId.trim() || slugify(newName);
      const created = await api.createTarget({
        target_id: id,
        name: newName.trim() || id,
        repository_url: newUrl.trim(),
        default_ref: newRef.trim() || "main"
      });
      setTargets((current) => [created, ...current]);
      setTargetId(created.target_id);
      setCheckout(await api.getCheckout(created.target_id));
    });

  const copyCommand = () => {
    if (!checkout?.command) {
      return;
    }
    void navigator.clipboard.writeText(checkout.command);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const refreshCheckout = () =>
    run("checkout", async () => {
      setCheckout(await api.getCheckout(targetId));
    });

  const pin = () =>
    run("pin", async () => {
      const state = await api.pinCheckout(targetId);
      setCheckout(state);
      setNotice(
        `Pinned at ${state.pinned_commit.slice(0, 8)}. Everything downstream reads ` +
          "that commit until somebody re-pins."
      );
    });

  const loadReadiness = () =>
    run("readiness", async () => {
      setReadiness(await api.getTargetReadiness(targetId));
      setGate(await api.getGateCheck(targetId));
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
      setReadiness(await api.getTargetReadiness(targetId));
    });

  const startGate = () =>
    run("gate", async () => {
      setGateError("");
      try {
        setGate(await api.startGateCheck(targetId));
      } catch (startError) {
        if (startError instanceof ApiError && startError.status === 422) {
          setGateError(
            `${startError.message} Adopt the inferred gate above first — with no ` +
              "validation command there is nothing for the check to run."
          );
          return;
        }
        throw startError;
      }
    });

  const draftCase = () =>
    run("draft", async () => {
      setAiDropped([]);
      const draft = await api.generateCaseDraft({
        description: aiDescription,
        selected_skills: [],
        target_id: targetId
      });
      if (draft.subject) {
        setCaseName(draft.subject);
      }
      if (draft.instruction) {
        setInstruction(draft.instruction);
      }
      // The server already screened the commands: what is left is one shell
      // command per line, which is the only shape this textarea preserves.
      setCommandText((draft.validation_commands ?? []).join("\n"));
      if (draft.max_steps) {
        setMaxSteps(draft.max_steps);
      }
      setAiDropped(draft.dropped_commands ?? []);
      if ((draft.validation_commands ?? []).length === 0) {
        setError(
          "The draft has no runnable validation command. Write one below, or " +
            "describe the check you want and draft it again."
        );
      }
    });

  /**
   * Save the fields as a case.
   *
   * The AI tab derives the id from the subject, so a second draft would collide
   * with the first and the suffix keeps it moving. A hand-written id is the
   * user's own choice, so a collision there is reported instead of renamed.
   */
  const saveCase = (baseId: string, allowSuffix: boolean) =>
    run("case", async () => {
      const payload = (candidate: string) => ({
        case_id: candidate,
        name: caseName.slice(0, 100) || candidate,
        type: "single_prompt",
        description: `One case against ${target?.name ?? targetId}, at the pinned commit.`,
        tags: ["repository-onboarding"],
        prompt_variants: { default: instruction },
        step_prompts: [],
        success_criteria: {},
        scoring_rubric: "",
        constraints: [],
        validation_commands: commands,
        max_steps: maxSteps,
        origin_target_id: targetId
      });
      const attempts = allowSuffix ? 5 : 1;
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        const candidate = attempt === 0 ? baseId : `${baseId}-${attempt + 1}`;
        try {
          const created = await api.createCase(payload(candidate));
          setCaseId(created.case_id);
          setNotice(`Case '${created.case_id}' created. Run it against the repository.`);
          return;
        } catch (createError) {
          if (!isDuplicateId(createError)) {
            throw createError;
          }
        }
      }
      throw new Error(
        `A case called '${baseId}' already exists. Pick another id, or reuse it ` +
          "from the Existing case tab."
      );
    });

  const derive = () =>
    run("derive", async () => {
      setCandidates(await api.deriveCases(targetId, 15));
    });

  const acceptCandidate = (candidate: CaseCandidate) =>
    run(candidate.commit, async () => {
      if (!target) {
        return;
      }
      const payload = candidateToCase(candidate, target);
      try {
        const created = await api.createCase(payload);
        setCaseId(created.case_id);
      } catch (createError) {
        // An earlier session already accepted this candidate. That is the
        // outcome the button was after, so it reads as success.
        if (!isDuplicateId(createError)) {
          throw createError;
        }
        setCaseId(payload.case_id);
        setNotice(`'${payload.case_id}' already existed — reusing it.`);
      }
      setCaseName(candidate.subject.slice(0, 100));
      setInstruction(candidate.suggested_prompt);
      setCommandText((target.validation_commands ?? []).join("\n"));
    });

  const selectExistingCase = (record: CaseRecord) => {
    setCaseId(record.case_id);
    setCaseName(record.name || record.case_id);
    setInstruction(record.prompt_variants?.default ?? record.description ?? "");
    setCommandText((record.validation_commands ?? []).join("\n"));
    if (record.max_steps) {
      setMaxSteps(record.max_steps);
    }
    setNotice(`Case '${record.case_id}' selected.`);
  };

  /**
   * One run, one case, this repository at the pinned commit.
   *
   * It is a subject validation and not a comparison: there is no control arm
   * here, so the only thing it can say is whether the case passes at all.
   */
  const validate = () =>
    run("validate", async () => {
      const base = slugify(`validate-${targetId}-${caseId}`).slice(0, 56);
      const executor = executorModel.trim() === "" ? null : executorModel.trim();
      let lastError: unknown = null;
      for (let attempt = 0; attempt < 5; attempt += 1) {
        const candidate = attempt === 0 ? base : `${base}-${attempt + 1}`;
        try {
          const created = await api.createExperiment({
            experiment_id: candidate,
            name: `First pass: ${target?.name ?? targetId}`,
            question: `Does '${caseId}' pass against ${target?.name ?? targetId} at the pinned commit?`,
            mode: "subject_validation",
            subject: target?.name ?? targetId,
            case_ids: [caseId],
            repetitions: 1,
            conditions: [
              {
                label: "this repository",
                role: "treatment",
                description: "The repository as it is, at the pinned commit.",
                overrides: {
                  target_repo_id: targetId,
                  target_ref: pinnedCommit,
                  harness_source: "native",
                  harness,
                  executor_model: executor
                }
              }
            ]
          });
          await api.launchExperiment(created.experiment_id, {
            case_id: caseId,
            prompt_variant: "default",
            harness,
            planner_model: null,
            executor_model: executor,
            score_mode: "manual",
            max_steps: maxSteps,
            resume: false,
            dry_run: false,
            target_repo_id: targetId,
            target_ref: pinnedCommit,
            harness_source: "native"
          });
          setExperimentId(created.experiment_id);
          setNotice("One run queued. It reads the pinned commit, with no control arm.");
          return;
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
    });

  const stepIndex = STEPS.findIndex((item) => item.key === step);
  // Nothing after step 1 means anything without a pin: readiness reads the
  // pinned commit, and a case derived from a moving branch measures the branch.
  const reached = (key: StepKey) => key === "clone" || pinnedCommit !== "";

  const caseFields = (
    <div className="form-grid">
      <label className="full">
        <span>Subject</span>
        <input
          onChange={(event) => setCaseName(event.target.value)}
          placeholder="Fix the failing serializer test"
          value={caseName}
        />
      </label>
      <label className="full">
        <span>The instruction the agent gets</span>
        <textarea
          onChange={(event) => setInstruction(event.target.value)}
          placeholder="Make the failing test in tests/test_serializer.py pass without changing the test."
          rows={4}
          value={instruction}
        />
      </label>
      <label className="full">
        <span>Validation commands — one per line</span>
        <textarea
          onChange={(event) => setCommandText(event.target.value)}
          placeholder={"make test\npytest tests/test_serializer.py"}
          rows={4}
          value={commandText}
        />
      </label>
      <label>
        <span>Max steps</span>
        <select
          onChange={(event) => setMaxSteps(Number(event.target.value))}
          value={maxSteps}
        >
          {MAX_STEP_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    </div>
  );

  return (
    <section className="page audit">
      <header className="page-header">
        <div>
          <h1>Set up a repository for audit</h1>
          <p className="lede">
            Three steps: you clone the repository and pin a commit, the lab reads what
            the repository already brings, and then one case runs against it. It ends
            when that case passes — not with a verdict about the repository.
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
            <button
              disabled={!reached(item.key)}
              onClick={() => setStep(item.key)}
              type="button"
            >
              <span className="stepper-index">{index + 1}</span>
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ol>

      {error ? <ErrorBanner message={error} /> : null}
      {notice ? <p className="callout callout--ok">{notice}</p> : null}

      {step === "clone" && !targetId ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>Which repository?</h2>
            </div>
            <p className="muted">
              The lab records the repository so runs can name it. It never fetches the
              code from here — that is the next step, and it happens in your terminal.
            </p>
            <div className="form-grid">
              <label>
                <span>Repository id</span>
                <input
                  onChange={(event) => setNewTargetId(event.target.value)}
                  placeholder="acme-billing"
                  value={newTargetId}
                />
              </label>
              <label>
                <span>Name</span>
                <input
                  onChange={(event) => setNewName(event.target.value)}
                  placeholder="Acme billing service"
                  value={newName}
                />
              </label>
              <label className="full">
                <span>Repository URL</span>
                <input
                  onChange={(event) => setNewUrl(event.target.value)}
                  placeholder="git@github.com:acme/billing.git"
                  value={newUrl}
                />
              </label>
              <label>
                <span>Default ref</span>
                <input
                  onChange={(event) => setNewRef(event.target.value)}
                  placeholder="main"
                  value={newRef}
                />
              </label>
            </div>
            <div className="panel-actions">
              <button
                className="primary"
                disabled={busy !== "" || !newUrl.trim() || !(newTargetId.trim() || newName.trim())}
                onClick={() => void createTarget()}
                type="button"
              >
                {busy === "target" ? "Registering…" : "Register this repository"}
                <IconArrowRight size={15} />
              </button>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Or pick one already registered</h2>
              <Link className="button-link" to="/evidence/repositories">
                Repositories
              </Link>
            </div>
            {targets.length === 0 ? (
              <EmptyState
                description="Nothing registered yet. Fill in the form above — it takes the URL and a name."
                title="No repositories yet."
              />
            ) : (
              <ul className="pick-list">
                {targets.map((item) => (
                  <li key={item.target_id}>
                    <button
                      className="secondary"
                      onClick={() => void selectTarget(item.target_id)}
                      type="button"
                    >
                      <strong>
                        {item.name}
                        {item.pinned_commit ? (
                          <span className="badge pick-tag">
                            pinned {item.pinned_commit.slice(0, 8)}
                          </span>
                        ) : null}
                      </strong>
                      <span className="muted">{item.repository_url}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      ) : null}

      {step === "clone" && targetId ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Clone it yourself</h2>
            <span className={`badge ${pinnedCommit ? "ok" : cloneFound ? "warn" : "neutral"}`}>
              {pinnedCommit ? "Pinned" : cloneFound ? "Clone found" : "Waiting for the clone"}
            </span>
          </div>
          <p className="muted">
            The lab has no credentials for <strong>{target?.name ?? targetId}</strong>, and
            a run container has none either. You clone it, on this machine, where your ssh
            agent and tokens already work — then the lab reads your clone and never
            contacts the remote again.
          </p>
          <ol className="numbered-steps">
            <li>Open a terminal on this machine (the one running the lab).</li>
            <li>Paste the command below and run it. It clones into the lab&apos;s own
              folder, which is where the lab looks.</li>
            <li>Leave this page open — it notices the clone within a few seconds.</li>
          </ol>

          {checkout ? (
            <>
              <div className="command-block">
                <pre>{checkout.command}</pre>
                <button className="secondary" onClick={copyCommand} type="button">
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="muted small">
                It lands in <code>{checkout.host_path}</code>. Nothing else in the
                command needs changing — the destination is already the path the lab
                watches.
              </p>
            </>
          ) : (
            <Spinner label="Reading the checkout" />
          )}

          {checkout?.error ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <span>{checkout.error}</span>
            </div>
          ) : null}

          {checkout && !cloneFound ? (
            <div className="panel-actions">
              <Spinner label="Watching for the clone" />
              <button
                className="ghost"
                disabled={busy !== ""}
                onClick={() => void refreshCheckout()}
                type="button"
              >
                Check now
              </button>
            </div>
          ) : null}

          {checkout && cloneFound ? (
            <>
              <div className="fact-row">
                <div>
                  <span className="muted small">HEAD</span>
                  <code>{checkout.head.slice(0, 8) || "—"}</code>
                </div>
                <div>
                  <span className="muted small">Branch</span>
                  <code>{checkout.branch || "—"}</code>
                </div>
                {pinnedCommit ? (
                  <div>
                    <span className="muted small">Pinned</span>
                    <code>{pinnedCommit.slice(0, 8)}</code>
                  </div>
                ) : null}
              </div>

              {checkout.dirty ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <span>
                    {checkout.dirty_files} uncommitted file(s) in your clone. They never
                    enter a run: the runner clones git objects, so it sees the committed
                    tree and nothing else. Commit them if the run is meant to see them.
                  </span>
                </div>
              ) : null}

              {checkout.pin_is_stale ? (
                <div className="callout callout--warning">
                  <IconAlert size={16} />
                  <span>
                    Your clone has moved since the pin. Re-pin to measure the commit you
                    are looking at, or leave it to keep comparing against the old one.
                  </span>
                </div>
              ) : null}

              <p className="muted">
                Pinning freezes the repository at the commit your clone is on. Every run,
                report and derived case reads that commit until somebody re-pins, so two
                experiments a week apart compare agents rather than a moving branch.
              </p>

              <div className="panel-actions">
                <button
                  className="primary"
                  disabled={busy !== ""}
                  onClick={() => void pin()}
                  type="button"
                >
                  {busy === "pin"
                    ? "Pinning…"
                    : pinnedCommit
                      ? "Pin this commit again"
                      : "Pin this commit"}
                </button>
                {pinnedCommit ? (
                  <button
                    className="secondary"
                    onClick={() => {
                      setStep("readiness");
                      void loadReadiness();
                    }}
                    type="button"
                  >
                    What this repository already has
                    <IconArrowRight size={15} />
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {step === "readiness" ? (
        <>
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
              <ReadinessReportView
                adopting={busy === "adopt"}
                onAdopt={adoptInferred}
                report={readiness}
              />
            ) : (
              <div className="panel-actions">
                <button
                  className="secondary"
                  disabled={busy !== ""}
                  onClick={() => void loadReadiness()}
                  type="button"
                >
                  Read the repository
                </button>
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Gate check</h2>
              {gate ? (
                <span
                  className={`badge ${
                    gate.status === "running" ? "neutral" : gate.passed ? "ok" : "error"
                  }`}
                >
                  {gate.status === "running" ? "Running" : gate.passed ? "ok" : "error"}
                </span>
              ) : null}
            </div>
            <p className="muted">
              This runs the repository's own gate at the pinned commit with no agent in
              the picture. It is what separates "the agent failed" from "this repository
              does not build here" — after a green gate check, a red run is a fact about
              the agent.
            </p>

            {gateError ? (
              <div className="callout callout--warning">
                <IconAlert size={16} />
                <span>{gateError}</span>
              </div>
            ) : null}

            {gate ? (
              <>
                <div className="fact-row">
                  <div>
                    <span className="muted small">Commit measured</span>
                    <code>{gate.commit.slice(0, 8)}</code>
                  </div>
                  {gate.failed_phase ? (
                    <div>
                      <span className="muted small">Failed at</span>
                      <code>{gate.failed_phase}</code>
                    </div>
                  ) : null}
                </div>
                {gate.status === "running" ? (
                  <Spinner label="Running the repository's own gate" />
                ) : null}
                {gate.error ? (
                  <div className="callout callout--warning">
                    <IconAlert size={16} />
                    <span>{gate.error}</span>
                  </div>
                ) : null}
                <ul className="gate-step-list">
                  {gate.checks_json.map((check) => (
                    <li
                      className={`gate-step ${check.passed ? "" : "gate-step--failed"}`}
                      key={`${check.phase}-${check.command}`}
                    >
                      <div className="gate-step-head">
                        <span className={`badge ${check.passed ? "ok" : "error"}`}>
                          {check.phase}
                        </span>
                        <code>{check.command}</code>
                        <span className="muted small">
                          exit {check.exit_code === null ? "—" : check.exit_code}
                        </span>
                      </div>
                      {!check.passed && check.detail ? (
                        <pre className="candidate-prompt">{check.detail}</pre>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            <div className="panel-actions">
              <button
                className="secondary"
                disabled={busy !== "" || gateRunning}
                onClick={() => void startGate()}
                type="button"
              >
                {busy === "gate" || gateRunning ? "Running…" : "Run the gate check"}
              </button>
              <button className="primary" onClick={() => setStep("case")} type="button">
                One case that passes
                <IconArrowRight size={15} />
              </button>
            </div>
            <p className="muted small">
              Recommended, not required. You can go on with it red or unrun — it only
              changes how you read the first failure.
            </p>
          </div>
        </>
      ) : null}

      {step === "case" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>One case that passes</h2>
              <div className="tab-bar tab-bar--inline">
                <button
                  className={`tab ${caseTab === "ai" ? "tab--active" : ""}`}
                  onClick={() => setCaseTab("ai")}
                  type="button"
                >
                  AI draft
                </button>
                <button
                  className={`tab ${caseTab === "history" ? "tab--active" : ""}`}
                  onClick={() => setCaseTab("history")}
                  type="button"
                >
                  From history
                </button>
                <button
                  className={`tab ${caseTab === "manual" ? "tab--active" : ""}`}
                  onClick={() => setCaseTab("manual")}
                  type="button"
                >
                  By hand
                </button>
                <button
                  className={`tab ${caseTab === "existing" ? "tab--active" : ""}`}
                  onClick={() => setCaseTab("existing")}
                  type="button"
                >
                  Existing case
                </button>
              </div>
            </div>

            {caseTab === "ai" ? (
              <>
                <div className="assist-card">
                  <div className="assist-card-head">
                    <strong className="assist-card-title">
                      <IconSparkles size={16} /> Draft it from a description
                    </strong>
                    <span className="muted small">
                      Read against <code>{targetId}</code> at the pin
                    </span>
                  </div>
                  <label>
                    <span>Describe the scenario, in plain language</span>
                    <textarea
                      onChange={(event) => setAiDescription(event.target.value)}
                      placeholder="e.g. Add a retry to the billing webhook client and keep the existing tests green."
                      rows={3}
                      value={aiDescription}
                    />
                  </label>
                  <div className="assist-card-row">
                    <button
                      className="primary"
                      disabled={busy !== "" || !aiDescription.trim()}
                      onClick={() => void draftCase()}
                      type="button"
                    >
                      {busy === "draft" ? (
                        <Spinner label="Drafting…" />
                      ) : (
                        <>
                          <IconSparkles size={14} /> Draft it
                        </>
                      )}
                    </button>
                  </div>
                  {aiDropped.length > 0 ? (
                    <div className="callout callout--warning">
                      <IconAlert size={16} />
                      <div>
                        <strong>Left out of the draft — the lab would not run these:</strong>
                        <ul className="blocking-list dropped-list">
                          {aiDropped.map((item) => (
                            <li key={item.command}>
                              <code>{item.command.split("\n")[0]}</code>
                              {item.command.includes("\n") ? " …" : ""}
                              <span className="muted small"> — {item.reason}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  ) : null}
                </div>
                {caseFields}
                <p className="muted">
                  The command is the whole measurement: it runs after the agent stops, and
                  its exit code is the pass. Read it before saving — a check that cannot
                  fail reports a pass that means nothing.
                </p>
                <div className="panel-actions">
                  <button
                    className="primary"
                    disabled={
                      busy !== "" ||
                      !caseName.trim() ||
                      !instruction.trim() ||
                      commands.length === 0
                    }
                    onClick={() => void saveCase(slugify(caseName), true)}
                    type="button"
                  >
                    {busy === "case" ? "Saving…" : "Create the case"}
                  </button>
                </div>
              </>
            ) : null}

            {caseTab === "history" ? (
              <>
                <p className="muted">
                  Proposed from the repository's own history — real changes this team
                  already made, each one a commit that paired a source change with a test
                  change. Read the prompt before creating the case.
                </p>
                <div className="panel-actions">
                  <button
                    className="secondary"
                    disabled={busy !== ""}
                    onClick={() => void derive()}
                    type="button"
                  >
                    {busy === "derive" ? "Reading history…" : "Read the history"}
                  </button>
                  {candidates ? (
                    <span className="badge neutral">
                      {candidates.candidates.length} of {candidates.scanned} commits
                      scanned
                    </span>
                  ) : null}
                </div>
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
                      {caseId === candidate.suggested_case_id ? (
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
              </>
            ) : null}

            {caseTab === "manual" ? (
              <>
                <div className="form-grid">
                  <label>
                    <span>Case id</span>
                    <input
                      onChange={(event) => setManualCaseId(event.target.value)}
                      placeholder="acme-billing-retry"
                      value={manualCaseId}
                    />
                  </label>
                </div>
                {caseFields}
                <div className="panel-actions">
                  <button
                    className="primary"
                    disabled={
                      busy !== "" ||
                      !manualCaseId.trim() ||
                      !instruction.trim() ||
                      commands.length === 0
                    }
                    onClick={() => void saveCase(slugify(manualCaseId), false)}
                    type="button"
                  >
                    {busy === "case" ? "Saving…" : "Create the case"}
                  </button>
                </div>
              </>
            ) : null}

            {caseTab === "existing" ? (
              <>
                <p className="muted">
                  A case already in the catalog, run against this repository. Nothing new
                  is created — the case keeps whichever repository it came from.
                </p>
                {existingCases.length === 0 ? (
                  <EmptyState
                    description="The catalog is empty, or it could not be read. Draft one on the first tab instead."
                    title="No cases to reuse."
                  />
                ) : (
                  <ul className="pick-list">
                    {existingCases.map((record) => (
                      <li key={record.case_id}>
                        <button
                          className={caseId === record.case_id ? "primary" : "secondary"}
                          onClick={() => selectExistingCase(record)}
                          type="button"
                        >
                          <strong>
                            {caseId === record.case_id ? <IconCheck size={14} /> : null}
                            {record.name || record.case_id}
                            <span className="badge pick-tag">{record.type}</span>
                          </strong>
                          <span className="muted">
                            <code>{record.case_id}</code> —{" "}
                            {record.description || "No description"}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Run it against the repository</h2>
              {caseId ? <span className="badge neutral">{caseId}</span> : null}
            </div>
            <p className="muted">
              One run of <code>{caseId || "the case"}</code> against{" "}
              <strong>{target?.name ?? targetId}</strong> at{" "}
              <code>{pinnedCommit.slice(0, 8) || "the pin"}</code>, under{" "}
              <code>
                {harness}/{executorModel.trim() || "CLI default"}
              </code>
              . No control arm — it answers one question: does the case pass here at
              all? <code>claude</code> needs a Claude credential in <code>.env</code>;{" "}
              <code>opencode</code> needs a gateway model.
            </p>
            <div className="form-grid">
              <label className="field">
                <span>Harness</span>
                <select
                  onChange={(event) =>
                    setHarness(event.target.value as (typeof HARNESS_OPTIONS)[number])
                  }
                  value={harness}
                >
                  {HARNESS_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>
                  Executor model — required for opencode; claude/codex fall back to
                  their CLI default
                </span>
                <input
                  onChange={(event) => setExecutorModel(event.target.value)}
                  placeholder="gateway/<alias> · lmstudio/<id> · claude-sonnet-5"
                  value={executorModel}
                />
              </label>
            </div>
            <div className="panel-actions">
              <button
                className="primary"
                disabled={
                  busy !== "" ||
                  !caseId ||
                  !pinnedCommit ||
                  (harness === "opencode" && executorModel.trim() === "")
                }
                onClick={() => void validate()}
                type="button"
              >
                {busy === "validate" ? "Queueing…" : "Validate"}
                <IconArrowRight size={15} />
              </button>
            </div>
            {experimentId ? (
              <div className="panel-actions">
                <Link
                  className="button-link"
                  to={`/evaluate/experiments?experiment=${experimentId}`}
                >
                  Watch the run
                </Link>
                <Link className="button-link" to={`/evidence/repositories/${targetId}`}>
                  Open this repository's workbench
                </Link>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  );
}
