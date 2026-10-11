"""Parse what people submit: offline links, 115 drive paths and 115 share links.

The item formats mirror 115's own web code: the drive dialog
(wl_disk2014/core/DG/DownFile.js) and the share page (115cdn.com/s/...).
"""
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import parse_qs, urlsplit

MAX_LINKS = 100
CHINA = timezone(timedelta(hours=8))
_OFFLINE = re.compile(r"(?:(?:https?|ftp)://[^\s/]+\S*|magnet:\?\S*xt=urn:btih:[0-9A-Za-z]{32,40}\S*|ed2k://\|file\|\S+)",
                      re.IGNORECASE)
_SHARE_URL = re.compile(r"https?://(?:[\w-]+\.)?(?:115|115cdn|anxia)\.com/s/([A-Za-z0-9]{4,32})(\?[^\s#]*)?",
                        re.IGNORECASE)
_RECEIVE_TEXT = re.compile(r"(?:访问码|提取码|密码|password)[:：=\s]*([A-Za-z0-9]{4})", re.IGNORECASE)
_RECEIVE_CODE = re.compile(r"[A-Za-z0-9]{4}")


def offline_links(text):
    """One link per line; 115 offline download accepts these schemes."""
    links = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not _OFFLINE.fullmatch(line):
            raise ValueError("无法识别的链接（每行一个磁力、ed2k、HTTP 或 FTP 链接）：" + line[:80])
        if line not in links:
            links.append(line)
    if not links:
        raise ValueError("请至少填写一个链接")
    if len(links) > MAX_LINKS:
        raise ValueError(f"一次最多提交 {MAX_LINKS} 个链接")
    return links


def drive_path(text):
    """'/电影/某文件夹' -> ['电影', '某文件夹']. The drive root itself cannot be downloaded."""
    path = (text or "").strip().replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    if not parts:
        raise ValueError("请填写网盘里的文件或文件夹路径，例如 /电影/某文件夹")
    if any(part in (".", "..") for part in parts) or len(path) > 2048:
        raise ValueError("网盘路径无效")
    return parts


def share_link(text, receive_code=""):
    """Return (share_code, receive_code, page_url) from a pasted 115 share link."""
    match = _SHARE_URL.search(text or "")
    if not match:
        raise ValueError("请填写 115 分享链接，例如 https://115cdn.com/s/sw...?password=abcd")
    code = (receive_code or "").strip()
    query = parse_qs((match.group(2) or "?")[1:])
    code = code or (query.get("password") or query.get("receive_code") or [""])[0]
    if not code:
        found = _RECEIVE_TEXT.search(text)
        code = found.group(1) if found else ""
    if code and not _RECEIVE_CODE.fullmatch(code):
        raise ValueError("提取码应为 4 位字母或数字")
    page = urlsplit(match.group(0))
    url = f"{page.scheme}://{page.netloc}/s/{match.group(1)}" + (f"?password={code}" if code else "")
    return match.group(1), code, url


def drive_item(entry):
    """A drive listing entry -> CreateDownloadTask item. Files carry fid; folders do not."""
    return {"n": entry["n"], "pc": entry["pc"], "is_dir": not entry.get("fid")}


def share_item(entry):
    """A share listing entry -> CreateSharedTask item, as built by 115's share page (p == 0 is a folder)."""
    is_dir = str(entry["p"]) == "0" if "p" in entry else not entry.get("fid")
    return {"n": entry["n"], "pc": "", "is_dir": is_dir,
            "sc": str(entry.get("cid") if is_dir else entry.get("fid") or "")}


