import type { AseFrame } from '../types/structure'

/**
 * Export structure frame to XYZ format (Extended XYZ with Lattice info)
 */
export function exportToXYZ(frame: AseFrame, filename: string): string {
  const symbols = frame.symbols || []
  const positions = frame.positions || []
  const nAtoms = positions.length

  if (nAtoms === 0) {
    throw new Error('No atoms to export')
  }

  const lines: string[] = []

  // XYZ format: first line is number of atoms
  lines.push(String(nAtoms))

  // Second line is comment (extended XYZ format can include Lattice)
  let comment = ''

  // Add lattice vectors if periodic (extended XYZ format)
  if (frame.cell && frame.pbc && frame.pbc.some(p => p)) {
    const cell = frame.cell
    // Flatten cell vectors: Lattice="v1x v1y v1z v2x v2y v2z v3x v3y v3z"
    const lattice = [
      ...cell[0].map(v => v.toFixed(8)),
      ...cell[1].map(v => v.toFixed(8)),
      ...cell[2].map(v => v.toFixed(8))
    ].join(' ')
    comment += `Lattice="${lattice}"`
  }

  // Add properties
  const fixedSet = new Set((frame.fixed_indices || []).filter(index => index >= 0 && index < nAtoms))
  const propertyColumns = ['species:S:1', 'pos:R:3']
  if (fixedSet.size > 0) propertyColumns.push('move_mask:L:1')

  const props: string[] = []
  if (frame.energy !== null && frame.energy !== undefined) {
    props.push(`energy=${frame.energy}`)
  }
  if (props.length > 0 || fixedSet.size > 0) {
    if (comment) comment += ' '
    comment += `Properties=${propertyColumns.join(':')} ${props.join(' ')}`.trimEnd()
  }

  // If no special properties, just use filename as comment
  if (!comment) {
    comment = filename || 'Structure'
  }

  lines.push(comment)

  // Atom lines: symbol x y z
  for (let i = 0; i < nAtoms; i++) {
    const symbol = symbols[i] || 'X'
    const pos = positions[i]
    const moveMask = fixedSet.size > 0 ? `  ${fixedSet.has(i) ? 'F' : 'T'}` : ''
    lines.push(`${symbol}  ${pos[0].toFixed(8)}  ${pos[1].toFixed(8)}  ${pos[2].toFixed(8)}${moveMask}`)
  }

  return lines.join('\n')
}

/**
 * Export multiple frames to XYZ format (trajectory)
 */
export function exportTrajectoryToXYZ(frames: AseFrame[], filename: string): string {
  return frames.map(frame => exportToXYZ(frame, filename)).join('\n')
}

/**
 * Export multiple frames to Arc format (trajectory)
 * Matches ASE write_dmol_arc format exactly
 */
