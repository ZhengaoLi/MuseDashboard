# Managed by muse-ui install. muse-ui may run exactly one program, as muse-deploy, with no shell and no env passthrough.
Defaults:muse-ui env_reset, !setenv, secure_path="/usr/sbin:/usr/bin:/sbin:/bin"
muse-ui ALL=(muse-deploy) NOPASSWD: /opt/muse-ui/bin/deploy-priv
