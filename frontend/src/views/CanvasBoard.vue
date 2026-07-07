<template>
  <div class="canvas-board-view">
    <aside class="canvas-sidebar">
      <div class="canvas-sidebar-header">
        <div>
          <span class="eyebrow">{{ t('canvas.eyebrow') }}</span>
          <strong>{{ t('canvas.title') }}</strong>
        </div>
        <el-tooltip :content="t('canvas.newBoard')" placement="right" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
          <el-button :icon="Plus" circle size="small" @click="createBoard" />
        </el-tooltip>
      </div>

      <div class="canvas-board-list">
        <button
          v-for="board in boardState.boards"
          :key="board.id"
          class="canvas-board-item"
          :class="{ 'is-active': board.id === boardState.activeBoardId }"
          type="button"
          @click="activateBoard(board.id)"
        >
          <span>{{ board.name }}</span>
          <small>{{ board.windows.length }} {{ t('canvas.windowCount') }}</small>
        </button>
      </div>

      <div class="canvas-sidebar-actions">
        <el-button :icon="EditPen" size="small" @click="renameActiveBoard">{{ t('canvas.renameBoard') }}</el-button>
        <el-button :icon="Delete" size="small" :disabled="boardState.boards.length <= 1" @click="deleteActiveBoard">
          {{ t('canvas.deleteBoard') }}
        </el-button>
      </div>
    </aside>

    <section class="canvas-workspace">
      <div class="canvas-toolbar">
        <div class="canvas-save-state" :class="`is-${saveStatus}`">
          <span />
          {{ saveStatusLabel }}
        </div>
        <div class="canvas-toolbar-actions">
          <el-dropdown trigger="click" @command="handleCreateWindowCommand">
            <el-button :icon="Plus" size="small" type="primary">{{ t('canvas.newWindow') }}</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="file-manager">{{ t('canvas.window.fileManager') }}</el-dropdown-item>
                <el-dropdown-item command="terminal">{{ t('canvas.window.terminal') }}</el-dropdown-item>
                <el-dropdown-item command="preview">{{ t('canvas.window.preview') }}</el-dropdown-item>
                <el-dropdown-item command="queue">{{ t('canvas.window.queue') }}</el-dropdown-item>
                <el-dropdown-item command="tail">{{ t('canvas.window.tail') }}</el-dropdown-item>
                <el-dropdown-item command="plugin">{{ t('canvas.window.plugin') }}</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-tooltip :content="t('canvas.fitView')" placement="bottom" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
            <el-button :icon="Aim" circle size="small" @click="resetView" />
          </el-tooltip>
        </div>
      </div>

      <div
        ref="surfaceRef"
        class="canvas-surface"
        @wheel="handleWheel"
        @pointerdown.self="startPan"
      >
        <div
          v-if="activeBoard && activeBoard.windows.length === 0"
          class="canvas-empty"
        >
          <strong>{{ t('canvas.emptyTitle') }}</strong>
          <p>{{ t('canvas.emptyHint') }}</p>
          <el-button :icon="Plus" type="primary" @click="createWindow('file-manager')">{{ t('canvas.newWindow') }}</el-button>
        </div>

        <div class="canvas-stage" :style="stageStyle" @pointerdown.self="startPan">
          <svg
            v-if="bindingLinks.length > 0"
            class="canvas-binding-layer"
            :style="bindingLayerStyle"
            aria-hidden="true"
          >
            <g v-for="link in bindingLinks" :key="link.id" class="canvas-binding-link">
              <path :d="bindingPath(link)" :stroke="link.color" />
              <circle :cx="bindingPoint(link.startX, 'x')" :cy="bindingPoint(link.startY, 'y')" r="4" :fill="link.color" />
              <circle :cx="bindingPoint(link.endX, 'x')" :cy="bindingPoint(link.endY, 'y')" r="4" :fill="link.color" />
              <text :x="bindingLabelX(link)" :y="bindingLabelY(link)" :fill="link.color">{{ link.label }}</text>
            </g>
          </svg>
          <CanvasWindow
            v-for="windowState in activeBoard?.windows ?? []"
            :key="windowState.id"
            :window="windowState"
            :active="windowState.id === activeWindowId"
            :zoom="activeBoard?.viewport.zoom ?? 1"
            :kind-label="windowKindLabel(windowState)"
            :binding-label="windowBindingLabel(windowState)"
            :binding-color="windowBindingColor(windowState)"
            @activate="activateWindow"
            @move="moveWindow"
            @resize="resizeWindow"
            @close="closeWindow"
            @toggle-minimize="toggleMinimize"
          >
            <CanvasFileManagerWindow
              v-if="windowState.type === 'file-manager'"
              :initial-path="fileManagerPath(windowState)"
              :refresh-token="fileManagerRefreshToken(windowState.id)"
              :launcher-bridge-capabilities="launcherBridgeCapabilities"
              :workspace-root="systemInfo?.workspace_root"
              :preview-providers="previewProviders"
              @path-change="path => updateFileManagerPath(windowState.id, path)"
              @open-file="item => openFileFromManager(windowState, item)"
              @selection-change="(items, primary) => updateFileManagerSelection(windowState.id, items, primary)"
              @directories-change="paths => refreshFileManagersForDirectories(paths)"
            />
            <QueueStatus
              v-else-if="windowState.type === 'queue'"
              compact
              :workspace-root="systemInfo?.workspace_root"
              @open-workdir="path => $emit('open-workdir', path)"
            />
            <div v-else-if="windowState.type === 'tail'" class="canvas-tail-window">
              <LogViewer
                :path="tailPath(windowState)"
                :initial-lines="tailLines(windowState)"
                @lines-change="lines => updateTailLines(windowState.id, lines)"
              >
                <template #header-actions>
                  <el-dropdown
                    trigger="click"
                    placement="bottom"
                    @command="handleTailBindingChange(windowState.id, $event)"
                  >
                    <button
                      class="terminal-tab-icon terminal-tab-bind canvas-tail-bind-button"
                      :class="{ 'is-bound': Boolean(boundFileManagerId(windowState)) }"
                      type="button"
                      :style="bindingButtonStyle(windowState)"
                      :title="tailBindingHint(windowState)"
                      :aria-label="t('canvas.binding.selectFileManager')"
                    >
                      <el-icon><Link /></el-icon>
                    </button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="">{{ t('canvas.binding.none') }}</el-dropdown-item>
                        <el-dropdown-item
                          v-for="manager in fileManagerTargets"
                          :key="manager.id"
                          :command="manager.id"
                        >
                          <span class="canvas-binding-option">
                            <span class="canvas-binding-dot" :style="{ background: manager.color }" />
                            <span>{{ manager.title }}</span>
                          </span>
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </template>
              </LogViewer>
            </div>
            <TerminalPanel
              v-else-if="windowState.type === 'terminal'"
              :layout-version="terminalLayoutVersion"
              :workspace-root="systemInfo?.workspace_root"
              :file-managers="fileManagerTargets"
              :initial-bindings="terminalBindingSummary(windowState)"
              @bound-cwd-change="(managerId, path) => updateBoundFileManagerPath(managerId, path)"
              @refresh-file-manager="(managerId, path) => refreshFileManagerFromTerminal(managerId, path)"
              @binding-summary-change="summary => updateTerminalBindingSummary(windowState.id, summary)"
            />
            <CanvasPreviewWindow
              v-else-if="windowState.type === 'preview'"
              :path="previewPath(windowState)"
              :preview-type="previewType(windowState)"
              :format="previewFormat(windowState)"
              :preview-providers="previewProviders"
              @path-change="(path, metadata) => updatePreviewPath(windowState.id, path, metadata)"
            />
            <CanvasPluginWindow
              v-else-if="windowState.type === 'plugin'"
              :instance-id="windowState.id"
              :plugin-id="pluginPayloadString(windowState, 'pluginId')"
              :panel-id="pluginPayloadString(windowState, 'panelId')"
              :asset-url="pluginPayloadString(windowState, 'assetUrl')"
              :api-base="pluginPayloadString(windowState, 'apiBase')"
              @payload-change="payload => updatePluginPayload(windowState.id, payload)"
              @title-change="title => updateWindowTitle(windowState.id, title)"
              @register-provider="registerPreviewProvider"
              @unregister-provider="unregisterPreviewProvider"
            />
            <div v-else class="canvas-placeholder">
              <strong>{{ windowState.title }}</strong>
              <p>{{ t('canvas.placeholder') }}</p>
            </div>
          </CanvasWindow>
        </div>

        <div class="canvas-zoom-controls">
          <el-button :icon="Minus" circle size="small" @click="zoomBy(0.9)" />
          <span>{{ Math.round((activeBoard?.viewport.zoom ?? 1) * 100) }}%</span>
          <el-button :icon="Plus" circle size="small" @click="zoomBy(1.1)" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Aim, Delete, EditPen, Link, Minus, Plus } from '@element-plus/icons-vue'
