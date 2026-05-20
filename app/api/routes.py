import base64

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.task import HealthResponse, TaskStatusResponse, TaskSubmitRequest, TaskSubmitResponse
from app.services.task_service import TaskQueueService
from app.tasks.catalog import TASK_DEFINITIONS

router = APIRouter()
task_queue = TaskQueueService()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="heavy-task-service")


@router.get("/tasks")
def list_tasks() -> dict:
    return {"tasks": [definition.__dict__ for definition in TASK_DEFINITIONS.values()]}


@router.post("/tasks", response_model=TaskSubmitResponse)
def submit_task(request: TaskSubmitRequest) -> TaskSubmitResponse:
    try:
        return task_queue.submit(request.task_type, request.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task_status(task_id: str) -> TaskStatusResponse:
    return task_queue.status(task_id)


@router.post("/mineru/pdf", response_model=TaskSubmitResponse)
async def submit_mineru_pdf(file: UploadFile = File(...)) -> TaskSubmitResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only .pdf files are accepted")

    file_bytes = await file.read()
    return task_queue.submit(
        "mineru.pdf.parse",
        {
            "filename": file.filename,
            "file_b64": base64.b64encode(file_bytes).decode("ascii"),
        },
    )
