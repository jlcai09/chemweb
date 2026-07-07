import { ElMessageBox } from 'element-plus'
import type { SystemInfo } from './system'
import { t } from '../i18n'

export function isOversized(sizeBytes: number, maxMb: number): boolean {
  return sizeBytes > maxMb * 1024 * 1024
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/**
 * Prompt the user to confirm uploading files that exceed the size limit.
 * @returns `true` to proceed (or when the limit is unknown / nothing is oversized),
 *          `false` to cancel the batch.
 */
export async function confirmOversizedUpload(
  systemInfo: SystemInfo | null,
  entries: { file: File; relativePath: string; displayPath: string }[],
): Promise<boolean> {
  if (!systemInfo) return true // no config yet — let the backend enforce the limit

  const maxMb = systemInfo.max_upload_size_mb
  const limitBytes = maxMb * 1024 * 1024
  const oversized = entries.filter(entry => entry.file.size > limitBytes)
  if (oversized.length === 0) return true

  const list = oversized
    .map(entry => `${entry.file.name}: ${formatBytes(entry.file.size)}`)
    .join('\n')

  try {
    await ElMessageBox.confirm(
      t('upload.oversizedMessage', { list, limit: maxMb }),
      t('upload.oversizedTitle'),
      {
        confirmButtonText: t('upload.oversizedConfirm'),
        cancelButtonText: t('upload.oversizedCancel'),
        type: 'warning'
      }
    )
    return true
  } catch {
    return false
  }
}
