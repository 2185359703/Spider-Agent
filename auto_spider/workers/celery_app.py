from __future__ import annotations

from auto_spider.config import get_settings

try:
    from celery import Celery
except ImportError:  # pragma: no cover - local dependency fallback
    Celery = None  # type: ignore[assignment,misc]


settings = get_settings()
celery_app = (
    Celery("auto_spider", broker=settings.redis_url, backend=settings.redis_url) if Celery else None
)
if celery_app:
    celery_app.conf.update(
        task_default_queue="analysis",
        task_routes={
            "auto_spider.workers.tasks.run_onboarding": {"queue": "analysis"},
            "auto_spider.workers.tasks.run_repair": {"queue": "coding"},
        },
        task_acks_late=True,
        broker_transport_options={"visibility_timeout": 3600},
        result_backend_transport_options={"visibility_timeout": 3600},
    )
