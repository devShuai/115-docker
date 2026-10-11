"""Authenticated page and JSON API that add downloads to the logged-in 115 Browser."""
import base64
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

# Sibling modules load even under python -I or PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth  # noqa: E402
import client  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import links  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"), "/icon.svg": ("icon.svg", "image/svg+xml")}
MAX_BODY = 64 * 1024
FAILURE_LIMIT, FAILURE_WINDOW = 10, 600
OFFLINE_STATES = {-1: "失败", 0: "已停止", 1: "下载中", 2: "已完成"}
OFFLINE_KEYS = {-1: "failed", 0: "waiting", 1: "downloading", 2: "done"}
DISPLAY_KEYS = {"finished": "done", "downloading": "downloading", "waiting": "waiting", "failed": "failed",
                "forbidden": "failed"}
TRANSFER_KEYS = {0: "downloading", 1: "paused", 2: "done", 3: "failed", 4: "waiting"}
ACTIVE = ("downloading", "verifying", "waiting")
_AMOUNT = re.compile(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?)(?:i?B)?", re.IGNORECASE)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                                      "base-uri 'none'; form-action 'self'"}
ERRORS = ((ValueError, HTTPStatus.BAD_REQUEST), (client.NotLoggedIn, HTTPStatus.CONFLICT),
          (client.VerificationRequired, HTTPStatus.CONFLICT), (client.BrowserUnavailable, HTTPStatus.SERVICE_UNAVAILABLE),
          (RuntimeError, HTTPStatus.BAD_GATEWAY))


class NotFound(Exception):
    """Unknown API path."""


def _number(value, kind=int):
    try:
        return kind(float(value or 0))
    except (TypeError, ValueError):
        return kind(0)


def human_size(value):
    """Same format as the page: 812 B, 5.3 GB, 812 MB."""
    units = ("B", "KB", "MB", "GB", "TB")
    value, index = float(value or 0), 0
    while value >= 1024 and index < len(units) - 1:
        value, index = value / 1024, index + 1
    return f"{value:.{1 if index and value < 100 else 0}f} {units[index]}"


def amount(text):
    """'1.25MB/s' or '3.4GB', as the transfer list writes them -> bytes."""
    match = _AMOUNT.match(text or "")
    return float(match.group(1)) * 1024 ** " KMGTP".index(match.group(2).upper() or " ") if match else None


def _tidy(text, suffix=""):
    value = amount(text)
    return (text or "") if value is None else human_size(value) + suffix


def offline_view(task, auto=None):
    status = _number(task.get("status"))
    view = {"hash": task.get("info_hash"), "name": task.get("name") or task.get("url") or "",
            "status": OFFLINE_STATES.get(status, str(status)),
            "state": DISPLAY_KEYS.get(task.get("display_status")) or OFFLINE_KEYS.get(status, "waiting"),
            "text": task.get("status_text") or OFFLINE_STATES.get(status, ""),
            "percent": _number(task.get("percentDone"), float), "size": _number(task.get("size")),
            "speed": _number(task.get("rateDownload")), "left": _number(task.get("left_time")),
            "added": task.get("add_time"), "folder": str(task.get("file_category")) == "0",
            "ready": status == 2 and bool(task.get("pick_code")) and str(task.get("move", 1)) not in ("0", "-1")}
    if auto:
        view["auto"] = auto
    return view


def transfer_view(item):
    """One entry of the browser's live transfer list."""
    state = TRANSFER_KEYS.get(item.get("status"), "waiting")
    hashed = _number(item.get("hash"), float)
    if state == "downloading" and item.get("paused"):
        state = "paused"
    elif state == "downloading" and hashed > 0.0001 and (not item.get("folder") or hashed < 1):
        # Downloaded; the browser is checking the file against 115's hash.
        state = "verifying"
    received, total = item.get("received") or "", item.get("total") or ""
    return {"id": str(item.get("id")), "name": item.get("name") or "", "state": state,
            "progress": min(1.0, max(0.0, _number(item.get("progress"), float))),
            "verify": min(1.0, hashed) if state == "verifying" else None,
            "speed": _tidy(item.get("speed"), "/s") if state == "downloading" else "",
            "remain": _number(item.get("remain")) if state == "downloading" else 0,
            "received": "" if amount(received) == 0 else _tidy(received),
            "total": "" if total in ("unknown", "大小未知") else _tidy(total),
            "folder": bool(item.get("folder")), "error": item.get("error") or "", "ended": item.get("ended") or ""}


def history_transfer(row, index):
    """A download history row, used while the browser's transfer list page is closed."""
    received, total, state = row["received"] or 0, row["total"] or 0, row.get("key") or "failed"
    ended = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["finished"])) if row.get("finished") else ""
    return {"id": f"h{index}", "name": row["name"], "state": state,
            "progress": 1.0 if state == "done" else (min(1.0, received / total) if total > 0 else 0.0),
            "verify": None, "speed": "", "remain": 0, "received": human_size(received),
            "total": human_size(total) if total > 0 else "", "folder": False, "error": "", "ended": ended}


