# chemssh

English | [中文](README.zh-CN.md)

`chemssh` is a browser workspace for computational chemistry, catalysis, and materials simulation directories. It runs next to the data, for example on a remote Linux server or HPC login node, and is usually opened from a local browser through SSH port forwarding or a trusted launcher proxy.

Use it when you want one lightweight web UI for a simulation working directory: file management, text/log editing, structure preview, scheduler queues, browser terminals, persistent canvas boards, and optional plugins.

ChemSSH is a local-trust tool. Run it as the user who owns the files, bind it to `127.0.0.1` unless you intentionally expose it, and keep `workspace.root` as small as practical.

## Install

### For Production (from release)

**Recommended for most users.**

If you downloaded a release package from [GitHub Releases](https://github.com/jlcai09/chemssh/releases), the frontend is already built. You only need Python:

```bash
# 1. Extract the release package
tar -xzf chemssh-<version>.tar.gz
cd chemssh-<version>

# 2. Create Python virtual environment and install
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"

# That's it! No Node.js or npm needed.
```

The pre-built `frontend/dist/` directory contains all optimized static files.

<details>
<summary>Development install from source</summary>

### For Development (from source)

**Only for developers who want to modify the code.**

If you cloned the repository and want to build from source:

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

After installation, the CLI entry point is:

```bash
chemssh --help
```

## Configure

Copy and edit `config.yaml`. The key setting is the workspace root:

```yaml
workspace:
  root: /home/user/project
```

All file reads, writes, uploads, downloads, deletes, moves, copies, previews, terminal cwd sync, and job submissions are checked against `workspace.root`.

Common options:

```yaml
server:
  host: 127.0.0.1              # Bind address; keep 127.0.0.1 for local/SSH-tunnel-only access. Use a trusted address and enable token before exposing publicly
  port: 8888                   # HTTP and WebSocket service port
  idle_shutdown_seconds: 3600 # Auto-shutdown after this many seconds of inactivity; 0 disables auto-shutdown

workspace:
  root: .                      # Workspace root; all file reads/writes/uploads/downloads/previews/terminal/job submissions are confined here (override via CHEMSSH_WORKSPACE or --workspace-root)
  allow_delete: true           # Whether the frontend may delete files/directories
  max_upload_size_mb: 500      # Maximum size for a single uploaded file (MB)
  max_read_size_mb: 5          # Maximum file size loaded for preview/read (MB); larger files require confirmation

scheduler:
  type: slurm                  # Job scheduler type: slurm or pbs
  refresh_interval: 10         # Queue status polling interval (seconds)

viewer:
  max_file_size_mb: 50         # Maximum file size that can be opened in the structure previewer (MB)
  ase:
    enabled: true             # Whether to enable ASE structure parsing and preview
    prefer_binary: true       # Prefer binary frame chunks for better performance on large structures/long trajectories
    max_atoms: 200000         # Maximum atoms per frame; preview is rejected beyond this
    max_frames: 5000          # Maximum number of frames loaded; excess is truncated
    binary_chunk_frames: 32   # Number of frames per chunk in binary streaming

terminal:
  enabled: true               # Whether to enable the built-in terminal
  shell: null                 # Login shell to use (e.g. /bin/bash); null uses the system default shell
  max_sessions: 10            # Maximum terminal sessions per browser client_id
  allow_sync_cwd: true        # Whether the terminal may sync its working directory with the file manager

security:
  enable_token: false         # Whether to require token auth for all /api HTTP requests and /api WebSocket connections
  token: change-me            # Auth token; replace with a long random string and change it before exposing the service

plugins:
  enabled: true               # Whether to enable the plugin system
  directories: []             # Extra plugin directories; when empty only built-in/bundled plugins are loaded

client_cache:
  enabled: true               # Whether to enable browser-side cache (preferences, canvas layouts, etc.)
  directory: null             # Server-side cache directory; null uses the default location
  cleanup_offline_days: 14    # Client caches offline longer than this many days are cleaned up
```

`CHEMSSH_WORKSPACE` or `chemssh --workspace-root ...` can override `workspace.root`.

Set `security.enable_token: true` to require every `/api` HTTP request and `/api` WebSocket connection to carry the configured token. HTTP clients should send `Authorization: Bearer <token>`; browser downloads, plugin iframes, and terminal WebSocket URLs may use a `token=<token>` query parameter. Replace `change-me` with a long random token before exposing the service beyond a trusted localhost tunnel.

<details>
<summary>Maintainer release packaging</summary>

## How to Release

**For maintainers creating release packages.**

Use the dedicated release archive script (version is read automatically from `backend/app/__init__.py`):

**Linux/Mac/Windows (Git Bash):**
```bash
chmod +x create-release-archive.sh
./create-release-archive.sh
```

On Windows, use Git Bash to run the `.sh` script. It is the supported release packaging entry point and generates both `.tar.gz` and `.zip` formats.

This will build the frontend and create `release/chemssh-{VERSION}/`, `.tar.gz`, and `.zip` with checksums. The release package includes pre-built `frontend/dist/` (no Node.js required for users).

For offline PyInstaller runtime packaging with replaceable plain-text backend code, see [packaging/pyinstaller-external-backend.md](packaging/pyinstaller-external-backend.md).

See [docs/RELEASE.md](docs/RELEASE.md) for detailed release procedures.

</details>

## Run

Start ChemSSH with the built frontend:

```bash
chemssh --config config.yaml --host 127.0.0.1 --port 8888
```

The CLI checks whether the port is already in use. If the same port already serves ChemSSH with the same `workspace.root`, the CLI reuses it and prints the URL. If the port is occupied by another application, or by ChemSSH serving another workspace, startup fails clearly.

Port reuse options:

```bash
chemssh --config config.yaml --reuse-existing auto
chemssh --config config.yaml --reuse-existing never
chemssh --config config.yaml --reuse-existing any-chemssh
```

Check the port without starting a server:

```bash
chemssh --config config.yaml --check-port
```

Development mode usually runs backend and frontend separately:

```bash
uvicorn backend.app.main:app --host 127.0.0.1 --port 8888 --reload
npm.cmd --prefix frontend run dev
```

On Linux or macOS, use `npm` instead of `npm.cmd`. The Vite dev server runs on `127.0.0.1:5173` and proxies `/api` to `127.0.0.1:8888`.

## SSH Port Forwarding

On the remote host:

```bash
chemssh --config config.yaml --host 127.0.0.1 --port 8888
```

On your local machine:

```bash
ssh -p 22 -L 8888:127.0.0.1:8888 user@server
```

Then open:

```text
http://localhost:8888
```

If an HPC login node uses a different loopback or internal address for the service target, adjust the right side of `-L` accordingly.

<details>
<summary>Feature details</summary>

## Main Features

- Workspace file management: browse, create folders/files, upload files or folders, download single files or zip selections, rename, delete, move, and copy.
- Preview and editing: Monaco-based text editing, log tailing, large-file confirmation, and unified preview windows in workspace and canvas views.
- Structure viewing: ASE parses common molecular/material formats; Three.js renders structures and trajectories with stick/sphere/line modes, cell display, supercell/wrap controls, atom selection, measurements, fixed-atom markers, and binary frame chunks for stable trajectories.
- Scheduler integration: Slurm by default, PBS when `scheduler.type: pbs`; queue listing, job detail, cancel, hold/release, and script submission through `sbatch` or `qsub`.
- Terminal: per-browser-client terminal sessions over WebSocket, xterm.js UI, tab management, cwd sync, drag-in path insertion, browser-mediated upload/download helpers, and optional canvas file-manager bindings.
- Canvas board: persistent freeform boards with file manager, terminal, preview, queue, tail, and plugin windows; window layout and viewport are saved in client cache.
- Plugins: manifest-scanned plugins under `plugins/` and configured plugin directories; activation is on demand, with optional Python backend routes, iframe frontend assets, preview-provider registration, and dependency management.
- Launcher bridge integration: when opened through a compatible Launcher same-origin proxy, the frontend can discover bridge capabilities for system icons, local open actions, and sync-event polling. Direct ChemSSH access degrades quietly when bridge endpoints are absent.
- Client cache isolation: preferences and canvas boards are stored per browser `client_id` and per workspace scope, so different origins/users/hosts/workspace roots do not reuse stale paths and layouts.

</details>

<details>
<summary>Developer and architecture notes</summary>

## Project Layout

```text
backend/app/                  FastAPI app, routers, services, providers, models
frontend/src/                 Vue workspace, canvas, terminal, preview, API wrappers
frontend/tests/               lightweight frontend logic tests
plugins/cclib/                bundled example plugin
docs/API.md                   endpoint and window-interaction reference
docs/PLUGIN_DEVELOPMENT.md    plugin manifest/lifecycle notes
tests/                        pytest backend tests
config.yaml                   example runtime configuration
```

## Architecture

ChemSSH is a single FastAPI app that can serve the built Vue SPA from `frontend/dist`.

```text
Browser
  Vue 3 / Element Plus / Vite SPA
  xterm.js terminal, Monaco editor, Three.js structure viewer
        |
        | HTTP / WebSocket under /api
        v
FastAPI backend
  routers: files, structures, queue, jobs, terminal, plugins, client-cache, system
  services: workspace-safe file operations, ASE parsing, schedulers, plugin host, client cache
  providers: local filesystem, local pty/winpty, Slurm, PBS
        |
        v
workspace.root, scheduler commands, plugin directories, cache/
```

## Backend Path

The backend stack is Python 3.10+, FastAPI, Uvicorn, Pydantic, PyYAML, ASE, NumPy, Brotli, `ptyprocess`, and `pywinpty` on Windows.

All backend paths are resolved by `WorkspaceSecurity` and must remain inside `workspace.root`. File operations use the local filesystem provider; ChemSSH itself does not currently implement a remote SFTP filesystem backend.

The API surface is organized under `/api`:

- `/api/system/*` for runtime info and server identity used by CLI port reuse.
- `/api/files/*` for listing, reading, writing, upload/download, archive download, tail, move, and copy.
- `/api/structures/ase/*` for ASE preview, single frame, JSON frame chunks, and binary frame chunks.
- `/api/queue/*` and `/api/jobs/*` for scheduler inspection and submission.
- `/api/terminal/*` for terminal sessions and WebSocket I/O.
- `/api/plugins/*` for plugin listing, activation, assets, plugin APIs, and dependency actions.
- `/api/client-cache/*` for browser-client preferences, canvas boards, heartbeat, and cache clearing.

Brotli middleware compresses eligible responses when the client advertises `Accept-Encoding: br`. Structure binary chunks use `application/vnd.chemssh.structure+bin`.

## Frontend Path

The frontend stack is Vue 3 with `<script setup lang="ts">`, Vite, Element Plus, `@element-plus/icons-vue`, Monaco Editor, xterm.js, and Three.js.

Main views:

- `Workspace.vue`: split workspace with file manager, preview, queue/tail/terminal/plugin panels, upload/drop handling, and Launcher bridge discovery.
- `CanvasBoard.vue`: freeform board that stores boards, windows, viewport, and window payloads in client cache.
- `Settings.vue`: local/cache settings such as current `client_id` and cache clearing.

Shared frontend API wrappers live in `frontend/src/api`. New UI code should use these wrappers instead of ad hoc `fetch`, especially for file metadata, preview-provider matching, client preferences, drag payloads, and workspace scope sanitization.

## Launcher Bridge

ChemSSH itself does not implement local Launcher bridge endpoints. When it is accessed through a compatible `chemssh-launcher` same-origin proxy, the frontend attempts:

```http
GET /api/chemssh-bridge/capabilities
```

If the bridge reports enabled features and endpoint paths, ChemSSH can:

- render system file icons through the bridge icon endpoint;
- add context-menu actions to open a remote file with the local default app or text editor;
- poll sync events so file managers refresh after local edits are copied back.

When these endpoints are unavailable, fail, or report `enabled !== true`, ChemSSH falls back to normal browser-only behavior. The frontend does not persist Launcher local cache paths and does not pass profile IDs, SFTP session IDs, passwords, or host keys through its own client cache.

Do not modify `chemssh-launcher` from this repository; the bridge contract is documented in `docs/API.md`.

## Plugins

Plugins are discovered from the project `plugins/` directory and any configured `plugins.directories`. Discovery reads only `chemssh-plugin.json`; Python backends and iframe assets are loaded only when the user opens or activates a plugin panel.

An activated plugin can provide:

- static frontend assets served from `/api/plugins/{plugin_id}/assets`;
- backend routes mounted under `/api/plugins/{plugin_id}/api`;
- file-manager preview providers registered through the iframe postMessage protocol;
- dependency status/install actions for Python dependencies in `host`, plugin `venv`, or `external` mode.

`plugins/cclib` is an included example plugin. It can parse quantum chemistry output through cclib only after the plugin is activated and its Python dependencies are available. It does not replace the default ASE preview for all files by merely being present on disk.

Plugin design and manifest details live in `docs/PLUGIN_DEVELOPMENT.md`.

## Cache And Workspace Scope

The frontend creates a browser `client_id` and sends it as:

```http
X-ChemSSH-Client-Id: client_xxx
```

Preferences and canvas boards are saved through `/api/client-cache`. Current workspace isolation also sends:

```http
X-ChemSSH-Cache-Scope: scope_xxx
```

The scope is derived from browser origin, username, hostname, and `workspace_root`. This keeps layouts, current paths, terminal tab bindings, and canvas windows from leaking between different ChemSSH workspaces in the same browser. On load, the frontend sanitizes cached paths so out-of-workspace paths are dropped or replaced by the current workspace root.

By default, server-side client cache is stored under `cache/` in the project root unless `client_cache.directory` is configured. Stale client caches are cleaned on startup after `client_cache.cleanup_offline_days`.

## Development And Verification

Useful commands:

```bash
python -m pytest
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run dev
npm.cmd --prefix frontend run preview
```

Focused backend examples:

```bash
python -m pytest tests/test_api.py
python -m pytest tests/test_terminal.py tests/test_terminal_transfer.py
```

The root `package.json` also exposes:

```bash
npm run frontend:build
npm run frontend:dev
npm run frontend:preview
```

## Security And Limits

- Bind to `127.0.0.1` by default and use SSH tunneling or a trusted launcher proxy.
- Set `workspace.root` to the smallest directory that contains the project data you need.
- Backend path checks protect ChemSSH APIs from escaping `workspace.root`; they do not sandbox arbitrary commands a user runs inside an interactive terminal.
- File read and structure preview have size limits; large previews require explicit `force=true` confirmation from the frontend.
- Delete can be disabled with `workspace.allow_delete: false`.
- Scheduler actions call fixed command names (`squeue`, `scontrol`, `scancel`, `sbatch` for Slurm; `qstat`, `qdel`, `qhold`, `qrls`, `qsub` for PBS). Job IDs and script names are validated, and job submission accepts script filenames rather than arbitrary shell strings.
- Terminal sessions are per browser client and are constrained by `terminal.max_sessions`, but they still run as the ChemSSH server user.
- Plugins run in the same trust boundary as ChemSSH once activated. Install only plugins you trust.

## Documentation

- API and window interaction reference: [docs/API.md](docs/API.md)
- Plugin development: [docs/PLUGIN_DEVELOPMENT.md](docs/PLUGIN_DEVELOPMENT.md)
- FastAPI docs while running: `http://127.0.0.1:8888/docs`

</details>
