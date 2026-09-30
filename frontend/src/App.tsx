import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CodeOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FilterOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
  UnorderedListOutlined,
  SendOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { App as AntApp, Button, Checkbox, Input, Modal, Select, Tag, Tooltip } from "antd";
import { configuredRole, createBatch, createPolicy, evidenceDownloadUrl, getBatch, getCurrentActor, getRepositoryStatus, getSubmissionDiff, getSystemHealth, getTaskBundle, isLiveApi, listAllSamples, listAllSubmissions, listBatches, listPolicies, listTaskLogs, listTasks, registerManualRun, resumeTask, submitReview, triggerRepair } from "./api";
import type { BatchDetail, BatchSummary, GlobalSample, GlobalSubmission, PolicyVersion, RepositoryStatusBundle, SampleRecord, Submission, SubmissionDiff, SystemHealth, Task, TaskBundle, TaskStatus } from "./types";

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

function roleAllows(...roles: string[]) {
  const role = configuredRole();
  return role === "admin" || roles.includes(role);
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
  const [actorRole, setActorRole] = useState(configuredRole());
  const [agentStatus, setAgentStatus] = useState("ready");
  useEffect(() => { getCurrentActor().then((actor) => setActorRole(actor.role)).catch(() => undefined); getSystemHealth().then((health) => setAgentStatus(health.checks.agent_server?.status ?? health.status)).catch(() => setAgentStatus("offline")); }, []);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <RepositoryMark />
        <div className="sidebar-section-label">工作台</div>
        <nav className="nav-list">
          <NavLink to="/tasks" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><InboxOutlined />任务</NavLink>
          <NavLink to="/batches" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><UnorderedListOutlined />批次</NavLink>
          <NavLink to="/tasks/new" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><PlusOutlined />新建接入</NavLink>
          <NavLink to="/samples" className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`}><DatabaseOutlined />采集数据</NavLink>
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
          <div className="topbar-actions"><span className={agentStatus === "ready" ? "live-dot" : "offline-dot"} /> Agent Server {agentStatus === "ready" ? "online" : agentStatus === "offline" ? "offline" : "degraded"} <span className="topbar-divider" /> <span className="role-chip">{actorRole}</span><span className="topbar-divider" /> <span className="topbar-date">29 SEP 2026</span></div>
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
  const canCreate = roleAllows("operator");
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [platformKey, setPlatformKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  async function handleSubmit(event: React.FormEvent) { event.preventDefault(); if (!canCreate) { message.warning("当前角色没有创建接入任务的权限"); return; } if (!url.trim()) return; setSubmitting(true); try { const result = await createBatch({ items: [{ entry_url: url.trim(), platform_name: name.trim() || undefined, platform_key: platformKey.trim() || undefined, repository_key: "aicoding-auto_spider" }], analysis_profile: "internship-http-v1", dry_run: false, client_request_id: `frontend-${Date.now()}` }); message.success(`任务已创建：${result.task_ids[0]}`); navigate(`/tasks/${result.task_ids[0]}`); } catch { message.error("任务创建失败，请检查 API 服务"); } finally { setSubmitting(false); } }
  return <div className="page-enter narrow-page"><button className="back-link" onClick={() => navigate("/tasks")}><ArrowLeftOutlined />返回任务</button><div className="page-heading"><div><div className="eyebrow">NEW INTAKE / 01</div><h1>新建接入</h1><p>提交招聘入口，系统会生成分析、规范和候选代码。</p></div></div><form className="intake-form" onSubmit={handleSubmit}><label>招聘入口 URL <span>必填</span><Input size="large" placeholder="https://careers.example.com/internships" value={url} onChange={(e) => setUrl(e.target.value)} /></label><div className="form-grid"><label>公司名称 <span>可选</span><Input size="large" placeholder="例如：快手" value={name} onChange={(e) => setName(e.target.value)} /></label><label>平台键 <span>可选</span><Input size="large" placeholder="例如：kuaishou" value={platformKey} onChange={(e) => setPlatformKey(e.target.value)} /></label></div><div className="repo-selection"><div className="repo-selection-title">代码边界</div><div className="repo-line"><span className="repo-line-dot source" /><div><strong>fun-crawler-v2</strong><small>只读实验源 · 基线 16cba84</small></div><Tag>SOURCE</Tag></div><div className="repo-line"><span className="repo-line-dot output" /><div><strong>aicoding-auto_spider</strong><small>AI 候选产出 · 仅本地提交</small></div><Tag color="orange">AI OUTPUT</Tag></div></div><Button type="primary" htmlType="submit" size="large" loading={submitting} disabled={!canCreate} icon={<SendOutlined />}>{canCreate ? "开始分析" : "当前角色只读"}</Button></form></div>;
}

function TaskDetailPage() {
  const { taskId = "" } = useParams();
  const [bundle, setBundle] = useState<TaskBundle | null>(null);
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = () => getTaskBundle(taskId).then(setBundle).catch(() => setError("任务详情暂时无法加载")).finally(() => setLoading(false));
  useEffect(() => { setLoading(true); setError(null); refresh(); }, [taskId]);
  if (!bundle) return <div className="page-enter"><Link to="/tasks" className="back-link"><ArrowLeftOutlined />任务列表</Link><div className="panel loading-panel">{error ?? "正在读取任务详情…"}</div></div>;
  const { task, submissions } = bundle;
  return <div className="page-enter"><Link to="/tasks" className="back-link"><ArrowLeftOutlined />任务列表</Link>{error && <div className="inline-error">{error} · <button onClick={refresh}>重试</button></div>}<div className="detail-heading"><div className="detail-title"><span className="company-avatar large">{(task.platform_name ?? task.platform_key).slice(0, 1)}</span><div><div className="eyebrow">ONBOARDING TASK / {task.task_id.slice(-8)}</div><h1>{task.platform_name ?? task.platform_key}</h1><a href={task.entry_url} target="_blank" rel="noreferrer">{task.entry_url}<ArrowRightOutlined /></a></div></div><StatusPill status={task.status} /></div><RepositoryStrip submission={submissions[0]} /><div className="detail-tabs">{[["overview", "总览"], ["logs", "AI 日志"], ["report", "接入汇报"], ["samples", "采集数据"], ["candidate", "候选代码"], ["manual", "人工运行"], ["review", "审查与修复"]].map(([key, label]) => <button key={key} className={tab === key ? "selected" : ""} onClick={() => setTab(key)}>{label}</button>)}</div>{loading && isLiveApi() ? <div className="panel loading-panel">正在读取任务历史…</div> : <>{tab === "overview" && <Overview bundle={bundle} setTab={setTab} />}{tab === "logs" && <WorkflowConsole taskId={task.task_id} taskStatus={task.status} initialLogs={bundle.logs} />}{tab === "report" && <ReportPanel bundle={bundle} />}{tab === "samples" && <SamplesPanel samples={bundle.samples} />}{tab === "candidate" && <CandidatePanel taskId={task.task_id} submissions={submissions} />}{tab === "manual" && <ManualPanel bundle={bundle} onRefresh={refresh} />}{tab === "review" && <ReviewPanel bundle={bundle} onRefresh={refresh} />}</>}</div>;
}

function RepositoryStrip({ submission }: { submission?: Submission }) { return <div className="repository-strip"><div><span className="strip-label">SOURCE REPOSITORY</span><strong>fun-crawler-v2</strong><small>基线 16cba84 · 只读</small></div><div className="strip-arrow">→</div><div><span className="strip-label orange">AI OUTPUT REPOSITORY</span><strong>aicoding-auto_spider</strong><small>{submission?.branch_name ?? "等待候选分支"}</small></div>{submission?.commit_sha && <code>{submission.commit_sha.slice(0, 8)}</code>}</div>; }

function WorkflowConsole({ taskId, taskStatus, initialLogs }: { taskId: string; taskStatus: TaskStatus; initialLogs: TaskBundle["logs"] }) {
  const [logs, setLogs] = useState(initialLogs);
  const [after, setAfter] = useState(initialLogs.at(-1)?.sequence ?? 0);
  const terminal = ["ADOPTED", "BLOCKED", "WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW", "REJECTED"].includes(taskStatus);
  useEffect(() => { setLogs(initialLogs); setAfter(initialLogs.at(-1)?.sequence ?? 0); }, [initialLogs]);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const result = await listTaskLogs(taskId, after);
        if (!active || !result.logs.length) return;
        setLogs((current) => [...current, ...result.logs]);
        setAfter(result.next_after);
      } catch { /* transient polling failures are shown on the next tick */ }
    };
    poll();
    if (terminal) return () => { active = false; };
    const timer = window.setInterval(poll, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [taskId, after, terminal]);
  return <div className="panel workflow-console"><div className="console-header"><div><span className="eyebrow">LIVE WORKFLOW LOG</span><h2>AI 工作日志</h2><p>{terminal ? "本轮工作流已结束，日志保留用于复盘。" : "每 2 秒读取新日志，展示当前节点和 Agent 事件。"}</p></div><Tag color={terminal ? "default" : "green"}>{terminal ? "ENDED" : "LIVE"}</Tag></div><div className="console-body">{logs.length ? logs.map((log) => <div className={`console-line ${log.level.toLowerCase()}`} key={log.log_id}><time>{new Date(log.created_at).toLocaleTimeString()}</time><span className="console-stage">{log.stage}</span><span className="console-level">{log.level}</span><span className="console-message">{log.message}</span></div>) : <div className="console-empty">等待工作流日志…</div>}</div></div>;
}

function Overview({ bundle, setTab }: { bundle: TaskBundle; setTab: (tab: string) => void }) { const { message } = AntApp.useApp(); const report = bundle.report; const observation = observationCopy(report?.observation_code); const [actionLoading, setActionLoading] = useState(false); async function retryWorkflow() { setActionLoading(true); try { await resumeTask(bundle.task.task_id); message.success("已重新提交工作流"); } catch { message.error("恢复失败，请检查后端服务"); } finally { setActionLoading(false); } } async function retryRepair() { const failure = bundle.failures[0]; if (!failure) return; setActionLoading(true); try { await triggerRepair(bundle.task.task_id, failure.bundle_id); message.success("已重新触发 AI 修复"); } catch { message.error("修复触发失败，请检查失败包状态"); } finally { setActionLoading(false); } } return <><div className="overview-grid"><div className="panel timeline-panel"><PanelTitle eyebrow="RUN TIMELINE" title="接入进度" /><Timeline task={bundle.task} timeline={bundle.timeline} /></div><div className="panel report-glance"><PanelTitle eyebrow="LATEST REPORT" title="当前判断" /><div className="glance-code"><span>OBSERVATION</span><strong>{report?.observation_code ?? "ANALYSIS_PENDING"}</strong></div><p>{report ? observation.detail : "系统正在收集入口和接口证据。"}</p><button className="text-button" onClick={() => setTab("report")}>查看完整汇报 <ArrowRightOutlined /></button></div></div><div className="panel next-panel"><div><span className="eyebrow">NEXT ACTION</span><h2>{bundle.task.status === "WAITING_MANUAL_RUN" ? "运行候选采集器" : bundle.task.status === "BLOCKED" ? "查看阻断原因" : bundle.task.status === "REPAIRING" ? "等待 AI 修复" : "等待系统完成分析"}</h2><p>{bundle.task.status === "BLOCKED" ? observation.detail : "人工运行会将真实样本带回审查台，完成后再决定是否采纳。"}</p></div><div className="next-actions"><Button type="primary" loading={actionLoading} onClick={() => setTab(bundle.task.status === "BLOCKED" ? "report" : "manual")} icon={<SendOutlined />}>{bundle.task.status === "BLOCKED" ? "查看接入汇报" : "进入人工运行"}</Button>{bundle.failures[0] && <Button loading={actionLoading} onClick={retryRepair}>重新触发修复</Button>}{bundle.task.status === "BLOCKED" && !bundle.failures.length && <Button loading={actionLoading} onClick={retryWorkflow}>重新分析</Button>}</div></div></>; }

function Timeline({ task, timeline }: { task: Task; timeline: TaskBundle["timeline"] }) { const stages = [{ label: "入口接入", done: true }, { label: "协议分析", done: ["ANALYZING", "WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW", "ADOPTED"].includes(task.status) }, { label: "PlatformSpec", done: true }, { label: "候选生成", done: true }, { label: "自动验证", done: true }, { label: "人工运行", done: ["WAITING_MANUAL_REVIEW", "ADOPTED"].includes(task.status) }, { label: "人工审查", done: task.status === "ADOPTED" }]; return <><div className="timeline">{stages.map((stage, index) => <div className={`timeline-step ${stage.done ? "done" : index === stages.findIndex((s) => !s.done) ? "current" : ""}`} key={stage.label}><span className="timeline-marker">{stage.done ? <CheckCircleOutlined /> : index + 1}</span><span>{stage.label}</span>{index < stages.length - 1 && <i />}</div>)}</div>{timeline.events.length > 0 && <div className="timeline-events"><span className="eyebrow">EVENTS</span>{timeline.events.slice(-4).reverse().map((event) => <div key={event.event_id}><b>{event.event_type}</b><time>{formatRelative(event.occurred_at)}</time></div>)}</div>}</>; }

function PanelTitle({ eyebrow, title }: { eyebrow: string; title: string }) { return <div className="panel-title"><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>; }

function ReportPanel({ bundle }: { bundle: TaskBundle }) { const { report, evidence, specs } = bundle; const validation = report?.report_json.validation; const observation = observationCopy(report?.observation_code); const listCount = report?.report_json.list_count; const internshipCount = report?.report_json.internship_count; return <div className="panel-stack"><div className="panel report-header"><div><span className="eyebrow">OBSERVATION</span><h2>{report?.observation_code ?? "尚未生成"}</h2><p>{report ? `${observation.label}。${listCount != null ? ` 共观察 ${listCount} 条列表记录${internshipCount != null ? `，其中 ${internshipCount} 条符合实习筛选` : ""}。` : ""}` : "报告尚未生成。"}</p><p className="report-detail">{observation.detail}</p></div><Tag color={report?.technical_status === "PASS" ? "green" : "orange"}>{report?.technical_status ?? "NOT_RUN"}</Tag></div><div className="panel"><PanelTitle eyebrow="VALIDATION GATE" title="确定性验证" /><div className="validation-grid">{Object.entries(validation ?? {}).map(([key, value]) => <div className="validation-cell" key={key}><span>{key.replace("_status", "").toUpperCase()}</span><strong className={value === "PASS" ? "pass" : "pending"}>{String(value)}</strong></div>)}</div></div><div className="panel unresolved"><PanelTitle eyebrow="SCOPE & EVIDENCE" title="证据范围" /><div className="evidence-line"><FileSearchOutlined /><span>PlatformSpec、候选验证和修复回归证据已归档</span><code>{report?.report_ref ?? "—"}</code></div><div className="evidence-list">{evidence.length ? evidence.map((item) => <div key={String(item.evidence_id)}><span>{String(item.file_type ?? "evidence")}</span><code>{String(item.evidence_id)}</code>{isLiveApi() && <a href={evidenceDownloadUrl(String(item.evidence_id))} target="_blank" rel="noreferrer" title="下载证据"><DownloadOutlined /></a>}</div>) : <span className="muted">暂无证据索引</span>}</div><div className="spec-footnote">当前 PlatformSpec：{specs[0] ? `v${specs[0].spec_version} · ${specs[0].status}` : "尚未生成"}</div></div></div>; }

function SamplesPanel({ samples }: { samples: TaskBundle["samples"] }) {
  const [selected, setSelected] = useState<SampleRecord | null>(null);
  return <div className="panel-stack"><div className="panel sample-summary"><div><span className="eyebrow">COLLECTED DATA</span><h2>{samples.count} 条采集样本</h2><p>{samples.manual_run ? `来自 ${samples.manual_run.manual_run_id} · ${samples.manual_run.code_revision.slice(0, 12)}` : "人工运行后，样本会显示在这里。"}</p></div><Tag color={samples.count ? "green" : "default"}>{samples.manual_run?.status ?? "NO_RUN"}</Tag></div>{samples.samples.length ? <div className="panel sample-table data-table"><div className="sample-head"><span>编号</span><span>标题 / 地点</span><span>来源 URL</span><span>操作</span></div>{samples.samples.map((sample) => <div className="sample-row" key={`${sample.sample_index}-${String(sample.source_id ?? "")}`}><span className="sample-id">{String(sample.source_id ?? `#${String(sample.sample_index).padStart(2, "0")}`)}</span><strong>{sample.title ?? sample.position ?? "未提供标题"}<small>{sample.location ?? "未提供地点"}</small></strong><span className="sample-url">{sample.source_url ?? "未提供来源 URL"}</span><Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(sample)}>查看</Button></div>)}</div> : <div className="panel empty-state"><DatabaseOutlined /><strong>暂无采集样本</strong><span>先在人工运行页登记带有 samples 的结果。</span></div>}<Modal width={760} open={Boolean(selected)} title="岗位样本详情" onCancel={() => setSelected(null)} footer={null}>{selected && <SampleDetail sample={selected} />}</Modal></div>;
}

