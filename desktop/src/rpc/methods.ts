/** RPC method 名常量与参数/结果类型（与 Python methods 一一对应）。 */

export const RpcMethods = {
  systemInfo: 'system.info',
  conversationList: 'conversation.list',
  conversationGet: 'conversation.get',
  conversationCreate: 'conversation.create',
  conversationSend: 'conversation.send',
  runList: 'run.list',
  runGet: 'run.get',
  runCancel: 'run.cancel',
  runRecover: 'run.recover',
  traceGet: 'trace.get',
  automationList: 'automation.list',
  automationGet: 'automation.get',
  automationCreate: 'automation.create',
  automationPause: 'automation.pause',
  automationResume: 'automation.resume',
  automationCancel: 'automation.cancel',
  approvalList: 'approval.list',
  approvalGet: 'approval.get',
  approvalApprove: 'approval.approve',
  approvalDeny: 'approval.deny',
  artifactList: 'artifact.list',
  artifactGet: 'artifact.get',
  taskGet: 'task.get',
  taskPlanAccept: 'task.plan_accept',
  taskPlanReject: 'task.plan_reject',
  computerStatus: 'computer.status',
  computerRequestPermission: 'computer.request_permission',
  computerLatestObservation: 'computer.latest_observation',
} as const

export type RpcMethodName = (typeof RpcMethods)[keyof typeof RpcMethods]

/** JSON-RPC 标准错误码（与 Python 一致）。 */
export const RpcMethodErrorCode = {
  ParseError: -32700,
  InvalidRequest: -32600,
  MethodNotFound: -32601,
  InvalidParams: -32602,
  InternalError: -32603,
  ResourceNotFound: -32000,
  InvalidState: -32001,
} as const
