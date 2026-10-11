"""Authenticated page and JSON API that add downloads to the logged-in 115 Browser."""
import base64
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
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
         "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_BODY = 64 * 1024
FAILURE_LIMIT, FAILURE_WINDOW = 10, 600
OFFLINE_STATES = {-1: "失败", 0: "已停止", 1: "下载中", 2: "已完成"}
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                                      "base-uri 'none'; form-action 'self'"}
ERRORS = ((ValueError, HTTPStatus.BAD_REQUEST), (client.NotLoggedIn, HTTPStatus.CONFLICT),
          (client.VerificationRequired, HTTPStatus.CONFLICT), (client.BrowserUnavailable, HTTPStatus.SERVICE_UNAVAILABLE),
          (RuntimeError, HTTPStatus.BAD_GATEWAY))


class NotFound(Exception):
    """Unknown API path."""


def offline_view(task):
    status = int(task.get("status") or 0)
    return {"hash": task.get("info_hash"), "name": task.get("name") or task.get("url") or "",
            "status": OFFLINE_STATES.get(status, str(status)), "percent": float(task.get("percentDone") or 0),
            "size": int(task.get("size") or 0), "added": task.get("add_time")}


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
            return {"tasks": [offline_view(task) for task in result.get("tasks") or []],
                    "count": int(result.get("count") or 0)}
        if path == "/api/local":
            return {"downloads": history.recent_downloads(self.profile_dir)}
        if path == "/api/watch":
            return {"tasks": self.watcher.snapshot()}
        raise NotFound

    def post(self, path, body):
        if path == "/api/offline":
            urls = links.offline_links(body.get("links") if isinstance(body.get("links"), str)
                                       else "\n".join(body.get("links") or []))
            result = self.cloud.add_offline(urls)
            added = [item for item in result.get("result") or [] if item.get("state")]
            failed = [{"url": item.get("url", ""), "error": item.get("error_msg") or item.get("error") or "失败"}
                      for item in result.get("result") or [] if not item.get("state")]
            if body.get("to_nas"):
                self.watcher.watch(added)
            return {"added": len(added) if result.get("result") else len(urls), "failed": failed,
                    "to_nas": bool(body.get("to_nas"))}
        if path == "/api/download":
            item = self.cloud.resolve(links.drive_path(body.get("path")))
            self.cloud.download([item])
            return {"name": item["n"], "is_dir": item["is_dir"]}
        if path == "/api/share":
            share_code, receive_code, page = links.share_link(body.get("url"), body.get("receive_code") or "")
            names = self.cloud.share(share_code, receive_code, page)
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