function SampleDetail({ sample }: { sample: SampleRecord }) {
  const fields: Array<[string, unknown]> = [["标题", sample.title ?? sample.position], ["来源 ID", sample.source_id], ["来源 URL", sample.source_url], ["地点", sample.location], ["部门", sample.department], ["雇佣类型", sample.employment_type], ["发布时间", sample.publish_time], ["职位描述", sample.description], ["任职要求", sample.requirements]];
  return <div className="sample-detail">{fields.map(([label, value]) => <div key={label}><span>{label}</span><p>{value == null || value === "" ? "—" : String(value)}</p></div>)}{Object.keys(sample.extra).length > 0 && <div><span>原始扩展字段</span><pre>{JSON.stringify(sample.extra, null, 2)}</pre></div>}</div>;
}

function CandidatePanel({ taskId, submissions }: { taskId: string; submissions: Submission[] }) { const [selected, setSelected] = useState<SubmissionDiff | null>(null); const [loading, setLoading] = useState(false); async function showDiff(submission: Submission) { setLoading(true); try { setSelected(await getSubmissionDiff(taskId, submission.submission_id)); } finally { setLoading(false); } } return <div className="panel-stack">{submissions.length === 0 && <div className="panel empty-state"><CodeOutlined /><strong>还没有候选提交</strong><span>自动验证通过后，候选分支会出现在这里。</span></div>}{submissions.map((submission) => <div className="panel candidate-card" key={submission.submission_id}><div className="candidate-top"><div><span className="eyebrow">{submission.adoption_status.toUpperCase()}</span><h2>{submission.branch_name}</h2><code>{submission.commit_sha ?? "commit pending"}</code></div><Tag color={submission.adoption_status === "adopted" ? "green" : "orange"}>{submission.adoption_status}</Tag></div><div className="file-list">{submission.changed_files.map((file) => <div key={file}><CodeOutlined />{file}</div>)}</div><div className="candidate-actions"><Button icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(submission.commit_sha ?? "")}>复制 commit</Button><Button type="primary" icon={<CodeOutlined />} loading={loading} onClick={() => showDiff(submission)}>查看 diff</Button></div></div>)}<Modal width={960} open={Boolean(selected)} title="候选提交 diff" onCancel={() => setSelected(null)} footer={null}>{selected && <><div className="diff-meta"><span>{selected.baseline_ref?.slice(0, 8)} → {selected.commit_sha?.slice(0, 8)}</span>{selected.truncated && <Tag color="orange">内容已截断</Tag>}</div>{selected.available ? <pre className="diff-view">{selected.diff || "没有可显示的差异"}</pre> : <div className="empty-state"><strong>diff 暂不可用</strong><span>{selected.reason}</span></div>}</>}</Modal></div>; }

