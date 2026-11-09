import type {
  ArtifactRecord,
  AttentionFeed,
  AttributionRecord,
  AuditRecord,
  CompareAuditRecord,
  ExperimentAuditRecord,
  ImprovementsDoc,
  CaseCreatePayload,
  CaseCandidates,
  CaseDraftRequest,
  CaseDraftResponse,
  ConditionRecord,
  CaseRecord,
  ContextProbeResult,
  DocContent,
  ExperimentRecord,
  ExperimentSummary,
  LaunchConfigSummary,
  FindingRecord,
  DocEntry,
  EnvSettings,
  EnvUpdateResult,
  EvidenceSummary,
  GatewayModelRecord,
  NativeModelRecord,
  GatewayTestResult,
  HarnessSkill,
  HealResult,
  HealthReport,
  LangfuseExport,
  LangfuseStatus,
  LMStudioCatalog,
  ModelRefreshResult,
  OllamaCatalog,
  RuntimeFacts,
  LeaderboardRunRow,
  ModelCatalog,
  OverviewReport,
  OfficialReferenceRow,
  ReadinessReport,
  CheckoutState,
  CheckoutSummary,
  LocalSourceListing,
  GateCheckRecord,
  GroupIssueResult,
  LocalGuardReport,
  LocalHistoryRow,
  LocalLoadConfig,
  LocalLoadResult,
  LocalMachine,
  LocalMemoryEstimate,
  LocalModelInfo,
  LocalModelLoadCheck,
  LocalProfileCompareResult,
  LocalProfileCreatePayload,
  LocalReference,
  LocalReferenceDevices,
  LocalResults,
  LocalServedConfig,
  LocalServerKind,
  LocalServerTestResult,
  LocalTestSession,
  LocalTuningProfile,
  MachineSpec,
  ProposalIssue,
  RunConfigPayload,
  ScoreRecord,
  RunBatchPayload,
  RunBatchResult,
  RunCreatePayload,
  RunValidationResult,
  SaveImprovementsResult,
  SavedProposalRecord,
  SessionState,
  RunContext,
  RunEventRecord,
  RunRecord,
  SandboxRecord,
  StagedSkillFileRecord,
  StagedSkillImportChoices,
  StagedSkillImportPreview,
  StagedSkillPromoteResult,
  StagedSkillRecord,
  SuggestionRecord,
  SwebenchProInstance,
  TargetCreatePayload,
  TargetRecord,
  TargetResolveResult,
  ValidationRecord,
  RepositoryAuditRecord
} from "./types";

