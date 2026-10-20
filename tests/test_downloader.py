import base64
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/downloader"))
import auth  # noqa: E402
import apidocs  # noqa: E402
import cli  # noqa: E402
import client  # noqa: E402
import history  # noqa: E402
import jobs  # noqa: E402
import links  # noqa: E402
import server  # noqa: E402
import session  # noqa: E402

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
        self.assertTrue(links.share_item({"n": "dir", "p": "0", "fid": "9", "cid": "5"})["is_dir"])
        self.assertTrue(links.share_item({"n": "dir", "cid": "5"})["is_dir"])

    def test_listings_from_115_pages(self):
        page = {"state": True, "cid": "7", "count": 3,
                "path": [{"name": "根目录", "cid": 0}, {"name": "电影", "cid": "7"}],
                "data": [{"n": "合集", "cid": "8", "pid": "7", "pc": "pcd", "tp": "1700000000"},
                         {"n": "a.mkv", "cid": "7", "fid": "9", "pc": "pcf", "s": "1024", "tp": "1700000100"}]}
        listing = links.drive_listing(page, links.drive_order("name", True))
        self.assertEqual(listing["path"], [{"cid": "0", "name": "网盘"}, {"cid": "7", "name": "电影"}])
        self.assertEqual(listing["items"], [
            {"id": "8", "name": "合集", "pc": "pcd", "is_dir": True, "size": 0, "time": 1700000000, "cid": "8"},
            {"id": "9", "name": "a.mkv", "pc": "pcf", "is_dir": False, "size": 1024, "time": 1700000100, "cid": ""}])
        self.assertEqual((listing["cid"], listing["count"], listing["truncated"]), ("7", 3, True))
        self.assertEqual((listing["sort"], listing["asc"]), ("name", True))
        snap = {"state": True, "data": {"count": 2, "shareinfo": {"share_title": "分享"},
                                        "list": [{"n": "d", "cid": "5", "p": 0, "t": "2023-11-15 06:13"},
                                                 {"n": "a.mkv", "fid": "6", "p": 1, "s": 9, "t": "1700000000"}]}}
        listing = links.share_listing(snap, "4")
        self.assertEqual(listing["items"], [{"id": "5", "name": "d", "sc": "5", "is_dir": True, "size": 0,
                                             "time": 1699999980, "cid": "5"},
                                            {"id": "6", "name": "a.mkv", "sc": "6", "is_dir": False, "size": 9,
                                             "time": 1700000000, "cid": ""}])
        self.assertEqual((listing["kind"], listing["cid"], listing["title"], listing["truncated"]), ("share", "4", "分享", False))

    def test_picked_items_are_checked_before_reaching_the_browser(self):
        self.assertEqual(links.picked_items([{"n": "a.mkv", "pc": "abc1", "is_dir": False, "extra": 1}]),
                         [{"n": "a.mkv", "pc": "abc1", "is_dir": False}])
        self.assertEqual(links.picked_items([{"n": "d", "sc": "12", "is_dir": True}], share=True),
                         [{"n": "d", "pc": "", "is_dir": True, "sc": "12"}])
        for raw in ([], "x", [{"n": "a", "pc": "p"}], [{"n": "../a", "pc": "p", "is_dir": False}],
                    [{"n": "a/b", "pc": "p", "is_dir": False}], [{"n": "..", "pc": "p", "is_dir": True}],
                    [{"n": "a\nb", "pc": "p", "is_dir": False}], [{"n": "a", "pc": "p-1", "is_dir": False}],
                    [{"n": "a", "pc": 5, "is_dir": False}], [{"n": "", "pc": "p", "is_dir": False}],
                    [{"n": "a", "pc": "p", "is_dir": False}] * (links.MAX_ITEMS + 1)):
            with self.assertRaises(ValueError, msg=raw):
                links.picked_items(raw)
        with self.assertRaises(ValueError):
            links.picked_items([{"n": "a", "sc": "x1", "is_dir": False}], share=True)

    def test_sort_choices_map_to_115_list_parameters(self):
        self.assertEqual(links.drive_order(), {"sort": "time", "asc": False, "o": "user_ptime", "fc_mix": 1, "natsort": 0})
        self.assertEqual(links.drive_order("name"), {"sort": "name", "asc": True, "o": "file_name", "fc_mix": 0, "natsort": 1})
        self.assertEqual(links.drive_order("size", True)["o"], "file_size")
        for sort, asc in (("user_ptime", None), ("time", "1"), ("name", 1)):
            with self.assertRaises(ValueError):
                links.drive_order(sort, asc)

    def test_detect_and_ids(self):
        self.assertEqual(links.detect("链接：https://115.com/s/swabc 访问码：q9w8"), "share")
        self.assertEqual(links.detect(" /电影/合集"), "path")
        self.assertEqual(links.detect(MAGNET), "links")
        self.assertEqual(links.folder_id("123"), "123")
        self.assertEqual(links.folder_id("", share=True), "")
        self.assertEqual(links.task_hash("a" * 40), "a" * 40)
        self.assertEqual([links.offset(value) for value in (None, 0, "1150", 2300)], [0, 0, 1150, 2300])
        for bad in ("", "1 OR 1", "abc", None):
            with self.assertRaises(ValueError):
                links.folder_id(bad)
        with self.assertRaises(ValueError):
            links.task_hash("../x")


class DeleteInputTests(unittest.TestCase):
    def test_delete_inputs_are_checked_before_reaching_the_browser(self):
        self.assertEqual(links.file_ids(["42", 43, "42"]), ["42", "43"])
        for bad in ([], "42", ["0"], ["00"], [True], ["4a"], ["-1"], [None], ["1"] * (links.MAX_ITEMS + 1)):
            with self.assertRaises(ValueError):
                links.file_ids(bad)
        self.assertEqual(links.task_hashes("a" * 40), ["a" * 40])
        self.assertEqual(links.task_hashes(["a" * 40, "a" * 40, "b" * 40]), ["a" * 40, "b" * 40])
        for bad in ([], ["../x"], [42], ["a" * 40] * (links.MAX_TASKS + 1)):
            with self.assertRaises(ValueError):
                links.task_hashes(bad)
        self.assertEqual(links.download_ids([3, "4", 3]), [3, 4])
        for bad in ([], [0], ["-2"], [True], ["x"], [1.5], list(range(1, links.MAX_LOCAL + 2))):
            with self.assertRaises(ValueError):
                links.download_ids(bad)


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
            connection.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, target_path TEXT, current_path TEXT, "
                               "received_bytes INT, total_bytes INT, state INT, start_time INT, end_time INT)")
            start = (1_700_000_000 + 11644473600) * 1_000_000
            connection.executemany("INSERT INTO downloads (target_path,current_path,received_bytes,total_bytes,state,start_time,end_time) "
                                   "VALUES (?,?,?,?,?,?,?)", [
                ("/opt/Downloads/old.iso", "/opt/Downloads/old.iso", 5, 5, 1, start, start + 1_000_000),
                ("/opt/Downloads/new.mkv", "/opt/Downloads/new.mkv.crdownload", 2, 10, 0, start + 60_000_000, 0)])
            connection.commit()
            connection.close()
            rows = history.recent_downloads(directory)
        self.assertEqual([row["name"] for row in rows], ["new.mkv", "old.iso"])
        self.assertEqual((rows[0]["state"], rows[0]["received"], rows[0]["total"]), ("下载中", 2, 10))
        self.assertEqual(rows[0]["current_path"], "/opt/Downloads/new.mkv.crdownload")
        self.assertEqual((rows[1]["started"], rows[1]["finished"]), (1_700_000_000, 1_700_000_001))
        self.assertEqual(history.recent_downloads("/nonexistent"), [])


