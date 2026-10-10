#!/usr/bin/env bash
set -euo pipefail
umask 077

export DISPLAY="${DISPLAY:-:115}"
export RUNTIME_DIR="${RUNTIME_DIR:-/run/115}"
export XDG_RUNTIME_DIR="$RUNTIME_DIR"
export PROFILE_DIR_IN_CONTAINER="${PROFILE_DIR_IN_CONTAINER:-/etc/115}"
export DISPLAY_WIDTH="${DISPLAY_WIDTH:-1600}"
export DISPLAY_HEIGHT="${DISPLAY_HEIGHT:-900}"
export AUTO_START="${AUTO_START:-true}"
export ENABLE_COOKIE_EXTENSION="${ENABLE_COOKIE_EXTENSION:-false}"

[[ "${PASSWORD:-}" =~ ^[A-Za-z0-9]{8}$ ]] || {
  echo 'PASSWORD must contain exactly 8 ASCII letters or digits.' >&2; exit 1;
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

mkdir -p "$RUNTIME_DIR" "$PROFILE_DIR_IN_CONTAINER"
chmod 700 "$RUNTIME_DIR"
printf '%s\n' "$PASSWORD" | tigervncpasswd -f >"$RUNTIME_DIR/passwd"
chmod 600 "$RUNTIME_DIR/passwd"
unset PASSWORD
python3 "${CONFIGURE_SCRIPT:-/opt/115/configure.py}"
# A profile must have only one owner process. Remove locks from a stopped instance.
rm -f "$PROFILE_DIR_IN_CONTAINER/SingletonLock" \
      "$PROFILE_DIR_IN_CONTAINER/SingletonSocket" \
      "$PROFILE_DIR_IN_CONTAINER/SingletonCookie"

all_pids=()
critical_pids=()
cleanup() {
  trap - EXIT TERM INT
  if (( ${#all_pids[@]} )); then
    kill -TERM "${all_pids[@]}" 2>/dev/null || true
    wait "${all_pids[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
spawn_critical() {
  local name="$1"; shift
  "$@" &
  all_pids+=("$!"); critical_pids+=("$!")
  echo "Started $name (PID $!)."
}

vnc_port=$((5900 + 10#${DISPLAY#:}))
spawn_critical TigerVNC Xtigervnc "$DISPLAY" \
  -geometry "${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" -depth 24 \
  -rfbport "$vnc_port" -localhost -nolisten tcp \
  -SecurityTypes VncAuth -PasswordFile "$RUNTIME_DIR/passwd" \
  -AcceptSetDesktopSize=1 -AlwaysShared -desktop '115 Desktop'

ready=false
for (( attempt=0; attempt<100; attempt++ )); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then ready=true; break; fi
  kill -0 "${critical_pids[0]}" 2>/dev/null || {
    echo 'TigerVNC exited during startup.' >&2; exit 1;
  }
  sleep 0.2
done
[[ "$ready" == true ]] || { echo 'X11 did not become ready within 20 seconds.' >&2; exit 1; }

spawn_critical Openbox openbox --config-file "${OPENBOX_CONFIG:-/opt/.config/openbox/rc.xml}"
spawn_critical Desktop pcmanfm --desktop --profile default
spawn_critical Panel tint2 -c "${TINT2_CONFIG:-/opt/.config/tint2/tint2rc}"
spawn_critical noVNC "${WEBSOCKIFY_RUN:-/usr/local/lib/websockify/run}" \
  --web "${NO_VNC_HOME:-/usr/local/share/noVNC}" 1150 "127.0.0.1:$vnc_port"

if [[ "$AUTO_START" == true ]]; then
  "${BROWSER_LAUNCHER:-/opt/115/115.sh}" &
  all_pids+=("$!")
  echo "Started 115 Browser (PID $!)."
fi

# A failed desktop service must stop the container so Docker can restart it.
# Closing the browser manually is allowed; the desktop remains available.
set +e
wait -n "${critical_pids[@]}"
status=$?
set -e
echo "A desktop service exited (status $status); shutting down." >&2
exit 1
