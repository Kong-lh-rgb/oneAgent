# OneAgent 任务日志

> 本文件用于记录每日开发任务与进展，作为项目留存。
> 追加规范：每日一个 `## YYYY-MM-DD` 小节，最新的日期放在最上方；任务用 `- [x] 已完成` / `- [ ] 未完成` 标记。
> 对架构调整和缺陷修复，应同时记录 Bad Case、影响、根因和修复结果，避免只记录最终功能。

---
## 2026-08-12

### 完成：MCP Client V1（stdio 工具闭环）

#### Bad Case
- [x] 如果在 Runtime 内直接识别和调用 MCP，会绕过现有 ToolExecutor 的权限、审批、超时、Hook 与执行日志
- [x] 多个 MCP Server 可能暴露同名或规范化后重名工具，直接注册会污染本地工具命名空间
- [x] 单个 Server 启动、工具发现或调用失败不应阻止其他 Server 和 OneAgent 主流程工作
- [x] MCP 多段内容、structuredContent 与 `isError` 若只取第一段文本，会丢失结果或把远端失败误判为成功
- [x] 把 API Key 直接写进 MCP JSON 容易误提交；配置又可以启动本地命令，必须明确其受信任边界

#### 实现结果
- [x] 新增 `app/mcp/`，定义严格配置、运行状态、错误类型、stdio Client、ClientManager 和 BaseTool 适配器
- [x] 基于官方 MCP SDK 完成 initialize、list_tools、call_tool 和正常关闭；启动与调用分别使用独立超时
- [x] MCP 工具以 `mcp__<server>__<tool>` 注册到现有 ToolRegistry，完整复用 PermissionHook、审批、ToolExecutor、输出截断与日志
- [x] 默认 MCP 工具权限为 `human_approval`；可信只读 Server 可显式配置 `allowed`，不根据远端 annotations 自动放权
- [x] 多 Server 逐个隔离启动；失败状态保存错误原因，已成功 Server 继续可用；单 Server 注册中途失败会回滚其工具
- [x] 保留多段 content 与 structuredContent，远端 `isError` 和协议异常统一转为工具执行失败
- [x] 支持 `.oneagent/mcp.json` 和 `--mcp-config`，环境值支持 `${ENV_VAR}` 引用；CLI `/mcp` 展示连接与工具状态
- [x] 离线测试覆盖配置、命名、故障隔离、冲突回滚、内容转换、真实 Fake stdio Server、调用错误/超时和 AgentRuntime 端到端闭环
- [x] 全量验证：`pytest` 388 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过
- [ ] V1 暂不支持 Streamable HTTP、Resources、Prompts、OAuth、自动重连与动态工具刷新

---
## 2026-08-12

### 完成：Memory 工具名改为下划线（DeepSeek API 拒绝点号）

#### Bad Case
- [x] DeepSeek（OpenAI 兼容）要求工具名匹配 `^[a-zA-Z0-9_-]+$`；`memory.read` / `core_memory.update` 等点分工具名在真实调用时 400：`Invalid 'tools[10].function.name'`
- [x] CLI `--help` 不触发模型调用，因此点号问题在离线/静态检查中未暴露，直到真实对话才暴露

#### 修复结果
- [x] 工具名统一改为下划线：`memory_read` / `memory_list` / `memory_create` / `memory_update` / `memory_archive` / `core_memory_update` / `core_memory_remove`
- [x] `ToolRegistry` 恢复仅允许 `[a-zA-Z0-9_]` 的严格工具名（点分命名与 Provider 协议冲突）
- [x] 同步更新 tools/prompts/index/models/runtime 引用与相关测试（test_memory_system、test_memory_reflection、test_agent_runtime、test_chat_sessions）
- [x] 全量验证：`pytest` 371 通过、`ruff`、`compileall`、`git diff --check` 通过

---
## 2026-08-12

### 修复：Reflection 同主题 UPDATE 漏判与 Eval 原始 I/O

#### Bad Case
- [x] 当前 Prompt 将 CREATE 的稀疏原则和 UPDATE 的知识修正使用同一保守偏置，用户明确确认同主题新规则时仍可能连续返回 NONE
- [x] Prompt 没有明确区分“本轮是否改了代码”和“本轮是否获得耐久项目知识”，系统外已完成的决定容易被忽略
- [x] Memory Eval 只保存 action/usage/最终文件，不保存 Reflection 完整输入、原始 JSON 和 NONE reason，真实失败无法直接复盘
- [x] memory-01 的中文关键字断言把英文 `vector database` 误报为否定事实丢失，说明机械字符串不能替代语义完整性判断

#### 修复结果
- [x] Reflection Prompt 明确稀疏增长主要约束 CREATE；已读同主题存在用户明确确认的 finalized/completed/corrected/extended 新规则时优先 UPDATE
- [x] 明确用户当前确认本身可以成为耐久证据，不要求当前 Run 必须执行代码或文件 mutation；提案、猜测和 Assistant 自述仍不能冒充确认
- [x] UPDATE 要求保留旧记录仍有效事实，以及否定、被拒方案、替代关系、数字限制和安全约束
- [x] `MemoryReflectionConfig.capture_raw_io` 默认关闭；Eval 单独开启并将完整输入和原始输出放入事件
- [x] Memory Eval 每个 Phase 写入 `artifacts/<phase>.json`，包含用户输入、最终回答、Reflection input/raw output/action/mutation/error
- [x] 新增 Prompt 边界、原始 I/O 开关和 Eval artifact 离线回归测试
- [x] 全量验证：`pytest` 378 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 完成：首轮长期记忆 Live Eval 结果归档

- [x] 读取 Qwen `qwen3.7-plus` 的 10 场景 × 3 次真实 Memory Eval 输出和原始报告
- [x] 将结果写入根目录 `evaluation.md` 的独立“长期记忆测评”大章节，与通用 Agent Runtime 测评分区
- [x] 使用表格记录基线信息、核心指标、Recall/Reflection/Maintenance 分区结果、逐场景稳定性、平均 Token/耗时和失败优先级
- [x] 原始自动结果为 27/33（81.8%）；人工复核发现 memory-01 三次均保留英文 `vector database` 否定事实，属于中文关键字断言误报，真实稳定缺陷集中在 memory-05 UPDATE 漏判
- [x] 保留原断言作为回归，不通过降低标准迎合当前模型；下一轮应采集 Reflection 原始输入/输出并补跑 Memory OFF 对照

### 完成：长期记忆多阶段 Eval V1

#### Bad Case
- [x] 单轮 Eval 只能检查一次 `memory_read`，无法证明记忆由前一会话产生、进程外持久化并在新会话被正确使用
- [x] Reflection、Main Agent 和 Maintenance 如果共用一个 Token 汇总，无法定位长期记忆的真实收益与额外成本
- [x] 场景中的 Memory ID 运行时动态分配，直接把 `M001` 写死在后续断言会让场景依赖预置顺序，难以扩展
- [x] 真实模型 Eval 不应进入 pytest，否则离线测试会产生 API 成本和随机失败

