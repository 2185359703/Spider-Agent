from __future__ import annotations

from auto_spider.workflows.runner import run_onboarding_task, run_repair_task

try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(func=None, **_kwargs):
        def decorator(inner):
            return inner

        return decorator if func is None else decorator(func)


@shared_task(name="auto_spider.workers.tasks.run_onboarding")
def run_onboarding(task_id: str) -> dict:
    return run_onboarding_task(task_id)


@shared_task(name="auto_spider.workers.tasks.run_repair")
def run_repair(task_id: str, bundle_id: str) -> dict:
    return run_repair_task(task_id, bundle_id)
