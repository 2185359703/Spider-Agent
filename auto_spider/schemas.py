from __future__ import annotations

import builtins
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SpecStatus(StrEnum):
    DRAFT = "DRAFT"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CANDIDATE = "CANDIDATE"
    ADOPTED = "ADOPTED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class AdapterFamily(StrEnum):
    CUSTOM_HTTP = "custom_http"
    FEISHU = "feishu"
    MOKAHR = "mokahr"
    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    SAP = "sap"
    UNKNOWN = "unknown"


class RuntimeMode(StrEnum):
    HTTP = "http"


class ResponseFormat(StrEnum):
    JSON = "json"
    HTML = "html"
    TEXT = "text"


class DecodeMode(StrEnum):
    NONE = "none"
    JSON_FIELD = "json_field"
    BASE64 = "base64"
    OPAQUE = "opaque"
    CUSTOM_HELPER = "custom_helper"


class SelectorKind(StrEnum):
    JSON_PATH = "json_path"
    CSS = "css"
    XPATH = "xpath"
    REGEX = "regex"
    CONSTANT = "constant"
    TEMPLATE = "template"
    RESPONSE_HEADER = "response_header"
    URL_COMPONENT = "url_component"


class SelectorSource(StrEnum):
    RESPONSE_BODY = "response_body"
    LIST_ITEM = "list_item"
    DETAIL = "detail"
    RELATED = "related"
    URL = "url"
    RESPONSE_HEADER = "response_header"


class PaginationMode(StrEnum):
    NONE = "none"
    PAGE = "page"
    OFFSET = "offset"
    CURSOR = "cursor"
    NEXT_URL = "next_url"


class TerminationRule(StrEnum):
    EMPTY_PAGE = "empty_page"
    NEXT_MISSING = "next_missing"
    REPEATED_PAGE_FINGERPRINT = "repeated_page_fingerprint"
    TOTAL_REACHED = "total_reached"
    MAX_PAGES = "max_pages"


class PlatformSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IdentitySpec(PlatformSpecModel):
    entry_url: str
    normalized_url: str
    platform_id: StrictInt | None = None
    entity_id: StrictInt | None = None

    @field_validator("platform_id", "entity_id")
    @classmethod
    def reject_zero_ids(cls, value: int | None) -> int | None:
        if value == 0:
            raise ValueError("platform_id/entity_id 不能使用 0 作为占位值")
        return value


class AdapterSpec(PlatformSpecModel):
    family: AdapterFamily
    runtime_mode: RuntimeMode = RuntimeMode.HTTP
    browser_required_for_discovery: bool = True
    browser_required_for_runtime: bool = False
    collector_template: str = "custom_http"
    version: str = "1.0"

    @model_validator(mode="after")
    def runtime_cannot_require_browser(self) -> AdapterSpec:
        if self.runtime_mode == RuntimeMode.HTTP and self.browser_required_for_runtime:
            raise ValueError("runtime_mode=http 时不能把浏览器作为正式采集依赖")
        return self


class RequestProfile(PlatformSpecModel):
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_seconds: list[float] = Field(default_factory=lambda: [1, 2, 4])
    rate_limit_per_second: float = Field(default=1, gt=0, le=100)
    allowed_headers: list[str] = Field(default_factory=list)
    credentials_required: bool = False


class SelectorSpec(PlatformSpecModel):
    source: SelectorSource
    kind: SelectorKind
    expression: str
    multiple: bool = False
    fallbacks: list[SelectorSpec] = Field(default_factory=list)
    transforms: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    inferred: bool = False
    inference_reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_inference_metadata(self) -> SelectorSpec:
        if self.inferred and not self.inference_reason:
            raise ValueError("inferred=true 时必须提供 inference_reason")
        if self.confidence == Confidence.HIGH and not self.evidence_refs:
            raise ValueError("high confidence selector 必须关联 evidence_refs")
        return self


class EndpointSpec(PlatformSpecModel):
    method: Literal["GET", "POST", "PUT", "PATCH"] = "GET"
    url_template: str
    query: dict[str, Any] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    response_format: ResponseFormat = ResponseFormat.JSON
    items_selector: SelectorSpec | None = None
    decode: DecodeSpec | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    inferred: bool = False


