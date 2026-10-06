"""Company-level progress is derived from persisted tasks/reports, never model predictions."""

from collections import Counter

from sqlalchemy import select

from auto_spider.db.models import (
    CodeSubmission,
    ManualRun,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
)


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
    # Include existing tasks referenced by duplicate import rows, then fetch
    # all projections in batches.  The report is polled by the UI and imports
    # may contain thousands of rows; one query per task would make that poll
    # increasingly expensive.
    import_rows = (batch.intake_json or {}).get("result", {}).get("rows", [])
    duplicate_ids = {
        row.get("existing_task_id")
        for row in import_rows
        if row.get("status") == "DUPLICATE" and row.get("existing_task_id")
    }
    task_by_id = {task.task_id: task for task in tasks}
    if duplicate_ids:
        duplicates = session.scalars(
            select(OnboardingTask).where(OnboardingTask.task_id.in_(duplicate_ids))
        ).all()
        for task in duplicates:
            task_by_id.setdefault(task.task_id, task)
    normal_task_ids = {task.task_id for task in tasks}
    all_tasks = list(tasks) + [
        task for task_id, task in task_by_id.items() if task_id not in normal_task_ids
    ]
    all_task_ids = [task.task_id for task in all_tasks]
    reports = {
        report.report_id: report
        for report in session.scalars(
            select(OnboardingReport).where(
                OnboardingReport.report_id.in_(
                    [task.last_report_id for task in all_tasks if task.last_report_id]
                )
            )
        )
    }
    submissions = {}
    for row in session.scalars(
        select(CodeSubmission)
        .where(
            CodeSubmission.task_id.in_(all_task_ids),
            CodeSubmission.adoption_status.in_(["candidate", "adopted"]),
        )
        .order_by(CodeSubmission.id.desc())
    ):
        submissions.setdefault(row.task_id, row)
    manual_runs = {}
    manual_data_runs = {}
    for row in session.scalars(
        select(ManualRun)
        .where(ManualRun.task_id.in_(all_task_ids))
        .order_by(ManualRun.id.desc())
    ):
        manual_runs.setdefault(row.task_id, row)
        result = row.result_json or {}
        if (
            row.status in {"WAITING_REVIEW", "REVIEWED", "COMPLETED"}
            and (result.get("records_ref") or (result.get("record_count") or 0) > 0)
        ):
            manual_data_runs.setdefault(row.task_id, row)
    entries = []

    def entry_for(task, *, import_status=None, existing_task_id=None):
        report = reports.get(task.last_report_id) if task.last_report_id else None
        submission = submissions.get(task.task_id)
        manual_run = manual_runs.get(task.task_id)
        data_run = manual_data_runs.get(task.task_id)
        manual_result = (manual_run.result_json or {}) if manual_run else {}
        # A failed retry should remain visible as the current status, but it
        # must not hide the last successful samples that the reviewer can
        # still inspect on the data page.
        data_result = (data_run.result_json or {}) if data_run else manual_result
        quality = manual_result.get("quality")
        if not isinstance(quality, dict) and data_run is not None:
            quality = data_result.get("quality")
        quality = quality if isinstance(quality, dict) else None
        latest_has_data = bool(
            manual_result.get("records_ref") or (manual_result.get("record_count") or 0) > 0
        )
        if latest_has_data:
            display_record_count = manual_result.get("record_count")
        elif data_run:
            display_record_count = data_result.get("record_count")
        else:
            display_record_count = manual_result.get("record_count")
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
            # Keep the batch report useful for triage without copying raw
            # records into the report.  The data page remains the source for
            # full samples and reviewer decisions.
            "manual_run_id": manual_run.manual_run_id if manual_run else None,
            "manual_run_status": manual_run.status if manual_run else None,
            "record_count": display_record_count,
            "quality_source_manual_run_id": data_run.manual_run_id if data_run else None,
            "quality_status": quality.get("status") if quality else None,
            "quality_metrics": quality.get("metrics") if quality else None,
            "quality_finding_count": len(quality.get("findings", [])) if quality else 0,
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
    for row in import_rows:
        if row.get("status") != "DUPLICATE":
            continue
        existing_id = row.get("existing_task_id")
        if not existing_id or existing_id in normal_task_ids:
            continue
        existing = task_by_id.get(existing_id)
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
