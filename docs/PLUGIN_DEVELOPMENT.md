# chemssh 插件开发规范

本文档描述 chemssh 插件系统的目标设计与开发约定。它用于指导后续实现插件宿主、标签页式工作区，以及第三方插件开发。

插件系统的核心目标是：

- 预览器、Slurm 队列、cclib 解析插件等功能以标签页形式占据工作区的一部分区域，而不是直接占满整个窗口。
- 标签栏最右侧提供 `+` 新建按钮，用于打开内置面板或已安装插件面板。
- 插件按需加载：扫描插件时只读取清单文件，只有用户从 `+` 菜单打开插件 UI 后，才激活插件前端、后端路由、后台轮询或其他运行逻辑。
- 插件可以通过复制目录到插件文件夹的方式安装，并出现在 `+` 菜单中。
- 插件可以开源，也可以闭源发布。闭源插件必须提供可读取的清单文件和可加载的前端/后端入口。
- 插件依赖不强制写入 chemssh 主项目依赖。插件可以选择安装到 chemssh 当前 Python 环境，也可以创建或指定自己的独立环境。

## 术语

- 插件宿主：chemssh 主程序中负责扫描、安装、激活、卸载插件的后端与前端基础设施。
- 插件目录：一个独立插件的根目录，包含 `chemssh-plugin.json`。
- 插件面板：插件在工作区标签页中打开的 UI。
- 面板实例：用户每打开一个插件标签页，就创建一个面板实例。单例插件可以限制同一时间只有一个实例。
- 激活：插件首次被打开时，宿主加载插件运行时代码、挂载后端路由、提供静态资源或启动必要后台逻辑。
- 停用：最后一个面板实例关闭后，宿主可停止轮询、后台任务和插件占用的外部进程。

## 插件目录

推荐目录结构：

```text
plugins/
  cclib/
    chemssh-plugin.json
    README.md
    backend/
      plugin.py
      requirements.txt
    frontend/
      bundle/
        index.html
        assets/
      src/
      package.json
```

说明：

- `chemssh-plugin.json` 是唯一必须存在的文件。
- `README.md` 推荐提供，用于说明用途、依赖、授权和安装方式。
- `backend/` 可选。需要后端接口、文件解析、外部命令或后台任务时提供。
- `frontend/bundle/` 可选。存放随插件发布、无需宿主构建的前端静态资源。
- `frontend/dist/` 保留给需要构建的插件作为本地或 CI 生成目录，默认不纳入版本控制。
- 源码、构建脚本、测试文件均为可选内容，不要求随闭源插件发布。

## 插件扫描路径

默认扫描路径建议为：

- 项目级：`plugins/`
- 用户级：由配置文件指定，例如 `config.yaml` 中的 `plugins.directories`

宿主扫描插件时必须遵守以下规则：

- 只读取 `chemssh-plugin.json`，不导入 Python 模块，不执行 JavaScript，不运行安装脚本。
- 插件 `id` 必须唯一。多个扫描路径出现同名插件时，应按配置优先级选择一个，并给出可诊断提示。
- 清单解析失败、版本不兼容或路径越界时，插件不进入 `+` 菜单。
- 默认只显示 `enabled=true` 或未显式禁用的插件。

## 清单文件

插件根目录必须包含 `chemssh-plugin.json`。示例：

```json
{
  "schema_version": 1,
  "id": "cclib",
  "name": "cclib",
  "version": "0.1.0",
  "description": "Read computational chemistry output files through cclib and send compatible structure data to the existing preview window.",
  "author": "chemssh contributors",
  "license": "GPL-3.0-or-later",
  "enabled": true,
  "entry": {
    "frontend": {
      "type": "iframe",
      "path": "frontend/bundle/index.html"
    },
    "backend": {
      "type": "python",
      "module": "backend.plugin",
      "factory": "create_plugin"
    }
  },
  "panels": [
    {
      "id": "cclib-provider",
      "title": "cclib",
      "icon": "LineChart",
      "kind": "tool",
      "singleton": false,
      "accepts": {
        "extensions": [".out", ".log", ".dat", ".txt"],
        "filenames": [],
        "preview_types": ["text", "file"]
      }
    }
  ],
  "file_manager": {
    "preview_providers": [
      {
        "id": "cclib-output",
        "title": "Open with cclib",
        "panel_id": "cclib-provider",
        "activate": "on_panel_open",
        "priority": 60,
        "accepts": {
          "extensions": [".out", ".log", ".dat", ".txt"],
          "preview_types": ["text", "file"]
        },
        "probe": {
          "method": "POST",
          "path": "/probe"
        },
        "open": {
          "mode": "preview-window",
          "structure_api": "/structures"
        }
      }
    ]
  },
  "dependencies": {
    "python": {
      "mode": "host",
      "requirements": "backend/requirements.txt"
    },
    "frontend": {
      "mode": "bundled",
      "bundle": "frontend/bundle"
    }
  }
}
```

