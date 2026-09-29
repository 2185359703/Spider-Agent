import json

import pytest
from pydantic import ValidationError

from auto_spider.schemas import (
    Confidence,
    DecodeMode,
    DecodeSpec,
    IdentitySpec,
    PaginationMode,
    PaginationSpec,
    SelectorKind,
    SelectorSource,
    SelectorSpec,
)
from auto_spider.services.spec_builder import build_fake_platform_spec


def test_platform_spec_hash_is_canonical_and_keeps_ids_null() -> None:
    spec = build_fake_platform_spec(
        task_id="task-001",
        platform_key="example_company",
        source_name="Example",
        entry_url="https://example.com/jobs?fixture=jobs",
        observation={
            "list_found": True,
            "detail_found": True,
            "list_count": 2,
            "internship_count": 1,
            "valid_record_count": 1,
        },
        evidence_id="evidence-001",
    ).with_hash()

    assert spec.identity.platform_id is None
    assert spec.identity.entity_id is None
    assert spec.spec_hash == spec.calculated_hash()
    assert spec.canonical_json() == json.dumps(
        spec.canonical_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_low_confidence_hard_field_blocks_candidate() -> None:
    spec = build_fake_platform_spec(
        task_id="task-001",
        platform_key="example_company",
        source_name="Example",
        entry_url="https://example.com/jobs",
        observation={
            "list_found": False,
            "detail_found": False,
            "list_count": None,
            "internship_count": None,
            "valid_record_count": 0,
        },
        evidence_id="evidence-001",
    )
    errors = spec.candidate_blocking_errors()
    assert "missing_list_endpoint" in errors
    assert "missing_source_id_selector" in errors


def test_unbounded_pagination_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PaginationSpec(mode=PaginationMode.PAGE, page_param="page", termination=[])


def test_zero_business_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        IdentitySpec(
            entry_url="https://example.com",
            normalized_url="https://example.com",
            platform_id=0,
        )


def test_inferred_selector_requires_reason() -> None:
    with pytest.raises(ValidationError):
        SelectorSpec(
            source=SelectorSource.DETAIL,
            kind=SelectorKind.CSS,
            expression=".title",
            confidence=Confidence.MEDIUM,
            inferred=True,
        )


def test_opaque_response_requires_decoder_helper() -> None:
    selector = SelectorSpec(
        source=SelectorSource.RESPONSE_BODY,
        kind=SelectorKind.JSON_PATH,
        expression="$.data",
        confidence=Confidence.HIGH,
        evidence_refs=["evidence-mokahr-list"],
    )
    with pytest.raises(ValidationError):
        DecodeSpec(mode=DecodeMode.OPAQUE, input_selector=selector, helper_required=True)
