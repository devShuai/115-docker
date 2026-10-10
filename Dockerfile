# syntax=docker/dockerfile:1
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/opt \
    DISPLAY=:115 \
    LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8 \
    XDG_CONFIG_HOME=/opt/.config \
    XDG_CACHE_HOME=/opt/.cache \
    XDG_RUNTIME_DIR=/run/115 \
    LD_LIBRARY_PATH=/usr/local/115Browser \
    NO_VNC_HOME=/usr/local/share/noVNC

# All application and Debian runtime packages are supplied by this project.
# BuildKit bind mounts keep the 115 installer out of the final image layers.
RUN --network=none --mount=type=bind,source=vendor,target=/vendor,readonly \
    test "$TARGETARCH" = amd64 \
    && cd /vendor && sha256sum -c SHA256SUMS \
    && mkdir -p /tmp/offline-apt/sources /tmp/offline-apt/lists \
    && apt-get install -y --no-install-recommends \
       -o Dir::Etc::sourcelist=/dev/null -o Dir::Etc::sourceparts=/tmp/offline-apt/sources \
       -o Dir::State::lists=/tmp/offline-apt/lists \
       /vendor/debian/*.deb /vendor/115browser-36.0.1-amd64.deb \
       /vendor/kasmvncserver-trixie-1.5.0-amd64.deb \
    && sed -i 's/^# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && mkdir -p /usr/local/share/noVNC /usr/local/lib/websockify \
    && tar -xzf /vendor/novnc-1.6.0.tar.gz --strip-components=1 -C /usr/local/share/noVNC \
    && tar -xzf /vendor/websockify-0.13.0.tar.gz --strip-components=1 -C /usr/local/lib/websockify \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/offline-apt

COPY rootfs/ /
COPY runtime/ /opt/115/
RUN mkdir -p /etc/115 /opt/Downloads /opt/.cache /opt/.config \
    && chmod 755 /opt/115/*.sh /opt/Desktop/*.desktop \
    && cp /opt/115/index.html /usr/local/share/noVNC/index.html \
    && cp /opt/115/index.html /usr/share/kasmvnc/www/index.html \
    && cp /opt/115/novnc/*.json /usr/local/share/noVNC/ \
    && ln -sf /opt/115/115.sh /usr/local/115Browser/115.sh \
    && rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/115/desktop.svg \
       -o /usr/share/backgrounds/115/desktop.png \
    && test -x /usr/local/115Browser/115Browser \
    && test -x /usr/bin/Xtigervnc \
    && test -x /usr/bin/Xkasmvnc \
    && test -f /usr/share/kasmvnc/www/vnc.html \
    && test -f /usr/local/share/noVNC/vnc.html

EXPOSE 1150
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python3", "/opt/115/healthcheck.py"]
ENTRYPOINT ["/opt/115/entrypoint.sh"]
