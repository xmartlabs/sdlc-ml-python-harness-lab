import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { ErrorBanner } from "../components/ErrorBanner";
import { FolderPicker } from "../components/FolderPicker";
import { Markdown } from "../components/Markdown";
import { ModelPicker } from "../components/ModelPicker";
import { ReadinessReportView } from "../components/ReadinessReportView";
import { Spinner } from "../components/Spinner";
import { IconAlert, IconArrowRight, IconCheck, IconSparkles } from "../components/icons";
import { HARNESS_NATIVE_MODELS, HARNESS_OPTIONS, HOST_HARNESSES } from "../harnesses";
import type {
  CaseCandidate,
  CaseCandidates,
  CaseRecord,
  CheckoutState,
  CheckoutSummary,
  DroppedCommand,
  GateCheckRecord,
  LocalSourceListing,
  ModelOption,
  ReadinessReport,
  RepositoryAuditCase,
  RepositoryAuditCaseStatus,
  RepositoryAuditProposedCase,
  RepositoryAuditRecord,
  RepositoryAuditUseCase,
  RunConfigPayload,
  TargetRecord
} from "../types";

const STEPS = [
  { key: "clone", label: "Point at the repository" },
  { key: "audit", label: "Harness audit" },
  { key: "case", label: "One case" },
  { key: "experiment", label: "Run an experiment" }
] as const;

type StepKey = (typeof STEPS)[number]["key"];

type CaseTab = "ai" | "history" | "manual" | "existing";

// The comparison an experiment runs. A bare arm (empty workspace, governance
// masked) only exists in the lab venue — it cannot be put inside somebody
// else's repository (ADR-0049), so the honest control here is the repository
// as it is, not nothing.
type Comparison = "native_only" | "native_vs_injected";

const REPETITION_OPTIONS = [1, 2, 3, 5];

/**
 * Where step 1's checkout comes from.
 *
 * All three end in the same place — a git repository under the lab's own
 * `data/checkouts/` — because a run container mounts only the lab root, so a
 * repository anywhere else on the host is invisible to every run.
 */
type CloneSource = "remote" | "existing" | "local";

// Ticks of the 3s poller before the wait explains itself. Long enough that a
// clone in progress is not interrupted by a wall of text, short enough that
// nobody watches a spinner wondering what it is watching.
const WAIT_TICKS_BEFORE_DIAGNOSIS = 5;

const MAX_STEP_OPTIONS = [5, 10, 20, 40];

// How the lab's per-case verdict reads. "Static" is not a lesser outcome: a
// case answered by reading the repository is settled, and no runtime can block
// it — which is why a Shell repository is evaluable at all.
const CASE_STATUS_LABEL: Record<RepositoryAuditCaseStatus, string> = {
  runnable: "runnable",
  static: "static",
  blocked: "blocked",
};

const CASE_STATUS_BADGE: Record<RepositoryAuditCaseStatus, string> = {
  runnable: "badge ok",
  static: "badge neutral",
  blocked: "badge warn",
};

const SHAPE_LABEL: Record<string, string> = {
  in_place: "governs this repository",
  installable: "installed into another project",
  both: "governs this repository and installs into others",
  unknown: "undetermined"
};

const MEASURES_LABEL: Record<string, string> = {
  harness_claim: "measures a harness claim",
  harness_development: "measures work on the harness",
  unknown: "unclassified"
};

