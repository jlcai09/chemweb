<template>
  <div class="canvas-preview-window">
    <div class="canvas-preview-pathbar">
      <el-input
        v-model="pathInput"
        class="canvas-file-path"
        size="small"
        clearable
        spellcheck="false"
        :placeholder="t('canvas.previewPath')"
        @keyup.enter="openPath(pathInput)"
      >
        <template #append>
          <el-tooltip :content="t('toolbar.go')" placement="bottom" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
            <el-button :icon="ArrowRight" @click="openPath(pathInput)" />
          </el-tooltip>
        </template>
      </el-input>
    </div>
    <FilePreview
      :file="preview"
      :ase-structure="asePreview"
      :mode="previewMode"
      :loading="loading"
      :structure-candidate="structureCandidate"
      :structure-error="previewError"
      @update:mode="setPreviewMode"
      @refresh="refreshPreview"
      @save="savePreview"
      @dragover="handlePreviewDragOver"
      @drop="handlePreviewDrop"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'
import { readFile, writeFile, type FileItem, type FileReadResponse, type PreviewType } from '../../api/files'
import { hasChemSSHFileDrag, readChemSSHFileDrag } from '../../api/fileDrag'
import { extensionFromName, isStructurePreviewPath, pathBaseName } from '../../api/fileTypes'
import {
  type FilePreviewProvider,
  type PreviewProbeResponse,
  providerMatchesItem
} from '../../api/filePreviewProviders'
import {
  confirmLargePreview,
  isLargePreviewError,
  normalizeTextLineEndings,
  previewApiErrorMessage
} from '../../api/previewUtils'
import { ASE_STRUCTURE_SOURCE, readStructurePreview } from '../../api/structures'
import { request } from '../../api/http'
import { t } from '../../i18n'
import type { AsePreviewResponse, StructureSource } from '../../types/structure'
import FilePreview from '../FilePreview.vue'

type PreviewMode = 'structure' | 'text'
type PreviewMetadata = {
  previewType?: PreviewType | null
  format?: string | null
}

const props = defineProps<{
  path?: string | null
  previewType?: PreviewType | null
  format?: string | null
  previewProviders?: FilePreviewProvider[]
}>()

const emit = defineEmits<{
  'path-change': [path: string, metadata?: PreviewMetadata]
}>()

const pathInput = ref(props.path ?? '')
const preview = ref<FileReadResponse | null>(null)
const asePreview = ref<AsePreviewResponse | null>(null)
const currentStructureSource = ref<StructureSource>(ASE_STRUCTURE_SOURCE)
const previewMode = ref<PreviewMode>('text')
const previewError = ref<string | null>(null)
const loading = ref(false)
const localMetadataPath = ref<string | null>(null)
const localPreviewType = ref<PreviewType | null>(null)
const localFormat = ref<string | null>(null)
const forcedText = new Set<string>()
const forcedStructure = new Set<string>()
let previewRequestSerial = 0
let structurePreviewAbortController: AbortController | null = null

const structureCandidate = computed(() => Boolean(pathInput.value && isStructureCandidate(pathInput.value)))

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

watch(
  () => [props.path, props.previewType, props.format] as const,
  ([path, previewType, format]) => {
    if (!path) {
      if (pathInput.value || preview.value || asePreview.value) void openPath('')
      return
    }
    const hasLoadedContent = preview.value || asePreview.value
    const metadataChanged = metadataPreviewType(path) !== previewType || structureFormat(path) !== format
    if (path === pathInput.value && hasLoadedContent && !metadataChanged) return
    pathInput.value = path
    void openPath(path, { previewType, format })
  },
  { immediate: true }
)

