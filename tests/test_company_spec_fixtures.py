import json
from pathlib import Path

from jsonschema import Draft202012Validator

from auto_spider.schemas import PlatformSpec


def test_nine_company_platform_spec_fixtures_validate():
    root = Path(__file__).parent / "fixtures" / "platform_specs"
    files = sorted(root.glob("*/platform_spec.json"))
    assert {path.parent.name for path in files} == {
        "kuaishou",
        "mihoyo",
        "lilith_games",
        "papegames",
        "sanqi",
        "hypergryph",
        "xd",
        "zulong",
        "g_bits",
    }
    schema = PlatformSpec.model_json_schema()
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        list(Draft202012Validator(schema).iter_errors(payload))
        spec = PlatformSpec.model_validate(payload)
        assert spec.identity.platform_id is None
        assert spec.identity.entity_id is None
        assert spec.spec_hash == spec.calculated_hash()
