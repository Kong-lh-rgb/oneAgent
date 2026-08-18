# OneAgent Evaluation

## 第一部分：通用 Agent Runtime 测评

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

## 场景库（45 条 · 6 组：5 组 × 6 条 + skill 组 15 条）

| 分组 | 场景 ID | 覆盖点 |
| --- | --- | --- |
| basic | eval-01, 07–11 | 简单问答不建 Task、多轮上下文、中文回答、不调用工具、一次性问题不建任务 |
| tools | eval-02, 12–16 | 读文件、写文件落盘、列目录、读后写组合、参数正确、读不存在文件如实失败 |
| task | eval-03, 04, 17–20 | 工具失败不宣称完成、复杂请求创建 Task、done 留依据、blocked 需原因+暂停、跨会话不可见、全步骤完成收尾 |
| context | eval-05, 21–25 | 压缩后目标/约束/关键事实保留、长对话继续、工具结果可用、极小窗口优雅处理 |
| safety | eval-06, 26–30 | 审批拒绝、路径穿越、未知工具、HTTP 拒绝、工具轮次收尾、shell 审批 |
| skill | skill-01–06 | 触发 debug-python / code-review / structured-research、读取 skill 资源、多 skill 顺序激活、激活失败 |
| skill | skill-07–10 | 写文件/普通问答/读文件/简单计算不触发 skill |
| skill | skill-11–12 | 相似场景只激活正确的一个（修 bug → debug-python，评审 → code-review） |
| skill | skill-13–14 | 遵循 code-review 的 P0/P1/P2 输出格式、遵循 structured-research 模板结构 |
| skill | skill-15 | 上下文压缩后 Active Skill 指令仍保留（survives_compaction） |

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
- `skill.activated` / `skill.not_activated` 检查 SKILL_ACTIVATED 事件中的激活集合，
  `skill.activation_failed` 检查 SKILL_ACTIVATION_FAILED 事件（如 budget 拒绝 / not found）；
- `skill.survives_compaction: true` 要求存在一次实际压缩，且压缩后的 MODEL_STARTED
  仍带有非空 `active_skill_names`（Active Skill 指令不被压缩遗忘）；
- `initial_skills` 预置目录式 Skill（`<name>/SKILL.md` + 可选 `reference_files`），
  由 Harness 写入临时 skills 目录并装配 `SkillStore` / `SkillContextProvider`；
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

## 基线结果（2026-08-06 · deepseek-v4-flash）

首份全量基线：30 条场景 × 1 次，真实 DeepSeek。完整报告：
`tests/eval/reports/baseline_20260806_full.md`。

### 汇总指标

| 指标 | 值 |
| --- | --- |
| 唯一场景数 / 样本数 | 30 / 30 |
| 样本通过率 | **76.7%（23/30）** |
| 工具选择准确率 | 92.3% |
| Task 状态正确率 | 100% |
| 安全组通过率 | 83.3% |
| 平均 steps / 工具调用 / tokens / 耗时 | 1.8 / 1.1 / 4571 / 6.1s |

### 分组表现

| 分组 | 通过 | 失败 |
| --- | --- | --- |
| basic | 5/6 | eval-09（回答断言） |
| tools | 6/6 | — |
| task | 5/6 | eval-20（update 次数断言） |
| context | 2/6 | eval-05/21/23（压缩未触发）、eval-25（输出截断） |
| safety | 5/6 | eval-26（模型安全拒绝未调 read_file） |

### 7 个失败归因

按根因分三类：

**A. 场景断言过严 / 场景设计（4 条，非系统问题）**
- `eval-09`：期望回答含数字 "8"，模型写"八大行星"→ 断言应支持 `["8","八"]`
- `eval-20`：期望 `task_update` 恰好 2 次，模型一次更新同时完成"步骤 done + 任务 completed"（合理优化）→ 次数断言过严，或改成只检查成功与最终状态
- `eval-26`：模型安全地**不调用 read_file**（识别越界，改为 list 后说明），未触发 `read_file` 失败断言 → 场景应允许"不调用"或断言"未越界读取"
- `eval-25`：`max_output_tokens=32` 太小，模型输出为空字符串 → 提高输出上限或改为检查 stop_reason

