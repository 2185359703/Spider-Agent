"""Synchronous LangGraph saver backed by the platform's MySQL/SQLite database."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Iterator, Sequence
from typing import Any

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from auto_spider.db.models import GraphCheckpoint, GraphWrite


def _key(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class SQLCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, factory: sessionmaker[Session]):
        super().__init__()
        self.factory = factory

    def _pack(self, value: Any) -> dict:
        kind, data = self.serde.dumps_typed(value)
        return {"type": kind, "data": base64.b64encode(data).decode("ascii")}

    def _unpack(self, value: dict) -> Any:
        return self.serde.loads_typed((value["type"], base64.b64decode(value["data"])))

    @staticmethod
    def _config(row: GraphCheckpoint, checkpoint_id: str | None = None) -> dict:
        return {
            "configurable": {
                "thread_id": row.thread_id,
                "checkpoint_ns": row.namespace,
                "checkpoint_id": checkpoint_id or row.checkpoint_id,
            }
        }

    def _tuple(self, session: Session, row: GraphCheckpoint) -> CheckpointTuple:
        writes = session.scalars(
            select(GraphWrite)
            .where(GraphWrite.checkpoint_key == row.checkpoint_key)
            .order_by(GraphWrite.graph_task_id, GraphWrite.index)
        ).all()
        return CheckpointTuple(
            config=self._config(row),
            checkpoint=self._unpack(row.payload),
            metadata=row.metadata_json,
            parent_config=self._config(row, row.parent_id) if row.parent_id else None,
            pending_writes=[(w.graph_task_id, w.channel, self._unpack(w.payload)) for w in writes],
        )

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        c = config["configurable"]
        query = select(GraphCheckpoint).where(
            GraphCheckpoint.thread_id == c["thread_id"],
            GraphCheckpoint.namespace == c.get("checkpoint_ns", ""),
        )
        if c.get("checkpoint_id"):
            query = query.where(GraphCheckpoint.checkpoint_id == c["checkpoint_id"])
        with self.factory() as session:
            row = session.scalar(query.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1))
            return self._tuple(session, row) if row else None

    def list(self, config=None, *, filter=None, before=None, limit=None) -> Iterator:
        query = select(GraphCheckpoint)
        if config:
            c = config["configurable"]
            query = query.where(GraphCheckpoint.thread_id == c["thread_id"])
            if "checkpoint_ns" in c:
                query = query.where(GraphCheckpoint.namespace == c["checkpoint_ns"])
        if before:
            query = query.where(
                GraphCheckpoint.checkpoint_id < before["configurable"]["checkpoint_id"]
            )
        with self.factory() as session:
            count = 0
            for row in session.scalars(query.order_by(GraphCheckpoint.checkpoint_id.desc())):
                if filter and any(row.metadata_json.get(k) != v for k, v in filter.items()):
                    continue
                if limit is not None and count >= limit:
                    break
                yield self._tuple(session, row)
                count += 1

    def put(self, config, checkpoint, metadata, new_versions):
        c = config["configurable"]
        row = GraphCheckpoint(
            checkpoint_key=_key(c["thread_id"], c.get("checkpoint_ns", ""), checkpoint["id"]),
            thread_id=c["thread_id"],
            namespace=c.get("checkpoint_ns", ""),
            checkpoint_id=checkpoint["id"],
            parent_id=c.get("checkpoint_id"),
            payload=self._pack(checkpoint),
            metadata_json=metadata,
        )
        with self.factory.begin() as session:
            session.merge(row)
        return self._config(row)

    def put_writes(self, config, writes: Sequence, task_id, task_path=""):
        c = config["configurable"]
        ck = _key(c["thread_id"], c.get("checkpoint_ns", ""), c["checkpoint_id"])
        with self.factory.begin() as session:
            for index, (channel, value) in enumerate(writes):
                index = WRITES_IDX_MAP.get(channel, index)
                wk = _key(ck, task_id, index)
                existing = session.get(GraphWrite, wk)
                if existing and index >= 0:
                    continue
                session.merge(
                    GraphWrite(
                        write_key=wk,
                        checkpoint_key=ck,
                        thread_id=c["thread_id"],
                        graph_task_id=task_id,
                        index=index,
                        channel=channel,
                        payload=self._pack(value),
                    )
                )

    def delete_thread(self, thread_id):
        with self.factory.begin() as session:
            session.execute(delete(GraphWrite).where(GraphWrite.thread_id == thread_id))
            session.execute(delete(GraphCheckpoint).where(GraphCheckpoint.thread_id == thread_id))