class FakeBrowser:
    """Stands in for the DevTools HTTP endpoint and its WebSocket connections."""
    def __init__(self, pages=(), results=(), up=True, cookies=()):
        self.pages, self.results, self.up, self.log = list(pages), list(results), up, []
        self.cookies = {(cookie["name"], cookie["domain"]): dict(cookie) for cookie in cookies}

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
                if method == "Storage.getCookies":
                    return {"cookies": [dict(cookie) for cookie in browser.cookies.values()]}
                if method == "Storage.setCookies":
                    for cookie in params["cookies"]:
                        browser.cookies[(cookie["name"], cookie["domain"])] = dict(cookie, session="expires" not in cookie)
                    return {}
                if method == "Storage.clearCookies":
                    browser.cookies.clear()
                    return {}
                if method == "Page.reload":
                    return {}
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
    def test_deletes_go_through_the_logged_in_page(self):
        browser = FakeBrowser([DRIVE_TAB], [{"state": True}, {"state": False, "error": "文件正在使用"},
                                            {"state": True, "errno": 0}, {"login": False}])
        cloud = browser.cloud()
        cloud.delete_files(["42", "43"], "5")
        self.assertIs(browser.log[-1][1], client.DELETE_FILES)
        self.assertEqual(browser.log[-1][2], {"ids": ["42", "43"], "pid": "5"})
        with self.assertRaisesRegex(RuntimeError, "文件正在使用"):
            cloud.delete_files(["42"])
        cloud.delete_offline(["a" * 40], files=True)
        self.assertIs(browser.log[-1][1], client.DELETE_OFFLINE)
        self.assertEqual(browser.log[-1][2], {"hashes": ["a" * 40], "files": True})
        with self.assertRaises(client.NotLoggedIn):
            cloud.delete_offline(["a" * 40])
        self.assertIn("rb/delete", client.DELETE_FILES)
        self.assertIn('lixian("task_del"', client.DELETE_OFFLINE)

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

    def test_browse_resolve_download_and_share(self):
        page = {"state": True, "cid": "5", "count": 1, "path": [{"name": "根目录", "cid": "0"}, {"name": "合集", "cid": "5"}],
                "data": [{"n": "a.mkv", "cid": "5", "fid": "9", "pc": "pca", "s": 3}]}
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[
            {"page": page, "folder": {"n": "合集", "pc": "pcx"}}, {"state": True},
            {"page": page, "folder": {"n": "合集", "pc": "pcx"}, "focus": "a.mkv"},
            {"missing": True},
            {"page": {"state": False, "error": "目录不存在"}, "folder": None},
            {"state": True, "data": {"list": [{"n": "a.mkv", "p": 1, "fid": "8", "cid": "5"}, {"n": "d", "p": 0, "cid": "6"}]}},
            {"state": True},
            {"state": True}])
        cloud = browser.cloud()
        item = cloud.resolve(["电影", "合集"])
        self.assertEqual(item, {"n": "合集", "pc": "pcx", "is_dir": True})
        self.assertEqual(browser.log[0][1:], (client.OPEN_PATH, {"parts": ["电影", "合集"], "order": None}))
        cloud.download([item])
        self.assertEqual(browser.log[1][2], {"items": [item]})
        self.assertEqual(cloud.resolve(["合集", "a.mkv"]), {"n": "a.mkv", "pc": "pca", "is_dir": False})
        with self.assertRaisesRegex(ValueError, "找不到"):
            cloud.resolve(["没有"])
        with self.assertRaisesRegex(RuntimeError, "目录不存在"):
            cloud.browse_drive("77")
        self.assertEqual(browser.log[-1][1:], (client.BROWSE_DRIVE, {"cid": "77", "offset": 0, "order": None}))
        self.assertEqual(cloud.share("swabc", "x1y2", "https://115cdn.com/s/swabc?password=x1y2"), ["a.mkv", "d"])
        self.assertEqual(browser.log[-1][1], client.SHARE_DOWNLOAD)
        self.assertEqual(browser.log[-1][2], {"items": [{"n": "a.mkv", "pc": "", "is_dir": False, "sc": "8"},
                                                        {"n": "d", "pc": "", "is_dir": True, "sc": "6"}],
                                              "share_code": "swabc", "receive_code": "x1y2",
                                              "ref_url": "https://115cdn.com/s/swabc?password=x1y2"})
        picked = [{"n": "d", "pc": "", "is_dir": True, "sc": "6"}]
        self.assertEqual(cloud.share("swabc", "", "https://115cdn.com/s/swabc", picked), ["d"])
        self.assertEqual(browser.log[-1][2]["items"], picked)

    def test_share_listing_and_errors(self):
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[
            {"state": True, "data": {"count": 1, "list": [{"n": "e1.mkv", "p": 1, "fid": "10", "s": 5}]}},
            {"state": False, "error": "提取码错误"}])
        cloud = browser.cloud()
        listing = cloud.browse_share("swabc", "x1y2", "6", 1150)
        self.assertEqual(browser.log[0][2], {"share_code": "swabc", "receive_code": "x1y2", "cid": "6", "offset": 1150})
        self.assertEqual((listing["offset"], listing["truncated"]), (1150, False))
        self.assertEqual([item["sc"] for item in listing["items"]], ["10"])
        with self.assertRaisesRegex(ValueError, "提取码错误"):
            cloud.browse_share("swabc", "bad1")

    def test_offline_results_use_the_task_pick_code(self):
        cloud = FakeBrowser(pages=[DRIVE_TAB]).cloud()
        folder = {"status": 2, "move": 1, "name": "合集", "pick_code": "pcd", "file_category": 0, "file_id": "50"}
        self.assertEqual(cloud.offline_result(folder), {"n": "合集", "pc": "pcd", "is_dir": True})
        self.assertEqual(cloud.offline_result(dict(folder, file_category=1)), {"n": "合集", "pc": "pcd", "is_dir": False})
        for task in (dict(folder, status=1), dict(folder, move=0), dict(folder, move=-1)):
            self.assertIsNone(cloud.offline_result(task), task)
        # Older tasks without a pick code: look the result up in the drive.
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"item": {"n": "合集", "pc": "pcz", "fid": "", "cid": "50"}}])
        self.assertEqual(browser.cloud().offline_result({"status": 2, "name": "合集", "file_id": "50"}),
                         {"n": "合集", "pc": "pcz", "is_dir": True})

    def test_offline_content_lists_folders_and_single_files(self):
        page = {"state": True, "cid": "50", "count": 1, "path": [], "data": [{"n": "e1.mkv", "cid": "50", "fid": "51", "pc": "pc1"}]}
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"page": page, "folder": {"n": "合集", "pc": "pcd"}}])
        task = {"status": 2, "move": 1, "name": "合集", "pick_code": "pcd", "file_category": 0, "file_id": "50", "size": 7}
        listing = browser.cloud().offline_content(task)
        self.assertEqual(browser.log[0][1:], (client.BROWSE_DRIVE, {"cid": "50", "offset": 0, "order": None}))
        self.assertEqual((listing["folder"], listing["result"]), ({"n": "合集", "pc": "pcd", "is_dir": True},) * 2)
        self.assertEqual([item["name"] for item in listing["items"]], ["e1.mkv"])
        single = FakeBrowser(pages=[DRIVE_TAB]).cloud().offline_content(dict(task, file_category=1, name="a.iso"))
        self.assertEqual(single["items"], [{"id": "pcd", "name": "a.iso", "pc": "pcd", "is_dir": False, "size": 7, "time": 0,
                                            "cid": ""}])
        self.assertIsNone(single["folder"])
        with self.assertRaisesRegex(ValueError, "还没有完成"):
            FakeBrowser(pages=[DRIVE_TAB]).cloud().offline_content(dict(task, status=1))

    def test_sorted_listing_reaches_the_page_and_the_result(self):
        page = {"state": True, "cid": "5", "count": 0, "path": [], "data": []}
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"page": page, "folder": None}])
        order = links.drive_order("size", False)
        listing = browser.cloud().browse_drive("5", 0, order)
        self.assertEqual(browser.log[0][2]["order"], order)
        self.assertEqual((listing["sort"], listing["asc"]), ("size", False))
        self.assertIn("custom_order=1", client.BROWSE_DRIVE, "without it 115 ignores the order")

    def test_offline_lookups_search_the_running_failed_and_finished_lists(self):
        def page(count, *hashes):
            return {"state": True, "page_count": count, "tasks": [{"info_hash": key} for key in hashes]}
        # A finished task can sit pages deep in the unfiltered list, behind every unfinished one.
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[
            {"state": True, "lists": [page(1, "r1"), page(2, "f1"), page(3, "d1")]},
            {"state": True, "lists": [page(2, "f2"), page(3, "d2")]},
            {"state": True, "lists": [page(3, "d3")]}])
        found = browser.cloud().offline_find({"r1", "d2", "missing"})
        self.assertEqual(found, {"r1": {"info_hash": "r1"}, "d2": {"info_hash": "d2"}})
        running, failed, done = client.RUNNING, client.FAILED, client.DONE
        self.assertEqual([entry[2]["lists"] for entry in browser.log], [
            [{"stat": running, "page": 1}, {"stat": failed, "page": 1}, {"stat": done, "page": 1}],
            [{"stat": failed, "page": 2}, {"stat": done, "page": 2}],
            [{"stat": done, "page": 3}]])
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"state": True, "lists": [page(9), page(9), page(9, "h1")]}])
        self.assertEqual(browser.cloud().find_offline("h1"), {"info_hash": "h1"})
        self.assertEqual(len(browser.log), 1, "stops once everything is found")
        browser = FakeBrowser(pages=[DRIVE_TAB], results=[{"state": True, "lists": [page(1, "h2")]}])
        self.assertEqual(browser.cloud().offline_tasks(2, client.DONE)["tasks"], [{"info_hash": "h2"}])
        self.assertEqual(browser.log[0][2], {"lists": [{"stat": client.DONE, "page": 2}]})
        with self.assertRaisesRegex(RuntimeError, "签名"):
            FakeBrowser(pages=[DRIVE_TAB], results=[{"state": False, "error": "无法获取云下载签名"}]).cloud().offline_tasks()

    def test_transfers_come_from_the_transfer_list_page(self):
        transfer_page = {"id": "tf", "type": "page", "url": "chrome://transfer-frame/", "webSocketDebuggerUrl": "ws://tf"}
        browser = FakeBrowser(pages=[DRIVE_TAB, transfer_page], results=[[{"id": "1", "status": 0}]])
        self.assertEqual(browser.cloud().transfers(), [{"id": "1", "status": 0}])
        self.assertEqual(browser.log, [("ws://tf", client.TRANSFERS, None)])
        self.assertIsNone(FakeBrowser(pages=[DRIVE_TAB]).cloud().transfers())

    def test_page_functions_take_input_only_as_arguments(self):
        for name in ("ADD_OFFLINE", "LIST_OFFLINE", "BROWSE_DRIVE", "OPEN_PATH", "FOLDER_RESULT", "DOWNLOAD",
                     "SHARE_LIST", "SHARE_DOWNLOAD"):
            declaration = getattr(client, name)
            self.assertTrue(declaration.startswith("async function (args) {"), name)
        self.assertIn("CreateDownloadTask(encodeURIComponent(JSON.stringify(payload)))", client.DOWNLOAD)
        self.assertIn("/web/lixian/?ct=lixian&ac=", client.ADD_OFFLINE)
        self.assertNotIn("AddRegisterCallBack", client.TRANSFERS, "reading the list must not take over its callbacks")


