import { frameFromTrajectoryStore } from './TrajectoryStore'
import { ThreeStructureRenderer } from './ThreeStructureRenderer'
import { NUMBER_SYMBOLS, elementFromNumber, elementColor, covalentRadius } from './elements'
import type {
  FixedAtomOptions,
  LabelOptions,
  SetStructureOptions,
  SetTrajectoryOptions,
  StructureDisplayOptions,
  StructureFrame,
  TrajectoryStore,
  ViewerOptions,
  ViewerStyle
} from './types'

type Vec3 = [number, number, number]
type ScreenPoint = { x: number; y: number }
type CellCopies = { x: number; y: number; z: number }

interface InternalFrame {
  frameIndex: number
  nAtoms: number
  symbols: string[]
  numbers: number[]
  positions: Float32Array
  cell?: Float32Array
  pbc?: boolean[]
  tags?: Int32Array
  fixedMask?: Uint8Array
  energy?: number | null
  fmax?: number | null
  cellCopies?: CellCopies
}

interface Bond {
  a: number
  b: number
}

interface ProjectedAtom {
  index: number
  x: number
  y: number
  depth: number
  radius: number
  color: string
  symbol: string
  tag: number
  fixed: boolean
  position: Vec3
}

interface SelectedAtomDetails {
  index: number
  symbol: string
  tag: number
  fixed: boolean
  position: Vec3
}

const DEFAULT_STYLE: Required<ViewerStyle> = {
  mode: 'stick',
  atomScale: 0.42,
  bondRadius: 0.065,
  bondScale: 1.25,
  backgroundColor: '#ffffff',
  showCell: true
}

const DEFAULT_LABELS: Required<LabelOptions> = {
  showAtomIndex: false,
  showAtomTag: false,
  atomIndexBase: 0,
  maxLabels: 600
}

const DEFAULT_FIXED: Required<FixedAtomOptions> = {
  show: true,
  color: '#26323f'
}

const DEFAULT_DISPLAY_OPTIONS = {
  supercell: {
    x: 1,
    y: 1,
    z: 1
  },
  wrap: false
}


const PICK_BUCKET_SIZE = 48
const PICK_BUCKET_MARGIN = 48
const LABEL_GRID_WIDTH = 54
const LABEL_GRID_HEIGHT = 18
const LABEL_GRID_MARGIN = 48
const LABEL_SCAN_TARGET_ATOMS = 120000


export class ChemSSHStructureViewer {
  private readonly container: HTMLElement
  private readonly root: HTMLDivElement
  private readonly sceneCanvas: HTMLCanvasElement
  private readonly overlayCanvas: HTMLCanvasElement
  private readonly overlayContext: CanvasRenderingContext2D
  private readonly renderer: ThreeStructureRenderer
  private style: Required<ViewerStyle>
  private labelOptions: Required<LabelOptions>
  private fixedOptions: Required<FixedAtomOptions>
  private displayOptions = cloneDisplayOptions(DEFAULT_DISPLAY_OPTIONS)
  private sourceFrame: InternalFrame | null = null
  private frame: InternalFrame | null = null
  private trajectory: TrajectoryStore | null = null
  private bonds: Bond[] = []
  private width = 1
  private height = 1
  private dpr = 1
  private center: Vec3 = [0, 0, 0]
  private sceneRadius = 1
  private baseScale = 1
  private transform = createInitialTransform()
  private zoom = 1
  private panX = 0
  private panY = 0
  private rafHandle = 0
  private hoveredAtom: number | null = null
  private projectedAtoms: ProjectedAtom[] = []
  private projectedPickBuckets: ProjectedAtom[][] = []
  private projectedPickColumns = 0
  private projectedPickRows = 0
  private isDragging = false
  private dragMode: 'select' | 'pan' | 'orbit' | 'roll' = 'select'
  private lastPointerX = 0
  private lastPointerY = 0
  private lastPickTime = 0
  private selectionStart: ScreenPoint | null = null
  private selectionEnd: ScreenPoint | null = null
  private selectedAtomOrder: number[] = []
  private projectedAtomsDirty = true

  constructor(container: HTMLElement, options: ViewerOptions = {}) {
    this.container = container
    this.style = { ...DEFAULT_STYLE, ...options.style, backgroundColor: options.backgroundColor ?? options.style?.backgroundColor ?? DEFAULT_STYLE.backgroundColor }
    this.labelOptions = { ...DEFAULT_LABELS, ...options.labelOptions }
    this.fixedOptions = { ...DEFAULT_FIXED, ...options.fixedAtomOptions }
    this.displayOptions = normalizeDisplayOptions(options.displayOptions)

    this.root = document.createElement('div')
    this.root.className = 'chemssh-viewer-root'
    this.root.style.position = 'relative'
    this.root.style.width = '100%'
    this.root.style.height = '100%'
    this.root.style.overflow = 'hidden'
    this.root.style.background = this.style.backgroundColor

    this.overlayCanvas = document.createElement('canvas')
    this.renderer = new ThreeStructureRenderer(this.root, this.style)
    this.renderer.setFixedMarkersVisible(this.fixedOptions.show)
    this.sceneCanvas = this.renderer.canvas
    this.overlayCanvas.style.position = 'absolute'
    this.overlayCanvas.style.inset = '0'
    this.overlayCanvas.style.width = '100%'
    this.overlayCanvas.style.height = '100%'
    this.overlayCanvas.style.pointerEvents = 'none'

    const overlayContext = this.overlayCanvas.getContext('2d')
    if (!overlayContext) {
      throw new Error('Canvas2D overlay is not available')
    }
    this.overlayContext = overlayContext

    this.root.append(this.overlayCanvas)
    this.container.append(this.root)
    this.installEvents()
    this.resize()
  }

  setStructure(frame: StructureFrame, options: SetStructureOptions = {}) {
    this.trajectory = null
    this.useSourceFrame(normalizeFrame(frame), {
      keepView: Boolean(options.keepView),
      updateBounds: true
    })
  }

  setTrajectory(trajectory: TrajectoryStore, options: SetTrajectoryOptions = {}) {
    this.trajectory = trajectory
    const preferredIndex = options.initialFrameIndex ?? trajectory.initialFrameIndex ?? 0
    const frame = frameFromTrajectoryStore(trajectory, preferredIndex) ?? firstAvailableFrame(trajectory)
    if (!frame) return
    this.useSourceFrame(normalizeFrame(frame), {
      keepView: Boolean(options.keepView),
      updateBounds: true
    })
  }

  setFrame(index: number) {
    if (!this.trajectory) return
    const frame = frameFromTrajectoryStore(this.trajectory, index)
    if (!frame) return
    this.useSourceFrame(normalizeFrame(frame), {
      keepView: true,
      updateBounds: false
    })
  }

  setStyle(style: ViewerStyle) {
    const previous = this.style
    const previousBondScale = this.style.bondScale
    this.style = { ...this.style, ...style }
    if (this.frame && style.bondScale !== undefined && style.bondScale !== previousBondScale) {
      this.bonds = estimateBonds(this.frame, this.style.bondScale)
      this.renderer.setStructure(this.frame, this.bonds, this.center)
    }
    this.root.style.background = this.style.backgroundColor
    this.renderer.setStyle(this.style)
    if (previous.mode !== this.style.mode || previous.atomScale !== this.style.atomScale) this.invalidateProjectedAtoms()
    this.requestRender()
  }

  setLabelOptions(options: LabelOptions) {
    this.labelOptions = { ...this.labelOptions, ...options }
    this.requestRender()
  }

  setFixedAtomOptions(options: FixedAtomOptions) {
    this.fixedOptions = { ...this.fixedOptions, ...options }
    this.renderer.setFixedMarkersVisible(this.fixedOptions.show)
    this.requestRender()
  }

  setDisplayOptions(options: StructureDisplayOptions) {
    const nextOptions = normalizeDisplayOptions({
      ...this.displayOptions,
      ...options,
      supercell: {
        ...this.displayOptions.supercell,
        ...options.supercell
      }
    })
    if (displayOptionsEqual(this.displayOptions, nextOptions)) return
    this.displayOptions = nextOptions
    if (!this.sourceFrame) return
    this.frame = buildDisplayFrame(this.sourceFrame, this.displayOptions)
    this.bonds = estimateBonds(this.frame, this.style.bondScale)
    this.updateBounds()
    this.renderer.setStructure(this.frame, this.bonds, this.center)
    this.syncSelectionForFrame(true)
    this.invalidateProjectedAtoms()
    this.requestRender()
  }

