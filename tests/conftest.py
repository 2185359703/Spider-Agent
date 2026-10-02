from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auto_spider.db.base import Base


@pytest.fixture(autouse=True)
def deterministic_workflow_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Keep tests offline even when a developer's .env enables OpenHands."""
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    monkeypatch.setenv("EVIDENCE_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setenv("WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setenv("AGENT_POLICY_ROOT", str(tmp_path / "agent-policies"))
    from auto_spider.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
