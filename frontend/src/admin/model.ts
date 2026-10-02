import type { Task, GlobalSubmission } from "../types";
export const statusLabels: Record<string, string> = {
  NO_DATA_CONFIRMED: "空结果已确认",
  SUBMITTED: "排队中",
  ANALYZING: "AI 接入中",
  WAITING_MANUAL_RUN: "待采集",
  WAITING_MANUAL_REVIEW: "待审查",
  REPAIRING: "AI 修复中",
  EDITING: "保存代码中",
  ADOPTED: "已采纳",
  BLOCKED: "需处理",
  PAUSED: "已暂停",
  PAUSE_REQUESTED: "正在暂停",
  CANCEL_REQUESTED: "正在停止",
  CANCELLED: "已停止",
  FAILED: "执行失败",
  TIMED_OUT: "执行超时",
  INTERRUPTED: "执行中断",
  REJECTED: "未采纳",
  PASS: "通过",
  ISSUE: "有问题",
  UNCHECKED: "未审查",
  QUEUED: "采集排队中",
  RUNNING: "采集中",
  WAITING_REVIEW: "待审查",
  COMPLETED: "已完成",
  REVIEWED: "已审查",
  PARTIAL: "部分通过",
  NOT_RUN: "未执行",
  FAIL: "失败",
  REPAIRED: "已修复",
  CREATED: "待诊断",
  PENDING: "排队等待",
  DIAGNOSING: "正在诊断",
  RESOLVED: "已解决",
  DISABLED: "未启用",
  PUSHED: "已推送",
  ready: "正常",
  healthy: "正常",
  degraded: "需要处理",
  draft: "草稿",
  published: "已发布",
  retired: "已停用",
  INFO: "信息",
  WARNING: "警告",
  ERROR: "错误",
};
export const observationLabels: Record<string, string> = {
  INTERNSHIPS_FOUND: "发现实习岗位",
  NO_INTERNSHIPS_OBSERVED: "验证范围内未发现实习",
  NO_JOBS_OBSERVED: "验证范围内暂无岗位",
  NO_JOB_LIST_FOUND: "未找到岗位列表",
  ACCESS_RESTRICTED: "访问受限",
  INCONCLUSIVE: "证据不足",
};
export const stageLabels: Record<string, string> = {
  normalize_input: "规范化入口",
  inspect_site: "分析页面与接口",
  build_spec: "建立 PlatformSpec",
  generate_code: "生成采集代码",
  run_validation: "自动验证",
  build_report: "生成接入汇报",
  report_gate: "检查接入条件",
  commit_candidate: "生成候选提交",
  await_manual_run: "等待人工采集",
  record_review: "人工审查",
  build_failure_bundle: "整理问题与证据",
  diagnose_failure: "诊断问题",
  patch_code: "修复代码",
  run_regression: "回归验证",
  submit_fix: "生成修复提交",
  openhands: "AI 工作",
  queue: "排队",
  manual_collection: "人工采集",
};
export const label = (v?: string | null) =>
  v ? statusLabels[v] || observationLabels[v] || v : "—";
export function tone(
  v?: string | null,
): "green" | "red" | "amber" | "blue" | "gray" {
  if (
    [
      "ADOPTED",
      "PASS",
      "COMPLETED",
      "REVIEWED",
      "REPAIRED",
      "RESOLVED",
      "ready",
      "healthy",
    ].includes(v || "")
  )
    return "green";
  if (
    [
      "ISSUE",
      "FAILED",
      "FAIL",
      "REJECTED",
      "TIMED_OUT",
      "ERROR",
      "degraded",
    ].includes(v || "")
  )
    return "red";
  if (
    [
      "WAITING_MANUAL_RUN",
      "WAITING_MANUAL_REVIEW",
      "WAITING_REVIEW",
      "BLOCKED",
      "PARTIAL",
      "PAUSED",
      "WARNING",
    ].includes(v || "")
  )
    return "amber";
  if (
    ["ANALYZING", "REPAIRING", "RUNNING", "QUEUED", "SUBMITTED"].includes(
      v || "",
    )
  )
    return "blue";
  return "gray";
}
export function dateText(value: unknown, seconds = false, year = false) {
  if (value == null || value === "" || value === 0 || value === "0")
    return "未提供";
  let v: string | number =
    typeof value === "number"
      ? value < 1e12
        ? value * 1000
        : value
      : String(value);
  if (typeof v === "string" && /^\d{10,13}$/.test(v))
    v = Number(v) * (v.length === 10 ? 1000 : 1);
  if (
    typeof v === "string" &&
    /^\d{4}-\d\d-\d\dT/.test(v) &&
    !/(Z|[+-]\d\d:\d\d)$/.test(v)
  )
    v += "Z";
  const d = new Date(v);
  return Number.isNaN(d.getTime())
    ? String(value)
    : d.toLocaleString("zh-CN", {
        month: "2-digit",
        ...(year ? { year: "numeric" as const } : {}),
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        ...(seconds ? { second: "2-digit" } : {}),
      });
}
export function text(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join("\n");
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  const doc = document.createElement("template");
  doc.innerHTML = String(value)
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<\/(p|div|li|h[1-6])>/gi, "\n");
  doc.content
    .querySelectorAll("script,style,iframe,object,embed")
    .forEach((n) => n.remove());
  return (doc.content.textContent || "").replace(/\u00a0/g, " ").trim();
}
export function safeUrl(url?: string) {
  try {
    const v = new URL(url || "");
    return ["https:", "http:"].includes(v.protocol) ? v.href : undefined;
  } catch {
    return undefined;
  }
}
export const companyName = (t: Task) =>
  t.platform_name?.trim() || t.platform_key;
export function entryName(t: Task) {
  const url = new URL(t.entry_url);
  const p = url.pathname + url.hash;
  if (/intern/i.test(p)) return "实习招聘";
  if (/campus|graduate/i.test(p)) return "校园招聘";
  if (/social|experienced/i.test(p)) return "社会招聘";
  return p === "/" ? "招聘官网" : `招聘入口 · ${p.replace(/\/$/, "")}`;
}
export interface Company {
  name: string;
  entries: Task[];
}
export function groupCompanies(tasks: Task[]): Company[] {
  const entries = new Map<string, Task>();
  [...tasks]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .forEach((t) => {
      const k = companyName(t) + "|" + t.normalized_url;
      if (!entries.has(k)) entries.set(k, t);
    });
  const companies = new Map<string, Task[]>();
  for (const t of entries.values())
    companies.set(companyName(t), [
      ...(companies.get(companyName(t)) || []),
      t,
    ]);
  return [...companies].map(([name, entries]) => ({ name, entries }));
}
export function defaultSubmission(rows: GlobalSubmission[], id: string) {
  const r = rows.filter(
    (s) => s.task_id === id && s.commit_sha && !s.simulated,
  );
  return (
    r.find((s) => s.adoption_status === "adopted") ||
    r.find((s) => s.adoption_status === "candidate") ||
    r[0]
  );
}
export function contextLink(
  path: string,
  task?: string,
  extra?: Record<string, string | undefined>,
) {
  const p = new URLSearchParams();
  if (task) p.set("task", task);
  Object.entries(extra || {}).forEach(([k, v]) => {
    if (v) p.set(k, v);
  });
  return path + (p.size ? "?" + p : "");
}