class DecodeSpec(PlatformSpecModel):
    mode: DecodeMode = DecodeMode.NONE
    input_selector: SelectorSpec | None = None
    encoding: str | None = None
    algorithm: str | None = None
    key_source: str | None = None
    helper_required: bool = False
    helper_entrypoint: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    inferred: bool = False

    @model_validator(mode="after")
    def check_decode(self) -> DecodeSpec:
        if self.mode != DecodeMode.NONE and self.input_selector is None:
            raise ValueError("非 none 解码模式必须提供 input_selector")
        if self.helper_required and not self.helper_entrypoint:
            raise ValueError("helper_required=true 时必须提供 helper_entrypoint")
        return self


class EndpointsSpec(PlatformSpecModel):
    list: EndpointSpec | None = None
    detail: EndpointSpec | None = None
    related: builtins.list[EndpointSpec] = Field(default_factory=lambda: [])


class PaginationSpec(PlatformSpecModel):
    mode: PaginationMode = PaginationMode.NONE
    page_param: str | None = None
    size_param: str | None = None
    cursor_param: str | None = None
    next_url_selector: SelectorSpec | None = None
    page_start: int = Field(default=1, ge=0)
    page_size: int = Field(default=50, ge=1, le=500)
    max_pages: int = Field(default=50, ge=1, le=1000)
    termination: list[TerminationRule] = Field(default_factory=list)
    total_count_selector: SelectorSpec | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    inferred: bool = False

    @model_validator(mode="after")
    def check_pagination(self) -> PaginationSpec:
        if self.mode != PaginationMode.NONE and not self.termination:
            raise ValueError("分页模式必须至少声明一个 termination")
        if self.mode == PaginationMode.PAGE and not self.page_param:
            raise ValueError("page 模式必须提供 page_param")
        if self.mode == PaginationMode.OFFSET and not self.page_param:
            raise ValueError("offset 模式必须提供 page_param")
        if self.mode == PaginationMode.CURSOR and not self.cursor_param:
            raise ValueError("cursor 模式必须提供 cursor_param")
        if self.mode == PaginationMode.NEXT_URL and not self.next_url_selector:
            raise ValueError("next_url 模式必须提供 next_url_selector")
        return self


class FieldSpec(PlatformSpecModel):
    required: bool = False
    selectors: list[SelectorSpec] = Field(default_factory=list)
    transforms: list[str] = Field(default_factory=list)


class FieldsSpec(PlatformSpecModel):
    source_id: FieldSpec = Field(default_factory=lambda: FieldSpec(required=True))
    title: FieldSpec = Field(default_factory=lambda: FieldSpec(required=True))
    source_url: FieldSpec = Field(default_factory=lambda: FieldSpec(required=True))
    location: FieldSpec = Field(default_factory=FieldSpec)
    description: FieldSpec = Field(default_factory=FieldSpec)
    requirements: FieldSpec = Field(default_factory=FieldSpec)
    publish_time: FieldSpec = Field(default_factory=FieldSpec)
    department: FieldSpec = Field(default_factory=FieldSpec)
    employment_type: FieldSpec = Field(default_factory=FieldSpec)
    job_type: FieldSpec = Field(default_factory=FieldSpec)

    def hard_field_names(self) -> tuple[str, ...]:
        return ("source_id", "title", "source_url")


class InternshipFilterSpec(PlatformSpecModel):
    match_scope: list[str] = Field(
        default_factory=lambda: [
            "title",
            "employment_type",
            "category",
            "description",
            "requirements",
        ]
    )
    include_keywords: list[str] = Field(default_factory=lambda: ["实习", "intern", "internship"])
    exclude_keywords: list[str] = Field(default_factory=list)
    match_mode: Literal["any", "all"] = "any"
    must_match: bool = True
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    inferred: bool = False
    inference_reason: str | None = None


