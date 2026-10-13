"""Download API reference, served from the same revision as the running client."""
import json
from urllib.parse import urlencode

# One definition feeds the webpage, agent guide and OpenAPI document.
ENDPOINTS = [
    {"cmd": "status", "method": "GET", "path": "/api/status", "summary": "登录与浏览器状态", "params": []},
    {"cmd": "apis", "method": "GET", "path": "/api/endpoints", "summary": "列出所有接口", "params": []},
    {"cmd": "inspect", "method": "POST", "path": "/api/inspect", "summary": "识别链接或网盘路径，列出可下载内容",
     "params": [{"name": "text", "in": "body", "required": True, "desc": "链接、分享链接或网盘路径"},
                {"name": "receive_code", "in": "body", "desc": "分享提取码"},
                {"name": "sort", "in": "body", "desc": "time|name|size"},
                {"name": "asc", "in": "body", "desc": "true 升序"}]},
    {"cmd": "browse", "method": "POST", "path": "/api/browse", "summary": "浏览网盘或分享目录",
     "params": [{"name": "kind", "in": "body", "desc": "drive（默认）或 share"},
                {"name": "cid", "in": "body", "desc": "目录 ID，网盘根为 0"},
                {"name": "url", "in": "body", "desc": "分享链接（kind=share）"},
                {"name": "receive_code", "in": "body", "desc": "分享提取码"},
                {"name": "offset", "in": "body", "desc": "翻页位置"},
                {"name": "sort", "in": "body", "desc": "time|name|size"}, {"name": "asc", "in": "body", "desc": "true 升序"}]},
    {"cmd": "offline", "method": "GET", "path": "/api/offline", "summary": "云下载任务列表",
     "params": [{"name": "page", "in": "query", "desc": "页码，默认 1"}]},
    {"cmd": "add", "method": "POST", "path": "/api/offline", "summary": "把链接加入 115 云下载",
     "params": [{"name": "links", "in": "body", "required": True, "desc": "磁力/ed2k/HTTP/FTP，每行一个"},
                {"name": "to_nas", "in": "body", "desc": "true 时完成后整体下到 NAS"}]},
    {"cmd": "content", "method": "GET", "path": "/api/offline/content", "summary": "云下载完成后的内容",
     "params": [{"name": "hash", "in": "query", "required": True, "desc": "任务 info_hash"},
                {"name": "sort", "in": "query", "desc": "time|name|size"}, {"name": "asc", "in": "query", "desc": "1 升序"}]},
    {"cmd": "download", "method": "POST", "path": "/api/download", "summary": "下载网盘内容到 NAS",
     "params": [{"name": "path", "in": "body", "desc": "网盘路径（整项下载）"},
                {"name": "items", "in": "body", "desc": "勾选的条目 [{n, pc, is_dir}]"}]},
    {"cmd": "share", "method": "POST", "path": "/api/share", "summary": "下载分享内容到 NAS",
     "params": [{"name": "url", "in": "body", "required": True, "desc": "115 分享链接"},
                {"name": "receive_code", "in": "body", "desc": "提取码"},
                {"name": "items", "in": "body", "desc": "勾选的条目 [{n, sc, is_dir}]，省略则全部"}]},
    {"cmd": "transfers", "method": "GET", "path": "/api/transfers", "summary": "NAS 实时传输进度", "params": []},
    {"cmd": "local", "method": "GET", "path": "/api/local", "summary": "NAS 本地下载记录", "params": []},
    {"cmd": "watch", "method": "GET", "path": "/api/watch", "summary": "等待下到 NAS 的任务", "params": []},
]


# Schemas describe accepted input; conditional rules are kept next to each endpoint.
STRING = {"type": "string"}
SORT = {"type": "string", "enum": ["time", "name", "size"], "default": "time"}
CODE = {"type": "string", "pattern": "^([A-Za-z0-9]{4})?$", "description": "4 位字母或数字；优先于链接里的 password 参数"}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 10000000, "default": 0}
ASC = {"type": "boolean", "description": "省略时 time/size 降序，name 升序；分享目录不支持此排序"}
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
SHARE = "https://115cdn.com/s/example"


