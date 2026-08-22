# Vesta Desktop 学习记录

## 2026-08-22：设置分类属于空间导航，不属于页面操作

顶部 actions 更适合“新增、刷新、导出”等对当前内容执行的动作；“通用 / 扩展能力”会改变
整个设置内容空间，应该放在稳定的左侧导航中。否则分类切换会和页面级按钮争夺同一位置。

通用设置的高频目标是确认状态，不是分析数据，因此：

1. Host、Computer、Desktop 按职责分组，组间使用分隔线，不堆叠独立卡片。
2. 关键值使用黑色正文；灰色只表达字段名、解释和低频路径，状态色只保留必要语义。
3. 键值信息用定义列表而不是 table，既保留对齐关系，也更容易在窄宽度下调整列宽。
4. 分类栏保持稳定宽度，内容区独立伸缩；响应式优先收窄导航，而不是把它重新移回顶部。
5. 视觉简化不能删除能力：Host 重试、macOS 权限请求和技术路径详情仍需保留。

## 2026-08-22：管理页布局应匹配信息的比较方式

卡片和列表没有绝对优劣，关键是用户如何比较信息。执行历史天然按时间连续，用户通常
逐行比较状态、任务和时间，因此单列条目比二维卡片更容易扫描。长期记忆则需要先解释
存储层次，再展示单条内容。

关键边界：

1. Run 条目保持稳定列：状态与 ID、指令、来源、时间和详情入口，异常原因附着在指令下方。
2. Core Memory 与普通 Memory 的进入上下文方式不同，视觉上也必须明确分层，不能混成同一种卡片。
3. 普通记忆的容量与归档数量属于系统状态，应在概览展示；版本、读取次数和更新时间属于单条元数据。
4. 归档是保留但退出索引，不是删除或失败，因此只弱化颜色，不使用危险状态样式。
5. 响应式布局应优先隐藏低价值来源字段、再折叠元数据，不能让核心标题和状态被挤掉。

## 2026-08-22：管理页面的卡片要先表达语义层级

自动化不是一行数据，它包含“要做什么”、“什么时候做”、“最近是否执行”
和“现在能否控制”四类信息。界面应该先展示高频决策信息，再按需展开原始调度值。

关键边界：

1. 卡片主层保留标题、状态、Prompt、调度摘要和上下次执行时间。
2. Cron原文、时区、最近Run ID等低频技术信息放入可展开详情。
3. 新建表单用调度类型驱动字段显示，避免用户同时面对互斥的run_at、interval和cron。
4. 视觉重构不应自动扩大为领域能力。当用户取消编辑需求时，对应Update协议应完整撤回，不留死接口。

## 2026-08-22：持久化事实不能只依赖组件局部状态

`lastRunId` 适合作为当前页面的交互缓存，但不是Run是否存在的权威来源。
React组件在页面切换时会卸载，局部状态随之丢失；Run本身已经持久化在
Host SQLite中，因此正确的恢复路径是：

```text
App层保留当前conversation_id
  ↓ Chat重新挂载
Host listRuns(conversation_id)
  ↓ 按created_at选择最新Run
Run Inspector恢复可查看对象
```

关键边界：

1. App层只跨页保留“用户正在看哪个会话”，不复制Run数据。
2. SQLite Run列表是恢复事实源；Renderer实时事件只负责降低运行中延迟。
3. 当前存在实时Run时应优先展示它；没有实时Run时才回退到持久化的最新Run。
4. Run Detail到Conversation的跳转由`conversation_id`完成，它是导航关系，不需要新的后端状态。

## 2026-08-22：浮窗负责态势感知，详情页负责分析

同一份Run事实可以有不同的信息密度，但不能复制成两套事实：

```text
Run Inspector浮窗
  当前状态 + 必要动作 + 少量Usage/Context/Trace信号

Run Detail
  完整Usage + 每步Context + 全量Trace与原始证据
```

关键边界：

1. 浮窗首先回答“现在做到哪里、是否需要我操作、有没有失败”，而不是把所有可用
   指标平铺出来。
2. Usage摘要保留Main total、预算计入量和calls；cache读写、Provider Total及
   Reflection细目属于完整详情。
3. Context摘要只展示最近一次模型请求，因为它最接近当前决策；历史Step对比继续
   留在Run Detail。
4. Trace摘要展示最近少量人类可读动作，而不是原始事件JSON；审批和错误不能因截断
   被隐藏，正常活动则可以只保留最近证据。
5. `budgeted tokens`是治理口径，不是账单金额。界面不能使用`charged`暗示缓存命中
   完全免费或等同Provider计费。
6. Progressive disclosure不是删除诊断能力，而是让高频界面保持安静，把低频深度
   分析放到明确的详情入口。

## 2026-08-22：Usage不是一个Total数字

一次Run的真实Provider处理量来自多条相互独立的数据链：

