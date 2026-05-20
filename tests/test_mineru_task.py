import base64
import json
import subprocess
from pathlib import Path

import pytest

from app.core import mineru_tuning
from app.core.config import get_settings
from app.core.mineru_tuning import build_mineru_subprocess_env
from app.tasks.mineru_pdf import (
    RESULT_CONTRACT_VERSION,
    MinerUPdfPayload,
    build_mineru_command,
    run_mineru_pdf_parse,
)


def test_build_mineru_command_uses_task_output_dir(tmp_path: Path) -> None:
    input_pdf = tmp_path / "demo.pdf"
    output_base = tmp_path / "output"
    input_pdf.write_bytes(b"%PDF-1.4\n")

    command, output_dir = build_mineru_command(
        MinerUPdfPayload(input_path=str(input_pdf), output_dir=str(output_base)),
        task_id="task-123",
    )

    assert Path(command[0]).name.lower() in {"mineru", "mineru.exe"}
    assert command[1:4] == ["-p", str(input_pdf.resolve()), "-o"]
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
    assert Path(command[0]).name.lower() in {"mineru", "mineru.exe"}
    assert command[1] == "-p"
    assert input_path == (tmp_path / "storage" / "inputs" / "task-inline" / "redis-demo.pdf").resolve()
    assert input_path.read_bytes() == pdf_bytes
    assert output_dir == output_base.resolve()

    get_settings.cache_clear()


def test_mineru_payload_requires_exactly_one_input_source() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MinerUPdfPayload()

    with pytest.raises(ValueError, match="exactly one"):
        MinerUPdfPayload(input_path="demo.pdf", file_b64="AAAA")


def test_mineru_subprocess_env_defaults_to_modelscope() -> None:
    env, _profile = build_mineru_subprocess_env(base_env={})

    assert env["MINERU_MODEL_SOURCE"] == "modelscope"


def test_mineru_subprocess_env_preserves_explicit_model_source() -> None:
    env, _profile = build_mineru_subprocess_env(base_env={"MINERU_MODEL_SOURCE": "huggingface"})

    assert env["MINERU_MODEL_SOURCE"] == "huggingface"


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



def _write_sample_mineru_outputs(output_dir: Path) -> None:
    (output_dir / "images").mkdir(parents=True, exist_ok=True)
    (output_dir / "images" / "fig.png").write_bytes(b"png")
    (output_dir / "demo.md").write_text("# demo", encoding="utf-8")
    (output_dir / "demo_middle.json").write_text(
        json.dumps(
            {
                "pdf_info": [
                    {
                        "page_idx": 0,
                        "page_size": [595.0, 842.0],
                        "para_blocks": [
                            {
                                "type": "text",
                                "bbox": [72, 120, 520, 160],
                                "lines": [
                                    {
                                        "bbox": [72, 120, 520, 160],
                                        "spans": [
                                            {
                                                "type": "text",
                                                "content": "1.0.1 body text",
                                                "bbox": [72, 120, 520, 160],
                                                "score": 0.95,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "_backend": "pipeline",
                "_version_name": "test",
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "demo_content_list.json").write_text(
        json.dumps(
            [
                {
                    "type": "text",
                    "text": "1.0.1 body text",
                    "bbox": [100, 100, 900, 150],
                    "page_idx": 0,
                },
                {
                    "type": "table",
                    "table_caption": ["Table 5.1.2 demo"],
                    "table_footnote": ["note"],
                    "table_body": (
                        "<table><tr><th>Name</th><th>Value</th></tr>"
                        "<tr><td>A</td><td>1</td></tr></table>"
                    ),
                    "bbox": [120, 200, 880, 400],
                    "page_idx": 0,
                },
                {
                    "type": "equation",
                    "text": "$$Q = K \\cdot A$$",
                    "bbox": [120, 420, 880, 470],
                    "page_idx": 0,
                },
                {
                    "type": "image",
                    "img_path": "images/fig.png",
                    "image_caption": ["Figure 6.2.1 demo"],
                    "bbox": [120, 500, 880, 800],
                    "page_idx": 0,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_run_mineru_pdf_parse_returns_success_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_pdf = tmp_path / "demo.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\ncontract\n")
    output_dir = tmp_path / "outputs"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "env" in kwargs
        _write_sample_mineru_outputs(Path(command[command.index("-o") + 1]))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("app.tasks.mineru_pdf.subprocess.run", fake_run)

    result = run_mineru_pdf_parse(
        {"input_path": str(input_pdf), "output_dir": str(output_dir)}, task_id="task-contract"
    )

    assert result["result_contract_version"] == RESULT_CONTRACT_VERSION
    assert result["task_id"] == "task-contract"
    assert result["status"] == "success"
    assert result["artifact_package"] is None
    assert result["source_pdf_sha256"]
    assert result["output_dir"] == str(output_dir.resolve())
    assert result["structured_result"]
    assert result["mineru_outputs"]["return_code"] == 0

    page = result["structured_result"]["pages"][0]
    assert page["page_no"] == 1
    assert page["width"] == 595.0
    assert page["height"] == 842.0
    block = page["layout_blocks"][0]
    assert block["block_id"] == "p1-b0001"
    assert block["page_no"] == 1
    assert block["type"] == "paragraph"
    assert block["bbox"] == [72.0, 120.0, 520.0, 160.0]
    assert block["bbox_coord_space"] == "pdf"
    assert block["confidence"] == 0.95
    assert block["text"] == "1.0.1 body text"
    assert block["source"] == "mineru"

    table = result["structured_result"]["tables"][0]
    assert table["table_no"] == "5.1.2"
    assert table["columns"] == ["Name", "Value"]
    assert table["rows"] == [["Name", "Value"], ["A", "1"]]
    assert "| Name | Value |" in table["markdown"]

    formula = result["structured_result"]["formulas"][0]
    assert formula["latex"] == "$$Q = K \\cdot A$$"
    assert formula["plain_text"] == "Q = K * A"

    figure = result["structured_result"]["figures"][0]
    assert figure["figure_no"] == "6.2.1"
    assert figure["image_path"] == "images/fig.png"

    serialized = json.dumps(result, ensure_ascii=False)
    assert "REDIS_URL" not in serialized
    assert "SECRET" not in serialized.upper()


def test_run_mineru_pdf_parse_returns_failed_contract_on_mineru_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_pdf = tmp_path / "demo.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    stderr = "x" * 2500

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="bad", stderr=stderr)

    monkeypatch.setattr("app.tasks.mineru_pdf.subprocess.run", fake_run)

    result = run_mineru_pdf_parse({"input_path": str(input_pdf)}, task_id="task-failed")

    assert result["result_contract_version"] == RESULT_CONTRACT_VERSION
    assert result["task_id"] == "task-failed"
    assert result["status"] == "failed"
    assert result["error_code"] == "MINERU_PARSE_FAILED"
    assert result["retryable"] is False
    assert len(result["stderr_tail"]) == 2000
    assert result["diagnostics"]["return_code"] == 1
    assert result["diagnostics"]["command"]


def test_run_mineru_pdf_parse_fails_closed_when_structured_result_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_pdf = tmp_path / "demo.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    output_dir = tmp_path / "empty-output"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr("app.tasks.mineru_pdf.subprocess.run", fake_run)

    result = run_mineru_pdf_parse(
        {"input_path": str(input_pdf), "output_dir": str(output_dir)}, task_id="task-invalid"
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "MINERU_RESULT_CONTRACT_INVALID"
    assert "validation_errors" in result["diagnostics"]
