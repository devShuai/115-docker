#!/bin/bash
# Exercises the multi-process browser (browser, zygote, renderer, GPU/utility) without a display.
set -euo pipefail
export BOX64_NOBANNER=1
start=$(date +%s)
timeout 420 box64 /usr/local/115Browser/115Browser --headless=new --no-sandbox --disable-gpu --no-first-run \
  --user-data-dir=/tmp/headless --disable-features=PartitionAllocUnretainedDanglingPtr \
  --dump-dom https://example.com >/w/.poc/headless.html 2>/w/.poc/headless.err || true
elapsed=$(( $(date +%s) - start ))
if grep -q 'Example Domain' /w/.poc/headless.html; then
  echo "RESULT: rendered example.com headless in ${elapsed}s ($(wc -c </w/.poc/headless.html) bytes of DOM)"
else
  echo "RESULT: no DOM after ${elapsed}s"
  tail -n 40 /w/.poc/headless.err
  exit 1
fi