export function exportTrajectoryToArc(frames: AseFrame[], _filename: string): string {
  if (frames.length === 0) return ''

  const lines: string[] = []

  // Arc header (only once)
  lines.push('!BIOSYM archive 3')
  lines.push('PBC=ON')

  // Export each frame
  for (const frame of frames) {
    const symbols = frame.symbols || []
    const positions = frame.positions || []
    const nAtoms = positions.length

    // Energy line (74 spaces + energy value right-aligned in 8 chars)
    const energy = frame.energy !== null && frame.energy !== undefined ? frame.energy : 0.0
    const energyStr = energy.toFixed(4)
    lines.push(`${' '.repeat(74)}${energyStr.padStart(8)}`)

    // Date line
    lines.push('!DATE     Jan 01 00:00:00 2000')

    // Cell parameters - ASE uses %9.5f format (9 chars wide, 5 decimals)
    let cellLine = 'PBC    0.0000    0.0000    0.0000   90.0000   90.0000   90.0000'
    if (frame.cell && frame.pbc && frame.pbc.some(p => p)) {
      const cell = frame.cell
      const a = Math.sqrt(cell[0][0]**2 + cell[0][1]**2 + cell[0][2]**2)
      const b = Math.sqrt(cell[1][0]**2 + cell[1][1]**2 + cell[1][2]**2)
      const c = Math.sqrt(cell[2][0]**2 + cell[2][1]**2 + cell[2][2]**2)

      const cosAlpha = (cell[1][0]*cell[2][0] + cell[1][1]*cell[2][1] + cell[1][2]*cell[2][2]) / (b * c)
      const cosBeta = (cell[0][0]*cell[2][0] + cell[0][1]*cell[2][1] + cell[0][2]*cell[2][2]) / (a * c)
      const cosGamma = (cell[0][0]*cell[1][0] + cell[0][1]*cell[1][1] + cell[0][2]*cell[1][2]) / (a * b)

      const alpha = Math.acos(Math.max(-1, Math.min(1, cosAlpha))) * 180 / Math.PI
      const beta = Math.acos(Math.max(-1, Math.min(1, cosBeta))) * 180 / Math.PI
      const gamma = Math.acos(Math.max(-1, Math.min(1, cosGamma))) * 180 / Math.PI

      // Format: 'PBC ' + 6 values each padded to 9 chars with 5 decimals
      cellLine = `PBC ${a.toFixed(5).padStart(9)} ${b.toFixed(5).padStart(9)} ${c.toFixed(5).padStart(9)} ${alpha.toFixed(5).padStart(9)} ${beta.toFixed(5).padStart(9)} ${gamma.toFixed(5).padStart(9)}`
    }
    lines.push(cellLine)

    // Atom lines matching ASE format: %-6s  %12.8f   %12.8f   %12.8f XXXX 1      xx      %-2s  0.000
    // Symbol+index left-aligned in 6 chars, 2 spaces, then coords in 12.8f (12 chars, 8 decimals), 3 spaces between coords
    for (let i = 0; i < nAtoms; i++) {
      const symbol = symbols[i] || 'X'
      const pos = positions[i]

      // Symbol field: symbol + 1-based index, left-aligned in 6 characters, then 2 spaces
      const symbolWithIndex = `${symbol}${i + 1}`.padEnd(6)

      // Coordinates: 12 characters wide with 8 decimal places, 3 spaces between
      const x = pos[0].toFixed(8).padStart(12)
      const y = pos[1].toFixed(8).padStart(12)
      const z = pos[2].toFixed(8).padStart(12)

      // Full line format
      lines.push(`${symbolWithIndex}  ${x}   ${y}   ${z} XXXX 1      xx      ${symbol.padEnd(2)}  0.000`)
    }

    lines.push('end')
    lines.push('end')
  }

  return lines.join('\n')
}

/**
 * Export one frame to the Materials Studio XSD shape used by the legacy 2xsd script.
 */
