#!/bin/bash
set -euo pipefail
. /etc/os-release
echo "RESULT: ${PRETTY_NAME}, dpkg arch $(dpkg --print-architecture), kernel $(uname -r), $(nproc) CPUs"
echo "RESULT: CPU: $(grep -m1 -i -E 'model name|CPU part' /proc/cpuinfo | cut -d: -f2- | xargs) / features: $(grep -m1 -i '^Features' /proc/cpuinfo | cut -d: -f2- | wc -w) flags"
echo "RESULT: memory $(awk '/MemTotal/ {printf "%.1f GiB", $2 / 1048576}' /proc/meminfo), page size $(getconf PAGESIZE)"
