/** Task API：全部走共享 JSON-RPC WebSocket（Plan Mode V1 最小接口）。 */

import { rpcClient } from '../rpc'
import { RpcMethods } from '../rpc/methods'
import type { Task } from './types'

export async function getTask(taskId: string): Promise<Task> {
  const data = await rpcClient.call<{ task: Task }>(RpcMethods.taskGet, {
    task_id: taskId,
  })
  return data.task
}

export async function planAccept(taskId: string): Promise<Task> {
  const data = await rpcClient.call<{ task: Task }>(RpcMethods.taskPlanAccept, {
    task_id: taskId,
  })
  return data.task
}

export async function planReject(taskId: string): Promise<Task> {
  const data = await rpcClient.call<{ task: Task }>(RpcMethods.taskPlanReject, {
    task_id: taskId,
  })
  return data.task
}
