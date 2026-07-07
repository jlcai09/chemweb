import { applyAuthQuery, authHeaders, fetchWithAuthRetry, request } from './http'
import { API_BASE, ApiError } from './http'
import type {
  AseFrame,
  AseFrameChunk,
  AseFrameJsonChunk,
  AsePreviewResponse,
  BinaryArraySpec,
  StructureSource
} from '../types/structure'
import { elementFromNumber } from '../viewer/elements'

export const ASE_STRUCTURE_SOURCE: StructureSource = {
  id: 'ase',
  parser: 'ase',
  apiBase: '/api/structures/ase'
}

export interface StructureRequestOptions {
  signal?: AbortSignal
}

function aseQuery(path: string, format?: string | null, force = false) {
  const params = new URLSearchParams({ path })
  if (format) params.set('format', format)
  if (force) params.set('force', 'true')
  return params
}

function sourceBase(source?: StructureSource | null) {
  return (source?.apiBase || ASE_STRUCTURE_SOURCE.apiBase).replace(/\/+$/, '')
}

export function readAsePreview(path: string, format?: string | null, force = false, options: StructureRequestOptions = {}) {
  return readStructurePreview(ASE_STRUCTURE_SOURCE, path, format, force, options)
}

export function readAseFrame(path: string, index: number, format?: string | null, force = false) {
  return readStructureFrame(ASE_STRUCTURE_SOURCE, path, index, format, force)
}

export function readAseFrameJsonChunk(path: string, start: number, count: number, format?: string | null, force = false) {
  return readStructureFrameJsonChunk(ASE_STRUCTURE_SOURCE, path, start, count, format, force)
}

export function readAseFrameChunk(path: string, start: number, count: number, format?: string | null, force = false): Promise<AseFrameChunk> {
  return readStructureFrameChunk(ASE_STRUCTURE_SOURCE, path, start, count, format, force)
}

export function readStructurePreview(
  source: StructureSource | null | undefined,
  path: string,
  format?: string | null,
  force = false,
  options: StructureRequestOptions = {}
) {
  const activeSource = source ?? ASE_STRUCTURE_SOURCE
  return request<AsePreviewResponse>(`${sourceBase(activeSource)}/preview?${aseQuery(path, format, force).toString()}`, {
    signal: options.signal
  })
    .then(preview => ({ ...preview, source: activeSource }))
}

export function readStructureFrame(source: StructureSource | null | undefined, path: string, index: number, format?: string | null, force = false) {
  const params = aseQuery(path, format, force)
  params.set('index', String(index))
  return request<AseFrame>(`${sourceBase(source)}/frame?${params.toString()}`)
}

export function readStructureFrameJsonChunk(source: StructureSource | null | undefined, path: string, start: number, count: number, format?: string | null, force = false) {
  const params = aseQuery(path, format, force)
  params.set('start', String(start))
  params.set('count', String(count))
  return request<AseFrameJsonChunk>(`${sourceBase(source)}/frames?${params.toString()}`)
}

export async function readStructureFrameChunk(source: StructureSource | null | undefined, path: string, start: number, count: number, format?: string | null, force = false): Promise<AseFrameChunk> {
  const params = aseQuery(path, format, force)
  params.set('start', String(start))
  params.set('count', String(count))

  const response = await fetchWithAuthRetry(`${API_BASE}${sourceBase(source)}/frames.bin?${params.toString()}`, {
    credentials: 'include',
    headers: authHeaders({ Accept: 'application/vnd.chemssh.structure+bin' })
  }, replaceAuth => authHeaders({ Accept: 'application/vnd.chemssh.structure+bin' }, { replaceAuth }))

  if (!response.ok) {
    const text = await response.text()
    let code = 'HTTP_ERROR'
    let message = response.statusText
    if (text) {
      try {
        const data = JSON.parse(text)
        code = data?.error?.code ?? code
        message = data?.error?.message ?? message
      } catch {
        message = text
      }
    }
    throw new ApiError(code, message, response.status)
  }

  const buffer = await response.arrayBuffer()
  return parseStructureBinaryChunk(buffer)
}

