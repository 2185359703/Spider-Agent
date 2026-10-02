"""Task-scoped Playwright CLI execution. No model-controlled shell or server-side JS."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.validation_activity import sanitize_validation_text

try:
    import fcntl
except ImportError:  # host-side tests; production browser execution is Linux
    fcntl = None

COMMANDS = {
    "open",
    "goto",
    "snapshot",
    "click",
    "select",
    "fill",
    "press",
    "requests",
    "request",
    "request-headers",
    "request-body",
    "response-headers",
    "response-body",
    "console",
    "screenshot",
    "close",
}


def runtime_root():
    return Path(
        os.getenv(
            "COLLECTOR_BROWSER_RUNTIME_ROOT", "/var/lib/auto-spider/openhands/browser-runtime"
        )
    ).resolve()


def validate_id(value):
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("INVALID_BROWSER_EXECUTION_ID")
    return value


def public_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("BROWSER_URL_DENIED")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("BROWSER_PRIVATE_NETWORK_DENIED")
    return url


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(r"cookie|authorization|csrf|token|password|secret", str(key), re.I)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return sanitize_validation_text(value) if isinstance(value, str) else value


class BrowserCLI:
    def __init__(self, execution_id, policy=None, root=None, runner=None):
        self.execution_id = validate_id(execution_id)
        self.policy = policy or {}
        self.resource_id = validate_id(self.policy.get("browser_group", self.execution_id))
        self.root = Path(root or runtime_root()).resolve()
        self.folder = self.root / self.resource_id
        self.runner = runner
        self.session = f"collector-batch-{self.resource_id}"

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{self.resource_id}.lock"
        if path.is_symlink() or self.folder.is_symlink():
            raise ValueError("BROWSER_RESOURCE_PATH_DENIED")
        with path.open("a") as handle:
            if fcntl:
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def receipt(self):
        path = self.folder / "resource.json"
        return json.loads(path.read_text("utf-8")) if path.is_file() else {}

    def save(self, data):
        (self.folder / "resource.json").write_text(json.dumps(data), "utf-8")

    def environment(self):
        return {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.folder / "home"),
            "XDG_CACHE_HOME": str(self.folder / "cache"),
            "TMPDIR": str(self.folder / "tmp"),
            "PLAYWRIGHT_BROWSERS_PATH": os.getenv(
                "PLAYWRIGHT_BROWSERS_PATH", "/opt/playwright-browsers"
            ),
            "PLAYWRIGHT_CLI_SESSION": self.session,
            "LANG": "C.UTF-8",
            "PWTEST_SOCKETS_DIR": f"/tmp/cb-{self.resource_id[:16]}",
        }

    def _run(self, arguments):
        command = [
            os.getenv("COLLECTOR_PLAYWRIGHT_CLI", "/usr/local/bin/playwright-cli"),
            f"-s={self.session}",
            *arguments,
        ]
        if self.runner:
            return self.runner(command, self.folder, self.environment())
        with tempfile_output(self.folder) as output:
            process = subprocess.Popen(
                command,
                cwd=self.folder,
                env=self.environment(),
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
            try:
                process.wait(timeout=50)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError("BROWSER_COMMAND_TIMED_OUT") from None
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 100_000))
            text = output.read().decode("utf-8", errors="replace")
            if process.returncode:
                raise RuntimeError(sanitize_text(text)[-3000:])
            return text

    def execute(self, action, *, url=None, ref=None, value=None, index=None, static=False):
        if action == "close" and self.policy.get("browser_group"):
            return self.release()
        if action not in COMMANDS:
            raise ValueError("BROWSER_COMMAND_DENIED")
        if action in {"open", "goto"}:
            if not url:
                raise ValueError("BROWSER_URL_REQUIRED")
            public_url(url)
        if action in {"click", "select", "fill"} and not re.fullmatch(
            r"[a-z]*\d+e\d+|e\d+", ref or ""
        ):
            raise ValueError("BROWSER_SNAPSHOT_REF_REQUIRED")
        if action in {
            "request",
            "request-headers",
            "request-body",
            "response-headers",
            "response-body",
        } and (not isinstance(index, int) or index < 1):
            raise ValueError("BROWSER_REQUEST_INDEX_REQUIRED")
        if value and (len(value) > 2000 or value.startswith("--") or "\x00" in value):
            raise ValueError("BROWSER_VALUE_DENIED")
        with self.lock():
            self.folder.mkdir(parents=True, exist_ok=True)
            for name in ("home", "cache", "tmp"):
                (self.folder / name).mkdir(exist_ok=True)
            receipt = self.receipt()
            if receipt.get("owner") not in (None, self.execution_id):
                raise ValueError("BROWSER_BATCH_BUSY: 同批次其他公司正在取证，请等待该公司释放页面")
            if receipt.get("closed"):
                raise ValueError("BROWSER_EXECUTION_ALREADY_RELEASED")
            if action not in {"open", "close"} and receipt.get("owner") != self.execution_id:
                raise ValueError("BROWSER_OPEN_REQUIRED")
            sequence = receipt.get("commands", 0) + 1
            owner_commands = (
                receipt.get("owner_commands", 0) if receipt.get("owner") == self.execution_id else 0
            ) + 1
            if owner_commands > 100 and action != "close":
                raise ValueError("BROWSER_ACTION_LIMIT_REACHED")
            config = self.folder / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "browser": {
                            "browserName": "chromium",
                            "isolated": True,
                            "launchOptions": {
                                "headless": True,
                                "channel": "",
                                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                            },
                        },
                        "timeouts": {"navigation": 30000, "action": 10000},
                        "outputDir": str(self.folder / "output"),
                    }
                ),
                "utf-8",
            )
            args = [action]
            if action == "open":
                args = ["goto" if receipt.get("page_open") else "tab-new", url]
            elif action == "goto":
                args += [url]
            elif action in {"click", "select", "fill"}:
                args += [ref]
                if action != "click":
                    args += [value or ""]
            elif action == "press":
                if value not in {
                    "Enter",
                    "Tab",
                    "Escape",
                    "ArrowDown",
                    "ArrowUp",
                    "PageDown",
                    "PageUp",
                    "End",
                    "Home",
                }:
                    raise ValueError("BROWSER_KEY_DENIED")
                args += [value]
            elif index is not None:
                args += [str(index)]
            elif action == "requests" and static:
                args += ["--static"]
            receipt.update(
                commands=sequence,
                owner_commands=owner_commands,
                busy=True,
                touched=time.time(),
                task_id=self.policy.get("task_id"),
                owner=self.execution_id,
            )
            self.save(receipt)
            try:
                if action == "open" and not receipt.get("opened"):
                    self._run(["open", "about:blank", f"--config={config}", "--idle-timeout=0"])
                    receipt["opened"] = True
                    receipt["browser_launches"] = receipt.get("browser_launches", 0) + 1
                    self.save(receipt)
                if action == "open":
                    # Navigation may fail after tab creation; release still needs to close it.
                    receipt["page_open"] = True
                output = self._run(args)
                safe = sanitize_validation_text(output)
                if action in {"open", "goto", "click", "fill", "select", "press", "snapshot"}:
                    if re.search(r"^### Error\s*$", safe, re.M):
                        raise RuntimeError(safe)
                artifact = self.capture(sequence, action, safe)
                return {
                    "session": self.session,
                    "action": action,
                    "evidence_ref": artifact,
                    "evidence_read_path": (
                        artifact.removeprefix(self.policy.get("evidence_prefix", "") + "/")
                        if artifact
                        else None
                    ),
                    "output": safe[:25_000],
                    "note": "真实浏览器取证；正式采集仍使用 Python HTTP",
                }
            except (OSError, RuntimeError) as exc:
                safe = sanitize_validation_text(str(exc))[:25_000]
                session_lost = any(
                    term in safe.lower() for term in ("not running", "not open", "no browser")
                )
                if session_lost:
                    receipt.update(opened=False, page_open=False)
                artifact = self.capture(sequence, action, safe)
                stale = bool(
                    re.search(r"Ref \S+ not found in the current page snapshot", safe, re.I)
                )
                recovery = None
                if stale and action in {"click", "fill", "select"}:
                    recovery = {"action": "snapshot", "status": "budget_exhausted"}
                    if receipt["owner_commands"] < 100:
                        receipt["commands"] += 1
                        receipt["owner_commands"] += 1
                        self.save(receipt)
                        try:
                            snapshot = sanitize_validation_text(self._run(["snapshot"]))
                            if re.search(r"^### Error\s*$", snapshot, re.M):
                                raise RuntimeError(snapshot)
                            snapshot_ref = self.capture(receipt["commands"], "snapshot", snapshot)
                            recovery.update(
                                status="completed",
                                evidence_ref=snapshot_ref,
                                evidence_read_path=snapshot_ref.removeprefix(
                                    self.policy.get("evidence_prefix", "") + "/"
                                )
                                if snapshot_ref
                                else None,
                                output=snapshot[:12_000],
                            )
                        except (OSError, RuntimeError) as refresh_error:
                            recovery.update(
                                status="failed",
                                error=sanitize_validation_text(str(refresh_error))[:2000],
                            )
                return {
                    "session": self.session,
                    "action": action,
                    "error": True,
                    "session_lost": session_lost,
                    "error_code": "BROWSER_STALE_REF"
                    if stale
                    else ("BROWSER_SESSION_LOST" if session_lost else "BROWSER_COMMAND_FAILED"),
                    "recovery": recovery,
                    "evidence_ref": artifact,
                    "evidence_read_path": (
                        artifact.removeprefix(self.policy.get("evidence_prefix", "") + "/")
                        if artifact
                        else None
                    ),
                    "output": safe,
                    "note": (
                        "本次操作未执行。使用 recovery 的新快照重新选择 ref；"
                        "目标消失时先检查筛选结果，不得猜测引用或盲目重复点击。"
                        if stale
                        else "会话失效时重新 open 并 snapshot；其他失败先核对原始错误。"
                    ),
                }
            finally:
                receipt.update(busy=False, touched=time.time(), closed=action == "close")
                self.save(receipt)

    def capture(self, sequence, action, output):
        if not self.policy.get("browser_evidence"):
            return None
        root = Path(self.policy["evidence"]).resolve()
        target = Path(self.policy["browser_evidence"]).resolve()
        if root not in target.parents or target.is_symlink():
            raise ValueError("BROWSER_EVIDENCE_PATH_DENIED")
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "action": action,
            "session": self.session,
            "output": output,
            "browser_launches": self.receipt().get("browser_launches", 0),
        }
        references = re.findall(r"\]\(([^\n)]+)\)", output)
        for reference in references[:4]:
            file = (self.folder / reference).resolve()
            if (
                self.folder not in file.parents
                or not file.is_file()
                or file.stat().st_size > 2_000_000
            ):
                continue
            if file.suffix in {".txt", ".json", ".yml", ".yaml", ".html"}:
                raw = file.read_text("utf-8", errors="replace")
                try:
                    payload["response"] = redact(json.loads(raw))
                except ValueError:
                    payload["response"] = sanitize_text(raw)
        file = target / f"{sequence:04d}-{action}.json"
        file.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2), "utf-8")
        relative = file.relative_to(root).as_posix()
        return (
            f"{self.policy['evidence_prefix']}/{relative}"
            if self.policy.get("evidence_prefix")
            else relative
        )

    def close(self):
        with self.lock():
            if not self.folder.exists():
                return {"released": True, "bytes": 0}
            receipt = self.receipt()
            if receipt.get("busy") or receipt.get("owner"):
                return {"released": False, "reason": "company_active"}
            if not receipt.get("closed"):
                try:
                    self._run(["close"])
                except RuntimeError as exc:
                    if not any(
                        term in str(exc).lower()
                        for term in ("not open", "not found", "no browser", "not running")
                    ):
                        raise
            total = sum(
                p.stat().st_size
                for p in self.folder.rglob("*")
                if p.is_file() and not p.is_symlink()
            )
            # Only delete the checked, task-specific runtime directory.
            checked = self.folder.resolve()
            if (
                checked.parent != self.root
                or checked.name != self.resource_id
                or self.folder.is_symlink()
            ):
                raise ValueError("BROWSER_CLEANUP_PATH_DENIED")
            shutil.rmtree(checked)
            # Socket paths are deliberately short; reclaim only this batch's socket directory.
            if os.name != "nt":
                sockets = Path(self.environment()["PWTEST_SOCKETS_DIR"])
                checked_sockets = sockets.resolve()
                if (
                    sockets.exists()
                    and not sockets.is_symlink()
                    and checked_sockets.parent == Path("/tmp").resolve()
                    and checked_sockets.name == f"cb-{self.resource_id[:16]}"
                ):
                    shutil.rmtree(checked_sockets)
            return {"released": True, "bytes": total}

    def release(self):
        with self.lock():
            if not self.folder.exists():
                return {"released": True, "browser_reused": True}
            receipt = self.receipt()
            if receipt.get("owner") not in (None, self.execution_id):
                return {"released": False, "reason": "other_company_active"}
            if receipt.get("owner") and receipt.get("page_open"):
                self._run(["tab-close"])
            receipt.update(owner=None, busy=False, page_open=False, touched=time.time())
            self.save(receipt)
            return {"released": True, "browser_reused": True, "session": self.session}


@contextmanager
def tempfile_output(folder):
    import tempfile

    with tempfile.TemporaryFile(dir=folder) as output:
        yield output
