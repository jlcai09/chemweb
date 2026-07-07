<template>
  <div
    ref="workspaceRef"
    class="workspace-view"
    :class="{ 'is-resizing': Boolean(activeResize), 'is-drag-upload': dragUploadActive }"
    :style="workspaceStyle"
    @dragenter="handleWorkspaceDragEnter"
    @dragover="handleWorkspaceDragOver"
    @dragleave="handleWorkspaceDragLeave"
    @drop="handleWorkspaceDrop"
  >
    <section ref="leftPaneRef" class="workspace-left">
      <div class="path-bar">
        <el-input
          v-model="pathInput"
          class="path-input"
          size="small"
          clearable
          spellcheck="false"
          @keyup.enter="openPathFromInput"
          @clear="openDirectory()"
        >
          <template #append>
            <el-tooltip :content="t('toolbar.go')" placement="bottom" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
              <el-button :icon="ArrowRight" @click="openPathFromInput" />
            </el-tooltip>
          </template>
        </el-input>
      </div>

      <FileToolbar
        :selected-items="selectedItems"
        :can-go-back="canGoBack"
        :history-entries="directoryHistoryEntries"
        :show-hidden-files="showHiddenFiles"
        @refresh="loadDirectory(currentPath, { refresh: true })"
        @go-back="goBack"
        @history-select="openHistoryPath"
        @create-file="promptCreateFile"
        @mkdir="promptMkdir"
        @upload="handleUpload"
        @download="downloadSelected"
        @rename="promptRename"
        @delete="confirmDelete"
        @update:show-hidden-files="setShowHiddenFiles"
      />

      <div class="file-table-shell">
        <FileTree
          :items="visibleItems"
          :parent-path="listing?.parent"
          :selected-items="selectedItems"
          :loading="loadingFiles"
          :preview-providers="previewProviders"
          :system-icon-provider="launcherSystemIconProvider"
          @selection-change="handleSelectionChange"
          @context-menu="openFileContextMenu"
          @move-items="handleMoveItems"
          @open="openItem"
        />
      </div>
    </section>

    <div
      class="pane-splitter pane-splitter-vertical left-splitter"
      role="separator"
      :aria-label="t('resize.fileList')"
      tabindex="0"
      @pointerdown="startColumnResize('left', $event)"
      @dblclick="resetColumnLayout"
    />

    <section ref="mainPaneRef" class="workspace-main">
      <TerminalPanel
        class="workspace-terminal"
        :initial-cwd="terminalInitialPath"
        :workspace-root="systemInfo?.workspace_root"
        :current-file-manager-path="currentPath"
        :layout-version="terminalLayoutVersion"
        :transfer-upload-handler="handleTerminalTransferUpload"
        :defer-init="!workspacePreferencesReady"
        :initial-bindings="workspaceTerminalBindings"
        @cwd-change="openDirectoryFromTerminal"
        @refresh-file-manager="refreshFileManagerFromTerminal"
        @binding-summary-change="saveWorkspaceTerminalBindings"
      />
    </section>

    <div
      class="pane-splitter pane-splitter-vertical right-splitter"
      role="separator"
      :aria-label="t('resize.previewSide')"
      tabindex="0"
      @pointerdown="startColumnResize('right', $event)"
      @dblclick="resetColumnLayout"
    />

    <aside ref="sidePaneRef" class="workspace-side" :style="sideStyle">
      <section class="side-work-panel">
        <div class="side-panel-tabbar">
          <button
            v-for="panel in workPanels"
            :key="panel.id"
            class="side-panel-tab"
            :class="{ 'is-active': panel.id === activeWorkPanelId }"
            type="button"
            draggable="true"
            @click="activeWorkPanelId = panel.id"
            @dragstart="onWorkPanelDragStart(panel.id, $event)"
            @dragover="onWorkPanelDragOver(panel.id, $event)"
            @drop="onWorkPanelDrop(panel.id, $event)"
          >
            <span>{{ panelTitle(panel) }}</span>
            <el-tooltip :content="t('panel.close')" placement="bottom" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
              <el-button
                class="side-panel-close"
                :icon="Close"
                size="small"
                text
                @click.stop="closeWorkPanel(panel.id)"
              />
            </el-tooltip>
          </button>
          <el-dropdown trigger="click" @command="openWorkPanelCommand">
            <el-button class="side-panel-add" :icon="Plus" circle size="small" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="builtin:preview">{{ t('preview.type.preview') }}</el-dropdown-item>
                <el-dropdown-item command="builtin:queue">{{ systemInfo?.scheduler?.toUpperCase() ?? t('queue.title') }}</el-dropdown-item>
                <el-dropdown-item
                  v-for="item in pluginPanelCommands"
                  :key="item.command"
                  :command="item.command"
                  divided
                >
                  {{ item.title }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
        <div class="side-panel-body">
          <FilePreview
            v-show="activeWorkPanel?.kind === 'preview'"
            :file="preview"
            :ase-structure="asePreview"
            :mode="previewMode"
            :loading="previewLoading"
            :structure-candidate="structurePreviewCandidate"
            :structure-error="previewError"
            @update:mode="setPreviewMode"
            @refresh="refreshPreview"
            @save="savePreview"
            @dragover="handlePreviewDragOver"
            @drop="handlePreviewDrop"
          />
          <QueueStatus
            v-if="activeWorkPanel?.kind === 'queue'"
            class="side-queue"
            :initial-interval="5"
            :workspace-root="systemInfo?.workspace_root"
            @open-workdir="openQueueWorkdir"
          />
          <iframe
            v-else-if="activeWorkPanel?.kind === 'plugin'"
            class="plugin-panel-frame"
            :src="activeWorkPanel.assetUrl"
            :title="activeWorkPanel.title"
            @load="handlePluginFrameLoad($event, activeWorkPanel)"
          />
          <div v-if="!activeWorkPanel" class="empty-state">
            <el-empty :description="t('panel.empty')" />
          </div>
        </div>
      </section>

      <div
        class="pane-splitter pane-splitter-horizontal side-splitter"
        role="separator"
        :aria-label="t('resize.queueLog')"
        tabindex="0"
        @pointerdown="startSideResize"
        @dblclick="resetSideLayout"
      />

      <LogViewer class="side-log" :path="logPath" :initial-lines="workspaceTailLines" @lines-change="setWorkspaceTailLines" />
    </aside>

    <div v-if="dragUploadActive" class="workspace-drop-overlay" aria-live="polite">
      <el-icon><UploadFilled /></el-icon>
      <span>{{ t('file.dropUpload') }}</span>
    </div>

    <div
      v-if="uploadState.active"
      class="upload-progress-widget"
      :class="{ 'is-open': uploadProgressOpen }"
      @mouseenter="uploadProgressOpen = true"
      @mouseleave="uploadProgressOpen = false"
    >
      <button
        class="upload-progress-orb"
        type="button"
        :aria-label="t('upload.progress')"
        @focus="uploadProgressOpen = true"
        @blur="uploadProgressOpen = false"
      >
        <span class="upload-progress-ring" />
        <span class="upload-progress-value">{{ uploadPercent }}%</span>
      </button>
      <div v-if="uploadProgressOpen" class="upload-progress-panel" role="status">
        <div class="upload-progress-title">{{ t('upload.progress') }}</div>
        <div class="upload-progress-file" :title="uploadState.currentFile">{{ uploadState.currentFile }}</div>
        <div class="upload-progress-bar" aria-hidden="true">
          <span :style="{ width: `${uploadPercent}%` }" />
        </div>
        <div class="upload-progress-meta">
          <span>{{ uploadPercent }}%</span>
          <span>{{ uploadSpeedLabel }}</span>
          <span>{{ uploadState.done }}/{{ uploadState.totalFiles }}</span>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="uploadConflictDialog.visible" class="upload-conflict-backdrop">
        <div class="upload-conflict-dialog" role="dialog" aria-modal="true">
          <div class="upload-conflict-title">
            {{ uploadConflictDialog.mode === 'move' ? t('move.conflictTitle') : t('upload.conflictTitle') }}
          </div>
          <p>
            {{
              uploadConflictDialog.mode === 'move'
                ? t('move.conflictMessage', { name: uploadConflictDialog.name })
                : t('upload.conflictMessage', { name: uploadConflictDialog.name })
            }}
          </p>
          <label class="upload-conflict-apply">
            <input v-model="uploadConflictDialog.applyAll" type="checkbox" />
            <span>{{ t('upload.applyAll') }}</span>
          </label>
          <div class="upload-conflict-actions">
            <el-button type="primary" @click="chooseUploadConflict('overwrite')">{{ t('upload.overwrite') }}</el-button>
            <el-button @click="chooseUploadConflict('skip')">{{ t('upload.skip') }}</el-button>
            <el-button @click="chooseUploadConflict('suffix')">{{ t('upload.renameNew') }}</el-button>
            <el-button @click="chooseUploadConflict('cancel')">{{ t('common.cancel') }}</el-button>
          </div>
        </div>
      </div>

      <div
        v-if="contextMenu.visible"
        class="file-context-menu"
        :class="{ 'opens-left': contextMenu.opensLeft }"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        role="menu"
        @click.stop
        @contextmenu.prevent
      >
        <button
          v-if="contextMenu.item && canLauncherOpenItem(contextMenu.item, 'default')"
          class="context-menu-item"
          type="button"
          role="menuitem"
          @click="openContextWithLocalApp"
        >
          <el-icon>
            <component :is="contextMenu.item?.type === 'directory' ? FolderOpened : Document" />
          </el-icon>
          <span>{{ t('context.openLocal') }}</span>
          <span />
        </button>
        <button
          v-if="contextMenu.item && canLauncherOpenItem(contextMenu.item, 'text')"
          class="context-menu-item"
          type="button"
          role="menuitem"
          @click="openContextWithNotepad"
        >
          <el-icon><EditPen /></el-icon>
          <span>{{ t('context.openNotepad') }}</span>
          <span />
        </button>
        <button class="context-menu-item" type="button" role="menuitem" @click="copyContextPath">
          <el-icon><CopyDocument /></el-icon>
          <span>{{ t('context.copyPath') }}</span>
          <span />
        </button>
        <div v-if="contextMenu.item?.type === 'file'" class="context-menu-item has-submenu" role="menuitem" tabindex="0">
          <el-icon><Promotion /></el-icon>
          <span>{{ t('context.submitQueue') }}</span>
          <el-icon class="submenu-arrow"><ArrowRight /></el-icon>
          <div class="context-submenu" role="menu">
            <button class="context-menu-item" type="button" role="menuitem" @click="submitContextJob('qsub')">
              qsub
            </button>
            <button class="context-menu-item" type="button" role="menuitem" @click="submitContextJob('sbatch')">
              sbatch
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Close, CopyDocument, Document, EditPen, FolderOpened, Plus, Promotion, UploadFilled } from '@element-plus/icons-vue'
import {
  deletePath,
  downloadArchive,
  listFiles,
  makeDirectory,
  movePaths,
  readFile,
  renamePath,
  writeFile,
  type DirectoryListing,
  type FileItem,
  type FileReadResponse
} from '../api/files'
import { API_BASE, apiUrl, downloadUrl, getAuthToken, request } from '../api/http'
import { apiErrorMessage, localizeBackendMessage } from '../api/apiMessages'
import {
  configureClientPreferencesScope,
  getClientPreferences,
  loadClientPreferencesState,
  saveClientPreferencesPatch
} from '../api/clientPreferences'
import { hasChemSSHFileDrag, readChemSSHFileDrag } from '../api/fileDrag'
import { moveApiErrorMessage, prepareMoveEntries } from '../api/fileMove'
import {
  providerMatchesItem,
  type FilePreviewProvider,
  type PreviewProbeResponse
} from '../api/filePreviewProviders'
import { isStructurePreviewItem } from '../api/fileTypes'
import { submitJob, type SubmitCommand } from '../api/jobs'
import {
  isPathInsideWorkspace,
  launcherFileIconUrl,
  loadLauncherBridgeCapabilities,
  openWithLocalApp,
  openWithNotepad,
  parentDirectoryPath,
  type LauncherBridgeCapabilities,
  type LauncherBridgeSyncEvent
} from '../api/launcherBridge'
import { activatePlugin, deactivatePlugin, listPlugins, type PluginManifest } from '../api/plugins'
import {
  confirmLargePreview,
  isLargePreviewError,
  normalizeTextLineEndings,
  previewApiErrorMessage
} from '../api/previewUtils'
import { ASE_STRUCTURE_SOURCE, readStructurePreview } from '../api/structures'
import {
  collectDropUploadEntries,
  hasFileDrag,
  joinDisplayPath,
  SAFE_UPLOAD_SEGMENT_RE,
  setUploadDropEffect,
  type UploadConflictResolution
} from '../api/uploadEntries'
import {
  createWorkspaceScope,
  sanitizeTerminalTabs,
  workspacePathOrRoot
} from '../api/workspaceScope'
import type { AsePreviewResponse, StructureSource } from '../types/structure'
import type { CanvasTerminalTabBinding } from '../types/canvasBoard'
import FilePreview from '../components/FilePreview.vue'
import FileToolbar from '../components/FileToolbar.vue'
import FileTree from '../components/FileTree.vue'
import LogViewer from '../components/LogViewer.vue'
import QueueStatus from '../components/QueueStatus.vue'
import TerminalPanel from '../components/terminal/TerminalPanel.vue'
import { t } from '../i18n'
import { useWorkspaceLayout } from '../composables/useWorkspaceLayout'
import { useWorkspaceUpload } from '../composables/useWorkspaceUpload'
import { useSystemStore } from '../stores/system'