  resize() {
    const rect = this.container.getBoundingClientRect()
    const nextWidth = Math.max(1, Math.round(rect.width))
    const nextHeight = Math.max(1, Math.round(rect.height))
    const nextDpr = Math.min(window.devicePixelRatio || 1, 2)
    const pixelWidth = Math.max(1, Math.floor(nextWidth * nextDpr))
    const pixelHeight = Math.max(1, Math.floor(nextHeight * nextDpr))
    if (
      this.width === nextWidth &&
      this.height === nextHeight &&
      this.dpr === nextDpr &&
      this.overlayCanvas.width === pixelWidth &&
      this.overlayCanvas.height === pixelHeight
    ) {
      return
    }
    this.width = nextWidth
    this.height = nextHeight
    this.dpr = nextDpr
    if (this.overlayCanvas.width !== pixelWidth) this.overlayCanvas.width = pixelWidth
    if (this.overlayCanvas.height !== pixelHeight) this.overlayCanvas.height = pixelHeight
    this.updateFitScale()
    this.invalidateProjectedAtoms()
    this.requestRender()
  }

  resetView() {
    this.resetCamera()
    this.requestRender()
  }

  clearSelection() {
    this.selectionStart = null
    this.selectionEnd = null
    this.hoveredAtom = null
    this.selectedAtomOrder = []
    this.renderer.setSelectedAtoms(this.selectedAtomOrder)
    this.requestRender()
  }

  dispose() {
    this.removeEvents()
    if (this.rafHandle) window.cancelAnimationFrame(this.rafHandle)
    this.rafHandle = 0
    this.container.innerHTML = ''
    this.sourceFrame = null
    this.frame = null
    this.trajectory = null
    this.projectedAtoms = []
    this.clearProjectedPickBuckets()
    this.selectedAtomOrder = []
    this.renderer.dispose()
  }

  screenshot() {
    this.render()
    const output = document.createElement('canvas')
    output.width = this.overlayCanvas.width
    output.height = this.overlayCanvas.height
    const context = output.getContext('2d')
    if (!context) return ''
    context.drawImage(this.sceneCanvas, 0, 0)
    context.drawImage(this.overlayCanvas, 0, 0)
    return output.toDataURL('image/png')
  }

  private useSourceFrame(frame: InternalFrame, options: { keepView: boolean; updateBounds: boolean }) {
    this.sourceFrame = frame
    this.frame = buildDisplayFrame(frame, this.displayOptions)
    this.bonds = estimateBonds(this.frame, this.style.bondScale)
    if (options.updateBounds) this.updateBounds()
    if (!options.keepView) this.resetCamera()
    this.renderer.setStructure(this.frame, this.bonds, this.center)
    this.renderer.setSelectedAtoms(this.selectedAtomOrder)
    this.syncSelectionForFrame(options.keepView)
    this.invalidateProjectedAtoms()
    this.requestRender()
  }

  private installEvents() {
    this.sceneCanvas.addEventListener('pointerdown', this.handlePointerDown)
    this.sceneCanvas.addEventListener('pointermove', this.handlePointerMove)
    this.sceneCanvas.addEventListener('pointerup', this.handlePointerUp)
    this.sceneCanvas.addEventListener('pointercancel', this.handlePointerUp)
    this.sceneCanvas.addEventListener('pointerleave', this.handlePointerLeave)
    this.sceneCanvas.addEventListener('wheel', this.handleWheel, { passive: false })
    this.sceneCanvas.addEventListener('contextmenu', this.preventContextMenu)
    window.addEventListener('resize', this.handleWindowResize)
  }

  private removeEvents() {
    this.sceneCanvas.removeEventListener('pointerdown', this.handlePointerDown)
    this.sceneCanvas.removeEventListener('pointermove', this.handlePointerMove)
    this.sceneCanvas.removeEventListener('pointerup', this.handlePointerUp)
    this.sceneCanvas.removeEventListener('pointercancel', this.handlePointerUp)
    this.sceneCanvas.removeEventListener('pointerleave', this.handlePointerLeave)
    this.sceneCanvas.removeEventListener('wheel', this.handleWheel)
    this.sceneCanvas.removeEventListener('contextmenu', this.preventContextMenu)
    window.removeEventListener('resize', this.handleWindowResize)
  }

  private handleWindowResize = () => {
    this.resize()
  }

  private preventContextMenu = (event: MouseEvent) => {
    event.preventDefault()
  }

  private handlePointerDown = (event: PointerEvent) => {
    event.preventDefault()
    const point = this.pointerPoint(event)
    this.isDragging = true
    this.dragMode = this.dragModeForPointer(event, point)
    this.lastPointerX = event.clientX
    this.lastPointerY = event.clientY
    this.selectionStart = this.dragMode === 'select' ? point : null
    this.selectionEnd = this.dragMode === 'select' ? point : null
    this.sceneCanvas.setPointerCapture(event.pointerId)
    this.sceneCanvas.style.cursor = this.cursorForDragMode(this.dragMode)
    this.requestRender()
  }

  private handlePointerMove = (event: PointerEvent) => {
    if (this.isDragging) {
      event.preventDefault()
      const previousX = this.lastPointerX
      const previousY = this.lastPointerY
      const dx = event.clientX - previousX
      const dy = event.clientY - previousY
      let viewChanged = false
      if (this.dragMode === 'pan') {
        this.panX += dx
        this.panY += dy
        viewChanged = true
      } else if (this.dragMode === 'orbit') {
        this.rotateOrbit(dx * 0.01, dy * 0.01)
        viewChanged = true
      } else if (this.dragMode === 'roll') {
        this.rotateRoll(-angleDeltaAround(this.viewCenter(), { x: previousX, y: previousY }, { x: event.clientX, y: event.clientY }))
        viewChanged = true
      } else {
        this.selectionEnd = this.pointerPoint(event)
      }
      this.lastPointerX = event.clientX
      this.lastPointerY = event.clientY
      if (viewChanged) this.invalidateProjectedAtoms()
      this.requestRender()
      return
    }

    const now = performance.now()
    if (now - this.lastPickTime < 33) return
    this.lastPickTime = now
    const point = this.pointerPoint(event)
    this.updateHover(point.x, point.y)
  }

  private handlePointerUp = (event: PointerEvent) => {
    const point = this.pointerPoint(event)
    if (this.dragMode === 'select') {
      this.completeAtomSelection(event, point)
    }
    this.isDragging = false
    if (this.sceneCanvas.hasPointerCapture(event.pointerId)) {
      this.sceneCanvas.releasePointerCapture(event.pointerId)
    }
    this.selectionStart = null
    this.selectionEnd = null
    this.sceneCanvas.style.cursor = 'crosshair'
    this.requestRender()
  }

  private handlePointerLeave = () => {
    if (this.isDragging) return
    this.isDragging = false
    this.selectionStart = null
    this.selectionEnd = null
    if (this.hoveredAtom !== null) {
      this.hoveredAtom = null
      this.requestRender()
    }
    this.sceneCanvas.style.cursor = 'crosshair'
  }

  private handleWheel = (event: WheelEvent) => {
    event.preventDefault()
    const factor = Math.exp(-event.deltaY * 0.001)
    this.zoom = clamp(this.zoom * factor, 0.05, 80)
    this.invalidateProjectedAtoms()
    this.requestRender()
  }