/**
 * Parse a CWB1 binary structure chunk from an ArrayBuffer.
 * Shared by both the HTTP chunk endpoint and the SSE stream path.
 */
export function parseStructureBinaryChunk(buffer: ArrayBuffer): AseFrameChunk {
  const view = new DataView(buffer)
  const magic = new TextDecoder().decode(buffer.slice(0, 4))
  if (magic !== 'CWB1') {
    throw new ApiError('INVALID_STRUCTURE_BINARY', 'Invalid structure binary payload')
  }

  const headerLength = view.getUint32(4, true)
  const headerStart = 8
  const headerEnd = headerStart + headerLength
  const header = JSON.parse(new TextDecoder().decode(buffer.slice(headerStart, headerEnd)))
  const padding = (4 - (headerLength % 4)) % 4
  const dataStart = headerEnd + padding

  function ensureArrayBounds(spec: BinaryArraySpec | undefined, elementSize: number, name: string) {
    if (!spec) return
    if (spec.offset < 0 || spec.byte_length < 0 || dataStart + spec.offset + spec.byte_length > buffer.byteLength) {
      throw new ApiError('INVALID_STRUCTURE_BINARY', `Structure binary array ${name} is out of bounds`)
    }
    if (spec.byte_length % elementSize !== 0) {
      throw new ApiError('INVALID_STRUCTURE_BINARY', `Structure binary array ${name} has invalid byte length`)
    }
  }

  function floatArray(spec?: BinaryArraySpec, name = 'float') {
    if (!spec) return undefined
    ensureArrayBounds(spec, Float32Array.BYTES_PER_ELEMENT, name)
    return new Float32Array(buffer, dataStart + spec.offset, spec.byte_length / Float32Array.BYTES_PER_ELEMENT)
  }

  function intArray(spec?: BinaryArraySpec, name = 'int') {
    if (!spec) return undefined
    ensureArrayBounds(spec, Int32Array.BYTES_PER_ELEMENT, name)
    return new Int32Array(buffer, dataStart + spec.offset, spec.byte_length / Int32Array.BYTES_PER_ELEMENT)
  }

  function uint8Array(spec?: BinaryArraySpec, name = 'uint8') {
    if (!spec) return undefined
    ensureArrayBounds(spec, Uint8Array.BYTES_PER_ELEMENT, name)
    return new Uint8Array(buffer, dataStart + spec.offset, spec.byte_length)
  }

  function assertLength(name: string, actual: number | undefined, expected: number) {
    if (actual !== expected) {
      throw new ApiError('INVALID_STRUCTURE_BINARY', `Structure binary array ${name} has invalid length`)
    }
  }

  const positions = floatArray(header.arrays.positions, 'positions')
  if (!positions) throw new ApiError('INVALID_STRUCTURE_BINARY', 'Structure binary payload has no positions array')

  if (header.frame_encoding === 'variable-atoms-v1') {
    const frameAtomCounts = intArray(header.arrays.frame_atom_counts, 'frame_atom_counts')
    const numbers = intArray(header.arrays.numbers, 'numbers')
    const cells = floatArray(header.arrays.cells, 'cells') ?? new Float32Array()
    const tags = intArray(header.arrays.tags, 'tags')
    const fixedMask = uint8Array(header.arrays.fixed_mask, 'fixed_mask')
    const pbc = uint8Array(header.arrays.pbc, 'pbc')
    const energy = floatArray(header.arrays.energy, 'energy')
    const fmax = floatArray(header.arrays.fmax, 'fmax')
    if (!frameAtomCounts || !numbers) {
      throw new ApiError('INVALID_STRUCTURE_BINARY', 'Variable structure binary payload is missing frame metadata')
    }
    assertLength('frame_atom_counts', frameAtomCounts.length, header.count)
    let totalAtoms = 0
    for (const atomCount of frameAtomCounts) {
      if (atomCount <= 0) throw new ApiError('INVALID_STRUCTURE_BINARY', 'Variable structure binary payload has invalid atom count')
      totalAtoms += atomCount
    }
    assertLength('positions', positions.length, totalAtoms * 3)
    assertLength('numbers', numbers.length, totalAtoms)
    if (tags) assertLength('tags', tags.length, totalAtoms)
    if (fixedMask) assertLength('fixed_mask', fixedMask.length, totalAtoms)
    assertLength('cells', cells.length, header.count * 9)
    if (pbc) assertLength('pbc', pbc.length, header.count * 3)
    if (energy) assertLength('energy', energy.length, header.count)
    if (fmax) assertLength('fmax', fmax.length, header.count)

    return {
      header,
      buffer,
      dataStart,
      positions,
      cells,
      tags,
      fixedMask,
      energy,
      fmax,
      frameAtomCounts,
      numbers,
      pbc
    }
  }

  if (header.frame_encoding) {
    throw new ApiError('INVALID_STRUCTURE_BINARY', `Unsupported structure binary frame encoding: ${header.frame_encoding}`)
  }

  return {
    header,
    buffer,
    dataStart,
    positions,
    cells: floatArray(header.arrays.cells, 'cells') ?? new Float32Array(),
    tags: intArray(header.arrays.tags, 'tags'),
    fixedMask: uint8Array(header.arrays.fixed_mask, 'fixed_mask'),
    energy: floatArray(header.arrays.energy, 'energy'),
    fmax: floatArray(header.arrays.fmax, 'fmax')
  }
}