def picked(share=False):
    key = "sc" if share else "pc"
    return {"type": "array", "minItems": 1, "maxItems": 1150, "items": {
        "type": "object", "required": ["n", key, "is_dir"], "properties": {
            "n": {"type": "string", "minLength": 1, "maxLength": 255,
                  "description": "使用目录响应的 name；不允许 /、反斜线、控制字符、. 或 .."},
            key: {"type": "string", "pattern": "^[0-9]{1,32}$" if share else "^[A-Za-z0-9]{1,64}$",
                  "description": "从目录响应原样复制 " + key},
            "is_dir": {"type": "boolean", "description": "从目录响应原样复制；文件夹会递归下载"}}}}


SCHEMAS = {
    "status": {}, "apis": {},
    "inspect": {"text": {"type": "string", "minLength": 1}, "receive_code": CODE, "sort": SORT, "asc": ASC},
    "browse": {"kind": {"type": "string", "enum": ["drive", "share"], "default": "drive"},
               "cid": {"type": "string", "pattern": "^[0-9]{0,32}$",
                       "description": "网盘根为 0；分享根为留空或省略，子目录用列表中的 cid"},
               "url": STRING, "receive_code": CODE, "offset": OFFSET, "sort": SORT, "asc": ASC},
    "offline": {"page": {"type": "integer", "default": 1, "description": "服务端将页码限制在 1–100"}},
    "add": {"links": {"oneOf": [STRING, {"type": "array", "items": STRING}],
                      "description": "每行一个链接或字符串数组；去重后最多 100 个，支持 magnet/ed2k/HTTP(S)/FTP"},
            "to_nas": {"type": "boolean", "default": False, "description": "只跟踪本次成功创建的云下载任务"}},
    "content": {"hash": {"type": "string", "pattern": "^[0-9A-Za-z]{8,64}$"}, "sort": SORT,
                "asc": {"type": "string", "enum": ["0", "1"], "description": "0 降序，1 升序；默认同 sort"}},
    "download": {"path": {"type": "string", "description": "非根网盘绝对路径，文件或文件夹；不能含 .."},
                 "items": picked()},
    "share": {"url": STRING, "receive_code": CODE, "items": picked(True)},
    "transfers": {}, "local": {}, "watch": {},
}
LISTING = {"kind": "drive", "cid": "0", "path": [{"cid": "0", "name": "网盘"}],
           "count": 1, "offset": 0, "truncated": False, "sort": "time", "asc": False,
           "items": [{"id": "42", "name": "example.mkv", "pc": "abc123", "is_dir": False,
                      "size": 1024, "time": 1760000000, "cid": ""}]}
