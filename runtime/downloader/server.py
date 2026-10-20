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
import sqlite3
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

# Sibling modules load even under python -I or PYTHONSAFEPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import auth  # noqa: E402
import apidocs  # noqa: E402
import client  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import links  # noqa: E402
import session  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
# The image's version (X.Y.Z, or edge-<commit> for main), written by the Dockerfile.
VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"), "/icon.svg": ("icon.svg", "image/svg+xml")}
MAX_BODY = 64 * 1024
FAILURE_LIMIT, FAILURE_WINDOW = 10, 600
OFFLINE_STATES = {-1: "失败", 0: "已停止", 1: "下载中", 2: "已完成"}
OFFLINE_KEYS = {-1: "failed", 0: "waiting", 1: "downloading", 2: "done"}
DISPLAY_KEYS = {"finished": "done", "downloading": "downloading", "waiting": "waiting", "failed": "failed",
                "forbidden": "failed"}
# GET /api/offline?state=: 115's own list filters; recent = the newest page of running, failed and done.
OFFLINE_LISTS = {"running": client.RUNNING, "failed": client.FAILED, "done": client.DONE, "all": 0}
RECENT = ("running", "failed", "done")
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

ENDPOINTS = apidocs.ENDPOINTS
# Where the 115 Browser saves to (compose mounts the NAS folder here); NAS deletes stay inside it.
DOWNLOAD_ROOT = Path(os.environ.get("DOWNLOAD_ROOT") or "/opt/Downloads")


class NotFound(Exception):
    """Unknown API path."""


def app_version():
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"  # A source checkout, not an image.


VERSION = app_version()


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
    state = DISPLAY_KEYS.get(task.get("display_status")) or OFFLINE_KEYS.get(status, "waiting")
    view = {"hash": task.get("info_hash"), "name": task.get("name") or task.get("url") or "",
            "status": OFFLINE_STATES.get(status, str(status)),
            "state": state,
            "text": task.get("status_text") or OFFLINE_STATES.get(status, ""),
            "percent": 100.0 if state == "done" else min(100.0, max(0.0, _number(task.get("percentDone"), float))),
            "size": _number(task.get("size")),
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
    # The native list can retain zero/missing progress even after its status
    # changes. Completion is authoritative; byte counts can fill a missing
    # running percentage, but rounded display sizes never prove completion.
    progress = min(1.0, max(0.0, _number(item.get("progress"), float)))
    if state == "done":
        progress = 1.0
    elif progress == 0 and state in ("downloading", "verifying", "paused"):
        received_bytes, total_bytes = amount(received), amount(total)
        if received_bytes and total_bytes and total_bytes > 0:
            progress = min(1.0, received_bytes / total_bytes)
    if state == "verifying" and not item.get("folder"):
        progress = 1.0  # File download finished; verification is a separate stage.
    return {"id": str(item.get("id")), "name": item.get("name") or "", "state": state,
            "progress": progress,
            "verify": min(1.0, hashed) if state == "verifying" else None,
            "speed": _tidy(item.get("speed"), "/s") if state == "downloading" else "",
            "remain": _number(item.get("remain")) if state == "downloading" else 0,
            "received": "" if amount(received) == 0 else _tidy(received),
            "total": "" if total in ("unknown", "大小未知") else _tidy(total),
            "folder": bool(item.get("folder")), "error": item.get("error") or "", "ended": item.get("ended") or ""}


def delete_local(profile_dir, ids, root):
    """Delete NAS files the 115 Browser downloaded, picked by their download-history id.

    Only regular files inside the download folder are removed, never a running download or a
    symlink; folders that become empty are removed up to the download folder itself. The
    history entries stay (the browser holds that database), so GET /api/local shows exists=false.
    """
    root = Path(root).resolve()
    rows = {row["id"]: row for row in history.recent_downloads(profile_dir, limit=5000)}
    deleted, missing, failed = [], [], []
    for identity in ids:
        row = rows.get(identity)
        if row is None:
            failed.append({"id": identity, "name": "", "error": "下载记录里没有它"})
            continue
        name = row["name"]
        if row["key"] == "downloading":
            failed.append({"id": identity, "name": name, "error": "还在下载，先在 115 浏览器里取消"})
            continue
        removed, inside = False, True
        for raw in dict.fromkeys(p for p in (row["path"], row.get("current_path")) if p):
            path = Path(raw)
            real = path.resolve()
            if real == root or not real.is_relative_to(root):
                inside = False
                continue
            if path.is_symlink() or not real.is_file():
                continue
            try:
                real.unlink()
            except OSError as error:
                failed.append({"id": identity, "name": name, "error": error.strerror or str(error)})
                break
            removed = True
            parent = real.parent
            while parent != root and parent.is_relative_to(root):
                try:
                    parent.rmdir()          # only succeeds when empty
                except OSError:
                    break
                parent = parent.parent
        else:
            if removed:
                deleted.append({"id": identity, "name": name})
            elif not inside:
                failed.append({"id": identity, "name": name, "error": "文件不在下载目录里"})
            else:
                missing.append({"id": identity, "name": name})
    return {"deleted": deleted, "missing": missing, "failed": failed}


def disk_bytes(row):
    """Bytes written so far by a running download, read from its partial file.

    The browser leaves received_bytes in its history at the starting value until the
    download changes state, so this is the only live figure when the transfer list
    page is closed. Returns None when it cannot tell.
    """
    path, total = row.get("current_path") or "", row.get("total") or 0
    if row.get("key") != "downloading" or not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return None
    written = info.st_size
    allocated = getattr(info, "st_blocks", 0) * 512
    if allocated and allocated < written:
        written = allocated  # Sparse file grown to full size up front: count written blocks.
    if total > 0 and written >= total:
        return None  # Preallocated to its final size; the length says nothing about progress.
    return written


def history_transfer(row, index, written=None, speed=0.0):
    """A download history row, used while the browser's transfer list page is closed."""
    received, total, state = row["received"] or 0, row["total"] or 0, row.get("key") or "failed"
    if state == "downloading" and written is not None:
        received = max(received, written)
    ended = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["finished"])) if row.get("finished") else ""
    running = state == "downloading" and speed > 0
    return {"id": f"h{row['id']}" if row.get("id") is not None else f"h{index}", "name": row["name"], "state": state,
            "progress": 1.0 if state == "done" else (min(1.0, received / total) if total > 0 else 0.0),
            "verify": None, "speed": human_size(speed) + "/s" if running else "",
            "remain": int((total - received) / speed) if running and total > received else 0,
            "received": human_size(received), "total": human_size(total) if total > 0 else "",
            "folder": False, "error": "", "ended": ended}


