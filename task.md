# OneAgent 任务日志

> 本文件用于记录每日开发任务与进展，作为项目留存。
> 追加规范：每日一个 `## YYYY-MM-DD` 小节，最新的日期放在最上方；任务用 `- [x] 已完成` / `- [ ] 未完成` 标记。
> 对架构调整和缺陷修复，应同时记录 Bad Case、影响、根因和修复结果，避免只记录最终功能。

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
- [x] 新增 `app/context/config.py`：`ContextSettings`（.env 可配窗口/预留输出/安全余量）+ `ContextWindowRegistry`（按模型族解析窗口）+ `ContextBudget`（输入预算 = 窗口 - 预留输出 - 安全余量）
- [x] 模型族识别抽为公共 `model_family(provider, model)`（估算系数与窗口注册表共用）
- [x] `ContextManager.prepare` 计算并返回 `budget`；`AgentEvent` 增加 `context_window` / `input_budget` 随 `MODEL_STARTED` 发射
- [x] 验收达成：切换模型（qwen=131072 / openai=200000 / anthropic=200000 / other=128000）后输入预算不同
- [x] 全量验证：`pytest` 127 个用例全部通过，`ruff` 无告警
### 完成：模型能力注册与动态上下文预算

- [x] 新增 `app/context/capabilities.py`：`ModelCapabilities`（provider/model/context_window/max_output_tokens/source）+ `ModelCapabilityRegistry`（查找优先级：用户覆盖 > 内置精确模型 > Provider 默认 > 保守兜底 32K）
- [x] 内置精确模型表登记 ModelSettings 默认模型（gpt-5.4-mini / gpt-4o-mini / qwen3.7-plus / deepseek-v4-flash / claude-sonnet-4-6），同 Provider 不同模型可不同窗口
- [x] 未知模型使用保守兜底（32K），记录 warning，不崩溃
- [x] 新增 `app/context/budget.py`：`ContextBudgetPolicy`（trigger=0.80 / target=0.60 / safety_margin=4096），`input_budget = window - reserved_output - safety_margin`；显式 max_output_tokens 优先；非法配置抛清晰错误
- [x] 配置覆盖：`ContextSettings` 新增 `context_override_provider/model`、`context_window_override`、`max_output_tokens_override`（作用于当前配置模型，不全局应用）
- [x] `ContextDecision` 展开预算状态字段（context_window/input_budget/trigger_tokens/target_tokens/usage_ratio/requires_compaction/capability_source 等）；estimated >= trigger 时 requires_compaction=True；消息原样返回
- [x] Runtime 修正模型解析顺序：先取 adapter → resolved_model/provider → prepare → complete（force_final_answer 同一流程）
- [x] `AgentEvent` 增加 usage_ratio/trigger_tokens/target_tokens/requires_compaction/capability_source
- [x] 测试：新增 `test_context_capabilities.py`、`test_context_budget.py`，重写 `test_context_config.py`
- [x] 全量验证：`pytest` 141 个用例全部通过，`ruff` 无告警
- [ ] 待办：真正的消息压缩（在 prepare 内超 trigger 后裁剪）、历史滚动摘要、可观测占比记录写回

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
