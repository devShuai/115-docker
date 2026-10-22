"""115 operations performed inside the logged-in 115 Browser over its local DevTools port.

Each call runs in a 115.com tab and uses the endpoints and the browser bridge
(window.browserInterface) that 115's own pages use, so files are fetched by the
browser's download engine and appear in its transfer list. User input reaches
the page only as a JSON argument of Runtime.callFunctionOn.
"""
import json
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from cdp import CDP
import links

HOME = "https://115.com/?cid=0&offset=0&mode=wangpan"
TRANSFER_PAGE = "chrome://transfer-frame/"
# The stat filters of 115's own cloud-download tabs: 正在下载, 下载失败, 完成记录.
# Unfiltered, the list puts every unfinished task first (old failures included), so a
# task that has just finished can sit several pages deep.
RUNNING, FAILED, DONE = 12, 9, 11

_PRELUDE = r"""
  const uid = String(window.USER_ID || window.user_id || "");
  async function getJSON(url, init) {
    const response = await fetch(url, Object.assign({credentials: "include", cache: "no-store"}, init || {}));
    const text = await response.text();
    try { return JSON.parse(text); } catch (error) { return {state: false, not_json: true}; }
  }
  let signing = null;
  async function lixian(action, fields) {
    // 115's offline plugin (offline_wl/offline5.0.js) signs every request this way;
    // one signature serves all the requests of a call.
    signing = signing || getJSON("/?ct=offline&ac=space&_=" + Date.now());
    const space = await signing;
    if (!space.sign) return {state: false, login: space.not_json ? false : undefined, error: "无法获取云下载签名"};
    const body = new URLSearchParams(fields);
    body.append("uid", uid);
    body.append("sign", space.sign);
    body.append("time", space.time);
    return getJSON("/web/lixian/?ct=lixian&ac=" + action, {method: "POST", body});
  }
  function listDrive(cid, offset, order) {
    let url = "https://webapi.115.com/files?aid=1&cid=" + encodeURIComponent(cid) +
      "&offset=" + (offset || 0) + "&show_dir=1&limit=1150&format=json";
    // custom_order=1 applies this order to this request only; 115 keeps the order
    // the person chose for the folder in the drive.
    if (order) url += "&custom_order=1&o=" + encodeURIComponent(order.o) + "&asc=" + (order.asc ? 1 : 0) +
      "&fc_mix=" + order.fc_mix + "&natsort=" + order.natsort;
    return getJSON(url);
  }
  async function folderId(parts) {
    if (!parts.length) return "0";
    const found = await getJSON("https://webapi.115.com/files/getid?path=" + encodeURIComponent("/" + parts.join("/")));
    return found.state && found.id && String(found.id) !== "0" ? String(found.id) : null;
  }
  async function folderItem(cid) {
    // The folder itself, so it can be downloaded whole; the drive root cannot.
    if (!cid || String(cid) === "0") return null;
    const info = await getJSON("https://webapi.115.com/category/get?cid=" + encodeURIComponent(cid));
    return info.pick_code && info.file_name ? {n: info.file_name, pc: info.pick_code} : null;
  }
"""


def _page_function(body):
    return "async function (args) {" + _PRELUDE + body + "}"


STATUS = _page_function("""
  return {logged_in: !!uid, user_id: uid,
          bridge: !!(window.browserInterface && browserInterface.CreateDownloadTask)};""")
ADD_OFFLINE = _page_function("""
  if (!uid) return {login: false};
  const fields = {savepath: "", wp_path_id: args.folder || ""};
  args.urls.forEach((url, index) => { fields["url[" + index + "]"] = url; });
  return lixian("add_task_urls", fields);""")
LIST_OFFLINE = _page_function("""
  if (!uid) return {login: false};
  const pages = await Promise.all(args.lists.map(list => {
    const fields = {page: String(list.page || 1)};
    if (list.stat) fields.stat = String(list.stat);
    return lixian("task_lists", fields);
  }));
  return pages.find(page => !page.state) || {state: true, lists: pages};""")
