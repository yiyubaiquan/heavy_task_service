from pathlib import Path

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