type OpenPathRequest = {
  path: string
  id: number
}

const props = defineProps<{
  openPathRequest?: OpenPathRequest | null
}>()

const systemStore = useSystemStore()
const { systemInfo: systemStoreInfo, syncEvents: systemStoreSyncEvents } = storeToRefs(systemStore)
const systemInfo = computed(() => systemStoreInfo.value)
const syncEvents = computed(() => systemStoreSyncEvents.value)

const listing = shallowRef<DirectoryListing | null>(null)
const currentPath = ref('')
const pathInput = ref('')
const selectedItems = ref<FileItem[]>([])
const selectedItem = ref<FileItem | null>(null)
const showHiddenFiles = ref(false)
const workspaceTailLines = ref(20)
const preview = ref<FileReadResponse | null>(null)
const asePreview = ref<AsePreviewResponse | null>(null)
const previewMode = ref<PreviewMode>('text')
const previewCandidate = ref<FileItem | null>(null)
const previewError = ref<string | null>(null)
const loadingFiles = ref(false)
const previewLoading = ref(false)
const dragUploadDepth = ref(0)
const directoryHistory = ref<string[]>([])
const forcedLargeTextPreviews = new Set<string>()
const forcedLargeStructurePreviews = new Set<string>()

type WorkPanelKind = 'preview' | 'queue' | 'plugin'
type PreviewMode = 'structure' | 'text'
type WorkPanel = {
  id: string
  kind: WorkPanelKind
  title: string
  pluginId?: string
  panelId?: string
  assetUrl?: string
  apiBase?: string
}
type ContextMenuState = {
  visible: boolean
  x: number
  y: number
  opensLeft: boolean
  item: FileItem | null
}

