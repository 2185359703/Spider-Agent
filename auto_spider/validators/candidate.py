from __future__ import annotations

import ast
import inspect
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auto_spider.git.policy import validate_changed_files
from auto_spider.services.validation_activity import emit_validation, sanitize_validation_text


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
    live: CommandCheck | None = None

    @property
    def passed(self) -> bool:
        return all(
            (
                self.pytest.status == "PASS",
                self.compile.status == "PASS",
                self.ruff.status == "PASS",
                self.contract_status == "PASS",
                self.business_status == "PASS",
                self.live is None or self.live.status == "PASS",
            )
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def _run_command(
    command: list[str], worktree: Path, timeout: int = 180, guard=None
) -> CommandCheck:
    if sys.platform != "linux":
        return CommandCheck("NOT_RUN", command, None, "VALIDATION_SANDBOX_UNAVAILABLE: use Docker")
    from auto_spider.services.browser_evidence import sanitize_text

    emit_validation("开始运行验证命令", phase="start", command=command)

    with tempfile.TemporaryDirectory(prefix="auto-spider-validation-") as scratch:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "LANG", "LC_ALL", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        env.update(
            APP_ENV="local",
            HOME=scratch,
            TMPDIR=scratch,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPYCACHEPREFIX=f"{scratch}/pycache",
            XDG_CACHE_HOME=f"{scratch}/cache",
            RUFF_CACHE_DIR=f"{scratch}/ruff",
            PYTHONPATH=str(worktree),
            PYTEST_ADDOPTS="-p no:cacheprovider",
        )
        launcher = [
            sys.executable,
            str(Path(__file__).with_name("sandbox_exec.py")),
            str(worktree.resolve()),
            scratch,
            *command,
        ]
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(
                    launcher,
                    cwd=worktree,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                return CommandCheck("NOT_RUN", command, None, str(exc))
            deadline = time.monotonic() + timeout
            timed_out = False
            offsets = [0, 0]

            def stream_output():
                for index, handle in enumerate((stdout, stderr)):
                    size = os.fstat(handle.fileno()).st_size
                    if size > offsets[index]:
                        chunk = os.pread(
                            handle.fileno(), min(size - offsets[index], 8000), offsets[index]
                        )
                        offsets[index] += len(chunk)
                        emit_validation(
                            "验证命令输出",
                            phase="output",
                            command=command,
                            output=sanitize_text(chunk.decode("utf-8", errors="replace")),
                        )

            try:
                while process.poll() is None:
                    if guard:
                        guard()
                    stream_output()
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(0.2)
            finally:
                # Kill children too, including daemonized work left by a test.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

            def tail(handle):
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 4000))
                return handle.read().decode("utf-8", errors="replace")

            output = sanitize_validation_text(tail(stdout) + tail(stderr))
            if timed_out:
                emit_validation(
                    "验证命令超时", phase="result", status="FAIL", command=command, output=output
                )
                return CommandCheck("FAIL", command, None, f"timeout: {output}")
            status = (
                "NOT_RUN"
                if process.returncode == 78
                else "PASS"
                if process.returncode == 0
                else "FAIL"
            )
            emit_validation(
                "验证命令完成",
                phase="result",
                status=status,
                command=command,
                returncode=process.returncode,
                output=output[-8000:],
            )
            return CommandCheck(status, command, process.returncode, output[-8000:])


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
                    isinstance(base, ast.Name)
                    and base.id in {"BaseCollector", "MokaPlatformCollector"}
                    for base in node.bases
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
            if "platform_id" in platform:
                errors.append("CONFIG_CONTRACT: collection-stage platform_id must be omitted")
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
    *,
    live_url: str | None = None,
    expected_observation: str | None = None,
    guard=None,
) -> CandidateValidation:
    collector_rel = f"collectors/{platform_key}.py"
    test_rel = f"tests/test_{platform_key}.py"
    python_executable = os.getenv("VALIDATION_PYTHON") or sys.executable
    ruff_executable = shutil.which("ruff")
    pytest_check = _run_command(
        [python_executable, "-m", "pytest", test_rel, "-q"],
        worktree,
        guard=guard,
    )
    compile_check = _run_command(
        [python_executable, "-m", "compileall", "-q", collector_rel],
        worktree,
        guard=guard,
    )
    ruff_check = (
        _run_command(
            [
                ruff_executable,
                "check",
                "--isolated",
                "--select",
                "E,F,I,UP,B",
                "--target-version",
                "py312",
                "--line-length",
                "100",
                collector_rel,
                test_rel,
            ],
            worktree,
            guard=guard,
        )
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
    live_check: CommandCheck | None = None
    if live_url:
        from auto_spider.services.collection_quality import (
            assess_collection_quality,
            plain_text,
            published_datetime,
        )

        quality_runtime = (
            "import hashlib, html, re\nfrom datetime import UTC, datetime, timedelta\n"
            "from urllib.parse import urlsplit\nfrom zoneinfo import ZoneInfo\n"
            + "\n".join(
                inspect.getsource(fn)
                for fn in (
                    plain_text,
                    published_datetime,
                    assess_collection_quality,
                )
            )
        )
        live_script = (
            "import json, sys\n"
            "from config.platforms import get_platform\n"
            "from collectors.registry import CollectorRegistry\n"
            f"platform = get_platform({platform_key!r})\n"
            "collector = CollectorRegistry.create(\n"
            "    platform, crawl_task='live-validation', crawl_batch='live-validation',\n"
            "    crawl_version='v1.0.0', runtime_options={}\n"
            ")\n"
            "records = collector.collect()\n"
            "for record in records:\n"
            "    job = record.raw_content.get('job', {})\n"
            "    if (getattr(record, 'source_platform', None) is not None or "
            "getattr(record, 'entity_id', None) is not None or "
            "job.get('platform_id') is not None or job.get('entity_id') is not None):\n"
            "        print('COLLECTION_IDS_MUST_BE_NULL'); sys.exit(23)\n"
            "summary = {'record_count': len(records), 'fields': []}\n"
            "for record in records[:3]:\n"
            "    job = record.raw_content.get('job', {})\n"
            "    source_url = (getattr(record, 'source_url', None) or "
            "job.get('source_url') or job.get('apply_url'))\n"
            "    summary['fields'].append({\n"
            "        'source_id': record.source_id,\n"
            "        'title': job.get('title'),\n"
            "        'source_url': source_url,\n"
            "        'body': bool(job.get('description') or job.get('requirements')),\n"
            "    })\n"
            "if any(\n"
            "    not item['source_id'] or not item['title'] or "
            "not item['source_url'] or not item['body']\n"
            "    for item in summary['fields']\n"
            "): sys.exit(22)\n"
            "items = [r.to_dict() if hasattr(r, 'to_dict') else r for r in records]\n"
            "quality = assess_collection_quality(items, "
            "pagination=getattr(collector, 'collection_diagnostics', {}))\n"
            "summary['quality'] = quality\n"
            "print(json.dumps(summary, ensure_ascii=False))\n"
            "if quality['metrics']['error_count']: sys.exit(24)\n"
            + (
                "\nif not records: sys.exit(21)\n"
                if expected_observation == "INTERNSHIPS_FOUND"
                else ""
            )
        )
        live_check = _run_command(
            [python_executable, "-c", quality_runtime + "\n" + live_script],
            worktree,
            timeout=240,
            guard=guard,
        )
    return CandidateValidation(
        compile=compile_check,
        pytest=pytest_check,
        ruff=ruff_check,
        contract_status=contract_status,
        contract_errors=contract_errors,
        business_status=business_status,
        business_errors=business_errors,
        live=live_check,
    )