class FakeCloud:
    def __init__(self, tasks=(), result=None):
        self.tasks, self.result, self.downloads, self.calls = list(tasks), result, [], []

    def offline_find(self, hashes, pages=5):
        self.calls.append(("find", set(hashes), pages))
        return {task["info_hash"]: task for task in self.tasks if task["info_hash"] in hashes}

    def offline_result(self, task):
        return self.result

    def download(self, items):
        self.downloads.append(items)


class WatcherTests(unittest.TestCase):
    def test_deleted_tasks_are_forgotten(self):
        watcher = self.watcher(FakeCloud())
        watcher.watch([{"info_hash": "h1", "name": "电影"}, {"info_hash": "h2", "name": "剧"}])
        self.assertEqual(watcher.forget(["h1", "h9"]), 1)
        self.assertEqual([task["hash"] for task in watcher.snapshot()], ["h2"])
        self.assertEqual([task["hash"] for task in self.watcher(FakeCloud()).snapshot()], ["h2"])   # 存下来了

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

    def test_failed_move_into_the_drive_is_reported(self):
        cloud = FakeCloud(tasks=[{"info_hash": "h1", "name": "电影", "status": 2, "move": -1}],
                          result={"n": "电影", "pc": "pc1", "is_dir": True})
        watcher = self.watcher(cloud)
        watcher.watch([{"info_hash": "h1", "url": MAGNET}])
        watcher.tick()
        self.assertEqual(watcher.snapshot()[0]["status"], jobs.FAILED)
        self.assertEqual(cloud.downloads, [])

    def test_new_tasks_are_checked_often_for_a_while(self):
        watcher = self.watcher(FakeCloud())
        self.assertEqual(watcher.delay(), watcher.interval)
        watcher.watch([{"info_hash": "h1", "url": MAGNET}])
        self.assertTrue(watcher.wake.is_set())
        self.assertEqual(watcher.delay(), jobs.FAST_INTERVAL)
        self.now += jobs.FAST_SECONDS
        self.assertEqual(watcher.delay(), watcher.interval)