export class ApiError extends Error {
  status: number;
  /**
   * The parsed `detail` payload, when the server sent one and it was a JSON
   * object rather than a string. The host guard's 409s carry `{guard: ...}`
   * here (and `tests` sometimes `{session: ...}`); a caller that needs the
   * structured body reads `error.detail` instead of re-parsing `message`.
   */
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Fired when any request comes back 401, so the shell can show the login form. */
export const SESSION_EXPIRED_EVENT = "harness-lab:session-expired";

/** Percent-encode each segment but keep the `/` separators the route expects. */
function encodeFilePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  // A multipart upload must let the browser set its own boundary header.
  const isFormData = typeof FormData !== "undefined" && options?.body instanceof FormData;
  const response = await fetch(path, {
    // The session cookie is same-origin (the dev proxy and the nginx prod build
    // both serve /api from this origin), and never sent cross-site.
    credentials: "same-origin",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(options?.headers ?? {})
    },
    ...options
  });

  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/api/auth/")) {
      // The session expired or the secret was rotated. Tell the shell once
      // instead of letting every page render its own auth error.
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const text = await response.text();
    let message = text || response.statusText;
    let objectDetail: unknown;
    try {
      const parsed = JSON.parse(text) as {
        detail?: string | string[] | { msg?: string }[] | Record<string, unknown>;
      };
      // A 422 from case validation carries one `detail` entry per offending
      // field, and FastAPI's own body errors carry objects. Joined here, so a
      // caller can render `error.message` verbatim instead of stringifying an
      // array into "a,b".
      if (Array.isArray(parsed.detail)) {
        const lines = parsed.detail
          .map((item) =>
            typeof item === "string" ? item : (item?.msg ?? JSON.stringify(item))
          )
          .filter(Boolean);
        if (lines.length > 0) {
          message = lines.join("\n");
        }
      } else if (parsed.detail && typeof parsed.detail === "object") {
        // The local-tuning host guard: `{detail: {guard: GuardReport}}` (and
        // `{detail: {session: TestSession}}` on a busy test host, or
        // `{detail: {instances, message}}` on an unload the lab did not load
        // itself). Kept structured in `detail` rather than stringified, so a
        // caller that needs the shape reads `error.detail` instead of
        // re-parsing `message` — but `message` itself is still made readable
        // here, so a caller that ignores `detail` never shows a bare
        // "Conflict".
        objectDetail = parsed.detail;
        const asRecord = parsed.detail as Record<string, unknown>;
        if (asRecord.session && typeof asRecord.session === "object") {
          const session = asRecord.session as { model_id?: string };
          message = session.model_id
            ? `A test session is already running on this host (${session.model_id}).`
            : "A test session is already running on this host.";
        } else if (typeof asRecord.message === "string") {
          message = asRecord.message;
        } else if (asRecord.guard) {
          // Handled by the host guard modal; a plain message still matters for
          // a caller that does not render that flow.
          message = "This host is busy with a lab run.";
        } else {
          message = response.statusText || text;
        }
      } else if (parsed.detail) {
        message = parsed.detail;
      }
    } catch {
      // Response body was not JSON; fall back to the raw text.
    }
    throw new ApiError(message, response.status, objectDetail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/** Compare's selection as repeated `run_id` params, in the order they appear. */
function compareQuery(runIds: string[]): string {
  const params = new URLSearchParams();
  for (const runId of runIds) params.append("run_id", runId);
  return params.toString();
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  readSession: () => request<SessionState>("/api/auth/session"),
  login: (password: string) =>
    request<SessionState>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password })
    }),
  logout: () => request<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),
  listHarnessSkills: () => request<HarnessSkill[]>("/api/harness/skills"),
  // `refresh` drops the gateway and LM Studio caches before rebuilding the
  // list — the two halves of the picker have different cache lifetimes, so a
  // reload has to clear both or it looks like it only half-worked.
  listModels: (refresh = false) =>
    request<ModelCatalog>(`/api/models${refresh ? "?refresh=true" : ""}`),
  listLmstudioModels: (refresh = false) =>
    request<LMStudioCatalog>(`/api/lmstudio/models${refresh ? "?refresh=true" : ""}`),
  listOllamaModels: (refresh = false) =>
    request<OllamaCatalog>(`/api/ollama/models${refresh ? "?refresh=true" : ""}`),
  listModelCatalog: () => request<GatewayModelRecord[]>("/api/models/catalog"),
  /** Native CLI model menus with gateway coverage (ADR-0069). */
  listNativeModels: () =>
    request<Record<string, NativeModelRecord[]>>("/api/models/native"),
  refreshModelCatalog: () =>
    request<ModelRefreshResult>("/api/models/refresh", { method: "POST" }),
  checkModelContext: (modelId: string, requiredTokens?: number) =>
    request<ContextProbeResult>(
      `/api/models/${modelId}/context-check` +
        (requiredTokens ? `?required_tokens=${requiredTokens}` : ""),
      { method: "POST" }
    ),
  updateGatewayModel: (
    modelId: string,
    changes: { label?: string; enabled?: boolean; is_default?: boolean }
  ) =>
    request<GatewayModelRecord>(`/api/models/${modelId}`, {
      method: "PATCH",
      body: JSON.stringify(changes)
    }),
  readEnvSettings: () => request<EnvSettings>("/api/settings/env"),
  writeEnvSettings: (updates: Record<string, string>) =>
    request<EnvUpdateResult>("/api/settings/env", {
      method: "PUT",
      body: JSON.stringify({ updates })
    }),
  readRuntimeFacts: () => request<RuntimeFacts>("/api/settings/runtime"),
  testGateway: (baseUrl: string, token: string) =>
    request<GatewayTestResult>("/api/settings/gateway/test", {
      method: "POST",
      body: JSON.stringify({ base_url: baseUrl, token })
    }),
  listDocs: () => request<DocEntry[]>("/api/docs"),
  getDoc: (slug: string) => request<DocContent>(`/api/docs/${slug}`),
  listStagedSkills: () => request<StagedSkillRecord[]>("/api/staged-skills"),
  createStagedSkill: (name: string, description: string, content: string) =>
    request<StagedSkillRecord>("/api/staged-skills", {
      method: "POST",
      body: JSON.stringify({ name, description, content })
    }),
  updateStagedSkill: (
    name: string,
    changes: { description?: string; content?: string; status?: string }
  ) =>
    request<StagedSkillRecord>(`/api/staged-skills/${name}`, {
      method: "PATCH",
      body: JSON.stringify(changes)
    }),
  deleteStagedSkill: (name: string) =>
    request<void>(`/api/staged-skills/${name}`, { method: "DELETE" }),
  promoteStagedSkill: (name: string) =>
    request<StagedSkillPromoteResult>(`/api/staged-skills/${name}/promote`, {
      method: "POST"
    }),
  // Two-step import: without `confirm` the server fetches, validates and
  // returns a preview, writing nothing (ADR-0017).
  // `skill` narrows a catalog source down to one of its skills; without it such
  // a source answers with `choices` instead of a preview.
  previewStagedSkillImport: (url: string, ref: string, name: string, skill = "") =>
    request<StagedSkillImportPreview | StagedSkillImportChoices>(
      "/api/staged-skills/import",
      {
        method: "POST",
        body: JSON.stringify({ url, ref, name, skill, confirm: false })
      }
    ),
  importStagedSkill: (
    url: string,
    ref: string,
    name: string,
    replace: boolean,
    skill = ""
  ) =>
    request<StagedSkillRecord>("/api/staged-skills/import", {
      method: "POST",
      body: JSON.stringify({ url, ref, name, skill, confirm: true, replace })
    }),
  uploadStagedSkill: (
    file: File,
    options: {
      name?: string;
      skill?: string;
      confirm?: boolean;
      replace?: boolean;
    } = {}
  ) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", options.name ?? "");
    form.append("skill", options.skill ?? "");
    form.append("confirm", String(options.confirm ?? false));
    form.append("replace", String(options.replace ?? false));
    return request<
      StagedSkillImportPreview | StagedSkillImportChoices | StagedSkillRecord
    >("/api/staged-skills/upload", { method: "POST", body: form });
  },
  listStagedSkillFiles: (name: string) =>
    request<StagedSkillFileRecord[]>(`/api/staged-skills/${name}/files`),
  readStagedSkillFile: (name: string, path: string) =>
    request<StagedSkillFileRecord>(
      `/api/staged-skills/${name}/files/${encodeFilePath(path)}`
    ),
  writeStagedSkillFile: (name: string, path: string, content: string) =>
    request<StagedSkillFileRecord>(
      `/api/staged-skills/${name}/files/${encodeFilePath(path)}`,
      { method: "PUT", body: JSON.stringify({ content }) }
    ),
  deleteStagedSkillFile: (name: string, path: string) =>
    request<void>(`/api/staged-skills/${name}/files/${encodeFilePath(path)}`, {
      method: "DELETE"
    }),
  getOverview: () => request<OverviewReport>("/api/overview"),
  // The home page's two calls. Kept separate because they answer different
  // questions: what should I do next, and what does the lab currently hold.
  getRunContext: (runId: string) => request<RunContext>(`/api/runs/${runId}/context`),
  getAttention: () => request<AttentionFeed>("/api/attention"),
  getEvidenceSummary: () => request<EvidenceSummary>("/api/evidence-summary"),
  listExperiments: () => request<ExperimentRecord[]>("/api/experiments"),
  getExperimentAudit: (id: string) =>
    request<ExperimentAuditRecord>(`/api/experiments/${id}/audit`),
  runExperimentAudit: (id: string, auditorModel?: string) => {
    const query = auditorModel ? `?auditor_model=${encodeURIComponent(auditorModel)}` : "";
    return request<{ status: string; experiment_id: string }>(
      `/api/experiments/${id}/audit${query}`,
      { method: "POST" }
    );
  },
  // Compare is ad-hoc: the run ids travel as repeated query params so the
  // client never has to know how the server keys the selection.
  getCompareAudit: (runIds: string[]) =>
    request<CompareAuditRecord>(`/api/compare/audit?${compareQuery(runIds)}`),
  runCompareAudit: (runIds: string[], auditorModel?: string) => {
    const params = new URLSearchParams();
    for (const runId of runIds) params.append("run_id", runId);
    if (auditorModel) params.set("auditor_model", auditorModel);
    return request<{ status: string; comparison_key: string }>(
      `/api/compare/audit?${params.toString()}`,
      { method: "POST" }
    );
  },
  getExperiment: (id: string) =>
    request<
      ExperimentRecord & {
        conditions: ConditionRecord[];
        runs: unknown[];
        launch_config: LaunchConfigSummary | null;
      }
    >(`/api/experiments/${id}`),
  createExperiment: (payload: unknown) =>
    request<ExperimentRecord>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  /**
   * A regression suite, as an experiment (§13).
   *
   * The suite contributes the design — which cases, repeated how often — and
   * the caller contributes the harness change. There is no second runner: what
   * comes back is an ordinary `harness_evaluation` experiment, read through the
   * same case matrix as everything else.
   */
  createExperimentFromSuite: (payload: {
    experiment_id: string;
    suite_id?: string;
    control: { label: string; description?: string; overrides: Record<string, unknown> };
    treatment: { label: string; description?: string; overrides: Record<string, unknown> };
    prompt_variant?: string;
    question?: string;
    name?: string;
    notes?: string;
  }) =>
    request<ExperimentRecord & { warnings?: string[] }>("/api/experiments/from-suite", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  /** Hide an experiment from the working set; its runs stay in the corpus. */
  abandonExperiment: (id: string) =>
    request<ExperimentRecord>(`/api/experiments/${id}/abandon`, { method: "POST" }),
  /** Erase an experiment and (explicitly) its runs, scores and artifacts. */
  deleteExperiment: (id: string, deleteRuns: boolean) =>
    request<{ experiment_id: string; deleted_runs: number }>(
      `/api/experiments/${id}?delete_runs=${deleteRuns}`,
      { method: "DELETE" }
    ),
  summarizeExperiment: (id: string) =>
    request<ExperimentSummary>(`/api/experiments/${id}/summary`),
  launchExperiment: (id: string, config: RunConfigPayload) =>
    request<{ count: number; run_ids: string[] }>(
      `/api/experiments/${id}/launch`,
      { method: "POST", body: JSON.stringify({ config }) }
    ),
  listFindings: () => request<FindingRecord[]>("/api/findings"),
  createFinding: (payload: unknown) =>
    request<FindingRecord>("/api/findings", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateFinding: (id: string, payload: unknown) =>
    request<FindingRecord>(`/api/findings/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  draftExperimentFromFinding: (id: string) =>
    request<Record<string, unknown>>(`/api/findings/${id}/experiment-draft`, {
      method: "POST"
    }),
  listSuggestions: () =>
    request<{ suggestions: SuggestionRecord[] }>("/api/findings/suggestions"),
  // Accepting writes a finding with the accepter's name on it; rejecting
  // writes the refusal down so the pattern is not offered again.
  acceptSuggestion: (
    key: string,
    payload: { author: string; finding_id?: string; overrides?: Record<string, unknown> }
  ) =>
    request<{ finding: FindingRecord; warnings: string[] }>(
      `/api/findings/suggestions/${encodeFilePath(key)}/accept`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
  rejectSuggestion: (key: string, payload: { author: string; reason?: string }) =>
    request<FindingRecord>(
      `/api/findings/suggestions/${encodeFilePath(key)}/reject`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
  getScoreDefinitions: () =>
    request<{
      count: number;
      categories: Record<
        string,
        { name: string; direction: string; provenance: string; category: string }[]
      >;
    }>("/api/scores/definitions"),
  listRunScores: (runId: string) =>
    request<ScoreRecord[]>(`/api/runs/${runId}/scores`),
  evaluateRunScores: (runId: string) =>
    request<ScoreRecord[]>(`/api/runs/${runId}/scores`, { method: "POST" }),
  listTargets: (includeArchived = false) =>
    request<TargetRecord[]>(
      `/api/targets?include_archived=${includeArchived ? "true" : "false"}`
    ),
  createTarget: (payload: TargetCreatePayload) =>
    request<TargetRecord>("/api/targets", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateTarget: (targetId: string, payload: Partial<TargetCreatePayload>) =>
    request<TargetRecord>(`/api/targets/${targetId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  archiveTarget: (targetId: string) =>
    request<void>(`/api/targets/${targetId}`, { method: "DELETE" }),
  // Resolve a ref without creating anything: the operator sees the exact
  // commit a run would be pinned to before spending a container on it.
  resolveTargetRef: (targetId: string, ref: string) =>
    request<TargetResolveResult>(`/api/targets/${targetId}/resolve`, {
      method: "POST",
      body: JSON.stringify({ ref })
    }),
  // Step 2 of the guided audit: what this repository already has, read from
  // the repository rather than assumed.
  getAuditReport: (targetId: string) =>
    request<{ target_id: string; markdown: string }>(
      `/api/targets/${targetId}/audit-report`
    ),
  // The LLM harness audit of a pinned repository: what harness it ships, in
  // which language, and what could be tried on it. Runs in the worker; poll
  // the GET until status leaves "running".
  getRepositoryAudit: (targetId: string) =>
    request<RepositoryAuditRecord>(`/api/targets/${targetId}/repository-audit`),
  runRepositoryAudit: (targetId: string, auditorModel?: string) => {
    const query = auditorModel
      ? `?auditor_model=${encodeURIComponent(auditorModel)}`
      : "";
    return request<{ status: string; target_id: string }>(
      `/api/targets/${targetId}/repository-audit${query}`,
      { method: "POST" }
    );
  },
  getTargetReadiness: (targetId: string) =>
    request<ReadinessReport>(`/api/targets/${targetId}/readiness`),
  // The engineer's own clone: the lab watches for it, it never makes it.
  // Their terminal has their credentials; a run container does not.
  getCheckout: (targetId: string) =>
    request<CheckoutState>(`/api/targets/${targetId}/checkout`),
  // Clones already sitting under data/checkouts, whoever made them. One repo
  // is often cloned once and audited twice; this is what the page offers
  // instead of asking for a second copy of a client's code.
  listCheckouts: () => request<CheckoutSummary[]>("/api/checkouts"),
  // Point a target at one of them. Attaching is not pinning.
  attachCheckout: (targetId: string, name: string) =>
    request<CheckoutState>(`/api/targets/${targetId}/checkout/attach`, {
      method: "POST",
      body: JSON.stringify({ name })
    }),
  // The command that copies a repository they already have, for their own
  // terminal: local, so no network and no credentials — but the lab cannot see
  // the path, so it can only hand over the command.
  localCopyCommand: (targetId: string, source: string) =>
    request<{ target_id: string; command: string }>(
      `/api/targets/${targetId}/checkout/local-command`,
      { method: "POST", body: JSON.stringify({ source }) }
    ),
  // The opt-in picker: one level of the directory they mounted read-only.
  browseLocalSources: (path = "") =>
    request<LocalSourceListing>(
      `/api/local-sources?path=${encodeURIComponent(path)}`
    ),
  // Copy the chosen repository into data/checkouts/<target_id>. A run container
  // mounts only the lab root, so a repository anywhere else is invisible to it.
  cloneFromLocal: (targetId: string, source: string) =>
    request<CheckoutState>(`/api/targets/${targetId}/checkout/from-local`, {
      method: "POST",
      body: JSON.stringify({ source })
    }),
  // Freeze the repository at the commit their clone is on. Every run, report
  // and derived case reads that commit until somebody re-pins.
  pinCheckout: (targetId: string) =>
    request<CheckoutState>(`/api/targets/${targetId}/checkout/pin`, {
      method: "POST"
    }),
  // Measure the repository alone: its own gate, at the pin, with no agent —
  // so a red run afterwards is a fact about the agent, not about the image.
  startGateCheck: (targetId: string) =>
    request<GateCheckRecord>(`/api/targets/${targetId}/gate-check`, {
      method: "POST"
    }),
  getGateCheck: (targetId: string) =>
    request<GateCheckRecord | null>(`/api/targets/${targetId}/gate-check`),
  // Inspect a repository before it is saved: the same readiness report a target
  // would produce, so a mismatch (unsupported runtime, or a command whose
  // toolchain the repo lacks) is caught before creation rather than at a run.
  inspectTarget: (payload: {
    repository_url: string;
    default_ref?: string;
    setup_commands?: string[];
    validation_commands?: string[];
  }) =>
    request<ReadinessReport>("/api/targets/inspect", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  deriveCases: (targetId: string, limit = 20) =>
    request<CaseCandidates>(
      `/api/targets/${targetId}/derive-cases?limit=${limit}`,
      { method: "POST" }
    ),
  listCases: (originTargetId = "") =>
    request<CaseRecord[]>(
      originTargetId
        ? `/api/cases?origin_target_id=${encodeURIComponent(originTargetId)}`
        : "/api/cases"
    ),
  createCase: (payload: CaseCreatePayload) =>
    request<CaseRecord>("/api/cases", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getCase: (caseId: string) => request<CaseRecord>(`/api/cases/${caseId}`),
  /**
   * Run this case's own checks against the clean, pinned tree.
   *
   * A check that passes here passed before the agent started, so its green says
   * nothing about the agent. The verdict lands on the case as
   * `metadata_json.dry_run`.
   */
  dryRunCase: (caseId: string) =>
    request<{ status: string; case_id: string }>(`/api/cases/${caseId}/dry-run`, {
      method: "POST"
    }),
  getCasePromptContent: (caseId: string, variant: string) =>
    request<{ variant: string; content: string }>(
      `/api/cases/${caseId}/prompt-content?variant=${encodeURIComponent(variant)}`
    ),
  updateCase: (caseId: string, payload: CaseCreatePayload) =>
    request<CaseRecord>(`/api/cases/${caseId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  // Soft delete: the API flips status to `deleted` and returns the case.
  deleteCase: (caseId: string) =>
    request<CaseRecord>(`/api/cases/${caseId}`, { method: "DELETE" }),
  cloneCase: (caseId: string, newCaseId: string, newName: string) =>
    request<CaseRecord>(
      `/api/cases/${caseId}/clone?new_case_id=${encodeURIComponent(
        newCaseId
      )}&new_name=${encodeURIComponent(newName)}`,
      { method: "POST" }
    ),
  syncCase: (caseId: string) =>
    request<{ task_path: string }>(`/api/cases/${caseId}/sync`, { method: "POST" }),
  listCaseRunIds: (caseId: string) =>
    request<{ run_ids: string[] }>(`/api/cases/${caseId}/run-ids`),
  importSwebench: (instanceId: string) =>
    request<CaseRecord>("/api/cases/import-swebench", {
      method: "POST",
      body: JSON.stringify({ instance_id: instanceId })
    }),
  listSwebenchProInstances: () =>
    request<SwebenchProInstance[]>("/api/swebench-pro/instances"),
  importSwebenchPro: (instanceId: string) =>
    request<CaseRecord>("/api/cases/import-swebench-pro", {
      method: "POST",
      body: JSON.stringify({ instance_id: instanceId })
    }),
  generateCaseDraft: (payload: CaseDraftRequest) =>
    request<CaseDraftResponse>("/api/cases/generate-draft", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  seedBenchmarks: () => request<CaseRecord[]>("/api/cases/seed", { method: "POST" }),

  listRuns: () => request<RunRecord[]>("/api/runs"),
  getLeaderboard: () => request<LeaderboardRunRow[]>("/api/leaderboard"),
  getLeaderboardReference: () =>
    request<OfficialReferenceRow[]>("/api/leaderboard/reference"),
  // Pre-creation check on a *configuration*. Not to be confused with
  // `validateRun` below, which validates the artifacts of a finished run.
  preflightRunConfig: (config: RunConfigPayload) =>
    request<RunValidationResult>("/api/runs/preflight", {
      method: "POST",
      body: JSON.stringify(config)
    }),
  createRun: (payload: RunCreatePayload) =>
    request<RunRecord>("/api/runs", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  // All-or-nothing: the server validates every combination before creating any,
  // so a rejection here means nothing was queued.
  createRunBatch: (payload: RunBatchPayload) =>
    request<RunBatchResult>("/api/runs/batch", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getRun: (runId: string) => request<RunRecord>(`/api/runs/${runId}`),
  listRunLogs: (runId: string) =>
    request<RunEventRecord[]>(`/api/runs/${runId}/logs?ascending=true`),
  cancelRun: (runId: string) =>
    request<RunRecord>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  retryRun: (runId: string) =>
    request<RunRecord>(`/api/runs/${runId}/retry`, { method: "POST" }),
  cloneRun: (runId: string) =>
    request<RunRecord>(`/api/runs/${runId}/clone`, { method: "POST" }),
  // Queues a child run seeded from this run's workspace (ADR-0026).
  continueRun: (runId: string, prompt: string, maxSteps?: number) =>
    request<RunRecord>(`/api/runs/${runId}/continue`, {
      method: "POST",
      body: JSON.stringify({ prompt, max_steps: maxSteps ?? null })
    }),
  deleteRun: (runId: string, force = false) =>
    request<void>(`/api/runs/${runId}?force=${force ? "true" : "false"}`, { method: "DELETE" }),
  scoreRun: (runId: string, score: number, interventions: number, notes: string) =>
    request<RunRecord>(`/api/runs/${runId}/score`, {
      method: "POST",
      body: JSON.stringify({ score, interventions, notes })
    }),
  getValidation: (runId: string) =>
    request<ValidationRecord>(`/api/runs/${runId}/validation`),
  validateRun: (runId: string) =>
    request<{ status: string; run_id: string }>(`/api/runs/${runId}/validate`, {
      method: "POST"
    }),
  getAttribution: (runId: string) =>
    request<AttributionRecord>(`/api/runs/${runId}/attribution`),
  indexAttribution: (runId: string) =>
    request<AttributionRecord>(`/api/runs/${runId}/attribution/index`, {
      method: "POST"
    }),
  getAudit: (runId: string) => request<AuditRecord>(`/api/runs/${runId}/audit`),
  getLangfuse: (runId: string) =>
    request<LangfuseStatus>(`/api/runs/${runId}/langfuse`),
  exportLangfuse: (runId: string) =>
    request<LangfuseExport>(`/api/runs/${runId}/langfuse-export`, {
      method: "POST"
    }),
  getImprovements: (runId: string) =>
    request<ImprovementsDoc>(`/api/runs/${runId}/improvements`),
  getProposalIssue: (runId: string) =>
    request<ProposalIssue>(`/api/runs/${runId}/proposal-issue`),
  saveImprovements: (runId: string) =>
    request<SaveImprovementsResult>(`/api/runs/${runId}/improvements/save`, {
      method: "POST"
    }),
  listSavedProposals: () => request<SavedProposalRecord[]>("/api/improvements"),
  deleteSavedProposal: (proposalId: number) =>
    request<void>(`/api/improvements/${proposalId}`, { method: "DELETE" }),
  createGroupIssue: (proposalIds: number[], model?: string) =>
    request<GroupIssueResult>("/api/improvements/group-issue", {
      method: "POST",
      body: JSON.stringify({ proposal_ids: proposalIds, model: model ?? null })
    }),
  runImprove: (runId: string, model?: string, strictness?: string) => {
    const params = new URLSearchParams();
    if (model) params.set("model", model);
    if (strictness) params.set("strictness", strictness);
    const query = params.size > 0 ? `?${params.toString()}` : "";
    return request<{ status: string; run_id: string }>(
      `/api/runs/${runId}/improve${query}`,
      { method: "POST" }
    );
  },
  runAudit: (runId: string, auditorModel?: string) => {
    const query = auditorModel ? `?auditor_model=${encodeURIComponent(auditorModel)}` : "";
    return request<{ status: string; run_id: string }>(`/api/runs/${runId}/audit${query}`, {
      method: "POST"
    });
  },
  indexArtifacts: (runId: string) =>
    request<ArtifactRecord[]>(`/api/runs/${runId}/artifacts/index`, { method: "POST" }),
  listArtifacts: (runId: string) =>
    request<ArtifactRecord[]>(`/api/runs/${runId}/artifacts`),
  readArtifact: (runId: string, name: string) =>
    request<{ content: string }>(`/api/runs/${runId}/artifacts/${name}`),
  getSandbox: (runId: string, kind: string) =>
    request<SandboxRecord>(`/api/runs/${runId}/sandbox?kind=${kind}`),
  startSandbox: (runId: string, kind: string) =>
    request<{ status: string; run_id: string; kind: string }>(
      `/api/runs/${runId}/sandbox?kind=${kind}`,
      { method: "POST" }
    ),
  stopSandbox: (runId: string, kind: string) =>
    request<{ status: string }>(`/api/runs/${runId}/sandbox?kind=${kind}`, {
      method: "DELETE"
    }),
  readHealth: () => request<HealthReport>("/api/health/detail"),
  heal: (action: string) =>
    request<HealResult>("/api/health/heal", {
      method: "POST",
      body: JSON.stringify({ action })
    }),

  // --- Tune your local model (spec 2026-11-08-local-model-tuning) ----------
  local: {
    getReference: () => request<LocalReference>("/api/local/reference"),
    referenceDevices: () =>
      request<LocalReferenceDevices>("/api/local/reference-devices"),

    listMachines: () => request<LocalMachine[]>("/api/local/machines"),
    createMachine: (payload: Omit<LocalMachine, "id" | "created_at">) =>
      request<LocalMachine>("/api/local/machines", {
        method: "POST",
        body: JSON.stringify(payload)
      }),
    updateMachine: (id: string, payload: Partial<Omit<LocalMachine, "id" | "created_at">>) =>
      request<LocalMachine>(`/api/local/machines/${id}`, {
        method: "PUT",
        body: JSON.stringify(payload)
      }),
    deleteMachine: (id: string) =>
      request<void>(`/api/local/machines/${id}`, { method: "DELETE" }),

    testServer: (kind: LocalServerKind, url: string) =>
      request<LocalServerTestResult>("/api/local/servers/test", {
        method: "POST",
        body: JSON.stringify({ kind, url })
      }),

    estimate: (model: LocalModelInfo, config: LocalLoadConfig, machine: MachineSpec) =>
      request<LocalMemoryEstimate>("/api/local/estimate", {
        method: "POST",
        body: JSON.stringify({ model, config, machine })
      }),

    guard: (kind: LocalServerKind, url: string) =>
      request<LocalGuardReport>("/api/local/guard", {
        method: "POST",
        body: JSON.stringify({ kind, url })
      }),

    load: (
      kind: LocalServerKind,
      url: string,
      modelId: string,
      config: LocalLoadConfig,
      acknowledgeGuard = false,
      replace = false
    ) =>
      request<LocalLoadResult>("/api/local/load", {
        method: "POST",
        body: JSON.stringify({
          kind,
          url,
          model_id: modelId,
          config,
          acknowledge_guard: acknowledgeGuard,
          replace
        })
      }),

    unload: (
      kind: LocalServerKind,
      url: string,
      modelId: string,
      acknowledgeGuard = false,
      instanceId: string | null = null,
      force = false
    ) =>
      request<void>("/api/local/unload", {
        method: "POST",
        body: JSON.stringify({
          kind,
          url,
          model_id: modelId,
          acknowledge_guard: acknowledgeGuard,
          instance_id: instanceId,
          force
        })
      }),

    startTests: (
      kind: LocalServerKind,
      url: string,
      model: LocalModelInfo,
      config: LocalLoadConfig,
      machine: MachineSpec,
      served: LocalServedConfig,
      acknowledgeGuard = false,
      machineId: number | null = null,
      estimate: LocalMemoryEstimate | null = null,
      evaluateOnly = false
    ) =>
      request<LocalTestSession>("/api/local/tests", {
        method: "POST",
        body: JSON.stringify({
          kind,
          url,
          model,
          config,
          machine,
          served,
          acknowledge_guard: acknowledgeGuard,
          machine_id: machineId,
          estimate,
          evaluate_only: evaluateOnly
        })
      }),
    getTestSession: (sessionId: string) =>
      request<LocalTestSession>(`/api/local/tests/${sessionId}`),

    listProfiles: (machineId: string, modelId: string) =>
      request<LocalTuningProfile[]>(
        `/api/local/profiles?machine_id=${encodeURIComponent(machineId)}&model_id=${encodeURIComponent(modelId)}`
      ),
    /** A machine's saved test runs, newest first, with the numbers that tell them apart. */
    history: (machineId: string, modelId?: string) =>
      request<LocalHistoryRow[]>(
        `/api/local/history?machine_id=${encodeURIComponent(machineId)}` +
          (modelId ? `&model_id=${encodeURIComponent(modelId)}` : "")
      ),
    /** Speed tests and local-model case runs, each ranked on its own terms. */
    results: () => request<LocalResults>("/api/local/results"),
    createProfile: (payload: LocalProfileCreatePayload) =>
      request<LocalTuningProfile>("/api/local/profiles", {
        method: "POST",
        body: JSON.stringify(payload)
      }),
    getProfile: (id: string) => request<LocalTuningProfile>(`/api/local/profiles/${id}`),
    updateProfile: (id: string, changes: { label?: string; recommended?: boolean; observed_memory_gb?: number | null }) =>
      request<LocalTuningProfile>(`/api/local/profiles/${id}`, {
        method: "PATCH",
        body: JSON.stringify(changes)
      }),
    deleteProfile: (id: string) =>
      request<void>(`/api/local/profiles/${id}`, { method: "DELETE" }),
    compareProfiles: (a: string, b: string) =>
      request<LocalProfileCompareResult>(
        `/api/local/profiles/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`
      ),

    modelLoadCheck: (model: string) =>
      request<LocalModelLoadCheck>(`/api/local/model-load-check?model=${encodeURIComponent(model)}`)
  }
};
