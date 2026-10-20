#!/usr/bin/env python3
"""Command-line client for the 115 download API (the same endpoints the page uses).

Examples:
  115dl apis                                  # list every endpoint
  115dl status
  115dl add 'magnet:?xt=urn:btih:...' --to-nas
  115dl inspect '/电影/某文件夹'
  115dl browse --cid 0 --sort time
  115dl download --path '/电影/某文件夹'
  115dl share 'https://115cdn.com/s/xxx?password=abcd'
  115dl transfers

Connection and credentials come from flags or the environment:
  --url   / DOWNLOAD_API_URL       (default http://127.0.0.1:1151)
  --user  / DOWNLOAD_API_USER      (default REMOTE_USER, else 115)
  --password / DOWNLOAD_API_PASSWORD   (else prompt)
  --password-file / DOWNLOAD_API_PASSWORD_FILE   (UTF-8 secret file)
  --no-input --json                    (noninteractive, machine-readable)
"""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ApiError(message)


class Api:
    def __init__(self, url, user, password):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ApiError("--url 需要不含账号、查询参数和片段的 HTTP(S) 服务地址")
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def request(self, method, path, body=None, query=None):
        url = self.url + path
        if query:
            pairs = "&".join(f"{key}={urllib.parse.quote(str(value))}" for key, value in query.items() if value is not None)
            if pairs:
                url += "?" + pairs
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self.auth}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            if error.code == 401:
                raise ApiError("认证失败：检查用户名和密码（与远程桌面相同）", error.code) from error
            raise ApiError(f"{error.code} {detail}", error.code) from error
        except urllib.error.URLError as error:
            raise ApiError(f"无法连接 {self.url}：{error.reason}") from error


def size(value):
    value, index = float(value or 0), 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if index else f"{int(value)} {unit}"
        value, index = value / 1024, index + 1


