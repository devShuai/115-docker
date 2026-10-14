#!/bin/bash
set -euo pipefail
. poc/arm64/box64-env.sh
start=$(date +%s)
out=$(timeout 300 box64 /usr/local/115Browser/115Browser --version 2>&1 | grep -v '^\[BOX64\]' | tail -n 3 || true)
echo "RESULT: 115Browser --version under box64: ${out:-<no output>} ($(( $(date +%s) - start ))s)"
# A crash prints a glibc or box64 message instead of a version.
grep -q -E "[0-9]+\.[0-9]+" <<<"$out" && ! grep -q -i -E "invalid pointer|abort|segmentation|error" <<<"$out"
