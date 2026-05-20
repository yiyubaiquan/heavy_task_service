from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "heavy-task-service"}


def test_list_tasks_contains_mineru_pdf_parse() -> None:
    response = TestClient(app).get("/tasks")

    assert response.status_code == 200
    task_types = {task["task_type"] for task in response.json()["tasks"]}
    assert "mineru.pdf.parse" in task_types
