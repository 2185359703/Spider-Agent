import json
from pathlib import Path

from auto_spider.schemas import PlatformSpec

FIXTURE = Path(__file__).parent / "fixtures" / "platform_specs" / "kuaishou"


def test_kuaishou_real_evidence_fixture_is_valid_platform_spec() -> None:
    payload = json.loads((FIXTURE / "platform_spec.json").read_text(encoding="utf-8"))
    spec = PlatformSpec.model_validate(payload)

    assert spec.platform_key == "kuaishou"
    assert spec.identity.platform_id is None
    assert spec.identity.entity_id is None
    assert spec.endpoints.list is not None
    assert spec.endpoints.detail is not None
    assert spec.pagination.page_param == "pageNum"
    assert spec.pagination.total_count_selector is not None
    assert spec.spec_hash == spec.calculated_hash()
    assert (FIXTURE / "manifest.json").exists()
