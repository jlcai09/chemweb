# chemssh API 与窗口交互指南

本文档面向后续维护者和 coding agent。目标是让新模块可以快速接入 chemssh 的后端接口、文件管理器、终端、预览器和队列窗口。

所有后端接口都以 `/api` 开头。错误统一返回：

```json
{
  "success": false,
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "File not found"
  }
}
```

前端请求封装在 `frontend/src/api/` 下。新增模块优先复用这些封装，不要在组件里手写重复的 `fetch` 错误处理。

## Token 鉴权

当配置启用：

```yaml
security:
  enable_token: true
  token: "一串随机长 token"
```

后端会要求所有 `/api` HTTP 接口和 `/api` WebSocket 连接携带 token。未携带或 token 不匹配时，HTTP 返回 `401`：

```json
{
  "success": false,
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "Invalid or missing ChemSSH token"
  }
}
```

支持的携带方式：

- HTTP API 首选请求头：`Authorization: Bearer <token>`。
- 兼容请求头：`X-ChemSSH-Token: <token>`。
- 浏览器无法设置请求头的下载链接、iframe 和 WebSocket 可使用查询参数：`token=<token>` 或 `access_token=<token>`。
- 后端在通过 header/query 校验成功的 HTTP 响应中写入 `HttpOnly`、`SameSite=Strict`、`Path=/api` 的 `chemssh_token` cookie；同源的插件 iframe 后续请求可通过该 cookie 继续通过鉴权。

前端封装：

- `frontend/src/api/http.ts` 会从 `?token=...`、`?chemssh_token=...`、URL hash、`window.__CHEMSSH_TOKEN__`、`sessionStorage["chemssh.token.v1"]` 或构建变量 `VITE_CHEMSSH_TOKEN` 读取 token，并自动给 `request()`、`cachedGet()`、`requestBlob()` 加 `Authorization`。
- 当 HTTP 请求收到 `401 AUTH_REQUIRED` 时，`frontend/src/api/http.ts` 会弹出 ChemSSH token 输入框；确认后通过 `setAuthToken(token)` 写入 `sessionStorage["chemssh.token.v1"]` 并自动重试当前请求。同一时间多个 401 会共享一个输入框，避免初始化阶段重复弹窗。带进度的上传、Launcher bridge 请求和结构二进制帧读取也应复用这套 token challenge 流程。
- `downloadUrl()`、`downloadSelectionUrl()`、插件 asset URL 和终端 WebSocket URL 会把 token 放入查询参数，因为这些浏览器加载方式无法可靠设置自定义 header。
- `setAuthToken(token)` 可供 Launcher 在打开前端后写入本次会话 token；建议只使用内存或 `sessionStorage`，不要长期保存到 `localStorage`。

`/api/system/identity` 也受 token 保护。`chemssh` CLI 使用同一份配置探测已有服务时，会在 `security.enable_token=true` 时自动带上配置中的 token。

## 系统信息

### `GET /api/system/info`

返回当前运行环境、项目版本、调度器类型和工作区根目录。

```json
{
  "project_version": "0.1.0",
  "username": "user",
  "hostname": "node01",
  "cwd": "/home/user/project",
  "python_version": "3.12.7",
  "scheduler": "slurm",
  "workspace_root": "/home/user",
  "max_upload_size_mb": 500
}
```

前端类型与封装：`frontend/src/api/system.ts`。

### `GET /api/system/identity`

返回当前服务的轻量身份信息。`chemssh` CLI 启动前会用此接口判断目标端口是否已由可复用的 ChemSSH 服务占用。

```json
{
  "app": "chemssh",
  "project_version": "0.2.0",
  "pid": 12345,
  "scheduler": "slurm",
  "workspace_root": "/home/user"
}
```

启动复用规则：

- 端口未占用：正常启动新服务。
- 端口占用且 `/api/system/identity` 返回 `app="chemssh"`：默认仅当 `workspace_root` 与当前配置一致时复用已有服务。
- 端口占用但不是 ChemSSH，或无法读取身份信息：启动失败并报告端口占用。

CLI 参数：

- `--reuse-existing auto`：默认值，复用同工作区的已有 ChemSSH 服务。
- `--reuse-existing never`：不复用已有服务；端口已占用时直接失败。
- `--reuse-existing any-chemssh`：复用该端口上的任意 ChemSSH 服务，即使工作区不同。
- `--check-port`：只检测目标端口并退出，不启动服务。端口空闲或可复用时退出码为 `0`；端口被不可复用服务占用时退出码为 `1`。

## 文件管理

文件路径必须在 `workspace.root` 内。后端会解析绝对路径并阻止越界访问。

### `GET /api/files/list?path=/workspace/project`

列出目录内容。省略 `path` 时列出工作区根目录。

前端封装：`listFiles(path, { refresh })` 位于 `frontend/src/api/files.ts`，会通过 `cachedGet()` 对相同目录做 1 秒 GET 缓存和并发去重；手动刷新、外部同步或冲突预检应传 `refresh: true` 绕过该目录缓存。`writeFile()`、`deletePath()`、`renamePath()`、`movePaths()`、`copyPaths()`、`makeDirectory()` 和 `uploadFile()` 成功后会清理请求缓存，避免文件变更后目录列表继续使用旧数据。

响应：

```json
{
  "path": "/workspace/project",
  "parent": "/workspace",
  "items": [
    {
      "name": "mol.xyz",
      "path": "/workspace/project/mol.xyz",
      "type": "file",
      "size": 128,
      "mtime": "2026-05-25T10:00:00",
      "extension": ".xyz",
      "preview_type": "structure",
      "format": "xyz"
    }
  ]
}
```

### `GET /api/files/read?path=/workspace/project/input.inp&force=false`

读取文本预览。默认大小上限由 `workspace.max_read_size_mb` 控制。遇到大文件时返回 `FILE_TOO_LARGE`，用户确认后前端可用 `force=true` 重试。

### `POST /api/files/write`

保存文本文件。

```json
{
  "path": "/workspace/project/input.inp",
  "content": "..."
}
```

### `POST /api/files/mkdir`

新建目录。

```json
{
  "path": "/workspace/project",
  "name": "new_case"
}
```

### `POST /api/files/upload`

上传文件，使用 multipart 表单：

- `path`: 目标目录。
- `file`: 上传文件。
- `relative_path`: 可选，上传到目标目录下的相对路径。用于文件夹上传，例如 `case/A/input.inp`。后端会逐段校验路径并自动创建父目录，路径仍必须留在工作区内。

前端封装：`uploadFile(path, file, { relativePath, onProgress })`。文件夹上传由 `Workspace.vue` 在上传前检查当前目录顶层重名项，并按用户选择逐文件调用该接口：

- 上传前会先规范化 `relative_path`：路径段中的空白字符自动替换为 `_`，然后按后端规则预检每个路径段是否只含字母、数字、点、下划线和短横线。不合规项目会在开始传输前跳过并提示，不等待后端上传失败。
- 前端通过 `GET /api/system/info` 获取 `max_upload_size_mb`，并在上传前预检；超出限制时弹出确认对话框，用户选择继续则上传并沿用后端 413 兜底；用户选择取消则跳过该批。
- `overwrite`：同名文件写入覆盖；同名目录只合并目录树，内部仅覆盖实际上传且同名的文件，远程额外文件保留。
- `skip`：跳过该顶层冲突项。
- `suffix`：把冲突顶层文件或文件夹自动改名为 `.new` 后缀，例如 `A` -> `A.new`。
- `cancel`：取消本批上传。

### `GET /api/files/download?path=/workspace/project/result.out`

下载单个文件。路径必须指向文件，目录会返回 `NOT_A_FILE`。

### `POST /api/files/download-archive`

将多个文件或目录打包为 `chemssh-selection.zip`。工具栏“下载”使用此接口。