function ManualPanel({ bundle, onRefresh }: { bundle: TaskBundle; onRefresh: () => void }) {
  const { message } = AntApp.useApp();
  const { task, submissions, manualRuns } = bundle;
  const canRun = roleAllows("operator");
  const [running, setRunning] = useState(false);
  const defaultRevision = submissions[0]?.commit_sha ?? "";
  const [revision, setRevision] = useState(defaultRevision);
  const [sampleJson, setSampleJson] = useState("[]");
  useEffect(() => { if (!revision && defaultRevision) setRevision(defaultRevision); }, [defaultRevision, revision]);
  const command = `python -m crawler --platform ${task.platform_key} --max-pages 1 --page-size 2`;
  async function submit() {
    if (!canRun) { message.warning("当前角色没有登记人工运行的权限"); return; }
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
  async function importSamples(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
      }
      const samples = Array.isArray(parsed) ? parsed : (parsed && typeof parsed === "object" && Array.isArray((parsed as { samples?: unknown }).samples) ? (parsed as { samples: unknown[] }).samples : null);
      if (!samples) throw new Error("样本文件必须是数组或包含 samples 数组的 JSON 对象");
      setSampleJson(JSON.stringify(samples, null, 2));
      message.success(`已导入 ${samples.length} 条样本`);
    } catch { message.error("无法读取样本文件，请使用 JSON 或 JSONL 格式"); } finally { event.target.value = ""; }
  }
  return <div className="panel-stack"><div className="panel manual-hero"><div><span className="eyebrow">MANUAL RUN / PREVIEW</span><h2>运行候选采集器</h2><p>正式采集仍由人工命令控制。平台登记运行结果，并把样本带入审查。</p></div><div className="manual-status"><ClockCircleOutlined /><span>{manualRuns[0]?.status ?? "等待运行"}</span></div></div><div className="panel manual-form"><label>代码版本<Input value={revision} onChange={(event) => setRevision(event.target.value)} placeholder="候选 commit SHA" /></label><label>推荐命令<div className="command-box"><code>{command}</code><Tooltip title="复制命令"><Button type="text" icon={<CopyOutlined />} onClick={() => navigator.clipboard?.writeText(command)} /></Tooltip></div></label><label className="file-picker">导入采集样本文件<small>支持 JSON 数组、带 samples 的 JSON 对象或 JSONL</small><input type="file" accept=".json,.jsonl,.ndjson,application/json" onChange={importSamples} /></label><label>采集样本（可选 JSON 数组）<Input.TextArea rows={6} value={sampleJson} onChange={(event) => setSampleJson(event.target.value)} placeholder='例如：[{"source_id":"123","title":"实习岗位"}]' /></label><Button type="primary" loading={running} onClick={submit}>登记人工运行结果</Button></div><div className="panel history-panel"><PanelTitle eyebrow="RUN HISTORY" title="人工运行记录" />{manualRuns.length ? manualRuns.map((run) => <div className="history-row" key={run.manual_run_id}><div><strong>{run.code_revision.slice(0, 12)}</strong><span>{run.command_profile} · {run.environment_fingerprint} · {String(run.result?.sample_count ?? 0)} 条样本</span></div><Tag color={run.status === "WAITING_REVIEW" ? "orange" : "green"}>{run.status}</Tag></div>) : <span className="muted">暂无人工运行记录</span>}</div></div>;
}

