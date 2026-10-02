"""Execute a pinned collector commit; never invoke the legacy database crawler service."""

from __future__ import annotations

import inspect
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    CollectionDispatch,
    ManualRun,
    OnboardingTask,
    WorkflowRun,
)
from auto_spider.db.session import SessionLocal
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.collection_dispatch import check_collection, claim_collection
from auto_spider.services.collection_quality import assess_collection_quality
from auto_spider.services.dispatch import queue_workflow
from auto_spider.services.evidence import EvidenceStore, sanitize
from auto_spider.services.request_interval import install_request_interval
from auto_spider.services.workflow_logs import append_durable_log
from auto_spider.workers.celery_app import celery_app


def filter_publication_range(records, days, now=None):
    if not days:
        return records
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    selected = []
    for item in records:
        value = item.get("publish_time")
        try:
            if not value or str(value) == "0":
                selected.append(item)
                continue
            if isinstance(value, (int, float)) or str(value).isdigit():
                seconds = float(value)
                published = datetime.fromtimestamp(
                    seconds / 1000 if seconds > 1e12 else seconds, UTC
                )
            else:
                published = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if not published.tzinfo:
                    from zoneinfo import ZoneInfo

                    published = published.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            if published >= cutoff:
                selected.append(item)
        except (TypeError, ValueError, OverflowError, OSError):
            selected.append(item)
    return selected


