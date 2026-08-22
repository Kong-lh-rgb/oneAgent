# Vesta 学习记录：层次架构与关键设计

> 本文记录 Vesta 当前已经落地的架构、设计理由和重要边界。
> `docs/task.md` 负责记录每天完成了什么；本文负责解释系统为什么这样设计、
> 各层如何协作，以及后续开发时不能破坏的约束。

## 1. 当前系统定位

Vesta 当前是一个运行在本地终端中的 Tool-Calling Agent，已经具备：

- GPT、Qwen、DeepSeek、Claude 模型适配；
- 多轮 Agent Loop；
- 本地文件、Shell、HTTP 和网页搜索工具；
- 工具 Hook、人工审批和可记忆权限规则；
- SQLite 会话、消息、运行轨迹和权限规则持久化；
- Token 估算、模型上下文预算、工具消息压缩和滚动摘要；
- 会话私有 Task、Run Checkpoint；
- Sparse, Model-Directed 长期记忆（Markdown 文件，模型自主 Recall）；
- 结构化 AgentResult 和 AgentEvent。

Subagent 仍未实现；MCP、Scheduler、Host transport 与 Desktop 已在后续阶段完成，
并继续保持在 Runtime 外围的应用编排或基础设施层。

## 2. 总体分层

```text
交互层
  CLI（app.models.chat）
    │
应用编排层
  AgentRuntime
    ├── ContextManager
    ├── ModelAdapterRegistry
    ├── ToolRegistry / ToolExecutor
    └── AgentEventHandler
    │
领域与策略层
  ├── Context Budget / MessageBlock / ToolReducer
  ├── Tool Hook / PermissionPolicy / ApprovalGate
  └── Provider-neutral Models
    │
基础设施层
  ├── OpenAI / Anthropic SDK Adapter
  ├── Tavily / DuckDuckGo Search Provider
  └── SQLite Conversation / Trace / Permission Store
```

分层的核心原则是：

1. Runtime 只编排，不理解 Provider 私有协议。
2. ContextManager 只生成临时请求上下文，不修改事实历史。
3. ToolExecutor 是不可绕过的工具安全边界。
4. Store 保存事实，不负责 Token 优化或 Agent 决策。
5. Event 只记录结构化事实，观察者故障不能破坏主流程。

## 3. 一次用户请求的数据流

```text
用户输入
  ↓
CLI 加载 SQLite 完整会话历史
  ↓
AgentRuntime 创建当前 Run 和用户 Message
  ↓
ContextManager.prepare()
  ├── 对完整候选请求估算 Token
  ├── 未达到 trigger：原样返回
  └── 达到 trigger：执行 ToolReducer
  ↓
ModelAdapter 把统一 ModelRequest 转成 Provider 协议
  ↓
模型返回普通回答或 ToolCall
  ├── 普通回答：结束
  └── ToolCall：ToolExecutor 执行并生成 ToolResult Message
                    ↓
              加入当前 Run，再次请求模型
  ↓
AgentResult 返回完整原始消息和运行统计
  ↓
CLI 将完整 AgentResult.messages 保存回 SQLite
```

这里同时存在两种消息视图：

- **原始历史**：数据库和 `AgentResult.messages` 使用，完整、可恢复、可审计；
- **请求上下文**：`ContextDecision.messages` 使用，只服务于本次模型请求，
  可以在预算压力下生成压缩副本。

两者不能混用。不能为了节省 Token 把压缩后的消息写回会话数据库。

## 4. 核心数据结构

模型层使用 Provider 无关的数据结构：

- `Message`：system、user、assistant、tool 四种角色；
- `ToolCall`：调用 ID、工具名和参数；
- `ToolDefinition`：模型可见的工具说明与 JSON Schema；
- `ToolResult`：统一成功状态、输出、错误和耗时；
- `ModelRequest`：消息、工具、模型名和生成参数；
- `ModelResponse`：统一回复、停止原因和 Token 用量；
- `ModelUsage`：输入、输出和总 Token。

Agent 层在这些模型之上增加：

- `AgentResult`：最终消息、完整历史、步骤、停止原因、工具轮和错误；
- `ToolRound`：一次模型回复发起的一组工具调用；
- `ToolCallRecord`：一个 ToolCall 与对应 ToolResult；
- `AgentEvent`：运行过程中的不可变事件。

统一数据结构的价值是：Runtime、工具层和存储层不需要知道 OpenAI 或
Anthropic SDK 对象长什么样。

## 5. AgentRuntime 的职责边界

Runtime 负责：

- 建立 Run ID；
- 维护当前 Run 的原始消息列表；
- 解析实际 Provider、Model 和输出上限；
- 把完整候选消息与历史边界交给 ContextManager；
- 调用模型、执行工具、追加 ToolResult；
- 累加 Token、工具轮和事件；
- 执行 `max_steps`、`max_tool_rounds` 和重复调用保护；
- 把错误转换为结构化 AgentResult，而不是让进程直接崩溃。

Runtime 不负责：

- 识别 OpenAI、Qwen 或 Claude 的消息格式；
- 决定具体上下文压缩算法；
- 直接进行人工输入或权限规则匹配；
- 写 SQLite；
- 实现 Task、Memory 或 Subagent。

当前工具调用按顺序执行。未来如果增加并行调用，需要保持同一 assistant
消息中的多个 ToolCall 与全部 ToolResult 的协议完整性。

## 6. 模型适配层

`ModelAdapterRegistry` 根据配置延迟创建 Adapter：

- OpenAI：Responses API 或 Chat Completions；
- Qwen、DeepSeek：OpenAI 兼容接口；
- Claude：Anthropic Messages API。

Runtime 始终传统一 `ModelRequest`。Adapter 负责：

- 转换 system/user/assistant/tool 消息；
- 转换 ToolDefinition；
- 把 Provider 工具调用还原成统一 ToolCall；
- 统一 Token usage 和 finish reason；
- 隔离 SDK 异常。

例如，同一个 ToolResult 在不同 Provider 中会变成：

- Chat Completions：`role=tool + tool_call_id`；
- Responses API：`function_call_output`；
- Anthropic：用户消息中的 `tool_result` content block。

Provider 差异必须停留在 Adapter 内，不能扩散到 AgentRuntime。

## 7. 上下文管理架构

### 7.1 模型能力与预算

上下文预算公式：

```text
input_budget = context_window - reserved_output_tokens - safety_margin_tokens
trigger_tokens = input_budget × 0.80
target_tokens = input_budget × 0.60
```

模型能力查找优先级：

```text
用户覆盖 > 内置精确模型 > Provider 默认 > 保守兜底
```

未知模型不会直接崩溃，而是使用保守窗口并记录 warning。Runtime 实际发送的
`max_output_tokens` 与预算预留必须使用同一个值，否则预算判断会失真。

### 7.2 MessageBlock

`partition_messages()` 是消息结构识别的唯一入口：

- `SystemBlock`：连续系统消息；
- `ConversationBlock`：普通用户与助手对话；
- `ToolRoundBlock`：完整合法的工具调用轮；
- `MalformedToolBlock`：孤立、未完成、重复 ID 或 ID 错配的工具协议。

合法 ToolRoundBlock 必须满足：

1. 第一条是带一个或多个 ToolCall 的 assistant 消息；
2. 每个 ToolCall ID 唯一且非空；
3. 后续只能是 ToolResult 消息；
4. ToolResult ID 集合与 ToolCall ID 集合完全一致。

异常工具协议必须保守保留。错误删除比暂时多消耗 Token 更危险，因为它可能
产生 Provider 无法理解的半截工具协议。

### 7.3 第一层压缩：ToolReducer

ContextManager 的顺序非常重要：

```text
完整候选上下文
  ↓ 第一次估算
低于 trigger ──→ 原样返回，不划块、不调用 Reducer
  ↓ 达到 trigger
划分历史 MessageBlock
  ↓
缩短旧的长 ToolResult
  ↓ 每次修改后重新估算
仍高于 target
  ↓
从最旧开始整体移除未保护 ToolRoundBlock
  ↓ 每移除一轮重新估算
达到 target 立即停止
```

默认策略：

- 保护当前 Run 的所有消息；
- 保护最近 2 个合法历史工具轮；
- ToolResult 超过 8000 字符才允许缩短；
- 默认保留开头 4000 字符和结尾 2000 字符；
- 标记中记录工具名、ToolCall ID、原字符数和省略字符数；
- SystemBlock、ConversationBlock、MalformedToolBlock 不处理。

如果工具层处理完仍高于 target，ContextManager 会继续进入第二层滚动摘要。
只有第二层未配置、失败或仍无法达到 target 时，才返回
`needs_next_compaction_stage=True`。

### 7.4 ContextDecision 语义

- `original_estimated_input_tokens`：完整候选上下文的估算；
- `prepared_input_tokens`：最终请求上下文的估算；
- `estimated_input_tokens`：兼容字段，与 prepared 相同；
- `requires_compaction`：原始估算达到 trigger；
- `trimmed`：最终请求消息确实发生变化；
- `reached_target`：最终估算不高于 target；
- `needs_next_compaction_stage`：已执行当前可用压缩层，但仍未达到 target；
- `exceeds_input_budget`：最终 prepared 仍超过硬预算；
- `compacted_tool_results`：实际缩短的 ToolResult 数；
- `removed_tool_rounds`：实际整体移除的 ToolRoundBlock 数；
- `compaction_stage`：none、工具结果、工具轮、滚动摘要或工具层与摘要组合。

`exceeds_input_budget` 必须基于 prepared 计算。只有最终 prepared 仍超限时，
Runtime 才返回 `CONTEXT_ERROR` 并禁止调用 Provider。

### 7.5 第二层：滚动结构化摘要

当前阶段不维护 WorkingContextLedger。Ledger 更接近长期工作记忆，需要事实来源、
更新规则、冲突处理和召回策略，应在未来 Memory 层统一设计。上下文管理只解决
“本次模型请求如何在窗口内保留足够历史”这一件事。

当工具层处理后仍高于 target，`ConversationReducer` 执行第二层压缩：

```text
完整 SQLite 历史
  ↓ 复用已有滚动摘要
模型请求候选
  ↓ ToolReducer
仍高于 target
  ↓ ConversationReducer + ContextSummarizer
旧摘要 + 新增旧对话 → 新结构化摘要
  ↓
摘要消息 + 近期原文 + 当前 Run
```

`RollingConversationSummary` 包含当前目标、用户约束、关键决定、已完成工作、
当前状态、未完成事项和重要事实。`ConversationSummaryState` 只额外记录
`covered_message_count`，表示摘要已覆盖的原始历史前缀。下一次压缩把旧摘要与
新增的较早对话合并，从而滚动前进，不需要维护一套独立工作台账。

安全边界：

1. SQLite messages 和 AgentResult.messages 始终保存完整原始历史；
2. 摘要仅是可重建的模型请求缓存，不是事实数据库或长期 Memory；
3. 主系统提示、当前 Run、最近普通对话、最近工具轮和异常工具协议受保护；
4. 摘要以带明确数据边界的受控 system message 注入，不能覆盖主系统提示；
5. 摘要模型只能合并输入中已有信息，使用严格 JSON 输出且不允许调用工具；
6. 摘要调用失败、覆盖位置失效或摘要未缩短请求时，原消息保持不变；
7. 摘要模型的 Token 用量计入 AgentResult，并通过 AgentEvent/Trace 可观测。

## 8. 工具系统架构

### 8.1 注册与执行

`ToolRegistry` 保存工具实例并向模型暴露允许的 ToolDefinition。
`ToolExecutor` 提供统一执行边界：

- 参数必须是 JSON object；
- 异步超时；
- 工具不存在、参数错误和执行异常统一转成 ToolResult；
- 输出最多保留 20000 字符；
- 记录耗时和执行观测；
- 不使用同步方式运行异步工具。

### 8.2 Hook 生命周期

```text
before_execute
  ↓
on_approval_required
  ↓
on_approval_completed
  ↓
执行工具
  ↓
after_execute
```

当前 Hook：

- `PermissionHook`：控制是否允许执行；
- `ObservabilityHook`：记录执行结果；
- `AgentEventHook`：把工具生命周期转换成 AgentEvent。

控制型 Hook 是安全关键组件，失败时必须 fail-closed。观察型 Hook 失败不能
改变工具授权或执行结果。

### 8.3 权限与审批

工具权限：

- `ALLOWED`：直接执行；
- `HUMAN_APPROVAL`：命中允许规则或人工批准后执行；
- `FORBIDDEN`：不向模型暴露，直接调用也拒绝。

审批范围：

- `ONCE`：仅本次；
- `RUN`：当前 Run 内完全相同参数；
- `CONVERSATION`：当前会话内完全相同参数。

规则使用完整参数精确匹配，避免把一次安全命令扩大成危险命令前缀。冲突时
优先级是 DENY > ASK > ALLOW，并优先更具体的 Run 规则。

## 9. 内置工具与安全边界

- `list_files`：最多返回 200 个 workspace 文件；
- `read_file`：只读取 workspace 内 UTF-8 文本；
- `write_file`：只写 workspace，自动创建父目录；
- `run_shell_command`：限制工作目录和超时，需要人工审批；
- `http_request`：限制方法、响应大小并防御 SSRF，需要人工审批；
- `web_search`：只读，无需审批。

文件工具统一通过安全路径解析，阻止 `../`、绝对路径逃逸和符号链接越界。

搜索层独立于模型：有 Tavily Key 时优先 Tavily，可恢复错误回退 DuckDuckGo；
鉴权错误不静默回退，避免错误配置长期被掩盖。

## 10. 持久化与可观测性

默认 SQLite 数据库同时保存：

- conversations / messages：完整会话事实；
- agent_runs / agent_events：运行摘要和完整事件；
- permission_rules：会话或临时权限规则。
- conversation_summaries：模型请求使用的滚动摘要缓存和覆盖位置。

Trace 事件包含 Run ID、conversation ID、序号、UTC 时间、模型步骤、工具结果、
Token 和压缩统计，但不应该记录 API Key 等秘密。

事件观察者相互隔离。Trace 写入失败不能让模型与工具主流程崩溃。

当前会话更新采用完整 `replace_messages()`，实现简单且保证顺序一致；当历史量
显著增大后，可以改为带 sequence 的增量追加，但不能牺牲完整历史语义。

## 11. 错误与停止原因

当前主要停止原因：

- `FINAL_ANSWER`
- `CONTEXT_ERROR`
- `MODEL_ERROR`
- `REPEATED_TOOL_CALL`
- `MAX_STEPS`

模型错误、上下文错误和工具错误需要保持区分：

- 模型或 Adapter 调用失败是 Model Error；
- 上下文准备或窗口溢出是 Context Error；
- 工具失败通常作为 ToolResult 返回模型，由模型决定如何向用户说明。

## 12. 当前上下文压缩层次

```text
第一层：工具结果缩短与旧工具轮移除       已完成
第二层：ConversationBlock 滚动结构化摘要  已完成
第三层：Memory / Artifact / 检索召回      未实现
```

未来 Memory 层不能直接写进 ToolReducer，也不能修改 SQLite 原始历史。上下文摘要
与长期记忆必须保持独立：前者服务窗口预算，后者服务跨会话事实保存和按需召回。

## 13. 任务层（Task）

### 13.1 解决的问题

长任务的目标、约束、进度与待办如果只存在对话里，会在上下文压缩（工具结果
缩短、旧工具轮移除、滚动摘要）时丢失或变模糊，且对话无法编程查询“任务做到
哪了”。

### 13.2 设计边界

- Task 是任务事实的权威源，独立于会话消息持久化；
- 对话降级为任务的执行日志，压缩只影响日志的紧凑表达；
- 显式状态源：任务状态由上层显式写入（Agent/用户/未来规划器），不自动从对话
  猜测，避免幻觉污染事实。

### 13.3 数据模型与生命周期

- `Task`：goal / status / priority / constraints / state / key_facts / steps
  / owner_conversation_id / run_ids / revision / 时间戳；owner 创建后不可变；
- `TaskStep` 状态：todo / in_progress / done / blocked；
- Task 生命周期状态：pending / active / paused / completed / failed /
  cancelled；普通 `task_update` 不允许恢复终态，终态记录 completed_at；
- 持久化：`FileTaskStore`——每个任务一个 `<id>.json` 放在 `tasks/` 目录
  （默认 `backend/.vesta/tasks/`），缩进 JSON 便于人工查看与版本管理；
  临时文件 + 原子替换写入，损坏文件在 list 中跳过。任务不写入 SQLite，与会话
  数据库分离。

### 13.4 与上下文压缩的关系

Task 文件是任务事实源，注入的 Task 快照只是当前模型请求视图，不进入
`AgentResult.messages` 或 SQLite 消息历史，因此上下文压缩不会破坏任务事实。

当前运行闭环：

