# OneAgent 任务日志

> 本文件用于记录每日开发任务与进展，作为项目留存。
> 追加规范：每日一个 `## YYYY-MM-DD` 小节，最新的日期放在最上方；任务用 `- [x] 已完成` / `- [ ] 未完成` 标记。
> 对架构调整和缺陷修复，应同时记录 Bad Case、影响、根因和修复结果，避免只记录最终功能。

---

## 2026-08-06

### 完成：任务领域层（Task）——长任务状态与对话解耦

#### Bad Case
- [x] 长任务的目标、约束、进度、待办与关键事实全部隐式保存在对话消息里
- [x] 上下文压缩（工具结果缩短、旧工具轮移除、滚动摘要）会替换或丢弃旧消息，任务状态随之丢失或变得模糊
- [x] 对话是聊天记录，无法编程查询"任务做到哪了"、无法在中断后按目标恢复

#### 设计原则
- [x] Task 是任务事实的权威源，独立于会话消息持久化，对话压缩不影响任务状态
- [x] 对话降级为任务的执行日志；压缩只影响日志的紧凑表达
- [x] 显式状态源：任务状态由上层显式写入（Agent/用户/未来规划器），不自动从对话猜测，避免幻觉污染事实

#### 实现
- [x] `app/task/models.py`：`TaskStatus`（pending/active/paused/completed/failed/cancelled）、`TaskPriority`、`TaskStepStatus`（todo/in_progress/done/blocked）、`TaskStep`、`Task`（goal/constraints/state/key_facts/steps/conversation_ids/run_ids/created_at/updated_at/completed_at，文本折叠与去重校验）
- [x] `app/task/store.py`：`FileTaskStore`（任务以独立 JSON 文件存储；create/get/resolve 前缀/list(status)/delete；update_goal/update_state/add_constraints/add_key_facts/replace_steps/set_step_status/set_status/attach_conversation/attach_run；终态维护 completed_at）
- [x] `app/task/__init__.py` 导出 `FileTaskStore` / `DEFAULT_TASKS_DIR`
- [x] `tests/test_task_store.py`（13 例：往返、规范化去重、前缀解析/歧义、生命周期 completed_at、步骤推进、重排步骤、目标/状态/事实更新、会话与 run 关联、状态过滤排序、删除、缺失抛错、进度摘要）
- [x] 全量验证：`pytest` 200 个用例全部通过，`ruff` 无告警

#### 后续待办（本阶段未接入）
- [x] 把当前会话活动 Task 渲染为受控 system 消息，只注入模型请求而不写入原始聊天历史
- [x] CLI Runtime 接入：模型通过任务工具创建、推进、查询并动态调整计划
- [ ] Task 与 Memory 层的边界：短期工作记忆（摘要）与长期事实（Task/Memory）职责分离

### 完成：任务管理工具（主模型可调用）
- [x] `app/task/tools.py`：4 个工具，持有共享 `FileTaskStore`，供主模型在长任务中自主调用
  - `task_create`：判断工作复杂/用户提出多个工作/用户要求时创建任务（title/goal/priority/steps）
  - `task_update`：步骤完成（step_id+step_status）、状态变化、替换目标/状态、追加约束/事实、动态重排计划；会话与 run 由系统自动关联
  - `task_get`：按 ID/前缀获取单个任务完整详情，供模型重新确认当前状态
  - `task_list`：按状态过滤列出任务（精简进度摘要），供总览或用户明确要求时调用
- [x] `register_task_tools(registry, store)` 注册函数；CLI `chat.py` 创建 `FileTaskStore` 并注册，任务工具随模型可用
- [x] 工具权限默认 ALLOWED（任务状态管理不涉危险操作），`for_model=True` 时对模型可见
- [x] `tests/test_task_tools.py`（13 例：注册 4 工具、创建带步骤、title 必填、步骤推进、状态/目标/约束/事实更新、关联 run/会话、至少一更新字段、step 成对、缺失任务、获取详情、列表过滤与精简、非法 limit）
- [x] 全量验证：`pytest` 213 个用例全部通过，`ruff` 无告警

### 完成：任务文件存储（tasks 文件夹，弃用 SQLite）
- [x] 需求：Task 不写入 SQLite，改为本地 tasks 文件夹存储结构化任务
- [x] `FileTaskStore` 取代 `SQLiteTaskStore`：每个任务一个 `<id>.json`（缩进 JSON，便于人工查看/备份/版本管理）
- [x] 默认目录 `backend/.oneagent/tasks/`（`DEFAULT_TASKS_DIR`），构造参数可自定义
- [x] 原子写入：临时文件 + `os.replace`，避免中断产生损坏文件；list 跳过损坏文件
- [x] 磁盘 IO 用 `asyncio.to_thread` 隔离，保持异步 API；tools.py / chat.py 仅换 store 类型，接口不变
- [x] 测试更新：`test_task_store.py`（文件往返/可读性/损坏跳过/歧义前缀）、`test_task_tools.py`（fixture 换 FileTaskStore）
- [x] 全量验证：`pytest` 216 个用例全部通过，`ruff` 无告警

### 完成：Task 长任务闭环与安全加固

#### Bad Case
- [x] Task ID 未限制时，文件路径可能通过 `../`、绝对路径或符号链接逃逸 tasks 目录
- [x] `task_update` 按字段多次写盘，后续字段校验失败时可能已经产生部分更新
- [x] 同一 Task 并发执行“读取—修改—写入”会丢失更新，固定 `.tmp` 文件也会互相冲突
- [x] `model_copy(update=...)` 不重新执行完整 Pydantic 校验，更新路径可能绕过文本规范化和领域不变量
- [x] 模型需要自己填写 conversation_id/run_id，但模型并不可靠地知道内部运行标识
- [x] Task 创建后没有自动进入模型请求上下文，下一轮模型仍需靠 task_list/task_get 猜测当前任务
- [x] 只能更新步骤状态，执行中无法根据实际情况重排或补充任务计划

