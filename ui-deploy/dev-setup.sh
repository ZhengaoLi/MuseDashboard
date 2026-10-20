#!/bin/bash
# Admin-only dev harness: a throwaway "production" (port 5052) + deploy tool under ./.devroot, no root needed.
set -euo pipefail
P="$(cd "$(dirname "$0")/.." && pwd)"; D="$P/.devroot"
rm -rf "$D"; mkdir -p "$D/opt/validator-ctx/ref" "$D/srv"
cp "$P/ui-deploy/validator/Dockerfile" "$P/ui-deploy/validator/checks.py" "$D/opt/validator-ctx/"
cp -r "$P/app" "$P/tests" "$P/pytest.ini" "$D/opt/validator-ctx/ref/"; find "$D" -name __pycache__ -prune -exec rm -rf {} +
cp -r "$P/app/static" "$D/opt/initial-static"
cat > "$D/config.json" <<JSON
{"root":"$D/srv","docker":"/usr/bin/docker","home":"/tmp","prod_url":"http://127.0.0.1:5052","prod_image":"muse-control:latest",
 "validator_image":"muse-ui-validator","initial_static":"$D/opt/initial-static","preview_port":5053,"preview_binds":["127.0.0.1"],"tz":"America/New_York"}
JSON
docker build -q -t muse-ui-validator "$D/opt/validator-ctx" >/dev/null
export MUSE_UI_DEV=1 MUSE_UI_CONFIG="$D/config.json"
"$P/ui-deploy/bin/deploy-priv" --init
docker rm -f muse-dev-prod >/dev/null 2>&1 || true
docker run -d --name muse-dev-prod --user 10001:10001 -p 127.0.0.1:5052:5050 -e MUSE_TOKEN=devtoken-0123456789abcdef -e MUSE_HUMAN_TOKEN=devhuman-0123456789abcdef -e MUSE_DB=/tmp/dev.db \
  --tmpfs /tmp:rw,mode=1777 -v "$D/srv/live:/srv/app/static:ro" muse-control:latest >/dev/null
sleep 3; curl -s http://127.0.0.1:5052/api/health; echo
