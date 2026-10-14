"""Keeps the box64 browser busy for 3 minutes of navigation, then reports its processes and log."""
import json
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "runtime/downloader")
from cdp import CDP  # noqa: E402

URLS = ["https://example.com/", "https://115.com/", "https://www.wikipedia.org/"]


def get(path):
    with urllib.request.urlopen("http://127.0.0.1:9222" + path, timeout=10) as response:
        return json.load(response)


def connect():
    page = next(item for item in get("/json/list") if item.get("type") == "page")
    return CDP(page["webSocketDebuggerUrl"])


client = connect()
done, attempts, times, failures = 0, 0, [], []
end = time.time() + 180
while time.time() < end:
    url = URLS[attempts % len(URLS)]
    attempts += 1
    start = time.time()
    try:
        # A new document has a new timeOrigin, which also covers redirects to other URLs.
        before = client.evaluate("performance.timeOrigin")
        client.call("Page.navigate", {"url": url}, timeout=60)
        while time.time() - start < 60:
            time.sleep(1)
            if client.evaluate("performance.timeOrigin") != before and client.evaluate("document.readyState") == "complete":
                break
        else:
            raise RuntimeError("not complete within 60s")
        times.append(time.time() - start)
        done += 1
    except Exception as error:  # noqa: BLE001 - every failure is a finding here
        failures.append(f"{url}: {type(error).__name__}: {error}"[:160])
        try:
            client.close()
            client = connect()
        except Exception as again:  # noqa: BLE001
            failures.append(f"reconnect: {type(again).__name__}: {again}"[:160])
            break

times.sort()
print(f"RESULT: {done}/{attempts} navigations completed in 3 minutes, median "
      f"{times[len(times) // 2] if times else 0:.1f}s, slowest {times[-1] if times else 0:.1f}s")
for item in failures[:5]:
    print("RESULT: failure: " + item)
rows = [line.split(None, 4) for line in subprocess.run(["ps", "-eo", "pid,etimes,rss,pcpu,args"], capture_output=True,
                                                       text=True).stdout.splitlines()[1:]]
browser = [row for row in rows if len(row) == 5 and "/usr/local/115Browser/115Browser" in row[4]]
main = [row for row in browser if "--type=" not in row[4]]
print(f"RESULT: {len(browser)} browser processes; main process up {main[0][1] if main else 'gone'}s; "
      f"RSS {sum(int(row[2]) for row in browser) / 1024:.0f} MiB; CPU {sum(float(row[3]) for row in browser):.0f}% "
      "(lifetime average, 100% = one core)")
log = open("/w/.poc/browser.log", encoding="utf-8", errors="replace").read()
for pattern in ("FATAL", "Segmentation fault", "SIGSEGV", "SIGILL", "Unimplemented Opcode", "Error loading needed lib"):
    if pattern in log:
        print(f"RESULT: browser.log contains {log.count(pattern)} x {pattern!r}")
if not main:
    raise SystemExit("the browser main process is gone")
if done < attempts / 2:
    raise SystemExit("most navigations failed")
