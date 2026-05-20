# Heavy Task Service

## 部署与服务启停（先看这里）

本项目使用 **uv** 管理 Python 环境、依赖和锁文件。Linux 长期运行使用 **systemd user service**，比 tmux/nohup 更适合作为稳定后台服务运行。

### 1. 准备配置

```bash
# Windows PowerShell / Linux shell 均可参考
cd heavy-task-service
cp .env.example .env
```

然后按实际环境修改 `.env`，至少确认：

```env
REDIS_URL=redis://:change-me@127.0.0.1:6379/0
API_HOST=0.0.0.0
API_PORT=8010
CELERY_TASK_DEFAULT_QUEUE=heavy_tasks
```

服务依赖 Redis；请先确保 Redis 可访问。如果本机需要直接执行 MinerU 任务，再安装可选依赖：

```bash
uv sync --locked --extra mineru
```

普通部署只需要：

```bash
uv sync --locked
```

### 2. Windows 启动 / 停止

```powershell
# 启动 API + Celery Worker（后台隐藏窗口运行，首次会执行 uv sync --locked）
.\scripts\start.ps1

# 如果已同步过依赖，可跳过同步
.\scripts\start.ps1 -SkipSync

# 停止 API + Celery Worker
.\scripts\stop.ps1
```

日志与 PID 文件：

- 日志：`logs/api.out.log`、`logs/api.err.log`、`logs/worker.out.log`、`logs/worker.err.log`
- PID：`run/api.pid`、`run/worker.pid`

> Windows 本地运行 Celery 使用 `--pool=solo`，避免 prefork 在 Windows 上的不兼容问题。

### 3. Linux 启动 / 停止（systemd，比 tmux 更稳定）

```bash
# 第一次执行前如果脚本没有执行权限
chmod +x scripts/start.sh scripts/stop.sh

# 启动并注册 systemd user services
./scripts/start.sh

# 停止服务
./scripts/stop.sh

# 停止并禁用开机/登录自启动
./scripts/stop.sh --disable
```

Linux 脚本会生成并启动两个 systemd 用户服务：

- `heavy-task-service-api.service`
- `heavy-task-service-worker.service`

常用查看命令：

```bash
systemctl --user status heavy-task-service-api.service heavy-task-service-worker.service
journalctl --user -u heavy-task-service-api.service -f
journalctl --user -u heavy-task-service-worker.service -f
```

如果希望用户退出登录后服务仍继续运行，请在服务器上执行一次：

```bash
sudo loginctl enable-linger $USER
```

### 4. 验证服务

```bash
curl http://127.0.0.1:8010/health
```

预期返回：

```json
{"status":"ok","service":"heavy-task-service"}
```

## 项目说明

一个职责单一的重型任务异步服务：API 只负责接收请求和投递任务，具体任务在 Celery Worker 中独立执行。当前内置任务为 `mineru.pdf.parse`。

## 架构约定

- **API 层**：`app/api`，只做参数接收、文件落盘、任务投递。
- **队列层**：`app/services/task_service.py`，只按 `task_type` 找到 Celery task name 并入队。
- **任务层**：`app/tasks`，每个重型任务一个独立模块；后续新增任务时不要改 API 主流程。
- **Worker 层**：`app/workers/celery_app.py`，集中维护 Celery 配置和任务模块加载列表。
- **敏感配置**：放在 `.env`，仓库只提交 `.env.example`。

## 本地开发

```bash
uv sync --locked --group dev
uv run pytest
uv run ruff check .
```

直接前台运行服务：

```bash
# API
uv run uvicorn app.main:app --host 0.0.0.0 --port 8010

# Worker：Linux/macOS
uv run celery -A app.workers.celery_app.celery_app worker -Q heavy_tasks --loglevel=INFO

# Worker：Windows
uv run celery -A app.workers.celery_app.celery_app worker -Q heavy_tasks --loglevel=INFO --pool=solo
```

## 使用示例

### 上传 PDF 并投递 MinerU 解析

```bash
curl -X POST "http://127.0.0.1:8010/mineru/pdf" -F "file=@/path/demo.pdf"
```

Windows PowerShell 示例：

```powershell
curl.exe -X POST "http://127.0.0.1:8010/mineru/pdf" -F "file=@D:\path\demo.pdf"
```

### 查询任务状态

```bash
curl "http://127.0.0.1:8010/tasks/<task_id>"
```

### 通用任务投递接口

```bash
curl -X POST "http://127.0.0.1:8010/tasks" \
  -H "Content-Type: application/json" \
  -d '{"task_type":"mineru.pdf.parse","payload":{"input_path":"/path/demo.pdf"}}'
```

## 新增任务方式

1. 在 `app/tasks/` 新增一个独立任务模块，例如 `ocr_image.py`。
2. 在该模块中注册 Celery task，例如 `@celery_app.task(name="ocr.image.parse")`。
3. 在 `app/tasks/catalog.py` 增加 `task_type -> celery_name` 映射。
4. 如果新增了模块，需要在 `app/workers/celery_app.py` 的 `include` 列表中加入模块路径。

这样 API、队列服务和具体任务实现可以保持解耦。
