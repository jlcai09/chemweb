<template>
  <Teleport to="body" :disabled="!largeOpen">
    <div class="terminal-panel" :class="{ 'is-large': largeOpen }" ref="terminalPanelRef">
      <div
        ref="tabsRef"
        class="terminal-tabs"
        :class="{ 'is-overflowing': tabsOverflowing }"
        role="tablist"
        :aria-label="t('terminal.title')"
      >
      <div
        v-for="tab in tabs"
        :key="tab.localId"
        class="terminal-tab"
        :class="{ 'is-active': tab.localId === activeTabId, 'is-connected': tab.connected }"
        role="tab"
        tabindex="0"
        draggable="true"
        :aria-selected="tab.localId === activeTabId"
        :title="tab.cwd || tab.sessionId || ''"
        @click="activateTab(tab.localId)"
        @keydown.enter.prevent="activateTab(tab.localId)"
        @keydown.space.prevent="activateTab(tab.localId)"
        @dragstart="handleTabDragStart(tab.localId, $event)"
        @dragover="handleTabDragOver($event)"
        @drop="handleTabDrop(tab.localId, $event)"
      >
        <span class="terminal-tab-label">{{ tabTitle(tab) }}</span>
        <span
          v-if="boundFileManagerLabel(tab)"
          class="terminal-tab-binding-label"
          :style="{ '--binding-color': boundFileManagerColor(tab) }"
        >
          {{ boundFileManagerLabel(tab) }}
        </span>
        <span class="terminal-tab-actions" @click.stop>
          <el-dropdown
            v-if="fileManagerOptions.length > 0"
            trigger="click"
            placement="bottom"
            @command="handleTabBindCommand(tab, $event)"
          >
            <button
              class="terminal-tab-icon terminal-tab-bind"
              :class="{ 'is-bound': Boolean(tab.boundFileManagerId) }"
              type="button"
              :aria-label="t('terminal.bindFileManager')"
            >
              <el-icon><Link /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="">{{ t('terminal.bindNone') }}</el-dropdown-item>
                <el-dropdown-item
                  v-for="manager in fileManagerOptions"
                  :key="manager.id"
                  :command="manager.id"
                >
                  <span class="terminal-bind-option">
                    <span class="terminal-bind-dot" :style="{ background: manager.color }" />
                    <span>{{ manager.title }}</span>
                  </span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-tooltip :content="syncModeLabel(tab.syncMode)" placement="top" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
            <button
              class="terminal-tab-icon terminal-tab-sync"
              :class="`is-${tab.syncMode}`"
              type="button"
              :aria-pressed="tab.syncMode !== 'off'"
              :aria-label="syncModeLabel(tab.syncMode)"
              @click="toggleTabSync(tab)"
            >
              <svg class="terminal-sync-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path class="sync-device sync-device-files" d="M3.5 8.5h4.4l1.2 1.7h3.4v5.9h-9z" />
                <path class="sync-device sync-device-terminal" d="M16 7h4.5v10H16z" />
                <path class="sync-arrow sync-arrow-forward" d="M10.4 10.2h4.3m-1.6-1.7 1.8 1.7-1.8 1.7" />
                <path class="sync-arrow sync-arrow-back" d="M13.6 14.8H9.3m1.6-1.7-1.8 1.7 1.8 1.7" />
                <path class="sync-slash" d="M4.8 19.2 19.2 4.8" />
              </svg>
            </button>
          </el-tooltip>
          <el-tooltip :content="t('terminal.close')" placement="top" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
            <button
              class="terminal-tab-icon terminal-tab-close"
              type="button"
              :aria-label="t('terminal.close')"
              @click="closeTab(tab.localId)"
            >
              <el-icon><Close /></el-icon>
            </button>
          </el-tooltip>
        </span>
      </div>

      <button class="terminal-tab-new" type="button" :aria-label="t('terminal.newTab')" @click="createTab()">
        <el-icon><Plus /></el-icon>
      </button>
      <el-dropdown
        trigger="click"
        placement="bottom-end"
        popper-class="terminal-tools-dropdown"
        @command="handleTerminalToolCommand"
      >
        <button class="terminal-tab-settings terminal-tab-tools" type="button" :aria-label="t('terminal.tools')">
          <el-icon><MoreFilled /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="search">
              <span class="terminal-tools-option">
                <el-icon><SearchIcon /></el-icon>
                <span>{{ t('terminal.search') }}</span>
              </span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-popover trigger="click" placement="top-end" :width="280" popper-class="terminal-settings-popper">
        <template #reference>
          <button class="terminal-tab-settings" type="button" :aria-label="t('terminal.settings')">
            <el-icon><Setting /></el-icon>
          </button>
        </template>
        <div class="terminal-settings-panel">
          <div class="terminal-settings-title">{{ t('terminal.settings') }}</div>
          <label class="terminal-settings-toggle">
            <span>{{ t('terminal.vimCompatibility') }}</span>
            <el-switch v-model="vimCompatibilityMode" size="small" />
          </label>
          <label class="terminal-settings-toggle">
            <span>{{ t('terminal.autoCopySelection') }}</span>
            <el-switch v-model="autoCopySelection" size="small" />
          </label>
          <label class="terminal-settings-toggle">
            <span>{{ t('terminal.refreshFilesAfterCommand') }}</span>
            <el-switch v-model="refreshFileManagerAfterCommand" size="small" />
          </label>
          <div class="terminal-settings-submenu">
            <div class="terminal-font-size-header">
              <span style="font-weight: 700;">{{ t('terminal.fontSize') }}</span>
              <strong>{{ terminalFontSize }}px</strong>
            </div>
            <div class="terminal-font-size-controls">
              <el-slider
                v-model="terminalFontSizeModel"
                class="terminal-font-size-slider"
                :min="TERMINAL_FONT_SIZE_MIN"
                :max="TERMINAL_FONT_SIZE_MAX"
                :step="1"
                :show-tooltip="false"
                size="small"
              />
              <el-input-number
                v-model="terminalFontSizeModel"
                class="terminal-font-size-input"
                :min="TERMINAL_FONT_SIZE_MIN"
                :max="TERMINAL_FONT_SIZE_MAX"
                :step="1"
                controls-position="right"
                size="small"
              />
            </div>
          </div>
        </div>
      </el-popover>
      <el-tooltip :content="largeOpen ? t('terminal.exitLarge') : t('terminal.openLarge')" placement="top" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
        <button class="terminal-tab-settings terminal-tab-large" type="button" :aria-label="largeOpen ? t('terminal.exitLarge') : t('terminal.openLarge')" @click="toggleLargeOpen">
          <el-icon><FullScreen /></el-icon>
        </button>
      </el-tooltip>
      </div>

      <div class="terminal-shell" @dragover="handleFileDragOver" @drop="handleFileDrop">
        <div
          v-for="tab in tabs"
          :key="tab.localId"
          :ref="el => setTerminalHost(tab.localId, el)"
          class="terminal-host"
          :class="{ 'is-active': tab.localId === activeTabId }"
        />
        <div v-if="activeTab?.awaitingVisibleShellOutput" class="terminal-overlay">{{ t('terminal.starting') }}</div>
      </div>
    </div>
    <!-- 搜索面板独立于终端，可拖动到当前页面（工作台/画板）的任意位置 -->
    <Teleport :to="searchPanelContainer" :disabled="!searchPanelContainer">
      <div
        v-if="searchOpen"
        ref="searchPanelRef"
        class="terminal-search-panel terminal-search-panel-floating"
        :style="searchPanelStyle"
        role="dialog"
        :aria-label="t('terminal.search')"
        @keydown.esc.stop.prevent="closeSearchPanel"
      >
        <div
          class="terminal-search-header"
          @mousedown="handleSearchPanelDragStart"
        >
          <strong>{{ t('terminal.search') }}</strong>
          <button class="terminal-search-close" type="button" :aria-label="t('terminal.searchClose')" @click="closeSearchPanel">
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <div class="terminal-search-body">
          <div class="terminal-search-input-row">
            <el-input
              ref="searchInputRef"
              v-model="searchTerm"
              class="terminal-search-input"
              size="small"
              clearable
              :placeholder="t('terminal.searchPlaceholder')"
              @keydown.enter.prevent="handleSearchEnter"
            />
            <el-checkbox v-model="searchCaseSensitive" size="small">{{ t('terminal.searchCaseSensitive') }}</el-checkbox>
            <el-checkbox v-model="searchRegex" size="small">{{ t('terminal.searchRegex') }}</el-checkbox>
          </div>
          <div class="terminal-search-actions">
            <el-button size="small" @click="findPreviousInTerminal">{{ t('terminal.searchPrevious') }}</el-button>
            <el-button size="small" type="primary" @click="findNextInTerminal">{{ t('terminal.searchNext') }}</el-button>
            <div class="terminal-search-count">
              <el-input-number
                v-if="searchResultCount > 0"
                v-model="searchResultIndexInput"
                class="terminal-search-index-input"
                size="small"
                :min="1"
                :max="searchResultCount"
                :controls="false"
                @change="handleSearchIndexChange"
              />
              <span v-else class="terminal-search-index-display">-</span>
              <span class="terminal-search-divider">/</span>
              <span class="terminal-search-total">{{ searchResultCount }}</span>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { Close, FullScreen, Link, MoreFilled, Plus, Search as SearchIcon, Setting } from '@element-plus/icons-vue'
