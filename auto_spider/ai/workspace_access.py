"""Filesystem policy shared by Agent tools; it does not execute model commands."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from auto_spider.services.browser_evidence import sanitize_text


class WorkspaceAccess:
    def __init__(self, policy: dict):
        self.policy = policy
        self.root = Path(policy["workspace"]).resolve()

    def path(self, relative: str, *, area: str = "workspace", write: bool = False) -> Path:
        normalized = relative.replace("\\", "/")
        if area == "evidence" and self.policy.get("evidence_prefix"):
            normalized = normalized.removeprefix(self.policy["evidence_prefix"] + "/")
        if area == "failure" and self.policy.get("failure_prefix"):
            normalized = normalized.removeprefix(self.policy["failure_prefix"] + "/")
        parts = PurePosixPath(normalized).parts
        if (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or ".." in parts
            or any(p.startswith(".env") or p == ".git" for p in parts)
        ):
            raise ValueError("WORKSPACE_PATH_DENIED")
        if area not in {"workspace", "evidence", "failure"}:
            raise ValueError("WORKSPACE_AREA_DENIED")
        if area == "failure" and not self.policy.get("failure_evidence"):
            raise ValueError("FAILURE_EVIDENCE_NOT_GRANTED")
        root = (
            self.root
            if area == "workspace"
            else Path(
                self.policy["failure_evidence"] if area == "failure" else self.policy["evidence"]
            ).resolve()
        )
        path = root.joinpath(*parts)
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("WORKSPACE_PATH_ESCAPE")
        current = path
        while current != root:
            if current.is_symlink():
                raise ValueError("WORKSPACE_SYMLINK_DENIED")
            current = current.parent
        if write:
            if self.policy["mode"] != "write" or area != "workspace":
                raise ValueError("READ_ONLY_WORKSPACE")
            if not any(
                fnmatchcase(normalized, pattern) for pattern in self.policy["allowed_files"]
            ):
                raise ValueError("CODE_SCOPE_VIOLATION")
            if resolved.exists() and resolved.stat().st_nlink != 1:
                raise ValueError("WORKSPACE_HARDLINK_DENIED")
        return path

    def read(self, path: str, area: str = "workspace", offset: int = 0) -> str:
        target = self.path(path, area=area)
        if target.is_dir():
            return "\n".join(
                sorted(
                    p.name
                    for p in target.iterdir()
                    if p.name != ".git" and not p.name.startswith(".env")
                )[:500]
            )
        if target.stat().st_size > 5_000_000:
            raise ValueError("FILE_TOO_LARGE")
        return sanitize_text(target.read_text(encoding="utf-8"))[offset : offset + 30_000]

    def write(self, path: str, content: str) -> str:
        target = self.path(path, write=True)
        if sanitize_text(content) != content:
            raise ValueError("UNSANITIZED_SENSITIVE_DATA")
        if len(content.encode()) > 1_000_000:
            raise ValueError("FILE_TOO_LARGE")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return (
            f"Saved {path} ({len(content)} characters). Platform validation runs after generation."
        )


def load_policy(policy_id: str) -> dict:
    if len(policy_id) != 32 or any(c not in "0123456789abcdef" for c in policy_id):
        raise ValueError("INVALID_POLICY_ID")
    root = Path("/srv/auto_spider/agent_policies")
    return json.loads((root / f"{policy_id}.json").read_text(encoding="utf-8"))
