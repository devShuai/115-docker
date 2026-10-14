#!/bin/bash
# Generic ARM64 box64 with the dynamic recompiler, pinned to the v0.4.4 release commit.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends git ca-certificates cmake make gcc g++ python3 >/dev/null
git clone -q https://github.com/ptitSeb/box64.git /src/box64
git -C /src/box64 checkout -q 2f130fab1d6e1a4ee8a71dc60cfdfcc839ad192a
start=$(date +%s)
cmake -S /src/box64 -B /src/box64/build -D ARM_DYNAREC=ON -D CMAKE_BUILD_TYPE=RelWithDebInfo >/dev/null
cmake --build /src/box64/build -j"$(nproc)" >/dev/null
cmake --install /src/box64/build
echo "RESULT: $(box64 --version 2>&1 | head -n 1) built in $(( $(date +%s) - start ))s"
echo "RESULT: x86-64 helper libs: $(ls /usr/lib/box64-x86_64-linux-gnu 2>/dev/null | tr '\n' ' ')"