```text
用户消息
  ↓ 模型判断：明确要求记录 / 复杂多步骤 / 长期跟踪
task_create
  ↓ ToolExecutionContext 自动绑定 conversation_id + run_id
tasks/<task_id>.json
  ↓ 下一模型步骤重新加载
TaskContextProvider 注入当前活动 Task 快照
  ↓
模型执行步骤或根据实际情况调整计划
  ↓
task_update（原子 TaskPatch + revision 检查）
```

同一会话存在多个非终态 Task 时，最近更新的 Task 作为当前活动任务。Task 完成、
失败或取消后不再自动注入，但仍可通过 `task_get` / `task_list` 查询。

后续增强：

- CLI `/task` 命令（用户视角创建/推进/查询）；
- Task 与 Memory 边界：短期工作记忆（摘要）服务窗口预算，长期事实（Task/
  Memory）服务跨会话恢复与按需召回。

### 13.5 模型可用工具

Task 领域通过 4 个工具暴露给主模型（`app/task/tools.py`），工具持有共享的
`FileTaskStore`，权限为 ALLOWED（状态管理不涉危险操作）：

- `task_create`：工作复杂需跟踪进度、用户提出多个工作、或用户要求时创建任务；
- `task_update`：步骤完成（step_id + step_status 成对）、状态变化、替换目标/
  状态、追加约束/事实、动态替换步骤计划；会话与 run 由系统自动关联；
- `task_get`：按 ID/前缀获取单个任务完整详情，模型重新确认当前状态；
- `task_list`：按状态过滤列出**当前会话**的任务（精简进度摘要），总览或用户
  明确要求时调用。

设计意图：主模型自主判断何时创建、更新、查询任务，任务状态成为模型可编程
访问的权威源，不再依赖“从对话里翻找进度”。任务按会话默认隔离，跨会话不可
见、不可更新。

### 13.6 写入一致性与安全边界

- Task ID 固定为 32 位十六进制，短 ID 查询只接受十六进制前缀；
- 文件路径必须停留在 tasks 目录，符号链接任务文件不读取、不更新；
- `TaskPatch` 先完成全部参数校验，再一次性更新，避免工具失败但部分字段已落盘；
- 单进程内按 task_id 加锁，避免并发更新相互覆盖；每次成功更新递增 revision；
- 模型可携带 expected_revision，基于旧快照更新时拒绝覆盖新版本；
- 临时文件使用唯一名称，写入后 flush/fsync，再通过 os.replace 原子替换；
- owner_conversation_id 和 run_id 来自 ToolExecutionContext，不接受模型猜测；
- 任务按会话隔离：`task_list`/`task_get`/`task_update` 只操作属于当前会话
  （`owner_conversation_id` 等于当前 conversation_id）的任务；其他会话统一按
  “任务不存在”处理以隐藏存在性；缺少会话上下文时模型工具直接拒绝执行；
- ID 前缀先在当前 owner 的任务集合中过滤，再判断唯一性，其他会话的相同前缀
  不会造成歧义；旧 `conversation_ids` 仅含一个值时自动迁移，为空或多个时禁止访问；
- Task/TaskStep 更新后统一重新校验：步骤 ID 唯一、最多一个 in_progress、paused
  不含 in_progress、completed 的步骤全部 done，时间统一为 UTC；
- 步骤状态需留依据：把步骤标记为 done 时必须同时提供非空 step_note（完成依据），
  标记为 blocked 时必须提供非空 step_note（阻塞原因）。系统不校验内容真假，只
  强制留痕；in_progress/todo 不强制。步骤 blocked（等待外部输入）
  时任务可置为 paused，使恢复时模型明确知道在等什么；
- done 步骤不可回退；整体重排不得删除或回退 done/in_progress；整体 steps 更新与
  单步骤更新互斥；completed/failed/cancelled 不可通过普通更新恢复；
- 损坏或超限文件不参与任务列表，并记录可观察 warning。

## 14. Run Checkpoint：中断边界与恢复证据

### 14.1 为什么 Trace 不能直接等于 Checkpoint

Trace 是观察层，记录“发生过什么”。事件处理器失败不能改变 Agent 的业务结果，
因此 Runtime 会隔离 Trace 异常。Checkpoint 是恢复正确性的一部分，必须由 Runtime
在关键边界直接写入，回答“最后确认停在哪里”。两者可以保存在同一个 SQLite
文件，但不能共享失败语义。

### 14.2 最小模型

```text
RunCheckpoint
├── run_id / conversation_id
├── user_message
├── status: running / completed / failed / interrupted
├── phase: starting / model_request / tool_execution
│          / tool_results_ready / finished
├── step
├── pending_tool_calls
├── completed_tool_results
├── stop_reason / error
├── started_at / updated_at / completed_at
├── recovered_by_run_id
└── revision
```

Checkpoint 不复制完整会话；完整历史仍属于 Conversation。保存 user_message 是因为
CLI 只会在 Run 结束后整体写回消息，中断时本轮用户请求可能尚未进入聊天历史。

### 14.3 Runtime 写入时序

```text
start(running, starting, user_message)
  ↓
before_model(step, model_request)
  ↓ 模型返回 ToolCall
before_tools(tool_execution, pending=[...])
  ↓ 每个工具获得统一 ToolResult
complete_tool(pending 移除, completed 追加)
  ↓ pending 清空
tool_results_ready
  ↓ 下一轮模型或最终回答
completed / failed
```

Runtime 被取消时保留当前 phase、pending 和 completed，并把状态改为 interrupted。
进程被强制结束来不及写 interrupted 时，下一次 CLI 启动或切换会话会把遗留
running 转成 interrupted。

### 14.4 “pending”不是“failed”

工具产生副作用与结果落盘无法成为一个跨系统原子事务。若工具已经写完文件，但
Checkpoint 尚未收到 ToolResult 就断电，唯一诚实的状态是“结果未知”。因此：

- pending ToolCall 表示必须核对现场，不能推断成功或失败；
- completed ToolResult 表示 Runtime 已确认收到统一结果；
- 副作用工具禁止自动重试；先查 Trace、文件、外部 API 或幂等键；
- 安全只读工具可以由恢复策略判断后重试，但 V1 不做自动续跑。

### 14.5 恢复上下文

同一会话下一次 Run 会读取最近未恢复的 interrupted Checkpoint，把原始用户请求、
未决工具和已确认结果渲染成临时 system message。它参与模型上下文预算，但不进入
`AgentResult.messages` 和 SQLite 聊天历史。只有后续 Run 正常完成，旧记录才写入
`recovered_by_run_id`；后续 Run 再次失败时仍保留恢复证据。

Task 与 Checkpoint 的边界：Task 保存已确认的业务进度，Checkpoint 保存一次 Run
最后确认的执行边界，Trace 保存详细时间线。Checkpoint V1 只帮助安全核对，不尝试
从 Python 调用栈中间继续，也不自动重放工具。

## 15. 关键工程教训

1. **先测量，再压缩。** 低于 trigger 时删除任何历史都是不必要的信息损失。
2. **事实历史与请求视图分离。** Token 优化不能破坏恢复和审计能力。
3. **按协议块操作。** ToolCall 与 ToolResult 不能按单条消息随意拆分。
4. **异常协议保守保留。** 不确定能否安全删除时，选择保留并上报下一层需求。
5. **预算使用真实请求参数。** 模型、工具定义和 max output 都必须参与估算。
6. **每次有损操作后重新估算。** 达到 target 就停止，避免过度压缩。
7. **权限检查必须不可绕过。** CLI、Runtime 或未来 API 都应经过 ToolExecutor。
8. **观察逻辑不能控制业务。** Trace 和日志故障不应改变授权与执行结果。
9. **Provider 特例留在 Adapter。** Runtime 保持模型无关，才能持续扩展模型。

## 15. 测试与阅读入口

建议按以下顺序阅读代码：

1. `app/models/types.py`
2. `app/agent/runtime.py`
3. `app/context/manager.py`
4. `app/context/blocks.py`
5. `app/context/reducers/tool.py`
6. `app/context/reducers/conversation.py`
7. `app/context/summarizer.py`
8. `app/tools/executor.py`
9. `app/tools/permission_hook.py`
10. `app/task/models.py`
11. `app/task/store.py`
12. `app/task/context.py`
13. `app/task/tools.py`
14. `app/conversation/store.py`
15. `app/trace/store.py`
16. `app/models/chat.py`

离线验证命令：

```bash
cd backend
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check app tests
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m app.models.chat --help
```

涉及上下文改动时，至少检查：

- 低于 trigger 是否完整保留；
- 原始消息是否未修改；
- 当前 Run 是否受保护；
- 多 ToolCall 协议是否完整；
- 每次缩短或移除后是否重估；
- 最终超预算时 Provider 是否未调用；
- AgentResult 和 SQLite 是否仍保存完整历史。

## 16. Eval Harness：测量状态变化而不是最终存在性

Agent 测评同时包含两类证据：模型回答属于非确定性文本，工具结果、Task、文件和
事件属于可直接验证的系统事实。评分应先检查系统事实，再用回答关键点验证模型是否
如实解释结果，不能只依靠语言相似度判断任务完成。

预置状态必须建立运行前快照。例如场景已有一个 Task 时，`created: false` 表示本轮
新增数量为零，而不是运行后没有 Task。检查具体任务也不能依赖 `tasks[0]`，应使用
场景 alias 或唯一新增对象定位。这个原则同样适用于未来的 Memory、Checkpoint 和
文件变更测评：比较 before/after，明确新增、修改、删除分别是什么。

测评指标必须区分 passed、failed 和 skipped。没有声明工具期望的问答场景不能进入
工具准确率分母，没有 Task 期望的场景也不能提高 Task 正确率。多次运行时还要区分
唯一场景数和运行样本数，避免把随机采样数量误写成场景覆盖数量。

上下文压缩不能只检查“达到触发线”。真正的压缩证据至少包括非 none 的压缩阶段、
请求上下文发生变化以及压缩后的核心目标仍被保留。Eval Harness 对压缩场景使用与
生产 CLI 相同的 ConversationReducer 和 ModelContextSummarizer，但它仍是 Runtime
级测评，不等于完整的会话持久化、Checkpoint 恢复和终端交互测评。

## 17. reasoning 模型与严格 JSON 摘要的适配

`deepseek-v4-flash` 是 reasoning 模型：输出预算先被思考 tokens 消耗。做严格 JSON
摘要时，只要 `max_output_tokens` 小于本次思考消耗，content 就为空，摘要组件表现为
“压缩失败 / 不稳定”。

关键实测结论：
- 空 content 是概率性的：同一输入时而成功时而失败；输入越大思考越多越容易空，
  并非“真实大上下文会自动消失”。
- 关闭思考（chat completions 的 `extra_body={"thinking":{"type":"disabled"}}`）
  是确定性解法：1024 预算下稳定输出；主 agent 可保留 reasoning。
- 关闭思考后模型倾向“全量输出”，摘要会变长 → 需配合紧凑约束（数组 ≤5 条、
  每条 ≤80 字）把摘要压到 ~400 token。
- 模型对 prompt 指令（如“必须短于输入”）是软约束，偶发不遵守 → 对确定性要求
  高的路径要加重试 / 校验兜底，不要假定模型一定遵守。
- 小输出预算下 reasoning 主 agent 也可能空 content → 主 agent 预算要 ≥4096
  （或按需关思考），场景配置应贴近真实运行配置，而不是随意缩小。

工程启示：reasoning 模型的“稳定输出”取决于 thinking 消耗与预算的余量，不要把
“换模型 / 关思考 / 加预算”看成互斥替代，而是按需求组合：结构化摘要这类任务优先
关思考；主 agent 深度推理保留 reasoning 但要给足预算。

## 18. 长期记忆：Sparse, Model-Directed Long-Term Memory

长期记忆的目标不是做一个 RAG 知识库，而是一个稀疏、增长缓慢的记忆系统。Main
Agent 决定何时回忆和处理显式 Core 变更；独立的 Post-Run Reflector 判断普通记忆的
创建与更新。

### 18.1 核心原则

> Default is forget. Memory is the exception.

只有未来跨 Session 仍明显有价值的信息才进入长期记忆。Runtime 不再根据当前
query 做关键词 / 向量检索并注入 Top-K Memory；它只负责加载 Core Memory 与
Memory Index、暴露语义工具、维护元数据、执行容量管理。

### 18.2 分层与文件布局

```text
.vesta/memory/
├── CORE.md           每次 Run 注入 System Prompt，仅身份/稳定偏好/长期约束
├── INDEX.md          Memory Index（Recall Cue 投影，自动重建）
├── active/Mxxx.md    普通长期记忆（最多 25 条，Markdown + Front Matter）
└── archive/          归档记忆，不进 Index、不进上下文
```

- Core Memory：<= 2000 tokens，不参与淘汰，只有用户明确长期信息时受控更新；
- 普通 Memory：每个带 Front Matter（id/title/summary/时间戳/access_count/status），
  正文按 ## Summary / ## Memory 组织；
- INDEX.md 是 Memory Store 的 projection：每次 create/update/archive 后自动重建，
  只含 id + title + Cue，不保存完整正文。

### 18.3 Main Agent 的语义工具

- `memory_read(id)`：读取完整记忆，自动 access_count+1 / last_accessed_at；
- `memory_list()`：返回 active 记忆的 id/title/summary（Recall Cue），不含正文；
- `core_memory_update(key, value, reason, explicit_user_statement)`：模型判断信息应常驻 Core，Harness 验证当前用户原话并按 key 更新；
- `core_memory_remove(key, reason, explicit_user_statement)`：用户明确撤销 Core 信息时按 key 移除。

`memory_create/update/archive` 的类和 Manager API 仍保留，但不注册到 Main Agent 的默认
Tool Registry。普通记忆写入由 Post-Run Reflector 通过 Manager 完成。

### 18.4 Model-directed recall

```text
System Prompt = Core Memory + Memory Index + Memory Policy
        ↓
    模型判断是否需要过去信息
        ├─ 不需要 → 继续
        └─ 需要 → memory_read(Mxxx)
```

外部系统提供 Recall Cue，模型决定何时真正 Recall。普通记忆完整正文绝不自动注入。

### 18.5 容量维护

active 数 > 25 时触发 Maintenance：启发式（最近使用时间 + 使用次数 + 最近更新时间）
只负责选出 3~5 个候选，最终 KEEP / MERGE / ARCHIVE 由模型决定。

### 18.6 边界

- 当前任务状态属于 Task，可复用流程属于 Skills，都不应写入 Memory；
- 不使用 SQLite / FTS / Embedding / Vector Search / Knowledge Graph / 自动 Top-K；
- Memory 与 Task / Skill 严格区分：Task 回答“当前正在做什么”，Memory 回答“关于用户和
  过去未来还应知道什么”，Skill 回答“以后遇到这种任务应该怎么做”。

### 18.7 Markdown Store 的一致性边界

模型负责 recall、Core 归属和 Reflection 的普通记忆语义判断，但文件系统不变量仍属于
Harness。所有 Memory ID 必须先按 `M` 加至少三位数字校验，不能直接拼成路径；active 目录只能存在
status=active 且 Front Matter ID 与文件名一致的记录，archived 记录不能通过普通 update
重新进入 active。模型提供的 update/archive reason 必须写入 Front Matter，不能只在
一次 ToolCall 参数中短暂出现。

INDEX 是 active Store 的 projection，因此不能只依赖每次 mutation 后的增量重建。进程
可能在 Memory 文件成功写入、INDEX 更新前中断，用户也可能直接编辑 Markdown。Manager
初始化时应重新扫描 active 并完整 rebuild INDEX；正常 create/update/archive 则在统一锁
内修改 Store 和重建投影。这样 INDEX 可丢弃、可重建，不会成为第二份权威事实。

归档涉及“修改 Front Matter 状态”和“移动目录”两个动作。实现先原子替换源文件内容，
再用同文件系统 `os.replace` 原子移动；初始化时识别 active/ 中 status=archived 的短暂
中断状态并完成移动。并发创建、读取计数和更新也由 MemoryManager 串行化，避免两个
create 同时分配 M001，或 read 的访问计数覆盖 update 的正文。

文件可写不代表模型可完整读回。ToolExecutor 对统一 ToolResult 有 20000 字符上限，因此
普通 Memory 正文限制为 12000 字符，并另外限制 title/summary 长度；否则系统可能成功
保存 512KB 文件，却在每次 memory_read 时固定截断，形成模型无法维护的半可见记忆。

容量维护存在一个不可消除的边界：算法不能自动删除，最终 KEEP/MERGE/ARCHIVE 又交给
模型，因此模型出错或 Run 在 max_steps 处停止时，active 可能暂时大于 25。当前 Policy
Post-Run Reflector 在第 26 条 CREATE 后复用现有 Maintenance 计算并记录 candidates，但
V1 不执行 ARCHIVE。若未来要求 25 是任何时刻都不可突破的硬不变量，就必须增加独立
Maintenance 执行者，或改成“满额时先维护、再创建”；不能在不改变现有语义的情况下
同时保证无条件自动收敛。

### 18.8 Core Memory：模型决定层级，Harness 约束写入

