import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSystemStore } from '../stores/system'
import { uploadFile, type UploadProgress } from '../api/files'
import {
  filesToUploadEntries,
  prepareUploadEntries,
  type UploadConflictAction,
  type UploadConflictResolution,
  type UploadEntry
} from '../api/uploadEntries'
import { confirmOversizedUpload } from '../api/uploadSizeCheck'
import { t } from '../i18n'

type UploadState = {
  active: boolean
  currentFile: string
  done: number
  totalFiles: number
  loaded: number
  total: number
  speedBytesPerSecond: number
}

type UploadConflictDialogState = {
  visible: boolean
  mode: 'upload' | 'move'
  name: string
  applyAll: boolean
  resolve: ((resolution: UploadConflictResolution) => void) | null
}

type UploadBatchResult = {
  uploaded: number
  failed: number
  total: number
  cancelled: boolean
  message: string
}

function formatUploadSpeed(bytesPerSecond: number) {
  if (!Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) return '0.0 MB/s'
  return `${(bytesPerSecond / 1024 / 1024).toFixed(1)} MB/s`
}

export function useWorkspaceUpload(
  currentPath: { value: string },
  onUploadComplete: () => void | Promise<void>
) {
  const systemStore = useSystemStore()
  const uploadState = ref<UploadState>({
    active: false,
    currentFile: '',
    done: 0,
    totalFiles: 0,
    loaded: 0,
    total: 0,
    speedBytesPerSecond: 0
  })
  const uploadConflictDialog = ref<UploadConflictDialogState>({
    visible: false,
    mode: 'upload',
    name: '',
    applyAll: false,
    resolve: null
  })
  const uploadProgressOpen = ref(false)

  const uploadPercent = computed(() => {
    if (!uploadState.value.active || uploadState.value.total <= 0) return 0
    return Math.min(100, Math.max(0, Math.round((uploadState.value.loaded / uploadState.value.total) * 100)))
  })

  const uploadSpeedLabel = computed(() => formatUploadSpeed(uploadState.value.speedBytesPerSecond))

  async function handleUploadEntries(entries: UploadEntry[], targetPath = currentPath.value): Promise<UploadBatchResult> {
    const emptyResult: UploadBatchResult = {
      uploaded: 0,
      failed: 0,
      total: entries.length,
      cancelled: false,
      message: ''
    }
    if (entries.length === 0) return emptyResult

    const preparedResult = await prepareUploadEntries(entries, {
      targetPath,
      promptConflict: promptUploadConflict
    })
    if (preparedResult.invalidCount > 0) ElMessage.error(t('message.uploadInvalidPath', { count: preparedResult.invalidCount }))
    if (preparedResult.renamedCount > 0) ElMessage.info(t('message.uploadPathRenamed', { count: preparedResult.renamedCount }))
    const prepared = preparedResult.entries
    if (prepared.length === 0) {
      return {
        uploaded: 0,
        failed: preparedResult.invalidCount,
        total: entries.length,
        cancelled: preparedResult.cancelled,
        message: preparedResult.invalidCount > 0 ? t('message.uploadInvalidPath', { count: preparedResult.invalidCount }) : ''
      }
    }

    const sizeCheck = await confirmOversizedUpload(systemStore.systemInfo, prepared)
    if (!sizeCheck) {
      return { uploaded: 0, failed: prepared.length, total: entries.length, cancelled: true, message: t('terminal.transferCancelled') }
    }

    let uploaded = 0
    let firstError: unknown = null
    const totalBytes = prepared.reduce((sum, entry) => sum + entry.file.size, 0)
    let completedBytes = 0
    uploadState.value = {
      active: true,
      currentFile: prepared[0]?.displayPath ?? '',
      done: 0,
      totalFiles: prepared.length,
      loaded: 0,
      total: totalBytes,
      speedBytesPerSecond: 0
    }
    uploadProgressOpen.value = false

    for (const entry of prepared) {
      const file = entry.file
      try {
        uploadState.value.currentFile = entry.displayPath
        let lastProgressLoaded = 0
        let lastProgressAt = performance.now()
        await uploadFile(targetPath, file, {
          relativePath: entry.relativePath,
          onProgress: (progress: UploadProgress) => {
            const fileLoaded = Math.min(progress.loaded, progress.total || file.size)
            const now = performance.now()
            const elapsedSeconds = Math.max((now - lastProgressAt) / 1000, 0.001)
            const deltaBytes = Math.max(0, fileLoaded - lastProgressLoaded)
            const instantSpeed = deltaBytes / elapsedSeconds
            uploadState.value.speedBytesPerSecond =
              uploadState.value.speedBytesPerSecond === 0
                ? instantSpeed
                : uploadState.value.speedBytesPerSecond * 0.72 + instantSpeed * 0.28
            lastProgressLoaded = fileLoaded
            lastProgressAt = now
            uploadState.value.loaded = Math.min(totalBytes, completedBytes + fileLoaded)
          }
        })
        uploaded += 1
        completedBytes += file.size
        uploadState.value.done = uploaded
        uploadState.value.loaded = Math.min(totalBytes, completedBytes)
      } catch (error) {
        firstError ??= error
        completedBytes += file.size
        uploadState.value.loaded = Math.min(totalBytes, completedBytes)
      }
    }

    await onUploadComplete()
    uploadState.value.active = false
    uploadProgressOpen.value = false

    const failed = prepared.length - uploaded + preparedResult.invalidCount
    const message = firstError instanceof Error
      ? firstError.message
      : preparedResult.invalidCount > 0
        ? t('message.uploadInvalidPath', { count: preparedResult.invalidCount })
        : t('message.uploadFailed')

    if (failed === 0) {
      ElMessage.success(
        prepared.length === 1 ? t('message.uploadComplete') : t('message.uploadCompleteMany', { count: prepared.length })
      )
    } else {
      ElMessage.error(t('message.uploadPartial', { message, uploaded, failed }))
    }

    return {
      uploaded,
      failed,
      total: entries.length,
      cancelled: false,
      message
    }
  }

  async function handleUpload(files: File[]) {
    const entries = filesToUploadEntries(files)
    await handleUploadEntries(entries, currentPath.value)
  }

  async function handleTerminalTransferUpload(path: string, files: File[]) {
    const entries = filesToUploadEntries(files)
    const result = await handleUploadEntries(entries, path)
    if (result.cancelled) throw new Error(t('terminal.transferCancelled'))
    if (result.failed > 0) {
      throw new Error(
        t('message.uploadPartial', {
          message: result.message || t('message.uploadFailed'),
          uploaded: result.uploaded,
          failed: result.failed
        })
      )
    }
    if (entries.length > 0 && result.uploaded === 0) throw new Error(t('message.uploadFailed'))
  }

  async function promptUploadConflict(name: string) {
    return new Promise<UploadConflictResolution>(resolve => {
      uploadConflictDialog.value = {
        visible: true,
        mode: 'upload',
        name,
        applyAll: false,
        resolve
      }
    })
  }

  async function promptMoveConflict(name: string) {
    return new Promise<UploadConflictResolution>(resolve => {
      uploadConflictDialog.value = {
        visible: true,
        mode: 'move',
        name,
        applyAll: false,
        resolve
      }
    })
  }

  function chooseUploadConflict(action: UploadConflictAction) {
    const dialog = uploadConflictDialog.value
    if (!dialog.resolve) return
    dialog.resolve({ action, applyAll: dialog.applyAll })
    uploadConflictDialog.value = {
      visible: false,
      mode: 'upload',
      name: '',
      applyAll: false,
      resolve: null
    }
  }

  return {
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
  }
}