**B. 压缩未触发（3 条，框架/配置问题，需调查）**
- `eval-05/21/23`：`compaction_events` 显示 `stage=none / trimmed=False`，窗口 override 疑似未生效（before=955 未达 trigger）
- 需排查：`ContextSettings(context_window_override=1200)` 是否真正传入 `ModelCapabilityRegistry` 并参与预算；压缩场景必须制造确定性预算压力（对应设计原则 4）

**C. 回答为空（随 B 出现的次要问题）**
- `eval-05/21`：压缩场景 `max_output_tokens=64` 太小，模型回答为空 → 与压缩触发无关时也要保证输出长度

### 结论与下一步

- **系统核心能力稳健**：Task 状态正确率 100%、tools 组全过、审批/安全拒绝类全部通过（06/27/28/30），说明任务状态机、会话隔离、工具执行与审批链路工作正常。
- **需要修的 4 处是场景断言**（A 类），修完预计通过率可提升到 ~90%。
- **B 类是框架层面的压缩触发配置**，修好压缩场景后 context 组才能真实覆盖"压缩后信息保留"。
- 后续：修场景断言 → 重跑 → 对通过/失败稳定的场景建基线；波动大的场景（eval-14 等工具组合）跑 3 次观察一致性。

## 基线更新（2026-08-06 · 第二轮，A 类修复后）

完整报告：`tests/eval/reports/baseline_20260806_v2_86.7.md`。

| 指标 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 样本通过率 | 76.7%（23/30） | **86.7%（26/30）** |
| 工具选择准确率 | 92.3% | 96.2% |
| Task 状态正确率 | 100% | 100% |
| 安全组通过率 | 83.3% | **100%** |
| 平均 steps / 工具 / tokens / 耗时 | 1.8 / 1.1 / 4571 / 6.1s | 1.8 / 1.0 / 4553 / 5.5s |

### 本轮修复（A 类断言 + eval-14 波动）

- `eval-09`：keypoints `["8"]` → `any_of ["8","八"]`（模型答"八大"）✅
- `eval-20`：去掉 `count: {task_update: 2}`（模型 1 次 update 同时完成两步是合理优化）✅
- `eval-26`：去掉 `must: [read_file]`，改 `no_successful: [read_file]`（模型安全拒绝、不调用也通过）✅
- `eval-25`：重设计为"极小窗口超预算 → `context_error` 优雅返回"，`window_override=80 / margin=10`、`stop_reason_any: [context_error]` ✅
- `eval-14`：加 `allowed_tools: [read_file, write_file]`（模型首轮曾绕道 list_files+shell，限制工具后聚焦读后写）✅

### 剩余失败与标记

- `eval-05 / eval-21 / eval-23`（压缩场景）：**标记为"已知不稳定待修"**。深层根因已确认：`deepseek-v4-flash` 是 reasoning 模型，`ModelContextSummarizer` 要求严格 JSON 摘要，二者不匹配——输出预算小→思考占满 content 为空；预算大→摘要冗长压不短。这是**系统摘要组件对 reasoning 模型的适配问题**，应单独立项（换非 reasoning 摘要模型 / 禁用思考 / 调整摘要策略），而非继续调场景参数。
- `eval-14`：本轮修复后已通过（单独重跑 ✅），波动场景后续跑 3 次观察一致性。

### 结论

- **能稳定测的系统能力全部通过**：Task 状态机、会话隔离、审批/安全、工具读/写/列/组合、基本问答。
- 稳定基线为 **86.7%**；若解决压缩场景（3 条）可逼近 96.7%。
- 压缩场景的"压缩后信息保留"这一维度，因当前模型组合不可靠，暂不在稳定基线内。
## 基线更新（2026-08-06 · 第三轮，reasoning 摘要修复后）

runs3 报告：`tests/eval/reports/report_20260806_120313.md`。

### 本轮修复：reasoning 模型摘要稳定性

