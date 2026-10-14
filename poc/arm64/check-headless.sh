#!/bin/bash
# Tries each box64 configuration on a headless render (browser, zygote, renderer and
# utility processes, no display). The first one that renders is used by the later stages.
set -uo pipefail
. poc/arm64/box64-env.sh
rm -f /w/.poc/winner.*
passed=0
while IFS='|' read -r name vars flags limit; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  start=$(date +%s)
  # shellcheck disable=SC2086 # vars and flags are word lists on purpose
  env $vars timeout "$limit" box64 /usr/local/115Browser/115Browser --headless=new --no-sandbox --disable-gpu \
    --no-first-run --user-data-dir="/tmp/headless-$name" --disable-features=PartitionAllocUnretainedDanglingPtr \
    $flags --dump-dom https://example.com >"/w/.poc/headless-$name.html" 2>"/w/.poc/headless-$name.err"
  code=$?
  elapsed=$(( $(date +%s) - start ))
  if grep -q 'Example Domain' "/w/.poc/headless-$name.html"; then
    echo "RESULT: $name: rendered example.com in ${elapsed}s"
    if [ "$passed" -eq 0 ]; then
      printf '%s' "$vars" >/w/.poc/winner.env
      printf '%s' "$flags" >/w/.poc/winner.flags
      printf '%s' "$name" >/w/.poc/winner.name
    fi
    passed=$((passed + 1))
  else
    crash=$(grep -a -m1 -o -E 'SIGSEGV @[^(]*|SIGILL[^,]*|SIGBUS[^,]*|free\(\): [a-z ]+|Unimplemented Opcode.{0,60}' "/w/.poc/headless-$name.err" | head -c 120)
    opcode=$(grep -a -m1 -o -E 'x64opcode=[0-9A-F ]{2,24}' "/w/.poc/headless-$name.err")
    echo "RESULT: $name: no page after ${elapsed}s (exit $code): ${crash:-no crash message} ${opcode}"
    # What the browser or box64 said last, without the noise every run prints.
    grep -a -v -E 'bus\.cc|cpufreq|CURLManager|thread id|^,|^ *$|^ ?(R[A-Z0-9]{1,2}:|RSP[-+]|ES:)' "/w/.poc/headless-$name.err" \
      | tail -n 7 | cut -c1-170 | sed "s/^/RESULT:   /"
  fi
done <poc/arm64/variants.txt
echo "RESULT: $passed configuration(s) rendered; later stages use: $(cat /w/.poc/winner.name 2>/dev/null || echo none)"
[ "$passed" -gt 0 ]
