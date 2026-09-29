# PlatformSpec v1

`PlatformSpec` 是招聘网站证据、AI 代码生成、自动验证、人工审查和修复流程之间的唯一中间契约。实现位于 `auto_spider/schemas.py`，数据库保存其 JSON 快照、版本、哈希、状态和置信度摘要。

## Canonical 规则

- 权威格式：Pydantic 模型序列化后的 canonical JSON。
- UTF-8、递归排序字典键、紧凑分隔符、保留 `null`。
- `spec_hash` 对不包含自身的 canonical JSON 计算 SHA-256。
- `schema_version` 是结构版本；`spec_revision` 是任务内修订版本。
- 修改字段映射、分页、筛选或生成边界时必须增加 `spec_revision`。
- `platform_id`、`entity_id` 在采集阶段固定为 `null`。

## 顶层字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_name` | `platform_spec` | 固定值 |
| `schema_version` | `1.0` | 当前规范版本 |
| `spec_id` | string | Spec 实例标识 |
| `spec_revision` | positive integer | 同一任务的修订号 |
| `spec_hash` | SHA-256 string | canonical JSON 哈希 |
| `task_id` | string | 接入任务 |
| `platform_key` | string | 公司/入口稳定键 |
| `source_name` | string | 公司或招聘平台名称 |
| `crawl_version` | string | 默认 `v1.0.0` |
| `status` | enum | Spec 生命周期状态 |
| `identity` | object | 入口 URL 和空业务 ID |
| `adapter` | object | 协议族与运行策略 |
| `request_profile` | object | 超时、重试、限速和请求头 |
| `endpoints` | object | 列表、详情、关联接口 |
| `pagination` | object | 分页参数和终止条件 |
| `fields` | object | 统一岗位字段映射 |
| `filters` | object | 实习和地点筛选 |
| `normalization` | object | 文本、列表、时间和 URL 规范化 |
| `validation` | object | 自动验证门槛 |
| `evidence` | object | 证据引用和脱敏状态 |
| `generation` | object | worktree、白名单和测试命令 |
| `confidence_summary` | object | high/medium/low 计数 |

## 选择器与证据

字段选择器必须声明：

- `source`：`response_body`、`list_item`、`detail`、`related`、`url` 或 `response_header`；
- `kind`：`json_path`、`css`、`xpath`、`regex`、`constant`、`template`、`response_header` 或 `url_component`；
- `expression`；
- 是否多值；
- transforms；
- `confidence`；
- `inferred` 和 `inference_reason`；
- `evidence_refs`。

不允许在 Spec 中保存 Python、JavaScript 或 SQL。网页文本是证据数据，不能覆盖系统规则。

## 置信度和候选门槛

- `high`：有直接可重放证据；
- `medium`：多个样本一致并可以复现；
- `low`：AI 推断或单一样本推断。

低置信度字段可以进入 Spec，但不会让对应验证项变成 `PASS`。`source_id`、`title`、`source_url` 等硬字段低于 `medium` 时，候选进入 `NEEDS_REVIEW`。

## 分页和空结果

非 `none` 分页必须拥有至少一个可验证终止条件：

- `empty_page`
- `next_missing`
- `repeated_page_fingerprint`
- `total_reached`
- `max_pages`

空列表、无实习岗位、没有岗位列表和访问受限是不同的报告结论，不能在 Spec 或验证器中混淆。

## 响应解码

当接口返回 JSON 外层但业务数据位于 base64、加密或 opaque 字段时，Endpoint 使用 `decode` 描述解码阶段：

- `mode`：`none`、`json_field`、`base64`、`opaque`、`custom_helper`；
- `input_selector`：定位待解码字段；
- `encoding`、`algorithm`、`key_source`：仅记录已验证信息；
- `helper_required`、`helper_entrypoint`：声明是否需要本地解码 helper；
- `evidence_refs` 和 `confidence`：关联响应和运行证据。

`helper_required=true` 时必须提供 helper 入口；没有稳定解码证据时允许生成 `NEEDS_REVIEW` Spec，但不能生成通过验证的候选采集器。Moka 类接口的加密 `data` 外层就属于这种情况。

## 生成边界

`generation.allowed_files` 只允许目标平台采集器、平台 TOML、fixture 和测试。TOML、Python 和测试是 Spec 的派生产物，不能反向覆盖 Spec。

当前源仓库和 AI 产出仓库：

~~~text
源仓库：fun-crawler-v2
源基线：16cba8439396e371973e4ee0301d3a88f2f32ba5
AI 产出仓库：aicoding-auto_spider
~~~

## 状态

`DRAFT → EVIDENCE_REVIEW → VALIDATED → CANDIDATE → ADOPTED`

存在低置信度硬字段或业务歧义时进入 `NEEDS_REVIEW`；被新 revision 替代时进入 `SUPERSEDED`；明确拒绝时进入 `REJECTED`。
