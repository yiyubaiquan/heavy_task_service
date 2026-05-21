from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.services.task_service import TaskQueueUnavailableError


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "heavy-task-service"}


def test_list_tasks_contains_mineru_pdf_parse() -> None:
    response = TestClient(app).get("/tasks")

    assert response.status_code == 200
    task_types = {task["task_type"] for task in response.json()["tasks"]}
    assert "mineru.pdf.parse" in task_types


def test_submit_task_returns_503_when_queue_is_unavailable(monkeypatch) -> None:
    class UnavailableTaskQueue:
        def submit(self, task_type: str, payload: dict) -> None:
            raise TaskQueueUnavailableError("task queue is unavailable")

    monkeypatch.setattr(routes, "task_queue", UnavailableTaskQueue())

    response = TestClient(app).post(
        "/tasks",
        json={"task_type": "mineru.pdf.parse", "payload": {}},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "task queue is unavailable"}


def test_task_status_returns_503_when_queue_is_unavailable(monkeypatch) -> None:
    class UnavailableTaskQueue:
        def status(self, task_id: str) -> None:
            raise TaskQueueUnavailableError("task queue is unavailable")

    monkeypatch.setattr(routes, "task_queue", UnavailableTaskQueue())

    response = TestClient(app).get("/tasks/task-123")

    assert response.status_code == 503
    assert response.json() == {"detail": "task queue is unavailable"}
