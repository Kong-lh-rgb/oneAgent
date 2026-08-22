/** Turn 展示层：把 AgentEvent[] 转成 Thinking / Tool / Approval / Verification / Usage 的 ViewModel。

- 纯逻辑、无 React；事件解析集中在 presentation 层，组件只负责渲染。
- 不做任何协议解析：不把 reasoning 文本当作 Tool Call 执行，也不解析 DSML/<tool_calls>/<invoke>。
- 参数摘要仅用于人类可读 label；原始 arguments 放 details（Show technical details）。
*/

import type { AgentEvent, ModelUsage } from '../api/types'
import type { ComputerObservation } from '../api/computer'

export type ToolState = 'active' | 'done' | 'failed' | 'waiting'

export interface ToolStepVM {
  id: string
  name: string
  /** 人类可读动作（进行态/完成态），如 “Typing “测试”” / “Typed text”。 */
  label: string
  state: ToolState
  /** 技术细节（原始 arguments 摘要），主界面不 dump。 */
  details: string
  resultDetails?: string
  durationMs?: number
  errorCode?: string | null
  approval?: 'pending' | 'approved' | 'denied'
  verification?: 'unverified' | 'verified'
  isComputer: boolean
}

export interface UsageVM {
  inputTokens: number
  outputTokens: number
  totalTokens: number
}

export interface TurnView {
  tools: ToolStepVM[]
  /** 真实工具调用数（按 tool_call_id 去重）。 */
  toolCount: number
  /** 真实 model step 数（model_started 去重）。 */
  steps: number
  usage: UsageVM | null
  durationMs: number | null
  status:
    | 'thinking'
    | 'working'
    | 'waiting_approval'
    | 'verifying'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'interrupted'
  finalText: string
  currentAction: string | null
  targetApp: string | null
  capability: 'Computer' | 'Files' | 'Web' | 'Memory' | 'Task' | 'Artifact' | null
  error: { title: string; message: string; technical: string | null } | null
}

export interface ComputerContextVM {
  target: string | null
  window: string | null
  lastAction: string | null
  verification: 'Verified' | 'Waiting for verification' | null
  executionMode: string | null
  recentActions: ToolStepVM[]
}

/** 从 ToolCall arguments 里取可读参数（可能为对象或 JSON 字符串）。 */
function argValue(args: unknown, key: string): string | null {
  if (!args) return null
  if (typeof args === 'string') {
    try {
      return argValue(JSON.parse(args), key)
    } catch {
      return null
    }
  }
  if (typeof args === 'object') {
    const value = (args as Record<string, unknown>)[key]
    return typeof value === 'string' && value ? value : null
  }
  return null
}

function formatKeyShortcut(args: unknown): string {
  const key = argValue(args, 'key') ?? argValue(args, 'keycode')
  if (!key) return 'a key'
  const rawMods =
    typeof args === 'object' && args !== null
      ? (args as Record<string, unknown>).modifiers
      : null
  const mods = Array.isArray(rawMods)
    ? rawMods.filter((item): item is string => typeof item === 'string')
    : (argValue(args, 'modifiers')?.split(',') ?? [])
  const symbols: Record<string, string> = {
    command: '⌘', cmd: '⌘', shift: '⇧', option: '⌥', alt: '⌥', control: '⌃', ctrl: '⌃',
  }
  const parts = mods.map((m) => symbols[m.trim()] ?? m.trim()).filter(Boolean)
  const prettyKey = key.length === 1 ? key.toUpperCase() : key.replace(/^(?:key|Key)/, '')
  return [...parts, prettyKey].join(' ')
}

const COMPUTER_TOOLS = new Set([
  'computer_click', 'computer_type', 'computer_key', 'computer_scroll',
  'computer_open_app', 'computer_focus_window', 'computer_observe',
])

/** 人类可读动作标签（进行态 / 完成态）。未知工具走 fallback。 */
export function toolActiveLabel(name: string, args: unknown): string {
  switch (name) {
    case 'computer_open_app': {
      const app = argValue(args, 'app')
      return app ? `Opening ${app}` : 'Opening an application'
    }
    case 'computer_observe': return 'Inspecting the screen'
    case 'computer_type': {
      const text = argValue(args, 'text')
      return text ? `Typing “${text.slice(0, 40)}”` : 'Typing text'
    }
    case 'computer_click': return 'Clicking an interface element'
    case 'computer_key': return `Pressing ${formatKeyShortcut(args)}`
    case 'computer_scroll': return 'Scrolling the window'
    case 'computer_focus_window': return 'Focusing the target window'
    case 'read_file': {
      const path = argValue(args, 'path')
      return path ? `Reading ${path}` : 'Reading a file'
    }
    case 'write_file': {
      const path = argValue(args, 'path')
      return path ? `Writing ${path}` : 'Writing a file'
    }
    case 'list_files': return 'Reviewing files'
    case 'artifact_publish': return 'Preparing a result'
    case 'memory_get': return 'Reading memory'
    case 'memory_search': return 'Searching memory'
    case 'task_create': return 'Creating a plan'
    case 'task_update': return 'Updating the plan'
    case 'task_get': return 'Reviewing the plan'
    case 'task_list': return 'Reviewing tasks'
    case 'run_shell_command': return 'Running command'
    case 'web_search': {
      const query = argValue(args, 'query')
      return query ? `Searching for “${query.slice(0, 40)}”` : 'Searching the web'
    }
    default: return `Running ${humanizeToolName(name)}`
  }
}

