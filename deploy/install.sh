#!/usr/bin/env bash
# Peygir — one-time server setup with automatic updates (continuous deployment).
#
#   curl -fsSL https://raw.githubusercontent.com/ali0083moi/Agent-New/release/deploy/install.sh -o /tmp/peygir-install.sh
#   sudo bash /tmp/peygir-install.sh
#
# What it does (idempotent, safe to re-run):
#   1. installs git/python tooling (and uv for a modern Python if needed)
#   2. finds an existing Peygir install (docker container or a running `app.main`), copies its .env and data
#      (the SQLite database is copied with the online-backup API), then stops it
#   3. checks out the `release` branch (built and tested by GitHub Actions) into /opt/peygir/app
#   4. runs the app as a systemd service (`peygir`) on the same port as before
#   5. installs a timer (`peygir-update.timer`) that checks GitHub every minute and deploys new releases
#      with a health check and automatic rollback
#
# Options (environment variables): PEYGIR_PORT, PEYGIR_REPO, PEYGIR_BRANCH, PEYGIR_HOME, PIP_INDEX_URL
set -euo pipefail

REPO="${PEYGIR_REPO:-https://github.com/ali0083moi/Agent-New.git}"
BRANCH="${PEYGIR_BRANCH:-release}"
HOME_DIR="${PEYGIR_HOME:-/opt/peygir}"
APP="$HOME_DIR/app"
SHARED="$HOME_DIR/shared"
VENV="$HOME_DIR/venv"
RUN_USER="peygir"

log() { printf '\033[1;35m[peygir]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[peygir] %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[peygir] %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
command -v systemctl >/dev/null || die "systemd is required"

# ------------------------------------------------------------------ 1) packages
if command -v apt-get >/dev/null; then
  log "installing packages…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq || warn "apt-get update failed (continuing)"
  apt-get install -y -qq git curl ca-certificates python3 python3-venv sqlite3 >/dev/null || warn "some packages failed to install"
elif command -v dnf >/dev/null; then
  dnf install -y -q git curl python3 sqlite >/dev/null || true
fi
command -v git >/dev/null || die "git is missing"

# ------------------------------------------------------------------ 2) find an existing install
mkdir -p "$SHARED/data" "$SHARED/backups"
OLD_DIR=""
OLD_KIND=""
OLD_PORT=""
OLD_DATA=""

if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  for c in $(docker ps -a --format '{{.Names}}'); do
    img="$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)"
    wd="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$c" 2>/dev/null || true)"
    if [[ "$c" == *peygir* || "$img" == *peygir* || "$img" == *agent-new* || "$wd" == *Agent-New* ]] \
      && [ -n "$wd" ] && [ -f "$wd/backend/app/main.py" ]; then
      OLD_DIR="$wd"; OLD_KIND="docker:$c"
      OLD_PORT="$(docker port "$c" 8000/tcp 2>/dev/null | head -1 | sed 's/.*://')"
      OLD_DATA="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' "$c" 2>/dev/null || true)"
      break
    fi
  done
fi

if [ -z "$OLD_DIR" ]; then
  for pid in $(pgrep -f 'app\.main' || true); do
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    [ -n "$cwd" ] || continue
    case "$cwd" in "$APP"*) continue ;; esac
    root="$(dirname "$cwd")"
    [ -f "$root/backend/app/main.py" ] || root="$cwd"
    if [ -f "$root/backend/app/main.py" ]; then
      OLD_DIR="$root"; OLD_KIND="process:$pid"
      OLD_PORT="$(ss -ltnpH 2>/dev/null | grep "pid=$pid," | awk '{print $4}' | sed 's/.*://' | head -1)"
      break
    fi
  done
fi

