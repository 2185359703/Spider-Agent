from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auto_spider.db.models import WorkflowLog
from auto_spider.db.session import SessionLocal
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.evidence import sanitize


def append_log(
    session: Session,
    *,
    task_id: str,
    run_id: str | None,
    stage: str,
    message: str,
    level: str = "INFO",
    detail: dict[str, Any] | None = None,
) -> WorkflowLog:
    current = session.scalar(
        select(func.max(WorkflowLog.sequence)).where(WorkflowLog.task_id == task_id)
    )
    row = WorkflowLog(
        log_id=uuid4().hex,
        task_id=task_id,
        run_id=run_id,
        sequence=(current or 0) + 1,
        stage=stage,
        level=level,
        message=sanitize_text(message),
        detail_json=sanitize(detail or {}),
    )
    session.add(row)
    session.flush()
    return row


def append_durable_log(
    *,
    task_id: str,
    run_id: str | None,
    stage: str,
    message: str,
    level: str = "INFO",
    detail: dict[str, Any] | None = None,
) -> None:
    for attempt in range(5):
        session = SessionLocal()
        try:
            append_log(
                session,
                task_id=task_id,
                run_id=run_id,
                stage=stage,
                message=message,
                level=level,
                detail=detail,
            )
            session.commit()
            return
        except IntegrityError:
            session.rollback()
            if attempt < 4:
                time.sleep(0.01 * (attempt + 1))
        except Exception:
            session.rollback()
            return
        finally:
            session.close()
