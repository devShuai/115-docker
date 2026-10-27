"""Plays a drive video through this server: 115's transcoded HLS, and the original file.

115 signs the variant playlists, segments and the original file for the User-Agent of the browser that asked for
the video, so every upstream request here sends the 115 Browser's own UA. Playlists are rewritten to paths relative
to /api/play/<pc>/ (v0.m3u8, v0/12.ts …): the signed upstream URLs stay on this server, and a proxy in front of it
can pass the playlists through as they are.
"""
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

TTL = 20 * 60                  # resolved upstream URLs are reused this long; 115 signs them, so a 403 resolves again
CHUNK = 256 * 1024
TIMEOUT = 30
MAX_PLAYLIST = 4 * 1024 * 1024
MAX_ENTRIES = 64
REFERER = "https://115.com/"
PLAYLIST_TYPE = "application/vnd.apple.mpegurl"
PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified", "ETag")
# 115 serves the original file as application/octet-stream; players want the real type.
MEDIA_TYPES = {"mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska",
               "webm": "video/webm", "ts": "video/mp2t", "avi": "video/x-msvideo", "wmv": "video/x-ms-wmv"}
_URI = re.compile(r'URI="([^"]+)"')
_ATTRS = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_EXPIRED = (403, 404, 410)


class Missing(Exception):
    """The asked-for playlist, segment or file does not exist (HTTP 404)."""


def extension(name):
    return name.rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def rewrite_master(text, base, variants):
    """115's master playlist with each variant (and alternative rendition) as v<n>.m3u8; their absolute URLs go
    into variants in that order."""
    def swap(match):
        variants.append(urljoin(base, match.group(1)))
        return f'URI="v{len(variants) - 1}.m3u8"'
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(_URI.sub(swap, line) if line.startswith(("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF")) else line)
            continue
        variants.append(urljoin(base, line))
        out.append(f"v{len(variants) - 1}.m3u8")
    return "\n".join(out) + "\n"


def rewrite_variant(text, base, index, uris):
    """A variant playlist with segments as v<index>/<n>.ts (keys and init sections as v<index>/<n>.bin); their
    absolute URLs go into uris in that order."""
    def swap(match):
        uris.append(urljoin(base, match.group(1)))
        return f'URI="v{index}/{len(uris) - 1}.bin"'
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.append(_URI.sub(swap, line) if line.startswith(("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-SESSION-KEY")) else line)
            continue
        uris.append(urljoin(base, line))
        out.append(f"v{index}/{len(uris) - 1}.ts")
    return "\n".join(out) + "\n"


def variant_views(master):
    """Name, resolution and bandwidth of each variant in a (rewritten) master playlist."""
    views, pending = [], None
    for line in master.splitlines():
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = {key: value.strip('"') for key, value in _ATTRS.findall(line.split(":", 1)[1])}
            pending = {"name": attrs.get("NAME", ""), "resolution": attrs.get("RESOLUTION", ""),
                       "bandwidth": int(attrs["BANDWIDTH"]) if attrs.get("BANDWIDTH", "").isdigit() else 0}
        elif pending is not None and line and not line.startswith("#"):
            views.append(pending)
            pending = None
    return views


class Upstream:
    """An open upstream response: its status, the headers worth passing on, and the body in chunks."""

    def __init__(self, response, status, content_type=""):
        self.response, self.status = response, status
        self.headers = {name: response.headers[name] for name in PASS_HEADERS if response.headers.get(name)}
        if content_type and self.headers.get("Content-Type", "application/octet-stream") == "application/octet-stream":
            self.headers["Content-Type"] = content_type

    def chunks(self):
        try:
            while True:
                chunk = self.response.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            self.response.close()

    def close(self):
        self.response.close()