Core 与普通 Memory 的归属是语义判断，应该由模型通过选择不同工具表达：主题相关的
历史背景进入普通 Memory；每次 Run 都必须知道的稳定身份、全局长期偏好和跨任务约束
进入 `core_memory_update`。Harness 不重新用关键词分类，但也不向模型开放整份 CORE.md
覆盖能力，而是按稳定小写点分 key 执行 upsert。

Core 更新必须携带 `explicit_user_statement`，并逐字出现在本轮 Runtime 保存的原始
`user_input` 中。这个校验不能证明模型对语义的理解一定正确，但可以证明证据确实来自
当前用户，而不是旧历史、Assistant 回答、工具输出或模型自己的推断。key/value/reason、
用户原话和时间写入 CORE.md Front Matter 供审计；实际 System Prompt 只注入生成后的
可见 Core 正文，避免审计元数据消耗常驻 Token。

按 key 更新同时解决了整文件覆盖问题：修改 `communication.language` 不会删除
`code.comment_language`，首次结构化更新也会保留用户原先手写的 Core Markdown。
Harness 在合并完成后对最终可见正文重新执行 2000 Token 校验，只有校验通过才原子替换
CORE.md；失败时原文件保持不变。同一 Run 通过 ToolResult 知道写入结果，后续 Run 再把
新 Core 作为临时 System Message 注入，不写入聊天原始历史。

### 18.9 Post-Run Reflection：把“完成任务”和“沉淀过去”拆开

主 Agent 的 step loop 只负责完成当前请求、按需 `memory_read`，以及处理用户本轮明确
表达的 Core 更新或撤销。只有 Run 以 `FINAL_ANSWER` 正常结束并完成 checkpoint 后，
Runtime 才同步调用 Post-Run Reflector。MODEL_ERROR、CONTEXT_ERROR、MAX_STEPS、重复
工具调用、中断和未完成 Run 都不会沉淀普通长期记忆。

Reflector 是独立模型调用，不拥有业务工具，也不继续回答用户。它看到的是有界的本轮
视图：当前用户输入、最终回答、截断后的工具调用/结果、Core、Index 与当前会话 Task
快照。它必须返回严格的单动作 JSON：`none`、`create` 或 `update`。V1 不允许一次 Run
生成多条 mutation，也不让 Reflector 修改 Core、Task、Skill 或主动 archive。

结构化决策仍是不可信输入。Pydantic 禁止额外字段，并按动作校验必填/禁填组合；通过
后只能调用既有 `MemoryManager.create/update`，不能直接写 Markdown。这样语义自主权在
模型，ID、长度、状态、原子写入和 INDEX projection 等不变量仍由 Harness 保证。

Reflection 可用 `MEMORY_REFLECTION_PROVIDER/MODEL` 选择独立廉价模型，并单独限制
temperature、max output、timeout 和工具上下文大小；留空时才回退主模型。provider
错误、超时、空响应和非法 JSON 只产生 Reflection failed event，已经成功的 AgentResult
保持成功。同步 V1 会增加 final answer 的可见延迟，但保证下一轮开始前一定看到更新后的
INDEX；未来若改后台队列，需要重新处理“一致性”和“低延迟”的取舍。

Post-Run 仍属于同一个 Run 生命周期，所以 `AGENT_COMPLETED/FAILED` 必须是最后一个事件，
不能在 Reflection 之前提前宣告终止。最终顺序是：Agent Loop 产出 Result、checkpoint
落盘、Reflection 完成或跳过、发射 terminal event、`run()` 返回。这样 CLI 不会先打印
“完成”再继续工作，Trace 的 `completed_at` 也覆盖完整同步生命周期。

UPDATE 比 CREATE 更危险，因为错误替换会丢失原正文。Prompt 中“只有掌握完整旧记忆才
更新”不是可靠不变量；Runtime 必须从当前 Run 成功且 `found=true` 的 `memory.read`
ToolResult 生成 `recalled_memory_ids`。Reflector 只能更新该集合中的 ID，仅看到 INDEX cue
时即使模型输出合法 UPDATE JSON，Harness 仍拒绝 mutation 并保留原文件。

主任务模型和 Reflection 模型也必须在观测层分开。每条 Reflection event 自己保存
provider、model、usage、latency 和 error，供 Memory Eval 分析；但 `agent_runs` 汇总表的
provider/model/Token 只由 Main Agent 生命周期事件更新。否则最后运行的小模型会覆盖
主模型身份，Reflection Token 较大时还会篡改主任务用量，造成成本和质量分析失真。

## 19. 模型输出属于不可信输入：摘要稳定性收口

摘要模型即使返回合法 JSON，也不代表该输出适合替换原历史。Prompt 中“建议 5 条、
每条 80 字、必须更短”包含软目标，因此生成结果还要经过代码级安全上限校验，并在替换后
使用实际请求估算器重新计算。任何校验失败都不能删除原消息。

摘要失败允许一次受控重试：第一次为空、JSON/Schema/长度不合法，或替换后没有缩短
请求时，第二次提示会携带精简失败原因并要求更紧凑。重试仍失败后保留完整历史；若
完整历史超过输入预算，返回 `context_error`。重试次数固定为一次，避免压缩本身形成
新的 Agent Loop 和 Token 放大。

重试产生的 Token 也是事实成本。第一次响应即使不可用，其 usage 仍应与第二次成功
响应相加并进入 `AgentResult.usage`。Eval 失败报告也必须展示 input budget、trigger、
target 和 `summary_error`，让“没触发、生成失败、不够短、压缩后仍超预算”可以一次
区分。

CLI 参数默认值不应覆盖 Provider 配置。用户未传 `--max-output-tokens` 时，Runtime
使用当前 Provider 的 `default_max_output_tokens`；只有显式参数才覆盖。这样 reasoning
模型可以保留足够输出预算，非 reasoning 模型也不必被全局硬编码绑死。

## 20. Memory V1（历史）：候选、混合召回与使用后学习

> 本章记录已被 18 章 Sparse, Model-Directed 长期记忆替换的旧架构（SQLite + FTS5
> + sqlite-vec + RRF），保留作为设计演进参考。

Memory 与聊天历史、Task、Checkpoint 的职责不同：聊天历史记录完整交流，Task 保存
长任务进度，Checkpoint 保存一次 Run 的可恢复边界，Memory 只保存跨会话仍可能改变
未来决策的稳定事实、历史经验和操作方法。因此系统保存 FACT、EPISODE、PROCEDURE，
而不是把全部 Conversation 做 embedding。

LLM 提取结果属于不可信输入，所有自动提取统一进入 candidate。Candidate 不进入正常
上下文；它必须经过用户 confirm 或真实任务 learn_from_use 才能
晋升。confirmation_count 和 use_count 分开记录，避免把“模型用过一次”伪装成“用户
确认过”。写入预算固定为每 Run 3 条、每 Session 5 条、每天 20 条，Memory 污染比少记
一条更危险。

SQLite 中有三个同步结构：memories 是权威事实，FTS5 保存全文索引，sqlite-vec 的
vec0 表保存 float32 embedding。写入和冲突替代在同一个事务中更新三者；检索时 BM25
擅长项目名、错误码和精确词，Vector 擅长同义表达，RRF 用 `1/(60+rank)` 合并两个
榜单。最终结果继续接受 ContextManager 的 Token 预算，而不是绕过上下文管理直接塞满
Prompt。

namespace 是检索隔离边界，可以是 global、user:local、project:vesta 或未来的
task:id。source session/run/message 只是“为什么知道”的证据锚点，不限制记忆跨会话
生效。FACT 的 namespace + key 表达当前事实槽位；新事实出现时旧记录进入 superseded，
新记录通过 supersedes_id 指向旧记录，历史不会被 DELETE。

Runtime 只依赖 MemoryManager 的 retrieve/observe，不了解 fingerprint、FTS5、vec0 或
RRF。召回结果作为临时 system message 进入实际模型请求，但不进入 AgentResult 和聊天
数据库。Memory 检索或观察失败会被隔离，不能让主 Agent 因辅助能力不可用而停止。

sqlite-vec 是 pre-v1 依赖，因此固定版本并在初始化时验证 vec_version 和 embedding
维度。Embedding 通过独立接口隔离：离线测试使用 HashMemoryEmbedder，生产使用独立的
OpenAI 兼容 Embeddings API。Embedding 模型或维度变化不能直接复用旧向量表，后续需要
显式 reindex/migration，而不是静默混用不同向量空间。

## 20. Memory 管理面：模型使用权和用户治理权分离

长期记忆不能只有自动写入和自动召回，还必须提供人类可检查的治理入口。当前 CLI 将
管理能力分为 list、get、confirm 和 archive：默认列表同时展示 candidate 与 active，
让待确认项不会悄悄堆积；superseded 和 archived 只有显式请求 `all` 或对应状态时出现，
避免历史记录干扰日常管理。

ID 前缀解析同样属于安全边界。Store 先按 CLI 配置允许的 namespace 过滤，再判断前缀
是否唯一，因此不在管理范围内的记忆既不会制造歧义，也不会通过“前缀不唯一”泄露存在
性。确认和归档先读取当前对象，再携带 revision 写入；列表看到的旧状态不能静默覆盖
并发更新。

状态转换必须放在 Store 领域路径，而不能只依赖命令参数校验。candidate 才能 confirm，
candidate/active 才能 archive，superseded 与 archived 不允许通过普通管理入口恢复。
这保证未来加入 API、前端或 Memory Tool 时，换一个入口也不能绕过生命周期。

当前模型只有 Memory 的召回使用权和受控观察写入权，没有 confirm/archive 治理权。
用户通过 CLI 掌握最终确认和退出检索的权限。若未来增加 Memory Tool，应首先区分只读
工具与状态修改工具，并让确认/归档进入人工审批，而不是简单暴露 Manager 全部方法。

## 21. Memory 稳定性：在检索阶段隔离，在回答之后学习

namespace 和 status 过滤必须发生在 KNN 内部，而不是全库 Top K 之后。后过滤虽然不会
返回越界数据，但其他项目或 archived 记忆会占用有限候选名额，造成合法记忆漏召回。
vec0 因此把 namespace 设为 partition key、status 设为 metadata；多 namespace 查询分别
执行受约束 KNN，再按 cosine distance 合并。旧无过滤列向量表通过初始化迁移无损升级。

关键词无法可靠证明“用户明确授权永久记住”。反问、引用和否定都可能包含“必须”或
“记住”，所以自动 Extractor 一律只产生 Candidate。Active 是治理决定，只能来自用户
confirm 或系统能够证明的真实使用。这个规则牺牲少量自动化，换取长期事实库不被模型
猜测污染。

namespace 也不交给模型自由生成。MemoryNamespaceRouter 只在配置允许的集合内选择：
项目、仓库和代码相关观察进入 project namespace，其余回到默认 user namespace。
未来接入活动 Task 时可以增加可信 task:id 上下文，但仍应由 Runtime 提供，而非 Prompt
输出任意作用域。

Memory Observe 属于回答后的学习，不应成为生成回答的关键路径。Runtime 在最终回答时
提交受管理后台任务，立即完成 Agent Run；交互循环继续运行，进程退出时统一 drain。
检索和观察通过 AgentEvent 记录开始、完成、失败、耗时、动作、Memory ID 和模型 usage，
因此后台失败仍可在 Trace 中审计，而不是静默消失。

Memory Eval 同时检查相关性和边界：Recall@K/MRR 衡量是否想起正确内容，namespace 与
inactive violation 衡量是否想起了不该出现的内容。长期记忆质量不能只看“搜到一条”，
必须同时证明没有跨作用域污染、没有把 Candidate/Archived 注入模型。

## 22. Memory V1 冻结：安全约束不能替代 Agent 自主性

Memory V1 的底层存储实验有效，但上层控制哲学不适合本地超级助理。主模型只能被动
读取 Runtime 固定召回的少量记忆；写入则依赖回答后的独立 Extractor，模型不能在任务
过程中主动 search、remember、update 或 forget。这使 Memory 更像后台 ETL，而不是
Agent 自身可以使用的能力。

Candidate 原本用于隔离不确定推断，但 V1 将它扩展为所有自动记忆的必经状态。用户
明确表达的偏好、决定和“请记住”仍需二次确认；同 key 新 Candidate 在确认时还会被
旧 Active FACT 的唯一约束拒绝。此 Bad Case 表明，谨慎不等于正确：当保护机制阻断
正常事实更新时，它已经侵入了模型应承担的语义决策。

Harness 应保护 namespace、事务、revision、审计、可撤销性和数据库不变量，而不应
包办“何时需要记忆、记住什么、何时更新”等全部语义选择。模型也不应获得任意 SQL、
跨作用域访问或不可恢复删除能力。新的边界需要在模型自主性、用户主权和可恢复副作用
之间重新确定，而不是继续给 V1 增加更多状态和审批分支。

在新架构确定前，CLI 不再装配 Memory V1，旧环境变量不会启动自动召回和后台提取。
SQLite、FTS5、sqlite-vec、领域模型和测试暂时保留，它们是基础设施实验与 Bad Case
样本，不代表最终架构。后续重构前应先回答：哪些记忆操作是模型可自主执行的低风险
动作，哪些操作需要用户明确授权，自动召回与主动搜索如何配合，以及怎样依靠审计和
撤销替代令人反感的逐项确认。

## 23. Memory Capacity：写入前腾位，语义选择与并发不变量分离

普通 Memory 的 25 条上限不能只在第 26 条写入后返回一个提示。Main Agent 已不再持有
普通 archive 工具，Post-Run Reflector V1 也只负责 none/create/update，因此“超限后
给出 candidates”没有实际执行者。容量闭环必须发生在 CREATE 之前：若 active 已满，
先尝试安全归档一个候选；只有真正腾出空位后才允许创建。维护模型选择 defer、调用失败
或无法证明候选未变化时，本次 CREATE 被跳过，旧记忆保持不变。

Reflection 与 mutation 应当分层。`PostRunMemoryReflector` 只把有界 Run 上下文转换成
严格决策，Runtime 再验证 UPDATE 的 recall 证据或协调 CREATE 容量，最终由
MemoryManager 写入。容量语义由独立 `MemoryMaintenanceReflector` 判断，它只看到现有
Retention 算法选出的少量完整候选，只能输出 archive/defer。Archive 是移动到保留
Front Matter 的 `archive/`，不是删除；V1 不实现需要“更新保留项 + 归档来源项”的 Merge。

候选评分只负责缩小模型输入，不能直接成为删除规则。当前算法优先挑选长期未访问、长期
未更新且访问较少的记录，但小时级时间负分远大于 `log1p(access_count)`，整体仍明显偏重
时间。Maintenance 模型必须读取候选完整正文，判断是否过时、重复或已被替代；信息不足
就 defer。后续优化评分应通过评测数据完成，不应为了容量闭环顺便重写 retention 语义。

模型调用期间存在并发窗口。只比较 `updated_at` 不够可靠，因为 Markdown 当前按秒保存
时间戳，同一秒内更新可能保持相同时间；并发 `memory.read` 还会改变 access_count 和
last_accessed_at，却不改变 updated_at。归档前因此要重新加载并比较完整 MemoryRecord
快照，任意正文或 retention metadata 变化都视为陈旧决策。CREATE 则通过
`create_if_capacity` 在 MemoryManager 同一把锁里再次检查数量和写入，避免两个 Run
同时抢到最后一个空位。

Maintenance 只在正常 FINAL_ANSWER 后运行，且每个 Run 默认最多执行 3 个 archive
动作。历史遗留 26+ 条可以逐轮恢复；达到动作上限仍未收敛时记录 remaining_overflow，
不无限调用小模型。provider error、timeout、非法 JSON、候选外 ID、陈旧快照和 defer
都不会改变主任务成功结果。Maintenance 事件单独记录 provider/model/usage/latency，
Trace 的 Main Agent 汇总仍只统计主模型。

最终链路为：

```text
FINAL_ANSWER
→ Checkpoint complete
→ Reflection 决策
→ UPDATE：验证当前 Run 已 read 后更新
→ CREATE：校验内容 → 必要时 Maintenance archive → 原子容量检查并创建
→ 若存在历史超限，执行有界 Maintenance
→ AGENT_COMPLETED
```

## 24. Memory Update：读取资格、语义版本与文件锁是三层保护

“本轮成功 `memory.read`”只解决语义资格：Reflection 确实看过目标记忆的完整
内容。它不能证明从读取到写入期间文件没有变化。普通 Memory 因此增加独立递增的
`revision`；read 返回 revision 但不会递增它，update/archive 才递增。Runtime 从真实
ToolResult 提取 `{memory_id: revision}`，Reflection UPDATE 必须携带完整 title、summary、
content，Harness 只在当前 revision 与读取版本一致时写入。冲突只让后处理失败，不覆盖
其他 Run 的新内容，也不改变主 AgentResult。