```text
Main Agent模型请求（含滚动摘要）
  + Memory Reflection
  + Memory Maintenance
  = Provider Total
```

关键细节：

1. `input_tokens`是Provider处理的输入，通常包含缓存命中；`cached_input_tokens`
   是其中的子集，不能再加到Total上。
2. Cache Read和Cache Write可能使用不同价格，而且并非所有Provider都返回全部字段。
   数据缺失必须显示`Unavailable`，不能用0代替。
3. Anthropic的processed input需要合并普通input、cache creation和cache read；OpenAI、
   Qwen和DeepSeek的顶层input通常已经包含缓存命中，不能重复相加。
4. `AgentResult.usage`继续只表达Main Agent，Post-Run用量从独立Reflection/Maintenance
   事件聚合；这样领域边界不被UI需求污染。
5. Durable AgentEvent是Usage事实来源，`RunUsageSummary`是可重建read model。
   因此旧Run无需数据库迁移，也能回算Post-Run Token和模型调用次数。
6. Tool Schema Token来自本地估算器，只能显示`≈`；Provider Usage是实际响应数据，
   两者不可伪装成相同精度。
7. 成本保护应建立在这份账本之后。没有缓存细分前，直接用processed input设硬上限
   可能把大量低价cache read错误当成高价新输入。

## 2026-08-22：Run Inspector 与可解释 Context

Run Inspector 不应该再创建一份运行事实。它只是已有事实的 read model：

```text
SQLite durable Trace ─┐
                      ├─ event_id 去重 + sequence 排序
Renderer live Events ─┘
                               ↓
                   runAnalysis（纯 ViewModel）
                               ↓
                  Run / Context / Trace 三种视图
```

关键边界：

1. durable Trace 负责刷新后的完整性，live Events 负责运行中的低延迟；相同
   `event_id` 只能显示一次。
2. Context 是“每一次模型请求”的属性，不是整个 Run 的单个总数。因此界面按
   Model Step 切换，默认查看最近一步，不能只展示一个模糊的全局百分比。
3. `prepared_usage_ratio` 相对的是 Runtime 的 Input Budget；`prepared / context_window`
   才是完整模型窗口占比。这两个比例必须分别展示。
4. `message_tokens_after` 已包含历史工具结果和 Skill 注入。Breakdown 再单列它们时，
   Messages 必须先扣除对应值，否则柱状图总和会超过实际 Input。
5. 当前事件没有 Memory、Task、system prompt 各自的独立 token 字段，所以它们只能
   诚实地留在 `Messages & injected`。展示层不能靠猜测制造“精确”成本。
6. `approval.resolved` 只证明用户做出决定，`tool_completed` 才证明工具执行结束；
   两者必须在执行时间线与 Trace 中分别保留。
7. “为什么贵”应由 Runtime 事实确定性解释（模型调用次数、重复 Schema、工具结果峰值、
   Context 增长和压缩开始步骤），不需要再调用模型做一次昂贵且不可复现的解释。

## 2026-08-20：实时 Agent Turn 与流式输出

一次“流式聊天”其实包含三条不同的数据链，不能混在一起：

```text
用户指令（立即反馈）
  Desktop optimistic message

Agent 执行过程（实时事实）
  AgentRuntime → agent.event → LiveAgentTurn / Activity

模型正文（文本增量）
  Provider native stream → model_output_delta → 按 Run + Step 聚合
```

关键边界：

1. 乐观消息只属于当前 Renderer 状态，最终仍以 SQLite 中的完整会话历史为准。
2. `model_output_delta` 是瞬时传输事件，不是 durable fact。若把每个 token 写入
   Trace，会扩大数据库、污染事件时间线，并让恢复逻辑承担没有必要的复杂度。
3. Runtime 只依赖统一 `complete_stream()`，不理解 OpenAI、Qwen、DeepSeek 或
   Anthropic 的私有 chunk 结构；拼装完整 `ModelResponse` 是 Provider Adapter 的职责。
4. 工具前的模型文本和最终回答可能属于不同 Step，因此 Desktop 必须按
   `run_id + step` 聚合，不能把一整个 Run 的所有文本 chunk 盲目拼成一条回答。
5. 流式结束后仍保存完整 Assistant Message。增量用于即时体验，完整消息用于恢复、
   审计、上下文与后续多轮对话，两者职责不同。

## 2026-08-20：浅色毛玻璃主题

毛玻璃不是给每个元素随意加透明度。稳定层次来自三部分：

- 页面底部有低对比环境色，提供可被模糊的背景；
- 导航、Header、Composer 和 Drawer 等主要层使用半透明白色与 backdrop blur；
- 文本、边框和阴影仍保持足够对比，业务状态色只表达 selected/running/error。

响应式验收仍需检查真实 DOM 宽度。玻璃阴影和 Drawer 很容易造成视觉或布局溢出，
因此固定验证 1360×860 与 980×640，并确保 `scrollWidth === clientWidth`。
