from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


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
    repository_key: str = Field(default="collector-catalog", max_length=128)
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
