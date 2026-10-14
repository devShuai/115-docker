#!/bin/bash
# The x86-64 browser comes with its x86-64 libraries through multiarch; the native
# arm64 builds of the same libraries let box64 call them directly where it wraps them.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
dpkg --add-architecture amd64
apt-get update -qq
apt-get install -y -qq --no-install-recommends ./vendor/115browser-36.0.1-amd64.deb >/w/.poc/apt-amd64.log
apt-get install -y -qq --no-install-recommends \
  libgtk-3-0t64 libnss3 libnspr4 libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 \
  libcairo2 libcups2t64 libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0t64 libpango-1.0-0 libx11-6 \
  libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 \
  xvfb fonts-noto-cjk procps >/w/.poc/apt-arm64.log
echo "RESULT: installed packages: $(dpkg -l | awk '$1 == "ii" && $4 == "amd64"' | wc -l) amd64, $(dpkg -l | awk '$1 == "ii" && $4 == "arm64"' | wc -l) arm64"
echo "RESULT: 115Browser $(dpkg-query -W -f '${Version} ${Architecture}' 115browser), binary $(od -An -t x2 -j 18 -N 2 /usr/local/115Browser/115Browser | xargs) (ELF machine 003e = x86-64)"
echo "RESULT: x86-64 libraries in /usr/lib/x86_64-linux-gnu: $(ls /usr/lib/x86_64-linux-gnu/*.so* 2>/dev/null | wc -l)"