  private pointerPoint(event: PointerEvent) {
    const rect = this.sceneCanvas.getBoundingClientRect()
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top
    }
  }

  private dragModeForPointer(event: PointerEvent, point: { x: number; y: number }): 'select' | 'pan' | 'orbit' | 'roll' {
    if (event.button === 1) return 'pan'
    if (event.button === 2) return this.rightDragModeForPoint(point)
    return 'select'
  }

  private rightDragModeForPoint(point: { x: number; y: number }): 'orbit' | 'roll' {
    const bounds = this.structureScreenBounds()
    if (!bounds) return 'orbit'
    if (this.isPointNearStructure(point, bounds)) return 'orbit'

    const sidePadding = Math.max(48, Math.min(96, (bounds.right - bounds.left) * 0.18))
    const insideStructureColumn = point.x >= bounds.left - sidePadding && point.x <= bounds.right + sidePadding
    if (insideStructureColumn) return 'orbit'

    const horizontalGap = point.x < bounds.left ? bounds.left - point.x : point.x - bounds.right
    const verticalGap = point.y < bounds.top ? bounds.top - point.y : point.y > bounds.bottom ? point.y - bounds.bottom : 0
    return horizontalGap > verticalGap ? 'roll' : 'orbit'
  }

  private structureScreenBounds() {
    const atoms = this.ensureProjectedAtoms()
    if (!atoms.length) return null
    let left = Number.POSITIVE_INFINITY
    let right = Number.NEGATIVE_INFINITY
    let top = Number.POSITIVE_INFINITY
    let bottom = Number.NEGATIVE_INFINITY
    for (const atom of atoms) {
      left = Math.min(left, atom.x - atom.radius)
      right = Math.max(right, atom.x + atom.radius)
      top = Math.min(top, atom.y - atom.radius)
      bottom = Math.max(bottom, atom.y + atom.radius)
    }
    return { left, right, top, bottom }
  }

  private isPointNearStructure(point: { x: number; y: number }, bounds: { left: number; right: number; top: number; bottom: number }) {
    const structureWidth = Math.max(1, bounds.right - bounds.left)
    const structureHeight = Math.max(1, bounds.bottom - bounds.top)
    const horizontalBand = point.x >= bounds.left - Math.max(36, structureWidth * 0.08) && point.x <= bounds.right + Math.max(36, structureWidth * 0.08)
    const verticalBand = point.y >= bounds.top - Math.max(44, structureHeight * 0.35) && point.y <= bounds.bottom + Math.max(44, structureHeight * 0.35)
    if (horizontalBand && verticalBand) return true

    if (point.x >= bounds.left && point.x <= bounds.right && point.y >= bounds.top && point.y <= bounds.bottom) return true

    const centerX = (bounds.left + bounds.right) / 2
    const centerY = (bounds.top + bounds.bottom) / 2
    const normalizedX = (point.x - centerX) / Math.max(60, structureWidth * 0.72)
    const normalizedY = (point.y - centerY) / Math.max(60, structureHeight * 0.72)
    if (normalizedX * normalizedX + normalizedY * normalizedY <= 1) return true

    for (const atom of this.ensureProjectedAtoms()) {
      const dx = point.x - atom.x
      const dy = point.y - atom.y
      const limit = Math.max(26, atom.radius + 14)
      if (dx * dx + dy * dy <= limit * limit) return true
    }
    return false
  }

  private cursorForDragMode(mode: 'select' | 'pan' | 'orbit' | 'roll') {
    if (mode === 'pan') return 'move'
    if (mode === 'select') return 'crosshair'
    return 'grabbing'
  }

  private viewCenter() {
    return {
      x: this.sceneCanvas.getBoundingClientRect().left + this.width / 2 + this.panX,
      y: this.sceneCanvas.getBoundingClientRect().top + this.height / 2 + this.panY
    }
  }

  private updateHover(x: number, y: number) {
    const bestIndex = this.pickAtomAt(x, y)
    if (bestIndex !== this.hoveredAtom) {
      this.hoveredAtom = bestIndex
      this.requestRender()
    }
  }

  private pickAtomAt(x: number, y: number) {
    const atoms = this.ensureProjectedAtoms()
    const candidates = this.projectedPickCandidates(x, y, atoms)
    let bestIndex: number | null = null
    let bestDistance = Number.POSITIVE_INFINITY
    let bestDepth = Number.NEGATIVE_INFINITY
    for (const atom of candidates) {
      const dx = atom.x - x
      const dy = atom.y - y
      const limit = Math.max(atom.radius + 6, 9)
      const distance = dx * dx + dy * dy
      if (distance > limit * limit) continue
      if (distance < bestDistance - 0.001 || (Math.abs(distance - bestDistance) <= 0.001 && atom.depth > bestDepth)) {
        bestDistance = distance
        bestDepth = atom.depth
        bestIndex = atom.index
      }
    }
    return bestIndex
  }

  private completeAtomSelection(event: PointerEvent, point: ScreenPoint) {
    const additive = event.ctrlKey || event.metaKey
    const start = this.selectionStart ?? point
    const end = this.selectionEnd ?? point
    const width = Math.abs(end.x - start.x)
    const height = Math.abs(end.y - start.y)

    if (width <= 4 && height <= 4) {
      const picked = this.pickAtomAt(point.x, point.y)
      if (picked === null) {
        if (!additive) this.replaceSelection([])
        return
      }
      if (additive) this.toggleAtomSelection(picked)
      else this.replaceSelection([picked])
      this.hoveredAtom = picked
      return
    }

    const indices = this.atomIndicesInSelectionRect(start, end)
    if (!indices.length) {
      if (!additive) this.replaceSelection([])
      return
    }
    if (additive) this.appendSelection(indices)
    else this.replaceSelection(indices)
  }

  private atomIndicesInSelectionRect(start: ScreenPoint, end: ScreenPoint) {
    const left = Math.min(start.x, end.x)
    const right = Math.max(start.x, end.x)
    const top = Math.min(start.y, end.y)
    const bottom = Math.max(start.y, end.y)
    return this.ensureProjectedAtoms()
      .filter(atom => atom.x >= left && atom.x <= right && atom.y >= top && atom.y <= bottom)
      .map(atom => atom.index)
      .sort((leftIndex, rightIndex) => leftIndex - rightIndex)
  }

  private replaceSelection(indices: number[]) {
    this.selectedAtomOrder = this.uniqueValidAtomIndices(indices)
    this.renderer.setSelectedAtoms(this.selectedAtomOrder)
  }

  private appendSelection(indices: number[]) {
    const next = [...this.selectedAtomOrder]
    const selected = new Set(next)
    for (const index of indices) {
      if (selected.has(index)) continue
      selected.add(index)
      next.push(index)
    }
    this.selectedAtomOrder = this.uniqueValidAtomIndices(next)
    this.renderer.setSelectedAtoms(this.selectedAtomOrder)
  }

  private toggleAtomSelection(index: number) {
    if (this.selectedAtomOrder.includes(index)) {
      this.selectedAtomOrder = this.selectedAtomOrder.filter(selected => selected !== index)
      this.renderer.setSelectedAtoms(this.selectedAtomOrder)
      return
    }
    this.appendSelection([index])
  }

  private uniqueValidAtomIndices(indices: number[]) {
    const nAtoms = this.frame?.nAtoms ?? 0
    const seen = new Set<number>()
    const output: number[] = []
    for (const index of indices) {
      if (!Number.isInteger(index) || index < 0 || index >= nAtoms || seen.has(index)) continue
      seen.add(index)
      output.push(index)
    }
    return output
  }

  private requestRender() {
    if (this.rafHandle) return
    this.rafHandle = window.requestAnimationFrame(() => {
      this.rafHandle = 0
      this.render()
    })
  }

  private invalidateProjectedAtoms() {
    this.projectedAtomsDirty = true
  }

  private render() {
    const overlay = this.overlayContext
    overlay.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)
    overlay.clearRect(0, 0, this.width, this.height)
    this.renderer.setView({
      width: this.width,
      height: this.height,
      dpr: this.dpr,
      center: this.center,
      sceneRadius: this.sceneRadius,
      baseScale: this.baseScale,
      zoom: this.zoom,
      panX: this.panX,
      panY: this.panY,
      transform: this.transform
    })

    if (!this.frame) {
      this.projectedAtoms = []
      this.clearProjectedPickBuckets()
      this.projectedAtomsDirty = false
      this.renderer.clear()
      return
    }

    const atoms = this.labelsVisible() ? this.projectLabelAtoms() : []
    const hoveredAtom = this.hoveredAtom === null ? null : this.projectAtom(this.hoveredAtom)
    const selectedAtoms = this.selectedAtomOrder.length <= 4
      ? this.selectedAtomOrder.map(index => this.projectAtom(index)).filter((atom): atom is ProjectedAtom => Boolean(atom))
      : []
    this.renderer.render()
    this.drawOverlay(overlay, atoms, hoveredAtom, selectedAtoms)
  }

  private projectAtoms() {
    if (!this.frame) {
      this.projectedAtoms = []
      this.clearProjectedPickBuckets()
      this.projectedAtomsDirty = false
      return []
    }
    const atoms: ProjectedAtom[] = []
    for (let index = 0; index < this.frame.nAtoms; index += 1) {
      const atom = this.projectAtom(index)
      if (atom) atoms.push(atom)
    }
    this.projectedAtoms = atoms
    this.rebuildProjectedPickBuckets(atoms)
    this.projectedAtomsDirty = false
    return atoms
  }

  private rebuildProjectedPickBuckets(atoms: ProjectedAtom[]) {
    const columns = Math.max(1, Math.ceil((this.width + PICK_BUCKET_MARGIN * 2) / PICK_BUCKET_SIZE))
    const rows = Math.max(1, Math.ceil((this.height + PICK_BUCKET_MARGIN * 2) / PICK_BUCKET_SIZE))
    const buckets = Array.from({ length: columns * rows }, () => [] as ProjectedAtom[])

    for (const atom of atoms) {
      const column = Math.floor((atom.x + PICK_BUCKET_MARGIN) / PICK_BUCKET_SIZE)
      const row = Math.floor((atom.y + PICK_BUCKET_MARGIN) / PICK_BUCKET_SIZE)
      if (column < 0 || row < 0 || column >= columns || row >= rows) continue
      buckets[row * columns + column]?.push(atom)
    }

    this.projectedPickBuckets = buckets
    this.projectedPickColumns = columns
    this.projectedPickRows = rows
  }

  private projectedPickCandidates(x: number, y: number, fallback: ProjectedAtom[]) {
    if (!this.projectedPickBuckets.length || !this.projectedPickColumns || !this.projectedPickRows) return fallback
    const centerColumn = Math.floor((x + PICK_BUCKET_MARGIN) / PICK_BUCKET_SIZE)
    const centerRow = Math.floor((y + PICK_BUCKET_MARGIN) / PICK_BUCKET_SIZE)
    const candidates: ProjectedAtom[] = []
    for (let row = centerRow - 1; row <= centerRow + 1; row += 1) {
      if (row < 0 || row >= this.projectedPickRows) continue
      for (let column = centerColumn - 1; column <= centerColumn + 1; column += 1) {
        if (column < 0 || column >= this.projectedPickColumns) continue
        const bucket = this.projectedPickBuckets[row * this.projectedPickColumns + column]
        if (!bucket?.length) continue
        for (const atom of bucket) candidates.push(atom)
      }
    }
    return candidates
  }

  private clearProjectedPickBuckets() {
    this.projectedPickBuckets = []
    this.projectedPickColumns = 0
    this.projectedPickRows = 0
  }

  private ensureProjectedAtoms() {
    if (this.projectedAtomsDirty) return this.projectAtoms()
    return this.projectedAtoms
  }

  private projectAtom(index: number): ProjectedAtom | null {
    if (!this.frame || index < 0 || index >= this.frame.nAtoms) return null
    const offset = index * 3
    const world: Vec3 = [
      this.frame.positions[offset] ?? 0,
      this.frame.positions[offset + 1] ?? 0,
      this.frame.positions[offset + 2] ?? 0
    ]
    const projected = this.projectPoint(world)
    return this.projectedAtomFromScreen(index, world, projected.x, projected.y, projected.depth)
  }

  private projectLabelAtoms() {
    const frame = this.frame
    if (!frame?.nAtoms) return []
    const columns = Math.max(1, Math.ceil(this.width / LABEL_GRID_WIDTH))
    const rows = Math.max(1, Math.ceil(this.height / LABEL_GRID_HEIGHT))
    const candidates = new Array<ProjectedAtom | null>(columns * rows).fill(null)
    const stride = labelProjectionStride(frame.nAtoms)
    const m = this.transform
    const scale = this.baseScale * this.zoom
    const screenCenterX = this.width / 2 + this.panX
    const screenCenterY = this.height / 2 + this.panY

    for (let index = 0; index < frame.nAtoms; index += stride) {
      const offset = index * 3
      const x = (frame.positions[offset] ?? 0) - this.center[0]
      const y = (frame.positions[offset + 1] ?? 0) - this.center[1]
      const z = (frame.positions[offset + 2] ?? 0) - this.center[2]
      const x2 = m[0] * x + m[1] * y + m[2] * z
      const y2 = m[3] * x + m[4] * y + m[5] * z
      const z2 = m[6] * x + m[7] * y + m[8] * z
      const screenX = screenCenterX + x2 * scale
      const screenY = screenCenterY - y2 * scale
      if (screenX < -LABEL_GRID_MARGIN || screenY < -LABEL_GRID_MARGIN || screenX > this.width + LABEL_GRID_MARGIN || screenY > this.height + LABEL_GRID_MARGIN) continue

      const column = Math.floor(clamp(screenX, 0, this.width - 1) / LABEL_GRID_WIDTH)
      const row = Math.floor(clamp(screenY, 0, this.height - 1) / LABEL_GRID_HEIGHT)
      const candidateIndex = row * columns + column
      const current = candidates[candidateIndex]
      if (current && current.depth >= z2) continue
      candidates[candidateIndex] = this.projectedAtomFromScreen(index, [x + this.center[0], y + this.center[1], z + this.center[2]], screenX, screenY, z2)
    }

    return candidates
      .filter((atom): atom is ProjectedAtom => Boolean(atom))
      .sort((left, right) => right.depth - left.depth)
  }

  private projectedAtomFromScreen(index: number, world: Vec3, x: number, y: number, depth: number): ProjectedAtom | null {
    if (!this.frame || index < 0 || index >= this.frame.nAtoms) return null
    const symbol = this.frame.symbols[index] ?? elementFromNumber(this.frame.numbers[index]) ?? 'X'
    const radius = atomScreenRadius(symbol, this.style, this.baseScale * this.zoom)
    return {
      index,
      x,
      y,
      depth,
      radius,
      color: elementColor(symbol),
      symbol,
      tag: this.frame.tags?.[index] ?? 0,
      fixed: this.frame.fixedMask?.[index] === 1,
      position: world
    }
  }

  private projectPoint(point: Vec3) {
    const x = point[0] - this.center[0]
    const y = point[1] - this.center[1]
    const z = point[2] - this.center[2]
    const m = this.transform
    const x2 = m[0] * x + m[1] * y + m[2] * z
    const y2 = m[3] * x + m[4] * y + m[5] * z
    const z2 = m[6] * x + m[7] * y + m[8] * z
    const scale = this.baseScale * this.zoom
    return {
      x: this.width / 2 + this.panX + x2 * scale,
      y: this.height / 2 + this.panY - y2 * scale,
      depth: z2
    }
  }

  private drawOverlay(context: CanvasRenderingContext2D, atoms: ProjectedAtom[], hoveredAtom: ProjectedAtom | null, selectedAtoms: ProjectedAtom[]) {
    this.drawAxes(context)
    this.drawHoveredAtomMarker(context, hoveredAtom)
    this.drawLabels(context, atoms, hoveredAtom)
    this.drawSelectedAtomMarkers(context, selectedAtoms)
    this.drawSelectionInfo(context)
    this.drawSelectionBox(context)
  }

  private drawHoveredAtomMarker(context: CanvasRenderingContext2D, atom: ProjectedAtom | null) {
    if (!atom) return
    if (atom.x < -atom.radius || atom.y < -atom.radius || atom.x > this.width + atom.radius || atom.y > this.height + atom.radius) return
    context.save()
    context.strokeStyle = 'rgba(23, 107, 135, 0.88)'
    context.lineWidth = 2.2
    context.beginPath()
    context.arc(atom.x, atom.y, atom.radius + 3, 0, Math.PI * 2)
    context.stroke()
    context.restore()
  }

  private drawAxes(context: CanvasRenderingContext2D) {
    const size = 96
    const left = this.width - size - 10
    const top = this.height - size - 10
    const origin = { x: left + size * 0.48, y: top + size * 0.58 }
    const length = 38
    const axes = [
      this.projectAxis('X', '#ff3b30', [1, 0, 0], origin, length),
      this.projectAxis('Y', '#34c759', [0, 1, 0], origin, length),
      this.projectAxis('Z', '#0a84ff', [0, 0, 1], origin, length)
    ]

    context.save()
    context.fillStyle = 'rgba(255, 255, 255, 0.82)'
    context.strokeStyle = 'rgba(31, 41, 55, 0.18)'
    context.lineWidth = 1
    roundedRect(context, left, top, size, size, 7)
    context.fill()
    context.stroke()
    context.font = '800 11px Inter, system-ui, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.lineCap = 'round'
    context.lineJoin = 'round'

    for (const axis of axes.filter(axis => axis.depth < 0).sort((a, b) => a.depth - b.depth)) {
      this.drawAxisArrow(context, origin, axis)
    }
    this.drawAxisOrigin(context, origin)
    for (const axis of axes.filter(axis => axis.depth >= 0).sort((a, b) => a.depth - b.depth)) {
      this.drawAxisArrow(context, origin, axis)
    }
    context.restore()
  }

  private projectAxis(label: string, color: string, vector: Vec3, origin: ScreenPoint, length: number) {
    const m = this.transform
    const x = m[0] * vector[0] + m[1] * vector[1] + m[2] * vector[2]
    const y = m[3] * vector[0] + m[4] * vector[1] + m[5] * vector[2]
    const depth = m[6] * vector[0] + m[7] * vector[1] + m[8] * vector[2]
    const perspective = clamp(1 + depth * 0.22, 0.72, 1.24)
    return {
      label,
      color,
      depth,
      perspective,
      end: {
        x: origin.x + x * length * perspective,
        y: origin.y - y * length * perspective
      }
    }
  }

  private drawAxisOrigin(context: CanvasRenderingContext2D, origin: ScreenPoint) {
    const gradient = context.createRadialGradient(origin.x - 2.5, origin.y - 3, 1, origin.x, origin.y, 7)
    gradient.addColorStop(0, '#ffffff')
    gradient.addColorStop(0.62, '#9aa8b5')
    gradient.addColorStop(1, '#26323f')
    context.save()
    context.fillStyle = gradient
    context.strokeStyle = 'rgba(17, 24, 39, 0.38)'
    context.lineWidth = 1
    context.beginPath()
    context.arc(origin.x, origin.y, 6.4, 0, Math.PI * 2)
    context.fill()
    context.stroke()
    context.restore()
  }

  private drawAxisArrow(
    context: CanvasRenderingContext2D,
    origin: ScreenPoint,
    axis: { label: string; color: string; depth: number; perspective: number; end: ScreenPoint }
  ) {
    const dx = axis.end.x - origin.x
    const dy = axis.end.y - origin.y
    const length = Math.hypot(dx, dy)
    const frontness = clamp((axis.depth + 1) / 2, 0, 1)
    const lineWidth = 1.5 + frontness * 1.7
    context.save()
    context.globalAlpha = 0.48 + frontness * 0.5
    context.strokeStyle = axis.color
    context.fillStyle = axis.color
    context.lineWidth = lineWidth + 2
    context.strokeStyle = 'rgba(255, 255, 255, 0.9)'
    context.beginPath()
    context.moveTo(origin.x, origin.y)
    context.lineTo(axis.end.x, axis.end.y)
    context.stroke()

    if (length < 7) {
      const radius = 4.6 + axis.perspective * 2.2
      context.strokeStyle = 'rgba(17, 24, 39, 0.58)'
      context.fillStyle = axis.color
      context.lineWidth = 1.2
      context.beginPath()
      context.arc(axis.end.x, axis.end.y, radius, 0, Math.PI * 2)
      context.fill()
      context.stroke()
      context.fillText(axis.label, axis.end.x + 12, axis.end.y - 9)
      context.restore()
      return
    }

    const unitX = dx / length
    const unitY = dy / length
    const perpX = -unitY
    const perpY = unitX
    const headLength = clamp(8.2 * axis.perspective, 6, 11)
    const headWidth = clamp(5.5 * axis.perspective, 4, 8)
    const baseX = axis.end.x - unitX * headLength
    const baseY = axis.end.y - unitY * headLength

    const rodGradient = context.createLinearGradient(origin.x, origin.y, axis.end.x, axis.end.y)
    rodGradient.addColorStop(0, darkenColor(axis.color, 0.22))
    rodGradient.addColorStop(1, axis.color)
    context.strokeStyle = rodGradient
    context.lineWidth = lineWidth
    context.beginPath()
    context.moveTo(origin.x, origin.y)
    context.lineTo(baseX, baseY)
    context.stroke()

    context.strokeStyle = 'rgba(17, 24, 39, 0.42)'
    context.fillStyle = axis.color
    context.lineWidth = 0.8
    context.beginPath()
    context.moveTo(axis.end.x, axis.end.y)
    context.lineTo(baseX + perpX * headWidth, baseY + perpY * headWidth)
    context.lineTo(baseX - perpX * headWidth, baseY - perpY * headWidth)
    context.closePath()
    context.fill()
    context.stroke()

    context.fillStyle = '#111827'
    context.strokeStyle = 'rgba(255, 255, 255, 0.9)'
    context.lineWidth = 3
    const labelX = axis.end.x + unitX * 10
    const labelY = axis.end.y + unitY * 10
    context.strokeText(axis.label, labelX, labelY)
    context.fillText(axis.label, labelX, labelY)
    context.restore()
  }

  private drawSelectedAtomMarkers(context: CanvasRenderingContext2D, atoms: ProjectedAtom[]) {
    if (!atoms.length) return
    const showOrderBadges = this.selectedAtomOrder.length <= 4
    context.save()
    context.font = '800 10px Inter, system-ui, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    for (let orderIndex = 0; orderIndex < atoms.length; orderIndex += 1) {
      const atom = atoms[orderIndex]
      if (showOrderBadges) {
        const x = atom.x + atom.radius * 0.72
        const y = atom.y - atom.radius * 0.72
        context.fillStyle = '#faff00'
        context.strokeStyle = 'rgba(0, 0, 0, 0.82)'
        context.lineWidth = 2.5
        context.beginPath()
        context.arc(x, y, 9, 0, Math.PI * 2)
        context.fill()
        context.stroke()
        context.fillStyle = '#111827'
        context.fillText(String(orderIndex + 1), x, y + 0.5)
      }
    }
    context.restore()
  }

  private drawSelectionInfo(context: CanvasRenderingContext2D) {
    const lines = this.selectionInfoLines()
    if (!lines.length) return

    context.save()
    context.font = '750 12px Inter, system-ui, sans-serif'
    context.textBaseline = 'middle'
    const paddingX = 10
    const paddingY = 8
    const lineHeight = 17
    const maxWidth = Math.max(120, Math.min(360, this.width - 24))
    const measuredWidth = Math.max(...lines.map(line => context.measureText(line).width))
    const width = Math.min(maxWidth, Math.max(120, measuredWidth + paddingX * 2))
    const height = paddingY * 2 + lineHeight * lines.length
    const left = 10
    const top = Math.max(10, this.height - height - 10)

    context.fillStyle = 'rgba(255, 255, 255, 0.9)'
    context.strokeStyle = 'rgba(89, 101, 121, 0.18)'
    context.lineWidth = 1
    roundedRect(context, left, top, width, height, 6)
    context.fill()
    context.stroke()

    context.save()
    context.beginPath()
    roundedRect(context, left, top, width, height, 6)
    context.clip()
    context.fillStyle = '#26323f'
    for (let index = 0; index < lines.length; index += 1) {
      const y = top + paddingY + lineHeight * index + lineHeight / 2
      context.fillText(lines[index], left + paddingX, y, width - paddingX * 2)
    }
    context.restore()
    context.restore()
  }

  private selectionInfoLines() {
    if (!this.selectedAtomOrder.length) return []
    if (this.selectedAtomOrder.length > 4) return [this.selectionSummaryLabel(this.selectedAtomOrder)]

    const atoms = this.selectedAtomOrder
      .map(index => this.selectedAtomDetails(index))
      .filter((atom): atom is SelectedAtomDetails => Boolean(atom))
    if (!atoms.length) return []
    if (atoms.length === 1) return [this.selectionAtomLabel(atoms[0])]

    const label = atoms.slice(0, 4).map(atom => atom.symbol).join('-')
    if (atoms.length === 2) {
      return [`${label} ${formatMeasurement(distance(atoms[0].position, atoms[1].position), 3)} \u00c5`]
    }
    if (atoms.length === 3) {
      return [`${label} ${formatMeasurement(angleDegrees(atoms[0].position, atoms[1].position, atoms[2].position), 1)}\u00b0`]
    }
    if (atoms.length === 4) {
      return [`${label} ${formatMeasurement(dihedralDegrees(atoms[0].position, atoms[1].position, atoms[2].position, atoms[3].position), 1)}\u00b0`]
    }
    return [elementCountSummary(atoms.map(atom => atom.symbol), atoms.length)]
  }

  private selectionSummaryLabel(indices: number[]) {
    if (!this.frame) return ''
    const symbols: string[] = []
    for (const index of indices) {
      if (index < 0 || index >= this.frame.nAtoms) continue
      symbols.push(this.frame.symbols[index] ?? elementFromNumber(this.frame.numbers[index]) ?? 'X')
    }
    return elementCountSummary(symbols, symbols.length)
  }

  private selectedAtomDetails(index: number): SelectedAtomDetails | null {
    if (!this.frame || index < 0 || index >= this.frame.nAtoms) return null
    const offset = index * 3
    const symbol = this.frame.symbols[index] ?? elementFromNumber(this.frame.numbers[index]) ?? 'X'
    return {
      index,
      symbol,
      tag: this.frame.tags?.[index] ?? 0,
      fixed: this.frame.fixedMask?.[index] === 1,
      position: [
        this.frame.positions[offset] ?? 0,
        this.frame.positions[offset + 1] ?? 0,
        this.frame.positions[offset + 2] ?? 0
      ] as Vec3
    }
  }

  private selectionAtomLabel(atom: SelectedAtomDetails) {
    const fixed = atom.fixed ? ' fixed' : ''
    return `#${this.displayAtomIndex(atom.index)} ${atom.symbol} tag=${atom.tag}${fixed} (${atom.position[0].toFixed(3)}, ${atom.position[1].toFixed(3)}, ${atom.position[2].toFixed(3)})`
  }

  private drawLabels(context: CanvasRenderingContext2D, atoms: ProjectedAtom[], hovered: ProjectedAtom | null) {
    const showLabels = this.labelsVisible()
    if (!showLabels && !hovered) return

    context.save()
    context.font = '700 11px Inter, system-ui, sans-serif'
    context.textBaseline = 'middle'
    context.lineJoin = 'round'
    const occupied: Array<{ x: number; y: number; width: number; height: number }> = []
    const sortedAtoms = atoms
    const maxLabels = atoms.length <= 500 ? atoms.length : this.labelOptions.maxLabels
    let drawn = 0

    if (showLabels) {
      for (const atom of sortedAtoms) {
        if (drawn >= maxLabels) break
        const text = this.atomLabel(atom)
        if (!text) continue
        const metrics = context.measureText(text)
        const width = metrics.width + 8
        const height = 15
        const x = atom.x + Math.max(4, atom.radius * 0.28)
        const y = atom.y - Math.max(2, atom.radius * 0.18)
        const box = { x: x - 4, y: y - height / 2, width, height }
        if (box.x < 0 || box.y < 0 || box.x + box.width > this.width || box.y + box.height > this.height) continue
        if (intersectsAny(box, occupied)) continue
        occupied.push(box)
        this.drawText(context, text, x, y, labelColor(atom.color))
        drawn += 1
      }
    }

    if (hovered) {
      const text = this.hoverLabel(hovered)
      const x = clamp(hovered.x + hovered.radius + 10, 8, this.width - 8)
      const y = clamp(hovered.y - hovered.radius - 12, 10, this.height - 10)
      this.drawText(context, text, x, y, '#111827', true)
    }
    context.restore()
  }

  private labelsVisible() {
    return this.labelOptions.showAtomIndex || this.labelOptions.showAtomTag
  }

  private drawSelectionBox(context: CanvasRenderingContext2D) {
    if (!this.selectionStart || !this.selectionEnd) return
    const left = Math.min(this.selectionStart.x, this.selectionEnd.x)
    const top = Math.min(this.selectionStart.y, this.selectionEnd.y)
    const width = Math.abs(this.selectionEnd.x - this.selectionStart.x)
    const height = Math.abs(this.selectionEnd.y - this.selectionStart.y)
    if (width < 2 && height < 2) return
    context.save()
    context.fillStyle = 'rgba(23, 107, 135, 0.08)'
    context.strokeStyle = 'rgba(23, 107, 135, 0.72)'
    context.lineWidth = 1
    context.setLineDash([4, 3])
    context.fillRect(left, top, width, height)
    context.strokeRect(left + 0.5, top + 0.5, width, height)
    context.restore()
  }

  private atomLabel(atom: ProjectedAtom) {
    const parts: string[] = []
    if (this.labelOptions.showAtomIndex) parts.push(String(this.displayAtomIndex(atom.index)))
    if (this.labelOptions.showAtomTag) parts.push(`T${atom.tag}`)
    return parts.join(' ')
  }

  private hoverLabel(atom: ProjectedAtom) {
    const fixed = atom.fixed ? ' fixed' : ''
    return `#${this.displayAtomIndex(atom.index)} ${atom.symbol} tag=${atom.tag}${fixed} (${atom.position[0].toFixed(3)}, ${atom.position[1].toFixed(3)}, ${atom.position[2].toFixed(3)})`
  }

  private displayAtomIndex(index: number) {
    return index + this.labelOptions.atomIndexBase
  }

  private drawText(context: CanvasRenderingContext2D, text: string, x: number, y: number, color: string, background = false) {
    if (background) {
      const metrics = context.measureText(text)
      const width = metrics.width + 10
      const height = 18
      const left = Math.min(x, this.width - width - 6)
      const top = clamp(y - height / 2, 6, this.height - height - 6)
      context.fillStyle = 'rgba(255, 255, 255, 0.9)'
      context.strokeStyle = 'rgba(89, 101, 121, 0.2)'
      context.lineWidth = 1
      roundedRect(context, left, top, width, height, 5)
      context.fill()
      context.stroke()
      x = left + 5
      y = top + height / 2
    }
    context.lineWidth = 3
    context.strokeStyle = color === '#ffffff' ? 'rgba(17, 24, 39, 0.68)' : 'rgba(255, 255, 255, 0.88)'
    context.fillStyle = color
    context.strokeText(text, x, y)
    context.fillText(text, x, y)
  }

  private syncSelectionForFrame(keepSelection: boolean) {
    this.selectedAtomOrder = keepSelection ? this.uniqueValidAtomIndices(this.selectedAtomOrder) : []
    this.renderer.setSelectedAtoms(this.selectedAtomOrder)
    if (this.hoveredAtom !== null && (this.hoveredAtom < 0 || this.hoveredAtom >= (this.frame?.nAtoms ?? 0))) this.hoveredAtom = null
  }

  private updateBounds() {
    if (!this.frame?.nAtoms) {
      this.center = [0, 0, 0]
      this.sceneRadius = 1
      this.updateFitScale()
      return
    }
    let minX = Number.POSITIVE_INFINITY
    let minY = Number.POSITIVE_INFINITY
    let minZ = Number.POSITIVE_INFINITY
    let maxX = Number.NEGATIVE_INFINITY
    let maxY = Number.NEGATIVE_INFINITY
    let maxZ = Number.NEGATIVE_INFINITY
    for (let index = 0; index < this.frame.nAtoms; index += 1) {
      const offset = index * 3
      const x = this.frame.positions[offset] ?? 0
      const y = this.frame.positions[offset + 1] ?? 0
      const z = this.frame.positions[offset + 2] ?? 0
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      minZ = Math.min(minZ, z)
      maxX = Math.max(maxX, x)
      maxY = Math.max(maxY, y)
      maxZ = Math.max(maxZ, z)
    }
    this.center = [(minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2]
    const dx = maxX - minX
    const dy = maxY - minY
    const dz = maxZ - minZ
    this.sceneRadius = Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz) / 2 + 0.8)
    this.updateFitScale()
  }

  private updateFitScale() {
    this.baseScale = Math.min(this.width, this.height) / Math.max(1, this.sceneRadius * 2.45)
  }

  private resetCamera() {
    this.transform = createInitialTransform()
    this.zoom = 1
    this.panX = 0
    this.panY = 0
    this.updateFitScale()
    this.invalidateProjectedAtoms()
  }

  private rotateOrbit(deltaYaw: number, deltaPitch: number) {
    const cosY = Math.cos(deltaYaw)
    const sinY = Math.sin(deltaYaw)
    const cosX = Math.cos(deltaPitch)
    const sinX = Math.sin(deltaPitch)
    const m = this.transform

    const row0x = m[0]
    const row0y = m[1]
    const row0z = m[2]
    const row1x = m[3]
    const row1y = m[4]
    const row1z = m[5]
    const row2x = m[6]
    const row2y = m[7]
    const row2z = m[8]

    const yawRow0x = cosY * row0x + sinY * row2x
    const yawRow0y = cosY * row0y + sinY * row2y
    const yawRow0z = cosY * row0z + sinY * row2z
    const yawRow2x = -sinY * row0x + cosY * row2x
    const yawRow2y = -sinY * row0y + cosY * row2y
    const yawRow2z = -sinY * row0z + cosY * row2z

    m[0] = yawRow0x
    m[1] = yawRow0y
    m[2] = yawRow0z
    m[3] = cosX * row1x - sinX * yawRow2x
    m[4] = cosX * row1y - sinX * yawRow2y
    m[5] = cosX * row1z - sinX * yawRow2z
    m[6] = sinX * row1x + cosX * yawRow2x
    m[7] = sinX * row1y + cosX * yawRow2y
    m[8] = sinX * row1z + cosX * yawRow2z

    this.normalizeTransform()
  }

  private rotateRoll(deltaRoll: number) {
    const cosR = Math.cos(deltaRoll)
    const sinR = Math.sin(deltaRoll)
    const m = this.transform

    const row0x = m[0]
    const row0y = m[1]
    const row0z = m[2]
    const row1x = m[3]
    const row1y = m[4]
    const row1z = m[5]

    m[0] = cosR * row0x - sinR * row1x
    m[1] = cosR * row0y - sinR * row1y
    m[2] = cosR * row0z - sinR * row1z
    m[3] = sinR * row0x + cosR * row1x
    m[4] = sinR * row0y + cosR * row1y
    m[5] = sinR * row0z + cosR * row1z

    this.normalizeTransform()
  }

  private normalizeTransform() {
    const m = this.transform
    const len0 = Math.hypot(m[0], m[1], m[2]) || 1
    m[0] /= len0
    m[1] /= len0
    m[2] /= len0

    const dot01 = m[0] * m[3] + m[1] * m[4] + m[2] * m[5]
    m[3] -= dot01 * m[0]
    m[4] -= dot01 * m[1]
    m[5] -= dot01 * m[2]
    const len1 = Math.hypot(m[3], m[4], m[5]) || 1
    m[3] /= len1
    m[4] /= len1
    m[5] /= len1

    m[6] = m[1] * m[5] - m[2] * m[4]
    m[7] = m[2] * m[3] - m[0] * m[5]
    m[8] = m[0] * m[4] - m[1] * m[3]
  }
}