UPDATE 必须替换完整 Recall Cue，而不只是正文。`INDEX.md` 由 title + summary 生成；若
正文已经改成新架构、Index 仍显示旧摘要，后续主模型可能根本不会召回这条记忆。因此
title、summary、content 构成一次语义版本，写入后在同一 mutation 临界区重建 Index。
旧 Markdown 没有 revision 时按 1 加载，完成向后兼容；这不是数据库迁移任务。

锁也分层。`asyncio.Lock` 只能串行化单个 Manager 实例，无法协调两个 CLI 进程。当前
Manager 在 mutation 时先取得实例锁，再在 Memory 目录的 `.memory.lock` 上取得 POSIX
`flock`，覆盖 ID 分配、容量检查、文件原子替换和 Index 重建。macOS/Linux 因而共享同一
写入边界；Windows 暂时退化为实例锁。底层 `MemoryStore` 仍保留无容量的原始文件能力，
只用于旧数据导入和修复；Runtime 与模型工具必须经过执行硬上限的 MemoryManager。

## 25. Memory Eval：不要用单轮工具命中率冒充长期记忆质量

长期记忆的最小评测单位不是一次 ToolCall，而是“产生信息的 Run → Reflection 写入 →
新会话发现 Cue → 主模型读取 → 基于记忆正确回答”。现有单 Run Eval 适合验证工具和
Task，但 Memory 需要共享同一临时 Store 的多阶段 Harness，并分别采集 Main、Reflection
和 Maintenance 的决策、事件、文件快照及成本。

测评必须分开回答两个问题：离线不变量测试证明系统不会越权、越容、覆盖并发更新或因
后处理失败破坏主任务；真实模型评测衡量是否值得记、是否想起正确内容、是否重复创建、
是否把 Core/Task 写入普通记忆，以及 Maintenance 是否误伤关键记录。最终采用 Memory
ON/OFF 对照衡量真实收益，用独立 Judge 和人工抽查评价自然语言语义，不能让被测模型
给自己打分，也不能为了场景通过率把自主召回策略改成固定轨迹。

Memory Eval V1 已采用独立目录实现。Scenario 内的多个 Phase 共享临时 Memory Store；
conversation 标签相同才继承聊天历史，因此“A 会话写入、B 会话召回”不会被旧 History
作弊。Reflection 产生的 ID 绑定成场景别名，断言不依赖 M001 等具体编号。每个阶段同时
保存 Core、Index、active、archive 快照和事件，并将三类模型 Token 分开报告。真实模型
只通过 `tests.memory_eval.run_live` 显式运行，pytest 只用 Fake Adapter 验证 Harness。

首轮 Live Eval 说明 CREATE 与 UPDATE 不应共享完全相同的保守阈值。CREATE 会扩张 active
集合，应默认稀疏；UPDATE 不增加条目数量，承担防止已有知识过时的职责。当前用户明确
确认同主题规则已经决定、完成、纠正或扩展时，即使本轮只有 read + explain、没有代码
mutation，也构成耐久项目知识证据。Harness 仍通过本轮成功读取和 revision 防止盲写与
并发覆盖，Prompt 则要求模型融合旧事实并保留否定、替代关系、数值和安全约束。

语义评测还必须保留模型原始现场。只存 action=none 无法知道模型为何拒绝 UPDATE；只用
中文子串也会把英文 `vector database` 误报为信息遗漏。因此 Eval 显式开启原始 I/O
捕获并写入阶段 artifact，生产默认关闭以保护用户上下文。后续自动关键点断言负责快速
门禁，跨语言完整性最终应由独立 Judge 与人工抽查共同评价。

## 26. MCP Client：协议适配属于工具基础设施，不属于 Runtime

MCP Server 对 Agent 来说仍然只是工具来源。正确接入方式不是在 AgentRuntime 增加
`if mcp` 分支，而是把远端工具适配成 `BaseTool` 并注册到同一个 ToolRegistry。模型看到
统一 ToolDefinition，调用后仍经过 PermissionHook、人工审批、ToolExecutor 超时、输出
截断、Hook 和 Trace；Runtime 无需知道工具运行在当前进程、HTTP 服务还是 stdio 子进程。

V1 的链路是：

```text
.vesta/mcp.json
→ MCPClientManager 启动每个 stdio Server
→ ClientSession initialize / list_tools
→ MCPToolAdapter 注册为 mcp__<server>__<tool>
→ AgentRuntime 正常产生 ToolCall
→ 现有 ToolExecutor 安全链
→ ClientSession call_tool
→ MCP content 转成统一 ToolResult
→ Provider Adapter 转成对应 tool message
```

Server 名称是稳定命名空间，原始工具名作为 Adapter 元数据保存，不能依靠拆分模型可见
名称反推远端调用。远端名称中的点号、连字符等会规范化为下划线；同一 Server 内若两个
原始名称规范化后冲突，整个 Server 的本轮注册失败并回滚，不能静默覆盖。

权限归本地 Harness 所有。MCP annotations 是远端提供的信息，不能自动获得信任；默认
使用 `human_approval`，只有用户明确配置的可信只读 Server 才设为 `allowed`。同样，MCP
JSON 能启动本地命令，本身就是受信任配置。密钥通过 `${ENV_VAR}` 引用进程环境，缺失时
只让对应 Server 进入 failed，不应拖垮其他服务器或 Vesta。

MCP 内容不只有文本。单文本且无 structuredContent 时可以直接返回文本；多段内容、图片、
资源或结构化结果必须序列化保留。`CallToolResult.isError=true` 虽然协议请求成功，但领域
含义是工具失败，应抛入现有 ToolExecutor，再由统一 ToolResult 记录错误和耗时。

stdio 的生命周期由同一个 Manager 持有：启动时进入 transport 与 ClientSession 的异步
上下文，退出 CLI 时按逆序关闭。启动超时和工具调用超时需要分离；很短的工具超时不能
反过来导致 initialize 握手失败。V1 选择显式启动和故障隔离，不做隐式自动重连，避免在
没有健康状态和幂等语义前制造重复工具调用。

## 27. 易变时间事实不应写入持久会话，也不应默认常驻请求

身份、行为边界和用户自定义规则适合成为持久 system message；当前日期时间、时区、服务
状态等会变化的事实不适合。若在创建会话时写入“当前日期”，会话恢复后它就从运行事实
变成了错误历史，模型越遵守系统提示反而越稳定地答错“今天”和“明天”。

时间事实不仅不能持久化，也没有必要在每个 Agent Step 常驻。当前 Runtime 不再注入
`vesta_runtime_environment`；内置只读工具 `get_current_time` 提供进程本地时间和可选
IANA 时区。模型只有在处理今天、明天、现在、近期、截止日期等相对时间时才调用它，普通
文件、代码和知识任务不会承担这部分重复上下文。`web_search` 的定义也不再动态拼接日期。

旧会话仍保存过去写入的固定日期。迁移时不直接修改数据库，而是在模型请求副本中识别
`当前日期是 YYYY-MM-DD。` 并移除；需要当前时间时再调用工具。这样模型不会收到过期日期，
同时数据库仍保留当时真实发送过的原始 system message，满足恢复与审计边界。

## 28. 工具目录应由 Registry 派生，工具激活应属于 Run

工具执行能力和模型可见能力不是同一集合。ToolRegistry 可以持有全部可执行工具，但模型
每一步只需要看到常驻核心工具，以及本轮任务已经确认相关的延迟工具。当前实现把 MCP
工具默认标为 deferred；存在延迟工具时，Runtime 自动提供一个常驻 `tool_search`。

ToolCatalog 不保存第二份静态索引。每次搜索直接读取 Registry 中当前工具的 name、
description 和参数说明，MCP 启动、关闭或以后动态刷新都会自然反映到目录，因此新增工具
不需要修改硬编码映射。目录结果只返回精简描述且最多 5 条，命中后的完整 ToolDefinition
从下一模型步骤开始加入请求。

激活集合是 Runtime 单次 `run()` 的局部状态，而不是 Registry 全局状态。这样同一 Run 后续
步骤可以继续使用已发现工具，Run 结束后又自动回到最小暴露状态，不会污染别的会话或并发
执行。模型即使从旧历史猜到延迟工具名，未在当前 Run 搜索激活也只能得到失败 ToolResult，
不能绕过发现边界。实际执行仍走统一 ToolExecutor，因此审批、超时、Hook 和 Trace 不变。

## 29. 上下文管理是请求投影，不是窗口溢出后的抢救

完整历史负责恢复和审计，模型请求只是一份临时投影。当前 Context Pipeline 每一步都先把
工具结果限制在独立预算内，再判断普通对话是否需要滚动摘要，最后才使用模型窗口做硬保护。
数据库、AgentResult 和工具执行记录仍保存完整内容，压缩只改变 `ModelRequest.messages`。

预算分成两套。`input_budget` 由模型窗口减去输出预留和安全余量得到，负责防止 Provider
拒绝请求；`working_input_budget` 默认上限为 32768，负责日常成本。有效触发线取硬窗口
80% 与工作预算 70% 中较小者，压缩目标取硬窗口 60% 与工作预算 45% 中较小者。工具
结果默认占压缩目标 35%。这些默认值是首个可观测基线，不冒充普适最优值；Trace 已分别
记录消息、Schema 和工具结果 Token，后续应依据真实任务分布调整。

工具整理每轮执行。最近两个工具轮作为正在消费的证据，不会被整轮删除；但任何单条超大
结果仍受字符上限约束，避免最新 MCP 结果独占请求。更旧结果先截短，再从最旧工具轮开始
成组移除，始终保持 ToolCall/ToolResult 协议合法。当前 Run 也遵守同一规则，因此多步循环
不会无限累积早期工具输出。

普通对话在工具整理后仍达到经济触发线，或者未摘要 ConversationBlock 超过 30 个时才进入
滚动摘要。`covered_message_count` 是持久水位线，摘要只消费新增的持久聊天历史；动态时间、
Memory、Checkpoint 和 Task Snapshot 都插在历史边界外，不会被摘要模型复制。

Task 是进度的独立事实源。Runtime 每一步重新从 Store 获取最新 revision；开始具体步骤前由
模型调用 `task_update(in_progress)`，获得充分证据后写 `done + note`，真实阻塞时写
`blocked + note`，最终回答前核对本轮进展是否已经写回。单个业务工具成功只是一条证据，
Harness 不会自动宣布语义步骤完成。注入视图折叠旧 done 步骤，但 Task 文件始终完整。

## 30. “最近工具轮”必须按语义邻近性保护，不能只按全局排名

只取历史中最后 N 个工具轮看似简单，却会制造永久屏障：如果之后几十轮都是普通对话，
那几个工具轮仍然是“最后 N 个”，滚动摘要为了保留它们只能把水位线停在它们之前。此时
系统能正确判断上下文超线，却找不到可推进的连续前缀，形成 `requires_compaction=true`
但 `compaction_stage=none` 的假触发状态。

工具证据是否仍需原样保留，关键不是它在工具轮排名中是否最新，而是它是否仍位于近期
对话区域。当前规则以最后一个仍可摘要的普通对话块为边界，只保护该边界之后最近 N 个
工具轮。与近期回答直接相邻的 ToolCall/ToolResult 仍成组保留；已经隔着多轮普通对话的
陈旧工具协议随摘要前缀退出模型请求。完整原始历史仍在 SQLite 和 AgentResult 中，因此
请求投影的删除不会破坏恢复与审计。

经济预算也不能只为避免窗口溢出服务。32K 日常工作上限仍保留，但默认在 70%（约 22.9K）
触发，并压到 45%（约 14.7K）目标；百万 Token 模型窗口只承担最终硬保护。这样降低每轮
重复输入成本，同时仍为近期对话、Task、Memory、工具 Schema 和当前 Run 留出空间。

## 31. Skill Runtime V2：Agent Skills compatible + Progressive Disclosure

Skill 回答“以后遇到这种任务应该怎么做”：调试某类模型的流程、某种部署方法、某类编码
工作流、反复验证有效的操作经验。它与 Task、Memory 严格区分：Task 保存当前任务状态，
Memory 保存跨会话事实，Skill 保存可复用流程。

V1 是“Front Matter Markdown + `skill_list`/`skill_read`”的扁平实现。V2 升级为目录式
`<name>/SKILL.md`、双层发现、metadata 与正文分离、Catalog 自动注入、Run-scoped 激活、
Active 指令每 Step 注入与上下文预算约束、路径安全加载。设计目标是“模型需要多少上下文
就注入多少，且注入后不会被压缩遗忘”。

### 31.1 与 V1 的差异（Bad Case 驱动）

- V1 模型必须先 `skill_list` 才知道有哪些 Skill，多一步发现成本且容易漏看；V2 用 Catalog
  自动注入替代 `skill_list`。
- V1 激活后的正文只是普通 ToolResult，会被 ToolReducer / 滚动摘要压缩遗忘；V2 的 Active
  指令是独立于 ToolResult 的每 Step 注入块，跨压缩保留。
- V1 是扁平 `<name>.md`，没有 resources；V2 目录式支持 references/scripts/assets。
- V1 文件名即 Skill 名，无严格校验；V2 name 必须过 `^[a-z0-9]+(?:-[a-z0-9]+)*$`（≤64，
  拒绝大写/下划线/首尾连字符/连续 `--`）再参与路径计算。
- V1 无分层；V2 分 user（`~/.vesta/skills`）与 project（`backend/.vesta/skills`），
  project 同名覆盖 user。

### 31.2 目录与数据模型

```text
skills/<name>/
├── SKILL.md         必选（Front Matter + 指令正文）
├── scripts/         可选
├── references/      可选
└── assets/          可选
```

数据模型（`app/skills/models.py`）：

- `SkillMetadata`：name、description、scope（user/project）、location、license、
  compatibility、metadata、allowed-tools —— **发现阶段只建立它**，不读正文；
- `SkillResources`：scripts/references/assets 的相对路径元组 —— 仅清单，不加载内容；
- `Skill`：metadata + content（正文）+ root + resources —— **激活阶段才加载**；
- 常量：`SKILL_FILE_NAME="SKILL.md"`、`SKILL_NAME_MAX_LENGTH=64`、
  `SKILL_DESCRIPTION_MAX_LENGTH=1024`。

Front Matter 只允许固定字段：name、description、license、compatibility、metadata、
allowed-tools/allowed_tools；**未知 top-level 字段会被拒绝**（抛 `SkillParseError`），
`allowed-tools` 与 `allowed_tools` 同时出现视为冲突；description 缺失/空/超长、body 为空、
name 与目录名不一致、YAML 非法都会抛 `SkillParseError`。`metadata:` 内部保持自由 mapping。

### 31.3 双层发现与加载分离（Discovery / Store）

`SkillDiscovery`：

- `discover()` 先扫 project 再扫 user，`dict` 合并（project 覆盖 user），按 name 稳定排序；
- 每个子目录先 `validate_skill_name`，再 `safe_skill_dir`，再读 `SKILL.md` 解析成 metadata；
- 坏 Skill（非法名、坏 front matter、超 512KB、符号链接、越界）跳过并记录
  `SkillDiagnostic`（scope/name/location/reason），**不影响其余 Skill 与 Agent 启动**。

`SkillStore`：

- `catalog()` → 轻量 metadata 元组（每 Run 首次发现后缓存）；
- `load(name)` → 激活时才读正文 + `_discover_resources`（rglob 列资源相对路径）。

分离的价值：Catalog 注入只付 metadata 的 token 成本；正文只有在模型明确要求时才读取。

### 31.4 上下文注入策略（Progressive Disclosure）

`SkillContextProvider`（`app/skills/context.py`）：

- **Catalog（每 Step 注入，独立 Token Budget）**：`vesta_skill_catalog` system 消息，
  只含 `[name] description`，每 Step 重建（发现只做一次，消息每 Step 生成），不进持久历史。
  受 `skill_catalog_max_tokens=2048` 预算约束：按稳定排序逐项加入，达到预算即停止，并在
  末尾提示“还有 N 个未展示”，结果确定性、不依赖模型；
- **Active 指令（每 Step 注入）**：`vesta_active_skill` system 消息，渲染
  `Skill.render_instructions()`（指令正文 + Resources 清单，提示用 `skill_resource_read`
  按需读取）。去重、按激活顺序。独立于普通 ToolResult，因此不会被 ToolReducer /
  ConversationReducer / Compaction 遗忘；它是 Skill 指令正文的**唯一权威注入源**——
  `skill_read` 不再返回正文，避免正文重复与超预算泄漏；
- **预算**：`skill_context_max_tokens=4096`、`skill_max_active=4`；
  `would_exceed_budget(current, candidate)` 按激活顺序确定性判断——超过数量上限或激活后
  总指令 token 超预算则拒绝该候选。

### 31.5 Run-scoped 激活

激活只发生在 `AgentRuntime._run_once` 内部：

```text
模型调用 skill_read（轻量请求激活，工具已暴露）
  → ToolResult 成功（只含 found/name/description/scope/resources，无正文）
  → _skill_read_activated_name() 解析 output（JSON str → dict，检查 found/name）
  → store.load(name)（不存在 → SKILL_ACTIVATION_FAILED）
  → would_exceed_budget（超预算 → SKILL_ACTIVATION_FAILED）
  → active_skills[name] = skill（Run 内局部 dict，不跨 Run 污染）
  → emit SKILL_ACTIVATED（携带 scope、active_skill_names、active_skill_tokens）
```

