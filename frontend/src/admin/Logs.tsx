import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Checkbox, Input, Modal, Select, Tag } from "antd";
import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  DownloadOutlined,
  FileOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  SearchOutlined,
  StopOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { WorkflowLogRecord } from "../types";
import { post, request, requestId, saveBlob } from "./api";
import { contextLink, dateText, stageLabels } from "./model";
import {
  Badge,
  LoadState,
  PageHeading,
  SelectionBar,
  Status,
  useSelection,
} from "./shared";

const logCache = new Map<string, WorkflowLogRecord[]>();
function useActivity(task?: string) {
  const [logs, setLogs] = useState<WorkflowLogRecord[]>([]);
  const [error, setError] = useState<Error | null>(null);
  const [connected, setConnected] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [syncAt, setSyncAt] = useState<number>();
  useEffect(() => {
    if (!task) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    let rows = logCache.get(task) || [];
    let cursor = rows.at(-1)?.sequence || 0;
    setLogs(rows);
    setError(null);
    setConnected(false);
    setLoadingHistory(true);
    async function poll() {
      try {
        const result = await request<{
          logs: WorkflowLogRecord[];
          next_after: number;
        }>(`/api/v1/onboarding/tasks/${task}/logs?after=${cursor}&limit=1000`, {
          signal: controller.signal,
        });
        if (!active) return;
        const merged = new Map(rows.map((l) => [l.sequence, l]));
        result.logs.forEach((l) => merged.set(l.sequence, l));
        rows = [...merged.values()].sort((a, b) => a.sequence - b.sequence);
        cursor = result.next_after;
        logCache.set(task!, rows);
        if (logCache.size > 5) {
          const oldest = logCache.keys().next().value;
          if (oldest && oldest !== task) logCache.delete(oldest);
        }
        setLogs(rows);
        setConnected(true);
        setError(null);
        setSyncAt(Date.now());
        setLoadingHistory(result.logs.length === 1000);
        timer = setTimeout(poll, result.logs.length === 1000 ? 50 : 2000);
      } catch (e) {
        if (!active) return;
        setError(e as Error);
        setConnected(false);
        timer = setTimeout(poll, 5000);
      }
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [task]);
  return { logs, error, connected, syncAt, loadingHistory };
}
function kind(log: WorkflowLogRecord) {
  const d = log.detail;
  if (d.action_type) return String(d.action_type);
  if (d.action) return String(d.action);
  if (/ErrorEvent|ERROR|失败/.test(log.message) || log.level === "ERROR")
    return "error";
  if (/WriteAction|修改|写入/.test(log.message)) return "write";
  if (/ReadAction|读取/.test(log.message)) return "read";
  if (/ObservationEvent|完成|返回/.test(log.message)) return "result";
  if (/验证|pytest|ruff|测试/.test(log.message)) return "validation";
  if (log.stage === "openhands") return "message";
  return "system";
}
const kinds: Record<string, string> = {
  browser: "浏览器分析",
  message: "AI 说明",
  read: "读取文件",
  write: "修改文件",
  result: "执行结果",
  validation: "自动验证",
  system: "平台事件",
  error: "执行异常",
  collection_start: "开始采集",
  collection_output: "采集输出",
  collection_result: "采集结果",
};
export function LogsPage() {
  const s = useSelection();
  const activity = useActivity(s.task?.task_id);
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [level, setLevel] = useState("");
  const [stage, setStage] = useState("");
  const [following, setFollowing] = useState(true);
  const [unread, setUnread] = useState(0);
  const [clock, setClock] = useState(Date.now());
  const feed = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);
  const query = useQueryClient();
  const { message } = App.useApp();
  const runs = s.bundle.data?.timeline.runs || [];
  const runId = s.params.get("run") || s.task?.current_run_id || "";
  const selectedRun = runs.find((r) => r.run_id === runId);
  const scoped = activity.logs.filter((l) => !runId || l.run_id === runId);
  const filtered = scoped.filter(
    (l) =>
      (!type || kind(l) === type) &&
      (!level || l.level === level) &&
      (!stage || l.stage === stage) &&
      (!search ||
        (l.message + JSON.stringify(l.detail))
          .toLowerCase()
          .includes(search.toLowerCase())),
  );
  const latest = scoped.at(-1);
  const live = selectedRun?.status === "RUNNING";
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    lastCount.current = 0;
    setUnread(0);
    setFollowing(true);
  }, [s.task?.task_id, runId]);
  useEffect(() => {
    const count = scoped.length;
    const delta = count - lastCount.current;
    if (count > lastCount.current && lastCount.current > 0 && !following)
      setUnread((n) => n + delta);
    lastCount.current = count;
    if (following && feed.current) {
      requestAnimationFrame(() => {
        if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
      });
      setUnread(0);
    }
  }, [scoped.length, following]);
  const start = selectedRun?.started_at
    ? new Date(
        selectedRun.started_at.endsWith("Z")
          ? selectedRun.started_at
          : selectedRun.started_at + "Z",
      ).getTime()
    : NaN;
  const end = selectedRun?.finished_at
    ? new Date(
        selectedRun.finished_at.endsWith("Z")
          ? selectedRun.finished_at
          : selectedRun.finished_at + "Z",
      ).getTime()
    : clock;
  const elapsed = Number.isNaN(start)
    ? "—"
    : `${Math.max(0, Math.floor((end - start) / 1000))} 秒`;
  const submission = s.submissions.find(
    (v) => v.task_id === s.task?.task_id && v.run_id === runId,
  );
  const currentStage = latest
    ? stageLabels[latest.stage] || latest.stage
    : "等待事件";
  const stop = () =>
    Modal.confirm({
      title: "停止当前 AI 任务",
      content: "平台将请求停止当前轮次，已保存的代码、证据和日志继续保留。",
      okText: "停止任务",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        try {
          await post(`/api/v1/onboarding/tasks/${s.task!.task_id}/cancel`, {
            client_request_id: requestId(),
          });
          query.invalidateQueries();
        } catch (e) {
          message.error((e as Error).message);
        }
      },
    });
  return (
    <section className="page-surface log-page">
      <PageHeading
        title="AI 工作日志"
        actions={
          <>
            {selectedRun && <span>本轮 <Status value={selectedRun.status} /></span>}
            <Status value={activity.connected ? "PASS" : "WARNING"}>
              {activity.connected ? "日志同步正常" : "日志正在重连"}
            </Status>
            <Link
              to={contextLink("/code", s.task?.task_id, {
                submission: submission?.submission_id,
              })}
            >
              查看代码 ↗
            </Link>
            <Button
              icon={<StopOutlined />}
              onClick={stop}
              disabled={
                !["admin", "operator"].includes(s.role) ||
                !live ||
                runId !== s.task?.current_run_id
              }
            >
              停止任务
            </Button>
          </>
        }
      />
      <SelectionBar
        selection={s}
        extra={
          <Select
            aria-label="选择工作轮次"
            className="version-select"
            value={runId || undefined}
            placeholder="工作轮次"
            onChange={(v) => {
              const p = new URLSearchParams(s.params);
              p.set("run", v);
              s.setParams(p);
            }}
            options={runs.map((r) => ({
              value: r.run_id,
              label: `${r.run_type === "repair" ? "AI 修复" : "AI 接入"} · 第 ${r.attempt} 轮 · ${dateText(r.started_at)}`,
            }))}
          />
        }
      />
      <div className="log-context muted">
        {submission
          ? `候选代码 ${submission.commit_sha?.slice(0, 8)}`
          : "当前轮次尚无候选提交"}{" "}
        · {runId ? `工作轮次 ${runId.slice(0, 8)}` : "等待工作轮次"}
      </div>
      <div className="current-action">
        <span>
          <ToolOutlined /> {live ? "当前动作：" : "最近动作："}
          {latest && (latest.detail.action_type || latest.detail.action)
            ? `${kinds[kind(latest)] || kind(latest)}`
            : currentStage}
        </span>
        <span>{scoped.length} 条事件已保存</span>
        <span>
          {selectedRun?.finished_at ? "本轮用时" : "已运行"} {elapsed}
        </span>
        <span>最近事件 {latest ? dateText(latest.created_at, true) : "—"}</span>
      </div>
      <div className="log-filters">
        <Select
          aria-label="日志事件类型"
          value={type}
          onChange={setType}
          options={[
            { value: "", label: "全部事件" },
            ...Object.entries(kinds).map(([value, label]) => ({
              value,
              label,
            })),
          ]}
          style={{ width: 150 }}
        />
        <Select
          aria-label="日志级别"
          value={level}
          onChange={setLevel}
          options={[
            { value: "", label: "全部级别" },
            ...["INFO", "WARNING", "ERROR"].map((value) => ({
              value,
              label: value,
            })),
          ]}
          style={{ width: 130 }}
        />
        <Select
          aria-label="工作阶段"
          value={stage}
          onChange={setStage}
          options={[
            { value: "", label: "全部阶段" },
            ...[...new Set(scoped.map((l) => l.stage))].map((v) => ({
              value: v,
              label: stageLabels[v] || v,
            })),
          ]}
          style={{ width: 160 }}
        />
        <Input
          aria-label="搜索日志"
          className="log-search"
          prefix={<SearchOutlined />}
          placeholder="搜索文件、动作或错误"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Checkbox
          checked={following}
          onChange={(e) => setFollowing(e.target.checked)}
        >
          自动跟随
        </Checkbox>
        <Button
          icon={<DownloadOutlined />}
          onClick={() =>
            saveBlob(
              new Blob([filtered.map((l) => JSON.stringify(l)).join("\n")], {
                type: "application/x-ndjson",
              }),
              "AI工作日志.jsonl",
            )
          }
        >
          导出日志
        </Button>
      </div>
      {activity.error && (
        <Alert
          type="warning"
          showIcon
          title="日志暂时断开，正在自动重连"
          description={activity.error.message}
        />
      )}
      <LoadState loading={s.loading} error={s.error} empty={!s.task}>
        <div className="log-feed-wrap">
          <div
            className="log-feed"
            ref={feed}
            onScroll={(e) => {
              const el = e.currentTarget;
              if (
                el.scrollHeight - el.scrollTop - el.clientHeight > 80 &&
                following
              )
                setFollowing(false);
            }}
          >
            {!filtered.length ? (
              <div className="log-empty">
                {activity.connected && !activity.loadingHistory
                  ? "本轮暂无匹配的日志"
                  : "正在读取工作日志…"}
              </div>
            ) : (
              filtered.map((l) => {
                const k = kind(l);
                const path =
                  typeof l.detail.path === "string" ? l.detail.path : undefined;
                const actor = String(
                  l.detail.actor ||
                    (["read", "write", "result"].includes(k)
                      ? "工具"
                      : l.stage === "openhands"
                        ? "AI"
                        : "系统"),
                );
                const phase = String(l.detail.phase || "");
                const actionTitle =
                  k === "validation"
                    ? (
                        {
                          start: "开始运行验证",
                          output: "验证输出",
                          result: "验证完成",
                        } as Record<string, string>
                      )[phase] || "自动验证"
                    : kinds[k] || stageLabels[l.stage] || l.stage;
                const platformKey = s.task?.platform_key;
                const codePath =
                  path &&
                  platformKey &&
                  (path === `collectors/${platformKey}.py` ||
                    path === `config/platforms/${platformKey}.toml` ||
                    path === `tests/test_${platformKey}.py` ||
                    path.startsWith(`tests/fixtures/${platformKey}/`)) &&
                  l.detail.area !== "evidence";
                return (
                  <div
                    className={`log-row ${l.level === "ERROR" ? "log-error" : ""}`}
                    key={l.log_id}
                  >
                    <time title={dateText(l.created_at, true)}>
                      {dateText(l.created_at, true).split(" ").at(-1)}
                    </time>
                    <div className="log-row-icon">
                      {k === "message" ? (
                        <MessageOutlined />
                      ) : k === "result" ? (
                        <CheckCircleOutlined />
                      ) : k === "read" || k === "write" ? (
                        <FileOutlined />
                      ) : (
                        <PlayCircleOutlined />
                      )}
                    </div>
                    <b className="log-action-label">{actionTitle}</b>
                    <Tag className={`actor-tag ${actor === "AI" ? "ai" : ""}`}>
                      {actor}
                    </Tag>
                    <div className="log-row-body">
                      <div className="log-message">
                        {typeof l.detail.status === "string" && (
                          <Badge value={l.detail.status} />
                        )}
                        {codePath && submission ? (
                          <Link
                            to={contextLink("/code", s.task?.task_id, {
                              submission: submission.submission_id,
                              file: path,
                            })}
                          >
                            {path}
                          </Link>
                        ) : (
                          l.message
                        )}
                      </div>
                      {Array.isArray(l.detail.command) && (
                        <pre className="log-command">
                          {l.detail.command.map(String).join(" ")}
                        </pre>
                      )}
                      {typeof l.detail.url === "string" && (
                        <div className="log-row-meta">{l.detail.url}</div>
                      )}
                      {typeof l.detail.output === "string" && (
                        <pre className="log-output">{l.detail.output}</pre>
                      )}
                      {k === "write" && submission && (
                        <Link
                          to={contextLink("/code", s.task?.task_id, {
                            submission: submission.submission_id,
                            tab: "diff",
                          })}
                        >
                          查看本轮差异 ↗
                        </Link>
                      )}
                      <div className="log-row-meta">
                        #{l.sequence} · {stageLabels[l.stage] || l.stage}
                        {l.level !== "INFO" && <Badge value={l.level} />}
                      </div>
                      {Object.keys(l.detail).length > 0 && (
                        <details>
                          <summary>查看执行详情</summary>
                          <pre>{JSON.stringify(l.detail, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
          {!following && unread > 0 && (
            <Button
              className="new-events"
              type="primary"
              ghost
              icon={<ArrowDownOutlined />}
              onClick={() => {
                setFollowing(true);
                setUnread(0);
              }}
            >
              有 {unread} 条新记录 · 回到最新
            </Button>
          )}
        </div>
        <div className="log-bottom">
          <Status value={activity.connected ? "PASS" : "WARNING"}>
            {activity.connected ? "连接正常" : "重连中"}
          </Status>
          <span>每 2 秒同步 · 日志已持久保存</span>
          <span className="muted">
            最近同步 {activity.syncAt ? dateText(activity.syncAt, true) : "—"}
          </span>
        </div>
      </LoadState>
    </section>
  );
}