class FakeWatcher:
    def __init__(self):
        self.watched = []

    def watch(self, added):
        self.watched.extend(added)

    def snapshot(self):
        return [{"hash": "h1", "name": "n", "status": jobs.WAITING}]

    def forget(self, hashes):
        return sum(1 for value in hashes if value == "h1" * 4)


class ServerCloud:
    def __init__(self):
        self.calls, self.error, self.live = [], None, None

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

    # 115's lists by stat: running, failed, finished, and 0 = unfiltered.
    OFFLINE = {client.RUNNING: [{"info_hash": "h1", "name": "电影", "status": 1, "percentDone": 50.5, "size": 9,
                                 "add_time": 20}],
               client.FAILED: [{"info_hash": "h3", "name": "坏", "status": -1, "add_time": 30}],
               client.DONE: [{"info_hash": "h2", "name": "剧", "status": 2, "move": 1, "pick_code": "pc2", "add_time": 10}],
               0: []}

    def offline_lists(self, lists):
        self.calls.append(("lists", lists))
        return [{"tasks": self.OFFLINE[stat] if page == 1 else [], "count": 10 + stat} for stat, page in lists]

    def offline_tasks(self, page=1, stat=0):
        return self.offline_lists([(stat, page)])[0]

    def resolve(self, parts):
        self._maybe_fail()
        self.calls.append(("resolve", parts))
        return {"n": parts[-1], "pc": "pc1", "is_dir": True}

    def download(self, items):
        self._maybe_fail()
        self.calls.append(("download", items))

    def share(self, share_code, receive_code, page, items=None):
        self.calls.append(("share", share_code, receive_code, page, items))
        return [item["n"] for item in items] if items else ["a", "b"]

    def open_path(self, parts, order=None):
        self.calls.append(("open", parts, order))
        return {"kind": "drive", "cid": "5", "items": [], "focus": parts[-1]}

    def browse_drive(self, cid, offset=0, order=None):
        self.calls.append(("drive", cid, offset, order["sort"], order["asc"]))
        return {"kind": "drive", "cid": cid, "items": []}

    def browse_share(self, share_code, receive_code, cid="", offset=0):
        self.calls.append(("browse_share", share_code, receive_code, cid, offset))
        return {"kind": "share", "cid": cid, "items": []}

    def find_offline(self, info_hash):
        return {"info_hash": "h1", "name": "电影", "status": 2, "pick_code": "pc1", "file_category": 0} if info_hash == "h1" * 4 else None

    def offline_content(self, task, order=None):
        self.calls.append(("content", order["sort"], order["asc"]))
        return {"kind": "drive", "cid": "50", "items": [], "result": {"n": task["name"], "pc": "pc1", "is_dir": True}}

    def transfers(self):
        self._maybe_fail()
        return self.live

    def delete_files(self, ids, pid=""):
        self._maybe_fail()
        self.calls.append(("delete", ids, pid))

    def delete_offline(self, hashes, files=False):
        self._maybe_fail()
        self.calls.append(("remove", hashes, files))