const workspaceRef = ref<HTMLElement | null>(null)
const leftPaneRef = ref<HTMLElement | null>(null)
const mainPaneRef = ref<HTMLElement | null>(null)
const sidePaneRef = ref<HTMLElement | null>(null)
const workPanels = ref<WorkPanel[]>([
  { id: 'builtin:preview', kind: 'preview', title: t('preview.type.preview') },
  { id: 'builtin:queue', kind: 'queue', title: t('queue.title') }
])
const activeWorkPanelId = ref('builtin:preview')
const pluginManifests = ref<PluginManifest[]>([])
const previewProviders = ref<FilePreviewProvider[]>([])
const currentStructureSource = ref<StructureSource>(ASE_STRUCTURE_SOURCE)
const launcherBridgeCapabilities = ref<LauncherBridgeCapabilities | null>(null)
const contextMenu = ref<ContextMenuState>({
  visible: false,
  x: 0,
  y: 0,
  opensLeft: false,
  item: null
})
let previewRequestSerial = 0
let structurePreviewAbortController: AbortController | null = null
let mounted = false
let initialized = false
const workspacePreferencesReady = ref(false)

const CONTEXT_MENU_WIDTH = 190
const CONTEXT_MENU_HEIGHT = 156
const DIRECTORY_HISTORY_LIMIT = 20

const dragUploadActive = computed(() => dragUploadDepth.value > 0)
const canGoBack = computed(() => directoryHistory.value.length > 0)
const directoryHistoryEntries = computed(() => directoryHistory.value.map(path => ({
  path,
  label: directoryHistoryLabel(path)
})))

const terminalInitialPath = computed(() => workspacePathOrRoot(currentPath.value, systemInfo.value?.workspace_root))

const launcherSystemIconProvider = computed(() => ({
  enabled: Boolean(
    launcherBridgeCapabilities.value?.enabled &&
    launcherBridgeCapabilities.value.features.system_icons &&
    launcherBridgeCapabilities.value.endpoints.icon
  ),
  iconUrl: (item: FileItem) => launcherFileIconUrl(item, 16)
}))

const {
  activeResize,
  leftPaneWidth,
  sidePaneWidth,
  sideQueueHeight,
  terminalLayoutVersion,
  workspaceStyle,
  sideStyle,
  startColumnResize,
  startSideResize,
  resetColumnLayout,
  resetSideLayout,
  notifyTerminalLayoutChanged,
  stopResize
} = useWorkspaceLayout(workspaceRef, leftPaneRef, sidePaneRef, saveWorkspacePreferences, closeContextMenu)

const {
  uploadState,
  uploadConflictDialog,
  uploadProgressOpen,
  uploadPercent,
  uploadSpeedLabel,
  handleUpload,
  handleUploadEntries,
  handleTerminalTransferUpload,
  promptUploadConflict,
  promptMoveConflict,
  chooseUploadConflict
} = useWorkspaceUpload(currentPath, () => loadDirectory(currentPath.value, { refresh: true }))

const workspaceTerminalBindings = computed<CanvasTerminalTabBinding[]>(() => {
  const tabs = getClientPreferences().terminal?.tabs
  if (!Array.isArray(tabs)) return []
  const workspaceRoot = systemInfo.value?.workspace_root ?? ''
  const normalized: CanvasTerminalTabBinding[] = tabs.flatMap(item => {
    if (!item || typeof item !== 'object') return []
    const binding = item as Partial<CanvasTerminalTabBinding>
    if (typeof binding.tabId !== 'string') return []
    return [{
      tabId: binding.tabId,
      title: typeof binding.title === 'string' ? binding.title : '',
      cwd: typeof binding.cwd === 'string' ? binding.cwd : '',
      syncMode: binding.syncMode === 'follow' || binding.syncMode === 'bidirectional' ? binding.syncMode : 'off',
      boundFileManagerId: typeof binding.boundFileManagerId === 'string' ? binding.boundFileManagerId : null,
      active: binding.active === true
    }]
  })
  return sanitizeTerminalTabs(normalized, workspaceRoot) ?? []
})

