from pathlib import Path

import pytest

from auto_spider.git.target import CollectorRepository
from auto_spider.git.worktree import WorktreeManager


def test_collector_target_uses_committed_baseline_without_dirty_files() -> None:
    target = CollectorRepository.source_repository()
    if not target.path.exists():
        pytest.skip(f"受管采集器源码仓库不可用: {target.path}")
    context = target.candidate_context()
    assert context["is_git_repository"] is True
    assert context["baseline_available"] is True
    assert context["dirty_state_excluded"] is True
    assert context["path"] == str(target.path)
    assert context["aicoding_repository_path"] != context["path"]


def test_worktree_manager_defaults_to_read_only(tmp_path: Path) -> None:
    target = CollectorRepository()
    if not target.path.exists():
        pytest.skip(f"AI 采集代码仓库不可用: {target.path}")
    manager = WorktreeManager(target)
    manager.root = tmp_path.resolve()
    context = manager.prepare("task-test", "run-test")
    assert context.created is False
    assert context.mutation_enabled is False
    assert context.baseline_ref == target.baseline_ref