#### 实现结果
- [x] 新增独立 `tests/memory_eval/`，实现严格 YAML Schema、递归 Loader、多阶段 Runner、断言、指标、Markdown 报告和 Live CLI
- [x] 同一场景共享临时 Markdown Memory Store；相同 conversation 继承历史，不同 conversation 只共享长期记忆
- [x] CREATE/UPDATE 产生的动态 Memory ID 可绑定稳定别名，后续阶段用别名断言召回和文件内容
- [x] 每阶段采集 AgentResult、AgentEvent、Core/Index/active/archive 快照和耗时
- [x] 支持 `--compare-off` 运行 Memory OFF 对照；Main、Reflection、Maintenance Token 独立统计
- [x] 首批 10 条场景覆盖跨会话创建召回、一次性 NONE、Core/Task 分层、同主题 UPDATE、无关不读、Archive 隔离、相似干扰、当前证据纠错和满容量维护
- [x] 新增 4 条离线框架测试，验证跨会话隔离、动态别名、场景加载/校验和分阶段成本报告；pytest 不调用真实 API
- [x] 全量验证：`pytest` 376 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过
- [ ] V1 尚未接入独立 Judge；正文耐久性、重复主题和 Maintenance 语义质量目前依赖关键点断言，后续需增加 Judge + 人工抽查

### 完成：CLI Memory 命令提示与长期记忆测评方案

#### Bad Case
- [x] CLI 已实现 `/memories` 和 `/memory <ID>`，但启动提示与 `/help` 没有展示，用户无法从终端发现入口
- [x] 现有 Eval 以单次 Run 为单位，直接加入几个 `memory.read` 断言无法验证跨会话写入、召回、更新和维护闭环

#### 实现结果
- [x] CLI 启动命令提示和 `/help` 补充长期记忆列表、详情命令，并抽取为共享文本避免两处再次漂移
- [x] 新增 CLI 帮助文本回归测试
- [x] 新增 `docs/memory-evaluation.md`，明确确定性不变量测试与真实模型语义测评分层
- [x] 设计独立多阶段 Memory Eval：同一临时 Store 跨 Run/会话执行，采集 Main、Reflection、Maintenance、文件快照与分模型成本
- [x] 定义 Recall、Reflection、跨会话、Update、Maintenance 五组场景以及写入精度、召回精度、层级误写、关键记忆误归档等指标
- [x] 明确 Memory ON/OFF 对照、独立 Judge 与人工抽查原则，避免使用被测模型自评或让测试反向绑死策略
- [x] 全量验证：`pytest` 372 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 收口：普通 Memory 更新一致性与跨实例写入保护

#### Bad Case
- [x] Reflection UPDATE 只能替换正文，标题和 Recall Cue 保持旧值，导致 `INDEX.md` 与真实内容语义脱节
- [x] “本轮成功读取”只能证明模型看过旧内容，无法阻止另一个 Run 在读取后先更新同一记忆并被后写者覆盖
- [x] `asyncio.Lock` 只保护单个 `MemoryManager` 实例，两个 CLI 进程或独立 Manager 仍可能同时分配 ID、抢占最后容量或覆盖临时文件
- [x] `MemoryManager.create` 与内部 `memory.create` 仍可绕过 Reflection 使用的硬容量路径，25 条上限没有在统一领域入口成立
- [ ] Reflection 输入仍采用有界字符截断；重要证据落在截断区时可能漏记，需要后续用评测数据决定是否改成结构化摘取
- [ ] Windows 暂无标准库 `flock`，当前退化为单实例锁；POSIX/macOS/Linux 使用目录级 `.memory.lock`

#### 修复结果
- [x] `MemoryRecord` 新增持久化 `revision`，旧 Markdown 缺少字段时兼容迁移为 revision 1；更新和归档时递增
- [x] `memory.read` 返回当时的 revision；Runtime 同时验证成功读取结果和 revision，Reflection 基于旧版本更新时明确冲突并保持文件不变
- [x] Reflection UPDATE 现在必须返回完整 title、summary、content；Store 原子更新三者并重建 Index，Recall Cue 不再滞后于正文
- [x] `MemoryManager` 增加目录级文件锁；同目录的独立 Manager 与 POSIX 进程共享 mutation 临界区，覆盖 create/read/update/archive/Core mutation 与 Index 重建
- [x] `MemoryManager.create` 和内部 `memory.create` 都执行硬容量检查；只有低层 `MemoryStore` 可用于旧数据迁移和溢出修复测试
- [x] 新增旧格式 revision 迁移、Index cue 同步、陈旧 update 失败不改文件、Runtime 并发冲突隔离、双 Manager 争抢容量和内部工具不可越限测试
- [x] 全量验证：`pytest` 371 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 完成：普通长期记忆容量维护闭环

#### Bad Case
- [x] Reflection CREATE 先写入第 26 条再发出维护信号，active 上限只是软提示，默认 Main Agent 又没有 archive 工具，容量无法自动收敛
- [x] Reflection 同时承担模型判断和 Markdown mutation，难以在 CREATE 前插入容量协调，也不利于隔离模型语义与 Harness 不变量
- [x] 仅比较 `updated_at` 无法可靠发现维护模型调用期间的并发变化；Markdown 时间戳保存到秒，同一秒内更新可能绕过检查
- [x] Reflection 或 Maintenance 小模型的 provider/JSON/timeout 错误不应让已成功的 Main Agent Run 失败
- [x] 两个并发 Run 都看到最后一个空位时，普通的“先检查、后创建”可能同时写入并突破 25 条
- [ ] Retention Score 仍明显偏重时间信号，access_count 的保护较弱；当前只用于生成候选，不直接机械归档
- [ ] Maintenance V1 只实现 recoverable archive/defer，不实现需要多文件一致性的 Merge；archive 文件也尚无正式 restore API

