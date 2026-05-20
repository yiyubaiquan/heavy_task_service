from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.paths import resolve_under


async def save_upload(file: UploadFile) -> Path:
    settings = get_settings()
    settings.input_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "").suffix.lower()
    filename = f"{uuid4().hex}{suffix}"
    target = resolve_under(settings.input_dir, filename)

    with target.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            output.write(chunk)

    return target
