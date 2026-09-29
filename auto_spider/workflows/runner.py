from __future__ import annotations

from datetime import UTC, datetime
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
from auto_spider.git.target import CollectorRepository
from auto_spider.schemas import NextAction, ObservationCode, TechnicalStatus
from auto_spider.services.evidence import EvidenceStore
from auto_spider.services.policies import next_action_for, technical_status_for
from auto_spider.services.spec_builder import build_fake_platform_spec

from .browser_analyzer import BrowserAnalyzer
from .checkpoints import CheckpointStore
from .codex_coder import CodexCodingGateway
from .fakes import FakeAnalyzer, FakeCodingGateway, FakeRepairGateway


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return uuid4().hex


class WorkflowRunner:
    def __init__(
        self,
        analyzer: FakeAnalyzer | None = None,
        coder: FakeCodingGateway | None = None,
        repairer: FakeRepairGateway | None = None,
    ) -> None:
        if analyzer is not None:
            self.analyzer = analyzer
        else:
            self.analyzer = (
                BrowserAnalyzer() if get_settings().analysis_mode == "browser" else FakeAnalyzer()
            )
        if coder is not None:
            self.coder = coder
        else:
            self.coder = (
                CodexCodingGateway()
                if get_settings().coding_mode == "codex"
                else FakeCodingGateway()
            )
        self.repairer = repairer or FakeRepairGateway()
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
        self._checkpoint(session, task, run, "normalize_input", {"task_id": task_id})

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

        old_specs = session.scalars(
            select(PlatformSpec).where(PlatformSpec.task_id == task.task_id)
        ).all()
        for old in old_specs:
            old.is_current = False
        spec_model = build_fake_platform_spec(
            task_id=task.task_id,
            platform_key=task.platform_key,
            source_name=task.platform_name or task.platform_key,
            entry_url=task.entry_url,
            observation=analysis.observation,
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

        generated = self.coder.generate(
            task.platform_key,
            task_id=task.task_id,
            run_id=run.run_id,
            spec=spec,
            evidence_refs=[evidence.evidence_id],
        )
        self._checkpoint(
            session,
            task,
            run,
            "generate_code",
            {"changed_files": generated["changed_files"], "simulated": generated["simulated"]},
        )

        invalid_files = self.repairer.validate(task.platform_key, generated["changed_files"])
        spec_errors = spec_model.candidate_blocking_errors()
        observation = ObservationCode(analysis.observation["observation_code"])
        validation_passed = (
            not invalid_files
            and not spec_errors
            and observation != ObservationCode.ACCESS_RESTRICTED
        )
        technical = technical_status_for(observation)
        if invalid_files:
            technical = TechnicalStatus.FAIL
        next_action = next_action_for(observation, validation_passed=validation_passed)
        if invalid_files:
            next_action = NextAction.REQUEST_INPUT

        validation = ValidationRun(
            run_id=run.run_id,
            pytest_status=generated["validation"]["pytest_status"] if validation_passed else "FAIL",
            ruff_status=generated["validation"]["ruff_status"] if validation_passed else "FAIL",
            contract_status=generated["validation"]["contract_status"]
            if validation_passed
            else "FAIL",
            business_status=generated["validation"]["business_status"]
            if validation_passed
            else "FAIL",
            sample_count=analysis.observation["list_count"],
            internship_count=analysis.observation["internship_count"],
            valid_record_count=analysis.observation["valid_record_count"],
            result_json={
                "invalid_files": invalid_files,
                "spec_errors": spec_errors,
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
            },
        )

        report_id = _id()
        report_json = {
            "task_id": task.task_id,
            "run_id": run.run_id,
            "report_version": task.policy_version,
            "spec_revision": spec_model.spec_revision,
            "spec_hash": spec_model.spec_hash,
            "spec_status": spec_model.status,
            "confidence_summary": spec_model.confidence_summary,
            "observation_code": observation,
            "technical_status": technical,
            "next_action": next_action,
            "adoptable": next_action == NextAction.CREATE_CANDIDATE,
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
            "validation": {
                "pytest_status": validation.pytest_status,
                "ruff_status": validation.ruff_status,
                "contract_status": validation.contract_status,
                "business_status": validation.business_status,
            },
            "evidence_refs": [evidence.evidence_id],
            "unresolved": [*invalid_files, *spec_errors],
            "simulated": True,
        }
        report = OnboardingReport(
            report_id=report_id,
            task_id=task.task_id,
            run_id=run.run_id,
            report_version=task.policy_version,
            observation_code=observation,
            technical_status=technical,
            next_action=next_action,
            adoptable=next_action == NextAction.CREATE_CANDIDATE,
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
                    get_settings().collector_baseline_ref,
                ),
                changed_files=generated["changed_files"],
                submission_type="onboarding",
                adoption_status="candidate",
                simulated=generated.get("simulated", True),
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
        repair = self.repairer.repair(task.platform_key, bundle.bundle_json.get("issue_summary"))
        invalid_files = self.repairer.validate(task.platform_key, repair["changed_files"])
        repair_run = RepairRun(
            repair_run_id=_id(),
            task_id=task_id,
            bundle_id=bundle_id,
            attempt=attempt,
            status="COMPLETED" if not invalid_files else "FAILED",
            diagnosis_json=repair["diagnosis"],
            changed_files=repair["changed_files"],
            regression_json={**repair["regression"], "invalid_files": invalid_files},
        )
        session.add(repair_run)
        if not invalid_files:
            session.add(
                CodeSubmission(
                    submission_id=_id(),
                    task_id=task_id,
                    run_id=run.run_id,
                    branch_name=f"ai/repair/{task.platform_key}/{task_id}-r{attempt}",
                    commit_sha=None,
                    baseline_ref=get_settings().collector_baseline_ref,
                    changed_files=repair["changed_files"],
                    submission_type="repair",
                    adoption_status="candidate",
                    simulated=True,
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
    finally:
        session.close()


def run_repair_task(task_id: str, bundle_id: str) -> dict:
    from auto_spider.db.session import SessionLocal

    session = SessionLocal()
    try:
        return WorkflowRunner().run_repair(session, task_id, bundle_id)
    finally:
        session.close()
