/** notification 的 params 类型（复用现有 AgentEvent / Run status）。 */

import type { AgentEvent } from '../api/types'

export interface AgentEventNotificationParams {
  /** JSON-RPC notification：method=agent.event，params=AgentEvent */
  jsonrpc?: '2.0'
  method?: string
  params: AgentEvent
}

export interface RunStatusNotificationParams {
  jsonrpc?: '2.0'
  method?: string
  params: { run_id: string; status: string }
}
