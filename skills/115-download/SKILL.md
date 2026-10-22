---
name: 115-download
description: "通过已部署的 115-docker 下载接口浏览网盘或分享、提交云下载和 NAS 下载、查询任务进度。用户需要操作 115 下载或调用其 JSON API 时使用。"
---

# 115 下载助手

使用本 skill 自带的 `scripts/115dl.py`，只依赖 Python 3.8+ 标准库。接口、参数、返回字段和错误码见 [完整接口说明](references/api.md)，API 工具可读取 [OpenAPI 3.1](references/openapi.json)。仅在需要相应操作的参数时读取该参考，不必一次加载全部文档。

## 连接

`DOWNLOAD_API_URL` 指向下载页面入口，例如 `http://NAS_IP:11502`，容器内为 `http://127.0.0.1:1151`；不要使用桌面端口 1150/11501。用户名 `DOWNLOAD_API_USER` 和密码与远程桌面相同。优先沿用当前环境中已有配置；缺少服务地址或凭据时向用户索取地址、用户名和密钥配置方式，不要求把密码发到聊天中。

密码使用 `DOWNLOAD_API_PASSWORD` 或 `DOWNLOAD_API_PASSWORD_FILE`（UTF-8，文件末尾换行会去除）。不要将真实密码写入 skill、日志、命令参数或生成文档。使用 `--password-file` 可以显式选择凭据文件。

下方 `$SKILL_DIR` 表示本 `SKILL.md` 所在目录；替换为实际路径。无需安装全局 `115dl` 命令：

```bash
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json status
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json apis
```

全局参数放在子命令前。`--no-input` 不等待终端输入；成功的 JSON 输出在 stdout、退出码 0；失败的 `{error,status}` 在 stderr、退出码 2，本地输入或连接错误 `status=null`。服务端采用 HTTP Basic，远程连接使用已有 HTTPS 入口或隧道，不关闭 TLS 校验。

## 按任务调用

- **查看网盘或分享**：`inspect` 识别链接/绝对路径，`browse` 列目录。分页使用 `offset + items.length`，直到 `truncated=false`；分享根目录用 `--cid ''`。列表中的 `name` 映射为下载项 `n`，原样复制 `pc`（网盘）或 `sc`（分享）以及 `is_dir`。
- **搜索**：`search 关键词` 用 115 网盘自己的搜索按名字找文件和文件夹（`--cid` 只搜某个目录），翻页用 `--offset`。结果条目和目录列表一样：下载时 `name` 映射为 `n` 并复制 `pc`、`is_dir`；`parent` 是所在目录 ID，`parent_name` 是目录名（可能为空）。`offline --q 关键词` 按名字搜索云下载任务，每页 30 个。
- **云下载**：`add` 提交 magnet/ed2k/HTTP(S)/FTP 链接；检查响应的 `failed`，HTTP 200 仍可能部分失败。用户要求完成后下载到 NAS 时加 `--to-nas`。否则先查询 `offline`，在 `ready=true` 后使用 `content HASH` 获取可选内容。
- **NAS 下载**：用 `download --path` 下载整项，或用 `--items` 下载用户选择的内容。`share` 带原分享链接与提取码；省略 `--items` 会下载分享全部内容，只有用户要求下载全部时才省略。
- **进度**：`transfers` 查询实时传输；`live=false` 时数据来自下载历史，进度按磁盘上已写入的数据估算，可用 `local` 核对。云下载 `percent` 是 0–100，NAS `progress` 是 0–1；最终完成以 `state=done` / `key=done` 为准。`verifying` 仍在校验。`watch` 的“已开始下载到 NAS”表示已提交，还需要跟踪本地完成状态。

## 常见示例

以下仅展示调用方式；占位路径、链接和条目标识需换为用户指定或实际查询结果。

```bash
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json browse --cid 0 --sort time --desc
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json inspect '/电影/某文件夹'
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json search '关键词'
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json browse --share 'https://115cdn.com/s/分享码' --code abcd --cid ''
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json add 'magnet:?xt=urn:btih:实际哈希' --to-nas
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json download --items '[{"n":"example.mkv","pc":"实际查询的pc","is_dir":false}]'
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json share 'https://115cdn.com/s/分享码' --code abcd --items '[{"n":"example.mkv","sc":"实际查询的数字ID","is_dir":false}]'
python3 "$SKILL_DIR/scripts/115dl.py" --no-input --json transfers
```

## 状态与重试

`status` 的 `logged_in=false` 或 HTTP 409 需要用户在桌面完成登录/验证；`logged_in=null` 表示暂时无法判断，不直接认定已退出。503 表示浏览器不可用。

对只读查询遇到暂时网络错误或 502/503，可间隔 3–5 秒最多重试 3 次；仍失败则报告错误。用户要求跟踪进度时每 3–5 秒轮询，用户取消或出现失败即停止；到环境可用执行时限仍未完成时报告“进行中”，不要声称完成。

POST 不自动重复提交：超时或 502 后先查已有云下载/本地任务；无法确认结果时报告“不确定”，由用户决定是否再次提交。401 检查凭据；429 等待限流解除，不循环试密码。删除只在用户明确要求删除具体条目时调用，调用前复述要删的内容：`delete` 把网盘文件删进 115 回收站（`ids` 用条目的 `id`，`--pid` 用所在目录），`remove` 删除云下载任务（`--files` 连同网盘文件），`local-delete` 从 NAS 删除下好的文件，不能恢复。客户端不支持暂停任务，不要编造其他 API。

