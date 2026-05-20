# MinerU PDF 解析生产者接口文档

本文档面向上游生产者服务，用于说明如何向 `heavy-task-service` 投递 PDF 解析任务。

## 1. 接入方式选择

### 仅 Redis 可公共访问时的推荐方式

生产者使用 Celery 客户端，把任务发送到 Redis。

不要手动用 `LPUSH` 写入普通 JSON。当前 worker 消费的是 Celery 协议消息，不是任意 Redis List 消息。

### HTTP API 可访问时的可选方式

如果生产者可以访问 HTTP 服务，也可以直接调用 HTTP API：

- Swagger UI：`http://<api-host>:8010/docs`
- OpenAPI JSON：`http://<api-host>:8010/openapi.json`
- 上传 PDF 接口：`POST /mineru/pdf`
- 通用任务投递接口：`POST /tasks`

## 2. Redis / Celery 对接约定

| 字段 | 值 |
| --- | --- |
| Broker 地址 | 服务 `.env` 里的 `REDIS_URL` |
| 结果后端 | 同一个 Redis 地址 |
| 队列名 | 默认 `heavy_tasks`，来自 `CELERY_TASK_DEFAULT_QUEUE` |
| Celery 任务名 | `mineru.pdf.parse` |
| 序列化方式 | JSON |
| 任务参数 | 一个位置参数对象：`payload` |

## 3. MinerU 任务 payload

任务输入源必须二选一，不能同时传，也不能都不传：

1. `file_b64` + `filename`：把 PDF 文件内容通过 Redis 发送。只有 Redis 是共享通道时，推荐使用这种方式。
2. `input_path`：传 worker 机器上已经可访问的本地文件路径。

### 推荐 payload：文件内容通过 Redis 传递

```json
{
  "filename": "demo.pdf",
  "file_b64": "JVBERi0xLjQK...",
  "output_dir": "D:/optional/output/dir",
  "backend": "pipeline",
  "extra_args": []
}
```

字段说明：

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `filename` | 推荐填写 | 原始 PDF 文件名。建议以 `.pdf` 结尾；不传时 worker 使用 `input.pdf`。 |
| `file_b64` | 必填 | PDF 文件字节的 Base64 编码字符串。 |
| `output_dir` | 可选 | worker 机器上的输出目录。不传时使用 `storage/outputs/<task_id>`。 |
| `backend` | 可选 | MinerU backend。不传时使用 `MINERU_DEFAULT_BACKEND`，通常是 `pipeline`。 |
| `extra_args` | 可选 | 追加到 MinerU 命令行末尾的额外参数。 |

### 备用 payload：worker 本地路径

```json
{
  "input_path": "D:/shared-or-local/demo.pdf",
  "output_dir": "D:/optional/output/dir",
  "backend": "pipeline",
  "extra_args": []
}
```

`input_path` 必须在 worker 机器上真实存在，并且 worker 进程有权限读取。

## 4. Python 生产者示例

在生产者项目里安装依赖：

```bash
pip install "celery[redis]>=5.4,<6"
```

通过 Redis 投递一个 PDF 解析任务：

```python
import base64
from celery import Celery

REDIS_URL = "redis://:password@redis-host:6379/0"
QUEUE = "heavy_tasks"
TASK_NAME = "mineru.pdf.parse"

celery_app = Celery(
    "mineru_producer",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

pdf_path = r"D:\path\demo.pdf"
with open(pdf_path, "rb") as f:
    file_b64 = base64.b64encode(f.read()).decode("ascii")

async_result = celery_app.send_task(
    TASK_NAME,
    args=[
        {
            "filename": "demo.pdf",
            "file_b64": file_b64,
            # 可选：
            # "output_dir": r"D:\worker-output\demo",
            # "backend": "pipeline",
            # "extra_args": [],
        }
    ],
    queue=QUEUE,
)

print("task_id=", async_result.id)
```

## 5. 生产者查询任务结果

如果生产者也可以访问同一个 Redis 结果后端，可以用 Celery 查询任务状态和结果：

```python
from celery import Celery

REDIS_URL = "redis://:password@redis-host:6379/0"
TASK_ID = "replace-with-task-id"

celery_app = Celery("mineru_producer", broker=REDIS_URL, backend=REDIS_URL)
result = celery_app.AsyncResult(TASK_ID)

print("status=", result.status)
print("ready=", result.ready())

if result.ready():
    if result.successful():
        print("result=", result.result)
    else:
        print("error=", result.result)
```

成功结果示例：

```json
{
  "command": ["mineru", "-p", "...", "-o", "...", "-b", "pipeline"],
  "input_path": ".../storage/inputs/<task_id>/demo.pdf",
  "output_dir": ".../storage/outputs/<task_id>",
  "return_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

## 6. 排队和内存注意事项

- Redis 同时作为任务 broker 和结果后端。使用 `file_b64` 时，PDF 文件内容会进入 Redis，直到 worker 消费任务。
- Base64 会让 payload 大约增大 33%。
- 排队由 Celery/Redis 自动完成：worker 忙时，任务会留在 Redis 队列里等待消费。
- worker 已配置 `worker_prefetch_multiplier=1`，可以避免单个 worker 一次预取太多大任务。
- Redis 仍然需要足够内存存放排队中的 PDF payload 和任务结果。PDF 很大或积压很多时，请谨慎配置 Redis `maxmemory` 和淘汰策略，或者增加 worker 数量。

## 7. 生产者最小校验清单

发送任务前建议确认：

1. 文件确实是 PDF，文件名以 `.pdf` 结尾。
2. 文件字节已编码成 ASCII Base64 字符串。
3. 发送到任务名 `mineru.pdf.parse`。
4. 队列名使用 `heavy_tasks`。
5. 保存 Celery 返回的 `task_id`。
6. 通过 Celery Redis 结果后端查询状态，或者在 HTTP 可访问时调用 `GET /tasks/<task_id>` 查询。
