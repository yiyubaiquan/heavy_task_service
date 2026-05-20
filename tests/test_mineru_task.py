import base64
from pathlib import Path

import pytest

from app.core import mineru_tuning
from app.core.config import get_settings
from app.core.mineru_tuning import build_mineru_subprocess_env
from app.tasks.mineru_pdf import MinerUPdfPayload, build_mineru_command


def test_build_mineru_command_uses_task_output_dir(tmp_path: Path) -> None:
    input_pdf = tmp_path / "demo.pdf"
    output_base = tmp_path / "output"
    input_pdf.write_bytes(b"%PDF-1.4\n")

    command, output_dir = build_mineru_command(
        MinerUPdfPayload(input_path=str(input_pdf), output_dir=str(output_base)),
        task_id="task-123",
    )

    assert command[:4] == ["mineru", "-p", str(input_pdf.resolve()), "-o"]
    assert output_dir == output_base.resolve()
    assert output_dir.exists()
    assert command[-2:] == ["-b", "pipeline"]


def test_build_mineru_command_materializes_inline_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_STORAGE_DIR", str(tmp_path / "storage"))
    get_settings.cache_clear()

    pdf_bytes = b"%PDF-1.4\nfrom redis\n"
    output_base = tmp_path / "output"

    command, output_dir = build_mineru_command(
        MinerUPdfPayload(
            filename="redis-demo.pdf",
            file_b64=base64.b64encode(pdf_bytes).decode("ascii"),
            output_dir=str(output_base),
        ),
        task_id="task-inline",
    )

    input_path = Path(command[2])
    assert command[:2] == ["mineru", "-p"]
    assert input_path == (tmp_path / "storage" / "inputs" / "task-inline" / "redis-demo.pdf").resolve()
    assert input_path.read_bytes() == pdf_bytes
    assert output_dir == output_base.resolve()

    get_settings.cache_clear()


def test_mineru_payload_requires_exactly_one_input_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MinerUPdfPayload()

    with pytest.raises(ValueError, match="exactly one"):
        MinerUPdfPayload(input_path="demo.pdf", file_b64="AAAA")


def test_mineru_auto_tuning_uses_current_cpu_and_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_tuning, "get_cpu_count", lambda: 8)
    monkeypatch.setattr(mineru_tuning, "get_available_memory_mb", lambda: 16_384)
    get_settings.cache_clear()

    env, profile = build_mineru_subprocess_env(base_env={})

    assert profile.core_budget == 8
    assert env["MINERU_PDF_RENDER_THREADS"] == "8"
    assert env["MINERU_INTRA_OP_NUM_THREADS"] == "8"
    assert env["MINERU_INTER_OP_NUM_THREADS"] == "2"
    assert env["MINERU_PROCESSING_WINDOW_SIZE"] == "16"

    get_settings.cache_clear()


def test_mineru_auto_tuning_reduces_threads_when_memory_is_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_tuning, "get_cpu_count", lambda: 8)
    monkeypatch.setattr(mineru_tuning, "get_available_memory_mb", lambda: 4096)
    get_settings.cache_clear()

    env, profile = build_mineru_subprocess_env(base_env={})

    assert profile.core_budget == 2
    assert env["MINERU_PDF_RENDER_THREADS"] == "2"
    assert env["MINERU_INTRA_OP_NUM_THREADS"] == "2"
    assert env["MINERU_PROCESSING_WINDOW_SIZE"] == "4"

    get_settings.cache_clear()


def test_mineru_tuning_accepts_explicit_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_tuning, "get_cpu_count", lambda: 8)
    monkeypatch.setattr(mineru_tuning, "get_available_memory_mb", lambda: 16_384)
    monkeypatch.setenv("MINERU_PDF_RENDER_THREADS", "3")
    monkeypatch.setenv("MINERU_INTRA_OP_NUM_THREADS", "4")
    get_settings.cache_clear()

    env, profile = build_mineru_subprocess_env(base_env={})

    assert profile.pdf_render_threads == 3
    assert profile.intra_op_num_threads == 4
    assert env["MINERU_PDF_RENDER_THREADS"] == "3"
    assert env["MINERU_INTRA_OP_NUM_THREADS"] == "4"

    get_settings.cache_clear()
