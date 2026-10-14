# 115 Docker

在 Docker 里运行 115 浏览器：通过网页远程操作桌面，扫码登录后，把云下载、网盘和分享里的内容下载到 NAS。工程自带 115 安装包、桌面组件、网页客户端、下载页面、JSON API、命令行工具和全部构建依赖；下载文件和浏览器数据分别保存在独立的挂载目录。

- 桌面默认使用 **KasmVNC 1.5.0 + Xfce 4.20**，并保留 TigerVNC + Websockify + noVNC 作为备用后端。
- 115 浏览器版本 **36.0.1**，镜像平台 **linux/amd64**，基于固定版本的 Debian 镜像构建。
- 架构参考 [dream10201/115Docker](https://github.com/dream10201/115Docker)。

目录：

- [特色功能](#特色功能)
- [部署方式](#部署方式)
- [访问与登录](#访问与登录)
- [下载页面、CLI 与 JSON API](#下载页面cli-与-json-api)
- [配置项](#配置项)
- [更新与维护](#更新与维护)
- [构建与流水线](#构建与流水线)
- [排错](#排错)

## 特色功能

| 功能 | 说明 |
| --- | --- |
| 网页访问完整桌面 | KasmVNC 原生 HTTP/WebSocket；Xfce 搭配 Arc-Dark 主题、Papirus-Dark 图标和中文字体，115 启动即最大化 |
| 分辨率自适应 | 窗口大小或全屏状态变化时，同步调整真实的 X11 分辨率，最大化的 115 窗口随之重排 |
| Mac Retina 与缩放 | 默认按网页逻辑像素适配，跨屏移动时跟随像素比例变化；需要更清晰时可开启“原生分辨率” |
| 网页添加下载 | 磁力、ed2k、HTTP、FTP 链接先加入云下载，完成后勾选文件，或自动把整个任务下载到 NAS |
| 网盘与分享 | 浏览网盘、解析路径和分享链接，进入子目录后勾选文件或文件夹；网盘可按完成时间、名称、大小排序 |
| 下载进度 | 分“下载中”“最近下载”“云下载”“云下载完成”四个标签；NAS 下载显示进度、速度和剩余时间，完成后自动移入“最近下载” |
| 自动保持登录 | 扫码后保存登录 Cookie，重开浏览器或重建容器后自动恢复；未登录时每 2 秒检查一次，已登录时每分钟一次 |
| CLI 与 JSON API | 内置 `115dl` 命令行；页面右上角提供可复制的命令和完整的参数、返回值、错误码说明；可下载独立客户端，agent 可读取 Markdown 与 OpenAPI 文档或安装 skill |
| 数据持久化 | 浏览器配置、登录状态和自动下载任务保存在 profile 目录，下载文件放在独立的挂载目录 |
| 依赖固定、验收后发布 | `vendor/` 中的依赖固定版本并校验 SHA256；流水线实测桌面连接、缩放、鼠标坐标、性能和登录恢复，通过后才发布；`latest` 只跟随正式版本 |

桌面关闭了合成器和动画，默认 30 FPS、2 个编码线程，优先使用开销较低的 JPEG 编码。远程桌面只传输画面和操作，文件由容器内 **115 浏览器自带的下载引擎**下载，关闭访问端的网页不会中断容器里正在进行的任务。

## 部署方式

### 主机要求与端口

推荐 x86-64 Linux 主机或 x86 NAS，资源上限约 2 CPU、4 GiB 内存，共享内存 1 GiB。profile 尽量放在本地 SSD，下载目录放在空间充足的数据盘。Apple Silicon 需要 amd64 模拟；ARM NAS 无法仅靠 `platform: linux/amd64` 运行本镜像。

| 用途 | 容器端口 | Compose 默认宿主机端口 | 下文群晖示例 |
| --- | --- | --- | --- |
| 远程桌面 | 1150/tcp | 1150 | 11501 |
| 下载页面与 JSON API | 1151/tcp | 1151 | 11502 |

浏览器调试端口 9222 只在容器内的回环地址监听，**不要映射到宿主机**。每种部署方式都需要两个持久目录：

| 容器目录 | 内容 |
| --- | --- |
| `/etc/115` | 浏览器 profile、`115-session.json` 登录状态、`115-downloads.json` 自动下载任务 |
| `/opt/Downloads` | 下载文件 |

一个 profile 同一时间只能给一个浏览器实例使用。迁移已有容器时沿用原来的目录，不要换成空目录。

### 方式一：Compose 拉取已发布镜像

适合直接使用流水线发布的镜像。先获取工程并准备配置：

```bash
git clone https://github.com/devShuai/115-docker.git
cd 115-docker
cp .env.example .env
chmod 600 .env
mkdir -p data/profile data/downloads
chmod 700 data/profile
chmod 750 data/downloads
```

编辑 `.env`，填写 `VNC_PASSWORD`（KasmVNC 支持 **8～128 位 ASCII 英文字母或数字**，建议至少 16 位）。`IMAGE_115` 保持不设置，registry 覆盖文件会自动使用 `latest`；也可以显式设为 `registry.devshuai.com/shuai-repo/115-docker:latest`。

```bash
# 私有仓库需要先用自己的拉取账号登录
docker login registry.devshuai.com
docker compose -f compose.yaml -f compose.registry.yaml config --quiet
docker compose -f compose.yaml -f compose.registry.yaml pull
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build
docker compose -f compose.yaml -f compose.registry.yaml ps
```

Compose 默认只绑定 `127.0.0.1`：在宿主机上用 `http://127.0.0.1:1150/` 打开桌面，用 `http://127.0.0.1:1151/` 打开下载页面；远程访问见下文的 SSH 隧道与 HTTPS 入口。之后的所有 Compose 命令都要带上同样的两个 `-f` 参数。

如果改用 **GHCR 镜像**，同样保持 `IMAGE_115` 不设置，换成工程里的 GHCR 覆盖文件：

```bash
docker compose -f compose.yaml -f compose.ghcr.yaml pull
docker compose -f compose.yaml -f compose.ghcr.yaml up -d --no-build
```

也可以显式设置 `IMAGE_115=ghcr.io/devshuai/115-docker:latest`。GHCR 包为私有时先执行 `docker login ghcr.io`，公开包可以匿名拉取。GHCR 与 Harbor 镜像的端口、环境变量和数据卷完全相同。

### 方式二：Compose 在本地构建

沿用上面准备好的配置和数据目录，保持 `IMAGE_115` 不设置，也不使用 `compose.registry.yaml`：

```bash
python3 scripts/vendor.py
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 browser115
```

工程内包含 **291 个 Debian 依赖包，共 297 个锁定归档，约 327 MiB**。安装步骤在 BuildKit `--network=none` 下执行，不访问 apt 源，也不下载 npm 或 Python 依赖；首次构建仍需拉取（或预先缓存）Debian 基础镜像和 Dockerfile 前端。复制工程时要带上 `vendor/`，缺失的归档可以用 `python3 scripts/vendor.py --fetch` 按固定来源补齐并校验。

### 方式三：群晖 Container Manager

在“映像”里下载 `registry.devshuai.com/shuai-repo/115-docker:latest`（私有 Harbor 需先配置拉取账号），然后创建名为 `115-docker` 的容器，参考下表设置。NAS 上的目录按自己的存储位置修改，并提前创建好。

| 设置 | 值 |
| --- | --- |
| 宿主机 11501 → 容器端口 | 1150/tcp |
| 宿主机 11502 → 容器端口 | 1151/tcp |
| `/volume2/docker/115-docker-profile` → 容器目录 | `/etc/115`，读写 |
| `/volume1/downloads/115` → 容器目录 | `/opt/Downloads`，读写 |
| `PASSWORD` | 自己设置的 8～128 位英文字母或数字 |
| `REMOTE_USER` / `REMOTE_BACKEND` | `115` / `kasm` |
| `ENABLE_DOWNLOAD_API` / `KEEP_115_LOGIN` | `true` / `true` |
| `TZ` | `Asia/Shanghai`；留空也使用北京时间 |
| CPU / 内存 / 共享内存 | 起步 2 CPU / 4 GiB / 1 GiB；界面没有的选项可用 Compose 或 docker run 设置 |
| 重启策略 | `unless-stopped`，或设置对应的自动重启与开机启动 |

保持镜像默认的入口和启动命令，不要开启 privileged，也不要挂载 Docker socket。下载的文件可能属于 root，可按 NAS 用户的需要设置共享组或 ACL。

启动后，用 `http://NAS_IP:11501/` 登录桌面，用 `http://NAS_IP:11502/` 打开下载页面。注意群晖界面里填的是 **`PASSWORD`**；`VNC_PASSWORD` 是 Compose 使用的变量名，直接创建容器时不要混用。

### 方式四：docker run（含 NAS SSH 部署）

在 NAS 上准备好目录和 `.env.nas`，填好 `PASSWORD` 后运行（密码规则同上）：

```dotenv
PASSWORD=
REMOTE_USER=115
REMOTE_BACKEND=kasm
ENABLE_DOWNLOAD_API=true
KEEP_115_LOGIN=true
TZ=Asia/Shanghai
```

```bash
mkdir -p /volume2/docker/115-docker-profile /volume1/downloads/115
chmod 600 .env.nas
sudo /usr/local/bin/docker login registry.devshuai.com
sudo /usr/local/bin/docker pull registry.devshuai.com/shuai-repo/115-docker:latest

# 改为自己的 NAS 局域网地址
NAS_IP=192.168.1.100
sudo /usr/local/bin/docker run -d --name 115-docker \
  --restart unless-stopped --init \
  --shm-size=1g --memory=4g --cpus=2 --pids-limit=512 \
  --security-opt no-new-privileges:true \
  --env-file .env.nas \
  -p "$NAS_IP:11501:1150" -p "$NAS_IP:11502:1151" \
  --mount type=bind,src=/volume2/docker/115-docker-profile,dst=/etc/115 \
  --mount type=bind,src=/volume1/downloads/115,dst=/opt/Downloads \
  registry.devshuai.com/shuai-repo/115-docker:latest
```

普通 Linux 主机换成自己的目录和 `docker` 命令路径；只在本机或通过隧道访问时，把 `NAS_IP` 改为 `127.0.0.1`。用命令行创建的容器在群晖里如何显示、能否开机管理，取决于所装的 Container Manager 版本。

## 访问与登录

桌面默认使用 KasmVNC 的 Basic 认证：用户名是 `REMOTE_USER`，密码是容器的 `PASSWORD`；下载页面和 API 使用同一组凭据。进入桌面后，用 115 官方客户端扫码登录。推荐使用 Chrome、Chromium 或 Edge；Safari 对带 Basic 认证的 WebSocket 的兼容性受 KasmVNC 上游限制。

登录保持默认开启：登录 Cookie 保存在 `/etc/115/115-session.json`（权限 600），检测到浏览器未登录时会自动恢复并刷新页面，重开浏览器后稍等几秒即可。如果 115 服务端撤销了登录、账号已退出或要求安全验证，仍需重新扫码。该功能依赖 `ENABLE_DOWNLOAD_API=true`，关闭下载接口会一并停止登录保持；只想关闭登录保持时，向容器传入 `KEEP_115_LOGIN=false`。

`.env`、`.env.nas` 和 profile 中含有账号凭据，不要提交或分享。可选的 Cookie 扩展通过 `ENABLE_COOKIE_EXTENSION=true` 和 `COOKIE_CID/SEID/UID/KID` 注入自己的 Cookie，默认关闭。

### SSH 隧道与 HTTPS

Compose 默认端口监听在远程主机的回环地址上，可以在访问端建立两条隧道：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:1150:127.0.0.1:1150 \
  -L 127.0.0.1:1151:127.0.0.1:1151 user@docker-host
```

然后在本机访问 1150 和 1151。宿主机端口或绑定地址不同时，替换 `-L` 右侧的目标地址和端口；例如上面的 NAS 示例绑定的是 NAS 的局域网地址，而不是回环地址。

HTTPS 桌面入口参考 [nginx/115-kasm.conf.example](nginx/115-kasm.conf.example)，需要转发 Authorization 和 WebSocket 升级头。该示例只配置了 1150；下载页面要另配一个入口（独立域名或端口）反代到 1151，并保留 Host 和 Authorization 头，两者不要共用同一个根路径。局域网直连可把 `BIND_IP` 设为宿主机的局域网地址；公网访问请使用 HTTPS 或隧道。

### Retina、中文与桌面

桌面默认跟随网页的 **CSS 逻辑分辨率**。例如可用区域为 1440×900、DPR=2 时，默认仍是 1440×900；在显示设置里开启“原生分辨率”后变为 2880×1800，画面更细腻，但编码的像素数也增加到 4 倍。KasmVNC 会把逻辑尺寸向下取整为偶数。Resize 请保持为 Remote；多个访问端共享同一个桌面时，窗口大小可能互相影响。

桌面和网页客户端默认中文，时区为北京时间。KasmVNC 默认开启“使用本机输入法（IME）”，可以直接用访问端的输入法输入中文；legacy 后端可通过剪贴板粘贴中文。访问 `/?lang=en` 可把网页客户端切换为英文。书签栏默认隐藏（已有 profile 保留原来的设置），按 `Ctrl+Shift+B` 切换。

## 下载页面、CLI 与 JSON API

首次使用前先在桌面扫码登录。下载页面的输入框支持三类内容：

1. **云下载链接**：磁力、ed2k、HTTP、FTP，每行一个。云下载完成后列出内容供勾选，也可以设置为自动把整个任务下载到 NAS。
2. **网盘路径**：输入路径，或点“浏览网盘”从根目录开始，进入子目录后勾选文件和文件夹。列表支持按完成时间（文件进入网盘的时间）、名称、大小排序。
3. **115 分享链接**：支持提取码、子目录浏览和部分选择，也可以下载整个文件夹。

页面下方分四个标签：**下载中**只显示正在下载到 NAS 的任务，完成后自动移入**最近下载**；**云下载**列出进行中的任务和最近 7 天失败的任务，**云下载完成**列出最近完成的任务，可以直接选择内容下载到 NAS。进度和速度优先取自浏览器的传输列表；传输列表没打开时，按磁盘上正在写入的临时文件大小估算。

已完成任务的进度固定为 100%。浏览器的传输列表滞后时，用同一任务 ID 和文件名的已完成下载记录校正状态；列表没有给出进度时，按已接收字节和总大小估算，但不会仅凭四舍五入后的大小判定完成，也不会隐藏正在进行的文件校验。进度查询失败时页面显示重试提示，恢复后自动刷新；所有任务结束后，顶部的百分比和进度条自动清除。

文件保存在 `/opt/Downloads` 对应的宿主机目录。设为自动下载的任务记录在 `115-downloads.json`，容器重建后继续跟踪：提交后的 10 分钟内每 10 秒查询一次，之后每分钟一次；已完成和失败的记录保留 7 天。

### 115dl 命令行

镜像内置 `115dl`。Compose 部署的服务名是 `browser115`，直接部署示例的容器名是 `115-docker`。下面的命令会提示输入与桌面相同的密码：

```bash
docker exec -it 115-docker 115dl apis
docker exec -it 115-docker 115dl status
docker exec -it 115-docker 115dl add 'magnet:?xt=urn:btih:...' --to-nas
docker exec -it 115-docker 115dl browse --cid 0 --sort time --desc
docker exec -it 115-docker 115dl inspect '/电影/某文件夹'
docker exec -it 115-docker 115dl download --path '/电影/某文件夹'
docker exec -it 115-docker 115dl share 'https://115cdn.com/s/分享码' --code abcd
docker exec -it 115-docker 115dl transfers
# Compose 部署的等价调用
docker compose -f compose.yaml -f compose.registry.yaml exec browser115 115dl status
```

有 Python 3 的访问端也可以直接运行工程里的客户端，连接自己的 HTTPS 入口或隧道：

```bash
python3 runtime/downloader/cli.py --url https://downloads.example.com --user 115 status
python3 runtime/downloader/cli.py --url http://127.0.0.1:1151 --json transfers
```

连接参数为 `--url` / `DOWNLOAD_API_URL`（默认 `http://127.0.0.1:1151`）和 `--user` / `DOWNLOAD_API_USER`（默认取 `REMOTE_USER`，否则为 `115`）。密码按以下顺序读取：`--password` → `--password-file` / `DOWNLOAD_API_PASSWORD_FILE` → `DOWNLOAD_API_PASSWORD` → `API_PASSWORD`；密码文件为 UTF-8，末尾的换行会被去掉。都没有时在终端询问；加上 `--no-input` 则不询问，缺少凭据时直接报错。

**全局参数必须放在子命令之前**。`--json` 成功时把原始 JSON 输出到 stdout，失败时把 `{"error":"说明","status":401}` 输出到 stderr（本地输入或连接错误时 `status` 为 `null`）；退出码成功为 0，失败为 2。`raw` 子命令可以直接调用任意接口。

### agent 安装与调用

下载接口已打包为标准的 `115-download` skill，包含 `SKILL.md`、agent 元数据、独立 CLI、全部 12 个操作的接口参考和 OpenAPI 3.1 文件，源码在 [skills/115-download](skills/115-download)。安装只需要 Python 3.8+（一键命令还要用到 curl），不需要 Docker、pip、Git，也不需要 NAS 凭据。

**一键安装到 Codex：**

```bash
curl -fsSL https://raw.githubusercontent.com/devShuai/115-docker/main/runtime/downloader/skill-installer.py | python3 -
```

默认安装到 `$CODEX_HOME/skills/115-download`，未设置 `CODEX_HOME` 时为 `~/.codex/skills/115-download`。安装器会锁定 GitHub 提交、逐个校验文件的 SHA-256，全部通过后才写入目录；技能中不含任何真实账号凭据。更新时在同一命令末尾加 `--update`，你自己添加的文件会被保留；下载或校验失败时，已安装的技能保持不变。

其他支持 `SKILL.md` 的 agent，用 `--skills-dir` 指定它的技能目录，例如技能目录为 `~/.claude/skills` 的环境：

```bash
curl -fsSL https://raw.githubusercontent.com/devShuai/115-docker/main/runtime/downloader/skill-installer.py | python3 - --skills-dir "$HOME/.claude/skills"
# 更新同一目录下的技能
curl -fsSL https://raw.githubusercontent.com/devShuai/115-docker/main/runtime/downloader/skill-installer.py | python3 - --skills-dir "$HOME/.claude/skills" --update
# 从当前工程离线安装，不需要联网
python3 runtime/downloader/skill-installer.py --source .
```

Windows 下载 `runtime/downloader/skill-installer.py` 后运行 `python skill-installer.py`，自定义目录和更新参数相同。

在 agent 的环境或密钥配置里提供 `DOWNLOAD_API_URL`（下载页面地址）、`DOWNLOAD_API_USER`（桌面用户名），以及 `DOWNLOAD_API_PASSWORD` 或 `DOWNLOAD_API_PASSWORD_FILE`（桌面密码或密码文件）。如果当前会话还没有识别到新技能，开一个新会话后使用 `$115-download`，例如“使用 $115-download 列出网盘根目录”“使用 $115-download 查看 NAS 下载进度”。提交下载时要说明下载哪个链接、路径或哪些内容。

技能通过 `python3 <技能目录>/scripts/115dl.py --no-input --json COMMAND` 调用，不需要安装全局的 `115dl`，完整参数与示例见[技能接口参考](skills/115-download/references/api.md)。客户端和文档由 `python3 scripts/build-agent-skill.py` 从现有实现生成；两条流水线都会检查是否同步，并提供 `115-download.skill.zip` 产物。本地也可以运行：

```bash
python3 scripts/build-agent-skill.py --check --zip .ci-artifacts/115-download.skill.zip
```

**只安装独立 CLI：**

下载页面右上角的“命令行”会按当前地址生成安装命令。客户端从正在运行的容器下载，与服务端版本一致，只用 Python 3.8+ 标准库，不需要 pip、Docker 或 sudo。下面是 agent 非交互安装的 Linux/macOS 示例：

```bash
export DOWNLOAD_API_URL='http://NAS_IP:11502'  # 或自己的 HTTPS 入口
export DOWNLOAD_API_USER='115'               # 与桌面用户名相同
# DOWNLOAD_API_PASSWORD 由 agent 的密钥环境注入
: "${DOWNLOAD_API_PASSWORD:?请通过密钥环境提供桌面密码}"
mkdir -p "$HOME/.local/bin"
curl --fail --silent --show-error --user "$DOWNLOAD_API_USER:$DOWNLOAD_API_PASSWORD" \
  "$DOWNLOAD_API_URL/115dl.py" --output "$HOME/.local/bin/115dl.tmp" && \
  python3 -m py_compile "$HOME/.local/bin/115dl.tmp" && \
  chmod 700 "$HOME/.local/bin/115dl.tmp" && mv "$HOME/.local/bin/115dl.tmp" "$HOME/.local/bin/115dl"
export PATH="$HOME/.local/bin:$PATH"
115dl --no-input --json status
115dl --no-input --json apis
115dl --no-input --json browse --cid 0
# 也可以改用密码文件
115dl --password-file /path/to/desktop-password --no-input --json transfers
```

重复以上步骤即可更新客户端。Windows 下载 `/115dl.py` 后，用 `python 115dl.py --no-input --json status` 调用。源码见 [runtime/downloader/cli.py](runtime/downloader/cli.py)。

agent 可以用同一账号读取以下资源，不需要额外的 API Key：

| GET 路径 | 内容 |
| --- | --- |
| `/agent.md` | 可以直接交给 agent 的安装步骤、接口参数、请求/返回示例、错误处理和任务执行流程 |
| `/openapi.json` | OpenAPI 3.1 文档，含 HTTP Basic 认证、请求 schema、条件参数和返回说明 |
| `/115dl.py` | 当前容器的独立 CLI 源码 |
| `/api/endpoints` | 机器可读的完整说明，与页面和 agent 文档来自同一份定义 |

推荐的执行流程：先用 `status` 确认已登录，再用 `inspect` / `browse` 列出目录；提交选中项时把 `name` 映射为 `n`，`pc` / `sc` 和 `is_dir` 原样复制。云下载用 `add` 提交后检查 `failed`，完成后用 `content` 查询内容；加 `--to-nas` 会自动转到 NAS。`offline` 默认返回进行中、失败、已完成三类任务各自最新的一页，按添加时间从新到旧排列；翻看更早的任务用 `--state running|failed|done|all` 加 `--page`。浏览分享的示例：`115dl --no-input --json browse --share 'https://115cdn.com/s/分享码' --code abcd --cid ''`。

下载类请求返回 200 只代表任务已提交。每 3～5 秒查询一次 `transfers`，以 `state=done` 判断 NAS 下载完成；`verifying` 表示仍在校验，不能只看 `progress=1`。`live=false` 表示传输列表页没有打开，此时数据来自下载历史，进度和速度按磁盘上已写入的数据估算，可用 `local` 核对。`watch` 中的“已开始下载到 NAS”只表示已提交。POST 超时后先查询已有任务，不要盲目重复提交。

### JSON API

页面右上角的“命令行”弹窗分三页：**命令行**按当前页面地址生成下载命令和常用命令（点击即可复制）；**Agent** 提供 skill 一键安装；**接口**列出全部 12 个操作，点开某一行可查看说明、参数、CLI 示例和返回字段。完整的参数约束、请求与返回 JSON、通用错误码见 `/agent.md` 和 `/openapi.json`，`GET /api/endpoints` 返回同一份数据。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/status`、`/api/endpoints` | 浏览器与登录状态、接口清单 |
| POST | `/api/inspect`、`/api/browse` | 识别输入，列出网盘或分享内容 |
| POST / GET | `/api/offline` | 提交云下载链接 / 查询云下载任务 |
| GET | `/api/offline/content?hash=...` | 查询已完成云下载的内容 |
| POST | `/api/download`、`/api/share` | 下载网盘或分享里的全部或选中内容 |
| GET | `/api/transfers`、`/api/local`、`/api/watch` | 传输进度、本地下载记录、自动下载等待中的任务 |

以下示例中的 `curl -u 115` 会提示输入密码；NAS 映射到 11502 时，把地址换成 `http://NAS_IP:11502`。

```bash
curl -u 115 http://127.0.0.1:1151/api/status
curl -u 115 -H 'Content-Type: application/json' \
  -d '{"links":"magnet:?xt=urn:btih:...","to_nas":true}' \
  http://127.0.0.1:1151/api/offline
curl -u 115 -H 'Content-Type: application/json' \
  -d '{"text":"/电影/某文件夹","sort":"time","asc":false}' \
  http://127.0.0.1:1151/api/inspect
curl -u 115 -H 'Content-Type: application/json' \
  -d '{"path":"/电影/某文件夹"}' http://127.0.0.1:1151/api/download
curl -u 115 -H 'Content-Type: application/json' \
  -d '{"url":"https://115cdn.com/s/分享码","receive_code":"abcd"}' \
  http://127.0.0.1:1151/api/share
```

只下载部分内容时，先列出目录，再提交选中条目的 `items`：网盘条目使用 `n`、`pc`、`is_dir`；分享条目使用 `n`、`sc`、`is_dir`，并且要带上原分享链接和提取码。

接口在容器内通过回环地址上的 CDP 调用 115 网页和浏览器的下载接口，不会另起下载引擎。这些都是 115 未公开的接口，上游变化后可能需要调整。POST 只接受 JSON 并校验 Origin；同一来源认证连续失败 10 次会被锁定 10 分钟。返回 409 通常表示未登录或需要完成 115 安全验证，503 表示浏览器不可用。

## 配置项

`.env.example` 是 Compose 的配置模板。表中“容器变量”用于群晖界面或 `docker run`，端口和目录类变量只用于 Compose。

| Compose 变量 | 容器变量 | 默认值 / 用途 |
| --- | --- | --- |
| `VNC_PASSWORD` | `PASSWORD` | 必填；Kasm 为 8～128 位字母数字，legacy 恰好 8 位 |
| `REMOTE_USER` / `REMOTE_BACKEND` | 同名 | `115` / `kasm`；后端可改为 `legacy` |
| `ENABLE_DOWNLOAD_API` | 同名 | `true`；关闭后下载接口和登录保持都会停止 |
| — | `KEEP_115_LOGIN` | `true`；设为 `false` 关闭自动保持登录 |
| `AUTO_START` | 同名 | `true`；容器启动时自动打开 115 浏览器 |
| `TZ` | 同名 | `Asia/Shanghai`；留空也使用北京时间 |
| `DISPLAY_WIDTH` / `DISPLAY_HEIGHT` | 同名 | `1600` / `900`；客户端连接前的初始尺寸 |
| `VNC_FPS` | 同名 | `30`；范围 1～60 |
| `KASM_THREADS` | 同名 | `2`；范围 1～16 |
| `KASM_WEBP_BUDGET` | 同名 | `0`；范围 0～100，每帧用于 WebP 编码的时间比例 |
| `BIND_IP` / `WEB_PORT` / `API_PORT` | — | `127.0.0.1` / `1150` / `1151`；宿主机绑定地址和端口 |
| `PROFILE_DIR` / `DOWNLOAD_DIR` | — | `./data/profile` / `./data/downloads`；支持绝对路径 |
| `SHM_SIZE` / `MEM_LIMIT` / `CPU_LIMIT` | — | `1gb` / `4g` / `2.0` |
| `IMAGE_115` | — | 未设置时：本地模式构建本地镜像，registry 模式拉取 `latest` |

注意：当前 Compose 没有把 `KEEP_115_LOGIN` 传给容器，只在 `.env` 里添加不会生效。需要关闭时，在自己的 Compose 覆盖文件里为 `browser115.environment` 加上 `KEEP_115_LOGIN: "false"`；群晖界面或 docker run 直接设置容器变量即可。

NAS CPU 较弱时，可以把 `VNC_FPS` 降到 15～20，并保持逻辑分辨率。带宽有限而 CPU 有余量时，可以试试 `KASM_WEBP_BUDGET=30`，再对比 CPU 占用和传输量。原生 Retina、视频和大量动态内容都会增加编码负载；桌面传输不使用 GPU 视频编码。

## 更新与维护

### 版本与镜像标签

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)：`主版本.次版本.修订号`，例如 `1.2.3`。修订号只修问题；次版本增加功能并保持兼容；主版本有不兼容的变化，例如配置项或接口改名。每个版本的改动记录在 [CHANGELOG.md](CHANGELOG.md)，同时发布在 [GitHub Releases](https://github.com/devShuai/115-docker/releases)。

Harbor 和 GHCR 使用相同的标签：

| 标签 | 含义 |
| --- | --- |
| `latest` | 最新的正式版本，日常部署用它 |
| `1.2.3` | 固定的某个版本，发布后不再变化 |
| `1.2` / `1` | 该系列最新的正式版本；锁定 `1` 能自动拿到修复和新功能，但不会升级到不兼容的 2.x |
| `1.3.0-rc.1` 等 | 预发布版本，只发布这一个标签，不更新 `latest` |
| `edge` | main 最新一次验收通过的构建，用于提前试用，可能包含尚未发版的改动 |
| `sha-<完整提交>` | main 上某次提交的构建；`build-<编号>`、`run-<编号>-<次数>` 是流水线的候选镜像 |

运行中的版本可以在下载页面左上角、`/api/status` 的 `version` 字段、`115dl status`、镜像标签 `org.opencontainers.image.version` 或下面的检查脚本里看到。正式版显示为 `v1.2.3`，main 构建显示为 `edge-<提交>`，本地源码显示为 `dev`。

### 检查镜像更新

脚本默认对比 `latest`；用 `--image` 可以改为跟踪 `1`、`edge` 等其他标签。

```bash
# 直接部署的容器
python3 scripts/check-update.py --container 115-docker
# Compose 自动命名的容器
python3 scripts/check-update.py --container "$(docker compose -f compose.yaml -f compose.registry.yaml ps -q browser115)"
# 群晖 SSH 使用 root 的 Docker 登录配置
sudo /usr/bin/python3 scripts/check-update.py --docker /usr/local/bin/docker --container 115-docker --json
# 检查 GHCR 镜像时显式指定仓库，避免查询默认的 Harbor 地址
python3 scripts/check-update.py --container 115-docker --image ghcr.io/devshuai/115-docker:latest
```

脚本查询同平台镜像在远端的 config digest，并与**运行中容器**的 image ID 比较，不会下载镜像层，同时显示运行中的版本号。退出码 0 表示已是最新，10 表示有更新，1 表示检查失败；能正确处理 manifest index 和 BuildKit 证明附件。

群晖自带的更新提醒取决于 Container Manager 版本和仓库支持情况。**私有 Harbor 即使用的是 `latest`，也不能只依赖“有可用更新”的提示**，请用上面的脚本确认。另外，下载新镜像并不会让正在运行的容器自动换成新版本。

### 更新容器

Compose 镜像部署：

```bash
docker compose -f compose.yaml -f compose.registry.yaml pull
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build
docker compose -f compose.yaml -f compose.registry.yaml ps
```

本地构建部署执行 `docker compose up -d --build`。群晖直接创建的容器，在拉取新镜像后重新创建，沿用原来的端口、凭据、挂载和资源设置。docker run 部署可以先 `docker stop 115-docker`、`docker rm 115-docker`，再执行原来的创建命令——操作前先保存好创建参数。

**保留原有的 profile 和下载目录**，不要删除挂载的数据。上述更新流程不会自动备份旧容器或数据。如需能够回退，升级前自行保存旧镜像和对应的 profile；注意旧镜像不一定能读取被新版 Chromium 升级过的 profile。确认不再使用的旧镜像，可以按明确的 image ID 删除，避免全局清理误删其他服务的镜像。

### 日常检查与后端回退

```bash
docker compose -f compose.yaml -f compose.registry.yaml logs --tail=100 browser115
docker stats --no-stream
docker compose -f compose.yaml -f compose.registry.yaml exec -e DISPLAY=:115 browser115 xrandr --current
# 手动关闭浏览器后，可以从面板重新打开，或执行：
docker compose -f compose.yaml -f compose.registry.yaml exec -d browser115 /opt/115/115.sh
```

健康检查覆盖认证 HTTP、X11 和窗口管理器，但不检查 115 账号、云下载或文件是否下载完成。Docker 不会仅因 unhealthy 而重启容器；关键桌面服务退出时，容器会随之退出，再由重启策略拉起。

两种桌面后端都包含在同一个镜像里。切换到 legacy 前，先把密码改为**恰好 8 位英文字母或数字**，然后执行：

```bash
docker compose -f compose.yaml -f compose.registry.yaml -f compose.legacy.yaml up -d --no-build
```

本地构建时去掉 registry 覆盖文件并加上 `--build`。切回 Kasm 时，把 `REMOTE_BACKEND` 设为 `kasm` 并移除 legacy 覆盖文件。切换后端沿用同一个数据卷，不能同时运行两个实例；legacy 不使用 Kasm 原生的 Basic 认证，远程入口请参考 [nginx/115.conf.example](nginx/115.conf.example)。

## 构建与流水线

代码仓库：GitHub [devShuai/115-docker](https://github.com/devShuai/115-docker)，OneDev `ssh://one.devshuai.com/shuai-toys/115-docker.git`。镜像仓库：`registry.devshuai.com/shuai-repo/115-docker`（Harbor）和 `ghcr.io/devshuai/115-docker`（GHCR）。

### GitHub Actions 与 GHCR

[CI and GHCR](https://github.com/devShuai/115-docker/actions/workflows/ci.yml) 定义在 `.github/workflows/ci.yml`，在推送 main、推送 `v*` 标签、提交 PR 或手动触发时运行：

1. 校验固定归档和 agent skill 是否同步，运行 Python 测试，检查 Shell 和 JavaScript 语法，并运行下载进度（从 0% 到完成、请求失败后恢复）与接口文档的回归测试；可安装的 skill 包作为产物保留 90 天。
2. 构建 linux/amd64 镜像，在隔离容器中运行工程自带的双后端桌面、分辨率、性能和登录保持验收。
3. 无论桌面验收是否成功，都会尝试上传截图、性能报告和诊断日志，保留 14 天。
4. 验收通过后，用 `GITHUB_TOKEN` 发布候选镜像 `ghcr.io/devshuai/115-docker:run-<run_id>-<attempt>`。main 构建另外发布 `sha-<完整提交>`，且只有该提交仍是 main 的最新提交时才更新 `edge`；版本标签的构建发布对应版本（正式版同时更新 `X.Y`、`X` 和 `latest`）。PR 不会登录镜像仓库，也不会推送镜像。
5. 版本标签的构建发布镜像后，再创建 GitHub Release，附上 CHANGELOG.md 中该版本的说明和可安装的 skill 包；预发布版本标记为 Pre-release。

工作流里的官方 Actions 都固定到具体提交，发布镜像的作业声明了 `packages: write`，创建 Release 的作业声明了 `contents: write`，不需要把 Harbor 密码复制到 GitHub。首次发布后，可以在 GitHub Packages 管理镜像的访问权限；公开仓库下新建的包不一定是公开的，需要匿名拉取时请把包设为 Public。OneDev 与 GitHub 各自验收、各自发布到自己的仓库，互不依赖。

### OneDev 与 Harbor

`.onedev-buildspec.yml` 包含三个作业：

- **validate**：分支或 PR 更新时触发，检查固定归档、agent skill 是否同步、Shell 语法、Python 代码和运行配置测试。
- **publish-image**：main 更新时触发，依赖 validate 成功；构建 amd64 候选镜像，运行两种后端的真实桌面与性能验收，发布结果和截图，通过后发布 `sha-<完整提交>` 并更新 `edge`。
- **release**：推送 `v*` 标签、且标签所在提交属于 main 时触发，依赖 validate 成功；同样构建并完整验收，通过后发布该版本的镜像标签，正式版同时更新 `X.Y`、`X` 和 `latest`。

两条流水线发布哪些标签，都由 `scripts/version.py` 根据标签名统一计算，构建时把版本号写入镜像。

执行器需要 Docker 和 Buildx，并启用 **Enable Buildx Image Build** 与 **Enable Buildx Image Tools**。OneDev 的 Job Secrets 使用 `registry-username` 和 `registry-password`，授权给受信任的 main 发布作业；Harbor 的 `shuai-repo` 项目需要允许该账号 Pull/Push。执行器要能访问 Docker Hub 和 Harbor；安装软件包的阶段不需要访问 apt 源。

流水线实测 1024×768、1280×720、1920×1080、1600×900 四种分辨率，模拟 125%、150%、200% DPR，核对 canvas、X11 和鼠标位置；另外检查认证、中文界面、时区、下载接口和登录恢复。静态和动画画面使用同一场景对比，结果保存在 `.ci-artifacts/`。其中 CPU 100% 表示占满一个核心；稀疏像素采样不等于渲染 FPS，也不能代替真实的多任务测试。

两个发布作业串行执行。重跑旧的 main 构建会把 `edge` 改回旧提交，重跑旧版本的 release 会把 `latest` 改回旧版本，操作前请核对。CI 中的 Chromium 使用 `--disable-dev-shm-usage`，以适应执行器默认 64 MiB 的共享内存；生产环境的 Compose 仍配置 1 GiB。

### 发版

正式版本由 `vX.Y.Z` 标签触发。在 main 上运行：

```bash
# 预览版本说明，不做任何改动
python3 scripts/release.py patch --dry-run
# 发版：参数也可以是 minor、major、1.2.0 或 1.3.0-rc.1
python3 scripts/release.py patch --push
```

脚本先检查当前在 main、工作区干净、没有落后于各远端，然后把上一个正式版之后的提交按 feat、fix、perf、docs 和其他分组，写成版本说明加入 CHANGELOG.md；接着提交 `release: vX.Y.Z`、创建带说明的标签，并把 main 和标签一起推送到所有远端。新版本必须高于已有的全部版本，所以 `latest` 只会往前走。需要手写版本说明时，用 `--notes 文件`。

两条流水线收到标签后都会重新构建、完整验收，通过后才发布版本标签。标签必须打在 main 的提交上，否则不会发布。

### 工程结构

```text
Dockerfile / compose*.yaml     构建与部署
.env.example                   Compose 配置模板
CHANGELOG.md                   各版本的更新记录
.onedev-buildspec.yml          OneDev 验证与镜像发布
.github/workflows/ci.yml       GitHub Actions 验证与 GHCR 发布
runtime/                       入口、进程管理、浏览器配置与健康检查
  downloader/                  下载页面、JSON API、CDP、登录保持、115dl
  web/ kasmvnc/ novnc/         中文客户端、DPI 适配、原后端配置
rootfs/                        Xfce 设置、主题、桌面快捷方式与壁纸
skills/115-download/           agent skill（由 scripts/build-agent-skill.py 生成）
vendor/                        固定安装包、源码归档、manifest 和 SHA256SUMS
scripts/                       归档校验、版本与发版、更新检查、skill 生成、桌面与性能验收
tests/                         配置、协议、下载接口与登录保持测试
nginx/                         两种后端的 HTTPS 反代示例
THIRD_PARTY.md                 第三方组件来源与许可
```

本地检查（与 CI 的验证步骤一致）：

```bash
python3 scripts/vendor.py
python3 scripts/build-agent-skill.py --check
bash -n runtime/*.sh scripts/ci-smoke.sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q runtime scripts
node --check runtime/downloader/static/app.js
node --test tests/*.mjs
```

启动管理相关的测试需要 Bash 4.3+，macOS 自带的旧版 Bash 会跳过这些测试；完整的桌面验收在 Linux 镜像中运行。升级软件时，需要同步更新固定归档、manifest、SHA256SUMS 和引用的版本号。第三方组件的来源与许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 排错

| 现象 | 检查方式 |
| --- | --- |
| 密码格式报错 | Compose 里设置 `VNC_PASSWORD`，容器界面里设置 `PASSWORD`；Kasm 为 8～128 位、legacy 恰好 8 位，都只能用英文字母和数字 |
| 打不开下载页面 | 确认已映射 1151、`ENABLE_DOWNLOAD_API=true`，并使用宿主机映射后的端口 |
| API 返回 409 / 503 | 409：在远程桌面扫码或完成安全验证；503：重新打开 115 浏览器 |
| 重建后看不到原来的数据 | 检查宿主机目录是否沿用原来的路径，尤其是 `/etc/115` 的挂载 |
| 登录没有自动恢复 | 重开后稍等几秒；确认下载接口和 `KEEP_115_LOGIN` 都已开启；若服务端已让登录失效，需要重新扫码 |
| 分辨率不跟随窗口 | 网页客户端的 Resize 选 Remote；检查是否有另一个访问端在反复改变尺寸 |
| 提示 XDG_RUNTIME_DIR 不存在 | 使用包含入口修复的镜像并保留默认入口；`/run/115` 不需要持久挂载 |
| 黑屏或容器退出 | 先看容器日志和健康状态，检查共享内存和密码设置；不要直接改成 privileged |

浏览器目前以 root 身份、带 `--no-sandbox` 运行，`--test-type` 只用于隐藏启动提示条，并不会启用沙箱。请保持容器隔离，不要挂载宿主机的敏感目录。只有日志明确指向 seccomp 兼容问题时才使用 `compose.compat.yaml`；它会降低系统调用隔离，之后的操作都要带上同一个覆盖文件。

### 群晖 inotify 错误

出现 `Cannot set up inotify ... No space left on device` 时，可能是宿主机共享的文件监听配额用完了，不能只凭这条信息判断磁盘已满。先在 NAS 上检查：

```bash
df -h / /volume1
df -i / /volume1
sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances
```

确认是监听配额不足后，可以临时提高下限（已有更高的值会保留）：

```bash
watch_limit=$(sysctl -n fs.inotify.max_user_watches)
instance_limit=$(sysctl -n fs.inotify.max_user_instances)
[ "$watch_limit" -ge 524288 ] || watch_limit=524288
[ "$instance_limit" -ge 1024 ] || instance_limit=1024
sudo sysctl -w "fs.inotify.max_user_watches=$watch_limit" \
  "fs.inotify.max_user_instances=$instance_limit"
```

这些是宿主机参数，不要写进容器的 sysctls。临时设置在重启后可能失效；确认有改善后，再通过 DSM 的开机任务持久化。如果当前值已经高于这个下限，应排查占用大量监听的进程和内核资源，不要反复调高上限。
