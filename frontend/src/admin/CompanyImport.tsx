import { useState } from "react";
import { App, Button, Input, Modal, Table, Tag, Upload } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { post, requestId } from "./api";

interface ImportRow {
  line: number;
  company_name: string;
  entry_url: string;
  status: "VALID" | "DUPLICATE" | "INVALID";
  message: string;
}
interface Preview {
  rows: ImportRow[];
  total: number;
  valid: number;
  duplicates: number;
  invalid: number;
}

export function CompanyImport({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Preview>();
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState(requestId());
  const { message } = App.useApp();
  const cache = useQueryClient();
  const navigate = useNavigate();
  function change(value: string) {
    setText(value); setPreview(undefined); setKey(requestId());
  }
  async function inspect() {
    setBusy(true);
    try { setPreview(await post<Preview>("/api/v1/admin/company-import/preview", { text })); }
    catch (e) { message.error((e as Error).message); }
    finally { setBusy(false); }
  }
  async function submit() {
    setBusy(true);
    try {
      const result = await post<{ accepted_count: number; batch_id: string }>(
        "/api/v1/admin/company-import", { text, client_request_id: key },
      );
      await cache.invalidateQueries();
      message.success(`已创建 ${result.accepted_count} 个接入任务`);
      change(""); onClose(); navigate(`/companies?batch=${result.batch_id}`);
    } catch (e) { message.error((e as Error).message); }
    finally { setBusy(false); }
  }
  return <Modal title="批量添加公司" open={open} onCancel={onClose} width={900}
    footer={<><Button disabled={busy} onClick={onClose}>取消</Button>
      <Button loading={busy} disabled={!text.trim()} onClick={inspect}>预览名单</Button>
      <Button type="primary" loading={busy} disabled={!preview?.valid} onClick={submit}>开始 AI 接入</Button></>}>
    <Upload accept=".csv,.tsv,.txt" showUploadList={false} beforeUpload={async (file) => {
      if (file.size > 2_000_000) { message.error("文件超过 2 MB"); return false; }
      const bytes = await file.arrayBuffer();
      let value: string;
      try { value = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
      catch { value = new TextDecoder("gb18030").decode(bytes); }
      change(value); return false;
    }}><Button icon={<UploadOutlined />}>导入 CSV / 文本</Button></Upload>
    <Input.TextArea aria-label="批量公司名单" rows={8} value={text} disabled={busy}
      placeholder={"公司名称\t招聘链接\n莉莉丝游戏\thttps://lilithgames.jobs.feishu.cn/intern/"}
      onChange={(e) => change(e.target.value)} style={{ marginTop: 12 }} />
    {preview && <>
      <div style={{ margin: "16px 0" }}>
        共 {preview.total} 行 · 可接入 {preview.valid} · 重复 {preview.duplicates} · 无效 {preview.invalid}
      </div>
      <Table<ImportRow> size="small" rowKey="line" dataSource={preview.rows} pagination={{ pageSize: 10 }}
        scroll={{ x: 650 }} columns={[
          { title: "行", dataIndex: "line", width: 45 },
          { title: "公司", dataIndex: "company_name", width: 150 },
          { title: "招聘链接", dataIndex: "entry_url", ellipsis: true },
          { title: "校验", width: 160, render: (_, row) => <>
            <Tag color={row.status === "VALID" ? "green" : row.status === "DUPLICATE" ? "gold" : "red"}>
              {{ VALID: "可接入", DUPLICATE: "重复", INVALID: "无效" }[row.status]}</Tag>
            {row.message}
          </> },
        ]} />
    </>}
  </Modal>;
}
