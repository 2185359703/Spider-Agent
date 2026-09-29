from __future__ import annotations

import subprocess
from pathlib import Path

from auto_spider.workflows.openhands_coder import OpenHandsCodingGateway


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_changed_files_includes_untracked_allowed_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "--quiet")
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(
        tmp_path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    collector = tmp_path / "collectors"
    collector.mkdir()
    (collector / "example.py").write_text("class Example:\n    pass\n", encoding="utf-8")

    assert OpenHandsCodingGateway._changed_files(tmp_path) == ["collectors/example.py"]
