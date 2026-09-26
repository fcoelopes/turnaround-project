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

UNIT_SOURCE="$APP_DIR/deploy/systemd/turnaround.service"
UNIT_TARGET="/etc/systemd/system/turnaround.service"

if [[ ! -f "$UNIT_SOURCE" ]]; then
  echo "Unit file versionado não encontrado: $UNIT_SOURCE" >&2
  exit 1
fi

# O unit file faz parte da versão publicada. Instale-o antes do restart para
# manter o ExecStart sincronizado com eventuais renomes do entrypoint.
sudo -n install -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
sudo -n systemctl daemon-reload

if ! systemctl cat turnaround.service >/dev/null 2>&1; then
  echo "turnaround.service não pôde ser carregado após a instalação." >&2
  exit 1
fi

# Impede que uma sessão Streamlit antiga dispute a migration com o deploy.
sudo -n systemctl stop turnaround.service

mkdir -p "$APP_DIR/data"
"$UV" run python -c "from turnaround.persistence import upgrade_database; upgrade_database()"

sudo -n systemctl start turnaround.service

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
