# MinerU PDF 解析消费者接口修改要求

本文档面向外部 `heavy-task-service` / MinerU 消费者项目。目标是让本仓库的 `mineru_pdf_parse_producer` 能通过 Celery/Redis 投递 PDF，并稳定取回后续全流程解析需要的 MinerU 骨架产物。

## 1. 任务协议

- Celery 任务名：`mineru.pdf.parse`
- 默认队列：`heavy_tasks`
- 参数：一个位置参数 `payload`
- 序列化：JSON

生产者仍按现有方式发送：

```json
{
  "filename": "demo.pdf",
  "file_b64": "JVBERi0xLjQK...",
  "backend": "pipeline",
  "extra_args": []
}
```

## 2. 成功结果必须返回的字段

消费者不能只返回 `command/stdout/stderr/output_dir`。成功时必须返回可被生产者读取的结构化骨架或 artifact 包。

```json
{
  "result_contract_version": "mineru-pdf-result-v1",
  "task_id": "celery-task-id",
  "status": "success",
  "source_pdf_sha256": "原 PDF sha256",
  "output_dir": "消费者侧输出目录",
  "structured_result": {},
  "artifact_package": null,
  "mineru_outputs": {},
  "warnings": [],
  "quality_report": {}
}
```

`structured_result` 和 `artifact_package` 二选一；推荐优先返回 `structured_result`。

## 3. structured_result 最小结构

```json
{
  "pages": [
    {
      "page_no": 1,
      "width": 595.0,
      "height": 842.0,
      "layout_blocks": [
        {
          "block_id": "p1-b0001",
          "page_no": 1,
          "type": "paragraph",
          "bbox": [72, 120, 520, 160],
          "bbox_coord_space": "pdf",
          "confidence": 0.95,
          "text": "1.0.1 ...",
          "source": "mineru"
        }
      ],
      "markdown": "...",
      "crops": []
    }
  ],
  "tables": [],
  "formulas": [],
  "figures": [],
  "raw_files": {}
}
```

每个 block 必须包含：

- `block_id`
- `page_no`
- `type`
- `bbox`
- `bbox_coord_space`：`pdf`、`image_px` 或 `norm`
- `confidence`
- `text` 或资源引用
- `source`

## 4. 表格、公式、图片建议字段

表格：

```json
{
  "table_id": "T_5_1_2",
  "table_no": "表 5.1.2",
  "title": "表名",
  "page_start": 35,
  "page_end": 36,
  "bbox": [72, 120, 530, 680],
  "columns": [],
  "rows": [],
  "notes": [],
  "html": "<table>...</table>",
  "markdown": "|...|",
  "quality": {
    "needs_human_review": false,
    "warnings": []
  }
}
```

公式：

```json
{
  "formula_id": "F_8_3_1",
  "formula_no": "8.3.1",
  "latex": "Q = K \\cdot A",
  "plain_text": "Q = K * A",
  "bbox": [80, 220, 500, 270],
  "quality": {
    "needs_human_review": false,
    "warnings": []
  }
}
```

图片：

```json
{
  "figure_id": "FIG_6_2_1",
  "figure_no": "图 6.2.1",
  "caption": "图名",
  "page_no": 52,
  "bbox": [60, 130, 540, 500],
  "image_path": "figures/FIG_6_2_1.png",
  "ocr_text_inside_figure": []
}
```

## 5. artifact_package 备用结构

如果不能直接返回完整 `structured_result`，可以返回 artifact 包描述：

```json
{
  "artifact_package": {
    "type": "zip",
    "uri": "http://host/artifacts/task-id.zip",
    "sha256": "zip sha256",
    "files": {
      "layout_json": "layout.json",
      "markdown": "document.md",
      "crops_dir": "crops/",
      "raw_dir": "raw/"
    }
  }
}
```

生产者必须能从 `uri` 下载，或能从共享路径读取包。

## 6. 失败结果

失败时返回机器可读错误，不要只把原因放在 stderr。

```json
{
  "result_contract_version": "mineru-pdf-result-v1",
  "task_id": "celery-task-id",
  "status": "failed",
  "error_code": "MINERU_PARSE_FAILED",
  "message": "解析失败原因",
  "retryable": false,
  "stderr_tail": "最后 2000 字符以内 stderr",
  "diagnostics": {
    "command": ["mineru", "..."],
    "return_code": 1
  }
}
```

## 7. 验收要求

- 成功结果必须能让生产者判断是否有 `structured_result` 或 `artifact_package`。
- `layout_blocks` 不能为空时，每个 block 必须有页码、类型、bbox 和置信度。
- 坐标空间必须明确，不能让生产者猜测 bbox 是 PDF 坐标还是图片像素。
- 跨页表格、低置信 OCR、解析异常必须写入 `warnings` 或 `quality_report`。
- 不要返回密钥、临时凭据或完整敏感环境变量。
