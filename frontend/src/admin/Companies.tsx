import { Fragment, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Pagination,
  Segmented,
  Select,
} from "antd";
import {
  DownOutlined,
  RightOutlined,
  SearchOutlined,
  PlusOutlined,
  PlayCircleOutlined,
  ExportOutlined,
} from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Task } from "../types";
import { post, recordsApi, requestId, request } from "./api";
import { CompanyImport } from "./CompanyImport";
import { BatchReport } from "./BatchReport";
import {
  companyName,
  contextLink,
  defaultSubmission,
  entryName,
  groupCompanies,
  label,
  safeUrl,
  dateText,
} from "./model";
import { Badge, LoadState, PageHeading, Status, useWorkspace } from "./shared";

export function CollectDrawer({
  entries,
  onClose,
  pinned,
}: {
  entries: Task[];
  onClose: () => void;
  pinned?: string;
}) {
  const { submissions, role } = useWorkspace();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const query = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [versions, setVersions] = useState<Record<string, string>>({});
  const [pages, setPages] = useState(50);
  const [timeout, setTimeout] = useState(600);
  const [interval, setInterval] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [scope, setScope] = useState("all");
  const [days, setDays] = useState(60);
  const initialized = useRef("");
  const seed = entries.map((t) => t.task_id).join("|") + (pinned || "");
  const ids = useRef<Record<string, string>>({});
  useEffect(() => {
    if (!entries.length) {
      initialized.current = "";
      return;
    }
    if (initialized.current === seed) return;
    initialized.current = seed;
    setSelected(entries.map((t) => t.task_id));
    setVersions(
      Object.fromEntries(
        entries.map((t) => [
          t.task_id,
          pinned ||
            defaultSubmission(submissions, t.task_id)?.submission_id ||
            "",
        ]),
      ),
    );
    ids.current = {};
    setScope("all");
    setPages(50);
    setTimeout(600);
    setInterval(1);
    setDays(60);
    setError("");
  }, [entries, pinned, submissions, seed]);
  async function start() {
    setBusy(true);
    setError("");
    const results = await Promise.allSettled(
      selected.map(async (task) => {
        const fingerprint = `${task}|${versions[task]}|${pages}|${timeout}|${interval}|${scope}|${days}`;
        ids.current[fingerprint] ||= requestId();
        return {
          task,
          ...(await post<{ manual_run_id: string }>(
            `/api/v1/admin/tasks/${task}/collect`,
            {
              submission_id: versions[task],
              client_request_id: ids.current[fingerprint],
              max_pages: pages,
              timeout_seconds: timeout,
              request_interval_seconds: interval,
              published_within_days: scope === "recent" ? days : null,
            },
          )),
        };
      }),
    );
    setBusy(false);
    query.invalidateQueries();
    const errors = results.filter((r) => r.status === "rejected");
    if (errors.length) {
      setError(
        errors
          .map((r) => (r.status === "rejected" ? String(r.reason.message) : ""))
          .join("；"),
      );
      return;
    }
    const first = results[0];
    message.success("采集任务已进入执行队列");
    onClose();
    if (first?.status === "fulfilled")
      navigate(
        contextLink("/data", first.value.task, {
          run: first.value.manual_run_id,
        }),
      );
  }
  return (
    <Drawer
      title="启动采集"
      size={600}
      open={!!entries.length}
      onClose={onClose}
      destroyOnHidden
      footer={
        <div className="drawer-footer">
          <span>由你点击触发，使用固定代码版本</span>
          <Button onClick={onClose}>取消</Button>
          <Button
            type="primary"
            loading={busy}
            icon={<PlayCircleOutlined />}
            disabled={
              !["admin", "operator"].includes(role) ||
              !selected.length ||
              selected.some((t) => !versions[t])
            }
            onClick={start}
          >
            启动采集
          </Button>
        </div>
      }
    >
      <p className="drawer-company">{entries[0] && companyName(entries[0])}</p>
      <div className="muted">
        选择招聘入口与运行版本，采集结果在独立的数据页查看。
      </div>
      <h3 className="section-label">招聘入口</h3>
      <div className="launch-entries">
        {entries.map((t) => {
          const allowed = submissions.filter(
            (s) =>
              s.task_id === t.task_id &&
              s.commit_sha &&
              !s.simulated &&
              ["adopted", "candidate"].includes(s.adoption_status),
          );
          return (
            <div className="launch-entry" key={t.task_id}>
              <Checkbox
                checked={selected.includes(t.task_id)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, t.task_id]
                      : selected.filter((id) => id !== t.task_id),
                  )
                }
              >
                {entryName(t)}
              </Checkbox>
              <a href={safeUrl(t.entry_url)} target="_blank" rel="noreferrer">
                官网 <ExportOutlined />
              </a>
              <Select
                aria-label={`${companyName(t)} ${entryName(t)} 采集版本`}
                value={versions[t.task_id] || undefined}
                placeholder="尚无可运行版本"
                onChange={(v) => setVersions({ ...versions, [t.task_id]: v })}
                options={allowed.map((s) => ({
                  value: s.submission_id,
                  label: `${s.adoption_status === "adopted" ? "已采纳" : "候选版本"} · ${s.commit_sha?.slice(0, 8)}`,
                }))}
              />
              {!allowed.length && (
                <p className="muted">采集器开发和验证完成后，才能运行。</p>
              )}
            </div>
          );
        })}
      </div>
      <h3 className="section-label">采集范围</h3>
      <Segmented
        block
        value={scope}
        onChange={(v) => setScope(String(v))}
        options={[
          { value: "all", label: "全部实习岗位" },
          { value: "recent", label: "指定发布时间范围" },
        ]}
      />
      {scope === "recent" && (
        <div className="recent-range">
          最近{" "}
          <InputNumber
            min={1}
            max={3650}
            value={days}
            onChange={(v) => setDays(v || 60)}
          />{" "}
          天内发布的岗位
        </div>
      )}
      <p className="muted">
        保留该轮次返回的完整记录，发布时间缺失的岗位也会保留。
      </p>
      <Collapse
        ghost
        items={[
          {
            key: "advanced",
            label: "高级参数",
            children: (
              <div className="advanced-options">
                <label>
                  请求间隔（秒）
                  <InputNumber
                    min={0.1}
                    max={30}
                    step={0.1}
                    value={interval}
                    onChange={(v) => setInterval(v || 1)}
                  />
                </label>
                <label>
                  分页安全上限
                  <InputNumber
                    min={1}
                    max={500}
                    value={pages}
                    onChange={(v) => setPages(v || 50)}
                  />
                </label>
                <label>
                  执行时限（秒）
                  <InputNumber
                    min={30}
                    max={3600}
                    value={timeout}
                    onChange={(v) => setTimeout(v || 600)}
                  />
                </label>
              </div>
            ),
          },
        ]}
      />
      {error && (
        <Alert type="error" showIcon title="启动失败" description={error} />
      )}
    </Drawer>
  );
}
export function CompaniesPage() {
  const [bulkOpen, setBulkOpen] = useState(false);
  const batchId = new URLSearchParams(location.search).get("batch");
  const workspace = useWorkspace();
  const { message } = App.useApp();
  const query = useQueryClient();
  const [search, setSearch] = useState("");
  const [state, setState] = useState("");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [launch, setLaunch] = useState<Task[]>([]);
  const [newCompany, setNewCompany] = useState(false);
  const [form] = Form.useForm();
  const [creating, setCreating] = useState(false);
  const records = useQuery({
    queryKey: ["record-summary"],
    queryFn: () => recordsApi(new URLSearchParams({ page_size: "1" })),
    refetchInterval: 5000,
  });
  const companies = groupCompanies(workspace.tasks).filter(
    (c) =>
      c.name.includes(search) &&
      (!state || c.entries.some((t) => t.status === state)),
  );
  const counts = records.data?.task_counts || {};
  const observations = useQuery({
    queryKey: ["task-observations"],
    queryFn: () =>
      request<Record<string, string>>("/api/v1/admin/task-observations"),
    refetchInterval: 5000,
  });
  const versions = (t: Task) =>
    workspace.submissions.filter(
      (s) => s.task_id === t.task_id && s.commit_sha && !s.simulated,
    );
  const last = (t: Task) =>
    records.data?.runs.find((r) => r.task_id === t.task_id);
  const currentStatus = (t: Task) => {
    const run = last(t);
    if (
      t.status === "BLOCKED" &&
      observations.data?.[t.task_id] &&
      observations.data[t.task_id] !== "INTERNSHIPS_FOUND"
    )
      return observations.data[t.task_id];
    return run &&
      ["RUNNING", "QUEUED", "CANCEL_REQUESTED", "FAILED", "TIMED_OUT"].includes(
        run.status,
      )
      ? run.status
      : t.status;
  };
  const companyStatus = (entries: Task[]) => {
    const statuses = new Map<string, number>();
    entries.forEach((t) => {
      const value = currentStatus(t);
      statuses.set(value, (statuses.get(value) || 0) + 1);
    });
    return [...statuses].map(([value, count]) => (
      <Status key={value} value={value}>
        {entries.length > 1 ? `${count} ${label(value)}` : label(value)}
      </Status>
    ));
  };
  const entryAction = (t: Task) => {
    const run = last(t);
    const status = currentStatus(t);
    if (run && ["RUNNING", "QUEUED", "CANCEL_REQUESTED"].includes(run.status))
      return (
        <Link to={contextLink("/data", t.task_id, { run: run.manual_run_id })}>
          查看运行
        </Link>
      );
    if (run && ["FAILED", "TIMED_OUT"].includes(run.status))
      return (
        <Link to={contextLink("/data", t.task_id, { run: run.manual_run_id })}>
          查看原因
        </Link>
      );
    if (run && run.status === "WAITING_REVIEW" && counts[t.task_id]?.count)
      return (
        <Link to={contextLink("/data", t.task_id, { run: run.manual_run_id })}>
          审查数据
        </Link>
      );
    if (["ANALYZING", "REPAIRING", "SUBMITTED"].includes(status))
      return <Link to={contextLink("/logs", t.task_id)}>查看进展</Link>;
    if (
      versions(t).some((v) =>
        ["candidate", "adopted"].includes(v.adoption_status),
      )
    )
      return (
        <Button className="outlined-primary" onClick={() => setLaunch([t])}>
          启动采集
        </Button>
      );
    return <Link to={contextLink("/status", t.task_id)}>查看汇报</Link>;
  };
  async function create() {
    try {
      const v = await form.validateFields();
      setCreating(true);
      const urls = (v.urls as string)
        .split(/\r?\n/)
        .map((v) => v.trim())
        .filter(Boolean);
      if (!urls.length || urls.some((url) => !safeUrl(url)))
        throw new Error("每行填写一个有效的 http/https 招聘链接");
      await post("/api/v1/onboarding/batches", {
        items: urls.map((entry_url) => ({ entry_url, platform_name: v.name })),
        analysis_profile: "default",
        dry_run: false,
        client_request_id: requestId(),
      });
      setNewCompany(false);
      form.resetFields();
      query.invalidateQueries();
      message.success("已创建接入任务");
    } catch (e) {
      if (e instanceof Error) message.error(e.message);
    } finally {
      setCreating(false);
    }
  }
  return (
    <section className="page-surface">
      <PageHeading
        title="公司采集"
        actions={
          <>
            <Input
              aria-label="搜索公司"
              placeholder="搜索公司名称"
              prefix={<SearchOutlined />}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              className="toolbar-search"
            />
            <Select
              aria-label="筛选公司状态"
              value={state}
              onChange={(v) => {
                setState(v);
                setPage(1);
              }}
              style={{ width: 150 }}
              options={[
                { label: "全部状态", value: "" },
                ...[
                  "ANALYZING",
                  "WAITING_MANUAL_RUN",
                  "WAITING_MANUAL_REVIEW",
                  "ADOPTED",
                  "REPAIRING",
                  "BLOCKED",
                  "FAILED",
                ].map((s) => ({ value: s, label: label(s) })),
              ]}
            />
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={!["admin", "operator"].includes(workspace.role)}
              onClick={() => setNewCompany(true)}
            >
              新增公司
            </Button>
            <Button disabled={!["admin", "operator"].includes(workspace.role)}
              onClick={() => setBulkOpen(true)}>批量添加</Button>
          </>
        }
      />
      <LoadState
        loading={workspace.loading}
        error={workspace.error}
        empty={!companies.length}
      >
        <div className="table-scroll">
          <table className="company-table">
            <colgroup>
              {[25, 14, 17, 20, 13, 11].map((w) => (
                <col key={w} style={{ width: `${w}%` }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {["公司", "数据", "代码", "状态", "最近采集", "操作"].map(
                  (s) => (
                    <th key={s}>{s}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {companies.slice((page - 1) * 20, page * 20).map((c) => (
                <Fragment key={c.name}>
                  <tr className="company-parent">
                    <td>
                      <button
                        className="expand-company"
                        aria-label={`展开${c.name}`}
                        aria-expanded={expanded.includes(c.name)}
                        onClick={() =>
                          setExpanded(
                            expanded.includes(c.name)
                              ? expanded.filter((n) => n !== c.name)
                              : [...expanded, c.name],
                          )
                        }
                      >
                        {expanded.includes(c.name) ? (
                          <DownOutlined />
                        ) : (
                          <RightOutlined />
                        )}
                      </button>
                      <div>
                        <strong>{c.name}</strong>
                        <div className="muted">
                          {c.entries.length} 个招聘入口
                        </div>
                      </div>
                    </td>
                    <td>
                      <span>
                        {c.entries.some((t) => counts[t.task_id])
                          ? `${c.entries.reduce((n, t) => n + (counts[t.task_id]?.count || 0), 0)} 条记录`
                          : "未采集"}
                      </span>
                      <Link
                        className="secondary-link"
                        to={contextLink("/data", c.entries[0].task_id)}
                      >
                        查看数据
                      </Link>
                    </td>
                    <td>
                      <Link to={contextLink("/code", c.entries[0].task_id)}>
                        {c.entries.filter((t) => versions(t).length).length}{" "}
                        个采集器
                      </Link>
                    </td>
                    <td>
                      <Link to={contextLink("/status", c.entries[0].task_id)}>
                        <span className="company-statuses">
                          {companyStatus(c.entries)}
                        </span>
                      </Link>
                    </td>
                    <td>
                      {last(c.entries[0])
                        ? dateText(last(c.entries[0])?.created_at)
                        : "—"}
                    </td>
                    <td>
                      {c.entries.length === 1 ? (
                        entryAction(c.entries[0])
                      ) : (
                        <Button
                          className="outlined-primary"
                          disabled={
                            !c.entries.some((t) =>
                              versions(t).some((s) =>
                                ["adopted", "candidate"].includes(
                                  s.adoption_status,
                                ),
                              ),
                            )
                          }
                          onClick={() => setLaunch(c.entries)}
                        >
                          启动采集
                        </Button>
                      )}
                    </td>
                  </tr>
                  {expanded.includes(c.name) &&
                    c.entries.map((t) => {
                      const sub = defaultSubmission(
                        workspace.submissions,
                        t.task_id,
                      );
                      return (
                        <tr key={t.task_id} className="company-entry">
                          <td>
                            <span className="entry-line" />
                            <div>
                              <b>{entryName(t)}</b>
                              <a
                                className="secondary-link"
                                href={safeUrl(t.entry_url)}
                                target="_blank"
                                rel="noreferrer"
                              >
                                查看招聘入口 ↗
                              </a>
                            </div>
                          </td>
                          <td>
                            <Link to={contextLink("/data", t.task_id)}>
                              {counts[t.task_id]
                                ? `${counts[t.task_id].count} 条`
                                : "未采集"}
                            </Link>
                          </td>
                          <td>
                            {sub ? (
                              <Link
                                to={contextLink("/code", t.task_id, {
                                  submission: sub.submission_id,
                                })}
                              >
                                <code>{sub.commit_sha?.slice(0, 7)}</code>{" "}
                                <Badge
                                  value={
                                    sub.adoption_status === "adopted"
                                      ? "ADOPTED"
                                      : "UNCHECKED"
                                  }
                                >
                                  {sub.adoption_status === "adopted"
                                    ? "已采纳"
                                    : "候选版本"}
                                </Badge>
                              </Link>
                            ) : (
                              <span className="muted">尚无代码</span>
                            )}
                          </td>
                          <td>
                            <Link to={contextLink("/status", t.task_id)}>
                              <Status value={currentStatus(t)} />
                            </Link>
                          </td>
                          <td>
                            {last(t) ? dateText(last(t)?.created_at) : "—"}
                          </td>
                          <td>{entryAction(t)}</td>
                        </tr>
                      );
                    })}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
        <div className="table-footer">
          <span>共 {companies.length} 家公司</span>
          <Pagination
            current={page}
            total={companies.length}
            pageSize={20}
            showSizeChanger={false}
            onChange={setPage}
          />
        </div>
      </LoadState>
      <CollectDrawer entries={launch} onClose={() => setLaunch([])} />
      <BatchReport batchId={batchId} />
      <CompanyImport open={bulkOpen} onClose={() => setBulkOpen(false)} />
      <Modal
        title="新增公司"
        open={newCompany}
        onCancel={() => setNewCompany(false)}
        onOk={create}
        confirmLoading={creating}
        okText="开始 AI 接入"
        cancelText="取消"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="公司名称"
            rules={[{ required: true, message: "填写公司名称" }]}
          >
            <Input placeholder="例如：莉莉丝游戏" maxLength={120} />
          </Form.Item>
          <Form.Item
            name="urls"
            label="招聘页面链接"
            rules={[{ required: true, message: "填写招聘入口" }]}
          >
            <Input.TextArea
              rows={5}
              placeholder="每行一个招聘入口 URL，同一公司可添加多个入口"
            />
          </Form.Item>
        </Form>
      </Modal>
    </section>
  );
}