import { BrowserClipboardProvider, ClipboardAddon, type ClipboardSelectionType } from '@xterm/addon-clipboard'
import { FitAddon } from '@xterm/addon-fit'
import { Terminal, type IDisposable } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import {
  closeTerminalSession,
  createTerminalSession,
  listTerminalSessions,
  terminalWebSocketUrl,
  type TerminalSession
} from '../../api/terminal'
import { formatFileDragTerminalInput, hasChemSSHFileDrag, readChemSSHFileDrag } from '../../api/fileDrag'
import { isPathInsideWorkspace, workspacePathOrRoot } from '../../api/workspaceScope'
import { t } from '../../i18n'
import type { CanvasFileManagerBindingTarget, CanvasTerminalTabBinding } from '../../types/canvasBoard'
import { useTerminalSettings } from '../../composables/useTerminalSettings'
import { useTerminalTransfer } from '../../composables/useTerminalTransfer'
import { useTerminalSearch, type SearchableTab } from '../../composables/useTerminalSearch'

type TerminalMessage =
  | { type: 'output'; data: string }
  | { type: 'cwd'; path: string }
  | { type: 'command_done'; seq: number; path: string }
  | {
      type: 'transfer_request'
      transfer_id: string
      direction: 'upload' | 'download'
      cwd: string
      argv: string[]
      paths?: string[]
      error?: string
    }
  | { type: 'error'; code?: string; message?: string }
  | { type: 'exit'; code?: number | null }

type TerminalSyncMode = 'off' | 'follow' | 'bidirectional'

type SearchMatch = {
  row: number
  col: number
  length: number
}

type TerminalTab = {
  localId: string
  sessionId: string | null
  cwd: string
  syncMode: TerminalSyncMode
  connected: boolean
  interactiveReady: boolean
  awaitingVisibleShellOutput: boolean
  starting: boolean
  exited: boolean
  terminal: Terminal | null
  clipboardAddon: ClipboardAddon | null
  fitAddon: FitAddon | null
  searchMatches: SearchMatch[]
  searchCurrentIndex: number
  socket: WebSocket | null
  inputDisposable: IDisposable | null
  selectionDisposable: IDisposable | null
  renderDisposable: IDisposable | null
  scrollDisposable: IDisposable | null
  middleClickCleanup: (() => void) | null
  latestSelection: string
  lastAutoCopiedSelection: string
  preservingMiddleClickSelection: boolean
  suppressAutoCopySelection: boolean
  resizeObserver: ResizeObserver | null
  fitFrame: number
  autoCopyFrame: number
  searchHighlightFrame: number
  searchHighlightTimer: number
  boundFileManagerId: string | null
  lastRecoveryStartedAt: number
  // Bumped every time the tab is intentionally torn down (disposeTab / closeTab).
  // The WebSocket handlers compare against the generation they captured at open
  // time to tell an unexpected drop (server restart, network blip) apart from a
  // user-initiated close, so the tab can self-heal instead of staying wedged on
  // a stale session id.
  disposalGen: number
  // True while a recovery attempt is in flight; prevents duplicate recovery from
  // the same broken socket's error and close events.
  recovering: boolean
}

const SYSTEM_CLIPBOARD_SELECTION = 'c' as ClipboardSelectionType
const TERMINAL_RECOVERY_MIN_INTERVAL_MS = 5000

