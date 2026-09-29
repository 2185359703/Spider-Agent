# 数据契约与 API

本文档把 [开发设计](开发设计.md) 中的状态、字段和接口落成可实现的契约。`PlatformSpec` 的完整规范见 [PlatformSpec v1](PlatformSpec-v1.md)，实际 Pydantic 实现位于 `auto_spider/schemas.py`。

## 1. 标识和版本

| 标识 | 生成方 | 用途 | 稳定性 |
| --- | --- | --- | --- |
| `task_id` | API | 一次公司或入口接入任务 | 永久不变 |
| `run_id` | 工作流 | 一次分析、验证、人工运行或修复轮次 | 永久不变 |
| `platform_key` | API/分析器 | 采集阶段业务键 | 交付后不随意改名 |
| `spec_version` | 规范服务 | PlatformSpec 版本 | 单调递增 |
| `policy_version` | 规则服务 | 汇报规则版本 | 绑定报告，不覆盖历史 |
| `code_revision` | Git | 采集器代码版本 | commit SHA |
| `environment_fingerprint` | runner | 镜像、工具和依赖指纹 | 每次运行记录 |
| `prompt_bundle_hash` | OpenHands gateway | 提示词和任务模板版本 | 每次 AI 运行记录 |

所有对外 ID 使用 UUIDv7 或 32 字符小写任务号。数据库内部自增主键不暴露给前端。

## 2. Pydantic 数据模型

### 2.1 创建任务

~~~python
from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


class IntakeItem(BaseModel):
    entry_url: AnyHttpUrl
    platform_name: str | None = Field(default=None, max_length=255)
    platform_key: str | None = Field(default=None, max_length=128)
    repository_key: str = Field(default="aicoding-auto_spider", max_length=128)
    policy_version: str = Field(default="report-v1", max_length=64)

    @field_validator("platform_key")
    @classmethod
    def validate_platform_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower().replace("-", "_")
        if not normalized or not normalized.replace("_", "").isalnum():
            raise ValueError("platform_key 只能包含小写字母、数字和下划线")
        return normalized


class CreateTaskRequest(BaseModel):
    items: list[IntakeItem] = Field(min_length=1, max_length=100)
    analysis_profile: str = Field(default="internship-http-v1", max_length=64)
    dry_run: bool = True
    client_request_id: str = Field(min_length=8, max_length=128)


class CreateTaskResponse(BaseModel):
    batch_id: str
    task_ids: list[str]
    accepted_count: int
    rejected_count: int
    status: str
~~~

API 只接受 HTTP(S) URL；网络出口、域名 allowlist 和禁止访问网段由 runner 执行，不能只依赖 Pydantic。

### 2.2 PlatformSpec

~~~python
class PaginationSpec(BaseModel):
    type: str
    page_param: str | None = None
    size_param: str | None = None
    cursor_param: str | None = None
    next_path: str | None = None
    page_size: int | None = Field(default=None, ge=1, le=200)
    max_pages: int = Field(default=20, ge=1, le=1000)


