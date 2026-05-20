from celery.result import AsyncResult

from app.schemas.task import TaskStatusResponse, TaskSubmitResponse
from app.tasks.catalog import get_task_definition
from app.workers.celery_app import celery_app


class TaskQueueService:
    """Queue-facing service kept generic so concrete task implementations stay decoupled."""

    def submit(self, task_type: str, payload: dict) -> TaskSubmitResponse:
        task_definition = get_task_definition(task_type)
        async_result = celery_app.send_task(task_definition.celery_name, args=[payload])
        return TaskSubmitResponse(task_id=async_result.id, task_type=task_type, status="queued")

    def status(self, task_id: str) -> TaskStatusResponse:
        result = AsyncResult(task_id, app=celery_app)
        successful = result.successful() if result.ready() else None
        error = None
        value = None

        if result.ready():
            if result.failed():
                error = str(result.result)
            else:
                value = result.result
        else:
            value = result.info if isinstance(result.info, dict) else None

        return TaskStatusResponse(
            task_id=task_id,
            status=result.status,
            ready=result.ready(),
            successful=successful,
            result=value,
            error=error,
        )
