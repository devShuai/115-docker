"""Send finished offline (cloud) downloads on to the NAS through the browser.

The watch list lives in the profile volume so it survives container rebuilds.
115 offline task status: -1 failed, 0 stopped, 1 downloading, 2 finished.
"""
import json
from pathlib import Path
import threading
import time

WAITING, SENT, FAILED = "等待云下载完成", "已开始下载到 NAS", "云下载失败"
MAX_PAGES = 5
KEEP_SECONDS = 7 * 86400
# 115 finishes files it already has within seconds, so new tasks are checked often for a while.
FAST_INTERVAL, FAST_SECONDS = 10, 600


class Watcher:
    def __init__(self, cloud, path, interval=60, clock=time.time, log=print):
        self.cloud, self.path, self.interval, self.clock, self.log = cloud, Path(path), interval, clock, log
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.recent = None
        self.items = self._load()

    def _load(self):
        try:
            tasks = json.loads(self.path.read_text(encoding="utf-8")).get("tasks", {})
        except (OSError, ValueError, AttributeError):
            return {}
        return {key: value for key, value in tasks.items() if isinstance(value, dict) and "status" in value}

    def _save(self):
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps({"tasks": self.items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def watch(self, added):
        """added: offline add results with info_hash; only successful ones are watched."""
        now = int(self.clock())
        with self.lock:
            for task in added:
                if task.get("info_hash"):
                    self.items[task["info_hash"]] = {"name": task.get("name") or task.get("url", ""),
                                                     "url": task.get("url", ""), "added": now, "status": WAITING}
            self._save()
        self.recent = now
        self.wake.set()

    def delay(self):
        recent = self.recent is not None and self.clock() - self.recent < FAST_SECONDS
        return FAST_INTERVAL if recent else self.interval

    def snapshot(self):
        with self.lock:
            return [{"hash": key, **value} for key, value in
                    sorted(self.items.items(), key=lambda pair: -pair[1].get("added", 0))]

    def tick(self):
        now = self.clock()
        with self.lock:
            waiting = {key: dict(value) for key, value in self.items.items() if value["status"] == WAITING}
            expired = [key for key, value in self.items.items()
                       if value["status"] != WAITING and now - value.get("added", now) > KEEP_SECONDS]
            for key in expired:
                del self.items[key]
            if expired:
                self._save()
        if not waiting:
            return
        found = self.cloud.offline_find(set(waiting), MAX_PAGES)
        updates = {}
        for key, item in waiting.items():
            task = found.get(key)
            if task is None:
                if now - item["added"] > KEEP_SECONDS:
                    updates[key] = dict(item, status=FAILED, message="云下载列表里已找不到这个任务")
                continue
            item["name"] = task.get("name") or item["name"]
            status = int(task.get("status") or 0)
            if status == -1:
                updates[key] = dict(item, status=FAILED)
            elif status == 2 and str(task.get("move")) == "-1":
                updates[key] = dict(item, status=FAILED, message="文件转存到网盘失败")
            elif status == 2:
                # Finished tasks may still be moving into the drive; retry until the file appears.
                target = self.cloud.offline_result(task)
                if target:
                    self.cloud.download([target])
                    updates[key] = dict(item, status=SENT, sent=int(now))
                    self.log(f"Offline task finished; NAS download started: {item['name']}", flush=True)
        if updates:
            with self.lock:
                for key, value in updates.items():
                    if key in self.items:
                        self.items[key] = value
                self._save()

    def run_forever(self):
        while not self.stopped.is_set():
            self.wake.wait(self.delay())
            self.wake.clear()
            if self.stopped.is_set():
                return
            try:
                self.tick()
            except Exception as error:  # A failed poll must not stop later polls.
                self.log(f"Offline watcher: {type(error).__name__}: {error}", flush=True)
