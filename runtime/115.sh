#!/usr/bin/env bash
set -euo pipefail
cd /usr/local/115Browser

# Root-mode Chromium still requires --no-sandbox. Chromium 125's --test-type
# skips startup infobars, including the unsupported-command-line warning.
extra_args=()
if [[ "${ENABLE_COOKIE_EXTENSION:-false}" == "true" ]]; then
  extra_args+=("--load-extension=${RUNTIME_DIR:-/run/115}/115Cookie")
fi
# The download API drives the logged-in browser through this port. Chromium binds
# it to 127.0.0.1 inside the container and Compose never publishes it.
if [[ "${ENABLE_DOWNLOAD_API:-true}" == "true" ]]; then
  extra_args+=("--remote-debugging-port=${BROWSER_DEBUG_PORT:-9222}")
fi

# 115 keeps its login (CID, SEID, UID, KID) in session cookies. Chromium writes
# them to the profile but deletes them at startup unless the last session is
# restored, so without this flag closing the browser or restarting the container
# logs 115 out. It also reopens the previous tabs.
# 115's download engine sometimes runs a callback for a request it has already
# freed after a network error. Chromium 125 then stops the whole browser with
# "Detected dangling raw_ptr in unretained": the check defaults to crashing, and
# only Chrome itself turns it into a report through Google's field trials, which
# a Chromium fork never receives. Downloads would otherwise kill the browser.
# Do not ignore TLS certificate errors or disable Chromium site isolation.
exec /usr/local/115Browser/115Browser \
  "--user-data-dir=${PROFILE_DIR_IN_CONTAINER:-/etc/115}" \
  --no-sandbox \
  --test-type \
  --no-first-run \
  --no-default-browser-check \
  --start-maximized \
  --restore-last-session \
  --disable-features=PartitionAllocUnretainedDanglingPtr \
  --lang=zh-CN \
  "${extra_args[@]}" "$@"
