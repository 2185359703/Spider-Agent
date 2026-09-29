import { mockBundle, mockTasks } from "./mock";
import type { CreateBatchRequest, Task, TaskBundle } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`请求失败 ${response.status}`);
  return response.json() as Promise<T>;
}

export async function listTasks(): Promise<Task[]> {
  if (!API_BASE) return mockTasks;
  return request<Task[]>("/api/v1/onboarding/tasks");
}

export async function getTaskBundle(taskId: string): Promise<TaskBundle> {
  if (!API_BASE) return { ...mockBundle, task: mockTasks.find((task) => task.task_id === taskId) ?? mockBundle.task };
  const [task, report, specs, submissions, evidence, validation] = await Promise.all([
    request<Task>(`/api/v1/onboarding/tasks/${taskId}`),
    request<TaskBundle["report"]>(`/api/v1/onboarding/tasks/${taskId}/reports/latest`).catch(() => null),
    request<TaskBundle["specs"]>(`/api/v1/onboarding/tasks/${taskId}/specs`),
    request<TaskBundle["submissions"]>(`/api/v1/onboarding/tasks/${taskId}/submissions`),
    request<TaskBundle["evidence"]>(`/api/v1/onboarding/tasks/${taskId}/evidence`),
    request<TaskBundle["validation"]>(`/api/v1/onboarding/tasks/${taskId}/validation`).catch(() => null),
  ]);
  return { task, report, specs, submissions, evidence, validation };
}

export async function createBatch(payload: CreateBatchRequest): Promise<{ batch_id: string; task_ids: string[] }> {
  if (!API_BASE) {
    const taskId = `local-${Date.now()}`;
    return { batch_id: `batch-${Date.now()}`, task_ids: [taskId] };
  }
  return request("/api/v1/onboarding/batches", { method: "POST", body: JSON.stringify(payload) });
}

export async function registerManualRun(taskId: string, payload: Record<string, unknown>) {
  if (!API_BASE) return { manual_run_id: `manual-${Date.now()}`, status: "WAITING_REVIEW" };
  return request(`/api/v1/onboarding/tasks/${taskId}/manual-runs`, { method: "POST", body: JSON.stringify(payload) });
}

export async function submitReview(taskId: string, payload: Record<string, unknown>) {
  if (!API_BASE) return { review_id: `review-${Date.now()}`, status: "ADOPTED" };
  return request(`/api/v1/onboarding/tasks/${taskId}/reviews`, { method: "POST", body: JSON.stringify(payload) });
}

export function isLiveApi() {
  return Boolean(API_BASE);
}
