#!/bin/bash
# One-time privileged install of the Muse frontend sandbox.  Run as:  sudo bash ui-deploy/install.sh
# Safe to re-run (idempotent). Does NOT touch the database, the Docker volume, tokens or other services.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"; PROJ="$(dirname "$HERE")"
OPT=${MUSE_UI_OPT:-/opt/muse-ui}; ROOT=${MUSE_UI_ROOT:-/srv/muse-ui}; SUDOERS=${MUSE_UI_SUDOERS:-/etc/sudoers.d/muse-ui}; BIN=${MUSE_UI_BIN:-/usr/local/bin}
ADMIN="${SUDO_USER:-root}"; TS_IP="${MUSE_UI_TS_IP:-$(command -v tailscale >/dev/null && tailscale ip -4 2>/dev/null | head -1 || true)}"; TS_IP="${TS_IP:-127.0.0.1}"
SKIP_DOCKER=${MUSE_UI_SKIP_DOCKER:-0}

id muse-ui >/dev/null 2>&1 || useradd --system --create-home --home-dir "$ROOT/home" --shell /bin/bash --user-group muse-ui
id muse-deploy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin --user-group muse-deploy
passwd -l muse-ui >/dev/null; passwd -l muse-deploy >/dev/null
[ "$SKIP_DOCKER" = 1 ] || usermod -aG docker muse-deploy          # muse-deploy only; muse-ui is NOT in docker/sudo groups
gpasswd -d muse-ui docker >/dev/null 2>&1 || true; gpasswd -d muse-ui sudo >/dev/null 2>&1 || true

# --- protected code & config (root-owned, read-only to everyone else)
install -d -m 755 -o root -g root "$OPT" "$OPT/bin" "$OPT/validator-ctx" "$OPT/initial-static"
install -m 755 -o root -g root "$HERE/bin/deploy-priv" "$OPT/bin/deploy-priv"
install -m 755 -o root -g root "$HERE/bin/muse-ui-deploy" "$BIN/muse-ui-deploy"
sed "s/__TAILSCALE_IP__/$TS_IP/; s#\"/srv/muse-ui\"#\"$ROOT\"#" "$HERE/config.json.tpl" > "$OPT/config.json"; chown root:root "$OPT/config.json"; chmod 644 "$OPT/config.json"
rm -rf "$OPT/validator-ctx"; mkdir -p "$OPT/validator-ctx/ref"
cp "$HERE/validator/Dockerfile" "$HERE/validator/checks.py" "$OPT/validator-ctx/"
cp -r "$PROJ/app" "$PROJ/client" "$PROJ/tests" "$PROJ/pytest.ini" "$OPT/validator-ctx/ref/"
rm -rf "$OPT/validator-ctx/ref/app/__pycache__" "$OPT/validator-ctx/ref/client/__pycache__" "$OPT/validator-ctx/ref/tests/__pycache__"
rm -rf "$OPT/initial-static"; mkdir -p "$OPT/initial-static"; cp -r "$PROJ/app/static/." "$OPT/initial-static/"
chown -R root:root "$OPT"; chmod -R go-w "$OPT"; chmod -R a+rX "$OPT"

# --- state tree: workspace is the ONLY thing muse-ui can write
install -d -m 755 -o root -g root "$ROOT"
install -d -m 755 -o muse-deploy -g muse-deploy "$ROOT/live" "$ROOT/releases" "$ROOT/reports" "$ROOT/staging"
install -d -m 755 -o muse-ui -g muse-ui "$ROOT/workspace"
install -d -m 750 -o muse-ui -g muse-ui "$ROOT/home"

# --- sudoers (validated before install)
tmp=$(mktemp); cp "$HERE/sudoers.tpl" "$tmp"; sed -i "s#/opt/muse-ui#$OPT#" "$tmp"
visudo -cf "$tmp" >/dev/null; install -m 440 -o root -g root "$tmp" "$SUDOERS"; rm -f "$tmp"

# seed the workspace (as muse-ui, so it owns the files) and commit to its own git history
if [ ! -d "$ROOT/workspace/static" ]; then
  sudo -u muse-ui cp -r "$OPT/initial-static" "$ROOT/workspace/static"
fi
if [ ! -d "$ROOT/workspace/.git" ]; then
  sudo -u muse-ui sh -c "cd '$ROOT/workspace' && git init -q && printf 'Frontend workspace. Edit static/ only. See MUSE-UI.md.\n' > README.md"
fi
sudo -u muse-ui sh -c "cd '$ROOT/workspace' && git add -A && (git diff --cached --quiet || git -c user.name=muse -c user.email=muse@localhost commit -qm 'seed frontend workspace')"

if [ "$SKIP_DOCKER" != 1 ]; then
  docker build -q -t muse-ui-validator "$OPT/validator-ctx" >/dev/null
  sudo -n -u muse-deploy "$OPT/bin/deploy-priv" --init
  # mount the live dir (read-only) into the dashboard container via a compose overlay; persisted in .env
  grep -q '^COMPOSE_FILE=' "$PROJ/.env" || echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.ui.yml' >> "$PROJ/.env"
  chown "$ADMIN" "$PROJ/.env"
  sudo -u "$ADMIN" -H bash -c "cd '$PROJ' && docker compose up -d" || echo "run: cd $PROJ && docker compose up -d"
fi
echo "Installed. Muse should run as user 'muse-ui' (e.g. sudo -u muse-ui -i). Workspace: $ROOT/workspace  Command: muse-ui-deploy"