#### 实现结果
- [x] `PostRunMemoryReflector` 收口为纯决策组件，只输出严格 none/create/update；Runtime/Harness 负责校验和应用 mutation
- [x] 新增独立 `MemoryMaintenanceReflector`，输入最多 5 条候选完整正文与 retention metadata，只能输出 archive/defer，不能修改正文、Merge 或选择候选外 ID
- [x] Reflection CREATE 在写入前检查容量；满 25 条时先归档一个未变化候选再创建，正常路径最终仍为 25 条；defer/失败时跳过新建且不删除旧记忆
- [x] `MemoryManager.create_if_capacity` 在同一锁内执行容量检查与创建，两个并发 CREATE 最多一个成功
- [x] 维护归档使用完整 `MemoryRecord` 乐观快照；正文、访问次数、访问时间或更新时间任一变化都拒绝陈旧归档
- [x] CREATE 内容在维护前完成领域模型校验，避免先归档旧记忆后才发现新记忆标题、摘要或正文非法
- [x] 已有 26+ 条的历史状态在正常 FINAL_ANSWER 后最多执行 3 次单动作维护，逐步恢复；达到动作上限会记录 remaining_overflow
- [x] Maintenance 可独立于 Reflection 处理既有超限；所有异常统一降级为事件，不改变 Main AgentResult
- [x] 新增 `MEMORY_MAINTENANCE_*` 独立模型、超时、候选数与动作数配置，默认继承 Reflection provider/model
- [x] 新增 maintenance started/completed/failed/skipped 事件；Trace 保存小模型明细但不污染 Main Agent provider/model/Token 汇总
- [x] CLI 展示 Reflection 与容量维护模型配置及实时维护状态
- [x] 离线测试覆盖满额归档后创建、defer、provider/JSON/timeout、越界 ID、陈旧快照、非法 CREATE、历史超限收敛、动作上限和并发 CREATE
- [x] 全量验证：`pytest` 366 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

## 2026-08-11

### 完成：普通长期记忆迁移至 Post-Run Memory Reflection

#### Bad Case
- [x] Main Agent 同时完成任务和判断普通 Memory CREATE/UPDATE/ARCHIVE，职责竞争会消耗 Agent Loop 步数并干扰最终任务
- [x] 普通 Memory 写工具常驻 Main Agent Registry，模型可能在任务尚未完成时过早沉淀临时状态
- [x] Reflection 若复用并写死主模型，无法用独立低成本模型，也无法单独限制输出和超时
- [x] Reflection provider error、timeout 或非法 JSON 若沿主调用链抛出，会把已经成功的用户任务错误标成失败
- [x] Reflection 输入若复制完整历史和全部原始工具输出，会形成新的上下文与 Token 膨胀
- [x] `AGENT_COMPLETED/FAILED` 先于 Reflection 发出，Trace 与 CLI 会出现“Run 已结束但仍继续产生后置事件”的生命周期倒置
- [x] UPDATE 的“必须掌握旧记忆完整正文”只写在 Prompt 中，模型仅凭 INDEX cue 也可能覆盖并丢失旧正文
- [x] Trace 汇总无差别接受 Reflection provider/model/usage，会把 Main Agent Run 错误显示成反思小模型并污染主任务 Token 摘要
- [x] Reflection V1 的第 26 条容量缺口已由 2026-08-12 的独立 Memory Maintenance Reflector 闭环

#### 实现结果
- [x] Main Agent 默认工具收口为 `memory.read`、可选 `memory.list`、`core_memory.update`、`core_memory.remove`；普通 create/update/archive 类保留为内部能力但不默认注册
- [x] 新增同步 `PostRunMemoryReflector`；仅 `FINAL_ANSWER` 且 checkpoint 完成后运行，不进入 Agent step loop，其他停止原因明确记录 skipped
- [x] Reflection 接收当前用户输入、最终回答、有界工具摘要、Core、Index 和当前会话 Task Context；严格输出单个 none/create/update 决策
- [x] 普通写入统一复用 `MemoryManager.create/update`，保留 Markdown Store、原子写入、访问元数据、INDEX rebuild、Maintenance 与 retention 算法
- [x] 新增独立 `MEMORY_REFLECTION_*` 配置；未指定 provider/model 时回退 Main Agent，独立限制 temperature、max output、timeout 与工具上下文字符数
- [x] Reflection 失败只产生 failed event，不覆盖成功的 AgentResult；事件记录 action、provider/model、latency、usage、error、memory ID 和容量信号
- [x] Agent 终止事件移动到 checkpoint 与 Reflection 之后，成为 Run 事件流中真正的最后一个 terminal event
- [x] Runtime 从成功且 `found=true` 的 `memory.read` ToolResult 生成 `recalled_memory_ids`；Reflector UPDATE 未命中该集合时拒绝写入并保持原文件不变
- [x] Trace 仍完整保存 Reflection 事件明细，但 Run 汇总的 provider/model/usage 只由 Main Agent 生命周期事件更新
- [x] Core 增加显式 remove 闭环；update/remove 均要求当前用户原话证据，Harness 只修改目标 key
- [x] 离线测试覆盖触发/跳过、默认工具边界、Core remove、NOOP/CREATE/UPDATE、独立模型、provider/JSON/timeout 隔离、INDEX rebuild 与第 26 条容量信号
- [x] 全量验证：`pytest` 355 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 完成：Core Memory 模型决策与 Harness 写入闭环

#### Bad Case
- [x] CoreMemoryManager 虽能更新整份 CORE.md，但没有 Runtime 工具入口，模型只能把明确的全局长期偏好错误地写进普通 Memory
- [x] 直接向模型暴露整份 CORE.md 覆盖能力会让一次错误调用破坏其他 Core 条目和人工维护内容
- [x] 只让模型声称“用户明确说过”无法形成证据边界，模型可能根据旧消息、Assistant 文本或自身推断修改 Core
- [x] 结构化 CORE.md 如果把 reason、用户原话和运行元数据全部注入模型，会浪费每次 Run 的常驻 Token
- [x] Core 正文已经包含 `# Core Memory` 时，Manager 再次添加标题会形成重复 System Prompt 标题

#### 实现结果
- [x] 新增 `core_memory.update(key, value, reason, explicit_user_statement)`；模型判断 Core 层级，Harness 验证并执行写入
- [x] Runtime 将当前 `user_input` 放入 ToolExecutionContext；Harness 要求 explicit_user_statement 必须逐字出现在当前用户消息中，拒绝旧消息、工具结果和模型推断作为 Core 证据
- [x] Core 按小写点分 key 执行 upsert，只更新目标条目并保留其他结构化条目与既有人工 Markdown，不向模型暴露整文件覆盖工具
- [x] CORE.md Front Matter 保存 value、reason、用户原话和 updated_at；每次 Run 只注入可见 Core 正文，不注入审计元数据
- [x] 更新后重新校验 2000 Token 上限并使用原子替换；超限失败不修改原 CORE.md
- [x] 同一 Run 通过 ToolResult 获得更新结果，下一 Run 自动加载新 Core；修复 Core 标题重复注入
- [x] 全量验证：`pytest` 338 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 收口：Sparse Memory 实现复核与文件一致性修补

