"""Save human edits as new local versions; preserve every reviewed code snapshot."""

import ast
import hashlib
import json
import tomllib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.db.models import (
    CodeSubmission,
    ManualRun,
    OnboardingReport,
    OnboardingTask,
    ValidationRun,
    WorkflowRun,
)
from auto_spider.git.publisher import publish_candidate
from auto_spider.git.target import CollectorRepository
from auto_spider.git.worktree import WorktreeManager
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.execution import ExecutionContext
from auto_spider.services.resource_cleanup import clear_checkout_cache
from auto_spider.services.version_binding import binding_for, manual_spec_revision
from auto_spider.validators.candidate import validate_candidate


def save_code_edit(session, task, source, *, path, content, client_request_id, actor_id=None):
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("代码文件过大")
    if sanitize_text(content) != content:
        raise ValueError("代码包含敏感信息")
    try:
        if path.endswith(".py"):
            ast.parse(content, filename=path)
        elif path.endswith(".toml"):
            tomllib.loads(content)
        elif path.endswith(".json"):
            json.loads(content)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"文件格式错误：{exc}") from exc
    digest = hashlib.sha256(
        json.dumps([source.submission_id, path, content], ensure_ascii=False).encode()
    ).hexdigest()
    key = hashlib.sha256(f"edit:{task.task_id}:{client_request_id}".encode()).hexdigest()
    session.refresh(task, with_for_update=True)
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.execution_key == key))
    if run:
        if run.context_json.get("request_hash") != digest:
            raise ValueError("相同请求编号的修改内容不一致")
        if run.context_json.get("submission_id"):
            return {k: run.context_json[k] for k in ("submission_id", "commit_sha", "status")}
        raise ValueError("保存正在进行，请稍后刷新")
    if task.status not in {
        "WAITING_MANUAL_RUN",
        "WAITING_MANUAL_REVIEW",
        "ADOPTED",
        "BLOCKED",
        "FAILED",
    }:
        raise ValueError("当前任务正在运行，暂时不能修改代码")
    if session.scalar(
        select(ManualRun.id)
        .where(
            ManualRun.task_id == task.task_id,
            ManualRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
        )
        .limit(1)
    ):
        raise ValueError("正在采集，暂时不能修改代码")
    old_status, old_run = task.status, task.current_run_id
    run = WorkflowRun(
        run_id=uuid4().hex,
        task_id=task.task_id,
        run_type="manual_edit",
        status="RUNNING",
        execution_key=key,
        context_json={
            "request_hash": digest,
            "source_submission_id": source.submission_id,
            "actor_id": actor_id,
        },
    )
    session.add(run)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    task_id, run_id = task.task_id, run.run_id
    resource = f"{task.repository_key}:{task.platform_key}"
    try:
        with ExecutionContext(factory, run_id, resource) as execution:
            session.refresh(task, with_for_update=True)
            if task.status != old_status or task.current_run_id != old_run:
                raise ValueError("任务状态已变化，请刷新后保存")
            task.status, task.current_run_id = "EDITING", run_id
            session.commit()
            manager = WorktreeManager(CollectorRepository(baseline_ref=source.commit_sha))
            context = manager.prepare(task_id, run_id, mutation_enabled=True, ref=source.commit_sha)
            access = WorkspaceAccess(
                {
                    "workspace": str(context.path),
                    "evidence": str(context.path),
                    "mode": "write",
                    "allowed_files": [path],
                }
            )
            access.write(path, content)
            result = validate_candidate(
                context.path, task.platform_key, [path], guard=execution.guard
            )
            clear_checkout_cache(context.path, manager.root)
            details = {
                **{
                    f"{name}_status": getattr(result, name).status
                    for name in ("compile", "pytest", "ruff")
                },
                "contract_status": result.contract_status,
                "business_status": result.business_status,
                "live_status": "NOT_RUN",
                "technical_status": "PARTIAL" if result.passed else "FAIL",
                "details": result.as_dict(),
            }
            publication = publish_candidate(
                str(context.path),
                platform_key=task.platform_key,
                run_id=run_id,
                baseline=source.commit_sha,
                commit_time=run.started_at.replace(tzinfo=UTC).isoformat(),
                repair=False,
                manual=True,
                guard=execution.guard,
            )
            execution.guard()
            session.expire_all()
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == task_id).with_for_update()
            )
            run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == run_id))
            submission_id = uuid4().hex
            status = "candidate" if result.passed else "draft"
            spec = manual_spec_revision(session, task, source, candidate=result.passed)
            binding = binding_for(spec, "manual_revision_requires_review")
            session.add(
                ValidationRun(
                    run_id=run_id,
                    pytest_status=result.pytest.status,
                    ruff_status=result.ruff.status,
                    contract_status=result.contract_status,
                    business_status=result.business_status,
                    result_json=details,
                )
            )
            session.add(
                CodeSubmission(
                    submission_id=submission_id,
                    task_id=task_id,
                    run_id=run_id,
                    branch_name=publication["branch_name"],
                    commit_sha=publication["commit_sha"],
                    baseline_ref=source.commit_sha,
                    changed_files=publication["changed_files"],
                    submission_type="manual_edit",
                    adoption_status=status,
                    simulated=False,
                )
            )
            if result.passed:
                for previous in session.scalars(
                    select(CodeSubmission).where(
                        CodeSubmission.task_id == task_id,
                        CodeSubmission.adoption_status == "candidate",
                    )
                ):
                    if previous.submission_id != submission_id:
                        previous.adoption_status = "superseded"
                task.status, task.current_run_id, task.next_action = (
                    "WAITING_MANUAL_RUN",
                    run_id,
                    "WAIT_MANUAL_RUN",
                )
            else:
                task.status, task.current_run_id = old_status, old_run
            run.status = "WAITING_MANUAL_RUN" if result.passed else "BLOCKED"
            run.worktree_path, run.finished_at = str(context.path), datetime.now(UTC)
            payload = {
                "submission_id": submission_id,
                "commit_sha": publication["commit_sha"],
                "status": status,
            }
            run.context_json = {
                **run.context_json,
                **payload,
                "spec_binding": binding,
                "requires_spec_review": True,
            }
            previous = session.scalar(
                select(OnboardingReport)
                .where(OnboardingReport.run_id == source.run_id)
                .order_by(OnboardingReport.id.desc())
                .limit(1)
            )
            observation = previous.observation_code if previous else "INCONCLUSIVE"
            report_id = uuid4().hex
            session.add(
                OnboardingReport(
                    report_id=report_id,
                    task_id=task_id,
                    run_id=run_id,
                    report_version=task.policy_version,
                    observation_code=observation,
                    technical_status=details["technical_status"],
                    next_action="WAIT_MANUAL_RUN" if result.passed else "REQUEST_INPUT",
                    adoptable=result.passed,
                    report_json={
                        "task_id": task_id,
                        "run_id": run_id,
                        "entry_url": task.entry_url,
                        "candidate_commit": publication["commit_sha"],
                        "candidate_branch": publication["branch_name"],
                        "validation": details,
                        "observation_code": observation,
                        "observation_source": "prior_code_report",
                        "technical_status": details["technical_status"],
                        "needs_manual_review": True,
                        "spec_revision": spec.spec_version if spec else None,
                        "spec_hash": spec.spec_hash if spec else None,
                        "spec_status": "NEEDS_REVIEW",
                        "unresolved": ["manual_code_spec_alignment_requires_review"],
                        "platform_id": None,
                        "entity_id": None,
                        "simulated": False,
                        "next_action": "WAIT_MANUAL_RUN" if result.passed else "REQUEST_INPUT",
                    },
                )
            )
            if result.passed:
                task.last_report_id = report_id
            session.commit()
            return payload
    except Exception:
        session.rollback()
        run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == run_id))
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if task.status == "EDITING" and task.current_run_id == run_id:
            task.status, task.current_run_id = old_status, old_run
        run.status, run.finished_at = "FAILED", datetime.now(UTC)
        session.commit()
        raise