import { heartbeatClientCache, loadClientCache, saveCanvasBoards } from '../api/clientCache'
import {
  configureClientPreferencesScope,
  getClientPreferences,
  normalizeClientPreferences,
  saveClientPreferencesPatch,
  setClientPreferencesState
} from '../api/clientPreferences'
import {
  loadLauncherBridgeCapabilities,
  parentDirectoryPath,
  type LauncherBridgeCapabilities,
  type LauncherBridgeSyncEvent
} from '../api/launcherBridge'
import QueueStatus from '../components/QueueStatus.vue'
import LogViewer from '../components/LogViewer.vue'
import TerminalPanel from '../components/terminal/TerminalPanel.vue'
import CanvasFileManagerWindow from '../components/canvas/CanvasFileManagerWindow.vue'
import CanvasPluginWindow from '../components/canvas/CanvasPluginWindow.vue'
import CanvasPreviewWindow from '../components/canvas/CanvasPreviewWindow.vue'
import CanvasWindow from '../components/canvas/CanvasWindow.vue'
import { t } from '../i18n'
import {
  createWorkspaceScope,
  sanitizeCanvasBoardStateForWorkspace,
  scopedLocalStorageKey,
  workspacePathOrRoot
} from '../api/workspaceScope'
import {
  DEFAULT_CANVAS_VIEWPORT,
  CANVAS_WINDOW_MINIMIZED_HEIGHT,
  createDefaultBoardState,
  newIdToken,
  type CanvasBoard,
  type CanvasBoardState,
  type CanvasFileManagerBindingTarget,
  type CanvasTerminalTabBinding,
  type CanvasWindowState,
  type CanvasWindowType,
  type ClientPreferences
} from '../types/canvasBoard'
import type { FileItem, PreviewType } from '../api/files'
import type { FilePreviewProvider } from '../api/filePreviewProviders'
import { useCanvasViewport } from '../composables/useCanvasViewport'
import { useSystemStore } from '../stores/system'

