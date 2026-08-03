# OneAgent 任务日志

> 本文件用于记录每日开发任务与进展，作为项目留存。
> 追加规范：每日一个 `## YYYY-MM-DD` 小节，最新的日期放在最上方；任务用 `- [x] 已完成` / `- [ ] 未完成` 标记。

---

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

### 进行中 / 待办
- [ ] 规划层设计（`task/`、`scheduler/`、`memory/` 目录目前为空）
- [ ] 工具层：工具并行执行、输出结构化、schema 字段级校验
- [ ] 将 `ConsoleApprovalGate` 接入 CLI / API，让人工审核真正可用
