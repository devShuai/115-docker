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

_PRELUDE = r"""
  const uid = String(window.USER_ID || window.user_id || "");
  async function getJSON(url, init) {
    const response = await fetch(url, Object.assign({credentials: "include", cache: "no-store"}, init || {}));
    const text = await response.text();
    try { return JSON.parse(text); } catch (error) { return {state: false, not_json: true}; }
  }
  async function lixian(action, fields) {
    // 115's offline plugin (offline_wl/offline5.0.js) signs every request this way.
    const space = await getJSON("/?ct=offline&ac=space&_=" + Date.now());
    if (!space.sign) return {state: false, login: space.not_json ? false : undefined, error: "无法获取云下载签名"};
    const body = new URLSearchParams(fields);
    body.append("uid", uid);
    body.append("sign", space.sign);
    body.append("time", space.time);
    return getJSON("/web/lixian/?ct=lixian&ac=" + action, {method: "POST", body});
  }
  async function findIn(cid, name) {
    for (let offset = 0; ; offset += 1150) {
      const page = await getJSON("https://webapi.115.com/files?aid=1&cid=" + encodeURIComponent(cid) +
        "&o=user_ptime&asc=0&offset=" + offset + "&show_dir=1&limit=1150&format=json");
      if (page.not_json) return {login: false};
      if (!page.state) return {error: page.error || page.msg || "读取网盘目录失败"};
      const entry = (page.data || []).find(item => item.n === name);
      if (entry) return {item: {n: entry.n, pc: entry.pc, fid: entry.fid || "", cid: entry.cid || ""}};
      if (offset + 1150 >= Number(page.count || 0)) return {missing: true};
    }
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
  return lixian("task_lists", {page: String(args.page || 1)});""")
RESOLVE_PATH = _page_function("""
  if (!uid) return {login: false};
  const parents = args.parts.slice(0, -1);
  let cid = "0";
  if (parents.length) {
    const found = await getJSON("https://webapi.115.com/files/getid?path=" + encodeURIComponent("/" + parents.join("/")));
    if (found.not_json) return {login: false};
    if (!found.state || !found.id || String(found.id) === "0") return {missing: true};
    cid = String(found.id);
  }
  return findIn(cid, args.parts[args.parts.length - 1]);""")
FOLDER_RESULT = _page_function("""
  if (!uid) return {login: false};
  const info = await getJSON("https://webapi.115.com/category/get?cid=" + encodeURIComponent(args.cid));
  if (info.not_json) return {login: false};
  if (info.file_name === args.name && info.pick_code)
    return {item: {n: info.file_name, pc: info.pick_code, fid: "", cid: args.cid}};
  return findIn(args.cid, args.name);""")
DOWNLOAD = _page_function("""
  if (!uid) return {login: false};
  if (!window.browserInterface || !browserInterface.CreateDownloadTask) return {bridge: false};
  const payload = {list: args.items, count: args.items.length, ref_url: location.href};
  browserInterface.CreateDownloadTask(encodeURIComponent(JSON.stringify(payload)));
  return {state: true};""")
SHARE_LIST = _page_function("""
  const query = new URLSearchParams({share_code: args.share_code, receive_code: args.receive_code,
                                     cid: "", offset: "0", limit: "1150"});
  return getJSON("https://webapi.115.com/share/snap?" + query);""")
SHARE_DOWNLOAD = _page_function("""
  if (!uid) return {login: false};
  if (!window.browserInterface || !browserInterface.CreateSharedTask) return {bridge: false};
  const payload = {list: args.items, count: args.items.length, ref_url: args.ref_url, share_code: args.share_code};
  if (args.receive_code) payload.receive_code = args.receive_code;
  browserInterface.CreateSharedTask(encodeURIComponent(JSON.stringify(payload)));
  return {state: true};""")


class BrowserUnavailable(RuntimeError):
    """The 115 Browser or its DevTools port is not running."""


class NotLoggedIn(RuntimeError):
    """115 is not logged in inside the browser."""


class VerificationRequired(RuntimeError):
    """115 asked for a captcha (errcode 911)."""


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

    def _tab(self, create):
        pages = [item for item in self._json("/json/list") if item.get("type") == "page"]
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
            page = next((item for item in self._json("/json/list") if item.get("id") == self.target), None)
            if page and page.get("webSocketDebuggerUrl"):
                client = self.connect(page["webSocketDebuggerUrl"])
                try:
                    if client.evaluate("document.readyState") == "complete":
                        return page
                finally:
                    client.close()
            time.sleep(0.5)
        raise BrowserUnavailable("打开 115 网盘页面超时")

    def run(self, declaration, argument=None, timeout=60, create=True):
        with self.lock:
            try:
                page = self._tab(create)
                if page is None:
                    return None
                client = self.connect(page["webSocketDebuggerUrl"])
            except OSError as error:
                raise BrowserUnavailable("无法连接 115 浏览器") from error
            try:
                return client.run(declaration, argument, timeout)
            finally:
                client.close()

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

    def offline_tasks(self, page=1):
        result = self._call(LIST_OFFLINE, {"page": page})
        if not result.get("state"):
            raise RuntimeError(_message(result, "读取云下载任务失败"))
        return result

    def resolve(self, parts):
        result = self._call(RESOLVE_PATH, {"parts": parts})
        if result.get("missing"):
            raise ValueError("网盘里找不到：/" + "/".join(parts))
        if "item" not in result:
            raise RuntimeError(_message(result, "读取网盘目录失败"))
        return links.drive_item(result["item"])

    def download(self, items):
        self._call(DOWNLOAD, {"items": items})

    def offline_result(self, task):
        """The cloud file or folder an offline task produced, or None while it is not ready."""
        cid, name = str(task.get("file_id") or ""), task.get("name") or ""
        if not cid or not name:
            return None
        result = self._call(FOLDER_RESULT, {"cid": cid, "name": name})
        return links.drive_item(result["item"]) if "item" in result else None

    def share(self, share_code, receive_code, page_url):
        listing = self._call(SHARE_LIST, {"share_code": share_code, "receive_code": receive_code})
        if not listing.get("state"):
            raise ValueError(_message(listing, "分享链接无效、已过期或提取码错误"))
        items = [links.share_item(entry) for entry in (listing.get("data") or {}).get("list") or []]
        if not items:
            raise ValueError("分享里没有可下载的文件")
        self._call(SHARE_DOWNLOAD, {"items": items, "share_code": share_code,
                                    "receive_code": receive_code, "ref_url": page_url})
        return [item["n"] for item in items]
