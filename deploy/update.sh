#!/usr/bin/env bash
# Deploys the newest commit of the release branch (run every minute by peygir-update.timer).
# Health-checks the new version and rolls back automatically if it doesn't come up.
set -euo pipefail

HOME_DIR="${PEYGIR_HOME:-/opt/peygir}"
BRANCH="${PEYGIR_BRANCH:-release}"
APP="$HOME_DIR/app"
SHARED="$HOME_DIR/shared"
VENV="$HOME_DIR/venv"
SERVICE="${PEYGIR_SERVICE:-peygir}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
HEALTH_TRIES="${PEYGIR_HEALTH_TRIES:-45}"
PORT="$(cat "$SHARED/port" 2>/dev/null || echo 8000)"

log() { echo "[peygir-update] $*"; }

exec 9>"$SHARED/update.lock"
flock -n 9 || exit 0

cd "$APP"
git fetch -q origin "$BRANCH" || { log "fetch failed (network?)"; exit 0; }
current="$(git rev-parse HEAD)"
target="$(git rev-parse "origin/$BRANCH")"
[ "$current" = "$target" ] && exit 0
if [ "$(cat "$SHARED/bad-release" 2>/dev/null || true)" = "$target" ]; then
  exit 0  # this release already failed its health check; wait for the next one
fi

log "deploying $(git log -1 --pretty='%h %s' "$target") (was ${current:0:7})"

healthy() {
  for _ in $(seq 1 "$HEALTH_TRIES"); do
    curl -fsS "http://127.0.0.1:$PORT/api/me" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

install_requirements() {
  local sum
  sum="$(sha256sum backend/requirements.txt | cut -d' ' -f1)"
  if [ "$sum" != "$(cat "$SHARED/requirements.sha" 2>/dev/null || true)" ]; then
    log "installing changed Python requirements…"
    "$VENV/bin/pip" install -q -r backend/requirements.txt
    echo "$sum" > "$SHARED/requirements.sha"
  fi
}

# keep a copy of the database before every deploy (last 15)
if [ -f "$SHARED/data/peygir.db" ]; then
  "$VENV/bin/python" - "$SHARED/data/peygir.db" "$SHARED/backups/peygir-$(date +%Y%m%d-%H%M%S)-${current:0:7}.db" <<'PY' || true
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
  ls -1t "$SHARED"/backups/peygir-*.db 2>/dev/null | tail -n +16 | xargs -r rm -f
fi

git reset -q --hard "$target"
if install_requirements && $SYSTEMCTL restart "$SERVICE" 9>&- && healthy; then
  rm -f "$SHARED/bad-release"
  log "✅ now running $(git log -1 --pretty='%h %s')"
  exit 0
fi

log "❌ new version is unhealthy — rolling back to ${current:0:7}"
echo "$target" > "$SHARED/bad-release"
git reset -q --hard "$current"
install_requirements || true
$SYSTEMCTL restart "$SERVICE" 9>&- || true
if healthy; then log "rolled back to ${current:0:7}"; else log "rollback is unhealthy too — check: journalctl -u $SERVICE"; fi
exit 1