def _size(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _epoch(value):
    """115 times are Unix seconds, or 'YYYY-MM-DD HH:MM' in China time on some share pages."""
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", text)
    if not match:
        return 0
    parts = [int(part or 0) for part in match.groups()]
    return int(datetime(*parts, tzinfo=CHINA).timestamp())


# Sort choices the page offers -> 115 list parameters. Name and size keep folders
# first; completion time (when the file entered the drive) mixes them so the
# newest items come first.
SORTS = {"time": ("user_ptime", 1), "name": ("file_name", 0), "size": ("file_size", 0)}
DEFAULT_ASC = {"time": False, "name": True, "size": False}


def drive_order(sort=None, asc=None):
    sort = sort or "time"
    if sort not in SORTS:
        raise ValueError("排序方式无效")
    asc = DEFAULT_ASC[sort] if asc is None else asc
    if not isinstance(asc, bool):
        raise ValueError("排序方向无效")
    field, mixed = SORTS[sort]
    return {"sort": sort, "asc": asc, "o": field, "fc_mix": mixed, "natsort": int(sort == "name")}


def drive_listing(page, order=None):
    """A webapi.115.com/files page -> the listing the download page shows."""
    entries = page.get("data") or []
    path = [{"cid": str(item.get("cid")), "name": item.get("name") or ""} for item in page.get("path") or []]
    if path and path[0]["cid"] == "0":
        path[0]["name"] = "网盘"
    items = []
    for entry in entries:
        item = drive_item(entry)
        items.append({"id": str(entry.get("fid") or entry.get("cid")), "name": item["n"], "pc": item["pc"],
                      "is_dir": item["is_dir"], "size": 0 if item["is_dir"] else _size(entry.get("s")),
                      "time": _size(entry.get("tp")), "cid": str(entry.get("cid")) if item["is_dir"] else ""})
    offset = _size(page.get("offset"))
    count = _size(page.get("count")) or offset + len(items)
    # truncated: more entries follow; the page asks for them with the next offset.
    listing = {"kind": "drive", "cid": str(page.get("cid", path[-1]["cid"] if path else "0")), "path": path,
               "count": count, "offset": offset, "truncated": count > offset + len(items), "items": items}
    if order:
        listing.update(sort=order["sort"], asc=order["asc"])
    return listing


def share_listing(snap, cid="", offset=0):
    """A webapi.115.com/share/snap page -> the listing the download page shows."""
    data = snap.get("data") or {}
    items = []
    for entry in data.get("list") or []:
        item = share_item(entry)
        items.append({"id": item["sc"], "name": item["n"], "sc": item["sc"], "is_dir": item["is_dir"],
                      "size": 0 if item["is_dir"] else _size(entry.get("s")), "time": _epoch(entry.get("t")),
                      "cid": item["sc"] if item["is_dir"] else ""})
    count = _size(data.get("count")) or offset + len(items)
    info = data.get("shareinfo") or {}
    return {"kind": "share", "cid": str(cid or ""), "title": str(info.get("share_title") or ""),
            "count": count, "offset": offset, "truncated": count > offset + len(items), "items": items}


def detect(text):
    """What was pasted: 'share' (a 115 share link), 'path' (a drive path) or 'links' (offline links)."""
    text = (text or "").strip()
    if _SHARE_URL.search(text):
        return "share"
    return "path" if text[:1] in ("/", "\\") else "links"


MAX_ITEMS = 1150
_PICK_CODE = re.compile(r"[A-Za-z0-9]{1,64}")
_SHARE_ID = re.compile(r"[0-9]{1,32}")
_TASK_HASH = re.compile(r"[0-9A-Za-z]{8,64}")


def folder_id(value, share=False):
    """Drive folders are numeric ids ("0" is the root); a share's top level is ""."""
    value = "" if value is None else str(value)
    if (share and value == "") or _SHARE_ID.fullmatch(value):
        return value
    raise ValueError("目录 ID 无效")


def offset(value):
    """Where the next page of a long folder starts."""
    if isinstance(value, bool) or not isinstance(value, (int, str, type(None))):
        raise ValueError("分页位置无效")
    text = str(value or 0)
    if not text.isdigit() or int(text) > 10_000_000:
        raise ValueError("分页位置无效")
    return int(text)


def task_hash(value):
    if isinstance(value, str) and _TASK_HASH.fullmatch(value):
        return value
    raise ValueError("云下载任务 ID 无效")


def _name(value):
    # Names become file names on the NAS; refuse anything that could leave the download folder.
    if (not isinstance(value, str) or not value or len(value) > 255 or value in (".", "..")
            or any(char in value for char in "/\\") or any(ord(char) < 32 for char in value)):
        raise ValueError("文件名无效")
    return value


def picked_items(raw, share=False):
    """Validate items the page sends back: drive {n, pc, is_dir} or share {n, sc, is_dir}."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("请先勾选要下载的内容")
    if len(raw) > MAX_ITEMS:
        raise ValueError(f"一次最多下载 {MAX_ITEMS} 项")
    items = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("is_dir"), bool):
            raise ValueError("下载项格式无效")
        key, pattern = ("sc", _SHARE_ID) if share else ("pc", _PICK_CODE)
        value = entry.get(key)
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError("下载项格式无效")
        item = {"n": _name(entry.get("n")), "pc": "" if share else value, "is_dir": entry["is_dir"]}
        if share:
            item["sc"] = value
        items.append(item)
    return items