#### Bad Case
- [x] `memory.update/archive` 虽要求 reason，但 Manager/Store 丢弃该字段，所谓“留痕”只存在于工具参数
- [x] archived 记录仍可通过普通 update 写回 `active/`，形成 archive 与 active 双份文件并重新进入 Index
- [x] Memory ID 直接拼接文件路径且未按 `M\d{3,}` 校验，模型输入可能成为路径穿越载体
- [x] INDEX 只在写操作后重建；缺失、人工改动或上次中断造成的陈旧 Index 会在后续 Run 持续注入
- [x] 并发 create 可同时计算出相同的下一个 ID，导致 Markdown 文件互相覆盖
- [x] archive 先写目标再删除源文件，中断窗口可能同时保留 active 与 archive 两份记录
- [x] CORE.md 只在 API update 时检查 Token，人工编辑可绕过 2000 Token 上限
- [ ] active 超限后的最终收敛仍依赖模型遵守 Maintenance 指令；模型错误或 max_steps 耗尽时不能在“不自动归档”的前提下机械保证立即回到 25 条

#### 修补结果
- [x] update/archive reason 写入 Front Matter；归档记录禁止普通更新，目录、Front Matter status 与文件名 ID 必须一致
- [x] 所有模型输入的 Memory ID 先严格规范化，拒绝路径分隔符、前缀和非法字符
- [x] MemoryManager 串行化读写；启动时按 active 文件重建 INDEX，保证 INDEX 始终是 Store projection
- [x] archive 改为更新状态后执行同文件系统原子移动；启动时自动修复移动阶段中断留下的错位 archived 文件
- [x] Memory 文件增加 512KB 写入上限，标题和 Recall Cue 增加紧凑长度限制；CORE.md 加载时也执行 Token 上限检查
- [x] 普通 Memory 正文限制为 12000 字符，低于 ToolExecutor 的 20000 字符输出上限，避免创建后无法通过 memory.read 完整取回
- [x] 初版由 Main Agent 在结束 Run 前处理 Maintenance；Post-Run 重构后保留候选算法与容量信号，自动归档执行者列为未解决 Bad Case
- [x] ToolRegistry 只允许合法的点分工具名，保留 `memory.read` 等语义命名而不放宽为任意点号组合
- [x] 全量验证：`pytest` 333 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 决定：冻结 Memory V1，等待重新设计

#### Bad Case
- [x] Memory V1 把主模型限制为被动消费召回结果，模型没有主动 search、remember、update、forget 的记忆能力
- [x] 所有自动提取一律降为 Candidate，连用户明确表达的偏好、决定和“请记住”也需要二次确认，降低本地助理的自主性与使用体验
- [x] 同 key 的新 Candidate 无法在确认时原子替代旧 Active FACT，谨慎状态机反而阻断了正常事实更新
- [x] 固定关键词路由、每 Run 固定召回和后台 Extractor 把过多语义选择收进 Harness，主模型只负责填写结构化候选
- [x] 向量检索缺少相关度拒绝，只要存在 Active Memory 就可能向无关请求注入“最近但不相关”的内容
- [x] Manager、Extractor、Router、Writer、Retriever 分散承载策略，模型自主权、用户主权和数据不变量的边界不够清晰

#### 暂停结果
- [x] 冻结 Memory V1，不继续围绕旧架构补丁式增加确认、路由和晋升规则
- [x] CLI 停止装配 Memory V1；即使旧环境变量仍在，也不会执行自动召回和回答后提取
- [x] 旧 Memory CLI 命令不再出现在帮助信息中；直接输入时明确提示 V1 已冻结
- [x] 保留 SQLite、FTS5、sqlite-vec、领域模型和离线测试，作为后续设计取舍与回归参考，不进行破坏性删除
- [x] 全量验证：`pytest` 317 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过
- [x] 已由 Sparse, Model-Directed Memory 重构重新定义模型自主操作与 Harness 文件一致性边界

### 完成：Memory V1 稳定性收口

#### Bad Case
- [x] vec0 先做全库 Top K 再过滤 namespace/status，其他项目和失效记忆可能挤占候选，形成不泄露但漏召回的问题
- [x] 仅凭“记住/必须”等关键词允许自动 Active 仍可能误判反问、引用或否定句
- [x] 自动写入固定落到 user:local，项目决定与用户偏好混在同一 namespace
- [x] Memory 错误虽然降级，但缺少检索/提取耗时、Token、动作和失败原因，Trace 无法解释“为什么没记住”
- [x] Run 在最终回答后同步等待 Memory LLM 和 Embedding，辅助能力增加用户响应延迟
- [x] 只有单元断言，没有 Recall@K、MRR、namespace/status 违规等专用 Memory Eval 指标

#### 收口结果
- [x] 新 vec0 使用 namespace TEXT PARTITION KEY 和 status metadata；每个允许 namespace 在 KNN 阶段直接限定 active，跨 namespace 结果按 cosine distance 合并
- [x] 自动迁移旧 memory_vectors：原向量无损复制到 memory_vectors_v2 并补入 namespace/status，事务完成后移除旧索引
- [x] 所有 LLM Extractor 输出统一降为 Candidate；只有用户 CLI confirm 或真实任务 learn_from_use 才能 Active
- [x] 新增可信 MemoryNamespaceRouter：项目/仓库/代码相关内容路由到配置允许的 project:*，其他内容回到默认 user namespace；模型不能自由指定 namespace
- [x] AgentEvent 增加 Memory retrieval/observation started/completed/failed，记录 namespace、动作、记忆 ID、耗时、错误和提取模型 usage，并由现有 Trace Store 持久化
- [x] Memory Observe 改为受 Runtime 管理的后台任务；AgentResult 和 AGENT_COMPLETED 不等待提取，CLI/进程退出时通过 drain 确保已提交观察完成
- [x] 新增 Memory Eval 指标 Recall@K、MRR、namespace violations、inactive violations，并加入大量噪声 namespace 下的隔离召回场景
- [x] 测试覆盖旧 vec 索引迁移、查询期过滤、默认 Candidate、namespace 路由、后台非阻塞、事件 usage 和 Eval 隔离
- [x] 全量验证：`pytest` 317 个用例通过；`ruff`、`compileall` 和 `git diff --check` 通过

### 完成：Memory CLI 管理闭环

#### Bad Case
- [x] Candidate 已能持久化，但用户无法在终端查看和确认，生命周期只有内部 API，没有可操作入口
- [x] 若直接按全库 ID 前缀查询，配置范围之外的 namespace 会参与歧义判断，甚至泄露记忆存在性
- [x] 已经 active 的记忆可被重复 confirm，已 archived 的记忆可重复 archive，导致 revision 和确认计数失真
- [x] 管理操作如果不携带 expected_revision，可能覆盖刚刚发生的状态变化

