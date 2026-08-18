# oneAgent

> 一个面向长期运行 Agent 的 Agent Harness，提供持久化 Task、Memory、Skill、Trace、Checkpoint、上下文管理，以及基于 Completed Task 的 Skill Learning。

oneAgent 想解决的不是“怎么让模型多调用几个工具”，而是一个 Agent 真正长期运行以后会遇到的问题：

- 对话越来越长，怎么压缩上下文但不丢关键状态
- 一个任务跨多轮甚至多个 Run 时，怎么持续推进
- 当前任务状态、长期记忆和可复用经验应该怎么分开管理
- Skill 怎么按需加载，而不是全部塞进上下文
- Agent 怎么从真实完成过的任务中沉淀稳定经验
- 一次 Run 中断以后，怎么恢复到可继续执行的状态

oneAgent 围绕这些问题构建了一套 Agent Harness。

## 核心能力

### Persistent Task

Task 独立于 Conversation 持久化，用来保存 Agent 当前真正正在做的事情。

一个 Task 会持续维护：

- 当前目标
- 用户约束
- 关键事实
- 当前状态
- 执行步骤
- 关联的 Agent Run

这样即使 Conversation 被压缩，任务本身的重要状态也不会丢失。

### Context Management

Conversation 是完整事实源，但不会被无限塞给模型。

oneAgent 会根据当前工作状态动态构建模型真正需要的 Context，包括：

- 最近的 Conversation
- Conversation Summary
- 最近工具执行结果
- 当前活跃 Task
- 相关 Memory
- 当前激活的 Skill

目标是在信息完整性和上下文成本之间保持平衡。

### Long-term Memory

Memory 用来保存 Agent 在未来仍然应该知道的信息，而不是当前任务的即时状态。

例如：

- 用户长期偏好
- 项目事实
- 稳定决策
- 后续任务可能继续用到的信息

Memory 和 Conversation、Task 分开管理，避免所有信息最后都堆进聊天历史。

### Skill Runtime

Skill 用来保存可复用的工作方法。

oneAgent 使用渐进式加载机制：

1. 先只向模型暴露 Skill 的 name 和 description
2. 模型判断某个 Skill 是否相关
3. 需要时再读取完整 Skill 内容
4. 已激活 Skill 在当前 Run 中持续生效

这样可以避免随着 Skill 数量增加，上下文也一起无限增长。

### Task-centric Skill Learning

oneAgent 不会在每次对话结束后都让模型判断“这次要不要生成 Skill”。

Skill Learning 以 **Completed Task** 作为主要经验入口。

系统会周期性地从最近完成的一批 Task 中构造轻量 TaskCard，只让模型先判断这些任务里有没有重复出现、值得复用的任务模式。

如果没有发现稳定模式，就不会继续读取大量 Trace。

如果发现多个 Task 实际属于同一类问题，系统才会继续分析这些 Task 的真实执行过程。

每个 Task 会保存相关的 `run_ids`，因此可以先定位到可能包含任务执行过程的 Run。

在对应 Run 中，再根据成功的 `task_update` 找到相关 Agent Step 的锚点，并截取更可能属于当前 Task 的 Agent Step 区间，而不是直接把整个 Run 交给模型。

选出的 Events 会先经过确定性的 Evidence Builder，压缩成更适合学习的执行证据，例如：

- 工具调用顺序
- 失败和重试
- Task 实际发生的变化
- 执行过程中形成的关键事实
- 最终完成与验证证据

模型最后才会根据多个相似 Task 的 Evidence 提炼：

- Procedure
- Pitfalls
- Verification

生成结果之前，还会先检查已有 Skill。

已有 Skill 同样采用渐进式读取：先看 name 和 description，只加载少量相关 Skill 的完整内容。

最终得到三种结果：

- **NONE**：已有 Skill 已经完整覆盖
- **UPDATE**：属于同一类任务，但出现了稳定的新步骤、踩坑经验或验证方式
- **CREATE**：发现了新的、独立可复用的任务类型

模型生成的 Skill Candidate 不会直接进入正式 Skill，而是先经过人工审核。

## Agent 状态分层

oneAgent 会把不同类型的信息拆开管理：

**Conversation**  
记录发生过什么。

**Task**  
记录当前正在完成什么。

**Memory**  
记录未来还应该知道什么。

**Skill**  
记录以后遇到这类事情应该怎么做。

**Trace**  
记录这次实际是怎么执行的。

**Checkpoint**  
记录 Run 中断以后恢复执行所需的最小状态。

这个分层是 oneAgent 整个 Harness 的核心设计之一。

## Trace & Checkpoint

每次 Agent Run 都会产生完整 Trace，用来记录模型调用、工具调用、执行结果、失败、审批和最终状态。

Trace 主要回答：

> 这次到底发生了什么？

Checkpoint 则只保存最小可恢复状态，用来处理执行被意外中断的情况。

它主要回答：

> 现在执行到哪里了，下一次应该从哪里继续？

两者职责分开，避免为了恢复执行而保存和加载完整历史。

## Tool Runtime & Permissions

oneAgent 的工具系统支持：

- 内置工具
- Deferred Tool Discovery
- MCP Tool
- 工具权限策略
- Human Approval
- 可记忆的审批规则

不常用工具可以先不进入模型 Tool Schema，需要时再通过搜索发现和激活，减少每次模型请求的工具上下文成本。

## MCP

oneAgent 支持通过 MCP 接入外部 Server 和 Tool。

MCP 工具会进入统一 Tool Runtime，由相同的执行、权限和 Trace 机制管理。

## Skill Learning Eval

目前 Skill Learning 有一组 20 个 Completed Task 的测试场景。

其中：

- 6 个 Task 属于同一种 Python interpreter / virtualenv 问题
- 14 个 Task 是普通任务或刻意加入的相似噪声

噪声中包括：

- 普通 Python 代码 bug
- Node pnpm 环境问题
- Ruby gemset 问题
- 其他无关任务

连续 3 次真实模型运行中，目前结果为：

- Cluster Precision：**1.00**
- Cluster Recall：**1.00**
- Pattern Detection Recall：**1.00**
- Action Accuracy：**1.00**
- Pitfall Recall：**1.00**
- 3 / 3 Run 通过

模型能够稳定识别真正属于同一模式的 6 个 Task，并从对应 Trace 中提炼出：

- 确认实际 Python interpreter
- 检查项目 virtualenv
- 确保 pip / pytest 使用同一环境
- 避免 global pip
- 处理 IDE / tox / CI 中残留的解释器状态
- 先做定向 pytest
- 最后执行完整 pytest

最终正确判断应该 **UPDATE** 已有的 `debug-python` Skill，而不是创建一个新的碎片 Skill。

## Quick Start

### 1. Clone

```bash
git clone https://github.com/Kong-lh-rgb/oneAgent.git
cd oneAgent/backend