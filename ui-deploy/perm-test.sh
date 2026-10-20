#!/bin/bash
# Runs INSIDE a throwaway ubuntu container (root there) to exercise the real install.sh + real uid/permission enforcement.
#   docker run --rm -v "$PWD":/proj:ro ubuntu:24.04 bash /proj/ui-deploy/perm-test.sh
set -u
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null && apt-get install -y -qq sudo python3 git passwd >/dev/null 2>&1
userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 admin
mkdir -p /home/admin/proj && cp -r /proj/. /home/admin/proj/ && echo "MUSE_TOKEN=secret-token-value-xyz" > /home/admin/proj/.env && chmod 600 /home/admin/proj/.env
chown -R admin:admin /home/admin/proj; chmod 755 /home/admin
MUSE_UI_SKIP_DOCKER=1 SUDO_USER=admin bash /home/admin/proj/ui-deploy/install.sh >/dev/null || { echo "INSTALL FAILED"; exit 1; }
[ -f /srv/muse-ui/workspace/static/index.html ] && [ "$(stat -c %U /srv/muse-ui/workspace/static/index.html)" = muse-ui ] && echo "workspace seeded by install, owned by muse-ui"; (cd /srv/muse-ui/workspace && runuser -u muse-ui -- git log --oneline | head -1)
runuser -u muse-deploy -- /opt/muse-ui/bin/deploy-priv --init >/tmp/init.out 2>&1 || { echo "INIT FAILED"; cat /tmp/init.out; exit 1; }
[ -f /srv/muse-ui/releases/.state.json ] && [ -f /srv/muse-ui/live/index.html ] && echo "init as muse-deploy OK (live populated, state written)"
as() { runuser -u muse-ui -- "$@"; }
pass=0; fail=0
expect_denied() { local d="$1"; shift; if as bash -c "$*" >/dev/null 2>&1; then echo "  [FAIL] NOT blocked: $d"; fail=$((fail+1)); else echo "  [PASS] blocked: $d"; pass=$((pass+1)); fi; }
expect_ok()     { local d="$1"; shift; if as bash -c "$*" >/dev/null 2>&1; then echo "  [PASS] allowed: $d"; pass=$((pass+1)); else echo "  [FAIL] should be allowed: $d"; fail=$((fail+1)); fi; }
echo "identity: $(as id)"
expect_ok     "edit workspace/static/app.css"          "echo '/* x */' >> /srv/muse-ui/workspace/static/app.css"
expect_ok     "create new workspace/static file"       "echo 'a{}' > /srv/muse-ui/workspace/static/extra.css"
expect_denied "edit backend app/main.py"               "echo '#' >> /home/admin/proj/app/main.py"
expect_denied "edit backend schema/db"                 "echo '#' >> /home/admin/proj/app/db.py"
expect_denied "edit docker-compose.yml"                "echo '#' >> /home/admin/proj/docker-compose.yml"
expect_denied "edit Dockerfile"                        "echo '#' >> /home/admin/proj/Dockerfile"
expect_denied "read .env (token)"                      "cat /home/admin/proj/.env"
expect_denied "edit deploy tool (deploy-priv)"         "echo '#' >> /opt/muse-ui/bin/deploy-priv"
expect_denied "replace wrapper muse-ui-deploy"         "echo '#' >> /usr/local/bin/muse-ui-deploy"
expect_denied "edit validator checks.py"               "echo '#' >> /opt/muse-ui/validator-ctx/checks.py"
expect_denied "edit validator image context"           "echo '#' >> /opt/muse-ui/validator-ctx/Dockerfile"
expect_denied "edit deploy config.json"                "echo '#' >> /opt/muse-ui/config.json"
expect_denied "edit sudoers rule"                      "echo 'muse-ui ALL=(ALL) NOPASSWD: ALL' >> /etc/sudoers.d/muse-ui"
expect_denied "create new sudoers file"                "echo 'muse-ui ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/evil"
expect_denied "write live/ directly (bypass deploy)"   "echo hacked > /srv/muse-ui/live/index.html"
expect_denied "write releases/ (known-good store)"     "mkdir /srv/muse-ui/releases/evil"
expect_denied "write state / reports"                  "touch /srv/muse-ui/releases/.state.json; touch /srv/muse-ui/reports/x"
expect_denied "become root via sudo"                   "sudo -n true"
expect_denied "sudo shell as muse-deploy"              "sudo -n -u muse-deploy /bin/sh -c id"
expect_denied "sudo arbitrary cmd as muse-deploy"      "sudo -n -u muse-deploy /usr/bin/python3 -c 'print(1)'"
expect_denied "pass env/config override via sudo"      "MUSE_UI_DEV=1 MUSE_UI_CONFIG=/tmp/x.json sudo -n -u muse-deploy /opt/muse-ui/bin/deploy-priv --status | grep -q /tmp/x.json"
expect_denied "in docker or sudo group"                "id -nG | grep -qwE 'docker|sudo'"
expect_ok     "run the sanctioned command (muse-ui-deploy --status)" "muse-ui-deploy --status"
echo "  -- allowlist enforcement by the deploy tool itself (real uids):"
as bash -c "ln -s /etc/passwd /srv/muse-ui/workspace/static/pw.css; echo x > /srv/muse-ui/workspace/evil.py; ln -s /home/admin/proj/.env /srv/muse-ui/workspace/static/secret.js"
out=$(as muse-ui-deploy 2>&1); echo "$out" | sed 's/^/    /'
echo "$out" | grep -q "REJECTED" && echo "$out" | grep -q "symlink not allowed: static/pw.css" && echo "$out" | grep -q "evil.py" && { echo "  [PASS] symlink + out-of-workspace file rejected"; pass=$((pass+1)); } || { echo "  [FAIL] rejection"; fail=$((fail+1)); }
echo "RESULT: $pass passed, $fail failed"; [ $fail = 0 ]