function sampleRecords(run?: TaskBundle["manualRuns"][number]) {
  const raw = run?.result?.samples;
  return Array.isArray(raw) ? raw.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object") : [];
}

function StructuredReviewModal({ open, initialSampleIndices, onCancel, onSubmit }: { open: boolean; initialSampleIndices: number[]; onCancel: () => void; onSubmit: (status: "PASS" | "CODE_FIX_REQUIRED", issues: Array<Record<string, unknown>>) => void }) {
  const [field, setField] = useState("title");
  const [issueType, setIssueType] = useState("incorrect");
  const [description, setDescription] = useState("");
  const [sampleIndices, setSampleIndices] = useState("");
  const [codeFixable, setCodeFixable] = useState("unknown");
  useEffect(() => { if (open) setSampleIndices(initialSampleIndices.join(",")); }, [open, initialSampleIndices]);
  function submitWithIssues(status: "PASS" | "CODE_FIX_REQUIRED") {
    if (status === "PASS") { onSubmit(status, []); return; }
    if (!description.trim()) return;
    const parsedIndices = sampleIndices.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0);
    onSubmit(status, [{ field, issue_type: issueType, description: description.trim(), sample_indices: parsedIndices, code_fixable: codeFixable === "unknown" ? null : codeFixable === "yes", evidence_refs: [] }]);
  }
  return <Modal open={open} title="提交人工审查" onCancel={onCancel} footer={null}><p className="modal-copy">通过时直接采纳当前版本；发现问题时，记录字段、样本和是否可以由代码修复。</p><div className="review-issue-form"><label>问题字段<Select value={field} onChange={setField} options={["source_id", "title", "source_url", "location", "description", "requirements", "publish_time", "employment_type", "internship_filter", "pagination", "other"].map((value) => ({ value, label: value }))} /></label><label>问题类型<Select value={issueType} onChange={setIssueType} options={[{ value: "missing", label: "字段缺失" }, { value: "incorrect", label: "字段错误" }, { value: "filter", label: "实习筛选错误" }, { value: "pagination", label: "分页漏数" }, { value: "other", label: "其他" }]} /></label><label>样本编号（逗号分隔，可选）<Input value={sampleIndices} onChange={(event) => setSampleIndices(event.target.value)} placeholder="例如：1,2" /></label><label>是否可由代码修复<Select value={codeFixable} onChange={setCodeFixable} options={[{ value: "unknown", label: "待 AI 诊断" }, { value: "yes", label: "可以由代码修复" }, { value: "no", label: "外部阻断或业务问题" }]} /></label><label>问题说明<Input.TextArea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明实际值、期望值和复现方式" /></label></div><div className="modal-actions"><Button onClick={() => submitWithIssues("CODE_FIX_REQUIRED")} disabled={!description.trim()}>有问题，进入修复</Button><Button type="primary" onClick={() => submitWithIssues("PASS")}>确认通过</Button></div></Modal>;
}

