import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  DownloadOutlined,
  FileSearchOutlined,
  FilterOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { App as AntApp, Button, Input, Modal, Select, Tag, Tooltip } from "antd";
import { createBatch, evidenceDownloadUrl, getSubmissionDiff, getTaskBundle, isLiveApi, listTasks, registerManualRun, submitReview } from "./api";
import { mockBundle } from "./mock";
import type { Submission, SubmissionDiff, Task, TaskBundle, TaskStatus } from "./types";

const statusMeta: Record<TaskStatus, { label: string; tone: string }> = {
  SUBMITTED: { label: "已提交", tone: "neutral" },
  ANALYZING: { label: "分析中", tone: "amber" },
  WAITING_MANUAL_RUN: { label: "待人工运行", tone: "blue" },
  WAITING_MANUAL_REVIEW: { label: "待人工审查", tone: "amber" },
  REPAIRING: { label: "AI 修复中", tone: "violet" },
  ADOPTED: { label: "已采纳", tone: "green" },
  BLOCKED: { label: "已阻断", tone: "red" },
};

const observationMeta: Record<string, { label: string; detail: string }> = {
  INTERNSHIPS_FOUND: { label: "已发现实习岗位", detail: "检查范围内发现了符合实习筛选规则的岗位。" },
  NO_INTERNSHIPS_OBSERVED: { label: "暂未观察到实习岗位", detail: "已验证范围内没有匹配项，不能据此推断公司永久没有实习岗位。" },
  NO_JOBS_OBSERVED: { label: "暂未观察到岗位", detail: "入口可访问，但当前验证范围没有返回岗位记录。" },
  NO_JOB_LIST_FOUND: { label: "未找到岗位列表", detail: "入口已检查，但没有确认到可采集的岗位列表接口或页面。" },
  NON_LISTING_RECRUITMENT: { label: "非岗位列表招聘入口", detail: "当前入口更像招聘说明或投递表单，暂不具备列表采集条件。" },
  DETAIL_UNAVAILABLE: { label: "岗位详情不可用", detail: "列表存在，但详情页或详情接口无法取得完整正文。" },
  ACCESS_RESTRICTED: { label: "访问受限", detail: "入口或接口受到访问限制，需要人工补充证据或凭证。" },
  INCONCLUSIVE: { label: "证据不足", detail: "现有证据不足以判断是否可采集。" },
};

function observationCopy(code?: string | null) {
  return observationMeta[code ?? ""] ?? { label: code ?? "等待分析", detail: "系统正在收集入口和接口证据。" };
}

function StatusPill({ status }: { status: TaskStatus }) {
  const meta = statusMeta[status];
  return <span className={`status-pill ${meta.tone}`}><i />{meta.label}</span>;
}

function RepositoryMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`repo-mark ${compact ? "compact" : ""}`}>
      <span className="repo-dot" />
      {!compact && <><span className="repo-name">FIELDWORK</span><span className="repo-caption">AI 招聘采集控制台</span></>}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <RepositoryMark />
        <div className="sidebar-section-label">工作台</div>
        <nav className="nav-list">
          <NavLink to="/tasks" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><InboxOutlined />任务</NavLink>
          <NavLink to="/tasks/new" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><PlusOutlined />新建接入</NavLink>
          <NavLink to="/repositories" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><CodeOutlined />代码候选</NavLink>
        </nav>
        <div className="sidebar-section-label">系统</div>
        <nav className="nav-list">
          <NavLink to="/policies" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><SettingOutlined />规则与工具</NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="local-only"><span />本地控制面</div>
          <div className="sidebar-version">v0.1 · local</div>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div className="crumb">AI ONBOARDING <span>/</span> OPERATIONS</div>
          <div className="topbar-actions"><span className="live-dot" /> Agent Server online <span className="topbar-divider" /> <span className="topbar-date">29 SEP 2026</span></div>
        </header>
        <div className="page-content">{children}</div>
      </main>
    </div>
  );
}

function TaskListPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const navigate = useNavigate();
  useEffect(() => { listTasks().then(setTasks); }, []);
  const visible = useMemo(() => tasks.filter((task) => {
    const matchesText = `${task.platform_name ?? ""} ${task.platform_key} ${task.entry_url}`.toLowerCase().includes(query.toLowerCase());
    return matchesText && (filter === "all" || task.status === filter);
  }), [tasks, query, filter]);
  const counts = useMemo(() => ({ total: tasks.length, waiting: tasks.filter((t) => t.status === "WAITING_MANUAL_RUN").length, review: tasks.filter((t) => t.status === "WAITING_MANUAL_REVIEW").length, adopted: tasks.filter((t) => t.status === "ADOPTED").length }), [tasks]);
  return (
    <div className="page-enter">
      <div className="page-heading">
        <div><div className="eyebrow">OPERATIONS / TASK QUEUE</div><h1>接入任务</h1><p>从招聘入口到人工采纳，所有轮次在这里汇合。</p></div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate("/tasks/new")}>新建接入</Button>
      </div>
      <div className="metric-row">
        <Metric label="全部任务" value={counts.total} suffix="项" accent="ink" />
        <Metric label="待人工运行" value={counts.waiting} suffix="项" accent="amber" />
        <Metric label="待人工审查" value={counts.review} suffix="项" accent="blue" />
        <Metric label="已采纳" value={counts.adopted} suffix="项" accent="green" />
      </div>
      <div className="toolbar">
        <Input prefix={<FileSearchOutlined />} placeholder="搜索公司、平台键或招聘入口" value={query} onChange={(event) => setQuery(event.target.value)} allowClear />
        <Select value={filter} onChange={setFilter} suffixIcon={<FilterOutlined />} options={[{ value: "all", label: "全部状态" }, ...Object.entries(statusMeta).map(([value, meta]) => ({ value, label: meta.label }))]} />
        <Button icon={<ReloadOutlined />} onClick={() => listTasks().then(setTasks)}>刷新</Button>
      </div>
      <div className="task-table-head"><span>任务 / 入口</span><span>当前阶段</span><span>观察结论</span><span>仓库来源</span><span>最后更新</span></div>
      <div className="task-list">
        {visible.map((task) => <TaskRow key={task.task_id} task={task} onClick={() => navigate(`/tasks/${task.task_id}`)} />)}
        {!visible.length && <div className="empty-state"><InboxOutlined /><strong>没有匹配的任务</strong><span>调整搜索或状态筛选试试。</span></div>}
      </div>
    </div>
  );
}

function Metric({ label, value, suffix, accent }: { label: string; value: number; suffix: string; accent: string }) {
  return <div className={`metric-card ${accent}`}><span>{label}</span><strong>{value}<small>{suffix}</small></strong><i /></div>;
}

function TaskRow({ task, onClick }: { task: Task; onClick: () => void }) {
  const observation = task.status === "ANALYZING" ? "等待协议分析" : task.task_id.includes("kuaishou") ? "已发现实习岗位" : "待人工确认";
  return <button className="task-row" onClick={onClick}>
    <div className="task-primary"><span className="company-avatar">{(task.platform_name ?? task.platform_key).slice(0, 1)}</span><div><strong>{task.platform_name ?? task.platform_key}</strong><span>{task.entry_url}</span></div></div>
    <div><StatusPill status={task.status} /><small className="next-action">{task.next_action ?? "—"}</small></div>
    <div className="observation"><span className={task.status === "ADOPTED" ? "green-dot" : "amber-dot"} />{observation}</div>
    <div className="repo-cell"><span className="mini-repo-dot" />{task.repository_key}<small>{task.platform_key}</small></div>
    <time>{formatRelative(task.updated_at)}</time><ArrowRightOutlined className="row-arrow" />
  </button>;
}