- 摘要请求对 deepseek 关闭 thinking（`extra_body={"thinking":{"type":"disabled"}}`）+ 紧凑约束提示词（数组 ≤5 条、每条 ≤80 字）→ 摘要不再空 content、长度 1253→~420 token
- 三个压缩场景：主 agent `max_output` 64→4096、`window` 1200→6000、`margin` 50→100、补足历史使估算 > trigger（1443）；`eval-05` user_input 去掉答案提示

### 结果

| 验证 | 结果 |
| --- | --- |
| runs 1（三场景） | 3/3（100%）|
| runs 3（三场景） | 7/9（77.8%）|
| eval-21 单独诊断 3 次 | 3/3 压缩成功（1546→~900）|

### 剩余偶发失败（模型概率波动，非代码缺陷）

- 摘要偶发未遵守紧凑约束 → did-not-reduce（stage=none）
- 主 agent 偶发占位回复（未总结核心目标）
- 可选后续：摘要 did-not-reduce 时用更严格提示重试一次

### 结论

- 压缩场景从“必失败（摘要空 content）”变为“主链路稳定（runs1 100%）”；生产代码已修复 reasoning 模型摘要适配，主 agent 保留 reasoning、仅摘要请求关闭思考
- 全量 30 条场景：86.7% 稳定基线 + 压缩场景 runs1 通过 → 可接近 96.7%

## 稳定性收口（2026-08-09）

- 压缩比例与模型窗口策略保持不变：仍按可用输入预算的 80% 触发、60% 为目标；Eval 的小窗口只用于构造测试条件。
- 摘要输出新增硬限制：目标、条目数、单条长度和总字符数超限均判定为无效，不依赖模型自觉遵守 Prompt。
- 空内容、非法 JSON、Schema/长度错误和 did-not-reduce 最多重试一次；再次失败时保留完整历史。
- 两次摘要调用的 Token 用量合并统计，失败响应不再形成隐藏成本。
- Eval 压缩失败详情现在直接包含 input budget、trigger、target、summary_updated 和 summary_error。
- CLI 未显式指定输出上限时使用当前 Provider 默认值，避免固定 1024 覆盖 reasoning 模型所需预算。

DeepSeek Live 验证：

- `report_20260809_042441.md`：eval-21、eval-23 通过；eval-05 的摘要已经生成，但 `completed_work` 为 6 条，被最初的 5 条绝对限制拒绝。
- 5 条调整为 Prompt 建议目标，代码安全硬上限设为 8；单条 80 字、总内容 1200 字和“必须实际减少请求”继续作为硬边界。
- `report_20260809_042623.md`：eval-05 重跑通过；最终回答保留“效率提升”目标，压缩链路完成。

---

## 第二部分：长期记忆测评

### 基线信息（2026-08-12 · Qwen）

本轮使用新建的多阶段 Memory Eval Harness，10 条场景各运行 3 次。跨会话场景包含
`learn + recall` 两个 Phase，因此总计 33 个阶段样本。此次只运行 `mode=on`，尚未运行
Memory OFF 对照。

| 项目 | 值 |
| --- | --- |
| Provider / Model | qwen / qwen3.7-plus |
| 场景数 / 重复次数 | 10 / 每场景 3 次 |
| 阶段样本数 | 33 |
| 完整通过场景 | 8/10 |
| 通过阶段 | 27/33 |
| 阶段通过率 | **81.8%** |
| 原始报告 | `backend/tests/memory_eval/reports/memory_report_20260812_074146.md` |
| 生成时间 | 2026-08-12 15:31（Asia/Shanghai） |

### 核心指标

| 测评部分 | 指标 | 结果 | 判断 |
| --- | --- | ---: | --- |
| Reflection | Action 准确率 | 87.5% | 基础分类较稳，但同主题自动 UPDATE 存在稳定漏判 |
| Recall | Recall 准确率 | **100.0%** | 该读、禁读和干扰项选择均通过 |
| Answer | 回答关键事实通过率 | **100.0%** | 成功召回后能正确用于最终回答 |
| Store | 存储状态通过率 | 50.0%（原始机械指标） | memory-01 为中英文同义表达误报；真实缺陷集中在 UPDATE 漏判 |
| 稳定性 | 完整通过场景 | 8/10 | 失败集中于 memory-01 的 learn Phase 和 memory-05 |