const COVERAGE_LABEL: Record<"none" | "partial" | "full", string> = {
  none: "none",
  partial: "partial",
  full: "full",
};

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
    scoring_rubric:
      `Score 10 when ${target.name}'s own gate passes at this commit and the ` +
      "change stays within the scope the candidate describes; partial credit " +
      "when the gate passes but the scope is exceeded; 0 when the gate fails.",
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
  // The folder picker leads: most repositories being audited are already on
  // the machine, and the two clone-it-yourself paths are the fallback.
  const [cloneSource, setCloneSource] = useState<CloneSource>("local");
  const [checkouts, setCheckouts] = useState<CheckoutSummary[]>([]);
  const [listing, setListing] = useState<LocalSourceListing | null>(null);
  const [localPath, setLocalPath] = useState("");
  const [localCommand, setLocalCommand] = useState("");
  // How long the poller has watched without finding anything, so a wait that
  // is going nowhere can say what it is looking at.
  const [waitTicks, setWaitTicks] = useState(0);

  // Step 2 — the LLM's reading of the repository's harness, then what it
  // already has on its own.
  const [repositoryAudit, setRepositoryAudit] = useState<RepositoryAuditRecord | null>(
    null
  );
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [gate, setGate] = useState<GateCheckRecord | null>(null);
  const [gateError, setGateError] = useState("");

  // Step 3 — one case, from whichever of the four sources fits.
  const [caseTab, setCaseTab] = useState<CaseTab>("ai");
  const [caseId, setCaseId] = useState("");
  // The cases picked for the experiment. `caseId` tracks the most recently
  // created or selected one, for the tabs' own "in the catalog" badges;
  // `caseIds` is the set step 4 actually runs.
  const [caseIds, setCaseIds] = useState<string[]>([]);
  const [manualCaseId, setManualCaseId] = useState("");
  const [caseName, setCaseName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [commandText, setCommandText] = useState("");
  const [scoringRubric, setScoringRubric] = useState("");
  const [maxSteps, setMaxSteps] = useState(10);
  const [aiDescription, setAiDescription] = useState("");
  const [aiDropped, setAiDropped] = useState<DroppedCommand[]>([]);
  const [candidates, setCandidates] = useState<CaseCandidates | null>(null);
  const [existingCases, setExistingCases] = useState<CaseRecord[]>([]);

  // Step 4 — one experiment, against this repository alone or against it and
  // the lab harness both.
  const [comparison, setComparison] = useState<Comparison>("native_vs_injected");
  const [repetitions, setRepetitions] = useState(1);
  // claude runs on its CLI default, so it is the harness that needs the least
  // configuration to answer "does this case pass here at all?".
  const [harness, setHarness] = useState<(typeof HARNESS_OPTIONS)[number]>("claude");
  const [executorModel, setExecutorModel] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [directErrors, setDirectErrors] = useState<Record<string, string>>({});
  const [gatewayDefault, setGatewayDefault] = useState("");
  const [reloadingModels, setReloadingModels] = useState(false);

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
   * The model catalog, so step 3 offers models instead of asking for an id.
   *
   * Loaded with the page rather than with the step: by the time somebody
   * reaches "one case that passes" the list is already there, and a gateway
   * that is slow to answer does not make the last step feel broken. A failure
   * is not surfaced — the picker says for itself that it has nothing, and a
   * host-CLI harness does not need the catalog at all.
   */
  const loadModels = (refresh: boolean) => {
    setReloadingModels(refresh);
    return api
      .listModels(refresh)
      .then((catalog) => {
        setModelOptions(catalog.models);
        setDirectErrors(catalog.direct_errors ?? {});
        setGatewayDefault(catalog.default_executor ?? "");
      })
      .catch(() => {})
      .finally(() => setReloadingModels(false));
  };

  useEffect(() => {
    void loadModels(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Keep the model in step with the harness it has to run under.
   *
   * The two menus are not interchangeable: `opencode` addresses gateway and
   * local ids, `claude` and `codex` address their own CLI's model names. A
   * model carried across the switch would name something the new harness
   * cannot reach, so the switch clears it and the rule below fills it back in.
   */
  const chooseHarness = (next: (typeof HARNESS_OPTIONS)[number]) => {
    setHarness(next);
    setExecutorModel("");
  };

  /**
   * A gateway harness always names a model; a host-CLI harness may decline to.
   *
   * Empty is a real answer for `claude` and `codex` — run whatever the CLI
   * picks — but for `opencode` it is a select showing an option the run would
   * not use. Adopting the catalog's default covers both orders of events: the
   * harness switched before the catalog arrived, or after.
   */
  useEffect(() => {
    if (!HOST_HARNESSES.has(harness) && executorModel === "" && gatewayDefault) {
      setExecutorModel(gatewayDefault);
    }
  }, [executorModel, gatewayDefault, harness]);

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
    setWaitTicks(0);
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
    const timer = window.setInterval(() => {
      setWaitTicks((ticks) => ticks + 1);
      load();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [cloneFound, step, targetId]);

  /**
   * Open the picker with the page, not on a click.
   *
   * The first thing this step asks for is the folder the repository is already
   * in — before any repository has been named, which is the whole point: an
   * operator should see their own directories, not a button promising them.
   * Re-runs are cheap and guarded: once `listing` is set this returns.
   */
  useEffect(() => {
    if (step !== "clone" || cloneFound || listing) {
      return;
    }
    if (targetId && !checkout?.local_sources_enabled) {
      return;
    }
    let cancelled = false;
    api
      .browseLocalSources("")
      .then((result) => {
        if (!cancelled) {
          setListing(result);
        }
      })
      .catch(() => {
        // The picker's own button asks again.
      });
    return () => {
      cancelled = true;
    };
  }, [checkout?.local_sources_enabled, cloneFound, listing, step, targetId]);

  /**
   * Clones already sitting under data/checkouts, whoever made them.
   *
   * Loaded with the step rather than on demand: the whole point is that the
   * operator sees the clone they already have without having to suspect it
   * exists.
   */
  useEffect(() => {
    if (step !== "clone" || !targetId) {
      return;
    }
    let cancelled = false;
    api
      .listCheckouts()
      .then((rows) => {
        if (!cancelled) {
          setCheckouts(rows);
        }
      })
      .catch(() => setCheckouts([]));
    return () => {
      cancelled = true;
    };
  }, [step, targetId]);

  // A gate check runs in a worker, so the record is polled until it settles.
  useEffect(() => {
    if (step !== "audit" || !targetId || !gateRunning) {
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

  /**
   * Read whatever harness audit already exists the moment this step opens.
   *
   * A 404 means nobody has generated one yet — a fact to render quietly, not
   * an error to surface, so it is handled here rather than through `run()`.
   */
  useEffect(() => {
    if (step !== "audit" || !targetId) {
      return;
    }
    let cancelled = false;
    api
      .getRepositoryAudit(targetId)
      .then((record) => {
        if (!cancelled) {
          setRepositoryAudit(record);
        }
      })
      .catch((loadError: unknown) => {
        if (cancelled) {
          return;
        }
        if (loadError instanceof ApiError && loadError.status === 404) {
          setRepositoryAudit(null);
          return;
        }
        // Transient: the "Generate the audit" button asks again.
      });
    return () => {
      cancelled = true;
    };
  }, [step, targetId]);

  // The audit runs in the worker, so the record is polled until it settles.
  useEffect(() => {
    if (step !== "audit" || !targetId || repositoryAudit?.status !== "running") {
      return;
    }
    let cancelled = false;
    const timer = window.setInterval(() => {
      api
        .getRepositoryAudit(targetId)
        .then((record) => {
          if (!cancelled) {
            setRepositoryAudit(record);
          }
        })
        .catch(() => {
          // Transient: the next tick asks again.
        });
    }, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [repositoryAudit?.status, step, targetId]);

  // The catalog is needed by step 3's fourth tab and by step 4's case picker,
  // so it is fetched when either opens rather than on every visit to the page.
  useEffect(() => {
    if (step !== "case" && step !== "experiment") {
      return;
    }
    api
      .listCases()
      .then(setExistingCases)
      .catch(() => {
        // Both steps degrade gracefully with an empty catalog.
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

  const copyText = (value: string) => {
    if (!value) {
      return;
    }
    void navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  // Point this target at a clone that is already on disk. Attaching is not
  // pinning: the commit stays a separate, deliberate act.
  const attach = (name: string) =>
    run("attach", async () => {
      const state = await api.attachCheckout(targetId, name);
      setCheckout(state);
      setNotice(
        `Using the clone in ${name}. Pin it to freeze the repository at a commit.`
      );
    });

  // The lab cannot see the operator's own folders, so when the picker is off
  // this is all it can offer: the exact command, for their own terminal.
  const buildLocalCommand = () =>
    run("local-command", async () => {
      const result = await api.localCopyCommand(targetId, localPath.trim());
      setLocalCommand(result.command);
    });

  const browseLocal = (path: string) =>
    run("browse", async () => {
      setListing(await api.browseLocalSources(path));
    });

  // The picker's single action. Two API calls because copying and pinning are
  // two things the lab does, but one press because "use this folder" is one
  // decision — and a copy nobody pinned is a repository the rest of the audit
  // refuses to read.
  const useThisFolder = (source: string) =>
    run("from-local", async () => {
      await api.cloneFromLocal(targetId, source);
      const state = await api.pinCheckout(targetId);
      setCheckout(state);
      setNotice(
        `Copied and frozen at ${state.pinned_commit.slice(0, 8)}. Your folder was ` +
          "only read, and only committed work came across. Everything from here " +
          "reads that commit until you pick the folder again."
      );
    });

  /**
   * Register the picked folder as a repository, copy it in, and freeze it.
   *
   * The form used to come first, which put a GitHub URL between the operator
   * and a repository already sitting on their disk. The folder knows its own
   * remote and branch, so picking it is the whole of step 1.
   */
  const adoptFolder = async (source: LocalSourceListing) => {
    const path = source.path ?? "";
    const folder = path.split("/").filter(Boolean).pop() ?? "";
    const url = (source.remote_url ?? "").trim() || newUrl.trim();
    if (!url) {
      throw new Error(
        "This folder has no `origin` remote, so the lab has no URL to record " +
          "for it. Put one in the field below and pick the folder again."
      );
    }
    const taken = new Set(targets.map((item) => item.target_id));
    const base = slugify(folder) || "repository";
    let id = base;
    for (let suffix = 2; taken.has(id); suffix += 1) {
      id = `${base}-${suffix}`;
    }
    const created = await api.createTarget({
      target_id: id,
      name: folder || id,
      repository_url: url,
      default_ref: (source.branch ?? "").trim() || "main"
    });
    setTargets((current) => [created, ...current]);
    setTargetId(created.target_id);
    await api.cloneFromLocal(created.target_id, path);
    const state = await api.pinCheckout(created.target_id);
    setCheckout(state);
    setNotice(
      `${created.name} is registered, copied and frozen at ` +
        `${state.pinned_commit.slice(0, 8)}. Your folder was only read, and only ` +
        "committed work came across. Everything from here reads that commit until " +
        "you pick the folder again."
    );
  };

  /**
   * Adopt a folder named by the picker, whether or not we are standing in it.
   *
   * A repository row offers the action directly — no "open it first, then find
   * the button" — so the folder's own remote and branch may not be loaded yet.
   * One extra read of that directory is what makes the row a single click.
   */
  const auditFolderAt = (path: string) =>
    run("adopt", async () => {
      const source =
        listing && (listing.path ?? "") === path
          ? listing
          : await api.browseLocalSources(path);
      setListing(source);
      await adoptFolder(source);
    });

  /**
   * The folder browser, shared by the two places that offer it: before a
   * repository exists (where picking one registers it) and after (where it
   * fills a checkout the operator already named).
   *
   * When no directory is mounted there is nothing to browse, and the button is
   * the only honest thing to show — pressing it produces the reason.
   */
  const folderPicker = (
    onUse: (path: string) => void,
    useLabel: string,
    workingLabel: string,
    useHint: string,
    working: boolean
  ) =>
    listing?.enabled ? (
      <FolderPicker
        browsing={busy === "browse"}
        disabled={busy !== ""}
        listing={listing}
        onBrowse={(path) => void browseLocal(path)}
        onUse={onUse}
        useHint={useHint}
        useLabel={useLabel}
        working={working}
        workingLabel={workingLabel}
      />
    ) : (
      <div className="panel-actions">
        <button
          className="secondary"
          disabled={busy !== ""}
          onClick={() => void browseLocal("")}
          type="button"
        >
          {busy === "browse" ? "Opening…" : "Browse my repositories"}
        </button>
      </div>
    );

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

  /**
   * Ask the worker to read the repository's harness and write a report.
   *
   * Whether the wizard can continue past this step is decided from
   * `evidence_json.next_step`, computed by the lab from deterministic facts —
   * not from anything the model itself concludes.
   */
  const runAudit = () =>
    run("repo-audit", async () => {
      await api.runRepositoryAudit(targetId);
      setRepositoryAudit(await api.getRepositoryAudit(targetId));
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

  /**
   * Record a case as both "the one the tabs point at" and "one to run".
   *
   * `caseId` drives the tabs' own "already in the catalog" badges; `caseIds`
   * is the set step 4 actually runs, so every source that lands on a case —
   * drafted, derived, hand-written, or reused — adds to both.
   */
  const selectCase = (id: string) => {
    setCaseId(id);
    setCaseIds((current) => (current.includes(id) ? current : [...current, id]));
  };

  // Step 4's checkbox list adds and drops cases from the set the experiment
  // runs; unlike `selectCase` it does not touch `caseId`, which the case
  // step's own tabs still read for their "already in the catalog" badges.
  const toggleCaseId = (id: string) =>
    setCaseIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id]
    );

  /**
   * Carry one proposed use case into the case form, structured.
   *
   * The button used to flatten the title, the reason and the hint into one
   * sentence and hand it to the case drafter to recover a command from. That
   * threw away work the audit had already done: the hint had been split into a
   * command and a note, and the command classified against the runner. A
   * runnable case therefore arrives with its command in the command field and
   * its rubric started from the auditor's own words — editable, but never
   * empty.
   */
  const seedCaseFromUseCase = (
    useCase: RepositoryAuditUseCase,
    planned?: RepositoryAuditCase
  ) => {
    const command = planned?.validation_command ?? "";
    const note = planned?.validation_note ?? useCase.validation_hint;
    // The id the AI tab would have derived from the subject. The "By hand" tab
    // used to be reached only by typing, so it never derived one, and a case
    // seeded from the audit arrived with the field empty and the button greyed.
    setManualCaseId(slugify(`${targetId}-${useCase.title}`));
    setCaseName(useCase.title.slice(0, 100));
    setInstruction(`${useCase.title}. ${useCase.why}`);
    if (command) {
      setCommandText(command);
      setCaseTab("manual");
    } else {
      // Nothing to run: the drafter's job is to turn the written check into a
      // command, which is exactly what it is for.
      setAiDescription(`${useCase.title}. ${useCase.why} Validate with: ${note}`);
      setCaseTab("ai");
    }
    setScoringRubric(
      `${useCase.why}\n\nScore 10 when this is true and the check passes; ` +
        `0 when it is not.${note ? `\n\nWhat to look for: ${note}` : ""}`
    );
    setStep("case");
  };

  /**
   * A proposed case (ADR-0058) carries its task and observable; the runner
   * match that decides its command is keyed by title like every use case.
   */
  const seedCaseFromProposal = (proposal: RepositoryAuditProposedCase) => {
    const cases = repositoryAudit?.evidence_json.case_plan?.cases;
    const planned = cases?.find((row) => row.title === proposal.title);
    seedCaseFromUseCase(
      {
        title: proposal.title,
        why: proposal.observable || proposal.task,
        validation_hint: proposal.validation_hint
      },
      planned
    );
    if (proposal.task) {
      setInstruction(proposal.task);
    }
  };

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
      // A case with commands and no rubric is refused at save time
      // (ADR-0057); the draft always carries one, model-written or defaulted.
      setScoringRubric(draft.scoring_rubric ?? "");
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
        scoring_rubric: scoringRubric.trim(),
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
          selectCase(created.case_id);
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
        selectCase(created.case_id);
      } catch (createError) {
        // An earlier session already accepted this candidate. That is the
        // outcome the button was after, so it reads as success.
        if (!isDuplicateId(createError)) {
          throw createError;
        }
        selectCase(payload.case_id);
        setNotice(`'${payload.case_id}' already existed — reusing it.`);
      }
      setCaseName(candidate.subject.slice(0, 100));
      setInstruction(candidate.suggested_prompt);
      setCommandText((target.validation_commands ?? []).join("\n"));
    });

  const selectExistingCase = (record: CaseRecord) => {
    selectCase(record.case_id);
    setCaseName(record.name || record.case_id);
    setInstruction(record.prompt_variants?.default ?? record.description ?? "");
    setCommandText((record.validation_commands ?? []).join("\n"));
    if (record.max_steps) {
      setMaxSteps(record.max_steps);
    }
    setNotice(`Case '${record.case_id}' selected.`);
  };

  /**
   * Create under the first free id derived from `base`, stepping past any
   * that collide. Both experiment kinds below race the same catalog, so the
   * retry lives once instead of twice.
   */
  const createExperimentUnderFreeId = async (
    base: string,
    create: (experimentId: string) => Promise<{ experiment_id: string }>
  ) => {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const candidate = attempt === 0 ? base : `${base}-${attempt + 1}`;
      try {
        return await create(candidate);
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

  /**
   * Queue the experiment step 4 configured.
   *
   * `native_only` is a subject validation with a single arm — it answers
   * whether the selected cases pass here at all. `native_vs_injected` is a
   * harness evaluation: the only thing that varies between its two arms is
   * whether the lab harness is injected into the same workspace at the same
   * commit, so the comparison isolates the harness and nothing else.
   */
  const runExperiment = () =>
    run("run-experiment", async () => {
      const executor = executorModel.trim() === "" ? null : executorModel.trim();
      const launchConfig: RunConfigPayload = {
        case_id: caseIds[0] ?? "",
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
      };

      if (comparison === "native_only") {
        const created = await createExperimentUnderFreeId(
          slugify(`validate-${targetId}`).slice(0, 56),
          (experimentId) =>
            api.createExperiment({
              experiment_id: experimentId,
              name: `First pass: ${target?.name ?? targetId}`,
              question: `Do the selected cases pass against ${target?.name ?? targetId} at the pinned commit?`,
              mode: "subject_validation",
              subject: target?.name ?? targetId,
              case_ids: caseIds,
              repetitions,
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
            })
        );
        await api.launchExperiment(created.experiment_id, launchConfig);
        setExperimentId(created.experiment_id);
        setNotice("Run queued. It reads the pinned commit, with no control arm.");
        return;
      }

      const created = await createExperimentUnderFreeId(
        slugify(`audit-${targetId}`).slice(0, 56),
        (experimentId) =>
          api.createExperiment({
            experiment_id: experimentId,
            name: `Harness audit: ${target?.name ?? targetId}`,
            question: `Does the injected lab harness improve over ${target?.name ?? targetId}'s own setup?`,
            mode: "harness_evaluation",
            case_ids: caseIds,
            repetitions,
            conditions: [
              {
                label: "native",
                role: "control",
                description: "The repository's own governance, with nothing injected.",
                overrides: {
                  target_repo_id: targetId,
                  target_ref: pinnedCommit,
                  harness_source: "native",
                  harness,
                  executor_model: executor
                }
              },
              {
                label: "injected",
                role: "treatment",
                description: "The lab harness (ml-python-base) injected into the workspace.",
                overrides: {
                  target_repo_id: targetId,
                  target_ref: pinnedCommit,
                  harness_source: "injected",
                  harness,
                  executor_model: executor
                }
              }
            ]
          })
      );
      await api.launchExperiment(created.experiment_id, launchConfig);
      setExperimentId(created.experiment_id);
      setNotice(
        "Comparison queued: this repository as it is, against the lab harness " +
          "injected into the same workspace."
      );
    });

  const stepIndex = STEPS.findIndex((item) => item.key === step);
  // Nothing after step 1 means anything without a pin: the audit and
  // readiness both read the pinned commit, and a case derived from a moving
  // branch measures the branch. Step 4 additionally needs a case to run.
  const reached = (key: StepKey) => {
    if (key === "clone") {
      return true;
    }
    if (key === "experiment") {
      return pinnedCommit !== "" && caseIds.length > 0;
    }
    return pinnedCommit !== "";
  };

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
      <label className="full">
        <span>Scoring rubric — what a judge should look for (optional)</span>
        <textarea
          onChange={(event) => setScoringRubric(event.target.value)}
          placeholder="Score 10 when the check passes and the change stays inside the declared files; 0 when it does not."
          rows={3}
          value={scoringRubric}
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

  // Step 4's picker: every case already in the catalog, plus any case picked
  // in step 3 that has not made it into `existingCases` yet (fetched only
  // once, when a step that needs it opens).
  const experimentCaseRows: CaseRecord[] = [
    ...existingCases,
    ...caseIds
      .filter((id) => !existingCases.some((record) => record.case_id === id))
      .map(
        (id): CaseRecord => ({
          case_id: id,
          name: id,
          type: "",
          status: "",
          sync_state: "",
          description: ""
        })
      )
  ];

  return (
    <section className="page audit">
      <header className="page-header">
        <div>
          <h1>Set up a repository for audit</h1>
          <p className="lede">
            Four steps: you point the lab at the repository and it freezes a commit,
            an LLM reads what harness it ships and what it could be tried on, you
            settle on one case, and then an experiment runs it — either alone or
            against the lab's own harness injected into the same workspace.
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
              <h2>Pick the folder you want audited</h2>
            </div>
            <p className="muted">
              Browse to the repository you already have on this machine. The lab reads
              its remote and branch off the folder, copies it into its own folder and
              freezes it at the commit it is on — no URL to type, no credentials, no
              network.
            </p>
            <div className="callout">
              <IconAlert size={16} />
              <div>
                <p>
                  The folder you pick is frozen at the commit it is on right now.
                  Commits you make afterwards do not reach the lab — every run, report
                  and case reads the frozen copy until you come back to this step and
                  pick the folder again.
                </p>
                <p className="muted small">
                  The copy is made with <code>git clone --no-hardlinks</code>: local, no
                  network, no credentials, and your working copy is only read. Only
                  committed work comes across — uncommitted files stay where they are.
                </p>
              </div>
            </div>
            {folderPicker(
              (path) => void auditFolderAt(path),
              "Audit this folder",
              "Registering and freezing…",
              "Registers it, copies it in, and freezes it at its current commit.",
              busy === "adopt"
            )}
          </div>

          <div className="panel">
            <div className="panel-head">
              <h2>Or register one the lab has to clone</h2>
            </div>
            <p className="muted">
              For a repository that is not on this machine yet. The lab records it so
              runs can name it; fetching the code is the next step, and it happens in
              your terminal.
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
            <h2>Point the lab at the repository</h2>
            <span className={`badge ${pinnedCommit ? "ok" : cloneFound ? "warn" : "neutral"}`}>
              {pinnedCommit ? "Pinned" : cloneFound ? "Clone found" : "Nothing picked yet"}
            </span>
          </div>
          <p className="muted">
            Find the folder where you already have{" "}
            <strong>{target?.name ?? targetId}</strong> on this machine. The lab copies it
            into its own folder and freezes it at the commit it is on: your working copy
            is only read, never written to, and the remote is never contacted. If the
            repository is not here yet, the other two tabs clone it.
          </p>
          {!checkout ? <Spinner label="Reading the checkout" /> : null}

          {checkout && !cloneFound ? (
            <>
              <div className="tab-bar tab-bar--inline">
                <button
                  className={`tab ${cloneSource === "local" ? "tab--active" : ""}`}
                  onClick={() => {
                    setCloneSource("local");
                    if (checkout.local_sources_enabled && !listing) {
                      void browseLocal("");
                    }
                  }}
                  type="button"
                >
                  Pick a folder on this machine
                </button>
                <button
                  className={`tab ${cloneSource === "existing" ? "tab--active" : ""}`}
                  onClick={() => setCloneSource("existing")}
                  type="button"
                >
                  A clone I already have
                  {checkouts.length ? (
                    <span className="badge pick-tag">{checkouts.length}</span>
                  ) : null}
                </button>
                <button
                  className={`tab ${cloneSource === "remote" ? "tab--active" : ""}`}
                  onClick={() => setCloneSource("remote")}
                  type="button"
                >
                  Clone from the remote
                </button>
              </div>

              {cloneSource === "local" ? (
                <>
                  <div className="callout">
                    <IconAlert size={16} />
                    <div>
                      <p>
                        The folder you pick is copied and frozen at the commit it is on
                        right now. Commits you make afterwards do not reach the lab —
                        every run, report and case reads the frozen copy until you come
                        back to this step and pick the folder again.
                      </p>
                      <p className="muted small">
                        The copy is made with <code>git clone --no-hardlinks</code>:
                        local, no network, no credentials, and your working copy is only
                        read. Only committed work comes across — uncommitted files stay
                        where they are. It exists because a run container mounts the
                        lab&apos;s own folder and nothing else.
                      </p>
                    </div>
                  </div>

                  {checkout.local_sources_enabled ? (
                    <>
                      {folderPicker(
                        (path) => void useThisFolder(path),
                        "Use this folder",
                        "Copying and freezing…",
                        "Copies it in and freezes it at its current commit.",
                        busy === "from-local"
                      )}
                      <p className="muted small">
                        Or run it yourself, from any path on this machine:
                      </p>
                    </>
                  ) : (
                    <p className="muted small">
                      The folder picker has no directory to browse. It normally opens on
                      the directory the lab sits in; set{" "}
                      <code>HARNESS_LAB_LOCAL_SOURCES_DIR</code> in <code>.env</code> to
                      the directory that holds your repositories and restart the lab.
                      Meanwhile this command does the same job from your own terminal:
                    </p>
                  )}

                  <div className="field">
                    <label htmlFor="local-source">
                      Absolute path of the repository you already have
                    </label>
                    <input
                      id="local-source"
                      onChange={(event) => setLocalPath(event.target.value)}
                      placeholder="/Users/you/code/payments"
                      value={localPath}
                    />
                  </div>
                  <div className="panel-actions">
                    <button
                      className="secondary"
                      disabled={busy !== "" || !localPath.trim()}
                      onClick={() => void buildLocalCommand()}
                      type="button"
                    >
                      {busy === "local-command" ? "Writing…" : "Write the command"}
                    </button>
                  </div>
                  {localCommand ? (
                    <div className="command-block">
                      <pre>{localCommand}</pre>
                      <button
                        className="secondary"
                        onClick={() => copyText(localCommand)}
                        type="button"
                      >
                        {copied ? "Copied" : "Copy"}
                      </button>
                    </div>
                  ) : null}
                </>
              ) : null}

              {cloneSource === "existing" ? (
                <>
                  <p className="muted">
                    Clones already in the lab&apos;s folder. A repository is often
                    cloned once and audited twice, so attaching one is not a second
                    copy of anybody&apos;s code — both targets read the same checkout.
                  </p>
                  {checkouts.length === 0 ? (
                    <EmptyState
                      description="Nothing under data/checkouts yet. Clone from the remote, or copy a folder you already have."
                      title="No clones on disk."
                    />
                  ) : (
                    <ul className="pick-list">
                      {checkouts.map((item) => (
                        <li key={item.name}>
                          <button
                            className="secondary"
                            disabled={busy !== ""}
                            onClick={() => void attach(item.name)}
                            type="button"
                          >
                            <strong>
                              {item.name}
                              {item.claimed_by.length ? (
                                <span className="badge pick-tag">
                                  used by {item.claimed_by.join(", ")}
                                </span>
                              ) : null}
                            </strong>
                            <span className="muted">
                              {item.remote_url || "no remote"} · {item.branch} ·{" "}
                              {item.head.slice(0, 8)}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              ) : null}

              {cloneSource === "remote" ? (
                <>
                  <p className="muted">
                    The lab has no credentials for this repository, and a run container
                    has none either. You clone it here, where your ssh agent and tokens
                    already work; the lab then reads your clone and never contacts the
                    remote again.
                  </p>
                  <ol className="numbered-steps">
                    <li>Open a terminal on this machine (the one running the lab).</li>
                    <li>Paste the command below and run it. It clones into the
                      lab&apos;s own folder, which is where the lab looks.</li>
                    <li>Leave this page open — it notices the clone within a few
                      seconds.</li>
                  </ol>
                  <div className="command-block">
                    <pre>{checkout.command}</pre>
                    <button
                      className="secondary"
                      onClick={() => copyText(checkout.command)}
                      type="button"
                    >
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                  <p className="muted small">
                    It lands in <code>{checkout.host_path}</code>. Nothing else in the
                    command needs changing — the destination is already the path the
                    lab watches.
                  </p>
                </>
              ) : null}
            </>
          ) : null}

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

          {checkout && !cloneFound && waitTicks >= WAIT_TICKS_BEFORE_DIAGNOSIS ? (
            <div className="callout callout--warning">
              <IconAlert size={16} />
              <div>
                <p>
                  Still nothing at <code>{checkout.host_path}</code>. The lab watches
                  that one directory, so a clone made anywhere else — or under another
                  name — never shows up here.
                </p>
                <p className="muted small">
                  {checkout.exists
                    ? "The directory is there but it is not a git repository."
                    : "The directory does not exist yet."}{" "}
                  If the clone is still running, leave this open. Otherwise:{" "}
                  {checkouts.length
                    ? "attach one of the clones already on disk"
                    : "copy a folder you already have"}{" "}
                  from the tabs above. The page keeps watching either way.
                </p>
              </div>
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

              {checkout.attached ? (
                <p className="muted small">
                  Reading <code>{checkout.host_path}</code> — a clone that was already
                  there, not one named after this repository. That is deliberate; the
                  pin and every run read this directory.
                </p>
              ) : null}

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
                      setStep("audit");
                      void loadReadiness();
                    }}
                    type="button"
                  >
                    Harness audit
                    <IconArrowRight size={15} />
                  </button>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {step === "audit" ? (
        <>
          <div className="panel">
            <div className="panel-head">
              <h2>What harness this repository ships</h2>
              {repositoryAudit ? (
                <span
                  className={`badge ${
                    repositoryAudit.status === "running"
                      ? "neutral"
                      : repositoryAudit.status === "done"
                        ? "ok"
                        : "error"
                  }`}
                >
                  {repositoryAudit.status}
                </span>
              ) : null}
            </div>
            <p className="muted">
              The lab reads the repository at the pinned commit — rules files, skills,
              agents, commands, hooks, CI, build files, README — and an LLM writes a
              short report: which harness parts exist, the language, and what could be
              tried here. Whether the wizard can continue past this step is decided
              from that evidence, not by the model.
            </p>

            {!repositoryAudit ? <p className="muted small">Not generated yet.</p> : null}

            {repositoryAudit?.status === "running" ? (
              <Spinner label="Reading the repository with the auditor" />
            ) : null}

            {repositoryAudit?.status === "failed" ? (
              <ErrorBanner message={repositoryAudit.error ?? "The audit failed"} />
            ) : null}

            {repositoryAudit?.status === "done" ? (
              <>
                <div className="fact-row">
                  <div>
                    <span className="muted small">Commit</span>
                    <code>{repositoryAudit.commit.slice(0, 8)}</code>
                  </div>
                  <div>
                    <span className="muted small">Language</span>
                    <code>
                      {repositoryAudit.evidence_json.runtime?.ecosystems.join(", ") ||
                        "not detected"}
                    </code>
                  </div>
                  <div>
                    <span className="muted small">Harness</span>
                    <code>
                      {"harness_verdict" in repositoryAudit.findings_json
                        ? repositoryAudit.findings_json.harness_verdict
                        : "unknown"}
                    </code>
                  </div>
                  <div>
                    <span className="muted small">Runner</span>
                    <code>
                      {(repositoryAudit.evidence_json.runtime?.capabilities ?? [])
                        .filter(
                          (capability) =>
                            capability.available &&
                            capability.runtime !== "sh" &&
                            capability.runtime !== "git"
                        )
                        .map((capability) =>
                          capability.version
                            ? `${capability.runtime} ${capability.version}`
                            : capability.runtime
                        )
                        .join(" · ") || "not probed"}
                    </code>
                  </div>
                  {repositoryAudit.evidence_json.case_plan ? (
                    <div>
                      <span className="muted small">Coverage</span>
                      <code>
                        {COVERAGE_LABEL[repositoryAudit.evidence_json.case_plan.coverage]}
                        {" · "}
                        {repositoryAudit.evidence_json.case_plan.runnable} runnable ·{" "}
                        {repositoryAudit.evidence_json.case_plan.static} static ·{" "}
                        {repositoryAudit.evidence_json.case_plan.blocked} blocked
                      </code>
                    </div>
                  ) : null}
                  <div>
                    <span className="muted small">Model</span>
                    <code>{repositoryAudit.auditor_model}</code>
                  </div>
                </div>

                <Markdown>{repositoryAudit.markdown}</Markdown>

                {repositoryAudit.evidence_json.next_step === "unsupported_runtime" ? (
                  <div className="callout callout--warning">
                    <IconAlert size={16} />
                    <div>
                      <p>
                        <strong>The lab cannot run cases here.</strong>{" "}
                        {repositoryAudit.evidence_json.next_step_reason}
                      </p>
                      <p className="muted small">
                        This is the end of the wizard for this repository — the runner
                        cannot execute anything in its runtime. The readiness and gate
                        check below still work; neither one runs an agent.
                      </p>
                    </div>
                  </div>
                ) : null}

                {repositoryAudit.evidence_json.next_step === "no_harness" ? (
                  <>
                    <div className="callout callout--warning">
                      <IconAlert size={16} />
                      <div>
                        <p>
                          <strong>No harness to evaluate.</strong>{" "}
                          {repositoryAudit.evidence_json.next_step_reason}
                        </p>
                      </div>
                    </div>
                    <div className="panel-actions">
                      <button
                        className="secondary"
                        onClick={() => setStep("case")}
                        type="button"
                      >
                        Continue anyway — compare it against the lab harness
                      </button>
                    </div>
                  </>
                ) : null}

                {repositoryAudit.evidence_json.next_step === "continue" ? (
                  <>
                    <div className="callout callout--ok">
                      <IconCheck size={16} />
                      <div>
                        <p>
                          <strong>This repository can be evaluated here.</strong>{" "}
                          {repositoryAudit.evidence_json.next_step_reason}
                        </p>
                      </div>
                    </div>
                    <div className="panel-actions">
                      <button
                        className="primary"
                        onClick={() => setStep("case")}
                        type="button"
                      >
                        Choose or create a case
                        <IconArrowRight size={15} />
                      </button>
                    </div>
                  </>
                ) : null}

                {repositoryAudit.findings_json.shape ||
                repositoryAudit.evidence_json.health_at_pin ? (
                  <div className="fact-row">
                    {repositoryAudit.findings_json.shape ? (
                      <div>
                        <span className="muted small">Harness shape</span>
                        <code>
                          {SHAPE_LABEL[repositoryAudit.findings_json.shape.shape] ??
                            repositoryAudit.findings_json.shape.shape}
                          {repositoryAudit.findings_json.shape.install_entry
                            ? ` · ${repositoryAudit.findings_json.shape.install_entry}${
                                repositoryAudit.findings_json.shape.install_entry_exists
                                  ? ""
                                  : " (not found)"
                              }`
                            : ""}
                        </code>
                      </div>
                    ) : null}
                    {repositoryAudit.findings_json.shape?.unit_of_work ? (
                      <div>
                        <span className="muted small">Unit of work</span>
                        <code>{repositoryAudit.findings_json.shape.unit_of_work}</code>
                      </div>
                    ) : null}
                    {repositoryAudit.evidence_json.health_at_pin ? (
                      <div>
                        <span className="muted small">Suite at the pin</span>
                        <code>
                          {repositoryAudit.evidence_json.health_at_pin.status}
                          {repositoryAudit.evidence_json.health_at_pin.counts
                            ? ` · ${repositoryAudit.evidence_json.health_at_pin.counts.passed} passed, ${repositoryAudit.evidence_json.health_at_pin.counts.failed} failed`
                            : ""}
                          {repositoryAudit.evidence_json.health_at_pin.reason
                            ? ` — ${repositoryAudit.evidence_json.health_at_pin.reason}`
                            : ""}
                        </code>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {repositoryAudit.findings_json.component_map &&
                repositoryAudit.findings_json.component_map.length > 0 ? (
                  <>
                    <h3>Component map</h3>
                    <p className="muted small">
                      What each harness component claims, what would show it helped,
                      and whether this lab can evaluate it. Evaluability is the lab's
                      verdict from declared services, not the auditor's opinion.
                    </p>
                    <div className="table-scroll">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Component</th>
                            <th>Claims</th>
                            <th>Observable</th>
                            <th>Lab counterpart</th>
                            <th>Needs</th>
                            <th>Evaluable</th>
                          </tr>
                        </thead>
                        <tbody>
                          {repositoryAudit.findings_json.component_map.map((row) => (
                            <tr key={`${row.kind}-${row.name}`}>
                              <td>
                                <span className="badge">{row.kind}</span>{" "}
                                <code>{row.name}</code>
                                {row.source_path ? (
                                  <div className="muted small">{row.source_path}</div>
                                ) : null}
                              </td>
                              <td>{row.claim}</td>
                              <td>{row.observable}</td>
                              <td>
                                {row.lab_counterpart ? (
                                  <code>{row.lab_counterpart}</code>
                                ) : (
                                  <span className="muted">—</span>
                                )}
                              </td>
                              <td>{row.needs.join(", ")}</td>
                              <td>
                                <span
                                  className={row.evaluable_here ? "badge ok" : "badge warn"}
                                  title={row.evaluable_reason || undefined}
                                >
                                  {row.evaluable_here ? "yes" : "no"}
                                </span>
                                {!row.evaluable_here && row.evaluable_reason ? (
                                  <div className="muted small">{row.evaluable_reason}</div>
                                ) : null}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    {repositoryAudit.findings_json.warnings &&
                    repositoryAudit.findings_json.warnings.length > 0 ? (
                      <details>
                        <summary className="muted small">
                          {repositoryAudit.findings_json.warnings.length} contract warning(s)
                        </summary>
                        <ul className="check-list">
                          {repositoryAudit.findings_json.warnings.map((warning) => (
                            <li key={warning} className="small muted">
                              {warning}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </>
                ) : null}

                {repositoryAudit.findings_json.cases &&
                repositoryAudit.findings_json.cases.length > 0 ? (
                  <>
                    <h3>Cases the auditor proposes</h3>
                    <ul className="check-list">
                      {repositoryAudit.findings_json.cases.map((proposal) => {
                        const cases = repositoryAudit.evidence_json.case_plan?.cases;
                        const planned = cases?.find((row) => row.title === proposal.title);
                        const dropped = proposal.dropped_reason !== "";
                        return (
                          <li key={proposal.title}>
                            <strong>{proposal.title}</strong>{" "}
                            <span className="badge">
                              {MEASURES_LABEL[proposal.measures] ?? proposal.measures}
                            </span>
                            {planned && !dropped ? (
                              <>
                                {" "}
                                <span className={CASE_STATUS_BADGE[planned.status]}>
                                  {CASE_STATUS_LABEL[planned.status]}
                                </span>
                              </>
                            ) : null}
                            <p className="small muted">
                              Exercises:{" "}
                              {proposal.exercises.length > 0
                                ? proposal.exercises.map((name) => (
                                    <code key={name}>{name} </code>
                                  ))
                                : "—"}
                            </p>
                            {dropped ? (
                              <p className="small warn">Dropped: {proposal.dropped_reason}</p>
                            ) : (
                              <>
                                {proposal.observable ? (
                                  <p className="muted">{proposal.observable}</p>
                                ) : null}
                                <p className="small muted">
                                  Check:{" "}
                                  {planned?.validation_command ? (
                                    <code>{planned.validation_command}</code>
                                  ) : (
                                    (planned?.validation_note ?? proposal.validation_hint)
                                  )}
                                </p>
                                {proposal.conditions && proposal.conditions.length > 0 ? (
                                  <p className="small muted">
                                    Derived arms:{" "}
                                    {proposal.conditions.map((arm) => (
                                      <span
                                        key={arm.label}
                                        className="badge"
                                        title={JSON.stringify(arm.overrides)}
                                      >
                                        {arm.label}
                                      </span>
                                    ))}
                                  </p>
                                ) : null}
                                {proposal.conditions_note ? (
                                  <p className="small muted">{proposal.conditions_note}</p>
                                ) : null}
                                {planned ? (
                                  <p className="small muted">{planned.reason}</p>
                                ) : null}
                                {planned?.status !== "blocked" ? (
                                  <button
                                    className="secondary"
                                    onClick={() => seedCaseFromProposal(proposal)}
                                    type="button"
                                  >
                                    Draft this as a case
                                  </button>
                                ) : null}
                              </>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : null}

                {repositoryAudit.findings_json.use_cases &&
                repositoryAudit.findings_json.use_cases.length > 0 ? (
                  <>
                    <h3>Use cases the auditor proposes</h3>
                    <ul className="check-list">
                      {repositoryAudit.findings_json.use_cases.map((useCase, index) => {
                        // The lab's own verdict on this one case. Absent on audits
                        // recorded before cases were matched against capabilities,
                        // which then read exactly as they did before.
                        //
                        // Matched by title, not by position: the plan is built
                        // from the same list, but a badge that lines up only by
                        // accident is a badge that will one day describe the
                        // wrong case, and nothing on screen would say so.
                        const cases = repositoryAudit.evidence_json.case_plan?.cases;
                        const planned =
                          cases?.find((row) => row.title === useCase.title) ??
                          cases?.[index];
                        return (
                          <li key={useCase.title}>
                            <strong>{useCase.title}</strong>
                            {planned ? (
                              <>
                                {" "}
                                <span className={CASE_STATUS_BADGE[planned.status]}>
                                  {CASE_STATUS_LABEL[planned.status]}
                                </span>
                                {planned.compatibility === "partial" ? (
                                  <span className="badge warn">
                                    {planned.runtime} {planned.runtime_version} vs{" "}
                                    {planned.required_runtime_version} — partial
                                  </span>
                                ) : null}
                              </>
                            ) : null}
                            <p className="muted">{useCase.why}</p>
                            <p className="small muted">
                              Check:{" "}
                              {planned?.validation_command ? (
                                <code>{planned.validation_command}</code>
                              ) : (
                                (planned?.validation_note ?? useCase.validation_hint)
                              )}
                            </p>
                            {planned ? (
                              <p className="small muted">{planned.reason}</p>
                            ) : null}
                            {planned?.validation_repair ? (
                              <p className="small warn">
                                {planned.validation_repair}
                              </p>
                            ) : null}
                            {(
                              planned
                                ? planned.status !== "blocked"
                                : repositoryAudit.evidence_json.next_step !==
                                  "unsupported_runtime"
                            ) ? (
                              <button
                                className="secondary"
                                onClick={() => {
                                  // The audit already separated the command
                                  // from the prose around it. Flattening the
                                  // two back into one sentence and asking a
                                  // model to recover the command is how a case
                                  // ends up validated by something nobody
                                  // wrote — so the command travels as a
                                  // command, and the rubric starts from what
                                  // the auditor said the case is for.
                                  seedCaseFromUseCase(useCase, planned);
                                }}
                                type="button"
                              >
                                Draft this as a case
                              </button>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : null}
              </>
            ) : null}

            <div className="panel-actions">
              <button
                className="primary"
                disabled={busy !== "" || repositoryAudit?.status === "running"}
                onClick={() => void runAudit()}
                type="button"
              >
                {busy === "repo-audit" || repositoryAudit?.status === "running"
                  ? "Auditing…"
                  : repositoryAudit?.status === "done"
                    ? "Audit again"
                    : "Generate the audit"}
              </button>
            </div>
          </div>

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

            <div className="panel-actions">
              <button
                className="primary"
                disabled={caseIds.length === 0}
                onClick={() => setStep("experiment")}
                type="button"
              >
                Run an experiment
                <IconArrowRight size={15} />
              </button>
            </div>
            <p className="muted small">
              Selected for the experiment: {caseIds.length > 0 ? caseIds.join(", ") : "none yet"}
            </p>
          </div>
        </>
      ) : null}

      {step === "experiment" ? (
        <div className="panel">
          <div className="panel-head">
            <h2>Run an experiment against this repository</h2>
            {experimentId ? <span className="badge neutral">{experimentId}</span> : null}
          </div>

          <div className="check-list">
            {experimentCaseRows.map((record) => (
              <label className="checkbox" key={record.case_id}>
                <input
                  checked={caseIds.includes(record.case_id)}
                  onChange={() => toggleCaseId(record.case_id)}
                  type="checkbox"
                />
                <span>
                  {record.name || record.case_id}
                  {record.type ? <span className="badge pick-tag">{record.type}</span> : null}
                  {record.origin_target_id === targetId ? (
                    <span className="badge pick-tag">from this repository</span>
                  ) : null}
                </span>
              </label>
            ))}
          </div>

          <div className="form-grid">
            <label className="checkbox full">
              <input
                checked={comparison === "native_only"}
                name="comparison"
                onChange={() => setComparison("native_only")}
                type="radio"
              />
              <span>
                <strong>This repository only — one arm</strong>
                <p className="muted small">
                  No control arm: it answers whether the case passes here at all.
                </p>
              </span>
            </label>
            <label className="checkbox full">
              <input
                checked={comparison === "native_vs_injected"}
                name="comparison"
                onChange={() => setComparison("native_vs_injected")}
                type="radio"
              />
              <span>
                <strong>This repository vs the lab harness (ml-python-base)</strong>
                <p className="muted small">
                  Control: the repository's own governance, nothing injected.
                  Treatment: the lab's harness (the synced ml-python-base release)
                  injected into the same workspace at the same commit. The only thing
                  that varies is the harness.
                </p>
              </span>
            </label>
          </div>
          <p className="muted small">
            A <em>bare</em> arm (empty workspace, governance masked) only exists in the
            lab venue — it cannot be put inside somebody else's repository (ADR-0049).
            The honest control here is the repository as it is.
          </p>

          <div className="form-grid">
            <label className="field">
              <span>Repetitions</span>
              <select
                onChange={(event) => setRepetitions(Number(event.target.value))}
                value={repetitions}
              >
                {REPETITION_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
              <span className="muted small">
                1 is exploratory; 5 or more per arm is what a comparison needs to be
                trusted.
              </span>
            </label>
            <label className="field">
              <span>Harness</span>
              <select
                onChange={(event) =>
                  chooseHarness(event.target.value as (typeof HARNESS_OPTIONS)[number])
                }
                value={harness}
              >
                {HARNESS_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
              {HOST_HARNESSES.has(harness) ? (
                <span className="muted small">
                  Runs the {harness} CLI with its native auth.{" "}
                  {harness === "claude"
                    ? "Works in Docker (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env) or host mode (make app-host)."
                    : "Needs host mode — run make app-host."}
                </span>
              ) : null}
            </label>
            <div className="field">
              <ModelPicker
                allowNone={HOST_HARNESSES.has(harness)}
                directErrors={directErrors}
                label="Executor model"
                nativeOptions={HARNESS_NATIVE_MODELS[harness]}
                noneLabel="CLI default"
                onChange={setExecutorModel}
                onReload={
                  HOST_HARNESSES.has(harness) ? undefined : () => void loadModels(true)
                }
                options={modelOptions}
                reloading={reloadingModels}
                value={executorModel}
              >
                <small className="muted">
                  {HOST_HARNESSES.has(harness)
                    ? `Pick a model the ${harness} CLI accepts, or leave it on its own default.`
                    : "opencode has no default of its own — this run needs a model named here."}
                </small>
              </ModelPicker>
            </div>
            <label className="field">
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

          <div className="panel-actions">
            <button
              className="primary"
              disabled={
                busy !== "" ||
                caseIds.length === 0 ||
                !pinnedCommit ||
                (harness === "opencode" && executorModel.trim() === "")
              }
              onClick={() => void runExperiment()}
              type="button"
            >
              {busy === "run-experiment" ? "Queueing…" : "Create and launch"}
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
      ) : null}
    </section>
  );
}