const props = defineProps<{
  initialCwd?: string | null
  workspaceRoot?: string | null
  currentFileManagerPath?: string | null
  fileManagers?: CanvasFileManagerBindingTarget[]
  initialBindings?: CanvasTerminalTabBinding[]
  layoutVersion?: number
  transferUploadHandler?: (path: string, files: File[]) => Promise<void>
  deferInit?: boolean
}>()

const emit = defineEmits<{
  'cwd-change': [path: string]
  'bound-cwd-change': [managerId: string, path: string]
  'refresh-file-manager': [managerId: string | null, path: string]
  'binding-summary-change': [summary: CanvasTerminalTabBinding[]]
}>()

const tabs = ref<TerminalTab[]>([])
const activeTabId = ref<string | null>(null)
const tabsRef = ref<HTMLElement | null>(null)
const tabsOverflowing = ref(false)
const terminalPanelRef = ref<HTMLElement | null>(null)

const {
  TERMINAL_FONT_SIZE_MIN,
  TERMINAL_FONT_SIZE_MAX,
  terminalFontSize,
  vimCompatibilityMode,
  autoCopySelection,
  refreshFileManagerAfterCommand,
  setTerminalFontSize,
  storeTerminalFontSize,
  storeVimCompatibilityMode,
  storeAutoCopySelection,
  storeRefreshFileManagerAfterCommand
} = useTerminalSettings()

const largeOpen = ref(false)
const searchInputRef = ref<{ focus: () => void } | null>(null)
const searchPanelRef = ref<HTMLElement | null>(null)
const searchPanelPosition = ref<{ x: number; y: number } | null>(null)
const searchPanelDragging = ref(false)
const searchPanelContainer = ref<string | null>(null)
const terminalFontSizeModel = computed({
  get: () => terminalFontSize.value,
  set: (value: number | undefined) => setTerminalFontSize(value)
})
const searchPanelStyle = computed(() => {
  if (!searchPanelPosition.value) return {}
  return {
    top: `${searchPanelPosition.value.y}px`,
    left: `${searchPanelPosition.value.x}px`,
    right: 'auto',
    transform: 'none'
  }
})
const terminalHosts = new Map<string, HTMLElement>()
const clipboardProvider = new BrowserClipboardProvider()
let tabsResizeObserver: ResizeObserver | null = null
let tabSerial = 0
const TERMINAL_TAB_DRAG_MIME = 'application/x-chemssh-terminal-tab'

const preferredCwd = computed(() => usableWorkspacePath(props.currentFileManagerPath || props.initialCwd || props.workspaceRoot))
const fileManagerOptions = computed(() => props.fileManagers ?? [])
const activeTab = computed(() => tabs.value.find(tab => tab.localId === activeTabId.value) ?? null)

const {
  searchOpen,
  searchTerm,
  searchCaseSensitive,
  searchRegex,
  searchResultCount,
  searchResultIndexInput,
  performSearch,
  findNextInTerminal,
  findPreviousInTerminal,
  handleSearchIndexChange,
  scheduleSearchHighlightRefresh,
  scheduleSearchHighlightRefreshAfterInteraction,
  syncSearchWithActiveTab,
  closeSearchPanel,
  clearAllSearchHighlights,
  updateSearchStatus
} = useTerminalSearch(activeTab as Ref<SearchableTab | null>, tabs as Ref<SearchableTab[]>)

const { handleTransferRequest } = useTerminalTransfer()

onMounted(async () => {
  window.addEventListener('resize', handleLayoutChange)
  window.addEventListener('chemssh:terminal-fit', handleLayoutChange)

  // 查找搜索面板应该传送到的容器
  detectSearchPanelContainer()

  await nextTick()
  await document.fonts?.ready
  if (tabsRef.value && typeof ResizeObserver !== 'undefined') {
    tabsResizeObserver = new ResizeObserver(measureTabsOverflow)
    tabsResizeObserver.observe(tabsRef.value)
  }
  if (!props.deferInit) {
    await ensureInitialTab()
  }
  measureTabsOverflow()
})

onBeforeUnmount(() => {
  stopSearchPanelDrag()
  window.removeEventListener('resize', handleLayoutChange)
  window.removeEventListener('chemssh:terminal-fit', handleLayoutChange)
  tabsResizeObserver?.disconnect()
  tabsResizeObserver = null
  for (const tab of [...tabs.value]) {
    void disposeTab(tab, true)
  }
  tabs.value = []
  activeTabId.value = null
})

watch(
  () => props.currentFileManagerPath,
  path => {
    if (!path) return
    for (const tab of tabs.value) {
      if (!tab.boundFileManagerId && isTabFollowingFileManager(tab) && tab.cwd !== path) sendTabSyncCwd(tab, path)
    }
  }
)

watch(
  () => fileManagerOptions.value.map(item => `${item.id}\u0000${item.path}`).join('\u0001'),
  () => {
    syncTabsWithBoundFileManagers()
  }
)

watch(preferredCwd, cwd => {
  if (!cwd || tabs.value.length > 0) return
  void ensureInitialTab(cwd)
})

watch(
  () => props.deferInit,
  deferred => {
    if (!deferred && tabs.value.length === 0) {
      void ensureInitialTab()
    }
  }
)

watch(
  () => props.layoutVersion,
  () => {
    requestActiveTabFit()
  },
  { flush: 'post' }
)

watch(activeTabId, () => {
  requestActiveTabFit()
  syncSearchWithActiveTab()
  emitTerminalBindingSummary()
})

watch(largeOpen, () => {
  window.setTimeout(handleLayoutChange, 0)
  window.setTimeout(handleLayoutChange, 120)
})

watch(
  () => tabs.value.length,
  () => {
    void nextTick(() => measureTabsOverflow())
  }
)

watch(terminalFontSize, size => {
  storeTerminalFontSize(size)
  for (const tab of tabs.value) {
    if (!tab.terminal) continue
    tab.terminal.options.fontSize = size
    void requestTabFit(tab)
  }
})

watch(vimCompatibilityMode, value => {
  storeVimCompatibilityMode(value)
})

watch(autoCopySelection, value => {
  storeAutoCopySelection(value)
})

watch(refreshFileManagerAfterCommand, value => {
  storeRefreshFileManagerAfterCommand(value)
})

watch(
  [searchTerm, searchCaseSensitive, searchRegex],
  () => {
    if (!searchOpen.value) return
    performSearch()
  }
)

