from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_spider.git.policy import validate_changed_files


@dataclass(frozen=True)
class CommandCheck:
    status: str
    command: list[str]
    returncode: int | None
    output: str


@dataclass(frozen=True)
class CandidateValidation:
    compile: CommandCheck
    pytest: CommandCheck
    ruff: CommandCheck
    contract_status: str
    contract_errors: list[str]
    business_status: str
    business_errors: list[str]

    @property
    def passed(self) -> bool:
        return all(
            (
                self.pytest.status == "PASS",
                self.compile.status == "PASS",
                self.ruff.status == "PASS",
                self.contract_status == "PASS",
                self.business_status == "PASS",
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def _run_command(command: list[str], worktree: Path, timeout: int = 180) -> CommandCheck:
    validation_env = os.environ.copy()
    # Candidate tests use the collector repository's deterministic local profile.
    validation_env["APP_ENV"] = "local"
    try:
        result = subprocess.run(
            command,
            cwd=worktree,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=validation_env,
        )
    except FileNotFoundError as exc:
        return CommandCheck("NOT_RUN", command, None, str(exc))
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return CommandCheck("FAIL", command, None, f"timeout: {output[-4000:]}")
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return CommandCheck(
        "PASS" if result.returncode == 0 else "FAIL",
        command,
        result.returncode,
        output[-4000:],
    )


def _safe_file(worktree: Path, relative: str) -> Path:
    root = worktree.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"VALIDATION_PATH_ESCAPE: {relative}")
    return path


def _contract_checks(worktree: Path, platform_key: str, changed_files: list[str]) -> list[str]:
    errors = [
        f"CODE_SCOPE_VIOLATION: {path}"
        for path in validate_changed_files(changed_files, platform_key)
    ]
    collector_rel = f"collectors/{platform_key}.py"
    config_rel = f"config/platforms/{platform_key}.toml"
    test_rel = f"tests/test_{platform_key}.py"
    collector = _safe_file(worktree, collector_rel)
    config = _safe_file(worktree, config_rel)
    test_file = _safe_file(worktree, test_rel)
    for path, label in ((collector, collector_rel), (config, config_rel), (test_file, test_rel)):
        if not path.exists():
            errors.append(f"MISSING_ALLOWED_FILE: {label}")
    if collector.exists():
        try:
            tree = ast.parse(collector.read_text(encoding="utf-8"))
            class_names = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
                and any(
                    isinstance(base, ast.Name) and base.id == "BaseCollector" for base in node.bases
                )
            }
            if not class_names:
                errors.append("COLLECTOR_CONTRACT: no BaseCollector subclass")
        except (OSError, SyntaxError) as exc:
            errors.append(f"COLLECTOR_CONTRACT: {exc}")
    if config.exists():
        try:
            with config.open("rb") as handle:
                document = tomllib.load(handle)
            platform = document.get("platform") or {}
            if platform.get("key") != platform_key:
                errors.append("CONFIG_CONTRACT: platform.key mismatch")
            adapter = str(platform.get("adapter") or "")
            if not adapter.startswith(f"collectors.{platform_key}:"):
                errors.append("CONFIG_CONTRACT: adapter mismatch")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"CONFIG_CONTRACT: {exc}")
    return errors


def validate_candidate(
    worktree: Path,
    platform_key: str,
    changed_files: list[str],
) -> CandidateValidation:
    collector_rel = f"collectors/{platform_key}.py"
    test_rel = f"tests/test_{platform_key}.py"
    pytest_check = _run_command(
        [sys.executable, "-m", "pytest", test_rel, "-q"],
        worktree,
    )
    compile_check = _run_command(
        [sys.executable, "-m", "compileall", "-q", collector_rel],
        worktree,
    )
    ruff_executable = shutil.which("ruff")
    ruff_check = (
        _run_command([ruff_executable, "check", collector_rel, test_rel], worktree)
        if ruff_executable
        else CommandCheck(
            "NOT_RUN",
            ["ruff", "check", collector_rel, test_rel],
            None,
            "ruff not installed",
        )
    )
    contract_errors = _contract_checks(worktree, platform_key, changed_files)
    contract_status = "PASS" if not contract_errors else "FAIL"
    business_errors: list[str] = []
    if pytest_check.status != "PASS":
        business_errors.append("BUSINESS_TESTS_NOT_PASS")
    if not _safe_file(worktree, f"tests/fixtures/{platform_key}").exists():
        business_errors.append("BUSINESS_FIXTURES_MISSING")
    business_status = "PASS" if not business_errors else "FAIL"
    return CandidateValidation(
        compile=compile_check,
        pytest=pytest_check,
        ruff=ruff_check,
        contract_status=contract_status,
        contract_errors=contract_errors,
        business_status=business_status,
        business_errors=business_errors,
    )