class ServerTests(unittest.TestCase):
    def test_a_busy_history_does_not_break_live_progress(self):
        self.cloud.live = [{"id": "1", "name": "test.bin", "status": 2, "progress": 0}]
        with patch.object(history, "recent_downloads", side_effect=sqlite3.OperationalError("database is locked")):
            status, _, body = self.request("GET", "/api/transfers")
        self.assertEqual(status, 200)
        item = json.loads(body)["items"][0]
        self.assertEqual((item["state"], item["progress"]), ("done", 1))

    def test_drive_and_offline_deletes(self):
        self.assertEqual(self.post("/api/delete", {"ids": ["42", "43"], "pid": "5"}), (200, {"count": 2}))
        self.assertEqual(self.cloud.calls[-1], ("delete", ["42", "43"], "5"))
        self.assertEqual(self.post("/api/delete", {"ids": ["42"], "pid": "../x"})[1], {"count": 1})
        self.assertEqual(self.cloud.calls[-1], ("delete", ["42"], ""))                 # 不像目录 ID 的 pid 不传
        self.assertEqual(self.post("/api/delete", {"ids": ["0"]})[0], 400)
        self.assertEqual(self.post("/api/offline/delete", {"hashes": ["h1" * 4], "files": True}),
                         (200, {"count": 1, "files": True, "forgotten": 1}))
        self.assertEqual(self.cloud.calls[-1], ("remove", ["h1" * 4], True))
        self.assertEqual(self.post("/api/offline/delete", {"hashes": []})[0], 400)
        self.cloud.error = RuntimeError("115 失败")
        self.assertEqual(self.post("/api/delete", {"ids": ["42"]})[0], 502)

    def test_nas_files_are_deleted_only_inside_the_download_folder(self):
        root = Path(self.temporary.name) / "Downloads"
        (root / "剧集" / "第1季").mkdir(parents=True)
        outside = Path(self.temporary.name) / "outside.mkv"
        files = {"done": root / "电影.mkv", "nested": root / "剧集" / "第1季" / "01.mkv", "running": root / "新.mkv",
                 "keep": root / "剧集" / "说明.txt"}
        for path in (*files.values(), outside, root / "新.mkv.crdownload"):
            path.write_bytes(b"x")
        (root / "链接.mkv").symlink_to(outside)
        database = Path(self.temporary.name) / "Default/History"
        database.parent.mkdir()
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, target_path TEXT, current_path TEXT, "
                           "received_bytes INT, total_bytes INT, state INT, start_time INT, end_time INT)")
        rows = [(1, files["done"], 1), (2, files["nested"], 1), (3, files["running"], 0), (4, outside, 1),
                (5, root / "链接.mkv", 1), (6, root / "早删了.mkv", 1), (7, root / ".." / "outside.mkv", 1)]
        connection.executemany("INSERT INTO downloads VALUES (?,?,?,1,1,?,?,1)",
                               [(i, str(p), str(p) + ".crdownload" if state == 0 else str(p), state, 1000 - i)
                                for i, p, state in rows])
        connection.commit()
        connection.close()
        self.app.download_root = root
        listed = json.loads(self.request("GET", "/api/local?limit=3")[2])["downloads"]
        self.assertEqual(len(listed), 3)
        status, result = self.post("/api/local/delete", {"ids": [1, 2, 3, 4, 5, 6, 7, 99]})
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in result["deleted"]], [1, 2])
        self.assertEqual([item["id"] for item in result["missing"]], [6])
        self.assertEqual({item["id"]: item["error"] for item in result["failed"]},     # 5 是指到目录外的符号链接
                         {3: "还在下载，先在 115 浏览器里取消", 4: "文件不在下载目录里", 5: "文件不在下载目录里",
                          7: "文件不在下载目录里", 99: "下载记录里没有它"})
        self.assertFalse(files["done"].exists())
        self.assertFalse((root / "剧集" / "第1季").exists())                           # 删空的文件夹一起删
        self.assertTrue(files["keep"].exists() and files["running"].exists() and outside.exists())
        self.assertTrue((root / "链接.mkv").is_symlink())
        exists = {row["id"]: row["exists"] for row in json.loads(self.request("GET", "/api/local?limit=50")[2])["downloads"]}
        self.assertEqual((exists[1], exists[3]), (False, True))
        self.assertEqual(self.post("/api/local/delete", {"ids": []})[0], 400)
        self.assertEqual(self.request("GET", "/api/local?limit=x")[0], 400)

    def setUp(self):
        self.cloud, self.watcher = ServerCloud(), FakeWatcher()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = app = server.App(self.cloud, self.watcher, "115", auth.encode("Check123"), self.temporary.name)
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

    def test_endpoints_are_listed_for_the_cli(self):
        status, _, data = self.request("GET", "/api/endpoints")
        paths = {item["path"] for item in json.loads(data)["endpoints"]}
        self.assertEqual(status, 200)
        self.assertLessEqual({"/api/status", "/api/inspect", "/api/transfers", "/api/endpoints"}, paths)

    def test_complete_reference_and_openapi_cover_every_operation(self):
        spec = json.loads(self.request("GET", "/openapi.json")[2])
        docs = json.loads(self.request("GET", "/api/endpoints")[2])
        self.assertEqual(len(docs["endpoints"]), 15)
        self.assertEqual(spec["security"], [{"desktopBasic": []}])
        for item in docs["endpoints"]:
            operation = spec["paths"][item["path"]][item["method"].lower()]
            self.assertEqual(operation["operationId"], item["cmd"])
            self.assertEqual(operation["responses"]["200"]["content"]["application/json"]["example"], item["response_example"])
            self.assertTrue(item["notes"])
            self.assertTrue(item["response_fields"])
            for param in item["params"]:
                self.assertIn("schema", param)
        body = spec["paths"]["/api/download"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(body["anyOf"], [{"required": ["path"]}, {"required": ["items"]}])
        self.assertEqual(body["properties"]["items"]["maxItems"], links.MAX_ITEMS)
        self.assertEqual({e["status"] for e in docs["errors"]}, {400, 401, 403, 404, 409, 413, 415, 429, 500, 502, 503})

    def test_agent_resources_require_auth_and_client_is_self_contained(self):
        for path in ("/115dl.py", "/agent.md", "/openapi.json"):
            self.assertEqual(self.request("GET", path, password=None)[0], 401)
            self.assertEqual(self.request("GET", path)[0], 200)
        code, headers, source = self.request("GET", "/115dl.py")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(source, (ROOT / "runtime/downloader/cli.py").read_bytes())
        script = Path(self.temporary.name) / "115dl.py"
        script.write_bytes(source)
        env = dict(os.environ, DOWNLOAD_API_URL=f"http://127.0.0.1:{self.httpd.server_address[1]}",
                   DOWNLOAD_API_USER="115", DOWNLOAD_API_PASSWORD="Check123")
        env.pop("DOWNLOAD_API_PASSWORD_FILE", None)
        result = subprocess.run([sys.executable, "-I", str(script), "--no-input", "--json", "status"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["logged_in"])
        guide = self.request("GET", "/agent.md")[2].decode()
        self.assertNotIn("Check123", guide)
        self.assertIn("--no-input --json", guide)
        for item in apidocs.ENDPOINTS:
            self.assertIn(item["method"] + " " + item["path"], guide)

    def test_authentication_and_security_headers(self):
        status, headers, _ = self.request("GET", "/", password=None)
        self.assertEqual(status, 401)
        self.assertIn("Basic", headers["WWW-Authenticate"])
        self.assertEqual(self.request("GET", "/", password="wrong")[0], 401)
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<title>115 下载</title>", page.decode())
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(self.request("GET", "/app.js")[0], 200)
        status, headers, _ = self.request("GET", "/icon.svg")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))

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
        self.assertEqual((status, data["added"]), (200, 1))
        self.assertEqual(data["failed"], [{"url": "x", "error": "任务已存在", "hash": ""}])
        self.assertEqual(data["tasks"], [{"hash": "h1", "name": MAGNET}])
        self.assertEqual([task["info_hash"] for task in self.watcher.watched], ["h1"])
        status, data = self.post("/api/download", {"path": "/电影/合集"})
        self.assertEqual((status, data["name"], data["is_dir"], data["count"]), (200, "合集", True, 1))
        self.assertIn(("download", [{"n": "合集", "pc": "pc1", "is_dir": True}]), self.cloud.calls)
        status, data = self.post("/api/download", {"items": [{"n": "a.mkv", "pc": "pca", "is_dir": False},
                                                             {"n": "b", "pc": "pcb", "is_dir": True}]})
        self.assertEqual((status, data["count"], data["names"]), (200, 2, ["a.mkv", "b"]))
        self.assertEqual(self.post("/api/download", {"items": [{"n": "../a", "pc": "p", "is_dir": False}]})[0], 400)
        status, data = self.post("/api/share", {"url": "https://115cdn.com/s/swabc?password=x1y2"})
        self.assertEqual((status, data["count"]), (200, 2))
        self.assertIn(("share", "swabc", "x1y2", "https://115cdn.com/s/swabc?password=x1y2", None), self.cloud.calls)
        status, data = self.post("/api/share", {"url": "https://115cdn.com/s/swabc", "receive_code": "x1y2",
                                                "items": [{"n": "d", "sc": "6", "is_dir": True}]})
        self.assertEqual((status, data["names"]), (200, ["d"]))
        self.assertEqual(self.cloud.calls[-1][4], [{"n": "d", "pc": "", "is_dir": True, "sc": "6"}])
        status, data = self.post("/api/offline", {"links": "not a link"})
        self.assertEqual(status, 400)
        self.assertIn("无法识别", data["error"])

    def test_inspect_and_browse(self):
        status, data = self.post("/api/inspect", {"text": MAGNET})
        self.assertEqual((status, data), (200, {"kind": "links", "links": [MAGNET]}))
        self.assertFalse([call for call in self.cloud.calls if call[0] == "offline"], "inspecting adds nothing")
        status, data = self.post("/api/inspect", {"text": "/电影/a.mkv"})
        self.assertEqual((status, data["focus"]), (200, "a.mkv"))
        status, data = self.post("/api/inspect", {"text": "链接：https://115.com/s/swabc 访问码：q9w8"})
        self.assertEqual((status, data["share"]), (200, {"url": "https://115.com/s/swabc?password=q9w8", "receive_code": "q9w8"}))
        self.assertIn(("browse_share", "swabc", "q9w8", "", 0), self.cloud.calls)
        status, data = self.post("/api/browse", {"kind": "share", "url": "https://115.com/s/swabc", "receive_code": "q9w8", "cid": "6"})
        self.assertEqual((status, data["cid"]), (200, "6"))
        status, data = self.post("/api/browse", {"kind": "drive", "cid": "7"})
        self.assertEqual((status, data["cid"]), (200, "7"))
        self.assertEqual(self.post("/api/browse", {"kind": "drive"})[0], 200)
        self.assertEqual(self.cloud.calls[-1], ("drive", "0", 0, "time", False))
        self.assertEqual(self.post("/api/browse", {"kind": "drive", "cid": "7", "offset": 1150, "sort": "name", "asc": False})[0], 200)
        self.assertEqual(self.cloud.calls[-1], ("drive", "7", 1150, "name", False))
        self.assertEqual(self.post("/api/inspect", {"text": "/电影", "sort": "size"})[0], 200)
        self.assertEqual(self.cloud.calls[-1][2]["o"], "file_size")
        for body in ({"kind": "drive", "cid": "1 or 1"}, {"kind": "share", "url": "https://115.com/s/swabc", "cid": "x"},
                     {"kind": "drive", "offset": -1}, {"kind": "drive", "offset": "1e9"}, {"kind": "drive", "offset": True},
                     {"kind": "drive", "sort": "user_ptime"}, {"kind": "drive", "asc": "yes"}):
            self.assertEqual(self.post("/api/browse", body)[0], 400, body)
        self.assertEqual(self.post("/api/inspect", {"text": " "})[0], 400)

    def test_reads_and_errors(self):
        status, _, data = self.request("GET", "/api/offline")
        recent = json.loads(data)
        self.assertEqual([task["hash"] for task in recent["tasks"]], ["h3", "h1", "h2"], "newest first across the lists")
        self.assertEqual(recent["tasks"][1], {"hash": "h1", "name": "电影", "status": "下载中", "state": "downloading",
                                              "text": "下载中", "percent": 50.5, "size": 9, "speed": 0, "left": 0,
                                              "added": 20, "folder": False, "ready": False, "auto": jobs.WAITING})
        self.assertEqual((recent["tasks"][2]["state"], recent["tasks"][2]["ready"]), ("done", True))
        counts = {"running": 10 + client.RUNNING, "failed": 10 + client.FAILED, "done": 10 + client.DONE}
        self.assertEqual((recent["counts"], recent["count"]), (counts, sum(counts.values())))
        self.assertEqual(self.cloud.calls[-1], ("lists", [(client.RUNNING, 1), (client.FAILED, 1), (client.DONE, 1)]))
        listing = json.loads(self.request("GET", "/api/offline?state=done&page=2")[2])
        self.assertEqual((listing, self.cloud.calls[-1]), ({"tasks": [], "count": 10 + client.DONE},
                                                          ("lists", [(client.DONE, 2)])))
        self.request("GET", "/api/offline?page=2")
        self.assertEqual(self.cloud.calls[-1], ("lists", [(0, 2)]), "page alone keeps paging the unfiltered list")
        finished = dict(ServerCloud.OFFLINE[client.RUNNING][0], status=2, move=1, pick_code="pc1")
        with patch.dict(ServerCloud.OFFLINE, {client.DONE: [finished]}):
            tasks = json.loads(self.request("GET", "/api/offline")[2])["tasks"]
        self.assertEqual([(task["hash"], task["state"]) for task in tasks], [("h3", "failed"), ("h1", "done")],
                         "a task caught finishing between the requests is listed once, as finished")
        self.assertEqual(self.request("GET", "/api/offline?state=stuck")[0], 400)
        status, _, data = self.request("GET", "/api/offline/content?hash=" + "h1" * 4 + "&sort=name&asc=1")
        listing = json.loads(data)
        self.assertEqual((status, listing["result"]["pc"], listing["task"]["ready"]), (200, "pc1", True))
        self.assertEqual(self.cloud.calls[-1], ("content", "name", True))
        self.assertEqual(self.request("GET", "/api/offline/content?hash=" + "h1" * 4 + "&asc=up")[0], 400)
        self.assertEqual(self.request("GET", "/api/offline/content?hash=" + "h2" * 4)[0], 400)
        self.assertEqual(self.request("GET", "/api/offline/content?hash=../x")[0], 400)
        self.assertEqual(json.loads(self.request("GET", "/api/local")[2]), {"downloads": []})
        self.assertEqual(self.request("GET", "/api/unknown")[0], 404)
        for error, code in ((client.NotLoggedIn("请登录"), 409), (client.VerificationRequired("验证"), 409),
                            (client.BrowserUnavailable("未运行"), 503), (RuntimeError("115 失败"), 502),
                            (KeyError("bug"), 500)):
            self.cloud.error = error
            status, data = self.post("/api/download", {"path": "/a"})
            self.assertEqual(status, code, error)
            self.assertTrue(data["error"])

    def test_history_mode_reports_progress_from_the_partial_file(self):
        partial = Path(self.temporary.name) / "movie.mkv.crdownload"
        partial.write_bytes(b"x" * 4000)
        database = Path(self.temporary.name) / "Default/HistoryEx"
        database.parent.mkdir()
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY, target_path TEXT, current_path TEXT, "
                           "received_bytes INT, total_bytes INT, state INT, start_time INT, end_time INT)")
        connection.execute("INSERT INTO downloads VALUES (3, ?, ?, 0, 10000, 0, ?, 0)",
                           (str(Path(self.temporary.name) / "movie.mkv"), str(partial),
                            (1_700_000_000 + 11644473600) * 1_000_000))
        connection.commit()
        connection.close()
        self.cloud.error = client.BrowserUnavailable("传输列表没有打开")
        data = json.loads(self.request("GET", "/api/transfers")[2])
        self.assertEqual((data["live"], data["active"]), (False, 1))
        self.assertEqual((data["items"][0]["id"], data["items"][0]["state"], data["items"][0]["progress"]),
                         ("h3", "downloading", 0.4))

    def test_transfers_are_live_or_fall_back_to_history(self):
        self.cloud.live = [
            {"id": "7", "name": "a.mkv", "status": 0, "progress": 0.5, "speed": "2.00MB/s", "remain": 30,
             "received": "1.00GB", "total": "2.00GB"},
            {"id": "9", "name": "b.mkv", "status": 0, "progress": 0.25, "speed": "1.00MB/s", "remain": 90,
             "received": "0B", "total": "6.00GB"},
            {"id": "8", "name": "c.iso", "status": 2, "progress": 1, "received": "1.00GB", "total": "1.00GB",
             "ended": "2026-10-11 12:00:00"}]
        data = json.loads(self.request("GET", "/api/transfers")[2])
        self.assertEqual((data["live"], data["active"], data["speed"]), (True, 2, "3.0 MB/s"))
        self.assertEqual([item["name"] for item in data["items"]], ["b.mkv", "a.mkv", "c.iso"])
        self.assertAlmostEqual(data["progress"], (1 + 1.5) / 8)
        self.assertEqual((data["items"][0]["received"], data["items"][0]["total"]), ("", "6.0 GB"))
        self.cloud.error = client.BrowserUnavailable("未运行")
        data = json.loads(self.request("GET", "/api/transfers")[2])
        self.assertEqual((data["live"], data["items"], data["active"], data["progress"]), (False, [], 0, None))


