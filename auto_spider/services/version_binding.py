"""Bind code snapshots to immutable specs instead of consulting an unrelated latest spec."""

from copy import deepcopy

from sqlalchemy import select

from auto_spider.db.models import OnboardingReport, PlatformSpec, WorkflowCheckpoint, WorkflowRun
from auto_spider.schemas import PlatformSpec as Spec


def submission_spec(session, submission):
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == submission.run_id))
    binding = (run.context_json or {}).get("spec_binding") if run else None
    if binding:
        row = session.scalar(
            select(PlatformSpec).where(
                PlatformSpec.task_id == submission.task_id,
                PlatformSpec.spec_version == binding["spec_revision"],
                PlatformSpec.spec_hash == binding["spec_hash"],
            )
        )
        if row is None:
            raise ValueError("CODE_SPEC_BINDING_MISSING")
        return row, binding.get("binding_source", "code_version")
    reports = session.scalars(
        select(OnboardingReport)
        .where(OnboardingReport.run_id == submission.run_id)
        .order_by(OnboardingReport.id.desc())
    )
    for report in reports:
        data = report.report_json
        if data.get("candidate_commit") not in {None, submission.commit_sha}:
            continue
        if data.get("spec_revision") and data.get("spec_hash"):
            row = session.scalar(
                select(PlatformSpec).where(
                    PlatformSpec.task_id == submission.task_id,
                    PlatformSpec.spec_version == data["spec_revision"],
                    PlatformSpec.spec_hash == data["spec_hash"],
                )
            )
            if row:
                return row, "candidate_report"
    snapshots = session.scalars(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.run_id == submission.run_id, WorkflowCheckpoint.node == "build_spec"
        )
        .order_by(WorkflowCheckpoint.id.desc())
    )
    for snapshot in snapshots:
        version = snapshot.state_json.get("spec_version") or snapshot.state_json.get(
            "spec", {}
        ).get("spec_revision")
        if version:
            row = session.scalar(
                select(PlatformSpec).where(
                    PlatformSpec.task_id == submission.task_id, PlatformSpec.spec_version == version
                )
            )
            if row:
                return row, "workflow_checkpoint"
    # Pre-binding historical local versions are retained, but their provenance
    # must remain explicit and requires review; do not label this a verified match.
    row = session.scalar(
        select(PlatformSpec).where(
            PlatformSpec.task_id == submission.task_id, PlatformSpec.is_current.is_(True)
        )
    )
    return row, "legacy_unverified"


def binding_for(row, source="code_version"):
    if row is None:
        return None
    return {"spec_revision": row.spec_version, "spec_hash": row.spec_hash, "binding_source": source}


def manual_spec_revision(session, task, source, *, candidate: bool):
    original, provenance = submission_spec(session, source)
    if original is None:
        return None
    rows = session.scalars(select(PlatformSpec).where(PlatformSpec.task_id == task.task_id)).all()
    data = deepcopy(original.spec_json)
    data.update(
        spec_revision=max(r.spec_version for r in rows) + 1,
        spec_hash=None,
        status="NEEDS_REVIEW" if candidate else "DRAFT",
    )
    data["generation"]["baseline_ref"] = source.commit_sha
    spec = Spec.model_validate(data).with_hash()
    if candidate:
        for row in rows:
            row.is_current = False
    row = PlatformSpec(
        task_id=task.task_id,
        schema_version=spec.schema_version,
        spec_version=spec.spec_revision,
        spec_hash=spec.spec_hash,
        status=spec.status.value,
        confidence_summary=spec.confidence_counts(),
        spec_json=spec.model_dump(mode="json"),
        evidence_manifest_ref=spec.evidence.manifest_ref,
        is_current=candidate,
    )
    session.add(row)
    return row