function createEmptyTab(cwd?: string): TerminalTab {
  tabSerial += 1
  return {
    localId: `terminal_tab_${tabSerial}`,
    sessionId: null,
    cwd: usableWorkspacePath(cwd ?? preferredCwd.value),
    syncMode: 'off',
    connected: false,
    interactiveReady: false,
    awaitingVisibleShellOutput: false,
    starting: false,
    exited: false,
    terminal: null,
    clipboardAddon: null,
    fitAddon: null,
    searchMatches: [],
    searchCurrentIndex: -1,
    socket: null,
    inputDisposable: null,
    selectionDisposable: null,
    renderDisposable: null,
    scrollDisposable: null,
    middleClickCleanup: null,
    latestSelection: '',
    lastAutoCopiedSelection: '',
    preservingMiddleClickSelection: false,
    suppressAutoCopySelection: false,
    resizeObserver: null,
    fitFrame: 0,
    autoCopyFrame: 0,
    searchHighlightFrame: 0,
    searchHighlightTimer: 0,
    boundFileManagerId: null,
    lastRecoveryStartedAt: 0,
    disposalGen: 0,
    recovering: false
  }
}

function normalizeInitialBindings() {
  const bindings = props.initialBindings ?? []
  return bindings.flatMap(binding => {
    if (!binding || typeof binding.tabId !== 'string') return []
    const rawCwd = typeof binding.cwd === 'string' ? binding.cwd : ''
    if (rawCwd && !isUsableWorkspacePath(rawCwd)) return []
    const cwd = usableWorkspacePath(rawCwd || props.workspaceRoot)
    if (!cwd) return []
    const syncMode: TerminalSyncMode = binding.syncMode === 'follow' || binding.syncMode === 'bidirectional'
      ? binding.syncMode
      : 'off'
    return [{
      ...binding,
      title: typeof binding.title === 'string' ? binding.title : '',
      cwd,
      syncMode,
      boundFileManagerId: typeof binding.boundFileManagerId === 'string' ? binding.boundFileManagerId : null,
      active: binding.active === true
    }]
  })
}

async function createTab(cwd?: string, binding?: CanvasTerminalTabBinding) {
  const tab = createEmptyTab(cwd)
  if (binding) {
    tab.syncMode = binding.syncMode
    tab.boundFileManagerId = binding.boundFileManagerId
    if (binding.cwd) tab.cwd = usableWorkspacePath(binding.cwd)
  }
  tabs.value.push(tab)
  activeTabId.value = tab.localId
  emitTerminalBindingSummary()
  await nextTick()
  await initializeTabTerminal(tab)
  await requestTabFit(tab)
  measureTabsOverflow()
  emitTerminalBindingSummary()
}

async function ensureInitialTab(cwd?: string) {
  if (tabs.value.length > 0) return
  const initialBindings = normalizeInitialBindings()
  if (initialBindings.length === 0) {
    await createTab(cwd)
    return
  }

  for (const binding of initialBindings) {
    await createTab(binding.cwd || cwd, binding)
  }
  const activeBinding = initialBindings.find(binding => binding.active) ?? initialBindings[0]
  const activeIndex = initialBindings.indexOf(activeBinding)
  activeTabId.value = tabs.value[Math.max(0, activeIndex)]?.localId ?? tabs.value[0]?.localId ?? null
  emitTerminalBindingSummary()
}

function activateTab(tabId: string) {
  activeTabId.value = tabId
  emitTerminalBindingSummary()
}

function handleTabDragStart(tabId: string, event: DragEvent) {
  if (!event.dataTransfer) return
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData(TERMINAL_TAB_DRAG_MIME, tabId)
}

