"""Audit the actual persisted outcome after an opt-in acceptance probe finishes."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from auto_spider.schemas import PlatformSpec


def audit(root: Path):
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    assert manifest["status"] == "PASS", manifest.get("error", manifest["status"])
    db = sqlite3.connect(root / manifest.get("database_export", "acceptance.db"))
    db.row_factory = sqlite3.Row
    task = db.execute(
        "select * from onboarding_tasks where task_id=?", (manifest["task_id"],)
    ).fetchone()
    assert task["status"] == "ADOPTED"
    assert task["platform_id"] is None and task["entity_id"] is None
    run = db.execute(
        "select * from workflow_runs where run_id=?", (task["current_run_id"],)
    ).fetchone()
    assert run["status"] == "COMPLETED", dict(run)
    code = db.execute(
        "select * from code_submissions where commit_sha=?", (manifest["final_commit"],)
    ).fetchone()
    assert code["adoption_status"] == "adopted" and not code["simulated"]
    binding = json.loads(run["context_json"])["spec_binding"]
    spec = db.execute(
        "select * from platform_specs where task_id=? and spec_version=?",
        (manifest["task_id"], binding["spec_revision"]),
    ).fetchone()
    document = PlatformSpec.model_validate_json(spec["spec_json"])
    assert spec["spec_hash"] == binding["spec_hash"] == document.calculated_hash()
    assert not document.candidate_blocking_errors(), document.candidate_blocking_errors()
    reviews = list(
        db.execute(
            "select * from manual_reviews where task_id=? order by id", (manifest["task_id"],)
        )
    )
    assert [r["review_status"] for r in reviews] == ["CODE_FIX_REQUIRED", "PASS"]
    evidence_root = root.parents[1]
    datasets = []
    for review in reviews:
        manual = db.execute(
            "select * from manual_runs where manual_run_id=?", (review["manual_run_id"],)
        ).fetchone()
        assert manual["status"] == "REVIEWED" and manual["code_revision"] == review["code_revision"]
        data = json.loads(manual["result_json"])
        datasets.append(json.loads((evidence_root / data["records_ref"]).read_text("utf-8")))
    assert datasets[0] and datasets[1]
    assert all("验收错误-" in r["raw_content"]["job"]["title"] for r in datasets[0])
    assert all(
        r["raw_content"]["job"]["title"] == r["raw_content"]["source_payload"]["detail"]["title"]
        for r in datasets[1]
    )
    for row in db.execute(
        "select relative_path,sha256 from evidence_files where file_type like 'failure_%'"
    ):
        assert (
            hashlib.sha256((evidence_root / row["relative_path"]).read_bytes()).hexdigest()
            == row["sha256"]
        )
    assert not db.execute(
        "select 1 from agent_executions where step_key='inspect_site:0'"
    ).fetchone()
    assert not db.execute(
        "select 1 from workflow_dispatches where status in ('PENDING','SENT')"
    ).fetchone()
    output = {
        "status": "PASS",
        "task_id": manifest["task_id"],
        "final_commit": code["commit_sha"],
        "spec_revision": spec["spec_version"],
        "original_record_count": len(datasets[0]),
        "recollected_record_count": len(datasets[1]),
        "source_records_preserved": True,
        "evidence_hashes_verified": True,
        "review_and_workflow_settled": True,
        "no_reanalysis_of_human_edit": True,
        "ids_remain_null": True,
    }
    (root / "completion-audit.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    audit(Path(sys.argv[1]))