LIST_FIELDS = {
    "kind": "drive 或 share；inspect 对普通下载链接返回 links",
    "cid / path / title": "当前目录 ID、网盘面包屑 [{cid,name}]、分享标题（视类型提供）",
    "count / offset / truncated": "目录总项数、本页起始位置、是否有下一页；下一页 offset = 当前 offset + items.length",
    "items[]": "id、name、is_dir、size（字节；文件夹为 0）、time（Unix 秒）、cid（仅文件夹有值）",
    "items[].pc / sc": "网盘下载 pick code / 分享文件或目录 ID；下载时 name 映射为 n",
    "sort / asc": "网盘排序方式和方向；分享列表不提供这两个字段，也不应用排序参数",
    "folder / focus / result": "按路径浏览时可含 folder 下载项、focus 文件名；离线内容可含 result 整项下载数据",
    "share": "分享列表附带 {url,receive_code}，供后续下载调用",
}
EXAMPLES = {
    "status": ({}, {"browser": True, "logged_in": True, "user_id": "12345"}, "115dl --json status"),
    "apis": ({}, {"version": "1", "endpoints": [], "errors": [], "resources": []}, "115dl --json apis"),
    "inspect": ({"text": "/电影", "sort": "time", "asc": False}, LISTING,
                "115dl --json inspect '/电影' --sort time --desc"),
    "browse": ({"kind": "drive", "cid": "0", "offset": 0}, LISTING,
               "115dl --json browse --cid 0 --offset 0"),
    "offline": ({"page": 1}, {"tasks": [{"hash": "a"*40, "name": "example.mkv", "state": "done",
                   "percent": 100, "ready": True}], "count": 1}, "115dl --json offline --page 1"),
    "add": ({"links": [MAGNET], "to_nas": True}, {"added": 1, "failed": [], "to_nas": True,
            "tasks": [{"hash": "a"*40, "name": "example.mkv"}]}, "115dl --json add '" + MAGNET + "' --to-nas"),
    "content": ({"hash": "a"*40, "sort": "time", "asc": "0"}, {**LISTING,
                "task": {"hash": "a"*40, "state": "done", "percent": 100, "ready": True}},
                "115dl --json content '" + "a"*40 + "' --sort time --desc"),
    "download": ({"items": [{"n": "example.mkv", "pc": "abc123", "is_dir": False}]},
                 {"count": 1, "names": ["example.mkv"], "name": "example.mkv", "is_dir": False},
                 "115dl --json download --items '[{\"n\":\"example.mkv\",\"pc\":\"abc123\",\"is_dir\":false}]'"),
    "share": ({"url": SHARE, "receive_code": "abcd", "items": [{"n": "example.mkv", "sc": "42", "is_dir": False}]},
              {"count": 1, "names": ["example.mkv"]}, "115dl --json share '" + SHARE + "' --code abcd"),
    "transfers": ({}, {"live": True, "active": 0, "speed": "", "progress": None, "items": [{
                  "id": "42", "name": "example.mkv", "state": "done", "progress": 1, "verify": None,
                  "received": "1 KB", "total": "1 KB", "speed": "", "remain": 0, "folder": False,
                  "error": "", "ended": "2026-10-13 12:00:00"}]}, "115dl --json transfers"),
    "local": ({}, {"downloads": [{"id": 42, "name": "example.mkv", "path": "/opt/Downloads/example.mkv",
               "received": 1024, "total": 1024, "state": "已完成", "key": "done", "started": 1760000000,
               "finished": 1760000001}]}, "115dl --json local"),
    "watch": ({}, {"tasks": [{"hash": "a"*40, "name": "example.mkv", "url": MAGNET,
               "added": 1760000000, "status": "已开始下载到 NAS", "sent": 1760000001}]}, "115dl --json watch"),
}
NOTES = {
    "status": "logged_in=false 需在桌面登录；null 表示暂时无法判断，不等于已退出。",
    "apis": "examples 为结构示例，不含真实账号数据；以当前容器返回的说明为准。",
    "inspect": "此接口只识别/浏览，不发起下载。普通链接返回 {kind:links,links:[...] }；分享返回 kind=share。",
    "browse": "kind=share 必须提供 url；分享根目录省略 cid，子目录使用 items[].cid。固定分页大小由上游决定。",
    "offline": "云端完成不代表 NAS 已完成。使用 hash 查询内容，ready=true 后再下载；count 为上游总数。",
    "add": "HTTP 200 仍需检查 failed；可能部分成功。重复提交可能创建重复任务，网络超时后先查询 offline，再决定是否重试。",
    "content": "hash 来自 offline；任务需完成并已搬入网盘。接口搜索最近任务，找不到或未就绪返回 400。",
    "download": "提供 path 或 items；两者都有时 items 优先。items 不可空，最多 1150 项；响应表示已提交，完成情况查询 transfers/local。",
    "share": "省略 items 会下载分享顶层全部内容（文件夹递归）；传 items 时不可空，最多 1150 项。响应表示已提交。",
    "transfers": "progress 为 0–1，完成恒为 1；verifying 表示文件下载后校验中，不能按 progress=1 判定最终完成。live=false 为历史降级，无实时速度。最多 50 项。",
    "local": "最近 30 条浏览器下载历史；received/total 为字节。以 key=done 为完成依据；并非 NAS 全盘文件列表。",
    "watch": "status 是中文状态：等待云下载完成 / 已开始下载到 NAS / 云下载失败。sent 表示已发送，不等于本地完成；数据持久化在配置卷。",
}
FIELDS = {
    "status": {"browser": "浏览器是否可用 boolean", "logged_in": "boolean 或 null，当前 115 登录状态",
               "user_id": "登录用户 ID，可能省略"},
    "apis": {"version": "文档版本", "endpoints": "命令、方法、参数 schema、约束、示例和返回字段",
             "errors / resources / auth / limits / installation / skill_installation": "通用错误、下载资源、认证、请求限制与客户端/技能安装命令"},
    "inspect": {**LIST_FIELDS, "links": "kind=links 时的已验证、去重链接数组"},
    "browse": LIST_FIELDS,
    "offline": {"tasks[]": "hash、name、status（中文）、state（failed/waiting/downloading/done）、text",
                "tasks[].percent / size / speed / left": "进度 0–100 / 字节 / 字节每秒 / 剩余秒",
                "tasks[].added / folder / ready / auto": "上游时间 / 是否文件夹 / 可否下载 / 自动下载中文状态（可省略）",
                "count": "上游任务总数，用于 page 翻页"},
    "add": {"added / failed[]": "成功数 / 失败的 {url,error,hash}；200 不代表全部成功",
            "to_nas / tasks[]": "是否自动下载到 NAS / 已加入的 {hash,name}，用于后续查询"},
    "content": {**LIST_FIELDS, "task": "对应的 offline 任务，字段同 GET /api/offline tasks[]"},
    "download": {"count / names": "已提交下载的项数 / 最多 20 个名称", "name / is_dir": "第一项名称 / 是否文件夹"},
    "share": {"count / names": "已提交下载项数 / 最多 20 个名称"},
    "transfers": {"live / active / speed / progress": "实时数据标志 / 进行中数 / 汇总速度字符串 / 进行中任务加权进度 0–1，无任务为 null",
                  "items[].id / name / state": "任务 ID / 文件名 / downloading, verifying, waiting, paused, done, canceled, failed",
                  "items[].progress / verify": "下载比例 0–1 / 校验比例 0–1 或 null；最终完成依据 state=done",
                  "items[].received / total / speed": "可读大小/速度字符串（如 1.2 GB、5 MB/s），未知为空字符串",
                  "items[].remain / folder / error / ended": "剩余秒 / 文件夹布尔 / 错误信息 / 完成时间字符串"},
    "local": {"downloads[]": "id、name、path（容器内路径）、received/total（字节）、state（中文）、key（downloading/done/canceled/failed）、started/finished（Unix 秒或 null）"},
    "watch": {"tasks[]": "hash、name、url、added（Unix 秒）、status（中文）；sent（发起时间）与 message（说明）可能省略"},
}
ERRORS = [{"status": code, "desc": desc} for code, desc in [
    (400, "参数无效、无效 JSON、路径/任务不存在或云下载未就绪"), (401, "需要 HTTP Basic 认证或凭据错误"),
    (403, "POST 的 Origin 与 Host 不匹配"), (404, "不存在的接口"), (413, "JSON 请求体超过 64 KiB"),
    (415, "POST Content-Type 必须为 application/json"), (429, "同一来源 10 次认证失败后等待 10 分钟"),
    (409, "115 未登录或需要安全验证，在远程桌面完成后再调用"), (500, "内部错误"),
    (502, "115 上游接口或浏览器调用失败"), (503, "浏览器未启动或不可用")]]
