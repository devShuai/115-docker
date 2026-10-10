#!/usr/bin/env bash
set -euo pipefail
umask 077
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/environment.sh"
python3 "$script_dir/prepare-display.py"
exec dbus-run-session -- "$script_dir/start.sh" "$@"