function saveWorkspaceTerminalBindings(summary: CanvasTerminalTabBinding[]) {
  const tabs = sanitizeTerminalTabs(summary, systemInfo.value?.workspace_root ?? '') ?? []
  void saveClientPreferencesPatch({
    version: 1,
    terminal: { tabs }
  }).catch(() => undefined)
}

function applyWorkspacePreferences() {
  const workspace = getClientPreferences().workspace
  if (!workspace) return
  leftPaneWidth.value = typeof workspace.fileTreeWidth === 'number' ? workspace.fileTreeWidth : null
  sidePaneWidth.value = typeof workspace.sidePaneWidth === 'number' ? workspace.sidePaneWidth : null
  sideQueueHeight.value = typeof workspace.queueHeight === 'number' ? workspace.queueHeight : null
  showHiddenFiles.value = Boolean(workspace.showHiddenFiles)
  workspaceTailLines.value = getClientPreferences().logs?.tailLines ?? 20
  if (workspace.activeWorkPanelId && workPanels.value.some(panel => panel.id === workspace.activeWorkPanelId)) {
    activeWorkPanelId.value = workspace.activeWorkPanelId
  }
}

function saveWorkspacePreferences() {
  if (!workspacePreferencesReady.value) return
  void saveClientPreferencesPatch({
    version: 1,
    logs: {
      tailLines: workspaceTailLines.value
    },
    workspace: {
      fileTreeWidth: leftPaneWidth.value,
      sidePaneWidth: sidePaneWidth.value,
      queueHeight: sideQueueHeight.value,
      currentPath: currentPath.value,
      showHiddenFiles: showHiddenFiles.value,
      activeWorkPanelId: activeWorkPanelId.value
    }
  }).catch(() => undefined)
}

function setWorkspaceTailLines(lines: number) {
  workspaceTailLines.value = lines
  saveWorkspacePreferences()
}

const visibleItems = computed(() => (listing.value?.items ?? []).filter(item => showHiddenFiles.value || !isHiddenItem(item)))
const logPath = computed(() => selectedItems.value.find(item => item.type === 'file')?.path ?? null)
const structurePreviewCandidate = computed(() => {
  if (previewCandidate.value) return isStructureCandidate(previewCandidate.value) || providerLightMatchExists(previewCandidate.value)
  if (previewMode.value === 'structure') return Boolean(asePreview.value)
  return preview.value?.preview_type === 'structure' || false
})
const activeWorkPanel = computed(() => workPanels.value.find(panel => panel.id === activeWorkPanelId.value) ?? null)
const pluginPanelCommands = computed(() =>
  pluginManifests.value.flatMap(plugin =>
    (plugin.panels ?? []).map(panel => ({
      command: `plugin:${plugin.id}:${panel.id}`,
      title: panel.title || plugin.name || plugin.id,
      plugin,
      panel
    }))
  )
)

function setSelection(items: FileItem[], primary: FileItem | null) {
  selectedItems.value = items
  selectedItem.value = primary ?? items[items.length - 1] ?? null
}

function isHiddenItem(item: FileItem) {
  return item.name.startsWith('.')
}

function isStructureCandidate(item: FileItem) {
  return isStructurePreviewItem(item)
}

function panelTitle(panel: WorkPanel) {
  if (panel.kind === 'preview') return t('preview.type.preview')
  if (panel.kind === 'queue') return systemInfo.value?.scheduler?.toUpperCase() ?? t('queue.title')
  return panel.title
}

function syncBuiltinPanelTitles() {
  workPanels.value = workPanels.value.map(panel => {
    if (panel.kind === 'preview') return { ...panel, title: t('preview.type.preview') }
    if (panel.kind === 'queue') return { ...panel, title: systemInfo.value?.scheduler?.toUpperCase() ?? t('queue.title') }
    return panel
  })
}

function openBuiltinPanel(kind: 'preview' | 'queue') {
  const id = `builtin:${kind}`
  if (!workPanels.value.some(panel => panel.id === id)) {
    workPanels.value.push({
      id,
      kind,
      title: kind === 'preview' ? t('preview.type.preview') : (systemInfo.value?.scheduler?.toUpperCase() ?? t('queue.title'))
    })
  }
  activeWorkPanelId.value = id
}

function moveWorkPanel(fromId: string, toId: string) {
  if (fromId === toId) return
  const fromIndex = workPanels.value.findIndex(panel => panel.id === fromId)
  const toIndex = workPanels.value.findIndex(panel => panel.id === toId)
  if (fromIndex < 0 || toIndex < 0) return
  const next = [...workPanels.value]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  workPanels.value = next
}

function closeWorkPanel(panelId: string) {
  const panel = workPanels.value.find(item => item.id === panelId)
  workPanels.value = workPanels.value.filter(item => item.id !== panelId)
  if (panel?.kind === 'plugin' && panel.pluginId) {
    unregisterProvidersByPlugin(panel.pluginId)
    if (!workPanels.value.some(item => item.kind === 'plugin' && item.pluginId === panel.pluginId)) {
      void deactivatePlugin(panel.pluginId).catch(() => undefined)
    }
  }
  if (activeWorkPanelId.value === panelId) {
    activeWorkPanelId.value = workPanels.value[workPanels.value.length - 1]?.id ?? ''
  }
}

function onWorkPanelDragStart(panelId: string, event: DragEvent) {
  if (!event.dataTransfer) return
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('application/x-chemssh-work-panel', panelId)
}

function onWorkPanelDragOver(panelId: string, event: DragEvent) {
  const types = Array.from(event.dataTransfer?.types ?? [])
  if (!types.includes('application/x-chemssh-work-panel')) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

function onWorkPanelDrop(panelId: string, event: DragEvent) {
  const fromId = event.dataTransfer?.getData('application/x-chemssh-work-panel')
  if (!fromId) return
  event.preventDefault()
  moveWorkPanel(fromId, panelId)
}

async function openWorkPanelCommand(command: string | number | object) {
  if (typeof command !== 'string') return
  if (command === 'builtin:preview') {
    openBuiltinPanel('preview')
    return
  }
  if (command === 'builtin:queue') {
    openBuiltinPanel('queue')
    return
  }
  if (!command.startsWith('plugin:')) return
  const [, pluginId, panelId] = command.split(':')
  await openPluginPanel(pluginId, panelId)
}

async function loadPluginManifests() {
  try {
    const response = await listPlugins()
    pluginManifests.value = response.plugins
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('plugin.loadFailed'))
  }
}

