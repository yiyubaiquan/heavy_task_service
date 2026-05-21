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
    task_publish_retry=False,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=settings.celery_redis_socket_connect_timeout,
    broker_transport_options={
        "socket_timeout": settings.celery_redis_socket_timeout,
        "socket_connect_timeout": settings.celery_redis_socket_connect_timeout,
        "retry_on_timeout": False,
    },
    result_backend_transport_options={
        "socket_timeout": settings.celery_redis_socket_timeout,
        "socket_connect_timeout": settings.celery_redis_socket_connect_timeout,
        "retry_on_timeout": False,
        "retry_policy": {"max_retries": 0},
    },
    redis_socket_timeout=settings.celery_redis_socket_timeout,
    redis_socket_connect_timeout=settings.celery_redis_socket_connect_timeout,
    redis_retry_on_timeout=False,
)