function formatRelative(value: string) { const date = new Date(value); if (Number.isNaN(date.valueOf())) return "刚刚"; const minutes = Math.max(1, Math.round((Date.now() - date.valueOf()) / 60000)); return minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前`; }

function NewTaskPage() {
  const navigate = useNavigate();
  const { message } = AntApp.useApp();
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [platformKey, setPlatformKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  async function handleSubmit(event: React.FormEvent) { event.preventDefault(); if (!url.trim()) return; setSubmitting(true); try { const result = await createBatch({ items: [{ entry_url: url.trim(), platform_name: name.trim() || undefined, platform_key: platformKey.trim() || undefined, repository_key: "aicoding-auto_spider" }], analysis_profile: "internship-http-v1", dry_run: true, client_request_id: `frontend-${Date.now()}` }); message.success(`任务已创建：${result.task_ids[0]}`); navigate(`/tasks/${result.task_ids[0]}`); } catch { message.error("任务创建失败，请检查 API 服务"); } finally { setSubmitting(false); } }
  return <div className="page-enter narrow-page"><button className="back-link" onClick={() => navigate("/tasks")}><ArrowLeftOutlined />返回任务</button><div className="page-heading"><div><div className="eyebrow">NEW INTAKE / 01</div><h1>新建接入</h1><p>提交招聘入口，系统会生成分析、规范和候选代码。</p></div></div><form className="intake-form" onSubmit={handleSubmit}><label>招聘入口 URL <span>必填</span><Input size="large" placeholder="https://careers.example.com/internships" value={url} onChange={(e) => setUrl(e.target.value)} /></label><div className="form-grid"><label>公司名称 <span>可选</span><Input size="large" placeholder="例如：快手" value={name} onChange={(e) => setName(e.target.value)} /></label><label>平台键 <span>可选</span><Input size="large" placeholder="例如：kuaishou" value={platformKey} onChange={(e) => setPlatformKey(e.target.value)} /></label></div><div className="repo-selection"><div className="repo-selection-title">代码边界</div><div className="repo-line"><span className="repo-line-dot source" /><div><strong>fun-crawler-v2</strong><small>只读实验源 · 基线 16cba84</small></div><Tag>SOURCE</Tag></div><div className="repo-line"><span className="repo-line-dot output" /><div><strong>aicoding-auto_spider</strong><small>AI 候选产出 · 自动推送分支</small></div><Tag color="orange">AI OUTPUT</Tag></div></div><Button type="primary" htmlType="submit" size="large" loading={submitting} icon={<SendOutlined />}>开始分析</Button></form></div>;
}

function TaskDetailPage() {
  const { taskId = "task-real-kuaishou-001" } = useParams();
  const [bundle, setBundle] = useState<TaskBundle>(mockBundle);
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => getTaskBundle(taskId).then(setBundle).catch(() => setError("任务详情暂时无法加载")).finally(() => setLoading(false));
  useEffect(() => { setLoading(true); setError(null); refresh(); }, [taskId]);
  const { task, submissions } = bundle;
  return <div className="page-enter"><Link to="/tasks" className="back-link"><ArrowLeftOutlined />任务列表</Link>{error && <div className="inline-error">{error} · <button onClick={refresh}>重试</button></div>}<div className="detail-heading"><div className="detail-title"><span className="company-avatar large">{(task.platform_name ?? task.platform_key).slice(0, 1)}</span><div><div className="eyebrow">ONBOARDING TASK / {task.task_id.slice(-8)}</div><h1>{task.platform_name ?? task.platform_key}</h1><a href={task.entry_url} target="_blank" rel="noreferrer">{task.entry_url}<ArrowRightOutlined /></a></div></div><StatusPill status={task.status} /></div><RepositoryStrip submission={submissions[0]} /><div className="detail-tabs">{[["overview", "总览"], ["report", "接入汇报"], ["candidate", "候选代码"], ["manual", "人工运行"], ["review", "审查与修复"]].map(([key, label]) => <button key={key} className={tab === key ? "selected" : ""} onClick={() => setTab(key)}>{label}</button>)}</div>{loading && isLiveApi() ? <div className="panel loading-panel">正在读取任务历史…</div> : <>{tab === "overview" && <Overview bundle={bundle} setTab={setTab} />}{tab === "report" && <ReportPanel bundle={bundle} />}{tab === "candidate" && <CandidatePanel taskId={task.task_id} submissions={submissions} />}{tab === "manual" && <ManualPanel bundle={bundle} onRefresh={refresh} />}{tab === "review" && <ReviewPanel bundle={bundle} onRefresh={refresh} />}</>}</div>;
}

function RepositoryStrip({ submission }: { submission?: Submission }) { return <div className="repository-strip"><div><span className="strip-label">SOURCE REPOSITORY</span><strong>fun-crawler-v2</strong><small>基线 16cba84 · 只读</small></div><div className="strip-arrow">→</div><div><span className="strip-label orange">AI OUTPUT REPOSITORY</span><strong>aicoding-auto_spider</strong><small>{submission?.branch_name ?? "等待候选分支"}</small></div>{submission?.commit_sha && <code>{submission.commit_sha.slice(0, 8)}</code>}</div>; }

function Overview({ bundle, setTab }: { bundle: TaskBundle; setTab: (tab: string) => void }) { const report = bundle.report; const observation = observationCopy(report?.observation_code); return <><div className="overview-grid"><div className="panel timeline-panel"><PanelTitle eyebrow="RUN TIMELINE" title="接入进度" /><Timeline task={bundle.task} timeline={bundle.timeline} /></div><div className="panel report-glance"><PanelTitle eyebrow="LATEST REPORT" title="当前判断" /><div className="glance-code"><span>OBSERVATION</span><strong>{report?.observation_code ?? "ANALYSIS_PENDING"}</strong></div><p>{report ? observation.detail : "系统正在收集入口和接口证据。"}</p><button className="text-button" onClick={() => setTab("report")}>查看完整汇报 <ArrowRightOutlined /></button></div></div><div className="panel next-panel"><div><span className="eyebrow">NEXT ACTION</span><h2>{bundle.task.status === "WAITING_MANUAL_RUN" ? "运行候选采集器" : bundle.task.status === "BLOCKED" ? "查看阻断原因" : "等待系统完成分析"}</h2><p>{bundle.task.status === "BLOCKED" ? observation.detail : "人工运行会将真实样本带回审查台，完成后再决定是否采纳。"}</p></div><Button type="primary" onClick={() => setTab(bundle.task.status === "BLOCKED" ? "report" : "manual")} icon={<SendOutlined />}>{bundle.task.status === "BLOCKED" ? "查看接入汇报" : "进入人工运行"}</Button></div></>; }

function Timeline({ task, timeline }: { task: Task; timeline: TaskBundle["timeline"] }) { const stages = [{ label: "入口接入", done: true }, { label: "协议分析", done: ["ANALYZING", "WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW", "ADOPTED"].includes(task.status) }, { label: "PlatformSpec", done: true }, { label: "候选生成", done: true }, { label: "自动验证", done: true }, { label: "人工运行", done: ["WAITING_MANUAL_REVIEW", "ADOPTED"].includes(task.status) }, { label: "人工审查", done: task.status === "ADOPTED" }]; return <><div className="timeline">{stages.map((stage, index) => <div className={`timeline-step ${stage.done ? "done" : index === stages.findIndex((s) => !s.done) ? "current" : ""}`} key={stage.label}><span className="timeline-marker">{stage.done ? <CheckCircleOutlined /> : index + 1}</span><span>{stage.label}</span>{index < stages.length - 1 && <i />}</div>)}</div>{timeline.events.length > 0 && <div className="timeline-events"><span className="eyebrow">EVENTS</span>{timeline.events.slice(-4).reverse().map((event) => <div key={event.event_id}><b>{event.event_type}</b><time>{formatRelative(event.occurred_at)}</time></div>)}</div>}</>; }

function PanelTitle({ eyebrow, title }: { eyebrow: string; title: string }) { return <div className="panel-title"><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>; }

function ReportPanel({ bundle }: { bundle: TaskBundle }) { const { report, evidence, specs } = bundle; const validation = report?.report_json.validation; const observation = observationCopy(report?.observation_code); const listCount = report?.report_json.list_count; const internshipCount = report?.report_json.internship_count; return <div className="panel-stack"><div className="panel report-header"><div><span className="eyebrow">OBSERVATION</span><h2>{report?.observation_code ?? "尚未生成"}</h2><p>{report ? `${observation.label}。${listCount != null ? ` 共观察 ${listCount} 条列表记录${internshipCount != null ? `，其中 ${internshipCount} 条符合实习筛选` : ""}。` : ""}` : "报告尚未生成。"}</p><p className="report-detail">{observation.detail}</p></div><Tag color={report?.technical_status === "PASS" ? "green" : "orange"}>{report?.technical_status ?? "NOT_RUN"}</Tag></div><div className="panel"><PanelTitle eyebrow="VALIDATION GATE" title="确定性验证" /><div className="validation-grid">{Object.entries(validation ?? {}).map(([key, value]) => <div className="validation-cell" key={key}><span>{key.replace("_status", "").toUpperCase()}</span><strong className={value === "PASS" ? "pass" : "pending"}>{String(value)}</strong></div>)}</div></div><div className="panel unresolved"><PanelTitle eyebrow="SCOPE & EVIDENCE" title="证据范围" /><div className="evidence-line"><FileSearchOutlined /><span>PlatformSpec、候选验证和修复回归证据已归档</span><code>{report?.report_ref ?? "—"}</code></div><div className="evidence-list">{evidence.length ? evidence.map((item) => <div key={String(item.evidence_id)}><span>{String(item.file_type ?? "evidence")}</span><code>{String(item.evidence_id)}</code>{isLiveApi() && <a href={evidenceDownloadUrl(String(item.evidence_id))} target="_blank" rel="noreferrer" title="下载证据"><DownloadOutlined /></a>}</div>) : <span className="muted">暂无证据索引</span>}</div><div className="spec-footnote">当前 PlatformSpec：{specs[0] ? `v${specs[0].spec_version} · ${specs[0].status}` : "尚未生成"}</div></div></div>; }

function CandidatePanel({ taskId, submissions }: { taskId: string; submissions: Submission[] }) { const [selected, setSelected] = useState<SubmissionDiff | null>(null); const [loading, setLoading] = useState(false); async function showDiff(submission: Submission) { setLoading(true); try { setSelected(await getSubmissionDiff(taskId, submission.submission_id)); } finally { setLoading(false); } } return <div className="panel-stack">{submissions.length === 0 && <div className="panel empty-state"><CodeOutlined /><strong>还没有候选提交</strong><span>自动验证通过后，候选分支会出现在这里。</span></div>}{submissions.map((submission) => <div className="panel candidate-card" key={submission.submission_id}><div className="candidate-top"><div><span className="eyebrow">{submission.adoption_status.toUpperCase()}</span><h2>{submission.branch_name}</h2><code>{submission.commit_sha ?? "commit pending"}</code></div><Tag color={submission.adoption_status === "adopted" ? "green" : "orange"}>{submission.adoption_status}</Tag></div><div className="file-list">{submission.changed_files.map((file) => <div key={file}><CodeOutlined />{file}</div>)}</div><div className="candidate-actions"><Button icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(submission.commit_sha ?? "")}>复制 commit</Button><Button type="primary" icon={<CodeOutlined />} loading={loading} onClick={() => showDiff(submission)}>查看 diff</Button></div></div>)}<Modal width={960} open={Boolean(selected)} title="候选提交 diff" onCancel={() => setSelected(null)} footer={null}>{selected && <><div className="diff-meta"><span>{selected.baseline_ref?.slice(0, 8)} → {selected.commit_sha?.slice(0, 8)}</span>{selected.truncated && <Tag color="orange">内容已截断</Tag>}</div>{selected.available ? <pre className="diff-view">{selected.diff || "没有可显示的差异"}</pre> : <div className="empty-state"><strong>diff 暂不可用</strong><span>{selected.reason}</span></div>}</>}</Modal></div>; }

function ManualPanel({ bundle, onRefresh }: { bundle: TaskBundle; onRefresh: () => void }) {
  const { message } = AntApp.useApp();
  const { task, submissions, manualRuns } = bundle;
  const [running, setRunning] = useState(false);
  const defaultRevision = submissions[0]?.commit_sha ?? "";
  const [revision, setRevision] = useState(defaultRevision);
  const [sampleJson, setSampleJson] = useState("[]");
  useEffect(() => { if (!revision && defaultRevision) setRevision(defaultRevision); }, [defaultRevision, revision]);
  const command = `python -m crawler --platform ${task.platform_key} --max-pages 1 --page-size 2`;
  async function submit() {
    if (!revision.trim()) { message.warning("请先填写候选代码版本"); return; }
    let samples: unknown[];
    try {
      const parsed = JSON.parse(sampleJson || "[]");
      if (!Array.isArray(parsed)) throw new Error("samples must be an array");
      samples = parsed;
    } catch {
      message.error("样本 JSON 必须是数组");
      return;
    }
    setRunning(true);
    try {
      await registerManualRun(task.task_id, {
        code_revision: revision.trim(),
        environment_fingerprint: "frontend-preview",
        started_at: new Date(Date.now() - 120000).toISOString(),
        finished_at: new Date().toISOString(),
        artifact_manifest_ref: "manual-run.json",
        client_request_id: `frontend-manual-${Date.now()}`,
        result: { sample_count: samples.length, samples, source: "console" },
      });
      message.success("人工运行结果已登记，任务进入审查阶段");
      onRefresh();
    } catch { message.error("登记失败，请检查后端服务"); } finally { setRunning(false); }
  }
  return <div className="panel-stack"><div className="panel manual-hero"><div><span className="eyebrow">MANUAL RUN / PREVIEW</span><h2>运行候选采集器</h2><p>正式采集仍由人工命令控制。平台登记运行结果，并把样本带入审查。</p></div><div className="manual-status"><ClockCircleOutlined /><span>{manualRuns[0]?.status ?? "等待运行"}</span></div></div><div className="panel manual-form"><label>代码版本<Input value={revision} onChange={(event) => setRevision(event.target.value)} placeholder="候选 commit SHA" /></label><label>推荐命令<div className="command-box"><code>{command}</code><Tooltip title="复制命令"><Button type="text" icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(command)} /></Tooltip></div></label><label>采集样本（可选 JSON 数组）<Input.TextArea rows={6} value={sampleJson} onChange={(event) => setSampleJson(event.target.value)} placeholder='例如：[{"source_id":"123","title":"实习岗位"}]' /></label><Button type="primary" loading={running} onClick={submit}>登记人工运行结果</Button></div><div className="panel history-panel"><PanelTitle eyebrow="RUN HISTORY" title="人工运行记录" />{manualRuns.length ? manualRuns.map((run) => <div className="history-row" key={run.manual_run_id}><div><strong>{run.code_revision.slice(0, 12)}</strong><span>{run.command_profile} · {run.environment_fingerprint} · {String(run.result?.sample_count ?? 0)} 条样本</span></div><Tag color={run.status === "WAITING_REVIEW" ? "orange" : "green"}>{run.status}</Tag></div>) : <span className="muted">暂无人工运行记录</span>}</div></div>;
}

function sampleRecords(run?: TaskBundle["manualRuns"][number]) {
  const raw = run?.result?.samples;
  return Array.isArray(raw) ? raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function ReviewPanel({ bundle, onRefresh }: { bundle: TaskBundle; onRefresh: () => void }) { const { message } = AntApp.useApp(); const { task, manualRuns, reviews, repairs } = bundle; const [open, setOpen] = useState(false); const latestRun = manualRuns[0]; const samples = sampleRecords(latestRun); async function review(status: "PASS" | "CODE_FIX_REQUIRED") { if (!latestRun) { message.warning("请先登记一次人工运行结果"); return; } try { await submitReview(task.task_id, { review_status: status, manual_run_id: latestRun.manual_run_id, code_revision: latestRun.code_revision, sample_count: Number(latestRun.result?.sample_count ?? samples.length), issue_summary: status === "PASS" ? null : "请补充字段回归", evidence_refs: [], client_request_id: `frontend-review-${Date.now()}` }); message.success(status === "PASS" ? "已提交通过审查" : "已生成修复请求"); setOpen(false); onRefresh(); } catch { message.error("提交审查失败，请检查人工运行记录"); } } return <div className="panel-stack"><div className="panel review-hero"><div><span className="eyebrow">HUMAN REVIEW</span><h2>样本审查工作台</h2><p>逐字段核对标题、来源 URL、实习类型、正文和发布时间。</p></div><Button type="primary" disabled={!latestRun || samples.length === 0} onClick={() => setOpen(true)}>提交审查结果</Button></div><div className="panel review-context"><span>当前运行</span><strong>{latestRun ? latestRun.manual_run_id : "尚未登记"}</strong><small>{latestRun ? `${latestRun.code_revision.slice(0, 12)} · ${latestRun.result?.sample_count ?? samples.length} 条样本` : "先在人工运行页登记结果"}</small></div><div className="panel sample-table"><div className="sample-head"><span>编号</span><span>标题 / 地点</span><span>正文 / 要求</span><span>审查状态</span></div>{samples.length ? samples.map((sample, index) => <div className="sample-row" key={`${String(sample.source_id ?? index)}-${index}`}><span className="sample-id">{String(sample.source_id ?? `#${String(index + 1).padStart(2, "0")}`)}</span><strong>{String(sample.title ?? sample.position ?? "未提供标题")}<small>{String(sample.location ?? "未提供地点")}</small></strong><span>{String(sample.description ?? sample.requirements ?? "未提供正文")}</span><Tag color="green">可审查</Tag></div>) : <div className="sample-empty">{latestRun ? "人工运行结果没有包含样本明细，请重新登记样本 JSON。" : "先在人工运行页登记结果，样本会显示在这里。"}</div>}</div>{reviews.length > 0 && <div className="panel history-panel"><PanelTitle eyebrow="REVIEWS" title="审查记录" />{reviews.map((item) => <div className="history-row" key={item.review_id}><div><strong>{item.review_status}</strong><span>{item.sample_count} 条样本 · {item.reviewer_id}</span></div><Tag color={item.review_status === "PASS" ? "green" : "orange"}>{item.review_status}</Tag></div>)}</div>}{repairs.length > 0 && <div className="panel history-panel"><PanelTitle eyebrow="REPAIR RUNS" title="AI 修复记录" />{repairs.map((item) => <div className="history-row" key={item.repair_run_id}><div><strong>第 {item.attempt} 轮修复</strong><span>{item.changed_files.join("、") || "未记录文件"}</span></div><Tag color={item.status === "COMPLETED" ? "green" : "orange"}>{item.status}</Tag></div>)}</div>}<Modal open={open} title="提交人工审查" onCancel={() => setOpen(false)} footer={null}><p className="modal-copy">确认当前样本字段、实习筛选和证据均无问题后提交通过。</p><div className="modal-actions"><Button onClick={() => review("CODE_FIX_REQUIRED")}>有问题，进入修复</Button><Button type="primary" onClick={() => review("PASS")}>确认通过</Button></div></Modal></div>; }

function PlaceholderPage({ title, note }: { title: string; note: string }) { return <div className="page-enter empty-page"><div className="eyebrow">COMING NEXT</div><h1>{title}</h1><p>{note}</p></div>; }

function AppRoutes() { return <Routes><Route path="/tasks" element={<TaskListPage />} /><Route path="/tasks/new" element={<NewTaskPage />} /><Route path="/tasks/:taskId" element={<TaskDetailPage />} /><Route path="/repositories" element={<PlaceholderPage title="代码候选" note="候选分支、diff 和远程推送记录将在这里集中管理。" />} /><Route path="/policies" element={<PlaceholderPage title="规则与工具" note="验证器版本、报告规则和工具健康状态将在这里管理。" />} /><Route path="*" element={<TaskListPage />} /></Routes>; }

export default function App() { return <BrowserRouter><AntApp><Shell><AppRoutes /></Shell></AntApp></BrowserRouter>; }

