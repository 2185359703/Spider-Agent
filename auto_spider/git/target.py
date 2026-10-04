from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from auto_spider.config import get_settings

LEGACY_REPOSITORY_KEYS = frozenset(
    {"collector-catalog", "collector-control", "collector_control"}
)


@dataclass(frozen=True)
class RepositoryInspection:
    path: Path
    baseline_ref: str
    head: str | None
    dirty_files: tuple[str, ...]
    is_git_repository: bool
    baseline_available: bool


class CollectorRepository:
    """Inspection of the AI output repository used for candidate worktrees.

    The source repository is kept separate and is exposed through
    ``source_repository`` for read-only comparison and evidence.
    """

    def __init__(self, path: Path | None = None, baseline_ref: str | None = None) -> None:
        settings = get_settings()
        self.path = Path(
            path
            or settings.aicoding_repo_path
            or settings.collector_repo_path
            or settings.collector_source_repo_path
        )
        self.baseline_ref = baseline_ref or settings.aicoding_baseline_ref

    @classmethod
    def for_repository_key(
        cls, repository_key: str | None, *, baseline_ref: str | None = None
    ) -> CollectorRepository:
        """Resolve the repository that owns a task's recorded commit.

        New tasks use the AI output repository. Older records created before
        the repository split use ``collector-catalog`` and keep their commits
        in the managed legacy checkout.
        """
        settings = get_settings()
        key = (repository_key or "aicoding-auto_spider").strip().lower()
        if cls.is_legacy_repository_key(key):
            path = settings.legacy_collector_repo_path or (
                settings.worktree_root / "collector-control"
            )
            return cls(path=path, baseline_ref=baseline_ref or "HEAD")
        return cls(baseline_ref=baseline_ref)

    @staticmethod
    def is_legacy_repository_key(repository_key: str | None) -> bool:
        return (repository_key or "").strip().lower() in LEGACY_REPOSITORY_KEYS

    @classmethod
    def source_repository(cls) -> CollectorRepository:
        settings = get_settings()
        return cls(
            path=settings.collector_source_repo_path,
            baseline_ref=settings.collector_source_baseline_ref,
        )

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "git command failed")
        return result.stdout.strip()

    def inspect(self) -> RepositoryInspection:
        if not (self.path / ".git").exists():
            return RepositoryInspection(
                path=self.path,
                baseline_ref=self.baseline_ref,
                head=None,
                dirty_files=(),
                is_git_repository=False,
                baseline_available=False,
            )
        head = self._git("rev-parse", "HEAD")
        status_result = subprocess.run(
            ["git", "-C", str(self.path), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if status_result.returncode:
            raise RuntimeError(status_result.stderr.strip() or "git status failed")
        status = status_result.stdout
        dirty = tuple(
            line[3:].strip() for line in status.splitlines() if len(line) >= 4 and line[3:].strip()
        )
        baseline_check = subprocess.run(
            ["git", "-C", str(self.path), "rev-parse", "--verify", self.baseline_ref],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return RepositoryInspection(
            path=self.path,
            baseline_ref=self.baseline_ref,
            head=head,
            dirty_files=dirty,
            is_git_repository=True,
            baseline_available=baseline_check.returncode == 0,
        )

    def candidate_context(self) -> dict[str, object]:
        inspection = self.inspect()
        settings = get_settings()
        return {
            "path": str(inspection.path),
            "source_repository_path": str(settings.collector_source_repo_path),
            "aicoding_repository_path": str(settings.aicoding_repo_path),
            "aicoding_remote_url": settings.aicoding_remote_url,
            "baseline_ref": inspection.baseline_ref,
            "head": inspection.head,
            "dirty_files": list(inspection.dirty_files),
            "is_git_repository": inspection.is_git_repository,
            "baseline_available": inspection.baseline_available,
            "dirty_state_excluded": True,
            "mutation_enabled": False,
        }
