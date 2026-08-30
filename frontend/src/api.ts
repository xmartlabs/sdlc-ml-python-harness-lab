import type {
  ArtifactRecord,
  AttributionRecord,
  AuditRecord,
  ImprovementsDoc,
  CaseCreatePayload,
  CaseCandidates,
  ConditionRecord,
  CaseRecord,
  ContextProbeResult,
  DocContent,
  ExperimentRecord,
  ExperimentSummary,
  FindingRecord,
  DocEntry,
  EnvSettings,
  EnvUpdateResult,
  GatewayModelRecord,
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
  GroupIssueResult,
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
  RunEventRecord,
  RunRecord,
  SandboxRecord,
  StagedSkillFileRecord,
  StagedSkillImportChoices,
  StagedSkillImportPreview,
  StagedSkillPromoteResult,
  StagedSkillRecord,
  SwebenchProInstance,
  TargetCreatePayload,
  TargetRecord,
  TargetResolveResult,
  ValidationRecord
} from "./types";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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
    try {
      const parsed = JSON.parse(text) as { detail?: string };
      if (parsed.detail) {
        message = parsed.detail;
      }
    } catch {
      // Response body was not JSON; fall back to the raw text.
    }
    throw new ApiError(message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
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
  listExperiments: () => request<ExperimentRecord[]>("/api/experiments"),
  getExperiment: (id: string) =>
    request<ExperimentRecord & { conditions: ConditionRecord[]; runs: unknown[] }>(
      `/api/experiments/${id}`
    ),
  createExperiment: (payload: unknown) =>
    request<ExperimentRecord>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
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
  deriveCases: (targetId: string, limit = 20) =>
    request<CaseCandidates>(
      `/api/targets/${targetId}/derive-cases?limit=${limit}`,
      { method: "POST" }
    ),
  listCases: () => request<CaseRecord[]>("/api/cases"),
  createCase: (payload: CaseCreatePayload) =>
    request<CaseRecord>("/api/cases", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getCase: (caseId: string) => request<CaseRecord>(`/api/cases/${caseId}`),
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
  seedTetris: () => request<CaseRecord>("/api/cases/seed/tetris", { method: "POST" }),
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
    })
};
