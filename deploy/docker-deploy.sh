#!/usr/bin/env bash
# Server side of push-to-deploy (.github/workflows/ci.yml, job "deploy").
#
# On every push that passes the tests, GitHub Actions uploads a git bundle of the pushed commit and this
# script over SSH, then runs:   bash deploy.sh run <sha> <bundle>
#
# It updates the code of the existing Docker install in place, rebuilds the image, recreates the container,
# waits until the new version answers, and otherwise switches back to the previous image.
#
# The install folder is found from the existing container (or set it with DEPLOY_PATH; a new server gets
# /opt/Agent-New). Kept across deploys: .env, data/ (the database is backed up before every deploy, the
# last 15 in data/backups) and a locally edited docker-compose.yml. Other local edits to tracked files are
# saved to .deploy/local-changes-*.patch before they are replaced.
set -euo pipefail

WORK="${DEPLOY_WORK:-/tmp/peygir-deploy}"
DOCKER="${DOCKER:-docker}"
HEALTH_TRIES="${DEPLOY_HEALTH_TRIES:-60}"

log() { echo "[deploy] $*"; }
die() { echo "[deploy] ❌ $*"; exit 1; }

# ------------------------------------------------------------------ entry: detach from the SSH session
run() {
  local sha="$1" bundle="$2"
  [ -n "$sha" ] && [ -f "$bundle" ] || { echo "usage: $0 run <sha> <bundle>"; exit 2; }
  mkdir -p "$WORK"
  # private copies, so the next upload can't change files a running deploy is still reading
  cp "$0" "$WORK/deploy-$sha.sh"
  cp "$bundle" "$WORK/repo-$sha.bundle"
  rm -f "$WORK/status-$sha"
  : > "$WORK/deploy-$sha.log"
  # the real work runs in its own session: a dropped SSH connection can't stop a deploy halfway
  setsid -w bash "$WORK/deploy-$sha.sh" work "$sha" "$WORK/repo-$sha.bundle" >> "$WORK/deploy-$sha.log" 2>&1 < /dev/null &
  tail --pid=$! -n +1 -f "$WORK/deploy-$sha.log"
  exit "$(cat "$WORK/status-$sha" 2>/dev/null || echo 1)"
}

# ------------------------------------------------------------------ helpers
find_app() {
  if [ -n "${DEPLOY_PATH:-}" ]; then echo "$DEPLOY_PATH"; return; fi
  local c wd d
  for c in $($DOCKER ps --format '{{.Names}}' 2>/dev/null) $($DOCKER ps -a --format '{{.Names}}' 2>/dev/null); do
    wd="$($DOCKER inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$c" 2>/dev/null || true)"
    if [ -n "$wd" ] && [ -f "$wd/backend/app/main.py" ] && [ -f "$wd/docker-compose.yml" ]; then echo "$wd"; return; fi
  done
  for d in /root/Agent-New /opt/Agent-New /srv/Agent-New /home/*/Agent-New /root/*/Agent-New; do
    if [ -f "$d/backend/app/main.py" ] && [ -f "$d/docker-compose.yml" ]; then echo "$d"; return; fi
  done
  echo /opt/Agent-New
}

sqlite_copy() {  # consistent copy of a database that may be open
  if command -v sqlite3 >/dev/null; then
    sqlite3 "$1" ".backup '$2'"
  elif command -v python3 >/dev/null; then
    python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "$1" "$2"
  else
    cp "$1" "$2"
  fi
}

running() { [ -n "$CID" ] && [ "$($DOCKER inspect -f '{{.State.Running}}' "$CID" 2>/dev/null)" = true ]; }

