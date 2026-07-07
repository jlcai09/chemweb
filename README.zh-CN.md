# chemssh 中文说明

[English](README.md) | 中文

`chemssh` 是一个面向计算化学、计算催化和材料模拟目录的浏览器工作台。它通常部署在数据所在的位置，例如远程 Linux 服务器或 HPC 登录节点，然后通过 SSH 端口转发或可信 Launcher 代理在本地浏览器访问。

它适合用一个轻量 Web UI 管理模拟工作目录：文件管理、文本/日志编辑、结构预览、调度队列、浏览器终端、持久化画板和可选插件。

ChemSSH 是本地信任模型工具。请用拥有目标文件的用户运行，除非明确要暴露服务，否则绑定 `127.0.0.1`，并尽量把 `workspace.root` 设小。

## 安装

### 生产环境（从发行版）

**推荐大多数用户使用。**

如果你从 [GitHub Releases](https://github.com/your-username/chemssh/releases) 下载了发行版，前端已经构建好了，只需要 Python：

```bash
# 1. 解压发行版
tar -xzf chemssh-0.3.4.tar.gz
cd chemssh-0.3.4

# 2. 创建 Python 虚拟环境并安装
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"

# 就这样！不需要 Node.js 或 npm。
```

预构建的 `frontend/dist/` 目录包含所有优化后的静态文件。

<details>
<summary>从源码开发安装</summary>

### 开发环境（从源码）

**仅供需要修改代码的开发者使用。**

如果你克隆了代码仓库并想从源码构建：

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"

npm.cmd --prefix frontend install
npm.cmd --prefix frontend run build
```

Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"

npm --prefix frontend install
npm --prefix frontend run build
```

</details>

安装完成后命令行入口为：

```bash
chemssh --help
```

## 配置

复制并修改 `config.yaml`。最重要的是工作区根目录：

```yaml
workspace:
  root: /home/user/project
```

所有文件读写、上传、下载、删除、移动、复制、预览、终端 cwd 同步和作业提交都会被限制在 `workspace.root` 内。

常用配置项：

```yaml
server:
  host: 127.0.0.1
  port: 8888
  idle_shutdown_seconds: 3600

workspace:
  root: .
  allow_delete: true
  max_upload_size_mb: 500
  max_read_size_mb: 5

scheduler:
  type: slurm        # slurm 或 pbs
  refresh_interval: 10

viewer:
  max_file_size_mb: 50
  ase:
    enabled: true
    prefer_binary: true
    max_atoms: 200000
    max_frames: 5000
    binary_chunk_frames: 32

terminal:
  enabled: true
  shell: null
  max_sessions: 10  # 每个浏览器 client_id 的限制
  allow_sync_cwd: true

security:
  enable_token: false
  token: change-me

plugins:
  enabled: true
  directories: []

client_cache:
  enabled: true
  directory: null
  cleanup_offline_days: 14
```

也可以用 `CHEMSSH_WORKSPACE` 或 `chemssh --workspace-root ...` 覆盖 `workspace.root`。

设置 `security.enable_token: true` 后，所有 `/api` HTTP 请求和 `/api` WebSocket 连接都必须携带配置中的 token。HTTP 客户端推荐发送 `Authorization: Bearer <token>`；浏览器下载、插件 iframe 和终端 WebSocket URL 可以使用 `token=<token>` 查询参数。服务暴露到可信 localhost 隧道之外前，请把 `change-me` 换成长随机 token。

<details>
<summary>维护者发行打包</summary>

## 如何发行

**仅供维护者创建发行包使用。**

使用专用的发行版打包脚本（版本号自动从 `backend/app/__init__.py` 读取）：

**Linux/Mac/Windows (Git Bash):**
```bash
chmod +x create-release-archive.sh
./create-release-archive.sh
```

Windows 用户请使用 Git Bash 运行 `.sh` 脚本。该脚本是受支持的发行打包入口，会同时生成 `.tar.gz` 和 `.zip` 格式。

脚本会自动构建前端，创建 `release/chemssh-{版本号}/` 发行目录、`.tar.gz` 和 `.zip` 压缩包及校验和文件。发行包包含预构建的 `frontend/dist/`（用户无需 Node.js）。

如果需要离线 PyInstaller runtime，并且希望后端代码保持明文、后续可直接替换更新，请阅读 [packaging/pyinstaller-external-backend.md](packaging/pyinstaller-external-backend.md)。

详细发行流程见 [docs/RELEASE.md](docs/RELEASE.md)。

</details>

## 启动

使用已构建的前端启动：

```bash
chemssh --config config.yaml --host 127.0.0.1 --port 8888
```

CLI 启动前会检查目标端口。如果同一端口已有 ChemSSH 服务，且 `workspace.root` 与当前配置一致，CLI 会复用已有服务并打印 URL。如果端口被其他程序占用，或被不同工作区的 ChemSSH 占用，会明确失败。

端口复用策略：

```bash
chemssh --config config.yaml --reuse-existing auto
chemssh --config config.yaml --reuse-existing never
chemssh --config config.yaml --reuse-existing any-chemssh
```

只检查端口、不启动服务：

```bash
chemssh --config config.yaml --check-port
```

开发模式通常分别启动后端和前端：

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8888 --reload
npm.cmd --prefix frontend run dev
```

Linux 或 macOS 上把 `npm.cmd` 换成 `npm`。Vite dev server 运行在 `127.0.0.1:5173`，并把 `/api` 代理到 `127.0.0.1:8888`。

## SSH 端口转发

远程服务器上启动：

```bash
chemssh --config config.yaml --host 127.0.0.1 --port 8888
```

本地机器执行：

```bash
ssh -p 22 -L 8888:127.0.0.1:8888 user@server
```

然后在本地浏览器打开：

```text
http://localhost:8888
```

如果 HPC 登录节点需要用不同 loopback 或内部地址作为服务目标，请相应调整 `-L` 右侧地址。

<details>
<summary>功能细节</summary>

## 主要功能

- 工作区文件管理：浏览、新建文件夹/文件、上传文件或文件夹、下载单文件或 zip 选择集、重命名、删除、移动和复制。
- 预览与编辑：Monaco 文本编辑、日志 tail、大文件确认流程，以及工作台/画板中统一的预览窗口。
- 结构查看：后端用 ASE 解析常见分子和材料格式，前端用 Three.js 渲染结构和轨迹，支持 stick/sphere/line、晶胞显示、超胞/wrap、原子选择、测量、固定原子标记，以及稳定拓扑轨迹的二进制分帧传输。
- 调度系统：默认 Slurm，也可配置 `scheduler.type: pbs`；支持队列列表、作业详情、取消、hold/release，以及通过 `sbatch` 或 `qsub` 提交脚本。
- 终端：按浏览器 client 隔离的 WebSocket 终端 session，xterm.js UI，多标签，cwd 同步，拖入路径写入，浏览器侧上传/下载辅助，以及画板中文件管理器绑定。
- 画板：持久化自由画板，窗口类型包括文件管理器、终端、预览、队列、tail 和插件；窗口布局与视口保存在 client cache。
- 插件：扫描 `plugins/` 和配置目录中的 manifest，按需激活；可挂载 Python 后端路由、iframe 前端资源、文件预览 provider，并提供依赖状态与安装入口。
- Launcher bridge：当 ChemSSH 通过兼容的 Launcher 同源代理打开时，前端可发现系统图标、本地打开动作和同步事件轮询能力。直接访问 ChemSSH 时这些能力会静默降级。
- Client cache 隔离：偏好与画板按浏览器 `client_id` 和工作区 scope 存储，不同 origin、用户、主机或 `workspace.root` 不会复用过期路径和布局。

</details>

<details>
<summary>开发与架构说明</summary>

## 项目结构

```text
backend/app/                  FastAPI 应用、路由、服务、provider、模型
frontend/src/                 Vue 工作台、画板、终端、预览、API wrapper
frontend/tests/               轻量前端逻辑测试
plugins/cclib/                随仓库提供的示例插件
docs/API.md                   API 和窗口交互参考
docs/PLUGIN_DEVELOPMENT.md    插件 manifest 与生命周期说明
tests/                        pytest 后端测试
config.yaml                   示例运行配置
```

## 架构概览

ChemSSH 是一个 FastAPI 应用；当前端构建完成后，后端会直接服务 `frontend/dist` 中的 Vue SPA。

```text
Browser
  Vue 3 / Element Plus / Vite SPA
  xterm.js 终端、Monaco 编辑器、Three.js 结构查看器
        |
        | /api 下的 HTTP / WebSocket
        v
FastAPI backend
  routers: files, structures, queue, jobs, terminal, plugins, client-cache, system
  services: 工作区安全文件操作、ASE 解析、调度器、插件 host、client cache
  providers: local filesystem, local pty/winpty, Slurm, PBS
        |
        v
workspace.root、调度命令、插件目录、cache/
```

## 后端技术路线

后端使用 Python 3.10+、FastAPI、Uvicorn、Pydantic、PyYAML、ASE、NumPy、Brotli、`ptyprocess`，Windows 终端还使用 `pywinpty`。

所有后端收到的文件路径都会经过 `WorkspaceSecurity` 解析，必须留在 `workspace.root` 内。文件操作目前通过本地文件系统 provider 实现；ChemSSH 本体没有实现远程 SFTP 文件系统后端。

主要 API 都在 `/api` 下：

- `/api/system/*`：运行环境信息和 CLI 端口复用所需的服务身份。
- `/api/files/*`：目录列表、读写、上传/下载、打包下载、tail、移动和复制。
- `/api/structures/ase/*`：ASE 预览、单帧、JSON 分帧和二进制分帧。
- `/api/queue/*` 与 `/api/jobs/*`：调度队列查看和作业提交。
- `/api/terminal/*`：终端 session 与 WebSocket I/O。
- `/api/plugins/*`：插件列表、激活、静态资源、插件 API 和依赖操作。
- `/api/client-cache/*`：浏览器客户端偏好、画板、heartbeat 和缓存清理。

Brotli middleware 会在客户端支持 `Accept-Encoding: br` 时压缩适合的响应。结构二进制分帧使用 `application/vnd.chemssh.structure+bin`。

## 前端技术路线

前端使用 Vue 3 `<script setup lang="ts">`、Vite、Element Plus、`@element-plus/icons-vue`、Monaco Editor、xterm.js 和 Three.js。

主要视图：

- `Workspace.vue`：传统分栏工作台，包含文件管理器、预览、队列/tail/终端/插件面板、上传/拖拽处理和 Launcher bridge 发现。
- `CanvasBoard.vue`：自由画板，保存 boards、windows、viewport 和各窗口 payload。
- `Settings.vue`：本地/cache 设置，例如当前 `client_id` 和清理缓存。

共享前端 API wrapper 位于 `frontend/src/api`。新增 UI 应优先复用这些 wrapper，尤其是文件 metadata、预览 provider 匹配、client preferences、拖拽 payload 和 workspace scope 清理逻辑。

## Launcher Bridge 联动

ChemSSH 本体不实现本地 Launcher bridge 端点。当它通过兼容的 `chemssh-launcher` 同源代理访问时，前端会尝试：

```http
GET /api/chemssh-bridge/capabilities
```

如果 bridge 返回可用能力和端点路径，ChemSSH 可以：

- 通过 bridge 图标端点显示系统文件图标；
- 在右键菜单中提供“用本地默认程序打开”或“用文本编辑器打开”；
- 轮询同步事件，在本地编辑回写后刷新相关文件管理器。

如果这些端点不存在、请求失败，或返回 `enabled !== true`，ChemSSH 会回退为普通浏览器行为。前端不会把 Launcher 的本地缓存路径持久化，也不会把 profile id、SFTP session id、密码或 host key 写入自身 client cache。

本仓库不修改 `chemssh-launcher`；bridge 契约记录在 `docs/API.md`。

## 插件

插件从项目 `plugins/` 和 `plugins.directories` 配置目录发现。发现阶段只读取 `chemssh-plugin.json`；只有用户打开或激活插件面板时，才加载 Python 后端和 iframe 资源。

激活后的插件可以提供：

- `/api/plugins/{plugin_id}/assets` 下的静态前端资源；
- `/api/plugins/{plugin_id}/api` 下的后端路由；
- 通过 iframe postMessage 注册的文件管理器预览 provider；
- Python 依赖状态/安装操作，模式包括 `host`、插件 `venv` 和 `external`。

`plugins/cclib` 是随仓库提供的示例插件。它只有在插件被激活且 Python 依赖可用后，才会通过 cclib 尝试解析量子化学输出文件；仅仅把插件放在磁盘上不会自动接管所有文件预览。

插件设计和 manifest 细节见 `docs/PLUGIN_DEVELOPMENT.md`。

## 缓存与工作区 Scope

前端会创建浏览器 `client_id` 并通过请求头发送：

```http
X-ChemSSH-Client-Id: client_xxx
```

偏好和画板状态通过 `/api/client-cache` 保存。当前工作区隔离还会发送：

```http
X-ChemSSH-Cache-Scope: scope_xxx
```

scope 基于浏览器 origin、用户名、主机名和 `workspace_root` 生成。这样同一浏览器访问不同 ChemSSH 工作区时，不会串用布局、当前路径、终端 tab 绑定和画板窗口。加载缓存时，前端会清理越界路径，把不属于当前工作区的路径丢弃或替换为当前工作区根目录。

默认情况下，服务端 client cache 存在项目根目录的 `cache/` 中；也可以通过 `client_cache.directory` 改位置。服务启动时会按 `client_cache.cleanup_offline_days` 清理长时间离线的 client cache。

## 开发与验证

常用命令：

```bash
python -m pytest
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run dev
npm.cmd --prefix frontend run preview
```

聚焦后端测试示例：

```bash
python -m pytest tests/test_api.py
python -m pytest tests/test_terminal.py tests/test_terminal_transfer.py
```

根目录 `package.json` 还提供：

```bash
npm run frontend:build
npm run frontend:dev
npm run frontend:preview
```

## 安全与限制

- 默认绑定 `127.0.0.1`，建议通过 SSH 隧道或可信 Launcher 代理访问。
- `workspace.root` 应设置为满足需求的最小目录。
- 后端路径检查能防止 ChemSSH API 越出 `workspace.root`；但用户在交互终端里运行的任意命令不受这种路径检查沙箱限制。
- 文件读取和结构预览有大小限制；大文件预览需要前端显式确认后才使用 `force=true`。
- 可用 `workspace.allow_delete: false` 禁用删除。
- 调度动作调用固定命令名：Slurm 使用 `squeue`、`scontrol`、`scancel`、`sbatch`；PBS 使用 `qstat`、`qdel`、`qhold`、`qrls`、`qsub`。作业 ID 和脚本名会校验，提交接口接受脚本文件名而不是任意 shell 字符串。
- 终端 session 按浏览器 client 隔离并受 `terminal.max_sessions` 限制，但仍以 ChemSSH 服务端用户权限运行。
- 插件激活后与 ChemSSH 处在同一信任边界内运行，请只安装可信插件。

## 文档

- API 和窗口交互参考：[docs/API.md](docs/API.md)
- 插件开发说明：[docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md)
- 服务运行时 FastAPI 文档：`http://127.0.0.1:8888/docs`

</details>