```json
{
  "paths": [
    "/workspace/project/result.out",
    "/workspace/project/case_dir"
  ]
}
```

### `GET /api/files/download-selection?path=/a&path=/b`

拖拽下载专用 GET 接口。单个普通文件会直接返回文件；多个路径或目录会返回 zip。文件管理器拖到浏览器外部时会把该接口写入 `text/uri-list`，浏览器打开该 URL 即可下载。

### `DELETE /api/files/delete?path=/workspace/project/old.log`

删除文件或目录。仅当 `workspace.allow_delete=true` 可用；后端会通过 `WorkspaceSecurity` 校验路径必须位于工作区内。

### `POST /api/files/rename`

重命名文件或目录。

```json
{
  "old_path": "/workspace/project/a.xyz",
  "new_path": "/workspace/project/b.xyz"
}
```

### `POST /api/files/move`

把一个或多个文件/目录移动到目标目录。后端会先校验所有路径仍在工作区内、目标必须是已存在目录、不能移动工作区根目录、不能把目录移动到自身或子目录内。默认 `paths` 模式下目标目录已有同名项会返回 `PATH_EXISTS`；前端拖拽移动会先检查目标目录，同名时弹出和上传一致的冲突处理：覆盖、跳过、添加 `.new` 后缀或取消。

兼容的简单请求：

```json
{
  "paths": [
    "/workspace/project/a.xyz",
    "/workspace/project/case_dir"
  ],
  "target_directory": "/workspace/project/done"
}
```

需要表达冲突处理时使用 `items`。`target_name` 省略时使用源文件名；`overwrite=true` 允许覆盖同名文件，或把同名目录递归合并，源目录中同名文件覆盖目标文件，目标目录里额外文件保留。后缀模式由前端生成唯一 `target_name`，例如 `a.xyz.new`。

```json
{
  "target_directory": "/workspace/project/done",
  "items": [
    {
      "path": "/workspace/project/a.xyz",
      "overwrite": true
    },
    {
      "path": "/workspace/project/case_dir",
      "target_name": "case_dir.new"
    }
  ]
}
```

响应：

```json
{
  "success": true,
  "path": "/workspace/project/done",
  "message": "Paths moved"
}
```

失败响应沿用统一的 `{ "error": { "code": "...", "message": "..." } }` 格式。移动接口的 `message` 会给出中文详细原因，常见错误码包括 `PATH_NOT_FOUND`（源路径不存在）、`DIRECTORY_NOT_FOUND`（目标文件夹不存在）、`PATH_EXISTS`（目标已有同名项目）、`MOVE_INTO_SELF`（移动到自身或子目录）、`MOVE_TYPE_CONFLICT`（文件和文件夹类型冲突）和 `MOVE_FAILED`（底层 `mv` 或文件系统移动失败）。前端文件管理器会优先展示后端返回的中文详情；如果只拿到旧版英文或错误码，则兜底转换为中文提示。

### `POST /api/files/copy`

把一个或多个文件/目录复制到目标目录。请求体和 `POST /api/files/move` 保持一致，支持 `paths` 简单模式和 `items` 冲突处理模式。复制目录时如果 `overwrite=true` 且目标已有同名目录，后端会递归合并目录树：源目录中的同名文件覆盖目标文件，目标目录里的额外文件保留。复制不会删除源项目。

```json
{
  "target_directory": "/workspace/project/done",
  "items": [
    {
      "path": "/workspace/project/a.xyz",
      "overwrite": true
    },
    {
      "path": "/workspace/project/case_dir",
      "target_name": "case_dir.new"
    }
  ]
}
```

响应：

```json
{
  "success": true,
  "path": "/workspace/project/done",
  "message": "Paths copied"
}
```

常见错误码包括 `PATH_NOT_FOUND`、`DIRECTORY_NOT_FOUND`、`PATH_EXISTS`、`COPY_INTO_SELF`、`COPY_SAME_PATH`、`COPY_TYPE_CONFLICT`、`COPY_WORKSPACE_ROOT` 和 `COPY_FAILED`。前端封装：`copyPaths(paths, targetDirectory, entries)`，定义在 `frontend/src/api/files.ts`。

### `GET /api/files/tail?path=/workspace/project/slurm-123.out&lines=300`

读取日志类文件末尾 N 行，适合 `.log`、`.out`、`slurm-*.out`、`OUTCAR`、`OSZICAR` 等。

前端封装：`frontend/src/api/files.ts`。

## Launcher 本地文件联动

当 ChemSSH 通过支持本地 bridge 的 Launcher 同源代理打开时，前端会调用 `frontend/src/api/launcherBridge.ts` 中的 `loadLauncherBridgeCapabilities()` 发现能力。该请求访问 `GET /api/chemssh-bridge/capabilities`；如果接口不存在、返回 404/503、网络失败，或响应中 `enabled !== true`，前端静默降级，文件管理器行为与直接访问 ChemSSH 时一致。

能力响应由 Launcher 提供，形如：

```json
{
  "enabled": true,
  "version": 1,
  "workspace_root": "/home/user/project",
  "features": {
    "system_icons": true,
    "open_default": true,
    "open_text": true,
    "open_sync_events": true
  },
  "endpoints": {
    "icon": "/api/file-icon",
    "open": "/api/chemssh-bridge/open",
    "open_text": "/api/chemssh-bridge/open-text",
    "sync_events": "/api/chemssh-bridge/open-sync-events"
  }
}
```

前端只使用能力声明中的端点，不保存 Launcher 的本地缓存路径，不传 profile id、SFTP session id、密码或 host key 参数。文件路径仍以 ChemSSH 的 `workspace_root` 做前端边界判断；真正的远程文件权限仍由 ChemSSH 后端和 Launcher bridge 各自校验。

### 系统文件图标

`FileTree.vue` 接收可选 `systemIconProvider`。当 `enabled === true`、`features.system_icons === true` 且 `endpoints.icon` 存在时，文件列表优先渲染 `GET /api/file-icon?name=<name>&is_dir=0|1&size=16` 返回的系统图标。目录使用 `name=folder`，文件使用 `item.name`。图标加载失败时按“目录/扩展名/size”缓存失败结果，并回退到原有 Element Plus 图标；失败不会弹消息，也不会改变双击文件预览行为。

### 本地打开

工作台文件管理器和画板文件管理器在右键普通文件时，根据能力在“复制路径”和“提交队列”之前显示：

- `context.openLocal`：调用 `POST /api/chemssh-bridge/open`，请求体 `{ "path": "/workspace/project/input.inp" }`。
- `context.openNotepad`：调用 `POST /api/chemssh-bridge/open-text`，请求体相同。

目录不显示这两个动作。双击文件仍打开 ChemSSH 预览；本地打开只由右键菜单触发。打开请求成功后只提示已交给本地程序，失败时才显示错误。

Launcher 成功响应示例：

```json
{
  "ok": true,
  "remote_path": "/workspace/project/input.inp",
  "local_path": "C:\\Users\\...\\ChemSSH Launcher\\sftp-open\\abcd1234\\input.inp"
}
```

`local_path` 只用于即时提示或调试，不写入 client cache 或画板 payload。

### 同步事件

当 `features.open_sync_events === true` 且 `endpoints.sync_events` 存在时，前端由 `App.vue` 统一每 1 秒轮询：

```http
GET /api/chemssh-bridge/open-sync-events?after=<lastSeq>
```

响应：

```json
{
  "events": [
    {
      "seq": 3,
      "time": "2026-06-07T13:00:00Z",
      "remote_path": "/workspace/project/input.inp",
      "local_path": "C:\\Users\\...\\input.inp",
      "status": "done",
      "error": ""
    }
  ]
}
```

