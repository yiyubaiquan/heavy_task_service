import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.workers.celery_app import celery_app


class MinerUPdfPayload(BaseModel):
    input_path: str = Field(..., description="Local PDF path to parse.")
    output_dir: str | None = Field(None, description="Directory for MinerU output artifacts.")
    backend: str | None = Field(None, description="MinerU backend, for example: pipeline.")
    extra_args: list[str] = Field(default_factory=list, description="Additional MinerU CLI args.")


def build_mineru_command(payload: MinerUPdfPayload, task_id: str | None = None) -> tuple[list[str], Path]:
    """Build the MinerU CLI command without coupling the queue service to MinerU internals."""

    settings = get_settings()
    input_path = Path(payload.input_path).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"input PDF not found: {input_path}")
    if input_path.suffix.lower() != ".pdf":
        raise ValueError(f"MinerU PDF task only accepts .pdf files: {input_path}")

    output_dir = (
        Path(payload.output_dir).expanduser().resolve()
        if payload.output_dir
        else (settings.output_dir / (task_id or input_path.stem)).resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [settings.mineru_command, "-p", str(input_path), "-o", str(output_dir)]
    backend = payload.backend or settings.mineru_default_backend
    if backend:
        command.extend(["-b", backend])
    command.extend(payload.extra_args)
    return command, output_dir


@celery_app.task(name="mineru.pdf.parse", bind=True)
def parse_pdf(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Celery task: run MinerU as an isolated external process."""

    parsed_payload = MinerUPdfPayload.model_validate(payload)
    command, output_dir = build_mineru_command(parsed_payload, task_id=self.request.id)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    result = {
        "command": command,
        "input_path": str(Path(parsed_payload.input_path).resolve()),
        "output_dir": str(output_dir),
        "return_code": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    if completed.returncode != 0:
        raise RuntimeError(f"MinerU failed with return_code={completed.returncode}: {result}")
    return result
