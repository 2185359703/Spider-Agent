from copy import deepcopy
from types import SimpleNamespace

from auto_spider.db.models import CodeSubmission, OnboardingTask, PlatformSpec, WorkflowRun
from auto_spider.services.spec_builder import build_platform_spec
from auto_spider.services.version_binding import submission_spec
from tests.test_admin_ui_api import admin_context as admin_fixture

admin_context = admin_fixture


def test_manual_edit_binds_new_spec_without_overwriting_previous(
    admin_context, monkeypatch, tmp_path
):
    from auto_spider.services import code_edit

    client, session, _, commit = admin_context
    spec = build_platform_spec(
        task_id="task",
        platform_key="example",
        source_name="公司",
        entry_url="https://example.com",
        evidence_id="evidence-1",
        observation={
            "list_found": True,
            "detail_found": True,
            "pagination_verified": True,
            "list_count": 1,
            "internship_count": 1,
            "valid_record_count": 1,
        },
    ).with_hash()
    original = PlatformSpec(
        task_id="task",
        spec_version=1,
        schema_version="1.0",
        spec_hash=spec.spec_hash,
        status="CANDIDATE",
        spec_json=spec.model_dump(mode="json"),
    )
    session.add(original)
    source_run = session.query(WorkflowRun).one()
    source_run.context_json = {"spec_binding": {"spec_revision": 1, "spec_hash": spec.spec_hash}}
    session.commit()
    previous_json = deepcopy(original.spec_json)
    check = SimpleNamespace(status="PASS")
    monkeypatch.setattr(
        code_edit,
        "validate_candidate",
        lambda *_args, **_kwargs: SimpleNamespace(
            compile=check,
            pytest=check,
            ruff=check,
            contract_status="PASS",
            business_status="PASS",
            passed=True,
            as_dict=lambda: {"passed": True},
        ),
    )
    response = client.put(
        "/api/v1/admin/tasks/task/code/submission",
        json={
            "path": "collectors/example.py",
            "content": "# independently edited version\n",
            "base_commit": commit,
            "client_request_id": "p1-spec-edit-0001",
        },
    )
    assert response.status_code == 200, response.text
    edited = (
        session.query(CodeSubmission)
        .filter_by(submission_id=response.json()["submission_id"])
        .one()
    )
    session.expire_all()
    bound, provenance = submission_spec(session, edited)
    assert bound.spec_version == bound.spec_json["spec_revision"] == 2
    assert bound.spec_hash != original.spec_hash
    assert bound.status == "NEEDS_REVIEW"
    assert original.spec_json == previous_json and not original.is_current
    assert bound.spec_json["identity"]["platform_id"] is None
    source = session.query(CodeSubmission).filter_by(submission_id="submission").one()
    assert submission_spec(session, source)[0].spec_version == 1
    assert session.query(OnboardingTask).one().last_report_id
