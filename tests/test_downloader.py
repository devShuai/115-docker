import base64
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/downloader"))
import auth  # noqa: E402
import client  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import links  # noqa: E402
import server  # noqa: E402

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=test"


class LinkTests(unittest.TestCase):
    def test_offline_links_accept_115_offline_schemes_one_per_line(self):
        text = f" {MAGNET} \n\ned2k://|file|a.mkv|123|{'B' * 32}|/\nhttps://example.com/a.iso\nftp://h/x\n{MAGNET}\n"
        self.assertEqual(links.offline_links(text), [MAGNET, f"ed2k://|file|a.mkv|123|{'B' * 32}|/",
                                                     "https://example.com/a.iso", "ftp://h/x"])

    def test_offline_links_reject_other_input(self):
        for text in ("", "   \n", "javascript:alert(1)", "file:///etc/passwd", "magnet:?xt=urn:btih:short",
                     "https://a.example/x https://b.example/y", "随便写的字"):
            with self.assertRaises(ValueError, msg=text):
                links.offline_links(text)
        with self.assertRaisesRegex(ValueError, "最多"):
            links.offline_links("\n".join(f"https://example.com/{n}" for n in range(101)))

    def test_drive_path(self):
        self.assertEqual(links.drive_path(" /电影//2024\\合集/ "), ["电影", "2024", "合集"])
        for text in ("", "/", "/a/../b", "./x"):
            with self.assertRaises(ValueError, msg=text):
                links.drive_path(text)

    def test_share_links(self):
        self.assertEqual(links.share_link("https://115cdn.com/s/swabc123?password=x1y2#"),
                         ("swabc123", "x1y2", "https://115cdn.com/s/swabc123?password=x1y2"))
        self.assertEqual(links.share_link("链接：https://115.com/s/swabc123 访问码：q9w8 复制这段内容"),
                         ("swabc123", "q9w8", "https://115.com/s/swabc123?password=q9w8"))
        self.assertEqual(links.share_link("https://anxia.com/s/swabc123", "zz11")[1], "zz11")
        self.assertEqual(links.share_link("https://115cdn.com/s/swabc123")[1:], ("", "https://115cdn.com/s/swabc123"))
        for text, code in (("https://example.com/s/abc", ""), ("https://115.com/s/swabc123", "bad code")):
            with self.assertRaises(ValueError):
                links.share_link(text, code)

    def test_items_match_115_web_formats(self):
        self.assertEqual(links.drive_item({"n": "a.mkv", "pc": "pc1", "fid": "9", "cid": "3"}),
                         {"n": "a.mkv", "pc": "pc1", "is_dir": False})
        self.assertEqual(links.drive_item({"n": "dir", "pc": "pc2", "cid": "7"}), {"n": "dir", "pc": "pc2", "is_dir": True})
        self.assertEqual(links.share_item({"n": "dir", "p": 0, "cid": 5}), {"n": "dir", "pc": "", "is_dir": True, "sc": "5"})
        self.assertEqual(links.share_item({"n": "a.mkv", "p": 1, "fid": "8", "cid": "5"}),
                         {"n": "a.mkv", "pc": "", "is_dir": False, "sc": "8"})


class AuthTests(unittest.TestCase):
    def test_hash_round_trip_and_file_format(self):
        encoded = auth.encode("Check123")
        self.assertTrue(auth.verify("Check123", encoded))
        self.assertFalse(auth.verify("Check124", encoded))
        self.assertFalse(auth.verify("Check123", "garbage"))
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("115:" + encoded + "\n")
        self.addCleanup(Path(handle.name).unlink)
        self.assertEqual(auth.load(handle.name), ("115", encoded))