RESOURCES = [
    {"path": "/115dl.py", "summary": "独立 CLI 源码；Python 3.8+ 标准库，无 pip 依赖"},
    {"path": "/agent.md", "summary": "agent 安装步骤、完整接口说明和任务执行流程"},
    {"path": "/openapi.json", "summary": "OpenAPI 3.1 文档，可供 agent 和 API 工具读取"},
]
for endpoint in ENDPOINTS:
    cmd = endpoint["cmd"]
    for param in endpoint["params"]:
        param["schema"] = SCHEMAS[cmd][param["name"]]
    request, response, command = EXAMPLES[cmd]
    endpoint.update(notes=NOTES[cmd], request_example=request, response_example=response,
                    response_fields=FIELDS[cmd], cli_example=command)


def reference():
    return {"version": "1", "auth": "HTTP Basic；用户名/密码与远程桌面相同，所有资源均需认证",
            "limits": "容器端口 1151；POST application/json，最大 64 KiB；不提供跨域调用",
            "endpoints": ENDPOINTS, "errors": ERRORS, "resources": RESOURCES,
            "installation": install_example("__DOWNLOAD_API_URL__"),
            "skill_installation": skill_install_example()}


def skill_install_example():
    return ('# 安装 115-download skill（Python 3.8+，无需 NAS 凭据）\n'
            'curl -fsSL https://raw.githubusercontent.com/devShuai/115-docker/main/runtime/downloader/skill-installer.py | python3 -\n'
            '# 更新已安装技能：在 python3 - 后加 --update\n'
            '# 默认安装到 $CODEX_HOME/skills 或 ~/.codex/skills；--skills-dir 可指定其他 agent 的目录\n'
            '# 在 agent 环境配置 DOWNLOAD_API_URL、DOWNLOAD_API_USER 和密码环境/文件，再使用 $115-download')


