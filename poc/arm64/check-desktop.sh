#!/bin/bash
# The way the image runs it: a real X display, DevTools on 127.0.0.1, same flags as runtime/115.sh,
# with the first box64 configuration that rendered headless.
set -euo pipefail
export DISPLAY=:99
. poc/arm64/box64-env.sh
echo "RESULT: configuration: $(cat /w/.poc/winner.name)"
setsid nohup Xvfb :99 -screen 0 1600x900x24 -nolisten tcp >/w/.poc/xvfb.log 2>&1 &
sleep 2
# shellcheck disable=SC2046 # word lists on purpose
setsid nohup env $(cat /w/.poc/winner.env) box64 /usr/local/115Browser/115Browser --user-data-dir=/tmp/profile \
  --no-sandbox --test-type --no-first-run --no-default-browser-check --start-maximized --remote-debugging-port=9222 \
  --lang=zh-CN --disable-features=PartitionAllocUnretainedDanglingPtr $(cat /w/.poc/winner.flags) https://115.com/ \
  >/w/.poc/browser.log 2>&1 &
python3 poc/arm64/desktop.py
