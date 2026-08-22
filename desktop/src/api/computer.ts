/** Computer API：状态 / 权限请求 / 只读最新 Observation（复用共享 rpcClient）。 */

import { SERVER_URL } from './config'
import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'

// ---------------------------------------------------------------------------
// 类型（与 Python model 对齐，不发明第二套语义）
// ---------------------------------------------------------------------------

export type ComputerPermissionStatus = 'granted' | 'required' | 'unknown'

export interface ComputerLeaseStatus {
  busy: boolean
  owner_run_id: string
  acquired_at: string | null
  process_id: number
}

export interface ComputerStatus {
  enabled: boolean
  available: boolean
  platform: string
  runtime: string | null
  reason: string | null
  helper_path: string | null
  permissions: {
    accessibility: ComputerPermissionStatus
    screen_recording: ComputerPermissionStatus
  }
  lease: ComputerLeaseStatus | null
}

export interface ComputerBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface ComputerActiveApp {
  name: string
  bundle_id: string | null
  pid: number | null
}

export interface ComputerWindow {
  ref: string
  title: string
  bounds: ComputerBounds
}

export interface ComputerElement {
  ref: string
  role: string
  title: string | null
  value: string | null
  enabled: boolean
  focused: boolean
  editable?: boolean
  bounds: ComputerBounds | null
  actions: string[]
}

export interface ComputerObservation {
  id: string
  created_at: string | null
  active_app: ComputerActiveApp | null
  target?: ComputerActiveApp | null
  target_is_frontmost?: boolean
  user_frontmost_app?: ComputerActiveApp | null
  active_window: ComputerWindow | null
  windows: ComputerWindow[]
  elements: ComputerElement[]
  focused_element_ref?: string | null
  truncated?: boolean
  element_stats?: {
    observed: number
    returned: number
    editable_count: number
    actionable_count: number
    repetitive_elements_dropped: number
  }
  screenshot_ref: string | null
}

export interface ComputerLatestObservation {
  run_id: string | null
  event_time: string | null
  observation: ComputerObservation | null
}

export type ComputerPermissionName = 'accessibility' | 'screen_recording'

// ---------------------------------------------------------------------------
// RPC
// ---------------------------------------------------------------------------

export async function getComputerStatus(): Promise<ComputerStatus> {
  return rpcClient.call<ComputerStatus>(RpcMethods.computerStatus, {})
}

export async function requestComputerPermission(
  permission: ComputerPermissionName,
): Promise<ComputerStatus> {
  return rpcClient.call<ComputerStatus>(RpcMethods.computerRequestPermission, {
    permission,
  })
}

export async function getLatestComputerObservation(
  runId?: string,
): Promise<ComputerLatestObservation> {
  const params: Record<string, unknown> = {}
  if (runId) params.run_id = runId
  return rpcClient.call<ComputerLatestObservation>(
    RpcMethods.computerLatestObservation,
    params,
  )
}

// ---------------------------------------------------------------------------
// 纯帮助函数（可独立测试）
// ---------------------------------------------------------------------------

/** 根据 observation.id 构造 screenshot URL（绝不用本地绝对路径 screenshot_ref）。 */
export function buildComputerScreenshotUrl(observationId: string): string {
  return `${SERVER_URL}/computer/screenshots/${observationId}.png`
}

/** Machine Lease 的简短展示文案。 */
export function leaseLabel(lease: ComputerLeaseStatus | null): string {
  if (!lease || !lease.owner_run_id) return 'Free'
  return `Controlled by Run ${lease.owner_run_id.slice(0, 8)}`
}

export function permissionLabel(status: ComputerPermissionStatus): string {
  if (status === 'granted') return 'Granted'
  if (status === 'required') return 'Required'
  return 'Unknown'
}
