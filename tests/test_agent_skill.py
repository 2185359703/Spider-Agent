from pathlib import Path

import pytest

from auto_spider.ai.skill_loader import (
    load_collector_agent_skill,
    load_spider_king_agent_skill,
    load_trusted_agent_skills,
)


def test_collector_skill_loads_entrypoint_and_references() -> None:
    content = load_collector_agent_skill()

    assert "## SKILL.md" in content
    assert "## references/collector-contract.md" in content
    assert "## references/verification.md" in content
    assert "platform_id" in content
    assert "Live gate" in content


def test_collector_skill_missing_entrypoint_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="AGENT_SKILL_MISSING"):
        load_collector_agent_skill(tmp_path)


def test_spider_king_adapter_and_combined_prompt_are_loadable() -> None:
    spider_skill = load_spider_king_agent_skill()
    combined = load_trusted_agent_skills()

    assert "name: spider-king-collector" in spider_skill
    assert "## references/protocol-recovery.md" in spider_skill
    assert '<skill name="collector-onboarding">' in combined
    assert '<skill name="spider-king-collector">' in combined
