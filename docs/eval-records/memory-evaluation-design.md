# Vesta 长期记忆测评设计

## 1. 测评目标

长期记忆不能只测 `memory.read` 是否被调用。完整质量由四步共同决定：

```text
值得记的信息被正确写入
→ 下一会话能发现相关 Recall Cue
→ 只读取真正相关的记忆
→ 最终回答正确使用记忆且不被错误记忆污染
```

因此评测分成两层：

- 确定性不变量测试：离线 Fake Model，验证权限、revision、容量、并发和失败隔离；
- 语义行为测评：真实 Main/Reflection/Maintenance 模型，验证写入、召回和取舍质量。

前者回答“系统会不会写坏”，后者回答“模型记得好不好”。两类结果不能混成一个
通过率。

## 2. 为什么需要独立的 Memory Eval Harness

现有 Eval Scenario 主要驱动一次 `AgentRuntime.run()`，适合工具、Task、上下文和安全
场景。长期记忆的关键行为跨越多个 Run 和会话，需要在同一个临时 Memory 目录中按阶段
执行：

```text
Phase A / conversation-A：产生耐久信息并完成 Reflection
Phase B / conversation-B：用间接表达询问旧信息
Phase C / conversation-C：修正旧信息并触发 UPDATE
Phase D：检查 active、archive、INDEX、事件和回答
```

Memory Eval 应单独增加 `MemoryEvalScenario` 与 Runner，复用现有 Adapter Registry、
AgentResult、AgentEvent 和报告结构，但不把多阶段语义塞进当前单 Run `Scenario`。

## 3. 场景结构

建议 YAML 结构：

```yaml
id: memory-cross-session-01
name: 跨会话召回项目架构决定
initial_memory:
  core: []
  active: []
phases:
  - conversation: A
    user_input: "我们决定普通记忆只使用 Markdown，不再使用向量库。"
    expect:
      reflection_action: create
      memory_contains: ["Markdown", "不再使用向量库"]
  - conversation: B
    user_input: "之前长期记忆最终选了什么存储方式？"
    expect:
      recalled: [created_in_phase_1]
      answer_contains: ["Markdown"]
      answer_excludes: ["SQLite", "向量库仍在使用"]
final_expect:
  active_count: 1
  duplicate_topics: 0
```

每个阶段保存：

- conversation ID 与原始输入；
- Main Agent 工具轨迹和回答；
- Reflection/Maintenance 决策与模型 usage；
- 阶段前后的 Core、Index、active、archive 快照；
- 总耗时和各模型 Token。

## 4. 必测场景组

### Recall

- 精确询问：问题直接命中标题或 Cue，应该读取目标记忆；
- 间接询问：表达方式与 Cue 不同，但语义相关，仍应召回；
- 干扰项：5～25 条相似记忆中只读取目标，不乱读其他条目；
- 无需记忆：答案完全来自当前输入，不应调用 `memory.read`；
- 归档隔离：archive 中存在高度相关内容，也不能通过普通 Recall 读取；
- 错误记忆：记忆与当前代码冲突时，应以真实工具证据为准。

### Reflection 写入

- 明确、稳定的项目决定应 CREATE；
- 天气、问候、临时错误和当前进度应 NONE；
- 用户长期偏好应走 Core，不应创建 Ordinary Memory；
- 当前进度应进入 Task，不应创建 Ordinary Memory；
- 已有主题且本轮成功读取时应 UPDATE，不重复 CREATE；
- 只有 Index Cue、没有读取全文时不得 UPDATE；
- Reflection 输入出现诱导文本时，不能把工具输出中的指令当成记忆政策。

### 跨会话闭环

- A 会话产生记忆，B 会话用不同措辞成功回答；
- CLI 重启后仍能召回，证明不是进程内状态；
- 清空聊天历史后仍能召回，证明不是 Conversation History 冒充 Memory；
- 同一项目共享，未来支持多项目后增加 namespace 隔离场景。

### 更新与并发

- UPDATE 同步修改 title、summary、content 和 INDEX；
- 两个 Run 读取同一 revision，后提交的旧更新必须冲突；
- 冲突不得改变 Memory 文件、Index 或主 AgentResult；
- 重复主题不能因为措辞变化不断创建新 ID。

### Capacity Maintenance

