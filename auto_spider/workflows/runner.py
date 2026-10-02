from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langgraph.types import Command, interrupt
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from auto_spider.config import get_settings
from auto_spider.db.models import (
    AgentExecution,
    CodeSubmission,
    EvidenceFile,
    ExecutionLease,
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingReport,
    OnboardingTask,
    PlatformSpec,
    RepairRun,
    ValidationRun,
    WorkflowCheckpoint,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStep,
)
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.publisher import publish_candidate
from auto_spider.schemas import PlatformSpec as Spec
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.evidence import EvidenceStore, sanitize
from auto_spider.services.execution import ExecutionContext, ExecutionStopped, RunBusy
from auto_spider.services.failure_diagnosis import diagnose_failure
from auto_spider.services.spec_builder import build_platform_spec
from auto_spider.services.workflow_logs import append_durable_log
from auto_spider.validators.candidate import validate_candidate

from .browser_analyzer import BrowserAnalyzer
from .discovery_spec_repair import (
    DiscoverySpecInvalid,
    DiscoverySpecRepairGateway,
    apply_correction,
    validation_feedback,
)
from .graph import build_graph
from .openhands_coder import OpenHandsCodingGateway
from .openhands_repair import OpenHandsRepairGateway
from .sql_checkpointer import SQLCheckpointSaver


def _id():
    return uuid4().hex


def _now():
    return datetime.now(UTC)


OBSERVATIONS_WITH_LIST = {"INTERNSHIPS_FOUND", "NO_JOBS_OBSERVED", "NO_INTERNSHIPS_OBSERVED"}
CHECKS = ("compile", "pytest", "ruff", "contract", "business", "live")