def live_transfers(live, recorded, written=None):
    """Correct lagging native rows only with the same committed download ID and filename."""
    written = written or {}
    completed = {(str(row["id"]), row["name"]): row for row in recorded
                 if row.get("id") is not None and row.get("key") == "done"}
    running = {str(row["id"]): row for row in recorded if row.get("id") is not None and row.get("key") == "downloading"}
    items = []
    for raw in live:
        item = transfer_view(raw)
        row = completed.get((item["id"], item["name"]))
        # Keep explicit hash verification and failures visible. A file on disk
        # or a similarly named historical download is not completion evidence.
        if row and item["state"] in ("downloading", "waiting", "paused"):
            item.update(state="done", progress=1.0, speed="", remain=0,
                        received=human_size(row["received"] or 0),
                        total=human_size(row["total"]) if row["total"] else "",
                        ended=history_transfer(row, 0)["ended"])
        elif item["state"] == "downloading" and item["id"] in written and item["id"] in running:
            # A hidden transfer page stops refreshing; the partial file keeps growing.
            got, speed = written[item["id"]]
            total = running[item["id"]]["total"] or 0
            if total > 0 and got / total > item["progress"]:
                item.update(progress=min(1.0, got / total), received=human_size(got), total=human_size(total))
                if not item["speed"] and speed > 0:
                    item.update(speed=human_size(speed) + "/s", remain=int((total - got) / speed))
        items.append(item)
    return items


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
    def __init__(self, cloud, watcher, user, encoded, profile_dir, clock=time.monotonic, download_root=None):
        self.cloud, self.watcher, self.user, self.encoded = cloud, watcher, user, encoded
        self.profile_dir, self.clock = profile_dir, clock
        self.download_root = Path(download_root or DOWNLOAD_ROOT)
        self.accepted = set()
        self.failures = {}
        self.samples = {}  # download id -> (time, bytes on disk, smoothed bytes per second)
        self.lock = threading.Lock()

    def _measure(self, rows):
        """Bytes on disk and speed for running downloads, from successive polls."""
        measured = {}
        now = self.clock()
        with self.lock:
            for row in rows:
                if row.get("id") is None:
                    continue
                written = disk_bytes(row)
                if written is None:
                    continue
                key = str(row["id"])
                speed = 0.0
                if key in self.samples:
                    then, before, speed = self.samples[key]
                    if now > then and written >= before:
                        instant = (written - before) / (now - then)
                        speed = instant if not speed else speed * 0.6 + instant * 0.4
                self.samples[key] = (now, written, speed)
                measured[key] = (written, speed)
            for key in [key for key in self.samples if key not in measured]:
                del self.samples[key]
        return measured

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
            return {**self.cloud.status(), "version": VERSION}
        if path == "/api/endpoints":
            return apidocs.reference()
        if path == "/openapi.json":
            return apidocs.openapi()
        if path == "/api/offline":
            page = max(1, min(int((query.get("page") or ["1"])[0]), 100))
            # page on its own keeps paging the unfiltered list, as older clients do.
            state = (query.get("state") or ["all" if page > 1 else "recent"])[0]
            if state == "recent":
                results = self.cloud.offline_lists([(OFFLINE_LISTS[name], 1) for name in RECENT])
                # A task that finishes between the requests keeps its later state.
                latest = {task.get("info_hash") or id(task): task for result in results for task in result.get("tasks") or []}
                tasks = sorted(latest.values(), key=lambda task: -_number(task.get("add_time")))
                counts = {name: int(result.get("count") or 0) for name, result in zip(RECENT, results)}
                extra = {"count": sum(counts.values()), "counts": counts}
            elif state in OFFLINE_LISTS:
                result = self.cloud.offline_tasks(page, OFFLINE_LISTS[state])
                tasks, extra = result.get("tasks") or [], {"count": int(result.get("count") or 0)}
            else:
                raise ValueError("state 只能是 recent、running、failed、done 或 all")
            watched = {task["hash"]: task["status"] for task in self.watcher.snapshot()}
            return {"tasks": [offline_view(task, watched.get(task.get("info_hash"))) for task in tasks], **extra}
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
            try:
                rows = history.recent_downloads(self.profile_dir, limit=100)
            except (sqlite3.Error, OSError):
                rows = []  # Rotating history must not break a working native list.
            written = self._measure(rows)
            if live is not None:
                return transfers_view(live_transfers(live, rows, written), True)
            return transfers_view([history_transfer(row, index, *written.get(str(row.get("id")), (None, 0.0)))
                                   for index, row in enumerate(rows)], False)
        if path == "/api/local":
            limit = max(1, min(int((query.get("limit") or ["30"])[0]), 500))
            rows = history.recent_downloads(self.profile_dir, limit=limit)
            return {"downloads": [{**row, "exists": os.path.isfile(row["path"])} for row in rows]}
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
        if path == "/api/delete":
            ids = links.file_ids(body.get("ids"))
            pid = body.get("pid")
            pid = pid if isinstance(pid, str) and pid.isdigit() else ""
            self.cloud.delete_files(ids, pid)
            return {"count": len(ids)}
        if path == "/api/offline/delete":
            hashes = links.task_hashes(body.get("hashes"))
            self.cloud.delete_offline(hashes, bool(body.get("files")))
            return {"count": len(hashes), "files": bool(body.get("files")), "forgotten": self.watcher.forget(hashes)}
        if path == "/api/local/delete":
            return delete_local(self.profile_dir, links.download_ids(body.get("ids")), self.download_root)
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
            if url.path == "/115dl.py":
                self.send(HTTPStatus.OK, (STATIC.parent / "cli.py").read_bytes(), "text/x-python; charset=utf-8",
                          {"Content-Disposition": 'attachment; filename="115dl.py"'})
                return
            if url.path == "/agent.md":
                self.send(HTTPStatus.OK, apidocs.agent_guide().encode(), "text/markdown; charset=utf-8")
                return
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
    if os.environ.get("KEEP_115_LOGIN", "true") == "true":
        keeper = session.SessionKeeper(cloud, Path(profile) / "115-session.json")
        threading.Thread(target=keeper.run_forever, name="session-keeper", daemon=True).start()
    port = int(os.environ.get("DOWNLOAD_API_PORT", "1151"))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_for(App(cloud, watcher, user, encoded, profile)))
    server.daemon_threads = True
    print(f"Download API listening on port {port}.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
