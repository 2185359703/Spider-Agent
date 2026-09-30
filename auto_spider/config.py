from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./auto_spider.db"
    redis_url: str = "redis://127.0.0.1:6379/0"
    evidence_root: Path = Path("./evidence")
    worktree_root: Path = Path("./worktrees")
    collector_agent_skill_path: Path = Path("./agent_skills/collector-onboarding")
    spider_king_agent_skill_path: Path = Path("./agent_skills/spider-king-collector")
    collector_source_repo_path: Path = Path(
        r"D:\Project\Auto_spider_repositories\fun-crawler-v2"
    )
    aicoding_repo_path: Path = Path(
        r"D:\Project\Auto_spider_repositories\aicoding-auto_spider"
    )
    collector_repo_path: Path | None = None
    collector_source_baseline_ref: str = "16cba8439396e371973e4ee0301d3a88f2f32ba5"
    aicoding_baseline_ref: str = "c932dbac4bb75df04008c85d3560c1b7a28ec211"
    aicoding_remote_url: str = "https://gitee.com/daxia-com/auto_spider.git"
    aicoding_push_enabled: bool = False
    openhands_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    openhands_llm_base_url: str | None = None
    openhands_llm_api_mode: str = "auto"
    openhands_server_url: str = "http://127.0.0.1:8000"
    openhands_workspace_root: str = "/srv/auto_spider/worktrees"
    openhands_session_api_key: str | None = None
    openhands_llm_api_key: str | None = None
    openhands_secret_key: str | None = None
    agent_mode: str = "openhands"
    analysis_mode: str = "browser"
    auth_mode: str = "dev"
    frontend_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    auto_spider_eager_workflow: bool = False
    queue_enabled: bool = False
    worker_concurrency: int = Field(default=1, ge=1)
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
