import {
  AmbientLight,
  BackSide,
  BufferGeometry,
  Color,
  CylinderGeometry,
  DirectionalLight,
  DynamicDrawUsage,
  Float32BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedMesh,
  LineBasicMaterial,
  LineSegments,
  Matrix4,
  MeshBasicMaterial,
  OrthographicCamera,
  Points,
  Quaternion,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
  type Material,
  type Mesh,
  type Object3D
} from 'three'
import { elementFromNumber, elementColor, covalentRadius } from './elements'
import type { ViewerStyle } from './types'

type Vec3 = [number, number, number]
type CellCopies = { x: number; y: number; z: number }

export interface RenderAtomFrame {
  nAtoms: number
  positions: Float32Array
  symbols: string[]
  numbers: number[]
  fixedMask?: Uint8Array
  cell?: Float32Array
  cellCopies?: CellCopies
}

export interface RenderBond {
  a: number
  b: number
}

export interface RenderView {
  width: number
  height: number
  dpr: number
  center: Vec3
  sceneRadius: number
  baseScale: number
  zoom: number
  panX: number
  panY: number
  transform: Float32Array
}

interface RendererStyle extends Required<ViewerStyle> {}

export class ThreeStructureRenderer {
  readonly canvas: HTMLCanvasElement

  private readonly renderer: WebGLRenderer
  private readonly scene = new Scene()
  private readonly camera = new OrthographicCamera(-1, 1, 1, -1, -100, 100)
  private readonly model = new Group()
  private readonly atomGeometries = [
    new SphereGeometry(1, 24, 16),
    new SphereGeometry(1, 16, 10),
    new SphereGeometry(1, 10, 6)
  ]
  private readonly selectionGeometry = new SphereGeometry(1, 12, 8)
  private readonly bondGeometry = new CylinderGeometry(1, 1, 1, 12, 1)
  private readonly bondMaterial = new MeshBasicMaterial({ color: 0x596575 })
  private readonly cellMaterial = new LineBasicMaterial({ color: 0x8b99a6, transparent: true, opacity: 0.78 })
  private readonly lightAtomOutlineMaterial = new MeshBasicMaterial({ color: 0x465568, side: BackSide })
  private readonly selectionBackMaterial = new MeshBasicMaterial({ color: 0x0b0f14, side: BackSide })
  private readonly selectionFrontMaterial = new MeshBasicMaterial({ color: 0xfaff00, side: BackSide })
  private atomMeshes: InstancedMesh[] = []
  private atomMaterials: Material[] = []
  private atomPointCloud: Points | null = null
  private readonly atomPointMaterial = createAtomPointMaterial()
  private selectionMeshes: InstancedMesh[] = []
  private selectionPointCloud: Points | null = null
  private readonly selectionPointMaterial = createSelectionPointMaterial()
  private bondMesh: InstancedMesh | null = null
  private cellLines: LineSegments | null = null
  private frame: RenderAtomFrame | null = null
  private bonds: RenderBond[] = []
  private style: RendererStyle
  private fixedMarkersVisible = true
  private selectedAtoms: readonly number[] = []
  private renderWidth = 0
  private renderHeight = 0
  private renderDpr = 0
  private center: Vec3 = [0, 0, 0]
  private readonly matrix = new Matrix4()
  private readonly position = new Vector3()
  private readonly scale = new Vector3()
  private readonly quaternion = new Quaternion()
  private readonly bondStart = new Vector3()
  private readonly bondEnd = new Vector3()
  private readonly bondMid = new Vector3()
  private readonly bondDirection = new Vector3()
  private readonly zAxis = new Vector3(0, 0, 1)

