import { ref } from 'vue'
import { getClientPreferences, saveClientPreferencesPatch } from '../api/clientPreferences'

const TERMINAL_FONT_SIZE_STORAGE_KEY = 'chemssh.terminal.fontSize'
const TERMINAL_VIM_COMPATIBILITY_STORAGE_KEY = 'chemssh.terminal.vimCompatibility'
const TERMINAL_AUTO_COPY_SELECTION_STORAGE_KEY = 'chemssh.terminal.autoCopySelection'
const TERMINAL_REFRESH_FILE_MANAGER_AFTER_COMMAND_STORAGE_KEY = 'chemssh.terminal.refreshFileManagerAfterCommand'
const DEFAULT_TERMINAL_FONT_SIZE = 13
const TERMINAL_FONT_SIZE_MIN = 10
const TERMINAL_FONT_SIZE_MAX = 24

function clampTerminalFontSize(value: number) {
  if (!Number.isFinite(value)) return DEFAULT_TERMINAL_FONT_SIZE
  return Math.min(TERMINAL_FONT_SIZE_MAX, Math.max(TERMINAL_FONT_SIZE_MIN, Math.round(value)))
}

export function useTerminalSettings() {
  const terminalFontSize = ref(readStoredTerminalFontSize())
  const vimCompatibilityMode = ref(readStoredVimCompatibilityMode())
  const autoCopySelection = ref(readStoredAutoCopySelection())
  const refreshFileManagerAfterCommand = ref(readStoredRefreshFileManagerAfterCommand())

  function setTerminalFontSize(value: number | undefined) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    terminalFontSize.value = clampTerminalFontSize(value)
  }

  function readStoredTerminalFontSize() {
    if (typeof window === 'undefined') return DEFAULT_TERMINAL_FONT_SIZE
    try {
      const stored = window.localStorage.getItem(TERMINAL_FONT_SIZE_STORAGE_KEY)
      if (stored === null) return DEFAULT_TERMINAL_FONT_SIZE
      return clampTerminalFontSize(Number(stored))
    } catch {
      return DEFAULT_TERMINAL_FONT_SIZE
    }
  }

  function storeTerminalFontSize(value: number) {
    try {
      window.localStorage.setItem(TERMINAL_FONT_SIZE_STORAGE_KEY, String(value))
    } catch {
    }
  }

  function readStoredVimCompatibilityMode() {
    if (typeof window === 'undefined') return true
    try {
      const stored = window.localStorage.getItem(TERMINAL_VIM_COMPATIBILITY_STORAGE_KEY)
      return stored === null ? true : stored !== 'false'
    } catch {
      return true
    }
  }

  function storeVimCompatibilityMode(value: boolean) {
    try {
      window.localStorage.setItem(TERMINAL_VIM_COMPATIBILITY_STORAGE_KEY, String(value))
    } catch {
    }
  }

  function readStoredAutoCopySelection() {
    const prefs = getClientPreferences().terminal?.autoCopySelection
    if (typeof prefs === 'boolean') return prefs
    if (typeof window === 'undefined') return false
    try {
      return window.localStorage.getItem(TERMINAL_AUTO_COPY_SELECTION_STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  }

  function storeAutoCopySelection(value: boolean) {
    try {
      window.localStorage.setItem(TERMINAL_AUTO_COPY_SELECTION_STORAGE_KEY, String(value))
    } catch {
    }
    void saveClientPreferencesPatch({ terminal: { autoCopySelection: value } })
  }

  function readStoredRefreshFileManagerAfterCommand() {
    const prefs = getClientPreferences().terminal?.refreshFileManagerAfterCommand
    if (typeof prefs === 'boolean') return prefs
    if (typeof window === 'undefined') return true
    try {
      const stored = window.localStorage.getItem(TERMINAL_REFRESH_FILE_MANAGER_AFTER_COMMAND_STORAGE_KEY)
      return stored === null ? true : stored !== 'false'
    } catch {
      return true
    }
  }

  function storeRefreshFileManagerAfterCommand(value: boolean) {
    try {
      window.localStorage.setItem(TERMINAL_REFRESH_FILE_MANAGER_AFTER_COMMAND_STORAGE_KEY, String(value))
    } catch {
    }
    void saveClientPreferencesPatch({ terminal: { refreshFileManagerAfterCommand: value } })
  }

  return {
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
  }
}
