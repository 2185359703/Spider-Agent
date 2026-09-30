from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    FailureBundle,
    OnboardingReport,
    OnboardingTask,
    PlatformSpec,
    RepairRun,
    ValidationRun,
    WorkflowEvent,
    WorkflowRun,
)
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.target import CollectorRepository
from auto_spider.schemas import NextAction, ObservationCode, TechnicalStatus
from auto_spider.services.evidence import EvidenceStore
from auto_spider.services.policies import next_action_for, technical_status_for
from auto_spider.services.spec_builder import build_platform_spec
from auto_spider.services.workflow_logs import append_durable_log

from .browser_analyzer import BrowserAnalyzer
from .checkpoints import CheckpointStore
from .openhands_coder import OpenHandsCodingGateway
from .openhands_repair import OpenHandsRepairGateway


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid4().hex


class WorkflowRunner:
    def __init__(
        self,
        analyzer: Any | None = None,
        coder: Any | None = None,
        repairer: Any | None = None,
    ) -> None:
        self.analyzer = analyzer or BrowserAnalyzer()
        self.coder = coder or OpenHandsCodingGateway()
        self.repairer = repairer or OpenHandsRepairGateway()
        self.checkpoints = CheckpointStore()
        self.evidence = EvidenceStore()

    def _checkpoint(
        self,
        session: Session,
        task: OnboardingTask,
        run: WorkflowRun,
        node: str,
        state: dict,
    ) -> None:
        self.checkpoints.save(
            session,
            task_id=task.task_id,
            run_id=run.run_id,
            node=node,
            state=state,
        )
        session.flush()

    @staticmethod
    def _log(
        session: Session,
        task: OnboardingTask,
        run: WorkflowRun,
        stage: str,
        message: str,
        *,
        level: str = "INFO",
        detail: dict | None = None,
        commit: bool = True,
    ) -> None:
        if commit:
            session.commit()
        else:
            session.flush()
        append_durable_log(
            task_id=task.task_id,
            run_id=run.run_id,
            stage=stage,
            message=message,
            level=level,
            detail=detail,
        )

    @staticmethod
    def _agent_event_callback(task_id: str, run_id: str):
        def callback(event: object) -> None:
            event_type = type(event).__name__
            source = getattr(event, "source", None)
            text = ""
            error = getattr(event, "error", None) or getattr(event, "exception", None)
            llm_message = getattr(event, "llm_message", None)
            content = getattr(llm_message, "content", None)
            if content:
                parts = [getattr(item, "text", "") for item in content]
                text = " ".join(part for part in parts if part).strip()
            action = getattr(event, "action", None)
            if not text and action is not None:
                text = str(action)
            observation = getattr(event, "observation", None)
            if not text and observation is not None:
                text = str(observation)
            if not text and error is not None:
                text = str(error)
            summary = getattr(event, "summary", None)
            message = f"OpenHands {event_type}"
            if source:
                message += f" ({source})"
            if summary:
                message += f" [{summary}]"
            if text:
                message += f": {text[:2000]}"
            level = "ERROR" if "Error" in event_type else "INFO"
            append_durable_log(
                task_id=task_id,
                run_id=run_id,
                stage="openhands",
                level=level,
                message=message,
                detail={
                    "event_type": event_type,
                    "source": source,
                    "error": str(error)[:1000] if error is not None else None,
                },
            )

        return callback

    def run_onboarding(self, session: Session, task_id: str) -> dict:
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        run = WorkflowRun(
            run_id=_id(),
            task_id=task.task_id,
            run_type="onboarding",
            attempt=1,
            status="RUNNING",
        )
        session.add(run)
        session.flush()
        task.current_run_id = run.run_id
        task.status = "ANALYZING"
        self._log(
            session,
            task,
            run,
            "normalize_input",
            "任务已接收，开始标准化招聘入口",
            detail={"entry_url": task.entry_url},
        )
        self._checkpoint(session, task, run, "normalize_input", {"task_id": task_id})

        self._log(session, task, run, "inspect_site", "启动真实网站分析器，采集页面和网络证据")
        analysis = self.analyzer.inspect(
            task.entry_url,
            task.platform_key,
            task.task_id,
            run.run_id,
        )
        evidence = self.evidence.write_json(
            session,
            task_id=task.task_id,
            run_id=run.run_id,
            name="analysis.json",
            payload={
                "entry_url": task.entry_url,
                "observation": analysis.observation,
                "list_endpoint": analysis.list_endpoint,
                "detail_endpoint": analysis.detail_endpoint,
                "evidence_refs": analysis.evidence_refs,
            },
            file_type="analysis",
        )
        self._checkpoint(
            session,
            task,
            run,
            "inspect_site",
            {"observation": analysis.observation, "evidence_id": evidence.evidence_id},
        )
        self._log(
            session,
            task,
            run,
            "inspect_site",
            "网站分析完成，已保存脱敏证据",
            detail={"observation": analysis.observation, "evidence_id": evidence.evidence_id},
        )

        old_specs = session.scalars(
            select(PlatformSpec).where(PlatformSpec.task_id == task.task_id)
        ).all()
        for old in old_specs:
            old.is_current = False
        spec_model = build_platform_spec(
            task_id=task.task_id,
            platform_key=task.platform_key,
            source_name=task.platform_name or task.platform_key,
            entry_url=task.entry_url,
            observation={
                **analysis.observation,
                "list_endpoint": analysis.list_endpoint,
                "detail_endpoint": analysis.detail_endpoint,
                "list_method": analysis.list_method,
                "list_query": analysis.list_query,
                "list_body": analysis.list_body,
                "list_response_file": analysis.list_response_file,
                "list_response_shape": analysis.list_response_shape,
            },
            evidence_id=evidence.evidence_id,
        )
        spec_model = spec_model.model_copy(
            update={
                "evidence": spec_model.evidence.model_copy(
                    update={
                        "manifest_ref": evidence.relative_path,
                        "redaction_status": "redacted",
                    }
                )
            }
        )
        spec_model = spec_model.with_hash()
        spec = spec_model.model_dump(mode="json")
        spec_row = PlatformSpec(
            task_id=task.task_id,
            schema_version=spec_model.schema_version,
            spec_version=len(old_specs) + 1,
            spec_hash=spec_model.spec_hash or spec_model.calculated_hash(),
            status=spec_model.status.value,
            confidence_summary=spec_model.confidence_summary,
            spec_json=spec,
            evidence_manifest_ref=evidence.relative_path,
        )
        session.add(spec_row)
        session.flush()
        self._checkpoint(session, task, run, "build_spec", {"spec_version": spec_row.spec_version})
        self._log(
            session,
            task,
            run,
            "build_spec",
            "PlatformSpec 已生成并完成结构校验",
            detail={"spec_version": spec_row.spec_version},
        )

        self._log(
            session,
            task,
            run,
            "generate_code",
            "正在调用 OpenHands Agent Server 生成采集器候选",
            detail={
                "agent_skills": ["collector-onboarding", "spider-king-collector"],
                "skill_versions": ["1.0.0", "1.0.0-adapter"],
            },
        )
        generation_kwargs = {
            "task_id": task.task_id,
            "run_id": run.run_id,
            "spec": spec,
            "evidence_refs": [evidence.evidence_id],
            "event_callback": self._agent_event_callback(task.task_id, run.run_id),
        }
        if isinstance(self.coder, OpenHandsCodingGateway):
            generation_kwargs["expected_observation"] = analysis.observation["observation_code"]
        generated = self.coder.generate(task.platform_key, **generation_kwargs)
        self._checkpoint(
            session,
            task,
            run,
            "generate_code",
            {"changed_files": generated["changed_files"], "simulated": generated["simulated"]},
        )
        self._log(
            session,
            task,
            run,
            "generate_code",
            "OpenHands 代码生成完成，开始检查修改范围",
            detail={
                "changed_files": generated["changed_files"],
                "commit_sha": generated.get("commit_sha"),
            },
        )

        invalid_files = validate_changed_files(generated["changed_files"], task.platform_key)
        spec_errors = spec_model.candidate_blocking_errors()
        hard_spec_errors = [
            error
            for error in spec_errors
            if error
            in {
                "missing_list_endpoint",
                "missing_list_response_decoder",
                "missing_detail_response_decoder",
                "unbounded_pagination",
                "pagination_not_verified",
                "missing_source_id_selector",
                "low_confidence_source_id",
                "missing_internship_filter_evidence",
            }
        ]
        generated_validation = generated.get("validation", {})
        validation_checks_passed = all(
            generated_validation.get(name, "PASS") == "PASS"
            for name in (
                "compile_status",
                "pytest_status",
                "ruff_status",
                "contract_status",
                "business_status",
            )
        )
        observation = ObservationCode(analysis.observation["observation_code"])
        validation_passed = (
            not invalid_files
            and not hard_spec_errors
            and observation != ObservationCode.ACCESS_RESTRICTED
            and validation_checks_passed
        )
        technical = technical_status_for(observation)
        if invalid_files or hard_spec_errors:
            technical = TechnicalStatus.FAIL
        next_action = next_action_for(observation, validation_passed=validation_passed)
        if invalid_files:
            next_action = NextAction.REQUEST_INPUT
        needs_manual_review = bool(spec_errors) and validation_passed
        if needs_manual_review:
            next_action = NextAction.WAIT_MANUAL_RUN
        code_validation_passed = not invalid_files and validation_checks_passed
        spec_row.status = "NEEDS_REVIEW" if needs_manual_review else spec_model.status.value

        validation = ValidationRun(
            run_id=run.run_id,
            pytest_status=generated["validation"]["pytest_status"]
            if code_validation_passed
            else "FAIL",
            ruff_status=generated["validation"]["ruff_status"]
            if code_validation_passed
            else "FAIL",
            contract_status=generated["validation"]["contract_status"]
            if code_validation_passed
            else "FAIL",
            business_status=generated["validation"]["business_status"]
            if code_validation_passed
            else "FAIL",
            sample_count=analysis.observation["list_count"],
            internship_count=analysis.observation["internship_count"],
            valid_record_count=analysis.observation["valid_record_count"],
            result_json={
                "invalid_files": invalid_files,
                "spec_errors": spec_errors,
                "hard_spec_errors": hard_spec_errors,
                "candidate_validation": generated_validation,
                "repository": CollectorRepository().candidate_context(),
            },
        )
        session.add(validation)
        self._checkpoint(
            session,
            task,
            run,
            "run_validation",
            {
                "technical_status": technical,
                "invalid_files": invalid_files,
                "spec_errors": spec_errors,
                "hard_spec_errors": hard_spec_errors,
            },
        )
        self._log(
            session,
            task,
            run,
            "run_validation",
            "候选代码自动验证完成",
            detail={
                "validation": generated_validation,
                "spec_errors": spec_errors,
                "hard_spec_errors": hard_spec_errors,
            },
        )

        report_id = _id()
        report_json = {
            "task_id": task.task_id,
            "run_id": run.run_id,
            "report_version": task.policy_version,
            "spec_revision": spec_model.spec_revision,
            "spec_hash": spec_model.spec_hash,
            "spec_status": spec_row.status,
            "confidence_summary": spec_model.confidence_summary,
            "observation_code": observation,
            "technical_status": technical,
            "next_action": next_action,
            "adoptable": validation_passed
            and next_action in {NextAction.CREATE_CANDIDATE, NextAction.WAIT_MANUAL_RUN},
            "needs_manual_review": needs_manual_review,
            "entry_url": task.entry_url,
            "platform_id": None,
            "entity_id": None,
            "list_found": analysis.observation["list_found"],
            "detail_found": analysis.observation["detail_found"],
            "pagination_verified": analysis.observation["pagination_verified"],
            "list_count": analysis.observation["list_count"],
            "internship_count": analysis.observation["internship_count"],
            "valid_record_count": analysis.observation["valid_record_count"],
            "missing_publish_time_count": 0,
            "candidate_commit": generated.get("commit_sha"),
            "candidate_branch": generated.get("branch_name"),
            "push_status": generated.get("push_status", "DISABLED"),
            "validation": {
                "compile_status": generated_validation.get("compile_status", "NOT_RUN"),
                "pytest_status": validation.pytest_status,
                "ruff_status": validation.ruff_status,
                "contract_status": validation.contract_status,
                "business_status": validation.business_status,
                "live_status": generated_validation.get("live_status", "NOT_RUN"),
            },
            "evidence_refs": [evidence.evidence_id],
            "unresolved": [*invalid_files, *spec_errors],
            "simulated": generated.get("simulated", False),
        }
        report = OnboardingReport(
            report_id=report_id,
            task_id=task.task_id,
            run_id=run.run_id,
            report_version=task.policy_version,
            observation_code=observation,
            technical_status=technical,
            next_action=next_action,
            adoptable=validation_passed
            and next_action in {NextAction.CREATE_CANDIDATE, NextAction.WAIT_MANUAL_RUN},
            report_json=report_json,
            report_ref=evidence.relative_path,
        )
        session.add(report)
        task.last_report_id = report_id
        self._checkpoint(session, task, run, "build_report", {"report_id": report_id})

        if report.adoptable:
            submission = CodeSubmission(
                submission_id=_id(),
                task_id=task.task_id,
                run_id=run.run_id,
                branch_name=generated.get(
                    "branch_name",
                    f"ai/onboarding/{task.platform_key}/{task.task_id}",
                ),
                commit_sha=generated.get("commit_sha"),
                baseline_ref=generated.get(
                    "baseline_ref",
                    get_settings().aicoding_baseline_ref,
                ),
                changed_files=generated["changed_files"],
                submission_type="onboarding",
                adoption_status="candidate",
                simulated=generated.get("simulated", False),
            )
            session.add(submission)
            task.status = "WAITING_MANUAL_RUN"
            task.next_action = NextAction.WAIT_MANUAL_RUN
        else:
            task.status = "BLOCKED"
            task.next_action = next_action

        run.status = "COMPLETED"
        run.finished_at = _now()
        session.add(
            WorkflowEvent(
                event_id=_id(),
                event_type="REPORT_CREATED",
                task_id=task.task_id,
                run_id=run.run_id,
                idempotency_key=f"{run.run_id}:report",
                payload_json={"report_id": report_id, "observation_code": observation},
            )
        )
        self._log(
            session,
            task,
            run,
            "build_report",
            "接入汇报已生成",
            detail={"observation_code": observation, "technical_status": technical},
        )
        session.commit()
        return report_json

    def run_repair(self, session: Session, task_id: str, bundle_id: str) -> dict:
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        bundle = session.scalar(select(FailureBundle).where(FailureBundle.bundle_id == bundle_id))
        if task is None or bundle is None:
            raise ValueError("task or failure bundle not found")
        previous_attempt = (
            session.scalar(select(func.max(RepairRun.attempt)).where(RepairRun.task_id == task_id))
            or 0
        )
        attempt = previous_attempt + 1
        run = WorkflowRun(
            run_id=_id(),
            task_id=task_id,
            run_type="repair",
            attempt=attempt,
            status="RUNNING",
        )
        session.add(run)
        session.flush()
        task.current_run_id = run.run_id
        task.status = "REPAIRING"
        self._log(
            session,
            task,
            run,
            "diagnose_failure",
            "开始分析人工审查失败包",
            detail={
                "agent_skills": ["collector-onboarding", "spider-king-collector"],
                "skill_versions": ["1.0.0", "1.0.0-adapter"],
            },
        )
        if isinstance(self.repairer, OpenHandsRepairGateway):
            latest_submission = session.scalar(
                select(CodeSubmission)
                .where(
                    CodeSubmission.task_id == task_id,
                    CodeSubmission.commit_sha.is_not(None),
                )
                .order_by(CodeSubmission.created_at.desc())
            )
            latest_spec = session.scalar(
                select(PlatformSpec)
                .where(PlatformSpec.task_id == task_id, PlatformSpec.is_current.is_(True))
                .order_by(PlatformSpec.spec_version.desc())
            )
            if latest_submission is None or latest_submission.commit_sha is None:
                raise RuntimeError("REPAIR_BASE_COMMIT_MISSING")
            if latest_spec is None:
                raise RuntimeError("REPAIR_SPEC_MISSING")
            latest_report = session.scalar(
                select(OnboardingReport)
                .where(OnboardingReport.task_id == task_id)
                .order_by(OnboardingReport.created_at.desc())
            )
            expected_observation = (
                str(latest_report.observation_code) if latest_report is not None else None
            )
            repair = self.repairer.repair(
                task.platform_key,
                task_id=task_id,
                run_id=run.run_id,
                base_ref=latest_submission.commit_sha,
                spec=latest_spec.spec_json,
                failure_bundle=bundle.bundle_json,
                expected_observation=expected_observation,
                event_callback=self._agent_event_callback(task_id, run.run_id),
            )
        else:
            repair = self.repairer.repair(
                task.platform_key,
                bundle.bundle_json.get("issue_summary"),
            )
        self._log(
            session,
            task,
            run,
            "patch_code",
            "AI 修复完成，开始验证修改范围和回归结果",
            detail={
                "changed_files": repair.get("changed_files", []),
                "commit_sha": repair.get("commit_sha"),
            },
        )
        invalid_files = validate_changed_files(repair["changed_files"], task.platform_key)
        diagnosis = repair.get(
            "diagnosis",
            {
                "code_fixable": True,
                "root_cause": bundle.bundle_json.get("issue_summary"),
            },
        )
        regression = repair.get("regression", repair.get("validation", {}))
        repair_run = RepairRun(
            repair_run_id=_id(),
            task_id=task_id,
            bundle_id=bundle_id,
            attempt=attempt,
            status="COMPLETED" if not invalid_files else "FAILED",
            diagnosis_json=diagnosis,
            changed_files=repair["changed_files"],
            regression_json={**regression, "invalid_files": invalid_files},
            commit_sha=repair.get("commit_sha"),
        )
        session.add(repair_run)
        if not invalid_files:
            session.add(
                CodeSubmission(
                    submission_id=_id(),
                    task_id=task_id,
                    run_id=run.run_id,
                    branch_name=f"ai/repair/{task.platform_key}/{task_id}-r{attempt}",
                    commit_sha=repair.get("commit_sha"),
                    baseline_ref=repair.get("baseline_ref", get_settings().aicoding_baseline_ref),
                    changed_files=repair["changed_files"],
                    submission_type="repair",
                    adoption_status="candidate",
                    simulated=repair.get("simulated", False),
                )
            )
            task.status = "WAITING_MANUAL_RUN"
            task.next_action = NextAction.WAIT_MANUAL_RUN
        else:
            task.status = "BLOCKED"
            task.next_action = NextAction.REQUEST_INPUT
        run.status = "COMPLETED" if not invalid_files else "FAILED"
        run.finished_at = _now()
        bundle.status = "REPAIRED" if not invalid_files else "NEEDS_REVIEW"
        session.add(
            WorkflowEvent(
                event_id=_id(),
                event_type="REPAIR_FINISHED",
                task_id=task_id,
                run_id=run.run_id,
                idempotency_key=f"{run.run_id}:repair",
                payload_json={"bundle_id": bundle_id, "invalid_files": invalid_files},
            )
        )
        session.commit()
        return {"task_id": task_id, "run_id": run.run_id, "invalid_files": invalid_files}