#### 实现结果
- [x] CLI 新增 `/memories [状态|all]`；默认展示 candidate + active，也可按 candidate/active/superseded/archived 过滤
- [x] CLI 新增 `/memory <ID>`，展示完整 ID、状态、类型、namespace、key、revision、重要度/置信度、访问/使用/确认次数、来源和替代链
- [x] CLI 新增 `/memory-confirm <ID>` 和 `/memory-archive <ID>`；Memory 未启用时给出明确配置提示，不把命令发送给模型
- [x] Store 增加受 namespace 限制的完整 ID/唯一前缀解析，先过滤允许范围再判断唯一性；标识符只接受 4–32 位十六进制
- [x] 生命周期收紧为 candidate 才能 confirm，candidate/active 才能 archive；superseded/archived 不能通过管理入口恢复或重复归档
- [x] CLI 状态修改先解析当前记忆，再携带 revision 作为 expected_revision，保留并发冲突检测
- [x] 模型仍没有 Memory 管理工具；确认和归档目前只属于用户终端权限
- [x] 新增 namespace 同前缀隔离、非法生命周期、状态过滤和终端渲染测试
- [x] 全量验证：`pytest` 313 个用例通过；`ruff`、`compileall`、CLI help 和 `git diff --check` 通过

### 完成：重构长期记忆为 Sparse, Model-Directed 系统

#### 设计（替换 Memory V1）
- [x] 删除 SQLite Memory Store、FTS5、sqlite-vec、Embedding、RRF、query-driven 自动检索与 before_run Top-K 注入（覆盖此前“保留 V1 作参考”的决定）
- [x] 持久化改用 Markdown 文件：`CORE.md` / `INDEX.md` / `active/Mxxx.md` / `archive/`，不使用 SQLite / FTS / Embedding / Vector Search
- [x] 只两层：Core Memory（每次 Run 注入，≤2000 tokens，不参与淘汰）+ 普通长期记忆（≤25 条）
- [x] `INDEX.md` 是 Memory Store 的 projection，只含 Recall Cue（id+title+summary），create/update/archive 后自动重建
- [x] 初版曾向 Main Agent 暴露全部普通 Memory 写工具；现已由 Post-Run Reflection 取代，Runtime 仍不做自动检索、不注入完整正文
- [x] 容量维护：active >25 触发，启发式选 3~5 个候选，KEEP/MERGE/ARCHIVE 由模型决定
- [x] Core 受控更新（`CoreMemoryManager`），模型不能随意改 Core

#### 模块与集成
- [x] `app/memory/`：models.py、store.py、index.py、core.py、maintenance.py、tools.py、manager.py、prompts.py
- [x] Runtime 移除自动 `context_message(query)` 检索注入与后台 `observe`；改为一次性注入 Core + Index + Policy（ephemeral，不持久化），memory 故障不阻塞 agent
- [x] 移除 `MEMORY_RETRIEVAL` / `MEMORY_OBSERVATION` 事件与字段；`requirements.txt` 移除 `sqlite-vec`、加入 `PyYAML`

#### 测试
- [x] 新增 `tests/test_memory_system.py`（28 例：Core/CRUD/元数据/Index/容量/Runtime 注入/Policy/工具）
- [x] 适配 `test_agent_runtime.py`（FakeMemoryManager → context_messages）与 `test_chat_sessions.py`
- [x] 全量验证：`pytest` 通过、`ruff`、`compileall`、CLI help 通过

---

## 2026-08-09

### 完成：Memory V1 生命周期与混合检索闭环

#### Bad Case
- [x] 把聊天记录直接向量化会同时保存大量寒暄、普通回复和临时工具结果，长期污染召回结果
- [x] LLM 判断“值得记住”后直接成为永久事实，会把模型猜测升级为用户事实
- [x] 仅依赖向量检索容易漏掉项目名、错误码和 Feature ID；仅依赖关键词又无法处理同义表达
- [x] 新事实直接覆盖旧事实会丢失来源和变化原因；召回上下文写回聊天历史则会反复膨胀
- [x] sqlite-vec 仍是 pre-v1，浮动依赖和静默降级会让不同环境产生不可解释行为

#### 实现结果
- [x] 领域模型收口为 FACT / EPISODE / PROCEDURE；namespace 支持 global、user、project、task 等任意隔离边界
- [x] 生命周期为 candidate / active / superseded / archived；候选只有经过用户确认或真实任务采用后才晋升，确认次数和使用次数分别记录
- [x] 每条记忆保存 normalized_content、SHA-256 fingerprint、importance、confidence、source session/run/message、访问遥测、替代链和 revision
- [x] `SQLiteMemoryStore` 在同一个 `oneagent.db` 中维护 memories、FTS5 和 sqlite-vec vec0；事实写入、索引写入和冲突替代共用事务
- [x] 指纹处理精确重复；active FACT 同 namespace/key 的新事实原子替代旧事实，旧记录保留为 superseded
- [x] `MemoryWriter` 实现 Rule Filter 后的写入边界、每 Run 3 条/Session 5 条/Day 20 条预算、候选确认与使用晋升
- [x] `HybridMemoryRetriever` 并行使用 FTS5 BM25 Top 20 和 Vector Top 20，以 RRF 合并并加入小幅 importance bonus，最终返回 3–5 条可解释结果
- [x] Embedding 通过 `MemoryEmbedder` 抽象；测试使用确定性 Hash Embedder，生产支持独立 OpenAI 兼容 Embeddings API
- [x] `ModelMemoryExtractor` 复用 Provider Adapter，结构化提取最多 3 条记忆；模型推断只能写 candidate
- [x] 所有自动提取结果统一写为 candidate；Assistant 和 Extractor 都不能直接晋升，用户确认与真实任务使用是仅有的普通晋升路径
- [x] `MemoryManager` 向 Runtime 暴露 retrieve/observe/confirm/learn_from_use；Runtime 不感知 SQLite、FTS5、向量、RRF 或 fingerprint
- [x] Memory 以临时 system message 进入 ContextManager 预算与压缩流程，不进入 AgentResult 或 SQLite 原始聊天历史；Memory 故障降级时不阻断 Agent 主流程
- [x] CLI 通过 MEMORY_ENABLED 显式启用，并配置独立 embedding key/base URL/model/dimensions/namespaces；未启用时不产生额外模型调用
- [x] 固定 `sqlite-vec==0.1.9`，启动时验证扩展加载和向量维度，失败给出明确错误
- [x] 离线测试覆盖扩展加载、FTS/Vector/RRF、namespace 隔离、去重、冲突历史、候选不可召回、确认/使用晋升、预算、归档、访问遥测和 Runtime 临时注入
- [x] 全量验证：`pytest` 309 个用例通过；`ruff`、`compileall`、CLI help 和 `git diff --check` 通过

### 完成：上下文压缩 V1 稳定性收口

#### Bad Case
- [x] CLI 固定传入 `max_output_tokens=1024`，覆盖 Provider 默认 4096；DeepSeek 主 Agent 保留 reasoning 时可能耗尽输出预算，Eval 与真实终端配置不一致
- [x] 摘要紧凑约束只存在于 Prompt，模型可返回过多条目、过长文本或整体过大的合法 JSON
- [x] 空内容、非法 JSON 和 did-not-reduce 均直接回退；上下文已经超预算时，没有一次受控修复机会
- [x] AgentEvent 已有 `summary_error` 和预算字段，但 Eval 失败报告没有展示，需额外重跑才能归因