async function openPath(path: string, metadata?: PreviewMetadata) {
  const nextPath = path.trim()
  if (!nextPath) {
    previewRequestSerial += 1
    preview.value = null
    asePreview.value = null
    currentStructureSource.value = ASE_STRUCTURE_SOURCE
    clearLocalMetadata()
    abortStructurePreviewRequest()
    pathInput.value = ''
    loading.value = false
    previewError.value = null
    emit('path-change', '')
    return
  }
  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  abortStructurePreviewRequest()
  applyLocalMetadata(nextPath, metadata)
  pathInput.value = nextPath
  emit('path-change', nextPath, metadata)
  loading.value = true
  previewError.value = null
  try {
    // Try plugin preview provider first
    const provider = await resolvePreviewProviderByPath(nextPath)
    if (!isCurrentRequest()) return
    const providerSource = provider ? providerStructureSource(provider) : null
    if (providerSource) {
      currentStructureSource.value = providerSource
      previewMode.value = 'structure'
      if (preview.value?.path !== nextPath) preview.value = null
      const structure = await readStructureWithConfirmation(nextPath, providerSource, structureFormat(nextPath), nextStructurePreviewSignal())
      if (!isCurrentRequest()) return
      if (structure) asePreview.value = structure
      return
    }

    if (isStructureCandidate(nextPath)) {
      currentStructureSource.value = ASE_STRUCTURE_SOURCE
      previewMode.value = 'structure'
      if (preview.value?.path !== nextPath) preview.value = null
      try {
        const structure = await readStructureWithConfirmation(nextPath, ASE_STRUCTURE_SOURCE, structureFormat(nextPath), nextStructurePreviewSignal())
        if (!isCurrentRequest()) return
        if (structure) asePreview.value = structure
        return
      } catch (error) {
        if (!isCurrentRequest() || isAbortError(error)) return
        previewError.value = previewApiErrorMessage(error)
        previewMode.value = 'text'
      }
    }
    currentStructureSource.value = ASE_STRUCTURE_SOURCE
    const file = await readTextWithConfirmation(nextPath)
    if (!isCurrentRequest()) return
    preview.value = file
    asePreview.value = null
    previewMode.value = 'text'
  } catch (error) {
    if (!isCurrentRequest()) return
    preview.value = null
    asePreview.value = null
    if (!isAbortError(error)) ElMessage.error(previewApiErrorMessage(error))
  } finally {
    if (isCurrentRequest()) loading.value = false
  }
}

async function refreshPreview() {
  if (!pathInput.value) return
  await openPath(pathInput.value, {
    previewType: metadataPreviewType(pathInput.value),
    format: structureFormat(pathInput.value)
  })
}

async function setPreviewMode(mode: PreviewMode) {
  previewMode.value = mode
  if (!pathInput.value) return
  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  if (mode !== 'structure') abortStructurePreviewRequest()
  loading.value = true
  try {
    const path = pathInput.value
    if (mode === 'structure') {
      const structure = await readStructureWithConfirmation(path, currentStructureSource.value, structureFormat(path), nextStructurePreviewSignal())
      if (!isCurrentRequest()) return
      if (structure) asePreview.value = structure
    } else if (!preview.value || preview.value.path !== path) {
      const file = await readTextWithConfirmation(path)
      if (!isCurrentRequest()) return
      preview.value = file
    }
    if (!isCurrentRequest()) return
  } catch (error) {
    if (!isCurrentRequest() || isAbortError(error)) return
    ElMessage.error(previewApiErrorMessage(error))
  } finally {
    if (isCurrentRequest()) loading.value = false
  }
}

async function savePreview(content: string) {
  if (!preview.value) return
  const path = preview.value.path
  const requestId = ++previewRequestSerial
  const isCurrentRequest = () => requestId === previewRequestSerial
  try {
    await writeFile(path, normalizeTextLineEndings(content))
    if (!isCurrentRequest()) return
    ElMessage.success(t('message.saved'))
    const file = await readTextWithConfirmation(path)
    if (!isCurrentRequest()) return
    preview.value = file
  } catch (error) {
    if (!isCurrentRequest()) return
    ElMessage.error(error instanceof Error ? error.message : t('message.saveFailed'))
  }
}