#### 完成结果
- [x] Task ID 固定为 32 位十六进制，前缀只接受 4–32 位十六进制；拒绝路径穿越、绝对路径和符号链接任务文件
- [x] 新增 `TaskPatch`，`task_update` 先验证全部参数，再一次读取、一次领域校验、一次原子写入；失败时不产生部分结果
- [x] FileTaskStore 增加按任务异步锁、唯一临时文件、flush/fsync + os.replace，并用 revision/expected_revision 检测过期覆盖
- [x] 更新后统一通过 `Task.model_validate()`，补充 Task/TaskStep 文本、唯一步骤 ID、UTC 时间、条目数量和文件尺寸约束
- [x] BaseTool 增加向后兼容的 `execute_with_context()`；Task 创建与更新从真实 ToolExecutionContext 自动关联 conversation_id/run_id
- [x] 新增 `TaskContextProvider`：每次模型调用前加载当前会话最近更新的非终态 Task，以受控 system 消息注入临时模型上下文
- [x] Task 上下文不进入 AgentResult.messages/SQLite 消息历史；同一 Run 中创建 Task 后，下一模型步骤即可看到最新 Task
- [x] `task_update` 支持携带 expected_revision，并支持整体替换步骤计划；已有步骤保留 ID，新步骤由系统生成 ID
- [x] CLI 新增 `--tasks-dir`，系统提示明确简单问题不建 Task，复杂/长任务或用户明确要求时创建，并在进度或计划变化后更新
- [x] 损坏 Task 文件不再完全静默，记录 warning；单文件超过安全上限时拒绝读取
- [x] 新增路径越界、符号链接、并发更新、revision 冲突、原子失败、上下文自动绑定、动态重排计划和 Runtime 创建/更新后即时刷新测试
- [x] 全量验证：`pytest` 226 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过

### 完成：任务按会话隔离（Bad Case）

#### Bad Case
- [x] 所有会话都能看到并更新所有任务，A 会话创建的任务在 B 会话也能 list/get/update，跨会话任务数据相互可见、可被覆盖
- [x] 对话压缩不会丢失任务，但会话隔离缺失会让任务事实被无关会话误改或泄露

#### 修复结果
- [x] 隔离原则：任务归属由 `conversation_ids` 决定；带有效会话上下文时强制按会话隔离
  - `task_list`：只返回当前会话的任务（`store.list(conversation_id=...)`）
  - `task_get` / `task_update`：只能操作属于当前会话的任务，其他会话统一按“任务不存在”处理（隐藏存在性）
  - `task_create`：自动绑定创建它的会话（原有）
- [x] 无会话上下文的直接调用/测试不强制隔离（向后兼容）；真实运行始终携带会话上下文，因此默认隔离生效
- [x] `store.list` 新增 `conversation_id` 过滤参数；tools 新增 `_resolve_owned` 归属校验 helper，三个工具改用 `execute_with_context` 获取会话上下文
- [x] 测试：跨会话 list 过滤、get/update 跨会话拒绝（含执行器路径）、store list 按会话过滤
- [x] 全量验证：`pytest` 231 个用例全部通过，`ruff` 无告警

### 完成：步骤状态需留依据（step_note）
- [x] 问题：模型可无凭据地把步骤标记为 done 或 blocked，之后无法回溯"为什么完成了 / 为什么卡住"
- [x] 约束：`task_update` 将 `step_status` 置为 `done` 时必须提供非空 `step_note`（完成依据）；置为 `blocked` 时必须提供非空 `step_note`（阻塞原因，如"缺少用户提供的实验结果文件"）。系统不校验内容真假，只强制留痕
- [x] 仅推进单步骤路径强制；`in_progress`/`todo` 不强制；`steps` 整体重排（保留已完成步骤）不强制
- [x] 任务可进入 `paused`：当步骤 blocked（等待用户输入/外部条件）时，建议把任务置为 paused，使下次恢复时模型明确知道在等什么；工具 `status` 描述已引导该用法
- [x] 工具定义描述同步说明该要求；空字符串不算依据
- [x] 测试：done 无 note 拒绝、blocked 无 note 拒绝、blocked 有原因成功、in_progress 无 note 允许、任务 paused→active 恢复、runtime 集成测试补 note
- [x] 全量验证：`pytest` 237 个用例全部通过，`ruff` 无告警

---

## 2026-08-05

### 完成：可记忆的人工审批规则与安全加固

#### Bad Case
- [x] HUMAN_APPROVAL 工具每次执行都要求用户重复输入，同一 Run 内相同的 shell/http 操作反复询问
- [x] 审批门只返回 approved/denied，没有"记住安全规则"的能力，也没有规则匹配
- [x] 初版 Shell 前缀规则会错误放行 `pytest x; rm ...`、`&&` 和 `$()` 等命令拼接
- [x] 初版 HTTP 主机规则会把一次 GET 扩大为同主机任意方法、路径、端口和请求体
- [x] SQLite Store 在空作用域 `()` 下错误返回全部规则，直接调用 Executor 时可能跨会话授权
- [x] RUN 规则永久残留在 SQLite，且 CLI 没有查看和撤销已记住规则的入口
- [x] 多条 ALLOW / ASK / DENY 规则同时命中时依赖数据库返回顺序，没有安全优先级

