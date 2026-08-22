/** 与 Vesta Host JSON-RPC 对应的 Desktop 类型。 */

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
  /** 模型思考/推理内容（DeepSeek/Qwen reasoning_content、Anthropic thinking）。 */
  reasoning?: string | null
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
  cached_input_tokens?: number | null
  uncached_input_tokens?: number | null
  cache_read_input_tokens?: number | null
  cache_write_input_tokens?: number | null
  model_calls?: number
}

export interface RunUsageSummary {
  main_agent: ModelUsage
  memory_reflection: ModelUsage
  memory_maintenance: ModelUsage
  provider_total: ModelUsage
  tool_schema_tokens_estimated: number
  memory_reflection_status: string
  memory_reflection_skip_reason: string | null
  main_agent_chargeable_tokens: number
  run_budget_status: string
  run_budget_reason: string | null
  run_budget_warning_tokens: number | null
  run_budget_finalization_tokens: number | null
  run_budget_hard_tokens: number | null
  run_budget_warning_model_calls: number | null
  run_budget_finalization_model_calls: number | null
  run_budget_hard_model_calls: number | null
}

export interface LongTermMemory {
  id: string
  title: string
  summary: string
  content: string
  created_at: string
  updated_at: string
  last_accessed_at: string
  access_count: number
  revision: number
  status: 'active' | 'archived'
  last_update_reason: string | null
  archive_reason: string | null
}

export interface LongTermMemoryOverview {
  core: string
  active: LongTermMemory[]
  archived: LongTermMemory[]
  active_count: number
  max_active: number
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
  delta?: string | null
  /** 流式思考增量（DeepSeek/Qwen reasoning_content delta）。 */
  reasoning_delta?: string | null
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
  /** agent_completed / agent_failed 携带的最终结果（含 usage / steps）。 */
  result?: AgentResult | null
  original_estimated_input_tokens?: number | null
  prepared_input_tokens?: number | null
  estimated_input_tokens?: number | null
  context_trimmed?: boolean | null
  context_window?: number | null
  input_budget?: number | null
  working_input_budget?: number | null
  trigger_tokens?: number | null
  target_tokens?: number | null
  usage_ratio?: number | null
  tool_result_budget_tokens?: number | null
  tool_result_tokens_before?: number | null
  tool_result_tokens_after?: number | null
  tool_schema_tokens?: number | null
  message_tokens_before?: number | null
  message_tokens_after?: number | null
  original_usage_ratio?: number | null
  prepared_usage_ratio?: number | null
  compaction_stage?: string | null
  compacted_tool_results?: number | null
  removed_tool_rounds?: number | null
  reached_target?: boolean | null
  summary_updated?: boolean | null
  summarized_conversation_blocks?: number | null
  summary_error?: string | null
  summary_usage?: ModelUsage | null
  available_skill_count?: number | null
  skill_catalog_tokens?: number | null
  active_skill_names?: string[]
  active_skill_tokens?: number | null
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
  /** 声明式审批落点：desktop（跟随用户注意力，可进浮窗）/ sandbox（进 Chat）。 */
  ui_scope?: 'desktop' | 'sandbox'
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