defineEmits<{
  'open-workdir': [path: string]
}>()

const systemStore = useSystemStore()
const { systemInfo: systemStoreInfo, syncEvents: systemStoreSyncEvents } = storeToRefs(systemStore)
const systemInfo = computed(() => systemStoreInfo.value)
const syncEvents = computed(() => systemStoreSyncEvents.value)

type SaveStatus = 'saved' | 'dirty' | 'saving' | 'error'

const LOCAL_BOARDS_KEY = 'chemssh.canvas.boards.v1'
const BINDING_ANCHOR_MAX_HEIGHT = 220
const BINDING_COLORS = ['#176b87', '#168a45', '#b54708', '#7a4fb4', '#c11574', '#0e7490']

const surfaceRef = ref<HTMLElement | null>(null)
const boardState = ref<CanvasBoardState>(createDefaultBoardState())
const preferences = ref<ClientPreferences>({ version: 1, logs: { tailLines: 20 } })
const activeWindowId = ref<string | null>(null)
const saveStatus = ref<SaveStatus>('saved')
const terminalLayoutVersion = ref(0)
const fileManagerRefreshTokens = ref<Record<string, number>>({})
const launcherBridgeCapabilities = ref<LauncherBridgeCapabilities | null>(null)
const previewProviders = ref<FilePreviewProvider[]>([])
const previewProviderRegistrations = ref<Record<string, Record<string, FilePreviewProvider>>>({})
let saveTimer: number | undefined
let heartbeatTimer: number | undefined
let hydrated = false
let mounted = false

const activeBoard = computed(() => {
  return boardState.value.boards.find(board => board.id === boardState.value.activeBoardId) ?? boardState.value.boards[0] ?? null
})

const { resetView, zoomBy, handleWheel, startPan } = useCanvasViewport(
  surfaceRef,
  activeBoard,
  () => {
    terminalLayoutVersion.value += 1
  }
)