#### 修复结果
- [x] 新增 `app/tools/permissions/` 包：`models` / `matchers` / `policy` / `store` / `rule_factory`
- [x] `ApprovalGate` 返回 `ApprovalResponse`（decision + scope：ONCE / RUN / CONVERSATION）
- [x] `ConsoleApprovalGate` 提供 4 选项菜单：仅此一次 / 本 Run 相同操作 / 记住安全规则 / 拒绝
- [x] `PermissionPolicyEngine` 匹配已存规则 → ALLOW / ASK / DENY；`PermissionRuleStore`（内存 + SQLite 持久化）
- [x] RUN 与 CONVERSATION 都只记住完整参数完全相同的操作，不再用 Shell 前缀或 HTTP 主机扩大权限
- [x] SQLite 初始化时使旧 `command_prefix` / `command_contains` / `host_exact` 宽泛规则失效，并迁移旧会话作用域名称
- [x] 空作用域严格返回空结果；规则冲突固定为 DENY 优先，其次 ASK、ALLOW，并优先更具体的 RUN 规则
- [x] `PermissionHook` 集成策略引擎 + 规则存储 + 规则工厂；`ToolExecutor`/`AgentRuntime` 透传 `policy_engine`/`rule_store`
- [x] Executor 自动从 Store 构造 Policy，并拒绝 Policy 与 Store 指向不同实例的错误接线
- [x] Agent Run 在正常完成、失败和取消时清理 RUN 临时规则，不在 SQLite 中永久累积
- [x] `AgentEvent` 增加 `rule_id`/`rule_description`，Trace 记录"审批创建规则"与"规则命中放行"事实
- [x] CLI 接入 SQLite 规则存储（与会话/Trace 共用 oneagent.db），审批菜单第 3 项由 `describe_safe_rule` 生成
- [x] CLI 新增 `/permissions`、`/permission remove <规则ID>` 和 `/permissions clear`，支持查看与撤销当前会话规则
- [x] 新增命令拼接、HTTP 权限扩大、空作用域、DENY 优先、旧规则迁移、RUN 清理和 CLI 撤销测试
- [x] 全量验证：`pytest` 107 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过
- [x] 重构：`_compact_conversation_history` 从 CLI（`app/models/chat.py`）移入会话层 `app/conversation/history.py`，以 `compact_conversation_history` 公开导出，chat.py 与测试改用新位置

### 进行中：上下文管理（第一步：token 估算）
- [x] 安装依赖 `tiktoken==0.13.0`，加入 requirements.txt（Context management 段）
- [x] 新增 `app/context/` 包：`TokenEstimator`（估算文本/消息序列/工具定义/完整请求 token 数）
- [x] 精度策略：OpenAI 模型用 tiktoken 精确编码；**非 OpenAI 模型（qwen/deepseek/anthropic/其他）用 cl100k_base 近似 + 保守系数**（默认 qwen/deepseek=1.2、anthropic=1.15、other=1.25，向上取整，可自定义覆盖），避免低估导致上下文溢出
- [x] `TokenEstimator.factor_for(provider, model)` 暴露模型族识别与系数；runtime 传入 provider 使系数生效
- [x] `AgentEvent` 新增 `estimated_input_tokens` 字段
- [x] `AgentRuntime` 新增 `token_estimator` 参数；每次模型调用前估算 `request_messages + tools`，随 `MODEL_STARTED` 事件发射（Trace 自动持久化）
- [x] CLI 传入 `TokenEstimator()` 启用估算
- [x] 新增 `test_token_estimator.py`（估算器单测 + 保守系数 + Runtime 事件带估算）
- [x] 新增 `ContextManager`（`app/context/manager.py`）：`prepare(messages, tools, model, provider) -> ContextDecision`（当前不压缩、原样返回 + 估算），作为上下文策略层入口
- [x] `AgentRuntime` 改用 `context_manager` 参数（替代 `token_estimator`），每轮模型调用前经 `ContextManager.prepare` 取上下文与估算；`AgentEvent` 增加 `context_trimmed` 字段（当前恒 False）
- [x] CLI 传入 `ContextManager()` 启用
- [x] 全量验证：`pytest` 121 个用例全部通过，`ruff` 无告警
- [x] 阶段性窗口预算实现随后收敛为 `ModelCapabilityRegistry` + `ContextBudgetPolicy`
- [x] 模型族识别抽为公共 `model_family(provider, model)`（估算系数与窗口注册表共用）
- [x] `ContextManager.prepare` 计算并返回 `budget`；`AgentEvent` 增加 `context_window` / `input_budget` 随 `MODEL_STARTED` 发射
- [x] 验收达成：切换模型后按实际 Provider / Model 使用不同输入预算
- [x] 全量验证：`pytest` 127 个用例全部通过，`ruff` 无告警
### 完成：模型能力注册与动态上下文预算

#### Bad Case
- [x] 初版只设置 `requires_compaction=True`，即使估算输入已超过预算仍原样调用 Provider
- [x] Qwen 3.7 Plus 与 DeepSeek V4 Flash 的内置窗口仍按 128K 记录，与当前官方 1M 能力不符
- [x] Runtime 未显式配置输出上限时，预算预留值与 Adapter 实际发送值可能不一致
- [x] 显式能力覆盖仍调用 `provider_config()`，没有 API Key 时会被静默忽略
- [x] 上下文准备异常被归类成 `ModelInvocationError`，无法区分预算错误与模型 API 错误