  constructor(container: HTMLElement, style: RendererStyle) {
    this.style = { ...style }
    this.bondGeometry.rotateX(Math.PI / 2)
    this.renderer = new WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance', // Enable GPU acceleration
      stencil: false, // Disable stencil buffer for better performance
      depth: true
    })
    this.renderer.setPixelRatio(1)
    this.renderer.outputColorSpace = SRGBColorSpace
    this.renderer.setClearColor(this.style.backgroundColor, 1)
    this.canvas = this.renderer.domElement
    this.canvas.style.position = 'absolute'
    this.canvas.style.inset = '0'
    this.canvas.style.width = '100%'
    this.canvas.style.height = '100%'
    this.canvas.style.cursor = 'crosshair'

    this.scene.add(new AmbientLight(0xffffff, 0.58))
    const key = new DirectionalLight(0xffffff, 0.88)
    key.position.set(-0.45, 0.68, 1.0)
    this.scene.add(key)
    const fill = new DirectionalLight(0xffffff, 0.28)
    fill.position.set(0.5, -0.4, -0.8)
    this.scene.add(fill)

    this.scene.add(this.model)
    container.append(this.canvas)
  }

  setStyle(style: RendererStyle) {
    const previous = this.style
    this.style = { ...this.style, ...style }
    this.renderer.setClearColor(this.style.backgroundColor, 1)
    if (previous.mode !== this.style.mode || previous.atomScale !== this.style.atomScale) {
      this.updateAtoms()
      this.updateSelection()
    }
    if (
      previous.mode !== this.style.mode ||
      previous.bondRadius !== this.style.bondRadius ||
      previous.showCell !== this.style.showCell
    ) {
      this.updateBonds()
      this.updateCell()
    }
  }

  setStructure(frame: RenderAtomFrame | null, bonds: RenderBond[], center: Vec3) {
    this.frame = frame
    this.bonds = bonds
    this.center = center
    this.updateAtoms()
    this.updateBonds()
    this.updateCell()
    this.updateSelection()
  }

  setFixedMarkersVisible(visible: boolean) {
    if (this.fixedMarkersVisible === visible) return
    this.fixedMarkersVisible = visible
    this.updateAtoms()
  }

  setView(view: RenderView) {
    this.center = view.center
    const width = Math.max(1, Math.round(view.width))
    const height = Math.max(1, Math.round(view.height))
    const dprChanged = this.renderDpr !== view.dpr
    if (dprChanged) {
      this.renderer.setPixelRatio(view.dpr)
      this.renderDpr = view.dpr
    }
    if (dprChanged || this.renderWidth !== width || this.renderHeight !== height) {
      this.renderer.setSize(width, height, false)
      this.renderWidth = width
      this.renderHeight = height
    }

    const scale = Math.max(1e-6, view.baseScale * view.zoom)
    this.atomPointMaterial.uniforms.pixelScale.value = scale * view.dpr
    this.selectionPointMaterial.uniforms.pixelScale.value = scale * view.dpr
    this.camera.left = -width / (2 * scale)
    this.camera.right = width / (2 * scale)
    this.camera.top = height / (2 * scale)
    this.camera.bottom = -height / (2 * scale)
    const distance = Math.max(10, view.sceneRadius * 5)
    this.camera.near = 0.1
    this.camera.far = distance * 2
    this.camera.position.set(-view.panX / scale, view.panY / scale, distance)
    this.camera.lookAt(this.camera.position.x, this.camera.position.y, 0)
    this.camera.updateProjectionMatrix()

    this.model.matrixAutoUpdate = false
    this.model.matrix.set(
      view.transform[0], view.transform[1], view.transform[2], 0,
      view.transform[3], view.transform[4], view.transform[5], 0,
      view.transform[6], view.transform[7], view.transform[8], 0,
      0, 0, 0, 1
    )
    this.model.matrixWorldNeedsUpdate = true
  }

  setSelectedAtoms(indices: readonly number[]) {
    this.selectedAtoms = indices
    this.updateSelection()
  }

  render() {
    this.renderer.render(this.scene, this.camera)
  }

  clear() {
    this.setStructure(null, [], [0, 0, 0])
    this.render()
  }

  dispose() {
    for (const mesh of this.atomMeshes) this.disposeObject(mesh)
    for (const mesh of this.selectionMeshes) this.disposeObject(mesh)
    this.disposeObject(this.selectionPointCloud)
    this.disposeObject(this.bondMesh)
    this.disposeObject(this.cellLines)
    for (const geometry of this.atomGeometries) geometry.dispose()
    this.selectionGeometry.dispose()
    this.bondGeometry.dispose()
    for (const material of this.atomMaterials) material.dispose()
    this.atomPointMaterial.dispose()
    this.selectionPointMaterial.dispose()
    this.bondMaterial.dispose()
    this.cellMaterial.dispose()
    this.lightAtomOutlineMaterial.dispose()
    this.selectionBackMaterial.dispose()
    this.selectionFrontMaterial.dispose()
    this.renderer.dispose()
  }

  private updateAtoms() {
    for (const mesh of this.atomMeshes) this.disposeObject(mesh)
    for (const material of this.atomMaterials) material.dispose()
    this.disposeObject(this.atomPointCloud)
    this.atomPointCloud = null
    this.atomMeshes = []
    this.atomMaterials = []
    const frame = this.frame
    if (!frame?.nAtoms) return

    if (this.shouldUseAtomPointCloud(frame.nAtoms)) {
      this.updateAtomPointCloud(frame)
      return
    }

    const atomGeometry = this.atomGeometryForFrame(frame.nAtoms)
    const groups = new Map<string, number[]>()
    for (let index = 0; index < frame.nAtoms; index += 1) {
      const symbol = frame.symbols[index] ?? elementFromNumber(frame.numbers[index]) ?? 'X'
      const group = groups.get(symbol)
      if (group) group.push(index)
      else groups.set(symbol, [index])
    }

    for (const [symbol, indices] of groups) {
      const color = elementColor(symbol)
      const geometry = atomGeometry.clone()
      const fixedFlags = new Float32Array(indices.length)
      geometry.setAttribute('fixedFlag', new InstancedBufferAttribute(fixedFlags, 1))
      const material = createAtomMaterial(color)
      const mesh = new InstancedMesh(geometry, material, indices.length)
      const outlineScale = lightAtomOutlineScale(symbol, color)
      const outlineMesh = outlineScale > 1 ? new InstancedMesh(atomGeometry, this.lightAtomOutlineMaterial, indices.length) : null
      mesh.instanceMatrix.setUsage(DynamicDrawUsage)
      mesh.frustumCulled = false
      if (outlineMesh) {
        outlineMesh.instanceMatrix.setUsage(DynamicDrawUsage)
        outlineMesh.frustumCulled = false
      }
      const radius = atomWorldRadius(symbol, this.style)
      for (let instanceIndex = 0; instanceIndex < indices.length; instanceIndex += 1) {
        const atomIndex = indices[instanceIndex]
        fixedFlags[instanceIndex] = this.fixedMarkersVisible && frame.fixedMask?.[atomIndex] === 1 ? 1 : 0
        const offset = atomIndex * 3
        this.position.set(
          (frame.positions[offset] ?? 0) - this.center[0],
          (frame.positions[offset + 1] ?? 0) - this.center[1],
          (frame.positions[offset + 2] ?? 0) - this.center[2]
        )
        this.scale.setScalar(radius)
        this.matrix.compose(this.position, this.quaternion.identity(), this.scale)
        mesh.setMatrixAt(instanceIndex, this.matrix)
        if (outlineMesh) {
          this.scale.setScalar(radius * outlineScale)
          this.matrix.compose(this.position, this.quaternion, this.scale)
          outlineMesh.setMatrixAt(instanceIndex, this.matrix)
        }
      }
      if (outlineMesh) {
        outlineMesh.instanceMatrix.needsUpdate = true
        this.atomMeshes.push(outlineMesh)
        this.model.add(outlineMesh)
      }
      mesh.instanceMatrix.needsUpdate = true
      const fixedAttribute = geometry.getAttribute('fixedFlag')
      if (fixedAttribute) fixedAttribute.needsUpdate = true
      this.atomMeshes.push(mesh)
      this.atomMaterials.push(material)
      this.model.add(mesh)
    }
  }

  private updateSelection() {
    for (const mesh of this.selectionMeshes) this.disposeObject(mesh)
    this.disposeObject(this.selectionPointCloud)
    this.selectionMeshes = []
    this.selectionPointCloud = null
    const frame = this.frame
    if (!frame?.nAtoms || !this.selectedAtoms.length) return

    const valid = this.selectedAtoms.filter(index => Number.isInteger(index) && index >= 0 && index < frame.nAtoms)
    if (!valid.length) return

    if (valid.length >= SELECTION_POINT_CLOUD_THRESHOLD) {
      this.updateSelectionPointCloud(frame, valid)
      return
    }

    const back = new InstancedMesh(this.selectionGeometry, this.selectionBackMaterial, valid.length)
    const front = new InstancedMesh(this.selectionGeometry, this.selectionFrontMaterial, valid.length)
    back.instanceMatrix.setUsage(DynamicDrawUsage)
    front.instanceMatrix.setUsage(DynamicDrawUsage)
    back.frustumCulled = false
    front.frustumCulled = false

    for (let instanceIndex = 0; instanceIndex < valid.length; instanceIndex += 1) {
      const atomIndex = valid[instanceIndex]
      const symbol = frame.symbols[atomIndex] ?? elementFromNumber(frame.numbers[atomIndex]) ?? 'X'
      this.setAtomPosition(frame, atomIndex, this.position)
      const radius = atomWorldRadius(symbol, this.style)
      const frontRadius = selectionFrontRadius(radius)
      const backRadius = selectionBackRadius(radius, frontRadius)
      this.scale.setScalar(backRadius)
      this.matrix.compose(this.position, this.quaternion.identity(), this.scale)
      back.setMatrixAt(instanceIndex, this.matrix)
      this.scale.setScalar(frontRadius)
      this.matrix.compose(this.position, this.quaternion, this.scale)
      front.setMatrixAt(instanceIndex, this.matrix)
    }

    back.instanceMatrix.needsUpdate = true
    front.instanceMatrix.needsUpdate = true
    this.selectionMeshes.push(back, front)
    this.model.add(back, front)
  }

  private updateSelectionPointCloud(frame: RenderAtomFrame, indices: number[]) {
    const positions = new Float32Array(indices.length * 3)
    const backRadii = new Float32Array(indices.length)
    const atomRatios = new Float32Array(indices.length)
    const frontRatios = new Float32Array(indices.length)

    for (let instanceIndex = 0; instanceIndex < indices.length; instanceIndex += 1) {
      const atomIndex = indices[instanceIndex]
      const offset = atomIndex * 3
      const targetOffset = instanceIndex * 3
      positions[targetOffset] = (frame.positions[offset] ?? 0) - this.center[0]
      positions[targetOffset + 1] = (frame.positions[offset + 1] ?? 0) - this.center[1]
      positions[targetOffset + 2] = (frame.positions[offset + 2] ?? 0) - this.center[2]

      const symbol = frame.symbols[atomIndex] ?? elementFromNumber(frame.numbers[atomIndex]) ?? 'X'
      const atomRadius = atomWorldRadius(symbol, this.style)
      const frontRadius = selectionFrontRadius(atomRadius)
      const backRadius = selectionBackRadius(atomRadius, frontRadius)
      backRadii[instanceIndex] = backRadius
      atomRatios[instanceIndex] = clampRatio(atomRadius / backRadius)
      frontRatios[instanceIndex] = clampRatio(frontRadius / backRadius)
    }

    const geometry = new BufferGeometry()
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
    geometry.setAttribute('selectionRadius', new Float32BufferAttribute(backRadii, 1))
    geometry.setAttribute('atomRatio', new Float32BufferAttribute(atomRatios, 1))
    geometry.setAttribute('frontRatio', new Float32BufferAttribute(frontRatios, 1))
    geometry.computeBoundingSphere()

    const points = new Points(geometry, this.selectionPointMaterial)
    points.frustumCulled = false
    points.renderOrder = 10
    this.selectionPointCloud = points
    this.model.add(points)
  }

  private updateBonds() {
    this.disposeObject(this.bondMesh)
    this.bondMesh = null
    const frame = this.frame
    if (!frame?.nAtoms || this.atomPointCloud || this.style.mode === 'sphere' || !this.bonds.length) return

    const mesh = new InstancedMesh(this.bondGeometry, this.bondMaterial, this.bonds.length)
    mesh.instanceMatrix.setUsage(DynamicDrawUsage)
    mesh.frustumCulled = false
    const radius = this.style.mode === 'line' ? 0.018 : this.style.bondRadius

    for (let index = 0; index < this.bonds.length; index += 1) {
      const bond = this.bonds[index]
      this.setAtomPosition(frame, bond.a, this.bondStart)
      this.setAtomPosition(frame, bond.b, this.bondEnd)
      this.bondDirection.subVectors(this.bondEnd, this.bondStart)
      const length = this.bondDirection.length()
      if (length <= 1e-6) {
        this.matrix.identity()
        mesh.setMatrixAt(index, this.matrix)
        continue
      }
      this.bondMid.addVectors(this.bondStart, this.bondEnd).multiplyScalar(0.5)
      this.quaternion.setFromUnitVectors(this.zAxis, this.bondDirection.normalize())
      this.scale.set(radius, radius, length)
      this.matrix.compose(this.bondMid, this.quaternion, this.scale)
      mesh.setMatrixAt(index, this.matrix)
    }

    mesh.instanceMatrix.needsUpdate = true
    this.bondMesh = mesh
    this.model.add(mesh)
  }

  private updateCell() {
    this.disposeObject(this.cellLines)
    this.cellLines = null
    if (!this.style.showCell || !this.frame?.cell || !hasCell(this.frame.cell)) return

    const vertices = cellLineVertices(this.frame.cell, this.frame.cellCopies, this.center)
    const geometry = new BufferGeometry()
    geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3))
    const lines = new LineSegments(geometry, this.cellMaterial)
    lines.frustumCulled = false
    this.cellLines = lines
    this.model.add(lines)
  }

  private setAtomPosition(frame: RenderAtomFrame, index: number, target: Vector3) {
    const offset = index * 3
    target.set(
      (frame.positions[offset] ?? 0) - this.center[0],
      (frame.positions[offset + 1] ?? 0) - this.center[1],
      (frame.positions[offset + 2] ?? 0) - this.center[2]
    )
  }

  private disposeObject(object: Object3D | null) {
    if (!object) return
    object.removeFromParent()
    const geometry = (object as Mesh | LineSegments).geometry
    if (geometry && !this.isSharedGeometry(geometry)) geometry.dispose()
  }

  private atomGeometryForFrame(atomCount: number) {
    if (atomCount >= 30000) return this.atomGeometries[2]
    if (atomCount >= 8000) return this.atomGeometries[1]
    return this.atomGeometries[0]
  }

  private shouldUseAtomPointCloud(atomCount: number) {
    return atomCount >= POINT_CLOUD_ATOM_THRESHOLD
  }

  private updateAtomPointCloud(frame: RenderAtomFrame) {
    const positions = new Float32Array(frame.nAtoms * 3)
    const colors = new Float32Array(frame.nAtoms * 3)
    const radii = new Float32Array(frame.nAtoms)
    const fixedFlags = new Float32Array(frame.nAtoms)
    const color = new Color()

    for (let index = 0; index < frame.nAtoms; index += 1) {
      const sourceOffset = index * 3
      positions[sourceOffset] = (frame.positions[sourceOffset] ?? 0) - this.center[0]
      positions[sourceOffset + 1] = (frame.positions[sourceOffset + 1] ?? 0) - this.center[1]
      positions[sourceOffset + 2] = (frame.positions[sourceOffset + 2] ?? 0) - this.center[2]

      const symbol = frame.symbols[index] ?? elementFromNumber(frame.numbers[index]) ?? 'X'
      color.set(elementColor(symbol))
      colors[sourceOffset] = color.r
      colors[sourceOffset + 1] = color.g
      colors[sourceOffset + 2] = color.b
      radii[index] = atomWorldRadius(symbol, this.style)
      fixedFlags[index] = this.fixedMarkersVisible && frame.fixedMask?.[index] === 1 ? 1 : 0
    }

    const geometry = new BufferGeometry()
    geometry.setAttribute('position', new Float32BufferAttribute(positions, 3))
    geometry.setAttribute('atomColor', new Float32BufferAttribute(colors, 3))
    geometry.setAttribute('atomRadius', new Float32BufferAttribute(radii, 1))
    geometry.setAttribute('fixedFlag', new Float32BufferAttribute(fixedFlags, 1))
    geometry.computeBoundingSphere()

    const points = new Points(geometry, this.atomPointMaterial)
    points.frustumCulled = false
    this.atomPointCloud = points
    this.model.add(points)
  }

  private isSharedGeometry(geometry: BufferGeometry) {
    return geometry === this.bondGeometry || geometry === this.selectionGeometry || this.atomGeometries.includes(geometry as SphereGeometry)
  }
}