前端维护 `lastSeq`，页面隐藏时暂停轮询，恢复可见后继续请求。`App.vue` 负责轮询并通过 store 广播事件；`Workspace.vue` 与 `CanvasBoard.vue` 只消费这些广播结果并做局部刷新。`status="done"` 时取 `parentDirectory(remote_path)`：工作台只在该目录等于当前目录时刷新当前列表，画板复用 `refreshFileManagersForDirectories(paths)` 刷新所有打开了该目录的文件管理窗口。`status="error"` 只弹同步失败提示，不刷新列表，避免把错误同步状态误导为远程文件已更新。

## 结构预览

### `GET /api/structures/ase/preview?path=/workspace/project/mol.xyz&force=false`

读取结构摘要和初始帧。大文件会返回 `STRUCTURE_FILE_TOO_LARGE`，用户确认后前端可用 `force=true` 重试。`XDATCAR` 预览可先返回尾部快速解析出的最后一帧，此时 `scan_completed=false` 表示后端尚未建立完整随机访问索引；后续 `/frame`、`/frames.bin` 和 XDATCAR 的 `/frames.stream` 会要求完整 summary 并按需重扫建立索引。普通固定步长 XYZ 的 `/frames.stream` 可复用快速 summary，先按稳定帧跨度推送二进制块，避免最后一帧已显示但缓存进度延迟启动。

响应包含 `warnings: string[]`，用于提示可预览但可能不完整或不适合作为结构源的情况。当前已定义：

- `vasp_outcar_md_may_lack_structure`：检测到 `OUTCAR` 属于 VASP MD 任务（`IBRION = 0`）。VASP MD 轨迹通常应优先读取 `XDATCAR`；当前 `OUTCAR` 可能不包含结构轨迹信息，导致 ASE/快速解析器解析失败或只拿到有限结构块。结构预览器应在画布下方或解析失败提示附近显示红色警告，并建议用户改看 `XDATCAR`。
- `vasp_outcar_constraints_missing` / `vasp_outcar_constraints_unreadable`：`OUTCAR` 自身不包含固定原子约束。解析器会与 ASE 一致，按同目录 `CONTCAR`、`POSCAR` 的顺序读取固定原子信息，用于 `fixed_indices` 和排除固定原子后的 `fmax`；如果没有这些文件或读取失败，结构预览器应提示当前 `Fmax` 为全部原子的 `Fmax`。
- `XDATCAR` 自身同样不包含固定原子约束。后端快速解析器会按同目录 `CONTCAR`、`POSCAR` 的顺序读取 Selective Dynamics，并把固定原子写入每帧 `fixed_indices` 和二进制块 `fixed_mask`；缺失或不可读时沿用上述固定信息 warning。

### `GET /api/structures/ase/frame?path=/workspace/project/mol.xyz&index=0&force=false`

按索引读取单帧结构。

### `GET /api/structures/ase/frames.bin?path=/workspace/project/traj.xyz&start=0&count=32&force=false`

读取轨迹二进制帧块。

`preview.transport` 在多帧轨迹且启用 `prefer_binary` 时返回 `binary-available`；前端据此决定是否预加载二进制帧块。拓扑稳定的轨迹走固定步长 `CWB1` 子格式，拓扑不稳定（每帧原子数或元素顺序不同）的轨迹走 `variable-atoms-v1` 子格式。

二进制信封固定为：`b"CWB1"` + `<I` header 长度 + UTF-8 JSON header + 4 字节对齐 padding + 各 typed array。Header 的 `arrays` 字典记录每个数组的 `offset`、`byte_length` 和 `shape`。`dtype` 为 `float32-le`，`int_dtype` 为 `int32-le`。

固定拓扑（`topology_stable=true`，无 `frame_encoding`）header 关键字段：

```json
{
  "n_atoms": 128,
  "topology_stable": true,
  "symbols": ["H", "O"],
  "numbers": [1, 8],
  "pbc": [true, true, false],
  "arrays": {
    "positions": { "shape": [count, n_atoms, 3] },
    "cells": { "shape": [count, 3, 3] },
    "tags": { "shape": [count, n_atoms] },
    "fixed_mask": { "shape": [count, n_atoms] },
    "energy": { "shape": [count] },
    "fmax": { "shape": [count] }
  },
  "nan_means_null": ["energy", "fmax"]
}
```

可变拓扑（`topology_stable=false`）header 增加 `frame_encoding` 标记，并把逐帧原子数据按帧拼接：

```json
{
  "n_atoms": 0,
  "topology_stable": false,
  "frame_encoding": "variable-atoms-v1",
  "symbols": [],
  "numbers": [],
  "pbc": [false, false, false],
  "arrays": {
    "frame_atom_counts": { "shape": [count] },
    "positions": { "shape": [total_atoms, 3] },
    "numbers": { "shape": [total_atoms] },
    "tags": { "shape": [total_atoms] },
    "fixed_mask": { "shape": [total_atoms] },
    "cells": { "shape": [count, 3, 3] },
    "pbc": { "shape": [count, 3] },
    "energy": { "shape": [count] },
    "fmax": { "shape": [count] }
  },
  "nan_means_null": ["energy", "fmax"]
}
```

`frame_atom_counts` 为 `int32-le`，长度等于 `count`。第 `i` 帧在拼接数组中的原子偏移为前 `i` 个 `frame_atom_counts` 的前缀和；`positions`/`numbers`/`tags`/`fixed_mask` 按帧顺序拼接。可变拓扑块额外带逐帧 `pbc`（`uint8`，0/1），因为 `.db`/`.traj` 等格式每帧 pbc 可能不同。`symbols` 在块级别为空数组，前端通过 `numbers` 推导每帧元素符号。

前端封装：`frontend/src/api/structures.ts` 的 `readStructureFrameChunk()` 解析二进制信封；需要从任意二进制块取单帧时使用 `frameFromStructureChunk(chunk, localIndex)`，它同时兼容固定拓扑块和 `variable-atoms-v1` 可变拓扑块。只有调用方已确认块级 `frame_encoding === "variable-atoms-v1"` 时才直接使用 `frameFromVariableStructureChunk(chunk, localIndex)`。新增模块不要自行解析 `CWB1` 信封，统一使用这些封装。

支持格式由 `backend/app/services/file_types.py` 与 ASE 能力共同决定。当前常见格式包括 `xyz`、`extxyz`、`traj`、`pdb`、`mol`、`sdf`、`cif`、`xsd`、`xtd`、`arc`、`db`，并强制识别 `POSCAR`、`CONTCAR`、`XDATCAR`、`OUTCAR` 等 VASP 文件名。

XSD 周期结构会优先走后端快速解析器。解析器读取 `Atom3d XYZ` 作为分数坐标、`SpaceGroup` 的 `AVector`/`BVector`/`CVector` 作为晶胞，并把 `Atom3d RestrictedProperties="FractionalXYZ"` 或 `CompleteRestriction RestrictsObjects=... RestrictsProperties="FractionalXYZ"` 转换为 `frame.fixed_indices`。当前结构协议只表达整原子固定，不表达单轴固定。

前端封装：`frontend/src/api/structures.ts`。

结构预览器下载菜单支持：

- 原格式：通过 `/api/files/download` 下载当前文件。
- 当前帧 XYZ：从当前显示帧生成 XYZ/extxyz；有固定原子时写入 `fixed:I:1` 列。
- 当前帧 XSD：从当前显示帧生成 Materials Studio XSD，保留 `RestrictedBy`、`RestrictedProperties="FractionalXYZ"` 和 `CompleteRestriction` 固定信息。
- 整个轨迹 XYZ / Arc：仅轨迹文件显示，导出已加载或按需读取的全部帧。

`frontend/src/api/structures.ts` 也提供通用 `StructureSource` 版本的读取函数。ASE 是默认数据源：

```json
{
  "id": "ase",
  "parser": "ase",
  "apiBase": "/api/structures/ase"
}
```