- [x] 新增 `app/context/capabilities.py`：`ModelCapabilities`（provider/model/context_window/max_output_tokens/source）+ `ModelCapabilityRegistry`（查找优先级：用户覆盖 > 内置精确模型 > Provider 默认 > 保守兜底 32K）
- [x] 内置精确模型表登记 ModelSettings 默认模型（gpt-5.4-mini / gpt-4o-mini / qwen3.7-plus / deepseek-v4-flash / claude-sonnet-4-6），同 Provider 不同模型可不同窗口
- [x] 未知模型使用保守兜底（32K），记录 warning，不崩溃
- [x] 新增 `app/context/budget.py`：`ContextBudgetPolicy`（trigger=0.80 / target=0.60 / safety_margin=4096），`input_budget = window - reserved_output - safety_margin`；显式 max_output_tokens 优先；非法配置抛清晰错误
- [x] 配置覆盖：`ContextSettings` 新增 `context_override_provider/model`、`context_window_override`、`max_output_tokens_override`（作用于当前配置模型，不全局应用）
- [x] `ContextDecision` 展开预算状态字段（context_window/input_budget/trigger_tokens/target_tokens/usage_ratio/requires_compaction/capability_source 等）；estimated >= trigger 时 requires_compaction=True；消息原样返回
- [x] Runtime 修正模型解析顺序：先取 adapter → resolved_model/provider → prepare → complete（force_final_answer 同一流程）
- [x] `AgentEvent` 增加 usage_ratio/trigger_tokens/target_tokens/requires_compaction/capability_source
- [x] 修正 Qwen 3.7 Plus 为 1M/64K、DeepSeek V4 Flash 为 1M/384K，并验证能力值必须为正数
- [x] Runtime 统一解析 `effective_max_output_tokens`，预算与实际 ModelRequest 使用同一个值
- [x] 输入达到 trigger 时继续记录压缩需求；真正超过 input_budget 时停止 API 请求并返回 `CONTEXT_ERROR`
- [x] 新增 `ContextPreparationError` / `ContextWindowExceededError`，不再把上下文问题误报为模型错误
- [x] 显式模型能力覆盖不再依赖 API Key；请求输出不得超过模型能力上限
- [x] `.env.example` 补充上下文预算与模型能力覆盖配置
- [x] 测试：新增 `test_context_capabilities.py`、`test_context_budget.py`，重写 `test_context_config.py`
- [x] 全量验证：`pytest` 148 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过

### 完成：消息块划分
- [x] 新增 `app/context/blocks.py`：`MessageBlock` 基类 + `SystemBlock` / `ConversationBlock` / `ToolRoundBlock` 三类块 + `BlockType` 枚举
- [x] `partition_messages()`：连续 SYSTEM 合并为 SystemBlock；assistant(tool_calls)+紧随 TOOL 结果为 ToolRoundBlock；其余 user/无工具 assistant 按轮合并为 ConversationBlock
- [x] 块划分保持消息顺序，不修改原消息；供后续压缩以块为最小单元保留/丢弃
- [x] 新增 `tests/test_context_blocks.py`（系统+对话、工具轮、连续 system 合并、多工具轮独立、对话轮拆分、空序列、顺序保持）
- [x] 全量验证：`pytest` 158 个用例全部通过，`ruff` 无告警

### 完成：分层保留历史工具结果
- [x] `compact_model_history(messages, keep_recent_tool_rounds=N)`：最近 N 轮工具调用（assistant(tool_calls)+TOOL 结果）完整保留；更旧工具轮降级——TOOL 结果移除，assistant 带文本则去 tool_calls 保留纯文本、否则整条移除；SYSTEM/普通对话始终保留
- [x] 默认 `keep_recent_tool_rounds=0` 保持旧行为（全部移除），向后兼容
- [x] `ContextManager.prepare` 新增 `keep_recent_tool_rounds` 参数透传，仅作用于历史前缀，当前 Run 工具协议不受影响；`reason` 中记录该参数
- [x] 新增 `tests/test_context_history.py`（默认回归、保留最近 1/2 轮、带文本降级、保留轮数超上限、孤立 TOOL 移除、ContextManager 透传）
- [x] 全量验证：`pytest` 165 个用例全部通过，`ruff` 无告警
- [x] 后续完成：基于块的工具压缩、ContextManager 接入、历史滚动摘要与压缩可观测字段

### 完成：原始会话历史与模型请求上下文分离

#### Bad Case
- [x] CLI 在每次运行结束后先删除 assistant tool-call 和 tool result 再写入 SQLite，导致数据库不是完整事实记录
- [x] 恢复或切换会话时再次压缩历史，工具调用参数和原始结果无法从会话消息中还原
- [x] 会话持久化层承担模型 Token 优化职责，原始数据与请求视图边界混乱
- [x] 如果直接压缩整个 Runtime 消息列表，会误删当前 Run 正在使用的工具协议，导致下一次模型请求无法关联 ToolCall 与 ToolResult

#### 修复结果
- [x] CLI 始终把 `AgentResult.messages` 完整写入 SQLite，创建、恢复和切换会话均加载原始消息
- [x] 将工具协议整理函数从 `app/conversation/` 移到 `app/context/`，会话层只负责事实存储
- [x] `ContextManager.prepare()` 新增历史前缀边界，只整理已持久化的旧历史，完整保留当前 Run 的用户消息、工具调用和工具结果
- [x] Runtime 使用独立的模型请求上下文调用 Adapter，同时继续用原始消息生成 `AgentResult`
- [x] Token 估算与输入预算判断改为基于处理后的实际模型请求上下文
- [x] 上下文确实移除旧工具协议时设置 `context_trimmed=True`，便于事件和 Trace 观测
- [x] 新增 SQLite 完整工具协议恢复、ContextManager 边界保护和 Runtime 请求/结果分离测试
- [x] 全量验证：`pytest` 151 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过

### 完成：上下文第一层工具消息压缩

#### Bad Case
- [x] ContextManager 在 Token 判断前就整理历史，导致低于 80% 时也丢失完整 ToolCall / ToolResult
- [x] 达到 trigger 后只记录 `requires_compaction`，没有真正压缩到 target
- [x] 工具轮缺少 ID 对应关系校验，孤立、缺失或错配的 ToolResult 可能被当成普通对话或安全工具轮处理
- [x] 按单条消息删除容易拆散 assistant ToolCall 与对应 ToolResult，形成 Provider 无法理解的不完整协议
- [x] 缺少压缩前后 Token、压缩阶段、缩短结果数、移除工具轮数和下一层需求等观测字段