class HistoryTests(unittest.TestCase):
    def test_recent_downloads_from_115_history(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "Default/HistoryEx"
            database.parent.mkdir()
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE downloads (target_path TEXT, received_bytes INT, total_bytes INT, "
                               "state INT, start_time INT, end_time INT)")
            start = (1_700_000_000 + 11644473600) * 1_000_000
            connection.executemany("INSERT INTO downloads VALUES (?,?,?,?,?,?)", [
                ("/opt/Downloads/old.iso", 5, 5, 1, start, start + 1_000_000),
                ("/opt/Downloads/new.mkv", 2, 10, 0, start + 60_000_000, 0)])
            connection.commit()
            connection.close()
            rows = history.recent_downloads(directory)
        self.assertEqual([row["name"] for row in rows], ["new.mkv", "old.iso"])
        self.assertEqual((rows[0]["state"], rows[0]["received"], rows[0]["total"]), ("下载中", 2, 10))
        self.assertEqual((rows[1]["started"], rows[1]["finished"]), (1_700_000_000, 1_700_000_001))
        self.assertEqual(history.recent_downloads("/nonexistent"), [])


class FakeBrowser:
    """Stands in for the DevTools HTTP endpoint and its WebSocket connections."""
    def __init__(self, pages=(), results=(), up=True):
        self.pages, self.results, self.up, self.log = list(pages), list(results), up, []

    def opener(self, url, timeout=5):
        if not self.up:
            raise URLError("refused")
        if url.endswith("/json/version"):
            return io.BytesIO(json.dumps({"webSocketDebuggerUrl": "ws://browser"}).encode())
        return io.BytesIO(json.dumps(self.pages).encode())

    def connect(self, url):
        browser = self

        class Connection:
            def call(self, method, params=None, timeout=15):
                browser.log.append((url, method, params))
                browser.pages.append({"id": "new", "type": "page", "url": params["url"], "webSocketDebuggerUrl": "ws://new"})
                return {"targetId": "new"}

            def evaluate(self, expression):
                return "complete"

            def run(self, declaration, argument=None, timeout=60):
                browser.log.append((url, declaration, argument))
                return browser.results.pop(0)

            def close(self):
                pass

        return Connection()

    def cloud(self):
        return client.Cloud115(port=9222, connect=self.connect, opener=self.opener)


DRIVE_TAB = {"id": "t1", "type": "page", "url": "https://115.com/?cid=0&offset=0&mode=wangpan", "webSocketDebuggerUrl": "ws://t1"}


class ClientTests(unittest.TestCase):
    def test_status_has_no_side_effects(self):
        self.assertEqual(FakeBrowser(up=False).cloud().status(), {"browser": False, "logged_in": False})
        browser = FakeBrowser(pages=[{"id": "x", "type": "page", "url": "file:///fixture.html"}])
        self.assertEqual(browser.cloud().status(), {"browser": True, "logged_in": None})
        self.assertEqual(browser.log, [])
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"logged_in": True, "user_id": "42", "bridge": True}])
        self.assertEqual(browser.cloud().status(), {"browser": True, "logged_in": True, "user_id": "42"})

    def test_operations_open_a_background_tab_when_needed(self):
        browser = FakeBrowser(results=[{"state": True, "result": []}])
        browser.cloud().add_offline([MAGNET], "77")
        self.assertEqual(browser.log[0], ("ws://browser", "Target.createTarget", {"url": client.HOME, "background": True}))
        self.assertEqual(browser.log[1], ("ws://new", client.ADD_OFFLINE, {"urls": [MAGNET], "folder": "77"}))

    def test_errors_from_the_page_are_mapped(self):
        for result, error in (({"login": False}, client.NotLoggedIn), ({"state": False, "errcode": 911}, client.VerificationRequired),
                              ({"state": False, "errcode": "911"}, client.VerificationRequired),
                              ({"state": False, "error_msg": "任务已存在"}, RuntimeError), ({"bridge": False}, RuntimeError)):
            with self.assertRaises(error, msg=result):
                FakeBrowser(pages=[DRIVE_TAB], results=[result]).cloud().add_offline([MAGNET])
        with self.assertRaises(client.BrowserUnavailable):
            FakeBrowser(up=False).cloud().offline_tasks()

    def test_resolve_download_and_share(self):
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[
            {"item": {"n": "合集", "pc": "pcx", "fid": "", "cid": "5"}}, {"state": True},
            {"missing": True},
            {"state": True, "data": {"list": [{"n": "a.mkv", "p": 1, "fid": "8", "cid": "5"}, {"n": "d", "p": 0, "cid": "6"}]}},
            {"state": True}])
        cloud = browser.cloud()
        item = cloud.resolve(["电影", "合集"])
        self.assertEqual(item, {"n": "合集", "pc": "pcx", "is_dir": True})
        cloud.download([item])
        self.assertEqual(browser.log[1][2], {"items": [item]})
        with self.assertRaisesRegex(ValueError, "找不到"):
            cloud.resolve(["没有"])
        self.assertEqual(cloud.share("swabc", "x1y2", "https://115cdn.com/s/swabc?password=x1y2"), ["a.mkv", "d"])
        self.assertEqual(browser.log[-1][1], client.SHARE_DOWNLOAD)
        self.assertEqual(browser.log[-1][2], {"items": [{"n": "a.mkv", "pc": "", "is_dir": False, "sc": "8"},
                                                        {"n": "d", "pc": "", "is_dir": True, "sc": "6"}],
                                              "share_code": "swabc", "receive_code": "x1y2",
                                              "ref_url": "https://115cdn.com/s/swabc?password=x1y2"})

    def test_page_functions_take_input_only_as_arguments(self):
        for name in ("ADD_OFFLINE", "LIST_OFFLINE", "RESOLVE_PATH", "FOLDER_RESULT", "DOWNLOAD", "SHARE_LIST", "SHARE_DOWNLOAD"):
            declaration = getattr(client, name)
            self.assertTrue(declaration.startswith("async function (args) {"), name)
        self.assertIn("CreateDownloadTask(encodeURIComponent(JSON.stringify(payload)))", client.DOWNLOAD)
        self.assertIn("/web/lixian/?ct=lixian&ac=", client.ADD_OFFLINE)