function atomWorldRadius(symbol: string, style: RendererStyle) {
  if (style.mode === 'line') return Math.max(0.045, covalentRadius(symbol) * style.atomScale * 0.62)
  const modeScale = style.mode === 'sphere' ? 1.62 : 1
  return Math.max(0.055, covalentRadius(symbol) * style.atomScale * modeScale)
}

// Material cache for better performance - avoid creating duplicate materials
const materialCache = new Map<string, ShaderMaterial>()

function createAtomMaterial(color: string) {
  // Check cache first
  if (materialCache.has(color)) {
    return materialCache.get(color)!.clone()
  }

  const material = new ShaderMaterial({
    uniforms: {
      baseColor: { value: new Color(color) }
    },
    vertexShader: `
      varying vec3 vViewNormal;
      varying float vFixed;
      attribute float fixedFlag;

      void main() {
        vViewNormal = normalize(normalMatrix * normal);
        vFixed = fixedFlag;
        gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 baseColor;
      varying vec3 vViewNormal;
      varying float vFixed;

      vec3 lighten(vec3 color, float amount) {
        return mix(color, vec3(1.0), amount);
      }

      vec3 darken(vec3 color, float amount) {
        return mix(color, vec3(0.0), amount);
      }

      void main() {
        vec3 normal = normalize(vViewNormal);
        float facing = clamp(normal.z, 0.0, 1.0);
        float luminance = dot(baseColor, vec3(0.2126, 0.7152, 0.0722));
        float brightAtom = smoothstep(0.66, 0.94, luminance);
        float highlight = pow(max(dot(normal, normalize(vec3(-0.42, 0.50, 0.76))), 0.0), 2.15);
        float rim = pow(1.0 - facing, 1.38);
        vec3 brightBase = mix(baseColor, vec3(1.0), mix(0.16, 0.06, brightAtom));
        vec3 color = mix(brightBase, darken(brightBase, mix(0.12, 0.34, brightAtom)), rim * 0.68);
        color = mix(color, lighten(brightBase, 0.58), highlight * 0.94);
        float edge = smoothstep(0.66, 1.0, rim);
        float outline = smoothstep(0.42, 0.92, rim);
        float hardOutline = smoothstep(0.72, 0.985, rim);
        vec3 edgeColor = mix(darken(brightBase, 0.28), vec3(0.32, 0.39, 0.48), brightAtom);
        color = mix(color, edgeColor, outline * mix(0.56, 1.0, brightAtom));
        color = mix(color, vec3(0.23, 0.29, 0.36), hardOutline * brightAtom * 0.86);

        if (vFixed > 0.5) {
          vec2 p = normal.xy;
          float lineWidth = 0.098;
          float softness = 0.026;
          float diagonalA = 1.0 - smoothstep(lineWidth, lineWidth + softness, abs(p.x - p.y));
          float diagonalB = 1.0 - smoothstep(lineWidth, lineWidth + softness, abs(p.x + p.y));
          float mask = max(diagonalA, diagonalB) * smoothstep(0.18, 0.42, facing) * (1.0 - smoothstep(0.84, 0.98, length(p)));
          vec3 mark = vec3(0.13, 0.17, 0.22);
          color = mix(color, mark, mask * 0.82);
        }
        gl_FragColor = vec4(color, 1.0);
      }
    `
  })

  // Cache the material
  materialCache.set(color, material)
  return material.clone()
}

