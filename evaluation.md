# OneAgent 测评框架

OneAgent 使用仓库内的轻量 Eval Harness 测量 `AgentRuntime` 的行为。它直接运行
真实 Runtime、工具、Task Store 和 ContextManager；离线自检使用 Fake Model，
Live Eval 才会调用已配置的模型 API。

## 测评边界

当前框架测量：

- 模型是否选择了正确工具，以及调用次数、顺序、关键参数和执行结果；
- 本轮是否新增 Task，指定 Task 的状态、目标和步骤是否正确；
- workspace 文件事实和最终回答关键点；
- 审批拒绝事件与工具是否确实没有成功；
- 上下文是否真正执行压缩、压缩前后估算是否变化；
- Runtime 是否以场景允许的停止原因结束。

它不是完整 CLI 端到端测评，暂不覆盖 SQLite 会话恢复、Checkpoint、持久化权限
规则和终端交互。相关能力应使用独立集成场景测试，避免一个 Harness 承担所有层次。

## 运行方式

在 `backend` 目录执行：

```bash
# 离线验证测评框架，不调用真实 API
.venv/bin/python -m pytest tests/test_harness.py -q

# 使用 .env 中的默认模型运行全部 Live Eval
.venv/bin/python -m tests.eval.run_live --print

# 筛选场景并重复运行
.venv/bin/python -m tests.eval.run_live --group task --runs 3 --print
.venv/bin/python -m tests.eval.run_live --scenario eval-02 --provider qwen
```

Live Eval 存在失败时默认返回退出码 1，可用于 CI 门禁。探索性运行需要保留退出码
0 时显式增加 `--allow-failures`。运行现场路径会写入报告；显式 `--root` 时每次创建
独立子目录，避免旧 Task 或文件污染新结果。

生成的时间戳报告属于本地测评产物，默认不提交 Git。需要建立基线时，应把确认过
的报告复制为有语义的固定文件名再提交。

## 场景库（30 条 · 5 组 × 6 条）

| 分组 | 场景 ID | 覆盖点 |
| --- | --- | --- |
| basic | eval-01, 07–11 | 简单问答不建 Task、多轮上下文、中文回答、不调用工具、一次性问题不建任务 |
| tools | eval-02, 12–16 | 读文件、写文件落盘、列目录、读后写组合、参数正确、读不存在文件如实失败 |
| task | eval-03, 04, 17–20 | 工具失败不宣称完成、复杂请求创建 Task、done 留依据、blocked 需原因+暂停、跨会话不可见、全步骤完成收尾 |
| context | eval-05, 21–25 | 压缩后目标/约束/关键事实保留、长对话继续、工具结果可用、极小窗口优雅处理 |
| safety | eval-06, 26–30 | 审批拒绝、路径穿越、未知工具、HTTP 拒绝、工具轮次收尾、shell 审批 |

场景文件：`tests/eval/scenarios/`（NN_名称.yaml）。

## 场景语义

场景位于 `tests/eval/scenarios/`。其中几个容易混淆的字段约定如下：

- `task.created: false` 表示本轮不能新增 Task，不表示运行后 Task 总数必须为零；
- `task.new_count` 精确检查相对初始 Task 快照的新增数量；
- `task.target` 使用初始 Task 的 `alias`，或使用 `new` 指向唯一新增 Task；
- `tools.successful` 要求至少一次成功，`unsuccessful` 要求至少一次失败；
- `tools.no_successful` 要求该工具所有调用都未成功；
- `tools.count`、`total_count` 和 `ordered` 分别检查次数、总数和有序子序列；
- `tools.args` 在同名工具的所有调用中寻找至少一次关键参数匹配；
- `requires_compaction: true` 要求既达到触发线，又实际改变请求上下文；
- `stop_reason_any` 默认只允许 `final_answer`，负面场景必须显式声明其他停止原因。

没有声明某一维度期望时，该检查记为 `skipped`，不会进入对应准确率的分母。

## 设计原则

1. 优先检查工具结果、Task 和文件等确定性事实，回答字符串只做补充。
2. 检查状态增量而不是只看最终集合，避免预置状态造成误判。
3. 失败场景同时检查“发生了预期失败”和“没有虚假成功”。
4. 压缩场景必须制造确定性的预算压力，不能只把窗口写得看起来很小。
5. Live Eval 的一次结果只是样本；有随机性的场景至少运行三次并观察一致性。
6. 报告中的工具、Task 准确率只统计声明了相应期望的适用场景。

## 扩展场景时的检查清单

- 是否存在相互冲突的 `must` / `must_not`；
- 工具名是否已经注册，是否被 `allowed_tools` 意外隐藏；
- 预置 Task 是否有唯一 alias，期望是否明确 target；
- 是否同时验证了工具成功状态、关键参数和调用次数；
- 失败后是否禁止步骤 done、Task completed 或虚假完成回答；
- 压缩后是否保留核心目标和用户约束；
- 是否允许了正确的停止原因；
- 失败报告是否提供足够的信息在运行现场复现。
