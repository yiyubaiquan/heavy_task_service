#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_PREFIX="${SERVICE_PREFIX:-heavy-task-service}"
API_SERVICE="${SERVICE_PREFIX}-api.service"
WORKER_SERVICE="${SERVICE_PREFIX}-worker.service"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"
LOG_DIR="$ROOT_DIR/logs"

mkdir -p "$SYSTEMD_USER_DIR" "$LOG_DIR" "$ROOT_DIR/run"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv was not found in PATH. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. Linux service mode requires systemd." >&2
  exit 1
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "systemd --user is not available in this shell. Try logging in normally or enabling user services." >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" && -f "$ENV_EXAMPLE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Created .env from .env.example. Please verify REDIS_URL before production use."
fi

sync_args=(--locked)
if [[ "${INSTALL_MINERU:-0}" == "1" ]]; then
  sync_args+=(--extra mineru)
fi
uv sync "${sync_args[@]}"

UV_BIN="$(command -v uv)"
CURRENT_PATH="$PATH"

cat > "$SYSTEMD_USER_DIR/$API_SERVICE" <<EOF
[Unit]
Description=Heavy Task Service API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=API_HOST=0.0.0.0
Environment=API_PORT=8010
Environment=PATH=$CURRENT_PATH
EnvironmentFile=-$ENV_FILE
ExecStart=$UV_BIN run uvicorn app.main:app --host \${API_HOST} --port \${API_PORT}
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
StandardOutput=append:$LOG_DIR/api.log
StandardError=append:$LOG_DIR/api.err.log

[Install]
WantedBy=default.target
EOF

cat > "$SYSTEMD_USER_DIR/$WORKER_SERVICE" <<EOF
[Unit]
Description=Heavy Task Service Celery Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT_DIR
Environment=PYTHONUNBUFFERED=1
Environment=CELERY_TASK_DEFAULT_QUEUE=heavy_tasks
Environment=PATH=$CURRENT_PATH
EnvironmentFile=-$ENV_FILE
ExecStart=$UV_BIN run celery -A app.workers.celery_app.celery_app worker -Q \${CELERY_TASK_DEFAULT_QUEUE} --loglevel=INFO
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=60
StandardOutput=append:$LOG_DIR/worker.log
StandardError=append:$LOG_DIR/worker.err.log

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$API_SERVICE" "$WORKER_SERVICE"

echo "Started systemd user services: $API_SERVICE, $WORKER_SERVICE"
systemctl --user --no-pager --full status "$API_SERVICE" "$WORKER_SERVICE" || true

if command -v loginctl >/dev/null 2>&1; then
  if ! loginctl show-user "${USER:-$(id -un)}" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    echo "Tip: to keep user services running after logout, run: sudo loginctl enable-linger ${USER:-$(id -un)}"
  fi
fi