function normalizeFrame(frame: StructureFrame): InternalFrame {
  const nAtoms = inferAtomCount(frame)
  const symbols = normalizeSymbols(frame.symbols, frame.numbers, nAtoms)
  const numbers = normalizeNumbers(frame.numbers, nAtoms)
  return {
    frameIndex: frame.frameIndex,
    nAtoms,
    symbols,
    numbers,
    positions: normalizePositions(frame.positions, nAtoms),
    cell: normalizeCell(frame.cell),
    pbc: frame.pbc,
    tags: normalizeTags(frame.tags, nAtoms),
    fixedMask: normalizeFixedMask(frame.fixedMask, frame.fixedIndices, nAtoms),
    energy: frame.energy,
    fmax: frame.fmax
  }
}

function cloneDisplayOptions(options: typeof DEFAULT_DISPLAY_OPTIONS) {
  return {
    supercell: { ...options.supercell },
    wrap: options.wrap
  }
}

function normalizeDisplayOptions(options?: StructureDisplayOptions) {
  return {
    supercell: {
      x: normalizeSupercellMultiplier(options?.supercell?.x ?? DEFAULT_DISPLAY_OPTIONS.supercell.x),
      y: normalizeSupercellMultiplier(options?.supercell?.y ?? DEFAULT_DISPLAY_OPTIONS.supercell.y),
      z: normalizeSupercellMultiplier(options?.supercell?.z ?? DEFAULT_DISPLAY_OPTIONS.supercell.z)
    },
    wrap: Boolean(options?.wrap ?? DEFAULT_DISPLAY_OPTIONS.wrap)
  }
}

