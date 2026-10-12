"""Keep 115 logged in across browser and container restarts.

115 stores its login as session cookies on 115.com. Chromium drops session
cookies unless it restores the previous session, and a container restart is
recorded as a crash, so `--restore-last-session` alone does not keep the login.

This saves the login cookies to the profile volume while logged in and, when the
browser comes up without them, injects them back as persistent cookies. The file
lives in the profile volume (mode 600), the same place and trust boundary as the
rest of the login state, so it survives rebuilding the container.
"""
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

import client

# The cookies 115 sets for a login. CID/SEID/UID identify the session; the rest
# are the other stable (non-rotating) login cookies, kept so the restored session
# is as complete as a fresh scan.
LOGIN_COOKIES = ("UID", "CID", "SEID", "KID", "USERSESSIONID", "GST")
REQUIRED = frozenset(("CID", "SEID", "UID"))
KEEP_DAYS = 365
MAX_ATTEMPTS = 3  # Stop re-injecting a saved login once 115 keeps rejecting it.


def _is_115(domain):
    host = (domain or "").lstrip(".")
    return host == "115.com" or host.endswith(".115.com") or host == "anxia.com" or host.endswith(".anxia.com")


def login_cookies(cdp):
    cookies = cdp.call("Storage.getCookies").get("cookies", [])
    return [cookie for cookie in cookies if cookie.get("name") in LOGIN_COOKIES and _is_115(cookie.get("domain"))]


class SessionKeeper:
    def __init__(self, cloud, path, interval=60, idle_interval=2, startup_delay=3, retry=300, clock=time.time, log=print):
        self.cloud, self.path = cloud, Path(path)
        # Poll slowly while logged in (just to keep the file fresh), fast while logged
        # out or the browser is closed, so reopening it restores the login within seconds.
        self.interval, self.idle_interval = interval, idle_interval
        self.startup_delay, self.retry = startup_delay, retry
        self.clock, self.log = clock, log
        self.stopped = threading.Event()
        self.logged_in = False
        self.saved = None            # The cookie values last written, to skip rewrites.
        self.attempt_mtime = None    # The saved file a restore was last attempted from.
        self.attempts = 0
        self.last_attempt = 0.0

    def _browser(self):
        """A CDP connection to the browser endpoint, where Storage.* act on every tab."""
        return self.cloud.connect(self.cloud._json("/json/version")["webSocketDebuggerUrl"])

    def _present(self, cdp):
        return {cookie["name"]: cookie.get("value") for cookie in login_cookies(cdp)}

    def save(self):
        """Write the current login cookies if 115 is logged in. Returns whether it is."""
        cdp = self._browser()
        try:
            found = login_cookies(cdp)
        finally:
            cdp.close()
        values = {cookie["name"]: cookie.get("value") for cookie in found}
        if not REQUIRED <= values.keys():
            return False
        if values != self.saved:
            self._write(found)
            self.saved = values
        return True

    def _write(self, cookies):
        keep = ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite")
        data = {"saved": int(self.clock()),
                "cookies": [{key: cookie.get(key) for key in keep} for cookie in cookies]}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def _load(self):
        try:
            cookies = json.loads(self.path.read_text(encoding="utf-8")).get("cookies") or []
        except (OSError, ValueError, AttributeError):
            return None
        valid = [cookie for cookie in cookies
                 if isinstance(cookie, dict) and cookie.get("name") and cookie.get("value") and _is_115(cookie.get("domain"))]
        return valid or None

    def restore(self):
        """Inject saved login cookies when the browser has none. Returns whether it injected."""
        saved = self._load()
        if not saved:
            return False
        cdp = self._browser()
        try:
            if REQUIRED <= self._present(cdp).keys():
                return False
            expires = self.clock() + KEEP_DAYS * 86400
            cookies = []
            for cookie in saved:
                # A far-future expiry makes the cookie persistent, so it also survives
                # a plain restart without depending on session restore.
                param = {"name": cookie["name"], "value": cookie["value"], "domain": cookie["domain"],
                         "path": cookie.get("path") or "/", "secure": bool(cookie.get("secure")),
                         "httpOnly": bool(cookie.get("httpOnly")), "expires": expires}
                if cookie.get("sameSite") in ("Strict", "Lax", "None"):
                    param["sameSite"] = cookie["sameSite"]
                cookies.append(param)
            cdp.call("Storage.setCookies", {"cookies": cookies})
        finally:
            cdp.close()
        self._reload_115_tabs()
        self.log("Restored the saved 115 login cookies.", flush=True)
        return True

    def _reload_115_tabs(self):
        for page in self.cloud._pages():
            if (urlsplit(page.get("url", "")).hostname or "").endswith("115.com") and page.get("webSocketDebuggerUrl"):
                try:
                    cdp = self.cloud.connect(page["webSocketDebuggerUrl"])
                    try:
                        cdp.call("Page.reload", {"ignoreCache": False})
                    finally:
                        cdp.close()
                except OSError:
                    pass

    def tick(self):
        try:
            self.logged_in = self.save()
        except client.BrowserUnavailable:
            self.logged_in = False  # Browser closed; poll fast to catch it reopening.
            return
        if self.logged_in:
            self.attempt_mtime, self.attempts = None, 0
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        now = self.clock()
        if self.attempt_mtime != mtime:
            self.attempt_mtime, self.attempts = mtime, 0
        if self.attempts >= MAX_ATTEMPTS or (self.attempts and now - self.last_attempt < self.retry):
            return
        self.attempts, self.last_attempt = self.attempts + 1, now
        try:
            self.restore()
        except client.BrowserUnavailable:
            pass

    def _delay(self):
        return self.interval if self.logged_in else self.idle_interval

    def run_forever(self):
        if self.stopped.wait(self.startup_delay):
            return
        while True:
            try:
                self.tick()
            except Exception as error:  # A failed poll must not stop later polls.
                self.log(f"Session keeper: {type(error).__name__}: {error}", flush=True)
            if self.stopped.wait(self._delay()):
                return


def main():
    import os
    import sys
    profile = os.environ.get("PROFILE_DIR_IN_CONTAINER", "/etc/115")
    cloud = client.Cloud115(port=int(os.environ.get("BROWSER_DEBUG_PORT", "9222")))
    keeper = SessionKeeper(cloud, Path(profile) / "115-session.json")
    action = sys.argv[1] if len(sys.argv) > 1 else "once"
    if action == "save":
        print("saved" if keeper.save() else "not logged in")
    elif action == "restore":
        print("restored" if keeper.restore() else "nothing to restore")
    else:
        keeper.tick()


if __name__ == "__main__":
    main()
