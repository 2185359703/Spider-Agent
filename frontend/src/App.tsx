import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
} from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import {
  ApartmentOutlined,
  DatabaseOutlined,
  CodeOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  SettingOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Avatar, Tooltip } from "antd";
import { actorApi, submissionsApi, tasksApi } from "./admin/api";
import { WorkspaceContext } from "./admin/shared";
const CompaniesPage = lazy(() =>
  import("./admin/Companies").then((m) => ({ default: m.CompaniesPage })),
);
const DataPage = lazy(() =>
  import("./admin/Data").then((m) => ({ default: m.DataPage })),
);
const ReviewPage = lazy(() =>
  import("./admin/Data").then((m) => ({ default: m.ReviewPage })),
);
const CodePage = lazy(() =>
  import("./admin/Code").then((m) => ({ default: m.CodePage })),
);
const LogsPage = lazy(() =>
  import("./admin/Logs").then((m) => ({ default: m.LogsPage })),
);
const StatusPage = lazy(() =>
  import("./admin/Status").then((m) => ({ default: m.StatusPage })),
);
const SettingsPage = lazy(() =>
  import("./admin/Status").then((m) => ({ default: m.SettingsPage })),
);
const modules = [
  ["/companies", "公司采集", ApartmentOutlined],
  ["/data", "采集数据", DatabaseOutlined],
  ["/code", "采集代码", CodeOutlined],
  ["/status", "任务状态", ClockCircleOutlined],
  ["/logs", "AI 工作日志", FileTextOutlined],
  ["/settings", "系统设置", SettingOutlined],
] as const;
function Admin() {
  const tasks = useQuery({
    queryKey: ["tasks"],
    queryFn: tasksApi,
    refetchInterval: 5000,
  });
  const submissions = useQuery({
    queryKey: ["submissions"],
    queryFn: submissionsApi,
    refetchInterval: 5000,
  });
  const actor = useQuery({ queryKey: ["actor"], queryFn: actorApi });
  const role = actor.data?.role || "viewer";
  return (
    <WorkspaceContext.Provider
      value={{
        tasks: tasks.data || [],
        submissions: submissions.data || [],
        role,
        loading: tasks.isLoading,
        error: tasks.error,
      }}
    >
      <header className="app-header">
        <NavLink to="/companies" className="app-brand">
          招聘采集管理
        </NavLink>
        <div className="header-account">
          <Tooltip title={actor.data?.user_id || "正在连接后台"}>
            <Avatar size={28} icon={<UserOutlined />} />
          </Tooltip>
          <span>
            {(
              {
                admin: "管理员",
                operator: "操作员",
                reviewer: "审查员",
                viewer: "只读用户",
              } as Record<string, string>
            )[role] || role}
          </span>
        </div>
      </header>
      <div className="app-layout">
        <aside className="app-sidebar">
          <nav aria-label="模块导航">
            {modules.map(([path, name, Icon]) => (
              <NavLink
                aria-label={name}
                key={path}
                to={path}
                className={({ isActive }) =>
                  isActive ? "nav-item active" : "nav-item"
                }
              >
                <Icon />
                <span>{name}</span>
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <span className="connection-mark" />
            人工触发 · 版本可追溯
          </div>
        </aside>
        <main className="app-main">
          <Suspense fallback={<div className="load-state">正在打开页面…</div>}>
            <Routes>
              <Route path="/companies" element={<CompaniesPage />} />
              <Route path="/data" element={<DataPage />} />
              <Route path="/data/review" element={<ReviewPage />} />
              <Route path="/code" element={<CodePage />} />
              <Route path="/status" element={<StatusPage />} />
              <Route path="/logs" element={<LogsPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/companies" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </WorkspaceContext.Provider>
  );
}
export default function App() {
  return (
    <BrowserRouter>
      <Admin />
    </BrowserRouter>
  );
}