class WorkflowRunner:
    def __init__(
        self,
        analyzer=None,
        coder=None,
        repairer=None,
        *,
        validator=None,
        publisher=None,
        spec_repairer=None,
    ):
        self.analyzer = analyzer or BrowserAnalyzer()
        self.coder = coder or OpenHandsCodingGateway()
        self.repairer = repairer or OpenHandsRepairGateway()
        self.spec_repairer = spec_repairer or DiscoverySpecRepairGateway()
        self.validator = validator or self._validate
        self.publisher = publisher or publish_candidate
        self.evidence = EvidenceStore()

    def run_onboarding(self, session: Session, task_id: str) -> dict:
        return self._run(session, task_id)

    def run_repair(self, session: Session, task_id: str, bundle_id: str) -> dict:
        task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
        if task and task.current_run_id:
            current = session.scalar(
                select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id)
            )
            if (
                current
                and current.execution_key
                and current.status in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW"}
            ):
                self.run_onboarding(session, task_id)
        return self._run(session, task_id, bundle_id)

    def _run(self, caller: Session, task_id: str, bundle_id: str | None = None) -> dict:
        caller.commit()
        self.factory = sessionmaker(bind=caller.get_bind(), expire_on_commit=False)
        with self.factory.begin() as session:
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == task_id).with_for_update()
            )
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            key = f"repair:{bundle_id}" if bundle_id else f"onboarding:{task_id}"
            current = (
                session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id))
                if task.current_run_id
                else None
            )
            # Manual-run/review notifications continue the current graph, including repairs.
            run = (
                current
                if not bundle_id and current
                else session.scalar(select(WorkflowRun).where(WorkflowRun.execution_key == key))
            )
            if run and run.run_type == "manual_edit":
                # Human edits have no onboarding graph. Late/replayed queue
                # notifications must never start inspect_site for this run.
                return self._result(session, task, run)
            if run and (
                run.status in {"COMPLETED", "CANCELLED", "BLOCKED"}
                or run.control in {"PAUSE", "CANCEL"}
            ):
                return self._result(session, task, run)
            if run and run.status in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW"}:
                candidate = session.scalar(
                    select(CodeSubmission)
                    .where(CodeSubmission.run_id == run.run_id)
                    .order_by(CodeSubmission.id.desc())
                    .limit(1)
                )
                manual = (
                    session.scalar(
                        select(ManualRun)
                        .where(
                            ManualRun.task_id == task_id,
                            ManualRun.code_revision == candidate.commit_sha,
                        )
                        .order_by(ManualRun.id.desc())
                        .limit(1)
                    )
                    if candidate
                    else None
                )
                if manual is None:
                    return self._result(session, task, run)
                if run.status == "WAITING_MANUAL_REVIEW" and not session.scalar(
                    select(ManualReview.review_id).where(
                        ManualReview.manual_run_id == manual.manual_run_id,
                        ManualReview.task_id == task_id,
                    )
                ):
                    return self._result(session, task, run)
            if run is None:
                if current and current.status in {"RUNNING", "QUEUED", "PAUSED"}:
                    raise RunBusy("TASK_RUN_ACTIVE")
                if bundle_id:
                    bundle = session.scalar(
                        select(FailureBundle).where(
                            FailureBundle.bundle_id == bundle_id, FailureBundle.task_id == task_id
                        )
                    )
                    if bundle is None:
                        raise ValueError("FAILURE_BUNDLE_NOT_FOUND")
                    if bundle.review_id is None:
                        raise ValueError("AUTO_FAILURE_RESUME_ORIGINAL_RUN")
                    if bundle.status == "REPAIRED":
                        return {"task_id": task_id, "status": task.status, "idempotent": True}
                run = WorkflowRun(
                    run_id=_id(),
                    task_id=task_id,
                    execution_key=key,
                    run_type="repair" if bundle_id else "onboarding",
                    status="QUEUED",
                    context_json={"input_bundle_id": bundle_id},
                )
                session.add(run)
                session.flush()
                task.current_run_id = run.run_id
            elif not run.execution_key:
                # Historical sequential runs cannot be claimed as native checkpoints.
                return self._result(session, task, run)
            self.task_id, self.run_id = task_id, run.run_id
            resource = f"{task.repository_key}:{task.platform_key}"
        caller.expire_all()
        try:
            with ExecutionContext(self.factory, self.run_id, resource) as execution:
                self.execution = execution
                with self.factory.begin() as session:
                    run = session.scalar(
                        select(WorkflowRun).where(WorkflowRun.run_id == self.run_id)
                    )
                    run.status, run.error_code, run.error_message = "RUNNING", None, None
                    run.heartbeat_at = _now()
                    task = session.scalar(
                        select(OnboardingTask).where(OnboardingTask.task_id == task_id)
                    )
                    if task.current_run_id == run.run_id and task.status in {
                        "SUBMITTED",
                        "FAILED",
                        "TIMED_OUT",
                        "INTERRUPTED",
                        "PAUSED",
                    }:
                        task.status = "REPAIRING" if run.run_type == "repair" else "ANALYZING"
                graph = build_graph(self._execute, SQLCheckpointSaver(self.factory))
                config = {"configurable": {"thread_id": self.run_id}, "recursion_limit": 200}
                snapshot = graph.get_state(config)
                initial = {"task_id": task_id, "run_id": self.run_id, "attempt": 0}
                argument = initial if not snapshot.values else None
                if any(getattr(t, "interrupts", ()) for t in snapshot.tasks):
                    argument = Command(resume={"database_updated": True})
                graph.invoke(argument, config)
                with self.factory() as session:
                    task = session.scalar(
                        select(OnboardingTask).where(OnboardingTask.task_id == task_id)
                    )
                    run = session.scalar(
                        select(WorkflowRun).where(WorkflowRun.run_id == self.run_id)
                    )
                    return self._result(session, task, run)
        except RunBusy:
            return {
                "task_id": task_id,
                "run_id": self.run_id,
                "status": "RUNNING",
                "idempotent": True,
            }
        except ExecutionStopped as exc:
            state = {"PAUSE": "PAUSED", "CANCEL": "CANCELLED", "TIMEOUT": "TIMED_OUT"}.get(
                exc.reason, "INTERRUPTED"
            )
            self._fail(state, exc.reason)
            return {"task_id": task_id, "run_id": self.run_id, "status": state}
        except Exception as exc:
            self._fail("FAILED", sanitize_text(str(exc))[:2000], getattr(exc, "details", None))
            raise
        finally:
            caller.expire_all()

    def _log(self, stage, message, detail=None, level="INFO"):
        append_durable_log(
            task_id=self.task_id,
            run_id=self.run_id,
            stage=stage,
            message=message,
            detail=detail,
            level=level,
            session_factory=self.factory,
        )

    def _events(self, event):
        from auto_spider.services.agent_events import activity_event

        if type(event).__name__ in {"StreamingDeltaEvent", "ConversationStateUpdateEvent"}:
            return
        message, detail = activity_event(event)
        self._log(
            "openhands",
            message,
            detail=detail,
            level=detail.get("severity")
            or ("ERROR" if "Error" in type(event).__name__ or detail.get("is_error") else "INFO"),
        )

    @staticmethod
    def _result(session, task, run):
        report = (
            session.scalar(
                select(OnboardingReport).where(OnboardingReport.report_id == task.last_report_id)
            )
            if task.last_report_id
            else None
        )
        return {
            **(report.report_json if report else {}),
            "task_id": task.task_id,
            "run_id": run.run_id,
            "status": task.status,
            "invalid_files": [],
        }

    def _fail(self, status, message, detail=None):
        with self.factory.begin() as session:
            lease = session.scalar(
                select(ExecutionLease).where(ExecutionLease.run_id == self.run_id)
            )
            owner = getattr(getattr(self, "execution", None), "owner", None)
            if lease is not None and lease.owner != owner:
                return
            run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == self.run_id))
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == self.task_id)
            )
            run.status, run.error_code, run.error_message = (
                status,
                message.split(":", 1)[0][:80],
                message,
            )
            run.finished_at = _now()
            if status == "FAILED":
                # A gateway may lose the final callback while the workflow
                # still records its terminal failure. Mark unfinished Agent
                # rows as disconnected so maintenance can verify the remote
                # conversation and release its browser resources.
                session.query(AgentExecution).filter(
                    AgentExecution.run_id == self.run_id,
                    AgentExecution.status.in_(["CREATED", "RUNNING"]),
                ).update({AgentExecution.status: "DISCONNECTED"}, synchronize_session=False)
            if task.current_run_id == self.run_id:
                task.status, task.next_action = status, "REQUEST_INPUT"
                if message != "LEASE_LOST":
                    previous = session.scalar(
                        select(OnboardingReport).where(
                            OnboardingReport.report_id == task.last_report_id
                        )
                    )
                    details = dict(previous.report_json) if previous else {}
                    details.update(detail or {})
                    details.update(
                        task_id=self.task_id,
                        run_id=self.run_id,
                        technical_status="PARTIAL" if status == "PAUSED" else "FAIL",
                        adoptable=False,
                        next_action="REQUEST_INPUT",
                        stop_reason=message,
                        execution_status=status,
                        simulated=False,
                    )
                    report = OnboardingReport(
                        report_id=_id(),
                        task_id=self.task_id,
                        run_id=self.run_id,
                        report_version=task.policy_version,
                        observation_code=details.get("observation_code", "INCONCLUSIVE"),
                        technical_status=details["technical_status"],
                        next_action="REQUEST_INPUT",
                        adoptable=False,
                        report_json=details,
                    )
                    session.add(report)
                    task.last_report_id = report.report_id
        self._log("workflow", f"执行已停止：{message}", detail=detail, level="ERROR")

    def _execute(self, name: str, state: dict) -> dict:
        self.execution.guard()
        if name in {"await_manual_run", "record_review"}:
            return {**state, **getattr(self, f"_{name}")(state)}
        step_key = f"{name}:{state.get('attempt', 0)}"
        with self.factory() as session:
            previous = session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.run_id == self.run_id, WorkflowStep.step_key == step_key
                )
            )
            if previous:
                return {**state, **previous.result_json}
        self._log(name, f"开始 {name}")
        # Side-effect nodes use stable keys/IDs. The journal and their SQL writes
        # share a transaction; Git and Agent calls have their own durable identity.
        with self.factory() as session:
            output = getattr(self, f"_{name}")(state, session)
            self.execution.guard()
            session.add(WorkflowStep(run_id=self.run_id, step_key=step_key, result_json=output))
            revision = (
                session.scalar(
                    select(func.max(WorkflowCheckpoint.revision)).where(
                        WorkflowCheckpoint.run_id == self.run_id
                    )
                )
                or 0
            ) + 1
            session.add(
                WorkflowCheckpoint(
                    task_id=self.task_id,
                    run_id=self.run_id,
                    node=name,
                    revision=revision,
                    state_json=output,
                )
            )
            session.commit()
        self._log(name, f"完成 {name}")
        return {**state, **output}

    def _task_run(self, session):
        return (
            session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == self.task_id)),
            session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == self.run_id)),
        )

    def _normalize_input(self, state, session):
        task, run = self._task_run(session)
        task.status = "REPAIRING" if run.run_type == "repair" else "ANALYZING"
        return {
            "kind": run.run_type,
            "entry_url": task.entry_url,
            "platform_key": task.platform_key,
            "source_name": task.platform_name or task.platform_key,
            "input_bundle_id": (run.context_json or {}).get("input_bundle_id"),
        }

    def _inspect_site(self, state, session):
        session.commit()
        kwargs = (
            {"execution": self.execution, "event_callback": self._events}
            if isinstance(self.analyzer, BrowserAnalyzer)
            else {}
        )
        result = self.analyzer.inspect(
            state["entry_url"], state["platform_key"], self.task_id, self.run_id, **kwargs
        )
        payload = asdict(result)
        self.evidence.register_browser_files(session, task_id=self.task_id, run_id=self.run_id)
        evidence = self.evidence.write_json(
            session,
            task_id=self.task_id,
            run_id=self.run_id,
            name="analysis.json",
            payload=payload,
            file_type="analysis",
        )
        return {
            "analysis": payload,
            "evidence_id": evidence.evidence_id,
            "manifest_ref": evidence.relative_path,
            "observation": result.observation,
        }

    def _save_spec(self, session, spec: Spec, reason="") -> dict:
        rows = session.scalars(
            select(PlatformSpec).where(PlatformSpec.task_id == self.task_id)
        ).all()
        revision = max((r.spec_version for r in rows), default=0) + 1
        for row in rows:
            row.is_current = False
        data = spec.model_dump(mode="json")
        data.update(spec_revision=revision, spec_hash=None)
        spec = Spec.model_validate(data).with_hash()
        payload = spec.model_dump(mode="json")
        session.add(
            PlatformSpec(
                task_id=self.task_id,
                schema_version=spec.schema_version,
                spec_version=revision,
                spec_hash=spec.spec_hash,
                status=spec.status.value,
                confidence_summary=spec.confidence_counts(),
                spec_json=payload,
                evidence_manifest_ref=spec.evidence.manifest_ref,
            )
        )
        if reason:
            session.add(
                WorkflowEvent(
                    event_id=_id(),
                    task_id=self.task_id,
                    run_id=self.run_id,
                    event_type="SPEC_REVISED",
                    idempotency_key=f"{self.run_id}:spec:{revision}",
                    payload_json={"reason": reason, "spec_revision": revision},
                )
            )
        return payload

    def _build_spec(self, state, session):
        if state["kind"] == "repair":
            bundle = session.scalar(
                select(FailureBundle).where(
                    FailureBundle.bundle_id == state["input_bundle_id"],
                    FailureBundle.task_id == self.task_id,
                )
            )
            sha = bundle.bundle_json.get("code_revision")
            query = select(CodeSubmission).where(
                CodeSubmission.task_id == self.task_id, CodeSubmission.commit_sha == sha
            )
            if bundle.bundle_json.get("submission_id"):
                query = query.where(
                    CodeSubmission.submission_id == bundle.bundle_json["submission_id"]
                )
            candidate = session.scalar(query.order_by(CodeSubmission.id.desc()))
            binding = bundle.bundle_json.get("spec_binding")
            if binding:
                spec_row = session.scalar(
                    select(PlatformSpec).where(
                        PlatformSpec.task_id == self.task_id,
                        PlatformSpec.spec_version == binding["spec_revision"],
                        PlatformSpec.spec_hash == binding["spec_hash"],
                    )
                )
            elif candidate:
                from auto_spider.services.version_binding import submission_spec

                spec_row, _ = submission_spec(session, candidate)
            else:
                spec_row = None
            if not spec_row or not candidate or candidate.adoption_status == "superseded":
                raise RuntimeError("REPAIR_BASE_REVISION_MISMATCH")
            report = session.scalar(
                select(OnboardingReport)
                .where(OnboardingReport.task_id == self.task_id)
                .order_by(OnboardingReport.id.desc())
                .limit(1)
            )
            return {
                "spec": spec_row.spec_json,
                "bundle_id": bundle.bundle_id,
                "bundle": bundle.bundle_json,
                "original_failure_bundle": bundle.bundle_json,
                "baseline_ref": sha,
                "observation": report.report_json if report else {},
                "skip_generation": False,
            }
        model, analysis, corrections = self._build_discovery_spec(state, session)
        manifest_ref = state["manifest_ref"]
        if corrections:
            evidence = self.evidence.write_json(
                session,
                task_id=self.task_id,
                run_id=self.run_id,
                name="spec-corrections/validated-analysis.json",
                payload=analysis,
                file_type="analysis_corrected",
            )
            manifest_ref = evidence.relative_path
        model.evidence.manifest_ref = manifest_ref
        return {
            "spec": self._save_spec(session, model),
            "spec_correction_attempts": corrections,
            "skip_generation": state["observation"]["observation_code"]
            not in OBSERVATIONS_WITH_LIST,
        }

    def _compile_discovery_spec(self, state, analysis):
        return build_platform_spec(
            task_id=self.task_id,
            platform_key=state["platform_key"],
            source_name=state["source_name"],
            entry_url=state["entry_url"],
            observation={
                **analysis["observation"],
                **{k: v for k, v in analysis.items() if k != "observation"},
            },
            evidence_id=state["evidence_id"],
        )

    def _build_discovery_spec(self, state, session):
        analysis, response, errors = state["analysis"], None, []
        limit = get_settings().max_spec_repair_attempts
        for attempt in range(limit + 1):
            try:
                candidate = (
                    apply_correction(analysis, response) if response is not None else analysis
                )
                model = self._compile_discovery_spec(state, candidate)
                if attempt:
                    self._log("build_spec", "接入规范纠错通过", {"attempt": attempt})
                return model, candidate, attempt
            except ValueError as exc:
                errors = validation_feedback(exc)
            if attempt == limit:
                raise DiscoverySpecInvalid(errors, attempt)
            # Commit only correction evidence and a stable receipt. A redelivery reuses it,
            # even when the Agent returned malformed JSON or this process died afterwards.
            key = f"discovery_spec_correction:{attempt + 1}"
            session.commit()
            with self.factory() as receipts:
                previous = receipts.scalar(
                    select(WorkflowStep).where(
                        WorkflowStep.run_id == self.run_id, WorkflowStep.step_key == key
                    )
                )
                cached = previous.result_json if previous else None
            if cached is None:
                self._log(
                    "build_spec",
                    "接入规范校验未通过，提交 AI 修正",
                    {
                        "attempt": attempt + 1,
                        "max_attempts": limit,
                        "errors": errors,
                    },
                    level="WARNING",
                )
                response = self.spec_repairer.repair(
                    analysis=analysis,
                    errors=errors,
                    previous_response=response,
                    attempt=attempt + 1,
                    state=state,
                    execution=self.execution,
                    task_id=self.task_id,
                    run_id=self.run_id,
                    event_callback=self._events,
                )
                self.execution.guard()
                with self.factory.begin() as receipts:
                    artifact = self.evidence.write_json(
                        receipts,
                        task_id=self.task_id,
                        run_id=self.run_id,
                        name=f"spec-corrections/attempt-{attempt + 1}.json",
                        payload={
                            "original_analysis_ref": state["manifest_ref"],
                            "errors": errors,
                            "response": response,
                        },
                        file_type="spec_correction",
                    )
                    receipts.add(
                        WorkflowStep(
                            run_id=self.run_id,
                            step_key=key,
                            result_json={
                                "response": response,
                                "evidence_ref": artifact.relative_path,
                            },
                        )
                    )
            else:
                response = cached["response"]
        raise DiscoverySpecInvalid(errors, limit)  # defensive; loop always returns/raises

    def _apply_spec_patch(self, generated, state, session):
        self.evidence.register_browser_files(session, task_id=self.task_id, run_id=self.run_id)
        proposal = generated.get("spec_patch")
        if not proposal:
            return state["spec"]
        if not isinstance(proposal, dict) or not isinstance(proposal.get("patch"), dict):
            generated["spec_patch_error"] = [
                {"loc": ["spec_patch"], "msg": "补丁及 patch 必须为 JSON 对象"}
            ]
            return state["spec"]
        if (
            not isinstance(proposal.get("reason"), str)
            or not proposal["reason"].strip()
            or not isinstance(proposal.get("evidence_refs"), list)
            or any(not isinstance(ref, str) for ref in proposal["evidence_refs"])
        ):
            generated["spec_patch_error"] = [
                {"loc": ["spec_patch"], "msg": "需要字符串 reason 与字符串数组 evidence_refs"}
            ]
            return state["spec"]
        serialized = json.dumps(proposal, ensure_ascii=False)
        if sanitize_text(serialized) != serialized:
            raise RuntimeError("UNSANITIZED_SENSITIVE_DATA")
        patch = proposal.get("patch", {})
        if (
            not proposal.get("reason")
            or not patch
            or set(patch) - {"endpoints", "fields", "pagination", "filters"}
        ):
            raise RuntimeError("SPEC_PATCH_SCOPE_VIOLATION")

        def refs(value):
            if isinstance(value, dict):
                result = set(value.get("evidence_refs", []))
                for child in value.values():
                    result.update(refs(child))
                return result
            if isinstance(value, list):
                return set().union(*(refs(child) for child in value))
            return set()

        supplied = set(proposal.get("evidence_refs", []))
        registered = session.scalars(
            select(EvidenceFile).where(
                EvidenceFile.task_id == self.task_id,
                EvidenceFile.file_type.in_(
                    [
                        "failure_sample",
                        "failure_manifest",
                        "failure_spec",
                        "failure_diagnostics",
                        "failure_records",
                    ]
                ),
            )
        ).all()
        registered_refs = set()
        for evidence in registered:
            path = (get_settings().evidence_root / evidence.relative_path).resolve()
            if path.is_file() and not path.is_symlink():
                import hashlib

                if hashlib.sha256(path.read_bytes()).hexdigest() == evidence.sha256:
                    registered_refs.update([evidence.evidence_id, evidence.relative_path])
        # Fresh CLI evidence is task-owned, persisted and added to the immutable Spec revision.
        evidence_root = get_settings().evidence_root.resolve()
        fresh = set()
        for ref in supplied - refs(state["spec"]):
            relative = Path(ref)
            if (
                not relative.parts
                or ".." in relative.parts
                or relative.parts[0] != self.task_id
                or "agent-browser" not in relative.parts
            ):
                continue
            file = (evidence_root / relative).resolve()
            if evidence_root not in file.parents or not file.is_file() or file.is_symlink():
                continue
            payload = json.loads(file.read_text("utf-8"))
            if payload.get("action") not in {
                "open",
                "goto",
                "snapshot",
                "click",
                "requests",
                "request",
                "response-body",
                "request-body",
                "response-headers",
            }:
                continue
            fresh.add(ref)
        fresh.update(supplied & registered_refs)
        if not supplied or not supplied <= refs(state["spec"]) | fresh:
            generated["spec_patch_error"] = [
                {
                    "loc": ["evidence_refs"],
                    "msg": "SPEC_PATCH_EVIDENCE_REQUIRED: 必须引用本任务的真实证据",
                }
            ]
            return state["spec"]
        if not fresh and all(state["spec"].get(key) == value for key, value in patch.items()):
            return state["spec"]
        data = {**state["spec"], **patch, "spec_hash": None}
        if fresh:
            data["evidence"] = {
                **data["evidence"],
                "required_refs": list(
                    dict.fromkeys([*data["evidence"]["required_refs"], *sorted(fresh)])
                ),
            }
        try:
            checked = Spec.model_validate(data)
        except ValidationError as exc:
            generated["spec_patch_error"] = exc.errors(
                include_url=False, include_input=False, include_context=False
            )
            return state["spec"]
        return self._save_spec(session, checked, proposal["reason"])

    def _generate_code(self, state, session):
        session.commit()
        generated = self.coder.generate(
            state["platform_key"],
            task_id=self.task_id,
            run_id=self.run_id,
            spec=state["spec"],
            evidence_refs=[state["evidence_id"]],
            execution=self.execution,
            event_callback=self._events,
        )
        _, run = self._task_run(session)
        run.worktree_path = generated.get("workspace")
        return {"generated": generated, "spec": self._apply_spec_patch(generated, state, session)}

    def _validate(self, generated, state):
        from auto_spider.services.validation_activity import validation_activity

        stage = (
            "run_regression"
            if state.get("attempt") or state.get("kind") == "repair"
            else "run_validation"
        )
        with validation_activity(
            lambda message, detail: self._log(
                stage, message, detail, level="ERROR" if detail.get("status") == "FAIL" else "INFO"
            )
        ):
            result = validate_candidate(
                Path(generated["workspace"]),
                state["platform_key"],
                generated["changed_files"],
                live_url=state["entry_url"],
                expected_observation=state["observation"].get("observation_code"),
                guard=self.execution.guard,
            )
        return {
            **{f"{n}_status": getattr(result, n).status for n in ("compile", "pytest", "ruff")},
            "live_status": result.live.status if result.live else "NOT_RUN",
            "contract_status": result.contract_status,
            "business_status": result.business_status,
            "details": result.as_dict(),
        }

    def _run_validation(self, state, session):
        session.commit()
        validation = self.validator(state["generated"], state)
        if state["generated"].get("spec_patch_error"):
            validation = {
                **validation,
                "contract_status": "FAIL",
                "spec_patch_errors": state["generated"]["spec_patch_error"],
            }
        invalid = validate_changed_files(state["generated"]["changed_files"], state["platform_key"])
        if invalid:
            validation = {**validation, "contract_status": "FAIL", "scope_errors": invalid}
        spec_errors = Spec.model_validate(state["spec"]).candidate_blocking_errors()
        hard = [e for e in spec_errors if not e.startswith("low_confidence_")]
        passed = all(validation.get(f"{name}_status") == "PASS" for name in CHECKS) and not hard
        if not state["generated"]["changed_files"]:
            passed = False
            validation["contract_status"] = "FAIL"
        session.add(
            ValidationRun(
                run_id=self.run_id,
                **{
                    f"{k}_status": validation.get(f"{k}_status", "NOT_RUN")
                    for k in ("pytest", "ruff", "contract", "business")
                },
                result_json={
                    "attempt": state["attempt"],
                    "validation": validation,
                    "spec_errors": spec_errors,
                    "invalid_files": invalid,
                },
            )
        )
        if state["attempt"]:
            repair = session.scalar(
                select(RepairRun).where(
                    RepairRun.task_id == self.task_id,
                    RepairRun.repair_run_id == state.get("repair_run_id"),
                )
            )
            if repair:
                repair.status, repair.regression_json = (
                    "COMPLETED" if passed else "FAILED",
                    validation,
                )
        return {"validation": validation, "spec_errors": spec_errors, "passed": passed}

    _run_regression = _run_validation

    def _build_report(self, state, session):
        task, run = self._task_run(session)
        observation = state["observation"]
        passed = state.get("passed", False)
        warnings = state.get("spec_errors", [])
        validation = state.get("validation", {f"{n}_status": "NOT_RUN" for n in CHECKS})
        code = observation.get("observation_code", "INCONCLUSIVE")
        report_json = {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "report_version": task.policy_version,
            "spec_revision": state["spec"]["spec_revision"],
            "spec_hash": state["spec"]["spec_hash"],
            "observation_code": code,
            "technical_status": "PARTIAL" if passed and warnings else "PASS" if passed else "FAIL",
            "adoptable": passed,
            "next_action": "CREATE_CANDIDATE" if passed else "REQUEST_INPUT",
            "entry_url": state["entry_url"],
            "platform_id": None,
            "entity_id": None,
            **{
                k: observation.get(k)
                for k in (
                    "list_found",
                    "detail_found",
                    "pagination_verified",
                    "list_count",
                    "internship_count",
                    "valid_record_count",
                )
            },
            "validation": validation,
            "unresolved": warnings,
            "needs_manual_review": bool(warnings),
            "push_status": "DISABLED",
            "simulated": False,
            "repair_attempt": state["attempt"],
        }
        report = OnboardingReport(
            report_id=_id(),
            task_id=self.task_id,
            run_id=self.run_id,
            report_version=task.policy_version,
            observation_code=code,
            technical_status=report_json["technical_status"],
            next_action=report_json["next_action"],
            adoptable=passed,
            report_json=report_json,
        )
        session.add(report)
        task.last_report_id = report.report_id
        return {"report_id": report.report_id}

    def _report_gate(self, state, session):
        if state.get("passed"):
            return {"route": "commit_candidate"}
        return {"route": "close" if state.get("skip_generation") else "build_failure_bundle"}

    def _build_failure_bundle(self, state, session):
        payload = sanitize(
            {
                "validation": state["validation"],
                "spec_errors": state["spec_errors"],
                "issue_summary": "自动验证未通过",
                "spec_hash": state["spec"]["spec_hash"],
                "workspace": state["generated"].get("workspace"),
                "changed_files": state["generated"]["changed_files"],
            }
        )
        original = state.get("original_failure_bundle") or {}
        if state["validation"].get("spec_patch_errors"):
            payload["issue_summary"] = (
                "Spec 补丁结构不符合契约。依据 spec_patch_errors 和 spec_patch_schema 修正补丁；"
                "保留已通过的采集器修复与测试，不需要重新分析整个站点。"
            )
            payload["spec_patch_schema"] = Spec.model_json_schema()
        for key in (
            "manual_run_id",
            "code_revision",
            "artifact_manifest_ref",
            "affected_samples",
            "affected_sample_count",
            "evidence_refs",
            "collection_options",
        ):
            if key in original:
                payload[key] = original[key]
        diagnosis = diagnose_failure(payload)
        bundle = FailureBundle(
            bundle_id=_id(),
            task_id=self.task_id,
            run_id=self.run_id,
            failure_type="AUTO_VALIDATION_FAILED",
            code_fixable=diagnosis["code_fixable"],
            bundle_json=payload,
        )
        session.add(bundle)
        return {"bundle_id": bundle.bundle_id, "bundle": payload}

    def _diagnose_failure(self, state, session):
        diagnosis = diagnose_failure(state["bundle"])
        exhausted = state["attempt"] >= get_settings().max_repair_attempts
        reason = "REPAIR_BUDGET_EXCEEDED" if exhausted else diagnosis["category"]
        if exhausted or not diagnosis["code_fixable"]:
            bundle = session.scalar(
                select(FailureBundle).where(FailureBundle.bundle_id == state["bundle_id"])
            )
            bundle.status, bundle.code_fixable = "NEEDS_REVIEW", diagnosis["code_fixable"]
            return {"route": "close", "stop_reason": reason}
        return {"route": "patch_code", "diagnosis": diagnosis}

    def _patch_code(self, state, session):
        attempt = state["attempt"] + 1
        task, run = self._task_run(session)
        run.repair_attempt = max(run.repair_attempt, attempt)
        repair_id = f"{self.run_id[:24]}{attempt:08d}"
        repair = session.scalar(select(RepairRun).where(RepairRun.repair_run_id == repair_id))
        if not repair:
            repair = RepairRun(
                repair_run_id=repair_id,
                task_id=self.task_id,
                bundle_id=state["bundle_id"],
                attempt=attempt,
                diagnosis_json=state["diagnosis"],
                status="RUNNING",
            )
            session.add(repair)
        session.commit()
        generated = state.get("generated", {})
        result = self.repairer.repair(
            state["platform_key"],
            task_id=self.task_id,
            run_id=self.run_id,
            base_ref=generated.get("baseline_ref") or state.get("baseline_ref"),
            spec=state["spec"],
            failure_bundle=state["bundle"],
            workspace=generated.get("workspace"),
            execution=self.execution,
            attempt=attempt,
            event_callback=self._events,
        )
        repair.changed_files = result["changed_files"]
        run.worktree_path = result.get("workspace")
        return {
            "attempt": attempt,
            "generated": result,
            "repair_run_id": repair_id,
            "spec": self._apply_spec_patch(result, state, session),
        }

    def _commit_candidate(self, state, session):
        task, run = self._task_run(session)
        started = run.started_at.replace(tzinfo=UTC).isoformat()
        session.commit()
        # Serialize publication with pause/cancel and review requests.
        session.scalar(
            select(OnboardingTask).where(OnboardingTask.task_id == self.task_id).with_for_update()
        )
        candidate = self.publisher(
            state["generated"]["workspace"],
            platform_key=state["platform_key"],
            run_id=self.run_id,
            baseline=state["generated"]["baseline_ref"],
            commit_time=started,
            repair=state["kind"] == "repair" or state["attempt"] > 0,
            guard=self.execution.guard,
        )
        run.context_json = {
            **(run.context_json or {}),
            "spec_binding": {
                "spec_revision": state["spec"]["spec_revision"],
                "spec_hash": state["spec"]["spec_hash"],
                "binding_source": "validated_candidate",
            },
        }
        for old in session.scalars(
            select(CodeSubmission).where(
                CodeSubmission.task_id == self.task_id,
                CodeSubmission.adoption_status == "candidate",
            )
        ):
            old.adoption_status = "superseded"
        session.add(
            CodeSubmission(
                submission_id=_id(),
                task_id=self.task_id,
                run_id=self.run_id,
                branch_name=candidate["branch_name"],
                commit_sha=candidate["commit_sha"],
                baseline_ref=candidate["baseline_ref"],
                changed_files=candidate["changed_files"],
                submission_type=state["kind"],
                adoption_status="candidate",
                simulated=False,
            )
        )
        report = session.scalar(
            select(OnboardingReport).where(OnboardingReport.report_id == state["report_id"])
        )
        report.report_json = {
            **report.report_json,
            "candidate_commit": candidate["commit_sha"],
            "candidate_branch": candidate["branch_name"],
            "next_action": "WAIT_MANUAL_RUN",
        }
        report.next_action = "WAIT_MANUAL_RUN"
        for bundle in session.scalars(
            select(FailureBundle).where(FailureBundle.run_id == self.run_id)
        ):
            bundle.status = "REPAIRED"
        if state.get("input_bundle_id"):
            bundle = session.scalar(
                select(FailureBundle).where(FailureBundle.bundle_id == state["input_bundle_id"])
            )
            bundle.status = "REPAIRED"
        spec = session.scalar(
            select(PlatformSpec).where(
                PlatformSpec.task_id == self.task_id, PlatformSpec.is_current.is_(True)
            )
        )
        spec.status = "NEEDS_REVIEW" if state.get("spec_errors") else "CANDIDATE"
        return {"candidate": candidate}

    def _await_manual_run(self, state):
        with self.factory.begin() as session:
            session.scalar(
                select(OnboardingTask)
                .where(OnboardingTask.task_id == self.task_id)
                .with_for_update()
            )
            task, run = self._task_run(session)
            manual = session.scalar(
                select(ManualRun)
                .where(
                    ManualRun.task_id == self.task_id,
                    ManualRun.code_revision == state["candidate"]["commit_sha"],
                )
                .order_by(ManualRun.id.desc())
                .limit(1)
            )
            if manual:
                return {"manual_run_id": manual.manual_run_id}
            task.status, run.status = "WAITING_MANUAL_RUN", "WAITING_MANUAL_RUN"
            task.next_action = "WAIT_MANUAL_RUN"
        interrupt({"kind": "manual_run", "commit_sha": state["candidate"]["commit_sha"]})
        # A resume token cannot substitute for a recorded human run.
        return self._await_manual_run(state)

    def _record_review(self, state):
        with self.factory.begin() as session:
            session.scalar(
                select(OnboardingTask)
                .where(OnboardingTask.task_id == self.task_id)
                .with_for_update()
            )
            task, run = self._task_run(session)
            latest_manual = session.scalar(
                select(ManualRun)
                .where(
                    ManualRun.task_id == self.task_id,
                    ManualRun.code_revision == state["candidate"]["commit_sha"],
                )
                .order_by(ManualRun.id.desc())
                .limit(1)
            )
            manual_id = latest_manual.manual_run_id if latest_manual else state["manual_run_id"]
            review = session.scalar(
                select(ManualReview).where(
                    ManualReview.task_id == self.task_id,
                    ManualReview.manual_run_id == manual_id,
                    ManualReview.code_revision == state["candidate"]["commit_sha"],
                )
            )
            if review:
                run.status, run.finished_at = "COMPLETED", _now()
                return {"review_id": review.review_id, "manual_run_id": manual_id}
            task.status, run.status = "WAITING_MANUAL_REVIEW", "WAITING_MANUAL_REVIEW"
            task.next_action = "WAIT_REVIEW"
        interrupt({"kind": "review", "manual_run_id": manual_id})
        return self._record_review(state)

    def _close(self, state, session):
        task, run = self._task_run(session)
        task.status, run.status, run.finished_at = "BLOCKED", "BLOCKED", _now()
        task.next_action = "REQUEST_INPUT"
        reason = state.get("stop_reason", state["observation"].get("observation_code"))
        run.error_code = reason
        report = session.scalar(
            select(OnboardingReport).where(OnboardingReport.report_id == task.last_report_id)
        )
        if report is None or report.run_id != self.run_id:
            result = self._build_report(state, session)
            session.flush()
            report = session.scalar(
                select(OnboardingReport).where(OnboardingReport.report_id == result["report_id"])
            )
        if report:
            report.report_json = {**report.report_json, "stop_reason": reason}
        return {"stop_reason": reason}


def run_onboarding_task(task_id: str) -> dict:
    from auto_spider.db.session import SessionLocal

    with SessionLocal() as session:
        return WorkflowRunner().run_onboarding(session, task_id)


def run_repair_task(task_id: str, bundle_id: str) -> dict:
    from auto_spider.db.session import SessionLocal

    with SessionLocal() as session:
        return WorkflowRunner().run_repair(session, task_id, bundle_id)
