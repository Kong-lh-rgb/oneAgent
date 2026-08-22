/** 单轮展示层：把 AgentEvent[] 转成分析、工具、审批、验证和用量视图模型。

- 纯逻辑、无 React；事件解析集中在 presentation 层，组件只负责渲染。
- 不展示或解析 Provider 原始 reasoning；用户可见过程只来自结构化 AgentEvent。
- 参数摘要仅用于生成可读标签；原始参数只放在技术详情中。
*/

import type { AgentEvent, ModelUsage } from '../api/types'
import type { ComputerObservation } from '../api/computer'

export type ToolState = 'active' | 'done' | 'failed' | 'waiting'

export interface ToolStepVM {
  id: string
  name: string
  /** 人类可读动作（进行态或完成态），例如“输入‘测试’”或“已输入文本”。 */
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
  cachedInputTokens: number | null
  cacheHitRate: number | null
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
  verification: '已验证' | '等待验证' | null
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
      return app ? `打开 ${app}` : '打开应用'
    }
    case 'computer_observe': return '检查屏幕'
    case 'computer_type': {
      const text = argValue(args, 'text')
      return text ? `输入 “${text.slice(0, 40)}”` : '输入文本'
    }
    case 'computer_click': return '点击界面元素'
    case 'computer_key': return `按键 ${formatKeyShortcut(args)}`
    case 'computer_scroll': return '滚动窗口'
    case 'computer_focus_window': return '聚焦目标窗口'
    case 'read_file': {
      const path = argValue(args, 'path')
      return path ? `读取 ${path}` : '读取文件'
    }
    case 'write_file': {
      const path = argValue(args, 'path')
      return path ? `写入 ${path}` : '写入文件'
    }
    case 'list_files': return '查看文件'
    case 'artifact_publish': return '准备结果'
    case 'memory_get': return '读取记忆'
    case 'memory_search': return '搜索记忆'
    case 'task_create': return '创建计划'
    case 'task_update': return '更新计划'
    case 'task_get': return '查看计划'
    case 'task_list': return '查看任务'
    case 'mcp_status': return '查看 MCP 工具'
    case 'run_shell_command': return '运行命令'
    case 'web_search': {
      const query = argValue(args, 'query')
      return query ? `搜索 “${query.slice(0, 40)}”` : '联网搜索'
    }
    default: return `运行 ${humanizeToolName(name)}`
  }
}

export function toolDoneLabel(name: string, args: unknown, ok: boolean): string {
  if (!ok) {
    switch (name) {
      case 'computer_click': return '点击失败'
      case 'computer_type': return '输入失败'
      case 'computer_key': return '按键失败'
      case 'computer_observe': return '检查屏幕失败'
      case 'read_file': return '无法读取文件'
      case 'write_file': return '无法写入文件'
      case 'run_shell_command': return '命令失败'
      default: return `失败 ${humanizeToolName(name)}`
    }
  }
  switch (name) {
    case 'computer_open_app': {
      const app = argValue(args, 'app')
      return app ? `已打开 ${app}` : '已打开应用'
    }
    case 'computer_observe': return '已检查目标窗口'
    case 'computer_type': {
      const text = argValue(args, 'text')
      return text ? `已输入 “${text.slice(0, 40)}”` : '已输入文本'
    }
    case 'computer_click': return '已点击元素'
    case 'computer_key': return `已按键 ${formatKeyShortcut(args)}`
    case 'computer_scroll': return '已滚动'
    case 'computer_focus_window': return '已聚焦窗口'
    case 'read_file': return '已读取文件'
    case 'write_file': return '已写入文件'
    case 'list_files': return '已查看文件'
    case 'artifact_publish': return '已生成结果'
    case 'memory_get': return '已读取记忆'
    case 'memory_search': return '已搜索记忆'
    case 'task_create': return '已创建计划'
    case 'task_update': return '已更新计划'
    case 'task_get': return '已查看计划'
    case 'task_list': return '已查看任务'
    case 'mcp_status': return '已读取 MCP 工具清单'
    case 'run_shell_command': return '命令已执行'
    case 'web_search': return '已完成搜索'
    default: return `完成 ${humanizeToolName(name)}`
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
    max_steps: 'Vesta 在完成前达到了最大执行步数。',
    repeated_tool_call: 'Vesta 重复了相同策略，但没有取得进展。',
    stale_observation: '电脑画面已变化，无法安全验证这次操作。',
    stale_snapshot: '电脑画面已变化，无法安全验证这次操作。',
    permission_denied: '请求的操作没有获得批准。',
    model_error: '模型无法继续本轮执行。',
    context_error: '当前对话超过了可用上下文窗口。',
    interrupted: '本轮执行已中断，可以从恢复点继续。',
  }
  return {
    title: stopReason === 'interrupted' ? '执行已中断' : '执行已停止',
    message: known[stopReason ?? ''] ?? 'Vesta 未能完成本轮执行。',
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
      case 'agent_failed':
      case 'agent_cancelled': {
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

  // 用量优先采用终态聚合；运行中则累计每次模型调用，并保留缓存字段的未知语义。
  let usage: UsageVM | null = null
  if (finalUsage) {
    const cachedInputTokens = finalUsage.cached_input_tokens ?? null
    usage = {
      inputTokens: finalUsage.input_tokens,
      outputTokens: finalUsage.output_tokens,
      totalTokens: finalUsage.total_tokens,
      cachedInputTokens,
      cacheHitRate: cacheHitRate(
        cachedInputTokens,
        finalUsage.input_tokens,
      ),
    }
  } else if (usageParts.length > 0) {
    const cachedInputTokens = sumOptionalUsage(
      usageParts,
      'cached_input_tokens',
    )
    const inputTokens = usageParts.reduce((sum, u) => sum + u.input_tokens, 0)
    usage = {
      inputTokens,
      outputTokens: usageParts.reduce((sum, u) => sum + u.output_tokens, 0),
      totalTokens: usageParts.reduce((sum, u) => sum + u.total_tokens, 0),
      cachedInputTokens,
      cacheHitRate: cacheHitRate(cachedInputTokens, inputTokens),
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

function sumOptionalUsage(
  usages: ModelUsage[],
  field: 'cached_input_tokens',
): number | null {
  if (usages.some((usage) => usage[field] === null || usage[field] === undefined)) {
    return null
  }
  return usages.reduce((sum, usage) => sum + (usage[field] ?? 0), 0)
}

function cacheHitRate(cachedInputTokens: number | null, inputTokens: number): number | null {
  if (cachedInputTokens === null || inputTokens <= 0) return null
  return Math.min(100, Math.max(0, (cachedInputTokens / inputTokens) * 100))
}

/** 缓存命中率显示：整数不保留小数，其余保留一位。 */
export function formatCacheHitRate(rate: number | null): string {
  if (rate === null) return '暂无'
  const rounded = Math.round(rate * 10) / 10
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}%`
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
      ? '已验证'
      : last?.verification === 'unverified'
        ? '等待验证'
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