`active_skills` 是 `_run_once` 的局部变量，因此 Skill 激活天然是 Run-scoped：一次 Run 的
选择不会影响后续 Run，也不会产生并发会话状态串扰。

### 31.6 安全边界

- name 必须先通过严格校验再参与路径计算；
- `safe_skill_dir` / `safe_skill_file` / `safe_skill_resource`：**先检查原始路径
  `is_symlink()`，再 `resolve()`** 并用 `relative_to` 确认仍在 Skill 根内（先 resolve 再查
  symlink 会让指向根内目录的链接逃过检查）；拒绝 `..`、绝对路径、符号链接、目录；
- `skill_resource_read` 单文件 ≤64KB，且**只能读取当前 Run 已激活 Skill 的资源**：Runtime
  在 `ToolExecutionContext.metadata` 携带 Run-scoped `active_skill_names`，工具
  `execute_with_context` 校验请求 name 必须在其中，否则拒绝（不把 Run 状态塞进全局
  SkillStore，避免并发污染）；
- 模型只能读、不能写 Skill（不提供写工具）；resource 不自动加载。

### 31.7 Runtime 数据流与装配

- `AgentRuntime.__init__` 新增 `skill_store` / `skill_context_provider`（provider 非空但
  store 为空时抛 `ValueError`）；
- `_run_once` 顶部初始化 `active_skills={}`、`skill_catalog_loaded=False`、
  `catalog_metadata=()`；ephemeral 构造段先注入 catalog（首次发现缓存）再注入 active；
- tool 循环里对 `skill_read` 成功结果做激活检测（见 31.5）；
- MODEL_STARTED 事件增加 `available_skill_count` / `skill_catalog_tokens` /
  `active_skill_names` / `active_skill_tokens` / `active_skill_message_names`（实际注入
  的 Active Skill 消息名，独立于 run state）观测字段；
- 事件 `SKILL_ACTIVATED` / `SKILL_ACTIVATION_FAILED` 由 Trace 事件驱动自动持久化，无需
  额外改动；
- CLI（`app/models/chat.py`）装配 `SkillStore` + `SkillContextProvider(SkillSettings())`，
  `register_skill_tools` 后传入 `AgentRuntime`。

### 31.8 工具面

- `skill_read`（常驻）：**轻量激活请求**，按名返回 found/name/description/scope/resources，
  不返回完整正文；正文只经 `vesta_active_skill` 注入；
- `skill_resource_read`（常驻）：按 name + path 安全读取**当前 Run 已激活** Skill 的资源；
- 不再有 `skill_list` —— Catalog 注入替代了发现职责。

### 31.9 Token 影响

- 示例 3 个 Skill：Catalog 每 Step 约 214 tokens（受 `skill_catalog_max_tokens=2048`
  约束，Skill 数量多时确定性截断）；激活后每个 Active 指令约 60～190 tokens 每 Step；
  skill 工具 schema 约 289 tokens；
- 未激活前只付 Catalog 的小额常驻成本；激活只在模型明确需要时发生。

### 31.10 边界（尚未支持）

- `allowed-tools` 已解析进 metadata，但尚未参与工具权限（TODO）；**语义明确：未来只能
  收窄当前 Run 的工具集合，不能把 approval 提升为 allowed、不能解禁 forbidden**；
- `metadata:` 自由扩展字段暂无 schema 约束；
- 无 Skill 编写/管理命令，用户级目录不自动创建；
- 无向量 / LLM 路由 / 自动生成 / Marketplace（规格边界内）。

### 31.11 关键取舍

- metadata 常驻（每 Step catalog 注入）但完整 instructions 按需（激活后才注入）；
- Active Skill 是 Run-scoped 的每 Step 注入块，不是普通 ToolResult，保证跨压缩不遗忘；
- resource 需要时用 `skill_resource_read` 读取，不自动加载；
- 模型不允许写 Skill，Skill 是“预置只读能力包”，由开发者/用户在两层目录维护。

## 32. Skill Learning V1：Completed Task → Skill Candidate

### 32.1 定位与领域边界

Skill Learning 在 Task 与 Skill 之间建立一条**独立学习管线**，与 Task 严格解耦：

- Task = 当前任务 / 最终任务事实的权威状态（不塞 revision_history / failed_tools /
  learned_lessons / skill_candidates）；
- Trace = Agent 实际执行过程的原始证据（SQLite 事件）；
- SkillCandidate = 从多个历史 Task 提炼、尚未生效的候选过程知识；
- Skill = 经用户确认后正式生效的长期 procedural knowledge。

`Task.revision` 继续只作为“当前版本 + 并发更新控制”，不解释成历史存储。

### 32.2 管线与成本约束

```text
Completed Tasks
  → TaskCard（轻量投影，无 Trace）
  → 每累计 N=20 个新 Completed Task（watermark 判断）
  → Task Pattern Mining（1 次 LLM call）
      ├─ {"clusters": []} → 停
      └─ Cluster（>=3 tasks）→ 读 task.run_ids → TraceStore.load_events
           → EvidenceBuilder 压缩 → Procedure Distillation（1 次 LLM call）
           → SkillCandidate（pending）→ Human Review → Accept → SKILL.md
```

关键约束：不每 Run Reflection；20 是扫描周期不是生成条件；只有发现 Cluster 才追加
Distillation call；Candidate 不自动生效。

### 32.3 Watermark

`.vesta/skill-learning/skill_learning_watermark.json`：

```json
{"version": 1, "processed_task_ids": [...], "pending_task_ids": [...], "last_mining_at": ...}
```

- `pending_task_ids` 累计新 Completed Task；达到 batch_size 才触发；
- 触发前先把 scan_ids 移入 `processed_task_ids`（原子写），保证同一批任务不因重启或
  重复扫描被反复学习，也不会不断产生相同 Candidate；
- Pattern Mining 仍可参考旧 Task 判断相似性，但已处理的不再作为“新 batch”触发。

### 32.4 模块划分

- `models.py`：TaskCard（投影）、TaskPatternCluster、PatternMiningResult、SkillCandidate、
  SkillCandidateStatus/Action；
- `config.py`：SkillLearningSettings（batch/min_cluster/provider/model/scope）；
- `store.py`：SkillCandidateStore（候选逐 `<id>.json`）+ MiningWatermark（JSON 原子写）；
- `miner.py`：第一阶段，只消费 TaskCard，输出严格 schema 的 cluster 列表；
- `evidence.py`：TraceEvidenceBuilder——从 AgentEvent 提取工具序列/失败/task_update/
  完成证据并压缩，Trace 缺失优雅降级；
- `distiller.py`：第二阶段，结合 cluster + evidence + 现有 Skill Catalog，输出
  create/update/none 的 SkillCandidate；
- `service.py`：SkillLearningService 编排（maybe_run_mining / accept / reject /
  list_candidates / render_candidate_details）；
- `_call.py` / `prompts.py`：统一模型调用（异常隔离）+ 严格 JSON 解析。

### 32.5 Existing Skill Detection

Distillation 时同时提供现有 Skill Catalog（name+description），模型先判断
`create` / `update` / `none`。UPDATE 要求 `existing_skill_name`，从源头避免
debug-python / python-debug / debug-python-v2 这类重复 Skill。

### 32.6 Human Gate

- 创建后是 pending，不影响 Skill Runtime（SkillStore 不可见）；
- `/skill-candidates` 列候选，`/skill-candidate <ID>` 看详情（Why / Source Tasks /
  Common Procedure / Repeated Problems / Verification）；
- `accept`：CREATE 写 `<scope>/<name>/SKILL.md`（scope 显式，默认 project，同名已存在则
  拒绝）；UPDATE 写 replacement proposal 到 proposals/，不静默覆盖；
- `reject`：状态置 rejected，不产生正式 Skill。

### 32.7 可追溯性

Candidate 必须保存 source_task_ids / source_run_ids / reason / evidence_summary，
不允许只保存一份最终 Markdown。原始证据继续由 Task / Trace 作为事实源，不复制完整
Conversation / ToolResult / AgentEvent payload。

### 32.8 边界（不做）

embedding / vector DB / semantic vector clustering / background continuous Reflection /
每 Run Skill Reflection / 自动 Skill promotion / 自动 Skill deletion / marketplace /
Multi-Agent / Task Graph / Planner DAG。Pattern Mining V1 完全用一次结构化 LLM 请求完成。
本能力准确描述为 Skill Learning V1，不是 autonomous self-modifying agent。

### 32.9 真实模型 Live Eval 的经验（deepseek-v4-flash）

- **Pattern Mining 高度可靠**：对无关 / 机械任务正确返回 `{"clusters": []}`（无假阳性），
  对真实相似任务 Cluster Precision / Recall 均接近 1.00；
- **Distillation 对 evidence 强度敏感**：只有“失败 + 元数据更新”、缺“修正 + 验证成功”
  的 Trace，模型会保守返回 `action=none`（理由常是“证据不足 / 现有 Skill 已覆盖”），
  不会编造流程——这是合理行为，Live 场景应预置完整证据闭环；
- **Reasoning Provider 的结构化输出缺陷**：deepseek 会把列表字段输出为 `null` 或单个
  字符串、大 prompt 偶发空 content。Skill Learning 的 `_Distilled` 需归一化 null/单字符串；
  对 deepseek 关闭 thinking（`extra_body={"thinking":{"type":"disabled"}}`，与
  ContextSummarizer 一致）；
- **名不可预知**：Live 中模型给出的 candidate 名与场景期望名常不同，Judge 应把
  exact-name 作为记录项、以 action/count 作为硬标准；Human Gate 用 accept_all / reject_all
  而非按名匹配；
- **成本**：每 20-Task batch 约 1 次 mining + 1 次 distillation，实测约 1,923 tokens /
  4.8s；Live Eval 用正式 `tests/eval/run_learning_live.py` + `learning_judge.py` 驱动并
  生成带真实模型输出的报告。

## 33. Skill Learning 收口：钉死 CREATE/UPDATE/NONE 语义 + 修 Eval pitfall 跨语言误判（2026-08-18）

> 只修 learning-10 真实 Eval 暴露的两个问题，不改主架构、不调 Prompt 迎合测试、
> 不放宽 Eval 期望。

### 33.1 背景与 Bad Case

- **learning-10（20-Task 真实 Eval）上一轮 3/3 FAIL**：模型 3 次都返回 `create` 专项名
  （fix-python-interpreter-mismatch / debug-python-interpreter-mismatch），而非期望的
  `update debug-python`。根因在 `_DISTILLATION_PROMPT` 最后一句
  "If no related skill's body covers the procedure, return action create" —— 当已有
  debug-python 但正文未覆盖 interpreter/virtualenv 专项时，模型 reason 明确说
  "正文只覆盖通用 traceback，缺专项诊断，故新建技能"。
- **Eval pitfall 关键词判断是纯 substring**：learning-10 run2 模型 pitfalls 用英文
  "global pip"/"interpreter"，关键词是中文 "全局"/"解释器" → 中英跨语言时 substring
  不匹配，recall 被错算成 0.00（Eval 误判，不是模型质量）。

### 33.2 修复 1：Distillation 判定语义 = task family / capability domain

`app/skill_learning/prompts.py` 的 related_skills 判定段重写为：
- 先判"新 procedure 是否属于已有 Skill 的同一 task family"；
- **同一 family**：body 已完整覆盖 → `NONE`；正文未覆盖但多次 completed task 提供
  稳定新步骤 / pitfalls / verification → `UPDATE`（**明确禁止"因正文缺具体步骤就改
  CREATE"**，同一 family 意味着扩展现有 Skill）；
- **不同 family**：有独立稳定复用价值 → `CREATE`，否则 `NONE`；
- 附 3 个示例：debug-python + interpreter/virtualenv mismatch → update；
  + PostgreSQL slow query → create；+ 发布到 PyPI → create。
- 未加 hard-coded skill name、未改模型输出 schema、未改生产数据结构。

### 33.3 修复 2：Eval pitfall 支持中英同义组（concept-based recall）

- `tests/eval/scenario.py`：`expected_pitfall_keywords` 类型
  `tuple[str, ...]` → `tuple[str | tuple[str, ...], ...]`（旧单字符串格式向后兼容）；
- `tests/eval/learning_judge.py`：pitfall 计算改为 concept-based —— 每组命中任意一个
  alias 即算该 concept 命中，recall = 命中 concept 数 / concept 总数；
- learning-10 YAML：`[全局, 解释器]` → `[[全局, global], [解释器, interpreter]]`，
  `expected_action=update`、min thresholds 不变。

### 33.4 验证（离线 + 真实模型）

- 离线新增 5 例（`_pitfall_concept` 归一化 / 英文 synonym 命中 / 部分命中恰好过阈 /
  全 miss FAIL / 旧单字符串兼容），全量 **545 passed**；
- 真实模型（deepseek-v4-flash，learning-10 × 3）：**3/3 PASS**（上一轮 0/3）。
  - 三次 action 全部 `update`、`existing_skill_name=debug-python`；
  - 模型 reason 三次都明确引用 task family 语义（"same task family as existing
    'debug-python' … naturally extend the existing skill, therefore update"）——
    Prompt 语义修改直接生效；
  - cluster precision / recall 三次均 1.00 / 1.00；trace deterministic checks 全过
    （py1~py6 selected steps 与 `expected_trace_steps` exact match、Evidence 含全部
    关键词无禁词）；
  - pitfall recall 三次均 1.00（上一轮 run2 被跨语言误判成 0.00）；
  - 成本：9 calls / 20,179 tokens / 30.3s，avg 6,726 tokens per 20-Task batch。

### 33.5 结论

- UPDATE vs CREATE 的稳定边界**可以**通过把"同一 task family 扩展现有 Skill"写进
  Distillation 语义来修正（此前历史结论"UPDATE 稳定性是纯模型行为难点"在本场景被
  推翻 —— 它同时是 Prompt 语义问题）；
- 无新功能性 Bad Case；仅候选文本风格波动（procedure 项是否带编号前缀、
  verification 条数 1~4 不等），不影响 Eval 与生产（生产走 Human Gate 评审）。

## 34. Run Manager V1：统一 Run 生命周期（2026-08-19）

> 定位：AgentRuntime 管"一个 Run 内部怎么执行"；Checkpoint 管"中断后从哪里恢复"；
> Trace 管"实际发生了什么"；RunManager 管"Run 的生命周期：创建/查询/取消/恢复"。
> 不重构 Runtime、不重造 Checkpoint 协议、不引入调度/队列/DB 迁移/FastAPI/UI。

### 34.1 为什么需要 RunManager（生命周期缺口）

- 之前没有独立的 Run 生命周期对象：Trace 的 `agent_runs` 表是事件派生的摘要
  （RUNNING/COMPLETED/FAILED 三态），Checkpoint 只保存恢复边界，Task 是业务任务。
  RUNNING 是进程级事实 —— 进程重启后旧 RUNNING 没有统一入口被修正；
  也没有统一入口去"取消一个正在执行的 Run"或"恢复一个中断的 Run"。
- RunManager V1 用一张 `runs` 表补齐：可持久化、可查询（按 conversation/status）、
  可取消、可恢复。

### 34.2 关键设计：Run 与 Checkpoint / Trace 共用同一个 run_id

- 现有 `AgentRuntime.run()` 内部 `uuid4().hex` 生成 run_id，外部无法把 Run 记录与
  Checkpoint / Trace 关联。最小修改：给 `run()` / `run_stream()` 增加可选 `run_id`，
  RunManager 总是传入自己的 Run.id —— 于是 Run / Checkpoint / Trace 三条记录共享
  同一个 id，recover 时用新 run_id + `recovery_run_id`（旧中断）精确定位恢复点。
- Checkpoint 层新增 `get_unrecovered(run_id)`：只取指定 run 的 INTERRUPTED 且未
  recovered 的 checkpoint，避免会话内多个中断 Run 时恢复错对象（原 `latest_unrecovered`
  按会话取最近一条，保留给非 RunManager 路径）。

### 34.3 RunStatus 与状态机

- `PENDING → RUNNING → COMPLETED / FAILED / CANCELLED / INTERRUPTED`
- `INTERRUPTED → RUNNING`（recover）
- COMPLETED / FAILED / CANCELLED 是终态，不可再转换；
  INTERRUPTED 不是终态（可恢复）。
- 终态转换由 `SQLiteRunStore.update_status` 用 `_ALLOWED_TRANSITIONS` 强制拒绝。

### 34.4 cancel 实际如何工作

1. `RunManager.cancel(run_id)` 校验 RUNNING，对 `_active_tasks[run_id]`（asyncio.Task）调
   `task.cancel()`，然后 `await task` 吞掉 CancelledError；
2. 取消信号进入 AgentRuntime：在当前 await 点（checkpoint 保存点 / 工具执行 await）抛出
   CancelledError —— 不再启动新的 Agent Step / Tool；