export function exportToXSD(frame: AseFrame, _filename: string): string {
  const symbols = frame.symbols || []
  const positions = frame.positions || []
  const nAtoms = positions.length

  if (nAtoms === 0) {
    throw new Error('No atoms to export')
  }

  const cell = usableCell(frame.cell) ?? identityCell()
  const fractionalPositions = positions.map(position => cartesianToFractional(position, cell))
  const fixedSet = new Set((frame.fixed_indices || []).filter(index => index >= 0 && index < nAtoms))
  const fixedAtomIds = [...fixedSet].sort((left, right) => left - right).map(index => index + 4)
  const fixedProperties = fixedAtomIds.map(() => 'FractionalXYZ').join(',')
  const energy = finiteNumber(frame.energy) ?? 0
  const fmax = finiteNumber(frame.fmax) ?? 0

  const id1 = nAtoms + 1
  const id4 = nAtoms + 4
  const id5 = nAtoms + 5
  const id6 = nAtoms + 6
  const id7 = nAtoms + 7
  const id8 = nAtoms + 8
  const id9 = nAtoms + 9
  const id10 = nAtoms + 10
  const ci0 = `ci(${nAtoms}):${nAtoms}+4`
  const ci1 = `ci(${nAtoms + 1}):${nAtoms}+4,${nAtoms + 5}`
  const ci2 = `ci(${nAtoms + 2}):${nAtoms + 2}+4`

  const lines: string[] = [
    '<?xml version="1.0" encoding="latin1"?>',
    '<!DOCTYPE XSD []>',
    '<XSD Version="5.0" WrittenBy="Materials Studio 5.0">',
    '\t<AtomisticTreeRoot ID="1" NumProperties="58" NumChildren="1">',
    ...xsdPropertyLines(),
    `\t\t<SymmetrySystem ID="2" Mapping="3" Children="${ci2}" Normalized="1" Name="E:${formatXsdMetric(energy, 3)} F:${formatXsdMetric(fmax, 3)} M:0.00" XYZ="0,0,0" OverspecificationTolerance="0.05" PeriodicDisplayType="Original" HasSymmetryHistory="1">`,
    `\t\t\t<MappingSet ID="${id6}" SymmetryDefinition="${id4}" ActiveSystem="2" NumFamilies="1" OwnsTotalConstraintMapping="1" TotalConstraintMapping="3">`,
    `\t\t\t\t<MappingFamily ID="${id7}" NumImageMappings="0">`,
    `\t\t\t\t\t<IdentityMapping ID="${id8}" Element="1,0,0,0,0,1,0,0,0,0,1,0" Constraint="1,0,0,0,0,1,0,0,0,0,1,0" MappedObjects="${ci1}" DefectObjects="${id4},${id9}" NumImages="${id1}" NumDefects="2">`
  ]

  for (let index = 0; index < nAtoms; index += 1) {
    const symbol = normalizeSymbol(symbols[index])
    const id = index + 4
    const name = xmlAttribute(`${symbol}${index + 1}`)
    const xyz = fractionalPositions[index].map(value => value.toFixed(12)).join(',')
    const restriction = fixedSet.has(index) ? ` RestrictedBy="${id5}" RestrictedProperties="FractionalXYZ"` : ''
    lines.push(`\t\t\t\t\t\t<Atom3d ID="${id}" Mapping="${id8}" Parent="2"${restriction} Name="${name}" XYZ="${xyz}" Charge="0" Components="${xmlAttribute(symbol)}" FormalSpin="0"/>`)
  }

  if (fixedAtomIds.length > 0) {
    lines.push(`\t\t\t\t\t\t<CompleteRestriction ID="${id5}" Mapping="${id8}" Parent="2" RestrictsObjects="ci(${fixedAtomIds.length}):${fixedAtomIds.join(',')}" RestrictsProperties="${fixedProperties}"/>`)
  } else {
    lines.push(`\t\t\t\t\t\t<CompleteRestriction ID="${id5}" Mapping="${id8}" Parent="2" Visible="0"/>`)
  }

  lines.push(
    `\t\t\t\t\t\t<SpaceGroup ID="${id4}" Parent="2" Children="${id9}" Name="P1" AVector="${formatCellVector(cell[0])}" BVector="${formatCellVector(cell[1])}" CVector="${formatCellVector(cell[2])}" Color="255,255,255,255" OrientationBase="C along Z, A in XZ plane" Centering="3D Primitive-Centered" Lattice="3D Triclinic" GroupName="P1" Operators="1,0,0,0,0,1,0,0,0,0,1,0" DisplayRange="0,1,0,1,0,1" CylinderRadius="0.2" LabelAxes="1" ActiveSystem="2" ITNumber="1" LongName="P 1" Qualifier="Origin-1" SchoenfliesName="C1-1" System="Triclinic" Class="1" DisplayStyle="Solid" LineThickness="2" />`,
    `\t\t\t\t\t\t<ReciprocalLattice3D ID="${id9}" Parent="${id4}" Visible="0"/>`,
    '\t\t\t\t\t</IdentityMapping>',
    '\t\t\t\t\t<MappingRepairs NumRepairs="0"/>',
    '\t\t\t\t</MappingFamily>',
    '\t\t\t\t<InfiniteMapping ID="3" Element="1,0,0,0,0,1,0,0,0,0,1,0" MappedObjects="2"/>',
    '\t\t\t</MappingSet>',
    `\t\t\t<OriginalObjects ID="${id10}">`,
    `\t\t\t\t<SetCollection Objects="${ci0}"/>`,
    '\t\t\t</OriginalObjects>',
    '\t\t</SymmetrySystem>',
    '\t</AtomisticTreeRoot>',
    '</XSD>',
    ''
  )

  return lines.join('\n')
}