class EndpointSpec(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    items_path: str | None = None
    pagination: PaginationSpec | None = None


class FieldMapping(BaseModel):
    source_id: str
    title: str
    source_url: str | None = None
    location: str | None = None
    description: str | None = None
    requirements: str | None = None
    publish_time: str | None = None


class InternshipRule(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    include_paths: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    must_match: bool = True
    evidence_refs: list[str] = Field(default_factory=list)


class PlatformSpec(BaseModel):
    platform_key: str
    source_name: str
    entry_url: str
    platform_id: int | None = None
    entity_id: int | None = None
    crawl_version: str = "v1.0.0"
    adapter_type: str
    list: EndpointSpec | None = None
    detail: EndpointSpec | None = None
    field_mapping: FieldMapping
    internship_rule: InternshipRule
    evidence_refs: list[str] = Field(default_factory=list)
~~~

代码中禁止给 `platform_id` 和 `entity_id` 设置默认数字。两个字段只有在业务映射配置已经确认时才从外部映射注入。

### 2.3 采集记录

~~~python
class RawCollectedJob(BaseModel):
    platform_key: str
    platform_id: int | None = None
    entity_id: int | None = None
    source_id: str
    source_name: str
    source_url: str
    title: str
    location: str | None = None
    description: str = ""
    requirements: str = ""
    publish_time: int | None = None
    raw_payload_ref: str
    crawl_version: str = "v1.0.0"
    crawl_status: int
    validation: dict
~~~

在 adapter 边界做最低校验：source_id、source_name、source_url、title 必须存在；描述和要求可分别为空，但岗位不能完全没有正文。失败记录用独立状态和 `error` 对象，不伪造岗位。

### 2.4 报告和人工审查

~~~python
from enum import StrEnum


class ObservationCode(StrEnum):
    INTERNSHIPS_FOUND = "INTERNSHIPS_FOUND"
    NO_INTERNSHIPS_OBSERVED = "NO_INTERNSHIPS_OBSERVED"
    NO_JOBS_OBSERVED = "NO_JOBS_OBSERVED"
    NO_JOB_LIST_FOUND = "NO_JOB_LIST_FOUND"
    NON_LISTING_RECRUITMENT = "NON_LISTING_RECRUITMENT"
    DETAIL_UNAVAILABLE = "DETAIL_UNAVAILABLE"
    ACCESS_RESTRICTED = "ACCESS_RESTRICTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class TechnicalStatus(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class NextAction(StrEnum):
    CREATE_CANDIDATE = "CREATE_CANDIDATE"
    AUTO_REPAIR = "AUTO_REPAIR"
    REQUEST_INPUT = "REQUEST_INPUT"
    WAIT_MANUAL_RUN = "WAIT_MANUAL_RUN"
    WAIT_REVIEW = "WAIT_REVIEW"
    CLOSE_WITH_REPORT = "CLOSE_WITH_REPORT"


class ValidationSummary(BaseModel):
    structural: TechnicalStatus
    code: TechnicalStatus
    contract: TechnicalStatus
    business: TechnicalStatus
    pytest_ref: str | None = None
    ruff_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class OnboardingReport(BaseModel):
    task_id: str
    run_id: str
    report_version: str
    observation_code: ObservationCode
    technical_status: TechnicalStatus
    next_action: NextAction
    adoptable: bool
    scope_text: str
    list_found: bool | None = None
    detail_found: bool | None = None
    pagination_verified: bool | None = None
    list_count: int | None = None
    internship_count: int | None = None
    valid_record_count: int | None = None
    missing_publish_time_count: int | None = None
    validation: ValidationSummary
    unresolved: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
~~~

`list_count=0` 代表在已知范围得到空列表；范围未知时使用 `null`。`internship_count=0` 必须有列表和筛选范围证据，不能由访问失败推出。

人工审查：

~~~python
class ReviewStatus(StrEnum):
    PASS = "PASS"
    CODE_FIX_REQUIRED = "CODE_FIX_REQUIRED"
    EXTERNAL_BLOCKED = "EXTERNAL_BLOCKED"
    BUSINESS_RULE_REVIEW = "BUSINESS_RULE_REVIEW"
    REJECT = "REJECT"


class ManualReviewRequest(BaseModel):
    review_status: ReviewStatus
    code_revision: str
    sample_count: int = Field(ge=0)
    issue_summary: str | None = Field(default=None, max_length=10000)
    evidence_refs: list[str] = Field(default_factory=list)
    client_request_id: str = Field(min_length=8, max_length=128)
~~~

## 3. 数据库契约

### 3.1 通用约定

表都包含 `created_at`、`updated_at`；状态变更另写事件，不靠更新时间推断。JSON 字段只存受 schema 校验的结构化对象，网页正文和日志在文件系统。

### 3.2 表关系

~~~mermaid
erDiagram
    ONBOARDING_BATCH ||--o{ ONBOARDING_TASK : contains
    ONBOARDING_TASK ||--o{ WORKFLOW_RUN : has
    ONBOARDING_TASK ||--o{ PLATFORM_SPEC : versions
    WORKFLOW_RUN ||--o{ VALIDATION_RUN : produces
    WORKFLOW_RUN ||--o{ ONBOARDING_REPORT : produces
    ONBOARDING_TASK ||--o{ MANUAL_RUN : executes
    MANUAL_RUN ||--o{ MANUAL_REVIEW : receives
    MANUAL_REVIEW ||--o{ FAILURE_BUNDLE : may_create
    FAILURE_BUNDLE ||--o{ REPAIR_RUN : triggers
    WORKFLOW_RUN ||--o{ CODE_SUBMISSION : creates
    WORKFLOW_RUN ||--o{ EVIDENCE_FILE : references
~~~

建议表：

- `onboarding_batches`：批量输入和汇总。
- `onboarding_tasks`：单公司任务、入口、平台键、两个空 ID。
- `workflow_runs`：分析、验证、修复和人工运行轮次。
- `platform_specs`：不可变规范版本。
- `validation_runs`：工具退出码、测试统计和业务规则结果。
- `onboarding_reports`：不可变报告正文和动作。
- `manual_runs`：人工执行的代码版本、参数和运行包。
- `manual_reviews`：审查结论和意见。
- `failure_bundles`：纠错包和可修复性判定。
- `repair_runs`：AI 修复尝试、范围和回归结果。
- `code_submissions`：分支、commit、文件清单和采纳状态。
- `evidence_files`：证据路径、SHA-256 和脱敏状态。
- `policy_versions`：汇报规则及发布审计。
- `workflow_events`：状态变更和幂等键。

### 3.3 关键唯一约束

| 约束 | 用途 |
| --- | --- |
| `uk_batch_request(client_request_id)` | 重复请求不重复建任务 |
| `uk_task_id(task_id)` | 对外任务号 |
| `uk_task_platform_revision(task_id, spec_version)` | 规范不可重复 |
| `uk_run_id(run_id)` | 轮次幂等 |
| `uk_submission(run_id, commit_sha)` | 不重复记候选提交 |
| `uk_event(idempotency_key)` | Celery 重投不重复推进 |
| `uk_policy_version(name, version)` | 规则版本不可覆盖 |

任务删除只做逻辑删除；报告和审查历史不物理删除。证据清理需要管理员确认、保留 manifest，并写一条 `EVIDENCE_PURGED` 事件。

## 4. API 约定

### 4.1 通用响应

所有 `/api/v1` 接口使用会话或内部 OIDC，错误响应统一：

~~~json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "任务不存在或当前用户无权访问",
    "request_id": "req-20260928-0001",
    "details": {}
  }
}
~~~

分页使用 `cursor` 和 `limit`；下载接口返回短期签名地址或流式响应，不返回服务器绝对路径。

角色权限：`viewer` 只能读取；`operator` 可创建接入、登记人工运行、恢复任务和触发修复；`reviewer` 可提交人工审查；`admin` 拥有全部权限。开发环境 `AUTH_MODE=dev` 默认使用 `dev-user/admin`，生产或联调环境设置 `AUTH_MODE=header` 后必须提供 `X-User-Id` 和 `X-User-Role`。

### 4.2 接口表

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/v1/onboarding/batches` | 批量创建 URL 任务 |
| GET | `/api/v1/onboarding/batches` | 查询最近批次 |
| GET | `/api/v1/me` | 查询当前用户和角色 |
| GET | `/api/v1/system/health` | 查询数据库、Redis 和 Agent Server 健康状态 |
| GET | `/api/v1/onboarding/batches/{batch_id}` | 查询批量汇总 |
| GET | `/api/v1/onboarding/submissions` | 查询全局候选提交 |
| GET | `/api/v1/onboarding/samples` | 查询全局人工运行样本 |
| GET | `/api/v1/onboarding/tasks/{task_id}` | 查询任务和下一步 |
| GET | `/api/v1/onboarding/tasks/{task_id}/timeline` | 查询工作流事件和轮次 |
| POST | `/api/v1/onboarding/tasks/{task_id}/resume` | 补充信息并恢复 |
| POST | `/api/v1/onboarding/tasks/{task_id}/retry` | 重新执行失败轮次 |
| GET | `/api/v1/onboarding/tasks/{task_id}/specs` | 查询规范版本 |
| GET | `/api/v1/onboarding/tasks/{task_id}/reports/latest` | 查询当前报告 |
| GET | `/api/v1/onboarding/tasks/{task_id}/submissions` | 查询候选提交 |
| POST | `/api/v1/onboarding/tasks/{task_id}/manual-runs` | 登记人工运行 |
| GET | `/api/v1/onboarding/tasks/{task_id}/manual-runs` | 查询人工运行历史 |
| GET | `/api/v1/onboarding/tasks/{task_id}/samples` | 查询任务采集样本 |
| POST | `/api/v1/onboarding/tasks/{task_id}/reviews` | 提交人工审查 |
| GET | `/api/v1/onboarding/tasks/{task_id}/reviews` | 查询审查历史 |
| POST | `/api/v1/onboarding/tasks/{task_id}/repair` | 触发修复 |
| GET | `/api/v1/onboarding/tasks/{task_id}/repairs` | 查询修复轮次 |
| GET | `/api/v1/onboarding/tasks/{task_id}/failures` | 查询失败纠错包 |
| GET | `/api/v1/onboarding/tasks/{task_id}/evidence` | 查询证据索引 |
| GET | `/api/v1/onboarding/tasks/{task_id}/submissions/{submission_id}/diff` | 获取候选差异 |
| GET | `/api/v1/onboarding/evidence/{evidence_id}/download` | 下载单个证据文件 |
| GET | `/api/v1/system/repositories` | 查询三个仓库边界和本地状态 |
| GET | `/api/v1/policies` | 查询规则版本 |
| POST | `/api/v1/policies/preview` | 用样本预览规则 |
| POST | `/api/v1/policies` | 发布规则，管理员 |

`resume`、`retry`、`repair` 和审查提交都必须有 `client_request_id`。重复请求返回原结果，不重复创建 worker 任务。

### 4.3 创建任务示例

~~~json
POST /api/v1/onboarding/batches

{
  "items": [
    {
      "entry_url": "https://careers.example.com/internships",
      "platform_name": "Example Company",
      "platform_key": "example_company"
    }
  ],
  "analysis_profile": "internship-http-v1",
  "dry_run": true,
  "client_request_id": "batch-20260928-0001"
}
~~~

响应必须返回 `batch_id`、创建/拒绝数量、每项 task_id 和当前状态。平台名称和 key 可以为空，由分析器提出候选，但不能覆盖人工已经确认的值。

### 4.4 人工运行和审查

人工运行接口登记 `code_revision`、运行参数、环境指纹、manifest、开始/结束时间和计数。服务端先校验 commit 是任务交付版本、manifest 存在且已脱敏，再进入 `WAITING_MANUAL_REVIEW`。

审查提交至少携带 `review_status`、`code_revision`、样本数、问题摘要和证据引用。发现问题时可附带 `field_issues`，逐项记录字段、问题类型、样本编号、复现说明、代码可修复性和证据引用；`sample_decisions` 记录每条样本的 `PASS/ISSUE` 决策。`PASS` 必须绑定当前 revision；`CODE_FIX_REQUIRED` 必须能生成包含这些结构化问题的 failure bundle。

### 4.5 证据下载

客户端只能用 evidence ID 下载。服务端从数据库索引解析相对路径，禁止客户端传递绝对路径或 `..`。响应带 SHA-256、任务号、文件类型和短期过期时间。

## 5. 事件和错误码

事件包括：`TASK_CREATED`、`ANALYSIS_STARTED`、`SPEC_CREATED`、`CODE_GENERATION_STARTED`、`VALIDATION_FINISHED`、`REPORT_CREATED`、`CANDIDATE_COMMITTED`、`MANUAL_RUN_REGISTERED`、`REVIEW_SUBMITTED`、`FAILURE_BUNDLE_CREATED`、`REPAIR_STARTED`、`REPAIR_FINISHED`、`CODE_ADOPTED`、`TASK_BLOCKED` 和 `TASK_CLOSED`。

事件结构：

~~~json
{
  "event_id": "evt-0001",
  "event_type": "VALIDATION_FINISHED",
  "task_id": "task-0001",
  "run_id": "run-0001",
  "occurred_at": "2026-09-28T10:00:00Z",
  "payload_ref": "evidence/task-0001/run-0001/events/evt-0001.json",
  "idempotency_key": "run-0001:validation:finished:v1"
}
~~~

核心错误码：

| 错误码 | 含义 | 默认动作 |
| --- | --- | --- |
| `INVALID_ENTRY_URL` | URL 校验失败 | 返回用户 |
| `ANALYSIS_NO_EVIDENCE` | 没有开发所需证据 | 重试或请求输入 |
| `SPEC_INVALID` | PlatformSpec schema 失败 | 回到规范生成 |
| `CODE_SCOPE_VIOLATION` | 修改超出白名单 | 阻断提交 |
| `VALIDATION_FAILED` | 自动测试失败 | 有证据时自动修复 |
| `LIST_EMPTY_OBSERVED` | 已验证范围为空 | 先出报告 |
| `DETAIL_FIELD_MISSING` | 详情缺字段 | 有证据时自动修复 |
| `MANUAL_RESULT_MISMATCH` | 人工结果错误 | 生成纠错包 |
| `PLATFORM_MAPPING_REQUIRED` | 下游写库缺业务编号 | 等待映射 |
| `ACCESS_RESTRICTED` | 登录、验证码或访问限制 | 尝试有界代码修复 |
| `TOOL_UNAVAILABLE` | 工具 profile 不可用 | 运维处理 |
| `REPAIR_BUDGET_EXCEEDED` | 达到修复/资源预算 | 转人工 |
| `CONFLICTING_RUN` | 同平台有运行锁 | 等待 |

错误消息不能代替机器路由；错误必须包含阶段、可重试性、证据引用和脱敏状态。

## 6. 版本与下游兼容

报告、规范、规则和代码均不可变；修订建立新版本。任务绑定创建时的 `policy_version`、`spec_version`、`code_revision` 和 `environment_fingerprint`。

采集暂存阶段的 `platform_id`、`entity_id` 始终为 NULL，清洗阶段再补充两个业务映射。旧原始库如果要求非空 `source_platform`，由单独的桥接适配器处理；映射缺失返回 `PLATFORM_MAPPING_REQUIRED`，不能写 0、随机数字或静默丢弃。这个旧表约束不阻断新平台的采集、人工审查和清洗前交付。

业务哈希仍按下游约定计算：标题、描述、要求各自规范化后用换行连接，再计算 MD5。证据文件用 SHA-256，二者不能混用。清洗库的 clean_status、sync_status 与本平台 task_state、review_status 分离。