插件如果提供兼容结构接口，可以注册自己的 `StructureSource`，并复用现有预览窗口和 `MoleculeViewer`。兼容接口包括：

- `GET {apiBase}/preview`
- `GET {apiBase}/frame`
- `GET {apiBase}/frames`
- `GET {apiBase}/frames.bin`
- `GET {apiBase}/frames.stream`

`frames.bin` 继续使用 `application/vnd.chemssh.structure+bin`，由现有 Brotli middleware 自动压缩。

### `GET /api/structures/ase/frames.stream?path=/workspace/project/traj.xyz&chunk=32&force=false`

SSE (Server-Sent Events) 长连接端点，后端持续推送二进制帧块，前端仅需一次连接即可接收所有数据，消除 N 次串行 HTTP 往返。

- **Content-Type**: `text/event-stream`
- **认证**: EventSource 不支持自定义 header，通过查询参数 `?token=xxx` 传递（`TokenAuthMiddleware` 已支持 `AUTH_QUERY_NAMES`）
- **查询参数**:
  - `path` (必填): 工作区相对路径
  - `format` (可选): 文件格式
  - `force` (可选, 默认 `false`): 跳过大文件保护
  - `chunk` (可选, 默认 `32`, 范围 1–512): 每个事件包含的帧数
- **响应头**:
  - `Cache-Control: no-cache`
  - `X-Accel-Buffering: no`（防止反向代理缓冲）

SSE 事件类型：

| 事件类型 | data 格式 | 说明 |
|---------|----------|------|
| `chunk` | base64 编码的 CWB1 二进制载荷 | 与 `/frames.bin` 返回的格式完全一致，仅多一层 base64 编码 |
| `done` | `{"n_frames": <int>}` | 所有帧块已发送完毕 |
| `error` | `{"code": "<str>", "message": "<str>"}` | 发生错误，可能的 code: `RESOLVE_FAILED`、`SCAN_FAILED`、`CHUNK_FAILED` |

Debug 诊断事件类型仅在后端以 `--debug` 启动，或配置 `server.debug=true` / 环境变量 `CHEMSSH_DEBUG=1` 时发送。正常模式不创建 profiling dict，也不发送这些事件，以避免正式轨迹加载的额外计时和 SSE 事件开销。

| 事件类型 | data 格式 | 说明 |
|---------|----------|------|
| `ready` | `{"t_resolve_ms": <number>, "t_scan_ms": <number>, "total_frames": <int>, "chunk_size": <int>, "server_emit_ms": <number>}` | 轨迹解析前置统计和总帧数 |
| `meta` | `{"start": <int>, "count": <int>, "t_pack_ms": <number>, "t_b64_ms": <number>, "server_emit_ms": <number>, "size_b64": <int>, "profile": <object>}` | 紧跟在每个 `chunk` 前，包含后端打包和 profiling 明细 |

后端在客户端断连时自动取消剩余块的计算（通过 `request.is_disconnected()` 轮询检测）。当客户端请求头包含 `Accept-Encoding: br` 时，BrotliMiddleware 会对 `text/event-stream` 使用 Brotli `quality=1` 做真正流式压缩：每个 SSE body chunk 经 `process()` 后立即 `flush()`，响应保留 SSE 协议但设置 `Content-Encoding: br`，不设置 `Content-Length`，避免为压缩而缓冲完整轨迹。

`meta.profile` 用于开发 profiling，字段可能按实现扩展；当前包含 `read_frame_range_ms`、`build_payload_total_ms`、`normalize_frame_data_ms`、`normalize_atoms_ms`、`topology_check_ms`、`array_fill_ms`、`add_array_tobytes_ms`、`header_json_ms`、`payload_join_ms`、`payload_bytes`、`safe_count`、`read_path`、`topology`。业务前端不应依赖这些字段做渲染逻辑，开发测试页可用它们定位后端瓶颈。

前端封装：`frontend/src/api/structures.ts` 的 `streamStructureFrames()` 创建 EventSource 并处理 SSE 事件，`onChunk` 回调直接调用 `parseStructureBinaryChunk()` 解码。`MoleculeViewer.vue` 的 `streamBinaryTrajectoryFrames()` 负责调度：对 `transport === 'binary-available'` 的轨迹优先走 SSE，每个 `chunk` 事件到达后立即写入 store/cache 并更新帧计数。

兼容接口扩展：插件如需 SSE 推送，可注册 `GET {apiBase}/frames.stream` 端点，遵循相同的 SSE 事件协议。

## 插件

插件目录默认扫描项目根目录下的 `plugins/`。扫描阶段只读取 `chemssh-plugin.json`，不会导入插件后端或加载插件 UI。用户从工作区右侧功能区的 `+` 菜单打开插件面板后，前端才调用激活接口。

### `GET /api/plugins`

返回已扫描到的插件清单和面板声明。

```json
{
  "plugins": [
    {
      "id": "cclib",
      "name": "cclib",
      "version": "0.1.0",
      "description": "Parse quantum chemistry output files with cclib and send structure data to the existing preview window.",
      "panels": [
        {
          "id": "cclib-provider",
          "title": "cclib",
          "kind": "tool",
          "singleton": true
        }
      ],
      "active": false
    }
  ]
}
```

前端封装：`frontend/src/api/plugins.ts`。

### `POST /api/plugins/{plugin_id}/activate`

激活插件。宿主会按需导入插件后端入口，挂载插件 API，并返回前端资源和 API 前缀。

```json
{
  "id": "cclib",
  "active": true,
  "asset_url": "/api/plugins/cclib/assets",
  "api_base": "/api/plugins/cclib/api",
  "panels": [],
  "file_manager": {}
}
```

### `POST /api/plugins/{plugin_id}/deactivate`

通知插件停用。当前实现会移除该插件挂载在 `/api/plugins/{plugin_id}/api/*` 下的后端路由，调用插件的 `on_deactivate()` 钩子，并让前端注销该插件注册的文件预览 provider。后续再次调用 `activate` 会复用已加载的插件实例并重新挂载插件路由。

### `GET /api/plugins/{plugin_id}/dependencies`

返回插件 Python 依赖状态。宿主会读取插件清单与插件状态文件，报告当前有效依赖模式、Python 解释器、requirements 路径、已安装包和缺失包。

```json
{
  "plugin_id": "cclib",
  "python": {
    "mode": "host",
    "manifest_mode": "host",
    "python": "D:/Git/chemssh/.venv/Scripts/python.exe",
    "requirements": "D:/Git/chemssh/plugins/cclib/backend/requirements.txt",
    "packages": [
      { "name": "cclib", "version": "1.8.1" }
    ],
    "missing": [],
    "satisfied": true
  }
}
```

### `POST /api/plugins/{plugin_id}/dependencies/install`

安装插件 Python 依赖。请求体：

```json
{
  "mode": "host"
}
```

或：

```json
{
  "mode": "venv",
  "venv": ".venv"
}
```

`host` 会修改运行 chemssh 的当前 Python 环境；`venv` 会创建或复用插件目录内的虚拟环境。安装结果会写入插件状态文件，不改写插件清单。

### `POST /api/plugins/{plugin_id}/dependencies/external`

保存并校验外部 Python 解释器路径：

```json
{
  "python": "D:/envs/cclib/python.exe"
}
```

该接口只验证解释器可运行并保存配置；需要 external 执行能力的插件应在自己的后端实现里读取该配置。

### `GET /api/plugins/{plugin_id}/assets/{path}`

读取插件已构建的前端静态资源。`GET /api/plugins/{plugin_id}/assets` 默认返回插件入口 `index.html`。
随插件发布且不需要构建的前端资源推荐放在 `frontend/bundle/`，并在清单中使用 `dependencies.frontend.mode="bundled"` 和 `bundle` 字段声明。需要构建的插件可把生成物输出到 `frontend/dist/`，并声明 `mode="build"`、`build` 命令和 `dist` 路径；`dist` 应视为可再生成产物。

