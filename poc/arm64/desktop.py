"""Waits for DevTools, then checks 115.com in the desktop browser running under box64."""
import base64
import json
import sys
import time
import urllib.request

sys.path.insert(0, "runtime/downloader")
from cdp import CDP  # noqa: E402


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:9222" + path, timeout=5) as response:
        return json.load(response)


def wait(what, check, limit):
    start = time.time()
    while time.time() - start < limit:
        try:
            value = check()
            if value:
                return value, time.time() - start
        except (OSError, ValueError, RuntimeError, KeyError):
            pass
        time.sleep(2)
    raise SystemExit(f"{what} not ready after {limit}s")


def loaded_115():
    for page in get("/json/list"):
        if page.get("type") == "page" and "115.com" in page.get("url", ""):
            client = CDP(page["webSocketDebuggerUrl"])
            if client.evaluate("document.readyState") == "complete":
                return client
            client.close()
    return None


version, took = wait("DevTools", lambda: get("/json/version"), 300)
print(f"RESULT: DevTools up after {took:.0f}s: {version.get('Browser')}; "
      f"user agent names 115Browser: {'115Browser' in version.get('User-Agent', '')}")
client, took = wait("115.com", loaded_115, 300)
info = json.loads(client.evaluate(
    "JSON.stringify({title: document.title, text: document.body ? document.body.innerText.length : 0,"
    " bridge: !!window.browserInterface,"
    " download: !!(window.browserInterface && browserInterface.CreateDownloadTask)})"))
print(f"RESULT: 115.com loaded {took:.0f}s after DevTools: title {info['title']!r}, {info['text']} characters of text")
print(f"RESULT: 115 bridge window.browserInterface: {info['bridge']}, CreateDownloadTask: {info['download']}")
bench = client.evaluate("(() => { const t = performance.now(); let x = 0;"
                        " for (let i = 0; i < 2e7; i++) x = (x + i * 7) % 1000003; return Math.round(performance.now() - t); })()")
print(f"RESULT: JS loop of 2e7 iterations: {bench} ms")
shot = client.call("Page.captureScreenshot", {"format": "png"}, timeout=60)
with open("/w/.poc/115.png", "wb") as handle:
    handle.write(base64.b64decode(shot["data"]))
print("RESULT: screenshot saved as 115.png in the arm64-box64-poc artifact")
client.close()
