"""Read-only Git views and human-triggered collection/review API for the admin UI."""

from __future__ import annotations

import io
import json
import re
import subprocess
import zipfile
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auto_spider.api.deps import CurrentActor, DbSession, OperatorActor, ReviewerActor
from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    CollectionDispatch,
    ManualReview,
    ManualRun,
    OnboardingReport,
    OnboardingTask,
    ValidationRun,
    WorkflowEvent,
    WorkflowRun,
)
from auto_spider.schemas import ManualReviewRequest
from auto_spider.services.evidence import sanitize
from auto_spider.services.tasks import create_review

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class CompanyImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128)


@router.get("/batches/{batch_id}/report")
def batch_report(batch_id: str, session: DbSession, actor: CurrentActor):
    from auto_spider.services.batch_report import build_batch_report

    try:
        return build_batch_report(session, batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/company-import/preview")
def company_import_preview(request: CompanyImportRequest, session: DbSession, actor: OperatorActor):
    from auto_spider.services.company_import import preview_company_import

    try:
        return preview_company_import(session, request.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/company-import", status_code=201)
def company_import_submit(request: CompanyImportRequest, session: DbSession, actor: OperatorActor):
    from auto_spider.api.main import enqueue_onboarding
    from auto_spider.services.company_import import submit_company_import

    if not request.client_request_id:
        raise HTTPException(422, "提交需要 client_request_id")
    try:
        result = submit_company_import(
            session, request.text, request.client_request_id, actor.user_id
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    for task_id in result["task_ids"]:
        task = task_or_404(session, task_id)
        if task.status == "SUBMITTED":
            enqueue_onboarding(task_id)
    return result


@router.get("/task-observations")
def task_observations(session: DbSession, actor: CurrentActor):
    rows = session.execute(
        select(OnboardingTask.task_id, OnboardingReport.observation_code).join(
            OnboardingReport, OnboardingTask.last_report_id == OnboardingReport.report_id
        )
    )
    return {task_id: observation for task_id, observation in rows}


def task_or_404(session, task_id):
    task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


def git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(get_settings().aicoding_repo_path), *args],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise HTTPException(409, "指定提交在采集代码仓库中不可用")
    return result.stdout


def source_context(session, task_id: str, submission_id: str):
    task = task_or_404(session, task_id)
    row = session.scalar(
        select(CodeSubmission).where(
            CodeSubmission.task_id == task_id,
            CodeSubmission.submission_id == submission_id,
        )
    )
    if not row or not row.commit_sha or row.simulated:
        raise HTTPException(404, "没有可读取的真实候选提交")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", row.commit_sha):
        raise HTTPException(409, "提交号无效")
    if not re.fullmatch(r"[a-zA-Z0-9_]+", task.platform_key):
        raise HTTPException(409, "平台键无效")
    names = git("ls-tree", "-r", "--name-only", row.commit_sha).decode().splitlines()
    key = task.platform_key
    files = [
        name
        for name in names
        if name
        in {
            f"collectors/{key}.py",
            f"config/platforms/{key}.toml",
            f"tests/test_{key}.py",
        }
        or name.startswith(f"tests/fixtures/{key}/")
    ]
    return task, row, files


def blob(commit: str, name: str) -> str:
    if git("cat-file", "-t", f"{commit}:{name}").strip() != b"blob":
        raise HTTPException(409, "文件类型不可读取")
    size = int(git("cat-file", "-s", f"{commit}:{name}"))
    if size > 2_000_000:
        raise HTTPException(413, "文件过大，请下载代码包查看")
    return git("show", f"{commit}:{name}").decode("utf-8", errors="replace")


@router.get("/tasks/{task_id}/code/{submission_id}")
def code_view(
    task_id: str,
    submission_id: str,
    session: DbSession,
    actor: CurrentActor,
    path: str | None = None,
):
    _, row, files = source_context(session, task_id, submission_id)
    selected = path or next((f for f in files if f.endswith(".py")), None)
    if selected and selected not in files:
        raise HTTPException(403, "文件不在当前入口允许范围内")
    validation = session.scalar(
        select(ValidationRun)
        .where(ValidationRun.run_id == row.run_id)
        .order_by(ValidationRun.id.desc())
    )
    report = session.scalar(
        select(OnboardingReport)
        .where(OnboardingReport.run_id == row.run_id)
        .order_by(OnboardingReport.id.desc())
    )
    validation_payload = None
    if validation:
        validation_payload = {
            **validation.result_json,
            "pytest_status": validation.pytest_status,
            "ruff_status": validation.ruff_status,
            "contract_status": validation.contract_status,
            "business_status": validation.business_status,
        }
    elif report and report.report_json.get("candidate_commit") == row.commit_sha:
        # Earlier locally finalized versions kept their verified results in the report.
        reported = report.report_json.get("validation")
        if isinstance(reported, dict):
            validation_payload = {
                **reported,
                "result_source": "onboarding_report",
                "report_id": report.report_id,
            }
    return {
        "commit_sha": row.commit_sha,
        "files": files,
        "path": selected,
        "content": blob(row.commit_sha, selected) if selected else "",
        "validation_status": validation.result_json.get("technical_status", "PARTIAL")
        if validation
        else report.technical_status
        if report and validation_payload
        else "NOT_RUN",
        "validation": validation_payload,
    }


class CodeEditRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=1_000_000)
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    client_request_id: str = Field(min_length=8, max_length=128)


@router.put("/tasks/{task_id}/code/{submission_id}")
def edit_code(
    task_id: str,
    submission_id: str,
    request: CodeEditRequest,
    session: DbSession,
    actor: OperatorActor,
):
    from auto_spider.services.code_edit import save_code_edit

    task, source, files = source_context(session, task_id, submission_id)
    if request.path not in files:
        raise HTTPException(403, "文件不在当前入口允许范围内")
    if request.base_commit != source.commit_sha:
        raise HTTPException(409, "代码版本已变化，请刷新")
    if request.content.replace("\r\n", "\n") == blob(source.commit_sha, request.path).replace(
        "\r\n", "\n"
    ):
        return {
            "submission_id": source.submission_id,
            "commit_sha": source.commit_sha,
            "status": source.adoption_status,
        }
    try:
        return save_code_edit(
            session,
            task,
            source,
            path=request.path,
            content=request.content,
            client_request_id=request.client_request_id,
            actor_id=actor.user_id,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks/{task_id}/code/{submission_id}/diff")
def code_diff(
    task_id: str,
    submission_id: str,
    session: DbSession,
    actor: CurrentActor,
    against: str | None = None,
):
    _, row, files = source_context(session, task_id, submission_id)
    base = source_context(session, task_id, against)[1].commit_sha if against else row.baseline_ref
    diff = git("diff", "--no-ext-diff", "--no-textconv", base, row.commit_sha, "--", *files)
    return {
        "base": base,
        "head": row.commit_sha,
        "diff": diff[:500_000].decode("utf-8", errors="replace"),
        "truncated": len(diff) > 500_000,
    }


@router.get("/tasks/{task_id}/code/{submission_id}/download")
def code_archive(task_id: str, submission_id: str, session: DbSession, actor: CurrentActor):
    task, row, files = source_context(session, task_id, submission_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.writestr(name, blob(row.commit_sha, name))
    filename = f"{task.platform_key}-{row.commit_sha[:8]}.zip"
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


class CollectRequest(BaseModel):
    submission_id: str
    client_request_id: str = Field(min_length=8, max_length=128)
    max_pages: int = Field(default=50, ge=1, le=500)
    timeout_seconds: int = Field(default=600, ge=30, le=3600)
    request_interval_seconds: float = Field(default=1, ge=0.1, le=30)
    published_within_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/tasks/{task_id}/collect", status_code=202)
def launch_collection(
    task_id: str, request: CollectRequest, session: DbSession, actor: OperatorActor
):
    from auto_spider.services.collection_dispatch import queue_collection

    task, submission, _ = source_context(session, task_id, request.submission_id)
    session.scalars(
        select(OnboardingTask)
        .where(OnboardingTask.normalized_url == task.normalized_url)
        .order_by(OnboardingTask.task_id)
        .with_for_update()
    ).all()
    session.refresh(task)
    key = f"{task_id}:collect:{request.client_request_id}"
    payload = request.model_dump()
    previous = session.scalar(select(WorkflowEvent).where(WorkflowEvent.idempotency_key == key))
    if previous:
        if previous.payload_json.get("request") != payload:
            raise HTTPException(409, "相同请求编号的参数不一致")
        run = session.scalar(
            select(ManualRun).where(
                ManualRun.manual_run_id == previous.payload_json["manual_run_id"]
            )
        )
        if (
            run.status == "FAILED"
            and run.result_json.get("error_msg") == "采集任务未能进入队列，请重试"
            and not run.result_json.get("records_ref")
        ):
            run.status = "QUEUED"
            run.result_json = {**run.result_json, "error_msg": None}
        if run.status == "QUEUED":
            queue_collection(session, run)
        return _collection_receipt(session, run)
    if submission.adoption_status not in {"candidate", "adopted"}:
        raise HTTPException(409, "只能运行当前候选或已采纳版本")
    if task.status not in {
        "WAITING_MANUAL_RUN",
        "WAITING_MANUAL_REVIEW",
        "ADOPTED",
        "NO_DATA_CONFIRMED",
    }:
        raise HTTPException(409, "当前入口尚未达到人工采集条件")
    active = session.scalar(
        select(ManualRun)
        .join(
            OnboardingTask,
            ManualRun.task_id == OnboardingTask.task_id,
        )
        .where(
            OnboardingTask.normalized_url == task.normalized_url,
            ManualRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
        )
    )
    if active:
        raise HTTPException(409, "这个招聘入口已有采集任务，请先等待或停止")
    if not get_settings().queue_enabled:
        raise HTTPException(503, "采集执行队列未启用，请在系统设置检查 worker")
    now = datetime.now(UTC)
    run = ManualRun(
        manual_run_id=uuid4().hex,
        task_id=task_id,
        code_revision=submission.commit_sha,
        command_profile="admin-http-collection-v1",
        environment_fingerprint="linux-landlock",
        started_at=now,
        finished_at=now,
        artifact_manifest_ref="pending",
        status="QUEUED",
        result_json={
            "options": payload,
            "decisions": {},
            "requested_by": actor.user_id,
            "workflow_run_id": submission.run_id,
        },
    )
    session.add(run)
    session.add(
        WorkflowEvent(
            event_id=uuid4().hex,
            task_id=task_id,
            run_id=task.current_run_id,
            event_type="COLLECTION_REQUESTED",
            idempotency_key=key,
            payload_json={"request": payload, "manual_run_id": run.manual_run_id},
        )
    )
    session.flush()
    queue_collection(session, run)
    return _collection_receipt(session, run)


def _collection_receipt(session, run):
    from auto_spider.services.admin_collection import collect_candidate
    from auto_spider.services.collection_dispatch import publish_collection

    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    publish_collection(
        factory,
        run.manual_run_id,
        lambda run_id: collect_candidate.apply_async(args=[run_id], queue="validation"),
        force=True,
    )
    session.refresh(run)
    dispatch = session.get(CollectionDispatch, run.manual_run_id)
    return {
        "manual_run_id": run.manual_run_id,
        "status": run.status,
        "dispatch_status": dispatch.status if dispatch else "DONE",
    }


def run_records(run):
    from auto_spider.services.failure_bundles import collection_records

    try:
        return collection_records(run)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/records")
def collected_records(
    session: DbSession,
    actor: CurrentActor,
    task_id: str | None = None,
    manual_run_id: str | None = None,
    company_name: str | None = None,
    search: str = "",
    state: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    export: bool = False,
):
    query = (
        select(ManualRun, OnboardingTask)
        .join(
            OnboardingTask,
            ManualRun.task_id == OnboardingTask.task_id,
        )
        .order_by(ManualRun.created_at.desc())
    )
    if task_id:
        query = query.where(ManualRun.task_id == task_id)
    if manual_run_id:
        query = query.where(ManualRun.manual_run_id == manual_run_id)
    if company_name:
        query = query.where(OnboardingTask.platform_name == company_name)
    rows, runs, task_counts = [], [], {}
    for run, task in session.execute(query):
        runs.append(
            {
                "manual_run_id": run.manual_run_id,
                "task_id": task.task_id,
                "code_revision": run.code_revision,
                "status": run.status,
                "created_at": run.created_at,
                "error_msg": (run.result_json or {}).get("error_msg"),
                "quality": (run.result_json or {}).get("quality"),
                "record_count": (run.result_json or {}).get("record_count"),
                "empty_conclusion": (run.result_json or {}).get("empty_conclusion"),
            }
        )
        decisions = (run.result_json or {}).get("decisions", {})
        records = run_records(run)
        runs[-1]["record_count"] = len(records)
        if task.task_id not in task_counts:
            task_counts[task.task_id] = {"count": len(records), "date": run.created_at}
        for index, item in enumerate(records, 1):
            if not isinstance(item, dict):
                continue
            item = sanitize(item)
            raw = item.get("raw_content") or {"job": item}
            if not isinstance(raw, dict):
                raw = {"unparsed": raw}
            job = raw.get("job") if isinstance(raw.get("job"), dict) else item
            decision = decisions.get(str(index), {})
            record = {
                **item,
                "raw_content": raw,
                "title": job.get("title"),
                "description": job.get("description"),
                "requirements": job.get("requirements"),
                "task_id": task.task_id,
                "source_name": item.get("source_name") or task.platform_name,
                "source_url": item.get("source_url") or job.get("source_url"),
                "publish_time": item.get("publish_time", job.get("publish_time")),
                "sample_index": index,
                "manual_run_id": run.manual_run_id,
                "code_revision": run.code_revision,
                "review": decision,
                "review_status": decision.get("status", "UNCHECKED"),
            }
            if state in {"success", "failed"}:
                if record.get("crawl_status") != (1 if state == "success" else 0):
                    continue
            elif state and record["review_status"] != state:
                continue
            if search and search.casefold() not in json.dumps(job, ensure_ascii=False).casefold():
                continue
            rows.append(record)
    if export:
        return Response(
            json.dumps(rows, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="collected-records.json"'},
        )
    start = (page - 1) * page_size
    summary = {
        "success": sum(row.get("crawl_status") == 1 for row in rows),
        "failed": sum(row.get("crawl_status") == 0 for row in rows),
        "missing_publish_time": sum(row.get("publish_time") in (None, 0, "0", "") for row in rows),
    }
    return {
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "records": rows[start : start + page_size],
        "runs": runs,
        "task_counts": task_counts,
        "summary": summary,
    }


@router.get("/runs/{run_id}/review-summary")
def review_summary(run_id: str, session: DbSession, actor: CurrentActor):
    run = session.scalar(select(ManualRun).where(ManualRun.manual_run_id == run_id))
    if not run:
        raise HTTPException(404, "采集轮次不存在")
    records = run_records(run)
    decisions = (run.result_json or {}).get("decisions", {})
    issues, samples = [], []
    for finding in ((run.result_json or {}).get("quality") or {}).get("findings", []):
        if finding.get("severity") == "error":
            issues.append(
                {
                    "field": finding.get("field", "other"),
                    "issue_type": "other",
                    "description": finding.get("message") or finding["code"],
                    "sample_indices": finding.get("sample_indices", []),
                    "actual": str(finding.get("actual"))
                    if finding.get("actual") is not None
                    else None,
                    "expected": str(finding.get("expected"))
                    if finding.get("expected") is not None
                    else None,
                    "evidence_refs": [],
                }
            )
    for index, value in decisions.items():
        refs = []
        if value.get("status") == "ISSUE":
            refs = [len(issues)]
            issues.append(
                {
                    "field": value.get("field", "other"),
                    "issue_type": "incorrect",
                    "description": value.get("note") or "人工核对发现问题",
                    "sample_indices": [int(index)],
                    "evidence_refs": [],
                }
            )
        samples.append(
            {
                "sample_index": int(index),
                "status": value["status"],
                "issue_refs": refs,
                "note": value.get("note"),
            }
        )
    return {
        "total": len(records),
        "checked": len(samples),
        "issue_count": len(issues),
        "manual_run_id": run_id,
        "code_revision": run.code_revision,
        "sample_count": len(samples),
        "field_issues": issues,
        "sample_decisions": samples,
    }


class FinalizeRequest(BaseModel):
    review_status: Literal["PASS", "CODE_FIX_REQUIRED", "NO_DATA_CONFIRMED"]
    client_request_id: str = Field(min_length=8, max_length=128)
    observation_code: (
        Literal["NO_JOBS_OBSERVED", "NO_INTERNSHIPS_OBSERVED", "INCONCLUSIVE"] | None
    ) = None
    comment: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_empty_conclusion(self):
        if self.review_status == "NO_DATA_CONFIRMED" and (
            not self.observation_code or not self.comment.strip()
        ):
            raise ValueError("空结果确认需要选择结论并填写核对依据")
        return self


@router.post("/runs/{run_id}/finalize")
def finalize_review(
    run_id: str, request: FinalizeRequest, session: DbSession, actor: ReviewerActor
):
    return _finalize_review(run_id, request, session, actor)


def _finalize_review(run_id, request, session, actor, *, commit=True):
    from auto_spider.api.main import enqueue_onboarding, enqueue_repair

    run = session.scalar(
        select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
    )
    if not run:
        raise HTTPException(404, "采集轮次不存在")
    previous = session.scalar(select(ManualReview).where(ManualReview.manual_run_id == run_id))
    if previous:
        if previous.review_status != request.review_status:
            raise HTTPException(409, "本轮最终审查已经提交")
        if request.review_status == "CODE_FIX_REQUIRED" and request.comment.strip():
            if previous.issue_summary != sanitize(request.comment.strip()):
                raise HTTPException(409, "本轮问题说明已经提交")
        if request.review_status == "NO_DATA_CONFIRMED":
            original = run.result_json.get("empty_conclusion", {})
            if original.get("observation_code") != request.observation_code or original.get(
                "comment"
            ) != sanitize(request.comment.strip()):
                raise HTTPException(409, "本轮空结果结论已经提交")
        return {"review_id": previous.review_id, "status": "REVIEWED"}
    if run.status not in {"WAITING_REVIEW", "FAILED", "TIMED_OUT"}:
        raise HTTPException(409, "本轮采集尚未完成或已停止")
    task = task_or_404(session, run.task_id)
    session.refresh(task, with_for_update=True)
    if task.status not in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW", "ADOPTED"}:
        raise HTTPException(409, "任务正在修改代码或处理问题，请稍后审查")
    summary = review_summary(run_id, session, actor)
    error = (run.result_json or {}).get("error_msg")
    if request.review_status == "PASS" and (
        not summary["total"] or summary["issue_count"] or error
    ):
        raise HTTPException(409, "本轮没有可审查数据或仍有未处理的问题")
    if request.review_status == "CODE_FIX_REQUIRED" and not summary["issue_count"]:
        missing_records = not summary["total"] and not error
        description = error or (request.comment.strip() if missing_records else "")
        if not description:
            raise HTTPException(409, "请记录具体问题；空结果可填写官网有岗位但未采到的核对依据")
        summary["field_issues"] = [
            {
                "field": "other",
                "issue_type": "missing" if missing_records else "other",
                "description": sanitize(description),
                "sample_indices": [],
                "evidence_refs": [],
            }
        ]
    submission = session.scalar(
        select(CodeSubmission)
        .where(
            CodeSubmission.task_id == task.task_id,
            CodeSubmission.commit_sha == run.code_revision,
            CodeSubmission.adoption_status.in_(["candidate", "adopted"]),
        )
        .order_by(CodeSubmission.id.desc())
    )
    if not submission:
        raise HTTPException(409, "这个版本已被新候选替代，请运行最新版本；单条审查记录仍会保留")
    if request.review_status == "NO_DATA_CONFIRMED":
        if summary["total"] or error:
            raise HTTPException(409, "只能确认成功采集且没有数据的轮次")
        from auto_spider.services.empty_reviews import confirm_empty_result

        try:
            return confirm_empty_result(
                session, task, run, submission, request, actor, commit=commit
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    if submission.adoption_status == "adopted" and request.review_status == "PASS":
        pending = session.scalar(
            select(CodeSubmission.id)
            .where(
                CodeSubmission.task_id == task.task_id,
                CodeSubmission.adoption_status == "candidate",
            )
            .limit(1)
        )
        if pending:
            raise HTTPException(409, "请采集并审查当前候选版本")
        review = ManualReview(
            review_id=uuid4().hex,
            task_id=task.task_id,
            manual_run_id=run_id,
            code_revision=run.code_revision,
            review_status="PASS",
            reviewer_id=actor.user_id,
            sample_count=summary["sample_count"],
            sample_decisions=summary["sample_decisions"],
            issue_details=[],
            evidence_refs=[],
        )
        session.add(review)
        run.status = "REVIEWED"
        task.status, task.next_action = "ADOPTED", "CLOSE_WITH_REPORT"
        session.commit() if commit else session.flush()
        return {"review_id": review.review_id, "status": "REVIEWED"}
    if submission.adoption_status == "adopted":
        if task.status != "ADOPTED":
            raise HTTPException(409, "当前入口有新的候选任务，请先完成当前轮次，避免修复错误的版本")
        session.add(
            CodeSubmission(
                submission_id=uuid4().hex,
                task_id=task.task_id,
                run_id=submission.run_id,
                branch_name=submission.branch_name,
                commit_sha=submission.commit_sha,
                baseline_ref=submission.baseline_ref,
                changed_files=submission.changed_files,
                submission_type="repair_baseline",
                adoption_status="candidate",
                simulated=False,
            )
        )
        session.flush()
    task.status = "WAITING_MANUAL_REVIEW"
    session.flush()
    review_request = ManualReviewRequest(
        review_status=request.review_status,
        manual_run_id=run_id,
        code_revision=run.code_revision,
        sample_count=summary["sample_count"],
        field_issues=summary["field_issues"],
        sample_decisions=summary["sample_decisions"],
        issue_summary=(sanitize(request.comment.strip()) or "人工核对发现问题")
        if request.review_status == "CODE_FIX_REQUIRED"
        else None,
        client_request_id=request.client_request_id,
    )
    try:
        review, bundle = create_review(
            session, task, review_request, actor_id=actor.user_id, commit=commit
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(409, str(exc)) from exc
    if commit:
        if bundle:
            enqueue_repair(task.task_id, bundle.bundle_id)
        elif not session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.run_id == task.current_run_id, WorkflowRun.run_type == "manual_edit"
            )
        ):
            enqueue_onboarding(task.task_id)
    return {
        "review_id": review.review_id,
        "status": task.status,
        "failure_bundle_id": bundle.bundle_id if bundle else None,
    }


def company_tasks(session, name):
    tasks = session.scalars(
        select(OnboardingTask)
        .where(
            (OnboardingTask.platform_name == name)
            | (OnboardingTask.platform_name.is_(None) & (OnboardingTask.platform_key == name))
        )
        .order_by(OnboardingTask.task_id)
        .with_for_update()
    ).all()
    if not tasks:
        raise HTTPException(404, "公司不存在")
    return tasks


@router.get("/companies/review-summary")
def company_review_summary(company_name: str, session: DbSession, actor: CurrentActor):
    runs = []
    for task in company_tasks(session, company_name):
        run = session.scalar(
            select(ManualRun)
            .where(ManualRun.task_id == task.task_id)
            .order_by(ManualRun.id.desc())
            .limit(1)
        )
        if not run or run.status != "WAITING_REVIEW":
            continue
        submission = session.scalar(
            select(CodeSubmission.id)
            .where(
                CodeSubmission.task_id == task.task_id,
                CodeSubmission.commit_sha == run.code_revision,
                CodeSubmission.adoption_status.in_(["candidate", "adopted"]),
            )
            .limit(1)
        )
        if not submission:
            continue
        summary = review_summary(run.manual_run_id, session, actor)
        runs.append(
            {
                "task_id": task.task_id,
                "manual_run_id": run.manual_run_id,
                "code_revision": run.code_revision,
                "platform_key": task.platform_key,
                "total": summary["total"],
                "issue_count": summary["issue_count"],
                "error_msg": (run.result_json or {}).get("error_msg"),
            }
        )
    return {"company_name": company_name, "runs": runs}


class CompanyReviewRun(BaseModel):
    task_id: str
    manual_run_id: str
    code_revision: str


class CompanyReviewRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    runs: list[CompanyReviewRun] = Field(min_length=1, max_length=100)
    client_request_id: str = Field(min_length=8, max_length=128)


@router.post("/companies/approve")
def approve_company(request: CompanyReviewRequest, session: DbSession, actor: ReviewerActor):
    from auto_spider.api.main import enqueue_onboarding

    tasks = {t.task_id: t for t in company_tasks(session, request.company_name)}
    results = []
    try:
        if len({r.task_id for r in request.runs}) != len(request.runs):
            raise HTTPException(422, "同一入口不能重复提交")
        for snapshot in request.runs:
            if snapshot.task_id not in tasks:
                raise HTTPException(409, "审查入口不属于所选公司")
            run = session.scalar(
                select(ManualRun)
                .where(ManualRun.task_id == snapshot.task_id)
                .order_by(ManualRun.id.desc())
                .limit(1)
                .with_for_update()
            )
            if (
                not run
                or run.manual_run_id != snapshot.manual_run_id
                or run.code_revision != snapshot.code_revision
            ):
                raise HTTPException(409, "采集版本已变化，请刷新后审查")
            results.append(
                _finalize_review(
                    run.manual_run_id,
                    FinalizeRequest(
                        review_status="PASS", client_request_id=request.client_request_id
                    ),
                    session,
                    actor,
                    commit=False,
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    for snapshot in request.runs:
        task = tasks[snapshot.task_id]
        if not session.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.run_id == task.current_run_id, WorkflowRun.run_type == "manual_edit"
            )
        ):
            enqueue_onboarding(snapshot.task_id)
    return {"company_name": request.company_name, "status": "PASS", "reviews": results}


class DecisionRequest(BaseModel):
    status: str = Field(pattern=r"^(PASS|ISSUE)$")
    field: str = "other"
    note: str = Field(default="", max_length=5000)


@router.put("/runs/{run_id}/records/{index}/review")
def review_record(
    run_id: str, index: int, request: DecisionRequest, session: DbSession, actor: ReviewerActor
):
    run = session.scalar(
        select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
    )
    if not run:
        raise HTTPException(404, "采集轮次不存在")
    if run.status not in {"WAITING_REVIEW", "COMPLETED"}:
        raise HTTPException(409, "该轮次尚未完成或已经提交最终审查")
    if index < 1 or index > len(run_records(run)):
        raise HTTPException(404, "岗位不存在")
    if request.status == "ISSUE" and not request.note.strip():
        raise HTTPException(422, "请填写具体的问题")
    if request.field not in {
        "source_id",
        "title",
        "source_url",
        "description",
        "requirements",
        "publish_time",
        "location",
        "internship_filter",
        "pagination",
        "other",
    }:
        raise HTTPException(422, "问题字段无效")
    decisions = dict((run.result_json or {}).get("decisions", {}))
    decisions[str(index)] = {
        **sanitize(request.model_dump()),
        "reviewer": actor.user_id,
        "at": datetime.now(UTC).isoformat(),
    }
    run.result_json = {**run.result_json, "decisions": decisions}
    session.commit()
    return decisions[str(index)]


@router.post("/runs/{run_id}/cancel")
def stop_collection(run_id: str, session: DbSession, actor: OperatorActor):
    run = session.scalar(
        select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
    )
    if not run:
        raise HTTPException(404, "采集轮次不存在")
    dispatch = session.get(CollectionDispatch, run_id)
    if run.status == "QUEUED" and (not dispatch or not dispatch.owner):
        run.status, run.finished_at = "CANCELLED", datetime.now(UTC)
        if dispatch:
            dispatch.status = "DONE"
        session.commit()
    elif run.status in {"QUEUED", "RUNNING"}:
        run.status = "CANCEL_REQUESTED"
        session.commit()
    return {"status": run.status}