backup_db() {
  if [ ! -f data/peygir.db ]; then log "no data/peygir.db here yet — nothing to back up"; return 0; fi
  mkdir -p data/backups
  local name="peygir-$TS-$(printf %.7s "${PREV:-first}").db"
  if ! { running && "${COMPOSE[@]}" exec -T "$SERVICE" python -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' \
      /app/data/peygir.db "/app/data/backups/$name" 2>/dev/null; }; then
    sqlite_copy data/peygir.db "data/backups/$name" || { log "warning: could not back up the database"; return 0; }
  fi
  ls -1t data/backups/peygir-*.db 2>/dev/null | tail -n +16 | xargs -r rm -f
  log "database backed up: data/backups/$name"
}

# an earlier version of the deploy tooling ran the app with systemd from /opt/peygir; take its data back
from_systemd() {
  [ -f /etc/systemd/system/peygir.service ] || return 0
  log "found the earlier systemd install in /opt/peygir — moving back to Docker"
  systemctl disable --now peygir-update.timer peygir.service >/dev/null 2>&1 || true
  local db=/opt/peygir/shared/data/peygir.db
  if [ -f "$db" ] && { [ ! -f data/peygir.db ] || [ "$db" -nt data/peygir.db ]; }; then
    if running; then "${COMPOSE[@]}" stop "$SERVICE" >/dev/null 2>&1 || true; fi
    mkdir -p data/backups
    [ -f data/peygir.db ] && sqlite_copy data/peygir.db "data/backups/peygir-$TS-before-merge.db"
    sqlite_copy "$db" data/peygir.db.new
    rm -f data/peygir.db-wal data/peygir.db-shm
    mv data/peygir.db.new data/peygir.db
    log "brought over the newer database from /opt/peygir (old one kept in data/backups)"
  fi
  if [ ! -f .env ] && [ -f /opt/peygir/shared/.env ]; then cp /opt/peygir/shared/.env .env; fi
  if [ -d /opt/peygir/shared/data/uploads ]; then
    mkdir -p data/uploads
    cp -rn /opt/peygir/shared/data/uploads/. data/uploads/ 2>/dev/null || true
  fi
  rm -f /etc/systemd/system/peygir.service /etc/systemd/system/peygir-update.service /etc/systemd/system/peygir-update.timer
  systemctl daemon-reload >/dev/null 2>&1 || true
}

healthy() {  # $1: the version that must answer (short sha), or "" for any version
  local i
  for i in $(seq 1 "$HEALTH_TRIES"); do
    if "${COMPOSE[@]}" exec -T "$SERVICE" python -c '
import json, os, sys, urllib.request
d = json.load(urllib.request.urlopen("http://127.0.0.1:%s/api/me" % os.environ.get("PORT", "8000"), timeout=5))
sys.exit(0 if not sys.argv[1] or d.get("version") == sys.argv[1] else 3)' "${1:-}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

restore_code() {
  if [ -n "$PREV" ]; then g reset -q --hard "$PREV"; fi
  if [ "$KEEP_COMPOSE" = 1 ]; then cp .deploy/docker-compose.yml.local docker-compose.yml; fi
}

# ------------------------------------------------------------------ the deploy
work() {
  SHA="$1"; BUNDLE="$2"
  trap 'echo $? > "$WORK/status-$SHA"' EXIT
  exec 8>"$WORK/lock"
  flock -w 3600 8 || die "another deploy is still running"

  if $DOCKER compose version >/dev/null 2>&1; then COMPOSE=("$DOCKER" compose)
  elif command -v docker-compose >/dev/null; then COMPOSE=(docker-compose)
  else die "docker compose is not installed"; fi
  if ! command -v git >/dev/null; then
    log "installing git…"
    (apt-get update -qq && apt-get install -y -qq git) >/dev/null 2>&1 || die "git is missing and could not be installed"
  fi

  APP="$(find_app)"
  mkdir -p "$APP"
  cd "$APP"
  log "deploying ${SHA:0:7} into $APP"
  g() { git -c safe.directory="$APP" -c advice.detachedHead=false "$@"; }
  [ -d .git ] || { g init -q; log "first deploy into this folder: it is now a git checkout"; }
  PREV="$(g rev-parse -q --verify HEAD 2>/dev/null || true)"
  g fetch -q "$BUNDLE" "+refs/heads/deploy-tip:refs/deploy/incoming"
  [ "$(g rev-parse refs/deploy/incoming)" = "$SHA" ] || die "the uploaded bundle does not contain $SHA"
  SUBJECT="$(g log -1 --pretty=%s refs/deploy/incoming)"
  TS="$(date +%Y%m%d-%H%M%S)"

  # keep what was changed on the server
  mkdir -p .deploy
  KEEP_COMPOSE=0
  if [ -n "$PREV" ]; then
    if ! g diff --quiet HEAD; then
      g diff HEAD > ".deploy/local-changes-$TS.patch"
      log "local edits on the server were saved to .deploy/local-changes-$TS.patch"
    fi
    g diff --quiet HEAD -- docker-compose.yml || KEEP_COMPOSE=1
  elif [ -f docker-compose.yml ]; then
    g show refs/deploy/incoming:docker-compose.yml | cmp -s - docker-compose.yml || KEEP_COMPOSE=1
  fi
  if [ "$KEEP_COMPOSE" = 1 ]; then cp docker-compose.yml .deploy/docker-compose.yml.local; fi

  # the running container (for the database backup and for rollback)
  SERVICE="$("${COMPOSE[@]}" config --services 2>/dev/null | head -1 || true)"
  SERVICE="${SERVICE:-peygir}"
  CID="$("${COMPOSE[@]}" ps -a -q "$SERVICE" 2>/dev/null | head -1 || true)"
  [ -n "$CID" ] || CID="$("${COMPOSE[@]}" ps -q "$SERVICE" 2>/dev/null | head -1 || true)"
  OLD_IMAGE=""
  IMAGE_NAME=""
  if [ -n "$CID" ]; then
    OLD_IMAGE="$($DOCKER inspect -f '{{.Image}}' "$CID" 2>/dev/null || true)"
    IMAGE_NAME="$($DOCKER inspect -f '{{.Config.Image}}' "$CID" 2>/dev/null || true)"
  fi

  from_systemd
  backup_db

  g reset -q --hard refs/deploy/incoming
  if [ "$KEEP_COMPOSE" = 1 ]; then
    cp .deploy/docker-compose.yml.local docker-compose.yml
    log "kept the server's own docker-compose.yml"
  fi
  if [ ! -f .env ]; then
    cp .env.example .env
    log "⚠️ there was no .env — created one from .env.example; fill in $APP/.env and push again"
  fi

  log "building the image…"
  if ! "${COMPOSE[@]}" build --build-arg GIT_SHA="$SHA" --build-arg GIT_SUBJECT="$SUBJECT"; then
    restore_code
    die "the image did not build; the running version was not touched"
  fi

  log "restarting the container…"
  if "${COMPOSE[@]}" up -d --force-recreate --no-deps "$SERVICE" && "${COMPOSE[@]}" up -d && healthy "${SHA:0:7}"; then
    log "✅ now running ${SHA:0:7}: $SUBJECT"
    $DOCKER image prune -f >/dev/null 2>&1 || true
    find "$WORK" -maxdepth 1 \( -name 'repo-*.bundle' -o -name 'deploy-*.sh' \) -mmin +60 -delete 2>/dev/null || true
    exit 0
  fi

  log "❌ the new version did not come up. Last log lines:"
  "${COMPOSE[@]}" logs --tail 80 "$SERVICE" 2>&1 || true
  restore_code
  if [ -n "$OLD_IMAGE" ] && [ -n "$IMAGE_NAME" ]; then
    log "switching back to the previous image…"
    $DOCKER tag "$OLD_IMAGE" "$IMAGE_NAME"
    "${COMPOSE[@]}" up -d --force-recreate --no-deps --no-build "$SERVICE" || true
    if healthy ""; then log "rolled back: the previous version is running again"
    else log "the previous version does not answer either — check: cd $APP && docker compose logs"; fi
  fi
  exit 1
}

main() {
  case "${1:-}" in
    run) run "${2:-}" "${3:-}" ;;
    work) work "$2" "$3" ;;
    *) echo "usage: $0 run <sha> <bundle>"; exit 2 ;;
  esac
}
main "$@"
