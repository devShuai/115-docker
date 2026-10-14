# syntax=docker/dockerfile:1
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/opt \
    DISPLAY=:115 \
    LANG=zh_CN.UTF-8 \
    LC_ALL=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    TZ=Asia/Shanghai \
    XDG_CURRENT_DESKTOP=XFCE \
    XDG_MENU_PREFIX=xfce- \
    XDG_CONFIG_HOME=/opt/.config \
    XDG_CACHE_HOME=/opt/.cache \
    XDG_RUNTIME_DIR=/run/115 \
    LD_LIBRARY_PATH=/usr/local/115Browser \
    NO_VNC_HOME=/usr/local/share/noVNC

# All application and Debian runtime packages are supplied by this project.
# BuildKit bind mounts keep the 115 installer out of the final image layers.
# The slim base drops every translation catalog; keep Simplified Chinese ones
# so desktop menus and GTK dialogs follow the zh_CN locale.
RUN --network=none --mount=type=bind,source=vendor,target=/vendor,readonly \
    test "$TARGETARCH" = amd64 \
    && cd /vendor && sha256sum -c SHA256SUMS \
    && echo 'path-include /usr/share/locale/zh_CN/*' >/etc/dpkg/dpkg.cfg.d/zz-115-zh-locale \
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
    && cp /opt/115/web/zh-default.js /usr/local/share/noVNC/ \
    && cp /opt/115/web/zh-default.js /opt/115/kasmvnc/disconnected.html /usr/share/kasmvnc/www/ \
    && cp /opt/115/kasmvnc/zh_CN.json /usr/share/kasmvnc/www/app/locale/zh_CN.json \
    && python3 /opt/115/web/localize.py /usr/local/share/noVNC/vnc.html /usr/share/kasmvnc/www/vnc.html \
    && python3 /opt/115/web/patch-client.py /usr/share/kasmvnc/www \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai >/etc/timezone \
    && ln -sf /opt/115/115.sh /usr/local/115Browser/115.sh \
    && printf '#!/bin/sh\nexec python3 /opt/115/downloader/cli.py "$@"\n' >/usr/local/bin/115dl \
    && chmod 755 /usr/local/bin/115dl \
    && rsvg-convert -w 1920 -h 1080 /usr/share/backgrounds/115/desktop.svg \
       -o /usr/share/backgrounds/115/desktop.png \
    && test -x /usr/local/115Browser/115Browser \
    && test -x /usr/bin/Xtigervnc \
    && test -x /usr/bin/Xkasmvnc \
    && test -f /usr/share/kasmvnc/www/vnc.html \
    && test -f /usr/local/share/noVNC/vnc.html \
    && test -f /usr/share/locale/zh_CN/LC_MESSAGES/gtk30.mo \
    && test -f /usr/share/locale/zh_CN/LC_MESSAGES/xfce4-panel.mo \
    && test -x /usr/bin/xfwm4 \
    && test -f /usr/share/themes/Arc-Dark/xfwm4/themerc \
    && test -f /usr/share/applications/115Browser.desktop \
    && test -f /opt/115/downloader/server.py \
    && test -f /opt/115/downloader/session.py \
    && test -x /usr/local/bin/115dl \
    && python3 -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" /opt/115/downloader/cli.py /opt/115/downloader/session.py

# Set by the pipelines from the release tag (X.Y.Z) or the commit (edge-<commit>).
# Last, so a new version reuses every cached layer above.
ARG APP_VERSION=dev
LABEL org.opencontainers.image.version=$APP_VERSION
RUN printf '%s\n' "$APP_VERSION" >/opt/115/VERSION

EXPOSE 1150 1151
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python3", "/opt/115/healthcheck.py"]
ENTRYPOINT ["/opt/115/entrypoint.sh"]