async function openPluginPanel(pluginId: string, panelId: string) {
  const manifest = pluginManifests.value.find(plugin => plugin.id === pluginId)
  const panel = manifest?.panels.find(item => item.id === panelId)
  if (!manifest || !panel) return

  const existing = workPanels.value.find(item => item.kind === 'plugin' && item.pluginId === pluginId && item.panelId === panelId)
  if (existing && panel.singleton !== false) {
    activeWorkPanelId.value = existing.id
    return
  }

  try {
    const runtime = await activatePlugin(pluginId)
    const id = `plugin:${pluginId}:${panelId}:${Date.now()}`
    workPanels.value.push({
      id,
      kind: 'plugin',
      title: panel.title || manifest.name || pluginId,
      pluginId,
      panelId,
      assetUrl: apiUrl(runtime.asset_url),
      apiBase: runtime.api_base
    })
    activeWorkPanelId.value = id
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('plugin.activateFailed'))
  }
}

function handlePluginFrameLoad(event: Event, panel: WorkPanel | null) {
  const frame = event.target instanceof HTMLIFrameElement ? event.target : null
  if (!frame?.contentWindow || !panel || !panel.pluginId || !panel.panelId) return
  frame.contentWindow.postMessage({
    type: 'chemssh:plugin:init',
    version: 1,
    pluginId: panel.pluginId,
    panelId: panel.panelId,
    instanceId: panel.id,
    locale: window.navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en',
    theme: 'light',
    apiBase: panel.apiBase,
    assetBase: panel.assetUrl,
    authToken: getAuthToken(),
    initialFile: previewCandidate.value
      ? { path: previewCandidate.value.path, name: previewCandidate.value.name }
      : null
  }, new URL(panel.assetUrl ?? window.location.href, window.location.href).origin)
}

function handlePluginMessage(event: MessageEvent) {
  if (!isTrustedPluginOrigin(event.origin)) return
  const data = event.data as { type?: string; provider?: FilePreviewProvider; providerId?: string }
  if (data?.type === 'chemssh:file-manager:register-preview-provider' && data.provider) {
    registerPreviewProvider(data.provider)
  } else if (data?.type === 'chemssh:file-manager:unregister-preview-provider' && data.providerId) {
    unregisterPreviewProvider(data.providerId)
  }
}

function isTrustedPluginOrigin(origin: string) {
  if (origin === window.location.origin) return true
  if (!API_BASE) return false
  try {
    return origin === new URL(API_BASE, window.location.href).origin
  } catch {
    return false
  }
}

function registerPreviewProvider(provider: FilePreviewProvider) {
  previewProviders.value = [
    ...previewProviders.value.filter(item => item.id !== provider.id),
    provider
  ].sort((left, right) => (right.priority ?? 0) - (left.priority ?? 0))
}

function unregisterPreviewProvider(providerId: string) {
  previewProviders.value = previewProviders.value.filter(provider => provider.id !== providerId)
}

function unregisterProvidersByPlugin(pluginId: string) {
  previewProviders.value = previewProviders.value.filter(provider => provider.pluginId !== pluginId)
}

function providerLightMatchExists(item: FileItem) {
  return previewProviders.value.some(provider => providerMatchesItem(provider, item))
}

async function resolvePreviewProvider(item: FileItem) {
  const candidates = previewProviders.value.filter(provider => providerMatchesItem(provider, item))
  for (const provider of candidates) {
    if (!provider.probe?.apiPath || !provider.apiBase) return provider
    try {
      const response = await request<PreviewProbeResponse>(`${provider.apiBase}${provider.probe.apiPath}`, {
        method: provider.probe.method ?? 'POST',
        body: JSON.stringify({ path: item.path, item })
      })
      if (response.can_preview) return provider
    } catch {
      // Ignore a failed plugin probe and continue with the next provider.
    }
  }
  return null
}

function providerStructureSource(provider: FilePreviewProvider): StructureSource | null {
  const source = provider.open?.structureSource
  if (!source?.apiBase) return null
  return source
}

function setShowHiddenFiles(value: boolean) {
  showHiddenFiles.value = value
  saveWorkspacePreferences()
  if (value) return
  const visibleSelection = selectedItems.value.filter(item => !isHiddenItem(item))
  const primary = selectedItem.value && !isHiddenItem(selectedItem.value) ? selectedItem.value : (visibleSelection[visibleSelection.length - 1] ?? null)
  setSelection(visibleSelection, primary)
}

function handleSelectionChange(items: FileItem[], primary: FileItem | null) {
  setSelection(items, primary)
}

async function loadDirectory(path?: string | null, options: { recordHistory?: boolean; refresh?: boolean } = {}) {
  const previousPath = currentPath.value
  loadingFiles.value = true
  try {
    listing.value = await listFiles(path ?? undefined, { refresh: options.refresh })
    currentPath.value = listing.value.path
    pathInput.value = listing.value.path
    if (options.recordHistory) recordDirectoryHistory(previousPath, currentPath.value)
    saveWorkspacePreferences()
    setSelection([], null)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.directoryLoadFailed'))
  } finally {
    loadingFiles.value = false
  }
}

async function openDirectory(path?: string | null) {
  await loadDirectory(path, { recordHistory: true })
}

async function openQueueWorkdir(path: string) {
  await openDirectory(path)
}

async function openDirectoryFromTerminal(path: string) {
  if (!path || path === currentPath.value) return
  await openDirectory(path)
}

async function refreshFileManagerFromTerminal(_managerId: string | null, path: string) {
  const targetPath = workspacePathOrRoot(path || currentPath.value, systemInfo.value?.workspace_root)
  if (!targetPath) return
  await loadDirectory(targetPath, { refresh: true })
}

async function openPathFromInput() {
  const value = pathInput.value.trim()
  await loadDirectory(value || undefined, { recordHistory: true })
}

async function goBack() {
  const path = directoryHistory.value[0]
  if (!path) return
  directoryHistory.value = directoryHistory.value.slice(1)
  await loadDirectory(path, { recordHistory: false })
}

async function openHistoryPath(path: string) {
  if (!path) return
  directoryHistory.value = directoryHistory.value.filter(item => item !== path)
  await loadDirectory(path, { recordHistory: false })
}

