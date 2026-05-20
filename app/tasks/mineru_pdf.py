import base64
import binascii
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings
from app.core.mineru_tuning import build_mineru_subprocess_env
from app.workers.celery_app import celery_app


class MinerUPdfPayload(BaseModel):
    input_path: str | None = Field(None, description="Local PDF path to parse.")
    file_b64: str | None = Field(None, description="Base64 encoded PDF bytes carried in the queue message.")
    filename: str | None = Field(None, description="Original PDF filename when file_b64 is used.")
    output_dir: str | None = Field(None, description="Directory for MinerU output artifacts.")
    backend: str | None = Field(None, description="MinerU backend, for example: pipeline.")
    extra_args: list[str] = Field(default_factory=list, description="Additional MinerU CLI args.")

    @model_validator(mode="after")
    def validate_input_source(self) -> "MinerUPdfPayload":
        if bool(self.input_path) == bool(self.file_b64):
            raise ValueError("provide exactly one of input_path or file_b64")
        return self


def _materialize_input_pdf(payload: MinerUPdfPayload, task_id: str | None = None) -> Path:
    settings = get_settings()

    if payload.input_path:
        input_path = Path(payload.input_path).expanduser().resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"input PDF not found: {input_path}")
    else:
        raw_filename = Path(payload.filename or "input.pdf").name
        filename = raw_filename if raw_filename.lower().endswith(".pdf") else f"{raw_filename}.pdf"
        input_dir = settings.input_dir / (task_id or "queued")
        input_dir.mkdir(parents=True, exist_ok=True)
        input_path = (input_dir / filename).resolve()
        try:
            pdf_bytes = base64.b64decode(payload.file_b64 or "", validate=True)
        except binascii.Error as exc:
            raise ValueError("file_b64 must be valid base64") from exc
        input_path.write_bytes(pdf_bytes)

    if input_path.suffix.lower() != ".pdf":
        raise ValueError(f"MinerU PDF task only accepts .pdf files: {input_path}")
    return input_path


def build_mineru_command(payload: MinerUPdfPayload, task_id: str | None = None) -> tuple[list[str], Path]:
    """Build the MinerU CLI command without coupling the queue service to MinerU internals."""

    settings = get_settings()
    input_path = _materialize_input_pdf(payload, task_id=task_id)

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
    mineru_env, tuning_profile = build_mineru_subprocess_env()

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=mineru_env,
    )

    result = {
        "command": command,
        "input_path": command[2],
        "output_dir": str(output_dir),
        "return_code": completed.returncode,
        "mineru_tuning": tuning_profile.to_dict(),
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
    if completed.returncode != 0:
        raise RuntimeError(f"MinerU failed with return_code={completed.returncode}: {result}")
    return result
