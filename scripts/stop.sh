#!/usr/bin/env bash
set -euo pipefail

SERVICE_PREFIX="${SERVICE_PREFIX:-heavy-task-service}"
API_SERVICE="${SERVICE_PREFIX}-api.service"
WORKER_SERVICE="${SERVICE_PREFIX}-worker.service"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl was not found. Linux service mode requires systemd." >&2
  exit 1
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "systemd --user is not available in this shell." >&2
  exit 1
fi

systemctl --user stop "$WORKER_SERVICE" "$API_SERVICE" || true

echo "Stopped systemd user services: $WORKER_SERVICE, $API_SERVICE"

if [[ "${1:-}" == "--disable" ]]; then
  systemctl --user disable "$WORKER_SERVICE" "$API_SERVICE" || true
  echo "Disabled systemd user services."
fi
