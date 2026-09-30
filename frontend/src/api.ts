import type { ActorProfile, BatchDetail, BatchSummary, CreateBatchRequest, GlobalSample, GlobalSubmission, PolicyVersion, RepositoryStatusBundle, SubmissionDiff, SystemHealth, Task, TaskBundle } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";
const API_USER_ID = (import.meta.env.VITE_USER_ID as string | undefined)?.trim();
const API_USER_ROLE = (import.meta.env.VITE_USER_ROLE as string | undefined)?.trim();

function requireApi() {
  if (!API_BASE) throw new Error("VITE_API_BASE_URL 未配置，已禁用模拟数据，请先启动 API");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const identityHeaders: Record<string, string> = {};
  if (API_USER_ID) identityHeaders["X-User-Id"] = API_USER_ID;
  if (API_USER_ROLE) identityHeaders["X-User-Role"] = API_USER_ROLE;
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...identityHeaders, ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`请求失败 ${response.status}`);
  return response.json() as Promise<T>;
}

export async function getCurrentActor(): Promise<ActorProfile> {
  requireApi();
  return request<ActorProfile>("/api/v1/me");
}

export async function getSystemHealth(): Promise<SystemHealth> {
  requireApi();
  return request<SystemHealth>("/api/v1/system/health");
}

export function configuredRole(): string {
  return API_USER_ROLE ?? "admin";
}

export async function listTasks(): Promise<Task[]> {
  requireApi();
  return request<Task[]>("/api/v1/onboarding/tasks");
}

export async function getTaskBundle(taskId: string): Promise<TaskBundle> {
  requireApi();
  const [task, report, specs, submissions, evidence, validation, timeline, manualRuns, reviews, repairs, failures, samples, logs] = await Promise.all([
    request<Task>(`/api/v1/onboarding/tasks/${taskId}`),
    request<TaskBundle["report"]>(`/api/v1/onboarding/tasks/${taskId}/reports/latest`).catch(() => null),
    request<TaskBundle["specs"]>(`/api/v1/onboarding/tasks/${taskId}/specs`),
    request<TaskBundle["submissions"]>(`/api/v1/onboarding/tasks/${taskId}/submissions`),
    request<TaskBundle["evidence"]>(`/api/v1/onboarding/tasks/${taskId}/evidence`),
    request<TaskBundle["validation"]>(`/api/v1/onboarding/tasks/${taskId}/validation`).catch(() => null),
    request<TaskBundle["timeline"]>(`/api/v1/onboarding/tasks/${taskId}/timeline`).catch(() => ({ events: [], runs: [] })),
    request<TaskBundle["manualRuns"]>(`/api/v1/onboarding/tasks/${taskId}/manual-runs`).catch(() => []),
    request<TaskBundle["reviews"]>(`/api/v1/onboarding/tasks/${taskId}/reviews`).catch(() => []),
    request<TaskBundle["repairs"]>(`/api/v1/onboarding/tasks/${taskId}/repairs`).catch(() => []),
    request<TaskBundle["failures"]>(`/api/v1/onboarding/tasks/${taskId}/failures`).catch(() => []),
    request<TaskBundle["samples"]>(`/api/v1/onboarding/tasks/${taskId}/samples`).catch(() => ({ manual_run: null, count: 0, samples: [] })),
    request<{ logs: TaskBundle["logs"] }>(`/api/v1/onboarding/tasks/${taskId}/logs`).then((body) => body.logs).catch(() => []),
  ]);
  return { task, report, specs, submissions, evidence, validation, timeline, manualRuns, reviews, repairs, failures, samples, logs };
}

export async function listTaskLogs(taskId: string, after: number): Promise<{ logs: TaskBundle["logs"]; next_after: number }> {
  requireApi();
  return request(`/api/v1/onboarding/tasks/${taskId}/logs?after=${after}`);
}

export async function listAllSubmissions(): Promise<GlobalSubmission[]> {
  requireApi();
  return request<GlobalSubmission[]>("/api/v1/onboarding/submissions");
}

export async function listAllSamples(): Promise<GlobalSample[]> {
  requireApi();
  return request<GlobalSample[]>("/api/v1/onboarding/samples");
}

export async function listPolicies(): Promise<PolicyVersion[]> {
  requireApi();
  return request<PolicyVersion[]>("/api/v1/policies");
}

export async function createPolicy(payload: { name: string; version: string; status: string; policy: Record<string, unknown> }): Promise<PolicyVersion> {
  requireApi();
  return request<PolicyVersion>("/api/v1/policies", { method: "POST", body: JSON.stringify(payload) });
}

export async function getRepositoryStatus(): Promise<RepositoryStatusBundle> {
  requireApi();
  return request<RepositoryStatusBundle>("/api/v1/system/repositories");
}

export async function getSubmissionDiff(taskId: string, submissionId: string): Promise<SubmissionDiff> {
  requireApi();
  return request<SubmissionDiff>(`/api/v1/onboarding/tasks/${taskId}/submissions/${submissionId}/diff`);
}

export function evidenceDownloadUrl(evidenceId: string): string {
  requireApi();
  return `${API_BASE}/api/v1/onboarding/evidence/${evidenceId}/download`;
}

export async function createBatch(payload: CreateBatchRequest): Promise<{ batch_id: string; task_ids: string[] }> {
  requireApi();
  return request("/api/v1/onboarding/batches", { method: "POST", body: JSON.stringify(payload) });
}

export async function listBatches(): Promise<BatchSummary[]> {
  requireApi();
  return request<BatchSummary[]>("/api/v1/onboarding/batches");
}

export async function getBatch(batchId: string): Promise<BatchDetail> {
  requireApi();
  return request<BatchDetail>(`/api/v1/onboarding/batches/${batchId}`);
}

export async function resumeTask(taskId: string) {
  requireApi();
  return request(`/api/v1/onboarding/tasks/${taskId}/resume`, { method: "POST", body: JSON.stringify({ client_request_id: `frontend-resume-${Date.now()}` }) });
}

export async function triggerRepair(taskId: string, failureBundleId: string) {
  requireApi();
  return request(`/api/v1/onboarding/tasks/${taskId}/repair`, { method: "POST", body: JSON.stringify({ failure_bundle_id: failureBundleId, client_request_id: `frontend-repair-${Date.now()}` }) });
}

export async function registerManualRun(taskId: string, payload: Record<string, unknown>) {
  requireApi();
  return request(`/api/v1/onboarding/tasks/${taskId}/manual-runs`, { method: "POST", body: JSON.stringify(payload) });
}

export async function submitReview(taskId: string, payload: Record<string, unknown>) {
  requireApi();
  return request(`/api/v1/onboarding/tasks/${taskId}/reviews`, { method: "POST", body: JSON.stringify(payload) });
}

export function isLiveApi() {
  return Boolean(API_BASE);
}