async function openItem(item: FileItem) {
  setSelection([item], item)
  if (item.type === 'directory') {
    await loadDirectory(item.path, { recordHistory: true })
    return
  }
  await previewFile(item)
}

function recordDirectoryHistory(previousPath: string, nextPath: string) {
  if (!previousPath || previousPath === nextPath) return
  directoryHistory.value = [previousPath, ...directoryHistory.value.filter(path => path !== previousPath)].slice(0, DIRECTORY_HISTORY_LIMIT)
}

function directoryHistoryLabel(path: string) {
  return path
}

function abortStructurePreviewRequest() {
  structurePreviewAbortController?.abort()
  structurePreviewAbortController = null
}

function nextStructurePreviewSignal() {
  abortStructurePreviewRequest()
  structurePreviewAbortController = new AbortController()
  return structurePreviewAbortController.signal
}

function isAbortError(error: unknown) {
  return error instanceof Error && error.name === 'AbortError'
}

function throwIfAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('The operation was aborted.', 'AbortError')
}

async function previewFile(itemOrPath: FileItem | string) {
  const path = typeof itemOrPath === 'string' ? itemOrPath : itemOrPath.path
  const item = typeof itemOrPath === 'string'
    ? selectedItem.value?.path === path
      ? selectedItem.value
      : previewCandidate.value?.path === path
        ? previewCandidate.value
        : null
    : itemOrPath

  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  abortStructurePreviewRequest()
  previewLoading.value = true
  previewCandidate.value = item
  previewError.value = null
  try {
    const provider = item ? await resolvePreviewProvider(item) : null
    if (!isCurrentRequest()) return
    const providerSource = provider ? providerStructureSource(provider) : null
    if (providerSource) {
      previewMode.value = 'structure'
      currentStructureSource.value = providerSource
      if (preview.value?.path !== path) preview.value = null
      const structure = await readStructurePreviewWithLargeConfirmation(path, providerSource, nextStructurePreviewSignal())
      if (!isCurrentRequest()) return
      if (structure) {
        asePreview.value = structure
        openBuiltinPanel('preview')
      }
      return
    }

    if (item && isStructureCandidate(item)) {
      previewMode.value = 'structure'
      currentStructureSource.value = ASE_STRUCTURE_SOURCE
      if (preview.value?.path !== path) preview.value = null
      try {
        const structure = await readStructurePreviewWithLargeConfirmation(path, ASE_STRUCTURE_SOURCE, nextStructurePreviewSignal())
        if (!isCurrentRequest()) return
        if (structure) {
          asePreview.value = structure
          openBuiltinPanel('preview')
        }
        return
      } catch (error) {
        if (!isCurrentRequest()) return
        previewError.value = previewApiErrorMessage(error)
        previewMode.value = 'text'
      }
    } else {
      previewMode.value = 'text'
    }

    if (preview.value?.path !== path) preview.value = null
    const file = await readTextPreviewWithLargeConfirmation(path)
    if (!isCurrentRequest()) return
    if (file) {
      preview.value = file
      previewMode.value = 'text'
      openBuiltinPanel('preview')
    }
  } catch (error) {
    if (isCurrentRequest()) {
      preview.value = null
      if (!isAbortError(error)) ElMessage.error(previewApiErrorMessage(error))
    }
  } finally {
    if (isCurrentRequest()) previewLoading.value = false
  }
}

function currentPreviewPath() {
  const candidatePath = previewCandidate.value?.path
  if (candidatePath) return candidatePath
  if (previewMode.value === 'structure') return asePreview.value?.path ?? preview.value?.path ?? null
  return preview.value?.path ?? asePreview.value?.path ?? null
}

function structurePreviewMatches(path: string, source: StructureSource) {
  const structure = asePreview.value
  return Boolean(
    structure &&
    structure.path === path &&
    structure.source?.id === source.id &&
    structure.source?.apiBase === source.apiBase
  )
}

async function loadTextPreview() {
  const path = currentPreviewPath()
  if (!path || preview.value?.path === path) return
  await readTextPreview(path)
}

async function refreshTextPreview() {
  const path = currentPreviewPath()
  if (!path) return
  await readTextPreview(path)
}

async function refreshPreview() {
  if (previewMode.value === 'structure') {
    await refreshStructurePreview()
    return
  }
  await refreshTextPreview()
}

async function readTextPreview(path: string) {
  previewLoading.value = true
  try {
    const file = await readTextPreviewWithLargeConfirmation(path)
    if (file) {
      preview.value = file
      openBuiltinPanel('preview')
    }
  } catch (error) {
    ElMessage.error(previewApiErrorMessage(error))
  } finally {
    previewLoading.value = false
  }
}

async function loadStructurePreview() {
  const path = currentPreviewPath()
  if (!path || structurePreviewMatches(path, currentStructureSource.value)) return
  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  const signal = nextStructurePreviewSignal()
  previewLoading.value = true
  previewError.value = null
  try {
    const structure = await readStructurePreviewWithLargeConfirmation(path, currentStructureSource.value, signal)
    if (!isCurrentRequest()) return
    if (structure) asePreview.value = structure
  } catch (error) {
    if (!isCurrentRequest() || isAbortError(error)) return
    previewError.value = previewApiErrorMessage(error)
    previewMode.value = 'text'
    await loadTextPreview()
  } finally {
    if (isCurrentRequest()) previewLoading.value = false
  }
}

async function refreshStructurePreview() {
  const path = currentPreviewPath()
  if (!path) return
  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  const signal = nextStructurePreviewSignal()
  previewLoading.value = true
  previewError.value = null
  try {
    const structure = await readStructurePreviewWithLargeConfirmation(path, currentStructureSource.value, signal)
    if (!isCurrentRequest()) return
    if (structure) {
      asePreview.value = structure
      openBuiltinPanel('preview')
    }
  } catch (error) {
    if (!isCurrentRequest() || isAbortError(error)) return
    previewError.value = previewApiErrorMessage(error)
    ElMessage.error(previewError.value)
  } finally {
    if (isCurrentRequest()) previewLoading.value = false
  }
}

async function readTextPreviewWithLargeConfirmation(path: string) {
  try {
    return await readFile(path, forcedLargeTextPreviews.has(path))
  } catch (error) {
    if (!isLargePreviewError(error, 'FILE_TOO_LARGE')) throw error
    const confirmed = await confirmLargePreview(error, 'text')
    if (!confirmed) return null
    forcedLargeTextPreviews.add(path)
    return readFile(path, true)
  }
}

