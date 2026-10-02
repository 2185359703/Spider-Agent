import pytest

from auto_spider.services.failure_diagnosis import diagnose_failure
from tests.fakes import fake_validation


@pytest.mark.parametrize(
    "bundle,fixable,category",
    [
        (
            {"field_issues": [{"code_fixable": False}, {"code_fixable": True}]},
            True,
            "CODE_OR_CONFIG",
        ),
        (
            {"validation": {**fake_validation({}, {}), "pytest_status": "FAIL"}},
            True,
            "CODE_OR_CONFIG",
        ),
        (
            {
                "validation": {
                    **fake_validation({}, {}),
                    "live_status": "FAIL",
                    "details": "SSLerror",
                }
            },
            False,
            "ENVIRONMENT_OR_ACCESS",
        ),
        ({"validation": {}}, False, "TOOL_UNAVAILABLE"),
        (
            {"validation": fake_validation({}, {}), "spec_errors": ["missing_detail_endpoint"]},
            False,
            "EVIDENCE_INSUFFICIENT",
        ),
        ({"issue_summary": "CODE_SCOPE_VIOLATION: base.py"}, False, "POLICY_VIOLATION"),
    ],
)
def test_failure_routing(bundle, fixable, category):
    result = diagnose_failure(bundle)
    assert result == {"code_fixable": fixable, "category": category}