def run_onboarding_task(task_id: str) -> dict:
    from auto_spider.db.session import SessionLocal

    session = SessionLocal()
    try:
        return WorkflowRunner().run_onboarding(session, task_id)
    except Exception as exc:
        session.rollback()
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if task is not None:
            run = session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.run_id == task.current_run_id)
                .order_by(WorkflowRun.started_at.desc())
            )
            if run is not None:
                run.status = "FAILED"
                run.finished_at = _now()
                run.error_code = type(exc).__name__
                run.error_message = str(exc)[:2000]
            task.status = "BLOCKED"
            task.next_action = NextAction.REQUEST_INPUT
            session.commit()
            append_durable_log(
                task_id=task_id,
                run_id=task.current_run_id,
                stage="workflow",
                level="ERROR",
                message="工作流执行失败，已保存失败状态",
                detail={"error_type": type(exc).__name__, "error": str(exc)[:2000]},
            )
        raise
    finally:
        session.close()


def run_repair_task(task_id: str, bundle_id: str) -> dict:
    from auto_spider.db.session import SessionLocal

    session = SessionLocal()
    try:
        return WorkflowRunner().run_repair(session, task_id, bundle_id)
    except Exception as exc:
        session.rollback()
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if task is not None:
            run = session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.run_id == task.current_run_id)
                .order_by(WorkflowRun.started_at.desc())
            )
            if run is not None:
                run.status = "FAILED"
                run.finished_at = _now()
                run.error_code = type(exc).__name__
                run.error_message = str(exc)[:2000]
            task.status = "BLOCKED"
            task.next_action = NextAction.REQUEST_INPUT
            session.commit()
            append_durable_log(
                task_id=task_id,
                run_id=task.current_run_id,
                stage="repair",
                level="ERROR",
                message="AI 修复执行失败，已保存失败状态",
                detail={"error_type": type(exc).__name__, "error": str(exc)[:2000]},
            )
        raise
    finally:
        session.close()
