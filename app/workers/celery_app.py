from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "heavy_task_service",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.mineru_pdf"],
)

celery_app.conf.update(
    task_default_queue=settings.celery_task_default_queue,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)
