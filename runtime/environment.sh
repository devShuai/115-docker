#!/usr/bin/env bash
# Shared by the container entrypoint and desktop supervisor.
export DISPLAY="${DISPLAY:-:115}"
export RUNTIME_DIR="${RUNTIME_DIR:-/run/115}"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export PROFILE_DIR_IN_CONTAINER="${PROFILE_DIR_IN_CONTAINER:-/etc/115}"
export DISPLAY_WIDTH="${DISPLAY_WIDTH:-1600}"
export DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-900}"
export AUTO_START="${AUTO_START:-true}"
export ENABLE_COOKIE_EXTENSION="${ENABLE_COOKIE_EXTENSION:-false}"

[[ "${PASSWORD:-}" =~ ^[A-Za-z0-9]{8}$ ]] || {
  echo 'PASSWORD must contain exactly 8 ASCII letters or digits. For Docker Compose, set VNC_PASSWORD in .env; for docker run or a container UI, set PASSWORD.' >&2
  exit 1
}
[[ "$DISPLAY" =~ ^:[0-9]+$ ]] || { echo 'Invalid DISPLAY.' >&2; exit 1; }
for size in "$DISPLAY_WIDTH" "$DISPLAY_HEIGHT"; do
  [[ "$size" =~ ^[1-9][0-9]{2,3}$ ]] && (( 10#$size <= 8192 )) || {
    echo 'Initial display dimensions must be between 100 and 8192.' >&2; exit 1;
  }
done
for flag in "$AUTO_START" "$ENABLE_COOKIE_EXTENSION"; do
  [[ "$flag" == true || "$flag" == false ]] || {
    echo 'AUTO_START and ENABLE_COOKIE_EXTENSION must be true or false.' >&2; exit 1;
  }
done

# D-Bus reads XDG_RUNTIME_DIR at startup, before the supervisor is launched.
mkdir -p "$RUNTIME_DIR" "$PROFILE_DIR_IN_CONTAINER"
chmod 700 "$RUNTIME_DIR"