#### 修复结果
- [x] ContextManager 先对完整候选上下文估算；低于 trigger 时不划块、不调用 Reducer，消息对象与顺序原样返回
- [x] 新增 `ToolReducer`：达到 trigger 后先逐条缩短未保护的长 ToolResult，仍高于 target 时按最旧优先整体移除已完成 ToolRoundBlock
- [x] 每缩短一个 ToolResult、每移除一个 ToolRoundBlock 后重新估算，达到 target 立即停止
- [x] `partition_messages()` 成为唯一工具轮识别入口；ToolRoundBlock 强校验 ToolCall/ToolResult ID 集合完整匹配
- [x] 新增 `MalformedToolBlock`，对孤立、未完成、重复 ID 或错配工具协议保守完整保留
- [x] 默认保护最近 2 个历史工具轮；当前 Run、SystemBlock、ConversationBlock 和异常工具块永不由本层压缩
- [x] 新增配置：工具轮保护数、工具结果长度阈值、首部/尾部保留字符数，并验证首尾长度总和不超过阈值
- [x] ContextDecision 与 MODEL_STARTED 事件记录压缩前/后 Token 和占比、阶段、修改数、目标状态及下一层压缩需求
- [x] 最终超过 input_budget 时继续由 Runtime 返回 CONTEXT_ERROR，Provider 不会被调用
- [x] Runtime 集成验证 Adapter 收到压缩副本，而 AgentResult 和 SQLite 会话边界继续保存完整原始历史
- [x] 全量验证：`pytest` 179 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过

### 完成：项目学习记录

- [x] 新增 `docs/learning-notes.md`，区分每日任务日志与长期架构知识
- [x] 记录交互、编排、领域策略和基础设施四层结构及完整请求数据流
- [x] 记录 Runtime、模型适配、上下文预算、MessageBlock、ToolReducer、工具 Hook、权限审批、SQLite 与 Trace 的职责边界
- [x] 记录原始历史与模型请求视图分离、工具协议完整性、异常协议保守处理等关键设计原则
- [x] 记录当前未实现层次、后续上下文压缩方向、工程教训、代码阅读顺序和离线验证命令

### 完成：第二层滚动结构化摘要

#### Bad Case
- [x] WorkingContextLedger 需要维护额外工作状态、更新规则和事实来源，对当前上下文压缩目标过度设计
- [x] 工具层压缩后仍可能高于 target，Runtime 只能报 CONTEXT_ERROR，无法继续压缩普通历史
- [x] 如果直接覆盖 SQLite 消息，会破坏完整历史、会话恢复和审计能力
- [x] 摘要模型失败或生成内容反而更长时，不能继续有损删除原消息

#### 修复结果
- [x] 移除 WorkingContextLedger 方案，明确把稳定事实记忆推迟到未来 Memory 层实现
- [x] 新增 `RollingConversationSummary` 与 `ConversationSummaryState`，只保存结构化摘要和已覆盖的原始消息数
- [x] 新增模型无关 `ContextSummarizer` 接口及 `ModelContextSummarizer`，要求模型返回严格 JSON，摘要请求不携带工具定义
- [x] 新增 `ConversationReducer`：工具层仍未达到 target 时，摘要最旧普通对话并保护系统提示、当前 Run、最近普通对话、最近工具轮和异常工具协议
- [x] 摘要失败、覆盖位置失效或新摘要未缩短请求时保持原上下文，不删除任何消息
- [x] `ContextManager` 按“已持久化摘要复用 → ToolReducer → ConversationReducer”顺序准备实际模型请求
- [x] `AgentResult` 返回摘要状态；摘要调用 Token 纳入总用量，`AgentEvent` 记录摘要更新、块数、Token 和错误
- [x] 新增 SQLite 摘要存储；CLI 自动恢复和保存摘要，`/clear` 同时清除摘要缓存
- [x] SQLite `messages` 与 `AgentResult.messages` 继续保存完整原始历史，滚动摘要仅是模型请求缓存
- [x] 新增配置：最近普通对话保护数和摘要最大输出 Token；补充模型摘要、滚动更新、失败回退、SQLite、Runtime/CLI 离线测试
- [x] 全量验证：`pytest` 187 个用例全部通过，`ruff`、编译、CLI 参数与 Diff 格式检查通过

---

## 2026-08-04

### 完成：AgentRuntime 返回完整运行过程
- [x] 新增 `app/agent/result.py`，定义统一的 `AgentResult` 返回结构
- [x] `AgentRuntime.run()` 由返回单条 `Message` 调整为返回 `AgentResult`
- [x] `AgentResult.messages` 返回传入历史与本轮新增消息组成的完整消息历史
- [x] 返回最终消息、模型执行步数和停止原因
- [x] 按模型轮次记录工具调用，并同时提供扁平化工具调用记录
- [x] 汇总多轮模型请求的输入、输出和总 token 用量
- [x] 模型错误、重复工具调用和最大步数停止均返回结构化错误
- [x] 保留 `content`、`role` 便捷属性，兼容现有结果读取方式
- [x] 扩展 FakeModel 离线测试，覆盖正常完成、工具失败、模型错误、重复调用和最大步数
- [x] 修正 `AgentRuntime.tool_records` 注释，明确其返回执行器累计观测记录
- [x] 将 `app.models.chat` CLI 接入真实 `AgentRuntime`，使用 `result.messages` 维护多轮历史
- [x] CLI 注册全部 6 个内置工具，危险工具通过 `ConsoleApprovalGate` 人工审批
- [x] 提取公共 `build_builtin_tool_registry()`，供 CLI 与演示脚本复用
- [x] AgentRuntime 支持透传 `max_output_tokens`，CLI 新增 `--max-steps`
- [x] 全量验证：`pytest` 47 个用例全部通过，`ruff` 无告警

