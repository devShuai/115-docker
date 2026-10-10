#!/usr/bin/env bash
set -euo pipefail
cd /usr/local/115Browser

# Root-mode Chromium still requires --no-sandbox.
extra_args=()
if [[ "${ENABLE_COOKIE_EXTENSION:-false}" == "true" ]]; then
  extra_args+=("--load-extension=${RUNTIME_DIR:-/run/115}/115Cookie")
fi

# Do not ignore TLS certificate errors or disable Chromium site isolation.
exec /usr/local/115Browser/115Browser \
  "--user-data-dir=${PROFILE_DIR_IN_CONTAINER:-/etc/115}" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --start-maximized \
  --lang=zh-CN \
  "${extra_args[@]}" "$@"
