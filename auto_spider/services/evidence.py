from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from auto_spider.config import get_settings
from auto_spider.db.models import AgentExecution, EvidenceFile
from auto_spider.services.browser_evidence import sanitize_text

SECRET_KEY_RE = re.compile(
    r"(cookie|authorization|csrf|token|password|secret|session|api_key)", re.I
)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


class EvidenceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().evidence_root

    def write_json(
        self,
        session: Session,
        *,
        task_id: str,
        run_id: str,
        name: str,
        payload: Any,
        file_type: str,
    ) -> EvidenceFile:
        relative = Path(task_id) / run_id / name
        target = (self.root / relative).resolve()
        root = self.root.resolve()
        if root not in target.parents:
            raise ValueError("证据路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(sanitize(payload), ensure_ascii=False, indent=2).encode("utf-8")
        target.write_bytes(content)
        evidence = EvidenceFile(
            evidence_id=hashlib.sha1(f"{task_id}:{run_id}:{relative}".encode()).hexdigest()[:32],
            task_id=task_id,
            run_id=run_id,
            relative_path=relative.as_posix(),
            file_type=file_type,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            redaction_status="redacted",
        )
        session.add(evidence)
        session.flush()
        return evidence

    def register_browser_files(self, session: Session, *, task_id: str, run_id: str) -> None:
        """Index already-sanitized CLI captures without overwriting their contents."""
        root = self.root.resolve()
        task_root = root / task_id
        if not task_root.is_dir() or task_root.is_symlink() or root not in task_root.parents:
            return
        ids = session.scalars(
            select(AgentExecution.execution_id).where(
                AgentExecution.task_id == task_id, AgentExecution.run_id == run_id
            )
        ).all()
        capture_folders = {
            folder
            for execution_id in ids
            for folder in task_root.glob(f"**/agent-browser/{execution_id}")
        }
        # The bounded Python Playwright fallback writes ``browser/`` directly
        # under a run.  Index the same redacted JSON/text/HTML artifacts so
        # selectors emitted by the fallback remain downloadable evidence too.
        capture_folders.update(folder for folder in task_root.glob("**/browser") if folder.is_dir())
        for folder in capture_folders:
            if folder.is_symlink() or task_root not in folder.resolve().parents:
                continue
            for file in folder.iterdir():
                if file.suffix.lower() not in {".json", ".txt", ".html"}:
                    continue
                if file.is_symlink() or file.stat().st_size > get_settings().max_evidence_bytes:
                    continue
                relative = file.relative_to(root).as_posix()
                evidence_id = hashlib.sha1(f"{task_id}:{run_id}:{relative}".encode()).hexdigest()[
                    :32
                ]
                if session.scalar(
                    select(EvidenceFile.id).where(EvidenceFile.evidence_id == evidence_id)
                ):
                    continue
                content = file.read_bytes()
                session.add(
                    EvidenceFile(
                        evidence_id=evidence_id,
                        task_id=task_id,
                        run_id=run_id,
                        relative_path=relative,
                        file_type="browser_cli",
                        sha256=hashlib.sha256(content).hexdigest(),
                        size_bytes=len(content),
                        redaction_status="redacted",
                    )
                )
        session.flush()