### 完成：SQLite 会话持久化与 CLI 恢复
- [x] 新增 `app/conversation/`，实现会话模型与 SQLite 存储
- [x] 持久化完整通用消息，包括 system/user/assistant/tool 与 ToolCall 参数
- [x] 数据库默认保存在 `backend/.oneagent/oneagent.db`，并加入 Git 忽略
- [x] CLI 启动时默认恢复最近会话，支持完整 ID 或唯一短 ID
- [x] CLI 新增 `/new`、`/sessions`、`/use <id>`，`/clear` 同步清空数据库历史
- [x] CLI 每轮使用 `AgentResult.messages` 更新 SQLite，并根据首条输入生成会话标题
- [x] 新增 `--database`、`--conversation`、`--new-conversation` 参数
- [x] 添加 SQLite 重启恢复、消息序列化、会话切换和 CLI 持久化离线测试
- [x] 全量验证：`pytest` 54 个用例全部通过，`ruff` 与 CLI 参数检查通过

### 完成：AgentEvent 事件模型
- [x] 新增 `app/agent/events.py`，定义统一 `AgentEvent` 与 `AgentEventType`
- [x] 事件包含唯一 `event_id`、`run_id`、可选 `conversation_id`、`sequence` 与 `step`
- [x] 新增带时区的 `event_time`，创建时使用 UTC，并将外部时区统一转换为 UTC
- [x] 事件载荷复用 Message、ToolCall、ToolResult、ModelUsage、AgentError 与 AgentStopReason
- [x] 事件模型设为不可变、禁止额外字段并支持 JSON 序列化往返
- [x] 添加事件 ID、UTC 时间、载荷序列化与非法参数离线测试
- [x] 全量验证：`pytest` 61 个用例全部通过，`ruff` 无告警

### 完成：AgentRuntime 事件发射
- [x] 新增 `AgentEventHandler`、`NullEventHandler` 与 `InMemoryEventHandler`
- [x] 每次 Runtime 运行生成唯一 `run_id`，并写入 `AgentResult`
- [x] 同一次运行的事件共享 `run_id` 和可选 `conversation_id`
- [x] 事件使用从 0 开始的连续 `sequence` 保证稳定顺序
- [x] Runtime 发射 Agent、模型、工具开始/完成及失败生命周期事件
- [x] CLI 将当前 SQLite 会话 ID 传给 Runtime，事件可关联会话
- [x] 事件处理器异常与 Agent 核心执行隔离，不会导致任务失败
- [x] 添加完整工具调用事件顺序、错误事件和处理器故障离线测试
- [x] 全量验证：`pytest` 62 个用例全部通过，`ruff` 与 CLI 参数检查通过

### 完成：Runtime 事件流与 CLI 实时进度
- [x] 最终 `AGENT_COMPLETED` / `AGENT_FAILED` 事件携带完整 `AgentResult`
- [x] 新增 `AgentRuntime.run_stream()`，通过异步队列复用现有 `run()` 循环
- [x] 调用方提前关闭事件流时自动取消后台模型任务，避免遗留执行
- [x] CLI 改为消费事件流，实时显示模型请求、工具执行和停止状态
- [x] 会话持久化继续使用最终事件中的 `AgentResult.messages`
- [x] 添加事件流顺序、最终结果传递、CLI 进度和取消行为离线测试
- [x] 全量验证：`pytest` 65 个用例全部通过，`ruff` 与 CLI 参数检查通过

### 完成：审批事件与 SQLite Trace
- [x] ToolExecutor 支持运行期审批回调，不改变现有 ApprovalGate 决策逻辑
- [x] Runtime 发射 `TOOL_APPROVAL_REQUIRED` 与 `TOOL_APPROVAL_COMPLETED`
- [x] 审批完成事件记录 approved / denied，观察者异常不影响授权结果
- [x] 新增 `app/trace/`，使用 SQLite 保存 Agent Run 摘要和完整事件
- [x] Trace 与 Conversation 共用 `oneagent.db`，但使用独立数据表
- [x] Trace 支持 Run 列表、完整/短 ID 查询、事件恢复、按会话过滤和删除
- [x] 事件写入幂等，完成状态不会因重复旧事件回退为 running
- [x] CLI 每轮自动持久化 Trace，新增 `/runs` 与 `/trace <run_id>`
- [x] 修复组合事件处理器接入后流结束信号发送目标错误导致的等待问题
- [x] 添加审批批准/拒绝、Trace 重启恢复、完成/失败、幂等和 CLI 落库测试
- [x] 全量验证：`pytest` 71 个用例全部通过，`ruff` 与 CLI 参数检查通过

### 完成：工具执行生命周期 Hooks 重构

#### 重构前 Bad Case
- [x] 工具生命周期分散：`AgentRuntime` 发射工具事件，`ToolExecutor` 处理权限和执行，Logger 单独记录结果，缺少统一扩展入口
- [x] Runtime 内嵌 `approval_callback`，导致模型编排层知道人工审批实现细节，职责越界
- [x] `ApprovalCallback`、`ToolExecutionLogger`、`AgentEventHandler` 三套扩展机制并存，新增审计或安全策略时容易重复接线
- [x] 工具开始和完成事件由 Runtime 手动包围 Executor，直接调用 Executor 与通过 Runtime 调用时生命周期行为不一致
- [x] 权限、审批事件和执行记录位于不同代码路径，异常分支容易漏记事件或日志
- [x] 普通观察逻辑与安全控制没有明确区分，无法表达“观察者失败可忽略、权限检查失败必须拒绝”的不同策略