### `/api/plugins/{plugin_id}/api/*`

插件自己的后端 API。示例 `cclib` 插件提供：

- `POST /api/plugins/cclib/api/probe`
- `GET /api/plugins/cclib/api/structures/preview`
- `GET /api/plugins/cclib/api/structures/frame`
- `GET /api/plugins/cclib/api/structures/frames`
- `GET /api/plugins/cclib/api/structures/frames.bin`

## 队列状态

### `GET /api/queue/list?current_user_only=false`

返回调度系统队列。Slurm 优先使用 `squeue --json`；不可用时回退到可解析文本。PBS 使用对应 PBS 命令。

### `GET /api/queue/job/{job_id}`

返回作业详情，例如 Slurm 的 `scontrol show job {job_id}` 解析结果。

### `POST /api/queue/action`

对作业执行调度器动作。

```json
{
  "job_id": "123456",
  "action": "cancel"
}
```

常见动作包括 `cancel`、`hold`、`release`。具体支持取决于后端 provider。

前端封装：`frontend/src/api/queue.ts`。

## 作业提交

### `POST /api/jobs/submit`

在指定工作目录执行 `sbatch <script>` 或 `qsub <script>`。

```json
{
  "workdir": "/workspace/project/co2rr_opt",
  "script": "run.sh",
  "scheduler": "slurm",
  "command": "sbatch"
}
```

安全约束：

- `workdir` 必须在 `workspace.root` 内。
- `script` 必须是文件名，不能是路径。
- `script` 只允许字母、数字、点、下划线和短横线。
- 后端不会执行任意 shell 字符串。

前端封装：`frontend/src/api/jobs.ts`。

## 终端

终端后端接口见 `frontend/src/api/terminal.ts` 和 `backend/app/api/terminal.py`。前端组件为 `frontend/src/components/terminal/TerminalPanel.vue`。

终端会话按浏览器 client id 轻量隔离。前端通过 `frontend/src/api/clientSession.ts` 在 `localStorage` 中保存 `chemssh.clientId.v1`，并在终端 HTTP 请求中发送：

```http
X-ChemSSH-Client-Id: client_xxx
```

WebSocket 连接使用 query 参数。启用 token 鉴权时还必须带 `token` 或先有有效 `chemssh_token` cookie：

```text
/api/terminal/ws/{session_id}?client_id=client_xxx&token=...
```

后端只会列出、关闭、连接当前 client id 拥有的终端会话。`terminal.max_sessions` 是每个 client id 的上限，不是全局上限。client id 只用于会话隔离，不代表用户身份或安全认证。

### `POST /api/terminal/sessions`

创建终端会话。请求必须带 `X-ChemSSH-Client-Id`，用于把会话归属到当前浏览器客户端。

```json
{
  "cwd": "/workspace/project",
  "shell": null,
  "rows": 30,
  "cols": 120,
  "vim_compatibility": true
}
```

字段说明：

- `cwd`：可选，终端启动目录；省略时使用工作区根目录。后端会通过 `WorkspaceSecurity` 校验路径必须留在工作区内。
- `shell`：可选，指定 shell；省略时使用后端默认 shell。
- `rows` / `cols`：可选，初始终端尺寸；`rows` 范围为 `2..200`，`cols` 范围为 `20..500`。
- `vim_compatibility`：可选，默认 `true`。只影响新建会话是否创建 `vi` / `vim` 兼容 shim，不影响已有终端。

响应：

```json
{
  "session_id": "term_xxx",
  "cwd": "/workspace/project",
  "shell": "/bin/bash",
  "created_at": "2026-06-05T12:00:00Z",
  "last_active_at": "2026-06-05T12:00:00Z",
  "alive": true,
  "clients": 1
}
```

前端封装：`createTerminalSession(payload)`，类型定义在 `frontend/src/api/terminal.ts`。

### `GET /api/terminal/sessions`

列出当前 `X-ChemSSH-Client-Id` 拥有的终端会话。响应形如：

```json
{
  "items": [
    {
      "session_id": "term_xxx",
      "cwd": "/workspace/project",
      "shell": "/bin/bash",
      "created_at": "2026-06-05T12:00:00Z",
      "last_active_at": "2026-06-05T12:05:00Z",
      "alive": true,
      "clients": 1
    }
  ]
}
```

### `DELETE /api/terminal/sessions/{session_id}`

关闭当前 `X-ChemSSH-Client-Id` 拥有的指定终端会话。

```json
{
  "success": true
}
```

常用交互：

- 创建终端会带上当前文件管理器目录作为 `cwd`。
- 创建终端可传 `vim_compatibility` 布尔值，默认 `true`。前端终端设置中的“Vim 兼容模式”会保存到 `localStorage` 并随新建会话发送；关闭后只影响之后新建的终端会话。
- 前端终端设置提供“自动复制选中文字”开关，默认关闭，保存到 `localStorage`。开启后，xterm 选区变化会把非空选中文本写入系统剪贴板；终端同时加载官方 `@xterm/addon-clipboard` 以支持 OSC 52 剪贴板访问。
- 前端终端设置提供“同步时命令后刷新文件”开关，默认开启，保存到 client preferences 和 `localStorage` 兜底。开启后，处于 `follow` 或 `bidirectional` 同步模式的终端标签页收到命令完成事件时，会让关联文件管理器用 `listFiles(path, { refresh: true })` 绕过缓存刷新；工作台刷新左侧文件管理器，画板刷新绑定的 `file-manager` 窗口并递增该窗口的 `refreshToken`。
- 终端工具按钮位于“新建终端”和“终端设置”之间。当前工具菜单只包含“搜索”；搜索浮窗输入内容后会扫描当前 xterm buffer，并在可见行上叠加高亮元素标记全部匹配项和当前匹配。点击“查找下一个”或“上一个”会切换当前匹配并滚动到可见区域；终端输出、滚动或点击后会重新渲染可见区域高亮。
- 文件管理器与终端支持目录同步：`follow` 表示终端跟随文件管理器，`bidirectional` 表示终端 cwd 变化也会反向打开文件管理器目录。
- 终端接收文件拖放时，会向当前活跃 tab 写入输入数据。当前约定是路径串前置一个空格，多个绝对路径用空格连接，例如 ` /abs/a /abs/b`。
- 终端支持“中键粘贴当前终端选区文本”。该行为依赖宿主环境放行中键事件；常规浏览器通常会拦截为自动滚屏，自定义 WebView2 启动器可通过关闭默认中键滚轮后启用。
- 终端启动时会清理 ChemSSH 服务进程继承来的 Python/Conda 激活状态：移除 `VIRTUAL_ENV`、`CONDA_*`、`PYTHONHOME` 以及这些环境对应的 `PATH` 片段，避免服务运行在 `.venv` 时污染 Web Terminal。清理发生在 shell 启动前；用户自己的 `/etc/profile`、`~/.bash_profile`、`~/.profile`，以及它们加载的 `.bashrc` 后续主动激活环境时不受影响，行为应与普通 SSH 登录保持一致。

如果新增终端相关模块，优先通过已有 websocket 消息发送：

```json
{
  "type": "input",
  "data": " ls\n"
}
```

后端会吞掉 shell prompt 中的 ChemSSH OSC cwd 标记，并在用户通过终端输入回车后、下一次 prompt 到达时发送命令完成消息。初始 prompt 不会产生该消息。前端用它驱动同步文件管理器刷新，不应从终端显示文本中猜测命令结束：

```json
{
  "type": "command_done",
  "seq": 1,
  "path": "/workspace/project"
}
```

### 终端 `rz` / `sz` 接管