/**
 * Trigger download of text content as a file
 */
export function downloadTextFile(content: string, filename: string, mimeType = 'text/plain') {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/**
 * Get base filename without extension
 */
export function getBaseFilename(path: string): string {
  const name = path.split(/[\\/]/).pop() || 'structure'
  return name.replace(/\.[^.]*$/, '')
}

function xsdPropertyLines() {
  return [
    '\t\t<Property Name="AngleAxisType" DefinedOn="AngleBetweenPlanesBender" Type="Enumerated"/>',
    '\t\t<Property Name="AngleEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="BeadDocumentID" DefinedOn="MesoMoleculeSet" Type="String"/>',
    '\t\t<Property Name="BendBendEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="BendTorsionBendEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="BondEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="EFGAsymmetry" DefinedOn="Atom" Type="Double"/>',
    '\t\t<Property Name="EFGQuadrupolarCoupling" DefinedOn="Atom" Type="Double"/>',
    '\t\t<Property Name="ElectrostaticEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="FaceMillerIndex" DefinedOn="GrowthFace" Type="MillerIndex"/>',
    '\t\t<Property Name="FacetTransparency" DefinedOn="GrowthFace" Type="Float"/>',
    '\t\t<Property Name="FermiLevel" DefinedOn="ScalarFieldBase" Type="Double"/>',
    '\t\t<Property Name="Force" DefinedOn="Matter" Type="CoDirection"/>',
    '\t\t<Property Name="FrameFilter" DefinedOn="Trajectory" Type="String"/>',
    '\t\t<Property Name="HarmonicForceConstant" DefinedOn="HarmonicRestraint" Type="Double"/>',
    '\t\t<Property Name="HarmonicMinimum" DefinedOn="HarmonicRestraint" Type="Double"/>',
    '\t\t<Property Name="HydrogenBondEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="ImportOrder" DefinedOn="Bondable" Type="UnsignedInteger"/>',
    '\t\t<Property Name="InversionEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="IsBackboneAtom" DefinedOn="Atom" Type="Boolean"/>',
    '\t\t<Property Name="IsChiralCenter" DefinedOn="Atom" Type="Boolean"/>',
    '\t\t<Property Name="IsOutOfPlane" DefinedOn="Atom" Type="Boolean"/>',
    '\t\t<Property Name="IsRepeatArrowVisible" DefinedOn="ElectrodeWire" Type="Boolean"/>',
    '\t\t<Property Name="KineticEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="LineExtentPadding" DefinedOn="BestFitLineMonitor" Type="Double"/>',
    '\t\t<Property Name="LinkageGroupName" DefinedOn="Linkage" Type="String"/>',
    '\t\t<Property Name="ListIdentifier" DefinedOn="PropertyList" Type="String"/>',
    '\t\t<Property Name="NMRShielding" DefinedOn="Atom" Type="Double"/>',
    '\t\t<Property Name="NonBondEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="NormalMode" DefinedOn="Bondable" Type="Direction"/>',
    '\t\t<Property Name="NormalModeFrequency" DefinedOn="Bondable" Type="Double"/>',
    '\t\t<Property Name="NumScanSteps" DefinedOn="LinearScan" Type="UnsignedInteger"/>',
    '\t\t<Property Name="OrbitalCutoffRadius" DefinedOn="Bondable" Type="Double"/>',
    '\t\t<Property Name="PlaneExtentPadding" DefinedOn="BestFitPlaneMonitor" Type="Double"/>',
    '\t\t<Property Name="PotentialEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="QuantizationValue" DefinedOn="ScalarFieldBase" Type="Double"/>',
    '\t\t<Property Name="RelativeVelocity" DefinedOn="Matter" Type="Direction"/>',
    '\t\t<Property Name="RepeatArrowScale" DefinedOn="ElectrodeWire" Type="Float"/>',
    '\t\t<Property Name="RestraintEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="ScanEnd" DefinedOn="LinearScan" Type="Double"/>',
    '\t\t<Property Name="ScanStart" DefinedOn="LinearScan" Type="Double"/>',
    '\t\t<Property Name="SeparatedStretchStretchEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="SimulationStep" DefinedOn="Trajectory" Type="Integer"/>',
    '\t\t<Property Name="StretchBendStretchEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="StretchStretchEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="StretchTorsionStretchEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="Temperature" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="ThreeBodyNonBondEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="TorsionBendBendEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="TorsionEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="TorsionStretchEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="TotalEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="Units" DefinedOn="ScalarFieldBase" Type="String"/>',
    '\t\t<Property Name="ValenceCrossTermEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="ValenceDiagonalEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="VanDerWaalsEnergy" DefinedOn="ClassicalEnergyHolder" Type="Double"/>',
    '\t\t<Property Name="_Stress" DefinedOn="MatterSymmetrySystem" Type="Matrix"/>',
    '\t\t<Property Name="_TrajectoryStress" DefinedOn="MatterSymmetrySystem" Type="Matrix"/>'
  ]
}

function usableCell(cell: number[][] | null | undefined): number[][] | null {
  if (!cell || cell.length !== 3) return null
  const rows = cell.map(row => row?.slice(0, 3).map(value => Number(value)) ?? [])
  if (rows.some(row => row.length !== 3 || row.some(value => !Number.isFinite(value)))) return null
  if (Math.abs(determinant3(rows)) < 1e-12) return null
  return rows
}

function identityCell(): number[][] {
  return [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1]
  ]
}