function handleTabDragOver(event: DragEvent) {
  const types = Array.from(event.dataTransfer?.types ?? [])
  if (!types.includes(TERMINAL_TAB_DRAG_MIME)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

function handleTabDrop(targetTabId: string, event: DragEvent) {
  const sourceTabId = event.dataTransfer?.getData(TERMINAL_TAB_DRAG_MIME)
  if (!sourceTabId || sourceTabId === targetTabId) return
  event.preventDefault()
  const fromIndex = tabs.value.findIndex(tab => tab.localId === sourceTabId)
  const toIndex = tabs.value.findIndex(tab => tab.localId === targetTabId)
  if (fromIndex < 0 || toIndex < 0) return
  const next = [...tabs.value]
  const [moved] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, moved)
  tabs.value = next
  void nextTick(() => {
    measureTabsOverflow()
    requestActiveTabFit()
  })
}

function setTerminalHost(tabId: string, el: unknown) {
  if (el instanceof HTMLElement) {
    terminalHosts.set(tabId, el)
    const tab = tabs.value.find(item => item.localId === tabId)
    if (tab && !tab.terminal) void initializeTabTerminal(tab)
    return
  }
  terminalHosts.delete(tabId)
}

async function initializeTabTerminal(tab: TerminalTab) {
  if (tab.terminal) return
  const host = terminalHosts.get(tab.localId)
  if (!host) return

  const terminal = markRaw(new Terminal({
    cursorBlink: true,
    fontFamily: '"JetBrains Mono", Consolas, "Liberation Mono", monospace',
    fontSize: terminalFontSize.value,
    lineHeight: 1.15,
    scrollback: 3000,
    theme: {
      background: '#151b22',
      foreground: '#d7e1ea',
      cursor: '#f7c46c',
      selectionBackground: '#36546a'
    }
  }))
  const fitAddon = markRaw(new FitAddon())
  const clipboardAddon = markRaw(new ClipboardAddon(undefined, clipboardProvider))
  terminal.loadAddon(fitAddon)
  terminal.loadAddon(clipboardAddon)
  terminal.open(host)
  terminal.attachCustomKeyEventHandler(event => {
    if (event.type === 'keydown' && event.key === 'Backspace') {
      sendTabSocketMessage(tab, { type: 'input', data: '\x7f' })
      return false
    }
    return true
  })
  tab.middleClickCleanup = bindTerminalMiddleClickPaste(tab, host)

  tab.terminal = terminal
  tab.fitAddon = fitAddon
  tab.clipboardAddon = clipboardAddon
  tab.selectionDisposable = terminal.onSelectionChange(() => {
    const selection = terminal.getSelection()
    if (selection || !tab.preservingMiddleClickSelection) tab.latestSelection = selection
    queueAutoCopySelection(tab, selection)
  })
  tab.renderDisposable = terminal.onRender(() => {
    scheduleSearchHighlightRefresh(tab)
  })
  tab.scrollDisposable = terminal.onScroll(() => {
    scheduleSearchHighlightRefresh(tab)
  })

  // 监听终端数据变化
  tab.inputDisposable = terminal.onData(data => {
    sendTabSocketMessage(tab, { type: 'input', data })
  })

  tab.resizeObserver = new ResizeObserver(() => scheduleTabFit(tab))
  tab.resizeObserver.observe(host)

  await requestTabFit(tab)
  await startTabSession(tab)
}

async function startTabSession(tab: TerminalTab) {
  if (!tab.terminal || tab.starting || tab.sessionId) return
  tab.starting = true
  tab.awaitingVisibleShellOutput = true
  tab.interactiveReady = false
  try {
    fitTabTerminal(tab)
    await cleanupDetachedSessions()
    const session = await createTerminalSession({
      cwd: usableWorkspacePath(tab.cwd || preferredCwd.value),
      rows: tab.terminal.rows,
      cols: tab.terminal.cols,
      vim_compatibility: vimCompatibilityMode.value
    })
    attachTabSession(tab, session)
  } catch (error) {
    tab.awaitingVisibleShellOutput = false
    tab.terminal.writeln(`\r\n${t('terminal.startFailed')}: ${error instanceof Error ? error.message : String(error)}`)
  } finally {
    tab.starting = false
  }
}

// Self-heal a tab whose session has become invalid (backend restart wiped the
// in-memory store, or a stale id was replayed). Drops the stale id, asks the
// server to delete it best-effort, then re-runs the normal session creation
// path. `recovering` deduplicates concurrent error/close events, while
// `lastRecoveryStartedAt` keeps a flapping backend from causing a reconnect
// storm.
async function recoverTabSession(tab: TerminalTab) {
  if (tab.recovering || !tab.terminal) return
  const staleId = tab.sessionId
  if (!staleId) return
  const now = Date.now()
  if (tab.lastRecoveryStartedAt > 0 && now - tab.lastRecoveryStartedAt < TERMINAL_RECOVERY_MIN_INTERVAL_MS) {
    pauseTabAutoRecovery(tab, staleId)
    return
  }
  tab.recovering = true
  tab.lastRecoveryStartedAt = now
  tab.disposalGen += 1
  closeTabSocket(tab)
  tab.sessionId = null
  tab.connected = false
  tab.starting = false
  // Best-effort: tell the server to drop the stale session. It is already gone
  // in the restart case, so ignore any error. Await it when possible so a live
  // stale session does not briefly count against the max-session limit.
  try {
    await closeTerminalSession(staleId)
  } catch {
    // The backend may already have dropped this session.
  }
  try {
    await startTabSession(tab)
  } finally {
    // startTabSession flips `starting`; recovering is cleared on a successful
    // ws.onopen. If creation failed, leave the tab recoverable by the next
    // user action (closing/reopening the tab) rather than auto-retrying.
    if (!tab.sessionId) tab.recovering = false
  }
}

function pauseTabAutoRecovery(tab: TerminalTab, sessionId: string) {
  tab.disposalGen += 1
  closeTabSocket(tab)
  tab.sessionId = null
  tab.connected = false
  tab.starting = false
  tab.recovering = false
  resetTabInteraction(tab)
  tab.terminal?.writeln(`\r\n${t('terminal.reconnectPaused')}`)
  void closeTerminalSession(sessionId).catch(() => undefined)
}

function attachTabSession(tab: TerminalTab, session: TerminalSession) {
  tab.sessionId = session.session_id
  tab.cwd = session.cwd
  tab.exited = false
  connectTabSocket(tab, session.session_id)
}

function connectTabSocket(tab: TerminalTab, id: string) {
  closeTabSocket(tab)
  // Snapshot the disposal generation at open time. If the WebSocket closes and
  // this number has not advanced, the close was NOT caused by an intentional
  // teardown (disposeTab/closeTab bump it) — it was an unexpected drop, so the
  // session id is likely stale and should be recovered.
  const openGen = tab.disposalGen
  let ws: WebSocket
  try {
    ws = markRaw(new WebSocket(terminalWebSocketUrl(id)))
  } catch (error) {
    tab.sessionId = null
    tab.cwd = ''
    void closeTerminalSession(id)
    throw error
  }
  tab.socket = ws

  ws.onopen = () => {
    if (tab.socket !== ws) return
    tab.connected = true
    tab.recovering = false
    markTabInteractive(tab)
    void requestTabFit(tab)
    const managerPath = fileManagerPathForTab(tab)
    if (isTabFollowingFileManager(tab) && managerPath && tab.cwd !== managerPath) {
      sendTabSyncCwd(tab, managerPath)
    }
  }

  ws.onmessage = event => {
    handleTabSocketMessage(tab, event.data)
  }

  ws.onclose = () => {
    if (tab.socket !== ws) return
    tab.connected = false
    resetTabInteraction(tab)
    if (tab.recovering && tab.sessionId === id) {
      pauseTabAutoRecovery(tab, id)
      return
    }
    // Unexpected close while we still believe this session is alive: the
    // backend most likely restarted (in-memory session store wiped) or the
    // connection dropped. Drop the stale id and recreate the session once so
    // the user is not left on a dead tab that only a full page reload could
    // recover. Skipped when the close was triggered by an intentional
    // teardown (openGen advanced) or when a recovery is already in flight.
    if (openGen === tab.disposalGen && tab.sessionId === id && !tab.exited && !tab.recovering) {
      void recoverTabSession(tab)
    }
  }

  ws.onerror = () => {
    if (tab.socket === ws) tab.connected = false
  }
}

function handleTabSocketMessage(tab: TerminalTab, raw: string) {
  if (!tab.terminal) return

  let message: TerminalMessage
  try {
    message = JSON.parse(raw) as TerminalMessage
  } catch {
    return
  }

  if (message.type === 'output') {
    tab.terminal.write(message.data)
    if (!tab.interactiveReady && hasVisibleTerminalContent(message.data)) markTabInteractive(tab)
    if (tab.localId === activeTabId.value) scheduleSearchHighlightRefresh(tab, 50)
  } else if (message.type === 'cwd') {
    applyTabCwd(tab, message.path)
  } else if (message.type === 'command_done') {
    handleTabCommandDone(tab, message.path)
  } else if (message.type === 'transfer_request') {
    void handleTransferRequestComposable(tab, message)
  } else if (message.type === 'error') {
    tab.awaitingVisibleShellOutput = false
    tab.terminal.writeln(`\r\n[${message.code ?? 'ERROR'}] ${message.message ?? ''}`)
    // The server could not find / no longer owns this session. This happens
    // after a backend restart (in-memory session store wiped) or when a stale
    // session id is replayed. The socket will close imminently, but to avoid
    // racing the close handler (and because the id is provably invalid now)
    // drop it here and recreate the session once. Without this, the tab stays
    // wedged on the stale id — refresh and "new terminal" cannot fix it
    // because the guard in startTabSession refuses to rebuild while a
    // sessionId is set.
    if (message.code === 'TERMINAL_SESSION_NOT_FOUND' && tab.sessionId && !tab.recovering) {
      void recoverTabSession(tab)
    }
  } else if (message.type === 'exit') {
    tab.connected = false
    tab.exited = true
    resetTabInteraction(tab)
    tab.terminal.writeln(`\r\n${t('terminal.exited')}`)
  }
}

async function handleTransferRequestComposable(tab: TerminalTab, message: Extract<TerminalMessage, { type: 'transfer_request' }>) {
  if (!tab.terminal) return
  await handleTransferRequest(
    message,
    text => tab.terminal?.writeln(text),
    payload => sendTabSocketMessage(tab, { type: 'transfer_result', ...payload }),
    props.transferUploadHandler
  )
}

async function closeTab(tabId: string) {
  const index = tabs.value.findIndex(tab => tab.localId === tabId)
  if (index < 0) return
  const tab = tabs.value[index]
  tabs.value.splice(index, 1)
  if (activeTabId.value === tabId) {
    activeTabId.value = tabs.value[Math.min(index, tabs.value.length - 1)]?.localId ?? null
  }
  await disposeTab(tab, true)
  await nextTick()
  measureTabsOverflow()
  requestActiveTabFit()
  emitTerminalBindingSummary()
}

async function disposeTab(tab: TerminalTab, closeSession: boolean) {
  // Advance the disposal generation so any in-flight WebSocket close handler
  // knows this teardown was intentional and suppresses the self-heal path.
  tab.disposalGen += 1
  tab.recovering = false
  closeTabSocket(tab)
  tab.resizeObserver?.disconnect()
  tab.inputDisposable?.dispose()
  tab.selectionDisposable?.dispose()
  tab.renderDisposable?.dispose()
  tab.scrollDisposable?.dispose()
  tab.middleClickCleanup?.()
  tab.clipboardAddon?.dispose()
  tab.terminal?.dispose()
  if (tab.fitFrame) window.cancelAnimationFrame(tab.fitFrame)
  if (tab.autoCopyFrame) window.cancelAnimationFrame(tab.autoCopyFrame)
  if (tab.searchHighlightFrame) window.cancelAnimationFrame(tab.searchHighlightFrame)
  if (tab.searchHighlightTimer) window.clearTimeout(tab.searchHighlightTimer)
  terminalHosts.delete(tab.localId)
  const sessionId = tab.sessionId

  tab.sessionId = null
  tab.connected = false
  tab.terminal = null
  tab.clipboardAddon = null
  tab.fitAddon = null
  tab.searchMatches = []
  tab.searchCurrentIndex = -1
  tab.inputDisposable = null
  tab.selectionDisposable = null
  tab.renderDisposable = null
  tab.scrollDisposable = null
  tab.middleClickCleanup = null
  tab.latestSelection = ''
  tab.lastAutoCopiedSelection = ''
  tab.preservingMiddleClickSelection = false
  tab.suppressAutoCopySelection = false
  tab.searchHighlightFrame = 0
  tab.searchHighlightTimer = 0
  tab.resizeObserver = null
  tab.fitFrame = 0
  tab.autoCopyFrame = 0
  resetTabInteraction(tab)

  if (!closeSession || !sessionId) return
  try {
    await closeTerminalSession(sessionId)
  } catch {
    // The backend may already have closed this session.
  }
}

function closeTabSocket(tab: TerminalTab) {
  const socket = tab.socket
  if (!socket) return
  socket.onopen = null
  socket.onmessage = null
  socket.onclose = null
  socket.onerror = null
  if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
    socket.close()
  }
  tab.socket = null
}

