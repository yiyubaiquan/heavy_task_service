import base64
import binascii
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import get_settings
from app.core.mineru_tuning import build_mineru_subprocess_env
from app.workers.celery_app import celery_app

RESULT_CONTRACT_VERSION = "mineru-pdf-result-v1"
STDOUT_TAIL_LIMIT = 4000
STDERR_TAIL_LIMIT = 2000
LOW_CONFIDENCE_THRESHOLD = 0.6


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


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None:
            cell = " ".join("".join(self._current_cell).split())
            if self._current_row is not None:
                self._current_row.append(html.unescape(cell))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


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


def _resolve_mineru_command(command: str) -> str:
    """Resolve MinerU from PATH or the active virtual environment."""

    command_path = Path(command).expanduser()
    if command_path.is_absolute() or command_path.parent != Path("."):
        return str(command_path)

    resolved = shutil.which(command)
    if resolved:
        return resolved

    scripts_dir = "Scripts" if sys.platform.startswith("win") else "bin"
    executable_name = (
        command
        if not sys.platform.startswith("win") or Path(command).suffix
        else f"{command}.exe"
    )
    venv_command = Path(sys.prefix) / scripts_dir / executable_name
    if venv_command.exists():
        return str(venv_command)

    return command


def build_mineru_command(payload: MinerUPdfPayload, task_id: str | None = None) -> tuple[list[str], Path]:
    """Build the MinerU command while keeping queue code decoupled from CLI details."""

    settings = get_settings()
    input_path = _materialize_input_pdf(payload, task_id=task_id)

    output_dir = (
        Path(payload.output_dir).expanduser().resolve()
        if payload.output_dir
        else (settings.output_dir / (task_id or input_path.stem)).resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    command = [_resolve_mineru_command(settings.mineru_command), "-p", str(input_path), "-o", str(output_dir)]
    backend = payload.backend or settings.mineru_default_backend
    if backend:
        command.extend(["-b", backend])
    command.extend(payload.extra_args)
    return command, output_dir


def _tail(value: str | None, limit: int) -> str:
    return (value or "")[-limit:]


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_relative(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.name


def _read_json(path: Path, warnings: list[dict[str, Any]]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        warnings.append(
            {
                "code": "MINERU_OUTPUT_JSON_UNREADABLE",
                "message": f"Cannot read MinerU JSON output: {path.name}: {exc}",
                "file": path.name,
            }
        )
        return None


def _discover_mineru_outputs(output_dir: Path) -> dict[str, Any]:
    files = [path for path in output_dir.rglob("*") if path.is_file()]

    def matching(suffix: str) -> list[Path]:
        return sorted(path for path in files if path.name.lower().endswith(suffix))

    markdown = sorted(path for path in files if path.suffix.lower() in {".md", ".markdown"})
    image_files = sorted(
        path
        for path in files
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
    )
    debug_pdfs = sorted(path for path in files if path.suffix.lower() == ".pdf")

    return {
        "all_files": sorted(files),
        "middle_json": matching("_middle.json"),
        "content_list_json": matching("_content_list.json"),
        "content_list_v2_json": matching("_content_list_v2.json"),
        "model_json": matching("_model.json"),
        "markdown": markdown,
        "images": image_files,
        "debug_pdfs": debug_pdfs,
    }


def _outputs_to_public_summary(outputs: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    return {
        "middle_json": [_safe_relative(path, output_dir) for path in outputs["middle_json"]],
        "content_list_json": [_safe_relative(path, output_dir) for path in outputs["content_list_json"]],
        "content_list_v2_json": [
            _safe_relative(path, output_dir) for path in outputs["content_list_v2_json"]
        ],
        "model_json": [_safe_relative(path, output_dir) for path in outputs["model_json"]],
        "markdown": [_safe_relative(path, output_dir) for path in outputs["markdown"]],
        "images": [_safe_relative(path, output_dir) for path in outputs["images"]],
        "debug_pdfs": [_safe_relative(path, output_dir) for path in outputs["debug_pdfs"]],
        "file_count": len(outputs["all_files"]),
    }


def _coerce_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    bbox: list[float] = []
    for coordinate in value:
        if not isinstance(coordinate, int | float):
            return None
        bbox.append(float(coordinate))
    return bbox


def _normalize_1000_bbox(value: Any) -> list[float] | None:
    bbox = _coerce_bbox(value)
    if bbox is None:
        return None
    return [round(coordinate / 1000.0, 6) for coordinate in bbox]


def _extract_spans(block: dict[str, Any]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for line in block.get("lines") or []:
        if isinstance(line, dict):
            spans.extend(span for span in line.get("spans") or [] if isinstance(span, dict))
    for child in block.get("blocks") or []:
        if isinstance(child, dict):
            spans.extend(_extract_spans(child))
    return spans


def _block_text(block: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "content", "table_body"):
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for span in _extract_spans(block):
        content = span.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return " ".join(parts).strip()


def _block_resource(block: dict[str, Any]) -> dict[str, str] | None:
    for key in ("image_path", "img_path"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return {key: value}
    for span in _extract_spans(block):
        for key in ("image_path", "img_path"):
            value = span.get(key)
            if isinstance(value, str) and value:
                return {key: value}
    return None


def _confidence(block: dict[str, Any], warnings: list[dict[str, Any]], block_id: str) -> float:
    scores: list[float] = []
    for value in (block.get("score"), block.get("confidence")):
        if isinstance(value, int | float):
            scores.append(float(value))
    for span in _extract_spans(block):
        value = span.get("score") or span.get("confidence")
        if isinstance(value, int | float):
            scores.append(float(value))

    if not scores:
        warnings.append(
            {
                "code": "MINERU_BLOCK_CONFIDENCE_MISSING",
                "message": "MinerU block did not provide confidence; emitted 1.0 to satisfy the result contract.",
                "block_id": block_id,
            }
        )
        return 1.0

    confidence = round(sum(scores) / len(scores), 6)
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        warnings.append(
            {
                "code": "MINERU_LOW_CONFIDENCE_BLOCK",
                "message": "MinerU block has low confidence and should be reviewed.",
                "block_id": block_id,
                "confidence": confidence,
            }
        )
    return confidence


def _normalize_block_type(value: Any) -> str:
    block_type = str(value or "unknown")
    return {
        "text": "paragraph",
        "title": "title",
        "doc_title": "title",
        "interline_equation": "formula",
        "inline_equation": "formula",
        "equation": "formula",
        "image_body": "figure",
        "chart_body": "figure",
    }.get(block_type, block_type)


def _make_layout_block(
    block: dict[str, Any],
    *,
    page_no: int,
    ordinal: int,
    coord_space: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    block_id = f"p{page_no}-b{ordinal:04d}"
    bbox = _coerce_bbox(block.get("bbox")) if coord_space == "pdf" else _normalize_1000_bbox(block.get("bbox"))
    if bbox is None:
        warnings.append(
            {
                "code": "MINERU_BLOCK_BBOX_MISSING",
                "message": "MinerU block is missing a usable bbox and was skipped.",
                "block_id": block_id,
                "page_no": page_no,
            }
        )
        return None

    layout_block: dict[str, Any] = {
        "block_id": block_id,
        "page_no": page_no,
        "type": _normalize_block_type(block.get("type")),
        "bbox": bbox,
        "bbox_coord_space": coord_space,
        "confidence": _confidence(block, warnings, block_id),
        "source": "mineru",
    }
    text = _block_text(block)
    if text:
        layout_block["text"] = text
    resource = _block_resource(block)
    if resource:
        layout_block["resource"] = resource
    if "text" not in layout_block and "resource" not in layout_block:
        layout_block["text"] = ""
    return layout_block


def _content_to_markdown(block: dict[str, Any]) -> str:
    block_type = block.get("type")
    if block_type == "table":
        return str(block.get("table_body") or "").strip()
    if block_type == "equation":
        return str(block.get("text") or "").strip()
    caption = _join_caption(block, "image_caption") or _join_caption(block, "chart_caption")
    if block_type in {"image", "chart"}:
        image_path = block.get("img_path") or block.get("image_path") or ""
        return f"![{caption}]({image_path})" if image_path else caption
    return str(block.get("text") or block.get("content") or "").strip()


def _join_caption(block: dict[str, Any], key: str) -> str:
    value = block.get(key)
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        return value.strip()
    return ""


def _parse_table_html(table_html: str) -> tuple[list[str], list[list[str]], str]:
    parser = _TableHTMLParser()
    parser.feed(table_html or "")
    rows = parser.rows
    max_columns = max((len(row) for row in rows), default=0)
    normalized_rows = [row + [""] * (max_columns - len(row)) for row in rows]
    columns = normalized_rows[0] if normalized_rows else []

    markdown = ""
    if columns:
        markdown_rows = ["| " + " | ".join(columns) + " |"]
        markdown_rows.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in normalized_rows[1:]:
            markdown_rows.append("| " + " | ".join(row) + " |")
        markdown = "\n".join(markdown_rows)
    return columns, normalized_rows, markdown


def _extract_number(text: str, prefix: str) -> str | None:
    labels = [re.escape(prefix), "\u8868", "\u56fe", r"Fig\.?", "Figure", "Table"]
    pattern = rf"(?:{'|'.join(labels)})\s*([0-9]+(?:[._-][0-9]+)*)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).replace("_", ".").replace("-", ".") if match else None


def _latex_to_plain_text(latex: str) -> str:
    plain = latex.strip().replace("$$", "").replace("$", "")
    plain = re.sub(r"\\cdot\b", "*", plain)
    plain = re.sub(r"\\[a-zA-Z]+", "", plain)
    plain = re.sub(r"[{}]", "", plain)
    return " ".join(plain.split())


def _pages_from_middle(
    middle: dict[str, Any] | None, warnings: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    pages: dict[int, dict[str, Any]] = {}
    if not isinstance(middle, dict) or not isinstance(middle.get("pdf_info"), list):
        return pages

    for fallback_index, page in enumerate(middle["pdf_info"], start=1):
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page_idx", fallback_index - 1)) + 1
        page_size = page.get("page_size") if isinstance(page.get("page_size"), list) else []
        width = float(page_size[0]) if len(page_size) >= 1 and isinstance(page_size[0], int | float) else 0.0
        height = float(page_size[1]) if len(page_size) >= 2 and isinstance(page_size[1], int | float) else 0.0
        source_blocks = page.get("para_blocks") or page.get("preproc_blocks") or []
        layout_blocks: list[dict[str, Any]] = []
        ordinal = 1
        for block in source_blocks:
            if not isinstance(block, dict):
                continue
            layout_block = _make_layout_block(
                block, page_no=page_no, ordinal=ordinal, coord_space="pdf", warnings=warnings
            )
            ordinal += 1
            if layout_block is not None:
                layout_blocks.append(layout_block)

        pages[page_no] = {
            "page_no": page_no,
            "width": width,
            "height": height,
            "layout_blocks": layout_blocks,
            "markdown": "\n\n".join(block.get("text", "") for block in layout_blocks).strip(),
            "crops": [],
        }
        if not layout_blocks:
            warnings.append(
                {
                    "code": "MINERU_PAGE_LAYOUT_BLOCKS_EMPTY",
                    "message": "MinerU did not produce layout_blocks for this page.",
                    "page_no": page_no,
                }
            )
    return pages


def _merge_content_blocks_into_pages(
    pages: dict[int, dict[str, Any]],
    content_list: list[Any],
    warnings: list[dict[str, Any]],
) -> None:
    per_page_markdown: dict[int, list[str]] = {}
    fallback_ordinals: dict[int, int] = {}
    for item in content_list:
        if not isinstance(item, dict):
            continue
        page_no = int(item.get("page_idx", 0)) + 1
        page = pages.setdefault(
            page_no,
            {
                "page_no": page_no,
                "width": 1.0,
                "height": 1.0,
                "layout_blocks": [],
                "markdown": "",
                "crops": [],
            },
        )
        if page["width"] == 1.0 and page["height"] == 1.0:
            warnings.append(
                {
                    "code": "MINERU_PAGE_SIZE_MISSING",
                    "message": "content_list does not provide page size; width/height are placeholders for norm coordinates.",
                    "page_no": page_no,
                }
            )

        per_page_markdown.setdefault(page_no, []).append(_content_to_markdown(item))
        if not page["layout_blocks"] and item.get("bbox") is not None:
            ordinal = fallback_ordinals.get(page_no, 1)
            layout_block = _make_layout_block(
                item, page_no=page_no, ordinal=ordinal, coord_space="norm", warnings=warnings
            )
            fallback_ordinals[page_no] = ordinal + 1
            if layout_block is not None:
                page["layout_blocks"].append(layout_block)

    for page_no, markdown_parts in per_page_markdown.items():
        markdown = "\n\n".join(part for part in markdown_parts if part).strip()
        if markdown:
            pages[page_no]["markdown"] = markdown


def _build_tables(content_list: list[Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "table":
            continue
        page_no = int(item.get("page_idx", 0)) + 1
        table_id = f"T_{page_no}_{len(tables) + 1}"
        caption = _join_caption(item, "table_caption")
        html_body = str(item.get("table_body") or "")
        columns, rows, markdown = _parse_table_html(html_body)
        if html_body and not rows:
            warnings.append(
                {
                    "code": "MINERU_TABLE_HTML_UNPARSED",
                    "message": "Table HTML could not be parsed into rows/columns; original HTML was preserved.",
                    "table_id": table_id,
                }
            )
        tables.append(
            {
                "table_id": table_id,
                "table_no": _extract_number(caption, "Table"),
                "title": caption,
                "page_start": page_no,
                "page_end": page_no,
                "bbox": _normalize_1000_bbox(item.get("bbox")) or [],
                "bbox_coord_space": "norm",
                "columns": columns,
                "rows": rows,
                "notes": item.get("table_footnote") or [],
                "html": html_body,
                "markdown": markdown,
                "quality": {"needs_human_review": False, "warnings": []},
            }
        )
    return tables


def _build_formulas(content_list: list[Any]) -> list[dict[str, Any]]:
    formulas: list[dict[str, Any]] = []
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") != "equation":
            continue
        page_no = int(item.get("page_idx", 0)) + 1
        latex = str(item.get("text") or item.get("content") or "").strip()
        formulas.append(
            {
                "formula_id": f"F_{page_no}_{len(formulas) + 1}",
                "formula_no": _extract_number(latex, "Equation"),
                "latex": latex,
                "plain_text": _latex_to_plain_text(latex),
                "page_no": page_no,
                "bbox": _normalize_1000_bbox(item.get("bbox")) or [],
                "bbox_coord_space": "norm",
                "quality": {"needs_human_review": False, "warnings": []},
            }
        )
    return formulas


def _build_figures(content_list: list[Any]) -> list[dict[str, Any]]:
    figures: list[dict[str, Any]] = []
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") not in {"image", "chart"}:
            continue
        page_no = int(item.get("page_idx", 0)) + 1
        caption = _join_caption(item, "image_caption") or _join_caption(item, "chart_caption")
        figures.append(
            {
                "figure_id": f"FIG_{page_no}_{len(figures) + 1}",
                "figure_no": _extract_number(caption, "Fig"),
                "caption": caption,
                "page_no": page_no,
                "bbox": _normalize_1000_bbox(item.get("bbox")) or [],
                "bbox_coord_space": "norm",
                "image_path": item.get("img_path") or item.get("image_path"),
                "ocr_text_inside_figure": item.get("ocr_text_inside_figure") or [],
            }
        )
    return figures


def _raw_files(outputs: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    return {
        "middle_json": [*_outputs_to_public_summary(outputs, output_dir)["middle_json"]],
        "content_list_json": [*_outputs_to_public_summary(outputs, output_dir)["content_list_json"]],
        "content_list_v2_json": [*_outputs_to_public_summary(outputs, output_dir)["content_list_v2_json"]],
        "model_json": [*_outputs_to_public_summary(outputs, output_dir)["model_json"]],
        "markdown": [*_outputs_to_public_summary(outputs, output_dir)["markdown"]],
        "images": [*_outputs_to_public_summary(outputs, output_dir)["images"]],
        "debug_pdfs": [*_outputs_to_public_summary(outputs, output_dir)["debug_pdfs"]],
    }


def build_structured_result(
    output_dir: Path, warnings: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    outputs = _discover_mineru_outputs(output_dir)
    middle = _read_json(outputs["middle_json"][0], warnings) if outputs["middle_json"] else None
    content_list = (
        _read_json(outputs["content_list_json"][0], warnings) if outputs["content_list_json"] else []
    )
    if not isinstance(content_list, list):
        warnings.append(
            {
                "code": "MINERU_CONTENT_LIST_INVALID",
                "message": "MinerU content_list.json is not a list and was ignored.",
            }
        )
        content_list = []

    pages = _pages_from_middle(middle if isinstance(middle, dict) else None, warnings)
    _merge_content_blocks_into_pages(pages, content_list, warnings)

    structured_result = {
        "pages": [pages[page_no] for page_no in sorted(pages)],
        "tables": _build_tables(content_list, warnings),
        "formulas": _build_formulas(content_list),
        "figures": _build_figures(content_list),
        "raw_files": _raw_files(outputs, output_dir),
    }
    return structured_result, _outputs_to_public_summary(outputs, output_dir)


def _quality_report(structured_result: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    pages = structured_result.get("pages") if isinstance(structured_result.get("pages"), list) else []
    block_count = sum(
        len(page.get("layout_blocks") or []) for page in pages if isinstance(page, dict)
    )
    return {
        "page_count": len(pages),
        "layout_block_count": block_count,
        "table_count": len(structured_result.get("tables") or []),
        "formula_count": len(structured_result.get("formulas") or []),
        "figure_count": len(structured_result.get("figures") or []),
        "warning_count": len(warnings),
        "needs_human_review": any(
            warning.get("code")
            in {
                "MINERU_LOW_CONFIDENCE_BLOCK",
                "MINERU_BLOCK_BBOX_MISSING",
                "MINERU_PAGE_LAYOUT_BLOCKS_EMPTY",
                "MINERU_OUTPUT_JSON_UNREADABLE",
            }
            for warning in warnings
        ),
    }


def _validate_structured_result(structured_result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pages = structured_result.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("structured_result.pages must be a non-empty list")
        return errors

    for page in pages:
        if not isinstance(page, dict):
            errors.append("structured_result.pages contains a non-object page")
            continue
        page_no = page.get("page_no")
        blocks = page.get("layout_blocks")
        if not isinstance(blocks, list):
            errors.append(f"page {page_no} layout_blocks must be a list")
            continue
        for block in blocks:
            if not isinstance(block, dict):
                errors.append(f"page {page_no} layout_blocks contains a non-object block")
                continue
            for field in (
                "block_id",
                "page_no",
                "type",
                "bbox",
                "bbox_coord_space",
                "confidence",
                "source",
            ):
                if field not in block:
                    errors.append(f"block missing required field: {field}")
            if "text" not in block and "resource" not in block:
                errors.append("block must include text or resource")
            if block.get("bbox_coord_space") not in {"pdf", "image_px", "norm"}:
                errors.append("block bbox_coord_space must be pdf, image_px or norm")
    return errors


def _failed_result(
    *,
    task_id: str,
    error_code: str,
    message: str,
    retryable: bool,
    command: list[str] | None = None,
    return_code: int | None = None,
    stderr: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "task_id": task_id,
        "status": "failed",
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
        "stderr_tail": _tail(stderr, STDERR_TAIL_LIMIT),
        "diagnostics": {
            "command": command or [],
            "return_code": return_code,
        },
    }
    if diagnostics:
        result["diagnostics"].update(diagnostics)
    return result


def run_mineru_pdf_parse(payload: dict[str, Any], task_id: str) -> dict[str, Any]:
    command: list[str] | None = None
    output_dir: Path | None = None
    try:
        parsed_payload = MinerUPdfPayload.model_validate(payload)
        command, output_dir = build_mineru_command(parsed_payload, task_id=task_id)
        source_pdf_sha256 = _sha256_file(Path(command[2]))
        mineru_env, tuning_profile = build_mineru_subprocess_env()
    except (ValidationError, OSError, ValueError) as exc:
        return _failed_result(
            task_id=task_id,
            error_code="MINERU_PAYLOAD_INVALID",
            message=str(exc),
            retryable=False,
            command=command,
            diagnostics={"output_dir": str(output_dir) if output_dir else None},
        )

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mineru_env,
        )
    except OSError as exc:
        return _failed_result(
            task_id=task_id,
            error_code="MINERU_PROCESS_START_FAILED",
            message=str(exc),
            retryable=True,
            command=command,
            diagnostics={"output_dir": str(output_dir)},
        )

    if completed.returncode != 0:
        return _failed_result(
            task_id=task_id,
            error_code="MINERU_PARSE_FAILED",
            message=f"MinerU parse failed, return_code={completed.returncode}",
            retryable=False,
            command=command,
            return_code=completed.returncode,
            stderr=completed.stderr,
            diagnostics={"output_dir": str(output_dir), "stdout_tail": _tail(completed.stdout, 2000)},
        )

    warnings: list[dict[str, Any]] = []
    structured_result, mineru_outputs = build_structured_result(output_dir, warnings)
    validation_errors = _validate_structured_result(structured_result)
    if validation_errors:
        return _failed_result(
            task_id=task_id,
            error_code="MINERU_RESULT_CONTRACT_INVALID",
            message="MinerU finished successfully, but its outputs do not satisfy the result contract.",
            retryable=False,
            command=command,
            return_code=completed.returncode,
            stderr=completed.stderr,
            diagnostics={
                "output_dir": str(output_dir),
                "validation_errors": validation_errors,
                "mineru_outputs": mineru_outputs,
                "stdout_tail": _tail(completed.stdout, 2000),
            },
        )

    return {
        "result_contract_version": RESULT_CONTRACT_VERSION,
        "task_id": task_id,
        "status": "success",
        "source_pdf_sha256": source_pdf_sha256,
        "output_dir": str(output_dir),
        "structured_result": structured_result,
        "artifact_package": None,
        "mineru_outputs": {
            **mineru_outputs,
            "command": command,
            "return_code": completed.returncode,
            "stdout_tail": _tail(completed.stdout, STDOUT_TAIL_LIMIT),
            "stderr_tail": _tail(completed.stderr, STDERR_TAIL_LIMIT),
            "mineru_tuning": tuning_profile.to_dict(),
        },
        "warnings": warnings,
        "quality_report": _quality_report(structured_result, warnings),
    }


@celery_app.task(name="mineru.pdf.parse", bind=True)
def parse_pdf(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Run MinerU and return the stable consumer result contract."""

    return run_mineru_pdf_parse(payload, task_id=str(self.request.id or "unknown"))