3. AgentRuntime 的 `except BaseException` 分支把 Checkpoint 转 INTERRUPTED（保留
   pending_tool_calls / completed_tool_results；未决工具语义 = "不确定，禁止直接重试"），
   不伪造"没执行"；
4. 已落库的 Trace 事件保留；
5. `_execute` 捕获 CancelledError → Run 标记 CANCELLED（终态）。

### 34.5 recover 实际如何工作（复用现有 Checkpoint 协议）

1. `RunManager.recover(run_id)` 校验 INTERRUPTED 且存在 `get_unrecovered` 的 Checkpoint；
2. 以同一 conversation 启动**新** Run，传 `recovery_run_id=旧 run`；
3. AgentRuntime 注入 `render_checkpoint_context` 恢复证据：completed_tool_results 作为
   "已完成"（模型继续、不重复执行）；pending_tool_calls 作为"不确定"（核对后再决定）；
4. 新 Run 正常完成后 `mark_recovered(旧 run, recovered_by_run_id=新 run)`；
5. 旧 Run 保持 INTERRUPTED（生命周期事实），新 Run 记录 `recovered_from_run_id`。

### 34.6 启动 reconciliation（为什么）

- RUNNING 是进程级事实。进程重启后，当前进程内不可能存在该 Run 的 Agent execution，
  旧的 RUNNING 不应残留。
- 规则：有 Checkpoint → INTERRUPTED（Checkpoint 仍 RUNNING 时先由 checkpoint 层转
  INTERRUPTED）；Checkpoint 已 COMPLETED/FAILED → Run 同步为对应终态（执行边界是事实源）；
  无 Checkpoint → FAILED（没有可恢复状态，不能假装可恢复）。

### 34.7 职责边界（不混）

- Run 不复制 Conversation / Events / Tool Results，也不复制 Checkpoint 数据结构；
- RunManager 不构建 Context、不执行 Tool、不加载 Skill、不推进 Task；
- Task.status 与 Run.status 解耦：一个 Task 可关联多个 Run，Run FAILED 不代表 Task FAILED。

### 34.8 CLI 接入（最小）

- `_send_message` 改经 `RunManager.start()` 启动（保留事件打印/历史回写/统计输出）；
- `/runs` 用 RunStore 输出完整生命周期；新增 `/run <id>`、`/run cancel <id>`、
  `/run recover <id>`；启动打印 reconciliation 结果；`/checkpoints` `/trace` 等不变。

### 34.9 测试与结果

- `tests/test_run_manager.py`（13 例）覆盖：start→COMPLETED / 异常→FAILED / cancel（模型
  请求与工具执行，checkpoint 保留未决工具）/ reconciliation（有/无 Checkpoint）/ recover→
  COMPLETED / 已完成 Tool Result 不重复执行 / 无效转换被拒 / completed 不能 cancel /
  多 Run 同会话 / Trace+Checkpoint 行为不变 / list 状态过滤；
- 全量 `pytest` 558 通过（545 + 13），`ruff` / `compileall` / `git diff --check` 全绿。

## 35. RunManager V1 架构修复（2026-08-19，不扩展功能）

> 只修 5 个架构问题，保持职责边界不变。

### 35.1 去掉 AgentRuntime 隐式自动恢复

- 原实现：普通 `run()` 未传 `recovery_run_id` 时，若传了 conversation_id 会隐式调
  `latest_unrecovered` 自动加载会话最近中断 Checkpoint。这会让"新对话"意外带上旧恢复
  证据，且把"恢复哪个 Run"的决定权散落在 runtime 里。
- 修复：删除该分支。**普通 start 永远不自动恢复**；只有 `RunManager.recover(run_id)`
  显式传 `recovery_run_id` 才加载指定 Checkpoint。恢复决定权统一归 RunManager。
- `latest_unrecovered` 保留为 checkpoint API（不再被 runtime 调用）。

### 35.2 Shell cancel 杀进程组

- 原实现：`ShellCommandTool.execute` 只处理 `asyncio.TimeoutError`（killpg）；收到
  `asyncio.CancelledError` 时不会清理子进程 → Run 被 cancel 时 shell 残留。
- 修复：捕获 `CancelledError` → `_terminate_process(process)`（killpg SIGKILL 整个进程
  组）→ 收尾 communicate → **重新抛出**。与 timeout 分支语义一致，不伪造"没执行"。

### 35.3 简化 recovery lineage

- 删除 `Run.recovery_count`（恒为 1，无真实递增价值）与 `SQLiteRunStore.record_recovery`
  （create 后再写同一 `recovered_from_run_id` 是重复写）。
- `recovered_from_run_id` 只在 `create()` 一次性写入；`_execute` 不再重复写。

### 35.4 统一 reconciliation

- 原实现：CLI 启动 / `/use` 直接调 `checkpoint_store.recover_running()` 修改生命周期
  状态，与 `RunManager.initialize()` 的 reconcile 重复。
- 修复：`RunManager.reconcile()` 先调 `checkpoint_store.recover_running()`（全局转
  INTERRUPTED），再基于 Checkpoint 事实修正 RUNNING Run；CLI 只展示结果（启动展示
  initialize 返回值、`/use` 只读列出该会话 INTERRUPTED Run），不再直接改状态。

### 35.5 CLI Ctrl+C cancel 当前 Run

- `_send_message` 的 `run_manager.wait(run_id)` 捕获 `KeyboardInterrupt` →
  `run_manager.cancel(run_id)` → 打印取消结果 → 返回输入循环，不退出 Vesta。
- 输入等待时的 Ctrl+C（`input()` 处）退出行为保持不变。

### 35.6 测试与结果

- 新增 5 例：普通 start 不自动恢复（不注入证据、不 mark_recovered、无 recovered_from）/
  reconciliation 处理遗留 RUNNING Checkpoint（无 Run 记录）也转 INTERRUPTED /
  ShellCommandTool cancel 无残留进程 / RunManager cancel shell Run 无残留 /
  runtime 普通 start 不注入恢复证据（原隐式恢复测试改为显式 recovery_run_id）。
- 全量 `pytest` 563 通过（558 + 5），`ruff` / `compileall` / `git diff --check` 全绿。

## 36. Automation / Scheduler V1（2026-08-19）

> 目标：让 Vesta 具备最基本的"长期运行 / 到时间自己启动 Run"能力。
> 职责：Automation="未来何时以什么 prompt 启动 Run"；RunManager="Run 生命周期"；
> AgentRuntime="Run 内部怎么执行"。Scheduler 绝不直接调用 AgentRuntime。

### 36.1 Schedule 数据模型

- `ScheduleKind.ONCE`：`run_at`（ISO8601，必须带时区偏移）；执行一次。
- `ScheduleKind.INTERVAL`：`interval_seconds`（>0）；固定间隔重复。
- `ScheduleKind.CRON`：`cron_expr`（crontab 五段）；简单 calendar recurrence。
- `timezone`（IANA）是 Schedule 的原时区语义；内部持久化 `next_run_at` 统一 UTC，
  计算下一次始终基于 `timezone`（用 APScheduler trigger 的 `get_next_fire_time`），
  避免"用 UTC 解释用户本地时间后又偷偷转换错"。

### 36.2 调度模型（可控 + 可恢复）

- 每个 ACTIVE Automation 注册一个"下次触发"的一次性 `DateTrigger` job；
- 触发后：通过 `RunManager.start` 启动 Run → 更新 last_run_id/last_run_at →
  用 APScheduler trigger 计算下一个未来触发点 → 写 store → 注册下一个一次性 job；
- 计划完全可控、与持久化 `next_run_at` 一致、重启恢复简单（从 store 读 next 再注册）。

### 36.3 misfire / coalesce 语义（V1 确定规则）

- 一次性（ONCE）：启动恢复时若已过期且从未执行 → 补跑一次（把 next 设为 now 触发）→
  触发后 COMPLETED；已执行过 → 直接 COMPLETED。
- 重复（INTERVAL/CRON）：不补跑所有错过次数，`get_next_fire_time` 直接取"从现在起的
  第一个未来触发点"——天然 coalesce，避免启动后瞬间创建几十个 Run。

### 36.4 并发与失败语义

- `max_instances = 1`：触发前检查上一次 `last_run_id` 对应 Run 是否仍 RUNNING，
  若在跑则跳过本次并推进 next（coalesce）。
- Run FAILED 不自动取消 Automation；重复任务下次仍继续；一次性保留 `last_run_id`
  供查看失败原因。V1 无 retry policy。单个 Automation 失败被 job 包装函数隔离。

### 36.5 重启恢复与 Conversation 上下文

- `AutomationScheduler.start()`：建表 → 加载 ACTIVE → 应用 misfire 规则 → 注册 job → 启动。
- 触发时从持久化 `ConversationStore.load_messages` 加载 history、`SummaryStore.load`
  加载 summary_state，再 `RunManager.start` —— 不依赖 CLI 内存 history，
  Automation 脱离 CLI 也能运行。

### 36.6 Agent Tools

- `automation_create`：模型把"明天早上9点提醒我"转成结构化参数（kind/run_at/interval/
  cron/timezone）；工具只接收明确结构化时间，不做自然语言解析；
  校验时间格式 / timezone / 过去时间 / interval>0 / cron 合法性（`build_schedule_and_next`）。
- `automation_list` / `automation_get` / `automation_cancel` / `automation_pause` /
  `automation_resume`。

### 36.7 测试与结果

- 12 例，全部 fake RunManager / fake ConversationStore，不调真实模型 API；
  用可控时间（构造过去/未来 next_run_at）避免真实等待；核心 `_trigger` / `_restore`
  白盒可测。
- 全量 `pytest` 575 通过（563 + 12），`ruff` / `compileall` / `git diff --check` 全绿。

## 37. Automation 执行出口重构：ConversationService（2026-08-19）

> 一句话原则："Automation 不是定时启动 Runtime，而是定时向 Conversation 投递一条新的输入。"

### 37.1 为什么抽取 ConversationService

- 原 Scheduler 直接 `RunManager.start`，自己承担 load history / load summary / Run 启动，
  未来还要处理写回 / Trace，职责膨胀；CLI 的 `_send_message` 又各自维护一套执行链。
- 统一后：CLI / Automation / 未来 API / Desktop 全部走 `ConversationService.dispatch`，
  只保留一套 "load 最新 → start → wait → 写回 → save summary → 返回" 逻辑。

### 37.2 ConversationService 职责（`app/conversation/service.py`）

1. 从 `SQLiteConversationStore` 加载**触发那一刻最新**的持久化 history（不依赖 CLI 内存）；
2. 加载 `ConversationSummaryState`；
3. 统一注入 `SQLiteTraceEventHandler`（+ 可选额外观察者）；
4. `RunManager.start` → `wait` → `result`；
5. 把完整 `AgentResult.messages` 写回 ConversationStore、保存最新 Summary；
6. 返回 `DispatchResult(run, result, trigger)`。
- 不包含任何 CLI print/input；`is_run_running` 供 Scheduler max_instances 检查。

### 37.3 Automation 构造 scheduled input（provenance）

- `Message` schema 是 `extra="forbid"` 且无 metadata → 新增 `TriggerContext`：
  `source(manual/automation) / automation_id / scheduled_for / triggered_at`。
- Scheduler 到点后 `conversation_service.dispatch(conversation_id, content=prompt,
  trigger=TriggerContext(source=AUTOMATION, automation_id, scheduled_for, triggered_at))`，
  **不解析 AgentResult / 不修改 Conversation / 不处理 Trace**。

### 37.4 Conversation / Summary / Trace 统一写回

- ConversationService 是唯一写回点：`replace_messages(conversation_id, result.messages)` +
  `summary_store.save`；下一次 Automation 或手动输入读到的是上一次执行结果
  （A B → 触发 C → A B C D）。
- Trace 由 Service 统一注入，Automation Run 与手动输入走同一条 Trace 路径。

### 37.5 顺手修复

- CLI async input：`input()` → `asyncio.to_thread(input, ...)`，避免阻塞事件循环，
  用户停在输入框时 Scheduler 仍能按时触发（有真实异步集成测试验证）。
- Automation 状态机：ACTIVE→PAUSED/COMPLETED/CANCELLED；PAUSED→ACTIVE/CANCELLED；
  终态不可再转换；`_restore` 用 `set_next_run_at`（仅更新 next，避免 ACTIVE→ACTIVE 非法）。
- 修复 `_job_ids` key 判断不一致（统一用 automation.id）。

### 37.6 测试与结果

- `test_conversation_service.py`（7 例）：最新 history 加载 / 写回 A B C D / Summary /
  Trace / provenance / is_run_running / 下一次读到上次结果。
- `test_automation.py`（15 例，重写）：经 FakeConversationService 投递、provenance、
  状态机非法转换、completed/cancelled 不再执行、真实 APScheduler 自动触发。
- 全量 `pytest` 584 通过，`ruff` / `compileall` / `git diff --check` 全绿。

## 38. Automation / Scheduler V1 收尾：并发锁、提前持久化、provenance（2026-08-19）

> 三个问题：①同 conversation 并发 dispatch 会交错丢消息；②Automation→Run 的 last_run_id
> 要等 Run 完成后才写，中途崩溃就丢了；③Trigger provenance 只存在内存 TriggerContext，
> 重启后查不到。V1 收口只修这三点，不扩展新功能。

### 38.1 按 conversation 的 asyncio.Lock（并发保护）

- `ConversationService` 维护 `_locks: dict[conversation_id, asyncio.Lock]` +
  `_lock_for(conversation_id)`（None → 共享 `_NullLock`，不锁）。
- `dispatch()` 外层 `async with self._lock_for(conversation_id)` 包住 `_dispatch_locked`。
- 关键取舍：
  - **同 conversation 串行**：后到的 dispatch 等前一个 Run 完全收尾（写回/Summary 都完成），
    读取"触发那一刻最新"的 history 才不丢消息 —— 这是 fix 的核心。
  - **不同 conversation 并行**：各用各的锁，互不阻塞。
  - **用 dict 缓存锁**而非每 dispatch 新建锁：新建锁会破坏串行语义。

### 38.2 on_run_started：Run 创建即持久化 last_run_id

- 崩溃窗口：Automation 触发 → Run 创建 → 完成前进程崩溃。若 last_run_id 只在 Run 完成后
  更新，崩溃时 Automation 记录里没有关联 Run，重启后一次性 Automation 会被当作
  "从未执行"而**再次补跑**。
- 解法：`dispatch(..., on_run_started=None)` 回调在 `run_manager.start(...)` 返回、
  `wait()` **之前**调用（run_id 已生成）。Scheduler 的回调里
  `store.mark_triggered(automation_id, last_run_id=run_id, last_run_at=now, next_run_at=None)`
  —— Run 一创建就落库。
- 注意：ONCE 完成分支**不再**二次 mark_triggered（避免覆盖 next_run_at）；崩溃重启后
  一次性 Automation 已是"已触发"状态，不会补跑。
- 回调签名用 `Any | None`，避免 scheduler → conversation 的 import 依赖方向反转。

### 38.3 provenance 落库到 Run

- `Run` 新增 4 列：`source / source_id / scheduled_for / triggered_at`。
- `run/store.py` 迁移是幂等的：`ALTER TABLE runs ADD COLUMN ...` 逐个 try/except
  OperationalError（老库补列、新库建表即带列）；`_run_from_row` 用
  `"source" in row.keys()` 保护老行。
- `RunManager.start(source=..., source_id=..., scheduled_for=..., triggered_at=...)`
  透传；`ConversationService` 从 `trigger.source / trigger.automation_id /
  trigger.scheduled_for / trigger.triggered_at` 注入。
- 收益：provenance 与 Run 同生命周期，重启后仍可 `get_run` 查询来源。

### 38.4 测试与结果

- 并发：StubRunManager 记录同时最大 active 数 + 动态生成结果消息；同 conversation 断言
  顺序不丢、不同 conversation 断言 max_active>1。
- 崩溃：dispatch 里 on_run_started 后抛异常（模拟崩溃），断言 store 里 last_run_id 已在；
  重启后一次性 Automation 不再补跑。
- provenance：真 SQLite 建 store → 创建带 provenance 的 Run → 关库重建 + 重启
  ConversationService → 再查询，字段仍在。
- 全量 `pytest` 589 通过，`ruff` / `compileall` / `git diff --check` 全绿。

## 39. Automation / Scheduler V1 收口：INTERRUPTED 终态化 + prompt 不含调度条件（2026-08-19）

### 39.1 INTERRUPTED 改为终态

- 背景：旧语义允许 `INTERRUPTED → RUNNING`（注释"recover() 重新进入执行"），但
  `RunManager.recover()` 实际实现是**创建新 Run B**（`B.recovered_from_run_id = A`），
  A 永远保持 INTERRUPTED。状态机允许的转换和实现语义不一致。
