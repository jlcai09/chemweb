export type CanvasWindowType = 'file-manager' | 'queue' | 'tail' | 'preview' | 'terminal' | 'plugin'

export const CANVAS_WINDOW_MINIMIZED_HEIGHT = 42

export interface CanvasViewport {
  x: number
  y: number
  zoom: number
}

export interface CanvasWindowState {
  id: string
  type: CanvasWindowType
  title: string
  x: number
  y: number
  width: number
  height: number
  zIndex: number
  minimized?: boolean
  payload?: Record<string, unknown>
}

export interface CanvasFileManagerBindingTarget {
  id: string
  title: string
  path: string
  color: string
}

export interface CanvasTerminalTabBinding {
  tabId: string
  title: string
  cwd: string
  syncMode: 'off' | 'follow' | 'bidirectional'
  boundFileManagerId: string | null
  active: boolean
}

export interface CanvasBoard {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  viewport: CanvasViewport
  windows: CanvasWindowState[]
}

export interface CanvasBoardState {
  version: 1
  activeBoardId: string | null
  boards: CanvasBoard[]
}

export interface ThemePreferences {
  animatedBackdrop: boolean
  glassBlur: boolean
}

export interface ClientPreferences {
  version: 1
  terminal?: {
    fontSize?: number
    autoCopySelection?: boolean
    refreshFileManagerAfterCommand?: boolean
    tabs?: CanvasTerminalTabBinding[]
  }
  logs?: {
    tailLines?: number
  }
  workspace?: {
    fileTreeWidth?: number | null
    sidePaneWidth?: number | null
    queueHeight?: number | null
    currentPath?: string
    showHiddenFiles?: boolean
    activeWorkPanelId?: string
  }
  canvas?: {
    lastBoardId?: string
    defaultWindowWidth?: number
    defaultWindowHeight?: number
  }
  theme?: Partial<ThemePreferences>
}

export const DEFAULT_CANVAS_VIEWPORT: CanvasViewport = {
  x: 120,
  y: 80,
  zoom: 1
}

export function createDefaultBoard(now = new Date().toISOString()): CanvasBoard {
  return {
    id: `board_${newIdToken()}`,
    name: 'Board 1',
    createdAt: now,
    updatedAt: now,
    viewport: { ...DEFAULT_CANVAS_VIEWPORT },
    windows: []
  }
}

export function createDefaultBoardState(): CanvasBoardState {
  const board = createDefaultBoard()
  return {
    version: 1,
    activeBoardId: board.id,
    boards: [board]
  }
}

export function newIdToken() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}
