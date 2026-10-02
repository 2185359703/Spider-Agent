from __future__ import annotations

import hashlib
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from auto_spider.config import get_settings
from auto_spider.db.models import ExecutionLease, WorkflowRun


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunBusy(RuntimeError):
    pass


class ExecutionStopped(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ExecutionContext:
    """Renew a platform lease while work runs; fence every durable side effect."""

    def __init__(self, factory: sessionmaker[Session], run_id: str, resource: str):
        self.factory = factory
        self.run_id = run_id
        self.resource_key = hashlib.sha256(resource.encode()).hexdigest()
        self.owner = uuid4().hex
        self.settings = get_settings()
        self.stopped = threading.Event()
        self.started = time.monotonic()
        self.heartbeat_error: Exception | None = None
        self.thread: threading.Thread | None = None
        self.retain_lease = False

    def __enter__(self):
        now = utcnow()
        until = now + timedelta(seconds=self.settings.execution_lease_seconds)
        with self.factory() as session:
            result = session.execute(
                update(ExecutionLease)
                .where(
                    ExecutionLease.resource_key == self.resource_key,
                    ExecutionLease.expires_at < now,
                    # Another run may still have a remote tool in flight. Only its
                    # own recovery may reclaim an expired lease.
                    ExecutionLease.run_id == self.run_id,
                )
                .values(owner=self.owner, expires_at=until)
            )
            if not result.rowcount:
                session.add(
                    ExecutionLease(
                        resource_key=self.resource_key,
                        run_id=self.run_id,
                        owner=self.owner,
                        expires_at=until,
                    )
                )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise RunBusy("PLATFORM_RUN_ACTIVE") from exc
        try:
            self.guard()
        except Exception:
            self.__exit__()
            raise
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def _heartbeat(self):
        interval = min(
            self.settings.execution_heartbeat_seconds, self.settings.execution_lease_seconds / 3
        )
        while not self.stopped.wait(interval):
            try:
                with self.factory.begin() as session:
                    result = session.execute(
                        update(ExecutionLease)
                        .where(
                            ExecutionLease.resource_key == self.resource_key,
                            ExecutionLease.owner == self.owner,
                        )
                        .values(
                            expires_at=utcnow()
                            + timedelta(seconds=self.settings.execution_lease_seconds)
                        )
                    )
                    if not result.rowcount:
                        raise ExecutionStopped("LEASE_LOST")
                    session.execute(
                        update(WorkflowRun)
                        .where(WorkflowRun.run_id == self.run_id)
                        .values(heartbeat_at=utcnow())
                    )
            except Exception as exc:
                self.heartbeat_error = exc
                return

    def guard(self):
        if self.heartbeat_error:
            raise ExecutionStopped("LEASE_LOST") from self.heartbeat_error
        with self.factory() as session:
            lease = session.get(ExecutionLease, self.resource_key)
            if lease is None or lease.owner != self.owner:
                raise ExecutionStopped("LEASE_LOST")
            run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == self.run_id))
            if run is None:
                raise ExecutionStopped("RUN_MISSING")
            if run.control in {"PAUSE", "CANCEL"}:
                raise ExecutionStopped(run.control)
        if time.monotonic() - self.started > self.settings.execution_timeout_seconds:
            raise ExecutionStopped("TIMEOUT")

    def __exit__(self, *args):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.retain_lease:
            return
        with self.factory.begin() as session:
            session.execute(
                delete(ExecutionLease).where(
                    ExecutionLease.resource_key == self.resource_key,
                    ExecutionLease.owner == self.owner,
                )
            )