class LocationFilterSpec(PlatformSpecModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class FiltersSpec(PlatformSpecModel):
    internship: InternshipFilterSpec = Field(default_factory=InternshipFilterSpec)
    location: LocationFilterSpec = Field(default_factory=LocationFilterSpec)


class ListsNormalizationSpec(PlatformSpecModel):
    deduplicate: bool = True
    preserve_order: bool = True


class TimeNormalizationSpec(PlatformSpecModel):
    accepted_formats: list[str] = Field(
        default_factory=lambda: [
            "unix_seconds",
            "unix_milliseconds",
            "iso8601",
            "yyyy-mm-dd",
        ]
    )
    default_timezone: str = "Asia/Shanghai"
    infer_missing_publish_time: bool = False


class UrlNormalizationSpec(PlatformSpecModel):
    resolve_relative: bool = True
    remove_tracking_params: bool = True


class NormalizationSpec(PlatformSpecModel):
    text: list[str] = Field(
        default_factory=lambda: [
            "decode_html_entities",
            "strip_html",
            "replace_nbsp",
            "collapse_whitespace",
            "drop_empty_lines",
        ]
    )
    lists: ListsNormalizationSpec = Field(default_factory=ListsNormalizationSpec)
    time: TimeNormalizationSpec = Field(default_factory=TimeNormalizationSpec)
    url: UrlNormalizationSpec = Field(default_factory=UrlNormalizationSpec)


class ValidationSpec(PlatformSpecModel):
    required_fields: list[str] = Field(default_factory=lambda: ["source_id", "title", "source_url"])
    body_required_any_of: list[str] = Field(default_factory=lambda: ["description", "requirements"])
    list_required: bool = True
    detail_required: bool = True
    pagination_required: bool = True
    min_sample_count: int = Field(default=1, ge=0)
    live_sample_count: int = Field(default=3, ge=0)
    expected_http_statuses: list[int] = Field(default_factory=lambda: [200])
    low_confidence_fields: list[str] = Field(default_factory=list)
    blocking_errors: list[str] = Field(
        default_factory=lambda: [
            "missing_source_id",
            "missing_title",
            "missing_source_url",
            "unbounded_pagination",
            "unsanitized_sensitive_data",
        ]
    )


class EvidenceSpec(PlatformSpecModel):
    required_refs: list[str] = Field(
        default_factory=lambda: [
            "entry_page",
            "list_request",
            "detail_request",
            "pagination_request",
            "internship_filter",
        ]
    )
    manifest_ref: str | None = None
    redaction_status: Literal["pending", "redacted", "failed"] = "pending"


class GenerationSpec(PlatformSpecModel):
    target_repository: str = "aicoding-auto_spider"
    baseline_ref: str
    allowed_files: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    commit_type: Literal["feat", "fix"] = "feat"
    simulated: bool = True

    @field_validator("allowed_files")
    @classmethod
    def reject_unsafe_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            normalized = value.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError(f"allowed_files 包含不安全路径: {value}")
        return values


class PlatformSpec(PlatformSpecModel):
    schema_name: Literal["platform_spec"] = "platform_spec"
    schema_version: Literal["1.0"] = "1.0"
    spec_id: str
    spec_revision: int = Field(default=1, ge=1)
    spec_hash: str | None = None
    task_id: str
    platform_key: str
    source_name: str
    crawl_version: str = "v1.0.0"
    status: SpecStatus = SpecStatus.DRAFT
    identity: IdentitySpec
    adapter: AdapterSpec
    request_profile: RequestProfile = Field(default_factory=RequestProfile)
    endpoints: EndpointsSpec = Field(default_factory=EndpointsSpec)
    pagination: PaginationSpec = Field(default_factory=PaginationSpec)
    fields: FieldsSpec = Field(default_factory=FieldsSpec)
    filters: FiltersSpec = Field(default_factory=FiltersSpec)
    normalization: NormalizationSpec = Field(default_factory=NormalizationSpec)
    validation: ValidationSpec = Field(default_factory=ValidationSpec)
    evidence: EvidenceSpec = Field(default_factory=EvidenceSpec)
    generation: GenerationSpec
    confidence_summary: dict[str, int] = Field(default_factory=dict)

    @field_validator("platform_key")
    @classmethod
    def validate_platform_key(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if not normalized or not normalized.replace("_", "").isalnum():
            raise ValueError("platform_key 只能包含小写字母、数字和下划线")
        return normalized

    @model_validator(mode="after")
    def check_top_level_consistency(self) -> PlatformSpec:
        if self.identity.platform_id is not None or self.identity.entity_id is not None:
            raise ValueError("采集阶段 PlatformSpec 的 platform_id/entity_id 必须为 null")
        if self.identity.entry_url.strip() == "":
            raise ValueError("entry_url 不能为空")
        return self

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"spec_hash"})

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def calculated_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def with_hash(self) -> PlatformSpec:
        return self.model_copy(update={"spec_hash": self.calculated_hash()})

    def confidence_counts(self) -> dict[str, int]:
        counts = {confidence.value: 0 for confidence in Confidence}
        for field_name in self.fields.hard_field_names():
            field = getattr(self.fields, field_name)
            for selector in field.selectors:
                counts[selector.confidence.value] += 1
        return counts

    def candidate_blocking_errors(self) -> list[str]:
        errors: list[str] = []
        if self.endpoints.list is None:
            errors.append("missing_list_endpoint")
        for endpoint_name, endpoint in (
            ("list", self.endpoints.list),
            ("detail", self.endpoints.detail),
        ):
            if endpoint is not None and endpoint.decode is not None:
                if endpoint.decode.helper_required and not endpoint.decode.helper_entrypoint:
                    errors.append(f"missing_{endpoint_name}_response_decoder")
        if self.validation.detail_required and self.endpoints.detail is None:
            errors.append("missing_detail_endpoint")
        if self.validation.pagination_required and self.pagination.mode != PaginationMode.NONE:
            if not self.pagination.termination:
                errors.append("unbounded_pagination")
        if self.validation.pagination_required and self.pagination.mode == PaginationMode.NONE:
            errors.append("pagination_not_verified")
        for field_name in self.fields.hard_field_names():
            field = getattr(self.fields, field_name)
            if not field.selectors:
                errors.append(f"missing_{field_name}_selector")
                continue
            if any(selector.confidence == Confidence.LOW for selector in field.selectors):
                errors.append(f"low_confidence_{field_name}")
        if self.filters.internship.must_match and not self.filters.internship.evidence_refs:
            errors.append("missing_internship_filter_evidence")
        return errors