function createAtomPointMaterial() {
  return new ShaderMaterial({
    uniforms: {
      pixelScale: { value: 1 }
    },
    depthTest: true,
    depthWrite: true,
    vertexShader: `
      uniform float pixelScale;
      attribute vec3 atomColor;
      attribute float atomRadius;
      attribute float fixedFlag;
      varying vec3 vColor;
      varying float vFixed;

      void main() {
        vColor = atomColor;
        vFixed = fixedFlag;
        gl_PointSize = max(2.0, atomRadius * pixelScale * 2.0);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      varying float vFixed;

      vec3 lighten(vec3 color, float amount) {
        return mix(color, vec3(1.0), amount);
      }

      vec3 darken(vec3 color, float amount) {
        return mix(color, vec3(0.0), amount);
      }

      void main() {
        vec2 p = gl_PointCoord * 2.0 - 1.0;
        float radiusSquared = dot(p, p);
        if (radiusSquared > 1.0) discard;

        float z = sqrt(max(0.0, 1.0 - radiusSquared));
        vec3 normal = normalize(vec3(p, z));
        float luminance = dot(vColor, vec3(0.2126, 0.7152, 0.0722));
        float brightAtom = smoothstep(0.66, 0.94, luminance);
        float highlight = pow(max(dot(normal, normalize(vec3(-0.42, 0.50, 0.76))), 0.0), 2.1);
        float rim = pow(1.0 - max(normal.z, 0.0), 1.35);
        vec3 brightBase = mix(vColor, vec3(1.0), mix(0.16, 0.06, brightAtom));
        vec3 color = mix(brightBase, darken(brightBase, mix(0.12, 0.34, brightAtom)), rim * 0.7);
        color = mix(color, lighten(brightBase, 0.58), highlight * 0.94);
        color = mix(color, mix(darken(brightBase, 0.3), vec3(0.32, 0.39, 0.48), brightAtom), smoothstep(0.42, 0.94, rim) * 0.78);
        color = mix(color, vec3(0.23, 0.29, 0.36), smoothstep(0.74, 0.99, rim) * brightAtom * 0.82);

        if (vFixed > 0.5) {
          float lineWidth = 0.105;
          float softness = 0.035;
          float diagonalA = 1.0 - smoothstep(lineWidth, lineWidth + softness, abs(p.x - p.y));
          float diagonalB = 1.0 - smoothstep(lineWidth, lineWidth + softness, abs(p.x + p.y));
          float mask = max(diagonalA, diagonalB) * smoothstep(0.18, 0.42, normal.z) * (1.0 - smoothstep(0.82, 0.98, length(p)));
          color = mix(color, vec3(0.13, 0.17, 0.22), mask * 0.82);
        }

        gl_FragColor = vec4(color, 1.0);
      }
    `
  })
}

