#!/bin/bash
# The way the image runs it: a real X display, DevTools on 127.0.0.1, same flags as runtime/115.sh.
set -euo pipefail
export DISPLAY=:99 BOX64_NOBANNER=1
setsid nohup Xvfb :99 -screen 0 1600x900x24 -nolisten tcp >/w/.poc/xvfb.log 2>&1 &
sleep 2
setsid nohup box64 /usr/local/115Browser/115Browser --user-data-dir=/tmp/profile --no-sandbox --test-type \
  --no-first-run --no-default-browser-check --start-maximized --remote-debugging-port=9222 --lang=zh-CN \
  --disable-features=PartitionAllocUnretainedDanglingPtr https://115.com/ >/w/.poc/browser.log 2>&1 &
python3 poc/arm64/desktop.py
