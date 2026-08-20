# Vesta Desktop 学习记录

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
