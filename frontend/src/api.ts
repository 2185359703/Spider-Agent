import { mockBundle, mockTasks } from "./mock";
import type { CreateBatchRequest, GlobalSample, GlobalSubmission, PolicyVersion, RepositoryStatusBundle, SubmissionDiff, Task, TaskBundle } from "./types";

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
  const [task, report, specs, submissions, evidence, validation, timeline, manualRuns, reviews, repairs, failures, samples] = await Promise.all([
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
  ]);
  return { task, report, specs, submissions, evidence, validation, timeline, manualRuns, reviews, repairs, failures, samples };
}

export async function listAllSubmissions(): Promise<GlobalSubmission[]> {
  if (!API_BASE) {
    return mockBundle.submissions.map((submission) => ({
      ...submission,
      task_id: mockBundle.task.task_id,
      platform_key: mockBundle.task.platform_key,
      platform_name: mockBundle.task.platform_name,
      entry_url: mockBundle.task.entry_url,
      created_at: mockBundle.task.updated_at,
    }));
  }
  return request<GlobalSubmission[]>("/api/v1/onboarding/submissions");
}

export async function listAllSamples(): Promise<GlobalSample[]> {
  if (!API_BASE) {
    return mockBundle.samples.samples.map((sample) => ({
      ...sample,
      task_id: mockBundle.task.task_id,
      platform_key: mockBundle.task.platform_key,
      platform_name: mockBundle.task.platform_name,
      entry_url: mockBundle.task.entry_url,
      manual_run_id: "manual-demo",
      code_revision: "e2159d7853542380ae0f80ae1a3220fd6a1bc139",
    }));
  }
  return request<GlobalSample[]>("/api/v1/onboarding/samples");
}

export async function listPolicies(): Promise<PolicyVersion[]> {
  if (!API_BASE) return [];
  return request<PolicyVersion[]>("/api/v1/policies");
}

export async function getRepositoryStatus(): Promise<RepositoryStatusBundle> {
  if (!API_BASE) {
    return {
      control_plane: { path: "D:/Project/Auto_spider", remote_configured: false, push_enabled: false },
      source: { path: "D:/Project/Auto_spider_repositories/fun-crawler-v2", baseline_ref: "16cba84", is_git_repository: true, baseline_available: true, dirty_files: [] },
      aicoding: { path: "D:/Project/Auto_spider_repositories/aicoding-auto_spider", remote_url: "https://gitee.com/daxia-com/auto_spider.git", push_enabled: true, is_git_repository: true, dirty_files: [] },
    };
  }
  return request<RepositoryStatusBundle>("/api/v1/system/repositories");
}

export async function getSubmissionDiff(taskId: string, submissionId: string): Promise<SubmissionDiff> {
  if (!API_BASE) {
    return {
      submission_id: submissionId,
      available: true,
      baseline_ref: "16cba8439396e371973e4ee0301d3a88f2f32ba5",
      commit_sha: "e2159d7853542380ae0f80ae1a3220fd6a1bc139",
      changed_files: ["collectors/kuaishou.py", "config/platforms/kuaishou.toml"],
      diff: "# Mock diff\n+collector candidate changes are available in the AI output repository.\n",
    };
  }
  return request<SubmissionDiff>(`/api/v1/onboarding/tasks/${taskId}/submissions/${submissionId}/diff`);
}

export function evidenceDownloadUrl(evidenceId: string): string {
  return `${API_BASE}/api/v1/onboarding/evidence/${evidenceId}/download`;
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