#### 修复结果
- [x] 新增 `ToolExecutionContext`，统一传递运行 ID、会话 ID、步数、工具定义和参数
- [x] 新增 `ToolHook` 与故障隔离的 `ToolHookRunner`，统一工具执行前后和审批生命周期
- [x] 新增不可绕过的 `PermissionHook`，继续保持默认拒绝、禁止工具拦截和人工审批语义
- [x] 新增 `ObservabilityHook`，替代 `ToolExecutor` 内部散落的执行记录逻辑
- [x] 新增 `AgentEventHook`，统一产生工具开始、审批和工具完成事件
- [x] 精简 `AgentRuntime`，移除工具事件直发、内嵌审批回调和审批细节
- [x] 删除 `ApprovalCallback`，避免 Callback、Logger、AgentEvent 三套生命周期机制并存
- [x] 保持 Provider、Conversation、Trace、CLI 和 `AgentResult` 公共行为不变
- [x] 新增 Hook 上下文、执行顺序、故障隔离、权限不可绕过和审批失败关闭测试
- [x] 全量验证：`pytest` 77 个用例全部通过，`ruff` 无告警

### 完成：网页搜索审批、循环失控与 Token 放大修复

#### Bad Case 与现场证据
- [x] `web_search` 被配置为 `HUMAN_APPROVAL`，一次新闻任务中的每次只读搜索都要求人工确认，交互体验差
- [x] 搜索工具实际返回成功，但 Bing 结果相关性不稳定；模型把“结果质量不足”误处理为继续改写查询
- [x] Runtime 只检测参数完全相同的连续调用，模型通过不断修改关键词连续搜索 10 步，最终触发 `max_steps`
- [x] 失败 Run 共执行 16 次工具调用，模型累计报告 `125003 tokens`，但没有形成最终回答
- [x] `http_request` 返回的原始网页结果单次约 2 万字符，并在后续每轮模型请求中重复发送
- [x] CLI 把工具协议消息和完整工具输出写入会话历史，下一次普通追问仍消耗 `44343 tokens`
- [x] 恢复旧会话时缺少当前日期提示，模型在 2026 年仍持续搜索 2025 年新闻

#### 修复结果
- [x] 将受控、只读的 `web_search` 调整为 `ALLOWED`，搜索不再请求人工审批
- [x] `http_request` 和 `run_shell_command` 继续保留人工审批，避免任意网络请求和本地命令失去安全边界
- [x] 搜索结果上限调整为 5 条，标题、摘要和查询长度均设置明确上限
- [x] 工具说明加入当前日期、聚焦查询和禁止重复宽泛搜索的提示
- [x] AgentRuntime 新增 `max_tool_rounds`；CLI 默认最多 3 个工具轮次，之后隐藏工具并要求模型基于已有结果收尾
- [x] CLI 默认系统提示加入当前日期和工具节制策略
- [x] 跨轮会话历史移除中间 assistant tool-call 与 tool result 消息，避免原始网页内容持续重复计费
- [x] 完整工具过程仍保留在 `AgentResult` 和 SQLite Trace，不影响运行审计
- [x] 新增工具轮次收尾、历史压缩、免审批权限和搜索摘要截断离线测试
- [x] 全量验证：`pytest` 81 个用例全部通过，`ruff` 无告警

#### 二次验证：默认搜索源本身失效（阶段性方案，已由统一搜索层替代）
- [x] 最新 Trace 显示“谷歌新闻”错误返回 Ticketmaster，“石家庄天气”错误返回 Microsoft Community，确认不是模型误判
- [x] 直接联网测试确认 Bing HTML 与 RSS 在当前中国区出口返回低相关或错误结果，Bing News 端点被重定向到首页
- [x] DuckDuckGo、Google、Brave、Yahoo 在当前网络环境超时，继续切换免费网页解析端点无法保证稳定性
- [x] 验证 Open-Meteo 结构化天气接口可用，后续可独立实现 WeatherTool，不再让天气依赖通用网页搜索
- [x] 根据 Qwen 官方能力，为 `ModelRequest` 增加 Provider 原生工具字段，并由 Responses Adapter 合并原生工具与自定义函数工具
- [x] CLI 检测到 Qwen 3.7 系列时自动切换 Responses API，启用官方 `web_search` 并移除本地 Bing 搜索工具
- [x] Qwen 官方搜索在服务端完成检索与内容整合，不再进入本地 Bing HTML 解析和多轮 ToolCall 循环
- [x] 其他模型暂时保留本地搜索降级路径，后续按 Provider 接入对应的正式搜索 API
- [x] 新增 Responses 原生工具合并、Runtime 透传和 Qwen 3.7 能力选择离线测试
- [x] 全量验证：`pytest` 82 个用例全部通过，`ruff` 无告警

### 完成：Tavily 主搜索与 DuckDuckGo 无密钥降级

#### Bad Case
- [x] 依赖 Bing HTML/RSS 页面解析，当前网络出口会返回低相关结果、错误跳转或搜索首页，但工具仍可能被模型理解为搜索成功
- [x] Qwen 原生搜索只能覆盖单个模型系列，切换 GPT、Claude 或 DeepSeek 后搜索能力和结果结构不一致
- [x] 为每个模型分别适配服务端搜索会把 Provider 特例带入 Runtime，增加耦合和维护成本
- [x] 免费搜索端点被限流、反爬或返回空页面时，旧实现缺少明确的“提供商不可用”错误语义
- [x] 搜索结果未经统一去重和长度限制，容易把重复网页与过长摘要反复送入模型，放大 Token 消耗