/**
 * SSE stream that pushes binary frame chunks continuously.
 *
 * Opens one EventSource connection to ``/frames.stream`` and receives all
 * chunks without further requests, eliminating N serial HTTP round-trips.
 * Each chunk is a base64-encoded CWB1 binary payload (same format as
 * ``/frames.bin``).
 *
 * Returns the EventSource so the caller can close it on unmount / navigation.
 */
export function streamStructureFrames(
  source: StructureSource | null | undefined,
  path: string,
  onChunk: (chunk: AseFrameChunk) => void,
  onDone: (nFrames: number) => void,
  onError: (error: Error) => void,
  format?: string | null,
  force = false,
  chunkSize = 32,
): EventSource {
  const base = sourceBase(source)
  const params = aseQuery(path, format, force)
  params.set('chunk', String(chunkSize))
  applyAuthQuery(params)

  const url = `${API_BASE}${base}/frames.stream?${params.toString()}`
  const es = new EventSource(url)

  es.addEventListener('chunk', (event: MessageEvent) => {
    try {
      const binary = base64ToArrayBuffer(event.data)
      const chunk = parseStructureBinaryChunk(binary)
      onChunk(chunk)
    } catch (err) {
      es.close()
      onError(err instanceof Error ? err : new Error(String(err)))
    }
  })

  es.addEventListener('done', (event: MessageEvent) => {
    es.close()
    try {
      const info = JSON.parse(event.data)
      onDone(info.n_frames ?? 0)
    } catch {
      onDone(0)
    }
  })

  es.addEventListener('error', () => {
    es.close()
    onError(new Error('SSE connection error'))
  })

  return es
}

/** Decode a base64 string to an ArrayBuffer. */
function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

export function frameFromStructureChunk(chunk: AseFrameChunk, localIndex: number): AseFrame | null {
  if (chunk.header.frame_encoding === 'variable-atoms-v1') {
    return frameFromVariableStructureChunk(chunk, localIndex)
  }
  return frameFromFixedStructureChunk(chunk, localIndex)
}

