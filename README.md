# 115 Docker

在 Docker 中运行 115 浏览器，通过网页访问桌面、选择网盘与分享内容并下载到 NAS。项目自带 115 安装包、桌面组件、网页客户端、下载页面、JSON API、命令行工具与构建依赖，使用自己的下载目录和浏览器数据卷。

默认采用 **KasmVNC 1.5.0 + Xfce 4.20**，保留 TigerVNC + Websockify + noVNC 后端。参考 [dream10201/115Docker](https://github.com/dream10201/115Docker) 的架构，使用固定 Debian 基础镜像构建。115 浏览器版本为 **36.0.1**，镜像支持 **linux/amd64**。

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
| 浏览器访问完整桌面 | KasmVNC 原生 HTTP/WebSocket，配合 Xfce、Arc-Dark 主题、Papirus-Dark 图标和中文字体；115 启动时最大化 |
| 分辨率自适应 | 窗口大小和全屏状态变化时调整真实 X11 分辨率，最大化的 115 窗口同步布局 |
| Mac Retina 与显示缩放 | 默认按网页逻辑像素适配，支持跨屏像素比例变化；可选择“原生分辨率”提高清晰度 |
| 网页添加下载 | 磁力、ed2k、HTTP、FTP 链接加入云下载，完成后选择文件，或自动将整个任务下载到 NAS |
| 网盘和分享内容选择 | 浏览网盘、解析路径与分享链接、进入子目录、勾选文件或文件夹；网盘支持按完成时间、名称、大小排序 |
| 下载进度 | 查看云下载任务、等待任务、NAS 下载进度、速度与剩余时间；传输列表关闭时显示本地下载记录 |
| 自动保持登录 | 扫码后保存登录 Cookie，浏览器重开或容器重建后尝试恢复登录；未登录时每 2 秒检查，已登录时每分钟检查 |
| CLI 与 JSON API | 内置 `115dl`，页面与接口使用同一套凭据；右上角“命令行与接口”弹窗提供使用当前访问地址的示例 |
| 数据持久化 | 浏览器配置、登录状态、自动下载任务保存在 profile，下载文件存放在独立挂载目录 |
| 工程内依赖与验收 | vendor 固定版本与 SHA256；OneDev 验证实际桌面连接、缩放、鼠标坐标、性能和登录恢复后才发布 `latest` |

桌面关闭合成器和动画，默认 30 FPS、2 个编码线程，优先使用开销较低的 JPEG。远程桌面传输画面和操作；文件由容器内 **115 浏览器自己的下载引擎**下载。关闭访问端网页不会停止容器内正在运行的任务。

## 部署方式

### 主机要求与端口

推荐使用 x86-64 Linux 主机或 x86 NAS，预留约 2 CPU、4 GiB 内存上限和 1 GiB 共享内存。profile 优先放在本地 SSD，下载目录使用有足够空间的数据盘。Apple Silicon 需要 amd64 模拟；ARM NAS 不能仅凭 `platform: linux/amd64` 运行此镜像。

| 用途 | 容器端口 | Compose 默认宿主机端口 | 下文群晖示例 |
| --- | --- | --- | --- |
| 远程桌面 | 1150/tcp | 1150 | 11501 |
| 下载页面与 JSON API | 1151/tcp | 1151 | 11502 |

浏览器调试端口 9222 仅在容器内回环监听，**不要映射到宿主机**。所有部署方式都需要两个持久目录：

| 容器目录 | 内容 |
| --- | --- |
| `/etc/115` | 浏览器 profile、`115-session.json` 登录状态、`115-downloads.json` 自动下载任务 |
| `/opt/Downloads` | 下载文件 |

同一 profile 只能由一个浏览器实例使用。迁移已有容器时沿用原目录，不要用空目录替换。

### 方式一：Compose 拉取已发布镜像

适合直接使用流水线镜像。先取得工程并准备配置：

```bash
git clone https://github.com/devShuai/115-docker.git
cd 115-docker
cp .env.example .env
chmod 600 .env
mkdir -p data/profile data/downloads
chmod 700 data/profile
chmod 750 data/downloads
```

编辑 `.env`，填写 `VNC_PASSWORD`：KasmVNC 支持 **8～128 位 ASCII 英文字母或数字**，建议至少 16 位。`IMAGE_115` 保持未设置，registry 覆盖文件会选择 `latest`；也可显式设为 `registry.devshuai.com/shuai-repo/115-docker:latest`。

```bash
# 私有仓库需要先使用自己的拉取账号登录
docker login registry.devshuai.com
docker compose -f compose.yaml -f compose.registry.yaml config --quiet
docker compose -f compose.yaml -f compose.registry.yaml pull
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build
docker compose -f compose.yaml -f compose.registry.yaml ps
```

默认仅绑定 `127.0.0.1`。在宿主机访问桌面 `http://127.0.0.1:1150/` 和下载页面 `http://127.0.0.1:1151/`；远程访问见下文 SSH 隧道或 HTTPS 入口。后续操作始终携带相同的两个 `-f` 参数。

使用 **GHCR 镜像**时，保持 `IMAGE_115` 未设置，改用工程内的 GHCR 覆盖文件：

```bash
docker compose -f compose.yaml -f compose.ghcr.yaml pull
docker compose -f compose.yaml -f compose.ghcr.yaml up -d --no-build
```

也可显式设置 `IMAGE_115=ghcr.io/devshuai/115-docker:latest`。如果 GHCR 包为私有，需要先 `docker login ghcr.io`；公开包可匿名拉取。GHCR 与 Harbor 使用相同的端口、环境变量和数据卷。

### 方式二：Compose 在本地构建

沿用上面的配置和数据目录准备步骤，保持 `IMAGE_115` 未设置，不使用 `compose.registry.yaml`：

```bash
python3 scripts/vendor.py
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 browser115
```

工程包含 **291 个 Debian 依赖包，共 297 个锁定归档，约 327 MiB**。安装步骤在 BuildKit `--network=none` 下执行，不查询 apt 源，也不下载 npm/Python 依赖。首次构建仍需拉取或预先缓存 Debian 基础镜像和 Dockerfile 前端。复制工程时需包含 `vendor/`；缺失归档可使用 `python3 scripts/vendor.py --fetch` 按固定来源补齐并校验。

### 方式三：群晖 Container Manager

在“映像”中下载 `registry.devshuai.com/shuai-repo/115-docker:latest`，私有 Harbor 需先配置拉取账号。创建名为 `115-docker` 的容器，参考以下设置；NAS 目录按自己的存储位置修改，并事先创建。

| 设置 | 值 |
| --- | --- |
| 宿主机 11501 → 容器端口 | 1150/tcp |
| 宿主机 11502 → 容器端口 | 1151/tcp |
| `/volume2/docker/115-docker-profile` → 容器目录 | `/etc/115`，读写 |
| `/volume1/downloads/115` → 容器目录 | `/opt/Downloads`，读写 |
| `PASSWORD` | 自己设置的 8～128 位英文字母或数字 |
| `REMOTE_USER` / `REMOTE_BACKEND` | `115` / `kasm` |
| `ENABLE_DOWNLOAD_API` / `KEEP_115_LOGIN` | `true` / `true` |
| `TZ` | `Asia/Shanghai`，留空时也使用该时区 |
| CPU / 内存 / 共享内存 | 起步 2 CPU / 4 GiB / 1 GiB；界面未提供的项可用 Compose 或 docker run 设置 |
| 重启策略 | `unless-stopped`，或配置对应的自动重启与开机启动 |

保持镜像默认的入口和启动命令，不启用 privileged，不挂载 Docker socket。下载文件可能属于 root，按 NAS 用户需要设置共享组或 ACL。

启动后打开 `http://NAS_IP:11501/` 登录桌面，`http://NAS_IP:11502/` 使用下载页面。群晖界面填写的是 **`PASSWORD`**；`VNC_PASSWORD` 是 Compose 的变量名，直接创建容器时不能混用。

### 方式四：docker run（含 NAS SSH 部署）

在 NAS 上准备目录和 `.env.nas`，填写 `PASSWORD` 后再运行。密码规则同上。

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

普通 Linux 主机使用自己的目录和 `docker` 命令路径；仅供本机或隧道访问时把 `NAS_IP` 改为 `127.0.0.1`。CLI 创建的容器在群晖中的展示、开机管理以安装的 Container Manager 版本为准。

## 访问与登录

默认桌面使用 KasmVNC Basic 认证，用户名为 `REMOTE_USER`，密码为容器的 `PASSWORD`；下载页面和 API 使用相同凭据。进入桌面后用 115 官方客户端扫码登录。优先使用 Chrome、Chromium 或 Edge；Safari 的 Basic 认证 WebSocket 兼容性受 KasmVNC 上游限制。

登录保持默认启用：Cookie 保存到 `/etc/115/115-session.json`，权限为 600；检测到浏览器未登录时尝试恢复并刷新页面。正常重开后稍等几秒；服务端撤销登录、账号退出或要求安全验证时仍需重新扫码。该功能依赖 `ENABLE_DOWNLOAD_API=true`；关闭下载接口也会停止登录保持。仅关闭保持功能时向容器传入 `KEEP_115_LOGIN=false`。

`.env`、`.env.nas` 和 profile 含账号凭据，应避免提交或分享。可选 Cookie 扩展通过 `ENABLE_COOKIE_EXTENSION=true` 和 `COOKIE_CID/SEID/UID/KID` 注入自己的 Cookie，默认关闭。

### SSH 隧道与 HTTPS

Compose 默认端口在远程主机回环地址监听，可从访问端建立两个隧道：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:1150:127.0.0.1:1150 \
  -L 127.0.0.1:1151:127.0.0.1:1151 user@docker-host
```

随后访问本机的 1150 和 1151。若宿主机端口或绑定地址不同，替换 `-L` 右侧的目标地址与端口；例如 NAS 示例绑定在 NAS 的 LAN 地址，而非回环地址。

HTTPS 桌面入口参考 [nginx/115-kasm.conf.example](nginx/115-kasm.conf.example)，转发 Authorization 和 WebSocket 升级头。下载接口使用独立域名或端口反代到 1151，保留 Host 与 Authorization。该桌面示例仅配置了 1150，需为下载页面另配入口；两者不要混用同一个根路径。LAN 直连可设置 `BIND_IP` 为宿主机 LAN 地址，公网访问使用 HTTPS 或隧道。

### Retina、中文与桌面

桌面默认跟随网页 **CSS 逻辑分辨率**。例如可用区域 1440×900、DPR=2 时，默认仍为 1440×900；在显示设置开启“原生分辨率”后变为 2880×1800，画面更细致，同时编码像素数变为 4 倍。KasmVNC 会将逻辑尺寸向下取整为偶数。保持 Resize 为 Remote；多个访问端共享同一桌面，窗口大小可能互相影响。

桌面和网页客户端默认中文，时区为北京时间。KasmVNC 默认开启“使用本机输入法（IME）”，可直接使用访问端输入法；legacy 后端可通过剪贴板粘贴中文。访问 `/?lang=en` 可切换网页客户端为英文。书签栏默认隐藏，已有 profile 的个人选择保留，`Ctrl+Shift+B` 可切换。

## 下载页面、CLI 与 JSON API

首次先在桌面扫码。下载页面输入框可以接收以下内容：

1. **云下载链接**：磁力、ed2k、HTTP、FTP，每行一个。任务完成后列出内容供勾选；也可自动将整个任务下载到 NAS。
2. **网盘路径**：输入路径或点击“浏览网盘”，进入子目录并勾选文件与文件夹。完成时间指文件进入网盘的时间；支持时间、名称、大小排序。
3. **115 分享链接**：支持提取码、子目录浏览和部分内容选择，也可下载整个文件夹。
4. **任务与进度**：查看云下载、自动下载等待任务和 NAS 传输情况。实时速度来自浏览器传输列表，关闭该列表时显示历史记录。

已完成任务的 API 进度固定为 100%；未提供运行进度时使用已接收字节与总大小估算，但不凭四舍五入的大小判断完成。进度查询失败会显示重试提示，恢复后自动刷新；任务全部结束后清除顶部百分比与进度条。

下载保存到 `/opt/Downloads` 对应的宿主机目录。自动下载任务保存在 `115-downloads.json`，容器重建后继续跟踪。提交后的 10 分钟内每 10 秒查询一次，之后每分钟一次；已完成和失败记录保留 7 天。

### 115dl 命令行

镜像内置 `115dl`。Compose 容器使用服务名 `browser115`，直接部署示例使用容器名 `115-docker`。以下命令会提示输入与桌面相同的密码：

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

也可在有 Python 3 的访问端运行工程内客户端，连接自己的 HTTPS 入口或隧道：

```bash
python3 runtime/downloader/cli.py --url https://downloads.example.com --user 115 status
python3 runtime/downloader/cli.py --url http://127.0.0.1:1151 --json transfers
```

连接参数支持 `--url` / `DOWNLOAD_API_URL`、`--user` / `DOWNLOAD_API_USER`、`--password` / `DOWNLOAD_API_PASSWORD`。默认地址为 `http://127.0.0.1:1151`，用户名默认使用 `REMOTE_USER` 或 `115`；没有传入密码时交互询问。自动化调用需提供凭据。`--json` 输出原始 JSON，`raw` 可调用任意接口。

### JSON API

页面右上角“命令行与接口”弹窗会根据 **当前页面地址**生成示例，不需要手动猜测 NAS 的映射端口。`GET /api/endpoints` 返回完整命令、接口与参数说明。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/status`、`/api/endpoints` | 浏览器与登录状态、接口清单 |
| POST | `/api/inspect`、`/api/browse` | 识别输入、列出网盘或分享内容 |
| POST / GET | `/api/offline` | 提交云下载链接 / 查询任务 |
| GET | `/api/offline/content?hash=...` | 查询已完成云下载的内容 |
| POST | `/api/download`、`/api/share` | 下载网盘或分享的全部或选中内容 |
| GET | `/api/transfers`、`/api/local`、`/api/watch` | 传输进度、本地记录、自动下载等待任务 |

以下示例中的 `curl -u 115` 会提示输入密码。NAS 映射为 11502 时，将示例地址改为 `http://NAS_IP:11502`。

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

只下载部分内容时，先列目录，再提交选中条目的 `items`：网盘条目使用 `n`、`pc`、`is_dir`；分享条目使用 `n`、`sc`、`is_dir`，调用分享下载时还需原分享链接与提取码。

接口在容器内部通过回环 CDP 调用 115 网页与浏览器下载接口，不额外启动下载引擎。这些是 115 未公开的接口，上游变化可能需要调整。POST 接受 JSON 并校验 Origin；认证连续失败 10 次后锁定来源 10 分钟。409 通常表示未登录或需完成 115 安全验证，503 表示浏览器不可用。

## 配置项

`.env.example` 是 Compose 模板。表中“容器变量”用于群晖界面或 `docker run`，端口与目录变量用于 Compose。

| Compose 变量 | 容器变量 | 默认值 / 用途 |
| --- | --- | --- |
| `VNC_PASSWORD` | `PASSWORD` | 必填；Kasm 8～128 位字母数字，legacy 恰好 8 位 |
| `REMOTE_USER` / `REMOTE_BACKEND` | 同名 | `115` / `kasm`；后端可改为 `legacy` |
| `ENABLE_DOWNLOAD_API` | 同名 | `true`；关闭后下载接口和登录保持均停止 |
| — | `KEEP_115_LOGIN` | `true`；关闭自动保持登录 |
| `AUTO_START` | 同名 | `true`；自动启动 115 |
| `TZ` | 同名 | `Asia/Shanghai`，空值同样使用北京时间 |
| `DISPLAY_WIDTH` / `DISPLAY_HEIGHT` | 同名 | `1600` / `900`；连接前的初始尺寸 |
| `VNC_FPS` | 同名 | `30`；范围 1～60 |
| `KASM_THREADS` | 同名 | `2`；范围 1～16 |
| `KASM_WEBP_BUDGET` | 同名 | `0`；范围 0～100，每帧 WebP 编码时间比例 |
| `BIND_IP` / `WEB_PORT` / `API_PORT` | — | `127.0.0.1` / `1150` / `1151`，宿主机地址与端口 |
| `PROFILE_DIR` / `DOWNLOAD_DIR` | — | `./data/profile` / `./data/downloads`，支持绝对路径 |
| `SHM_SIZE` / `MEM_LIMIT` / `CPU_LIMIT` | — | `1gb` / `4g` / `2.0` |
| `IMAGE_115` | — | 未设置时：本地模式构建本地镜像，registry 模式拉取 `latest` |

当前 Compose 没有转发 `KEEP_115_LOGIN`，仅在 `.env` 加这一项不会生效。需要关闭时，在自己的 Compose 覆盖文件中给 `browser115.environment` 增加 `KEEP_115_LOGIN: "false"`；NAS 界面或 docker run 直接设置容器变量即可。

NAS CPU 较弱时可将 `VNC_FPS` 降为 15～20，并保持逻辑分辨率。带宽有限且 CPU 有余量时，可尝试 `KASM_WEBP_BUDGET=30` 后比较 CPU 和传输量。原生 Retina 模式、视频和大量动态内容都会增加编码负载；本工程的桌面传输不启用 GPU 视频编码。

## 更新与维护

### 检查镜像更新

`latest` 只有在流水线运行验收成功后才更新；`build-<编号>` 和 `sha-<完整提交>` 是候选构建标签，需确认对应构建成功后再使用。

```bash
# 直接部署的容器
python3 scripts/check-update.py --container 115-docker
# Compose 自动命名的容器
python3 scripts/check-update.py --container "$(docker compose -f compose.yaml -f compose.registry.yaml ps -q browser115)"
# 群晖 SSH 使用 root 的 Docker 登录配置
sudo /usr/bin/python3 scripts/check-update.py --docker /usr/local/bin/docker --container 115-docker --json
# 检查 GHCR 镜像时显式指定仓库，避免查询默认 Harbor 地址
python3 scripts/check-update.py --container 115-docker --image ghcr.io/devshuai/115-docker:latest
```

脚本查询同平台的远端 config digest，与**运行中容器**的 image ID 比较，不下载镜像层。退出码 0 表示最新，10 表示有更新，1 表示检查失败；可处理 manifest index 和 BuildKit 证明附件。

群晖原生更新提醒受 Container Manager 版本与仓库支持情况影响。**私有 Harbor 即使使用 latest，也不能只依赖“更新可用”提示**；使用上述脚本直接确认。下载新镜像并不会让正在运行的容器自动换版本。

### 更新容器

Compose 镜像部署：

```bash
docker compose -f compose.yaml -f compose.registry.yaml pull
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build
docker compose -f compose.yaml -f compose.registry.yaml ps
```

本地构建部署执行 `docker compose up -d --build`。群晖直接创建的容器，在拉取新镜像后重新创建，沿用原有端口、凭据、挂载和资源配置。docker run 部署可先 `docker stop 115-docker`、`docker rm 115-docker`，再执行原创建命令；保存好创建参数后再操作。

**保留现有 profile 和下载目录**，不要删除挂载数据。上述更新流程不自动保存旧容器或数据副本。如需回退，升级前自行保存旧镜像和配套 profile；旧镜像可能不能直接读取新版 Chromium 已升级的 profile。旧镜像确认不再使用后，可按明确 image ID 删除，避免全局清理其他服务的镜像。

### 日常检查与后端回退

```bash
docker compose -f compose.yaml -f compose.registry.yaml logs --tail=100 browser115
docker stats --no-stream
docker compose -f compose.yaml -f compose.registry.yaml exec -e DISPLAY=:115 browser115 xrandr --current
# 主动关闭浏览器后，从面板重新打开，或使用：
docker compose -f compose.yaml -f compose.registry.yaml exec -d browser115 /opt/115/115.sh
```

健康检查覆盖认证 HTTP、X11 和窗口管理器；它不确认 115 账号、云下载或文件是否完成。Docker 不会仅因 unhealthy 自动重启；关键桌面服务退出时容器会退出，再由重启策略恢复。

两种桌面后端已包含在同一镜像中。切换 legacy 时先将密码改为**恰好 8 位英文字母或数字**，再执行：

```bash
docker compose -f compose.yaml -f compose.registry.yaml -f compose.legacy.yaml up -d --no-build
```

本地构建省略 registry 文件并使用 `--build`。恢复 Kasm 时将 `REMOTE_BACKEND` 设为 `kasm` 并移除 legacy 覆盖文件。后端切换沿用同一数据卷，不能同时启动两个实例；legacy 不使用 Kasm 原生 Basic 认证，远程入口按 [nginx/115.conf.example](nginx/115.conf.example) 配置。

## 构建与流水线

GitHub：[devShuai/115-docker](https://github.com/devShuai/115-docker)。OneDev：`ssh://one.devshuai.com/shuai-toys/115-docker.git`。镜像仓库：`registry.devshuai.com/shuai-repo/115-docker`。

### GitHub Actions 与 GHCR

[CI and GHCR](https://github.com/devShuai/115-docker/actions/workflows/ci.yml) 由 `.github/workflows/ci.yml` 定义，支持 main 推送、PR 和手动运行：

1. 验证固定归档、Python 测试、Shell/JavaScript 语法，以及下载进度从 0% 到完成、请求失败后恢复的回归测试。
2. 构建 linux/amd64 镜像，在隔离容器中执行工程自带的双后端桌面、分辨率、性能与登录保持验收。
3. 无论桌面验收是否成功，都尝试上传截图、性能报告和诊断日志，产物保留 14 天。
4. main 验收成功后通过 `GITHUB_TOKEN` 发布 `ghcr.io/devshuai/115-docker:run-<run_id>-<attempt>` 和 `sha-<完整提交>`；该提交仍为 main 最新提交时才更新 `latest`。PR 不登录仓库或推送镜像。

流水线使用固定提交的官方 Actions，发布作业声明 `packages: write`，无需复制 Harbor 密码到 GitHub。首次发布后可在 GitHub Packages 管理镜像访问权限；公开仓库不代表新建包一定公开，匿名拉取需将包设为 Public。OneDev 与 GitHub 各自执行验收、发布到各自仓库，不互相依赖。

### OneDev 与 Harbor

`.onedev-buildspec.yml` 包含两个作业：

- **validate**：分支与 PR 更新触发，检查固定归档、Shell 语法、Python 代码和运行配置测试。
- **publish-image**：main 更新触发，依赖 validate 成功；构建 amd64 候选镜像，运行两种后端的真实桌面与性能验收，发布结果和截图，通过后再更新 latest。

执行器需要 Docker、Buildx，并启用 **Enable Buildx Image Build** 和 **Enable Buildx Image Tools**。OneDev Job Secrets 使用 `registry-username`、`registry-password`，授权给可信 main 发布作业；Harbor 的 `shuai-repo` 项目需允许该账号 Pull/Push。执行器须能访问 Docker Hub 与 Harbor，应用安装阶段不需要 apt 网络。

流水线覆盖实际 1024×768、1280×720、1920×1080、1600×900 分辨率，模拟 125%、150%、200% DPR，核对 canvas、X11 与鼠标位置；另检查认证、中文界面、时区、下载接口和登录恢复。静态/动画对比使用同一场景，结果位于 `.ci-artifacts/`。CPU 100% 表示一个核心，稀疏像素采样不代表渲染 FPS，也不能代替实际多任务测试。

发布作业串行更新 latest。重跑旧提交也会重新发布旧版本，操作前核对提交。CI 的 Chromium 使用 `--disable-dev-shm-usage` 适配执行器默认 64 MiB 共享内存，生产 Compose 仍配置 1 GiB。

### 工程结构

```text
Dockerfile / compose*.yaml      构建与部署
.env.example                  Compose 配置模板
.onedev-buildspec.yml          OneDev 验证与镜像发布
.github/workflows/ci.yml       GitHub Actions 验证与 GHCR 发布
runtime/                      入口、进程管理、浏览器配置与健康检查
  downloader/                 下载页面、JSON API、CDP、登录保持、115dl
  web/ kasmvnc/ novnc/         中文客户端、DPI 适配、原后端配置
rootfs/                       Xfce 设置、主题、桌面快捷方式与壁纸
vendor/                       固定安装包、源码归档、manifest 和 SHA256SUMS
scripts/                      归档校验、更新检查、桌面与性能验收
tests/                        配置、协议、下载接口与登录保持测试
nginx/                        两种后端的 HTTPS 反代示例
THIRD_PARTY.md                 第三方组件来源与许可
```

本地代码检查：

```bash
python3 scripts/vendor.py
python3 -m unittest discover -s tests -v
bash -n runtime/*.sh scripts/ci-smoke.sh
node --test tests/test_download_progress.mjs
```

启动管理测试需要 Bash 4.3+，macOS 系统旧 Bash 会跳过相应测试，完整桌面验收在 Linux 镜像中执行。软件升级需更新固定归档、manifest、SHA256SUMS 与引用版本。[第三方来源与许可](THIRD_PARTY.md)

## 排错

| 现象 | 检查方式 |
| --- | --- |
| 密码格式报错 | Compose 设置 `VNC_PASSWORD`，容器界面设置 `PASSWORD`；Kasm 8～128 位，legacy 恰好 8 位，均只允许英文字母数字 |
| 下载页面无法访问 | 确认 1151 已映射、`ENABLE_DOWNLOAD_API=true`，并使用宿主机映射后的端口 |
| API 返回 409 / 503 | 在远程桌面处理扫码或安全验证 / 重新打开 115 浏览器 |
| 重建后看不到原数据 | 检查宿主机目录是否沿用原路径，尤其是 `/etc/115` 挂载 |
| 登录恢复未成功 | 稍等几秒，确认下载接口与 KEEP_115_LOGIN 已开启；服务端使登录失效时重新扫码 |
| 分辨率不跟随窗口 | 网页 Resize 使用 Remote；检查是否有第二个访问端反复改变尺寸 |
| XDG_RUNTIME_DIR 不存在 | 使用包含入口修复的镜像，保留默认入口；无需持久挂载 `/run/115` |
| 黑屏或退出 | 先查看容器日志和健康状态，检查共享内存与密码；不要直接改成 privileged |

浏览器目前以 root 和 `--no-sandbox` 运行，`--test-type` 仅隐藏启动提示条，不会启用 sandbox。保持容器隔离，不挂载宿主机敏感目录。只有日志明确指向 seccomp 兼容问题时才使用 `compose.compat.yaml`；它会降低系统调用隔离，后续操作需携带相同覆盖文件。

### 群晖 inotify 错误

`Cannot set up inotify ... No space left on device` 可能是宿主机共享的文件监听配额耗尽，不能只据此判断磁盘已满。先在 NAS 检查：

```bash
df -h / /volume1
df -i / /volume1
sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances
```

确认监听配额不足后，可临时提高下限，保留已有更高值：

```bash
watch_limit=$(sysctl -n fs.inotify.max_user_watches)
instance_limit=$(sysctl -n fs.inotify.max_user_instances)
[ "$watch_limit" -ge 524288 ] || watch_limit=524288
[ "$instance_limit" -ge 1024 ] || instance_limit=1024
sudo sysctl -w "fs.inotify.max_user_watches=$watch_limit" \
  "fs.inotify.max_user_instances=$instance_limit"
```

这些是宿主机参数，不写入容器 sysctls。临时设置重启后可能失效；确认改善后再通过 DSM 开机任务持久设置。已高于下限时，应排查大量监听的进程与内核资源，避免反复提高上限。