function cartesianToFractional(position: number[], cell: number[][]) {
  const inverse = invert3(cell)
  if (!inverse) return normalizeFractional(position)
  const x = position[0] ?? 0
  const y = position[1] ?? 0
  const z = position[2] ?? 0
  return normalizeFractional([
    x * inverse[0][0] + y * inverse[1][0] + z * inverse[2][0],
    x * inverse[0][1] + y * inverse[1][1] + z * inverse[2][1],
    x * inverse[0][2] + y * inverse[1][2] + z * inverse[2][2]
  ])
}

function normalizeFractional(values: number[]) {
  return values.slice(0, 3).map(value => {
    if (!Number.isFinite(value)) return 0
    const wrapped = value - Math.floor(value)
    return Object.is(wrapped, -0) ? 0 : wrapped
  })
}

function determinant3(matrix: number[][]) {
  const [a, b, c] = matrix
  return (
    a[0] * (b[1] * c[2] - b[2] * c[1]) -
    a[1] * (b[0] * c[2] - b[2] * c[0]) +
    a[2] * (b[0] * c[1] - b[1] * c[0])
  )
}

function invert3(matrix: number[][]): number[][] | null {
  const [a, b, c] = matrix
  const determinant = determinant3(matrix)
  if (Math.abs(determinant) < 1e-12) return null
  return [
    [
      (b[1] * c[2] - b[2] * c[1]) / determinant,
      (a[2] * c[1] - a[1] * c[2]) / determinant,
      (a[1] * b[2] - a[2] * b[1]) / determinant
    ],
    [
      (b[2] * c[0] - b[0] * c[2]) / determinant,
      (a[0] * c[2] - a[2] * c[0]) / determinant,
      (a[2] * b[0] - a[0] * b[2]) / determinant
    ],
    [
      (b[0] * c[1] - b[1] * c[0]) / determinant,
      (a[1] * c[0] - a[0] * c[1]) / determinant,
      (a[0] * b[1] - a[1] * b[0]) / determinant
    ]
  ]
}

function normalizeSymbol(symbol: string | null | undefined) {
  const clean = (symbol || 'X').trim()
  if (!clean) return 'X'
  return clean.slice(0, 1).toUpperCase() + clean.slice(1, 2).toLowerCase()
}

function xmlAttribute(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function formatCellVector(vector: number[]) {
  return vector.map(value => formatXsdMetric(value, 10)).join(',')
}

function finiteNumber(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? null : value
}

function formatXsdMetric(value: number, decimals: number) {
  return value.toFixed(decimals).replace(/^-0(\.0+)?$/, '0$1')
}