function normalizeSupercellMultiplier(value: number | undefined) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 1
  return Math.min(10, Math.max(1, Math.round(numeric)))
}

function displayOptionsEqual(left: ReturnType<typeof normalizeDisplayOptions>, right: ReturnType<typeof normalizeDisplayOptions>) {
  return (
    left.wrap === right.wrap &&
    left.supercell.x === right.supercell.x &&
    left.supercell.y === right.supercell.y &&
    left.supercell.z === right.supercell.z
  )
}

function buildDisplayFrame(source: InternalFrame, options: ReturnType<typeof normalizeDisplayOptions>): InternalFrame {
  const cell = source.cell && hasCell(source.cell) ? source.cell : undefined
  const inverse = cell ? invertCell(cell) : null
  const wrap = options.wrap && Boolean(cell && inverse)
  const { x: copiesX, y: copiesY, z: copiesZ } = displaySupercellCopies(source, options)
  const copyCount = copiesX * copiesY * copiesZ
  const cellCopies = cell ? { x: copiesX, y: copiesY, z: copiesZ } : undefined
  if (!wrap && copyCount === 1) return cellCopies ? { ...source, cellCopies } : source

  const nAtoms = source.nAtoms
  const totalAtoms = nAtoms * copyCount
  const positions = new Float32Array(totalAtoms * 3)
  const tags = source.tags ? new Int32Array(totalAtoms) : undefined
  const fixedMask = source.fixedMask ? new Uint8Array(totalAtoms) : undefined
  const symbols = new Array<string>(totalAtoms)
  const numbers = new Array<number>(totalAtoms)
  const a = cellVector(cell, 0)
  const b = cellVector(cell, 1)
  const c = cellVector(cell, 2)
  let outputIndex = 0

  for (let ix = 0; ix < copiesX; ix += 1) {
    for (let iy = 0; iy < copiesY; iy += 1) {
      for (let iz = 0; iz < copiesZ; iz += 1) {
        const tx = ix * a[0] + iy * b[0] + iz * c[0]
        const ty = ix * a[1] + iy * b[1] + iz * c[1]
        const tz = ix * a[2] + iy * b[2] + iz * c[2]
        for (let atomIndex = 0; atomIndex < nAtoms; atomIndex += 1) {
          const sourceOffset = atomIndex * 3
          const outputOffset = outputIndex * 3
          let x = source.positions[sourceOffset] ?? 0
          let y = source.positions[sourceOffset + 1] ?? 0
          let z = source.positions[sourceOffset + 2] ?? 0
          if (wrap && cell && inverse) {
            const wrapped = wrapToCell(x, y, z, cell, inverse)
            x = wrapped[0]
            y = wrapped[1]
            z = wrapped[2]
          }
          positions[outputOffset] = x + tx
          positions[outputOffset + 1] = y + ty
          positions[outputOffset + 2] = z + tz
          symbols[outputIndex] = source.symbols[atomIndex] ?? 'X'
          numbers[outputIndex] = source.numbers[atomIndex] ?? 0
          if (tags) tags[outputIndex] = source.tags?.[atomIndex] ?? 0
          if (fixedMask) fixedMask[outputIndex] = source.fixedMask?.[atomIndex] ?? 0
          outputIndex += 1
        }
      }
    }
  }

  return {
    ...source,
    nAtoms: totalAtoms,
    symbols,
    numbers,
    positions,
    cell: cell ?? source.cell,
    cellCopies,
    tags,
    fixedMask
  }
}

