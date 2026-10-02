import { useState } from "react";
import { App, Modal } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { post, request, requestId } from "./api";

interface CompanyRun {
  task_id: string;
  manual_run_id: string;
  code_revision: string;
  platform_key: string;
  total: number;
  issue_count: number;
  error_msg?: string;
}

export function useCompanyApproval() {
  const [busy, setBusy] = useState(false);
  const { message } = App.useApp();
  const cache = useQueryClient();
  async function approve(company: string) {
    setBusy(true);
    try {
      const summary = await request<{ runs: CompanyRun[] }>(
        `/api/v1/admin/companies/review-summary?company_name=${encodeURIComponent(company)}`,
      );
      if (!summary.runs.length) throw new Error("没有待审查的采集结果");
      if (summary.runs.some((r) => !r.total && !r.error_msg))
        throw new Error("请先在采集数据页选择空结果轮次，点击“处理空结果”记录核对结论");
      if (summary.runs.some((r) => !r.total || r.issue_count || r.error_msg))
        throw new Error("请先处理本公司的采集问题");
      Modal.confirm({
        title: `${company}审查通过`,
        content: `${summary.runs.length} 个入口，共 ${summary.runs.reduce((n, r) => n + r.total, 0)} 条记录`,
        okText: "公司通过",
        cancelText: "取消",
        onOk: async () => {
          await post("/api/v1/admin/companies/approve", {
            company_name: company,
            runs: summary.runs.map(({ task_id, manual_run_id, code_revision }) => ({
              task_id, manual_run_id, code_revision,
            })),
            client_request_id: requestId(),
          });
          await cache.invalidateQueries();
          message.success("公司审查通过");
        },
      });
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return { approve, busy };
}