- 修正：`_ALLOWED_TRANSITIONS[INTERRUPTED] = frozenset()`（无转换），并把 INTERRUPTED
  加入 `TERMINAL_STATUSES`（`completed_at` 随中断一并盖章，与"attempt 已结束"一致）。
- 最终语义：**一个 Run = 一次 execution attempt**；中断后该 attempt 结束；
  恢复 = 创建新的 execution attempt。Checkpoint recovery 协议完全不动。
- 注意：`mark_interrupted` 现在会设置 `completed_at`（因为它属于终态集合），
  这是语义一致的结果，不是副作用。

### 39.2 automation_create 的 prompt 只保存执行指令

- 问题：prompt 描述过于宽泛（"触发时要执行的用户指令"），模型可能把
  "每天晚上10点总结项目进度"整句存进 prompt，触发后模型把调度词也当指令，
  可能再次创建新自动化。
- 修正：Tool description 与 prompt 参数 description 都明确约束——
  **prompt 只保存"到触发时间真正要执行的指令"，调度条件（时间/频率/时区）
  必须放进 kind / run_at / interval_seconds / cron_expr / timezone**，
  并给示例拆解（“每天晚上10点总结项目进度”→ schedule=每天22:00、
  prompt=“总结项目进度”）与后果说明。
- 只改 schema 说明文字，不做自然语言时间解析（模型负责拆解，工具只收结构化参数）。

### 39.3 测试与结果

- `test_run_manager.py`：INTERRUPTED 后 mark_started / mark_completed / mark_cancelled
  全部抛 invalid run transition。
- `test_automation.py`：新测试断言 Tool 说明与 prompt 参数说明都含"不含调度条件"
  约束与示例。
- 全量 `pytest` 590 通过，`ruff` / `compileall` / `git diff --check` 全绿。
  Automation / Scheduler V1 正式收口。
## 40. Desktop V0：Application Bootstrap + Agent Server + Electron（2026-08-19）

> 一句话目标：把已经存在的 Agent Harness 用一个真正的 Desktop UI 跑起来，
> 不重新设计 Harness，核心链路保持 ConversationService → RunManager → AgentRuntime。

### 40.1 为什么抽 Application（composition root）

- 原 `app/models/chat.py` 里塞了大量依赖初始化与 wiring；Server 若再复制一套就会
  出现两处 load history → start → save 的实现漂移。
- `app/application.py::Application` 统一装配并持有全部运行依赖，提供
  `await app.start()` / `await app.close()`；CLI 与 Agent Server 都复用。
- 测试可注入离线 fake registry、关闭 memory reflection / skill learning，避免调真实模型。

### 40.2 Server 只是"薄壳"，不复制 Agent 逻辑

- 发消息必须走 `ConversationService.dispatch`；recover 走现有 `RunManager.recover`
  （旧 Run   （旧 Run   （旧 Run   （旧 Run   （旧 Run   （旧 Run   （旧    （旧 Run   （旧 Run   （旧 Run   （旧 Run   （旧 Run   （旧 Run   ext`（结构化 schedule，无自然语言解析）。

### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSoc + ### 40.3 WebSocket 事件流：复用 Agen### 40.3 W者### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复用 Agen### 40.3 Webler。### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket ath### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复用 AgxtI### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复API### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事件流：复用 Agen### 40.3 WebSocket 事### 40.3 WebSocket 事件流：复用 Agen### 40.3 Webecute`（只有 execute_with_context）：
  由于 `register_automation_tools` 此前从未被任何测试触发，这个"实例化即崩"的 bug
  一直潜伏；Desktop Server 启动注册工具时暴露，补一个 stub 修复。
- 同步 TestClient 下无法用 POST 制造"运行中的 Run"做 cancel 测试 —— 用
  `client.portal.call(...)` 在应用事件循环里启动阻塞 Run，再走 HTTP cancel。

### 40.6 测试与结果

- 新增 `tests/test_agent_server.py` 14 例（全离线 fake model）：health / conversation
  CRUD / send 走 ConversationService / 写回 / run list·detail·cancel / trace /
  automation CRUD·control / WS 收到 AgentEvent / automation Run 也广播并产生
  source=automation Run / shutdown 正确关闭资源 / Application start-close 幂等。
- 后端全量 `pytest` 604 通过；Desktop `typecheck` / `build` 通过。

## 41. Computer Runtime：从“事件发送”到“效果可证明”（2026-08-21）

### 41.1 Observation 是给模型的工作视图，不是 AX 树转储

- 原生层仍遍历 AX 树建立完整的短期 ref 映射，但输出顺序必须服务于决策。
- Helper 直接读取 `AXFocusedUIElement`，保证真实焦点即使位于 DFS 截断范围外也能进入结果。
- 元素优先级固定为：真实焦点 → 可编辑 → 可操作 → 其它信息元素。
- 工具层在执行器的 20K 硬上限之前主动裁剪到 18K；上下文层再次压缩时重建合法 JSON，不能做字符串腰斩。

### 41.2 输入目标必须来自最近一次 Observation

- `element_ref` 不是永久 UI ID，只对最近 Observation 有效。
- `computer_type` 的显式 ref 必须存在且 `editable=true`。
- 没有 ref 时，只允许自动绑定唯一的 `focused=true && editable=true` 元素；否则返回 `editable_target_required`。
- 审批窗口造成焦点漂移后，Helper 先恢复 App/Window，再聚焦已批准元素，并把事件定向投递到目标 PID。

### 41.3 成功分成投递成功与效果成功

- `delivery_status=delivered`：CGEvent 已发给目标进程，只说明命令送达。
- `computer_type` 完成时固定是 `verification_status=unverified`：mutation 前的 AX 对象不能证明 mutation 后的真实 UI。
- Action 后必须立即 invalidate Snapshot；只有下一次 fresh Observe 得到的新 Snapshot 才能作为效果证据。
- 因此“事件已投递”和“界面已出现文字”属于两个时刻、两份 Snapshot，不能在 Native 返回里合并成一个成功结论。

### 41.4 Agent Loop 的最后一步也必须能消费证据

- `max_steps` 限制正常工具循环，但最后一步若刚执行 `computer_observe`，再给一次 tools=() 的最终化调用。
- 这次调用只能读取最后证据并回答，不能继续操作。
- 若 `computer_type` 仍是 unverified，Runtime 会拒绝模型的无证据成功声明，直到出现新的 Observe 或安全停止。

## 42. Desktop 浮窗与 macOS frontmost 不是同一个概念（2026-08-21）

- `focusable:false` 只保证 BrowserWindow 不成为键盘焦点窗口，不能保证 Electron application 永远不成为 frontmost。
- `showInactive()` 解决首次显示不主动聚焦，但浮窗点击、状态更新和窗口 resize 仍可能改变 App 级激活状态。
- 因此审批 UI 的安全生命周期必须分段：pending/submitting 时显示；批准后执行期间隐藏；Run 终态后才能再次显示结果。
- `open_app` 的“进程启动成功”也不等于“用户正在看到该 App”。可靠语义必须继续验证 `frontmostApplication.pid == target.pid`。
- Agent 最终回答同样需要执行证据：Provider 工具协议如果只是普通文本，没有形成 `ToolCall`，就没有被执行，Run 不能标记 completed。

## 43. Computer Target & Recovery：目标、观察和前台是三种状态（2026-08-21）

### 43.1 Target 不应由每次 observe 临时猜测

- `frontmost app` 只是此刻位于系统最前面的应用，可能是审批窗口、Finder 或用户临时切换的 App。
- `computer target` 是本轮桌面任务明确要操作的进程，由 PID、bundle id、app name 组成；`open_app` 成功启动进程后建立。
- `observation target` 是某次快照绑定的 target PID + window + observation id。清空元素 ref 不能顺便忘掉长期到下一次 open 的 target。
- 因此 observe 可以读取后台 target 的 AX Tree；真正执行 click/type/key/scroll 时，才恢复 target 到前台并重新验证。

### 43.2 launch 成功与 activation 成功必须独立表达

- launch 回答“目标进程是否存在”，activation 回答“目标是否成为 frontmost”。
- 已运行但未抢到前台的 App 仍是成功建立的 target，不能返回假的打开失败。
- 副作用前再做 `activate → raise window → freshness verify → execute`，失败时不向当前任意前台 App 发送输入。

### 43.3 AX 遍历预算与模型输出预算不是一回事

- 旧策略在 DFS 收到 300 个元素时停止：后续排序再聪明，也看不到第 301 个之后的编辑器。
- 新策略最多访问 3000 个节点形成候选，再按“真实焦点 → 可编辑 → 可操作 → 有意义控件 → 重复列表项”选择最多 300 个。
- row/cell/list 等重复角色单独设 80 个配额，避免大型侧边栏吃光预算；最终 18K 裁剪仍输出完整 JSON。
- `element_stats` 同时报告候选数、返回数、编辑/动作数量与重复丢弃数，让模型和 Trace 能判断观察质量。

### 43.4 停滞不是“调用参数相同”，而是“世界状态没变”

- open/observe/type 交替调用的签名都不同，但如果 target 相同、错误码相同、Observation 证据没有变化，本质上仍是同一失败策略。
- ComputerStagnationGuard 用 target identity、desktop revision 和 failure code 组成失败键。
- 第一次失败允许正常恢复；第二次把纠偏建议写入工具结果；第三次隐藏 Computer 工具，要求模型说明阻塞并收尾，避免烧到 `max_steps`。
- 新 Observation 指纹发生变化或已验证输入成功时推进 desktop revision，旧失败计数自然失效，不会把真实进展误判成停滞。

### 43.5 AXFocused 和窗口 bounds 不是稳定身份

- Notes 的 AX Tree 会让许多 outline/cell 同时返回 `AXFocused=true`；真正可靠的焦点来源是应用级 `AXFocusedUIElement`。
- 一旦取得真实焦点，就应抑制其它节点的 reported focus，否则伪焦点会排在 text_area 前面并耗尽序列化预算。
- `AXValue` 可写不等于能够输入文本：splitter、scroll bar 也可能 editable。文本输入必须进一步限制为 text_area/text_field/combo_box。
- AX window identity 与 window bounds 是不同概念。同一窗口被系统移动后，语义 element ref 仍可安全使用；只有基于截图的坐标点击必须要求 bounds 保持不变。
- 停滞指纹同样应忽略 bounds 和统计数量的小幅抖动，只把目标变化、语义控件变化或已验证副作用视为真正进展。

## 44. Computer Runtime V2：Session 是输入安全边界（2026-08-21）

### 44.1 握手必须双向确认

- Python 创建 session 只是本地意图；Native 返回 `accepted=true` 才代表控制权真的建立。
- 同一个 session 重复 begin 是幂等恢复，不应制造新状态。
- Native 已有其它 active session 时必须返回结构化 `session_mismatch`，不能把冲突藏在普通 result 的布尔值里。
- 返回缺少 `accepted=true` 时，Python 回滚本地 session。宁可本轮停止，也不能让两端各自相信不同的 active owner。

### 44.2 Background-first 不等于向任意后台应用发事件

- 输入目标来自当前 Session Snapshot 的 PID、AX window 和 element ref，三者共同构成精确目标。
- 首先在后台尝试 AX focus，再用 `CGEventPostToPid` 定向投递；这条路径不依赖用户此刻的 frontmost App。
- 只有后台 AX focus 失败时才允许 foreground fallback，而且只能恢复同一 Session 已记录的 App/window。
- restore 后必须重新检查 session、observation id、PID、window 和真实 `AXFocusedUIElement`；任何一项变化都 fail closed。

### 44.3 Type 是插入动作，不是字段赋值

- `AXSetValue` 的语义是替换整个字段，不能拿来实现“在当前光标处输入”。
- `computer_type` 始终用 Unicode CGEvent 插入，因此已有 `hello` 时输入 ` Vesta` 会得到 `hello Vesta`。
- Native 只能确认事件已定向投递；真实效果由 mutation 后的新 Observe 确认。
- 真机 E2E 应验证 UI 内容，而不是只断言返回的 `characters` 或 `success=true`。

## 45. Agent Workspace：消息不是长期工作的正确产品单位（2026-08-21）

### 45.1 Request、AgentTurn、Run 各有不同职责

- Request 是用户交给 Vesta 的目标；AgentTurn 是用户能理解和控制的一次工作记录；Run 是一次可追踪、可恢复的执行 attempt。
- 实时事件不应在 Run 结束后消失。AgentTurn 需要从同一组事件经历 Working → Approval → Verifying → Completed，并在终态压缩成持久 Work Record。
- Conversation 是长期上下文，Runs 是执行历史。两者不能做成两份相同列表：Conversation 展示连续工作，Runs 负责单次执行分析与恢复。

### 45.2 Presentation Layer 隔离 Runtime 协议与产品语言

- React 组件不应到处解析 `AgentEvent`。`turnPresentation` 统一把 tool name、arguments、usage、verification、target 和 stop reason 变成稳定 ViewModel。
- 主界面只显示“Opened Notes”“Waiting for verification”这类用户动作；tool_name、raw result、error_code、snapshot id 留在 Technical details。
- `delivery_status=delivered` 仍不能渲染为绿色完成；只有 fresh evidence 能把动作从 Waiting for verification 推进到 Verified。

### 45.3 Progressive Disclosure 是克制与可分析性的共同解

- Layer 1 直接可见：Working、Approval、Completed、Stopped、Result。
- Layer 2 轻量或可展开：动作、steps、tokens、duration、Computer target、verification。
- Layer 3 默认折叠：provider/model、arguments、raw result、event JSON 与错误码。
- 因此 AgentTurn 负责“看懂”，Activity / RunDetail 负责“分析”，Computer Observation inspector 负责“查证”。极简不等于删掉证据。

### 45.4 Durable UI 依赖正确的 Read Model

- 实时 `AgentEvent[]` 足以构建当前 AgentTurn，但历史消息若没有稳定的 message_id → run_id 关联，就无法可靠判断哪几条消息属于哪次 Run。
- 前端不能按时间或消息顺序猜测这种关系，否则 recovery、automation 或并发 Run 会产生错误归组。
- 正确的后续最小能力是只读关联字段或 historical AgentTurn read-model，而不是在本轮为了界面新增第二套事件系统。

### 45.5 性能边界仍然属于产品正确性

- Provider delta 继续在 Store 中短时批量提交，AgentTurn 按 run + step 细粒度订阅；ChatPage 不随每个 token 全量重渲染。
- 流式正文直接使用真实增量，不增加假打字机；完成事件立即 flush，避免最后几个字符丢失。
- 持久 AgentTurn 只改变展示生命周期，不改变 Runtime、RPC、审批路由或 durable SQLite 事实来源。
## 46. Reflection Gate必须保守（2026-08-22）

Reflection Gate的职责不是替代模型判断“什么值得记住”，而是过滤确定性没有长期
价值的请求。正确边界是：

```text
明确无长期价值 → Harness跳过模型调用
可能有长期价值 → Reflection模型自主决定none/create/update
```

关键细节：

1. 寒暄、能力盘点、天气和时间查询可以用窄规则确定性跳过，但规则必须先检查
   “以后、记住、偏好、项目决定、纠正”等耐久信号。
2. 语义不确定时必须fail open到Reflection，而不是为了省Token直接返回none。
3. 本轮读取过普通Memory意味着可能需要UPDATE，Gate不能切断这个闭环。
4. skipped、none和failed是三个不同事实：skipped没有调用模型，none是模型判断
   无记忆变化，failed是调用或解析失败。
5. Gate跳过Reflection不等于跳过容量维护；Maintenance仍需保持原有独立语义。
6. 每次跳过必须写入结构化reason，Usage才能解释为什么Post-Run为0。

## 47. Run Budget治理累计成本，不治理单次窗口（2026-08-22）

Context Budget与Run Budget解决的是两个不同问题：

```text
Context Budget：一次请求能否安全放进模型窗口
Run Budget：一个Agent Loop累计请求了多少模型计算
```

关键细节：

1. Run的预算Token优先使用`uncached input + output`。缓存命中的输入仍会被Provider
   处理，但不能与新输入按相同口径决定是否停止；缓存明细未知时必须回退到全部
   input，不能假设它已经被缓存。
2. Model Calls是独立预算轴。即使每次请求很小，反复发送Tool Schema和保留上下文
   仍可能形成低效循环，因此Token与调用数任一越线都应触发对应阶段。
3. Warning不剥夺工具能力，只提醒模型减少重复调查；Finalization才隐藏工具并要求
   根据已有证据收口；Hard表示不再发起新的模型请求。
4. Finalization必须有且只有一次专用机会，并限制输出上限。否则“达到预算”可能
   反而触发新的无限收尾循环。
5. Context Summary发生在主模型请求前，也是critical-path模型开销，所以summary
   完成后必须重新检查预算。
6. Reflection与Maintenance已经属于Run终态后的background housekeeping。它们进入
   Provider Total账本，但不能参与Main Agent Run Budget，也不能因此延迟
   `agent_completed`。
7. 50K/75K/100K与8/10/12是可调的初始治理基线，不是从Context Window推导出的
   固定真理；后续应根据真实Run成功率、cache结构和成本分布校准。