class ViewTests(unittest.TestCase):
    def test_partial_file_size_is_the_running_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "a.mkv.crdownload"
            partial.write_bytes(b"x" * 300)
            row = {"id": 9, "name": "a.mkv", "current_path": str(partial), "received": 0, "total": 1000,
                   "key": "downloading", "finished": None}
            self.assertEqual(server.disk_bytes(row), 300)
            item = server.history_transfer(row, 0, server.disk_bytes(row), 100.0)
            self.assertEqual((item["id"], item["progress"], item["speed"], item["remain"]), ("h9", 0.3, "100 B/s", 7))
            partial.write_bytes(b"x" * 1000)  # Grown to its final size up front: no progress evidence.
            self.assertIsNone(server.disk_bytes(row))
            self.assertIsNone(server.disk_bytes(dict(row, key="done")))
            self.assertIsNone(server.disk_bytes(dict(row, current_path=str(Path(directory) / "missing"))))

    def test_a_frozen_transfer_page_takes_progress_from_the_partial_file(self):
        live = {"id": 5, "name": "a.mkv", "status": 0, "progress": 0, "speed": "", "received": "", "total": ""}
        running = {"id": 5, "name": "a.mkv", "received": 0, "total": 2048, "key": "downloading"}
        item = server.live_transfers([live], [running], {"5": (1024, 512.0)})[0]
        self.assertEqual((item["state"], item["progress"], item["received"], item["total"]),
                         ("downloading", 0.5, "1.0 KB", "2.0 KB"))
        self.assertEqual((item["speed"], item["remain"]), ("512 B/s", 2))
        other = server.live_transfers([dict(live, id=6)], [running], {"5": (1024, 512.0)})[0]
        self.assertEqual(other["progress"], 0, "another task's file never moves this one")

    def test_speed_comes_from_successive_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "a.crdownload"
            partial.write_bytes(b"x" * 100)
            now = [10.0]
            app = server.App(None, None, "115", "x", directory, clock=lambda: now[0])
            row = {"id": 1, "name": "a", "current_path": str(partial), "received": 0, "total": 10_000,
                   "key": "downloading"}
            self.assertEqual(app._measure([row]), {"1": (100, 0.0)})
            partial.write_bytes(b"x" * 300)
            now[0] = 12.0
            self.assertEqual(app._measure([row]), {"1": (300, 100.0)})
            self.assertEqual(app._measure([]), {})
            self.assertEqual(app.samples, {}, "finished downloads drop their samples")

    def test_completed_history_corrects_stale_native_state_without_matching_other_tasks(self):
        row = {"id": 5, "name": "test.bin", "received": 1024, "total": 1024,
               "key": "done", "finished": 1700000000}
        live = {"id": 5, "name": "test.bin", "status": 0, "progress": 0, "speed": "1MB/s"}
        item = server.live_transfers([live], [row])[0]
        self.assertEqual((item["state"], item["progress"], item["speed"]), ("done", 1, ""))
        self.assertEqual(server.transfers_view([item], True)["active"], 0)
        for other in (dict(live, id=6), dict(live, name="another.bin")):
            self.assertEqual(server.live_transfers([other], [row])[0]["state"], "downloading")
        for code, expected in ((2, "done"), (3, "failed")):
            self.assertEqual(server.live_transfers([dict(live, status=code)], [row])[0]["state"], expected)
        verifying = server.live_transfers([dict(live, hash=0.5)], [row])[0]
        self.assertEqual((verifying["state"], verifying["verify"]), ("verifying", 0.5))

    def test_completed_tasks_do_not_keep_zero_or_missing_progress(self):
        for progress in (0, None, 0.5):
            done = server.transfer_view({"id": 1, "status": 2, "progress": progress})
            self.assertEqual((done["state"], done["progress"]), ("done", 1))
            summary = server.transfers_view([done], True)
            self.assertEqual((summary["active"], summary["progress"]), (0, None))
        for task in ({"status": 2, "percentDone": 0}, {"status": 1, "display_status": "finished"}):
            self.assertEqual(server.offline_view(task)["percent"], 100)

    def test_running_progress_falls_back_to_bytes_without_claiming_completion(self):
        item = {"id": 1, "status": 0, "progress": 0, "received": "512MB", "total": "1GB"}
        self.assertEqual(server.transfer_view(item)["progress"], 0.5)
        same_size = server.transfer_view(dict(item, received="1GB"))
        self.assertEqual((same_size["state"], same_size["progress"]), ("downloading", 1))
        self.assertEqual(server.transfer_view(dict(item, total="unknown"))["progress"], 0)
        self.assertEqual(server.transfer_view(dict(item, progress=0.6))["progress"], 0.6)
        verifying = server.transfer_view(dict(item, hash=0.25))
        self.assertEqual((verifying["state"], verifying["progress"], verifying["verify"]), ("verifying", 1, 0.25))
        self.assertEqual(server.transfer_view(dict(item, status=3))["progress"], 0)

    def test_amounts(self):
        self.assertEqual(server.amount("1.5MB/s"), 1.5 * 1024 ** 2)
        self.assertEqual(server.amount("812.00KB"), 812 * 1024)
        self.assertEqual(server.amount("0B"), 0)
        self.assertIsNone(server.amount("大小未知"))
        self.assertEqual([server.human_size(value) for value in (0, 812, 2048, 5.3 * 1024 ** 3, 812 * 1024 ** 2)],
                         ["0 B", "812 B", "2.0 KB", "5.3 GB", "812 MB"])

    def test_transfer_states(self):
        base = {"id": 1, "name": "a", "progress": 1, "total": "unknown"}
        self.assertEqual(server.transfer_view(dict(base, status=0, paused=True))["state"], "paused")
        verifying = server.transfer_view(dict(base, status=0, hash=0.4))
        self.assertEqual((verifying["state"], verifying["verify"], verifying["total"]), ("verifying", 0.4, ""))
        # Folders keep downloading other files after one file is verified.
        self.assertEqual(server.transfer_view(dict(base, status=0, hash=1, folder=True))["state"], "downloading")
        self.assertEqual([server.transfer_view(dict(base, status=code))["state"] for code in (1, 2, 3, 4, 9)],
                         ["paused", "done", "failed", "waiting", "waiting"])

    def test_history_rows_become_transfers(self):
        row = {"name": "a.iso", "received": 512, "total": 1024, "key": "downloading", "finished": None}
        self.assertEqual(server.history_transfer(row, 3)["progress"], 0.5)
        self.assertEqual(server.history_transfer(row, 3)["id"], "h3")
        self.assertEqual(server.history_transfer(dict(row, key="done", total=0), 0)["progress"], 1.0)


