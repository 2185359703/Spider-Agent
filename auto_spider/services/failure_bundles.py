"""Preserve the exact human-reviewed collection as a bounded Agent-readable evidence pack."""

import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from auto_spider.config import get_settings
from auto_spider.db.models import WorkflowLog
from auto_spider.schemas import PlatformSpec as Spec
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.evidence import EvidenceStore, sanitize
from auto_spider.services.version_binding import binding_for, submission_spec


def evidence_path(task_id, relative):
    root = get_settings().evidence_root.resolve()
    rel = Path(relative)
    target = (root / rel).resolve()
    if (
        rel.is_absolute()
        or ".." in rel.parts
        or not rel.parts
        or rel.parts[0] != task_id
        or (root / task_id).resolve() not in target.parents
        or target.is_symlink()
    ):
        raise ValueError("COLLECTION_EVIDENCE_SCOPE_VIOLATION")
    if not target.is_file():
        raise ValueError("COLLECTION_EVIDENCE_MISSING")
    return target


def collection_records(run):
    result = run.result_json or {}
    if result.get("records_ref"):
        rows = json.loads(evidence_path(run.task_id, result["records_ref"]).read_text("utf-8"))
    else:
        rows = result.get("samples", [])
    if not isinstance(rows, list):
        raise ValueError("COLLECTION_RECORDS_INVALID")
    return rows


def build_failure_evidence(session, task, manual, candidate, request, bundle_id):
    records = collection_records(manual)
    workflow_id = candidate.run_id
    prefix = f"failure-bundles/{bundle_id}"
    store = EvidenceStore()
    indexed = []

    def artifact(name, data, kind):
        safe = json.loads(sanitize_text(json.dumps(sanitize(data), ensure_ascii=False)))
        row = store.write_json(
            session,
            task_id=task.task_id,
            run_id=workflow_id,
            name=f"{prefix}/{name}",
            payload=safe,
            file_type=kind,
        )
        indexed.append(row.evidence_id)
        return {
            "path": name,
            "evidence_ref": row.relative_path,
            "evidence_id": row.evidence_id,
            "sha256": row.sha256,
        }

    selected = sorted({index for issue in request.field_issues for index in issue.sample_indices})
    if any(index < 1 or index > len(records) for index in selected):
        raise ValueError("FAILURE_SAMPLE_NOT_FOUND")
    # A general collection problem still gets real representative records, if
    # available. For an exception with no records, diagnostics remain the source.
    if not selected:
        selected = list(range(1, min(len(records), 3) + 1))
    sample_refs = []
    for index in selected:
        item = records[index - 1]
        raw = item.get("raw_content") if isinstance(item, dict) else None
        job = raw.get("job") if isinstance(raw, dict) else item
        job = job if isinstance(job, dict) else {}
        details = []
        for issue in request.field_issues:
            if index in issue.sample_indices:
                details.append(
                    {
                        "field": issue.field,
                        "issue_type": issue.issue_type,
                        "description": issue.description,
                        "actual": issue.actual
                        if issue.actual is not None
                        else job.get(issue.field),
                        "expected": issue.expected,
                        "reviewer_note": issue.description,
                    }
                )
        sample_refs.append(
            {
                "sample_index": index,
                "source_id": item.get("source_id") if isinstance(item, dict) else None,
                "source_url": (item.get("source_url") or job.get("source_url"))
                if isinstance(item, dict)
                else None,
                **artifact(
                    f"samples/{index}.json",
                    {"sample_index": index, "record": item, "field_issues": details},
                    "failure_sample",
                ),
            }
        )
    spec_row, provenance = submission_spec(session, candidate)
    spec_ref = (
        artifact("platform-spec.json", spec_row.spec_json, "failure_spec") if spec_row else None
    )
    schema_ref = artifact("platform-spec-schema.json", Spec.model_json_schema(), "failure_schema")
    records_ref = artifact("records.json", records, "failure_records")
    logs = []
    for row in session.scalars(
        select(WorkflowLog)
        .where(WorkflowLog.task_id == task.task_id)
        .order_by(WorkflowLog.sequence.desc())
        .limit(500)
    ):
        if (row.detail_json or {}).get("manual_run_id") == manual.manual_run_id:
            logs.append(
                {
                    "sequence": row.sequence,
                    "stage": row.stage,
                    "level": row.level,
                    "message": row.message,
                    "detail": row.detail_json,
                }
            )
        if len(logs) >= 50:
            break
    result = manual.result_json or {}
    diagnostics = {}
    if result.get("diagnostics_ref"):
        diagnostics = json.loads(
            evidence_path(task.task_id, result["diagnostics_ref"]).read_text("utf-8")
        )
    diagnostics_ref = artifact(
        "collection-diagnostics.json",
        {
            "manual_run_id": manual.manual_run_id,
            "code_revision": manual.code_revision,
            "options": result.get("options", {}),
            "command_profile": manual.command_profile,
            "environment_fingerprint": manual.environment_fingerprint,
            "status": manual.status,
            "error_msg": result.get("error_msg"),
            "error_code": result.get("error_code"),
            "started_at": manual.started_at.isoformat(),
            "finished_at": manual.finished_at.isoformat(),
            "diagnostics": diagnostics,
            "logs": list(reversed(logs)),
        },
        "failure_diagnostics",
    )
    snapshot_hash = hashlib.sha256(
        json.dumps(sanitize(records), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    manifest = {
        "schema_version": "failure-bundle-v2",
        "task_id": task.task_id,
        "bundle_id": bundle_id,
        "manual_run_id": manual.manual_run_id,
        "code_revision": manual.code_revision,
        "submission_id": candidate.submission_id,
        "baseline_ref": candidate.baseline_ref,
        "spec_binding": binding_for(spec_row, provenance),
        "spec": spec_ref,
        "spec_schema": schema_ref,
        "collection_records": records_ref,
        "collection_options": result.get("options", {}),
        "record_count": len(records),
        "records_snapshot_sha256": snapshot_hash,
        "original_records_ref": result.get("records_ref"),
        "affected_samples": sample_refs,
        "field_issues": [issue.model_dump(mode="json") for issue in request.field_issues],
        "sample_decisions": [
            decision.model_dump(mode="json") for decision in request.sample_decisions
        ],
        "issue_summary": request.issue_summary,
        "diagnostics": diagnostics_ref,
        "sanitized": True,
    }
    receipt = artifact("manifest.json", manifest, "failure_manifest")
    # Only the index goes into the prompt; complete records and diagnostics are
    # preserved in the scoped evidence directory, not discarded by truncation.
    bundle = {
        **manifest,
        "affected_samples": sample_refs[:20],
        "affected_sample_count": len(sample_refs),
        "artifact_manifest_ref": receipt["evidence_ref"],
        "artifact_manifest_sha256": receipt["sha256"],
        "evidence_refs": indexed,
    }
    return bundle