SelectorSpec.model_rebuild()
EndpointSpec.model_rebuild()
PlatformSpec.model_rebuild()


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


class ReviewStatus(StrEnum):
    PASS = "PASS"
    CODE_FIX_REQUIRED = "CODE_FIX_REQUIRED"
    EXTERNAL_BLOCKED = "EXTERNAL_BLOCKED"
    BUSINESS_RULE_REVIEW = "BUSINESS_RULE_REVIEW"
    REJECT = "REJECT"


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


class CreateBatchRequest(BaseModel):
    items: list[IntakeItem] = Field(min_length=1, max_length=100)
    analysis_profile: str = Field(default="internship-http-v1", max_length=64)
    dry_run: bool = True
    client_request_id: str = Field(min_length=8, max_length=128)


class CreateBatchResponse(BaseModel):
    batch_id: str
    task_ids: list[str]
    accepted_count: int
    rejected_count: int
    status: str


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    batch_id: str
    entry_url: str
    normalized_url: str
    platform_name: str | None
    platform_key: str
    repository_key: str
    platform_id: int | None
    entity_id: int | None
    status: str
    next_action: str | None
    current_run_id: str | None
    last_report_id: str | None
    created_at: datetime
    updated_at: datetime


class ReportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    report_id: str
    task_id: str
    run_id: str
    report_version: str
    observation_code: ObservationCode
    technical_status: TechnicalStatus
    next_action: NextAction
    adoptable: bool
    report_json: dict[str, Any]
    report_ref: str | None
    created_at: datetime


class ManualRunRequest(BaseModel):
    code_revision: str = Field(min_length=1, max_length=80)
    command_profile: str = Field(default="internship-preview-v1", max_length=80)
    environment_fingerprint: str = Field(min_length=1, max_length=255)
    started_at: datetime
    finished_at: datetime
    artifact_manifest_ref: str = Field(min_length=1, max_length=1024)
    result: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str = Field(min_length=8, max_length=128)

    @field_validator("finished_at")
    @classmethod
    def finished_after_start(cls, value: datetime, info: Any) -> datetime:
        started = info.data.get("started_at")
        if started and value < started:
            raise ValueError("finished_at 不能早于 started_at")
        return value


class ManualReviewRequest(BaseModel):
    review_status: ReviewStatus
    manual_run_id: str = Field(min_length=1, max_length=32)
    code_revision: str = Field(min_length=1, max_length=80)
    sample_count: int = Field(default=0, ge=0)
    issue_summary: str | None = Field(default=None, max_length=10000)
    evidence_refs: list[str] = Field(default_factory=list)
    client_request_id: str = Field(min_length=8, max_length=128)


class ResumeRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str = Field(min_length=8, max_length=128)


class RepairRequest(BaseModel):
    failure_bundle_id: str | None = Field(default=None, max_length=32)
    client_request_id: str = Field(min_length=8, max_length=128)


def platform_key_from_url(url: str) -> str:
    host = urlparse(url).hostname or "unknown"
    value = host.removeprefix("www.").replace(".", "_").replace("-", "_")
    return value[:120] or "unknown"