终端会话启动时，后端会创建临时 `rz`、`sz` shim，并把 shim 目录放到该终端进程的 `PATH` 最前面。脚本或用户命令通过普通 `PATH` 查找调用 `rz` / `sz` 时，不会进入原生 ZMODEM 传输；shim 会向 pty 输出 ChemSSH 私有 OSC 标记，后端读取终端输出时吞掉该标记并转成 WebSocket 传输请求。shim 会阻塞等待前端回传 `transfer_result`，收到成功后以 `0` 退出，收到失败或取消后以非零码退出；这让脚本中位于 `sz` 后面的 `rm` 等清理命令在浏览器下载请求完成后才继续执行。

后端发送上传请求：

```json
{
  "type": "transfer_request",
  "transfer_id": "transfer_xxx",
  "direction": "upload",
  "cwd": "/workspace/project",
  "argv": ["rz", "-y"]
}
```

前端收到 `direction="upload"` 后打开浏览器文件选择器，并走与文件管理器相同的上传准备流程上传到该终端当前目录：空白字符会先改为 `_`，路径段预检失败的文件不会开始传输。

后端发送下载请求：

```json
{
  "type": "transfer_request",
  "transfer_id": "transfer_xxx",
  "direction": "download",
  "cwd": "/workspace/project",
  "argv": ["sz", "result.out"],
  "paths": ["/workspace/project/result.out"]
}
```

前端收到 `direction="download"` 后，单文件使用 `downloadUrl(path)`，多文件或目录使用 `downloadSelectionUrl(paths, { forceArchive: true })`。

前端完成、失败或取消后回传结果：

```json
{
  "type": "transfer_result",
  "transfer_id": "transfer_xxx",
  "success": true,
  "message": "Uploaded 1 file(s)"
}
```

后端收到 `transfer_result` 后，会写入 shim 专属临时 ack 文件释放正在等待的 `rz` / `sz` 进程。ack 路径只允许位于该终端会话创建的临时 shim 目录内，不能由前端任意指定。

安全规则：

- shim 只影响当前终端会话子进程的 `PATH`，不会修改系统环境。
- `rz` 上传目标目录来自终端 shim 上报的 cwd，后端会按 workspace 安全规则解析。
- `sz` 参数由后端解析为 workspace 内路径；越界、缺失或不存在的路径会以 `transfer_request.error` 返回，前端显示失败并回传 `transfer_result`。
- 如果脚本显式调用 `/usr/bin/rz`、`/usr/bin/sz` 等绝对路径，会绕过 shim。后端会在 pty 输出流中识别常见原生 ZMODEM 起始特征并发送 `Ctrl+C` 中断，避免终端继续卡死。原生 `rz` 可继续接管为浏览器上传；原生 `sz` 在协议流中通常无法可靠恢复原始文件参数，因此会提示改用 PATH 解析到的 `sz` shim，除非后续实现完整 ZMODEM 接收器。

### 终端 `vi` / `vim` 兼容 shim

终端会话支持“Vim 兼容模式”，默认开启。开启后，临时 shim 目录还会放置 `vi` 和 `vim` 包装脚本。Linux Vim 在 xterm 兼容终端中会发起 `t_RV`、`t_u7`、`t_RF`、`t_RB`、`t_RK` 等终端探测；当前 WebTerminal 链路对这些探测的完整响应仍不够稳定，会导致 Vim 启动后等待终端响应，看起来像卡死。包装脚本只在真实命令是 Vim 时注入：

```bash
--cmd 'set t_RV= t_u7= t_RF= t_RB= t_RK='
```

前端设置面板可以手动关闭“Vim 兼容模式”，关闭后创建会话请求会发送 `vim_compatibility: false`，后端仍创建 `rz` / `sz` shim，但不会创建 `vi` / `vim` 包装脚本。脚本会先从移除 shim 目录后的 `PATH` 中找到真实 `vi` / `vim`。如果真实命令不是 Vim，则原样执行；如果用户显式调用 `/usr/bin/vim` 等绝对路径，也会绕过 shim。该兼容层只影响 ChemSSH 创建的终端会话，不修改用户的 `~/.vimrc` 或系统配置。后续如果前端完整实现 Vim 所需的 xterm 查询响应，可移除此 shim。

## 客户端缓存

客户端缓存用于保存同一个浏览器 `client_id` 的 UI 状态，例如画板布局、窗口大小、tail 行数等。它不是认证或权限系统，不应保存密码、token 等敏感信息。

前端封装：

```text
frontend/src/api/clientCache.ts
```

后端实现：

```text
backend/app/api/client_cache.py
backend/app/services/client_cache_service.py
```

默认保存位置：

```text
cache/<client_id>/
```

目录内文件：

- `meta.json`：`created_at`、`last_seen_at`、`last_saved_at`。
- `preferences.json`：用户偏好，例如 tail 行数。
- `boards.json`：画板、viewport、窗口布局和窗口 payload。

后端启动时会清理超过 `client_cache.cleanup_offline_days` 没有上线的 client cache，默认 14 天。

### `GET /api/client-cache`

读取当前客户端缓存。请求必须带：

```http
X-ChemSSH-Client-Id: client_xxx
```

响应：

```json
{
  "enabled": true,
  "client_id": "client_xxx",
  "preferences": {
    "version": 1,
    "logs": {
      "tailLines": 20
    },
    "theme": {
      "animatedBackdrop": false,
      "glassBlur": true
    }
  },
  "boards": {
    "version": 1,
    "activeBoardId": "board_xxx",
    "boards": []
  },
  "updated_at": "2026-06-05T12:00:00Z"
}
```

### `PUT /api/client-cache/preferences`

保存用户偏好。

```json
{
  "version": 1,
  "logs": {
    "tailLines": 80
  },
  "workspace": {
    "fileTreeWidth": 360,
    "sidePaneWidth": 420,
    "queueHeight": 260,
    "currentPath": "/workspace/project",
    "showHiddenFiles": false,
    "activeWorkPanelId": "builtin:preview"
  },
  "canvas": {
    "lastBoardId": "board_xxx"
  },
  "theme": {
    "animatedBackdrop": false,
    "glassBlur": true
  }
}
```

前端通过 `frontend/src/api/clientPreferences.ts` 合并保存偏好，避免画板、工作台和日志窗口分别保存时互相覆盖。当前接入项包括：

- `workspace.fileTreeWidth`：工作台左侧文件管理器宽度。
- `workspace.sidePaneWidth`：工作台右侧窗口宽度。
- `workspace.queueHeight`：工作台右侧队列/日志高度。
- `workspace.currentPath`：工作台当前目录。
- `workspace.showHiddenFiles`：是否显示隐藏文件。
- `workspace.activeWorkPanelId`：工作台右侧活跃面板。
- `terminal.refreshFileManagerAfterCommand`：终端处于目录同步模式时，命令完成后是否刷新关联文件管理器，默认开启。
- `logs.tailLines`：tail 行数。
- `theme.animatedBackdrop`：是否启用全屏动态背景。
- `theme.glassBlur`：是否启用毛玻璃背景模糊。

### `PUT /api/client-cache/boards`

保存画板布局。

```json
{
  "version": 1,
  "activeBoardId": "board_xxx",
  "boards": [
    {
      "id": "board_xxx",
      "name": "Board 1",
      "createdAt": "2026-06-05T12:00:00Z",
      "updatedAt": "2026-06-05T12:05:00Z",
      "viewport": {
        "x": 120,
        "y": 80,
        "zoom": 1
      },
      "windows": [
        {
          "id": "window_xxx",
          "type": "tail",
          "title": "slurm.out",
          "x": 80,
          "y": 80,
          "width": 520,
          "height": 340,
          "zIndex": 2,
          "payload": {
            "path": "/workspace/project/slurm.out",
            "lines": 80,
            "boundFileManagerId": "window_files"
          }
        },
        {
          "id": "window_files",
          "type": "file-manager",
          "title": "project",
          "x": 40,
          "y": 60,
          "width": 620,
          "height": 430,
          "zIndex": 1,
          "payload": {
            "path": "/workspace/project",
            "bindingNumber": 1,
            "bindingColor": "#176b87"
          }
        }
      ]
    }
  ]
}
```

