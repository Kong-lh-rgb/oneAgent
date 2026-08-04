# OneAgent 任务日志

> 本文件用于记录每日开发任务与进展，作为项目留存。
> 追加规范：每日一个 `## YYYY-MM-DD` 小节，最新的日期放在最上方；任务用 `- [x] 已完成` / `- [ ] 未完成` 标记。

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