- 满 25 条时只从授权候选中归档；
- 重要但低频记忆不应仅因时间久被归档；
- 过时、重复、被替代的候选应优先归档；
- 证据不足时应 defer；
- 维护模型失败或超时不能影响主回答；
- 维护期间候选变化时必须拒绝陈旧决策。

## 5. 核心指标

### 召回指标

- Recall Hit Rate：需要目标记忆的阶段中，成功读取目标的比例；
- Recall Precision：所有成功读取中，真正相关记忆的比例；
- Unnecessary Read Rate：无需记忆的阶段仍调用 `memory.read` 的比例；
- Grounded Answer Rate：回答包含目标事实且没有引入冲突事实的比例。

不能只看 Recall@K，因为当前架构没有自动 Top-K；主模型的工具选择就是检索器。

### 写入指标

- Write Precision：所有 CREATE/UPDATE 中确实值得长期保存的比例；
- Write Recall：所有应保存信息中被正确保存的比例；
- Action Accuracy：none/create/update 分类准确率；
- Duplicate Creation Rate：已有主题被错误 CREATE 的比例；
- Layer Misroute Rate：Core、Task、普通记忆之间写错层级的比例；
- Update Preservation Rate：更新后旧的重要事实仍被保留的比例。

### 维护指标

- Safe Archive Rate：归档对象确实过时、重复或被替代的比例；
- Critical Retention Loss：仍有关键价值的记忆被归档的比例，此项应设为硬失败；
- Defer Accuracy：证据不足时正确 defer 的比例；
- Capacity Convergence：合法维护后 active 是否回到上限以内。

### 成本指标

- Main、Reflection、Maintenance Token 分开统计；
- Reflection 调用次数与平均延迟；
- 每成功产生一条有效记忆的 Token 成本；
- 每次正确召回增加的 Token 和延迟；
- Memory 开启与关闭的回答质量差值，而不是只看绝对 Token。

## 6. 评分方法

确定性项目直接由 Harness 判定：action、ID、工具轨迹、revision、文件状态、事件顺序和
容量。正文语义采用三层评分：

1. 场景声明必须保留和禁止出现的关键事实；
2. 独立 Judge Model 按固定 Rubric 判断相关性、耐久性、重复和事实保留；
3. 对 Judge 分歧和高风险归档样本进行人工抽查。

不要让被测 Reflection 模型同时担任 Judge。报告中必须保留原始提案、最终文件和 Judge
理由，避免只输出一个不可解释的总分。

## 7. 基线与运行方式

每条语义场景建议至少运行 3 次，并做两组对照：

```text
Memory OFF：不注入 Core/Index，不运行 Reflection
Memory ON：启用完整长期记忆闭环
```

关键指标是 Memory ON 相比 OFF 对跨会话回答的提升，以及为此增加的成本和错误污染。
首批场景建议控制在 20 条：Recall 6、Reflection 6、跨会话 3、Update 3、Maintenance 2。
先形成稳定小集，再根据真实 Bad Case 追加场景，不能为了提高通过率反向修改测试答案或
把模型策略绑死成固定工具轨迹。

## 8. 推荐实现顺序

当前已经完成：

1. 独立 `tests/memory_eval/`，包含多阶段 Scenario、Loader、Runner、断言和快照采集；
2. 10 条不依赖 Judge 的基础语义场景；
3. Memory ON/OFF 对照开关和 Main/Reflection/Maintenance 分模型 Token 统计；
4. Markdown 报告与可复现运行现场。

运行全部 Memory Eval：

```bash
cd backend
.venv/bin/python -m tests.memory_eval.run_live --runs 3 --print
```

只跑召回组并启用 OFF 对照：

```bash
.venv/bin/python -m tests.memory_eval.run_live \
  --tag recall --runs 3 --compare-off --print
```

真实模型评测会产生 API 成本，pytest 只验证 Harness 自身，不会调用真实 API。下一阶段
再接入独立 Judge，评估正文质量、重复主题和事实保留，并扩充 Maintenance 人工审计集。

每个 Memory ON Phase 还会在运行现场生成：

```text
<run-root>/<scenario>/run-<n>/on/artifacts/<phase>.json
```

其中保存当前 Phase 的用户输入、最终回答、Reflection 完整结构化输入、模型原始文本
输出、解析后的 action、mutation 结果和错误。原始 I/O 只在 Eval 中通过
`capture_raw_io=true` 开启，生产 CLI 默认关闭，避免把用户上下文无条件复制到 Trace。