LOGIN = [{"name": name, "value": "v" + name, "domain": ".115.com", "path": "/", "secure": True,
          "httpOnly": True, "sameSite": "None"} for name in ("CID", "SEID", "UID", "KID")]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "115-session.json"
        self.now = 1000.0

    def keeper(self, browser, **kwargs):
        return session.SessionKeeper(browser.cloud(), self.path, clock=lambda: self.now,
                                     log=lambda *a, **k: None, **kwargs)

    def present(self, browser):
        return {cookie["name"]: cookie for cookie in session.login_cookies(browser.connect("ws://browser"))}

    def test_saves_login_cookies_and_restores_them_after_a_restart(self):
        # acw_tc is not a login cookie and must not be saved.
        browser = FakeBrowser(cookies=LOGIN + [{"name": "acw_tc", "value": "x", "domain": "115.com"}])
        keeper = self.keeper(browser)
        self.assertTrue(keeper.save())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        saved = json.loads(self.path.read_text())
        self.assertEqual({cookie["name"] for cookie in saved["cookies"]}, {"CID", "SEID", "UID", "KID"})
        unchanged = self.path.stat().st_mtime_ns
        self.assertTrue(keeper.save())  # No change, so the file is not rewritten.
        self.assertEqual(self.path.stat().st_mtime_ns, unchanged)
        browser.cookies.clear()  # A restart dropped the session cookies.
        self.assertFalse(self.present(browser))
        self.assertTrue(keeper.restore())
        restored = self.present(browser)
        self.assertEqual(set(restored), {"CID", "SEID", "UID", "KID"})
        self.assertTrue(all("expires" in cookie for cookie in restored.values()), "restored cookies must be persistent")
        self.assertFalse(keeper.restore(), "already logged in")

    def test_polls_fast_when_logged_out_and_survives_a_closed_browser(self):
        browser = FakeBrowser(cookies=LOGIN)
        keeper = self.keeper(browser)
        keeper.tick()
        self.assertTrue(keeper.logged_in)
        self.assertEqual(keeper._delay(), keeper.interval)
        browser.cookies.clear()
        keeper.tick()  # Logged out now; the next poll must be the fast one.
        self.assertFalse(keeper.logged_in)
        self.assertEqual(keeper._delay(), keeper.idle_interval)
        closed = session.SessionKeeper(FakeBrowser(up=False).cloud(), self.path,
                                       clock=lambda: self.now, log=lambda *a, **k: None)
        closed.tick()  # A closed browser must not raise.
        self.assertFalse(closed.logged_in)
        self.assertEqual(closed._delay(), closed.idle_interval)

    def test_save_needs_a_login_and_restore_needs_a_file(self):
        browser = FakeBrowser(cookies=[{"name": "CID", "value": "x", "domain": ".115.com"}])  # SEID/UID missing
        keeper = self.keeper(browser)
        self.assertFalse(keeper.save())
        self.assertFalse(self.path.exists())
        self.assertFalse(keeper.restore())

    def test_restore_stops_after_repeated_failures(self):
        browser = FakeBrowser(cookies=LOGIN)
        keeper = self.keeper(browser, retry=300)
        keeper.save()
        browser.cookies.clear()
        attempts = []
        keeper.restore = lambda: attempts.append(1)  # Pretends to inject; 115 keeps rejecting it.
        for _ in range(session.MAX_ATTEMPTS + 3):
            keeper.tick()
            self.now += 301  # Past the retry delay, so only the attempt cap stops it.
        self.assertEqual(len(attempts), session.MAX_ATTEMPTS)
        # A later tick that finds a login resets the counter, so a future loss retries.
        browser.cookies = {(c["name"], c["domain"]): dict(c) for c in LOGIN}
        keeper.tick()
        self.assertIsNone(keeper.attempt_mtime)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.cloud, self.watcher = ServerCloud(), FakeWatcher()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.app = app = server.App(self.cloud, self.watcher, "115", auth.encode("Check123"), self.temporary.name)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = ["--url", f"http://127.0.0.1:{self.httpd.server_address[1]}", "--user", "115", "--password", "Check123"]

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(self.base + list(args))
        return code, out.getvalue()

    def test_apis_status_and_download(self):
        code, out = self.run_cli("apis")
        self.assertEqual(code, 0)
        self.assertIn("/api/status", out)
        self.assertIn("inspect", out)
        with patch.object(server, "VERSION", "1.2.0"):
            self.assertEqual(self.run_cli("status")[1].splitlines(), ["115 已登录（user_id 42）", "服务端版本 1.2.0"])
            self.assertEqual(json.loads(self.run_cli("--json", "status")[1])["version"], "1.2.0")
        code, _ = self.run_cli("download", "--path", "/电影/合集")
        self.assertEqual(code, 0)
        self.assertIn(("download", [{"n": "合集", "pc": "pc1", "is_dir": True}]), self.cloud.calls)
        code, out = self.run_cli("--json", "offline")
        self.assertEqual([task["name"] for task in json.loads(out)["tasks"]], ["坏", "电影", "剧"])
        code, out = self.run_cli("offline")
        self.assertIn(f"进行中 {10 + client.RUNNING} · 失败 {10 + client.FAILED} · 已完成 {10 + client.DONE}", out)
        self.run_cli("offline", "--state", "done", "--page", "2")
        self.assertEqual(self.cloud.calls[-1], ("lists", [(client.DONE, 2)]))

    def test_wrong_password_reports_an_auth_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main([self.base[0], self.base[1], "--user", "115", "--password", "nope", "status"])
        self.assertEqual(code, 2)
        self.assertIn("认证", err.getvalue())

    def test_share_url_does_not_override_api_url(self):
        code, out = self.run_cli("--json", "share", "https://115cdn.com/s/abcd1234", "--code", "abcd")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 2)
        self.assertIn(("share", "abcd1234", "abcd", "https://115cdn.com/s/abcd1234?password=abcd", None), self.cloud.calls)

    def test_json_errors_include_http_status_and_local_errors_without_traceback(self):
        for args, expected in [(["--url", self.base[1], "--password", "wrong", "--json", "status"], 401),
                               (self.base + ["--json", "download", "--items", "bad-json"], None),
                               (["--json", "--invalid-flag"], None)]:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(args)
            self.assertEqual(code, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(json.loads(err.getvalue())["status"], expected)
            self.assertNotIn("Traceback", err.getvalue())

    def test_password_file_and_no_input_mode(self):
        password_file = Path(self.temporary.name) / "password"
        password_file.write_text("Check123\n")
        with patch.dict(os.environ, {"DOWNLOAD_API_PASSWORD_FILE": str(password_file), "DOWNLOAD_API_PASSWORD": "wrong"}):
            code, _ = self.run_cli("--password", "", "--no-input", "status")
        self.assertEqual(code, 0)
        with patch.dict(os.environ, {}, clear=True), patch.object(sys.stdin, "isatty", return_value=True), \
                patch.object(cli.getpass, "getpass", side_effect=AssertionError("must not prompt")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main(["--no-input", "--json", "status"])
        self.assertEqual(code, 2)
        self.assertIn("缺少密码", json.loads(err.getvalue())["error"])


if __name__ == "__main__":
    unittest.main()
