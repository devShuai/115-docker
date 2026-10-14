#!/bin/bash
set -euo pipefail
export BOX64_NOBANNER=1
start=$(date +%s)
out=$(timeout 300 box64 /usr/local/115Browser/115Browser --version 2>&1 | grep -v '^\[BOX64\]' | tail -n 3 || true)
echo "RESULT: 115Browser --version under box64: ${out:-<no output>} ($(( $(date +%s) - start ))s)"
[ -n "$out" ]