class Player:
    def __init__(self, cloud, open_url=None, clock=time.monotonic):
        self.cloud, self.clock = cloud, clock
        self.open_url = open_url or urllib.request.urlopen
        self.lock = threading.Lock()
        self.entries = {}  # pc -> {at, info, master, variants: [url], media (text when 115 gave a media playlist), lists}
        self.agent = None

    def _agent(self):
        if not self.agent:
            self.agent = self.cloud.user_agent()
        return self.agent

    def _entry(self, pc, fresh=False):
        with self.lock:
            entry = self.entries.get(pc)
            if entry is not None and not fresh and self.clock() - entry["at"] < TTL:
                return entry
        info = self.cloud.video(pc)
        text, base, variants = info.get("master") or "", info.get("master_url") or "", []
        entry = {"at": self.clock(), "info": info, "variants": variants, "media": None, "lists": {}}
        if "#EXTINF" in text:
            # A media playlist straight away: keep the text (it was read with the login cookies) as variant 0.
            entry["media"] = text
            entry["master"] = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=0\nv0.m3u8\n"
        else:
            entry["master"] = rewrite_master(text, base, variants) if text.startswith("#EXTM3U") else ""
        with self.lock:
            if len(self.entries) >= MAX_ENTRIES and pc not in self.entries:
                self.entries.pop(min(self.entries, key=lambda key: self.entries[key]["at"]))
            self.entries[pc] = entry
        return entry

    def _open(self, url, range_header=None, content_type=""):
        headers = {"User-Agent": self._agent(), "Referer": REFERER}
        if range_header:
            headers["Range"] = range_header
        try:
            response = self.open_url(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)
            return Upstream(response, response.status, content_type)
        except urllib.error.HTTPError as error:
            return Upstream(error, error.code)
        except OSError as error:
            raise RuntimeError(f"连不上 115：{error}") from error

    def info(self, pc):
        entry = self._entry(pc)
        info = entry["info"]
        name = info.get("name") or ""
        return {"pc": pc, "name": name, "ext": extension(name), "size": info.get("size") or 0,
                "duration": info.get("duration") or 0, "width": info.get("width") or 0, "height": info.get("height") or 0,
                "transcoded": bool(info.get("transcoded")), "variants": variant_views(entry["master"]),
                "hls": f"/api/play/{pc}/master.m3u8" if entry["master"] else "",
                "file": f"/api/play/{pc}/file" if info.get("origin") else "",
                "error": "" if entry["master"] else info.get("master_error") or "115 还没有转好码"}

    def master(self, pc):
        entry = self._entry(pc)
        if not entry["master"]:
            raise Missing("115 还没有给这个视频转好码，只能播原文件")
        return entry["master"].encode()

    def variant(self, pc, index):
        for retry in (False, True):
            entry = self._entry(pc, fresh=retry)
            if entry["media"] is not None and index == 0:
                text, base = entry["media"], entry["info"].get("master_url") or ""
            elif index < len(entry["variants"]):
                base = entry["variants"][index]
                upstream = self._open(base)
                if upstream.status in _EXPIRED and not retry:
                    upstream.close()
                    continue
                if upstream.status != 200:
                    upstream.close()
                    raise RuntimeError(f"115 返回了 HTTP {upstream.status}")
                try:
                    text = upstream.response.read(MAX_PLAYLIST).decode("utf-8", "replace")
                finally:
                    upstream.close()
            else:
                raise Missing("没有这个清晰度")
            uris = []
            body = rewrite_variant(text, base, index, uris)
            with self.lock:
                entry["lists"][index] = uris
            return body.encode()
        raise RuntimeError("115 的播放地址过期了，重新取也没成")

    def _uris(self, pc, index, fresh):
        entry = self._entry(pc, fresh=fresh)
        if index not in entry["lists"]:
            self.variant(pc, index)
        return entry["lists"].get(index) or []

    def segment(self, pc, index, number, range_header=None):
        for retry in (False, True):
            uris = self._uris(pc, index, retry)
            if number >= len(uris):
                raise Missing("没有这一段")
            upstream = self._open(uris[number], range_header)
            if upstream.status in _EXPIRED and not retry:
                upstream.close()
                continue
            return upstream
        raise RuntimeError("115 的播放地址过期了，重新取也没成")

    def file(self, pc, range_header=None):
        for retry in (False, True):
            info = self._entry(pc, fresh=retry)["info"]
            if not info.get("origin"):
                raise Missing("115 没有给出这个视频的原文件地址")
            upstream = self._open(info["origin"], range_header, MEDIA_TYPES.get(extension(info.get("name") or ""), ""))
            if upstream.status in _EXPIRED and not retry:
                upstream.close()
                continue
            return upstream
        raise RuntimeError("115 的播放地址过期了，重新取也没成")