#### 修复结果
- [x] 新增与模型无关的 `SearchProvider`、`SearchService`、请求/响应模型和统一错误体系
- [x] 配置 `TAVILY_API_KEY` 时以 Tavily REST API 为主搜索源，使用异步 `httpx`，不增加 SDK 依赖
- [x] 未配置 Key 时使用 DuckDuckGo Lite；Tavily 网络、限流或空结果时自动回退 DuckDuckGo
- [x] Tavily 鉴权错误不静默回退，避免错误 Key 长期被掩盖；两个来源均失败时返回明确的搜索不可用错误
- [x] WebSearchTool 对所有模型暴露相同工具协议，并保持只读 `ALLOWED` 权限，不触发人工审批
- [x] 统一限制查询长度、结果数、标题与摘要长度，并按规范化 URL 去重，减少无效上下文和 Token 消耗
- [x] 支持 `general/news/finance`、时间范围及包含/排除域名参数；CLI 启动时显示当前搜索源和降级策略
- [x] 新增 `backend/.env.example`，记录搜索配置项且不包含真实密钥
- [x] 新增 Tavily 请求映射、DuckDuckGo 解析、自动选择、降级、鉴权、双源失败和工具输出离线测试
- [x] 移除 Qwen 原生搜索特殊分支，Runtime 与 Provider Adapter 恢复模型无关边界
- [x] 全量验证：`pytest` 84 个用例全部通过，`ruff`、编译、CLI 参数检查和 Diff 格式检查通过

## 2026-08-03

### 完成
- [x] 通读项目代码，梳理整体架构：
  - 模型适配层（`app/models/`）：提供商无关类型 + OpenAI/Qwen/DeepSeek/Anthropic 适配器 + 注册表
  - Agent 主循环（`app/agent/runtime.py`）：ReAct 式"思考-行动-观察"循环，含 `max_steps` 上限与重复调用检测
  - 工具系统（`app/tools/`）：`ToolExecutor` 安全执行边界 + 内置文件工具（list/read/write）
- [x] 修复两处 Python 2 风格语法错误（Python 3 下无法导入）：
  - `backend/app/models/chat.py`：`except EOFError, KeyboardInterrupt` → `except (EOFError, KeyboardInterrupt)`
  - `backend/app/tools/executor.py`：`except TypeError, ValueError` → `except (TypeError, ValueError)`
- [x] 运行测试验证：`pytest` 23 个用例全部通过
- [x] 讨论 Agent 主循环与"规划层"的分层设计（当前循环仅为执行层，规划层尚未实现）

### 完成：完善工具层（本轮）
- [x] 新增工具：
  - `run_shell_command`（shell 命令执行，含超时终止进程组、工作目录限定）
  - `http_request`（通用 HTTP GET/POST/HEAD，含 SSRF 防护，默认拦截内网/回环地址）
  - `web_search`（网络搜索，默认走 DuckDuckGo lite，可注入 fetcher 便于测试）
- [x] 权限设计（三档）：`ToolPermission` = `ALLOWED` / `HUMAN_APPROVAL` / `FORBIDDEN`
  - 注册表 `definitions(for_model=True)` 对模型隐藏 FORBIDDEN 工具
  - 执行器：FORBIDDEN 直接拒绝；HUMAN_APPROVAL 走 `ApprovalGate`（默认 `DenyAllGate` 安全拒绝）
  - 审批门实现：`AutoApproveGate` / `DenyAllGate` / `ConsoleApprovalGate`（终端 y/N）
- [x] 可观测性：`ToolExecutionRecord` 记录每次执行的成功/失败/error 原因/耗时/权限档
  - `InMemoryExecutionLogger`（环形缓冲）+ `StructLogExecutionLogger`（structlog）
  - `AgentRuntime` 暴露 `tool_records`
- [x] 注册表增强：工具名校验、`unregister`、`names()`、`definitions(for_model=...)`
- [x] `httpx==0.28.1` 加入 requirements.txt 显式依赖
- [x] 新增测试 `test_tool_permissions.py`、`test_tool_extras.py`；全量 `pytest` 44 个用例通过，`ruff` 无告警

### 完成：web_search 修复与完善
- [x] 诊断 `web_search` 返回 0 条问题：DuckDuckGo（lite/ddg-html）在当前网络被反爬拦截（202 anomaly / SSL 重置），Bing 可用
- [x] 默认引擎 DuckDuckGo → **Bing**（`search_engine` 参数仍可选 duckduckgo）
- [x] 移除自定义 Chrome UA：实测 Bing 对浏览器 UA 返回**不含 `b_algo` 的无结果页**，对默认 httpx UA 返回可解析的标准 SERP
- [x] 重写 Bing 解析器：标题只取 `h2 > a`（不再混入域名面包屑）；摘要取标题后的 `p`；解码 `/ck/a` 重定向的 `u=` 参数（去 `a1` 前缀 + base64）还原真实 URL
- [x] 新增真实结构测试（域名链接 + 重定向 URL）；全量 `pytest` 47 个用例通过，`ruff` 无告警
- [x] 端到端在线验证：真实 Bing 返回 5 条干净结果（标题/URL/摘要正常）

### 今日总结（2026-08-03）
- ✅ **工具层已完成**：6 个内置工具 + 三档权限（ALLOWED / HUMAN_APPROVAL / FORBIDDEN）+ 审批门 + 可观测性记录
- ✅ 全量测试 47 个通过，`ruff` 无告警；`web_search` 真实 Bing 在线验证可用
- 📌 遗留待办：`chat.py` 接入 Agent 运行时/工具层；规划层设计；工具并行执行
- 🧪 演示入口：`backend/scripts/demo_tools.py --direct`（测工具层）/ `--agent "..."`（端到端）

### 进行中 / 待办
- [ ] 规划层设计（`task/`、`scheduler/`、`memory/` 目录目前为空）
- [ ] 工具层：工具并行执行、输出结构化、schema 字段级校验
- [ ] 将 `ConsoleApprovalGate` 接入 CLI / API（`chat.py` 目前仍是纯聊天，未接工具层），让人工审核真正可用