画板窗口交互约定：

- 新建窗口菜单顺序为：文件、终端、预览、队列、Tail、插件。空画板的默认新建入口创建文件管理窗口。
- 文件管理窗口会把当前目录保存到 `payload.path`，标题只显示当前目录名；新建时写入 `payload.bindingNumber` 和 `payload.bindingColor`，用于窗口类型标签、绑定徽标和关系线的一致颜色/编号。绑定到该文件管理器的 Tail 窗口在单击选中任意文件时会自动更新 `payload.path`，与工作台 Tail 的选中文件行为保持一致。画板文件管理窗口完成内部移动后会向 `CanvasBoard.vue` 上报受影响目录，画布层按各文件管理器当前目录匹配并递增 `refreshToken`，因此源目录窗口、目标目录窗口，以及其它打开相同目录的窗口都会自动刷新。
- 文件管理器列表顶部在存在上级目录时固定显示 `..` 文件夹行。该行不参与选择、重命名或删除；双击进入上一级，也可作为内部文件拖拽的移动目标。工具栏不再提供“上一级”按钮；原位置改为类似 XFTP 的“后退”按钮和历史下拉，每个文件管理器本地记录最近 20 个用户主动访问过的目录，历史下拉显示完整绝对路径。刷新、上传完成、移动完成等重载当前目录的操作不会写入历史。
- Tail 窗口通过 `payload.boundFileManagerId` 记录绑定的文件管理窗口。画板 Tail 不提供手动路径输入，绑定入口位于 Tail 标题栏右侧的连接图标；日志内容仍由 `LogViewer.vue` 调用 `GET /api/files/tail`。
- Terminal 窗口通过 `payload.tabBindings` 保存当前终端标签页绑定摘要，形如：

```json
[
  {
    "tabId": "terminal_tab_1",
    "title": "project",
    "cwd": "/workspace/project",
    "syncMode": "follow",
    "boundFileManagerId": "window_files",
    "active": true
  }
]
```

终端标签页绑定到文件管理窗口后，默认进入跟随目录模式；切到双向同步时，终端 `cwd` 变化会回写对应的文件管理窗口，而不是全局工作台文件管理器。Terminal 设置中的“同步时命令后刷新文件”开启时，`follow` 标签页命令完成后刷新当前绑定文件管理器目录，`bidirectional` 标签页命令完成后按终端最新 cwd 更新并刷新绑定文件管理器；画板通过递增对应 `file-manager` 的 `refreshToken` 触发刷新。画板会用窗口标题徽标和关系线展示文件管理器、Tail、Terminal 标签页之间的绑定关系。Terminal 的大窗口按钮会把整个 TerminalPanel teleport 到 `body` 后全视口覆盖，避免被画板 transform 限制；多标签页切换、拖拽排序和标签级绑定保持不变。

### `POST /api/client-cache/heartbeat`

刷新当前 `client_id` 的 `last_seen_at`。前端启动时调用一次，之后每 5 到 10 分钟调用一次。

### `DELETE /api/client-cache`

清理当前请求头 `X-ChemSSH-Client-Id` 对应的缓存目录。该接口不接受前端传入路径，只删除服务端解析出的 `cache/<client_id>/`。

设置页“清理当前缓存”会调用该接口，同时清理前端本地兜底缓存，然后刷新页面，让工作台布局、tail 行数、画板列表和窗口布局回到默认值。

响应：

```json
{
  "success": true,
  "client_id": "client_xxx",
  "removed": true
}
```

## 前端窗口交互协议

### 无边画板

顶部“画板”视图由 `frontend/src/views/CanvasBoard.vue` 实现，窗口外壳由 `frontend/src/components/canvas/CanvasWindow.vue` 统一管理。画板状态保存在 client cache 的 `boards.json` 中。

第一版画板窗口类型：

| 类型 | 当前行为 |
| --- | --- |
| `file-manager` | 渲染画板文件管理器窗口，可浏览目录并把文件拖给其它窗口 |
| `queue` | 渲染 `QueueStatus.vue`，队列仍可打开作业工作目录 |
| `tail` | 渲染 `LogViewer.vue`，保存路径和 tail 行数 |
| `terminal` | 渲染 `TerminalPanel.vue`，窗口 resize 后触发 terminal fit |
| `preview` | 渲染画板预览窗口，复用 `FilePreview.vue`、文本读取/保存和 ASE 结构预览接口 |
| `plugin` | 渲染插件 iframe 窗口，可选择插件 panel，激活后发送 `chemssh:plugin:init` |

窗口布局使用画布坐标，而不是屏幕像素。保存字段包括：

- `x`、`y`：画布坐标。
- `width`、`height`：窗口尺寸。
- `zIndex`：窗口层级。
- `payload`：窗口类型自己的轻量状态，例如 tail 的 `{ path, lines }`。

`file-manager` 窗口的 `payload.path` 保存当前目录。画板文件管理器复用工作台的完整工具栏能力：刷新、上级目录、新建文件、新建文件夹、显示隐藏文件、上传文件/文件夹、下载、重命名和删除。外部文件拖拽上传只在具体文件管理器窗口内响应，目标目录就是该窗口当前目录，便于多个文件管理器并存时选择上传位置。双击目录进入目录；双击文件统一打开 `preview` 窗口。文件树加载中使用不拦截鼠标的轻量指示；目录刷新期间的快速双击会在加载完成后按最后点击位置打开当前条目。Tail 窗口不由双击文件触发，而是和工作台一样由当前文件选择驱动：绑定文件管理器后，单击/选中文件会更新对应 Tail 路径。

`preview` 窗口的 `payload.path` 保存当前文件。从文件管理器打开或从内部文件拖拽打开时还会保存 `payload.previewType` 与 `payload.format`，预览窗口优先使用后端文件类型判定结果，再按文件名和扩展名兜底判断结构文件；大文件沿用现有确认流程。预览大窗口与 Terminal 大窗口一致，使用 `Teleport to="body"` 加 `position: fixed; inset: 0` 覆盖整个页面，不使用 Element Plus fullscreen dialog。

`plugin` 窗口保存 `pluginId`、`panelId`、`assetUrl`、`apiBase` 和标题。iframe 加载后接收：

```json
{
  "type": "chemssh:plugin:init",
  "version": 1,
  "pluginId": "plugin_id",
  "panelId": "panel_id",
  "instanceId": "window_xxx",
  "locale": "zh",
  "theme": "light",
  "apiBase": "/api/plugins/plugin_id/api",
  "assetBase": "/api/plugins/plugin_id/assets",
  "authToken": "当前会话 token，未配置时为 null",
  "initialFile": null
}
```

插件 iframe 如果自己调用 `apiBase` 下的接口，应在启用 token 鉴权时发送 `Authorization: Bearer <authToken>`。宿主也会在带 token 加载插件 asset 时写入 `chemssh_token` HttpOnly cookie，便于同源 iframe 的后续请求通过鉴权；插件仍应优先显式使用 `authToken`，避免依赖第三方 cookie 策略。

画板 UI 要保持工具化和低干扰：浅色点阵背景、贴边工具栏、图标按钮加 tooltip、窗口薄边框和不超过 8px 的圆角。多窗口、窄屏和不同缩放比例下不得出现文字溢出或控件重叠。

### 文件拖拽 payload

文件管理器长按文件行后进入“文件拖拽”模式；普通按住拖动仍用于多选。拖拽实现集中在：

- 写入端：`frontend/src/components/FileTree.vue`
- 协议工具：`frontend/src/api/fileDrag.ts`
- 终端接收端：`frontend/src/components/terminal/TerminalPanel.vue`
- 预览接收端：`frontend/src/views/Workspace.vue`

