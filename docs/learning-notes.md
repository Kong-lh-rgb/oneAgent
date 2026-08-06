# OneAgent 学习记录：层次架构与关键设计

> 本文记录 OneAgent 当前已经落地的架构、设计理由和重要边界。
> `task.md` 负责记录每天完成了什么；本文负责解释系统为什么这样设计、
> 各层如何协作，以及后续开发时不能破坏的约束。

## 1. 当前系统定位

OneAgent 当前是一个运行在本地终端中的 Tool-Calling Agent，已经具备：

- GPT、Qwen、DeepSeek、Claude 模型适配；
- 多轮 Agent Loop；
- 本地文件、Shell、HTTP 和网页搜索工具；
- 工具 Hook、人工审批和可记忆权限规则；
- SQLite 会话、消息、运行轨迹和权限规则持久化；
- Token 估算、模型上下文预算和第一层工具消息压缩；
- 结构化 AgentResult 和 AgentEvent。

当前没有实现 Task、Checkpoint、Subagent、Memory、MCP、Scheduler、FastAPI
和前端。这些目录仍是后续能力的边界，不应该提前把它们的职责塞进 Runtime。

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
  （默认 `backend/.oneagent/tasks/`），缩进 JSON 便于人工查看与版本管理；
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

## 14. 关键工程教训

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
