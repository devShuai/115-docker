# 第三方组件来源

本工程参考 [dream10201/115Docker](https://github.com/dream10201/115Docker) 的架构，自行提供镜像构建、桌面配置、进程管理、Cookie 扩展和远程分辨率配置。构建不使用该项目或作者 WebVNC 的应用镜像。

`vendor/manifest.json` 记录各安装包或源码归档的版本、下载地址、文件大小与 SHA256。校验值来自首次通过 HTTPS 下载的文件，用于后续一致性验证，不代表第三方签名验证或安全审计。

| 组件 | 版本 | 来源和许可 |
| --- | --- | --- |
| 115 浏览器 Linux amd64 安装包 | 36.0.1 | [115 官方下载](https://down.115.com/client/115pc/lin/115br_v36.0.1.deb)，闭源软件，适用 115 的许可与使用条款 |
| noVNC | 1.6.0 | [noVNC](https://github.com/novnc/noVNC/tree/v1.6.0)，主要为 MPL 2.0，具体以归档内 LICENSE 文件为准 |
| Websockify | 0.13.0 | [Websockify](https://github.com/novnc/websockify/tree/v0.13.0)，LGPL 3.0，具体以归档内许可证文件为准 |
| KasmVNC | 1.5.0 | [KasmVNC](https://github.com/kasmtech/KasmVNC/tree/v1.5.0)，GPL 2.0；官方 trixie amd64 安装包和对应完整服务端源码归档均在 vendor |
| KasmVNC 网页客户端 | 475ecfa5356579ef222983c7ce4619a7576a3bce | [Kasm noVNC](https://github.com/kasmtech/noVNC/tree/475ecfa5356579ef222983c7ce4619a7576a3bce)，以归档内 LICENSE 为准；这是 KasmVNC 1.5.0 固定的 kasmweb 子模块提交 |
| Debian 和桌面系统包 | manifest 中逐包锁定版本 | [Debian](https://www.debian.org/)，283 个传递依赖 deb 已保存在 vendor/debian，各组件许可保存在镜像的 `/usr/share/doc/组件/copyright` |
| Papirus 图标 | Debian 软件源版本 | [Papirus](https://github.com/PapirusDevelopmentTeam/papirus-icon-theme)，GPL 3.0，以系统包版权信息为准 |

noVNC、Websockify、KasmVNC 及对应 kasmweb 客户端源码与许可证保留在 `vendor/`。KasmVNC 的安装包包含编译完成的网页资源，不需要 npm 安装。115 安装包为本地构建准备；若要公开分发工程或镜像，先确认 115 安装包的分发许可。Debian 基础镜像固定 OCI digest，仍需预先拉取或缓存；系统和应用安装步骤在禁用网络时从当前工程完成。
