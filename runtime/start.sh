#!/usr/bin/env bash
set -euo pipefail
umask 077

source "$(dirname -- "${BASH_SOURCE[0]}")/environment.sh"
if [[ "$REMOTE_BACKEND" == kasm ]]; then
  printf '%s\n%s\n' "$PASSWORD" "$PASSWORD" | \
    kasmvncpasswd -u "$REMOTE_USER" -r -w -o "$RUNTIME_DIR/passwd"
else
  printf '%s\n' "$PASSWORD" | tigervncpasswd -f >"$RUNTIME_DIR/passwd"
fi
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
if [[ "$REMOTE_BACKEND" == kasm ]]; then
  # The native web endpoint replaces the separate Python Websockify relay.
  # Basic authentication remains required. Publish through HTTPS or an SSH tunnel.
  spawn_critical KasmVNC Xkasmvnc "$DISPLAY" \
    -geometry "${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" -depth 24 -nolisten tcp \
    -rfbport -1 -websocketPort 1150 -interface 0.0.0.0 \
    -httpd /usr/share/kasmvnc/www -sslOnly 0 \
    -KasmPasswordFile "$RUNTIME_DIR/passwd" -SecurityTypes None \
    -AcceptSetDesktopSize=1 -AlwaysShared -desktop '115 Desktop' \
    -FrameRate "$VNC_FPS" -RectThreads "$KASM_THREADS" \
    -webpEncodingTime "$KASM_WEBP_BUDGET" \
    -PreferBandwidth 1 -DynamicQualityMin 6 -DynamicQualityMax 8 \
    -IgnoreClientSettingsKasm 1 -PublicIP 127.0.0.1
else
  spawn_critical TigerVNC Xtigervnc "$DISPLAY" \
    -geometry "${DISPLAY_WIDTH}x${DISPLAY_HEIGHT}" -depth 24 \
    -rfbport "$vnc_port" -localhost -nolisten tcp \
    -SecurityTypes VncAuth -PasswordFile "$RUNTIME_DIR/passwd" \
    -AcceptSetDesktopSize=1 -AlwaysShared -FrameRate "$VNC_FPS" -desktop '115 Desktop'
fi

ready=false
for (( attempt=0; attempt<100; attempt++ )); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then ready=true; break; fi
  kill -0 "${critical_pids[0]}" 2>/dev/null || {
    echo 'Display server exited during startup.' >&2; exit 1;
  }
  sleep 0.2
done
[[ "$ready" == true ]] || { echo 'X11 did not become ready within 20 seconds.' >&2; exit 1; }

# Xfce runs without its session manager so this supervisor sees every part.
# The window manager starts first; the panel and desktop wait for it.
spawn_critical 'Window manager' xfwm4 --compositor=off
spawn_critical Settings xfsettingsd
spawn_critical Panel xfce4-panel
spawn_critical Desktop xfdesktop
if [[ "$REMOTE_BACKEND" == legacy ]]; then
  spawn_critical noVNC "${WEBSOCKIFY_RUN:-/usr/local/lib/websockify/run}" \
    --web "${NO_VNC_HOME:-/usr/local/share/noVNC}" 1150 "127.0.0.1:$vnc_port"
fi

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