### 必填字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | number | 插件清单规范版本。当前为 `1`。 |
| `id` | string | 插件唯一 ID。建议使用小写字母、数字和短横线。 |
| `name` | string | 展示名称。 |
| `version` | string | 插件版本。推荐 SemVer。 |
| `entry` | object | 前端和/或后端入口。 |
| `panels` | array | 出现在 `+` 菜单中的面板定义。 |

### 推荐字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `description` | string | 插件用途说明。 |
| `author` | string | 作者或组织。 |
| `license` | string | 授权信息，可为开源协议或 `proprietary`。 |
| `enabled` | boolean | 是否启用。默认视为 `true`。 |
| `homepage` | string | 项目主页或文档地址。 |
| `chemssh` | object | 兼容的 chemssh 版本范围，例如 `{ "min": "0.1.0" }`。 |
| `dependencies` | object | Python 和前端依赖安装约定。 |
| `file_manager` | object | 插件激活后可注册到文件管理器的预览能力。 |

## 标签页与 `+` 菜单

工作区右侧或下方的功能区应抽象为标签页容器。现有预览窗口、Slurm 队列、终端辅助面板和第三方插件都使用同一套标签页行为。

推荐行为：

- 标签页只占据工作区的一部分区域，并支持拖动分隔条调整大小。
- 每个标签页都可以关闭。关闭最后一个标签页时，功能区可以折叠或显示空状态。
- 标签栏最右侧固定显示 `+` 按钮。
- 点击 `+` 后显示菜单，菜单包含内置面板和插件面板。
- 打开插件面板时创建面板实例，并向插件 UI 发送初始化上下文。
- 单例面板再次打开时聚焦已有标签页；非单例面板可以打开多个实例。
- 面板标题可由插件在运行时更新，例如根据当前文件显示 `cclib: opt.log`。

内置面板建议也按插件宿主的思路管理：

- `preview`：现有预览窗口。
- `slurm` 或 `queue`：队列/作业面板。
- 后续内置功能可继续添加为 built-in panel。

## 插件生命周期

插件生命周期分为五个阶段：

1. 安装：用户复制插件目录，或通过管理命令下载插件目录。
2. 扫描：宿主读取清单并加入插件注册表。
3. 激活：用户从 `+` 菜单打开插件面板，宿主加载后端入口并准备前端资源。
4. 运行：插件面板和插件后端处理用户交互、文件拖拽、API 调用或后台任务。
5. 停用：最后一个面板实例关闭后，宿主通知插件释放资源。

要求：

- 扫描阶段不得执行插件代码。
- 激活阶段应幂等。同一插件多次打开时，不应重复挂载同一路由或重复启动同一后台服务。
- 停用阶段应尽量释放定时器、子进程、文件句柄和网络连接。宿主会卸载插件挂载在 `/api/plugins/{plugin_id}/api/*` 下的后端路由；再次激活同一插件时，宿主可复用已加载实例，但必须重新挂载这些路由。
- 插件异常不能导致主程序退出。宿主应捕获异常并在 UI 中显示插件错误状态。

## 后端插件接口

需要后端能力的插件应提供 Python 入口。推荐入口为：

```python
def create_plugin(context):
    ...
```

`context` 由宿主提供，至少应包含：

- `plugin_id`：插件 ID。
- `plugin_dir`：插件根目录。
- `workspace_security`：路径校验工具。所有用户传入路径都必须通过它解析。
- `logger`：插件专用日志器。
- `config`：插件配置。

返回对象建议包含：

- `router`：FastAPI `APIRouter`，挂载在 `/api/plugins/{plugin_id}/api`。
- `on_activate()`：可选，插件激活时执行。
- `on_deactivate()`：可选，最后一个实例关闭时执行。