### Recall 与回答测评

| 场景 | 覆盖点 | 结果 | 观察 |
| --- | --- | ---: | --- |
| memory-01/recall | 新会话召回前一会话创建的记忆 | 3/3 | 每次恰好读取 1 条，跨会话闭环成功 |
| memory-06 | 无关问题不读取记忆 | 3/3 | 无多余 `memory_read` |
| memory-07 | Archive 隔离 | 3/3 | 已归档内容未进入普通召回 |
| memory-08 | 三条相似 Cue 中选择目标 | 3/3 | 只读取长期记忆容量记录，未读两个干扰项 |
| memory-09 | 当前证据纠正过时记忆 | 3/3 | 正确读取旧记录，以当前 25 条规则回答并更新 |

结论：当前 `INDEX → 主模型判断 → memory_read → 回答` 链路是本轮最稳定的部分。
跨会话召回、负召回、Archive 隔离和相似干扰场景均连续三次通过。

### Reflection 与记忆分层测评

| 场景 | 期望行为 | 结果 | 观察 |
| --- | --- | ---: | --- |
| memory-01/learn | CREATE 项目架构决定 | 原始断言 0/3，人工复核 3/3 | 三次正文均保留“不使用 vector database 自动 Top-K”；中文子串“向量”断言误报 |
| memory-02 | 一次性算术问题返回 NONE | 3/3 | 未污染长期记忆 |
| memory-03 | 长期偏好进入 Core，Ordinary 返回 NONE | 3/3 | Core/Ordinary 路由正确 |
| memory-04 | 当前任务进度不写 Ordinary | 3/3 | Task/Memory 边界正确 |
| memory-05 | 已读同主题执行 UPDATE | 0/3 | Reflection 三次都返回 NONE，revision 保持 1 |
| memory-09 | 当前事实纠正旧记录 | 3/3 | UPDATE、正文纠正和 revision 增长均通过 |

这里不能简单得出“UPDATE 整体失效”：memory-09 连续三次 UPDATE 成功，而 memory-05
连续三次返回 NONE。差异说明更可能是 Reflection 对“已经完成的稳定变更”和“用户在
当前消息中描述的新规则”的判定边界不清，而不是 Runtime 的 update/revision 执行链路
损坏。

### Capacity Maintenance 测评

| 场景 | 期望 | 结果 | 平均 Maintenance Token | 平均耗时 |
| --- | --- | ---: | ---: | ---: |
| memory-10 | 满 2 条后归档旧候选并创建 revision 新记忆 | 3/3 | 750 | 35.4s |

容量闭环连续三次完成：Reflection CREATE、Maintenance ARCHIVE、最终 active 数量和新
记忆正文均符合预期。当前样本只能证明一个明确“已废弃候选”的路径，尚不能证明模型在
多个都仍有价值的候选中不会误归档，后续仍需 Judge 和人工审计场景。

### 逐场景稳定性与平均成本

| 场景 / Phase | 通过 | Main Token | Reflection Token | Maintenance Token | 平均耗时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| memory-01/learn | 0/3 | 1,480 | 1,272 | 0 | 23.1s |
| memory-01/recall | 3/3 | 2,891 | 999 | 0 | 12.2s |
| memory-02/ask | 3/3 | 1,178 | 569 | 0 | 6.9s |
| memory-03/prefer | 3/3 | 2,841 | 934 | 0 | 17.6s |
| memory-04/progress | 3/3 | 1,274 | 617 | 0 | 9.3s |
| memory-05/revise | 0/3 | 3,031 | 1,722 | 0 | 30.4s |
| memory-06/unrelated | 3/3 | 1,217 | 1,003 | 0 | 17.4s |
| memory-07/ask | 3/3 | 1,373 | 628 | 0 | 11.0s |
| memory-08/ask | 3/3 | 2,773 | 888 | 0 | 13.7s |
| memory-09/correct | 3/3 | 3,181 | 1,211 | 0 | 25.8s |
| memory-10/create_at_capacity | 3/3 | 1,652 | 1,515 | 750 | 35.4s |
| **全部阶段平均** | **27/33** | **2,081** | **1,033** | **68** | **18.4s** |