function displaySupercellCopies(source: InternalFrame, options: ReturnType<typeof normalizeDisplayOptions>) {
  const cell = source.cell && hasCell(source.cell) ? source.cell : undefined
  const x = cell ? options.supercell.x : 1
  const y = cell ? options.supercell.y : 1
  const z = cell ? options.supercell.z : 1
  return { x, y, z }
}

function inferAtomCount(frame: StructureFrame) {
  if (frame.symbols.length) return frame.symbols.length
  if (frame.numbers.length) return frame.numbers.length
  return frame.positions instanceof Float32Array ? Math.floor(frame.positions.length / 3) : frame.positions.length
}

function normalizePositions(positions: Float32Array | number[][], nAtoms: number) {
  if (positions instanceof Float32Array) return positions.length === nAtoms * 3 ? positions : positions.subarray(0, nAtoms * 3)
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

function normalizeCell(cell?: Float32Array | number[][]) {
  if (!cell) return undefined
  if (cell instanceof Float32Array) return cell.length === 9 ? cell : cell.subarray(0, 9)
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

function cellVector(cell: Float32Array | undefined, row: number): Vec3 {
  if (!cell) return [0, 0, 0]
  const offset = row * 3
  return [cell[offset] ?? 0, cell[offset + 1] ?? 0, cell[offset + 2] ?? 0]
}

function invertCell(cell: Float32Array) {
  const a = cell[0] ?? 0
  const b = cell[1] ?? 0
  const c = cell[2] ?? 0
  const d = cell[3] ?? 0
  const e = cell[4] ?? 0
  const f = cell[5] ?? 0
  const g = cell[6] ?? 0
  const h = cell[7] ?? 0
  const i = cell[8] ?? 0
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
  if (Math.abs(det) < 1e-10) return null
  const invDet = 1 / det
  return new Float32Array([
    (e * i - f * h) * invDet,
    (c * h - b * i) * invDet,
    (b * f - c * e) * invDet,
    (f * g - d * i) * invDet,
    (a * i - c * g) * invDet,
    (c * d - a * f) * invDet,
    (d * h - e * g) * invDet,
    (b * g - a * h) * invDet,
    (a * e - b * d) * invDet
  ])
}

function wrapToCell(x: number, y: number, z: number, cell: Float32Array, inverse: Float32Array): Vec3 {
  const fx = wrapFraction(x * inverse[0] + y * inverse[3] + z * inverse[6])
  const fy = wrapFraction(x * inverse[1] + y * inverse[4] + z * inverse[7])
  const fz = wrapFraction(x * inverse[2] + y * inverse[5] + z * inverse[8])
  return [
    fx * cell[0] + fy * cell[3] + fz * cell[6],
    fx * cell[1] + fy * cell[4] + fz * cell[7],
    fx * cell[2] + fy * cell[5] + fz * cell[8]
  ]
}

function wrapFraction(value: number) {
  if (!Number.isFinite(value)) return 0
  const wrapped = value - Math.floor(value)
  return wrapped >= 1 ? 0 : wrapped
}

function normalizeTags(tags: Int32Array | number[] | undefined, nAtoms: number) {
  if (!tags) return undefined
  if (tags instanceof Int32Array) return tags.length === nAtoms ? tags : tags.subarray(0, nAtoms)
  return new Int32Array(tags.slice(0, nAtoms))
}

function normalizeFixedMask(mask: Uint8Array | undefined, indices: number[] | undefined, nAtoms: number) {
  if (mask) return mask.length === nAtoms ? mask : mask.subarray(0, nAtoms)
  if (!indices?.length) return undefined
  const output = new Uint8Array(nAtoms)
  for (const index of indices) {
    if (index >= 0 && index < nAtoms) output[index] = 1
  }
  return output
}

function normalizeSymbols(symbols: string[], numbers: number[], nAtoms: number) {
  const output: string[] = []
  for (let index = 0; index < nAtoms; index += 1) {
    output.push(symbols[index] ?? elementFromNumber(numbers[index]) ?? 'X')
  }
  return output
}

function normalizeNumbers(numbers: number[], nAtoms: number) {
  const output: number[] = []
  for (let index = 0; index < nAtoms; index += 1) {
    output.push(numbers[index] ?? 0)
  }
  return output
}

function firstAvailableFrame(store: TrajectoryStore) {
  if (!store.availableFrames) return frameFromTrajectoryStore(store, 0)
  for (let index = 0; index < store.nFrames; index += 1) {
    if (store.availableFrames[index] === 1) return frameFromTrajectoryStore(store, index)
  }
  return null
}

function createInitialTransform() {
  const yaw = -0.65
  const pitch = 0.5
  const cosY = Math.cos(yaw)
  const sinY = Math.sin(yaw)
  const cosX = Math.cos(pitch)
  const sinX = Math.sin(pitch)
  return new Float32Array([
    cosY,
    0,
    sinY,
    sinX * sinY,
    cosX,
    -sinX * cosY,
    -cosX * sinY,
    sinX,
    cosX * cosY
  ])
}

function estimateBonds(frame: InternalFrame, bondScale: number) {
  const bonds: Bond[] = []
  const nAtoms = frame.nAtoms
  if (nAtoms < 2 || nAtoms > 50000) return bonds

  const cellSize = 2.4
  const buckets = new Map<string, number[]>()
  const bucketKeys: Array<[number, number, number]> = []
  for (let index = 0; index < nAtoms; index += 1) {
    const offset = index * 3
    const ix = Math.floor((frame.positions[offset] ?? 0) / cellSize)
    const iy = Math.floor((frame.positions[offset + 1] ?? 0) / cellSize)
    const iz = Math.floor((frame.positions[offset + 2] ?? 0) / cellSize)
    bucketKeys[index] = [ix, iy, iz]
    const key = bucketKey(ix, iy, iz)
    const bucket = buckets.get(key)
    if (bucket) bucket.push(index)
    else buckets.set(key, [index])
  }

  const maxBonds = nAtoms * 8
  for (let index = 0; index < nAtoms && bonds.length < maxBonds; index += 1) {
    const [ix, iy, iz] = bucketKeys[index]
    for (let dx = -1; dx <= 1 && bonds.length < maxBonds; dx += 1) {
      for (let dy = -1; dy <= 1 && bonds.length < maxBonds; dy += 1) {
        for (let dz = -1; dz <= 1 && bonds.length < maxBonds; dz += 1) {
          const bucket = buckets.get(bucketKey(ix + dx, iy + dy, iz + dz))
          if (!bucket) continue
          for (const other of bucket) {
            if (other <= index) continue
            if (isBonded(frame, index, other, bondScale)) bonds.push({ a: index, b: other })
            if (bonds.length >= maxBonds) break
          }
        }
      }
    }
  }
  return bonds
}

function isBonded(frame: InternalFrame, left: number, right: number, bondScale: number) {
  const leftOffset = left * 3
  const rightOffset = right * 3
  const dx = (frame.positions[leftOffset] ?? 0) - (frame.positions[rightOffset] ?? 0)
  const dy = (frame.positions[leftOffset + 1] ?? 0) - (frame.positions[rightOffset + 1] ?? 0)
  const dz = (frame.positions[leftOffset + 2] ?? 0) - (frame.positions[rightOffset + 2] ?? 0)
  const distanceSquared = dx * dx + dy * dy + dz * dz
  if (distanceSquared < 0.16) return false
  const leftRadius = covalentRadius(frame.symbols[left])
  const rightRadius = covalentRadius(frame.symbols[right])
  const cutoff = Math.max(0.45, (leftRadius + rightRadius) * bondScale + 0.18)
  return distanceSquared <= cutoff * cutoff
}

function distance(left: Vec3, right: Vec3) {
  return Math.hypot(left[0] - right[0], left[1] - right[1], left[2] - right[2])
}

function angleDegrees(left: Vec3, center: Vec3, right: Vec3) {
  const a = subtractVec(left, center)
  const b = subtractVec(right, center)
  const denominator = vectorLength(a) * vectorLength(b)
  if (denominator === 0) return Number.NaN
  return Math.acos(clamp(dotVec(a, b) / denominator, -1, 1)) * 180 / Math.PI
}

function dihedralDegrees(first: Vec3, second: Vec3, third: Vec3, fourth: Vec3) {
  const b0 = subtractVec(second, first)
  const b1 = normalizeVec(subtractVec(third, second))
  const b2 = subtractVec(fourth, third)
  const v = subtractVec(b0, scaleVec(b1, dotVec(b0, b1)))
  const w = subtractVec(b2, scaleVec(b1, dotVec(b2, b1)))
  const x = dotVec(v, w)
  const y = dotVec(crossVec(b1, v), w)
  return Math.atan2(y, x) * 180 / Math.PI
}

function elementCountSummary(symbols: string[], total: number) {
  const counts = new Map<string, number>()
  for (const symbol of symbols) {
    counts.set(symbol, (counts.get(symbol) ?? 0) + 1)
  }
  const groups = [...counts.entries()]
    .sort(([left], [right]) => {
      const rank = elementRank(left) - elementRank(right)
      return rank === 0 ? left.localeCompare(right) : rank
    })
    .map(([symbol, count]) => `${count}${symbol}`)
  return `${groups.join('+')} ${total}Atoms`
}

function elementRank(symbol: string) {
  const index = NUMBER_SYMBOLS.indexOf(symbol)
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER
}

function formatMeasurement(value: number, digits: number) {
  if (!Number.isFinite(value)) return 'N/A'
  const normalized = Math.abs(value) < 1e-9 ? 0 : value
  return normalized.toFixed(digits).replace(/\.?0+$/, '')
}

function subtractVec(left: Vec3, right: Vec3): Vec3 {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]]
}

