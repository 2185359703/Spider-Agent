from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from auto_spider.config import get_settings
from auto_spider.git.target import CollectorRepository


@dataclass(frozen=True)
class WorktreeContext:
    path: Path
    baseline_ref: str
    created: bool
    mutation_enabled: bool


class WorktreeManager:
    def __init__(self, repository: CollectorRepository | None = None) -> None:
        self.repository = repository or CollectorRepository()
        self.root = get_settings().worktree_root.resolve()

    def prepare(
        self,
        task_id: str,
        run_id: str,
        *,
        mutation_enabled: bool = False,
    ) -> WorktreeContext:
        inspection = self.repository.inspect()
        if not inspection.is_git_repository or not inspection.baseline_available:
            raise RuntimeError("受管采集器仓库或指定基线不可用")
        path = (self.root / task_id / run_id).resolve()
        if self.root not in path.parents:
            raise ValueError("worktree 路径越界")
        if not mutation_enabled:
            return WorktreeContext(
                path=path,
                baseline_ref=inspection.baseline_ref,
                created=False,
                mutation_enabled=False,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(inspection.path),
                "worktree",
                "add",
                "--detach",
                str(path),
                inspection.baseline_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return WorktreeContext(
            path=path,
            baseline_ref=inspection.baseline_ref,
            created=True,
            mutation_enabled=True,
        )
