"""Parse what people submit: offline links, 115 drive paths and 115 share links.

The item formats mirror 115's own web code: the drive dialog
(wl_disk2014/core/DG/DownFile.js) and the share page (115cdn.com/s/...).
"""
import re
from urllib.parse import parse_qs, urlsplit

MAX_LINKS = 100
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
    """A share listing entry -> CreateSharedTask item, as built by 115's share page."""
    is_dir = entry.get("p") == 0 or not entry.get("fid")
    return {"n": entry["n"], "pc": "", "is_dir": is_dir,
            "sc": str(entry.get("cid") if is_dir else entry.get("fid") or "")}
