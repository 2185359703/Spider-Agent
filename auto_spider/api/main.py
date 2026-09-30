from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select

from auto_spider.ai.skill_loader import (
    load_collector_agent_skill,
    load_spider_king_agent_skill,
)
from auto_spider.api.deps import AdminActor, CurrentActor, DbSession, OperatorActor, ReviewerActor
from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    EvidenceFile,
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingBatch,
    OnboardingTask,
    PlatformSpec,
    PolicyVersion,
    RepairRun,
    WorkflowEvent,
    WorkflowLog,
    WorkflowRun,
)
from auto_spider.git.target import CollectorRepository
from auto_spider.schemas import (
    CreateBatchRequest,
    CreateBatchResponse,
    ManualReviewRequest,
    ManualRunRequest,
    PolicyVersionRequest,
    RepairRequest,
    ReportResponse,
    ResumeRequest,
    TaskResponse,
)
from auto_spider.services.evidence import sanitize
from auto_spider.services.tasks import (
    create_batch,
    create_manual_run,
    create_review,
    get_task,
    latest_report,
)
from auto_spider.services.workflow_logs import append_durable_log
from auto_spider.workers.tasks import run_onboarding, run_repair

app = FastAPI(title="AI Recruitment Collector Onboarding", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in get_settings().frontend_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def enqueue_onboarding(task_id: str) -> None:
    settings = get_settings()
    append_durable_log(
        task_id=task_id,
        run_id=None,
        stage="queue",
        message="任务已提交到工作流队列",
        detail={
            "eager": settings.auto_spider_eager_workflow,
            "queue_enabled": settings.queue_enabled,
        },
    )
    if settings.auto_spider_eager_workflow:
        run_onboarding(task_id)
    elif settings.queue_enabled and hasattr(run_onboarding, "delay"):
        run_onboarding.delay(task_id)


def enqueue_repair(task_id: str, bundle_id: str) -> None:
    settings = get_settings()
    append_durable_log(
        task_id=task_id,
        run_id=None,
        stage="queue",
        message="修复任务已提交到工作流队列",
        detail={"bundle_id": bundle_id, "eager": settings.auto_spider_eager_workflow},
    )
    if settings.auto_spider_eager_workflow:
        run_repair(task_id, bundle_id)
    elif settings.queue_enabled and hasattr(run_repair, "delay"):
        run_repair.delay(task_id, bundle_id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "auto-spider"}


@app.get("/api/v1/me")
def current_actor(actor: CurrentActor) -> dict[str, str]:
    return {"user_id": actor.user_id, "role": actor.role}


@app.get("/api/v1/system/health")
def system_health(session: DbSession, actor: CurrentActor) -> dict:
    settings = get_settings()
    checks: dict[str, dict[str, object]] = {}
    try:
        session.execute(select(1))
        checks["database"] = {"status": "ready"}
    except Exception as exc:  # pragma: no cover - driver-specific failures
        checks["database"] = {"status": "error", "detail": type(exc).__name__}

    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
        checks["redis"] = {"status": "ready"}
    except Exception as exc:  # pragma: no cover - local service availability
        checks["redis"] = {"status": "offline", "detail": type(exc).__name__}

    agent_url = settings.openhands_server_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(agent_url, timeout=2) as response:
            checks["agent_server"] = {
                "status": "ready" if response.status == 200 else "degraded",
                "url": settings.openhands_server_url,
            }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        checks["agent_server"] = {
            "status": "offline",
            "url": settings.openhands_server_url,
            "detail": type(exc).__name__,
        }

    try:
        skill_text = load_collector_agent_skill()
        checks["collector_skill"] = {
            "status": "ready",
            "name": "collector-onboarding",
            "size_bytes": len(skill_text.encode("utf-8")),
        }
    except (OSError, RuntimeError) as exc:
        checks["collector_skill"] = {
            "status": "offline",
            "name": "collector-onboarding",
            "detail": type(exc).__name__,
        }

    try:
        spider_skill_text = load_spider_king_agent_skill()
        checks["spider_king_skill"] = {
            "status": "ready",
            "name": "spider-king-collector",
            "size_bytes": len(spider_skill_text.encode("utf-8")),
        }
    except (OSError, RuntimeError) as exc:
        checks["spider_king_skill"] = {
            "status": "offline",
            "name": "spider-king-collector",
            "detail": type(exc).__name__,
        }

    overall = "ready" if all(item["status"] == "ready" for item in checks.values()) else "degraded"
    return {
        "status": overall,
        "agent_mode": settings.agent_mode,
        "analysis_mode": settings.analysis_mode,
        "queue_enabled": settings.queue_enabled,
        "checks": checks,
    }


def _require_task(session, task_id: str) -> OnboardingTask:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _git_output(repository: Path, *args: str, timeout: int = 20) -> tuple[int, str, str]:
    """Run a read-only git command for a stored submission reference."""
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def _sample_rows(result_json: dict) -> list[dict]:
    raw_samples = result_json.get("samples", []) if isinstance(result_json, dict) else []
    if not isinstance(raw_samples, list):
        return []
    known_fields = {
        "source_id",
        "title",
        "source_url",
        "location",
        "description",
        "requirements",
        "publish_time",
        "department",
        "employment_type",
        "job_type",
        "apply_url",
        "position",
    }
    rows: list[dict] = []
    for index, item in enumerate(raw_samples, start=1):
        if not isinstance(item, dict):
            continue
        safe_item = sanitize(item)
        rows.append(
            {
                "sample_index": index,
                **{field: safe_item.get(field) for field in known_fields},
                "extra": {
                    key: value for key, value in safe_item.items() if key not in known_fields
                },
            }
        )
    return rows


def _repository_payload(repository: CollectorRepository) -> dict:
    try:
        inspection = repository.inspect()
        return {
            "path": str(inspection.path),
            "baseline_ref": inspection.baseline_ref,
            "head": inspection.head,
            "dirty_files": list(inspection.dirty_files),
            "is_git_repository": inspection.is_git_repository,
            "baseline_available": inspection.baseline_available,
        }
    except (OSError, RuntimeError) as exc:
        return {
            "path": str(repository.path),
            "baseline_ref": repository.baseline_ref,
            "head": None,
            "dirty_files": [],
            "is_git_repository": False,
            "baseline_available": False,
            "error": str(exc),
        }


@app.get("/api/v1/onboarding/batches")
def list_onboarding_batches(
    session: DbSession,
    actor: CurrentActor,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict]:
    rows = session.scalars(
        select(OnboardingBatch).order_by(OnboardingBatch.updated_at.desc()).limit(limit)
    ).all()
    return [
        {
            "batch_id": row.batch_id,
            "client_request_id": row.client_request_id,
            "status": row.status,
            "requested_count": row.requested_count,
            "accepted_count": row.accepted_count,
            "rejected_count": row.rejected_count,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/batches/{batch_id}")
def read_onboarding_batch(batch_id: str, session: DbSession, actor: CurrentActor) -> dict:
    batch = session.scalar(select(OnboardingBatch).where(OnboardingBatch.batch_id == batch_id))
    if batch is None:
        raise HTTPException(status_code=404, detail="批次不存在")
    tasks = session.scalars(
        select(OnboardingTask)
        .where(OnboardingTask.batch_id == batch_id)
        .order_by(OnboardingTask.created_at.asc())
    ).all()
    return {
        "batch": {
            "batch_id": batch.batch_id,
            "client_request_id": batch.client_request_id,
            "status": batch.status,
            "requested_count": batch.requested_count,
            "accepted_count": batch.accepted_count,
            "rejected_count": batch.rejected_count,
            "created_by": batch.created_by,
            "created_at": batch.created_at,
            "updated_at": batch.updated_at,
        },
        "tasks": [TaskResponse.model_validate(task) for task in tasks],
    }


@app.get("/api/v1/onboarding/submissions")
def list_all_submissions(
    session: DbSession,
    actor: CurrentActor,
    adoption_status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    statement = (
        select(CodeSubmission, OnboardingTask)
        .join(OnboardingTask, CodeSubmission.task_id == OnboardingTask.task_id)
        .order_by(CodeSubmission.created_at.desc())
    )
    if adoption_status:
        statement = statement.where(CodeSubmission.adoption_status == adoption_status)
    statement = statement.limit(limit)
    return [
        {
            "submission_id": submission.submission_id,
            "task_id": submission.task_id,
            "platform_key": task.platform_key,
            "platform_name": task.platform_name,
            "entry_url": task.entry_url,
            "run_id": submission.run_id,
            "branch_name": submission.branch_name,
            "commit_sha": submission.commit_sha,
            "baseline_ref": submission.baseline_ref,
            "changed_files": submission.changed_files,
            "submission_type": submission.submission_type,
            "adoption_status": submission.adoption_status,
            "simulated": submission.simulated,
            "created_at": submission.created_at,
            "adopted_at": submission.adopted_at,
        }
        for submission, task in session.execute(statement).all()
    ]


@app.get("/api/v1/onboarding/samples")
def list_all_samples(
    session: DbSession,
    actor: CurrentActor,
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict]:
    rows = session.execute(
        select(ManualRun, OnboardingTask)
        .join(OnboardingTask, ManualRun.task_id == OnboardingTask.task_id)
        .order_by(ManualRun.created_at.desc())
        .limit(200)
    ).all()
    flattened: list[dict] = []
    for run, task in rows:
        for sample in _sample_rows(run.result_json):
            flattened.append(
                {
                    "task_id": task.task_id,
                    "platform_key": task.platform_key,
                    "platform_name": task.platform_name,
                    "entry_url": task.entry_url,
                    "manual_run_id": run.manual_run_id,
                    "code_revision": run.code_revision,
                    **sample,
                }
            )
            if len(flattened) >= limit:
                return flattened
    return flattened


@app.get("/api/v1/policies")
def list_policy_versions(
    session: DbSession,
    actor: CurrentActor,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[dict]:
    rows = session.scalars(
        select(PolicyVersion).order_by(PolicyVersion.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "name": row.name,
            "version": row.version,
            "status": row.status,
            "policy": row.policy_json,
            "created_by": row.created_by,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.post("/api/v1/policies", status_code=status.HTTP_201_CREATED)
def create_policy_version(
    request: PolicyVersionRequest,
    session: DbSession,
    actor: AdminActor,
) -> dict:
    duplicate = session.scalar(
        select(PolicyVersion).where(
            PolicyVersion.name == request.name,
            PolicyVersion.version == request.version,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="规则版本已存在")
    row = PolicyVersion(
        name=request.name,
        version=request.version,
        status=request.status,
        policy_json=request.policy,
        created_by=actor.user_id,
    )
    session.add(row)
    session.commit()
    return {
        "name": row.name,
        "version": row.version,
        "status": row.status,
        "policy": row.policy_json,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


@app.get("/api/v1/system/repositories")
def repository_status(session: DbSession, actor: CurrentActor) -> dict:
    settings = get_settings()
    return {
        "control_plane": {
            "path": str(Path.cwd()),
            "remote_configured": False,
            "push_enabled": False,
        },
        "source": _repository_payload(CollectorRepository.source_repository()),
        "aicoding": {
            **_repository_payload(CollectorRepository()),
            "remote_url": settings.aicoding_remote_url,
            "push_enabled": settings.aicoding_push_enabled,
        },
    }


@app.get("/api/v1/onboarding/tasks", response_model=list[TaskResponse])
def list_onboarding_tasks(
    session: DbSession,
    actor: CurrentActor,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[TaskResponse]:
    statement = select(OnboardingTask).order_by(OnboardingTask.updated_at.desc()).limit(limit)
    if status_filter:
        statement = statement.where(OnboardingTask.status == status_filter)
    return [TaskResponse.model_validate(row) for row in session.scalars(statement).all()]


@app.post(
    "/api/v1/onboarding/batches",
    response_model=CreateBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_onboarding_batch(
    request: CreateBatchRequest,
    session: DbSession,
    actor: OperatorActor,
) -> CreateBatchResponse:
    batch, tasks = create_batch(session, request, actor.user_id)
    for task in tasks:
        if task.status == "SUBMITTED":
            enqueue_onboarding(task.task_id)
    return CreateBatchResponse(
        batch_id=batch.batch_id,
        task_ids=[task.task_id for task in tasks],
        accepted_count=batch.accepted_count,
        rejected_count=batch.rejected_count,
        status=batch.status,
    )


@app.get("/api/v1/onboarding/tasks/{task_id}", response_model=TaskResponse)
def read_task(task_id: str, session: DbSession, actor: CurrentActor) -> TaskResponse:
    return TaskResponse.model_validate(_require_task(session, task_id))


@app.get("/api/v1/onboarding/tasks/{task_id}/timeline")
def task_timeline(task_id: str, session: DbSession, actor: CurrentActor) -> dict:
    _require_task(session, task_id)
    events = session.scalars(
        select(WorkflowEvent)
        .where(WorkflowEvent.task_id == task_id)
        .order_by(WorkflowEvent.occurred_at.asc())
    ).all()
    runs = session.scalars(
        select(WorkflowRun)
        .where(WorkflowRun.task_id == task_id)
        .order_by(WorkflowRun.started_at.asc())
    ).all()
    return {
        "events": [
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "run_id": row.run_id,
                "occurred_at": row.occurred_at,
                "payload": row.payload_json,
            }
            for row in events
        ],
        "runs": [
            {
                "run_id": row.run_id,
                "run_type": row.run_type,
                "attempt": row.attempt,
                "status": row.status,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "error_code": row.error_code,
                "error_message": row.error_message,
            }
            for row in runs
        ],
    }


@app.get("/api/v1/onboarding/tasks/{task_id}/logs")
def task_logs(
    task_id: str,
    session: DbSession,
    actor: CurrentActor,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict:
    _require_task(session, task_id)
    rows = session.scalars(
        select(WorkflowLog)
        .where(WorkflowLog.task_id == task_id, WorkflowLog.sequence > after)
        .order_by(WorkflowLog.sequence.asc())
        .limit(limit)
    ).all()
    return {
        "logs": [
            {
                "log_id": row.log_id,
                "sequence": row.sequence,
                "run_id": row.run_id,
                "stage": row.stage,
                "level": row.level,
                "message": row.message,
                "detail": row.detail_json,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "next_after": rows[-1].sequence if rows else after,
    }


@app.get("/api/v1/onboarding/tasks/{task_id}/reports/latest", response_model=ReportResponse)
def read_latest_report(task_id: str, session: DbSession, actor: CurrentActor) -> ReportResponse:
    if get_task(session, task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    report = latest_report(session, task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return ReportResponse.model_validate(report)


@app.get("/api/v1/onboarding/tasks/{task_id}/specs")
def list_specs(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = (
        session.query(PlatformSpec)
        .filter(PlatformSpec.task_id == task_id)
        .order_by(PlatformSpec.spec_version.desc())
        .all()
    )
    return [
        {
            "task_id": row.task_id,
            "spec_version": row.spec_version,
            "schema_version": row.schema_version,
            "spec_hash": row.spec_hash,
            "status": row.status,
            "confidence_summary": row.confidence_summary,
            "spec": row.spec_json,
            "evidence_manifest_ref": row.evidence_manifest_ref,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.post("/api/v1/onboarding/tasks/{task_id}/manual-runs")
def register_manual_run(
    task_id: str,
    request: ManualRunRequest,
    session: DbSession,
    actor: OperatorActor,
) -> dict:
    task = _require_task(session, task_id)
    manual = create_manual_run(session, task, request)
    return {"manual_run_id": manual.manual_run_id, "status": manual.status}


@app.get("/api/v1/onboarding/tasks/{task_id}/manual-runs")
def list_manual_runs(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(ManualRun)
        .where(ManualRun.task_id == task_id)
        .order_by(ManualRun.created_at.desc())
    ).all()
    return [
        {
            "manual_run_id": row.manual_run_id,
            "code_revision": row.code_revision,
            "command_profile": row.command_profile,
            "environment_fingerprint": row.environment_fingerprint,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "artifact_manifest_ref": row.artifact_manifest_ref,
            "result": row.result_json,
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/tasks/{task_id}/samples")
def list_task_samples(
    task_id: str,
    session: DbSession,
    actor: CurrentActor,
    manual_run_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    _require_task(session, task_id)
    if manual_run_id:
        run = session.scalar(
            select(ManualRun).where(
                ManualRun.task_id == task_id,
                ManualRun.manual_run_id == manual_run_id,
            )
        )
    else:
        run = session.scalar(
            select(ManualRun)
            .where(ManualRun.task_id == task_id)
            .order_by(ManualRun.created_at.desc())
            .limit(1)
        )
    if run is None:
        return {"manual_run": None, "count": 0, "samples": []}
    samples = _sample_rows(run.result_json)[:limit]
    return {
        "manual_run": {
            "manual_run_id": run.manual_run_id,
            "code_revision": run.code_revision,
            "status": run.status,
            "created_at": run.created_at,
        },
        "count": len(samples),
        "samples": samples,
    }


@app.post("/api/v1/onboarding/tasks/{task_id}/reviews")
def submit_review(
    task_id: str,
    request: ManualReviewRequest,
    session: DbSession,
    actor: ReviewerActor,
) -> dict:
    task = _require_task(session, task_id)
    review, bundle = create_review(session, task, request)
    if bundle is not None:
        enqueue_repair(task_id, bundle.bundle_id)
    return {
        "review_id": review.review_id,
        "failure_bundle_id": bundle.bundle_id if bundle else None,
        "status": task.status,
    }


@app.get("/api/v1/onboarding/tasks/{task_id}/reviews")
def list_reviews(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(ManualReview)
        .where(ManualReview.task_id == task_id)
        .order_by(ManualReview.created_at.desc())
    ).all()
    return [
        {
            "review_id": row.review_id,
            "manual_run_id": row.manual_run_id,
            "code_revision": row.code_revision,
            "review_status": row.review_status,
            "reviewer_id": row.reviewer_id,
            "sample_count": row.sample_count,
            "issue_summary": row.issue_summary,
            "field_issues": row.issue_details or [],
            "sample_decisions": row.sample_decisions or [],
            "evidence_refs": row.evidence_refs,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/tasks/{task_id}/repairs")
def list_repairs(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(RepairRun)
        .where(RepairRun.task_id == task_id)
        .order_by(RepairRun.created_at.desc())
    ).all()
    return [
        {
            "repair_run_id": row.repair_run_id,
            "bundle_id": row.bundle_id,
            "attempt": row.attempt,
            "status": row.status,
            "diagnosis": row.diagnosis_json,
            "changed_files": row.changed_files,
            "regression": row.regression_json,
            "commit_sha": row.commit_sha,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/tasks/{task_id}/failures")
def list_failure_bundles(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(FailureBundle)
        .where(FailureBundle.task_id == task_id)
        .order_by(FailureBundle.created_at.desc())
    ).all()
    return [
        {
            "bundle_id": row.bundle_id,
            "run_id": row.run_id,
            "review_id": row.review_id,
            "failure_type": row.failure_type,
            "code_fixable": row.code_fixable,
            "status": row.status,
            "bundle": row.bundle_json,
            "artifact_manifest_ref": row.artifact_manifest_ref,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.post("/api/v1/onboarding/tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    request: ResumeRequest,
    session: DbSession,
    actor: OperatorActor,
) -> dict:
    task = _require_task(session, task_id)
    idempotency_key = f"{task_id}:resume:{request.client_request_id}"
    previous = session.scalar(
        select(WorkflowEvent).where(WorkflowEvent.idempotency_key == idempotency_key)
    )
    if previous is not None:
        return {
            "task_id": task_id,
            "status": task.status,
            "run_id": task.current_run_id,
            "idempotent": True,
        }
    active_run = None
    if task.status in {"SUBMITTED", "ANALYZING", "REPAIRING"} and task.current_run_id:
        active_run = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.task_id == task_id,
                WorkflowRun.run_id == task.current_run_id,
                WorkflowRun.status == "RUNNING",
            )
        )
    if active_run is not None:
        return {
            "task_id": task_id,
            "status": task.status,
            "run_id": active_run.run_id,
            "idempotent": True,
        }
    task.status = "SUBMITTED"
    task.next_action = "CREATE_CANDIDATE"
    session.add(
        WorkflowEvent(
            event_id=uuid4().hex,
            event_type="RESUME_REQUESTED",
            task_id=task_id,
            run_id=task.current_run_id,
            idempotency_key=idempotency_key,
            payload_json={"client_request_id": request.client_request_id},
        )
    )
    session.commit()
    enqueue_onboarding(task_id)
    return {"task_id": task_id, "status": task.status, "idempotent": False}


@app.post("/api/v1/onboarding/tasks/{task_id}/repair")
def repair_task(
    task_id: str,
    request: RepairRequest,
    session: DbSession,
    actor: OperatorActor,
) -> dict:
    _require_task(session, task_id)
    if not request.failure_bundle_id:
        raise HTTPException(status_code=400, detail="failure_bundle_id 必填")
    enqueue_repair(task_id, request.failure_bundle_id)
    return {"task_id": task_id, "status": "REPAIRING"}


@app.get("/api/v1/onboarding/tasks/{task_id}/submissions")
def list_submissions(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(CodeSubmission)
        .where(CodeSubmission.task_id == task_id)
        .order_by(CodeSubmission.created_at.desc())
    ).all()
    return [
        {
            "submission_id": row.submission_id,
            "run_id": row.run_id,
            "branch_name": row.branch_name,
            "commit_sha": row.commit_sha,
            "baseline_ref": row.baseline_ref,
            "changed_files": row.changed_files,
            "adoption_status": row.adoption_status,
            "simulated": row.simulated,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/tasks/{task_id}/submissions/{submission_id}/diff")
def submission_diff(
    task_id: str,
    submission_id: str,
    session: DbSession,
    actor: CurrentActor,
) -> dict:
    _require_task(session, task_id)
    submission = session.scalar(
        select(CodeSubmission).where(
            CodeSubmission.task_id == task_id,
            CodeSubmission.submission_id == submission_id,
        )
    )
    if submission is None:
        raise HTTPException(status_code=404, detail="候选提交不存在")
    if not submission.commit_sha:
        return {
            "submission_id": submission_id,
            "available": False,
            "reason": "候选提交尚未生成 commit",
            "diff": "",
        }

    repository = get_settings().aicoding_repo_path
    if not (repository / ".git").exists():
        return {
            "submission_id": submission_id,
            "available": False,
            "reason": "AI 产出仓库不存在",
            "diff": "",
        }
    code, diff, error = _git_output(
        repository,
        "diff",
        "--no-ext-diff",
        "--unified=3",
        submission.baseline_ref,
        submission.commit_sha,
    )
    if code != 0:
        return {
            "submission_id": submission_id,
            "available": False,
            "reason": error.strip() or "无法读取候选 diff",
            "diff": "",
        }
    max_chars = 200_000
    return {
        "submission_id": submission_id,
        "available": True,
        "baseline_ref": submission.baseline_ref,
        "commit_sha": submission.commit_sha,
        "changed_files": submission.changed_files,
        "truncated": len(diff) > max_chars,
        "diff": diff[:max_chars],
    }


@app.get("/api/v1/onboarding/tasks/{task_id}/evidence")
def list_evidence(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    _require_task(session, task_id)
    rows = session.scalars(
        select(EvidenceFile)
        .where(EvidenceFile.task_id == task_id)
        .order_by(EvidenceFile.created_at.desc())
    ).all()
    return [
        {
            "evidence_id": row.evidence_id,
            "relative_path": row.relative_path,
            "file_type": row.file_type,
            "sha256": row.sha256,
            "size_bytes": row.size_bytes,
            "redaction_status": row.redaction_status,
        }
        for row in rows
    ]


@app.get("/api/v1/onboarding/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, session: DbSession, actor: CurrentActor) -> FileResponse:
    evidence = session.scalar(select(EvidenceFile).where(EvidenceFile.evidence_id == evidence_id))
    if evidence is None:
        raise HTTPException(status_code=404, detail="证据文件不存在")
    root = get_settings().evidence_root.resolve()
    target = (root / evidence.relative_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="证据文件不可用")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=Path(evidence.relative_path).name,
    )


@app.get("/api/v1/onboarding/tasks/{task_id}/validation")
def validation_status(task_id: str, session: DbSession, actor: CurrentActor) -> dict:
    report = latest_report(session, task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="验证结果尚未生成")
    return report.report_json.get("validation", {})
