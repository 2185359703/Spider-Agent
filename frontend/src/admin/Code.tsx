import { useEffect, useState } from "react";
import { Alert, App, Button, Input, Select, Table, Tabs, Tag } from "antd";
import {
  BranchesOutlined,
  CopyOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderOutlined,
  LockOutlined,
  PlayCircleOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { codeApi, download, request, requestId, saveBlob } from "./api";
import { contextLink, dateText, defaultSubmission } from "./model";
import {
  Badge,
  LoadState,
  PageHeading,
  SelectionBar,
  useSelection,
} from "./shared";
import { CollectDrawer } from "./Companies";

function highlighted(line: string) {
  const parts = line.split(
    /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|#.*$|\b(?:class|def|return|if|elif|else|for|while|in|import|from|as|try|except|finally|raise|with|yield|None|True|False|and|or|not|async|await|break|continue|pass)\b|\b\d+(?:\.\d+)?\b)/g,
  );
  return parts.map((v, i) => (
    <span
      key={i}
      className={
        v.startsWith("#")
          ? "syntax-comment"
          : /^["']/.test(v)
            ? "syntax-string"
            : /^\d/.test(v)
              ? "syntax-number"
              : /^(class|def|return|if|elif|else|for|while|in|import|from|as|try|except|finally|raise|with|yield|None|True|False|and|or|not|async|await|break|continue|pass)$/.test(
                    v,
                  )
                ? "syntax-keyword"
                : undefined
      }
    >
      {v}
    </span>
  ));
}
export function CodePage() {
  const s = useSelection();
  const { message } = App.useApp();
  const cache = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [fileSearch, setFileSearch] = useState("");
  const [codeSearch, setCodeSearch] = useState("");
  const [raw, setRaw] = useState(false);
  const [launch, setLaunch] = useState(false);
  const [compare, setCompare] = useState<string | undefined>();
  const versions = s.submissions.filter(
    (v) => v.task_id === s.task?.task_id && v.commit_sha && !v.simulated,
  );
  const selected =
    versions.find((v) => v.submission_id === s.params.get("submission")) ||
    defaultSubmission(s.submissions, s.task?.task_id || "");
  const id = selected?.submission_id;
  const taskId = s.task?.task_id || "";
  const file = s.params.get("file") || undefined;
  const tab = s.params.get("tab") || "files";
  useEffect(() => {
    if (id && taskId && !s.params.has("submission")) {
      const p = new URLSearchParams(s.params);
      p.set("task", taskId);
      p.set("submission", id);
      s.setParams(p, { replace: true });
    }
  }, [id, taskId, s.params, s.setParams]);
  const source = useQuery({
    queryKey: ["code", taskId, id, file],
    queryFn: () => codeApi(taskId, id!, file),
    enabled: !!id,
  });
  const draftKey = `${id}:${source.data?.path}`;
  const content = drafts[draftKey] ?? source.data?.content ?? "";
  const editable = ["admin", "operator"].includes(s.role);
  async function save() {
    if (!source.data?.path || !id) return;
    setSaving(true);
    try {
      const result = await request<{ submission_id: string; status: string }>(
        `/api/v1/admin/tasks/${taskId}/code/${id}`,
        { method: "PUT", body: JSON.stringify({ path: source.data.path, content,
          base_commit: source.data.commit_sha, client_request_id: requestId() }) },
      );
      await cache.invalidateQueries();
      set({ submission: result.submission_id, file: source.data.path });
      setEditing(false);
      setDrafts((all) => { const next = { ...all }; delete next[draftKey]; return next; });
      if (result.status === "draft") message.warning("已保存，代码检查未通过");
      else message.success("新版本已保存");
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const diff = useQuery({
    queryKey: ["diff", taskId, id, compare],
    queryFn: () =>
      request<{ base: string; head: string; diff: string; truncated: boolean }>(
        `/api/v1/admin/tasks/${taskId}/code/${id}/diff` +
          (compare ? "?against=" + compare : ""),
      ),
    enabled: !!id && tab === "diff",
  });
  const set = (v: Record<string, string | undefined>) => {
    const p = new URLSearchParams(s.params);
    Object.entries(v).forEach(([k, x]) => (x ? p.set(k, x) : p.delete(k)));
    s.setParams(p);
  };
  const validation =
    source.data && "validation" in source.data
      ? (source.data.validation as Record<string, unknown> | null)
      : null;
  const groups = new Map<string, string[]>();
  source.data?.files
    .filter((f) => f.includes(fileSearch))
    .forEach((f) => {
      const at = f.lastIndexOf("/");
      const dir = f.slice(0, at);
      groups.set(dir, [...(groups.get(dir) || []), f]);
    });
  return (
    <section className="page-surface code-page">
      <PageHeading
        title="采集代码"
        actions={
          <>
            <Button
              icon={<DownloadOutlined />}
              disabled={!id || source.isError || source.isLoading}
              onClick={() =>
                download(
                  `/api/v1/admin/tasks/${taskId}/code/${id}/download`,
                  `${s.task?.platform_key}-${selected?.commit_sha?.slice(0, 8)}.zip`,
                ).catch((e) => message.error(e.message))
              }
            >
              下载平台代码
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={
                editing || saving ||
                !selected ||
                source.isError ||
                source.isLoading ||
                !["adopted", "candidate"].includes(selected.adoption_status) ||
                !["admin", "operator"].includes(s.role)
              }
              onClick={() => setLaunch(true)}
            >
              使用此版本采集
            </Button>
            <Link to={contextLink("/logs", taskId, { run: selected?.run_id })}>
              查看生成日志 ↗
            </Link>
          </>
        }
      />
      <SelectionBar
        selection={s}
        extra={
          <Select
            aria-label="选择代码版本"
            className="version-select"
            suffixIcon={<BranchesOutlined />}
            disabled={saving}
            value={id}
            placeholder="暂无提交版本"
            onChange={(v) => { setEditing(false); set({ submission: v, file: undefined }); }}
            options={versions.map((v) => ({
              value: v.submission_id,
              label: `${v.adoption_status === "adopted" ? "已采纳" : v.adoption_status === "candidate" ? "候选版本" : v.adoption_status === "draft" ? "草稿" : "历史版本"} · ${v.commit_sha?.slice(0, 8)}`,
            }))}
          />
        }
      />
      <LoadState loading={s.loading} error={s.error} empty={!s.task}>
        <div className="version-strip">
          {source.data?.validation_status === "PASS" && (
            <Badge value="PASS">自动验证通过</Badge>
          )}
          {selected?.submission_type === "manual_edit" && source.data?.validation_status === "PARTIAL" && (
            <Badge value="PASS">代码检查通过</Badge>
          )}
          <Badge
            value={
              selected?.adoption_status === "adopted"
                ? "ADOPTED"
                : selected?.adoption_status === "candidate"
                  ? "WAITING_REVIEW"
                  : "UNCHECKED"
            }
          >
            {selected?.adoption_status === "adopted"
              ? "人工已采纳"
              : selected?.adoption_status === "candidate"
                ? "待人工验收"
                : selected?.adoption_status === "draft" ? "草稿" : "历史提交"}
          </Badge>
          <span>
            提交 <code>{selected?.commit_sha?.slice(0, 12) || "暂无"}</code>
          </span>
          <span className="muted">{dateText(selected?.created_at)}</span>
          {!editable && <><LockOutlined /> 只读</>}
        </div>
        <Tabs
          activeKey={tab}
          onChange={(v) => set({ tab: v })}
          className="code-tabs"
          items={[
            {
              key: "files",
              label: "代码文件",
              children: (
                <LoadState
                  loading={source.isLoading}
                  error={source.error}
                  empty={!id}
                  onRetry={() => source.refetch()}
                >
                  <div className="commit-bar">
                    <BranchesOutlined />
                    <b>{selected?.submission_type === "manual_edit" ? "人工修改" : "AI 采集助手"}</b>
                    <span>
                      {selected?.submission_type === "repair"
                        ? "修复采集器"
                        : "接入采集器"}
                    </span>
                    <code className="commit-sha">
                      {selected?.commit_sha?.slice(0, 7)}
                    </code>
                  </div>
                  <div className="source-browser">
                    <aside className="file-tree">
                      <Input
                        aria-label="查找文件"
                        prefix={<SearchOutlined />}
                        placeholder="查找文件"
                        value={fileSearch}
                        onChange={(e) => setFileSearch(e.target.value)}
                      />
                      {[...groups].map(([dir, files]) => (
                        <div className="file-group" key={dir}>
                          <div className="folder-label" title={dir}>
                            <FolderOutlined /> {dir}
                          </div>
                          {files.map((f) => (
                            <button
                              key={f}
                              className={`file-node ${source.data?.path === f ? "selected" : ""}`}
                              title={f}
                              disabled={saving}
                              onClick={() => {
                                set({ file: f });
                                setRaw(false);
                                setEditing(false);
                              }}
                            >
                              <FileOutlined />
                              <span>{f.split("/").at(-1)}</span>
                            </button>
                          ))}
                        </div>
                      ))}
                    </aside>
                    <section className="source-pane">
                      <div className="source-toolbar">
                        <span>
                          <b>{source.data?.path}</b> {editing ? <Tag>编辑中</Tag> : !editable ? <Tag>只读</Tag> : null}
                        </span>
                        <div>
                          {editable && (editing ? <>
                            <Button size="small" type="primary" loading={saving}
                              disabled={content === source.data?.content} onClick={save}>
                              保存新版本
                            </Button>
                            <Button size="small" aria-label="取消编辑" disabled={saving} onClick={() => {
                              setEditing(false);
                              setDrafts((all) => { const next = { ...all }; delete next[draftKey]; return next; });
                            }}>取消</Button>
                          </> : <Button size="small" aria-label="编辑代码" disabled={!source.data?.path}
                            onClick={() => { setEditing(true); setRaw(false); }}>编辑</Button>)}
                          <Button size="small" disabled={editing} onClick={() => setRaw(!raw)}>
                            {raw ? "代码" : "Raw"}
                          </Button>
                          <Button
                            size="small"
                            icon={<CopyOutlined />}
                            onClick={() =>
                              navigator.clipboard
                                .writeText(editing ? content : source.data?.content || "")
                                .then(() => message.success("已复制"))
                                .catch(() => message.error("复制失败"))
                            }
                          >
                            复制
                          </Button>
                          <Button
                            size="small"
                            onClick={() =>
                              saveBlob(
                                new Blob([source.data?.content || ""], {
                                  type: "text/plain",
                                }),
                                source.data?.path?.split("/").at(-1) ||
                                  "source.txt",
                              )
                            }
                          >
                            下载
                          </Button>
                          <Button
                            size="small"
                            onClick={() =>
                              set({
                                tab: "history",
                                file: source.data?.path || undefined,
                              })
                            }
                          >
                            历史
                          </Button>
                          <Button
                            size="small"
                            onClick={() => {
                              const p = new URLSearchParams(s.params);
                              if (id) p.set("submission", id);
                              if (source.data?.path)
                                p.set("file", source.data.path);
                              navigator.clipboard
                                .writeText(
                                  location.origin +
                                    contextLink(
                                      "/code",
                                      taskId,
                                      Object.fromEntries(p),
                                    ),
                                )
                                .then(() =>
                                  message.success("已复制固定版本链接"),
                                );
                            }}
                          >
                            固定版本
                          </Button>
                        </div>
                      </div>
                      <div className="source-search">
                        <Input.Search
                          size="small"
                          aria-label="搜索代码"
                          placeholder="搜索当前文件"
                          value={codeSearch}
                          onChange={(e) => setCodeSearch(e.target.value)}
                          onSearch={() =>
                            document
                              .querySelector(".code-line.matched")
                              ?.scrollIntoView({
                                block: "center",
                                behavior: "smooth",
                              })
                          }
                        />
                        <span className="muted">
                          {source.data?.content.split("\n").length || 0} 行
                        </span>
                      </div>
                      {editing ? <Input.TextArea aria-label="编辑采集代码"
                        className="source-editor" value={content} spellCheck={false} readOnly={saving} wrap="off"
                        onChange={(e) => setDrafts((all) => ({ ...all, [draftKey]: e.target.value }))}
                        onKeyDown={(e) => {
                          if (e.key === "Tab") {
                            e.preventDefault();
                            const target = e.currentTarget;
                            const start = target.selectionStart;
                            const end = target.selectionEnd;
                            setDrafts((all) => ({ ...all, [draftKey]: content.slice(0, start) + "    " + content.slice(end) }));
                            requestAnimationFrame(() => { target.selectionStart = target.selectionEnd = start + 4; });
                          }
                        }}
                      /> : <div className="source-scroll" tabIndex={0} role="region" aria-label="代码内容">
                        {raw ? (
                          <pre>{source.data?.content}</pre>
                        ) : (
                          source.data?.content.split("\n").map((line, i) => (
                            <div
                              key={i}
                              className={`code-line ${codeSearch && line.toLowerCase().includes(codeSearch.toLowerCase()) ? "matched" : ""}`}
                            >
                              <a
                                className="line-number"
                                href={`#L${i + 1}`}
                                id={`L${i + 1}`}
                              >
                                {i + 1}
                              </a>
                              <code>{highlighted(line) || " "}</code>
                            </div>
                          ))
                        )}
                      </div>}
                    </section>
                  </div>
                </LoadState>
              ),
            },
            {
              key: "history",
              label: "提交历史",
              children: (
                <Table
                  rowKey="submission_id"
                  dataSource={
                    file
                      ? versions.filter((v) => v.changed_files.includes(file))
                      : versions
                  }
                  pagination={false}
                  columns={[
                    {
                      title: "版本",
                      render: (_, v) => (
                        <Button
                          type="link"
                          onClick={() =>
                            set({
                              submission: v.submission_id,
                              tab: "files",
                              file: undefined,
                            })
                          }
                        >
                          <BranchesOutlined /> {v.commit_sha?.slice(0, 8)}
                        </Button>
                      ),
                    },
                    {
                      title: "状态",
                      render: (_, v) => (
                        <Badge
                          value={
                            v.adoption_status === "adopted"
                              ? "ADOPTED"
                              : "UNCHECKED"
                          }
                        >
                          {v.adoption_status === "adopted"
                            ? "已采纳"
                            : v.adoption_status === "candidate"
                              ? "当前候选"
                              : "历史版本"}
                        </Badge>
                      ),
                    },
                    {
                      title: "修改文件",
                      render: (_, v) => v.changed_files.join("、"),
                    },
                    {
                      title: "提交时间",
                      render: (_, v) => dateText(v.created_at),
                    },
                  ]}
                />
              ),
            },
            {
              key: "diff",
              label: "版本差异",
              children: (
                <>
                  <div className="diff-options">
                    <span>比较基准</span>
                    <Select
                      aria-label="差异基准"
                      value={compare || ""}
                      onChange={(v) => setCompare(v || undefined)}
                      options={[
                        { value: "", label: "生成时的基线" },
                        ...versions
                          .filter((v) => v.submission_id !== id)
                          .map((v) => ({
                            value: v.submission_id,
                            label: `${v.adoption_status === "adopted" ? "已采纳 · " : ""}${v.commit_sha?.slice(0, 8)}`,
                          })),
                      ]}
                      style={{ width: 230 }}
                    />
                    <span>→ {selected?.commit_sha?.slice(0, 8)}</span>
                  </div>
                  <LoadState loading={diff.isLoading} error={diff.error}>
                    {diff.data?.truncated && (
                      <Alert
                        type="warning"
                        title="差异超过展示上限，建议下载文件核对"
                      />
                    )}
                    <pre className="diff-code">
                      {diff.data?.diff
                        ? diff.data.diff.split("\n").map((l, i) => (
                            <div
                              key={i}
                              className={
                                l.startsWith("+")
                                  ? "added"
                                  : l.startsWith("-")
                                    ? "removed"
                                    : l.startsWith("@@")
                                      ? "hunk"
                                      : ""
                              }
                            >
                              {l || " "}
                            </div>
                          ))
                        : "两个版本没有差异"}
                    </pre>
                  </LoadState>
                </>
              ),
            },
            {
              key: "validation",
              label: "验证结果",
              children: validation ? (
                <>
                  <Table
                    pagination={false}
                    rowKey="key"
                    dataSource={[
                      { key: "pytest_status", name: "采集器测试" },
                      { key: "ruff_status", name: "代码规范" },
                      { key: "contract_status", name: "采集契约" },
                      { key: "business_status", name: "业务规则" },
                    ].map((check) => ({
                      ...check,
                      status: String(validation[check.key] || "NOT_RUN"),
                    }))}
                    columns={[
                      { title: "验证项目", dataIndex: "name" },
                      {
                        title: "结果",
                        render: (_, row) => <Badge value={row.status} />,
                      },
                    ]}
                  />
                  <pre className="validation-json">
                    {JSON.stringify(validation, null, 2)}
                  </pre>
                </>
              ) : (
                <Alert
                  type="warning"
                  showIcon
                  title="该版本尚无可用的自动验证记录"
                />
              ),
            },
          ]}
        />
      </LoadState>
      <CollectDrawer
        entries={launch && s.task ? [s.task] : []}
        pinned={id}
        onClose={() => setLaunch(false)}
      />
    </section>
  );
}
