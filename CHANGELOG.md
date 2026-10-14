# 更新日志

版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)：主版本号.次版本号.修订号。镜像标签的含义见 README 的“版本与发布”。

## v1.0.0 - 2026-10-14

首个正式版本。从这个版本开始按版本号发布镜像，`latest` 只跟随正式版本。

### 主要功能

- 在 Docker 中运行 115 浏览器 36.0.1：KasmVNC 1.5.0 + Xfce 4.20 中文桌面，网页直接访问，分辨率跟随窗口，支持 Mac Retina 和缩放；保留 TigerVNC + noVNC 作为备用后端。
- 下载页面与 JSON API（端口 1151）：磁力、ed2k、HTTP、FTP 链接加入云下载，完成后勾选内容或整体下载到 NAS；可以浏览网盘和 115 分享并勾选下载。
- 下载进度分「下载中」「最近下载」「云下载」「云下载完成」四个标签；云下载按 115 自己的进行中、失败、已完成三个列表读取，刚完成的任务不会被旧任务挤掉。
- 自动保持 115 登录：重开浏览器或重建容器后几秒内恢复。
- `115dl` 命令行、Markdown 与 OpenAPI 接口文档，以及可一键安装的 `115-download` agent skill。
- 依赖全部固定在工程内并校验 SHA256，构建时不访问 apt 源；OneDev 与 GitHub Actions 实测桌面、缩放、性能和登录恢复后才发布镜像。

### 版本与发布

- 镜像标签：`latest`、`1.0.0`、`1.0`、`1` 跟随正式版本，`edge` 跟随 main 最新验收通过的构建。
- 页面左上角、`/api/status`、`115dl status`、镜像标签和 `scripts/check-update.py` 都能看到当前版本。
- `scripts/release.py` 一条命令发版；GitHub Release 附带版本说明和 skill 包。