async function readStructurePreviewWithLargeConfirmation(
  path: string,
  source: StructureSource = ASE_STRUCTURE_SOURCE,
  signal?: AbortSignal
) {
  const cacheKey = `${source.id}:${path}`
  try {
    return await readStructurePreview(source, path, undefined, forcedLargeStructurePreviews.has(cacheKey), { signal })
  } catch (error) {
    if (isAbortError(error)) throw error
    if (!isLargePreviewError(error, 'STRUCTURE_FILE_TOO_LARGE')) throw error
    const confirmed = await confirmLargePreview(error, 'structure')
    throwIfAborted(signal)
    if (!confirmed) return null
    forcedLargeStructurePreviews.add(cacheKey)
    return readStructurePreview(source, path, undefined, true, { signal })
  }
}

function setPreviewMode(mode: PreviewMode) {
  if (previewMode.value === mode) return
  previewMode.value = mode
  void ensurePreviewModeLoaded(mode)
}

async function ensurePreviewModeLoaded(mode: PreviewMode) {
  if (mode === 'text') {
    await loadTextPreview()
  } else {
    await loadStructurePreview()
  }
}

async function savePreview(content: string) {
  if (!preview.value) return
  const path = preview.value.path
  try {
    await writeFile(path, normalizeTextLineEndings(content))
    ElMessage.success(t('message.saved'))
    const file = await readTextPreviewWithLargeConfirmation(path)
    if (file) preview.value = file
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.saveFailed'))
  }
}

function openFileContextMenu(item: FileItem, event: MouseEvent) {
  contextMenu.value = {
    visible: true,
    x: Math.min(Math.max(event.clientX, 8), window.innerWidth - CONTEXT_MENU_WIDTH - 8),
    y: Math.min(Math.max(event.clientY, 8), window.innerHeight - CONTEXT_MENU_HEIGHT - 8),
    opensLeft: event.clientX > window.innerWidth - 360,
    item
  }
}

function canLauncherOpenItem(item: FileItem, mode: 'default' | 'text') {
  if (item.type !== 'file') return false
  const capabilities = launcherBridgeCapabilities.value
  if (!capabilities?.enabled) return false
  const hasFeature = mode === 'default'
    ? Boolean(capabilities.features.open_default && capabilities.endpoints.open)
    : Boolean(capabilities.features.open_text && capabilities.endpoints.open_text)
  if (!hasFeature) return false
  return isPathInsideWorkspace(item.path, launcherWorkspaceRoot())
}

function launcherWorkspaceRoot() {
  return systemInfo.value?.workspace_root || launcherBridgeCapabilities.value?.workspace_root || ''
}

async function openContextWithLocalApp() {
  const item = contextMenu.value.item
  if (!item || !canLauncherOpenItem(item, 'default')) return
  closeContextMenu()
  try {
    await openWithLocalApp(item.path)
    ElMessage.success(t('message.localOpenStarted'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.localOpenFailed'))
  }
}

async function openContextWithNotepad() {
  const item = contextMenu.value.item
  if (!item || !canLauncherOpenItem(item, 'text')) return
  closeContextMenu()
  try {
    await openWithNotepad(item.path)
    ElMessage.success(t('message.localOpenStarted'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.localOpenFailed'))
  }
}

async function loadLauncherBridge() {
  launcherBridgeCapabilities.value = await loadLauncherBridgeCapabilities()
}

// Watch for sync events from App.vue (centralized handling)
watch(
  () => syncEvents.value,
  async (events) => {
    if (!events || events.length === 0) return
    await handleLauncherSyncEvents(events)
  }
)

async function handleLauncherSyncEvents(events: LauncherBridgeSyncEvent[]) {
  let shouldRefreshDirectory = false
  let shouldRefreshPreview = false

  for (const event of events) {
    // Skip error events (already handled by App.vue)
    if (event.status !== 'done') continue

    if (normalizeRemotePath(parentDirectoryPath(event.remote_path)) === normalizeRemotePath(currentPath.value)) {
      shouldRefreshDirectory = true
    }
    if (normalizeRemotePath(event.remote_path) === normalizeRemotePath(currentPreviewPath() ?? '')) {
      shouldRefreshPreview = true
    }
  }

  if (shouldRefreshDirectory) await loadDirectory(currentPath.value, { refresh: true })
  if (shouldRefreshPreview) await refreshPreview()
  // Note: success/error messages are handled by App.vue
}

function normalizeRemotePath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '')
}

function closeContextMenu(event?: Event) {
  if (!contextMenu.value.visible) return

  if (event?.type === 'scroll') {
    const target = event.target
    if (target instanceof Node && target !== document && leftPaneRef.value && !leftPaneRef.value.contains(target)) {
      return
    }
  }

  contextMenu.value = {
    visible: false,
    x: 0,
    y: 0,
    opensLeft: false,
    item: null
  }
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeContextMenu()
}

async function submitContextJob(command: SubmitCommand) {
  const item = contextMenu.value.item
  if (!item || item.type !== 'file') return
  closeContextMenu()
  try {
    const response = await submitJob(currentPath.value, item.name, command)
    ElMessage.success(localizeBackendMessage(response.message) || t('submit.jobSubmitted', { id: response.job_id ?? '' }))
  } catch (error) {
    ElMessage.error(apiErrorMessage(error, 'message.submitFailed'))
  }
}

async function copyContextPath() {
  const path = selectedItems.value[0]?.path ?? contextMenu.value.item?.path
  if (!path) return
  closeContextMenu()
  try {
    await copyTextToClipboard(path)
    ElMessage.success(t('message.pathCopied'))
  } catch {
    ElMessage.error(t('message.clipboardFailed'))
  }
}

async function copyTextToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }

  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', 'true')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    if (!document.execCommand('copy')) throw new Error('copy failed')
  } finally {
    textarea.remove()
  }
}

