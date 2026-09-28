from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auto_spider.db.models import WorkflowCheckpoint


class CheckpointStore:
    """Durable application checkpoint store used around LangGraph nodes."""

    def save(
        self,
        session: Session,
        *,
        task_id: str,
        run_id: str,
        node: str,
        state: dict[str, Any],
    ) -> WorkflowCheckpoint:
        current = session.scalar(
            select(func.max(WorkflowCheckpoint.revision)).where(
                WorkflowCheckpoint.task_id == task_id,
                WorkflowCheckpoint.run_id == run_id,
            )
        )
        checkpoint = WorkflowCheckpoint(
            task_id=task_id,
            run_id=run_id,
            node=node,
            revision=(current or 0) + 1,
            state_json=state,
        )
        session.add(checkpoint)
        session.flush()
        return checkpoint

    def latest(self, session: Session, task_id: str, run_id: str) -> WorkflowCheckpoint | None:
        return session.scalar(
            select(WorkflowCheckpoint)
            .where(
                WorkflowCheckpoint.task_id == task_id,
                WorkflowCheckpoint.run_id == run_id,
            )
            .order_by(WorkflowCheckpoint.revision.desc())
            .limit(1)
        )