def dump(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def show_apis(data, _):
    print(data.get("auth", ""))
    print(data.get("limits", ""))
    for item in data["endpoints"]:
        print(f"  {item['cmd']:<10} {item['method']:<4} {item['path']:<24} {item['summary']}")
        for param in item.get("params") or []:
            flag = "*" if param.get("required") else " "
            schema = json.dumps(param.get("schema", {}), ensure_ascii=False)
            print(f"       {flag}{param['name']:<14} [{param['in']}] {param.get('desc', '')} {schema}")
        print("    约束:", item.get("notes", ""))
        print("    CLI:", item.get("cli_example", ""))
        print("    请求:", json.dumps(item.get("request_example"), ensure_ascii=False))
        print("    返回示例:", json.dumps(item.get("response_example"), ensure_ascii=False))
        for field, description in item.get("response_fields", {}).items():
            print(f"       {field}: {description}")
    for error in data.get("errors", []):
        print(f"  错误 {error['status']}: {error['desc']}")
    for resource in data.get("resources", []):
        print(f"  GET {resource['path']}: {resource['summary']}")


def show_status(data, _):
    if not data.get("browser"):
        print("115 浏览器未运行")
    elif data.get("logged_in") is False:
        print("115 浏览器运行中，但未登录（请在远程桌面扫码）")
    elif data.get("logged_in"):
        print(f"115 已登录（user_id {data.get('user_id', '')}）")
    else:
        print("115 浏览器运行中")
    if data.get("version"):
        print(f"服务端版本 {data['version']}")


def show_listing(data, _):
    kind = data.get("kind")
    where = " / ".join(part["name"] for part in data.get("path") or []) or data.get("title") or kind
    print(f"[{kind}] {where}  共 {data.get('count', len(data.get('items', [])))} 项"
          + ("（已截断，可用 offset 翻页）" if data.get("truncated") else ""))
    for item in data.get("items") or []:
        mark = "D" if item.get("is_dir") else "-"
        key = item.get("pc") or item.get("sc") or ""
        print(f"  {mark} {('' if item.get('is_dir') else size(item.get('size'))):>10}  {key:<18} {item.get('name', '')}")
    if data.get("result"):
        print("  结果:", data["result"].get("n"))


def show_offline(data, _):
    for task in data.get("tasks") or []:
        extra = f" {task['percent']:.0f}%" if task.get("state") == "downloading" else ""
        ready = " [可选择内容]" if task.get("ready") else ""
        print(f"  {task.get('state', ''):<12}{extra:<6} {task.get('hash', '')[:16]}  {task.get('name', '')}{ready}")
    counts = data.get("counts")
    if counts:
        print(f"  进行中 {counts.get('running', 0)} · 失败 {counts.get('failed', 0)} · 已完成 {counts.get('done', 0)}")
    else:
        print(f"  共 {data.get('count', 0)} 个任务")


def show_transfers(data, _):
    if data.get("active"):
        head = f"{data['active']} 个进行中"
        if data.get("speed"):
            head += f" · {data['speed']}"
        if data.get("progress") is not None:
            head += f" · {data['progress'] * 100:.0f}%"
        print(head)
    if not data.get("live"):
        print("（传输列表未打开，显示的是下载记录，无实时速度）")
    for item in data.get("items") or []:
        pct = f"{item.get('progress', 0) * 100:.0f}%"
        detail = item.get("speed") or ((item.get("received", "") + "/" + item.get("total", "")).strip("/"))
        print(f"  {item.get('state', ''):<12}{pct:>5}  {detail:<20} {item.get('name', '')}")


def show_local(data, _):
    for row in data.get("downloads") or []:
        gone = "（已不在 NAS 上）" if row.get("exists") is False else ""
        print(f"  {row.get('id', ''):>6} {row.get('state', ''):<8} {size(row.get('received'))}/{size(row.get('total'))}  "
              f"{row.get('name', '')}{gone}")


def show_delete(data, _):
    print(f"已删除 {data.get('count', 0)} 项，在 115 回收站里可以恢复")


def show_remove(data, _):
    print(f"已删除 {data.get('count', 0)} 个云下载任务" + ("，连同存到网盘的文件" if data.get("files") else ""))


def show_local_delete(data, _):
    names = lambda key: "、".join(item.get("name") or str(item.get("id")) for item in data.get(key) or [])  # noqa: E731
    print(f"已从 NAS 删除 {len(data.get('deleted') or [])} 个文件" + (f"：{names('deleted')}" if data.get("deleted") else ""))
    if data.get("missing"):
        print("本来就不在：" + names("missing"))
    for item in data.get("failed") or []:
        print(f"没删：{item.get('name') or item.get('id')}（{item.get('error', '')}）")


def show_watch(data, _):
    for task in data.get("tasks") or []:
        note = f"：{task['message']}" if task.get("message") else ""
        print(f"  {task.get('status', ''):<16} {task.get('name', '')}{note}")


def show_download(data, _):
    print(f"已开始下载 {data.get('count', 1)} 项到 NAS：" + "、".join(data.get("names") or [data.get("name", "")]))


def show_share(data, _):
    print(f"已开始下载分享里的 {data.get('count', 0)} 项：" + "、".join(data.get("names") or []))


def show_add(data, _):
    message = f"已添加 {data.get('added', 0)} 个云下载任务"
    if data.get("to_nas"):
        message += "，完成后自动下到 NAS"
    if data.get("failed"):
        message += "；未添加：" + "；".join(item.get("error", "") for item in data["failed"])
    print(message)


def run(args):
    api = Api(args.url, args.user, resolve_password(args))
    if args.command == "apis":
        return api.request("GET", "/api/endpoints"), show_apis
    if args.command == "status":
        return api.request("GET", "/api/status"), show_status
    if args.command == "add":
        return api.request("POST", "/api/offline", {"links": "\n".join(args.links), "to_nas": args.to_nas}), show_add
    if args.command == "offline":
        return api.request("GET", "/api/offline", query={"state": args.state, "page": args.page}), show_offline
    if args.command == "content":
        return api.request("GET", "/api/offline/content",
                           query={"hash": args.hash, "sort": args.sort, "asc": _asc(args.asc)}), show_listing
    if args.command == "inspect":
        return api.request("POST", "/api/inspect", _clean({"text": args.text, "receive_code": args.code,
                                                           "sort": args.sort, "asc": args.asc})), show_listing
    if args.command == "browse":
        body = {"kind": "share" if args.share else "drive", "url": args.share, "cid": args.cid,
                "receive_code": args.code, "offset": args.offset, "sort": args.sort, "asc": args.asc}
        return api.request("POST", "/api/browse", _clean(body)), show_listing
    if args.command == "download":
        if not args.path and not args.items:
            raise ApiError("需要 --path 或 --items")
        body = {"items": _items(args.items)} if args.items else {"path": args.path}
        return api.request("POST", "/api/download", body), show_download
    if args.command == "share":
        body = _clean({"url": args.share_url, "receive_code": args.code})
        if args.items:
            body["items"] = _items(args.items)
        return api.request("POST", "/api/share", body), show_share
    if args.command == "transfers":
        return api.request("GET", "/api/transfers"), show_transfers
    if args.command == "local":
        return api.request("GET", "/api/local", query={"limit": args.limit}), show_local
    if args.command == "delete":
        return api.request("POST", "/api/delete", _clean({"ids": args.ids, "pid": args.pid})), show_delete
    if args.command == "remove":
        return api.request("POST", "/api/offline/delete", {"hashes": args.hashes, "files": args.files}), show_remove
    if args.command == "local-delete":
        return api.request("POST", "/api/local/delete", {"ids": args.ids}), show_local_delete
    if args.command == "watch":
        return api.request("GET", "/api/watch"), show_watch
    if args.command == "raw":
        body = json.loads(args.data) if args.data else None
        return api.request(args.method.upper(), args.path, body), None
    raise ApiError("未知命令")


def _asc(flag):
    return None if flag is None else ("1" if flag else "0")


def _clean(body):
    return {key: value for key, value in body.items() if value is not None}


def _items(raw):
    items = json.loads(raw)
    if not isinstance(items, list):
        raise ApiError("--items 需要是 JSON 数组")
    return items


def resolve_password(args):
    if args.password:
        return args.password
    if args.password_file:
        password = Path(args.password_file).read_text(encoding="utf-8").rstrip("\r\n")
        if not password:
            raise ApiError("密码文件为空")
        return password
    password = os.environ.get("DOWNLOAD_API_PASSWORD") or os.environ.get("API_PASSWORD")
    if password:
        return password
    if not args.no_input and sys.stdin.isatty():
        return getpass.getpass(f"{args.user} 的密码: ")
    raise ApiError("缺少密码：用 --password-file 或 DOWNLOAD_API_PASSWORD 环境变量提供凭据")


def build_parser():
    parser = Parser(prog="115dl", description="115 下载命令行客户端；全局参数放在子命令前")
    parser.add_argument("--url", default=os.environ.get("DOWNLOAD_API_URL", "http://127.0.0.1:1151"))
    parser.add_argument("--user", default=os.environ.get("DOWNLOAD_API_USER") or os.environ.get("REMOTE_USER") or "115")
    parser.add_argument("--password")
    parser.add_argument("--password-file", default=os.environ.get("DOWNLOAD_API_PASSWORD_FILE"), help="UTF-8 密码文件（或 DOWNLOAD_API_PASSWORD_FILE）")
    parser.add_argument("--no-input", action="store_true", help="非交互调用，缺少密码时直接报错")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("apis", help="列出所有接口")
    sub.add_parser("status", help="登录与浏览器状态")
    add = sub.add_parser("add", help="把链接加入 115 云下载")
    add.add_argument("links", nargs="+")
    add.add_argument("--to-nas", action="store_true", help="完成后整体下到 NAS")
    offline = sub.add_parser("offline", help="云下载任务列表")
    offline.add_argument("--state", choices=("recent", "running", "failed", "done", "all"),
                         help="默认 recent：进行中、失败、已完成各取最新一页")
    offline.add_argument("--page", type=int, help="翻页，用于 running/failed/done/all")
    content = sub.add_parser("content", help="云下载完成后的内容")
    content.add_argument("hash")
    _sorting(content)
    inspect = sub.add_parser("inspect", help="识别链接或路径并列出内容")
    inspect.add_argument("text")
    inspect.add_argument("--code", help="分享提取码")
    _sorting(inspect)
    browse = sub.add_parser("browse", help="浏览网盘或分享目录")
    browse.add_argument("--cid", default="0")
    browse.add_argument("--share", help="分享链接（浏览分享）")
    browse.add_argument("--code", help="分享提取码")
    browse.add_argument("--offset", type=int)
    _sorting(browse)
    download = sub.add_parser("download", help="下载网盘内容到 NAS")
    download.add_argument("--path", help="网盘路径（整项下载）")
    download.add_argument("--items", help='勾选条目的 JSON：[{"n":..,"pc":..,"is_dir":false}]')
    share = sub.add_parser("share", help="下载分享内容到 NAS")
    share.add_argument("share_url", metavar="url")
    share.add_argument("--code", help="提取码")
    share.add_argument("--items", help='勾选条目的 JSON：[{"n":..,"sc":..,"is_dir":false}]')
    sub.add_parser("transfers", help="NAS 实时传输进度")
    local = sub.add_parser("local", help="NAS 本地下载记录")
    local.add_argument("--limit", type=int, help="最多几条，默认 30")
    delete = sub.add_parser("delete", help="删除网盘文件或文件夹（进 115 回收站）")
    delete.add_argument("ids", nargs="+", help="目录列表里的 id")
    delete.add_argument("--pid", help="它们所在的目录 ID")
    remove = sub.add_parser("remove", help="删除云下载任务")
    remove.add_argument("hashes", nargs="+", help="任务 info_hash")
    remove.add_argument("--files", action="store_true", help="连同任务存到网盘的文件一起删")
    local_delete = sub.add_parser("local-delete", help="删除 NAS 上下载好的文件（不能恢复）")
    local_delete.add_argument("ids", nargs="+", type=int, help="local 列表里的 id")
    sub.add_parser("watch", help="等待下到 NAS 的任务")
    raw = sub.add_parser("raw", help="直接调用任意接口")
    raw.add_argument("method")
    raw.add_argument("path")
    raw.add_argument("--data", help="JSON 请求体")
    return parser


def _sorting(parser):
    parser.add_argument("--sort", choices=("time", "name", "size"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--asc", dest="asc", action="store_true", default=None, help="升序")
    group.add_argument("--desc", dest="asc", action="store_false", help="降序")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_errors = "--json" in argv
    try:
        args = build_parser().parse_args(argv)
        data, render = run(args)
    except (ApiError, ValueError, OSError) as error:
        if json_errors:
            print(json.dumps({"error": str(error), "status": getattr(error, "status", None)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(error, file=sys.stderr)
        return 2
    if args.json or render is None:
        dump(data)
    else:
        render(data, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
