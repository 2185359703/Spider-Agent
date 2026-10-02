import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Empty, Select, Spin, Tag } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { Link, useSearchParams } from "react-router-dom";
import type { Task, GlobalSubmission } from "../types";
import { bundleApi } from "./api";
import {
  companyName,
  contextLink,
  entryName,
  groupCompanies,
  label,
  tone,
} from "./model";
export interface Workspace {
  tasks: Task[];
  submissions: GlobalSubmission[];
  role: string;
  loading: boolean;
  error: Error | null;
}
export const WorkspaceContext = createContext<Workspace>({
  tasks: [],
  submissions: [],
  role: "viewer",
  loading: true,
  error: null,
});
export const useWorkspace = () => useContext(WorkspaceContext);
export function useSelection() {
  const workspace = useWorkspace();
  const [params, setParams] = useSearchParams();
  const companies = groupCompanies(workspace.tasks);
  const task = params.has("task")
    ? workspace.tasks.find((t) => t.task_id === params.get("task"))
    : companies[0]?.entries[0];
  useEffect(() => {
    if (task && !params.has("task")) {
      const next = new URLSearchParams(params);
      next.set("task", task.task_id);
      setParams(next, { replace: true });
    }
  }, [task, params, setParams]);
  const setTask = (id: string) => {
    const n = new URLSearchParams(params);
    n.set("task", id);
    ["submission", "file", "run", "index", "tab"].forEach((k) => n.delete(k));
    setParams(n);
  };
  const bundle = useQuery({
    queryKey: ["bundle", task?.task_id],
    queryFn: () => bundleApi(task!.task_id),
    enabled: !!task,
    refetchInterval: 5000,
  });
  return { ...workspace, companies, task, setTask, bundle, params, setParams };
}
export function SelectionBar({
  extra,
  selection,
}: {
  extra?: ReactNode;
  selection: ReturnType<typeof useSelection>;
}) {
  const { companies, task, setTask } = selection;
  const c = companies.find((c) => c.name === (task ? companyName(task) : ""));
  return (
    <div className="selection-bar">
      <Select
        aria-label="选择公司"
        value={c?.name}
        placeholder="选择公司"
        className="company-select"
        onChange={(v) => {
          const t = companies.find((c) => c.name === v)?.entries[0];
          if (t) setTask(t.task_id);
        }}
        options={companies.map((c) => ({ label: c.name, value: c.name }))}
      />
      <Select
        aria-label="选择招聘入口"
        value={task?.task_id}
        placeholder="选择招聘入口"
        className="entry-select"
        onChange={setTask}
        options={(c?.entries || []).map((t) => ({
          value: t.task_id,
          label: entryName(t),
        }))}
      />
      {extra}
    </div>
  );
}
export function PageHeading({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="heading-actions">{actions}</div>
    </div>
  );
}
export function Status({
  value,
  children,
}: {
  value?: string | null;
  children?: ReactNode;
}) {
  return (
    <span className={`status-dot ${tone(value)}`}>
      <i />
      {children || label(value)}
    </span>
  );
}
export function Badge({
  value,
  children,
}: {
  value?: string | null;
  children?: ReactNode;
}) {
  return (
    <Tag className={`quiet-tag ${tone(value)}`}>{children || label(value)}</Tag>
  );
}
export function LoadState({
  loading,
  error,
  empty,
  children,
  onRetry,
}: {
  loading?: boolean;
  error?: Error | null;
  empty?: boolean;
  children?: ReactNode;
  onRetry?: () => void;
}) {
  if (error)
    return (
      <div className="load-state">
        <Alert
          type="error"
          showIcon
          title="暂时无法读取数据"
          description={error.message}
          action={
            onRetry && (
              <Button onClick={onRetry} icon={<ReloadOutlined />}>
                重试
              </Button>
            )
          }
        />
      </div>
    );
  if (loading)
    return (
      <div className="load-state">
        <Spin description="正在读取…">
          <div style={{ height: 60 }} />
        </Spin>
      </div>
    );
  if (empty)
    return (
      <div className="load-state">
        <Empty description="暂无记录">
          <Link to="/companies">前往公司采集</Link>
        </Empty>
      </div>
    );
  return <>{children}</>;
}
export function ContextLinks({ task }: { task?: Task }) {
  return (
    <div className="context-links">
      <Link to={contextLink("/code", task?.task_id)}>查看代码 ↗</Link>
      <Link to={contextLink("/logs", task?.task_id)}>查看工作日志 ↗</Link>
    </div>
  );
}
