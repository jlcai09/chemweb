<template>
  <div class="molecule-viewer">
    <div class="viewer-toolbar">
      <div class="viewer-style-group">
        <el-radio-group v-model="selectedStyle" size="small">
          <el-radio-button label="stick">{{ t('viewer.styleStick') }}</el-radio-button>
          <el-radio-button label="sphere">{{ t('viewer.styleSphere') }}</el-radio-button>
          <el-radio-button label="line">{{ t('viewer.styleLine') }}</el-radio-button>
        </el-radio-group>
      </div>

      <div v-if="asePreview?.is_trajectory" class="trajectory-controls">
        <button
          class="frame-mode-button"
          type="button"
          :aria-label="t('viewer.toggleFrameNumbering')"
          :aria-pressed="frameNumberMode === 'frame'"
          @click="toggleFrameNumberMode"
        >
          {{ frameNumberModeLabel }}
        </button>
        <el-input-number
          v-model="frameDisplayInput"
          class="frame-number"
          size="small"
          :min="frameDisplayMin"
          :max="frameDisplayMax"
          :step="1"
          controls-position="right"
          @change="scheduleFrameChange"
        />
        <span class="frame-total">/ {{ frameDisplayMax }}</span>
        <el-slider
          v-model="frameDisplayInput"
          class="frame-slider"
          :min="frameDisplayMin"
          :max="frameDisplayMax"
          :show-tooltip="false"
          size="small"
          @input="scheduleFrameChange"
        />
      </div>

      <div class="viewer-toolbar-actions">
        <el-tooltip :content="t('toolbar.refresh')" placement="bottom" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
          <el-button :icon="Refresh" circle size="small" @click="refreshStructure" />
        </el-tooltip>
        <el-dropdown trigger="click" @command="handleExportCommand">
          <el-button :icon="Download" circle size="small" />
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="original">{{ t('viewer.exportOriginal') }}</el-dropdown-item>
              <el-dropdown-item command="current-xyz">{{ t('viewer.exportCurrentXYZ') }}</el-dropdown-item>
              <el-dropdown-item command="current-xsd">{{ t('viewer.exportCurrentXSD') }}</el-dropdown-item>
              <el-dropdown-item v-if="asePreview?.is_trajectory" command="trajectory-xyz" divided>{{ t('viewer.exportTrajectoryXYZ') }}</el-dropdown-item>
              <el-dropdown-item v-if="asePreview?.is_trajectory" command="trajectory-arc">{{ t('viewer.exportTrajectoryArc') }}</el-dropdown-item>
              <el-dropdown-item command="screenshot">{{ t('viewer.exportScreenshot') }}</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
    <div class="viewer-stage">
      <div ref="container" class="viewer-canvas" />
      <div v-if="asePreview" class="atom-index-base-status" aria-live="polite">
        {{ atomIndexBaseStatus }}
        <span v-if="asePreview?.file_incomplete" class="file-incomplete-hint">{{ t('viewer.fileIncomplete') }}</span>
      </div>
      <div v-if="showExpandedStructureWarning" class="structure-warning-banner" role="status">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ structureWarningText }}</span>
        <el-tooltip :content="t('panel.close')" placement="top" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
          <button class="structure-warning-close" type="button" :aria-label="t('panel.close')" @click="dismissStructureWarning">
            <el-icon><Close /></el-icon>
          </button>
        </el-tooltip>
      </div>
      <el-tooltip
        v-else-if="showCollapsedStructureWarning"
        :content="structureWarningText"
        placement="top"
        popper-class="chemssh-passive-tooltip"
        :enterable="false"
        :show-after="150"
      >
        <button class="structure-warning-indicator" type="button" :aria-label="structureWarningText">
          <el-icon><WarningFilled /></el-icon>
        </button>
      </el-tooltip>
      <div v-if="asePreview" class="viewer-floating-tools">
        <el-popover trigger="click" placement="right-start" :width="260" :teleported="false">
          <template #reference>
            <el-button :aria-label="t('viewer.bondSettings')" :icon="Connection" circle size="small" />
          </template>
          <div class="bond-settings-panel">
            <div class="bond-settings-header">
              <span>{{ t('viewer.bondScale') }}</span>
              <strong>{{ bondScale.toFixed(2) }}</strong>
            </div>
            <el-slider
              v-model="bondScale"
              class="bond-settings-slider"
              :min="0.6"
              :max="2"
              :step="0.01"
              size="small"
            />
            <div class="bond-settings-actions">
              <el-button size="small" text @click="resetBondScale">{{ t('viewer.resetBondScale') }}</el-button>
            </div>
          </div>
        </el-popover>

        <el-popover trigger="click" placement="right-start" :width="220" :teleported="false">
          <template #reference>
            <el-button :aria-label="t('viewer.displaySettings')" :icon="View" circle size="small" />
          </template>
          <div class="display-settings-panel">
            <el-checkbox v-model="showAtomIndex">{{ t('viewer.atomIndex') }}</el-checkbox>
            <el-checkbox v-model="showAtomTag">{{ t('viewer.atomTag') }}</el-checkbox>
            <div class="atom-index-base-row">
              <span>{{ t('viewer.atomIndexBase') }}</span>
              <el-segmented v-model="atomIndexBase" :options="atomIndexBaseOptions" size="small" />
            </div>
          </div>
        </el-popover>

        <el-tooltip :content="t('viewer.resetView')" placement="right" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
          <el-button :aria-label="t('viewer.resetView')" :icon="ResetViewIcon" circle size="small" @click="resetView" />
        </el-tooltip>

        <el-popover trigger="click" placement="right-start" :width="236" :disabled="!structureHasCell" :teleported="false">
          <template #reference>
            <el-button
              :aria-label="t('viewer.supercellSettings')"
              :type="hasSupercell ? 'success' : undefined"
              :disabled="!structureHasCell"
              :icon="Grid"
              circle
              size="small"
            />
          </template>
          <div class="supercell-settings-panel">
            <div class="supercell-settings-header">
              <span>{{ t('viewer.supercell') }}</span>
              <strong>{{ supercellX }} x {{ supercellY }} x {{ supercellZ }}</strong>
            </div>
            <div class="supercell-axis-row">
              <span class="supercell-axis-label">X</span>
              <el-input-number
                v-model="supercellX"
                class="supercell-axis-input"
                size="small"
                :min="1"
                :max="supercellAxisMax('x')"
                :step="1"
                step-strictly
                controls-position="right"
                @change="clampSupercell"
              />
            </div>
            <div class="supercell-axis-row">
              <span class="supercell-axis-label">Y</span>
              <el-input-number
                v-model="supercellY"
                class="supercell-axis-input"
                size="small"
                :min="1"
                :max="supercellAxisMax('y')"
                :step="1"
                step-strictly
                controls-position="right"
                @change="clampSupercell"
              />
            </div>
            <div class="supercell-axis-row">
              <span class="supercell-axis-label">Z</span>
              <el-input-number
                v-model="supercellZ"
                class="supercell-axis-input"
                size="small"
                :min="1"
                :max="supercellAxisMax('z')"
                :step="1"
                step-strictly
                controls-position="right"
                @change="clampSupercell"
              />
            </div>
            <div class="supercell-settings-actions">
              <el-button size="small" text @click="resetSupercell">{{ t('viewer.resetSupercell') }}</el-button>
            </div>
          </div>
        </el-popover>

        <el-tooltip :content="t('viewer.wrapAtoms')" placement="right" popper-class="chemssh-passive-tooltip" :enterable="false" :show-after="500">
          <el-button
            :aria-label="t('viewer.wrapAtoms')"
            :aria-pressed="wrapAtoms"
            :type="wrapAtoms ? 'success' : undefined"
            :disabled="!structureHasCell"
            :icon="Crop"
            circle
            size="small"
            @click="toggleWrapAtoms"
          />
        </el-tooltip>
      </div>
      <div v-if="asePreview" class="viewer-overlay" :class="{ 'is-collapsed': overlayCollapsed }">
        <button
          class="viewer-overlay-toggle"
          type="button"
          :aria-label="overlayCollapsed ? t('viewer.expandOverlay') : t('viewer.collapseOverlay')"
          :title="overlayCollapsed ? t('viewer.expandOverlay') : t('viewer.collapseOverlay')"
          @click="overlayCollapsed = !overlayCollapsed"
        >
          <el-icon>
            <CaretBottom v-if="overlayCollapsed" />
            <CaretTop v-else />
          </el-icon>
        </button>
        <el-tooltip
          v-if="!overlayCollapsed && trajectoryMetricsChart?.sampled"
          :content="t('viewer.sampledMetrics')"
          placement="left"
          popper-class="chemssh-passive-tooltip"
          :enterable="false"
          :show-after="500"
        >
          <span class="viewer-metric-sampled-warning" :aria-label="t('viewer.sampledMetrics')">
            <el-icon><WarningFilled /></el-icon>
          </span>
        </el-tooltip>
        <div v-if="!overlayCollapsed" class="viewer-overlay-content">
          <span class="viewer-overlay-row">{{ frameNumberModeLabel }}: {{ currentFrameDisplayValue }} / {{ frameDisplayMax }}</span>
          <span class="viewer-overlay-row">Energy: {{ formatMetric(currentFrame.energy, ' eV') }}</span>
          <span class="viewer-overlay-row">Fmax: {{ formatMetric(currentFrame.fmax, ' eV/A') }}</span>
          <div v-if="trajectoryMetricsChart" class="viewer-metrics-chart">
            <div
              v-for="curve in trajectoryMetricsChart.curves"
              :key="curve.key"
              class="viewer-metric-plot"
            >
              <div class="viewer-metric-plot-header">
                <span>{{ curve.label }}</span>
                <span>min {{ trajectoryMetricsChart.xAxisLabel }} {{ curve.min.display }}</span>
              </div>
              <svg
                aria-hidden="true"
                class="viewer-metric-svg"
                :viewBox="`0 0 ${trajectoryMetricsChart.width} ${trajectoryMetricsChart.height}`"
              >
                <line
                  class="viewer-metric-axis"
                  :x1="trajectoryMetricsChart.padding.left"
                  :y1="trajectoryMetricsChart.axisY"
                  :x2="trajectoryMetricsChart.width - trajectoryMetricsChart.padding.right"
                  :y2="trajectoryMetricsChart.axisY"
                />
                <path class="viewer-metric-area" :d="curve.areaPath" :style="{ fill: curve.areaColor }" />
                <path class="viewer-metric-line" :d="curve.path" :style="{ stroke: curve.color }" />
                <circle class="viewer-metric-min-dot" :cx="curve.min.x" :cy="curve.min.y" r="2.4" :style="{ fill: curve.color }" />
                <text
                  class="viewer-metric-min-label"
                  :x="curve.min.labelX"
                  :y="curve.min.labelY"
                  :text-anchor="curve.min.anchor"
                >
                  {{ curve.min.display }}
                </text>
              </svg>
            </div>
          </div>
          <span v-if="cacheStatus">{{ cacheStatus }}</span>
        </div>
      </div>
      <div v-if="frameLoading" class="viewer-loading" aria-live="polite">
        <div class="viewer-loading-card">
          <span class="viewer-loading-title">{{ t('viewer.frameLoading') }}</span>
          <span class="viewer-loading-frame">{{ frameNumberModeLabel }} {{ frameIndexToDisplayValue(requestedFrameIndex) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { CaretBottom, CaretTop, Close, Connection, Crop, Download, Grid, Refresh, View, WarningFilled } from '@element-plus/icons-vue'
import { frameFromStructureChunk, readStructureFrame, readStructureFrameChunk, readStructureFrameJsonChunk, streamStructureFrames } from '../api/structures'
import { downloadUrl } from '../api/http'
import { t } from '../i18n'
import type { AseFrame, AseFrameChunk, AsePreviewResponse } from '../types/structure'
import type { AtomIndexBase, StructureFrame, TrajectoryStore, ViewerStyleMode } from '../viewer'
import { exportToXYZ, exportToXSD, exportTrajectoryToXYZ, exportTrajectoryToArc, downloadTextFile, getBaseFilename } from '../utils/structureExport'

type StyleMode = ViewerStyleMode
type ViewerModule = typeof import('../viewer')
type ChemSSHViewerInstance = InstanceType<ViewerModule['ChemSSHStructureViewer']>
type FrameNumberMode = 'index' | 'frame'
type TrajectoryMetricKey = 'energy' | 'fmax'

interface TrajectoryMetricPoint {
  index: number
  value: number
  display: number
  x: number
  y: number
}

interface TrajectoryMetricSample {
  index: number
  value: number
}

interface TrajectoryMetricCurve {
  key: TrajectoryMetricKey
  label: string
  color: string
  areaColor: string
  sampled: boolean
  path: string
  areaPath: string
  min: TrajectoryMetricPoint & {
    labelX: number
    labelY: number
    anchor: 'start' | 'end'
  }
}

interface PendingFrameChunkRequest {
  version?: number
  promise: Promise<void>
}

const props = withDefaults(
  defineProps<{
    asePreview?: AsePreviewResponse | null
    active?: boolean
    styleMode?: StyleMode
    backgroundColor?: string
  }>(),
  {
    asePreview: null,
    active: true,
    styleMode: 'stick',
    backgroundColor: '#ffffff'
  }
)

const emit = defineEmits<{
  refresh: []
  'render-start': []
  'render-complete': []
}>()

const ResetViewIcon = defineComponent({
  name: 'ResetViewIcon',
  setup() {
    return () =>
      h('svg', { viewBox: '0 0 1024 1024', xmlns: 'http://www.w3.org/2000/svg' }, [
        h('path', {
          fill: 'currentColor',
          d: 'M512 104 208 256v312l304 152 304-152V256L512 104zm0 72 180 90-180 90-180-90 180-90zM272 327l208 104v201L272 528V327zm272 305V431l208-104v201L544 632z'
        }),
        h('path', {
          fill: 'currentColor',
          d: 'M250 721a318 318 0 0 0 431 80l-48-33 147-54 8 156-50-34a382 382 0 0 1-546-88l58-27zM774 303A318 318 0 0 0 343 223l48 33-147 54-8-156 50 34a382 382 0 0 1 546 88l-58 27z'
        })
      ])
  }
})

const container = ref<HTMLElement | null>(null)
const selectedStyle = ref<StyleMode>(props.styleMode)
const bondScale = ref(1.25)
const showAtomIndex = ref(false)
const showAtomTag = ref(false)
const atomIndexBase = ref<AtomIndexBase>(0)
const supercellX = ref(1)
const supercellY = ref(1)
const supercellZ = ref(1)
const wrapAtoms = ref(false)
const frameNumberMode = ref<FrameNumberMode>('index')
const requestedFrameIndex = ref(0)
const frameLoading = ref(false)
const cachedFrameCount = ref(0)
const currentFrame = ref<AseFrame>(props.asePreview?.frame ?? emptyFrame())
const overlayCollapsed = ref(false)
const structureWarningDismissed = ref(false)

let chemsshViewer: ChemSSHViewerInstance | null = null
let resizeObserver: ResizeObserver | null = null
let renderVersion = 0
let scheduledFrame: number | null = null
let frameRequestHandle = 0
let frameLoadVersion = 0
let frameLoadingVersion = 0
let frameLoadingTimer: number | null = null
let preloadVersion = 0
let trajectoryStore: TrajectoryStore | null = null
let viewerModulePromise: Promise<ViewerModule> | null = null
let activeEventSource: EventSource | null = null

const jsonFrameCache = new Map<number, AseFrame>()
const variableBinaryFrameCache = new Map<number, AseFrame>()
const pendingChunks = new Map<number, PendingFrameChunkRequest>()
const pendingVariableChunks = new Map<number, PendingFrameChunkRequest>()
const pendingJsonChunks = new Map<number, PendingFrameChunkRequest>()
const chunkSize = 32
const streamChunkSize = 32
const jsonChunkSize = 32
const jsonWarmChunkRadius = 1
const frameLoadingDelayMs = 120
const maxSupercellAxis = 10
const metricChartWidth = 132
const metricChartHeight = 44
const metricChartPadding = { left: 8, right: 8, top: 7, bottom: 12 }
const maxMetricPlotPoints = 180
const metricSamplingFrameThreshold = 1000
const atomIndexBaseOptions = [
  { label: '0', value: 0 },
  { label: '1', value: 1 }
]

const cacheStatus = computed(() => {
  if (!props.asePreview?.is_trajectory) return ''
  if (cachedFrameCount.value >= props.asePreview.n_frames) return ''
  return t('viewer.cachedFrames', { cached: cachedFrameCount.value, total: props.asePreview.n_frames })
})

const hasSupercell = computed(() => supercellX.value > 1 || supercellY.value > 1 || supercellZ.value > 1)

const structureHasCell = computed(() => hasUsableCell(currentFrame.value.cell))
const atomIndexBaseStatus = computed(() => t('viewer.atomIndexBaseStatus', { start: atomIndexBase.value }))
const effectiveFrameNumberMode = computed<FrameNumberMode>(() => props.asePreview?.is_trajectory ? frameNumberMode.value : 'frame')
const frameNumberModeLabel = computed(() => effectiveFrameNumberMode.value === 'frame' ? t('viewer.frame') : t('viewer.index'))
const frameDisplayMin = computed(() => effectiveFrameNumberMode.value === 'frame' ? 1 : 0)
const frameDisplayMax = computed(() => {
  const frames = props.asePreview?.n_frames ?? 1
  return effectiveFrameNumberMode.value === 'frame' ? Math.max(1, frames) : Math.max(0, frames - 1)
})
const frameDisplayInput = computed({
  get: () => frameIndexToDisplayValue(requestedFrameIndex.value),
  set: value => {
    requestedFrameIndex.value = displayValueToFrameIndex(value)
  }
})
const currentFrameDisplayValue = computed(() => frameIndexToDisplayValue(currentFrame.value.frame_index))
const structureWarningMessages = computed(() => {
  const warnings = props.asePreview?.warnings ?? []
  const messages: string[] = []
  if (warnings.includes('vasp_outcar_md_may_lack_structure')) {
    messages.push(t('viewer.vaspOutcarMdWarning'))
  }
  if (warnings.includes('vasp_outcar_constraints_missing') || warnings.includes('vasp_outcar_constraints_unreadable')) {
    messages.push(t('viewer.vaspOutcarConstraintWarning'))
  }
  return messages
})
const structureWarningText = computed(() => structureWarningMessages.value.join(' '))
const showStructureWarning = computed(() => structureWarningMessages.value.length > 0)
const showExpandedStructureWarning = computed(() => showStructureWarning.value && !structureWarningDismissed.value)
const showCollapsedStructureWarning = computed(() => showStructureWarning.value && structureWarningDismissed.value)

// Optimize: Cache chart computation to avoid rebuilding on every tick
const trajectoryMetricsChart = computed(() => {
  const preview = props.asePreview
  if (!props.active || !preview?.is_trajectory || preview.n_frames < 2) return null

  // Only depend on cached frame count, not current frame index for chart data
  // The chart shows all frames, not just the current one
  const reactiveTick = cachedFrameCount.value
  void reactiveTick

  const energy = buildMetricCurve('energy', 'Energy', '#176b87', 'rgba(23, 107, 135, 0.12)')
  const fmax = buildMetricCurve('fmax', 'Fmax', '#9a5b13', 'rgba(154, 91, 19, 0.12)')
  const curves = [energy, fmax].filter((curve): curve is TrajectoryMetricCurve => curve !== null)
  if (curves.length === 0) return null

  return {
    width: metricChartWidth,
    height: metricChartHeight,
    padding: metricChartPadding,
    axisY: metricChartHeight - metricChartPadding.bottom,
    xAxisLabel: frameNumberModeLabel.value,
    sampled: curves.some(curve => curve.sampled),
    curves
  }
})

function emptyFrame(): AseFrame {
  return {
    frame_index: 0,
    positions: [],
    cell: [],
    pbc: [false, false, false],
    tags: [],
    fixed_indices: [],
    energy: null,
    fmax: null,
    symbols: [],
    numbers: []
  }
}

function loadChemSSHViewer() {
  viewerModulePromise ??= import('../viewer')
  return viewerModulePromise
}

function viewerStyleMode(): ViewerStyleMode {
  return selectedStyle.value
}

function viewerStyle() {
  const mode = viewerStyleMode()
  return {
    mode,
    atomScale: mode === 'sphere' ? 0.52 : mode === 'line' ? 0.2 : 0.42,
    bondRadius: mode === 'line' ? 0.012 : 0.065,
    bondScale: bondScale.value,
    backgroundColor: props.backgroundColor,
    showCell: true
  }
}

function viewerDisplayOptions() {
  return {
    supercell: {
      x: supercellX.value,
      y: supercellY.value,
      z: supercellZ.value
    },
    wrap: wrapAtoms.value
  }
}

async function renderStructure(keepView = false) {
  const version = ++renderVersion
  emit('render-start')
  await nextTick()
  if (!container.value || version !== renderVersion) return

  if (!props.asePreview) {
    disposeChemSSHViewer()
    container.value.innerHTML = ''
    if (version === renderVersion) emit('render-complete')
    return
  }

  try {
    await renderChemSSHStructure(keepView)
  } catch (error) {
    console.warn('ChemSSH viewer failed.', error)
    disposeChemSSHViewer()
  } finally {
    if (version === renderVersion) emit('render-complete')
  }
}

async function renderChemSSHStructure(keepView = false) {
  if (!container.value || !props.asePreview) return
  if (!chemsshViewer) {
    container.value.innerHTML = ''
    const { ChemSSHStructureViewer } = await loadChemSSHViewer()
    chemsshViewer = new ChemSSHStructureViewer(container.value, {
      backgroundColor: props.backgroundColor,
      style: viewerStyle(),
      labelOptions: {
        showAtomIndex: showAtomIndex.value,
        showAtomTag: showAtomTag.value,
        atomIndexBase: atomIndexBase.value
      }
    })
  }

  chemsshViewer.setStyle(viewerStyle())
  chemsshViewer.setLabelOptions({
    showAtomIndex: showAtomIndex.value,
    showAtomTag: showAtomTag.value,
    atomIndexBase: atomIndexBase.value
  })
  chemsshViewer.setDisplayOptions(viewerDisplayOptions())

  if (trajectoryStore && props.asePreview.is_trajectory) {
    chemsshViewer.setTrajectory(trajectoryStore, {
      keepView,
      initialFrameIndex: currentFrame.value.frame_index
    })
    chemsshViewer.setFrame(currentFrame.value.frame_index)
    return
  }

  chemsshViewer.setStructure(frameToViewerFrame(currentFrame.value), { keepView })
}

function frameToViewerFrame(frame: AseFrame): StructureFrame {
  return {
    frameIndex: frame.frame_index,
    positions: frame.positions,
    cell: frame.cell,
    pbc: frame.pbc,
    tags: frame.tags,
    fixedIndices: frame.fixed_indices,
    energy: frame.energy,
    fmax: frame.fmax,
    symbols: frame.symbols ?? props.asePreview?.frame.symbols ?? [],
    numbers: frame.numbers ?? props.asePreview?.frame.numbers ?? []
  }
}

function resizeViewer() {
  chemsshViewer?.resize()
}

function resetView() {
  chemsshViewer?.resetView()
}

function refreshStructure() {
  emit('refresh')
}

function dismissStructureWarning() {
  structureWarningDismissed.value = true
}

function handleExportCommand(command: string) {
  switch (command) {
    case 'original':
      exportOriginal()
      break
    case 'current-xyz':
      exportCurrentXYZ()
      break
    case 'current-xsd':
      exportCurrentXSD()
      break
    case 'trajectory-xyz':
      exportTrajectoryXYZ()
      break
    case 'trajectory-arc':
      exportTrajectoryArc()
      break
    case 'screenshot':
      exportPng()
      break
  }
}

function exportOriginal() {
  if (!props.asePreview) return
  const path = props.asePreview.path
  const url = downloadUrl(path)
  const link = document.createElement('a')
  link.href = url
  link.download = props.asePreview.name
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function exportCurrentXYZ() {
  if (!props.asePreview) return
  try {
    const basename = getBaseFilename(props.asePreview.path)
    const frame = getCurrentFrame()
    if (!frame) return
    const suffix = props.asePreview.is_trajectory ? `-frame-${frame.frame_index}` : ''
    const content = exportToXYZ(frame, basename)
    downloadTextFile(content, `${basename}${suffix}.xyz`, 'chemical/x-xyz')
  } catch (error) {
    console.error('Failed to export current XYZ:', error)
  }
}

function exportCurrentXSD() {
  if (!props.asePreview) return
  try {
    const basename = getBaseFilename(props.asePreview.path)
    const frame = getCurrentFrame()
    if (!frame) return
    const suffix = props.asePreview.is_trajectory ? `-frame-${frame.frame_index}` : ''
    const content = exportToXSD(frame, basename)
    downloadTextFile(content, `${basename}${suffix}.xsd`, 'chemical/x-xsd')
  } catch (error) {
    console.error('Failed to export current XSD:', error)
  }
}

async function exportTrajectoryXYZ() {
  if (!props.asePreview) return
  try {
    const basename = getBaseFilename(props.asePreview.path)
    const frames = await loadAllFrames()
    if (frames.length === 0) return
    const content = exportTrajectoryToXYZ(frames, basename)
    downloadTextFile(content, `${basename}.xyz`, 'chemical/x-xyz')
  } catch (error) {
    console.error('Failed to export trajectory XYZ:', error)
  }
}

async function exportTrajectoryArc() {
  if (!props.asePreview) return
  try {
    const basename = getBaseFilename(props.asePreview.path)
    const frames = await loadAllFrames()
    if (frames.length === 0) return
    const content = exportTrajectoryToArc(frames, basename)
    downloadTextFile(content, `${basename}.arc`, 'chemical/x-arc')
  } catch (error) {
    console.error('Failed to export trajectory Arc:', error)
  }
}

async function loadAllFrames(): Promise<AseFrame[]> {
  if (!props.asePreview) return []

  const frames: AseFrame[] = []
  const nFrames = props.asePreview.n_frames

  // Try to load from trajectory store first
  if (trajectoryStore && trajectoryStore.nFrames === nFrames) {
    for (let i = 0; i < nFrames; i++) {
      if (isStoreFrameAvailable(trajectoryStore, i)) {
        const frame = frameFromStore(i)
        if (frame) frames.push(frame)
      }
    }
    // If we got all frames from store, return them
    if (frames.length === nFrames) return frames
  }

  // Otherwise, load all frames from server
  frames.length = 0
  for (let i = 0; i < nFrames; i++) {
    try {
      const frame = await loadFrame(i)
      frames.push(frame)
    } catch (error) {
      console.error(`Failed to load frame ${i}:`, error)
    }
  }

  return frames
}

function getCurrentFrame(): AseFrame | null {
  // Use the current frame ref directly
  return currentFrame.value || props.asePreview?.frame || null
}

function exportPng() {
  const uri = chemsshViewer?.screenshot()
  if (!uri) return
  const link = document.createElement('a')
  link.href = uri
  link.download = `structure-${Date.now()}.png`
  link.click()
}

function formatMetric(value: number | null | undefined, unit: string) {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'N/A'
  return `${value.toFixed(6)}${unit}`
}

function buildMetricCurve(
  key: TrajectoryMetricKey,
  label: string,
  color: string,
  areaColor: string
): TrajectoryMetricCurve | null {
  const preview = props.asePreview
  if (!preview?.is_trajectory || preview.n_frames < 2) return null

  const series = collectMetricSamples(key, preview.n_frames)
  if (!series || series.count < 2 || series.samples.length < 2) return null

  const minValue = series.min
  const maxValue = series.max
  const span = maxValue - minValue
  const yMin = span === 0 ? minValue - 1 : minValue - span * 0.08
  const yMax = span === 0 ? maxValue + 1 : maxValue + span * 0.08
  const plotWidth = metricChartWidth - metricChartPadding.left - metricChartPadding.right
  const plotHeight = metricChartHeight - metricChartPadding.top - metricChartPadding.bottom

  const points = series.samples.map(sample => {
    const x = metricChartPadding.left + (sample.index / (preview.n_frames - 1)) * plotWidth
    const y = metricChartPadding.top + (1 - (sample.value - yMin) / (yMax - yMin)) * plotHeight
    return { index: sample.index, value: sample.value, display: frameIndexToDisplayValue(sample.index), x, y }
  })
  const minX = metricChartPadding.left + (series.minPoint.index / (preview.n_frames - 1)) * plotWidth
  const minY = metricChartPadding.top + (1 - (series.minPoint.value - yMin) / (yMax - yMin)) * plotHeight
  const min: TrajectoryMetricPoint = {
    index: series.minPoint.index,
    value: series.minPoint.value,
    display: frameIndexToDisplayValue(series.minPoint.index),
    x: minX,
    y: minY
  }
  if (!points.some(point => point.index === min.index)) {
    points.push(min)
    points.sort((left, right) => left.index - right.index)
  }
  if (points.length < 2) return null

  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${roundSvg(point.x)} ${roundSvg(point.y)}`).join(' ')
  const axisY = metricChartHeight - metricChartPadding.bottom
  const areaPath = `${path} L ${roundSvg(points[points.length - 1].x)} ${axisY} L ${roundSvg(points[0].x)} ${axisY} Z`
  const anchor = min.x > metricChartWidth - 42 ? 'end' : 'start'
  const labelX = anchor === 'end' ? min.x - 4 : min.x + 4
  const labelY = min.y > metricChartPadding.top + 9 ? min.y - 4 : min.y + 10

  return {
    key,
    label,
    color,
    areaColor,
    sampled: series.sampled,
    path,
    areaPath,
    min: {
      ...min,
      labelX: clampNumber(labelX, metricChartPadding.left, metricChartWidth - metricChartPadding.right),
      labelY: clampNumber(labelY, metricChartPadding.top + 6, axisY - 2),
      anchor
    }
  }
}

function collectMetricSamples(key: TrajectoryMetricKey, nFrames: number) {
  const samples = new Map<number, TrajectoryMetricSample>()
  const sampled = nFrames > metricSamplingFrameThreshold
  const sampleEvery = sampled ? Math.max(1, Math.floor(nFrames / maxMetricPlotPoints)) : 1
  let min = Number.POSITIVE_INFINITY
  let max = Number.NEGATIVE_INFINITY
  let count = 0
  let minPoint: TrajectoryMetricSample | null = null
  let firstPoint: TrajectoryMetricSample | null = null
  let lastPoint: TrajectoryMetricSample | null = null

  const visit = (index: number, rawValue: number | null | undefined) => {
    const value = finiteOrNaN(rawValue)
    if (!Number.isFinite(value) || index < 0 || index >= nFrames) return false
    const point = { index, value }
    count += 1
    min = Math.min(min, value)
    max = Math.max(max, value)
    if (!minPoint || value < minPoint.value) minPoint = point
    if (!firstPoint || index < firstPoint.index) firstPoint = point
    if (!lastPoint || index > lastPoint.index) lastPoint = point
    if (index === 0 || index === nFrames - 1 || index % sampleEvery === 0) samples.set(index, point)
    return true
  }

  const storeValues = trajectoryStore?.[key]
  if (storeValues) {
    for (let index = 0; index < Math.min(nFrames, storeValues.length); index += 1) {
      visit(index, storeValues[index])
    }
  } else {
    const visited = new Set<number>()
    for (const frame of variableBinaryFrameCache.values()) {
      if (visit(frame.frame_index, frame[key])) visited.add(frame.frame_index)
    }
    for (const frame of jsonFrameCache.values()) {
      if (visited.has(frame.frame_index)) continue
      if (visit(frame.frame_index, frame[key])) visited.add(frame.frame_index)
    }
    const currentIndex = currentFrame.value.frame_index
    if (!visited.has(currentIndex)) visit(currentIndex, currentFrame.value[key])
  }

  const firstSample = firstPoint as TrajectoryMetricSample | null
  const lastSample = lastPoint as TrajectoryMetricSample | null
  const minSample = minPoint as TrajectoryMetricSample | null
  if (!minSample || !firstSample || !lastSample) return null
  samples.set(firstSample.index, firstSample)
  samples.set(lastSample.index, lastSample)
  samples.set(minSample.index, minSample)
  return {
    min,
    max,
    count,
    minPoint: minSample,
    sampled,
    samples: [...samples.values()].sort((left, right) => left.index - right.index)
  }
}

function roundSvg(value: number) {
  return Number(value.toFixed(2))
}

function clampNumber(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function scheduleFrameChange() {
  if (!props.asePreview) return
  scheduledFrame = clampFrameIndex(Number(requestedFrameIndex.value))
  if (frameRequestHandle) return
  frameRequestHandle = window.requestAnimationFrame(() => {
    frameRequestHandle = 0
    if (scheduledFrame === null) return
    void setFrame(scheduledFrame)
  })
}

function resetBondScale() {
  bondScale.value = 1.25
}

function resetSupercell() {
  supercellX.value = 1
  supercellY.value = 1
  supercellZ.value = 1
}

function resetStructureSwitchState() {
  resetSupercell()
  chemsshViewer?.clearSelection()
  chemsshViewer?.resetView()
}

function toggleWrapAtoms() {
  wrapAtoms.value = !wrapAtoms.value
}

function toggleFrameNumberMode() {
  frameNumberMode.value = frameNumberMode.value === 'frame' ? 'index' : 'frame'
}

function frameIndexToDisplayValue(index: number) {
  return effectiveFrameNumberMode.value === 'frame' ? index + 1 : index
}

function displayValueToFrameIndex(value: number | undefined) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return currentFrame.value.frame_index
  return clampFrameIndex(effectiveFrameNumberMode.value === 'frame' ? numeric - 1 : numeric)
}

function supercellAxisMax(_axis: 'x' | 'y' | 'z') {
  return maxSupercellAxis
}

function clampSupercell() {
  supercellX.value = clampMultiplier(supercellX.value, supercellAxisMax('x'))
  supercellY.value = clampMultiplier(supercellY.value, supercellAxisMax('y'))
  supercellZ.value = clampMultiplier(supercellZ.value, supercellAxisMax('z'))
}

function clampMultiplier(value: number | undefined, max: number) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 1
  return Math.min(max, Math.max(1, Math.round(numeric)))
}

function hasUsableCell(cell: number[][] | undefined) {
  if (!cell || cell.length < 3) return false
  const ax = cell[0]?.[0] ?? 0
  const ay = cell[0]?.[1] ?? 0
  const az = cell[0]?.[2] ?? 0
  const bx = cell[1]?.[0] ?? 0
  const by = cell[1]?.[1] ?? 0
  const bz = cell[1]?.[2] ?? 0
  const cx = cell[2]?.[0] ?? 0
  const cy = cell[2]?.[1] ?? 0
  const cz = cell[2]?.[2] ?? 0
  const det = ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
  return Math.abs(det) > 1e-10
}

async function setFrame(index: number) {
  const preview = props.asePreview
  if (!preview) return
  const target = clampFrameIndex(index)
  requestedFrameIndex.value = target
  const requestVersion = ++frameLoadVersion
  if (target === currentFrame.value.frame_index) {
    clearFrameLoading(requestVersion)
    return
  }

  // Fast path: any cached frame is applied synchronously without showing the
  // loading overlay. This avoids the mask flashing when navigating back to a
  // frame that was already loaded.
  if (trajectoryStore && isStoreFrameAvailable(trajectoryStore, target)) {
    clearFrameLoading(requestVersion)
    applyTrajectoryFrame(target, requestVersion)
    return
  }
  const cachedFrame = variableBinaryFrameCache.get(target) ?? jsonFrameCache.get(target)
  if (cachedFrame) {
    if (requestVersion !== frameLoadVersion || requestedFrameIndex.value !== target) {
      // Superseded before we could apply; do not touch the overlay because a
      // newer request now owns it.
      return
    }
    clearFrameLoading(requestVersion)
    currentFrame.value = cachedFrame
    requestedFrameIndex.value = target
    await renderStructure(true)
    warmJsonFramesAround(target)
    return
  }

  // Slow path: need an async chunk/frame fetch. Claim the overlay for this
  // request version so only this request (or a newer one) can clear it.
  showFrameLoading(requestVersion)
  try {
    if (preview.is_trajectory && canUseFixedBinaryStore(preview) && trajectoryStore) {
      await ensureChunk(target)
    } else if (preview.is_trajectory && canUseVariableBinaryStream(preview)) {
      await ensureVariableChunk(target, preview).catch(() => undefined)
    } else if (preview.is_trajectory && !canUseBinaryStream(preview)) {
      await ensureJsonChunk(target, preview).catch(() => undefined)
    }

    if (requestVersion !== frameLoadVersion || requestedFrameIndex.value !== target) return

    // After the chunk has been fetched, re-check every cache the chunk could
    // have populated. A variable binary chunk writes to variableBinaryFrameCache,
    // a JSON chunk writes to jsonFrameCache, and a fixed binary chunk writes to
    // the trajectory store.
    if (trajectoryStore && isStoreFrameAvailable(trajectoryStore, target)) {
      applyTrajectoryFrame(target, requestVersion)
      return
    }
    const afterChunk = variableBinaryFrameCache.get(target) ?? jsonFrameCache.get(target)
    if (afterChunk) {
      currentFrame.value = afterChunk
      requestedFrameIndex.value = target
      await renderStructure(true)
      warmJsonFramesAround(target)
      return
    }

    const frame = await loadFrame(target)
    if (requestVersion !== frameLoadVersion || requestedFrameIndex.value !== target) return
    currentFrame.value = frame
    cacheJsonFrame(frame)
    requestedFrameIndex.value = target
    await renderStructure(true)
    warmJsonFramesAround(target)
  } finally {
    // Only the request that currently owns the overlay is allowed to clear it.
    // A superseding request that took the fast path already cleared its own
    // overlay; if it then started a new slow-path fetch it claimed the overlay
    // again with a newer version, so this finally must not clobber that.
    if (frameLoadingVersion === requestVersion) clearFrameLoading(requestVersion)
  }
}

function showFrameLoading(version: number) {
  frameLoadingVersion = version
  if (frameLoadingTimer !== null) {
    window.clearTimeout(frameLoadingTimer)
    frameLoadingTimer = null
  }
  frameLoadingTimer = window.setTimeout(() => {
    frameLoadingTimer = null
    if (frameLoadingVersion === version) frameLoading.value = true
  }, frameLoadingDelayMs)
}

function clearFrameLoading(version: number) {
  frameLoadingVersion = version
  if (frameLoadingTimer !== null) {
    window.clearTimeout(frameLoadingTimer)
    frameLoadingTimer = null
  }
  frameLoading.value = false
}

function applyTrajectoryFrame(index: number, requestVersion = frameLoadVersion) {
  if (requestVersion !== frameLoadVersion || requestedFrameIndex.value !== index) return
  if (!trajectoryStore) return
  const energy = trajectoryStore.energy?.[index]
  const fmax = trajectoryStore.fmax?.[index]
  currentFrame.value = {
    ...currentFrame.value,
    frame_index: index,
    energy: energy === undefined || Number.isNaN(energy) ? null : energy,
    fmax: fmax === undefined || Number.isNaN(fmax) ? null : fmax
  }
  requestedFrameIndex.value = index
  clearFrameLoading(requestVersion)
  chemsshViewer?.setFrame(index)
}

async function loadFrame(index: number) {
  if (!props.asePreview) return emptyFrame()
  const variableCached = variableBinaryFrameCache.get(index)
  if (variableCached) return variableCached
  if (trajectoryStore && isStoreFrameAvailable(trajectoryStore, index)) {
    return frameFromStore(index) ?? emptyFrame()
  }
  return readStructureFrame(
    props.asePreview.source,
    props.asePreview.path,
    index,
    props.asePreview.format,
    props.asePreview.size_limit_overridden === true
  )
}

function canUseBinaryStream(preview: AsePreviewResponse) {
  return preview.transport === 'binary-available'
}

function canUseFixedBinaryStore(preview: AsePreviewResponse) {
  return canUseBinaryStream(preview) && preview.topology_stable
}

function canUseVariableBinaryStream(preview: AsePreviewResponse) {
  return canUseBinaryStream(preview) && preview.is_trajectory && !preview.topology_stable
}

function canPreloadJsonTrajectory(preview: AsePreviewResponse) {
  return preview.is_trajectory && !canUseBinaryStream(preview)
}

async function preloadTrajectoryFrames(version: number) {
  const preview = props.asePreview
  if (!props.active || !preview?.is_trajectory) return

  if (canUseBinaryStream(preview)) {
    await streamBinaryTrajectoryFrames(version, preview)
    return
  }

  if (canPreloadJsonTrajectory(preview)) {
    await preloadJsonTrajectoryFrames(version, preview)
    return
  }
  warmJsonFramesAround(preview.initial_frame_index)
}

async function streamBinaryTrajectoryFrames(version: number, preview: AsePreviewResponse) {
  const store = trajectoryStore  // non-null for fixed-topology

  // Close any previous SSE connection.
  closeActiveEventSource()

  await new Promise<void>((resolve) => {
    const es = streamStructureFrames(
      preview.source,
      preview.path,
      (chunk) => {
        if (version !== preloadVersion || !props.active || !props.asePreview || props.asePreview.path !== preview.path) {
          es.close()
          activeEventSource = null
          resolve()
          return
        }
        // Write chunk data to store (fixed) or cache (variable) immediately.
        if (store && chunk.header.n_atoms === store.nAtoms) {
          writeChunkToStore(chunk)
        } else {
          writeVariableChunkToCache(chunk)
        }
        updateCachedFrameCount()
      },
      (_nFrames) => {
        activeEventSource = null
        resolve()
      },
      (_error) => {
        activeEventSource = null
        // SSE failed — fall back to JSON preload if eligible.
        if (version === preloadVersion && props.active && canPreloadJsonTrajectory(preview)) {
          void preloadJsonTrajectoryFrames(version, preview)
        }
        resolve()
      },
      preview.format,
      preview.size_limit_overridden === true,
      streamChunkSize,
    )

    activeEventSource = es

    // Poll for cancellation (preloadVersion bump or component inactive).
    const check = setInterval(() => {
      if (version !== preloadVersion || !props.active) {
        clearInterval(check)
        es.close()
        activeEventSource = null
        resolve()
      }
    }, 200)

    // Clean up the interval when the EventSource closes naturally.
    es.addEventListener('error', () => { clearInterval(check) })
    es.addEventListener('done', () => { clearInterval(check) })
  })
}

function closeActiveEventSource() {
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
}

async function preloadJsonTrajectoryFrames(version: number, preview: AsePreviewResponse) {
  const starts = jsonChunkStartsForFullTrajectory(preview)
  const initialStart = jsonChunkStartForIndex(preview.initial_frame_index)
  starts.sort((left, right) => {
    if (left === initialStart) return -1
    if (right === initialStart) return 1
    return left - right
  })

  for (const start of starts) {
    if (!props.active || version !== preloadVersion || !props.asePreview || props.asePreview.path !== preview.path) return
    try {
      await ensureJsonChunkStart(start, preview, version)
    } catch {
      return
    }
  }
}

function jsonChunkStartsForFullTrajectory(preview: AsePreviewResponse) {
  const starts: number[] = []
  for (let start = 0; start < preview.n_frames; start += jsonChunkSize) {
    starts.push(start)
  }
  return starts
}

async function ensureChunk(index: number) {
  if (!props.asePreview) return
  const chunkStart = Math.floor(index / chunkSize) * chunkSize
  await ensureChunkStart(chunkStart, props.asePreview)
}

async function ensureChunkStart(chunkStart: number, preview: AsePreviewResponse, version?: number) {
  if (trajectoryStore && isChunkAvailable(trajectoryStore, chunkStart, preview)) return
  const pending = pendingChunks.get(chunkStart)
  if (pending) {
    await pending.promise
    return
  }
  const request = readStructureFrameChunk(
    preview.source,
    preview.path,
    chunkStart,
    Math.min(chunkSize, preview.n_frames - chunkStart),
    preview.format,
    preview.size_limit_overridden === true
  )
    .then(chunk => {
      if (version !== undefined && (version !== preloadVersion || !props.active)) return
      if (!props.asePreview || props.asePreview.path !== preview.path) return
      writeChunkToStore(chunk)
      updateCachedFrameCount()
    })
    .finally(() => {
      if (pendingChunks.get(chunkStart)?.promise === request) pendingChunks.delete(chunkStart)
    })
  pendingChunks.set(chunkStart, { version, promise: request })
  await request
}

async function ensureVariableChunk(index: number, preview: AsePreviewResponse) {
  const chunkStart = Math.floor(index / chunkSize) * chunkSize
  await ensureVariableChunkStart(chunkStart, preview)
}

async function ensureVariableChunkStart(chunkStart: number, preview: AsePreviewResponse, version?: number) {
  if (isVariableChunkAvailable(chunkStart, preview)) return
  const pending = pendingVariableChunks.get(chunkStart)
  if (pending) {
    await pending.promise
    return
  }
  const request = readStructureFrameChunk(
    preview.source,
    preview.path,
    chunkStart,
    Math.min(chunkSize, preview.n_frames - chunkStart),
    preview.format,
    preview.size_limit_overridden === true
  )
    .then(chunk => {
      if (version !== undefined && (version !== preloadVersion || !props.active)) return
      if (!props.asePreview || props.asePreview.path !== preview.path) return
      writeVariableChunkToCache(chunk)
      updateCachedFrameCount()
    })
    .catch(error => {
      // Keep JSON fallback available when a provider cannot serve variable binary.
      void ensureJsonChunkStart(jsonChunkStartForIndex(chunkStart), preview, version).catch(() => undefined)
      throw error
    })
    .finally(() => {
      if (pendingVariableChunks.get(chunkStart)?.promise === request) pendingVariableChunks.delete(chunkStart)
    })
  pendingVariableChunks.set(chunkStart, { version, promise: request })
  await request
}

async function ensureJsonChunk(index: number, preview: AsePreviewResponse) {
  await ensureJsonChunkStart(jsonChunkStartForIndex(index), preview)
}

async function ensureJsonChunkStart(chunkStart: number, preview: AsePreviewResponse, version?: number) {
  if (isJsonChunkAvailable(chunkStart, preview)) return
  const pending = pendingJsonChunks.get(chunkStart)
  if (pending) {
    await pending.promise
    return
  }

  const request = readStructureFrameJsonChunk(
    preview.source,
    preview.path,
    chunkStart,
    Math.min(jsonChunkSize, preview.n_frames - chunkStart),
    preview.format,
    preview.size_limit_overridden === true
  )
    .then(chunk => {
      if (version !== undefined && (version !== preloadVersion || !props.active)) return
      if (!props.asePreview || props.asePreview.path !== preview.path) return
      for (const frame of chunk.frames) {
        jsonFrameCache.set(frame.frame_index, frame)
      }
      updateCachedFrameCount()
    })
    .finally(() => {
      if (pendingJsonChunks.get(chunkStart)?.promise === request) pendingJsonChunks.delete(chunkStart)
    })
  pendingJsonChunks.set(chunkStart, { version, promise: request })
  await request
}

function warmJsonFramesAround(index: number) {
  const preview = props.asePreview
  if (!props.active || !preview?.is_trajectory || canUseBinaryStream(preview)) return
  const center = jsonChunkStartForIndex(index)
  const starts: number[] = []
  for (let radius = 0; radius <= jsonWarmChunkRadius; radius += 1) {
    starts.push(center - radius * jsonChunkSize, center + radius * jsonChunkSize)
  }
  for (const start of new Set(starts)) {
    if (start < 0 || start >= preview.n_frames) continue
    void ensureJsonChunkStart(start, preview).catch(() => undefined)
  }
}

function createTrajectoryStore(preview: AsePreviewResponse): TrajectoryStore | null {
  if (!canUseFixedBinaryStore(preview)) return null
  const store: TrajectoryStore = {
    nFrames: preview.n_frames,
    nAtoms: preview.n_atoms,
    symbols: preview.frame.symbols ?? [],
    numbers: preview.frame.numbers ?? [],
    positions: new Float32Array(preview.n_frames * preview.n_atoms * 3),
    cells: new Float32Array(preview.n_frames * 9),
    tags: new Int32Array(preview.n_frames * preview.n_atoms),
    fixedMask: new Uint8Array(preview.n_frames * preview.n_atoms),
    energy: filledFloatArray(preview.n_frames),
    fmax: filledFloatArray(preview.n_frames),
    pbc: preview.frame.pbc,
    availableFrames: new Uint8Array(preview.n_frames),
    initialFrameIndex: preview.initial_frame_index
  }
  writeFrameToStore(store, frameToViewerFrame(preview.frame))
  return store
}

function filledFloatArray(length: number) {
  const array = new Float32Array(length)
  array.fill(Number.NaN)
  return array
}

function writeFrameToStore(store: TrajectoryStore, frame: StructureFrame) {
  if (frame.frameIndex < 0 || frame.frameIndex >= store.nFrames) return
  const atomOffset = frame.frameIndex * store.nAtoms
  const positionOffset = atomOffset * 3
  const positions = frame.positions instanceof Float32Array ? frame.positions : flattenPositions(frame.positions, store.nAtoms)
  store.positions.set(positions.subarray(0, store.nAtoms * 3), positionOffset)

  const cellOffset = frame.frameIndex * 9
  if (store.cells && frame.cell) store.cells.set(flattenCell(frame.cell), cellOffset)
  if (store.tags && frame.tags) {
    const tags = frame.tags instanceof Int32Array ? frame.tags : new Int32Array(frame.tags)
    store.tags.set(tags.subarray(0, store.nAtoms), atomOffset)
  }
  if (store.fixedMask) {
    store.fixedMask.fill(0, atomOffset, atomOffset + store.nAtoms)
    if (frame.fixedMask) {
      store.fixedMask.set(frame.fixedMask.subarray(0, store.nAtoms), atomOffset)
    } else {
      for (const index of frame.fixedIndices ?? []) {
        if (index >= 0 && index < store.nAtoms) store.fixedMask[atomOffset + index] = 1
      }
    }
  }
  if (store.energy) store.energy[frame.frameIndex] = finiteOrNaN(frame.energy)
  if (store.fmax) store.fmax[frame.frameIndex] = finiteOrNaN(frame.fmax)
  if (store.availableFrames) store.availableFrames[frame.frameIndex] = 1
}

function writeChunkToStore(chunk: AseFrameChunk) {
  if (!trajectoryStore) return
  const start = chunk.header.start
  const count = chunk.header.count
  const nAtoms = chunk.header.n_atoms
  if (nAtoms !== trajectoryStore.nAtoms) return

  trajectoryStore.positions.set(chunk.positions, start * nAtoms * 3)
  if (trajectoryStore.cells && chunk.cells) trajectoryStore.cells.set(chunk.cells, start * 9)
  if (trajectoryStore.tags && chunk.tags) trajectoryStore.tags.set(chunk.tags, start * nAtoms)
  if (trajectoryStore.fixedMask && chunk.fixedMask) trajectoryStore.fixedMask.set(chunk.fixedMask, start * nAtoms)
  if (trajectoryStore.energy && chunk.energy) trajectoryStore.energy.set(chunk.energy, start)
  if (trajectoryStore.fmax && chunk.fmax) trajectoryStore.fmax.set(chunk.fmax, start)
  trajectoryStore.symbols = chunk.header.symbols
  trajectoryStore.numbers = chunk.header.numbers
  trajectoryStore.pbc = chunk.header.pbc
  for (let localIndex = 0; localIndex < count; localIndex += 1) {
    if (trajectoryStore.availableFrames) trajectoryStore.availableFrames[start + localIndex] = 1
  }
  chemsshViewer?.setFrame(currentFrame.value.frame_index)
}

function writeVariableChunkToCache(chunk: AseFrameChunk) {
  for (let localIndex = 0; localIndex < chunk.header.count; localIndex += 1) {
    const frame = frameFromStructureChunk(chunk, localIndex)
    if (frame) variableBinaryFrameCache.set(frame.frame_index, frame)
  }
}

function updateCachedFrameCount() {
  const fixedCount = trajectoryStore?.availableFrames?.reduce((total, value) => total + value, 0)
  if (fixedCount !== undefined) {
    cachedFrameCount.value = fixedCount
    return
  }
  const indices = new Set<number>()
  for (const index of variableBinaryFrameCache.keys()) indices.add(index)
  for (const index of jsonFrameCache.keys()) indices.add(index)
  cachedFrameCount.value = indices.size
}

function cacheJsonFrame(frame: AseFrame) {
  jsonFrameCache.set(frame.frame_index, frame)
  updateCachedFrameCount()
}

function isChunkAvailable(store: TrajectoryStore, chunkStart: number, preview: AsePreviewResponse) {
  const count = Math.min(chunkSize, preview.n_frames - chunkStart)
  for (let offset = 0; offset < count; offset += 1) {
    if (store.availableFrames?.[chunkStart + offset] !== 1) return false
  }
  return true
}

function isVariableChunkAvailable(chunkStart: number, preview: AsePreviewResponse) {
  const count = Math.min(chunkSize, preview.n_frames - chunkStart)
  for (let offset = 0; offset < count; offset += 1) {
    if (!variableBinaryFrameCache.has(chunkStart + offset)) return false
  }
  return true
}

function isJsonChunkAvailable(chunkStart: number, preview: AsePreviewResponse) {
  const count = Math.min(jsonChunkSize, preview.n_frames - chunkStart)
  for (let offset = 0; offset < count; offset += 1) {
    if (!jsonFrameCache.has(chunkStart + offset)) return false
  }
  return true
}

function jsonChunkStartForIndex(index: number) {
  return Math.floor(index / jsonChunkSize) * jsonChunkSize
}

function isStoreFrameAvailable(store: TrajectoryStore, index: number) {
  return index >= 0 && index < store.nFrames && (!store.availableFrames || store.availableFrames[index] === 1)
}

function frameFromStore(index: number): AseFrame | null {
  if (!trajectoryStore || !isStoreFrameAvailable(trajectoryStore, index)) return null
  const nAtoms = trajectoryStore.nAtoms
  const atomOffset = index * nAtoms
  const positionOffset = atomOffset * 3
  const cellOffset = index * 9
  const positions: number[][] = []
  const tags: number[] = []
  const fixedIndices: number[] = []
  const cell: number[][] = []

  for (let atomIndex = 0; atomIndex < nAtoms; atomIndex += 1) {
    const offset = positionOffset + atomIndex * 3
    positions.push([
      trajectoryStore.positions[offset] ?? 0,
      trajectoryStore.positions[offset + 1] ?? 0,
      trajectoryStore.positions[offset + 2] ?? 0
    ])
    tags.push(trajectoryStore.tags?.[atomOffset + atomIndex] ?? 0)
    if (trajectoryStore.fixedMask?.[atomOffset + atomIndex]) fixedIndices.push(atomIndex)
  }

  for (let row = 0; row < 3; row += 1) {
    const offset = cellOffset + row * 3
    cell.push([
      trajectoryStore.cells?.[offset] ?? 0,
      trajectoryStore.cells?.[offset + 1] ?? 0,
      trajectoryStore.cells?.[offset + 2] ?? 0
    ])
  }

  const energy = trajectoryStore.energy?.[index]
  const fmax = trajectoryStore.fmax?.[index]
  return {
    frame_index: index,
    positions,
    cell,
    pbc: trajectoryStore.pbc ?? [false, false, false],
    tags,
    fixed_indices: fixedIndices,
    energy: energy === undefined || Number.isNaN(energy) ? null : energy,
    fmax: fmax === undefined || Number.isNaN(fmax) ? null : fmax,
    symbols: trajectoryStore.symbols,
    numbers: trajectoryStore.numbers
  }
}

function flattenPositions(positions: number[][], nAtoms: number) {
  const output = new Float32Array(nAtoms * 3)
  for (let index = 0; index < nAtoms; index += 1) {
    const position = positions[index] ?? [0, 0, 0]
    const offset = index * 3
    output[offset] = position[0] ?? 0
    output[offset + 1] = position[1] ?? 0
    output[offset + 2] = position[2] ?? 0
  }
  return output
}

function flattenCell(cell: Float32Array | number[][]) {
  if (cell instanceof Float32Array) return cell.subarray(0, 9)
  const output = new Float32Array(9)
  for (let row = 0; row < 3; row += 1) {
    const values = cell[row] ?? [0, 0, 0]
    const offset = row * 3
    output[offset] = values[0] ?? 0
    output[offset + 1] = values[1] ?? 0
    output[offset + 2] = values[2] ?? 0
  }
  return output
}

function finiteOrNaN(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? Number.NaN : value
}

function clampFrameIndex(index: number) {
  const max = Math.max(0, (props.asePreview?.n_frames ?? 1) - 1)
  if (!Number.isFinite(index)) return currentFrame.value.frame_index
  return Math.min(max, Math.max(0, Math.round(index)))
}

function resetAseState() {
  preloadVersion += 1
  frameLoadVersion += 1
  closeActiveEventSource()
  clearFrameLoading(frameLoadVersion)
  jsonFrameCache.clear()
  variableBinaryFrameCache.clear()
  pendingChunks.clear()
  pendingVariableChunks.clear()
  pendingJsonChunks.clear()
  const frame = props.asePreview?.frame ?? emptyFrame()
  currentFrame.value = frame
  requestedFrameIndex.value = frame.frame_index
  cacheJsonFrame(frame)
  trajectoryStore = props.asePreview ? createTrajectoryStore(props.asePreview) : null
  updateCachedFrameCount()
  startTrajectoryPreload()
}

function pauseTrajectoryPreload() {
  preloadVersion += 1
  closeActiveEventSource()
}

function startTrajectoryPreload() {
  if (!props.active || !props.asePreview?.is_trajectory) return
  const version = ++preloadVersion
  void preloadTrajectoryFrames(version)
}

function disposeChemSSHViewer() {
  chemsshViewer?.dispose()
  chemsshViewer = null
}

onMounted(() => {
  resetAseState()
  void renderStructure()
  if (container.value && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(resizeViewer)
    resizeObserver.observe(container.value)
  }
})

onBeforeUnmount(() => {
  // Clean up SSE connection
  closeActiveEventSource()

  // Clean up ResizeObserver
  resizeObserver?.disconnect()
  resizeObserver = null

  // Clean up viewer
  disposeChemSSHViewer()

  // Cancel animation frame
  if (frameRequestHandle) window.cancelAnimationFrame(frameRequestHandle)

  // Clean up caches to free memory
  jsonFrameCache.clear()
  variableBinaryFrameCache.clear()
  pendingChunks.clear()
  pendingVariableChunks.clear()
  pendingJsonChunks.clear()

  // Clean up trajectory store
  trajectoryStore = null
  viewerModulePromise = null
})

watch(
  () => props.asePreview,
  () => {
    structureWarningDismissed.value = false
    resetStructureSwitchState()
    resetAseState()
    void renderStructure()
  }
)

watch(
  showStructureWarning,
  visible => {
    if (!visible) structureWarningDismissed.value = false
  }
)

watch(
  () => props.active,
  async active => {
    if (!active) {
      pauseTrajectoryPreload()
      return
    }
    await nextTick()
    resizeViewer()
    startTrajectoryPreload()
  },
  { flush: 'post' }
)

// Optimize: Combine style and display option watches into a single watchEffect
watchEffect(() => {
  // Track dependencies first
  const styleChanged = [selectedStyle.value, bondScale.value, showAtomIndex.value, showAtomTag.value, atomIndexBase.value]
  const displayChanged = [supercellX.value, supercellY.value, supercellZ.value, wrapAtoms.value, structureHasCell.value]

  // Early return if viewer not ready, but still track dependencies
  if (!chemsshViewer || !props.asePreview) {
    // If viewer doesn't exist but dependencies changed, trigger re-render
    void styleChanged
    void displayChanged
    void renderStructure(true)
    return
  }

  // Keep cell-only controls constrained without blocking atom labels or bond styling.
  if (!structureHasCell.value) {
    if (supercellX.value !== 1 || supercellY.value !== 1 || supercellZ.value !== 1) resetSupercell()
    if (wrapAtoms.value) wrapAtoms.value = false
  } else {
    clampSupercell()
  }

  // Apply updates to viewer
  chemsshViewer.setStyle(viewerStyle())
  chemsshViewer.setLabelOptions({
    showAtomIndex: showAtomIndex.value,
    showAtomTag: showAtomTag.value,
    atomIndexBase: atomIndexBase.value
  })
  chemsshViewer.setDisplayOptions(viewerDisplayOptions())
})
</script>
