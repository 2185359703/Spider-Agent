export const actionLabels: Record<string, string> = {
  WAIT_MANUAL_RUN: "等待人工启动采集",
  WAIT_REVIEW: "等待人工审查",
  CREATE_CANDIDATE: "生成候选代码版本",
  REQUEST_INPUT: "需要补充信息或处理问题",
  CLOSE_WITH_REPORT: "汇报完成",
  AUTO_REPAIR: "进入 AI 诊断与修复",
  RESUME_WORKFLOW: "恢复任务执行",
  RETRY_WORKFLOW: "重新执行任务",
  CLOSE: "结束本轮任务",
};
export const eventLabels: Record<string, string> = {
  TASK_CREATED: "接入任务已创建",
  SKILLED_CANDIDATE_FINALIZED: "候选代码与验证结果已确认",
  MANUAL_RUN_REGISTERED: "人工采集结果已登记",
  COLLECTION_REQUESTED: "人工采集已启动",
  REVIEW_SUBMITTED: "人工审查已提交",
  SPEC_REVISED: "接入规范已更新",
  PAUSE_REQUESTED: "已请求暂停任务",
  CANCEL_REQUESTED: "已请求停止任务",
  RESUME_REQUESTED: "已请求恢复任务",
  RETRY_REQUESTED: "已请求重试任务",
  CANDIDATE_COMMITTED: "候选代码已提交",
  REPAIR_COMPLETED: "代码修复已完成",
};
export const failureLabels: Record<string, string> = {
  MANUAL_RESULT_MISMATCH: "人工核对发现数据不一致",
  AUTO_VALIDATION_FAILED: "自动验证未通过",
  PAGINATION_ERROR: "分页采集有问题",
  PARSING_ERROR: "岗位解析有问题",
  FIELD_MAPPING_ERROR: "字段映射有问题",
  ACCESS_RESTRICTED: "网站访问受限",
  EVIDENCE_INSUFFICIENT: "分析证据不足",
  POLICY_VIOLATION: "修改超出允许范围",
  CODE_OR_CONFIG: "代码或配置需要修复",
  ENVIRONMENT_OR_ACCESS: "运行环境或访问条件需要处理",
  TOOL_UNAVAILABLE: "分析工具不可用",
  REQUIRES_INPUT: "需要人工补充信息",
};
export const fieldLabels: Record<string, string> = {
  source_id: "原始岗位编号",
  title: "岗位标题",
  source_url: "岗位官网链接",
  location: "工作地点",
  description: "岗位描述",
  requirements: "岗位要求",
  publish_time: "发布时间",
  employment_type: "用工类型",
  internship_filter: "实习岗位筛选",
  pagination: "分页规则",
  other: "其他问题",
};
export const nextAction = (value?: string | null) =>
  value ? actionLabels[value] || "需要进一步确认" : "暂无后续动作";
export const eventLabel = (value: string) =>
  eventLabels[value] || "其他任务事件";
export const failureLabel = (value: string) =>
  failureLabels[value] || "其他采集问题";
export function readableIssue(value: unknown) {
  if (value == null) return "未提供";
  return String(value).replace(
    /\b(source_id|source_url|publish_time|requirements|description|title|record_count|endpoint)\b/g,
    (key) =>
      fieldLabels[key] ||
      (
        { record_count: "记录数量", endpoint: "接口地址" } as Record<
          string,
          string
        >
      )[key] ||
      key,
  );
}
