/** 长期记忆只读 API：Desktop 只观察，不绕过 Agent/Harness 写入边界。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'
import type { LongTermMemoryOverview } from './types'

export async function listMemories(): Promise<LongTermMemoryOverview> {
  return rpcClient.call<LongTermMemoryOverview>(RpcMethods.memoryList, {})
}