export function toolDoneLabel(name: string, args: unknown, ok: boolean): string {
  if (!ok) {
    switch (name) {
      case 'computer_click': return 'Click failed'
      case 'computer_type': return 'Typing failed'
      case 'computer_key': return 'Key press failed'
      case 'computer_observe': return 'Screen inspection failed'
      case 'read_file': return 'Could not read file'
      case 'write_file': return 'Could not write file'
      case 'run_shell_command': return 'Command failed'
      default: return `Failed ${humanizeToolName(name)}`
    }
  }
  switch (name) {
    case 'computer_open_app': {
      const app = argValue(args, 'app')
      return app ? `Opened ${app}` : 'Opened application'
    }
    case 'computer_observe': return 'Observed the target window'
    case 'computer_type': {
      const text = argValue(args, 'text')
      return text ? `Typed “${text.slice(0, 40)}”` : 'Typed text'
    }
    case 'computer_click': return 'Clicked element'
    case 'computer_key': return `Pressed ${formatKeyShortcut(args)}`
    case 'computer_scroll': return 'Scrolled'
    case 'computer_focus_window': return 'Focused window'
    case 'read_file': return 'Read file'
    case 'write_file': return 'Wrote file'
    case 'list_files': return 'Reviewed files'
    case 'artifact_publish': return 'Created a result'
    case 'memory_get': return 'Read memory'
    case 'memory_search': return 'Searched memory'
    case 'task_create': return 'Created a plan'
    case 'task_update': return 'Updated the plan'
    case 'task_get': return 'Reviewed the plan'
    case 'task_list': return 'Reviewed tasks'
    case 'run_shell_command': return 'Command completed'
    case 'web_search': return 'Searched the web'
    default: return `Completed ${humanizeToolName(name)}`
  }
}

export function humanizeToolName(name: string): string {
  return name.replace(/^mcp__[^_]+__/, '').replaceAll('_', ' ')
}

