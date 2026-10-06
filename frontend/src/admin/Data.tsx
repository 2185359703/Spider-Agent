import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Drawer,
  Input,
  Modal,
  Pagination,
  Select,
  Table,
  Tabs,
} from "antd";
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  DownloadOutlined,
  ExportOutlined,
  FileTextOutlined,
  MessageOutlined,
  SearchOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import {
  type CollectedRecord,
  download,
  post,
  recordsApi,
  request,
  requestId,
} from "./api";
import {
  companyName,
  contextLink,
  dateText,
  entryName,
  groupCompanies,
  safeUrl,
  text,
  label,
} from "./model";
import { Badge, LoadState, PageHeading, Status, useWorkspace } from "./shared";
import { useCompanyApproval } from "./companyReview";

function RecordEvidence({ record }: { record: CollectedRecord }) {
  return (
    <Tabs
      defaultActiveKey="snapshot"
      items={[
        {
          key: "snapshot",
          label: "网页快照",
          children: record.raw_html ? (
            <iframe
              title="采集时保存的原始网页"
              className="evidence-frame"
              sandbox=""
              referrerPolicy="no-referrer"
              srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:;"><style>body{font-family:sans-serif;color:#1e293b;padding:16px;line-height:1.8}</style>${record.raw_html}`}
            />
          ) : (
            <div className="evidence-missing">
              本次采集未保存 HTML 网页快照。
              <p>可以查看接口原始 JSON，并打开官网核对。</p>
            </div>
          ),
        },
        {
          key: "json",
          label: "原始 JSON",
          children: (
            <pre className="raw-evidence">
              {JSON.stringify(record.raw_content, null, 2)}
            </pre>
          ),
        },
        {
          key: "html",
          label: "HTML 源码",
          children: (
            <pre className="raw-evidence">
              {record.raw_html || "未提供 raw_html"}
            </pre>
          ),
        },
      ]}
    />
  );
}
export function DataPage() {
  const companyApproval = useCompanyApproval();
  const workspace = useWorkspace();
  const companies = groupCompanies(workspace.tasks);
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const [evidence, setEvidence] = useState<CollectedRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [emptyRun, setEmptyRun] = useState<{ id: string; code: string } | null>(null);
  const [emptyCode, setEmptyCode] = useState("INCONCLUSIVE");
  const [emptyNote, setEmptyNote] = useState("");
  const page = Number(params.get("page") || 1);
  const size = Number(params.get("size") || 20);
  const selectedTask = workspace.tasks.find(
    (t) => t.task_id === params.get("task"),
  );
  const company = selectedTask ? companyName(selectedTask) : params.get("company") || "";
  const apiParams = new URLSearchParams({
    page: String(page),
    page_size: String(size),
  });
  ["task", "run", "search", "state"].forEach((k) => {
    const v = params.get(k);
    if (v)
      apiParams.set(
        ({ task: "task_id", run: "manual_run_id" } as Record<string, string>)[
          k
        ] || k,
        v,
      );
  });
  if (company && !selectedTask) apiParams.set("company_name", company);
  const data = useQuery({
    queryKey: ["records", apiParams.toString()],
    queryFn: () => recordsApi(apiParams),
    refetchInterval: 3000,
  });
  const update = (values: Record<string, string | undefined>) => {
    const n = new URLSearchParams(params);
    Object.entries(values).forEach(([k, v]) => (v ? n.set(k, v) : n.delete(k)));
    if (!("page" in values)) n.delete("page");
    setParams(n);
  };
  const runs = data.data?.runs || [];
  const run = runs.find((r) => r.manual_run_id === params.get("run"));
  async function confirmEmpty() {
    if (!emptyRun || !emptyNote.trim()) return;
    setBusy(true);
    try {
      const needsRepair = emptyCode === "MISSING_RECORDS";
      await post(`/api/v1/admin/runs/${emptyRun.id}/finalize`, {
        review_status: needsRepair ? "CODE_FIX_REQUIRED" : "NO_DATA_CONFIRMED",
        observation_code: needsRepair ? undefined : emptyCode,
        comment: emptyNote.trim(), client_request_id: requestId(),
      });
      message.success(needsRepair ? "漏采问题已提交，进入 AI 诊断与纠错" : "空结果结论已保存，候选代码未自动采纳");
      setEmptyRun(null);
      queryClient.invalidateQueries();
    } catch (e) { message.error((e as Error).message); }
    finally { setBusy(false); }
  }
  async function finalize() {
    if (!run) return;
    if (run.record_count === 0 && !run.error_msg) {
      setEmptyRun({ id: run.manual_run_id, code: run.code_revision });
      setEmptyNote("");
      setEmptyCode("MISSING_RECORDS");
      return;
    }
    setBusy(true);
    try {
      const summary = await request<{
        total: number;
        checked: number;
        issue_count: number;
        code_revision: string;
        manual_run_id: string;
        sample_count: number;
        field_issues: unknown[];
        sample_decisions: unknown[];
      }>(`/api/v1/admin/runs/${run.manual_run_id}/review-summary`);
      const hasIssues = summary.issue_count > 0 || !!run.error_msg;
      if (!hasIssues) throw new Error("请先记录具体问题");
      Modal.confirm({
        title: hasIssues ? "提交问题并进入 AI 纠错" : "确认本轮审查通过",
        content: `本轮 ${summary.total} 条，有问题 ${summary.issue_count} 条`,
        okText: hasIssues ? "提交问题" : "确认通过",
        cancelText: "继续核对",
        onOk: async () => {
          await post(`/api/v1/admin/runs/${run.manual_run_id}/finalize`, {
            review_status: hasIssues ? "CODE_FIX_REQUIRED" : "PASS",
            client_request_id: requestId(),
          });
          message.success("审查结果已保存");
          queryClient.invalidateQueries();
        },
      });
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="page-surface">
      <PageHeading
        title="采集数据"
        actions={
          <>
            <Button
              icon={<DownloadOutlined />}
              onClick={() => {
                const p = new URLSearchParams(apiParams);
                p.set("export", "true");
                download("/api/v1/admin/records?" + p, "采集数据.json").catch(
                  (e) => message.error(e.message),
                );
              }}
            >
              导出数据
            </Button>
            {company && (
              <Button type="primary" aria-label="公司通过" icon={<CheckCircleOutlined />}
                loading={companyApproval.busy}
                disabled={!['admin', 'reviewer'].includes(workspace.role)}
                onClick={() => companyApproval.approve(company)}>
                公司通过
              </Button>
            )}
            {run && (
              <Button
                type="default"
                icon={<MessageOutlined />}
                loading={busy}
                onClick={finalize}
                disabled={
                  !["admin", "reviewer"].includes(workspace.role) ||
                  !["WAITING_REVIEW", "FAILED", "TIMED_OUT"].includes(
                    run.status,
                  )
                }
              >
                反馈给 AI
              </Button>
            )}
            {run?.status === "WAITING_REVIEW" && run.record_count === 0 && !run.error_msg && (
              <Button loading={busy} disabled={!["admin", "reviewer"].includes(workspace.role)}
                onClick={() => { setEmptyRun({ id: run.manual_run_id, code: run.code_revision });
                  setEmptyNote(""); setEmptyCode("INCONCLUSIVE"); }}>
                处理空结果
              </Button>
            )}
          </>
        }
      />
      <div className="filter-bar">
        <Select
          aria-label="筛选公司"
          value={company}
          options={[
            { value: "", label: "全部公司" },
            ...companies.map((c) => ({ value: c.name, label: c.name })),
          ]}
          onChange={(v) =>
            update({ company: v, task: undefined, run: undefined })
          }
          style={{ width: 180 }}
        />
        <Select
          aria-label="筛选招聘入口"
          value={selectedTask?.task_id || ""}
          options={[
            { value: "", label: "全部入口" },
            ...(companies.find((c) => c.name === company)?.entries || []).map(
              (t) => ({ value: t.task_id, label: entryName(t) }),
            ),
          ]}
          onChange={(v) => update({ task: v, run: undefined })}
          style={{ width: 190 }}
        />
        <Select
          aria-label="筛选采集轮次"
          value={params.get("run") || ""}
          placeholder="采集轮次"
          style={{ width: 220 }}
          options={[
            { value: "", label: "全部采集轮次" },
            ...runs.map((r) => ({
              value: r.manual_run_id,
              label: `${dateText(r.created_at)} · ${r.code_revision.slice(0, 7)}`,
            })),
          ]}
          onChange={(v) => update({ run: v })}
        />
        <Select
          aria-label="采集状态"
          value={params.get("state") || ""}
          options={[
            { value: "", label: "全部采集状态" },
            { value: "success", label: "采集成功" },
            { value: "failed", label: "采集失败" },
          ]}
          onChange={(v) => update({ state: v })}
          style={{ width: 150 }}
        />
        <Input.Search
          aria-label="搜索岗位"
          placeholder="搜索岗位或正文"
          defaultValue={params.get("search") || ""}
          onSearch={(v) => update({ search: v })}
          prefix={<SearchOutlined />}
          style={{ width: 230 }}
        />
      </div>
      {run && (
        <div className="run-strip">
          <Status value={run.status} />
          <span>
            采集轮次 <code>{run.manual_run_id.slice(0, 8)}</code>
          </span>
          <Link
            to={contextLink("/code", run.task_id, {
              submission: workspace.submissions.find(
                (s) =>
                  s.task_id === run.task_id &&
                  s.commit_sha === run.code_revision,
              )?.submission_id,
            })}
          >
            代码 {run.code_revision.slice(0, 7)} ↗
          </Link>
          <Link
            to={contextLink("/logs", run.task_id, {
              run: run.workflow_run_id,
            })}
          >
            <Button type="link" size="small" icon={<FileTextOutlined />}>
              采集日志
            </Button>
          </Link>
          {["RUNNING", "QUEUED", "CANCEL_REQUESTED"].includes(run.status) && (
            <Button
              size="small"
              danger
              onClick={() =>
                post(`/api/v1/admin/runs/${run.manual_run_id}/cancel`, {})
                  .then(() => data.refetch())
                  .catch((e) => message.error(e.message))
              }
            >
              停止本轮采集
            </Button>
          )}
          {run.error_msg && <span className="error-text">{run.error_msg}</span>}
          {run.empty_conclusion && <span>{label(run.empty_conclusion.observation_code)} · {run.empty_conclusion.comment}</span>}
        </div>
      )}
      {run?.quality && <div className="quality-summary">
        <h3>自动质检</h3>
        <div style={{ display: "flex", gap: 14, margin: "12px 0" }}>
          <Badge value={run.quality.status} />
          <span className="error-text">{run.quality.metrics.error_count} 个问题</span>
          <span className="missing-value">{run.quality.metrics.warning_count} 个待核对项</span>
          <span>分页：<Badge value={run.quality.pagination_status} /></span>
        </div>
        {run.quality.findings.length > 0 && <Table size="small" rowKey={(row, index) => `${row.code}:${index}`} pagination={false}
          dataSource={run.quality.findings} columns={[
            { title: "级别", width: 90, render: (_, item) => <Badge value={item.severity === "error" ? "FAIL" : "PARTIAL"} /> },
            { title: "问题", dataIndex: "message" },
            { title: "记录", width: 100, render: (_, item) => item.sample_indices.join("、") || "本轮" },
          ]} />}
      </div>}
      <Modal open={!!emptyRun} title="处理本轮空结果"
        okText={emptyCode === "MISSING_RECORDS" ? "提交问题" : "保存结论"} cancelText="继续核对"
        confirmLoading={busy} okButtonProps={{ disabled: !emptyNote.trim() }}
        onCancel={() => setEmptyRun(null)} onOk={confirmEmpty}>
        <p>代码版本 {emptyRun?.code.slice(0, 7)}。结论仅适用于本次入口及采集参数范围，不代表公司永久没有岗位。</p>
        <Select aria-label="空结果结论" value={emptyCode} onChange={setEmptyCode} style={{ width: "100%" }}
          options={[
            { value: "INCONCLUSIVE", label: "证据不足，需要补查" },
            { value: "MISSING_RECORDS", label: "官网有岗位但未采到，提交 AI 排查" },
            { value: "NO_JOBS_OBSERVED", label: "本次范围内没有岗位" },
            { value: "NO_INTERNSHIPS_OBSERVED", label: "本次范围内没有实习岗位" },
          ]} />
        <Input.TextArea aria-label="空结果核对依据" value={emptyNote} onChange={(e) => setEmptyNote(e.target.value)}
          placeholder="填写核对的页面、筛选条件和结论依据" rows={4} maxLength={5000} style={{ marginTop: 16 }} />
      </Modal>
      <LoadState
        loading={data.isLoading}
        error={data.error}
        onRetry={() => data.refetch()}
      >
        <div className="data-summary">
          共 {data.data?.total || 0} 条记录
          <span className="success-text">
            {data.data?.summary?.success || 0} 条成功
          </span>
          <span className="error-text">
            {data.data?.summary?.failed || 0} 条异常
          </span>
          <span className="missing-value">
            {data.data?.summary?.missing_publish_time || 0} 条发布时间缺失
          </span>
        </div>
        <Table<CollectedRecord>
          className="records-table"
          rowKey={(r) => r.manual_run_id + ":" + r.sample_index}
          dataSource={data.data?.records || []}
          pagination={false}
          scroll={{ x: 1500 }}
          rowClassName={(r) => (r.crawl_status === 0 ? "failed-record" : "")}
          columns={[
            {
              title: "数据源",
              dataIndex: "source_name",
              width: 120,
              fixed: "left",
            },
            {
              title: "岗位标题",
              width: 190,
              render: (_, r) => (
                <Link
                  className="job-title-link"
                  to={contextLink("/data/review", r.task_id, {
                    run: r.manual_run_id,
                    index: String(r.sample_index),
                  })}
                >
                  {text(r.title) || "标题缺失"}
                </Link>
              ),
            },
            {
              title: "岗位描述",
              width: 210,
              render: (_, r) => (
                <div className="excerpt">
                  {text(r.description) || <span className="muted">未提供</span>}
                </div>
              ),
            },
            {
              title: "岗位要求",
              width: 210,
              render: (_, r) => (
                <div className="excerpt">
                  {text(r.requirements) || (
                    <span className="muted">未提供</span>
                  )}
                </div>
              ),
            },
            {
              title: "发布时间",
              width: 130,
              render: (_, r) => (
                <span
                  className={
                    dateText(r.publish_time) === "未提供" ? "missing-value" : ""
                  }
                >
                  {dateText(r.publish_time, false, true)}
                </span>
              ),
            },
            {
              title: "详情链接",
              width: 100,
              render: (_, r) =>
                safeUrl(r.source_url) ? (
                  <a
                    href={safeUrl(r.source_url)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    官网 <ExportOutlined />
                  </a>
                ) : (
                  <span className="muted">链接缺失</span>
                ),
            },
            {
              title: "采集状态",
              width: 100,
              render: (_, r) => (
                <Badge value={r.crawl_status === 0 ? "FAIL" : "PASS"}>
                  {r.crawl_status === 0 ? "失败" : "成功"}
                </Badge>
              ),
            },
            {
              title: "错误信息",
              width: 170,
              render: (_, r) => (
                <span className="excerpt error-text">{r.error_msg || "—"}</span>
              ),
            },
            {
              title: "原始证据",
              width: 110,
              render: (_, r) => (
                <Button type="link" onClick={() => setEvidence(r)}>
                  JSON / HTML
                </Button>
              ),
            },
            {
              title: "操作",
              width: 80,
              fixed: "right",
              render: (_, r) => (
                <Link
                  to={contextLink("/data/review", r.task_id, {
                    run: r.manual_run_id,
                    index: String(r.sample_index),
                  })}
                >
                  核对
                </Link>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (r) => (
              <div className="expanded-job">
                <section>
                  <h4>岗位描述</h4>
                  <p>{text(r.description) || "未提供"}</p>
                </section>
                <section>
                  <h4>岗位要求</h4>
                  <p>{text(r.requirements) || "未提供"}</p>
                </section>
              </div>
            ),
          }}
        />
        <div className="table-footer">
          <span />
          <Pagination
            current={page}
            pageSize={size}
            total={data.data?.total || 0}
            showSizeChanger
            onChange={(p, s) => update({ page: String(p), size: String(s) })}
          />
        </div>
      </LoadState>
      <Drawer
        title="原始采集证据"
        size={720}
        open={!!evidence}
        onClose={() => setEvidence(null)}
      >
        {evidence && <RecordEvidence record={evidence} />}
      </Drawer>
    </section>
  );
}
export function ReviewPage() {
  const companyApproval = useCompanyApproval();
  const [params, setParams] = useSearchParams();
  const workspace = useWorkspace();
  const { message } = App.useApp();
  const cache = useQueryClient();
  const [issue, setIssue] = useState(false);
  const [note, setNote] = useState("");
  const [field, setField] = useState("description");
  const [busy, setBusy] = useState(false);
  const index = Math.max(1, Number(params.get("index") || 1));
  const p = new URLSearchParams({
    task_id: params.get("task") || "",
    manual_run_id: params.get("run") || "",
    page: String(index),
    page_size: "1",
  });
  const query = useQuery({
    queryKey: ["record-detail", p.toString()],
    queryFn: () => recordsApi(p),
    enabled: !!params.get("task") && !!params.get("run"),
  });
  const record = query.data?.records[0];
  async function decide(status: string) {
    if (!record) return;
    setBusy(true);
    try {
      await request(
        `/api/v1/admin/runs/${record.manual_run_id}/records/${record.sample_index}/review`,
        {
          method: "PUT",
          body: JSON.stringify({
            status,
            field: status === "ISSUE" ? field : "other",
            note: status === "ISSUE" ? note : "",
          }),
        },
      );
      cache.invalidateQueries();
      setIssue(false);
      setNote("");
      message.success(
        status === "PASS"
          ? "这条岗位已审核通过"
          : "问题已记录，提交本轮审查后进入纠错",
      );
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const move = (n: number) => {
    const v = new URLSearchParams(params);
    v.set("index", String(n));
    setParams(v);
  };
  return (
    <section className="review-page">
      <Link
        className="back-link"
        to={contextLink("/data", params.get("task") || undefined, {
          run: params.get("run") || undefined,
        })}
      >
        <ArrowLeftOutlined /> 返回采集数据
      </Link>
      <LoadState loading={query.isLoading} error={query.error} empty={!record}>
        <PageHeading
          title={text(record?.title) || "岗位标题缺失"}
          actions={
            safeUrl(record?.source_url) && (
              <Button
                href={safeUrl(record?.source_url)}
                target="_blank"
                icon={<ExportOutlined />}
              >
                打开官网
              </Button>
            )
          }
        />
        {record && (
          <>
            <div className="review-meta">
              <b>{record.source_name}</b>
              <Badge
                value={
                  record.crawl_status === 1
                    ? "PASS"
                    : record.crawl_status === 0
                      ? "FAIL"
                      : "NOT_RUN"
                }
              >
                {record.crawl_status === 1
                  ? "采集成功"
                  : record.crawl_status === 0
                    ? "采集失败"
                    : "采集状态未提供"}
              </Badge>
              <span>
                发布时间：{dateText(record.publish_time, false, true)}
              </span>
              <span>
                本轮记录 {record.sample_index} / {query.data?.total}
              </span>
              <Link
                to={contextLink("/code", record.task_id, {
                  submission: workspace.submissions.find(
                    (s) =>
                      s.task_id === record.task_id &&
                      s.commit_sha === record.code_revision,
                  )?.submission_id,
                })}
              >
                代码 {record.code_revision.slice(0, 7)} ↗
              </Link>
            </div>
            {record.error_msg && (
              <Alert type="error" showIcon title={record.error_msg} />
            )}
            <div className="review-columns">
              <article className="collected-text">
                <h2>采集内容</h2>
                <section>
                  <h3>岗位描述</h3>
                  <div>
                    {text(record.description) || (
                      <span className="missing-value">该字段未提供</span>
                    )}
                  </div>
                </section>
                <section>
                  <h3>岗位要求</h3>
                  <div>
                    {text(record.requirements) || (
                      <span className="missing-value">该字段未提供</span>
                    )}
                  </div>
                </section>
              </article>
              <section className="original-evidence">
                <h2>原始证据</h2>
                <RecordEvidence record={record} />
              </section>
            </div>
            <div className="review-bottom">
              <div>
                <Button disabled={index === 1} onClick={() => move(index - 1)}>
                  上一条
                </Button>
                <Button
                  disabled={index >= (query.data?.total || 0)}
                  onClick={() => move(index + 1)}
                >
                  下一条
                </Button>
              </div>
              <div>
                <Button
                  icon={<WarningOutlined />}
                  onClick={() => setIssue(true)}
                  disabled={!["admin", "reviewer"].includes(workspace.role)}
                >
                  记录问题
                </Button>
                <Button
                  type="primary"
                  aria-label="公司通过"
                  icon={<CheckCircleOutlined />}
                  loading={companyApproval.busy}
                  disabled={!["admin", "reviewer"].includes(workspace.role)}
                  onClick={() => {
                    const task = workspace.tasks.find((t) => t.task_id === record.task_id);
                    companyApproval.approve(task ? companyName(task) : record.source_name);
                  }}
                >
                  公司通过
                </Button>
              </div>
            </div>
          </>
        )}
      </LoadState>
      <Modal
        title="记录官网核对问题"
        open={issue}
        onCancel={() => setIssue(false)}
        onOk={() => decide("ISSUE")}
        confirmLoading={busy}
        okText="保存问题"
        cancelText="取消"
      >
        <Select
          aria-label="有问题的字段"
          value={field}
          onChange={setField}
          options={[
            ["title", "岗位标题"],
            ["description", "岗位描述"],
            ["requirements", "岗位要求"],
            ["source_url", "详情链接"],
            ["publish_time", "发布时间"],
            ["internship_filter", "实习筛选"],
            ["pagination", "分页缺失"],
            ["other", "其他问题"],
          ].map(([value, label]) => ({ value, label }))}
          style={{ width: "100%", marginBottom: 16 }}
        />
        <Input.TextArea
          aria-label="问题说明"
          placeholder="指出采集内容与官网的具体差异"
          rows={5}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </Modal>
    </section>
  );
}