拖拽时会写入以下 `DataTransfer` 类型：

| 类型 | 内容 | 用途 |
| --- | --- | --- |
| `application/x-chemssh-files` | JSON payload | chemssh 内部窗口优先读取 |
| `application/x-chemssh-file-paths` | JSON string array | 轻量路径列表备用 |
| `text/plain` | ` /abs/a /abs/b` | 拖到终端或其他文本输入 |
| `text/uri-list` | 下载 URL | 拖到浏览器外部触发下载 |
| `DownloadURL` | Chrome 下载拖拽格式 | 外部下载兼容增强 |

写入端设置 `effectAllowed="copyMove"`：拖到终端、预览或浏览器外部仍按原来的 copy/download 行为处理；拖回文件管理器目录行时，只有合法目标文件夹会 `preventDefault()` 并设置 `dropEffect="move"`，其它行和空白区域保持浏览器默认禁止图标。

JSON payload：

```json
{
  "source": "chemssh:file-manager",
  "version": 1,
  "paths": ["/workspace/project/a.xyz"],
  "items": [
    {
      "name": "a.xyz",
      "path": "/workspace/project/a.xyz",
      "type": "file",
      "size": 128,
      "mtime": "2026-05-25T10:00:00",
      "extension": ".xyz",
      "preview_type": "structure",
      "format": "xyz"
    }
  ]
}
```

新增窗口如果要接收文件拖拽，推荐这样写：

```ts
import { hasChemSSHFileDrag, readChemSSHFileDrag } from '../api/fileDrag'

function handleDragOver(event: DragEvent) {
  if (!hasChemSSHFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function handleDrop(event: DragEvent) {
  const payload = readChemSSHFileDrag(event.dataTransfer)
  if (!payload) return
  event.preventDefault()
  // payload.paths 是绝对路径列表；payload.items 带文件类型与预览类型。
}
```

### 推荐的窗口行为

- 文件管理器 -> 浏览器外部：打开 `text/uri-list` 中的下载 URL。单文件直接下载，多文件或目录下载 zip。
- 文件管理器 -> 浏览器外部拖拽目录时，即使只拖了一个目录，也使用 `download-selection` 返回 zip。
- 文件管理器 -> 当前目录另一个文件夹：长按文件行进入内部文件拖拽后，可拖动当前选择的多个文件/文件夹到目录行。合法目标目录会高亮并以 move 光标提示；松开后先读取目标目录，若有同名项则使用和上传一致的冲突弹窗选择覆盖、跳过、添加 `.new` 后缀或取消，然后调用 `movePaths(paths, targetDirectory, entries)`，也就是 `POST /api/files/move`。文件行、空白区域、选中的目标目录、自身/子目录等非法位置不接收目录行移动 drop，保留默认禁止图标。画板文件管理窗口在内部拖拽期间还会显示当前目录悬浮投放区：一个用于移动到该窗口当前目录，另一个用于复制到该窗口当前目录。复制区使用 `copyPaths(paths, targetDirectory, entries)`，也就是 `POST /api/files/copy`；同名冲突仍使用覆盖、跳过、添加 `.new` 后缀或取消。拖到画板文件管理窗口的非目录行或空白区域时，会按移动到该窗口当前目录处理，类似 SFTP pane 级 drop 行为；目录行仍作为更精确的移动目标优先处理，复制只通过复制悬浮区触发。外部文件拖入画板文件管理窗口时显示留有少量边距的窗口级圆角“松开以上传文件”遮罩，目标目录就是该窗口当前目录，不支持直接拖拽上传到列表中的子文件夹。
- 文件管理器 -> 终端：向当前 tab 输入 ` ${paths.join(' ')}`，不自动回车。
- 文件管理器 -> 预览：只打开第一个路径，并切换到预览面板。
- 预览面板统一使用结构/文本切换窗口；结构与文本子视图保活，文件管理器切换目录不会清空当前预览目标，打开普通文件时进入文本视图，只有当前目标可作为结构预览时才显示结构切换入口。结构加载和重绘期间会在旧结构上显示非阻塞半透明遮罩；继续打开下一个结构会取消上一条结构 preview 请求，并且旧响应不能覆盖新状态。此竞态保护同时适用于工作台预览面板和画板预览窗口：两者都使用 `previewRequestSerial` 递增请求代号和 `AbortController` 取消未完成结构请求，旧响应返回后会检查当前 request id 是否仍是最新的，不匹配则直接丢弃。预览器工具栏提供“大窗口打开”按钮，使用与 Terminal 一致的 `Teleport to="body"` 固定全页层复用当前结构或文本预览器，适合临时放大查看而不改变当前文件选择。
- 文件管理器 -> 插件结构 provider：如果插件 UI 已加载并注册 active preview provider，文件管理器可先调用插件 `probe`，匹配成功后把插件 `StructureSource` 和文件路径发送到现有预览窗口。画板预览窗口同样支持插件 preview provider：`CanvasPluginWindow.vue` 接管插件注册/注销消息并上报 `CanvasBoard.vue`，画板通过共享的 `previewProviders` 状态把 provider 传递给 `CanvasFileManagerWindow.vue` 和 `CanvasPreviewWindow.vue`。预览窗口打开文件时会先尝试 resolve 匹配的 provider，命中后使用 provider 的 `StructureSource` 替代默认 ASE 数据源。
- 文件管理器图标：已加载插件注册 active preview provider 后，文件列表会用 `accepts.extensions`、`accepts.filenames`、`accepts.preview_types` 做轻量匹配；匹配到的文件显示与结构文件一致的小眼睛图标。列表渲染阶段不调用 `probe`，真实可预览性仍在双击打开时确认。
- 文件管理器 -> 新模块：默认读取 `application/x-chemssh-files`。如果模块只需要路径，使用 `payload.paths`；如果需要判断结构/文本/目录，使用 `payload.items[*].preview_type` 和 `type`。
- 文件管理器右键菜单：第一项复制当前选择的第一个绝对路径到剪贴板；右键未选中项时先选中该项再复制。
- 外部文件 -> 工作区：根 `Workspace.vue` 只在 `DataTransfer.types` 包含 `Files` 时触发上传，避免和内部文件拖拽冲突。

### 给新增模块的实现提示

- 不要解析 DOM 文本来拿路径；始终读取拖拽 payload 或调用文件 API。
- 组件需要“打开文件”时，优先接受绝对路径字符串，再由父级决定调用 `readFile`、`readAsePreview` 或目录打开。
- 插件预览 provider 的类型与轻量匹配工具在 `frontend/src/api/filePreviewProviders.ts`；文件列表组件通过 `previewProviders` 属性接收当前 active providers。
- 需要下载时优先使用 `downloadUrl(path)` 或 `downloadSelectionUrl(paths)`，不要硬编码接口地址。
- 需要预览结构时，先看 `preview_type === 'structure'`，同时兼容 VASP 强制文件名。
- VASP 强制结构文件名由后端 `file_types.py` 统一判断：`POSCAR`、`CONTCAR`、`XDATCAR`、`OUTCAR` 作为文件名片段出现时可识别为结构，并允许数字后缀或备份后缀，例如 `XDATCAR2`、`case_CONTCAR_001`、`case_xdatcar_backup`、`POSCAR.bak`。只要文件名匹配该规则，即使带 `.txt` 等文本扩展也按结构候选处理，例如 `notes_about_POSCAR.txt`。
- VASP XML 结构预览仅对 `vasp.xml` / `vasprun.xml` 返回 `format: "vasp-xml"`；普通 `.xml` 文件按文本预览处理。
- 大文件预览必须走确认流程，确认后才使用 `force=true`。
- 所有路径展示可以是绝对路径；所有后端请求仍会做工作区越界校验。
