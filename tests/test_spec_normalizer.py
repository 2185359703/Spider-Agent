import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_spider.services.analysis_submission import DiscoveryDraft
from auto_spider.services.spec_builder import build_platform_spec
from auto_spider.services.spec_normalizer import normalize_spec_draft

FIXTURE = Path(__file__).parent / "fixtures" / "platform_specs" / "sanqi" / "platform_spec.json"


def draft_with_offset_alias():
    draft = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The fixture is a canonical PlatformSpec envelope; keep only the typed
    # discovery sections expected by DiscoveryDraft.
    draft = {
        name: draft[name]
        for name in ("adapter", "endpoints", "fields", "pagination", "filters")
    }
    draft["endpoints"]["list"]["body"] = {"offset": 0, "limit": 20}
    draft["pagination"] = deepcopy(draft["pagination"])
    draft["pagination"]["page_param"] = None
    draft["pagination"]["cursor_param"] = "offset"
    return draft


def test_offset_cursor_alias_is_repaired_from_captured_request():
    draft = draft_with_offset_alias()
    normalized, corrections = normalize_spec_draft(draft)

    assert normalized["pagination"]["page_param"] == "offset"
    assert normalized["pagination"]["cursor_param"] is None
    assert {item["code"] for item in corrections} == {"PAGINATION_CURSOR_ALIAS"}
    assert draft["pagination"]["page_param"] is None


def test_typed_analysis_accepts_the_repaired_alias_without_agent_retry():
    model = DiscoveryDraft.model_validate(draft_with_offset_alias())
    assert model.pagination.page_param == "offset"
    assert model.pagination.cursor_param is None


def test_platform_spec_builder_repairs_the_same_alias_from_request_evidence():
    draft = draft_with_offset_alias()
    spec = build_platform_spec(
        task_id="task-spec-normalizer",
        platform_key="sanqi",
        source_name="三七互娱",
        entry_url="https://example.test/jobs",
        observation={
            "spec_draft": draft,
            "list_body": {"offset": 0, "limit": 20},
            "detail_found": True,
        },
        evidence_id="evidence-spec-normalizer",
    )
    assert spec.pagination.page_param == "offset"
    assert spec.pagination.cursor_param is None


def test_unobserved_cursor_alias_is_not_guessed():
    draft = draft_with_offset_alias()
    draft["endpoints"]["list"]["body"].pop("offset", None)
    draft["endpoints"]["list"]["query"] = {}

    with pytest.raises(ValidationError, match="offset 模式必须提供 page_param"):
        DiscoveryDraft.model_validate(draft)
