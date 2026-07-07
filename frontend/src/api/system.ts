import { request } from './http'

export interface SystemInfo {
  username: string
  hostname: string
  cwd: string
  project_version: string
  python_version: string
  scheduler: string
  workspace_root: string
  max_upload_size_mb: number
}

export function getSystemInfo() {
  return request<SystemInfo>('/api/system/info')
}
