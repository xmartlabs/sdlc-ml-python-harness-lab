import type {
  CaseCreatePayload,
  CaseRecord,
  RunCreatePayload,
  RunEventRecord,
  RunRecord
} from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {})
    },
    ...options
  });

  if (!response.ok) {
    throw new Error(await response.text());
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
  scoreRun: (runId: string, score: number, interventions: number, notes: string) =>
    request<RunRecord>(`/api/runs/${runId}/score`, {
      method: "POST",
      body: JSON.stringify({ score, interventions, notes })
    })
};
