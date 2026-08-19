# oneAgent

> Build agents that remember, continue, and learn.

一个面向长期运行 Agent 的 Harness。

oneAgent 想做的事情很简单：

让 Agent 不只是完成当前这一轮任务，
而是能够记住重要的信息、持续推进长期任务，
并从一次次真实完成的工作中逐渐形成可复用的 Skill。

它目前包含：

- 🧠 **Memory** — 保留长期重要的信息
- ✅ **Task** — 持续跟踪正在完成的工作
- 🧩 **Skill** — 按需加载可复用的工作方法
- 🌱 **Skill Learning** — 从重复完成的 Task 中形成新的经验
- 🧱 **Context** — 在长对话中控制上下文并保留关键状态
- 🔍 **Trace** — 保存 Agent 的真实执行过程
- 💾 **Checkpoint** — 让中断的 Run 可以继续恢复
- 🔌 **MCP & Tools** — 接入外部能力并管理工具权限

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

### 🖥️ Desktop V0（Agent Server + Electron）

```bash
# 终端 1：启动 Python Agent Server（FastAPI + WebSocket）
cd backend
.venv/bin/python -m app.server            # http://127.0.0.1:8000

# 终端 2：启动 Desktop（Electron + React + TS + Vite）
cd desktop
npm install
npm run electron:dev                        # 或 npm run dev（纯 Renderer）
```

- Renderer 只通过 HTTP / WebSocket 与 localhost Agent Server 通信；
  Electron Main 只负责桌面壳（contextIsolation / nodeIntegration:false）。
- 核心链路不变：Desktop → Agent Server → `ConversationService` → `RunManager` →
  `AgentRuntime`；Automation → `ConversationService`。WebSocket `/api/events`
  复用现有 `AgentEvent` 实时推送执行进度。

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
Server         → 把上面的能力暴露成 HTTP / WebSocket
Desktop        → Electron + React 桌面壳（V0）
```

oneAgent 试着把这些东西真正拆开，
再由 Agent Runtime 在每次模型调用前组合成当前需要的 Context。