## 48. 扩展安装是 Host 管理能力，不是 Agent Tool（2026-08-22）

模型“使用扩展”和用户“安装扩展”是两条不同的数据链：

```text
使用：Skill Catalog / Tool Registry → AgentRuntime 按需激活或调用
安装：Desktop 表单 → JSON-RPC → Host 校验 → 原子写入配置
```

关键细节：

1. Skill 安装输入是结构化 name、description、scope 和 instructions。Host 生成 YAML
   Front Matter，并复用正式 Skill Parser 校验，避免 UI 与 Runtime 各维护一套格式。
2. MCP 参数不能用一条 shell 字符串猜测拆词；界面按“一行一个参数”收集，最终明确写成
   JSON `args` 数组。环境变量同理解析为 object，并鼓励用 `${ENV_NAME}` 引用密钥。
3. Renderer 不直接写文件。Host 对重复名称、路径、Schema 和已有配置负责，并通过临时
   文件加 replace 原子提交；失败时不得损坏原配置。
4. 添加 MCP 配置不等于启动 MCP Server。V1 Manager 没有动态重载，因此 mutation 只写
   JSON并返回 `restart_required`，重启 Host 后才执行命令、握手和注册工具。
5. 扩展安装具有供应链风险，必须由用户在设置页明确发起。模型可以使用已经安装的 Skill
   和 MCP 工具，但不能把一句自然语言请求自动升级为宿主机代码安装权限。
6. 配置读取接口只返回环境变量名称，不返回值。即使是本地应用，也不应让 Renderer 获得
   无需展示的长期密钥。
7. “停用”必须可逆且不能伪装成删除。Skill 通过同作用域 `.disabled/<name>` 目录隔离，
   因而立即退出 Catalog、资源仍完整保留；重新启用只是安全地移回正式目录。
8. 管理视图不能复用 Runtime Catalog 的 project-overrides-user 合并结果，否则同名 user
   Skill 会从设置页消失、无法管理。管理清单必须保留 `(scope, name)` 精确身份。
9. MCP 配置变更和当前进程状态是两份事实。删除 JSON 条目不会瞬间终止已经启动的子进程，
   所以 Host 必须持续暴露全局 `restart_required`，直到进程重启重新加载配置。
10. 删除属于不可逆管理操作：Desktop 二次确认，Host 再按受控根目录、合法 name、scope
    和 enabled 状态解析精确目标，不能接受 Renderer 直接传任意文件路径。

## 49. 外部扩展导入必须是“翻译计划”，不能是“粘贴即执行”（2026-08-22）

外部生态把安装方式混在同一个文本入口里：GitHub 仓库、Skill CLI 命令、Claude
风格 `mcpServers` 和 Vesta `servers` 都可能被用户复制进来。Host 应先把它们翻译成
统一计划，再由人确认，而不是猜一段 shell 命令并立即运行：

```text
外部文本
  → 本地 Parse / Normalize（零副作用）
  → Import Plan + SHA-256 fingerprint
  → 用户查看来源、目标、命令和警告
  → Confirm
  → 重新解析并核对 fingerprint
  → 静态 Skill 下载 / MCP 配置原子写入
```

关键细节：

1. `npx skills add owner/repo` 是一次性 Skill 安装语义，不是 stdio MCP Server；若把它
   交给 MCP Manager，进程安装后退出，Initialize 必然失败。
2. Preview 不能联网或执行任何命令，否则“看一下会发生什么”本身已经产生供应链副作用。
3. Confirm 不能只信 Renderer 传回的结构化 Plan。Host 必须用原始输入重新解析，并核对
   输入、scope、permission 共同生成的 SHA-256 指纹，防止预览后内容变化。
4. GitHub Skill 导入不需要执行仓库代码。下载受限大小的 ZIP 后，只提取通过正式 Parser
   的 `SKILL.md` 和受支持资源目录；拒绝路径穿越与符号链接，再走 SkillStore 原子安装。
5. 外部 MCP JSON 只是配置翻译：`mcpServers` object 转成 Vesta `servers` model，非法名称
   确定性规范化，env 在预览中只暴露变量名；实际进程仍到 Host 重启后才启动。
6. “用户确认下载”与“用户批准 MCP 每次工具调用”是两个不同 Gate：前者治理供应链安装，
   后者治理运行时副作用，不能因为安装时确认过就默认授予工具永久权限。

## 50. 能调用 MCP 不等于能审视 MCP；原始推理也不等于 Trace（2026-08-22）

MCP 延迟加载解决的是“按任务找到并调用某个外部工具”，不是“列出宿主当前连接的所有
Server和工具”。这两类问题必须有不同入口：

```text
业务调用：tool_search → 激活相关 MCP Schema → ToolExecutor
运行审视：mcp_status → MCPClientManager.statuses() → 紧凑只读快照
```

关键细节：

1. 不应为了回答管理问题把全部 MCP Schema常驻注入模型；一个小型 `mcp_status` 可以直接
   读取 Manager 的权威内存状态，并保留 MCP 工具本身的 progressive disclosure。
2. 状态查询必须观察现有连接，不能重新启动 Server。重新创建 Client 不仅慢，还会产生
   重复子进程、额外日志和与当前 Host 不一致的瞬时状态。
3. Desktop RPC 与 Agent Tool 是两个消费边界。`extension.list` 面向管理页面；模型需要
   独立、收窄、只读的 Tool，而不是获得调用任意 Host RPC 的能力。
4. Provider `reasoning_content` 是模型原始内部推理，可能包含试探路径、错误猜测和协议
   细节。它不是 Vesta Trace，不应流向聊天、持久消息或面向用户的实时面板。
5. Vesta Trace 应由 Harness 的结构化事实构成：model started/completed、tool started/result、
   approval、compaction、usage 和终态。它可审计且可复现，不依赖展示模型思维链。
6. 推理隔离要双层生效：Runtime 在事件与持久化前清除；Desktop 对旧数据库中的历史
   reasoning 也忽略。这样无需破坏性迁移已有数据，同时新数据不再继续积累。
7. Reflection Gate 的能力查询语言必须覆盖自然口语，如“我看看、看一下、看下”，否则
   一个纯状态查询会在 Run 结束后再次调用模型，产生明确无价值的 Post-Run 成本。

## 51. 用户可见“思考”应是结构化运行状态，不是模型思维链（2026-08-22）

用户真正需要知道的是 Agent 当前在做什么、是否需要自己介入，以及执行是否取得了可验证
的结果，而不是 Provider 生成的逐字推理。合适的数据流是：

```text
AgentEvent 事实
  → 展示层 ViewModel
  → 中文阶段 + 动作时间线 + 用量统计
```

关键细节：

1. “正在分析、正在执行、等待确认、正在验证”是稳定的产品阶段；它们来自模型、工具、
   审批和验证事件，而不是从自然语言 reasoning 中猜测。
2. 当前动作应使用工具名和结构化参数生成，例如“读取文件”“输入文本”“查看 MCP 工具”，
   不能直接展示协议字段或原始 JSON。
3. 一轮执行的辅助信息只保留有决策价值的字段：步骤、操作次数、输入/输出用量、耗时和
   目标应用。详细参数仍进入详情与 Trace，不挤占主聊天界面。
4. `approval resolved` 只代表用户作出决定，电脑动作投递也不代表界面效果成立；因此
   “等待确认”和“正在验证”必须是两个不同状态。
5. 中文化应发生在展示边界。底层事件类型和 Provider 协议继续保持稳定，避免为了改文案
   破坏持久化数据与跨端接口。

## 52. 时间线标记与裁切边界不能相互冲突（2026-08-22）

时间线常见的实现方式是让圆形标记跨在容器左边框上，但如果同一容器还承担高度动画并设置
`overflow: hidden`，负坐标标记就会被裁掉。更稳定的实现是：

1. 标记作为网格第一列正常参与布局，始终位于容器边界内。
2. 连接线由每个非末尾条目的伪元素绘制，只连接相邻节点，不使用贯穿整个列表的边框。
3. 高度折叠和裁切继续由列表容器负责，节点无需依赖越界定位，因此动画与静态布局不会互相
   破坏。
4. 动作文字、最终回复和作者标题应共享同一内容起点；层级主要通过图标、字号和颜色表达，
   不应依赖不断叠加的左边距。

## 53. 压缩更小不等于缓存更好：Run 内应冻结已准备前缀（2026-08-22）

Prompt Cache依赖确定性前缀。若每个Step都从完整原始历史重新计算“应该删掉哪些旧工具轮”，
即使最终请求长度都在目标范围内，删除边界也可能随新结果增长而前移，从而让整个历史前缀
发生变化。

Vesta现在采用以下续接规则：

```text
Step 1：原始历史 + 稳定系统上下文
  → ContextManager压缩/摘要
  → 保存实际发送前缀 P1

Step 2：若系统上下文和工具集合未变化
  → P1 + 新Assistant消息 + 新ToolResult
  → 仅在再次越线时执行下一次压缩
```

关键细节：

1. 缓存基线必须保存“实际发送给Provider的消息”，不能保存原始数据库历史；否则下一步仍会
   重做已经完成的压缩投影。
2. 原始历史仍是持久化权威源。稳定前缀只属于当前Run的模型请求视图，不能写回聊天数据库。
3. Task修订、Skill激活、延迟工具激活或工具集合变化都属于真实语义变化，必须主动重建请求；
   缓存不能凌驾于状态正确性。
4. 工具Schema除了内容稳定，还必须顺序稳定。模型可见定义按工具名排序，使不同注册时序产生
   相同请求结构。
5. Harness只能保证“具备缓存命中的前提”，不能承诺Provider一定命中。Trace因此同时记录
   `cache_prefix_reused`和Provider返回的`cached_input_tokens`，两者分别回答“请求是否稳定”和
   “服务端是否实际复用”。
6. 当已续接候选再次超过工具结果或上下文预算时，ContextManager仍可裁剪并建立新基线；这次
   缓存断点是有价值的治理动作，而不是每Step无意义漂移。

## 54. 缓存命中率必须保留“未知”语义（2026-08-22）

消息底部的缓存命中率使用以下口径：

```text
cache hit rate = cached_input_tokens / input_tokens
```

这里的`input_tokens`包含缓存与未缓存输入，因而百分比表达Provider处理的输入中有多少复用了
缓存。实现时必须注意：

1. `cached_input_tokens = 0`表示Provider明确报告未命中；字段为`null/undefined`则表示Provider
   没有提供该维度，两者不能混为一谈。
2. 多Step聚合时，只要有一次调用缺少缓存字段，整轮缓存率就应保持未知；把已知部分相加再除
   以全部输入会系统性低估命中率。
3. Run终态应优先使用Harness已经聚合的AgentResult Usage，运行中才临时累计各个
   `model_completed`事件，避免两种口径在同一条消息上跳变。
4. 缓存命中率描述的是处理结构，不等于费用折扣比例；不同Provider对缓存读取、缓存写入和
   普通输入有不同价格。

## 55. 信息密度应由分隔关系组织，而不是由卡片数量组织（2026-08-22）

设置、历史、审批、记忆和运行状态都属于高频扫描型页面。每个区块都增加背景、边框与圆角，
会让所有内容看起来同样重要，并在宽屏中进一步压缩有效阅读宽度。当前Desktop采用以下原则：

1. 页面标题、分区标题、留白和一像素分隔线负责主要层级；卡片只保留给需要独立交互边界或
   强调状态的内容。
2. 列表项共享一条连续阅读轴，不为每一行重复绘制完整外框；Hover只提供轻微背景反馈。
3. 内容最大宽度应适配桌面窗口。保留舒适页边距，但不能让固定窄容器在大窗口中产生大片
   无信息空白。
4. 简约化只是展示层调整。审批按钮、安全状态、记忆内容展开和电脑证据仍保留原有语义与
   操作边界。
5. 共享卡片可以在聊天中保持阅读宽度，但页面级列表必须显式解除该上限。长参数要在条目内部
   换行或滚动，不能让单个审批内容反向撑宽整个页面。
6. 审批列表的默认视图应服务于快速决策与扫描：操作名称、状态和决策按钮常驻；原因、来源和
   原始参数属于二级证据，通过下拉按需展开。收起细节不能移除审批动作本身。
7. 模型上下文格式不等于用户展示格式。Core Memory中的Markdown标题和稳定key服务于Harness
   管理，观察页应解析成“偏好、身份信息、长期约束”等语义条目，只展示真正需要用户阅读的值。

## 56. 自动化应以自然语言创建，以管理页面承接结果（2026-08-22）

当Agent已经具备结构化自动化工具时，再向普通用户暴露Cron、间隔秒数和时区表单，会形成
两套创建路径。Vesta将职责收敛为：

1. 用户在对话中用自然语言描述“做什么”和“什么时候做”。
2. 模型负责理解意图并生成结构化参数，Harness负责校验和实际写入。
3. 自动化页面只承担查看、暂停、恢复和取消，不重复承担创建器职责。
4. 底层API仍然保留，模型工具、测试和未来的受控系统入口可以继续复用；移除的是用户界面的
   手动表单，不是领域能力。
5. 自动化列表优先展示状态、调度计划和下次执行时间；可能很长的自然语言任务描述默认折叠，
   由用户按需展开，避免每条描述撑高列表并干扰快速扫描。

## 57. Task应作为会话状态展示，而不是聊天消息的附属文本（2026-08-22）

Task是长任务的权威事实源，消息只是交互记录。主界面展示Task时应遵守：

1. 通过当前`conversation_id`直接查询Task Store投影，不从模型最终回答中解析进度。
2. Task状态条位于聊天顶栏与消息区之间，保持可见但默认只占一行；详细步骤按需展开。
3. 切换会话会切换查询键，后端仍按不可变owner过滤，不能先拉全局任务再由前端过滤。
4. Task更新不写回历史消息，因此不会污染上下文、触发摘要或制造重复事实。
5. 轮询用于覆盖后台工具更新，Run完成和Plan决策后的主动失效用于缩短用户看到旧状态的时间。

## 58. 模型设置必须分离“公开配置、秘密和运行时快照”（2026-08-22）

模型设置看起来只是几个输入框，实际包含三种生命周期完全不同的数据：

```text
非敏感配置（Provider / Model / Base URL / API Style）
  → .vesta/settings/models.json

秘密（API Key）
  → macOS Keychain

当前运行时（Registry / AgentRuntime / 后台模型）
  → Host 启动时生成的不可变快照
```

实现时应遵守：

1. API Key不能进入JSON、RPC读取结果、输入框回显或日志。前端只展示是否配置以及来源；用户留空
   表示保留现有密钥。
2. `.env`仍是兼容入口，但显式保存的JSON和Keychain优先。这样旧用户不需要迁移，新用户也不再
   必须理解环境变量。
3. 保存配置不能直接替换活跃Runtime。一个Run内部切换Provider会破坏上下文、缓存、Usage口径
   和恢复语义；V2采用“保存成功，重启Host生效”。
4. 主Agent、Memory Reflection和Maintenance是三个模型角色。后台角色默认继承主模型，也可以
   独立关闭或指定更便宜的模型，但实际写入与执行仍由Harness负责。
5. “可填写自定义Base URL”不等于“设置页应自动向任意地址发送密钥”。连接测试只允许官方
   HTTPS主机；自定义端点可以保存并由用户明确重启使用，避免粘贴恶意地址后立即泄露密钥。
6. JSON落盘使用临时文件和原子替换；密钥存储抽象成可替换接口，使单元测试完全离线且不会
   触碰真实Keychain。

## 59. 设置表单的对齐单位应该是行，而不是单个控件（2026-08-22）

两列表单中，如果只有某个字段带辅助说明，依靠内容自然撑高会让下一行输入框错位。更稳定的
方式是让每个字段共享相同的三行轨道：标签、控件、辅助说明；没有说明的字段也保留空轨道。

同样，后台模型一行中的名称、启用状态、继承状态和自定义配置应各占固定列。条件字段出现时只
填充预留列，不能重新排列前面的开关。视觉对齐因此来自统一栅格，而不是为每种状态增加偏移量。

## 60. 摘要模型可以独立，但上下文预算仍属于主模型（2026-08-22）

滚动摘要是一次辅助模型调用：输入旧摘要和待压缩历史，输出结构化短摘要。它适合使用便宜、
稳定且擅长遵循JSON格式的小模型，但模型职责不能与主请求预算混合：

1. 是否触发压缩、目标Token和最终上下文是否能放入窗口，始终按照主Agent模型计算。
2. 只有压缩确实进入滚动摘要阶段时才调用摘要模型，普通短会话不会因为配置了小模型而增加调用。
3. 小模型生成失败时，现有Reducer继续保留原始历史并失败关闭，不能为了省Token删除未经摘要的
   消息。
4. 摘要模型Provider复用统一Registry和Keychain配置，不单独保存API Key。
5. 设置默认启用并继承主模型，保证旧配置行为不变；选择独立模型或关闭后都需要重启Host，避免
   同一个Run中途改变摘要语义。
