export interface StructureSource {
  id: string
  parser: string
  apiBase: string
}

export interface AseFrame {
  frame_index: number
  positions: number[][]
  cell: number[][]
  pbc: boolean[]
  tags: number[]
  fixed_indices: number[]
  energy?: number | null
  fmax?: number | null
  symbols?: string[] | null
  numbers?: number[] | null
}

export interface AsePreviewResponse {
  path: string
  name: string
  format?: string | null
  transport: 'json' | 'binary-available'
  is_trajectory: boolean
  n_frames: number
  n_atoms: number
  initial_frame_index: number
  topology_stable: boolean
  size_limit_overridden?: boolean
  frame: AseFrame
  source?: StructureSource
  file_incomplete?: boolean
  scan_completed?: boolean
  warnings?: string[]
}

export interface BinaryArraySpec {
  offset: number
  byte_length: number
  shape: number[]
}

export interface AseFrameChunkHeader {
  version: number
  dtype: 'float32-le'
  int_dtype: 'int32-le'
  start: number
  count: number
  n_frames_total: number
  n_atoms: number
  topology_stable: boolean
  frame_encoding?: 'variable-atoms-v1'
  path?: string
  name?: string
  format?: string | null
  symbols: string[]
  numbers: number[]
  pbc: boolean[]
  arrays: Record<string, BinaryArraySpec>
  nan_means_null?: string[]
}

export interface AseFrameChunk {
  header: AseFrameChunkHeader
  buffer: ArrayBuffer
  dataStart: number
  positions: Float32Array
  cells: Float32Array
  tags?: Int32Array
  fixedMask?: Uint8Array
  energy?: Float32Array
  fmax?: Float32Array
  // Variable-topology (frame_encoding === 'variable-atoms-v1') fields.
  // positions is flattened to [total_atoms, 3]; numbers/tags/fixedMask are
  // concatenated by frame with offsets derived from frameAtomCounts prefix sums.
  frameAtomCounts?: Int32Array
  numbers?: Int32Array
  pbc?: Uint8Array
}

export interface AseFrameJsonChunk {
  start: number
  count: number
  frames: AseFrame[]
}
