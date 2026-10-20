# Template: replace __USER__ (a member of the docker group) and __PROJECT_DIR__, then install as /etc/systemd/system/muse-control.service
[Unit]
Description=Muse Control dashboard (docker compose)
After=docker.service tailscaled.service network-online.target
Wants=network-online.target tailscaled.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=__USER__
Group=docker
WorkingDirectory=__PROJECT_DIR__
# wait (max 3 min) until the Tailscale address exists, otherwise the 100.x port bind fails at boot
ExecStartPre=/bin/sh -c 'for i in $(seq 1 90); do ip -4 addr show tailscale0 2>/dev/null | grep -q "inet 100\\." && exit 0; sleep 2; done; exit 0'
ExecStart=/usr/bin/docker compose up -d
ExecStop=/bin/true
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
