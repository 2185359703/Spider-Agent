import { Drawer, Table } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { request } from "./api";
import { contextLink, label } from "./model";
import { nextAction } from "./labels";
import { Badge, LoadState, Status } from "./shared";

interface Entry {
  task_id: string; company_name: string; entry_url: string; status: string;
  observation_code?: string; technical_status: string; commit_sha?: string;
  next_action?: string; record_count?: number | null; quality_status?: string | null;
  quality_finding_count?: number;
}
interface Report { summary: { companies: number; entries: number; finished: number }; entries: Entry[] }

export function BatchReport({ batchId }: { batchId: string | null }) {
  const [params, setParams] = useSearchParams();
  const query = useQuery({ queryKey: ["batch-report", batchId], enabled: !!batchId,
    queryFn: () => request<Report>(`/api/v1/admin/batches/${batchId}/report`), refetchInterval: 3000 });
  return <Drawer title="批次接入汇报" open={!!batchId} width={1000} onClose={() => {
    const next = new URLSearchParams(params); next.delete("batch"); setParams(next);
  }}>
    <LoadState loading={query.isLoading} error={query.error}>
      {query.data && <>
        <div style={{ marginBottom: 20 }}>{query.data.summary.companies} 家公司 · {query.data.summary.entries} 个入口 · 已完成 {query.data.summary.finished}</div>
        <Table<Entry> rowKey="task_id" dataSource={query.data.entries} pagination={{ pageSize: 20 }} columns={[
          { title: "公司", dataIndex: "company_name", width: 140 },
          { title: "状态", width: 125, render: (_, row) => <Status value={row.status} /> },
          { title: "接入结论", render: (_, row) => row.observation_code ? label(row.observation_code) : "待汇报" },
          { title: "验证", width: 90, render: (_, row) => <Badge value={row.technical_status} /> },
          { title: "采集质量", width: 135, render: (_, row) => {
            const quality = row.quality_status;
            const label = quality === "PASS" ? "通过" : quality === "FAIL" ? "有问题" : quality === "PARTIAL" ? "待核对" : "未运行";
            const count = row.record_count == null ? "—" : `${row.record_count} 条`;
            const findings = row.quality_finding_count ? ` · ${row.quality_finding_count} 项` : "";
            return `${count} · ${label}${findings}`;
          } },
          { title: "代码版本", width: 110, render: (_, row) => row.commit_sha ? <Link to={contextLink("/code", row.task_id)}>{row.commit_sha.slice(0, 8)}</Link> : "—" },
          { title: "下一步", render: (_, row) => row.next_action ? nextAction(row.next_action) : "—" },
          { title: "操作", render: (_, row) => <Link to={contextLink("/status", row.task_id)}>查看详情</Link> },
        ]} />
      </>}
    </LoadState>
  </Drawer>;
}