function createSelectionPointMaterial() {
  return new ShaderMaterial({
    uniforms: {
      pixelScale: { value: 1 }
    },
    transparent: true,
    depthTest: true,
    depthWrite: false,
    vertexShader: `
      uniform float pixelScale;
      attribute float selectionRadius;
      attribute float atomRatio;
      attribute float frontRatio;
      varying float vAtomRatio;
      varying float vFrontRatio;

      void main() {
        vAtomRatio = atomRatio;
        vFrontRatio = frontRatio;
        gl_PointSize = max(4.0, selectionRadius * pixelScale * 2.0);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying float vAtomRatio;
      varying float vFrontRatio;

      void main() {
        vec2 p = gl_PointCoord * 2.0 - 1.0;
        float distanceFromCenter = length(p);
        if (distanceFromCenter > 1.0) discard;

        float inner = smoothstep(vAtomRatio - 0.018, vAtomRatio + 0.018, distanceFromCenter);
        float outer = 1.0 - smoothstep(0.97, 1.0, distanceFromCenter);
        float alpha = inner * outer;
        if (alpha <= 0.01) discard;

        float border = smoothstep(vFrontRatio - 0.012, vFrontRatio + 0.012, distanceFromCenter);
        vec3 color = mix(vec3(0.980, 1.000, 0.000), vec3(0.035, 0.047, 0.062), border);
        gl_FragColor = vec4(color, alpha);
      }
    `
  })
}

