import type {
  Task,
  TaskBundle,
  GlobalSubmission,
  SystemHealth,
  ActorProfile,
  AiGatewayStatus,
  RepositoryStatusBundle,
  PolicyVersion,
} from "../types";
const base =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  ) || "";
function identity() {
  const h: Record<string, string> = {};
  if (import.meta.env.VITE_USER_ID)
    h["X-User-Id"] = import.meta.env.VITE_USER_ID;
  if (import.meta.env.VITE_USER_ROLE)
    h["X-User-Role"] = import.meta.env.VITE_USER_ROLE;
  return h;
}
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...identity(),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new ApiError(
      typeof error?.detail === "string"
        ? error.detail
        : `接口请求失败 (${response.status})`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
export const post = <T>(path: string, payload: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(payload) });
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function download(path: string, filename: string) {
  const r = await fetch(base + path, { headers: identity() });
  if (!r.ok) throw new Error(`下载失败 (${r.status})`);
  saveBlob(await r.blob(), filename);
}
export const tasksApi = () => request<Task[]>("/api/v1/onboarding/tasks");
export const submissionsApi = () =>
  request<GlobalSubmission[]>("/api/v1/onboarding/submissions");
export const actorApi = () => request<ActorProfile>("/api/v1/me");
export const healthApi = () => request<SystemHealth>("/api/v1/system/health");
export const aiGatewayApi = () =>
  request<AiGatewayStatus>("/api/v1/system/ai-gateway");
export const selectAiGateway = (profile: string) =>
  post<AiGatewayStatus>("/api/v1/system/ai-gateway/select", { profile });
export const repositoriesApi = () =>
  request<RepositoryStatusBundle>("/api/v1/system/repositories");
export const policiesApi = () => request<PolicyVersion[]>("/api/v1/policies");
export async function bundleApi(id: string): Promise<TaskBundle> {
  const p = `/api/v1/onboarding/tasks/${id}`;
  const optional = async <T>(s: string, f: T) => {
    try {
      return await request<T>(p + s);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return f;
      throw e;
    }
  };
  const [
    task,
    report,
    specs,
    submissions,
    evidence,
    validation,
    timeline,
    manualRuns,
    reviews,
    repairs,
    failures,
  ] = await Promise.all([
    request<Task>(p),
    optional<TaskBundle["report"]>("/reports/latest", null),
    request<TaskBundle["specs"]>(p + "/specs"),
    request<TaskBundle["submissions"]>(p + "/submissions"),
    request<TaskBundle["evidence"]>(p + "/evidence"),
    optional<TaskBundle["validation"]>("/validation", null),
    request<TaskBundle["timeline"]>(p + "/timeline"),
    request<TaskBundle["manualRuns"]>(p + "/manual-runs"),
    request<TaskBundle["reviews"]>(p + "/reviews"),
    request<TaskBundle["repairs"]>(p + "/repairs"),
    request<TaskBundle["failures"]>(p + "/failures"),
  ]);
  return {
    task,
    report,
    specs,
    submissions,
    evidence,
    validation,
    timeline,
    manualRuns,
    reviews,
    repairs,
    failures,
    samples: { manual_run: null, count: 0, samples: [] },
    logs: [],
  };
}
export interface CollectedRecord {
  task_id: string;
  source_name: string;
  source_id?: string;
  title?: string;
  description?: string;
  requirements?: string;
  source_url?: string;
  publish_time?: unknown;
  raw_content: Record<string, unknown>;
  raw_html?: string;
  error_msg?: string;
  crawl_status?: number;
  http_status?: number;
  manual_run_id: string;
  code_revision: string;
  sample_index: number;
  review_status: string;
  review: {
    status?: string;
    note?: string;
    field?: string;
    at?: string;
    reviewer?: string;
  };
}
export interface RecordRun {
  manual_run_id: string;
  task_id: string;
  code_revision: string;
  status: string;
  created_at: string;
  error_msg?: string;
  quality?: {
    status: string; pagination_status: string;
    metrics: { record_count: number; error_count: number; warning_count: number; missing_publish_time: number };
    findings: { code: string; severity: string; field: string; sample_indices: number[]; message: string }[];
  };
  record_count?: number;
  empty_conclusion?: { observation_code: string; comment: string };
}
export interface RecordPage {
  summary?: { success: number; failed: number; missing_publish_time: number };
  records: CollectedRecord[];
  total: number;
  page: number;
  page_size: number;
  runs: RecordRun[];
  task_counts?: Record<string, { count: number; date: string }>;
}
export const recordsApi = (params: URLSearchParams) =>
  request<RecordPage>("/api/v1/admin/records?" + params);
export interface CodeView {
  validation_status?: string;
  commit_sha: string;
  files: string[];
  path: string | null;
  content: string;
}
export const codeApi = (task: string, submission: string, path?: string) =>
  request<CodeView>(
    `/api/v1/admin/tasks/${task}/code/${submission}` +
      (path ? "?path=" + encodeURIComponent(path) : ""),
  );
export const requestId = () => crypto.randomUUID();
