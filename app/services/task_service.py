from celery.result import AsyncResult
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError

from app.schemas.task import TaskStatusResponse, TaskSubmitResponse
from app.tasks.catalog import get_task_definition
from app.workers.celery_app import celery_app


class TaskQueueUnavailableError(RuntimeError):
    """Raised when the Redis-backed Celery queue cannot be reached quickly."""


def _raise_queue_unavailable(exc: BaseException) -> None:
    if isinstance(exc, (KombuOperationalError, RedisError, TimeoutError)):
        raise TaskQueueUnavailableError("task queue is unavailable") from exc

    if isinstance(exc, RuntimeError) and "Celery result store" in str(exc):
        raise TaskQueueUnavailableError("task queue is unavailable") from exc

    raise exc


class TaskQueueService:
    """Queue-facing service kept generic so concrete task implementations stay decoupled."""

    def submit(self, task_type: str, payload: dict) -> TaskSubmitResponse:
        task_definition = get_task_definition(task_type)
        try:
            async_result = celery_app.send_task(task_definition.celery_name, args=[payload])
        except (KombuOperationalError, RedisError, TimeoutError, RuntimeError) as exc:
            _raise_queue_unavailable(exc)
        return TaskSubmitResponse(task_id=async_result.id, task_type=task_type, status="queued")

    def status(self, task_id: str) -> TaskStatusResponse:
        try:
            result = AsyncResult(task_id, app=celery_app)
            ready = result.ready()
            status = result.status
            successful = result.successful() if ready else None
        except (KombuOperationalError, RedisError, TimeoutError, RuntimeError) as exc:
            _raise_queue_unavailable(exc)

        error = None
        value = None

        try:
            if ready:
                if result.failed():
                    error = str(result.result)
                else:
                    value = result.result
            else:
                value = result.info if isinstance(result.info, dict) else None
        except (KombuOperationalError, RedisError, TimeoutError, RuntimeError) as exc:
            _raise_queue_unavailable(exc)

        return TaskStatusResponse(
            task_id=task_id,
            status=status,
            ready=ready,
            successful=successful,
            result=value,
            error=error,
        )
