# oneAgent

> Build agents that remember, continue, and learn.

一个面向长期运行 Agent 的 Harness。

oneAgent 想做的事情很简单：

让 Agent 不只是完成当前这一轮任务，
而是能够记住重要的信息、持续推进长期任务，
并从一次次真实完成的工作中逐渐形成可复用的 Skill。

它目前包含：

- 🧠 **Memory** — 保留跨会话的长期重要信息
- ✅ **Task / Plan Mode** — 跟踪复杂任务并支持计划确认
- 🧩 **Skill / Skill Learning** — 按需加载方法，并从已完成任务中提炼经验
- 🧱 **Context** — 管理长对话预算、工具结果压缩与滚动摘要
- 🔍 **Trace / Checkpoint** — 记录真实执行过程和可恢复状态
- 🔄 **Run / Recovery** — 持久化执行生命周期，并从中断点创建恢复 Run
- ⏰ **Automation** — 通过 once、interval 或 cron 调度长期工作
- 🛡️ **Async Approval** — 后台等待人工审批，并通过 Desktop 继续处理
- 🖥️ **Computer Runtime (macOS)** — 基于原生 Helper 的结构化观察与桌面操作
- 📦 **Artifacts** — 发布、保存并交付 Run 产生的文件或链接
- 🔌 **MCP & Tools** — 接入外部工具，并统一执行、权限与审计边界
- 🪟 **Desktop** — Electron 桌面入口、实时状态、审批、Run 与 Artifact 查看

## 🌱 Agents should learn from doing

oneAgent 里我比较喜欢的一部分是 Task-centric Skill Learning。

它不会在每次聊天结束后都问模型：

> “这次有没有什么值得记成 Skill？”

而是先让 Agent 真正完成很多任务。

当系统发现用户反复在做同一类事情时，
才回到这些 Completed Task 的真实 Trace，
找到真正相关的执行过程，
再从多次成功经验中提炼稳定的 Procedure、Pitfalls 和 Verification。

如果已有 Skill 已经覆盖，就什么都不做。

如果是同一类任务学到了新的东西，就更新原来的 Skill。

只有真的出现新的任务类型，才创建新的 Skill。

最后的 Skill Candidate 仍然需要人工审核。

**不是每次对话都制造经验，而是让经验从长期使用里慢慢长出来。**

## 🚀 Quick Start

```bash
git clone https://github.com/Kong-lh-rgb/oneAgent.git
cd oneAgent/backend

python -m venv .venv
pip install -r requirements.txt
```

```bash
cp .env.example .env
.venv/bin/python -m app.models.chat
```

目前支持 OpenAI、Qwen、DeepSeek 和 Anthropic。

### 🖥️ Desktop + oneAgent Host

```bash
# 终端 1：启动 oneAgent Host
cd backend
.venv/bin/python -m app.server            # http://127.0.0.1:8000

# 终端 2：启动 Desktop（Electron + React + TS + Vite）
cd desktop
npm install
npm run electron:dev                        # 或 npm run dev（纯 Renderer）
```

- Desktop 的正常业务统一通过 `WS /rpc`（JSON-RPC）访问本机 oneAgent Host；
  Electron Main 只负责桌面生命周期、受限外链和原生通知。
- `GET /health`、Computer screenshot 与 Artifact content 端点只是本地 transport，
  不承担业务 CRUD。

```text
Desktop
  ↓
WS /rpc (JSON-RPC)
  ↓
oneAgent Host
  ↓
ConversationService / RunManager / AgentRuntime
```

```text
🏗️ What's inside?
Conversation   → 发生过什么
Task           → 现在正在做什么
Memory         → 以后还应该知道什么
Skill          → 以后这种事情怎么做
Trace          → 这次具体怎么执行的
Checkpoint     → 中断后从哪里继续
Run            → 这次执行的 Run 生命周期
Automation     → 未来何时以什么 prompt 再启动一次
oneAgent Host  → 通过 WS /rpc 组合并暴露应用能力
Desktop        → Electron + React 桌面入口
```

oneAgent 试着把这些东西真正拆开，
再由 Agent Runtime 在每次模型调用前组合成当前需要的 Context。