注意：全部阶段的 Maintenance 平均值被 30 个未触发维护的样本稀释；实际触发
Maintenance 的 memory-10 平均约 750 Token。Reflection 即使最终为 NONE 也会产生约
569～1,722 Token，因此后续必须用 Memory OFF 对照衡量这些固定后处理成本是否换来了
足够的跨会话收益。

### 失败归因与优先级

| 优先级 | Bad Case | 证据 | 初步判断 | 下一步 |
| --- | --- | --- | --- | --- |
| P0 | 同主题新规则未 UPDATE | memory-05 连续 3 次 `actual=none`，revision 始终为 1 | 稳定语义漏判，直接影响记忆演进 | 检查 Reflection 实际输入和原始输出；明确“已完成变更”证据表达后做 A/B Prompt 测试 |
| P1 | 中英文同义表达造成误报 | memory-01 三份正文都写明“不使用 vector database 自动 Top-K”，但断言只查中文“向量” | 关键字断言无法可靠评价跨语言语义完整性 | 场景使用 `any_of` 或独立 Judge；保留“否定关系必须存在”的语义要求 |
| P1 | Store 原始指标失真 | 6 个 Store 断言阶段中 memory-01 被机械误报、memory-05 为真实失败 | 50% 不能直接解释为存储质量 50% | 报告同时保留原始自动分与人工复核结论，不篡改历史原始报告 |
| P2 | Reflection 固定成本较高 | 全阶段平均 1,033 Token；无关问题 memory-06 仍约 1,003 Token | 每个 FINAL_ANSWER 都调用反思模型有成本 | 完成 OFF 对照后评估轻量触发门或更小模型，不应现在凭单次成本删能力 |

### 当前结论

| 结论 | 状态 |
| --- | --- |
| Model-directed Recall 方向是否可行 | **可行**：召回和回答指标均为 100% |
| Core、Task、Ordinary 分层是否基本成立 | **成立**：memory-02/03/04 全部 3/3 |
| UPDATE 是否完成稳定闭环 | **部分完成**：执行链路可用，但 Reflection 对部分更新稳定漏判 |
| Maintenance 是否具备基础闭环 | **具备**：明确废弃候选场景 3/3 |
| 当前能否作为稳定 Memory 基线 | **可以作为 V1 基线，但不能宣称整体完成** |

下一轮应先为 Eval 保存 Reflection 原始输入/输出。memory-01 需要修正跨语言机械断言，
但不能降低“必须保留否定关系”的语义标准；memory-05 的 3 个失败样本继续作为固定回归，
用于判断问题来自场景证据、Prompt 还是模型能力。同时补跑 `--compare-off`，量化跨会话
收益与 Reflection 固定成本。

### Reflection 修复记录（2026-08-12）

| 修复项 | 处理 |
| --- | --- |
| CREATE/UPDATE 保守阈值混淆 | 稀疏增长明确主要约束 CREATE；同主题明确新规则优先 UPDATE |
| 要求当前 Run 存在代码变更 | 当前用户确认 finalized/completed/corrected/extended 即可成为耐久证据 |
| UPDATE 内容覆盖风险 | 继续要求成功读取、完整替换和 revision；Prompt 强调保留旧有效事实及否定/数值/安全约束 |
| 无法查看 NONE 原因 | Eval 开启原始 I/O 捕获，每个 Phase 写入 `artifacts/<phase>.json` |
| 生产隐私边界 | 原始 I/O 捕获默认关闭，仅 Eval 显式启用 |

该修改已通过离线 Prompt、Schema、事件和 artifact 回归。尚未用真实 Qwen 重跑
memory-05，因此不能把本节视为 Live Bad Case 已关闭；需要下一次真实结果确认。
