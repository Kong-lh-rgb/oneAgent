/** 与 Agent Server JSON 对应的类型（V0 最小集）。 */

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool'

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown> | string
}

export interface Message {
  role: MessageRole
  content: string | null
  name?: string | null
  tool_call_id?: string | null
  tool_calls?: ToolCall[]
}

export type RunStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export type AgentMode = 'normal' | 'plan'

export interface Run {
  id: string
  conversation_id: string | null
  status: RunStatus
  user_message: string
  created_at: string
  started_at: string | null
  updated_at: string
  completed_at: string | null
  error: string | null
  stop_reason: string | null
  recovered_from_run_id: string | null
  source: string | null
  source_id: string | null
  scheduled_for: string | null
  triggered_at: string | null
  mode: AgentMode
}

export interface ModelUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface AgentResult {
  run_id: string
  final_message: Message
  messages: Message[]
  steps: number
  stop_reason: string
  usage: ModelUsage
  error: { type: string; message: string } | null
  plan_task_id: string | null
}

export interface SendMessageResponse {
  conversation_id: string
  content: string | null
  run: Run
  result: AgentResult
  plan_task_id: string | null
}

export type TaskStatus =
  | 'pending'
  | 'active'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface TaskStep {
  id: string
  title: string
  status: 'todo' | 'in_progress' | 'done' | 'blocked'
  note: string | null
}

export interface Task {
  id: string
  title: string
  description: string | null
  goal: string | null
  status: TaskStatus
  priority: string
  constraints: string[]
  state: string[]
  key_facts: string[]
  steps: TaskStep[]
  owner_conversation_id: string
  run_ids: string[]
  created_at: string
  updated_at: string
  completed_at: string | null
  revision: number
}

export interface AgentRunTrace {
  run_id: string
  conversation_id: string | null
  status: string
  started_at: string
  completed_at: string | null
  provider: string | null
  model: string | null
  steps: number
  stop_reason: string | null
  input_tokens: number
  output_tokens: number
  total_tokens: number
  event_count: number
}

export interface AgentEvent {
  event_id: string
  run_id: string
  conversation_id: string | null
  sequence: number
  type: string
  event_time: string
  step: number | null
  provider: string | null
  model: string | null
  message: Message | null
  tool_call: ToolCall | null
  tool_result: {
    tool_call_id: string
    tool_name: string
    success: boolean
    output: string | null
    error: string | null
    duration_ms: number
  } | null
  usage: ModelUsage | null
  stop_reason: string | null
  approval_decision: string | null
  [key: string]: unknown
}

export type AutomationStatus = 'active' | 'paused' | 'completed' | 'cancelled'
export type AutomationKind = 'once' | 'interval' | 'cron'

export interface Schedule {
  kind: AutomationKind
  run_at: string | null
  interval_seconds: number | null
  cron_expr: string | null
  timezone: string
}

export interface Automation {
  id: string
  title: string
  prompt: string
  conversation_id: string | null
  status: AutomationStatus
  schedule: Schedule
  next_run_at: string | null
  last_run_at: string | null
  last_run_id: string | null
  created_at: string
  updated_at: string
}

export type ApprovalStatus = 'pending' | 'approved' | 'denied'

export interface ApprovalRequest {
  id: string
  run_id: string | null
  conversation_id: string | null
  tool_name: string
  tool_call_id: string
  arguments: Record<string, unknown>
  reason: string
  status: ApprovalStatus
  created_at: string
  resolved_at: string | null
}

export interface Health {
  status: string
  provider: string
  model: string
  version: string
}

export type WsMessage =
  | { type: 'agent_event'; data: AgentEvent }
  | { type: 'run_status'; data: { run_id: string; status: string } }