def object_schema(properties, required=()):
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


def array_schema(items):
    return {"type": "array", "items": items}


BOOL = {"type": "boolean"}
INT = {"type": "integer"}
NULL_INT = {"type": ["integer", "null"]}
FRACTION = {"type": "number", "minimum": 0, "maximum": 1}
OFFLINE_TASK = object_schema({
    "hash": {"type": ["string", "null"]}, "name": STRING, "status": STRING, "state": {
        "type": "string", "enum": ["failed", "waiting", "downloading", "done"]}, "text": STRING,
    "percent": {"type": "number", "minimum": 0, "maximum": 100}, "size": INT, "speed": INT,
    "left": INT, "added": {"description": "上游原始时间，格式视任务来源而定"},
    "folder": BOOL, "ready": BOOL, "auto": STRING})
DIRECTORY_ITEM = object_schema({"id": STRING, "name": STRING, "pc": STRING, "sc": STRING,
                               "is_dir": BOOL, "size": INT, "time": INT, "cid": STRING})
DIRECTORY = object_schema({
    "kind": {"type": "string", "enum": ["drive", "share"]}, "cid": STRING,
    "path": array_schema(object_schema({"cid": STRING, "name": STRING})), "title": STRING,
    "count": INT, "offset": INT, "truncated": BOOL, "items": array_schema(DIRECTORY_ITEM),
    "sort": SORT, "asc": BOOL, "folder": picked()["items"], "focus": STRING, "result": picked()["items"],
    "share": object_schema({"url": STRING, "receive_code": STRING}), "task": OFFLINE_TASK}, ["kind"])
TRANSFER = object_schema({"id": STRING, "name": STRING, "state": {"type": "string", "enum": [
    "downloading", "verifying", "waiting", "paused", "done", "canceled", "failed"]},
    "progress": FRACTION, "verify": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "speed": STRING, "remain": INT, "received": STRING, "total": STRING, "folder": BOOL,
    "error": STRING, "ended": STRING})
RESPONSE_SCHEMAS = {
    "status": object_schema({"browser": BOOL, "logged_in": {"type": ["boolean", "null"]}, "user_id": STRING}),
    "apis": object_schema({"version": STRING, "auth": STRING, "limits": STRING, "installation": STRING, "skill_installation": STRING,
        "endpoints": array_schema({"type": "object"}), "errors": array_schema(object_schema({"status": INT, "desc": STRING})),
        "resources": array_schema(object_schema({"path": STRING, "summary": STRING}))}),
    "inspect": {"oneOf": [DIRECTORY, object_schema({"kind": {"const": "links"}, "links": array_schema(STRING)}, ["kind", "links"])]},
    "browse": DIRECTORY, "content": DIRECTORY,
    "offline": object_schema({"tasks": array_schema(OFFLINE_TASK), "count": INT}),
    "add": object_schema({"added": INT, "to_nas": BOOL, "failed": array_schema(object_schema({
        "url": STRING, "error": STRING, "hash": STRING})), "tasks": array_schema(object_schema({"hash": STRING, "name": STRING}))}),
    "download": object_schema({"count": INT, "names": array_schema(STRING), "name": STRING, "is_dir": BOOL}),
    "share": object_schema({"count": INT, "names": array_schema(STRING)}),
    "transfers": object_schema({"live": BOOL, "items": array_schema(TRANSFER), "active": INT,
        "speed": STRING, "progress": {"type": ["number", "null"], "minimum": 0, "maximum": 1}}),
    "local": object_schema({"downloads": array_schema(object_schema({"id": INT, "name": STRING,
        "path": STRING, "received": INT, "total": INT, "state": STRING, "key": {"type": "string",
        "enum": ["downloading", "done", "canceled", "failed"]}, "started": NULL_INT, "finished": NULL_INT}))}),
    "watch": object_schema({"tasks": array_schema(object_schema({"hash": STRING, "name": STRING,
        "url": STRING, "added": INT, "status": STRING, "sent": INT, "message": STRING}))}),
}


