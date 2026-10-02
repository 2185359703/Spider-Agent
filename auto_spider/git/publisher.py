from __future__ import annotations

import os
import subprocess
from pathlib import Path

from auto_spider.git.policy import validate_changed_files
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.workflows.openhands_coder import OpenHandsCodingGateway


def publish_candidate(
    workspace: str,
    *,
    platform_key: str,
    run_id: str,
    baseline: str,
    commit_time: str,
    repair: bool,
    guard,
    manual: bool = False,
) -> dict:
    """A deterministic commit/tree and local ref survive a crash before the DB write."""
    root = Path(workspace).resolve()

    def git(*args, input=None, env=None, check=True):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=check,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    guard()
    head = git("rev-parse", "HEAD").stdout.strip()
    if head != baseline:
        raise RuntimeError("UNEXPECTED_WORKTREE_HEAD")
    changed = OpenHandsCodingGateway._changed_files(root)
    if not changed or validate_changed_files(changed, platform_key):
        raise RuntimeError("INVALID_CANDIDATE_SCOPE")
    for relative in changed:
        path = root / relative
        if root not in path.resolve().parents or path.is_symlink():
            raise RuntimeError("CANDIDATE_PATH_ESCAPE")
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if sanitize_text(content) != content:
                raise RuntimeError("UNSANITIZED_SENSITIVE_DATA")
    git("add", "--", *changed)
    git("diff", "--cached", "--check")
    tree = git("write-tree").stdout.strip()
    branch = f"codex/candidate/{platform_key}/{run_id}"
    message = (
        f"{'fix' if repair or manual else 'feat'}(collectors): "
        f"{'人工修改' if manual else '修复' if repair else '接入'} {platform_key} 招聘采集\n\n"
        f"Auto-Spider-Run: {run_id}\n"
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Auto Spider",
        "GIT_AUTHOR_EMAIL": "auto-spider@example.invalid",
        "GIT_COMMITTER_NAME": "Auto Spider",
        "GIT_COMMITTER_EMAIL": "auto-spider@example.invalid",
        "GIT_AUTHOR_DATE": commit_time,
        "GIT_COMMITTER_DATE": commit_time,
    }
    guard()
    sha = git("commit-tree", tree, "-p", baseline, input=message, env=env).stdout.strip()
    existing = git("rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    if existing.returncode == 0 and existing.stdout.strip() != sha:
        raise RuntimeError("CANDIDATE_COMMIT_CONFLICT")
    guard()
    git(
        "update-ref",
        f"refs/heads/{branch}",
        sha,
        existing.stdout.strip() if existing.returncode == 0 else "0" * 40,
    )
    return {
        "commit_sha": sha,
        "branch_name": branch,
        "baseline_ref": baseline,
        "changed_files": changed,
        "push_status": "DISABLED",
        "simulated": False,
    }