BROWSE_DRIVE = _page_function("""
  if (!uid) return {login: false};
  const page = await listDrive(args.cid, args.offset, args.order);
  return page.not_json ? {login: false} : {page, folder: await folderItem(args.cid)};""")
OPEN_PATH = _page_function("""
  if (!uid) return {login: false};
  const name = args.parts[args.parts.length - 1];
  const parent = await folderId(args.parts.slice(0, -1));
  if (parent === null) return {missing: true};
  const page = await listDrive(parent, 0, args.order);
  if (page.not_json) return {login: false};
  const entry = (page.data || []).find(item => item.n === name);
  if (entry && !entry.fid) return {page: await listDrive(String(entry.cid), 0, args.order), folder: {n: entry.n, pc: entry.pc}};
  if (entry) return {page, folder: await folderItem(parent), focus: name};
  // Folders with more than one page of entries: ask for the folder id directly.
  const direct = await folderId(args.parts);
  return direct === null ? {missing: true} : {page: await listDrive(direct, 0, args.order), folder: await folderItem(direct)};""")
FOLDER_RESULT = _page_function("""
  if (!uid) return {login: false};
  const info = await getJSON("https://webapi.115.com/category/get?cid=" + encodeURIComponent(args.cid));
  if (info.not_json) return {login: false};
  if (info.file_name === args.name && info.pick_code)
    return {item: {n: info.file_name, pc: info.pick_code, fid: "", cid: args.cid}};
  const page = await listDrive(args.cid);
  const entry = (page.data || []).find(item => item.n === args.name);
  return entry ? {item: {n: entry.n, pc: entry.pc, fid: entry.fid || "", cid: entry.cid || ""}} : {missing: true};""")
DOWNLOAD = _page_function("""
  if (!uid) return {login: false};
  if (!window.browserInterface || !browserInterface.CreateDownloadTask) return {bridge: false};
  const payload = {list: args.items, count: args.items.length, ref_url: location.href};
  browserInterface.CreateDownloadTask(encodeURIComponent(JSON.stringify(payload)));
  return {state: true};""")
# The drive's own search box: webapi.115.com/files/search matches names across the drive (cid 0) or in a folder.
SEARCH_DRIVE = _page_function("""
  if (!uid) return {login: false};
  const query = new URLSearchParams({aid: "1", cid: args.cid || "0", search_value: args.q, offset: String(args.offset || 0),
                                     limit: String(args.limit), show_dir: "1", format: "json"});
  const page = await getJSON("https://webapi.115.com/files/search?" + query);
  return page.not_json ? {login: false} : page;""")
SEARCH_LIMIT = 100
SHARE_LIST = _page_function("""
  const query = new URLSearchParams({share_code: args.share_code, receive_code: args.receive_code,
                                     cid: args.cid || "", offset: String(args.offset || 0), limit: "1150"});
  return getJSON("https://webapi.115.com/share/snap?" + query);""")
SHARE_DOWNLOAD = _page_function("""
  if (!uid) return {login: false};
  if (!window.browserInterface || !browserInterface.CreateSharedTask) return {bridge: false};
  const payload = {list: args.items, count: args.items.length, ref_url: args.ref_url, share_code: args.share_code};
  if (args.receive_code) payload.receive_code = args.receive_code;
  browserInterface.CreateSharedTask(encodeURIComponent(JSON.stringify(payload)));
  return {state: true};""")
# 115's web drive deletes into the recycle bin: POST webapi.115.com/rb/delete with fid[0], fid[1], ...
DELETE_FILES = _page_function("""
  if (!uid) return {login: false};
  const body = new URLSearchParams();
  args.ids.forEach((id, index) => body.append("fid[" + index + "]", id));
  if (args.pid) body.append("pid", args.pid);
  body.append("ignore_warn", "1");
  const result = await getJSON("https://webapi.115.com/rb/delete", {method: "POST", body});
  return result.not_json ? {login: false} : result;""")