function frameFromFixedStructureChunk(chunk: AseFrameChunk, localIndex: number): AseFrame | null {
  const nAtoms = chunk.header.n_atoms
  if (nAtoms <= 0 || localIndex < 0 || localIndex >= chunk.header.count) return null

  const atomOffset = localIndex * nAtoms
  const positionOffset = atomOffset * 3
  const positions: number[][] = []
  const tags: number[] = []
  const fixedIndices: number[] = []
  for (let atomIndex = 0; atomIndex < nAtoms; atomIndex += 1) {
    const offset = positionOffset + atomIndex * 3
    positions.push([
      chunk.positions[offset] ?? 0,
      chunk.positions[offset + 1] ?? 0,
      chunk.positions[offset + 2] ?? 0
    ])
    tags.push(chunk.tags?.[atomOffset + atomIndex] ?? 0)
    if (chunk.fixedMask?.[atomOffset + atomIndex]) fixedIndices.push(atomIndex)
  }

  const cellOffset = localIndex * 9
  const cell: number[][] = []
  for (let row = 0; row < 3; row += 1) {
    const offset = cellOffset + row * 3
    cell.push([
      chunk.cells[offset] ?? 0,
      chunk.cells[offset + 1] ?? 0,
      chunk.cells[offset + 2] ?? 0
    ])
  }

  const energy = chunk.energy?.[localIndex]
  const fmax = chunk.fmax?.[localIndex]

  return {
    frame_index: chunk.header.start + localIndex,
    positions,
    cell,
    pbc: chunk.header.pbc,
    tags,
    fixed_indices: fixedIndices,
    energy: energy === undefined || Number.isNaN(energy) ? null : energy,
    fmax: fmax === undefined || Number.isNaN(fmax) ? null : fmax,
    symbols: chunk.header.symbols,
    numbers: chunk.header.numbers
  }
}

export function frameFromVariableStructureChunk(chunk: AseFrameChunk, localIndex: number): AseFrame | null {
  if (chunk.header.frame_encoding !== 'variable-atoms-v1') return null
  if (!chunk.frameAtomCounts || !chunk.numbers) return null
  if (localIndex < 0 || localIndex >= chunk.header.count) return null

  let atomOffset = 0
  for (let index = 0; index < localIndex; index += 1) atomOffset += chunk.frameAtomCounts[index] ?? 0
  const nAtoms = chunk.frameAtomCounts[localIndex] ?? 0
  if (nAtoms <= 0) return null
  const atomEnd = atomOffset + nAtoms
  const positionOffset = atomOffset * 3

  const positions: number[][] = []
  const tags: number[] = []
  const fixedIndices: number[] = []
  const numbers = Array.from(chunk.numbers.subarray(atomOffset, atomEnd))
  for (let atomIndex = 0; atomIndex < nAtoms; atomIndex += 1) {
    const offset = positionOffset + atomIndex * 3
    positions.push([
      chunk.positions[offset] ?? 0,
      chunk.positions[offset + 1] ?? 0,
      chunk.positions[offset + 2] ?? 0
    ])
    tags.push(chunk.tags?.[atomOffset + atomIndex] ?? 0)
    if (chunk.fixedMask?.[atomOffset + atomIndex]) fixedIndices.push(atomIndex)
  }

  const cellOffset = localIndex * 9
  const cell: number[][] = []
  for (let row = 0; row < 3; row += 1) {
    const offset = cellOffset + row * 3
    cell.push([
      chunk.cells[offset] ?? 0,
      chunk.cells[offset + 1] ?? 0,
      chunk.cells[offset + 2] ?? 0
    ])
  }

  const pbcOffset = localIndex * 3
  const pbc = chunk.pbc
    ? [Boolean(chunk.pbc[pbcOffset]), Boolean(chunk.pbc[pbcOffset + 1]), Boolean(chunk.pbc[pbcOffset + 2])]
    : chunk.header.pbc
  const energy = chunk.energy?.[localIndex]
  const fmax = chunk.fmax?.[localIndex]

  return {
    frame_index: chunk.header.start + localIndex,
    positions,
    cell,
    pbc,
    tags,
    fixed_indices: fixedIndices,
    energy: energy === undefined || Number.isNaN(energy) ? null : energy,
    fmax: fmax === undefined || Number.isNaN(fmax) ? null : fmax,
    symbols: numbers.map(n => elementFromNumber(n) ?? 'X'),
    numbers
  }
}
