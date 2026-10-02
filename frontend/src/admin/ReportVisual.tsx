import { Alert, App, Button, Steps } from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  QuestionCircleOutlined,
  DownloadOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import type { FailureBundle, Report } from "../types";
import { download, saveBlob } from "./api";
import { Badge } from "./shared";
import { dateText, label, observationLabels, safeUrl } from "./model";
import { fieldLabels, nextAction, readableIssue } from "./labels";

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function count(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? String(value)
    : "待确认";
}
const checks = [
  ["compile_status", "语法检查"],
  ["pytest_status", "采集器测试"],
  ["ruff_status", "代码规范"],
  ["contract_status", "字段契约"],
  ["business_status", "业务规则"],
  ["live_status", "真实页面验证"],
] as const;
function EvidenceLinks({
  refs,
  evidence,
}: {
  refs: unknown;
  evidence: Record<string, unknown>[];
}) {
  const { message } = App.useApp();
  const list = Array.isArray(refs)
    ? refs.filter((r): r is string => typeof r === "string")
    : [];
  if (!list.length) return <span className="muted">暂无关联证据</span>;
  return (
    <div className="report-evidence-links">
      {list.map((ref) => {
        const file = evidence.find(
          (e) => e.relative_path === ref || e.evidence_id === ref,
        );
        const name =
          typeof file?.relative_path === "string"
            ? file.relative_path.split("/").at(-1) || "验证证据"
            : ref.split("/").at(-1) || ref;
        return file && typeof file.evidence_id === "string" ? (
          <Button
            key={ref}
            size="small"
            icon={<DownloadOutlined />}
            onClick={() =>
              download(
                `/api/v1/onboarding/evidence/${file.evidence_id}/download`,
                name,
              ).catch((e) => message.error(e.message))
            }
          >
            {name}
          </Button>
        ) : (
          <span key={ref} className="evidence-reference" title={ref}>
            {name} · 证据已关联
          </span>
        );
      })}
    </div>
  );
}
export function ReportOverview({
  report,
  evidence,
}: {
  report: Report;
  evidence: Record<string, unknown>[];
}) {
  const data = report.report_json;
  const validation = object(data.validation);
  const unresolved = Array.isArray(data.unresolved) ? data.unresolved : [];
  const passed = checks.filter(([key]) => validation[key] === "PASS").length;
  return (
    <div className="report-visual">
      <div className="report-outcome">
        <div>
          <span className="report-eyebrow">接入验证结论</span>
          <h3>
            {observationLabels[report.observation_code] || "接入情况待确认"}
          </h3>
          <p>{nextAction(report.next_action)}</p>
        </div>
        <div>
          <Badge value={report.technical_status} />
          <span className="muted">{dateText(report.created_at)}</span>
        </div>
      </div>
      <div className="report-path" aria-label="接入进度">
        <Steps
          size="small"
          items={[
            {
              title: "分析招聘入口",
              status:
                Array.isArray(data.evidence_refs) && data.evidence_refs.length
                  ? "finish"
                  : "wait",
            },
            {
              title: "生成采集器",
              status: data.candidate_commit ? "finish" : "wait",
            },
            {
              title: "自动验证",
              status:
                report.technical_status === "PASS"
                  ? "finish"
                  : report.technical_status === "FAIL"
                    ? "error"
                    : "wait",
            },
            { title: "人工采集与审查", status: "wait" },
          ]}
        />
      </div>
      <div className="report-stat-line">
        {[
          ["验证时发现岗位", data.list_count],
          ["匹配实习岗位", data.internship_count],
          ["有效验证样本", data.valid_record_count],
          ["样本发布时间缺失", data.missing_publish_time_count],
        ].map(([name, value]) => (
          <div key={String(name)}>
            <strong>{count(value)}</strong>
            <span>{String(name)}</span>
          </div>
        ))}
      </div>
      <div className="report-section-title">
        <h4>自动验证结果</h4>
        <span className="muted">
          {passed} / {checks.length} 项通过
        </span>
      </div>
      <div className="report-check-grid">
        {checks.map(([key, name]) => {
          const status =
            typeof validation[key] === "string"
              ? String(validation[key])
              : "NOT_RUN";
          return (
            <div
              key={key}
              className={`report-check ${status === "PASS" ? "passed" : status === "FAIL" ? "failed" : "unknown"}`}
            >
              {status === "PASS" ? (
                <CheckCircleOutlined />
              ) : status === "FAIL" ? (
                <CloseCircleOutlined />
              ) : (
                <QuestionCircleOutlined />
              )}
              <span>{name}</span>
              <Badge value={status} />
            </div>
          );
        })}
      </div>
      <div className="report-facts">
        {[
          ["岗位列表", data.list_found],
          ["岗位详情", data.detail_found],
          ["分页验证", data.pagination_verified],
        ].map(([name, value]) => (
          <div key={String(name)}>
            <span>{String(name)}</span>
            <Badge
              value={
                value === true ? "PASS" : value === false ? "FAIL" : "NOT_RUN"
              }
            >
              {value === true
                ? "已确认"
                : value === false
                  ? "未确认"
                  : "待确认"}
            </Badge>
          </div>
        ))}
      </div>
      <div className="report-decision">
        <h4>
          {report.adoptable ? "已满足候选接入条件" : "当前需要处理的问题"}
        </h4>
        <p>
          {report.adoptable
            ? "自动验证通过后仍需人工运行采集、核对岗位，审查通过才采纳代码。"
            : "请按验证结果与证据处理问题，当前不会直接采纳代码。"}
        </p>
        {unresolved.map((issue, i) => (
          <p key={i} className="error-text">
            {readableIssue(
              typeof issue === "object"
                ? object(issue).message ||
                    object(issue).description ||
                    "存在待确认项"
                : issue,
            )}
          </p>
        ))}
      </div>
      <div className="report-section-title">
        <h4>验证依据</h4>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          onClick={() =>
            saveBlob(
              new Blob([JSON.stringify(data, null, 2)], {
                type: "application/json",
              }),
              "接入汇报原始记录.json",
            )
          }
        >
          下载原始记录
        </Button>
      </div>
      <EvidenceLinks refs={data.evidence_refs} evidence={evidence} />
      <div className="report-footnote">
        {data.simulated === true ? "本轮为模拟验证" : "本轮记录来自接入验证"} ·
        岗位数量是验证时观测结果，正式采集结果在采集数据页查看。
        {safeUrl(String(data.entry_url || "")) && (
          <a
            href={safeUrl(String(data.entry_url))}
            target="_blank"
            rel="noreferrer"
          >
            <LinkOutlined /> 招聘官网
          </a>
        )}
      </div>
    </div>
  );
}
export function FailureOverview({
  failure,
  evidence,
}: {
  failure: FailureBundle;
  evidence: Record<string, unknown>[];
}) {
  const data = failure.bundle;
  const issues = Array.isArray(data.field_issues)
    ? data.field_issues.map(object)
    : [];
  return (
    <div className="failure-visual">
      <div className="failure-summary">
        <h4>问题说明</h4>
        <p>
          {readableIssue(
            data.issue_summary || "请根据以下验证证据和字段差异定位问题。",
          )}
        </p>
      </div>
      {issues.map((issue, i) => (
        <section className="issue-compare" key={i}>
          <div className="issue-compare-title">
            <h4>{fieldLabels[String(issue.field)] || "待核对字段"}</h4>
            <Badge
              value={
                issue.code_fixable === true
                  ? "PASS"
                  : issue.code_fixable === false
                    ? "BLOCKED"
                    : "NOT_RUN"
              }
            >
              {issue.code_fixable === true
                ? "可通过代码修复"
                : issue.code_fixable === false
                  ? "需要人工处理"
                  : "等待诊断"}
            </Badge>
          </div>
          <p>{readableIssue(issue.description)}</p>
          <div className="issue-compare-columns">
            <div>
              <span>预期结果</span>
              <p>{readableIssue(issue.expected)}</p>
            </div>
            <div>
              <span>实际结果</span>
              <p>{readableIssue(issue.actual)}</p>
            </div>
          </div>
          <EvidenceLinks refs={issue.evidence_refs} evidence={evidence} />
        </section>
      ))}
      {/非\s*json|non.?json|json\s*解析/i.test(JSON.stringify(data)) && (
        <Alert
          type="info"
          showIcon
          title="非 JSON 响应本身不代表异常"
          description="招聘入口返回 HTML、接口返回 XML 或文本都可能正常。应先核对接口地址、声明的响应格式和状态码；如果采集器错误地按 JSON 处理页面，再修复请求或解析逻辑。"
        />
      )}
      {!!Array.isArray(data.sample_decisions) &&
        data.sample_decisions.length > 0 && (
          <div className="report-decision">
            <h4>人工核对记录</h4>
            {data.sample_decisions.map((raw, i) => {
              const item = object(raw);
              return (
                <p key={i}>
                  第 {String(item.sample_index || "?")} 条岗位 ·{" "}
                  {label(String(item.status || "UNCHECKED"))}{" "}
                  {readableIssue(item.note || "")}
                </p>
              );
            })}
          </div>
        )}
      <div className="report-section-title">
        <h4>关联证据</h4>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          onClick={() =>
            saveBlob(
              new Blob([JSON.stringify(data, null, 2)], {
                type: "application/json",
              }),
              "纠错依据原始记录.json",
            )
          }
        >
          下载原始记录
        </Button>
      </div>
      <EvidenceLinks refs={data.evidence_refs} evidence={evidence} />
    </div>
  );
}
