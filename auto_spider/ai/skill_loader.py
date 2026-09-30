from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from auto_spider.config import get_settings


@lru_cache(maxsize=8)
def _load_skill(root_value: str) -> str:
    root = Path(root_value).resolve()
    entrypoint = (root / "SKILL.md").resolve()
    if root not in entrypoint.parents or not entrypoint.is_file():
        raise RuntimeError(f"AGENT_SKILL_MISSING: {entrypoint}")

    files = [entrypoint]
    references = (root / "references").resolve()
    if references.is_dir() and root in references.parents:
        files.extend(sorted(path for path in references.glob("*.md") if path.is_file()))

    parts: list[str] = []
    for path in files:
        resolved = path.resolve()
        if root not in resolved.parents:
            raise RuntimeError(f"AGENT_SKILL_PATH_ESCAPE: {resolved}")
        relative = resolved.relative_to(root).as_posix()
        parts.append(f"## {relative}\n\n{resolved.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


def load_collector_agent_skill(path: Path | None = None) -> str:
    root = (path or get_settings().collector_agent_skill_path).resolve()
    return _load_skill(str(root))


def load_spider_king_agent_skill(path: Path | None = None) -> str:
    root = (path or get_settings().spider_king_agent_skill_path).resolve()
    return _load_skill(str(root))


def load_trusted_agent_skills() -> str:
    skills = (
        ("collector-onboarding", load_collector_agent_skill()),
        ("spider-king-collector", load_spider_king_agent_skill()),
    )
    return "\n\n".join(
        f'<skill name="{name}">\n{content}\n</skill>' for name, content in skills
    )