# The offline plugin's task_del: hash[0], hash[1], ...; flag=1 also deletes what the tasks saved in the drive.
DELETE_OFFLINE = _page_function("""
  if (!uid) return {login: false};
  const fields = {flag: args.files ? "1" : "0"};
  args.hashes.forEach((hash, index) => { fields["hash[" + index + "]"] = hash; });
  const result = await lixian("task_del", fields);
  return result.not_json ? {login: false} : result;""")
# Read-only: the browser's transfer list page (transfer-frame) keeps live state for each download.
# _status: 0 downloading, 1 paused, 2 finished, 3 failed, 4 waiting.
TRANSFERS = """async function () {
  const data = window.downloadsData || {};
  return Object.keys(data).map(key => {
    const item = data[key];
    return {id: String(item._id || item.id || key), name: item._file_name || "", status: Number(item._status),
            paused: !!item._paused, progress: Number(item._progress) || 0, speed: String(item._speed || ""),
            remain: Number(item._remain_time_sec) || 0, received: String(item._receive || ""),
            total: String(item._total || ""), folder: !!item._is_folder, hash: Number(item._hash_progress) || 0,
            error: String(item._error_msg || ""), ended: String(item._end_time || "")};
  });
}"""


class BrowserUnavailable(RuntimeError):
    """The 115 Browser or its DevTools port is not running."""


class NotLoggedIn(RuntimeError):
    """115 is not logged in inside the browser."""


class VerificationRequired(RuntimeError):
    """115 asked for a captcha (errcode 911)."""


