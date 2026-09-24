#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/turnaround-project}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8501/_stcore/health}"

cd "$APP_DIR"

if [[ -x "$HOME/.local/bin/uv" ]]; then
  UV="$HOME/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  echo "uv não encontrado" >&2
  exit 1
fi

"$UV" sync --frozen

sudo -n systemctl restart turnaround.service

for attempt in {1..20}; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then
    echo "Deploy concluído: aplicação saudável em $HEALTH_URL"
    exit 0
  fi
  sleep 2
done

echo "Healthcheck falhou após o restart" >&2
sudo -n systemctl status turnaround.service --no-pager || true
exit 1
