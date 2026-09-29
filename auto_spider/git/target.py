from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from auto_spider.config import get_settings


@dataclass(frozen=True)
class RepositoryInspection:
    path: Path
    baseline_ref: str
    head: str | None
    dirty_files: tuple[str, ...]
    is_git_repository: bool
    baseline_available: bool


class CollectorRepository:
    """Read-only inspection of the existing collector repository.

    Candidate worktrees are intentionally disabled until a real OpenHands gateway
    supplies a change set and an explicit worker policy enables mutation.
    """

    def __init__(self, path: Path | None = None, baseline_ref: str | None = None) -> None:
        settings = get_settings()
        self.path = Path(path or settings.collector_repo_path)
        self.baseline_ref = baseline_ref or settings.collector_baseline_ref

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
        return {
            "path": str(inspection.path),
            "baseline_ref": inspection.baseline_ref,
            "head": inspection.head,
            "dirty_files": list(inspection.dirty_files),
            "is_git_repository": inspection.is_git_repository,
            "baseline_available": inspection.baseline_available,
            "dirty_state_excluded": True,
            "mutation_enabled": False,
        }