#### 收口结果
- [x] CLI 未显式指定 `--max-output-tokens` 时传入 None，由 Runtime 使用 `ProviderConfig.default_max_output_tokens`；显式参数继续优先
- [x] `ModelContextSummarizer` 对目标长度、每字段安全上限、单条长度和摘要总字符数执行代码级硬校验；Prompt 建议每字段 5 条，硬上限 8 条，避免把轻微超出软目标但确实更短的有效摘要误拒绝
- [x] `ContextSummarizer` 增加唯一重试入口；空内容、非法 JSON、Schema/长度错误或摘要不减反增时最多重试一次，第二次仍失败则完整保留原历史
- [x] 重试提示携带精简失败原因，并再次强调优先保留用户约束、关键决定、当前状态和未完成事项
- [x] 两次摘要请求的 Token 用量统一累加到 `AgentResult.usage`；失败响应已有用量也不会漏记
- [x] Eval 压缩详情增加 input budget、trigger、target、summary_updated 和 summary_error
- [x] 新增 Provider 默认输出预算、显式覆盖、非法摘要重试、did-not-reduce 重试、用量累计和失败最多重试一次测试
- [x] 全量验证：`pytest` 294 个用例通过；`ruff`、`compileall`、`git diff --check` 通过
- [x] DeepSeek Live Eval：eval-21、eval-23 首轮通过；eval-05 暴露“软目标 5 条被当作绝对上限”的误拒绝，区分建议目标与安全硬上限后重跑通过

---

## 2026-08-06

### 完成：Run Checkpoint V1——中断边界与安全恢复证据

#### Bad Case
- [x] Task 只能表示最后确认的业务进度；工具产生副作用后、`task_update(done)` 前中断时，无法判断动作未执行、执行中还是已经成功
- [x] Trace 是可失败的观察层，Runtime 会隔离事件处理器异常，不能作为关键恢复状态源
- [x] CLI 只在 Run 正常结束后保存完整会话，中断时本轮 user message 也可能尚未进入会话历史
- [x] 遗留 `running` 没有明确转为 interrupted，恢复时容易盲目重试具有副作用的工具

#### 实现结果
- [x] 新增 `app/checkpoint/`：`RunCheckpoint`、`CheckpointStatus`、`CheckpointPhase`、`SQLiteCheckpointStore` 与恢复上下文渲染
- [x] 复用现有 `oneagent.db` 的独立 `run_checkpoints` 表，不新增数据库；保存原始 user message、step、phase、pending ToolCall、已确认 ToolResult、终态、错误、时间和 revision
- [x] Runtime 在 Run 开始、模型请求前、工具批次执行前、每个工具结果后和 Run 终态直接写 Checkpoint；Checkpoint 是关键路径，不依赖可忽略的 Event Handler
- [x] 状态：running / completed / failed / interrupted；阶段：starting / model_request / tool_execution / tool_results_ready / finished
- [x] 工具执行前先持久化 pending；只有获得统一 ToolResult 后才移入 completed。中断时保留 pending，明确表达“执行结果未知”
- [x] Runtime 被取消或异常退出时标记 interrupted；非正常进程退出留下的 running 在 CLI 启动/切换会话时转换为 interrupted
- [x] 下一次同会话 Run 临时注入最近未恢复 Checkpoint，包含原始用户请求、未决工具和已确认结果；不写入 AgentResult/SQLite 聊天历史
- [x] 恢复提示明确要求先查 Trace/实际环境，副作用工具禁止盲目重试；参数提示最多保留 4000 字符，完整参数仍在 Checkpoint
- [x] 后续 Run 正常结束后记录 `recovered_by_run_id`；新 Run 失败或再次中断时保留旧恢复证据
- [x] CLI 新增 `/checkpoints`，启动发现中断 Run 时显示 phase、step 和待核对工具数
- [x] 测试覆盖 Store 生命周期、跨重启、遗留 running、pending 不可跳过、Runtime 完成/失败、模型取消、工具取消、恢复上下文不污染原始历史
- [x] 全量验证：`pytest` 271 个用例全部通过；`ruff check .`、`compileall app tests`、`git diff --check` 通过

### 完成：Task V1 收口——严格会话私有与状态机不变量

#### Bad Case
- [x] `conversation_ids` 允许一个 Task 被多个会话共享，owner 可在更新时继续追加，无法形成不可变的任务归属
- [x] 模型工具缺少 conversation context 时会退化成全局访问，跨会话 ID 前缀还可能提前产生歧义
- [x] 任务步骤可同时有多个 `in_progress`，done/blocked 可不留依据，paused/completed 与步骤状态可能互相矛盾
- [x] 普通更新可以回退 done、删除已开始步骤、重开终态任务，任务文件虽然可写但状态不可信

#### 收口结果
- [x] Task 使用不可变 `owner_conversation_id`；`task_create` 只从 `ToolExecutionContext` 绑定 owner，Patch 不提供修改 owner 的入口
- [x] `task_list/get/update` 缺少 conversation context 直接拒绝；跨会话统一表现为“任务不存在”
- [x] ID 前缀先按 owner 过滤再判断唯一性，不同会话的相同前缀互不干扰
- [x] 旧 JSON 只有一个 `conversation_ids` 时原子迁移为 owner，revision 和业务时间不变；为空或多个时记录 warning 并禁止模型访问
- [x] 领域不变量统一放入 `Task` / `TaskStep` 校验和 `FileTaskStore.apply_patch` 路径：单一 in_progress、done/blocked 必须有 note、paused 无 in_progress、completed 的全部步骤 done
- [x] 普通更新禁止回退 done、删除或回退 done/in_progress、恢复 completed/failed/cancelled；整体 steps 与单步骤更新互斥
- [x] 所有更新继续保持 revision 冲突检测、任务级异步锁、内存完整校验和原子替换；非法组合测试逐项验证文件字节不变
- [x] `TaskContextProvider` 继续只按当前会话 owner 读取活动任务，以临时 system 消息注入模型请求，不写入原始聊天历史
- [x] 验收覆盖 A/B 会话 list/get/update 隔离、跨 owner 同前缀、缺失 context、全部状态非法组合、旧 JSON 迁移和 Runtime 注入
- [x] 全量验证：`pytest` 260 个用例全部通过；`ruff check .`、`compileall app tests`、`git diff --check` 通过

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
- [x] `app/task/models.py`：`TaskStatus`（pending/active/paused/completed/failed/cancelled）、`TaskPriority`、`TaskStepStatus`（todo/in_progress/done/blocked）、`TaskStep`、`Task`（goal/constraints/state/key_facts/steps/owner_conversation_id/run_ids/created_at/updated_at/completed_at，文本折叠与去重校验）
- [x] `app/task/store.py`：`FileTaskStore`（任务以独立 JSON 文件存储；create/get/resolve 前缀/list(status)/delete；update_goal/update_state/add_constraints/add_key_facts/replace_steps/set_step_status/set_status/attach_run；终态维护 completed_at）
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
- [x] 隔离原则：任务归属由不可变 `owner_conversation_id` 决定；带有效会话上下文时强制按会话隔离
  - `task_list`：只返回当前会话的任务（`store.list(owner_conversation_id=...)`）
  - `task_get` / `task_update`：只能操作属于当前会话的任务，其他会话统一按“任务不存在”处理（隐藏存在性）
  - `task_create`：自动绑定创建它的会话（原有）