function selectionFrontRadius(atomRadius: number) {
  return Math.max(atomRadius * 1.34, atomRadius + MIN_SELECTION_HALO_THICKNESS)
}

function selectionBackRadius(atomRadius: number, frontRadius: number) {
  return Math.max(atomRadius * 1.42, frontRadius + MIN_SELECTION_BORDER_THICKNESS)
}

function clampRatio(value: number) {
  return Math.min(0.96, Math.max(0.1, value))
}

function cellLineVertices(cell: Float32Array, copies: CellCopies | undefined, center: Vec3) {
  const a: Vec3 = [cell[0] ?? 0, cell[1] ?? 0, cell[2] ?? 0]
  const b: Vec3 = [cell[3] ?? 0, cell[4] ?? 0, cell[5] ?? 0]
  const c: Vec3 = [cell[6] ?? 0, cell[7] ?? 0, cell[8] ?? 0]
  const nx = normalizeCellCopyCount(copies?.x)
  const ny = normalizeCellCopyCount(copies?.y)
  const nz = normalizeCellCopyCount(copies?.z)
  const edges: Array<[Vec3, Vec3]> = []
  for (let iy = 0; iy <= ny; iy += 1) {
    for (let iz = 0; iz <= nz; iz += 1) edges.push([latticePoint(a, b, c, 0, iy, iz), latticePoint(a, b, c, nx, iy, iz)])
  }
  for (let ix = 0; ix <= nx; ix += 1) {
    for (let iz = 0; iz <= nz; iz += 1) edges.push([latticePoint(a, b, c, ix, 0, iz), latticePoint(a, b, c, ix, ny, iz)])
  }
  for (let ix = 0; ix <= nx; ix += 1) {
    for (let iy = 0; iy <= ny; iy += 1) edges.push([latticePoint(a, b, c, ix, iy, 0), latticePoint(a, b, c, ix, iy, nz)])
  }
  const vertices: number[] = []
  for (const [start, end] of edges) addDashedEdgeVertices(vertices, start, end, center)
  return vertices
}

