from __future__ import annotations

from auto_spider.workers.celery_app import celery_app
from auto_spider.workflows.runner import run_onboarding_task, run_repair_task

try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(func=None, **_kwargs):
        def decorator(inner):
            return inner

        return decorator if func is None else decorator(func)


task_decorator = celery_app.task if celery_app is not None else shared_task


@task_decorator(name="auto_spider.workers.tasks.run_onboarding")
def run_onboarding(task_id: str) -> dict:
    return run_onboarding_task(task_id)


@task_decorator(name="auto_spider.workers.tasks.run_repair")
def run_repair(task_id: str, bundle_id: str) -> dict:
    return run_repair_task(task_id, bundle_id)


@task_decorator(name="auto_spider.workers.tasks.execute_dispatch")
def execute_dispatch(dispatch_id: str):
    from auto_spider.db.session import SessionLocal
    from auto_spider.services.dispatch import execute_dispatch as execute

    execute(SessionLocal, dispatch_id)


@task_decorator(name="auto_spider.workers.tasks.recover_dispatches")
def recover_dispatches():
    from auto_spider.config import get_settings
    from auto_spider.db.session import SessionLocal
    from auto_spider.services.agent_control import reconcile_stale_runs, reconcile_stopped_runs

    if not get_settings().queue_enabled and not get_settings().auto_spider_eager_workflow:
        return
    from auto_spider.services.dispatch import dispatch_pending

    reconcile_stale_runs(SessionLocal)
    reconcile_stopped_runs(SessionLocal)
    dispatch_pending(SessionLocal, execute_dispatch.delay)
    from auto_spider.services.admin_collection import collect_candidate
    from auto_spider.services.collection_dispatch import recover_collections

    recover_collections(
        SessionLocal,
        lambda run_id: collect_candidate.apply_async(args=[run_id], queue="validation"),
    )


@task_decorator(name="auto_spider.workers.tasks.cleanup_resources")
def cleanup_resources():
    from auto_spider.db.session import SessionLocal
    from auto_spider.services.resource_cleanup import cleanup_resources as reclaim

    return reclaim(SessionLocal)


from auto_spider.services import admin_collection as _admin_collection  # noqa: E402,F401
