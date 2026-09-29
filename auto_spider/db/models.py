from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class OnboardingBatch(Base):
    __tablename__ = "onboarding_batches"
    __table_args__ = (UniqueConstraint("client_request_id", name="uk_batch_request"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    client_request_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), default="SUBMITTED")
    requested_count: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(128), default="dev-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OnboardingTask(Base):
    __tablename__ = "onboarding_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("onboarding_batches.batch_id"), index=True)
    entry_url: Mapped[str] = mapped_column(String(2048))
    normalized_url: Mapped[str] = mapped_column(String(2048))
    platform_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_key: Mapped[str] = mapped_column(String(128), index=True)
    repository_key: Mapped[str] = mapped_column(String(128), default="aicoding-auto_spider")
    policy_version: Mapped[str] = mapped_column(String(64), default="report-v1")
    platform_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="SUBMITTED", index=True)
    next_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), default="dev-user")
    current_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_report_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_type: Mapped[str] = mapped_column(String(40))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="RUNNING")
    worktree_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlatformSpec(Base):
    __tablename__ = "platform_specs"
    __table_args__ = (UniqueConstraint("task_id", "spec_version", name="uk_task_spec_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    spec_version: Mapped[int] = mapped_column(Integer)
    spec_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    confidence_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_manifest_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    validator_version: Mapped[str] = mapped_column(String(40), default="validation-v1")
    pytest_status: Mapped[str] = mapped_column(String(20), default="NOT_RUN")
    ruff_status: Mapped[str] = mapped_column(String(20), default="NOT_RUN")
    contract_status: Mapped[str] = mapped_column(String(20), default="NOT_RUN")
    business_status: Mapped[str] = mapped_column(String(20), default="NOT_RUN")
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    internship_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OnboardingReport(Base):
    __tablename__ = "onboarding_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    report_version: Mapped[str] = mapped_column(String(40), default="report-v1")
    observation_code: Mapped[str] = mapped_column(String(50))
    technical_status: Mapped[str] = mapped_column(String(20))
    next_action: Mapped[str] = mapped_column(String(50))
    adoptable: Mapped[bool] = mapped_column(Boolean, default=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManualRun(Base):
    __tablename__ = "manual_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manual_run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    code_revision: Mapped[str] = mapped_column(String(80))
    command_profile: Mapped[str] = mapped_column(String(80))
    environment_fingerprint: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    artifact_manifest_ref: Mapped[str] = mapped_column(String(1024))
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="WAITING_REVIEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManualReview(Base):
    __tablename__ = "manual_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    manual_run_id: Mapped[str] = mapped_column(ForeignKey("manual_runs.manual_run_id"), index=True)
    code_revision: Mapped[str] = mapped_column(String(80))
    review_status: Mapped[str] = mapped_column(String(40))
    reviewer_id: Mapped[str] = mapped_column(String(128))
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    issue_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_details: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sample_decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FailureBundle(Base):
    __tablename__ = "failure_bundles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bundle_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    review_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_type: Mapped[str] = mapped_column(String(80))
    code_fixable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="CREATED")
    bundle_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_manifest_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RepairRun(Base):
    __tablename__ = "repair_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repair_run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("failure_bundles.bundle_id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    diagnosis_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    changed_files: Mapped[list[str]] = mapped_column(JSON, default=list)
    regression_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    commit_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CodeSubmission(Base):
    __tablename__ = "code_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    branch_name: Mapped[str] = mapped_column(String(255))
    commit_sha: Mapped[str | None] = mapped_column(String(80), nullable=True)
    baseline_ref: Mapped[str] = mapped_column(String(80))
    changed_files: Mapped[list[str]] = mapped_column(JSON, default=list)
    submission_type: Mapped[str] = mapped_column(String(30), default="onboarding")
    adoption_status: Mapped[str] = mapped_column(String(30), default="candidate")
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceFile(Base):
    __tablename__ = "evidence_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    file_type: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    redaction_status: Mapped[str] = mapped_column(String(20), default="redacted")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uk_policy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(40))
    policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint("task_id", "run_id", "revision", name="uk_checkpoint_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("onboarding_tasks.task_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.run_id"), index=True)
    node: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