def execute_collection(run_id: str, session_factory=SessionLocal) -> dict:
    with session_factory() as session:
        run = session.scalar(
            select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
        )
        if not run or run.status not in {"QUEUED", "CANCEL_REQUESTED"}:
            return {"status": run.status if run else "NOT_FOUND"}
        if run.status == "CANCEL_REQUESTED":
            receipt = session.get(CollectionDispatch, run_id)
            if receipt and receipt.owner:
                return {"status": "RUNNING"}
            run.status = "CANCELLED"
            if receipt:
                receipt.status = "DONE"
            session.commit()
            return {"status": "CANCELLED"}
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == run.task_id))
        selected = session.scalar(
            select(CodeSubmission).where(
                CodeSubmission.task_id == run.task_id,
                CodeSubmission.commit_sha == run.code_revision,
                CodeSubmission.submission_id
                == run.result_json.get("options", {}).get("submission_id"),
            )
        )
        key, commit, task_id, workflow_id = (
            task.platform_key,
            run.code_revision,
            task.task_id,
            run.result_json.get("workflow_run_id")
            or (selected.run_id if selected else task.current_run_id),
        )
        options = run.result_json.get("options", {})
        owner = claim_collection(session, run)
        if not owner:
            return {"status": run.status}
        session.commit()

    def log(message, level="INFO", **detail):
        append_durable_log(
            task_id=task_id,
            run_id=workflow_id,
            stage="manual_collection",
            message=message,
            level=level,
            detail={"manual_run_id": run_id, **detail},
            session_factory=session_factory,
        )

    log("人工触发采集，正在准备指定版本", commit_sha=commit)
    records, error, status, tail, source_count = [], None, "WAITING_REVIEW", "", 0
    pagination = {}
    try:
        if sys.platform != "linux":
            raise RuntimeError("COLLECTION_SANDBOX_UNAVAILABLE: 请在 Linux 容器执行")
        with tempfile.TemporaryDirectory(prefix="collector-run-") as directory:
            root = Path(directory)
            workspace, scratch = root / "checkout", root / "scratch"
            workspace.mkdir()
            scratch.mkdir()
            archive = subprocess.run(
                ["git", "-C", str(get_settings().aicoding_repo_path), "archive", commit],
                capture_output=True,
                check=True,
                timeout=30,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                members = bundle.getmembers()
                if any(not (m.isfile() or m.isdir()) for m in members):
                    raise RuntimeError("COLLECTION_ARCHIVE_UNSAFE: 不允许符号链接或特殊文件")
                if sum(m.size for m in members) > 100_000_000:
                    raise RuntimeError("COLLECTION_ARCHIVE_TOO_LARGE")
                members = [
                    m
                    for m in members
                    if not any(
                        part.startswith(".env") or part in {"output", "logs", ".git"}
                        for part in Path(m.name).parts
                    )
                ]
                bundle.extractall(workspace, members=members, filter="data")
            script = (
                "import json, sys\n"
                "from config.platforms import get_platform\n"
                "from collectors.registry import CollectorRegistry\n"
                f"platform = get_platform({key!r})\n"
                f"collector = CollectorRegistry.create(platform, crawl_task={run_id!r}, "
                f"crawl_batch={run_id!r}, crawl_version='v1.0.0', "
                f"runtime_options={{'max_pages': {options.get('max_pages', 50)}}})\n"
                f"collector.max_pages = {options.get('max_pages', 50)}\n"
                "records = collector.collect()\n"
                f"with open({str(scratch / 'pagination.json')!r}, 'w', encoding='utf-8') as out:\n"
                "    pagination = getattr(collector, 'collection_diagnostics', {})\n"
                "    json.dump(pagination, out, default=str)\n"
                "items = [r.to_dict() if hasattr(r, 'to_dict') else r for r in records]\n"
                "for item in items:\n"
                "    job = item.get('raw_content', {}).get('job', {})\n"
                "    if any(item.get(k) is not None for k in "
                "('source_platform', 'platform_id', 'entity_id')) or "
                "any(job.get(k) is not None for k in ('platform_id', 'entity_id')):\n"
                "        raise RuntimeError('COLLECTION_IDS_MUST_BE_NULL')\n"
                f"with open({str(scratch / 'records.json')!r}, 'w', encoding='utf-8') as out:\n"
                "    json.dump(items, out, ensure_ascii=False, default=str)\n"
                "print('Collection complete:', len(items))\n"
            )
            script = (
                inspect.getsource(install_request_interval)
                + f"\ninstall_request_interval({options.get('request_interval_seconds', 1)!r})\n"
                + script
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if k
                in {
                    "PATH",
                    "LANG",
                    "LC_ALL",
                    "TZ",
                    "SSL_CERT_FILE",
                    "SSL_CERT_DIR",
                }
            }
            env.update(
                HOME=str(scratch),
                TMPDIR=str(scratch),
                PYTHONPATH=str(workspace),
                PYTHONDONTWRITEBYTECODE="1",
                APP_ENV="local",
            )
            launcher = Path(__file__).parents[1] / "validators" / "sandbox_exec.py"
            log(
                "正在运行纯 HTTP 采集器",
                action="collection_start",
                max_pages=options.get("max_pages"),
            )
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(launcher),
                        str(workspace),
                        str(scratch),
                        sys.executable,
                        "-c",
                        script,
                    ],
                    cwd=workspace,
                    env=env,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
                deadline = time.monotonic() + options.get("timeout_seconds", 600)
                try:
                    while process.poll() is None:
                        check_collection(session_factory, run_id, owner)
                        if time.monotonic() > deadline:
                            status = "TIMED_OUT"
                            raise RuntimeError("采集超时，请检查接口或调整执行时限")
                        time.sleep(0.5)
                    output.seek(0, os.SEEK_END)
                    output.seek(max(0, output.tell() - 5000))
                    tail = sanitize_text(output.read().decode("utf-8", errors="replace"))
                    log("采集器执行输出", action="collection_output", output=tail)
                    if process.returncode:
                        raise RuntimeError(f"采集器执行失败（{process.returncode}）：{tail}")
                    records = json.loads((scratch / "records.json").read_text("utf-8"))
                    pagination = json.loads((scratch / "pagination.json").read_text("utf-8"))
                    if not isinstance(pagination, dict):
                        pagination = {}
                    source_count = len(records)
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
        records = sanitize(filter_publication_range(records, options.get("published_within_days")))
    except Exception as exc:
        error = sanitize_text(str(exc))
        if str(exc) == "COLLECTION_LEASE_LOST":
            return {"status": "STALE_DELIVERY"}
        if str(exc) == "COLLECTION_CANCELLED":
            status = "CANCELLED"
        if status == "WAITING_REVIEW":
            status = "FAILED"
    quality = assess_collection_quality(records, pagination=pagination) if not error else None
    with session_factory() as session:
        run = session.scalar(
            select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
        )
        receipt = session.get(CollectionDispatch, run_id)
        if not receipt or receipt.owner != owner:
            return {"status": "STALE_DELIVERY"}
        if run.status == "CANCEL_REQUESTED":
            status, error = "CANCELLED", "采集已停止"
        run.status, run.finished_at = status, datetime.now(UTC)
        evidence = EvidenceStore()
        diagnostics = evidence.write_json(
            session,
            task_id=task_id,
            run_id=workflow_id,
            name=f"manual-runs/{run_id}/{owner}/diagnostics.json",
            file_type="collection_diagnostics",
            payload={
                "manual_run_id": run_id,
                "code_revision": commit,
                "options": options,
                "status": status,
                "error_msg": error,
                "output": tail,
                "source_record_count": source_count,
                "record_count": len(records),
                "environment": "linux-landlock",
                "crawl_version": "v1.0.0",
            },
        )
        result = {
            **run.result_json,
            "record_count": len(records),
            "error_msg": error,
            "source_record_count": source_count,
            "diagnostics_ref": diagnostics.relative_path,
            "workflow_run_id": workflow_id,
            "quality": quality,
            "pagination_diagnostics": pagination,
        }
        if not error:
            output = evidence.write_json(
                session,
                task_id=task_id,
                run_id=workflow_id,
                name=f"manual-runs/{run_id}/{owner}/records.json",
                payload=records,
                file_type="collection_records",
            )
            run.artifact_manifest_ref = output.relative_path
            result["records_ref"] = output.relative_path
        run.result_json = result
        receipt.status, receipt.owner, receipt.last_error = "DONE", None, error
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if (
            status in {"WAITING_REVIEW", "FAILED", "TIMED_OUT"}
            and task.current_run_id == workflow_id
        ):
            if task.status in {"WAITING_MANUAL_RUN", "NO_DATA_CONFIRMED"}:
                task.status, task.next_action = "WAITING_MANUAL_REVIEW", "WAIT_REVIEW"
            current = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == workflow_id))
            if current and current.run_type == "manual_edit":
                current.status = "WAITING_MANUAL_REVIEW"
            elif (
                current
                and current.execution_key
                and current.status in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW"}
            ):
                queue_workflow(session, task_id, f"collection-complete:{run_id}")
        session.commit()
    log(
        f"采集结束：{len(records)} 条记录" if not error else error,
        "INFO" if not error else "ERROR",
        action="collection_result",
        status=status,
    )
    return {"status": status, "count": len(records)}


if celery_app:

    @celery_app.task(name="auto_spider.admin.collect_candidate", bind=True, max_retries=125)
    def collect_candidate(self, run_id):
        result = execute_collection(run_id)
        if result["status"] == "RUNNING":
            raise self.retry(countdown=30)
        return result
else:  # dependency availability is checked before launch
    collect_candidate = execute_collection
