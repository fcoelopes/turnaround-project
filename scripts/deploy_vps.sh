#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/turnaround-project}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8501/_stcore/health}"
EXPECTED_SHA="${1:-${DEPLOY_SHA:-}}"

cd "$APP_DIR"

if [[ -n "$EXPECTED_SHA" ]]; then
  ACTUAL_SHA="$(git rev-parse HEAD)"
  if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    echo "Commit incorreto antes do deploy: esperado $EXPECTED_SHA, atual $ACTUAL_SHA" >&2
    exit 1
  fi
  echo "Deploy confirmado para commit $ACTUAL_SHA"
fi

if [[ -x "$HOME/.local/bin/uv" ]]; then
  UV="$HOME/.local/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
else
  echo "uv não encontrado" >&2
  exit 1
fi

"$UV" sync --frozen

mkdir -p "$APP_DIR/data"
"$UV" run alembic upgrade head

if ! systemctl cat turnaround.service >/dev/null 2>&1; then
  echo "turnaround.service não está instalado. Consulte docs/deploy-automatico-vps.md." >&2
  exit 1
fi

sudo -n systemctl restart turnaround.service

for attempt in {1..20}; do
  if curl -fsS "$HEALTH_URL" >/dev/null; then
    echo "Deploy concluído: aplicação saudável em $HEALTH_URL"
    exit 0
  fi
  sleep 2
done

echo "Healthcheck falhou após o restart" >&2
systemctl status turnaround.service --no-pager || true
exit 1
