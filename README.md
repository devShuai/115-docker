# 115 浏览器 Docker 部署方案

本工程默认使用 **KasmVNC 1.5.0 原生网页桌面**，包含 115 Linux 安装包、KasmVNC 安装包及服务端和网页客户端源码、283 个 Debian 系统依赖包、桌面主题、启动与健康检查脚本、Compose 配置和 HTTPS 反代示例。原来的 TigerVNC + Websockify + noVNC 完整保留，可通过环境变量回退。参考 [dream10201/115Docker](https://github.com/dream10201/115Docker) 的架构，直接从 Debian 构建自己的镜像。

桌面采用 **Openbox 加 tint2 加 PCManFM**，搭配自带的深色窗口主题、Adwaita 深色控件、Papirus 图标、中文字体和静态壁纸。未启用桌面合成器、动画或动态壁纸；远程浏览器客户端是主要负载。连接后远程桌面分辨率跟随访问端窗口变化，115 启动时最大化。

## 工程内容

```text
Dockerfile                         本地构建完整应用镜像
compose.yaml                       应用部署和数据卷
compose.compat.yaml                seccomp 兼容配置
compose.registry.yaml              拉取流水线发布的镜像
compose.legacy.yaml                切换原后端用于回退
.onedev-buildspec.yml               OneDev 校验、构建、运行验收与发布
.env.example                       环境变量模板
.dockerignore                      排除敏感数据和运行目录
runtime/
  entrypoint.sh                    校验配置、准备运行目录后建立 D-Bus 会话
  environment.sh                   入口和桌面共用的环境校验与目录准备
  start.sh                         启动与进程管理
  115.sh                           浏览器启动参数
  configure.py                     下载路径和 Cookie 配置
  healthcheck.py                   后端认证 HTTP、X11 和 Openbox 检查
  index.html                       默认连接入口
  novnc/                           自动连接和强制分辨率适配
  115Cookie/                       本工程的可选 Cookie 扩展
rootfs/
  opt/.config/                     Openbox、tint2、PCManFM、GTK 配置
  opt/.gtkrc-2.0                   GTK 2 深色控件
  opt/Desktop/                     浏览器与下载目录快捷方式
  usr/share/themes/115Dark/         深色窗口主题
  usr/share/backgrounds/115/        本地静态 SVG 壁纸
  usr/share/icons/                  浏览器启动图标
vendor/
  115browser-36.0.1-amd64.deb        115 官方安装包
  kasmvncserver-trixie-1.5.0-amd64.deb  KasmVNC 和编译完成的网页资源
  kasmvnc-1.5.0-source.tar.gz        KasmVNC 对应服务端源码
  kasmvnc-web-475ecfa53565.tar.gz     对应网页客户端完整源码
  debian/                          283 个系统传递依赖 deb
  novnc-1.6.0.tar.gz                noVNC 完整源码归档
  websockify-0.13.0.tar.gz           Websockify 完整源码归档
  manifest.json                    来源、版本、大小和校验值
  SHA256SUMS                       构建前校验清单
scripts/vendor.py                  校验或重新下载固定归档
scripts/ci-smoke.sh                 镜像内启动桌面和浏览器验收
scripts/ci-rfb.py                   回退后端 RFB 认证与远程分辨率验收
scripts/ci-desktop.py               两种后端真实网页连接、自适应及性能对比
scripts/cdp.py                      工程自带的标准库 CDP/WebSocket 测试客户端
scripts/perf-fixture.html           固定本地静态及动画测试画面
tests/test_runtime.py              配置与启动管理验证
nginx/115-kasm.conf.example        KasmVNC HTTPS 和原生认证转发
nginx/115.conf.example             原后端 HTTPS、独立认证与 WebSocket 示例
THIRD_PARTY.md                     第三方来源和许可
data/                             部署时创建的运行数据
backups/                          部署时创建的备份
```

移动整个工程即可继续构建，复制时应包含约 326 MiB 的 vendor。系统和应用安装在 BuildKit `--network=none` 下完成，不查询 apt 源，不安装 npm/Python 网络依赖。Debian 基础镜像固定为 manifest 中的 OCI digest；首次构建仍需拉取或预先缓存基础镜像和 BuildKit 前端，CI 执行器镜像也由基础设施提供。[组件来源和许可](THIRD_PARTY.md)

## 桌面选择

[Openbox](https://openbox.org/) 是精简且可配置的窗口管理器，[tint2](https://gitlab.com/o9000/tint2) 提供轻量任务栏，[PCManFM](https://github.com/lxde/pcmanfm) 负责文件管理和桌面，适合远程操作 115 和查看下载文件。

本地主题统一采用深蓝背景和青绿色强调色。底部任务栏包含浏览器、下载目录、窗口切换、托盘和时钟。壁纸在构建时转换为 PNG，运行时显示静态图片。Papirus 增加镜像体积，但不增加常驻图标服务。[Papirus 项目](https://github.com/PapirusDevelopmentTeam/papirus-icon-theme)

快捷键：Alt 加 Tab 切换窗口，Alt 加 F4 关闭窗口，Super 加 E 打开下载目录，Super 加 B 打开浏览器，Super 加 D 显示桌面。双击标题栏切换最大化。

流水线在相同 1600×900 桌面、30 FPS 上限和全新测试 profile 下，依次运行两种后端，测量静态画面 8 秒与动画 15 秒。产物包含完整桌面及远程后端的 CPU、PSS 内存、WebSocket 接收量、测试画面的生成帧率、客户端像素变化和截图。像素以约 2 Hz 稀疏采样，只用于确认持续更新，不代表渲染 FPS。CPU 100% 表示一个核心，传输量不含 HTTP/TLS/TCP 开销；这是执行器上的固定场景对比，实际标签页、视频、下载和网络条件仍需在部署后测量。

## 架构和主机

```text
访问端浏览器 → SSH 隧道或 HTTPS 认证代理 → 宿主机 127.0.0.1:1150
  → KasmVNC 原生 HTTP/WebSocket → Openbox 桌面 → 115 浏览器
  回退：noVNC → Websockify → TigerVNC → 同一桌面
       /etc/115       → data/profile
       /opt/Downloads → data/downloads
```

网页客户端传输桌面画面与操作，115 下载流量由容器直接访问 115 服务并写入数据卷。关闭访问网页时，容器和浏览器继续运行；主机休眠、账号失效和容器退出仍会中断任务。

推荐 Linux x86 主机或 x86 NAS，起步为 2 CPU、4 GiB 内存上限和 1 GiB 共享内存；宿主机为系统和其他服务留余量。profile 放在本地 SSD，下载放在数据盘，不将运行中的 Chromium profile 放在 SMB 或 NFS 共享上。下载容量按文件大小和未完成任务预留。

115 安装包为 amd64，Compose 明确指定 `linux/amd64`。Apple Silicon 可以在支持模拟的 Docker Desktop 中试用；ARM NAS 需要额外的模拟能力，不能仅靠 platform 参数运行。长期下载优先使用 x86 主机。

Linux 建议 Docker Engine 28 或更新的受支持版本及 Compose 插件。Docker 文档说明，旧于 28 的版本存在同一二层网络访问回环发布端口的历史问题；老 NAS 还需配置主机或网络防火墙。[Docker 端口发布说明](https://docs.docker.com/engine/network/port-publishing/)

## 首次部署

在目标 Docker 主机的项目目录执行：

```bash
docker version
docker compose version
python3 scripts/vendor.py
cp .env.example .env
chmod 600 .env
mkdir -p data/profile data/downloads backups
chmod 700 data/profile backups
chmod 750 data/downloads
```

编辑 `.env`：默认 `REMOTE_BACKEND=kasm`、`REMOTE_USER=115`，`VNC_PASSWORD` 使用随机的 **8～128 位 ASCII 字母数字**，建议至少 16 位。需要原后端回退时，使用恰好 8 位字母数字。启动入口在建立 D-Bus 会话前校验密码和配置。`VNC_FPS=30` 控制刷新上限，`KASM_THREADS=2` 控制矩形编码线程；默认走 JPEG/WebP 图像传输，不启用 GPU 或 H.264/H.265/AV1 编码。[KasmVNC 配置](https://docs.kasmvnc.com/docs/configuration/)、[TigerVNC 密码说明](https://tigervnc.org/doc/vncpasswd.html)

默认 `IMAGE_115=115-browser-local:36.0.1` 是本地镜像名称，Compose 不拉取该名称的远程镜像。保留 `BIND_IP=127.0.0.1`。如需使用已有磁盘，将 DOWNLOAD_DIR 改为目标主机绝对路径，例如 `/volume1/downloads/115`，预先创建目录并允许容器写入。PROFILE_DIR 同样支持绝对路径。

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 browser115
```

也可使用 `docker compose up -d --build`。构建校验所有本地包后离线安装 115、KasmVNC 和系统依赖，并解压用于回退的 noVNC 与 Websockify，不查询上游 latest 应用镜像或动态版本。BuildKit 临时挂载 vendor，使 deb 安装包不留在最终镜像层中。

启动时先校验密码和环境参数、创建权限为 700 的 `/run/115`，再建立 D-Bus 会话、准备密码文件和浏览器配置，依次启动 KasmVNC、Openbox、PCManFM、tint2 和可选的 115 浏览器。Kasm 模式不启动 Python Websockify 中继；legacy 模式启动原 TigerVNC 与 Websockify。关键桌面服务退出会让容器退出，由 Docker 重启策略恢复；主动关闭浏览器会保留桌面。

初始化设置默认下载目录为 `/opt/Downloads`，保留已有下载设置。首次登录后仍需在 115 中核对下载目录，并验证文件出现在宿主机。

## 访问和登录

推荐使用 Chrome、Chromium 或 Edge，在 Docker 宿主机打开 `http://127.0.0.1:1150/`，输入 `REMOTE_USER`（默认 `115`）及配置的密码后，用 115 官方客户端扫描二维码登录。浏览器配置和登录凭据保存在 `/etc/115` 数据卷中，重建后保留；115 服务端仍可能撤销登录。

KasmVNC 使用浏览器原生 Basic 认证，页面和 WebSocket 均要求认证；原始 RFB TCP 端口不开放。Safari 的 Basic 认证 WebSocket 兼容性存在上游限制，优先使用 Chromium 系浏览器。[客户端说明](https://docs.kasmvnc.com/docs/clientside)

远程部署时，在访问端电脑建立 SSH 隧道：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:1150:127.0.0.1:1150 user@docker-host
```

随后打开访问端的 `http://127.0.0.1:1150/`。访问端端口已占用时，修改 `-L` 的第一个 1150。

LAN 直连可将 BIND_IP 改为宿主机实际 LAN 地址，同时限制来源并配置 HTTPS。Docker 发布端口会调整防火墙规则，应实测可达性，不能仅依据 UFW 面板判断。[Docker 端口发布说明](https://docs.docker.com/engine/network/port-publishing/)

优先扫码登录。Cookie 注入需设置 `ENABLE_COOKIE_EXTENSION=true` 并填写自己的 COOKIE 字段，再重新创建容器。扩展由本工程提供，不加载远程脚本；值通过 JSON 编码生成到容器私有运行目录。Cookie 的客户端到期日期不保证服务端继续接受它。

`.env`、profile 和备份均作为账号凭据保护。Docker 管理员可读取容器环境变量。不要把实际账号配置提交或加入镜像构建上下文。

## 自适应分辨率

默认入口显式设置 `resize=remote`、自动连接和重连。KasmVNC 自带网页客户端的 remote resize 与服务端 `AcceptSetDesktopSize=1` 配合，改变 X11 桌面的真实分辨率。原后端继续使用 `runtime/novnc/mandatory.json` 强制 remote resize，覆盖旧的浏览器偏好。

Openbox 管理最大化窗口。首次连接、访问端窗口变化和进出全屏时，远程桌面应随可用视口变化；DISPLAY_WIDTH、DISPLAY_HEIGHT 仅决定连接前的尺寸。网页设置中不要把 Resize 改为 Scaling 或 Disabled。高 DPI 默认按 CSS 像素适配，避免无意加倍编码像素数。

```bash
docker compose exec -e DISPLAY=:115 browser115 xrandr --current
```

改变窗口后再次执行，确认实际尺寸变化，并检查 115 内容重新布局。流水线使用真实网页客户端验证 1024×768、1280×720、1920×1080、1600×900，要求网页 canvas 与 xrandr 同时匹配。[KasmVNC 配置](https://docs.kasmvnc.com/docs/configuration/)

多个访问端共享同一桌面和分辨率，不同窗口大小可能互相影响。独立会话使用独立 Compose 项目、profile 和宿主机端口。

## HTTPS 认证入口

默认后端使用 `nginx/115-kasm.conf.example`，在同一宿主机 Nginx 的 http 上下文加载。替换域名、证书路径和上游端口，配置证书与续期，执行 `sudo nginx -t` 后重载 Nginx。1150 保持回环绑定。示例将 Authorization 与 WebSocket 升级头转发给 KasmVNC，由原生认证校验配置的用户名和长密码。

回退后端使用原来的 `nginx/115.conf.example`，另外配置 htpasswd 长密码认证。切换后端时同步切换反代示例；两个独立 Basic 认证层不能使用不同凭据并覆盖同一个 Authorization 头。

Nginx 若在容器中，将服务放到同一 Docker 网络，上游改为 `browser115:1150`。[Nginx WebSocket 文档](https://nginx.org/en/docs/http/websocket.html)

## 维护和备份

```bash
docker compose ps
docker compose logs --tail=200 browser115
docker compose stats --no-stream
df -h data/downloads
# 手动关闭浏览器后可从任务栏打开，或执行：
docker compose exec -d browser115 /opt/115/115.sh
```

浏览器和桌面日志统一输出到容器日志，按每个 10 MB、最多 3 个轮转。健康检查验证 Kasm 原生认证 HTTP、X11 和 Openbox；legacy 模式额外验证 RFB greeting，不验证账号、下载任务或网页。Docker 不会仅因 unhealthy 自动重启，应接入已有告警系统。

同一 profile 只能由一个实例使用。下载文件可能属于 root，可对下载目录设置共享组或默认 ACL，不使用 chmod 777，也不假设 PUID 和 PGID 能改变这个自建镜像的进程身份。

升级前关闭浏览器以落盘，再停止容器。默认目录布局下：

```bash
docker compose stop
mkdir -p backups
chmod 700 backups
sudo tar -czf "backups/115-state-$(date +%Y%m%d-%H%M%S).tar.gz" \
  .env compose.yaml compose.compat.yaml data/profile
docker compose start
```

另备份整个工程，包含 Dockerfile、runtime、rootfs、vendor 与校验清单。下载文件使用独立快照或备份工具。profile 使用绝对路径时，备份实际目录。凭据备份加密并放在独立存储，定期验证恢复。

## 升级与回滚

在当前镜像内切换回原后端，设置恰好 8 位字母数字密码，再执行：

```bash
# 本地构建模式
docker compose -f compose.yaml -f compose.legacy.yaml up -d --build
# 流水线镜像模式
docker compose -f compose.yaml -f compose.registry.yaml -f compose.legacy.yaml up -d --no-build
```

也可以在 `.env` 中设置 `REMOTE_BACKEND=legacy`，或在群晖 Container Manager 重新创建容器时设置同名环境变量。恢复 Kasm 时设置 `REMOTE_BACKEND=kasm` 并去掉 legacy 覆盖文件。两种后端使用同一版本 115 和同一数据卷，不同时打开同一 profile。更换镜像版本仍需按下文备份 profile。


主题或脚本更新后执行 `docker compose up -d --build`。更新软件版本时，下载候选归档并记录来源和 SHA256，同步 vendor/manifest.json、SHA256SUMS、Dockerfile 归档名称和本地镜像标签；构建不自动跟随 master 或 latest。先在独立 profile 上验证。

更新前可保存旧镜像：

```bash
docker image tag 115-browser-local:36.0.1 115-browser-local:before-change
```

回滚需停止容器并保留当前 profile，再恢复升级前的 profile 和工程配置。新版 Chromium 可能升级 profile 格式，镜像和 profile 必须配套恢复。已有旧镜像时，将 `.env` 中 IMAGE_115 改为旧标签，执行 `docker compose up -d --no-build --pull never`；此时不要加 --build，以免新代码覆盖旧镜像。

归档被删除后，`python3 scripts/vendor.py --fetch` 可按锁定 URL 下载并校验，不改变版本；校验失败会拒绝文件。

## OneDev 流水线与 Harbor 镜像

代码仓库为 `ssh://one.devshuai.com/shuai-toys/115-docker.git`，镜像为 `registry.devshuai.com/shuai-repo/115-docker`。所有构建定义和验收脚本均在当前工程，不引用外部流水线模板。

`.onedev-buildspec.yml` 包含两个作业：

- `validate`：所有分支更新和 PR 更新触发，校验 vendor 的 SHA256、Shell 语法及配置和进程管理测试。
- `publish-image`：main 更新触发，依赖 validate 成功。构建 linux/amd64 镜像并推送 `build-<构建编号>` 和 `sha-<完整提交哈希>`；再从 Harbor 拉取该镜像，启动桌面和 115 浏览器，依次验证两种后端的健康与真实网页连接、Kasm 原生认证和未认证拒绝、原后端 RFB 认证、四种实际分辨率、最大化测试网页布局与静态/动画性能。随后发布 `.ci-artifacts/**` 中的 JSON、截图和诊断日志。成功后将同一镜像发布为 `latest`，并输出 manifest。

验收失败时不更新 latest；失败候选仍保留在构建编号标签，便于诊断。生产部署使用验收成功的构建编号或 digest。发布作业使用同一 sequentialGroup；应让其使用同一个明确的执行器，避免并发覆盖 latest。重跑旧提交会再次发布该旧提交，回滚或重跑时需留意。

执行器命令容器默认只有 64 MiB `/dev/shm`，验收中的两个 Chromium 进程使用 `--disable-dev-shm-usage`，通过临时目录进行内存映射，避免大分辨率时卡住。该参数只在 CI 测试启动器中设置；实际部署仍使用 Compose 的 1 GiB `shm_size`。性能报告记录这一测试条件。

首次使用需在 OneDev 和 Harbor 准备以下设置：

1. Harbor 中存在 `shuai-repo` 项目，推送账号拥有该项目 Pull/Push 权限；发布作业还需 Pull 权限才能验收。
2. OneDev 本项目或父项目的 Settings → Build → Job Secrets 包含 `registry-username` 和 `registry-password`。用户名也通过 Secret 引用，可支持 Harbor robot 账号中的 `$`。两个 Secret 的 Authorization 设为 `on branch "main"`，不要写入 Git 或镜像。[OneDev Secret 文档](https://docs.onedev.io/tutorials/cicd/job-secrets)
3. 有可运行 amd64 容器的 Server Docker Executor 或 Remote Docker Executor，安装 Docker 和 Buildx，并启用 **Enable Buildx Image Build** 和 **Enable Buildx Image Tools**，后者用于验收后更新 latest 及检查 manifest。自动发现的执行器默认不允许这些步骤。具备这两项能力的执行器应限制到本项目 main 上的可信 publish-image 作业；validate 可使用单独的普通 Docker 执行器。选择现有合适的执行器，需要固定名称时在作业里设置 jobExecutor。[OneDev 镜像构建设置](https://docs.onedev.io/tutorials/cicd/build-docker-image)
4. 执行器可访问 Docker Hub 和 Harbor；系统安装阶段无需 Debian 软件源，信任 Harbor 的 TLS 证书。运行验收会执行 openssl 的 legacy DES provider，仅用于 VNC 协议认证；没有修改 TLS 验证。

推送 main 后，在 OneDev 的 Builds / Pipeline 查看这两个作业。首次完成前不能将候选镜像视为已验收。若安装的 OneDev 不支持 buildspec version 53，应先通过其构建编辑器确认兼容版本；此文件采用当前官方的 type 字段格式。

目标 Docker 主机使用流水线镜像部署：

```bash
cp .env.example .env
# 编辑 .env：设置 VNC_PASSWORD 和已验收的 IMAGE_115
# IMAGE_115=registry.devshuai.com/shuai-repo/115-docker:build-<number>
mkdir -p data/profile data/downloads
docker login registry.devshuai.com
docker compose -f compose.yaml -f compose.registry.yaml pull
docker compose -f compose.yaml -f compose.registry.yaml up -d --no-build
```

私有 Harbor 需要先登录；登录信息由 Docker 凭据配置管理。后续命令保持相同的 -f 参数。registry 覆盖文件设置 pull_policy 为 always；本地构建模式不加此覆盖文件。回滚将 IMAGE_115 改为旧的成功标签或 digest，重新 pull 和 up；仍需恢复与镜像配套的 profile 备份。

## 验证与兼容

```bash
python3 scripts/vendor.py
python3 -m unittest discover -s tests -v
bash -n runtime/*.sh scripts/ci-smoke.sh
docker compose config --quiet
docker compose build
```

启动管理测试需要 Bash 4.3 或更新版本；Linux 镜像满足要求，macOS 默认旧 Bash 会跳过这部分测试。TEST_BASH 可指定本机已有的新版本。配置仍需在目标 Docker 主机完成完整构建与运行验收。

Compose 使用 bridge 网络、no-new-privileges 和默认 seccomp。浏览器保留证书校验与默认站点隔离，当前仍采用 root 和 --no-sandbox，供可信人员操作专用桌面。不挂载 Docker socket、宿主机根目录或额外敏感目录。

只有日志显示确切 seccomp 兼容问题时才执行：

```bash
docker compose -f compose.yaml -f compose.compat.yaml up -d --build
```

后续维护携带相同的 -f 参数。该配置移除系统调用过滤，降低隔离；一般黑屏不通过 privileged 或 host 网络解决。[Docker seccomp 文档](https://docs.docker.com/engine/security/seccomp/)

## 群晖启动报错处理

在 Container Manager 界面直接创建容器时，环境变量名是 **`PASSWORD`**，Kasm 模式值为 8～128 位英文字母或数字（推荐至少 16 位），并设置 `REMOTE_USER=115`；legacy 模式恰好 8 位。`VNC_PASSWORD` 是本项目 Compose 使用的 `.env` 变量，Compose 会将其映射为容器的 `PASSWORD`。两者不能在直接创建容器时混用。修改环境变量后重新创建容器，并继续挂载原来的 `/etc/115` 和 `/opt/Downloads` 数据目录；仅重新启动旧容器不会更新其环境配置。

`XDG_RUNTIME_DIR "/run/115" not available` 是旧镜像的启动顺序问题；使用包含 `runtime/entrypoint.sh` 修复的镜像。无需把 `/run/115` 映射到 NAS 持久目录，也不要在 Container Manager 中覆盖镜像的启动命令。

`Cannot set up inotify ... No space left on device` 表示添加文件监听失败，通常为同一宿主机用户的 inotify 配额耗尽，也可能是内核资源不足，不能仅据此判定磁盘已满。[Linux inotify 错误说明](https://man7.org/linux/man-pages/man2/inotify_add_watch.2.html)

通过 SSH 登录 **群晖宿主机**，先检查磁盘、inode 与监听配额：

```bash
df -h / /volume1
df -i / /volume1
sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances
```

关闭不必要的文件监听进程或容器；如果配额确实不足，可以先临时提高下限，同时保留已有的更高值：

```bash
watch_limit=$(sysctl -n fs.inotify.max_user_watches)
instance_limit=$(sysctl -n fs.inotify.max_user_instances)
[ "$watch_limit" -ge 524288 ] || watch_limit=524288
[ "$instance_limit" -ge 1024 ] || instance_limit=1024
sudo sysctl -w "fs.inotify.max_user_watches=$watch_limit" \
  "fs.inotify.max_user_instances=$instance_limit"
```

这些是宿主机共享内核参数，不要写入 Compose 的容器 sysctls。临时设置在宿主机重启后可能失效；确认有效后，在 DSM 任务计划新增开机触发任务，以 root 执行上述检查并提高下限的脚本（任务中省略 sudo）。提高上限不会预先分配全部监听内存，实际内存使用随监听数量增加。若已经高于这些下限，继续排查大量监听的进程和内核内存情况，避免反复提高参数。

## 上线验收

- 自建镜像构建成功，账号配置没有进入镜像。
- 深色桌面、任务栏、图标和壁纸正常，中文无乱码。
- 本机或 SSH 连接正常，未授权网络无法直连 1150，HTTPS 入口要求认证。
- 扫码和测试下载成功，宿主机能找到文件。
- 重建后文件与浏览器配置保留，登录仍受服务端策略影响。
- 改变窗口并进出全屏，xrandr 确认实际尺寸变化，115 最大化窗口跟随。
- 关键桌面服务故障触发容器恢复，主动关闭浏览器可从任务栏重新打开。
- 备份可恢复到独立实例，回滚恢复配套旧镜像和 profile。
