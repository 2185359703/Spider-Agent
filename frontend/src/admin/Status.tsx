import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Select,
  Table,
  Timeline,
} from "antd";
import {
  PauseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  healthApi,
  policiesApi,
  post,
  repositoriesApi,
  requestId,
} from "./api";
import { contextLink, dateText, label, stageLabels } from "./model";
import { eventLabel, failureLabel, nextAction } from "./labels";
import { FailureOverview, ReportOverview } from "./ReportVisual";
import {
  Badge,
  ContextLinks,
  LoadState,
  PageHeading,
  SelectionBar,
  Status,
  useSelection,
  useWorkspace,
} from "./shared";

const onboarding = [
  "normalize_input",
  "inspect_site",
  "build_spec",
  "generate_code",
  "run_validation",
  "build_report",
  "report_gate",
  "commit_candidate",
  "await_manual_run",
  "record_review",
];
const repair = [
  "build_failure_bundle",
  "diagnose_failure",
  "patch_code",
  "run_regression",
  "submit_fix",
  "await_manual_run",
  "record_review",
];
export function StatusPage() {
  const s = useSelection();
  const query = useQueryClient();
  const { message } = App.useApp();
  const [busy, setBusy] = useState(false);
  const bundle = s.bundle.data;
  const runs = bundle?.timeline.runs || [];
  const runId = s.params.get("run") || s.task?.current_run_id || "";
  const run = runs.find((r) => r.run_id === runId);
  const checkpoints =
    bundle?.timeline.checkpoints?.filter((c) => c.run_id === runId) || [];
  const steps = run?.run_type === "repair" ? repair : onboarding;
  const task = bundle?.task || s.task;
  const report = bundle?.report?.run_id === runId ? bundle.report : null;
  const submission = s.submissions.find(
    (x) => x.run_id === runId && x.task_id === task?.task_id,
  );
  async function control(action: string) {
    if (!task) return;
    setBusy(true);
    try {
      await post(`/api/v1/onboarding/tasks/${task.task_id}/${action}`, {
        client_request_id: requestId(),
      });
      query.invalidateQueries();
      message.success("操作已提交");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const canOperate = ["admin", "operator"].includes(s.role);
  const active = runId === task?.current_run_id;
  const paused = ["PAUSED", "INTERRUPTED"].includes(task?.status || "");
  const failed = ["FAILED", "TIMED_OUT", "INTERRUPTED", "BLOCKED"].includes(
    task?.status || "",
  );
  return (
    <section className="page-surface">
      <PageHeading
        title="任务状态"
        actions={
          <>
            <ContextLinks task={task} />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => s.bundle.refetch()}
            >
              刷新
            </Button>
          </>
        }
      />
      <SelectionBar
        selection={s}
        extra={
          <Select
            aria-label="选择任务轮次"
            className="version-select"
            value={runId || undefined}
            onChange={(v) => {
              const p = new URLSearchParams(s.params);
              p.set("run", v);
              s.setParams(p);
            }}
            options={runs.map((r) => ({
              value: r.run_id,
              label: `${r.run_type === "repair" ? "修复" : "接入"} · 第 ${r.attempt} 轮 · ${dateText(r.started_at)}`,
            }))}
          />
        }
      />
      <LoadState
        loading={s.loading || s.bundle.isLoading}
        error={s.error || s.bundle.error}
        empty={!task}
      >
        <div className="task-state-strip">
          <div>
            <Status value={active ? task?.status : run?.status} />
          </div>
          <div className="task-controls">
            {canOperate && active && (
              <>
                {paused ? (
                  <Button
                    loading={busy}
                    icon={<PlayCircleOutlined />}
                    onClick={() => control("resume")}
                  >
                    恢复
                  </Button>
                ) : (
                  <Button
                    loading={busy}
                    icon={<PauseCircleOutlined />}
                    disabled={run?.status !== "RUNNING"}
                    onClick={() => control("pause")}
                  >
                    暂停
                  </Button>
                )}
                <Button
                  loading={busy}
                  danger
                  icon={<StopOutlined />}
                  disabled={run?.status !== "RUNNING"}
                  onClick={() => control("cancel")}
                >
                  停止
                </Button>
                {failed && (
                  <Button
                    type="primary"
                    loading={busy}
                    onClick={() => control("retry")}
                  >
                    重试
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <Descriptions
          className="run-metadata"
          column={3}
          items={[
            {
              key: "entry",
              label: "招聘入口",
              children: (
                <a href={task?.entry_url} target="_blank" rel="noreferrer">
                  查看官网 ↗
                </a>
              ),
            },
            {
              key: "start",
              label: "开始时间",
              children: dateText(run?.started_at),
            },
            {
              key: "end",
              label: "结束时间",
              children: run?.finished_at
                ? dateText(run.finished_at)
                : "尚未结束",
            },
            {
              key: "version",
              label: "候选提交",
              children: submission ? (
                <Link
                  to={contextLink("/code", task?.task_id, {
                    submission: submission.submission_id,
                  })}
                >
                  {submission.commit_sha?.slice(0, 12)}
                </Link>
              ) : (
                "尚未生成"
              ),
            },
            {
              key: "id",
              label: "轮次编号",
              children: <code>{runId.slice(0, 12) || "—"}</code>,
            },
            {
              key: "next",
              label: "下一动作",
              children: nextAction(task?.next_action),
            },
          ]}
        />
        {(run?.error_code || run?.error_message) && (
          <Alert
            type="error"
            showIcon
            title={run.error_code || "执行失败"}
            description={run.error_message}
          />
        )}
        <h2 className="section-heading">工作流进度</h2>
        <div className="workflow-stage-list">
          {steps.map((node, i) => {
            const cp = checkpoints.filter((c) => c.node === node).at(-1);
            const isWaiting =
              active &&
              ((node === "await_manual_run" &&
                task?.status === "WAITING_MANUAL_RUN") ||
                (node === "record_review" &&
                  task?.status === "WAITING_MANUAL_REVIEW"));
            const state =
              cp?.passed === true
                ? "PASS"
                : cp?.passed === false
                  ? "FAIL"
                  : isWaiting
                    ? "WAITING_REVIEW"
                    : cp
                      ? "COMPLETED"
                      : "NOT_RUN";
            return (
              <div key={node} className="workflow-stage">
                <span className={`step-number ${cp?.passed ? "done" : ""}`}>
                  {i + 1}
                </span>
                <b>{stageLabels[node]}</b>
                <Badge value={state}>
                  {state === "NOT_RUN"
                    ? "暂无完成记录"
                    : state === "WAITING_REVIEW"
                      ? "等待人工"
                      : label(state)}
                </Badge>
                {cp && (
                  <span className="muted">执行记录第 {cp.revision} 版</span>
                )}
              </div>
            );
          })}
        </div>
        <h2 className="section-heading">接入汇报</h2>
        {report ? (
          <ReportOverview report={report} evidence={bundle?.evidence || []} />
        ) : (
          <Alert type="info" showIcon title="本轮尚未生成接入汇报" />
        )}
        {!!bundle?.failures.length && (
          <>
            <h2 className="section-heading">失败纠错包</h2>
            <Table
              rowKey="bundle_id"
              pagination={false}
              dataSource={bundle.failures}
              columns={[
                {
                  title: "问题类型",
                  render: (_, r) => (
                    <span title={r.failure_type}>
                      {failureLabel(r.failure_type)}
                    </span>
                  ),
                },
                { title: "状态", render: (_, r) => <Badge value={r.status} /> },
                {
                  title: "代码能否解决",
                  render: (_, r) =>
                    r.code_fixable === null
                      ? "等待诊断"
                      : r.code_fixable
                        ? "可通过代码修复"
                        : "需要外部处理",
                },
                {
                  title: "操作",
                  render: (_, r) => (
                    <Button
                      disabled={
                        !canOperate ||
                        r.code_fixable === false ||
                        ["REPAIRED", "RESOLVED"].includes(r.status)
                      }
                      onClick={() =>
                        post(
                          `/api/v1/onboarding/tasks/${task?.task_id}/repair`,
                          {
                            failure_bundle_id: r.bundle_id,
                            client_request_id: requestId(),
                          },
                        )
                          .then(() => {
                            query.invalidateQueries();
                            message.success("修复任务已提交");
                          })
                          .catch((e) => message.error(e.message))
                      }
                    >
                      触发修复
                    </Button>
                  ),
                },
              ]}
              expandable={{
                expandedRowRender: (r) => (
                  <FailureOverview
                    failure={r}
                    evidence={bundle?.evidence || []}
                  />
                ),
              }}
            />
          </>
        )}
        <h2 className="section-heading">事件记录</h2>
        <Timeline
          items={(bundle?.timeline.events || [])
            .filter((e) => !e.run_id || e.run_id === runId)
            .map((e) => ({
              content: (
                <div>
                  <b title={e.event_type}>{eventLabel(e.event_type)}</b>
                  <span className="timeline-date">
                    {dateText(e.occurred_at)}
                  </span>
                </div>
              ),
            }))}
        />
      </LoadState>
    </section>
  );
}
export function SettingsPage() {
  const workspace = useWorkspace();
  const { message } = App.useApp();
  const cache = useQueryClient();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: healthApi,
    refetchInterval: 30000,
  });
  const repos = useQuery({
    queryKey: ["repositories"],
    queryFn: repositoriesApi,
  });
  const policies = useQuery({ queryKey: ["policies"], queryFn: policiesApi });
  const [open, setOpen] = useState(false);
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  async function createPolicy() {
    try {
      const v = await form.validateFields();
      const policy = JSON.parse(v.policy);
      if (!policy || Array.isArray(policy) || typeof policy !== "object")
        throw new Error("规则必须是 JSON 对象");
      setBusy(true);
      await post("/api/v1/policies", {
        name: v.name,
        version: v.version,
        status: "draft",
        policy,
      });
      cache.invalidateQueries({ queryKey: ["policies"] });
      setOpen(false);
      form.resetFields();
      message.success("规则草稿已保存");
    } catch (e) {
      if (e instanceof Error) message.error(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page-surface settings-page">
      <PageHeading
        title="系统设置"
        actions={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              health.refetch();
              repos.refetch();
              policies.refetch();
            }}
          >
            刷新状态
          </Button>
        }
      />
      <h2 className="section-heading">服务状态</h2>
      <LoadState
        loading={health.isLoading}
        error={health.error}
        onRetry={() => health.refetch()}
      >
        <div className="settings-services">
          {Object.entries(health.data?.checks || {}).map(([name, value]) => (
            <div key={name}>
              <b>
                {(
                  {
                    mysql: "MySQL",
                    database: "数据库",
                    redis: "Redis",
                    openhands: "OpenHands Agent Server",
                    agent_server: "OpenHands Agent Server",
                  } as Record<string, string>
                )[name] || name}
              </b>
              <Status value={value.status}>{label(value.status)}</Status>
              <span className="muted">
                {value.detail || value.url || value.name || "已检查"}
              </span>
            </div>
          ))}
        </div>
        <Descriptions
          column={3}
          items={[
            { label: "AI 执行", children: health.data?.agent_mode },
            { label: "网站分析", children: health.data?.analysis_mode },
            {
              label: "执行队列",
              children: health.data?.queue_enabled ? "已启用" : "未启用",
            },
          ]}
        />
      </LoadState>
      <h2 className="section-heading">项目与仓库</h2>
      <LoadState loading={repos.isLoading} error={repos.error}>
        {repos.data &&
          Object.entries(repos.data).map(([role, repo]) => (
            <div className="repo-setting" key={role}>
              <div>
                <b>
                  {(
                    {
                      control_plane: "AI 采集管理平台",
                      source: "采集代码基线",
                      aicoding: "AI 生成采集代码",
                    } as Record<string, string>
                  )[role] || role}
                </b>
                <Badge
                  value={repo.is_git_repository === false ? "FAIL" : "PASS"}
                >
                  {role === "source"
                    ? "只读基线"
                    : role === "control_plane"
                      ? "本地项目"
                      : "候选提交仓库"}
                </Badge>
              </div>
              <code>{repo.path}</code>
              <p className="muted">
                {repo.baseline_ref &&
                  `基线 ${repo.baseline_ref.slice(0, 12)} · `}
                {repo.head && `HEAD ${repo.head.slice(0, 12)}`}
                {repo.push_enabled === false ? " · 自动推送未启用" : ""}
              </p>
              {repo.error && <Alert type="error" title={repo.error} />}
            </div>
          ))}
      </LoadState>
      <div className="section-heading-row">
        <h2 className="section-heading">规则版本</h2>
        <Button
          disabled={workspace.role !== "admin"}
          onClick={() => setOpen(true)}
        >
          新建规则草稿
        </Button>
      </div>
      <LoadState loading={policies.isLoading} error={policies.error}>
        <Table
          pagination={false}
          rowKey={(r) => r.name + ":" + r.version}
          dataSource={policies.data}
          columns={[
            { title: "规则", dataIndex: "name" },
            { title: "版本", dataIndex: "version" },
            { title: "状态", render: (_, r) => <Badge value={r.status} /> },
            { title: "创建人", dataIndex: "created_by" },
            { title: "创建时间", render: (_, r) => dateText(r.created_at) },
          ]}
          expandable={{
            expandedRowRender: (r) => (
              <pre className="validation-json">
                {JSON.stringify(r.policy, null, 2)}
              </pre>
            ),
          }}
        />
      </LoadState>
      <Modal
        title="新建规则草稿"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={createPolicy}
        confirmLoading={busy}
        okText="保存草稿"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="version" label="版本" rules={[{ required: true }]}>
            <Input placeholder="例如 report-v2" />
          </Form.Item>
          <Form.Item
            name="policy"
            label="规则内容（JSON）"
            rules={[{ required: true }]}
          >
            <Input.TextArea
              rows={8}
              placeholder={'{\n  "required_fields": ["title"]\n}'}
            />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  );
}
