from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskSubmitRequest(BaseModel):
    task_type: str = Field(..., examples=["mineru.pdf.parse"])
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskSubmitResponse(BaseModel):
    task_id: str
    task_type: str
    status: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    ready: bool
    successful: bool | None = None
    result: Any = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
