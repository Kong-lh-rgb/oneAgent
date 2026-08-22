# Vesta Desktop 学习记录

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