if [ -z "$OLD_DIR" ]; then
  for d in /root/Agent-New /opt/Agent-New /home/*/Agent-New /srv/Agent-New; do
    if [ -f "$d/backend/app/main.py" ]; then OLD_DIR="$d"; OLD_KIND="directory"; break; fi
  done
fi

if [ -n "$OLD_DIR" ]; then
  log "found an existing install: $OLD_DIR ($OLD_KIND, port ${OLD_PORT:-?})"
  [ -n "$OLD_DATA" ] || OLD_DATA="$OLD_DIR/data"
  if [ ! -f "$SHARED/.env" ] && [ -f "$OLD_DIR/.env" ]; then
    cp "$OLD_DIR/.env" "$SHARED/.env"
    log "copied .env"
  fi
  if [ ! -f "$SHARED/data/peygir.db" ] && [ -f "$OLD_DATA/peygir.db" ]; then
    python3 - "$OLD_DATA/peygir.db" "$SHARED/data/peygir.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
    cp "$SHARED/data/peygir.db" "$SHARED/backups/migrated-$(date +%Y%m%d-%H%M%S).db"
    log "copied the database (a backup is in $SHARED/backups)"
  fi
  if [ -d "$OLD_DATA/uploads" ]; then
    mkdir -p "$SHARED/data/uploads"
    cp -rn "$OLD_DATA/uploads/." "$SHARED/data/uploads/" 2>/dev/null || true
  fi
fi

if [ ! -f "$SHARED/.env" ]; then
  warn "no .env found — creating one from the template; edit $SHARED/.env (GAPGPT_API_KEY, TELEGRAM_BOT_TOKEN, APP_PASSWORD)"
  curl -fsSL "https://raw.githubusercontent.com/ali0083moi/Agent-New/$BRANCH/.env.example" -o "$SHARED/.env" || touch "$SHARED/.env"
fi

env_port="$(grep -E '^PORT=' "$SHARED/.env" | tail -1 | cut -d= -f2 | tr -d ' "' || true)"
PORT="${PEYGIR_PORT:-${OLD_PORT:-${env_port:-8000}}}"
echo "$PORT" > "$SHARED/port"

# ------------------------------------------------------------------ 3) code: the release branch
log "waiting for the '$BRANCH' branch on GitHub…"
for i in $(seq 1 60); do
  git ls-remote --exit-code --heads "$REPO" "$BRANCH" >/dev/null 2>&1 && break
  [ "$i" -eq 60 ] && die "branch '$BRANCH' not found — has the GitHub Actions CI finished successfully?"
  sleep 10
done
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin "$BRANCH"
  git -C "$APP" reset -q --hard "origin/$BRANCH"
else
  rm -rf "$APP"
  git clone -q --branch "$BRANCH" --single-branch "$REPO" "$APP"
fi
ln -sfn "$SHARED/.env" "$APP/.env"
log "code at $(git -C "$APP" log -1 --pretty='%h %s')"

# ------------------------------------------------------------------ 4) python environment
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$(command -v "$cand")"; break
  fi
done
if [ -z "$PY" ] || ! "$PY" -m venv --help >/dev/null 2>&1; then
  log "installing a modern Python with uv…"
  export UV_INSTALL_DIR=/usr/local/bin
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  /usr/local/bin/uv python install 3.12 >/dev/null
  PY="$(/usr/local/bin/uv python find 3.12)"
fi
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip >/dev/null || true
log "installing Python packages…"
"$VENV/bin/pip" install -q -r "$APP/backend/requirements.txt"
sha256sum "$APP/backend/requirements.txt" | cut -d' ' -f1 > "$SHARED/requirements.sha"

id "$RUN_USER" >/dev/null 2>&1 || useradd --system --home "$HOME_DIR" --shell /usr/sbin/nologin "$RUN_USER"
chown -R "$RUN_USER:$RUN_USER" "$SHARED"
chmod 600 "$SHARED/.env"

# ------------------------------------------------------------------ 5) stop the old instance
case "$OLD_KIND" in
  docker:*)
    c="${OLD_KIND#docker:}"
    log "stopping old container $c (it is kept, not deleted)"
    docker update --restart=no "$c" >/dev/null 2>&1 || true
    docker stop "$c" >/dev/null 2>&1 || true
    ;;
  process:*)
    pid="${OLD_KIND#process:}"
    unit="$(systemctl status "$pid" 2>/dev/null | head -1 | awk '{print $2}')"
    if [ -n "$unit" ] && [[ "$unit" == *.service ]] && [ "$unit" != "peygir.service" ]; then
      log "stopping and disabling old service $unit"
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
    else
      log "stopping old process $pid"
      kill "$pid" 2>/dev/null || true
      sleep 2
    fi
    ;;
esac

# ------------------------------------------------------------------ 6) systemd units
cat > /etc/systemd/system/peygir.service <<UNIT
[Unit]
Description=Peygir personal assistant
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP/backend
# the app reads $APP/.env (a symlink to $SHARED/.env); these paths win over anything in it
Environment=DATABASE_URL=sqlite:///$SHARED/data/peygir.db
Environment=UPLOAD_DIR=$SHARED/data/uploads
Environment=FRONTEND_DIST=$APP/frontend/dist
ExecStart=$VENV/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/peygir-update.service <<UNIT
[Unit]
Description=Deploy new Peygir releases from GitHub
After=network-online.target

[Service]
Type=oneshot
Environment=PEYGIR_HOME=$HOME_DIR
Environment=PEYGIR_BRANCH=$BRANCH
ExecStart=/bin/bash $APP/deploy/update.sh
UNIT

cat > /etc/systemd/system/peygir-update.timer <<UNIT
[Unit]
Description=Check GitHub for new Peygir releases every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now peygir.service >/dev/null
systemctl enable --now peygir-update.timer >/dev/null

# ------------------------------------------------------------------ 7) health check
log "waiting for the app on port $PORT…"
for i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/api/me" >/dev/null 2>&1; then
    log "✅ Peygir is running: $(git -C "$APP" log -1 --pretty='%h %s')"
    log "logs:      journalctl -u peygir -f"
    log "deploys:   journalctl -u peygir-update -f"
    log "settings:  $SHARED/.env  (then: systemctl restart peygir)"
    exit 0
  fi
  sleep 2
done
journalctl -u peygir -n 40 --no-pager || true
die "the app did not start — see the log above"
