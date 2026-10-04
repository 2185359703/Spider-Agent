import subprocess
from pathlib import Path

import pytest

from auto_spider.config import get_settings
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


def test_legacy_repository_key_uses_configured_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "collector-catalog"
    monkeypatch.setenv("LEGACY_COLLECTOR_REPO_PATH", str(legacy))
    get_settings.cache_clear()

    target = CollectorRepository.for_repository_key("collector-catalog")

    assert target.path == legacy
    assert target.baseline_ref == "HEAD"


def test_worktree_manager_isolates_candidate_from_discovery_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    baseline = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
    ).strip()
    run_root = tmp_path / "worktrees" / "task" / "run"
    (run_root / "discovery").mkdir(parents=True)

    manager = WorktreeManager(CollectorRepository(repository, baseline))
    manager.root = (tmp_path / "worktrees").resolve()
    context = manager.prepare("task", "run", mutation_enabled=True)

    assert context.created is True
    assert context.path == run_root / "candidate"
    assert (context.path / ".git").exists()
