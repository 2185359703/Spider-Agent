from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from auto_spider.api.deps import CurrentActor, DbSession
from auto_spider.config import get_settings
from auto_spider.db.models import CodeSubmission, EvidenceFile
from auto_spider.schemas import (
    CreateBatchRequest,
    CreateBatchResponse,
    ManualReviewRequest,
    ManualRunRequest,
    RepairRequest,
    ReportResponse,
    ResumeRequest,
    TaskResponse,
)
from auto_spider.services.tasks import (
    create_batch,
    create_manual_run,
    create_review,
    get_task,
    latest_report,
)
from auto_spider.workers.tasks import run_onboarding, run_repair

app = FastAPI(title="AI Recruitment Collector Onboarding", version="0.1.0")


def enqueue_onboarding(task_id: str) -> None:
    settings = get_settings()
    if settings.auto_spider_eager_workflow:
        run_onboarding(task_id)
    elif settings.queue_enabled and hasattr(run_onboarding, "delay"):
        run_onboarding.delay(task_id)


def enqueue_repair(task_id: str, bundle_id: str) -> None:
    settings = get_settings()
    if settings.auto_spider_eager_workflow:
        run_repair(task_id, bundle_id)
    elif settings.queue_enabled and hasattr(run_repair, "delay"):
        run_repair.delay(task_id, bundle_id)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "auto-spider"}


@app.post(
    "/api/v1/onboarding/batches",
    response_model=CreateBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_onboarding_batch(
    request: CreateBatchRequest,
    session: DbSession,
    actor: CurrentActor,
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
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskResponse.model_validate(task)


@app.get("/api/v1/onboarding/tasks/{task_id}/reports/latest", response_model=ReportResponse)
def read_latest_report(task_id: str, session: DbSession, actor: CurrentActor) -> ReportResponse:
    if get_task(session, task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    report = latest_report(session, task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告尚未生成")
    return ReportResponse.model_validate(report)


@app.post("/api/v1/onboarding/tasks/{task_id}/manual-runs")
def register_manual_run(
    task_id: str,
    request: ManualRunRequest,
    session: DbSession,
    actor: CurrentActor,
) -> dict:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    manual = create_manual_run(session, task, request)
    return {"manual_run_id": manual.manual_run_id, "status": manual.status}


@app.post("/api/v1/onboarding/tasks/{task_id}/reviews")
def submit_review(
    task_id: str,
    request: ManualReviewRequest,
    session: DbSession,
    actor: CurrentActor,
) -> dict:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    review, bundle = create_review(session, task, request)
    if bundle is not None:
        enqueue_repair(task_id, bundle.bundle_id)
    return {
        "review_id": review.review_id,
        "failure_bundle_id": bundle.bundle_id if bundle else None,
        "status": task.status,
    }


@app.post("/api/v1/onboarding/tasks/{task_id}/resume")
def resume_task(
    task_id: str,
    request: ResumeRequest,
    session: DbSession,
    actor: CurrentActor,
) -> dict:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.status = "SUBMITTED"
    task.next_action = "CREATE_CANDIDATE"
    session.commit()
    enqueue_onboarding(task_id)
    return {"task_id": task_id, "status": task.status}


@app.post("/api/v1/onboarding/tasks/{task_id}/repair")
def repair_task(
    task_id: str,
    request: RepairRequest,
    session: DbSession,
    actor: CurrentActor,
) -> dict:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not request.failure_bundle_id:
        raise HTTPException(status_code=400, detail="failure_bundle_id 必填")
    enqueue_repair(task_id, request.failure_bundle_id)
    return {"task_id": task_id, "status": "REPAIRING"}


@app.get("/api/v1/onboarding/tasks/{task_id}/submissions")
def list_submissions(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    if get_task(session, task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = session.query(CodeSubmission).filter(CodeSubmission.task_id == task_id).all()
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


@app.get("/api/v1/onboarding/tasks/{task_id}/evidence")
def list_evidence(task_id: str, session: DbSession, actor: CurrentActor) -> list[dict]:
    if get_task(session, task_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = session.query(EvidenceFile).filter(EvidenceFile.task_id == task_id).all()
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


@app.get("/api/v1/onboarding/tasks/{task_id}/validation")
def validation_status(task_id: str, session: DbSession, actor: CurrentActor) -> dict:
    report = latest_report(session, task_id)
    if report is None:
        raise HTTPException(status_code=404, detail="验证结果尚未生成")
    return report.report_json.get("validation", {})