function ReviewPanel({ bundle, onRefresh }: { bundle: TaskBundle; onRefresh: () => void }) { const { message } = AntApp.useApp(); const { task, manualRuns, reviews, repairs } = bundle; const [open, setOpen] = useState(false); const latestRun = manualRuns[0]; const samples = sampleRecords(latestRun); const [issueSampleIndices, setIssueSampleIndices] = useState<number[]>([]); async function review(status: "PASS" | "CODE_FIX_REQUIRED", issues: Array<Record<string, unknown>> = []) { if (!latestRun) { message.warning("请先登记一次人工运行结果"); return; } const issueIndices = new Set((issues[0]?.sample_indices as number[] | undefined) ?? issueSampleIndices); const sampleDecisions = samples.map((_, index) => ({ sample_index: index + 1, status: issueIndices.has(index + 1) ? "ISSUE" : "PASS", issue_refs: issueIndices.has(index + 1) ? [0] : [] })); try { await submitReview(task.task_id, { review_status: status, manual_run_id: latestRun.manual_run_id, code_revision: latestRun.code_revision, sample_count: Number(latestRun.result?.sample_count ?? samples.length), issue_summary: status === "PASS" ? null : String(issues[0]?.description ?? "字段问题待诊断"), field_issues: issues, sample_decisions: sampleDecisions, evidence_refs: [], client_request_id: `frontend-review-${Date.now()}` }); message.success(status === "PASS" ? "已提交通过审查" : "已生成结构化修复请求"); setOpen(false); onRefresh(); } catch { message.error("提交审查失败，请检查人工运行记录"); } } return <div className="panel-stack"><div className="panel review-hero"><div><span className="eyebrow">HUMAN REVIEW</span><h2>样本审查工作台</h2><p>逐字段核对标题、来源 URL、实习类型、正文和发布时间。</p></div><Button type="primary" disabled={!latestRun || samples.length === 0} onClick={() => setOpen(true)}>提交审查结果</Button></div><div className="panel review-context"><span>当前运行</span><strong>{latestRun ? latestRun.manual_run_id : "尚未登记"}</strong><small>{latestRun ? `${latestRun.code_revision.slice(0, 12)} · ${latestRun.result?.sample_count ?? samples.length} 条样本` : "先在人工运行页登记结果"}</small></div><div className="panel sample-table"><div className="sample-head"><span>编号</span><span>标题 / 地点</span><span>正文 / 要求</span><span>审查状态</span></div>{samples.length ? samples.map((sample, index) => <div className="sample-row" key={`${String(sample.source_id ?? index)}-${index}`}><span className="sample-id"><Checkbox checked={issueSampleIndices.includes(index + 1)} onChange={(event) => setIssueSampleIndices((current) => event.target.checked ? [...current, index + 1] : current.filter((value) => value !== index + 1))} /> {String(sample.source_id ?? `#${String(index + 1).padStart(2, "0")}`)}</span><strong>{String(sample.title ?? sample.position ?? "未提供标题")}<small>{String(sample.location ?? "未提供地点")}</small></strong><span>{String(sample.description ?? sample.requirements ?? "未提供正文")}</span><Tag color={issueSampleIndices.includes(index + 1) ? "orange" : "green"}>{issueSampleIndices.includes(index + 1) ? "标记问题" : "待审查"}</Tag></div>) : <div className="sample-empty">{latestRun ? "人工运行结果没有包含样本明细，请重新登记样本 JSON。" : "先在人工运行页登记结果，样本会显示在这里。"}</div>}</div>{reviews.length > 0 && <div className="panel history-panel"><PanelTitle eyebrow="REVIEWS" title="审查记录" />{reviews.map((item) => <div className="history-row" key={item.review_id}><div><strong>{item.review_status}</strong><span>{item.sample_count} 条样本 · {item.reviewer_id} · {item.field_issues?.length ?? 0} 个字段问题</span></div><Tag color={item.review_status === "PASS" ? "green" : "orange"}>{item.review_status}</Tag></div>)}</div>}{repairs.length > 0 && <div className="panel history-panel"><PanelTitle eyebrow="REPAIR RUNS" title="AI 修复记录" />{repairs.map((item) => <div className="history-row" key={item.repair_run_id}><div><strong>第 {item.attempt} 轮修复</strong><span>{item.changed_files.join("、") || "未记录文件"}</span></div><Tag color={item.status === "COMPLETED" ? "green" : "orange"}>{item.status}</Tag></div>)}</div>}<StructuredReviewModal open={open} initialSampleIndices={issueSampleIndices} onCancel={() => setOpen(false)} onSubmit={review} /></div>; }

