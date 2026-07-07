import type { FileItem } from './files'
import { downloadSelectionUrl } from './http'

export const CHEMSSH_FILE_DRAG_MIME = 'application/x-chemssh-files'
export const CHEMSSH_FILE_PATHS_MIME = 'application/x-chemssh-file-paths'

export interface ChemSSHFileDragPayload {
  source: 'chemssh:file-manager'
  version: 1
  paths: string[]
  items: Pick<FileItem, 'name' | 'path' | 'type' | 'size' | 'mtime' | 'extension' | 'preview_type' | 'format'>[]
}

let activeChemSSHFileDragPayload: ChemSSHFileDragPayload | null = null

export function createChemSSHFileDragPayload(items: FileItem[]): ChemSSHFileDragPayload {
  return {
    source: 'chemssh:file-manager',
    version: 1,
    paths: items.map(item => item.path),
    items: items.map(item => ({
      name: item.name,
      path: item.path,
      type: item.type,
      size: item.size,
      mtime: item.mtime,
      extension: item.extension,
      preview_type: item.preview_type,
      format: item.format
    }))
  }
}

export function formatFileDragTerminalInput(paths: string[]) {
  return paths.length === 0 ? '' : ` ${paths.join(' ')}`
}

export function getFileDragDownloadUrl(paths: string[], options: { forceArchive?: boolean } = {}) {
  const relative = downloadSelectionUrl(paths, options)
  return new URL(relative, window.location.href).href
}

export function writeChemSSHFileDrag(dataTransfer: DataTransfer, items: FileItem[]) {
  const payload = createChemSSHFileDragPayload(items)
  activeChemSSHFileDragPayload = payload
  const paths = payload.paths
  const downloadUrl = getFileDragDownloadUrl(paths, { forceArchive: paths.length === 1 && items[0]?.type === 'directory' })
  const filename = paths.length === 1 ? items[0]?.name ?? 'chemssh-file' : 'chemssh-selection.zip'

  dataTransfer.effectAllowed = 'copyMove'
  dataTransfer.setData(CHEMSSH_FILE_DRAG_MIME, JSON.stringify(payload))
  dataTransfer.setData(CHEMSSH_FILE_PATHS_MIME, JSON.stringify(paths))
  dataTransfer.setData('text/plain', formatFileDragTerminalInput(paths))
  dataTransfer.setData('text/uri-list', downloadUrl)
  const mediaType = paths.length === 1 && items[0]?.type === 'file' ? 'application/octet-stream' : 'application/zip'
  dataTransfer.setData('DownloadURL', `${mediaType}:${filename}:${downloadUrl}`)
}

export function getActiveChemSSHFileDragPayload() {
  return activeChemSSHFileDragPayload
}

export function clearActiveChemSSHFileDragPayload() {
  activeChemSSHFileDragPayload = null
}

export function readChemSSHFileDrag(dataTransfer: DataTransfer | null): ChemSSHFileDragPayload | null {
  if (!dataTransfer) return null

  const payload = readJson(dataTransfer.getData(CHEMSSH_FILE_DRAG_MIME))
  if (isChemSSHFileDragPayload(payload)) return payload

  const paths = readJson(dataTransfer.getData(CHEMSSH_FILE_PATHS_MIME))
  if (Array.isArray(paths) && paths.every(path => typeof path === 'string')) {
    return {
      source: 'chemssh:file-manager',
      version: 1,
      paths,
      items: paths.map(path => ({
        name: pathBaseName(path),
        path,
        type: 'file',
        size: null,
        mtime: '',
        extension: '',
        preview_type: 'file',
        format: null
      }))
    }
  }

  return null
}

export function hasChemSSHFileDrag(event: DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes(CHEMSSH_FILE_DRAG_MIME)
}

function readJson(value: string) {
  if (!value) return null
  try {
    return JSON.parse(value) as unknown
  } catch {
    return null
  }
}

function isChemSSHFileDragPayload(value: unknown): value is ChemSSHFileDragPayload {
  if (!value || typeof value !== 'object') return false
  const payload = value as Partial<ChemSSHFileDragPayload>
  return payload.source === 'chemssh:file-manager'
    && payload.version === 1
    && Array.isArray(payload.paths)
    && payload.paths.every(path => typeof path === 'string')
}

function pathBaseName(path: string) {
  const normalized = path.trim().replace(/[\\/]+$/, '')
  if (!normalized) return path
  return normalized.split(/[\\/]/).filter(Boolean).pop() ?? normalized
}
