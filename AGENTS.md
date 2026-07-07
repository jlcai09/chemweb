# AGENTS.md

## Project Quick Start

- chemssh is a FastAPI + Vue 3 workspace app for computational chemistry workflows.
- Backend code lives in `backend/app`; frontend code lives in `frontend/src`; tests live in `tests`; API and interaction notes live in `docs/API.md`.
- Read `docs/API.md` before adding a new window/module. It documents backend endpoints, frontend API wrappers, and window-to-window file drag behavior.

## Encoding And Editing

- Always read and write documents, configuration files, and source files as UTF-8.
- Do not rely on the system default encoding; this project contains Chinese text and previously had mojibake issues.
- Preserve existing formatting, indentation, and line endings unless the task requires a broader cleanup.
- Keep changes scoped. Do not revert unrelated local edits.

## Backend

- Framework: FastAPI.
- API routers: `backend/app/api`.
- Pydantic models: `backend/app/models`.
- Business logic: `backend/app/services`.
- Filesystem safety is enforced through `WorkspaceSecurity`; all file paths must stay inside `workspace.root`.
- File type detection is in `backend/app/services/file_types.py`.

Common verification:

```powershell
.\.venv\Scripts\python -m pytest tests\test_api.py
```

Run a narrower test file when the change is focused. Use the full test suite for cross-cutting backend changes:

```powershell
.\.venv\Scripts\python -m pytest
```

## Frontend

- Framework: Vue 3 with `<script setup lang="ts">`.
- UI library: Element Plus.
- Main workspace: `frontend/src/views/Workspace.vue`.
- File manager: `frontend/src/components/FileTree.vue`, `FileToolbar.vue`, `FilePreview.vue`.
- Terminal: `frontend/src/components/terminal/TerminalPanel.vue`.
- API wrappers: `frontend/src/api`.
- Global styles: `frontend/src/styles.css`.
- i18n strings: `frontend/src/i18n.ts`.

### File Type And Preview Standards

- Backend file type detection is the source of truth. Use `FileItem.preview_type` and `FileItem.format` returned by `listFiles()` / drag payloads before falling back to names or extensions.
- Do not create per-window structure/text extension allowlists. Shared frontend fallback helpers live in `frontend/src/api/fileTypes.ts`; update that file only when the backend `backend/app/services/file_types.py` capability changes.
- Workspace and canvas preview behavior should stay aligned. New canvas/file-manager modules should pass through the existing `FileItem` metadata (`preview_type`, `format`, `extension`) instead of passing only a path when the metadata is available.
- Tail/log-opening heuristics are only for choosing a convenience window such as Tail. They must not be reused as file preview type standards.

After modifying frontend code, you usually do not need to start the backend/server. Verify with:

```powershell
cd frontend
npm.cmd run build
```

Give frontend builds a generous timeout (at least 240 seconds) because `vue-tsc` + Vite can take over 100 seconds on this workspace; avoid retrying just because a shorter tool timeout expired after the build had effectively completed.

If sandboxed `npm` fails on Windows, rerun the same command with approval outside the sandbox.

## Window Interaction Conventions

- The file manager supports normal drag selection and long-press file drag.
- Long-press file drag protocol is implemented in `frontend/src/api/fileDrag.ts`.
- Internal file drag MIME: `application/x-chemssh-files`.
- Terminal drops insert selected absolute paths as text, prefixed by one space and joined by spaces.
- Preview drops open only the first selected path.
- File-manager double-click behavior must stay consistent across workspace and canvas: directories open in the same file manager; files open in Preview. Tail/log windows follow file-manager selection when bound, and must not be opened by special-casing log-like filenames on double-click.
- External browser drops use download URLs; multiple files or directories download as `chemssh-selection.zip`.
- New modules that accept dragged files should use `hasChemSSHFileDrag` and `readChemSSHFileDrag` from `frontend/src/api/fileDrag.ts`.

## Documentation Expectations

- Update `docs/API.md` when adding or changing backend endpoints, frontend API wrappers, or window interaction behavior.
- Document enough for a future agent to implement compatible features without reading the whole codebase.
- Include request/response shapes and the preferred frontend wrapper when practical.

## Practical Notes

- Prefer existing helpers in `frontend/src/api` instead of ad hoc `fetch`.
- Prefer existing backend services/providers instead of putting business logic in routers.
- Large file previews should ask for confirmation before using `force=true`.
- Do not start a dev server unless the user explicitly needs to try the running UI.
- Do not operate on `git` unless the user explicitly asks for it.
