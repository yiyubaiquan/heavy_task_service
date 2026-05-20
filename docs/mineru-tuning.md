# MinerU 单任务资源调优

本服务默认让 Celery Worker 并发保持为 1，并在每个 `mineru.pdf.parse` 任务启动 MinerU 子进程前，根据当前机器 CPU 与可用内存自动设置 MinerU 相关并行参数。这样可以把算力集中给单个 PDF 解析任务，同时避免 Redis/Celery 队列侧预取过多大任务。

## 自动调优逻辑

任务执行前会读取 `mineru.pdf.parse` 任务的运行环境，并设置以下环境变量：

- `MINERU_PDF_RENDER_THREADS`：PDF 渲染线程数。
- `MINERU_INTRA_OP_NUM_THREADS`：单个算子内部并行线程数。
- `MINERU_INTER_OP_NUM_THREADS`：算子之间并行线程数。
- `MINERU_PROCESSING_WINDOW_SIZE`：流水处理窗口大小。

默认计算方式：

```text
可用线程数 = min(可用 CPU 核数, (可用内存 - 预留内存) / 单线程预估内存)
```

默认配置：

```env
MINERU_AUTO_TUNE=true
MINERU_MEMORY_PER_THREAD_MB=1536
CELERY_WORKER_CONCURRENCY=1
```

如果未显式设置 `MINERU_RESERVED_MEMORY_MB`，服务会至少预留 1024 MB，或预留当前可用内存的 20%，取两者较大值。

## 为什么 Worker 并发保持为 1

MinerU 单个任务通常会占用较多 CPU 与内存。如果同时运行多个 MinerU 子进程，容易出现以下问题：

- Celery Worker 进程之间争抢 CPU，单任务变慢。
- MinerU 子进程同时占用大量内存，增加 OOM 风险。
- Redis 队列中大 payload 与结果同时堆积，放大内存压力。

因此推荐先用 `CELERY_WORKER_CONCURRENCY=1` 控制每个 Worker 只跑一个 PDF 解析任务，再通过 MinerU 内部线程数把单任务性能打满。需要更高吞吐时，优先横向增加 Worker 机器或 Worker 实例数量。

## 手动覆盖

如果希望固定并行参数，可以在 `.env` 中手动设置：

```env
MINERU_RESERVED_MEMORY_MB=4096
MINERU_PDF_RENDER_THREADS=8
MINERU_INTRA_OP_NUM_THREADS=8
MINERU_INTER_OP_NUM_THREADS=2
MINERU_PROCESSING_WINDOW_SIZE=16
```

显式配置的变量会覆盖自动计算结果。若设置 `MINERU_AUTO_TUNE=false` 且没有显式配置上述变量，服务不会主动向 MinerU 子进程注入这些调优环境变量。

## 返回结果中的调优信息

任务完成后，Celery result 会包含本次使用的调优信息，便于排查性能和资源问题：

```json
{
  "mineru_tuning": {
    "auto_tune": true,
    "cpu_count": 8,
    "available_memory_mb": 16384,
    "reserved_memory_mb": 3276,
    "memory_per_thread_mb": 1536,
    "core_budget": 8,
    "pdf_render_threads": 8,
    "intra_op_num_threads": 8,
    "inter_op_num_threads": 2,
    "processing_window_size": 16
  }
}
```