function toggleTabSync(tab: TerminalTab) {
  const nextMode = nextSyncMode(tab.syncMode)
  tab.syncMode = nextMode
  const managerPath = fileManagerPathForTab(tab)
  if (nextMode === 'follow' && managerPath) {
    sendTabSyncCwd(tab, managerPath)
    emitTerminalBindingSummary()
    return
  }
  if (nextMode === 'bidirectional' && tab.cwd && tab.cwd !== managerPath) {
    emitTabCwdChange(tab, tab.cwd)
  }
  emitTerminalBindingSummary()
}

function nextSyncMode(mode: TerminalSyncMode): TerminalSyncMode {
  if (mode === 'off') return 'follow'
  if (mode === 'follow') return 'bidirectional'
  return 'off'
}

function syncModeLabel(mode: TerminalSyncMode) {
  if (mode === 'follow') return t('terminal.syncCwdFollow')
  if (mode === 'bidirectional') return t('terminal.syncCwdBidirectional')
  return t('terminal.syncCwdOff')
}

function handleTabBindCommand(tab: TerminalTab, command: unknown) {
  const managerId = typeof command === 'string' ? command : ''
  tab.boundFileManagerId = managerId || null
  if (tab.boundFileManagerId && tab.syncMode === 'off') tab.syncMode = 'follow'
  const path = fileManagerPathForTab(tab)
  if (path && isTabFollowingFileManager(tab) && tab.cwd !== path) sendTabSyncCwd(tab, path)
  emitTerminalBindingSummary()
}

function boundFileManager(tab: TerminalTab) {
  if (!tab.boundFileManagerId) return null
  return fileManagerOptions.value.find(item => item.id === tab.boundFileManagerId) ?? null
}

function boundFileManagerLabel(tab: TerminalTab) {
  return boundFileManager(tab)?.title ?? ''
}

function boundFileManagerColor(tab: TerminalTab) {
  return boundFileManager(tab)?.color ?? '#176b87'
}

function fileManagerPathForTab(tab: TerminalTab) {
  return boundFileManager(tab)?.path || props.currentFileManagerPath || ''
}

function usableWorkspacePath(path: string | null | undefined) {
  if (!props.workspaceRoot) return path ?? ''
  return workspacePathOrRoot(path, props.workspaceRoot)
}

function isUsableWorkspacePath(path: string | null | undefined) {
  if (!path) return false
  return props.workspaceRoot ? isPathInsideWorkspace(path, props.workspaceRoot) : true
}

