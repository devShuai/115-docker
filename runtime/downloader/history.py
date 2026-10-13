"""Read the browser's own download list. 115 Browser keeps Chromium's history as HistoryEx."""
from pathlib import Path
import sqlite3

# Chromium download states; 115 adds its accelerated transfers to the same table.
STATES = {0: "下载中", 1: "已完成", 2: "已取消", 3: "已中断", 4: "已中断"}
KEYS = {0: "downloading", 1: "done", 2: "canceled", 3: "failed", 4: "failed"}
_WINDOWS_EPOCH_OFFSET = 11644473600


def recent_downloads(profile_dir, limit=30):
    database = next((path for path in (Path(profile_dir) / "Default/HistoryEx",
                                       Path(profile_dir) / "Default/History") if path.is_file()), None)
    if database is None:
        return []
    # The browser holds the database open; read the last committed state without locking it.
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, target_path, received_bytes, total_bytes, state, start_time, end_time "
            "FROM downloads ORDER BY start_time DESC LIMIT ?", (limit,)).fetchall()
    finally:
        connection.close()
    return [{"id": identity, "name": Path(target).name or target, "path": target, "received": received,
             "total": total, "state": STATES.get(state, str(state)), "key": KEYS.get(state, "failed"),
             "started": int(start / 1_000_000 - _WINDOWS_EPOCH_OFFSET) if start else None,
             "finished": int(end / 1_000_000 - _WINDOWS_EPOCH_OFFSET) if end else None}
            for identity, target, received, total, state, start, end in rows]