class FakeCloud:
    def __init__(self, tasks=(), result=None):
        self.tasks, self.result, self.downloads, self.calls = list(tasks), result, [], []

    def offline_tasks(self, page=1):
        self.calls.append(("tasks", page))
        return {"state": True, "tasks": self.tasks, "count": len(self.tasks), "page_row": 30}

    def offline_result(self, task):
        return self.result

    def download(self, items):
        self.downloads.append(items)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"
        self.now = 1000.0

    def watcher(self, cloud):
        return jobs.Watcher(cloud, self.path, clock=lambda: self.now, log=lambda *args, **kwargs: None)

    def test_finished_task_is_sent_to_the_nas_once(self):
        cloud = FakeCloud(tasks=[{"info_hash": "h1", "name": "电影", "status": 1}])
        watcher = self.watcher(cloud)
        watcher.watch([{"info_hash": "h1", "url": MAGNET, "state": True}, {"url": "no-hash"}])
        watcher.tick()
        self.assertEqual(watcher.snapshot()[0]["status"], jobs.WAITING)
        cloud.tasks[0]["status"] = 2
        watcher.tick()
        self.assertEqual(watcher.snapshot()[0]["status"], jobs.WAITING, "result not moved into the drive yet")
        cloud.result = {"n": "电影", "pc": "pc1", "is_dir": True}
        watcher.tick()
        watcher.tick()
        self.assertEqual(cloud.downloads, [[cloud.result]])
        reloaded = self.watcher(FakeCloud())
        self.assertEqual([(t["hash"], t["status"], t["name"]) for t in reloaded.snapshot()], [("h1", jobs.SENT, "电影")])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_failures_expiry_and_idle_ticks(self):
        cloud = FakeCloud(tasks=[{"info_hash": "bad", "status": -1}])
        watcher = self.watcher(cloud)
        watcher.tick()
        self.assertEqual(cloud.calls, [], "no pending tasks means no 115 requests")
        watcher.watch([{"info_hash": "bad", "url": "u1"}, {"info_hash": "gone", "url": "u2"}])
        watcher.tick()
        states = {task["hash"]: task["status"] for task in watcher.snapshot()}
        self.assertEqual(states, {"bad": jobs.FAILED, "gone": jobs.WAITING})
        self.now += jobs.KEEP_SECONDS + 1
        watcher.tick()
        states = {task["hash"]: task["status"] for task in watcher.snapshot()}
        self.assertEqual(states, {"gone": jobs.FAILED})


class FakeWatcher:
    def __init__(self):
        self.watched = []

    def watch(self, added):
        self.watched.extend(added)

    def snapshot(self):
        return [{"hash": "h", "name": "n", "status": jobs.WAITING}]