- [x] 模型工具缺少会话上下文时拒绝执行；真实运行始终携带会话上下文
- [x] `store.list/resolve/apply_patch` 支持 owner 过滤；tools 使用 `_resolve_owned` 和 `execute_with_context` 获取可信会话上下文
- [x] 测试：跨会话 list 过滤、get/update 跨会话拒绝（含执行器路径）、store list 按会话过滤
- [x] 全量验证：`pytest` 231 个用例全部通过，`ruff` 无告警

### 完成：步骤状态需留依据（step_note）
- [x] 问题：模型可无凭据地把步骤标记为 done 或 blocked，之后无法回溯"为什么完成了 / 为什么卡住"
- [x] 约束：`task_update` 将 `step_status` 置为 `done` 时必须提供非空 `step_note`（完成依据）；置为 `blocked` 时必须提供非空 `step_note`（阻塞原因，如"缺少用户提供的实验结果文件"）。系统不校验内容真假，只强制留痕
- [x] 领域模型对单步骤更新和 `steps` 整体重排统一强制；`in_progress`/`todo` 不强制 note
- [x] 任务可进入 `paused`：当步骤 blocked（等待用户输入/外部条件）时，建议把任务置为 paused，使下次恢复时模型明确知道在等什么；工具 `status` 描述已引导该用法
- [x] 工具定义描述同步说明该要求；空字符串不算依据
- [x] 测试：done 无 note 拒绝、blocked 无 note 拒绝、blocked 有原因成功、in_progress 无 note 允许、任务 paused→active 恢复、runtime 集成测试补 note
- [x] 全量验证：`pytest` 237 个用例全部通过，`ruff` 无告警

### 完成：自建轻量 Eval Harness（v1 测评框架）
- [x] 策略确定：自建（不引 LangSmith/DeepEval/Inspect）；直接驱动真实 `AgentRuntime` 并读取 `AgentResult`/事件/`FileTaskStore`/workspace 内部状态
- [x] 两套运行：`pytest` 用 Mock 模型自检 harness（CI 可跑）；`tests.eval.run_live` 用真实模型跑场景
- [x] 场景 YAML（`tests/eval/scenarios/`）：初始历史/预置 Task/文件、用户输入、Runtime 限制、审批/上下文覆盖、期望（工具 must/must_not/no_successful、Task 状态/步骤、文件、回答关键点/任一、是否压缩）
- [x] 评分宽松：工具只查必须包含/禁止包含/参数关键值；步骤支持 status_any；回答支持 keypoints（全含）与 any_of（任一）
- [x] 指标与报告（`metrics.py`）：场景通过率、工具选择准确率、Task 状态正确率、安全组通过率、平均 steps/工具调用/tokens/耗时、失败归因；Markdown 报告存 `tests/eval/reports/`
- [x] 首批 6 条场景：简单问答不建 Task、读取文件、工具失败不宣称完成、复杂请求创建 Task、压缩后遵守目标、审批拒绝不执行
- [x] Harness 自检：`tests/test_harness.py`（mock 模型验证加载/运行/预置/评分/报告，6 例全通过）
- [x] 运行：`pytest tests/test_harness.py`（离线）；`.venv/bin/python -m tests.eval.run_live [--group/--scenario/--runs]`（真实模型）
- [x] 全量验证：`pytest` 277 个用例全部通过，`ruff` 无告警
- [ ] 待办：跑通 6 条 live 场景 → 扩到 20–30 条（basic/tools/task/context/safety）→ 波动大场景跑 3 次 → 失败归因沉淀

### 完成：Eval Harness 误判修复与断言增强

#### Bad Case
- [x] `created: false` 被解释为运行后 Task 总数为零，导致预置 Task 的场景必然误判，并提前跳过目标与步骤检查
- [x] 工具断言只确认调用名称，不确认成功、失败、次数和顺序；同名多次调用只检查最后一次参数
- [x] 模型以 error/max_steps 等原因停止时，只要返回 `AgentResult` 就会被视为正常运行
- [x] 未声明某维度期望的场景仍进入准确率分母，工具与 Task 指标会被无关场景稀释
- [x] 压缩场景没有制造足够预算压力，且 Harness 未接入滚动摘要器，报告中的压缩失败不具备归因价值

#### 修复结果
- [x] 保存初始 Task ID 快照；`created` 改为检查本轮新增，支持 `new_count`、初始 Task alias、`target: new` 和明确目标选择
- [x] 工具断言增加 successful/unsuccessful/no_successful、精确次数、总次数、有序子序列、任意一次参数匹配和审批拒绝事件
- [x] 默认只接受 `final_answer`，负面场景可用 `stop_reason_any` 显式声明合法停止原因
- [x] 检查结果增加 applicable/skipped 语义，指标只统计真正声明了该维度期望的场景
- [x] 压缩断言要求达到触发线、压缩阶段非 none 且请求上下文确实变化；压缩场景接入与 CLI 同类的滚动摘要链路
- [x] 场景加载增加冲突、重复 ID、未知工具、隐藏必需工具和 Task target 校验；预置文件拒绝绝对路径与 `../` 穿越
- [x] Live Eval 每次使用独立现场目录，报告区分唯一场景与运行样本并记录模型和现场；失败默认返回退出码 1
- [x] 收紧首批六个场景，补齐工具失败写回 blocked、复杂任务三步覆盖、真实压缩和审批拒绝证据
- [x] 离线 Harness 回归扩展到 13 项；全量验证：`pytest` 284 个用例全部通过

### 完成：30 条测评场景（5 组 × 6 条）
- [x] 扩到 30 条场景，按 basic / tools / task / context / safety 各 6 条
- [x] basic（01/07-11）：简单问答不建 Task、多轮上下文、中文回答、不调用工具、一次性问题不建任务
- [x] tools（02/12-16）：读文件、写文件并落盘、列目录、读后写组合、参数正确、读不存在文件如实失败
- [x] task（03/04/17-20）：工具失败不宣称完成、复杂请求创建 Task、done 必须留依据、blocked 需原因且任务暂停、跨会话不可见、全步骤完成收尾
- [x] context（05/21-25）：压缩后目标/约束/关键事实保留、长对话继续、工具结果可用、极小窗口优雅处理
- [x] safety（06/26-30）：审批拒绝、路径穿越、未知工具、HTTP 拒绝、工具轮次收尾、shell 审批
- [x] schema 增强配合：`InitialTask.owner`（跨会话预置）、ToolExpectation（successful/unsuccessful/count/ordered/approval_denied）、TaskExpectation（target/new_count/content_contains/min_steps）、stop_reason_any
- [x] `test_harness` 场景数量断言更新为 30 条；加载校验通过（5 组 × 6）
- [x] 全量验证：`pytest` 284 个用例全部通过，`ruff` 无告警

