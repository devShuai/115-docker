#!/usr/bin/env bash
# Runs inside the newly published image. All credentials and profiles are synthetic.
set -euo pipefail
export PASSWORD=Check123 AUTO_START=true ENABLE_COOKIE_EXTENSION=false
export DISPLAY=:115 DISPLAY_WIDTH=1600 DISPLAY_HEIGHT=900
export PROFILE_DIR_IN_CONTAINER=/tmp/115-ci-profile RUNTIME_DIR=/run/115
log_file=/tmp/115-ci-desktop.log
dbus-run-session -- /opt/115/start.sh >"$log_file" 2>&1 &
desktop_pid=$!
cleanup() {
  status=$?
  trap - EXIT
  kill -TERM "$desktop_pid" 2>/dev/null || true
  wait "$desktop_pid" 2>/dev/null || true
  if (( status != 0 )); then cat "$log_file" >&2; fi
  exit "$status"
}
trap cleanup EXIT
ready=false
for (( attempt=0; attempt<60; attempt++ )); do
  kill -0 "$desktop_pid" || { echo 'Desktop exited during CI.' >&2; exit 1; }
  if python3 /opt/115/healthcheck.py >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]] || { echo 'Desktop health check timed out.' >&2; exit 1; }
python3 scripts/ci-rfb.py
python3 - <<'PY'
from pathlib import Path
import time
import urllib.request
import json
for _ in range(30):
    browser = any(p.resolve().name == '115Browser' for p in Path('/proc').glob('[0-9]*/exe'))
    if browser:
        break
    time.sleep(1)
else:
    raise RuntimeError('115 Browser did not remain running')
with urllib.request.urlopen('http://127.0.0.1:1150/mandatory.json', timeout=5) as response:
    assert json.load(response)['resize'] == 'remote'
assert Path('/usr/share/backgrounds/115/desktop.png').stat().st_size > 0
print('PASS: browser process, desktop health, wallpaper, noVNC remote resize settings')
PY