def transfers_view(items, live):
    groups = {"downloading": 0, "verifying": 0, "waiting": 1, "paused": 2}
    if live:
        items = sorted(items, key=lambda item: -_number(item["id"]))
    items = sorted(items, key=lambda item: groups.get(item["state"], 3))[:50]
    active = [item for item in items if item["state"] in ACTIVE]
    speed = sum(amount(item["speed"]) or 0 for item in active)
    sized = [(amount(item["total"]), item["progress"]) for item in active if amount(item["total"])]
    if sized:
        progress = sum(size * done for size, done in sized) / sum(size for size, _ in sized)
    else:
        progress = sum(item["progress"] for item in active) / len(active) if active else None
    return {"live": live, "items": items, "active": len(active),
            "speed": human_size(speed) + "/s" if speed else "", "progress": progress}


def _text(value):
    if isinstance(value, str):
        return value
    return "\n".join(str(item) for item in value) if isinstance(value, list) else ""


def _query_order(query):
    asc = (query.get("asc") or [""])[0]
    if asc not in ("", "0", "1"):
        raise ValueError("排序方向无效")
    return links.drive_order((query.get("sort") or [None])[0], None if asc == "" else asc == "1")


class App:
    def __init__(self, cloud, watcher, user, encoded, profile_dir, clock=time.monotonic):
        self.cloud, self.watcher, self.user, self.encoded = cloud, watcher, user, encoded
        self.profile_dir, self.clock = profile_dir, clock
        self.accepted = set()
        self.failures = {}
        self.lock = threading.Lock()

    def check_login(self, header, address):
        """Return None when authorized, else the HTTP status to send."""
        now = self.clock()
        with self.lock:
            recent = [moment for moment in self.failures.get(address, []) if now - moment < FAILURE_WINDOW]
            self.failures[address] = recent
            if len(recent) >= FAILURE_LIMIT:
                return HTTPStatus.TOO_MANY_REQUESTS
            # Verified headers are cached as digests so polling does not rerun PBKDF2.
            digest = hashlib.sha256((header or "").encode()).hexdigest()
            if digest in self.accepted:
                return None
        user, password = "", ""
        if (header or "").startswith("Basic "):
            try:
                user, _, password = base64.b64decode(header[6:], validate=True).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                pass
        valid = hmac.compare_digest(user.encode(), self.user.encode()) & auth.verify(password, self.encoded)
        with self.lock:
            if valid:
                if len(self.accepted) > 32:
                    self.accepted.clear()
                self.accepted.add(digest)
                return None
            self.failures[address].append(now)
        return HTTPStatus.UNAUTHORIZED

    def get(self, path, query):
        if path == "/api/status":
            return self.cloud.status()
        if path == "/api/offline":
            page = int((query.get("page") or ["1"])[0])
            result = self.cloud.offline_tasks(max(1, min(page, 100)))
            watched = {task["hash"]: task["status"] for task in self.watcher.snapshot()}
            return {"tasks": [offline_view(task, watched.get(task.get("info_hash"))) for task in result.get("tasks") or []],
                    "count": int(result.get("count") or 0)}
        if path == "/api/offline/content":
            order = _query_order(query)
            task = self.cloud.find_offline(links.task_hash((query.get("hash") or [""])[0]))
            if task is None:
                raise ValueError("最近的云下载任务里找不到它")
            listing = self.cloud.offline_content(task, order)
            listing["task"] = offline_view(task)
            return listing
        if path == "/api/transfers":
            try:
                live = self.cloud.transfers()
            except (client.BrowserUnavailable, RuntimeError, OSError):
                live = None
            if live is not None:
                return transfers_view([transfer_view(item) for item in live], True)
            rows = history.recent_downloads(self.profile_dir)
            return transfers_view([history_transfer(row, index) for index, row in enumerate(rows)], False)
        if path == "/api/local":
            return {"downloads": history.recent_downloads(self.profile_dir)}
        if path == "/api/watch":
            return {"tasks": self.watcher.snapshot()}
        raise NotFound

    def _share(self, body, cid="", offset=0):
        code = body.get("receive_code")
        share_code, receive_code, page = links.share_link(_text(body.get("url") or body.get("text")),
                                                          code if isinstance(code, str) else "")
        listing = self.cloud.browse_share(share_code, receive_code, cid, offset)
        listing["share"] = {"url": page, "receive_code": receive_code}
        return listing

    def post(self, path, body):
        if path == "/api/offline":
            urls = links.offline_links(_text(body.get("links")))
            result = self.cloud.add_offline(urls)
            added = [item for item in result.get("result") or [] if item.get("state")]
            failed = [{"url": item.get("url", ""), "error": item.get("error_msg") or item.get("error") or "失败",
                       "hash": item.get("info_hash") or ""}
                      for item in result.get("result") or [] if not item.get("state")]
            if body.get("to_nas"):
                self.watcher.watch(added)
            return {"added": len(added) if result.get("result") else len(urls), "failed": failed,
                    "to_nas": bool(body.get("to_nas")),
                    "tasks": [{"hash": item["info_hash"], "name": item.get("name") or item.get("url", "")}
                              for item in added if item.get("info_hash")]}
        if path == "/api/inspect":
            text = _text(body.get("text")).strip()
            if not text:
                raise ValueError("请粘贴链接，或填写网盘路径")
            kind = links.detect(text)
            if kind == "links":
                return {"kind": "links", "links": links.offline_links(text)}
            if kind == "path":
                return self.cloud.open_path(links.drive_path(text), links.drive_order(body.get("sort"), body.get("asc")))
            return self._share({"url": text, "receive_code": body.get("receive_code")})
        if path == "/api/browse":
            start = links.offset(body.get("offset"))
            if body.get("kind") == "share":
                return self._share(body, links.folder_id(body.get("cid"), share=True), start)
            return self.cloud.browse_drive(links.folder_id(body.get("cid", "0")), start,
                                           links.drive_order(body.get("sort"), body.get("asc")))
        if path == "/api/download":
            if "items" in body:
                items = links.picked_items(body["items"])
            else:
                items = [self.cloud.resolve(links.drive_path(_text(body.get("path"))))]
            self.cloud.download(items)
            return {"count": len(items), "names": [item["n"] for item in items][:20],
                    "name": items[0]["n"], "is_dir": items[0]["is_dir"]}
        if path == "/api/share":
            code = body.get("receive_code")
            share_code, receive_code, page = links.share_link(_text(body.get("url")), code if isinstance(code, str) else "")
            items = links.picked_items(body["items"], share=True) if "items" in body else None
            names = self.cloud.share(share_code, receive_code, page, items)
            return {"count": len(names), "names": names[:20]}
        raise NotFound


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "115-download-api"
        sys_version = ""

        def log_message(self, format, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            for name, value in {**HEADERS, **(extra or {}), "Content-Type": content_type}.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            status = app.check_login(self.headers.get("Authorization"), self.client_address[0])
            if status is None:
                return True
            self.send(status, {"error": "需要登录" if status == HTTPStatus.UNAUTHORIZED else "失败次数过多，请稍后再试"},
                      extra={"WWW-Authenticate": 'Basic realm="115 downloads", charset="UTF-8"'})
            return False

        def dispatch(self, action):
            try:
                self.send(HTTPStatus.OK, action())
            except NotFound:
                self.send(HTTPStatus.NOT_FOUND, {"error": "不存在的接口"})
            except Exception as error:
                for kind, status in ERRORS:
                    if isinstance(error, kind):
                        self.send(status, {"error": str(error)})
                        return
                print(f"Download API error: {type(error).__name__}: {error}", flush=True)
                self.send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "内部错误"})

        def do_GET(self):
            if not self.authorized():
                return
            url = urlsplit(self.path)
            if url.path in FILES:
                name, content_type = FILES[url.path]
                self.send(HTTPStatus.OK, (STATIC / name).read_bytes(), content_type)
                return
            self.dispatch(lambda: app.get(url.path, parse_qs(url.query)))

        def do_POST(self):
            if not self.authorized():
                return
            # A browser can attach cached Basic credentials to cross-site form posts.
            # JSON bodies and an Origin check keep other sites from adding downloads.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.send(HTTPStatus.FORBIDDEN, {"error": "拒绝跨站请求"})
                return
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                self.send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "请求体必须是 JSON"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = MAX_BODY + 1
            if length > MAX_BODY:
                self.send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "请求内容过大"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self.send(HTTPStatus.BAD_REQUEST, {"error": "无效的 JSON"})
                return
            self.dispatch(lambda: app.post(urlsplit(self.path).path, body))

    return Handler


def main():
    user, encoded = auth.load(Path(os.environ.get("RUNTIME_DIR", "/run/115")) / "api-auth")
    profile = os.environ.get("PROFILE_DIR_IN_CONTAINER", "/etc/115")
    cloud = client.Cloud115(port=int(os.environ.get("BROWSER_DEBUG_PORT", "9222")))
    watcher = jobs.Watcher(cloud, Path(profile) / "115-downloads.json")
    threading.Thread(target=watcher.run_forever, name="offline-watcher", daemon=True).start()
    port = int(os.environ.get("DOWNLOAD_API_PORT", "1151"))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_for(App(cloud, watcher, user, encoded, profile)))
    server.daemon_threads = True
    print(f"Download API listening on port {port}.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