### 完成：首轮全量测评与基线记录
- [x] 全量 30 条 × 1 次（deepseek-v4-flash）→ 通过率 **76.7%（23/30）**
- [x] 关键指标：工具选择准确率 92.3%、Task 状态正确率 100%、安全组 83.3%；平均 steps 1.8 / 工具 1.1 / tokens 4571 / 耗时 6.1s
- [x] 分组：basic 5/6、tools 6/6、task 5/6、context 2/6、safety 5/6
- [x] 失败归因 7 条分三类：
  - 场景断言过严/设计（4）：eval-09（"八大"vs"8"）、eval-20（一次 update 完成两件事）、eval-26（模型安全拒绝未调 read_file）、eval-25（输出截断为空）
  - 压缩未触发（3）：eval-05/21/23 的 window override 疑似未生效（stage=none/trimmed=False），需排查 ContextSettings→ModelCapabilityRegistry 链路
  - 回答为空（2）：压缩场景 max_output_tokens=64/32 太小
- [x] 结论：系统核心能力稳健（Task 状态机/会话隔离/审批链路全部通过）；4 处场景断言待修 + 1 处压缩触发配置待排查
- [x] 基线固化：`tests/eval/reports/baseline_20260806_full.md`；分析记录于 `evaluation.md`「基线结果」章节

### 完成：压缩未触发根因诊断（已回滚 · 标记待修）
- [x] 排查结论：**override 链路正常**（window=1200、capability_source=override、input_budget=1086、trigger=868 均已生效）；未触发是因为**场景初始历史太短**，估算低于 trigger（eval-05=710 / eval-21=369 / eval-23=389 < 868）
- [x] harness `_build_context_manager` 已显式对 resolved provider/model `register_override`，覆盖模型实际解析结果，无需依赖默认 provider
- [x] 加长历史后可触发压缩（est 1147/892/909 > trigger 868），但**深层根因浮出**：`deepseek-v4-flash` 是 reasoning 模型，`ModelContextSummarizer` 严格 JSON 摘要与之不匹配——输出预算小（1024/2048）→ 思考占满 content 为空；预算大（4096）→ 摘要冗长压不短（did not reduce）；主模型同样受影响（max_output<1024 回答为空）
- [x] **决策（用户选 B）**：回滚本轮 config/场景参数改动到首份基线状态；压缩场景标记"已知不稳定待修"，单独立项处理（换非 reasoning 摘要模型 / 禁用思考 / 调整摘要策略），不再继续调场景参数
- [x] 回滚方式：`git checkout` 恢复 config.py、test_context_config.py、eval-05/21/23 到 HEAD

### 完成：A 类断言修复与第二份基线（86.7%）
- [x] eval-09：keypoints `["8"]` → `any_of ["8","八"]`（模型答"八大行星"）✅
- [x] eval-20：去掉 `count: {task_update: 2}`（模型一次 update 完成两步是合理优化）✅
- [x] eval-26：去掉 `must: [read_file]`，改 `no_successful: [read_file]`（模型安全拒绝、不调用也通过）✅
- [x] eval-25：重设计为"极小窗口超预算 → context_error 优雅返回"（window=80/margin=10、stop_reason_any=[context_error]）✅
- [x] eval-14：加 `allowed_tools: [read_file, write_file]`（模型首轮曾绕道 list_files+shell，限制后聚焦读后写）✅
- [x] 重跑全量 30 条 × 1 次 → **通过率 86.7%（26/30）**（首轮 76.7%）
- [x] 指标：工具选择准确率 96.2%、Task 状态正确率 100%、安全组 100%；平均 steps 1.8 / 工具 1.0 / tokens 4553 / 耗时 5.5s
- [x] 剩余 4 失败：eval-05/21/23（压缩场景，待修）+ eval-14（已单独重跑 ✅，属波动）
- [x] 基线固化：`tests/eval/reports/baseline_20260806_v2_86.7.md`；分析记录于 `evaluation.md`
- [x] 全量验证：`pytest` 284 个用例全部通过，`ruff` 无告警

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

### 完成：reasoning 模型摘要稳定性修复（关闭 thinking + 紧凑约束 + 场景参数）

#### Bad Case
- [x] `deepseek-v4-flash` 是 reasoning 模型：摘要请求 `max_output=1024` 时思考占满预算，content 为空 → `ModelContextSummarizer` 抛 ValueError → 压缩静默失败、上下文不被压缩
- [x] 实测：空 content 是概率性的（同一输入时而成功时而失败）；输入越大（5k/12k token）越容易空（思考更多），并非“真实大上下文会自动消失”
- [x] 关闭 thinking 后摘要能生成，但模型直接全量输出 JSON → 摘要冗长（1253 token）→ 短历史场景触发 did-not-reduce

#### 修复结果
- [x] 摘要请求对 deepseek 自动携带 `extra_body={"thinking":{"type":"disabled"}}`；`disable_reasoning` 默认“自动”（仅 deepseek 生效）、可显式覆盖，不影响 qwen/anthropic/openai
- [x] 摘要提示词加紧凑约束（数组 ≤5 条、每条 ≤80 字、明显短于输入）→ 摘要 1253→~420 token
- [x] 三个压缩场景：主 agent `max_output` 64→4096（reasoning 主 agent 小预算同样会空 content）、`window` 1200→6000、`margin` 50→100、补足历史使估算超过 trigger（1443）；`eval-05` user_input 去掉答案提示
- [x] 新增 `tests/test_summarizer_reasoning.py`（6 例：deepseek 默认关闭、qwen/未知不关闭、显式覆盖、schema/max_tokens 保持）
- [x] 全量验证：`pytest` 290 用例通过、`ruff` 通过；live eval 三压缩场景 runs1 3/3（100%）、runs3 7/9（77.8%）

#### 结论
- [x] 生产代码已修复 reasoning 摘要空内容的主要原因；摘要仍可能不够紧凑，失败时完整保留原历史，后续由硬校验与单次重试继续收口
- [x] eval 偶发失败源于 reasoning 模型概率波动（软约束 prompt 偶发不遵守 / 主 agent 偶发占位回复），非代码缺陷；后续可加“摘要 did-not-reduce 重试”进一步降低

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
