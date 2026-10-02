"""Durable delivery of human collection requests; never creates scheduled crawls."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from auto_spider.db.models import CollectionDispatch, ManualRun
from auto_spider.services.browser_evidence import sanitize_text

STALE_SECONDS = 90
TERMINAL = {"WAITING_REVIEW", "REVIEWED", "COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}


def now():
    return datetime.now(UTC)


def queue_collection(session, run):
    row = session.get(CollectionDispatch, run.manual_run_id)
    if row is None:
        row = CollectionDispatch(manual_run_id=run.manual_run_id)
        session.add(row)
    return row


def publish_collection(factory, run_id, publish, *, force=False):
    with factory.begin() as session:
        run = session.scalar(
            select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
        )
        dispatch = session.get(CollectionDispatch, run_id)
        if not run or not dispatch:
            return
        if run.status in TERMINAL:
            dispatch.status = "DONE"
            return
        if run.status != "QUEUED" or dispatch.owner:
            return
        if dispatch.status == "SENT" and dispatch.next_at.replace(tzinfo=UTC) > now():
            return
        if not force and dispatch.next_at.replace(tzinfo=UTC) > now():
            return
        dispatch.status = "SENT"
        dispatch.attempts += 1
        dispatch.next_at = now() + timedelta(seconds=STALE_SECONDS)
    try:
        publish(run_id)
    except Exception as exc:
        with factory.begin() as session:
            run = session.scalar(
                select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
            )
            dispatch = session.get(CollectionDispatch, run_id)
            # Delivery may have succeeded before the broker connection broke.
            if run.status == "QUEUED" and not dispatch.owner:
                dispatch.status = "PENDING"
                dispatch.last_error = sanitize_text(str(exc))[:1000]
                dispatch.next_at = now() + timedelta(seconds=30)


def claim_collection(session, run):
    dispatch = queue_collection(session, run)
    session.flush()
    if run.status != "QUEUED" or dispatch.owner:
        return None
    owner = uuid4().hex
    dispatch.owner, dispatch.status, dispatch.heartbeat_at = owner, "RUNNING", now()
    dispatch.next_at = now() + timedelta(seconds=STALE_SECONDS)
    run.status, run.started_at = "RUNNING", now()
    return owner


def check_collection(factory, run_id, owner):
    with factory.begin() as session:
        run = session.scalar(
            select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
        )
        dispatch = session.get(CollectionDispatch, run_id)
        if not dispatch or dispatch.owner != owner:
            raise RuntimeError("COLLECTION_LEASE_LOST")
        if run.status != "RUNNING":
            raise RuntimeError("COLLECTION_CANCELLED")
        dispatch.heartbeat_at = now()
        dispatch.next_at = now() + timedelta(seconds=STALE_SECONDS)


def recover_collections(factory, publish, *, limit=50):
    # Backfill requests made by the previous API, including requests whose
    # process exited after committing the run but before sending the message.
    with factory.begin() as session:
        runs = session.scalars(
            select(ManualRun)
            .where(
                ManualRun.command_profile == "admin-http-collection-v1",
                ManualRun.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
            )
            .order_by(ManualRun.id)
            .limit(limit)
        ).all()
        for run in runs:
            if session.get(CollectionDispatch, run.manual_run_id) is None:
                row = queue_collection(session, run)
                if run.status == "RUNNING":
                    # No heartbeat exists for legacy processes; allow their
                    # configured timeout to finish before treating them as lost.
                    row.owner = "legacy"
                    row.status = "RUNNING"
                    timeout = run.result_json.get("options", {}).get("timeout_seconds", 600)
                    row.heartbeat_at = run.started_at + timedelta(seconds=timeout + 120)
    with factory() as session:
        ids = list(
            session.scalars(
                select(CollectionDispatch.manual_run_id)
                .where(CollectionDispatch.status != "DONE", CollectionDispatch.next_at <= now())
                .order_by(CollectionDispatch.next_at)
                .limit(limit)
            )
        )
    for run_id in ids:
        with factory.begin() as session:
            run = session.scalar(
                select(ManualRun).where(ManualRun.manual_run_id == run_id).with_for_update()
            )
            dispatch = session.get(CollectionDispatch, run_id)
            if run.status in TERMINAL:
                dispatch.status, dispatch.owner = "DONE", None
                continue
            fresh = (
                dispatch.owner
                and dispatch.heartbeat_at
                and dispatch.heartbeat_at.replace(tzinfo=UTC)
                > now() - timedelta(seconds=STALE_SECONDS)
            )
            if fresh:
                continue
            if run.status == "CANCEL_REQUESTED":
                run.status, run.finished_at = "CANCELLED", now()
                dispatch.status, dispatch.owner = "DONE", None
                continue
            if run.status == "RUNNING":
                # Fence the dead worker. Re-execute this explicitly requested,
                # pinned run; no new manual run or autonomous schedule is made.
                recoveries = run.result_json.get("recovery_count", 0)
                if recoveries >= 3:
                    run.status, run.finished_at = "FAILED", now()
                    run.result_json = {
                        **run.result_json,
                        "error_code": "COLLECTION_WORKER_LOST",
                        "error_msg": "采集执行多次中断，请人工重新发起",
                    }
                    dispatch.status, dispatch.owner = "DONE", None
                    continue
                run.status = "QUEUED"
                run.result_json = {
                    **run.result_json,
                    "recovery_count": recoveries + 1,
                }
                dispatch.status, dispatch.owner = "PENDING", None
        publish_collection(factory, run_id, publish, force=True)