插件后端接口必须遵守：

- 不直接挂载到 `/api` 根路径，避免和主程序接口冲突。
- 不信任前端传入的文件路径，必须通过 `WorkspaceSecurity` 确认路径位于 `workspace.root` 内。
- 大文件解析应复用主程序的大文件确认模式，首次拒绝并返回可确认错误，用户确认后再使用 `force=true`。
- 长时间解析建议放入线程池、进程池或插件独立环境，避免阻塞主 FastAPI 事件循环。
- 返回 JSON 时优先使用结构化数据，不返回不可控的终端文本。

建议宿主提供的核心插件接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/plugins` | 返回可用插件和面板清单。 |
| `POST /api/plugins/{plugin_id}/activate` | 激活插件并返回运行时信息。 |
| `POST /api/plugins/{plugin_id}/deactivate` | 请求插件停用。 |
| `GET /api/plugins/{plugin_id}/assets/{path}` | 读取插件前端静态资源。 |
| `/api/plugins/{plugin_id}/api/*` | 插件自己的后端 API 前缀。 |

## 前端插件接口

首版推荐使用 `iframe` 作为插件 UI 容器。原因：

- 允许闭源插件交付构建后的 `index.html` 和资源文件。
- 降低插件和主程序 Vue/Vite 版本耦合。
- 宿主可以通过 `postMessage` 提供受控通信协议。

插件 iframe 加载完成后，宿主发送初始化消息：

```json
{
  "type": "chemssh:plugin:init",
  "version": 1,
  "pluginId": "cclib",
  "panelId": "cclib-provider",
  "instanceId": "cclib:1",
  "locale": "zh",
  "theme": "light",
  "apiBase": "/api/plugins/cclib/api",
  "assetBase": "/api/plugins/cclib/assets",
  "authToken": "当前会话 token，未配置时为 null",
  "initialFile": {
    "path": "/workspace/case/opt.log",
    "name": "opt.log"
  }
}
```

插件到宿主的消息：

| `type` | 说明 |
| --- | --- |
| `chemssh:plugin:ready` | 插件 UI 已准备接收上下文。 |
| `chemssh:plugin:set-title` | 更新当前标签页标题。 |
| `chemssh:plugin:notify` | 请求宿主显示消息提示。 |
| `chemssh:plugin:open-file` | 请求宿主打开工作区文件。 |
| `chemssh:file-manager:register-preview-provider` | 插件 UI 激活后，向文件管理器注册双击预览能力。 |
| `chemssh:file-manager:unregister-preview-provider` | 插件 UI 关闭或停用时，注销文件管理器预览能力。 |
| `chemssh:plugin:close` | 请求关闭当前插件标签页。 |

宿主到插件的消息：

| `type` | 说明 |
| --- | --- |
| `chemssh:plugin:init` | 初始化上下文。 |
| `chemssh:plugin:file-opened` | 宿主要求插件打开某个文件。 |
| `chemssh:plugin:file-dropped` | 文件管理器拖入插件标签页。 |
| `chemssh:plugin:theme-changed` | 主题变化。 |
| `chemssh:plugin:dispose` | 标签页关闭前通知插件释放前端资源。 |

插件 UI 调用后端时应使用 `apiBase`，例如：

```ts
await fetch(`${context.apiBase}/structures/preview?path=${encodeURIComponent(path)}`, {
  headers: context.authToken ? { Authorization: `Bearer ${context.authToken}` } : undefined
})
```

## 文件管理器 API 与双击预览

插件可以扩展文件管理器的双击预览能力，但必须是按需生效：

- 清单中的 `file_manager.preview_providers` 只声明插件具备哪些预览能力，用于展示安装信息和初始化菜单。
- 仅复制插件或扫描清单时，不应改变文件管理器双击行为，也不应自动激活插件。
- 用户从 `+` 菜单打开插件 UI 后，插件通过文件管理器 API 注册预览 provider。
- 注册成功后，文件管理器双击匹配文件时可以打开或聚焦现有预览窗口，并把插件声明的数据源交给预览窗口。
- 插件 UI 关闭或停用后，必须注销 provider；文件管理器双击行为恢复到现有预览窗口或其他仍激活的 provider。
- 结构类插件只负责后端解析和数据传输，不应重复实现分子画布；坐标、能量、受力摘要等数据由插件后端提供，并发送到现有预览窗口/MoleculeViewer 完成可视化。

推荐前端 API：

```ts
registerFilePreviewProvider(provider)
unregisterFilePreviewProvider(providerId)
listActiveFilePreviewProviders()
openFileWithPreviewProvider(providerId, item)
```

iframe 插件不能直接访问主程序模块，应通过 `postMessage` 请求宿主调用这些 API。推荐注册消息：

```json
{
  "type": "chemssh:file-manager:register-preview-provider",
  "version": 1,
  "provider": {
    "id": "cclib:cclib-output",
    "pluginId": "cclib",
    "panelId": "cclib-provider",
    "title": "Open with cclib",
    "priority": 60,
    "accepts": {
      "extensions": [".out", ".log", ".dat", ".txt"],
      "preview_types": ["text", "file"]
    },
    "probe": {
      "method": "POST",
      "apiPath": "/probe"
    },
    "open": {
      "mode": "preview-window",
      "reuse": "preview-tab",
      "structureSource": {
        "type": "plugin",
        "apiBase": "/api/plugins/cclib/api/structures"
      }
    }
  }
}
```

清单里的 `open.structure_api` 是相对于插件 `apiBase` 的路径；注册成 active provider 时，宿主应展开为完整 `structureSource.apiBase`，例如 `/api/plugins/cclib/api/structures`。

推荐注销消息：

```json
{
  "type": "chemssh:file-manager:unregister-preview-provider",
  "version": 1,
  "providerId": "cclib:cclib-output"
}
```

文件管理器双击流程：

1. 文件管理器拿到 `FileItem` 后，先查询当前已注册的 active providers。
2. 用 `accepts.extensions`、`accepts.filenames`、`accepts.preview_types` 做轻量匹配。
3. 目录列表渲染时只做轻量匹配。匹配到 active provider 的文件应显示与结构文件一致的小眼睛图标，表示该文件可以被当前已加载插件尝试预览。
4. 如果 provider 声明了 `probe`，文件管理器在双击打开时通过宿主调用插件后端进行内容探测；不要在目录列表渲染阶段解析每个文件。
5. 匹配成功后，文件管理器打开或聚焦现有预览窗口，并把 `structureSource`、文件路径和 provider 信息传给 preview。
6. 没有 provider 接管时，回退到现有预览窗口。

主前端提供的 provider 类型与轻量匹配工具位于 `frontend/src/api/filePreviewProviders.ts`。文件列表组件通过 `previewProviders` 属性接收当前 active providers；插件 UI 仍通过 `postMessage` 注册或注销 provider。

`probe` 接口建议：

```http
POST /api/plugins/cclib/api/probe
```

请求：

```json
{
  "path": "/workspace/case/opt.log",
  "item": {
    "name": "opt.log",
    "path": "/workspace/case/opt.log",
    "type": "file",
    "extension": ".log",
    "preview_type": "text"
  }
}
```

响应：

```json
{
  "can_preview": true,
  "handler": "cclib-output",
  "program": "ORCA",
  "reason": null
}
```

要求：

- `probe` 必须快速，优先读取文件头、尾或少量内容；完整解析应留给结构预览数据源接口。
- `probe` 和结构预览数据源接口都必须通过 `WorkspaceSecurity` 校验路径。
- 文件管理器 API 只负责打开、聚焦和分发文件，不直接解析化学文件内容。
- provider 的 `priority` 用于多个插件同时匹配时排序；用户显式选择的默认处理器优先级最高。

## 结构预览数据源协议

结构类插件应复用现有 ASE 结构预览架构：后端解析，结构信息发送到现有预览窗口可视化。`cclib` 与 ASE 的区别只在解析器来源不同；数据传输、分帧读取、二进制块和压缩策略应保持一致。

推荐把可视化数据源抽象为 `StructureSource`：

```ts
interface StructureSource {
  id: string
  parser: 'ase' | string
  apiBase: string
}
```

内置 ASE 数据源可表示为：

```json
{
  "id": "ase",
  "parser": "ase",
  "apiBase": "/api/structures/ase"
}
```

`cclib` 激活后注册的数据源可表示为：

```json
{
  "id": "cclib",
  "parser": "cclib",
  "apiBase": "/api/plugins/cclib/api/structures"
}
```

preview 前端应基于 `StructureSource.apiBase` 调用同构接口，而不是写死 ASE 路径：

| 接口 | 响应 | 说明 |
| --- | --- | --- |
| `GET {apiBase}/preview?path=...&force=false` | `AsePreviewResponse` 兼容结构 | 返回摘要和初始帧。 |
| `GET {apiBase}/frame?path=...&index=0&force=false` | `AseFrame` 兼容结构 | 返回单帧。 |
| `GET {apiBase}/frames?path=...&start=0&count=32&force=false` | `AseFrameChunkResponse` 兼容结构 | JSON 分帧块。 |
| `GET {apiBase}/frames.bin?path=...&start=0&count=32&force=false` | `application/vnd.chemssh.structure+bin` | 二进制分帧块。 |

实现时可以把现有 `readAsePreview`、`readAseFrame`、`readAseFrameJsonChunk`、`readAseFrameChunk` 泛化为 `readStructurePreview(source, ...)` 这一类 wrapper；ASE 只是默认 `StructureSource`，cclib 插件是另一个后端数据源。

`cclib` 后端应把 `cclib` 解析结果转换为 preview 已支持的结构模型：

- `atomcoords` -> `frame.positions`，单位必须是 Angstrom。
- `atomnos` -> `frame.numbers`，并同步生成 `frame.symbols`。
- 能量序列 -> `frame.energy` 和二进制块中的 `energy` 数组，单位应与 preview 文档保持一致，推荐 eV。
- 梯度或受力 -> `frame.fmax` 和二进制块中的 `fmax` 数组；如果要展示完整受力向量，应先扩展 preview 的通用结构协议，不要只在插件里私有实现。
- `cell`、`pbc`、`tags`、`fixed_indices` 在量化输出缺失时使用安全默认值，例如零晶胞、非周期、空标签和空固定原子列表。
- `topology_stable=true` 仅在所有帧原子数和元素顺序一致时返回；否则必须禁用二进制块并使用 JSON 单帧/分块读取。

二进制块要求：

- 复用 `STRUCTURE_BINARY_MAGIC = CWB1`。
- 复用媒体类型 `application/vnd.chemssh.structure+bin`。
- 复用现有 header + typed-array 布局，至少包含 `positions`、`cells`、`energy`、`fmax`；缺失值用 `NaN`，并在 header 的 `nan_means_null` 中声明。
- preview 请求二进制块时继续使用 `Accept: application/vnd.chemssh.structure+bin`。
- 插件不要自行压缩响应。只要插件路由挂载在 chemssh 主 FastAPI app 下，并返回 `application/json` 或 `application/vnd.chemssh.structure+bin`，现有 Brotli middleware 会在请求 `Accept-Encoding` 支持 `br` 时自动压缩。

## 文件拖拽与预览集成

插件面板接收文件拖拽时，宿主应优先读取现有内部拖拽协议：

- MIME：`application/x-chemssh-files`
- 工具：`frontend/src/api/fileDrag.ts`

宿主解析后，通过 `chemssh:plugin:file-dropped` 转发给插件 iframe。插件不需要直接读取主程序 DOM。

推荐 payload：

```json
{
  "type": "chemssh:plugin:file-dropped",
  "version": 1,
  "paths": ["/workspace/project/opt.log"],
  "items": [
    {
      "name": "opt.log",
      "path": "/workspace/project/opt.log",
      "type": "file",
      "extension": ".log",
      "preview_type": "text"
    }
  ]
}
```

文件树双击或选择文件时的插件候选规则：

- 文件管理器应把现有预览窗口也视为 preview provider 参与排序；通用文本预览应作为兜底，而不是压过已激活的专用插件 provider。
- 如果有插件在 `panels[*].accepts` 中声明支持该扩展名，可以在预览候选菜单中展示插件，但未激活插件不应自动接管双击。
- 如果插件 UI 已加载并注册了 active preview provider，文件管理器双击可以直接使用该 provider 打开现有预览窗口。
- 用户也可以先点 `+` 打开插件，再把文件拖入插件面板。
- 不应仅因为插件支持某种文件类型就自动激活插件，除非用户明确选择该插件作为默认处理器。

## 依赖安装策略

插件依赖分为 Python 依赖和前端依赖。插件依赖不应默认写入 chemssh 主项目配置文件，除非用户明确选择主环境安装。

Python 依赖模式：

| `mode` | 说明 | 适用场景 |
| --- | --- | --- |
| `none` | 无额外 Python 依赖。 | 纯前端插件或只用标准库。 |
| `host` | 安装到 chemssh 当前 Python 环境。 | 轻量、低冲突、希望直接复用主进程的插件。 |
| `venv` | 在插件目录或指定目录创建独立虚拟环境。 | 依赖较重、版本可能冲突、闭源 wheel 或二进制依赖。 |
| `external` | 使用用户提供的 Python 解释器或服务地址。 | 已有 Conda/venv、HPC 模块环境、商业软件接口。 |

示例：

```json
{
  "dependencies": {
    "python": {
      "mode": "host",
      "requirements": "backend/requirements.txt"
    }
  }
}
```

```json
{
  "dependencies": {
    "python": {
      "mode": "venv",
      "requirements": "backend/requirements.txt",
      "venv": ".venv"
    }
  }
}
```

```json
{
  "dependencies": {
    "python": {
      "mode": "external",
      "python": "D:/tools/cclib-env/Scripts/python.exe"
    }
  }
}
```

要求：

- `host` 模式必须提示用户该操作会修改 chemssh 当前环境。
- `venv` 模式可以将环境放在插件目录内，也可以由用户提供环境目录。
- `external` 模式必须校验解释器或服务是否可用，并在不可用时给出清晰错误。
- 插件安装依赖和插件激活是两个动作。安装依赖不应自动打开插件 UI，打开插件 UI 也不应静默安装依赖。
- 插件如需运行外部命令，必须记录命令来源、工作目录和参数，不执行用户不可见的任意 shell 字符串。

### 插件依赖管理接口

宿主应提供统一的插件依赖管理能力，避免用户手动打开插件目录查看 `requirements.txt` 或复制安装命令。推荐接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/plugins/{plugin_id}/dependencies` | 返回插件 Python 依赖模式、requirements 路径、当前 Python 解释器、已安装/缺失包。 |
| `POST /api/plugins/{plugin_id}/dependencies/install` | 安装依赖。请求体可指定 `{ "mode": "host" }` 或 `{ "mode": "venv", "venv": ".venv" }`。 |
| `POST /api/plugins/{plugin_id}/dependencies/external` | 保存并校验外部 Python 解释器，例如 `{ "python": "D:/envs/cclib/python.exe" }`。 |

管理行为：

- `host` 安装使用运行 chemssh 的 Python 执行 `python -m pip install -r <requirements>`，必须在 UI 中明确提示会修改主环境。
- `venv` 安装由宿主创建或复用插件虚拟环境，再执行该环境的 `python -m pip install -r <requirements>`。
- `external` 不自动安装依赖，只校验解释器可运行并保存路径；需要外部执行能力的插件应在自己的后端实现中读取该配置。
- 安装或配置结果应写入插件状态文件，例如插件目录下的 `.chemssh-plugin-state.json`，不要改写 `chemssh-plugin.json`，以便闭源插件和升级包保持只读。
- 插件 UI 可以调用这些接口显示“已就绪 / 缺失依赖 / 安装到 host / 安装到 venv / 使用 external”的操作入口。

前端依赖模式：

| `mode` | 说明 |
| --- | --- |
| `bundled` | 插件提供已构建的静态资源，宿主直接加载。资源目录推荐命名为 `frontend/bundle/`，并随插件发布。 |
| `build` | 插件提供源码和构建命令，构建产物输出到 `frontend/dist/`。该目录是可再生成产物，默认忽略。 |
| `host` | 插件作为受信任源码接入主前端构建。仅适合内置插件或核心贡献。 |

闭源插件推荐使用 `frontend.mode=bundled`，并只发布 `frontend/bundle`。需要复杂构建的插件应使用 `frontend.mode=build`，在发布流程中运行插件自己的构建命令并把生成结果复制或打包到最终部署产物。

`bundled` 示例：

```json
{
  "entry": {
    "frontend": {
      "type": "iframe",
      "path": "frontend/bundle/index.html"
    }
  },
  "dependencies": {
    "frontend": {
      "mode": "bundled",
      "bundle": "frontend/bundle"
    }
  }
}
```

`build` 示例：

```json
{
  "entry": {
    "frontend": {
      "type": "iframe",
      "path": "frontend/dist/index.html"
    }
  },
  "dependencies": {
    "frontend": {
      "mode": "build",
      "package": "frontend/package.json",
      "build": "npm ci && npm run build",
      "dist": "frontend/dist"
    }
  }
}
```

## 权限与安全

插件代码与 chemssh 运行在同一用户权限下，具备访问本机文件和网络的潜在能力。因此插件应被视为受信任扩展。

最低安全要求：

- 插件清单中的所有相对路径必须解析到插件目录内。
- 插件后端读取工作区文件必须通过 `WorkspaceSecurity`。
- 插件 API 不得暴露任意文件读取、任意命令执行或越权下载。
- 插件 iframe 推荐同源加载，但宿主仍应验证 `postMessage` 的 `origin` 和 `instanceId`。
- 插件错误日志不应泄露凭据、令牌或私有路径之外的敏感信息。
- 第三方闭源插件应在 README 中说明功能、依赖、外部命令和数据访问范围。

## cclib 结构解析插件示例

示例插件 ID：`cclib`

目标：

- 兼容并参考现有 ASE 结构预览架构：后端解析、同构结构接口传输、结构信息发送到现有预览窗口可视化；区别只是解析器由 ASE 换成 `cclib`。
- 后端支持 `cclib` 可解析的量子化学输出文件，不绑定某一个程序。
- 常见候选扩展名包括 `.out`、`.log`、`.dat`、`.txt`；真实可预览性由插件 `probe` 接口根据文件内容确认。
- 用户从 `+` 菜单加载 `cclib` UI 后，插件通过文件管理器 API 注册 active preview provider。
- 注册完成后，文件管理器双击匹配文件可以直接打开或聚焦现有预览窗口进行可视化。
- 提取原子序数、元素、结构轨迹坐标、能量、梯度/受力、振动或电荷等 `cclib` 已解析出的相关信息。
- 将结构数据转换为 chemssh preview 已支持的 `AsePreviewResponse` / `AseFrame` / `frames.bin` 兼容格式。
- 前端分子画布、轨迹播放、能量曲线和最大受力/梯度展示由现有预览窗口/MoleculeViewer 实现；插件 UI 只承担启用、状态、配置或错误说明等辅助职责。

推荐 Python 依赖：

```text
cclib>=1.8
numpy>=1.26
```

示例 `cclib` 插件默认使用 `host` 模式，表示通过插件管理接口把上述依赖安装到运行 chemssh 的当前 Python 环境。用户也可以在插件管理界面选择安装到插件 venv 或配置 external Python，但该选择会写入插件状态文件，不改写插件清单。

推荐插件接口：

```http
POST /api/plugins/cclib/api/probe
GET /api/plugins/cclib/api/structures/preview?path=/workspace/case/opt.log&force=false
GET /api/plugins/cclib/api/structures/frame?path=/workspace/case/opt.log&index=0&force=false
GET /api/plugins/cclib/api/structures/frames?path=/workspace/case/opt.log&start=0&count=32&force=false
GET /api/plugins/cclib/api/structures/frames.bin?path=/workspace/case/opt.log&start=0&count=32&force=false
```

`probe` 响应用于文件管理器双击前的快速判断：

```json
{
  "can_preview": true,
  "handler": "cclib-output",
  "program": "ORCA",
  "reason": null
}
```

`structures/preview` 响应必须与现有 ASE preview 兼容：

```json
{
  "path": "/workspace/case/opt.log",
  "name": "opt.log",
  "format": "cclib-output",
  "transport": "binary-available",
  "is_trajectory": true,
  "n_atoms": 3,
  "n_frames": 18,
  "initial_frame_index": 17,
  "topology_stable": true,
  "size_limit_overridden": false,
  "frame": {
    "frame_index": 17,
    "positions": [[0.0, 0.0, 0.0], [1.1, 0.0, 0.0], [0.0, 0.9, 0.0]],
    "cell": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    "pbc": [false, false, false],
    "tags": [0, 0, 0],
    "fixed_indices": [],
    "energy": -1140.12,
    "fmax": 0.024,
    "symbols": ["C", "O", "H"],
    "numbers": [6, 8, 1]
  },
  "metadata": {
    "parser": "cclib",
    "package": "ORCA",
    "charge": 0,
    "multiplicity": 1,
    "success": true
  }
}
```

`metadata`、`properties` 等扩展字段是可选的；现有 preview 只依赖 `AsePreviewResponse` 和 `AseFrame` 的核心字段。前端尚未消费的扩展字段必须可以被安全忽略。

实现建议：

- `cclib.io.ccread(path)` 负责解析文件。
- 不要在插件中硬编码某个具体量化程序作为入口条件；应优先让 `cclib` 根据文件内容识别。
- `atomnos` 转换为元素符号，`atomcoords` 转换为多帧坐标。
- `scfenergies` 可作为能量序列；如果帧数不一致，按可用数据对齐并允许 `null`。
- `grads` 可用于计算最大梯度或最大受力摘要。注意梯度和受力方向相反，且必须明确单位；如果无法可靠转换受力，只返回 `fmax` 或插件元数据，不伪造完整 `forces`。
- 其他可选属性，例如振动频率、电荷、自旋、轨道能级等，应放在 `properties` 或 `metadata` 中，避免破坏轨迹主结构。
- 大文件第一次解析前应检查大小，超过限制时返回需要确认的错误；用户确认后再带 `force=true`。
- 解析失败时返回结构化错误，提示文件可能不是 `cclib` 支持的输出，或计算未正常写出可解析轨迹。
- `probe` 只做轻量探测，不应完整解析大型输出；完整解析留给 `structures/preview`、`structures/frame` 和分块接口。
- `frames.bin` 应返回 `application/vnd.chemssh.structure+bin`，让现有 Brotli middleware 自动压缩，不要在插件里手动压缩。
- 插件 UI 激活时注册文件管理器 provider，关闭最后一个 `cclib` 标签页时注销 provider。

示例清单：

```json
{
  "schema_version": 1,
  "id": "cclib",
  "name": "cclib",
  "version": "0.1.0",
  "description": "Parse computational chemistry outputs with cclib and send compatible structure data to the existing preview window.",
  "license": "GPL-3.0-or-later",
  "entry": {
    "frontend": {
      "type": "iframe",
      "path": "frontend/bundle/index.html"
    },
    "backend": {
      "type": "python",
      "module": "backend.plugin",
      "factory": "create_plugin"
    }
  },
  "panels": [
    {
      "id": "cclib-provider",
      "title": "cclib",
      "icon": "LineChart",
      "kind": "tool",
      "singleton": false,
      "accepts": {
        "extensions": [".out", ".log", ".dat", ".txt"],
        "preview_types": ["text", "file"]
      }
    }
  ],
  "file_manager": {
    "preview_providers": [
      {
        "id": "cclib-output",
        "title": "Open with cclib",
        "panel_id": "cclib-provider",
        "activate": "on_panel_open",
        "priority": 60,
        "accepts": {
          "extensions": [".out", ".log", ".dat", ".txt"],
          "preview_types": ["text", "file"]
        },
        "probe": {
          "method": "POST",
          "path": "/probe"
        },
        "open": {
          "mode": "preview-window",
          "structure_api": "/structures"
        }
      }
    ]
  },
  "dependencies": {
    "python": {
      "mode": "host",
      "requirements": "backend/requirements.txt"
    },
    "frontend": {
      "mode": "bundled",
      "bundle": "frontend/bundle"
    }
  }
}
```

## 版本兼容

插件规范使用 `schema_version` 管理破坏性变化。

兼容策略：

- 宿主必须拒绝高于自身支持版本的 `schema_version`。
- 同一 `schema_version` 内新增字段必须保持向后兼容。
- 插件应在清单中声明需要的最低 chemssh 版本。
- 插件接口变更应同时更新插件 README 和本规范中的示例。

## 文档要求

新增或修改插件宿主能力时，应同步更新：

- `docs/PLUGIN_DEVELOPMENT.md`：插件目录、清单、生命周期、依赖、消息协议。
- `docs/API.md`：实际新增的后端接口、前端 API wrapper、窗口拖拽或预览行为。

单个插件应至少提供：

- 插件用途。
- 支持的文件类型。
- 依赖安装方式。
- 是否会调用外部命令。
- 是否需要网络访问。
- 已知限制和错误排查方式。