def openapi():
    paths = {}
    for item in ENDPOINTS:
        cmd = item["cmd"]
        operation = {"operationId": cmd, "summary": item["summary"], "description": item["notes"],
                     "responses": {"200": {"description": "; ".join(f"{k}: {v}" for k, v in FIELDS[cmd].items()),
                         "content": {"application/json": {"schema": RESPONSE_SCHEMAS[cmd],
                                                           "example": item["response_example"]}}}}}
        for error in ERRORS:
            operation["responses"][str(error["status"])] = {"description": error["desc"], "content": {
                "application/json": {"schema": {"type": "object", "required": ["error"],
                    "properties": {"error": STRING}}, "example": {"error": error["desc"]}}}}
        if item["method"] == "POST":
            schema = {"type": "object", "properties": SCHEMAS[cmd]}
            required = [p["name"] for p in item["params"] if p.get("required")]
            if required:
                schema["required"] = required
            if cmd == "download":
                schema["anyOf"] = [{"required": ["path"]}, {"required": ["items"]}]
            if cmd == "browse":
                schema["allOf"] = [{"if": {"required": ["kind"], "properties": {"kind": {"const": "share"}}},
                                    "then": {"required": ["url"]}}]
            operation["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": schema, "example": item["request_example"]}}}
        elif item["params"]:
            operation["parameters"] = [{"name": p["name"], "in": "query", "required": bool(p.get("required")),
                "description": p["desc"], "schema": p["schema"], "example": item["request_example"][p["name"]]}
                for p in item["params"]]
        paths.setdefault(item["path"], {})[item["method"].lower()] = operation
    for resource, media in [(RESOURCES[0], "text/x-python"), (RESOURCES[1], "text/markdown"), (RESOURCES[2], "application/json")]:
        paths[resource["path"]] = {"get": {"summary": resource["summary"], "responses": {
            "200": {"description": resource["summary"], "content": {media: {"schema": {
                "type": "object" if media == "application/json" else "string"}}}},
            "401": {"description": "需要相同 HTTP Basic 认证"}, "429": {"description": "认证失败次数过多"}}}}
    return {"openapi": "3.1.0", "info": {"title": "115 Download API", "version": "1"},
            "servers": [{"url": "/", "description": "使用下载页面所在的服务地址和映射端口"}],
            "security": [{"desktopBasic": []}], "components": {"securitySchemes": {
                "desktopBasic": {"type": "http", "scheme": "basic"}}}, "paths": paths}


def install_example(base_url):
    # No credentials from the running container enter the generated guide.
    quoted = "'" + base_url.replace("'", "'\"'\"'") + "'"
    return '\n'.join([
        "# Python 3.8+、curl；Linux/macOS，不需要 Docker、pip 或 sudo",
        "export DOWNLOAD_API_URL=" + quoted,
        "export DOWNLOAD_API_USER='115'  # 改为桌面用户名",
        "# DOWNLOAD_API_PASSWORD 由 agent 的密钥环境注入，不要写进代码或对话",
        ': "${DOWNLOAD_API_PASSWORD:?请通过密钥环境提供桌面密码}"',
        'mkdir -p "$HOME/.local/bin"',
        'curl --fail --silent --show-error --user "$DOWNLOAD_API_USER:$DOWNLOAD_API_PASSWORD" \\',
        '  "$DOWNLOAD_API_URL/115dl.py" --output "$HOME/.local/bin/115dl.tmp" && \\',
        '  python3 -m py_compile "$HOME/.local/bin/115dl.tmp" && \\',
        '  chmod 700 "$HOME/.local/bin/115dl.tmp" && mv "$HOME/.local/bin/115dl.tmp" "$HOME/.local/bin/115dl"',
        'export PATH="$HOME/.local/bin:$PATH"',
        '115dl --no-input --json status',
        '115dl --no-input --json apis',
        '# 已安装客户端：重复以上下载步骤即可更新到当前容器版本',
    ])