function emitTabCwdChange(tab: TerminalTab, path: string) {
  if (!isUsableWorkspacePath(path)) return
  if (tab.boundFileManagerId) {
    emit('bound-cwd-change', tab.boundFileManagerId, path)
    return
  }
  emit('cwd-change', path)
}

function syncTabsWithBoundFileManagers() {
  const managerIds = new Set(fileManagerOptions.value.map(item => item.id))
  for (const tab of tabs.value) {
    if (tab.boundFileManagerId && !managerIds.has(tab.boundFileManagerId)) {
      tab.boundFileManagerId = null
      continue
    }
    const path = fileManagerPathForTab(tab)
    if (tab.boundFileManagerId && path && isTabFollowingFileManager(tab) && tab.cwd !== path) {
      sendTabSyncCwd(tab, path)
    }
  }
  emitTerminalBindingSummary()
}

function emitTerminalBindingSummary() {
  emit('binding-summary-change', tabs.value.map(tab => ({
    tabId: tab.localId,
    title: tabTitle(tab),
    cwd: tab.cwd,
    syncMode: tab.syncMode,
    boundFileManagerId: tab.boundFileManagerId,
    active: tab.localId === activeTabId.value
  })))
}

function isTabFollowingFileManager(tab: TerminalTab) {
  return tab.syncMode === 'follow' || tab.syncMode === 'bidirectional'
}

function applyTabCwd(tab: TerminalTab, path: string) {
  if (!path) return
  tab.cwd = path
  emitTerminalBindingSummary()
  if (tab.syncMode !== 'bidirectional') return
  if (path === fileManagerPathForTab(tab)) return
  emitTabCwdChange(tab, path)
  emitTerminalBindingSummary()
}

function handleTabCommandDone(tab: TerminalTab, path: string) {
  if (!refreshFileManagerAfterCommand.value || !isTabFollowingFileManager(tab)) return
  const managerPath = fileManagerPathForTab(tab)
  const refreshPath = tab.syncMode === 'bidirectional'
    ? usableWorkspacePath(path || tab.cwd || managerPath)
    : managerPath
  if (!isUsableWorkspacePath(refreshPath)) return
  emit('refresh-file-manager', tab.boundFileManagerId, refreshPath)
}

function markTabInteractive(tab: TerminalTab) {
  tab.interactiveReady = true
  tab.awaitingVisibleShellOutput = false
}

function resetTabInteraction(tab: TerminalTab) {
  tab.interactiveReady = false
  tab.awaitingVisibleShellOutput = false
}

function hasVisibleTerminalContent(data: string) {
  const withoutAnsi = data.replace(/\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))/g, '')
  const withoutControls = withoutAnsi.replace(/[\u0000-\u001f\u007f]/g, '')
  return withoutControls.trim().length > 0
}

function sendTabSocketMessage(tab: TerminalTab, payload: Record<string, unknown>) {
  if (!tab.socket || tab.socket.readyState !== WebSocket.OPEN) return
  tab.socket.send(JSON.stringify(payload))
}

function sendTabSyncCwd(tab: TerminalTab, path: string) {
  if (!isUsableWorkspacePath(path)) return
  tab.cwd = path
  sendTabSocketMessage(tab, { type: 'sync_cwd', path })
  emitTerminalBindingSummary()
}

function handleTerminalToolCommand(command: unknown) {
  if (command === 'search') openSearchPanel()
}

function openSearchPanel() {
  searchOpen.value = true
  searchPanelPosition.value = null // 重置位置
  detectSearchPanelContainer() // 重新检测容器
  void nextTick(() => {
    searchInputRef.value?.focus()
    if (searchTerm.value) performSearch()
  })
}

function detectSearchPanelContainer() {
  // 向上查找容器：优先查找 .workspace-view（工作台）或 .canvas-workspace（画板）
  let element = terminalPanelRef.value?.parentElement
  while (element) {
    if (element.classList.contains('workspace-view')) {
      searchPanelContainer.value = '.workspace-view'
      return
    }
    if (element.classList.contains('canvas-stage')) {
      searchPanelContainer.value = '.canvas-stage'
      return
    }
    element = element.parentElement
  }
  // 如果没找到，回退到 body
  searchPanelContainer.value = 'body'
}

// --- 搜索面板拖拽监听器（暴露到组件作用域以便 onBeforeUnmount 清理） ---
let searchPanelDragMouseMove: ((e: MouseEvent) => void) | null = null
let searchPanelDragMouseUp: (() => void) | null = null

function stopSearchPanelDrag() {
  if (searchPanelDragMouseMove) {
    document.removeEventListener('mousemove', searchPanelDragMouseMove)
    searchPanelDragMouseMove = null
  }
  if (searchPanelDragMouseUp) {
    document.removeEventListener('mouseup', searchPanelDragMouseUp)
    searchPanelDragMouseUp = null
  }
  searchPanelDragging.value = false
}

function handleSearchPanelDragStart(event: MouseEvent) {
  if (event.button !== 0) return // 只响应左键

  event.preventDefault()
  event.stopPropagation()

  const panel = searchPanelRef.value
  if (!panel) return

  // 获取容器元素
  let container: HTMLElement | null = null
  if (searchPanelContainer.value) {
    container = document.querySelector(searchPanelContainer.value)
  }
  if (!container) return

  const startX = event.clientX
  const startY = event.clientY
  const rect = panel.getBoundingClientRect()
  const containerRect = container.getBoundingClientRect()

  // 如果还没有自定义位置，使用当前位置（相对于容器）
  const initialX = searchPanelPosition.value?.x ?? (rect.left - containerRect.left)
  const initialY = searchPanelPosition.value?.y ?? (rect.top - containerRect.top)

  searchPanelDragging.value = true

  const handleMouseMove = (e: MouseEvent) => {
    e.preventDefault()
    const deltaX = e.clientX - startX
    const deltaY = e.clientY - startY

    // 更新容器尺寸（可能窗口大小改变）
    const currentContainerRect = container!.getBoundingClientRect()
    const maxX = currentContainerRect.width - rect.width
    const maxY = currentContainerRect.height - rect.height

    searchPanelPosition.value = {
      x: Math.max(0, Math.min(maxX, initialX + deltaX)),
      y: Math.max(0, Math.min(maxY, initialY + deltaY))
    }
  }

  const handleMouseUp = () => {
    stopSearchPanelDrag()
  }

  searchPanelDragMouseMove = handleMouseMove
  searchPanelDragMouseUp = handleMouseUp

  document.addEventListener('mousemove', searchPanelDragMouseMove)
  document.addEventListener('mouseup', searchPanelDragMouseUp)
}