async function readTextWithConfirmation(path: string) {
  try {
    return await readFile(path, forcedText.has(path))
  } catch (error) {
    if (!isLargePreviewError(error, 'FILE_TOO_LARGE')) throw error
    const confirmed = await confirmLargePreview(error, 'text')
    if (!confirmed) return null
    forcedText.add(path)
    return readFile(path, true)
  }
}

async function readStructureWithConfirmation(path: string, source: StructureSource, format?: string | null, signal?: AbortSignal) {
  const cacheKey = `${source.id}:${path}`
  try {
    return await readStructurePreview(source, path, format, forcedStructure.has(cacheKey), { signal })
  } catch (error) {
    if (isAbortError(error)) throw error
    if (!isLargePreviewError(error, 'STRUCTURE_FILE_TOO_LARGE')) throw error
    const confirmed = await confirmLargePreview(error, 'structure')
    throwIfAborted(signal)
    if (!confirmed) return null
    forcedStructure.add(cacheKey)
    return readStructurePreview(source, path, format, true, { signal })
  }
}

function propMetadataApplies(path: string) {
  return Boolean(props.path && path === props.path)
}

function localMetadataApplies(path: string) {
  return localMetadataPath.value === path
}

function metadataPreviewType(path: string) {
  if (localMetadataApplies(path)) return localPreviewType.value
  return propMetadataApplies(path) ? props.previewType : null
}

function structureFormat(path: string) {
  if (localMetadataApplies(path)) return localFormat.value
  return propMetadataApplies(path) ? props.format : null
}

function isStructureCandidate(path: string) {
  return isStructurePreviewPath(path, metadataPreviewType(path))
}

function applyLocalMetadata(path: string, metadata?: PreviewMetadata) {
  if (!metadata) {
    clearLocalMetadata()
    return
  }
  localMetadataPath.value = path
  localPreviewType.value = metadata.previewType ?? null
  localFormat.value = metadata.format ?? null
}

function clearLocalMetadata() {
  localMetadataPath.value = null
  localPreviewType.value = null
  localFormat.value = null
}

function handlePreviewDragOver(event: DragEvent) {
  if (!hasChemSSHFileDrag(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
}

async function resolvePreviewProviderByPath(path: string) {
  const providers = props.previewProviders
  if (!providers || providers.length === 0) return null
  const item = providerCandidateItem(path)
  const candidate = providers.filter(provider => providerMatchesItem(provider, item))
  for (const provider of candidate) {
    if (!provider.probe?.apiPath || !provider.apiBase) return provider
    try {
      const response = await request<PreviewProbeResponse>(`${provider.apiBase}${provider.probe.apiPath}`, {
        method: provider.probe.method ?? 'POST',
        body: JSON.stringify({ path, item })
      })
      if (response.can_preview) return provider
    } catch {
      // Ignore failed probes.
    }
  }
  return null
}

function providerCandidateItem(path: string): FileItem {
  const name = pathBaseName(path)
  const previewType = metadataPreviewType(path) ?? (isStructurePreviewPath(path, null) ? 'structure' : 'file')
  return {
    name,
    path,
    type: 'file',
    size: null,
    mtime: '',
    extension: extensionFromName(name),
    preview_type: previewType,
    format: structureFormat(path)
  }
}

function providerStructureSource(provider: FilePreviewProvider): StructureSource | null {
  const source = provider.open?.structureSource
  if (!source?.apiBase) return null
  return source
}

function handlePreviewDrop(event: DragEvent) {
  const payload = readChemSSHFileDrag(event.dataTransfer)
  const item = payload?.items[0]
  const path = item?.path ?? payload?.paths[0]
  if (!path) return
  event.preventDefault()
  void openPath(path, {
    previewType: item?.preview_type ?? null,
    format: item?.format ?? null
  })
}

</script>
