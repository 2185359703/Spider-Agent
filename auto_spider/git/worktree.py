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
        ref: str | None = None,
    ) -> WorktreeContext:
        inspection = self.repository.inspect()
        if not inspection.is_git_repository or not inspection.baseline_available:
            raise RuntimeError("受管采集器仓库或指定基线不可用")
        baseline_ref = ref or inspection.baseline_ref
        verified_ref = subprocess.run(
            ["git", "-C", str(inspection.path), "rev-parse", "--verify", baseline_ref],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if verified_ref.returncode:
            raise RuntimeError(f"WORKTREE_REF_NOT_FOUND: {baseline_ref}")
        path = (self.root / task_id / run_id).resolve()
        if self.root not in path.parents:
            raise ValueError("worktree 路径越界")
        if not mutation_enabled:
            return WorktreeContext(
                path=path,
                baseline_ref=baseline_ref,
                created=False,
                mutation_enabled=False,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():

            def read_git(*args):
                return subprocess.run(
                    ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
                ).stdout.strip()

            common = Path(read_git("rev-parse", "--path-format=absolute", "--git-common-dir"))
            expected = (inspection.path / ".git").resolve()
            if (
                common.resolve() != expected
                or read_git("rev-parse", "HEAD") != verified_ref.stdout.strip()
            ):
                raise RuntimeError("WORKTREE_RESUME_MISMATCH")
            return WorktreeContext(path, verified_ref.stdout.strip(), False, True)
        subprocess.run(
            [
                "git",
                "-C",
                str(inspection.path),
                "worktree",
                "add",
                "--detach",
                str(path),
                baseline_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return WorktreeContext(
            path=path,
            baseline_ref=verified_ref.stdout.strip(),
            created=True,
            mutation_enabled=True,
        )