def agent_guide():
    sections = ["# 115 下载 agent 接口", reference()["auth"], reference()["limits"],
        "## 安装 Agent Skill", "技能包含完整 API 参考、OpenAPI 和独立客户端，安装不需要 NAS 凭据。",
        "```bash\n" + skill_install_example() + "\n```",
        "## 安装与非交互调用", "将下面 URL 替换为下载页面的 origin（如 http://NAS_IP:11502 或 HTTPS 域名）。",
        "```bash\n" + install_example("http://NAS_IP:11502") + "\n```",
        "Windows 可下载 /115dl.py 后用 python 115dl.py 调用，无需安装第三方库。",
        "全局参数必须放在子命令前。凭据优先级：--password > --password-file（或 DOWNLOAD_API_PASSWORD_FILE）"
        " > DOWNLOAD_API_PASSWORD > API_PASSWORD；用户名 --user > DOWNLOAD_API_USER > REMOTE_USER > 115。"
        " --no-input 禁止询问密码；--json 成功输出 JSON 到 stdout，失败输出 {error,status} 到 stderr，退出码 2；成功为 0。"
        " 参数解析错误也返回相同结构；status=null 表示本地输入/连接错误。",
        "## agent 执行流程",
        "1. GET /api/status 确认 browser=true、logged_in=true；false/409 提示用户完成桌面登录，null 稍后再查。",
        "2. inspect/browse 查看内容；根据用户选择把 name 映射为 n，并复制 pc/sc/is_dir，不要编造标识。"
        " 分享使用 browse --share URL --code CODE，分享根需 --cid ''。",
        "3. add 提交云下载，检查 failed；to_nas=true 会自动转到 NAS，否则完成后通过 content 选择下载。",
        "4. download/share 返回的是任务已提交；每 3–5 秒查 transfers，以 state=done 判定完成。"
        " verifying 时等待校验；live=false 可用 local 历史核对。watch 的 sent 只表示已提交。",
        "5. GET 可有限退避重试；POST 超时或 502 后先查询已有任务，不盲目重复下载。401 检查凭据，429 等待限流解除。",
        "## 直接调用 JSON API",
        '```bash\ncurl --fail-with-body --user "$DOWNLOAD_API_USER:$DOWNLOAD_API_PASSWORD" "$DOWNLOAD_API_URL/api/status"\n'
        '115dl --no-input --json raw POST /api/browse --data \'{"kind":"drive","cid":"0"}\'\n```',
        "HTTP Basic 账号与桌面相同；远程访问使用部署的 HTTPS 入口或 SSH 隧道。客户端不关闭 TLS 校验。",
        "## 资源", *[f"- GET {r['path']}：{r['summary']}（需相同认证）" for r in RESOURCES],
        "## 通用错误", "错误 JSON：`{\"error\":\"说明\"}`。", *[f"- {e['status']}：{e['desc']}" for e in ERRORS]]
    for item in ENDPOINTS:
        query = "?" + urlencode(item["request_example"]) if item["method"] == "GET" and item["params"] else ""
        sections += [f"## {item['method']} {item['path']} — {item['summary']}", item["notes"],
                     "CLI：`" + item["cli_example"] + "`", "参数："]
        sections += [f"- {p['name']} ({p['in']}, {'必填' if p.get('required') else '可选'})：{p['desc']}；"
                     + json.dumps(p["schema"], ensure_ascii=False) for p in item["params"]] or ["无参数。"]
        sections += ["请求：`" + item["method"] + " " + item["path"] + query + "`"]
        if item["method"] == "POST":
            sections += ["```json\n" + json.dumps(item["request_example"], ensure_ascii=False, indent=2) + "\n```"]
        sections += ["200 返回示例（示意数据）：", "```json\n" + json.dumps(item["response_example"], ensure_ascii=False, indent=2) + "\n```",
                     *[f"- {key}：{value}" for key, value in item["response_fields"].items()]]
    return "\n\n".join(sections) + "\n"