class ServerCloud:
    def __init__(self):
        self.calls, self.error = [], None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def status(self):
        return {"browser": True, "logged_in": True, "user_id": "42"}

    def add_offline(self, urls, folder=""):
        self._maybe_fail()
        self.calls.append(("offline", urls))
        return {"state": True, "result": [{"state": True, "info_hash": "h1", "url": urls[0]},
                                          {"state": False, "url": "x", "error_msg": "任务已存在"}]}

    def offline_tasks(self, page=1):
        return {"tasks": [{"info_hash": "h1", "name": "电影", "status": 1, "percentDone": 50.5, "size": 9}], "count": 1}

    def resolve(self, parts):
        self._maybe_fail()
        self.calls.append(("resolve", parts))
        return {"n": parts[-1], "pc": "pc1", "is_dir": True}

    def download(self, items):
        self.calls.append(("download", items))

    def share(self, share_code, receive_code, page):
        self.calls.append(("share", share_code, receive_code, page))
        return ["a", "b"]


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.cloud, self.watcher = ServerCloud(), FakeWatcher()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        app = server.App(self.cloud, self.watcher, "115", auth.encode("Check123"), self.temporary.name)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, headers=None, password="Check123"):
        headers = dict(headers or {})
        if password is not None:
            headers["Authorization"] = "Basic " + base64.b64encode(f"115:{password}".encode()).decode()
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def post(self, path, payload, headers=None):
        status, _, data = self.request("POST", path, json.dumps(payload), {"Content-Type": "application/json", **(headers or {})})
        return status, json.loads(data)

    def test_authentication_and_security_headers(self):
        status, headers, _ = self.request("GET", "/", password=None)
        self.assertEqual(status, 401)
        self.assertIn("Basic", headers["WWW-Authenticate"])
        self.assertEqual(self.request("GET", "/", password="wrong")[0], 401)
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("115 添加下载", page.decode())
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(self.request("GET", "/app.js")[0], 200)

    def test_repeated_failures_are_rate_limited(self):
        for _ in range(server.FAILURE_LIMIT):
            self.assertEqual(self.request("GET", "/api/status", password="wrong")[0], 401)
        self.assertEqual(self.request("GET", "/api/status")[0], 429)

    def test_posts_must_be_same_origin_json(self):
        port = self.httpd.server_address[1]
        self.assertEqual(self.request("POST", "/api/offline", "links=x", {"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.assertEqual(self.post("/api/offline", {"links": MAGNET}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/offline", {"links": MAGNET}, {"Origin": f"http://127.0.0.1:{port}"})[0], 200)
        self.assertEqual(self.request("POST", "/api/offline", "[1]", {"Content-Type": "application/json"})[0], 400)
        # The size is refused from the header, before any body is read.
        oversized = {"Content-Type": "application/json", "Content-Length": str(server.MAX_BODY + 1)}
        self.assertEqual(self.request("POST", "/api/offline", "{}", oversized)[0], 413)

    def test_offline_download_and_share_endpoints(self):
        status, data = self.post("/api/offline", {"links": MAGNET + "\n", "to_nas": True})
        self.assertEqual((status, data["added"], data["failed"]), (200, 1, [{"url": "x", "error": "任务已存在"}]))
        self.assertEqual([task["info_hash"] for task in self.watcher.watched], ["h1"])
        status, data = self.post("/api/download", {"path": "/电影/合集"})
        self.assertEqual((status, data), (200, {"name": "合集", "is_dir": True}))
        self.assertIn(("download", [{"n": "合集", "pc": "pc1", "is_dir": True}]), self.cloud.calls)
        status, data = self.post("/api/share", {"url": "https://115cdn.com/s/swabc?password=x1y2"})
        self.assertEqual((status, data["count"]), (200, 2))
        self.assertIn(("share", "swabc", "x1y2", "https://115cdn.com/s/swabc?password=x1y2"), self.cloud.calls)
        status, data = self.post("/api/offline", {"links": "not a link"})
        self.assertEqual(status, 400)
        self.assertIn("无法识别", data["error"])

    def test_reads_and_errors(self):
        status, _, data = self.request("GET", "/api/offline")
        self.assertEqual(json.loads(data), {"tasks": [{"hash": "h1", "name": "电影", "status": "下载中", "percent": 50.5,
                                                       "size": 9, "added": None}], "count": 1})
        self.assertEqual(json.loads(self.request("GET", "/api/local")[2]), {"downloads": []})
        self.assertEqual(self.request("GET", "/api/unknown")[0], 404)
        for error, code in ((client.NotLoggedIn("请登录"), 409), (client.VerificationRequired("验证"), 409),
                            (client.BrowserUnavailable("未运行"), 503), (RuntimeError("115 失败"), 502),
                            (KeyError("bug"), 500)):
            self.cloud.error = error
            status, data = self.post("/api/download", {"path": "/a"})
            self.assertEqual(status, code, error)
            self.assertTrue(data["error"])


if __name__ == "__main__":
    unittest.main()