def _whole(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _message(result, fallback):
    for key in ("error_msg", "error", "msg", "message"):
        if result.get(key):
            return str(result[key])
    return fallback


class Cloud115:
    def __init__(self, port=9222, connect=CDP, opener=urllib.request.urlopen, home=HOME):
        self.port, self.connect, self.opener, self.home = port, connect, opener, home
        self.lock = threading.Lock()
        self.target = None

    def _json(self, path):
        try:
            with self.opener(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
                return json.load(response)
        except OSError as error:
            raise BrowserUnavailable("115 浏览器未运行，或没有开启本地调试端口") from error

    def _pages(self):
        return [item for item in self._json("/json/list") if item.get("type") == "page"]

    def _tab(self, create):
        pages = self._pages()
        for page in pages:
            if page.get("id") == self.target:
                return page
        for page in pages:
            url = urlsplit(page.get("url", ""))
            if url.hostname in ("115.com", "www.115.com") and "mode=wangpan" in url.query:
                self.target = page["id"]
                return page
        if not create:
            return None
        # A background tab keeps the person's own tabs untouched.
        browser = self.connect(self._json("/json/version")["webSocketDebuggerUrl"])
        try:
            self.target = browser.call("Target.createTarget", {"url": self.home, "background": True})["targetId"]
        finally:
            browser.close()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            page = next((item for item in self._pages() if item.get("id") == self.target), None)
            if page and page.get("webSocketDebuggerUrl"):
                client = self.connect(page["webSocketDebuggerUrl"])
                try:
                    if client.evaluate("document.readyState") == "complete":
                        return page
                finally:
                    client.close()
            time.sleep(0.5)
        raise BrowserUnavailable("打开 115 网盘页面超时")

    def _evaluate(self, page, declaration, argument, timeout):
        try:
            client = self.connect(page["webSocketDebuggerUrl"])
        except OSError as error:
            raise BrowserUnavailable("无法连接 115 浏览器") from error
        try:
            return client.run(declaration, argument, timeout)
        finally:
            client.close()

    def run(self, declaration, argument=None, timeout=60, create=True):
        with self.lock:
            page = self._tab(create)
            return None if page is None else self._evaluate(page, declaration, argument, timeout)

    def _call(self, declaration, argument=None, timeout=60):
        result = self.run(declaration, argument, timeout) or {}
        if result.get("login") is False:
            raise NotLoggedIn("115 未登录，请先在远程桌面里扫码登录")
        if result.get("bridge") is False:
            raise RuntimeError("当前页面没有 115 浏览器下载接口")
        if 911 in (result.get("errcode"), result.get("errNo"), result.get("code")) or str(result.get("errcode")) == "911":
            raise VerificationRequired("115 要求完成安全验证，请在远程桌面的 115 浏览器里操作一次")
        return result

    def status(self):
        """Has no side effects: it never opens a tab."""
        try:
            self._json("/json/version")
        except BrowserUnavailable:
            return {"browser": False, "logged_in": False}
        try:
            result = self.run(STATUS, create=False)
        except (BrowserUnavailable, RuntimeError):
            result = None
        if result is None:
            return {"browser": True, "logged_in": None}
        return {"browser": True, "logged_in": result["logged_in"], "user_id": result["user_id"]}

    def add_offline(self, urls, folder=""):
        result = self._call(ADD_OFFLINE, {"urls": urls, "folder": folder})
        if not result.get("state"):
            raise RuntimeError(_message(result, "添加云下载失败"))
        return result

    def offline_lists(self, lists):
        """Pages of the offline task list in one round trip; lists holds (stat, page) pairs, stat 0 = unfiltered."""
        result = self._call(LIST_OFFLINE, {"lists": [{"stat": stat, "page": page} for stat, page in lists]})
        if not result.get("state"):
            raise RuntimeError(_message(result, "读取云下载任务失败"))
        return result["lists"]

    def offline_tasks(self, page=1, stat=0):
        return self.offline_lists([(stat, page)])[0]

    def offline_find(self, hashes, pages=5):
        """info_hash -> task, looked up newest first in the running, failed and finished lists."""
        wanted, found, stats = set(hashes), {}, [RUNNING, FAILED, DONE]
        for page in range(1, pages + 1):
            if not stats or wanted <= found.keys():
                break
            results = self.offline_lists([(stat, page) for stat in stats])
            for result in results:
                found.update((task["info_hash"], task) for task in result.get("tasks") or []
                             if task.get("info_hash") in wanted)
            stats = [stat for stat, result in zip(stats, results) if page < int(result.get("page_count") or 1)]
        return found

    def find_offline(self, info_hash, pages=5):
        return self.offline_find({info_hash}, pages).get(info_hash)

    @staticmethod
    def _listing(result, order=None):
        page = result.get("page") or {}
        if not page.get("state"):
            raise RuntimeError(_message(page, "读取网盘目录失败"))
        listing = links.drive_listing(page, order)
        folder = result.get("folder")
        listing["folder"] = {"n": folder["n"], "pc": folder["pc"], "is_dir": True} if folder else None
        if result.get("focus"):
            listing["focus"] = result["focus"]
        return listing

    def browse_drive(self, cid, offset=0, order=None):
        return self._listing(self._call(BROWSE_DRIVE, {"cid": str(cid or "0"), "offset": offset, "order": order}), order)

    def search_drive(self, q, offset=0, cid="0"):
        """Files and folders whose names match q, SEARCH_LIMIT at a time, each with the folder it sits in."""
        page = self._call(SEARCH_DRIVE, {"q": q, "offset": offset, "cid": str(cid or "0"), "limit": SEARCH_LIMIT})
        if not page.get("state"):
            raise RuntimeError(_message(page, "搜索网盘失败"))
        return links.search_listing(page, q, offset)

    def open_path(self, parts, order=None):
        """A drive path -> the listing of that folder, or of a file's folder with the file in focus."""
        result = self._call(OPEN_PATH, {"parts": parts, "order": order})
        if result.get("missing"):
            raise ValueError("网盘里找不到：/" + "/".join(parts))
        return self._listing(result, order)

    def resolve(self, parts):
        listing = self.open_path(parts)
        if "focus" in listing:
            entry = next(item for item in listing["items"] if item["name"] == listing["focus"])
            return {"n": entry["name"], "pc": entry["pc"], "is_dir": False}
        if not listing["folder"]:
            raise RuntimeError("读取网盘目录失败")
        return listing["folder"]

    def download(self, items):
        self._call(DOWNLOAD, {"items": items})

    def delete_files(self, ids, pid=""):
        """Drive files or folders -> 115's recycle bin."""
        result = self._call(DELETE_FILES, {"ids": list(ids), "pid": str(pid or "")})
        if not result.get("state"):
            raise RuntimeError(_message(result, "删除网盘文件失败"))
        return result

    def delete_offline(self, hashes, files=False):
        """Offline tasks, and with files=True what they saved in the drive."""
        result = self._call(DELETE_OFFLINE, {"hashes": list(hashes), "files": bool(files)})
        if not result.get("state"):
            raise RuntimeError(_message(result, "删除云下载任务失败"))
        return result

    def offline_result(self, task):
        """The cloud file or folder a finished offline task produced, or None while it is not ready."""
        # move: 0 while the files are still being moved into the drive, -1 when that failed.
        if int(task.get("status") or 0) != 2 or str(task.get("move", 1)) in ("0", "-1"):
            return None
        if task.get("pick_code") and str(task.get("file_category")) in ("0", "1"):
            # file_category 0 is a folder (file_id is that folder); 1 is a file (file_id is its folder).
            return {"n": task.get("name") or "", "pc": task["pick_code"], "is_dir": str(task["file_category"]) == "0"}
        cid, name = str(task.get("file_id") or ""), task.get("name") or ""
        if not cid or not name:
            return None
        result = self._call(FOLDER_RESULT, {"cid": cid, "name": name})
        return links.drive_item(result["item"]) if "item" in result else None

    def offline_content(self, task, order=None):
        """What a finished offline task produced, as a listing to pick from for the NAS."""
        item = self.offline_result(task)
        if item is None:
            raise ValueError("云下载还没有完成，或文件还在转存到网盘")
        if item["is_dir"] and task.get("file_id"):
            listing = self.browse_drive(str(task["file_id"]), order=order)
            listing["folder"] = item
        else:
            listing = {"kind": "drive", "cid": str(task.get("file_id") or ""), "path": [], "folder": None,
                       "count": 1, "offset": 0, "truncated": False,
                       "items": [{"id": item["pc"], "name": item["n"], "pc": item["pc"], "is_dir": False,
                                  "size": _whole(task.get("size")), "time": _whole(task.get("last_update")),
                                  "cid": ""}]}
        listing["result"] = item
        return listing

    def browse_share(self, share_code, receive_code, cid="", offset=0):
        snap = self._call(SHARE_LIST, {"share_code": share_code, "receive_code": receive_code, "cid": cid,
                                       "offset": offset})
        if not snap.get("state"):
            raise ValueError(_message(snap, "分享链接无效、已过期或提取码错误"))
        return links.share_listing(snap, cid, offset)

    def share(self, share_code, receive_code, page_url, items=None):
        """Download share items to the NAS; without items, everything at the share's top level."""
        if items is None:
            items = [{"n": entry["name"], "pc": "", "is_dir": entry["is_dir"], "sc": entry["sc"]}
                     for entry in self.browse_share(share_code, receive_code)["items"]]
        if not items:
            raise ValueError("分享里没有可下载的文件")
        self._call(SHARE_DOWNLOAD, {"items": items, "share_code": share_code,
                                    "receive_code": receive_code, "ref_url": page_url})
        return [item["n"] for item in items]

    def transfers(self):
        """Live transfers from the browser's transfer list page, or None while that page is closed.

        It reads a different page than the 115.com tab, so it does not wait for slower 115 calls.
        """
        page = next((item for item in self._pages() if item.get("url", "").startswith(TRANSFER_PAGE)), None)
        if page is None or not page.get("webSocketDebuggerUrl"):
            return None
        return self._evaluate(page, TRANSFERS, None, 15)
