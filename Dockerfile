# syntax=docker/dockerfile:1
FROM debian:trixie-slim
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

# All application archives are supplied by this project and verified before use.
# BuildKit bind mounts keep the 115 installer out of the final image layers.
RUN --mount=type=bind,source=vendor,target=/vendor,readonly \
    test "$TARGETARCH" = amd64 \
    && cd /vendor && sha256sum -c SHA256SUMS \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates locales dbus-x11 \
       tigervnc-standalone-server tigervnc-tools \
       openbox tint2 pcmanfm x11-utils x11-xserver-utils \
       python3 python3-numpy fonts-noto-cjk fonts-liberation \
       papirus-icon-theme adwaita-icon-theme gnome-themes-extra librsvg2-bin \
       libasound2t64 libgtk-3-0t64 libnss3 libgbm1 \
       /vendor/115browser-36.0.1-amd64.deb \
    && sed -i 's/^# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && mkdir -p /usr/local/share/noVNC /usr/local/lib/websockify \
    && tar -xzf /vendor/novnc-1.6.0.tar.gz --strip-components=1 -C /usr/local/share/noVNC \
    && tar -xzf /vendor/websockify-0.13.0.tar.gz --strip-components=1 -C /usr/local/lib/websockify \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY rootfs/ /
COPY runtime/ /opt/115/
RUN mkdir -p /etc/115 /opt/Downloads /opt/.cache /opt/.config \
    && chmod 755 /opt/115/*.sh /opt/Desktop/*.desktop \
    && cp /opt/115/index.html /usr/local/share/noVNC/index.html \
    && cp /opt/115/novnc/*.json /usr/local/share/noVNC/ \
    && ln -sf /opt/115/115.sh /usr/local/115Browser/115.sh \
    && rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/115/desktop.svg \
       -o /usr/share/backgrounds/115/desktop.png \
    && test -x /usr/local/115Browser/115Browser \
    && test -x /usr/bin/Xtigervnc \
    && test -f /usr/local/share/noVNC/vnc.html

EXPOSE 1150
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python3", "/opt/115/healthcheck.py"]
ENTRYPOINT ["dbus-run-session", "--", "/opt/115/start.sh"]