function BatchesPage() {
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [selected, setSelected] = useState<BatchDetail | null>(null);
  useEffect(() => { listBatches().then(setBatches); }, []);
  async function openBatch(batchId: string) { setSelected(await getBatch(batchId)); }
  return <div className="page-enter"><div className="page-heading"><div><div className="eyebrow">OPERATIONS / BATCHES</div><h1>接入批次</h1><p>按一次提交查看入口数量、任务状态和处理结果。</p></div><Tag color={batches.length ? "green" : "default"}>{batches.length} 个批次</Tag></div><div className="panel batch-list">{batches.length ? batches.map((batch) => <button className="batch-row" key={batch.batch_id} onClick={() => openBatch(batch.batch_id)}><div><strong>{batch.batch_id.slice(0, 12)}</strong><span>{batch.client_request_id}</span></div><div><b>{batch.accepted_count}/{batch.requested_count}</b><small>已接收 / 请求</small></div><Tag color={batch.status === "SUBMITTED" ? "orange" : "green"}>{batch.status}</Tag><time>{formatRelative(batch.updated_at)}</time><ArrowRightOutlined /></button>) : <div className="empty-state"><UnorderedListOutlined /><strong>暂无接入批次</strong><span>从新建接入提交第一个招聘入口。</span></div>}</div><Modal width={820} open={Boolean(selected)} title={selected ? `批次 ${selected.batch.batch_id.slice(0, 12)}` : "批次详情"} onCancel={() => setSelected(null)} footer={null}>{selected && <div className="batch-detail"><div className="batch-detail-metrics"><span>请求 {selected.batch.requested_count}</span><span>接收 {selected.batch.accepted_count}</span><span>拒绝 {selected.batch.rejected_count}</span></div>{selected.tasks.map((task) => <Link className="batch-task" key={task.task_id} to={`/tasks/${task.task_id}`} onClick={() => setSelected(null)}><div><strong>{task.platform_name ?? task.platform_key}</strong><small>{task.entry_url}</small></div><StatusPill status={task.status} /><ArrowRightOutlined /></Link>)}</div>}</Modal></div>;
}

