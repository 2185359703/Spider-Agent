from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auto_spider.schemas import (
    CreateBatchRequest,
    IntakeItem,
    ManualRunRequest,
    platform_key_from_url,
)


def test_intake_keeps_platform_and_entity_ids_out_of_input() -> None:
    request = CreateBatchRequest(
        items=[IntakeItem(entry_url="https://careers.example.com/internships")],
        client_request_id="request-0001",
    )
    assert request.items[0].platform_key is None
    assert platform_key_from_url(str(request.items[0].entry_url)) == "careers_example_com"


def test_invalid_platform_key_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IntakeItem(
            entry_url="https://careers.example.com",
            platform_key="Company With Spaces",
        )


def test_manual_run_requires_monotonic_timestamps() -> None:
    with pytest.raises(ValidationError):
        ManualRunRequest(
            code_revision="abc123",
            environment_fingerprint="test",
            started_at=datetime(2026, 1, 2, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, tzinfo=UTC),
            artifact_manifest_ref="evidence/manifest.json",
            client_request_id="manual-0001",
        )
