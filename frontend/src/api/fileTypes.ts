import type { FileItem, PreviewType } from './files'

const STRUCTURE_FALLBACK_EXTENSIONS = new Set([
  '.xyz',
  '.pdb',
  '.mol',
  '.sdf',
  '.cif',
  '.traj',
  '.extxyz',
  '.vasp',
  '.xsf',
  '.xsd',
  '.xtd',
  '.arc',
  '.cube',
  '.gen',
  '.db',
  '.lammps',
  '.dump',
  '.gjf',
  '.com',
  '.fdf',
  '.pwi'
])

const STRUCTURE_FALLBACK_NAMES = new Set(['vasp.xml', 'vasprun.xml'])
const VASP_STRUCTURE_NAME_RE = /(^|[^A-Z0-9])(POSCAR|CONTCAR|XDATCAR|OUTCAR)(?:[0-9]+)?($|[^A-Z0-9])/

export function pathBaseName(path: string) {
  return path.split(/[\\/]/).pop() ?? path
}

export function extensionFromName(name: string) {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index).toLowerCase() : ''
}

export function isForcedStructureName(name: string) {
  return VASP_STRUCTURE_NAME_RE.test(name.toUpperCase())
}

export function isStructurePreviewItem(item: Pick<FileItem, 'name' | 'preview_type'>) {
  return item.preview_type === 'structure' || isForcedStructureName(item.name)
}

export function isStructurePreviewPath(path: string, previewType?: PreviewType | null) {
  if (previewType === 'structure') return true
  const name = pathBaseName(path).toLowerCase()
  return (
    isForcedStructureName(name) ||
    STRUCTURE_FALLBACK_NAMES.has(name) ||
    STRUCTURE_FALLBACK_EXTENSIONS.has(extensionFromName(name))
  )
}
