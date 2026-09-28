from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from auto_spider.config import get_settings
from auto_spider.db.models import EvidenceFile

SECRET_KEY_RE = re.compile(r"(cookie|authorization|csrf|token|password|secret|session)", re.I)


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and SECRET_KEY_RE.search(value[:80]):
        return "[REDACTED]"
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
