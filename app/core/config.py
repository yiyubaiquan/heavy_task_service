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
    mineru_auto_tune: bool = Field(True, alias="MINERU_AUTO_TUNE")
    mineru_memory_per_thread_mb: int = Field(1536, alias="MINERU_MEMORY_PER_THREAD_MB")
    mineru_reserved_memory_mb: int | None = Field(None, alias="MINERU_RESERVED_MEMORY_MB")
    mineru_pdf_render_threads: int | None = Field(None, alias="MINERU_PDF_RENDER_THREADS")
    mineru_intra_op_num_threads: int | None = Field(None, alias="MINERU_INTRA_OP_NUM_THREADS")
    mineru_inter_op_num_threads: int | None = Field(None, alias="MINERU_INTER_OP_NUM_THREADS")
    mineru_processing_window_size: int | None = Field(None, alias="MINERU_PROCESSING_WINDOW_SIZE")
    celery_task_default_queue: str = Field("heavy_tasks", alias="CELERY_TASK_DEFAULT_QUEUE")
    celery_redis_socket_timeout: float = Field(3.0, alias="CELERY_REDIS_SOCKET_TIMEOUT")
    celery_redis_socket_connect_timeout: float = Field(
        3.0, alias="CELERY_REDIS_SOCKET_CONNECT_TIMEOUT"
    )

    @property
    def input_dir(self) -> Path:
        return self.service_storage_dir / "inputs"

    @property
    def output_dir(self) -> Path:
        return self.service_storage_dir / "outputs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
