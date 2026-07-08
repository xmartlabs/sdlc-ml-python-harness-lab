import type {
  AttributionRecord,
  AuditRecord,
  CaseCreatePayload,
  CaseRecord,
  RunCreatePayload,
  RunEventRecord,
  RunRecord,
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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {})
    },
    ...options
  });

  if (!response.ok) {
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
  login: (username: string, password: string) =>
    request<{ authenticated: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password })
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  listCases: () => request<CaseRecord[]>("/api/cases"),
  createCase: (payload: CaseCreatePayload) =>
    request<CaseRecord>("/api/cases", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listCaseRunIds: (caseId: string) =>
    request<{ run_ids: string[] }>(`/api/cases/${caseId}/run-ids`),
  seedTetris: () => request<CaseRecord>("/api/cases/seed/tetris", { method: "POST" }),
  listRuns: () => request<RunRecord[]>("/api/runs"),
  createRun: (payload: RunCreatePayload) =>
    request<RunRecord>("/api/runs", {
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
  runAudit: (runId: string, auditorModel?: string) => {
    const query = auditorModel ? `?auditor_model=${encodeURIComponent(auditorModel)}` : "";
    return request<{ status: string; run_id: string }>(`/api/runs/${runId}/audit${query}`, {
      method: "POST"
    });
  }
};
