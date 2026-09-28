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
    codex_model: str = "gpt-6-sol"
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