function handleSearchEnter(event: KeyboardEvent) {
  if (event.shiftKey) {
    findPreviousInTerminal()
    return
  }
  findNextInTerminal()
}

function queueAutoCopySelection(tab: TerminalTab, selection: string) {
  if (tab.suppressAutoCopySelection) return
  if (!autoCopySelection.value || !selection || selection === tab.lastAutoCopiedSelection) return
  if (tab.autoCopyFrame) window.cancelAnimationFrame(tab.autoCopyFrame)
  tab.autoCopyFrame = window.requestAnimationFrame(() => {
    tab.autoCopyFrame = 0
    const currentSelection = tab.terminal?.getSelection() || selection
    if (!autoCopySelection.value || !currentSelection || currentSelection === tab.lastAutoCopiedSelection) return
    void clipboardProvider.writeText(SYSTEM_CLIPBOARD_SELECTION, currentSelection).then(() => {
      tab.lastAutoCopiedSelection = currentSelection
    }).catch(() => undefined)
  })
}

function handleFileDragOver(event: DragEvent) {
  if (!hasChemSSHFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

function handleFileDrop(event: DragEvent) {
  const payload = readChemSSHFileDrag(event.dataTransfer)
  if (!payload || payload.paths.length === 0) return
  event.preventDefault()
  const tab = activeTab.value
  if (!tab) return
  sendTabSocketMessage(tab, { type: 'input', data: formatFileDragTerminalInput(payload.paths) })
  tab.terminal?.focus()
}

function bindTerminalMiddleClickPaste(tab: TerminalTab, host: HTMLElement) {
  let handledCurrentClick = false

  const handleMouseDown = (event: MouseEvent) => {
    if (event.button !== 1) return
    tab.preservingMiddleClickSelection = true
    handledCurrentClick = false
    event.preventDefault()
    event.stopPropagation()
  }

  const handleClick = (event: MouseEvent) => {
    if (event.button !== 0) return
    scheduleSearchHighlightRefreshAfterInteraction(tab)
  }

  const handleMouseUp = (event: MouseEvent) => {
    if (event.button !== 1) return
    event.preventDefault()
    event.stopPropagation()
    pasteMiddleClickClipboard(tab, handledCurrentClick)
    handledCurrentClick = true
    finishMiddleClickSelection(tab)
  }

  const handleAuxClick = (event: MouseEvent) => {
    if (event.button !== 1) return
    event.preventDefault()
    event.stopPropagation()
    pasteMiddleClickClipboard(tab, handledCurrentClick)
    handledCurrentClick = true
    finishMiddleClickSelection(tab)
  }

  host.addEventListener('mousedown', handleMouseDown, { capture: true })
  host.addEventListener('click', handleClick, { capture: true })
  host.addEventListener('mouseup', handleMouseUp, { capture: true })
  host.addEventListener('auxclick', handleAuxClick, { capture: true })

  return () => {
    host.removeEventListener('mousedown', handleMouseDown, { capture: true })
    host.removeEventListener('click', handleClick, { capture: true })
    host.removeEventListener('mouseup', handleMouseUp, { capture: true })
    host.removeEventListener('auxclick', handleAuxClick, { capture: true })
  }
}

function finishMiddleClickSelection(tab: TerminalTab) {
  window.setTimeout(() => {
    tab.preservingMiddleClickSelection = false
  }, 0)
}

async function pasteMiddleClickClipboard(tab: TerminalTab, alreadyHandled: boolean) {
  if (alreadyHandled) return
  const terminal = tab.terminal
  if (!terminal) return
  try {
    const text = await clipboardProvider.readText(SYSTEM_CLIPBOARD_SELECTION)
    if (text) {
      terminal.paste(text)
    }
  } catch {
    // Clipboard read may fail (e.g. permission denied); fall back to no-op.
  }
  terminal.focus()
}

async function cleanupDetachedSessions() {
  try {
    const sessions = await listTerminalSessions()
    await Promise.all(
      sessions.items
        .filter(item => !item.alive)
        .map(item => closeTerminalSession(item.session_id).catch(() => undefined))
    )
  } catch {
    // Cleanup is best-effort; session creation will report the real error.
  }
}

function handleLayoutChange() {
  measureTabsOverflow()
  requestActiveTabFit()
}

function toggleLargeOpen() {
  largeOpen.value = !largeOpen.value
  requestActiveTabFit()
}

function measureTabsOverflow() {
  const element = tabsRef.value
  if (!element) {
    tabsOverflowing.value = false
    return
  }
  tabsOverflowing.value = element.scrollWidth > element.clientWidth + 1
}

function requestActiveTabFit() {
  const tab = activeTab.value
  if (!tab) return
  void requestTabFit(tab)
}

async function requestTabFit(tab: TerminalTab) {
  await nextTick()
  scheduleTabFit(tab)
  window.setTimeout(() => scheduleTabFit(tab), 30)
  window.setTimeout(() => scheduleTabFit(tab), 120)
  window.setTimeout(() => scheduleTabFit(tab), 300)
  window.setTimeout(() => scheduleTabFit(tab), 600)
}

function scheduleTabFit(tab: TerminalTab) {
  if (tab.fitFrame) window.cancelAnimationFrame(tab.fitFrame)
  tab.fitFrame = window.requestAnimationFrame(() => fitTabTerminal(tab))
}

function fitTabTerminal(tab: TerminalTab) {
  const host = terminalHosts.get(tab.localId)
  if (!tab.terminal || !tab.fitAddon || !host) return
  if (host.clientWidth <= 0 || host.clientHeight <= 0) return

  try {
    tab.fitAddon.fit()
    if (tab.terminal.rows > 0) tab.terminal.refresh(0, tab.terminal.rows - 1)
    sendTabSocketMessage(tab, { type: 'resize', rows: tab.terminal.rows, cols: tab.terminal.cols })
  } catch {
    // Fit can fail while the tab is mounting or temporarily hidden.
  }
}

function tabTitle(tab: TerminalTab) {
  return pathBaseName(tab.cwd) || tab.sessionId || `Term ${tabs.value.findIndex(item => item.localId === tab.localId) + 1}`
}

function pathBaseName(path: string) {
  const normalized = path.trim().replace(/[\\/]+$/, '')
  if (!normalized) return ''
  const parts = normalized.split(/[\\/]/).filter(Boolean)
  if (parts.length > 0) return parts[parts.length - 1]
  return normalized
}
</script>