const stageStyle = computed(() => {
  const viewport = activeBoard.value?.viewport ?? DEFAULT_CANVAS_VIEWPORT
  return {
    transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.zoom})`
  }
})

const saveStatusLabel = computed(() => {
  if (saveStatus.value === 'saving') return t('canvas.saving')
  if (saveStatus.value === 'dirty') return t('canvas.unsaved')
  if (saveStatus.value === 'error') return t('canvas.saveFailed')
  return t('canvas.saved')
})

const fileManagerTargets = computed<CanvasFileManagerBindingTarget[]>(() => {
  return (activeBoard.value?.windows ?? [])
    .filter(windowState => windowState.type === 'file-manager')
    .map((windowState, index) => ({
      id: windowState.id,
      title: fileManagerDisplayLabel(windowState, index),
      path: fileManagerPath(windowState),
      color: fileManagerColor(windowState, index)
    }))
})

type BindingLink = {
  id: string
  startX: number
  startY: number
  endX: number
  endY: number
  color: string
  label: string
}

const bindingLinks = computed<BindingLink[]>(() => {
  const board = activeBoard.value
  if (!board) return []
  const fileManagers = new Map(fileManagerTargets.value.map(item => [item.id, item]))
  const windows = new Map(board.windows.map(windowState => [windowState.id, windowState]))
  const links: BindingLink[] = []

  for (const windowState of board.windows) {
    if (windowState.type === 'tail') {
      const managerId = boundFileManagerId(windowState)
      const manager = managerId ? fileManagers.get(managerId) : null
      const source = manager ? windows.get(manager.id) : null
      if (!manager || !source) continue
      links.push(createBindingLink(source, windowState, manager.color, t('canvas.window.tail'), `${source.id}:${windowState.id}`))
    }

    if (windowState.type === 'terminal') {
      const grouped = terminalBindingGroups(windowState)
      for (const group of grouped) {
        const manager = fileManagers.get(group.managerId)
        const source = manager ? windows.get(manager.id) : null
        if (!manager || !source) continue
        links.push(createBindingLink(source, windowState, manager.color, group.label, `${source.id}:${windowState.id}:${group.managerId}`))
      }
    }
  }

  return links
})

const bindingLayerBounds = computed(() => {
  const links = bindingLinks.value
  if (links.length === 0) return { left: 0, top: 0, width: 1, height: 1 }
  const xs = links.flatMap(link => [link.startX, link.endX])
  const ys = links.flatMap(link => [link.startY, link.endY])
  const left = Math.min(...xs) - 80
  const top = Math.min(...ys) - 50
  const right = Math.max(...xs) + 80
  const bottom = Math.max(...ys) + 50
  return {
    left,
    top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top)
  }
})

const bindingLayerStyle = computed(() => {
  const bounds = bindingLayerBounds.value
  return {
    left: `${bounds.left}px`,
    top: `${bounds.top}px`,
    width: `${bounds.width}px`,
    height: `${bounds.height}px`
  }
})

async function initializeCanvasBoard() {
  if (!mounted || hydrated || !systemInfo.value) return
  configureClientPreferencesScope(createWorkspaceScope(systemInfo.value))
  await hydrate()
  void loadLauncherBridge()
  void heartbeatClientCache().catch(() => undefined)
  heartbeatTimer = window.setInterval(() => {
    void heartbeatClientCache().catch(() => undefined)
  }, 5 * 60 * 1000)
}

onMounted(() => {
  mounted = true
  void initializeCanvasBoard()
})

watch(
  () => systemInfo.value?.workspace_root,
  () => {
    void initializeCanvasBoard()
  }
)

onBeforeUnmount(() => {
  if (saveTimer) window.clearTimeout(saveTimer)
  if (heartbeatTimer) window.clearInterval(heartbeatTimer)
})

watch(
  boardState,
  () => {
    if (!hydrated) return
    markDirty()
  },
  { deep: true }
)

watch(
  preferences,
  () => {
    if (!hydrated) return
    void saveClientPreferencesPatch(preferences.value).catch(() => undefined)
  }
)

async function hydrate() {
  const localBoards = readLocalBoards()
  const localPreferences = getClientPreferences()
  if (localBoards) boardState.value = localBoards
  if (localPreferences) preferences.value = localPreferences

  try {
    const cache = await loadClientCache()
    boardState.value = normalizeBoardState(cache.boards)
    setClientPreferencesState(cache.preferences)
    preferences.value = normalizeClientPreferences(getClientPreferences())
    saveLocalBoards()
    saveStatus.value = 'saved'
  } catch {
    boardState.value = normalizeBoardState(boardState.value)
    preferences.value = normalizeClientPreferences(preferences.value)
  } finally {
    hydrated = true
  }
}

function createBoard() {
  const now = new Date().toISOString()
  const index = boardState.value.boards.length + 1
  const board: CanvasBoard = {
    id: `board_${newIdToken()}`,
    name: `${t('canvas.board')} ${index}`,
    createdAt: now,
    updatedAt: now,
    viewport: { ...DEFAULT_CANVAS_VIEWPORT },
    windows: []
  }
  boardState.value.boards.push(board)
  boardState.value.activeBoardId = board.id
  activeWindowId.value = null
}

function activateBoard(boardId: string) {
  boardState.value.activeBoardId = boardId
  preferences.value = { ...(preferences.value ?? {}), canvas: { ...(preferences.value.canvas ?? {}), lastBoardId: boardId } }
  activeWindowId.value = null
  terminalLayoutVersion.value += 1
}

async function renameActiveBoard() {
  const board = activeBoard.value
  if (!board) return
  try {
    const result = await ElMessageBox.prompt(t('canvas.boardName'), t('canvas.renameBoard'), {
      inputValue: board.name,
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
    const name = result.value.trim()
    if (!name) return
    board.name = name
    board.updatedAt = new Date().toISOString()
  } catch {
    // User cancelled.
  }
}

async function deleteActiveBoard() {
  const board = activeBoard.value
  if (!board || boardState.value.boards.length <= 1) return
  try {
    await ElMessageBox.confirm(t('canvas.deleteBoardConfirm', { name: board.name }), t('prompt.confirmDelete'), {
      type: 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }
  boardState.value.boards = boardState.value.boards.filter(item => item.id !== board.id)
  boardState.value.activeBoardId = boardState.value.boards[0]?.id ?? null
  activeWindowId.value = null
}

function createWindow(rawType: string) {
  const type = normalizeWindowType(rawType)
  const board = activeBoard.value
  const surface = surfaceRef.value
  if (!type || !board || !surface) return
  const rect = surface.getBoundingClientRect()
  const width = type === 'terminal' ? 680 : type === 'queue' ? 720 : type === 'file-manager' ? 620 : 520
  const height = type === 'terminal' ? 630 : type === 'queue' ? 690 : type === 'file-manager' ? 645 : 510
  const x = (rect.width / 2 - board.viewport.x) / board.viewport.zoom - width / 2
  const y = (rect.height / 2 - board.viewport.y) / board.viewport.zoom - height / 2
  const payload = defaultPayload(type, board)
  const windowState: CanvasWindowState = {
    id: `window_${newIdToken()}`,
    type,
    title: initialWindowTitle(type, payload),
    x,
    y,
    width,
    height,
    zIndex: nextZIndex(board),
    payload
  }
  board.windows.push(windowState)
  board.updatedAt = new Date().toISOString()
  activeWindowId.value = windowState.id
  terminalLayoutVersion.value += 1
}

function handleCreateWindowCommand(command: string | number | boolean) {
  createWindow(String(command))
}

function registerPreviewProvider(provider: FilePreviewProvider, ownerId = provider.id) {
  previewProviderRegistrations.value = {
    ...previewProviderRegistrations.value,
    [provider.id]: {
      ...(previewProviderRegistrations.value[provider.id] ?? {}),
      [ownerId]: provider
    }
  }
  syncPreviewProviders()
}

function unregisterPreviewProvider(providerId: string, ownerId = providerId) {
  const registrations = { ...previewProviderRegistrations.value }
  const owners = { ...(registrations[providerId] ?? {}) }
  delete owners[ownerId]
  if (Object.keys(owners).length > 0) {
    registrations[providerId] = owners
  } else {
    delete registrations[providerId]
  }
  previewProviderRegistrations.value = registrations
  syncPreviewProviders()
}

function syncPreviewProviders() {
  previewProviders.value = Object.values(previewProviderRegistrations.value)
    .map(registrations => {
      const providers = Object.values(registrations)
      return providers[providers.length - 1]
    })
    .filter((provider): provider is FilePreviewProvider => Boolean(provider))
    .sort((left, right) => (right.priority ?? 0) - (left.priority ?? 0))
}

function activateWindow(windowId: string) {
  const board = activeBoard.value
  const target = board?.windows.find(item => item.id === windowId)
  if (!board || !target) return
  activeWindowId.value = windowId
  target.zIndex = nextZIndex(board)
}

function moveWindow(windowId: string, x: number, y: number) {
  updateWindow(windowId, windowState => {
    windowState.x = Math.round(x)
    windowState.y = Math.round(y)
  })
}

function resizeWindow(windowId: string, width: number, height: number) {
  updateWindow(windowId, windowState => {
    windowState.width = Math.round(width)
    windowState.height = Math.round(height)
  })
  terminalLayoutVersion.value += 1
}

function closeWindow(windowId: string) {
  const board = activeBoard.value
  if (!board) return
  board.windows = board.windows.filter(item => item.id !== windowId)
  if (activeWindowId.value === windowId) activeWindowId.value = null
}

function toggleMinimize(windowId: string) {
  updateWindow(windowId, windowState => {
    windowState.minimized = !windowState.minimized
  })
  terminalLayoutVersion.value += 1
}

function updateTailPath(windowId: string, path: string) {
  updateWindow(windowId, windowState => {
    windowState.payload = { ...(windowState.payload ?? {}), path }
    windowState.title = path ? path.split(/[\\/]/).pop() || t('canvas.window.tail') : t('canvas.window.tail')
  })
}

function updateFileManagerPath(windowId: string, path: string) {
  updateWindow(windowId, windowState => {
    windowState.payload = { ...(windowState.payload ?? {}), path }
    windowState.title = fileManagerDirectoryName(path) || t('canvas.window.fileManager')
  })
}

function fileManagerRefreshToken(windowId: string) {
  return fileManagerRefreshTokens.value[windowId] ?? 0
}

function refreshFileManagersForDirectories(paths: string[]) {
  const changed = new Set(paths.map(normalizePath).filter(Boolean))
  if (changed.size === 0) return
  const next = { ...fileManagerRefreshTokens.value }
  for (const windowState of activeBoard.value?.windows ?? []) {
    if (windowState.type !== 'file-manager') continue
    if (!changed.has(normalizePath(fileManagerPath(windowState)))) continue
    next[windowState.id] = (next[windowState.id] ?? 0) + 1
  }
  fileManagerRefreshTokens.value = next
}

function refreshFileManagerFromTerminal(managerId: string | null, path: string) {
  if (!managerId) {
    refreshFileManagersForDirectories([path])
    return
  }
  updateFileManagerPath(managerId, path)
  fileManagerRefreshTokens.value = {
    ...fileManagerRefreshTokens.value,
    [managerId]: (fileManagerRefreshTokens.value[managerId] ?? 0) + 1
  }
}

async function loadLauncherBridge() {
  launcherBridgeCapabilities.value = await loadLauncherBridgeCapabilities()
}

// Watch for sync events from App.vue (centralized handling)
watch(
  () => syncEvents.value,
  (events) => {
    if (!events || events.length === 0) return
    handleLauncherSyncEvents(events)
  }
)

function handleLauncherSyncEvents(events: LauncherBridgeSyncEvent[]) {
  const directories = new Set<string>()

  for (const event of events) {
    // Skip error events (already handled by App.vue)
    if (event.status !== 'done') continue

    const directory = parentDirectoryPath(event.remote_path)
    if (directory) directories.add(directory)
  }

  if (directories.size > 0) refreshFileManagersForDirectories(Array.from(directories))
  // Note: success/error messages are handled by App.vue
}

function updatePreviewPath(windowId: string, path: string, metadata?: { previewType?: PreviewType | null; format?: string | null }) {
  updateWindow(windowId, windowState => {
    const previousPath = typeof windowState.payload?.path === 'string' ? windowState.payload.path : ''
    windowState.payload = {
      ...(windowState.payload ?? {}),
      path,
      previewType: metadata ? (metadata.previewType ?? null) : (previousPath === path ? windowState.payload?.previewType : null),
      format: metadata ? (metadata.format ?? null) : (previousPath === path ? windowState.payload?.format : null)
    }
    windowState.title = path ? path.split(/[\\/]/).pop() || t('canvas.window.preview') : t('canvas.window.preview')
  })
}

function previewPath(windowState: CanvasWindowState) {
  const path = windowState.payload?.path
  return typeof path === 'string' ? path : ''
}

function previewType(windowState: CanvasWindowState): PreviewType | null {
  const value = windowState.payload?.previewType
  return value === 'text' || value === 'structure' || value === 'file' || value === 'directory' ? value : null
}

function previewFormat(windowState: CanvasWindowState) {
  const value = windowState.payload?.format
  return typeof value === 'string' ? value : null
}

function updatePluginPayload(windowId: string, payload: Record<string, unknown>) {
  updateWindow(windowId, windowState => {
    windowState.payload = { ...(windowState.payload ?? {}), ...payload }
  })
}

function updateWindowTitle(windowId: string, title: string) {
  updateWindow(windowId, windowState => {
    windowState.title = title || windowTitle(windowState.type)
  })
}

function pluginPayloadString(windowState: CanvasWindowState, key: string) {
  const value = windowState.payload?.[key]
  return typeof value === 'string' ? value : null
}

function fileManagerPath(windowState: CanvasWindowState) {
  const path = windowState.payload?.path
  if (typeof path === 'string') return workspacePathOrRoot(path, systemInfo.value?.workspace_root)
  return systemInfo.value?.workspace_root ?? ''
}

function fileManagerDirectoryName(path: string) {
  const normalized = path.trim().replace(/[\\/]+$/, '')
  if (!normalized) return ''
  const parts = normalized.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || normalized
}

function fileManagerDisplayLabel(windowState: CanvasWindowState, index: number) {
  const number = fileManagerNumber(windowState, index)
  return `${t('canvas.window.fileManager')} ${number}`
}

function fileManagerNumber(windowState: CanvasWindowState, fallbackIndex = 0) {
  const value = windowState.payload?.bindingNumber
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallbackIndex + 1
}

function fileManagerColor(windowState: CanvasWindowState, fallbackIndex = 0) {
  const value = windowState.payload?.bindingColor
  if (typeof value === 'string' && value) return value
  return BINDING_COLORS[fallbackIndex % BINDING_COLORS.length]
}

function nextFileManagerNumber(board: CanvasBoard) {
  const numbers = board.windows
    .filter(windowState => windowState.type === 'file-manager')
    .map(windowState => {
      const value = windowState.payload?.bindingNumber
      return typeof value === 'number' && Number.isFinite(value) ? value : 0
    })
  return Math.max(0, ...numbers) + 1
}

function openFileFromManager(sourceWindow: CanvasWindowState, item: FileItem) {
  const created = createWindowNear('preview', sourceWindow)
  if (created) {
    setPreviewWindowPayload(created, item)
    created.title = item.name || t('canvas.window.preview')
  }
}

function setPreviewWindowPayload(windowState: CanvasWindowState, item: FileItem) {
  windowState.payload = {
    ...(windowState.payload ?? {}),
    path: item.path,
    previewType: item.preview_type,
    format: item.format ?? null
  }
}

function updateFileManagerSelection(windowId: string, items: FileItem[], primary: FileItem | null) {
  const selectedTailFile = primary?.type === 'file'
    ? primary
    : items.find(item => item.type === 'file')
  updateWindow(windowId, windowState => {
    windowState.payload = {
      ...(windowState.payload ?? {}),
      selectedPaths: items.map(item => item.path),
      selectedLogPath: selectedTailFile?.path ?? null
    }
  })

  if (!selectedTailFile) return
  for (const windowState of activeBoard.value?.windows ?? []) {
    if (windowState.type === 'tail' && boundFileManagerId(windowState) === windowId) {
      updateTailPath(windowState.id, selectedTailFile.path)
    }
  }
}

function createWindowNear(type: CanvasWindowType, sourceWindow: CanvasWindowState) {
  const board = activeBoard.value
  if (!board) return null
  const payload = defaultPayload(type, board)
  const windowState: CanvasWindowState = {
    id: `window_${newIdToken()}`,
    type,
    title: initialWindowTitle(type, payload),
    x: sourceWindow.x + 34,
    y: sourceWindow.y + 34,
    width: type === 'tail' ? 520 : 560,
    height: type === 'tail' ? 510 : 540,
    zIndex: nextZIndex(board),
    payload
  }
  board.windows.push(windowState)
  board.updatedAt = new Date().toISOString()
  activeWindowId.value = windowState.id
  return windowState
}

function updateTailLines(windowId: string, lines: number) {
  updateWindow(windowId, windowState => {
    windowState.payload = { ...(windowState.payload ?? {}), lines }
  })
  preferences.value = { ...(preferences.value ?? {}), logs: { ...(preferences.value.logs ?? {}), tailLines: lines } }
}

function boundFileManagerId(windowState: CanvasWindowState) {
  const value = windowState.payload?.boundFileManagerId
  return typeof value === 'string' && value ? value : null
}

function setWindowBinding(windowId: string, managerId: string) {
  const safeManagerId = managerId || null
  updateWindow(windowId, windowState => {
    windowState.payload = {
      ...(windowState.payload ?? {}),
      boundFileManagerId: safeManagerId
    }
  })
  if (!safeManagerId) return
  const windowState = activeBoard.value?.windows.find(item => item.id === windowId)
  if (windowState?.type !== 'tail') return
  const manager = activeBoard.value?.windows.find(item => item.id === safeManagerId)
  const selectedLogPath = manager?.payload?.selectedLogPath
  if (typeof selectedLogPath === 'string' && selectedLogPath) updateTailPath(windowId, selectedLogPath)
}

function handleTailBindingChange(windowId: string, value: unknown) {
  setWindowBinding(windowId, typeof value === 'string' ? value : '')
}

function tailBindingHint(windowState: CanvasWindowState) {
  const managerId = boundFileManagerId(windowState)
  if (!managerId) return t('canvas.binding.tailUnbound')
  const manager = fileManagerTargets.value.find(item => item.id === managerId)
  if (!manager) return t('canvas.binding.missing')
  return manager.path || t('canvas.binding.noPath')
}

function updateBoundFileManagerPath(managerId: string, path: string) {
  updateFileManagerPath(managerId, path)
}

function updateTerminalBindingSummary(windowId: string, summary: CanvasTerminalTabBinding[]) {
  updateWindow(windowId, windowState => {
    windowState.payload = {
      ...(windowState.payload ?? {}),
      tabBindings: summary
    }
  })
}

function terminalBindingSummary(windowState: CanvasWindowState): CanvasTerminalTabBinding[] {
  const raw = windowState.payload?.tabBindings
  if (!Array.isArray(raw)) return []
  return raw.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const value = item as Partial<CanvasTerminalTabBinding>
    if (typeof value.tabId !== 'string') return []
    return [{
      tabId: value.tabId,
      title: typeof value.title === 'string' ? value.title : t('canvas.window.terminal'),
      cwd: typeof value.cwd === 'string' ? value.cwd : '',
      syncMode: value.syncMode === 'follow' || value.syncMode === 'bidirectional' ? value.syncMode : 'off',
      boundFileManagerId: typeof value.boundFileManagerId === 'string' ? value.boundFileManagerId : null,
      active: value.active === true
    }]
  })
}

function terminalBindingGroups(windowState: CanvasWindowState) {
  const groups = new Map<string, CanvasTerminalTabBinding[]>()
  for (const tab of terminalBindingSummary(windowState)) {
    if (!tab.boundFileManagerId) continue
    groups.set(tab.boundFileManagerId, [...(groups.get(tab.boundFileManagerId) ?? []), tab])
  }
  return Array.from(groups.entries()).map(([managerId, tabs]) => ({
    managerId,
    label: tabs.length === 1
      ? tabs[0].title
      : t('canvas.binding.terminalTabs', { count: tabs.length })
  }))
}

function windowBindingLabel(windowState: CanvasWindowState) {
  if (windowState.type === 'tail') {
    const manager = fileManagerTargets.value.find(item => item.id === boundFileManagerId(windowState))
    return manager ? manager.title : ''
  }
  if (windowState.type === 'terminal') {
    const boundTabs = terminalBindingSummary(windowState).filter(tab => tab.boundFileManagerId)
    if (boundTabs.length === 0) return ''
    return boundTabs.length === 1
      ? boundTabs[0].title
      : t('canvas.binding.terminalTabs', { count: boundTabs.length })
  }
  if (windowState.type === 'file-manager') {
    const linkedCount = bindingLinks.value.filter(link => link.id.startsWith(`${windowState.id}:`)).length
    return linkedCount > 0 ? t('canvas.binding.linkedCount', { count: linkedCount }) : ''
  }
  return ''
}

function windowBindingColor(windowState: CanvasWindowState) {
  if (windowState.type === 'file-manager') {
    return fileManagerTargets.value.find(item => item.id === windowState.id)?.color
  }
  const managerId = boundFileManagerId(windowState) ?? terminalBindingSummary(windowState).find(tab => tab.boundFileManagerId)?.boundFileManagerId
  return fileManagerTargets.value.find(item => item.id === managerId)?.color
}

function windowKindLabel(windowState: CanvasWindowState) {
  if (windowState.type !== 'file-manager') return ''
  const index = fileManagerTargets.value.findIndex(item => item.id === windowState.id)
  return fileManagerDisplayLabel(windowState, index >= 0 ? index : 0)
}

function bindingButtonStyle(windowState: CanvasWindowState) {
  return { '--binding-color': windowBindingColor(windowState) || '#98a2b3' }
}

function createBindingLink(source: CanvasWindowState, target: CanvasWindowState, color: string, label: string, id: string): BindingLink {
  return {
    id,
    startX: source.x + source.width,
    startY: bindingAnchorY(source),
    endX: target.x,
    endY: bindingAnchorY(target),
    color,
    label
  }
}

function bindingAnchorY(windowState: CanvasWindowState) {
  const visibleHeight = windowState.minimized ? CANVAS_WINDOW_MINIMIZED_HEIGHT : windowState.height
  return windowState.y + Math.min(visibleHeight, BINDING_ANCHOR_MAX_HEIGHT) / 2
}

function bindingPoint(value: number, axis: 'x' | 'y') {
  const bounds = bindingLayerBounds.value
  return axis === 'x' ? value - bounds.left : value - bounds.top
}

function bindingPath(link: BindingLink) {
  const x1 = bindingPoint(link.startX, 'x')
  const y1 = bindingPoint(link.startY, 'y')
  const x2 = bindingPoint(link.endX, 'x')
  const y2 = bindingPoint(link.endY, 'y')

  // 计算距离和控制点
  const dx = x2 - x1
  const dy = y2 - y1
  const distance = Math.sqrt(dx * dx + dy * dy)

  // 类似 iPad 台前调度的优雅曲线
  // 使用更大的水平偏移和轻微的垂直弧度
  const horizontalCurve = Math.max(60, Math.min(200, distance * 0.4))
  const verticalOffset = Math.max(20, Math.min(60, Math.abs(dy) * 0.15))

  // 创建平滑的 S 形曲线
  const cp1x = x1 + horizontalCurve
  const cp1y = y1 + (dy > 0 ? verticalOffset : -verticalOffset)
  const cp2x = x2 - horizontalCurve
  const cp2y = y2 - (dy > 0 ? verticalOffset : -verticalOffset)

  return `M ${x1} ${y1} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${x2} ${y2}`
}

function bindingLabelX(link: BindingLink) {
  return (bindingPoint(link.startX, 'x') + bindingPoint(link.endX, 'x')) / 2
}

function bindingLabelY(link: BindingLink) {
  return (bindingPoint(link.startY, 'y') + bindingPoint(link.endY, 'y')) / 2 - 8
}

function tailPath(windowState: CanvasWindowState) {
  const path = windowState.payload?.path
  return typeof path === 'string' ? path : ''
}

function tailLines(windowState: CanvasWindowState) {
  const lines = windowState.payload?.lines
  if (typeof lines === 'number') return lines
  return preferences.value.logs?.tailLines ?? 20
}

function updateWindow(windowId: string, updater: (windowState: CanvasWindowState) => void) {
  const board = activeBoard.value
  const windowState = board?.windows.find(item => item.id === windowId)
  if (!board || !windowState) return
  updater(windowState)
  board.updatedAt = new Date().toISOString()
}

function markDirty() {
  saveStatus.value = 'dirty'
  saveLocalBoards()
  if (saveTimer) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    void persistBoardState()
  }, 900)
}

async function persistBoardState() {
  if (!hydrated) return
  if (saveTimer) window.clearTimeout(saveTimer)
  saveStatus.value = 'saving'
  try {
    await saveCanvasBoards(boardState.value)
    await saveClientPreferencesPatch(preferences.value, { immediate: true })
    saveStatus.value = 'saved'
  } catch (error) {
    saveStatus.value = 'error'
    ElMessage.error(error instanceof Error ? error.message : t('message.saveFailed'))
  }
}

function saveLocalBoards() {
  try {
    window.localStorage.setItem(scopedLocalStorageKey(LOCAL_BOARDS_KEY), JSON.stringify(boardState.value))
  } catch {
    // Server cache remains the source of truth when local storage is unavailable.
  }
}

function readLocalBoards() {
  try {
    const raw = window.localStorage.getItem(scopedLocalStorageKey(LOCAL_BOARDS_KEY)) ?? window.localStorage.getItem(LOCAL_BOARDS_KEY)
    return raw ? normalizeBoardState(JSON.parse(raw) as CanvasBoardState) : null
  } catch {
    return null
  }
}

function normalizeBoardState(value: CanvasBoardState | null | undefined): CanvasBoardState {
  if (!value || !Array.isArray(value.boards) || value.boards.length === 0) return createDefaultBoardState()
  const boards = value.boards.map((board, index) => ({
    ...board,
    id: board.id || `board_${newIdToken()}`,
    name: board.name || `${t('canvas.board')} ${index + 1}`,
    viewport: {
      x: Number.isFinite(board.viewport?.x) ? board.viewport.x : DEFAULT_CANVAS_VIEWPORT.x,
      y: Number.isFinite(board.viewport?.y) ? board.viewport.y : DEFAULT_CANVAS_VIEWPORT.y,
      zoom: clamp(board.viewport?.zoom ?? 1, 0.25, 2.5)
    },
    windows: Array.isArray(board.windows) ? board.windows.map(normalizeWindowState) : []
  }))
  const normalized: CanvasBoardState = {
    version: 1,
    activeBoardId: boards.some(board => board.id === value.activeBoardId) ? value.activeBoardId : boards[0].id,
    boards
  }
  return sanitizeCanvasBoardStateForWorkspace(normalized, systemInfo.value?.workspace_root ?? '')
}

function normalizeWindowState(windowState: CanvasWindowState): CanvasWindowState {
  const type = normalizeWindowType(windowState.type) ?? 'file-manager'
  const payload = windowState.payload ?? defaultPayload(type)
  return {
    ...windowState,
    id: windowState.id || `window_${newIdToken()}`,
    type,
    title: type === 'file-manager' ? initialWindowTitle(type, payload) : (windowState.title || windowTitle(type)),
    x: Number.isFinite(windowState.x) ? windowState.x : 0,
    y: Number.isFinite(windowState.y) ? windowState.y : 0,
    width: Math.max(280, Number.isFinite(windowState.width) ? windowState.width : 520),
    height: Math.max(180, Number.isFinite(windowState.height) ? windowState.height : 340),
    zIndex: Number.isFinite(windowState.zIndex) ? windowState.zIndex : 1,
    payload
  }
}

function normalizeWindowType(type: string): CanvasWindowType | null {
  if (type === 'file-manager' || type === 'queue' || type === 'tail' || type === 'preview' || type === 'terminal' || type === 'plugin') return type
  return null
}

function defaultPayload(type: CanvasWindowType, board?: CanvasBoard) {
  if (type === 'file-manager') {
    const number = board ? nextFileManagerNumber(board) : 1
    return {
      path: systemInfo.value?.workspace_root ?? '',
      bindingNumber: number,
      bindingColor: BINDING_COLORS[(number - 1) % BINDING_COLORS.length]
    }
  }
  if (type === 'tail') return { path: '', lines: preferences.value.logs?.tailLines ?? 20 }
  if (type === 'terminal') return { tabBindings: [] }
  return {}
}

function windowTitle(type: CanvasWindowType) {
  const labels: Record<CanvasWindowType, string> = {
    'file-manager': t('canvas.window.fileManager'),
    queue: t('canvas.window.queue'),
    tail: t('canvas.window.tail'),
    preview: t('canvas.window.preview'),
    terminal: t('canvas.window.terminal'),
    plugin: t('canvas.window.plugin')
  }
  return labels[type]
}

function initialWindowTitle(type: CanvasWindowType, payload: Record<string, unknown>) {
  if (type !== 'file-manager') return windowTitle(type)
  const path = payload.path
  return typeof path === 'string' ? (fileManagerDirectoryName(path) || windowTitle(type)) : windowTitle(type)
}

function nextZIndex(board: CanvasBoard) {
  return Math.max(0, ...board.windows.map(item => item.zIndex || 0)) + 1
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function normalizePath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '')
}
</script>
