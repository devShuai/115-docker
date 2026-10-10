#!/bin/sh
# Run from the full checked-out project as root. Credentials stay in Docker's config.
set -eu
umask 077
test "$(id -u)" = 0 || { echo 'Run this installer with sudo.' >&2; exit 1; }
app_root=${1:-/volume2/docker/115-docker-manager}
config_source=${2:-}
case "$app_root" in /*) ;; *) echo 'Absolute install path required.' >&2; exit 1 ;; esac
case "$app_root" in *[!a-zA-Z0-9_./-]*) echo 'Unsupported install path.' >&2; exit 1 ;; esac
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test -x /usr/bin/python3
test -x /usr/local/bin/docker
mkdir -p "$app_root/state"
chmod 700 "$app_root" "$app_root/state"
stage="$app_root/app-new"
test ! -e "$stage" || { echo 'Staging directory already exists; inspect before retry.' >&2; exit 1; }
mkdir -p "$stage/manager" "$stage/scripts"
cp "$project_root"/manager/*.py "$project_root"/manager/*.html "$project_root"/manager/*.js "$project_root"/manager/*.css "$stage/manager/"
cp "$project_root/scripts/check-update.py" "$stage/scripts/"
if [ -n "$config_source" ]; then
  cp "$config_source" "$app_root/config.json.new"
elif [ ! -f "$app_root/config.json" ]; then
  /usr/bin/python3 - "$app_root" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])
config={'bind':'127.0.0.1','port':11502,'allowed_hosts':['127.0.0.1','localhost'],
        'docker':'/usr/local/bin/docker','container':'115-docker',
        'image':'registry.devshuai.com/shuai-repo/115-docker:latest',
        'state_dir':str(root/'state'),'desktop_url':'http://127.0.0.1:11501/',
        'synology_manager_dir':'/var/packages/ContainerManager/etc'}
(root/'config.json.new').write_text(json.dumps(config,indent=2))
PY
fi
if [ -f "$app_root/config.json.new" ]; then
  /usr/bin/python3 -m json.tool "$app_root/config.json.new" >/dev/null
fi
unit=/usr/local/lib/systemd/system/115-docker-manager.service
if [ -f "$unit" ]; then
  systemctl stop 115-docker-manager.service
fi
if [ -d "$app_root/app" ]; then
  mv "$app_root/app" "$app_root/app-before-$(date +%Y%m%d-%H%M%S)"
fi
mv "$stage" "$app_root/app"
if [ -f "$app_root/config.json.new" ]; then
  if [ -f "$app_root/config.json" ]; then
    cp "$app_root/config.json" "$app_root/config-before-$(date +%Y%m%d-%H%M%S).json"
  fi
  mv "$app_root/config.json.new" "$app_root/config.json"
fi
chmod 600 "$app_root/config.json"
cat >"$unit" <<EOF
[Unit]
Description=115 Docker update manager
After=network.target pkgctl-ContainerManager.service

[Service]
Type=simple
User=root
WorkingDirectory=$app_root/app/manager
ExecStart=/usr/bin/python3 -u $app_root/app/manager/server.py --config $app_root/config.json
Restart=on-failure
RestartSec=5
TimeoutStopSec=900
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$unit"
systemctl daemon-reload
systemctl enable 115-docker-manager.service
systemctl start 115-docker-manager.service
systemctl is-active 115-docker-manager.service
