from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./auto_spider.db"
    redis_url: str = "redis://127.0.0.1:6379/0"
    evidence_root: Path = Path("./evidence")
    worktree_root: Path = Path("./worktrees")
    collector_repo_path: Path = Path(r"C:\Users\ASUS\Desktop\jichu-v5-sync")
    collector_baseline_ref: str = "d1f3c72ec5e10041f32914d465464808f5c18d9a"
    openhands_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    openhands_server_url: str = "http://127.0.0.1:8000"
    openhands_session_api_key: str | None = None
    openhands_llm_api_key: str | None = None
    openhands_secret_key: str | None = None
    agent_mode: str = "fake"
    analysis_mode: str = "fake"
    auth_mode: str = "dev"
    auto_spider_eager_workflow: bool = False
    queue_enabled: bool = False
    max_repair_attempts: int = 3
    max_evidence_bytes: int = 5_000_000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.evidence_root.mkdir(parents=True, exist_ok=True)
    settings.worktree_root.mkdir(parents=True, exist_ok=True)
    return settings
