from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from .env and environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = Field(..., alias="REDIS_URL")
    api_host: str = Field("0.0.0.0", alias="API_HOST")
    api_port: int = Field(8010, alias="API_PORT")

    service_storage_dir: Path = Field(Path("storage"), alias="SERVICE_STORAGE_DIR")
    mineru_command: str = Field("mineru", alias="MINERU_COMMAND")
    mineru_default_backend: str | None = Field("pipeline", alias="MINERU_DEFAULT_BACKEND")
    celery_task_default_queue: str = Field("heavy_tasks", alias="CELERY_TASK_DEFAULT_QUEUE")

    @property
    def input_dir(self) -> Path:
        return self.service_storage_dir / "inputs"

    @property
    def output_dir(self) -> Path:
        return self.service_storage_dir / "outputs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