async function promptCreateFile() {
  let result: { value: string }
  try {
    result = await ElMessageBox.prompt(t('prompt.fileName'), t('prompt.newFile'), {
      inputPattern: SAFE_UPLOAD_SEGMENT_RE,
      inputErrorMessage: t('message.namePattern'),
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }
  const name = result.value
  if ((listing.value?.items ?? []).some(item => item.name === name)) {
    ElMessage.error(t('message.pathExists'))
    return
  }
  try {
    await writeFile(joinDisplayPath(currentPath.value, name), '')
    await loadDirectory(currentPath.value, { refresh: true })
    ElMessage.success(t('message.fileCreated'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.createFailed'))
  }
}

async function promptMkdir() {
  let result: { value: string }
  try {
    result = await ElMessageBox.prompt(t('prompt.folderName'), t('prompt.newFolder'), {
      inputPattern: SAFE_UPLOAD_SEGMENT_RE,
      inputErrorMessage: t('message.namePattern'),
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }
  try {
    await makeDirectory(currentPath.value, result.value)
    await loadDirectory(currentPath.value, { refresh: true })
    ElMessage.success(t('message.folderCreated'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.createFailed'))
  }
}

function handleWorkspaceDragEnter(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  closeContextMenu()
  dragUploadDepth.value = 1
  setUploadDropEffect(event)
}

function handleWorkspaceDragOver(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  dragUploadDepth.value = 1
  setUploadDropEffect(event)
}

function handleWorkspaceDragLeave(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  const root = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  const related = event.relatedTarget instanceof Node ? event.relatedTarget : null
  if (root && related && root.contains(related)) return
  dragUploadDepth.value = 0
}

function handleWorkspaceDrop(event: DragEvent) {
  if (!hasFileDrag(event)) return
  event.preventDefault()
  dragUploadDepth.value = 0
  void collectDropUploadEntries(event).then(entries => {
    if (entries.length > 0) void handleUploadEntries(entries)
  })
}

function handlePreviewDragOver(event: DragEvent) {
  if (!hasChemSSHFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function handlePreviewDrop(event: DragEvent) {
  const payload = readChemSSHFileDrag(event.dataTransfer)
  const path = payload?.paths[0]
  if (!path) return
  event.preventDefault()
  openBuiltinPanel('preview')
  void previewFile(payload.items[0] ?? path)
}

function triggerDownload(path: string) {
  const link = document.createElement('a')
  link.href = downloadUrl(path)
  link.rel = 'noopener'
  link.click()
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function downloadSelected() {
  const targets = selectedItems.value.filter(item => item.type === 'file' || item.type === 'directory')
  if (targets.length === 0) return

  if (targets.length === 1 && targets[0].type === 'file') {
    triggerDownload(targets[0].path)
    return
  }

  try {
    const response = await downloadArchive(targets.map(item => item.path))
    triggerBlobDownload(response.blob, response.filename ?? 'chemssh-selection.zip')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.downloadFailed'))
  }
}

async function handleMoveItems(items: FileItem[], targetDirectory: FileItem) {
  if (items.length === 0) return
  try {
    const prepared = await prepareMoveEntries(items, {
      targetPath: targetDirectory.path,
      promptConflict: promptMoveConflict
    })
    if (prepared.cancelled) return
    if (prepared.entries.length === 0) {
      ElMessage.info(t('message.moveSkipped'))
      return
    }
    await movePaths(prepared.entries.map(entry => entry.path), targetDirectory.path, prepared.entries)
    await loadDirectory(currentPath.value, { refresh: true })
    ElMessage.success(t('message.moved'))
  } catch (error) {
    ElMessage.error(moveApiErrorMessage(error))
  }
}

async function promptRename() {
  if (selectedItems.value.length !== 1 || !selectedItem.value) return
  const oldPath = selectedItem.value.path
  let result: { value: string }
  try {
    result = await ElMessageBox.prompt(t('prompt.newName'), t('prompt.rename'), {
      inputValue: selectedItem.value.name,
      inputPattern: SAFE_UPLOAD_SEGMENT_RE,
      inputErrorMessage: t('message.namePattern'),
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }
  const separator = oldPath.includes('\\') ? '\\' : '/'
  const parent = oldPath.split(/[\\/]/).slice(0, -1).join(separator)
  try {
    await renamePath(oldPath, `${parent}${separator}${result.value}`)
    await loadDirectory(currentPath.value, { refresh: true })
    ElMessage.success(t('message.renamed'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.renameFailed'))
  }
}

async function confirmDelete() {
  const targets = selectedItems.value.length > 0 ? selectedItems.value : selectedItem.value ? [selectedItem.value] : []
  if (!targets.length) return
  try {
    await ElMessageBox.confirm(t('prompt.deleteItems', { count: targets.length }), t('prompt.confirmDelete'), {
      type: 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }
  try {
    for (const item of targets) {
      await deletePath(item.path)
    }
    setSelection([], null)
    preview.value = null
    asePreview.value = null
    previewCandidate.value = null
    previewError.value = null
    currentStructureSource.value = ASE_STRUCTURE_SOURCE
    await loadDirectory(currentPath.value, { refresh: true })
    ElMessage.success(t('message.deleted'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('message.deleteFailed'))
  }
}

async function initializeWorkspace() {
  if (!mounted || initialized || !systemInfo.value) return
  initialized = true
  configureClientPreferencesScope(createWorkspaceScope(systemInfo.value))
  applyWorkspacePreferences()
  await loadClientPreferencesState()
  applyWorkspacePreferences()
  const initialPath = workspacePathOrRoot(getClientPreferences().workspace?.currentPath, systemInfo.value?.workspace_root)
  await loadDirectory(initialPath)
  workspacePreferencesReady.value = true
  void loadLauncherBridge()
  void loadPluginManifests()
  window.addEventListener('click', closeContextMenu)
  window.addEventListener('keydown', handleGlobalKeydown)
  window.addEventListener('message', handlePluginMessage)
  window.addEventListener('resize', closeContextMenu)
  window.addEventListener('scroll', closeContextMenu, true)
}

onMounted(() => {
  mounted = true
  void initializeWorkspace()
})

watch(
  () => systemInfo.value?.workspace_root,
  () => {
    void initializeWorkspace()
  }
)

watch(
  () => props.openPathRequest?.id,
  () => {
    const path = props.openPathRequest?.path
    if (path) void openDirectory(path)
  }
)

watch(
  () => systemInfo.value?.scheduler,
  () => {
    syncBuiltinPanelTitles()
  },
  { immediate: true }
)

watch(activeWorkPanelId, () => {
  saveWorkspacePreferences()
})

onBeforeUnmount(() => {
  if (uploadConflictDialog.value.resolve) chooseUploadConflict('cancel')
  stopResize()
  window.removeEventListener('click', closeContextMenu)
  window.removeEventListener('keydown', handleGlobalKeydown)
  window.removeEventListener('message', handlePluginMessage)
  window.removeEventListener('resize', closeContextMenu)
  window.removeEventListener('scroll', closeContextMenu, true)
})
</script>