function SamplesPage() {
  const [rows, setRows] = useState<GlobalSample[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<GlobalSample | null>(null);
  useEffect(() => { listAllSamples().then(setRows); }, []);
  const visible = rows.filter((row) => `${row.platform_name ?? ""} ${row.platform_key} ${row.title ?? ""} ${row.location ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  function exportRows() { const blob = new Blob([JSON.stringify(visible, null, 2)], { type: "application/json;charset=utf-8" }); const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = `auto-spider-samples-${new Date().toISOString().slice(0, 10)}.json`; anchor.click(); URL.revokeObjectURL(url); }
  return <div className="page-enter"><div className="page-heading"><div><div className="eyebrow">DATA / MANUAL RUN SAMPLES</div><h1>采集数据</h1><p>查看人工运行带回的岗位样本，审查前后都保留原始字段。</p></div><Tag color={rows.length ? "green" : "default"}>{rows.length} 条样本</Tag></div><div className="toolbar"><Input prefix={<FileSearchOutlined />} placeholder="搜索公司、岗位或地点" value={query} onChange={(event) => setQuery(event.target.value)} allowClear /><Button icon={<DownloadOutlined />} disabled={!visible.length} onClick={exportRows}>导出 JSON</Button><Button icon={<ReloadOutlined />} onClick={() => listAllSamples().then(setRows)}>刷新</Button></div><div className="panel sample-table data-table"><div className="sample-head"><span>公司 / 任务</span><span>岗位 / 地点</span><span>正文</span><span>运行版本</span><span>操作</span></div>{visible.length ? visible.map((row) => <div className="sample-row global-sample-row" key={`${row.task_id}-${row.manual_run_id}-${row.sample_index}`}><span><strong>{row.platform_name ?? row.platform_key}</strong><small>{row.task_id.slice(-8)}</small></span><strong>{row.title ?? row.position ?? "未提供标题"}<small>{row.location ?? "未提供地点"}</small></strong><span className="sample-url">{row.description ?? row.requirements ?? "未提供正文"}</span><code>{row.code_revision.slice(0, 10)}</code><Button type="link" icon={<EyeOutlined />} onClick={() => setSelected(row)}>查看</Button></div>) : <div className="sample-empty">暂无符合条件的采集样本</div>}</div><Modal width={760} open={Boolean(selected)} title="采集样本详情" onCancel={() => setSelected(null)} footer={null}>{selected && <SampleDetail sample={selected} />}</Modal></div>;
}

function RepositoriesPage() {
  const [submissions, setSubmissions] = useState<GlobalSubmission[]>([]);
  useEffect(() => { listAllSubmissions().then(setSubmissions); }, []);
  return <div className="page-enter"><div className="page-heading"><div><div className="eyebrow">CODE / CANDIDATE SUBMISSIONS</div><h1>代码候选</h1><p>候选采集器来自 AI 产出仓库，源仓库只读作为基线。</p></div><Tag color="orange">{submissions.length} 个候选</Tag></div><div className="repository-cards"><RepositoryHealth title="实验源仓库" name="fun-crawler-v2" detail="只读 · 基线 16cba84" tone="source" /><RepositoryHealth title="AI 产出仓库" name="aicoding-auto_spider" detail="候选分支与提交" tone="output" /></div><div className="panel-stack">{submissions.length ? submissions.map((submission) => <div className="panel candidate-card" key={submission.submission_id}><div className="candidate-top"><div><span className="eyebrow">{submission.adoption_status.toUpperCase()} · {submission.platform_name ?? submission.platform_key}</span><h2>{submission.branch_name}</h2><code>{submission.commit_sha ?? "commit pending"}</code></div><Link className="text-button" to={`/tasks/${submission.task_id}`}>打开任务 <ArrowRightOutlined /></Link></div><div className="candidate-summary"><span>{submission.changed_files.length} 个修改文件</span><span>{submission.submission_type ?? "onboarding"}</span><span>{submission.simulated ? "模拟提交" : "真实候选"}</span></div></div>) : <div className="panel empty-state"><BranchesOutlined /><strong>暂无代码候选</strong><span>自动验证通过后，候选提交会显示在这里。</span></div>}</div></div>;
}

function RepositoryHealth({ title, name, detail, tone }: { title: string; name: string; detail: string; tone: "source" | "output" }) { return <div className={`repository-health ${tone}`}><span className="eyebrow">{title}</span><strong>{name}</strong><small>{detail}</small><span className="health-line"><i />本地状态由 API 实时读取</span></div>; }

function AgentSkillsPanel({ health }: { health: SystemHealth | null }) {
  const skills = [health?.checks.collector_skill, health?.checks.spider_king_skill].filter(Boolean);
  return <div className="panel"><PanelTitle eyebrow="AGENT SKILLS" title="OpenHands 受信规则" /><div className="repo-status-grid">{skills.length ? skills.map((skill) => <div className="repo-status-item" key={skill?.name}><CodeOutlined /><div><strong>{skill?.name}</strong><span>自动注入代码生成与修复会话</span><small>{skill?.size_bytes ? `${skill.size_bytes} bytes` : "等待读取"}</small></div><Tag color={skill?.status === "ready" ? "green" : "red"}>{skill?.status?.toUpperCase()}</Tag></div>) : <div className="empty-inline"><CodeOutlined /><span>正在读取 Agent Skill 状态…</span></div>}</div></div>;
}

function PoliciesPage() {
  const { message } = AntApp.useApp();
  const [policies, setPolicies] = useState<PolicyVersion[]>([]);
  const [repositories, setRepositories] = useState<RepositoryStatusBundle | null>(null);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("report");
  const [version, setVersion] = useState("report-v2");
  const [status, setStatus] = useState("draft");
  const [policyJson, setPolicyJson] = useState('{"required_fields":["title","source_url"],"body_required_any_of":["description","requirements"]}');
  const [saving, setSaving] = useState(false);
  async function refresh() { const [policyRows, repoRows, healthRow] = await Promise.all([listPolicies(), getRepositoryStatus(), getSystemHealth()]); setPolicies(policyRows); setRepositories(repoRows); setHealth(healthRow); }
  useEffect(() => { refresh(); }, []);
  async function savePolicy() { let policy: Record<string, unknown>; try { policy = JSON.parse(policyJson); } catch { message.error("规则 JSON 格式不正确"); return; } setSaving(true); try { await createPolicy({ name: name.trim(), version: version.trim(), status, policy }); message.success("规则版本已保存"); setFormOpen(false); await refresh(); } catch { message.error("规则版本保存失败，可能是版本已存在"); } finally { setSaving(false); } }
  const repoItems = repositories ? [repositories.control_plane, repositories.source, repositories.aicoding] : [];
  return <div className="page-enter"><div className="page-heading"><div><div className="eyebrow">SYSTEM / POLICIES & TOOLS</div><h1>规则与工具</h1><p>查看规则版本、仓库边界和本地执行环境状态。</p></div><div className="heading-actions"><Tag color="green"><i className="status-dot" /> 本地控制面</Tag><Button type="primary" icon={<SettingOutlined />} onClick={() => setFormOpen(true)}>新建规则版本</Button></div></div><div className="panel-stack"><div className="panel"><PanelTitle eyebrow="REPOSITORY BOUNDARY" title="三个仓库的职责" /><div className="repo-status-grid">{repoItems.map((repo, index) => <div key={repo.path} className="repo-status-item"><DeploymentUnitOutlined /><div><strong>{["控制面", "实验源", "AI 产出"][index]}</strong><span>{repo.path}</span><small>{repo.is_git_repository === false ? "不可用" : repo.dirty_files?.length ? `${repo.dirty_files.length} 个未提交文件` : "工作区干净"}</small></div><Tag color={repo.is_git_repository === false ? "red" : repo.dirty_files?.length ? "orange" : "green"}>{repo.is_git_repository === false ? "OFFLINE" : repo.dirty_files?.length ? "DIRTY" : "READY"}</Tag></div>)}</div></div><AgentSkillsPanel health={health} /><div className="panel"><PanelTitle eyebrow="POLICY VERSIONS" title="汇报与验收规则" />{policies.length ? policies.map((policy) => <div className="policy-row" key={`${policy.name}-${policy.version}`}><div><strong>{policy.name} · {policy.version}</strong><span>{policy.created_by} · {formatRelative(policy.created_at)}</span></div><Tag color={policy.status === "published" ? "green" : "orange"}>{policy.status}</Tag></div>) : <div className="empty-inline"><SettingOutlined /><span>数据库中还没有发布的规则版本，当前任务使用 report-v1 默认规则。</span></div>}</div></div><Modal open={formOpen} title="新建规则版本" onCancel={() => setFormOpen(false)} footer={null}><div className="policy-form"><label>规则名称<Input value={name} onChange={(event) => setName(event.target.value)} /></label><label>版本号<Input value={version} onChange={(event) => setVersion(event.target.value)} /></label><label>状态<Select value={status} onChange={setStatus} options={[{ value: "draft", label: "草稿" }, { value: "published", label: "发布" }, { value: "retired", label: "停用" }]} /></label><label>规则 JSON<Input.TextArea rows={8} value={policyJson} onChange={(event) => setPolicyJson(event.target.value)} /></label></div><div className="modal-actions"><Button onClick={() => setFormOpen(false)}>取消</Button><Button type="primary" loading={saving} onClick={savePolicy}>保存版本</Button></div></Modal></div>;
}

function AppRoutes() { return <Routes><Route path="/tasks" element={<TaskListPage />} /><Route path="/batches" element={<BatchesPage />} /><Route path="/tasks/new" element={<NewTaskPage />} /><Route path="/samples" element={<SamplesPage />} /><Route path="/tasks/:taskId" element={<TaskDetailPage />} /><Route path="/repositories" element={<RepositoriesPage />} /><Route path="/policies" element={<PoliciesPage />} /><Route path="*" element={<TaskListPage />} /></Routes>; }

export default function App() { return <BrowserRouter><AntApp><Shell><AppRoutes /></Shell></AntApp></BrowserRouter>; }