function normalizeCellCopyCount(value: number | undefined) {
  if (!Number.isFinite(value)) return 1
  return Math.max(1, Math.round(value ?? 1))
}

function addDashedEdgeVertices(vertices: number[], start: Vec3, end: Vec3, center: Vec3) {
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const dz = end[2] - start[2]
  const length = Math.hypot(dx, dy, dz)
  if (length <= 1e-6) return
  const dash = Math.min(CELL_DASH_LENGTH, length)
  const gap = CELL_DASH_GAP
  const step = dash + gap
  const count = Math.max(1, Math.floor((length + gap) / step))
  const drawnLength = count * dash + (count - 1) * gap
  const inset = Math.max(0, (length - drawnLength) / 2)
  for (let index = 0; index < count; index += 1) {
    const offset = inset + index * step
    const startRatio = offset / length
    const endRatio = Math.min(offset + dash, length) / length
    vertices.push(
      start[0] + dx * startRatio - center[0],
      start[1] + dy * startRatio - center[1],
      start[2] + dz * startRatio - center[2],
      start[0] + dx * endRatio - center[0],
      start[1] + dy * endRatio - center[1],
      start[2] + dz * endRatio - center[2]
    )
  }
}

function latticePoint(a: Vec3, b: Vec3, c: Vec3, x: number, y: number, z: number): Vec3 {
  return [
    x * a[0] + y * b[0] + z * c[0],
    x * a[1] + y * b[1] + z * c[1],
    x * a[2] + y * b[2] + z * c[2]
  ]
}

function lightAtomOutlineScale(symbol: string, color: string) {
  const luminance = colorLuminance(color)
  if (symbol === 'H') return 1.13
  if (luminance > 0.82) return 1.1
  if (luminance > 0.66) return 1.075
  return 1
}

function colorLuminance(hex: string) {
  const normalized = hex.replace('#', '')
  if (normalized.length !== 6) return 0
  const value = Number.parseInt(normalized, 16)
  const r = ((value >> 16) & 255) / 255
  const g = ((value >> 8) & 255) / 255
  const b = (value & 255) / 255
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function hasCell(cell: Float32Array) {
  return cell.length >= 9 && cell.some(value => Math.abs(value) > 1e-6)
}

const POINT_CLOUD_ATOM_THRESHOLD = 50001
const SELECTION_POINT_CLOUD_THRESHOLD = 5000
const CELL_DASH_LENGTH = 0.18
const CELL_DASH_GAP = 0.12
const MIN_SELECTION_HALO_THICKNESS = 0.12
const MIN_SELECTION_BORDER_THICKNESS = 0.025
