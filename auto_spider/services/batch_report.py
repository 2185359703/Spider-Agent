"""Company-level progress is derived from persisted tasks/reports, never model predictions."""

from collections import Counter

from sqlalchemy import select

from auto_spider.db.models import CodeSubmission, OnboardingBatch, OnboardingReport, OnboardingTask


def _report_reason(report: OnboardingReport | None) -> str | None:
    if report is None:
        return None
    payload = report.report_json or {}
    if payload.get("gate_reason"):
        return str(payload["gate_reason"])
    if payload.get("stop_reason"):
        return f"流程停止：{payload['stop_reason']}"
    unresolved = payload.get("unresolved") or []
    if unresolved:
        return "待处理：" + "、".join(str(item) for item in unresolved[:5])
    validation = payload.get("validation") or {}
    failed = [
        name
        for name in ("compile", "pytest", "ruff", "contract", "business", "live")
        if str(validation.get(f"{name}_status", "")).upper() == "FAIL"
    ]
    if failed:
        return "验证失败：" + "、".join(failed)
    if report.technical_status not in {"PASS", "NOT_RUN"}:
        return f"技术状态：{report.technical_status}"
    return None


def build_batch_report(session, batch_id):
    batch = session.scalar(select(OnboardingBatch).where(OnboardingBatch.batch_id == batch_id))
    if not batch:
        raise ValueError("批次不存在")
    tasks = list(
        session.scalars(
            select(OnboardingTask)
            .where(OnboardingTask.batch_id == batch_id)
            .order_by(OnboardingTask.id)
        )
    )
    entries = []

    def entry_for(task, *, import_status=None, existing_task_id=None):
        report = (
            session.scalar(
                select(OnboardingReport).where(OnboardingReport.report_id == task.last_report_id)
            )
            if task.last_report_id
            else None
        )
        submission = session.scalar(
            select(CodeSubmission)
            .where(
                CodeSubmission.task_id == task.task_id,
                CodeSubmission.adoption_status.in_(["candidate", "adopted"]),
            )
            .order_by(CodeSubmission.id.desc())
            .limit(1)
        )
        value = {
            "task_id": task.task_id,
            "company_name": task.platform_name or task.platform_key,
            "entry_url": task.entry_url,
            "status": task.status,
            "next_action": task.next_action,
            "observation_code": report.observation_code if report else None,
            "technical_status": report.technical_status if report else "NOT_RUN",
            "commit_sha": submission.commit_sha if submission else None,
            "submission_id": submission.submission_id if submission else None,
            "reason": _report_reason(report),
        }
        if import_status:
            value.update(import_status=import_status, existing_task_id=existing_task_id)
        return value

    for task in tasks:
        entries.append(entry_for(task))

    # A duplicate URL is intentionally not copied into the batch as a second
    # task.  Include its existing task in the report, however, so a nine-line
    # import still has a nine-company review surface and the operator can see
    # the latest known result for that company.
    task_by_id = {task.task_id: task for task in tasks}
    import_rows = (batch.intake_json or {}).get("result", {}).get("rows", [])
    for row in import_rows:
        if row.get("status") != "DUPLICATE":
            continue
        existing_id = row.get("existing_task_id")
        if not existing_id or existing_id in task_by_id:
            continue
        existing = session.scalar(
            select(OnboardingTask).where(OnboardingTask.task_id == existing_id)
        )
        if existing is not None:
            entries.append(
                entry_for(existing, import_status="DUPLICATE", existing_task_id=existing_id)
            )
    terminal = {
        "WAITING_MANUAL_RUN",
        "WAITING_MANUAL_REVIEW",
        "ADOPTED",
        "NO_DATA_CONFIRMED",
        "BLOCKED",
        "FAILED",
        "CANCELLED",
        "REJECTED",
        "TIMED_OUT",
    }
    return {
        "batch_id": batch_id,
        "status": batch.status,
        "requested_count": batch.requested_count,
        "accepted_count": batch.accepted_count,
        "import": (batch.intake_json or {}).get("result"),
        "entries": entries,
        "summary": {
            "companies": len({e["company_name"] for e in entries}),
            "entries": len(entries),
            "finished": sum(e["status"] in terminal for e in entries),
            "status_counts": dict(Counter(e["status"] for e in entries)),
            "observation_counts": dict(
                Counter(e["observation_code"] for e in entries if e["observation_code"])
            ),
        },
    }
