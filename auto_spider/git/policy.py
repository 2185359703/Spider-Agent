from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

ALLOWED_PATTERNS = (
    "collectors/{platform_key}.py",
    "config/platforms/{platform_key}.toml",
    "tests/fixtures/{platform_key}/*",
    "tests/test_{platform_key}.py",
)


def allowed_changed_file(path: str, platform_key: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized or ".." in PurePosixPath(normalized).parts:
        return False
    patterns = [pattern.format(platform_key=platform_key) for pattern in ALLOWED_PATTERNS]
    return any(fnmatch(normalized, pattern) for pattern in patterns)


def validate_changed_files(paths: list[str], platform_key: str) -> list[str]:
    return [path for path in paths if not allowed_changed_file(path, platform_key)]