function scaleVec(vector: Vec3, scale: number): Vec3 {
  return [vector[0] * scale, vector[1] * scale, vector[2] * scale]
}

function dotVec(left: Vec3, right: Vec3) {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]
}

function crossVec(left: Vec3, right: Vec3): Vec3 {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0]
  ]
}

function vectorLength(vector: Vec3) {
  return Math.hypot(vector[0], vector[1], vector[2])
}

function normalizeVec(vector: Vec3): Vec3 {
  const length = vectorLength(vector) || 1
  return [vector[0] / length, vector[1] / length, vector[2] / length]
}

function atomScreenRadius(symbol: string, style: Required<ViewerStyle>, scale: number) {
  if (style.mode === 'line') return 2.2
  const modeScale = style.mode === 'sphere' ? 1.62 : 1
  return clamp(covalentRadius(symbol) * style.atomScale * modeScale * scale, 2.8, 34)
}

function labelProjectionStride(atomCount: number) {
  if (atomCount <= LABEL_SCAN_TARGET_ATOMS) return 1
  return Math.ceil(atomCount / LABEL_SCAN_TARGET_ATOMS)
}

function bucketKey(ix: number, iy: number, iz: number) {
  return `${ix},${iy},${iz}`
}

function hasCell(cell: Float32Array) {
  return cell.length >= 9 && cell.some(value => Math.abs(value) > 1e-6)
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function angleDeltaAround(center: { x: number; y: number }, previous: { x: number; y: number }, current: { x: number; y: number }) {
  const previousAngle = Math.atan2(previous.y - center.y, previous.x - center.x)
  const currentAngle = Math.atan2(current.y - center.y, current.x - center.x)
  let delta = currentAngle - previousAngle
  if (delta > Math.PI) delta -= Math.PI * 2
  if (delta < -Math.PI) delta += Math.PI * 2
  return delta
}

function intersectsAny(box: { x: number; y: number; width: number; height: number }, others: Array<{ x: number; y: number; width: number; height: number }>) {
  return others.some(other => !(box.x + box.width < other.x || other.x + other.width < box.x || box.y + box.height < other.y || other.y + other.height < box.y))
}

function labelColor(background: string) {
  return luminance(background) > 0.58 ? '#111827' : '#ffffff'
}

function luminance(hex: string) {
  const [r, g, b] = hexToRgb(hex)
  const srgb = [r, g, b].map(value => {
    const channel = value / 255
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * (srgb[0] ?? 0) + 0.7152 * (srgb[1] ?? 0) + 0.0722 * (srgb[2] ?? 0)
}

function darkenColor(hex: string, amount: number) {
  const [r, g, b] = hexToRgb(hex)
  return rgbToHex(mix(r, 0, amount), mix(g, 0, amount), mix(b, 0, amount))
}

function mix(left: number, right: number, amount: number) {
  return Math.round(left + (right - left) * amount)
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace('#', '')
  if (normalized.length !== 6) return [139, 153, 166]
  const value = Number.parseInt(normalized, 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

function rgbToHex(r: number, g: number, b: number) {
  return `#${[r, g, b].map(value => clamp(value, 0, 255).toString(16).padStart(2, '0')).join('')}`
}

function roundedRect(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  context.beginPath()
  context.moveTo(x + radius, y)
  context.lineTo(x + width - radius, y)
  context.quadraticCurveTo(x + width, y, x + width, y + radius)
  context.lineTo(x + width, y + height - radius)
  context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height)
  context.lineTo(x + radius, y + height)
  context.quadraticCurveTo(x, y + height, x, y + height - radius)
  context.lineTo(x, y + radius)
  context.quadraticCurveTo(x, y, x + radius, y)
  context.closePath()
}