function detailsText(args: unknown): string {
  if (!args) return ''
  if (typeof args === 'string') return args
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function parseObject(value: string | null): Record<string, unknown> | null {
  if (!value) return null
  try {
    const parsed = JSON.parse(value)
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

function targetFromOutput(output: string | null): string | null {
  const parsed = parseObject(output)
  if (!parsed) return null
  const direct = parsed.app
  if (typeof direct === 'string' && direct) return direct
  for (const key of ['target', 'active_app']) {
    const app = parsed[key]
    if (typeof app === 'object' && app !== null) {
      const name = (app as Record<string, unknown>).name
      if (typeof name === 'string' && name) return name
    }
  }
  return null
}

function capabilityForTool(name: string): TurnView['capability'] {
  if (COMPUTER_TOOLS.has(name)) return 'Computer'
  if (name.includes('artifact')) return 'Artifact'
  if (name.startsWith('memory_')) return 'Memory'
  if (name.startsWith('task_')) return 'Task'
  if (name === 'web_search') return 'Web'
  if (name.includes('file') || name.includes('shell')) return 'Files'
  return null
}

export function humanizeRunError(
  stopReason: string | null,
  rawError: string | null = null,
): { title: string; message: string; technical: string | null } {
  const known: Record<string, string> = {
    max_steps: 'Vesta reached the step limit before it could finish.',
    repeated_tool_call: 'Vesta repeated the same strategy without making progress.',
    stale_observation: 'The desktop changed before the action could be verified.',
    stale_snapshot: 'The desktop changed before the action could be verified.',
    permission_denied: 'The requested action was not approved.',
    model_error: 'The model could not continue this run.',
    context_error: 'The conversation exceeded the available context window.',
    interrupted: 'The run was interrupted and can be recovered.',
  }
  return {
    title: stopReason === 'interrupted' ? 'Interrupted' : 'Stopped',
    message: known[stopReason ?? ''] ?? 'Vesta could not complete this run.',
    technical: rawError || stopReason || null,
  }
}

/** 从 computer 工具输出里解析验证状态（纯展示，不依赖后端字段）。 */
export function parseVerificationStatus(output: string | null): 'verified' | 'unverified' | null {
  if (!output) return null
  const status = /"verification_status"\s*:\s*"(verified|unverified)"/.exec(output)
  if (status) return status[1] as 'verified' | 'unverified'
  if (/frontmost_verified"\s*:\s*true/.test(output)) return 'verified'
  return null
}

export function buildTurnView(
  events: AgentEvent[],
  opts: { now?: number } = {},
): TurnView {
  const tools: ToolStepVM[] = []
  const toolIndexes = new Map<string, number>()
  let lastUnverifiedComputer: number | null = null
  let steps = 0
  const usageParts: ModelUsage[] = []
  let finalUsage: ModelUsage | null = null
  let startedAt: number | null = null
  let endedAt: number | null = null
  let finalText = ''
  let targetApp: string | null = null
  let capability: TurnView['capability'] = null
  let stopReason: string | null = null
  let rawError: string | null = null
  const modelSteps = new Set<number>()

  const upsertTool = (
    id: string,
    name: string,
    args: unknown,
    state: ToolState,
    isComputer: boolean,
  ): number => {
    const existing = toolIndexes.get(id)
    if (existing === undefined) {
      toolIndexes.set(id, tools.length)
      tools.push({
        id,
        name,
        label: toolActiveLabel(name, args),
        state,
        details: detailsText(args),
        isComputer,
      })
      return tools.length - 1
    }
    tools[existing] = { ...tools[existing], name, state }
    return existing
  }

  for (const event of events) {
    if (event.event_time) {
      const t = Date.parse(event.event_time)
      if (!Number.isNaN(t)) {
        if (event.type === 'agent_started') startedAt = t
        if (
          event.type === 'agent_completed' ||
          event.type === 'agent_failed' ||
          event.type === 'agent_cancelled'
        ) {
          endedAt = t
        }
      }
    }

    switch (event.type) {
      case 'model_started': {
        if (event.step !== null && event.step !== undefined) modelSteps.add(event.step)
        break
      }
      case 'model_completed': {
        if (event.step !== null && event.step !== undefined) modelSteps.add(event.step)
        if (event.usage) usageParts.push(event.usage)
        break
      }
      case 'tool_started': {
        if (event.tool_call) {
          const name = event.tool_call.name
          const isComputer = COMPUTER_TOOLS.has(name)
          capability = capabilityForTool(name) ?? capability
          if (name === 'computer_open_app') {
            targetApp = argValue(event.tool_call.arguments, 'app') ?? targetApp
          }
          const idx = upsertTool(
            event.tool_call.id,
            name,
            event.tool_call.arguments,
            'active',
            isComputer,
          )
          tools[idx].label = toolActiveLabel(name, event.tool_call.arguments)
          // 新电脑操作开始时重置验证传播；observe 是验证动作，不重置。
          if (isComputer && name !== 'computer_observe') lastUnverifiedComputer = null
        }
        break
      }
      case 'tool_approval_required': {
        if (event.tool_call) {
          const idx = upsertTool(
            event.tool_call.id,
            event.tool_call.name,
            event.tool_call.arguments,
            'waiting',
            COMPUTER_TOOLS.has(event.tool_call.name),
          )
          tools[idx].approval = 'pending'
          tools[idx].state = 'waiting'
        }
        break
      }
      case 'tool_approval_completed': {
        if (event.tool_call && event.approval_decision) {
          const idx = toolIndexes.get(event.tool_call.id)
          if (idx !== undefined) {
            const decision = event.approval_decision === 'approved' ? 'approved' : 'denied'
            tools[idx].approval = decision
            // 批准后回到执行态；拒绝则终止。
            tools[idx].state = decision === 'approved' ? 'active' : 'failed'
            if (decision === 'approved') lastUnverifiedComputer = null
          }
        }
        break
      }
      case 'tool_completed': {
        if (event.tool_result) {
          const idx = toolIndexes.get(event.tool_result.tool_call_id)
          const name = event.tool_result.tool_name
          const ok = event.tool_result.success
          if (idx !== undefined) {
            tools[idx].name = name
            tools[idx].state = ok ? 'done' : 'failed'
            tools[idx].label = toolDoneLabel(name, tools[idx].details, ok)
            tools[idx].durationMs = event.tool_result.duration_ms
            tools[idx].resultDetails = event.tool_result.output ?? undefined
            tools[idx].errorCode = event.tool_result.error
            targetApp = targetFromOutput(event.tool_result.output) ?? targetApp
            if (COMPUTER_TOOLS.has(name)) {
              const verification = parseVerificationStatus(event.tool_result.output)
              if (verification === 'unverified') {
                tools[idx].verification = 'unverified'
                lastUnverifiedComputer = idx
              } else if (verification === 'verified') {
                tools[idx].verification = 'verified'
                if (lastUnverifiedComputer === idx) lastUnverifiedComputer = null
              }
            }
            // observe 验证通过 → 把最近未验证的电脑操作标为已验证。
            if (name === 'computer_observe' && ok && lastUnverifiedComputer !== null) {
              const verification = parseVerificationStatus(event.tool_result.output)
              if (verification === 'verified' && tools[lastUnverifiedComputer]) {
                tools[lastUnverifiedComputer].verification = 'verified'
                lastUnverifiedComputer = null
              }
            }
          }
        }
        break
      }
      case 'agent_completed':
      case 'agent_failed': {
        finalUsage = event.result?.usage ?? event.usage ?? finalUsage
        if (event.result?.steps) steps = event.result.steps
        finalText = event.result?.final_message?.content ?? finalText
        stopReason = event.stop_reason ?? event.result?.stop_reason ?? stopReason
        rawError = event.result?.error?.message ?? rawError
        break
      }
      default:
        break
    }
  }

  // usage：优先 agent_completed/failed 的最终 usage；否则累计各 model_completed usage。
  let usage: UsageVM | null = null
  if (finalUsage) {
    usage = {
      inputTokens: finalUsage.input_tokens,
      outputTokens: finalUsage.output_tokens,
      totalTokens: finalUsage.total_tokens,
    }
  } else if (usageParts.length > 0) {
    usage = {
      inputTokens: usageParts.reduce((sum, u) => sum + u.input_tokens, 0),
      outputTokens: usageParts.reduce((sum, u) => sum + u.output_tokens, 0),
      totalTokens: usageParts.reduce((sum, u) => sum + u.total_tokens, 0),
    }
  }

  // 只统计出现过 tool_started 的真实工具调用（按 tool_call_id 去重）。
  const startedIds = new Set<string>()
  for (const event of events) {
    if (event.type === 'tool_started' && event.tool_call) startedIds.add(event.tool_call.id)
  }
  const toolCount = startedIds.size

  if (steps === 0) steps = modelSteps.size

  let durationMs: number | null = null
  if (startedAt !== null) {
    const end = endedAt ?? opts.now ?? Date.now()
    durationMs = Math.max(0, end - startedAt)
  }

  const hasPendingApproval = tools.some(
    (tool) => tool.approval === 'pending' && tool.state === 'waiting',
  )
  const hasUnverified = tools.some(
    (tool) => tool.verification === 'unverified',
  )
  const hasActiveTool = tools.some((tool) => tool.state === 'active')
  const status: TurnView['status'] = events.some((e) => e.type === 'agent_failed')
    ? stopReason === 'interrupted' ? 'interrupted' : 'failed'
    : events.some((e) => e.type === 'agent_cancelled')
      ? 'cancelled'
      : events.some((e) => e.type === 'agent_completed')
        ? 'completed'
        : hasPendingApproval
          ? 'waiting_approval'
          : hasUnverified
            ? 'verifying'
            : hasActiveTool
              ? 'working'
              : 'thinking'

  const currentTool = [...tools].reverse().find(
    (tool) => tool.state === 'active' || tool.state === 'waiting',
  )

  return {
    tools,
    toolCount,
    steps,
    usage,
    durationMs,
    status,
    finalText,
    currentAction: currentTool?.label ?? null,
    targetApp,
    capability,
    error:
      status === 'failed' || status === 'interrupted'
        ? humanizeRunError(stopReason, rawError)
        : null,
  }
}

export function buildComputerContext(
  events: AgentEvent[],
  observation: ComputerObservation | null,
): ComputerContextVM {
  const view = buildTurnView(events)
  const computerActions = view.tools.filter((tool) => tool.isComputer)
  const last = computerActions.at(-1)
  const parsed = parseObject(last?.resultDetails ?? null)
  const mode = parsed?.execution_mode
  return {
    target:
      observation?.target?.name
      ?? observation?.active_app?.name
      ?? view.targetApp,
    window: observation?.active_window?.title || null,
    lastAction: last?.label ?? null,
    verification: last?.verification === 'verified'
      ? 'Verified'
      : last?.verification === 'unverified'
        ? 'Waiting for verification'
        : null,
    executionMode: typeof mode === 'string' ? mode.replaceAll('_', ' ') : null,
    recentActions: computerActions.slice(-6),
  }
}

/** token 显示：980 → “980”，1234 → “1.2k”，18400 → “18.4k”。 */
export function formatTokens(n: number): string {
  if (n < 1000) return String(Math.round(n))
  return `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k`
}

/** duration 显示：18.4s；超过 60s 用 m ss。 */
export function formatDuration(ms: number | null): string {
  if (ms === null) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${String(s).padStart(2, '0')}s`
}
