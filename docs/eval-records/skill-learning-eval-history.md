# Skill Learning V1：Bad Case 修复与 Eval 效果提升记录

> 原与根目录 `task.md` 同级，现集中到 `docs/` 下（`docs/task.md` 为任务日志）。
> 本文件记录最近几轮 Skill Learning V1 的真实 Bad Case、修复方式，
> 以及每一次修复带来的 Eval 效果提升（全部为真实模型 deepseek-v4-flash 实测，
> 不是"测试通过"的口头承诺）。
>
> 时间跨度：2026-08-18（Skill V1 Debug → Eval 收口 → Progressive Disclosure →
> UPDATE description 契约）。

---

## 0. 一句话时间线

```
Skill V1 Debug + Live Eval        →  pass rate 67% (18/27)   报告 skill_learning_live_20260818.md
↓ Eval 指标收口 + 场景修正 + Human Gate 预置
Skill Learning V1 Eval 收口        →  pass rate 87% (26/30)   报告 skill_learning_live_20260818b.md
↓ Distiller 只加载相关 Skill 正文（Progressive Disclosure）
Distiller Progressive Disclosure   →  pass rate 91% (30/33)   报告 skill_learning_live_20260818c.md
↓ UPDATE description 契约修复
UPDATE description 契约 Bad Case   →  update + 无 description → Candidate 创建成功（不再 validation failed）
```

> ⚠️ 各阶段场景数不同（9 → 10 → 11），pass rate 不能直接横向对比；但**提升方向**和
> **每个 Bad Case 是否真的消失**是可靠的。

---

## 1. 阶段一：Skill Learning V1 Debug + 真实模型 Live Eval（67%）

**报告**：`backend/tests/eval/reports/skill_learning_live_20260818.md`
**结论**：18/27 pass，Cluster Precision/Recall=1.00，Action Accuracy=1.00，FP=0%，
Duplicate=0%，Pitfall Recall=0.25；45 calls / 51,918 tokens / 128.8s。

### Bad Case 1：TraceEvidenceBuilder 与真实 task_update 参数错位
- **现象**：Evidence 看不到真实内容（模型以为"只是发生了 change"）
- **根因**：用内部 TaskPatch 字段（add_constraints/add_key_facts/replace_steps）解析
  Trace，但 Trace 保存的是模型真实发出的 ToolCall（constraints/facts/state/steps/
  step_id/step_status/step_note）
- **修复**：`evidence.py` 按真实 API 字段输出具体变化（goal/status/state replaced/
  constraints added/facts added/plan replaced/step <id> -> <status>: <note>），bounded
- **测试**：6 例对齐真实参数

### Bad Case 2：Mining 模型失败永久吃掉 Batch
- **现象**：模型 timeout/错误后该批 Task 永久失去学习机会
- **根因**：pending 达到 batch 后先把 Task 移入 processed 再调模型
- **修复**：watermark 增加 `inflight` batch（batch_id/task_ids/attempt/last_error）；
  失败保留 inflight 供下次触发点重试（at-least-once），达到 max_attempts(3) 才放弃，
  防无限重试；一次 maybe_run_mining 最多一次模型调用
- **测试**：失败不 processed / 重启 inflight 仍在 / 二次成功 / 成功后不重复 / invalid JSON

### Bad Case 3：Pending Candidate 重复创建
- **现象**：下个 batch 又生成同义 Candidate
- **根因**：Distiller 只看 Existing Skill Catalog；不同 batch 的 source_task_ids 天然不同，
  exact-source 去重失效
- **修复**：Distiller 输入加 pending candidates 轻量上下文（prompt 明确"被覆盖→none"）+
  Service 层 exact-name 确定性防线；reject 不参与去重
- **测试**：同名不创建 / 语义覆盖返回 none / reject 不阻止未来

### Bad Case 4：Usage 只统计 Mining
- **现象**：一次 batch = 1 mining + N distillation，但 Outcome 只记 mining 的 usage
- **修复**：`SkillLearningOutcome` 聚合 pattern_mining_calls / distillation_calls /
  tokens / 各阶段 duration；CLI 完整输出

### 真实模型暴露的其他问题
- deepseek-v4-flash 把列表字段输出为 **null / 单字符串** → `_Distilled` 归一化
- 大 prompt 偶发 **空 content** → 对 deepseek 禁用 thinking
- candidate 名不可预知 → Judge 把 exact-name 软化为记录项
- learning-04/05 大部分 FAIL 根因：**Live 场景 Trace 证据强度不足**（缺"失败→修正→
  验证成功"闭环），模型保守返回 none，非管线 Bug

---

## 2. 阶段二：Skill Learning V1 Eval 收口（87%）

**报告**：`backend/tests/eval/reports/skill_learning_live_20260818b.md`
**结论**：26/30 pass，Pattern Detection Recall=0.93(14/15)，Action Accuracy=0.75(9/12)，
Positive Abstention=0.33(3/9)，FP=0%(0/6)，Duplicate=0%(0/3)，Pitfall=0.50；
35 calls / 43,042 tokens / 96.1s。

### Bad Case 5：Eval 指标口径错误（不是模型问题，是度量问题）
- **问题**：
  - positive 场景没发现 cluster 时 Pattern Detection Recall 被跳过（记 N/A）而非 0
  - Action Accuracy 依赖 Skill 名字匹配，模型起不同但合理的名就判错
  - False Positive Rate 除全部 runs（含 positive），分母错
  - Duplicate Rate 除全部 runs，分母错
- **修复**（`learning_judge.py`）：
  - Pattern Detection Recall：positive 场景必须记 0/1，未检测记 0
  - Action Accuracy：只看 CREATE/UPDATE/NONE（`expected_action`），不依赖名字
  - FP 只除 negative 场景；Duplicate 只除 duplicate 场景
  - 新增 Positive Abstention Rate
- **效果**：指标从"好看但失真"变为"口径正确、能定位模型卡在哪"

### Bad Case 6：Distillation action=none 时报告信息不足
- **问题**：模型返回 none 时报告只显示"无 Candidate"，看不到为什么不沉淀
- **修复**：`DistillationOutcome` 增加 reason/proposed_name/existing_skill_name
  （action=none 也保留）；`SkillLearningOutcome.distillations` 记录每个 cluster 的
  action/reason/proposed_name/existing_skill_name/error；报告新增 "Actual Distillation" 块
- **效果**：每个失败都能看到模型的完整判断依据

### Bad Case 7：Human Gate 测试依赖模型随机产 Candidate
- **问题**：learning-06/07/08（pending 不可见 / accept / reject）依赖 Distiller 产候选，
  Distiller 偶发 none 就误判 Human Gate 失败
- **修复**：这些场景改为 `human_gate_only` 直接**预置 Candidate**，不调模型；
  机制测试与 Distiller 解耦
- **效果**：Human Gate 3/3 稳定 PASS

### 场景修正
- learning-04：pitfall 期望改为真实 Trace 内容（去掉不存在的 reinstall/环境）
- learning-05 拆成 05a（证据不足+已有 skill 覆盖→NONE）/ 05b（强证据→UPDATE）
- learning-09 标记为 duplicate 场景

---

## 3. 阶段三：Distiller Progressive Disclosure（91%）

**报告**：`backend/tests/eval/reports/skill_learning_live_20260818c.md`
**结论**：30/33 pass，Pattern Detection=0.89(16/18)，Action Accuracy=0.87(13/15)，
Abstention=0.22(2/9)，FP=0%，Duplicate=0%；48 calls / 55,231 tokens / 132.1s。

### Bad Case 8：只给 name+description 无法可靠区分 UPDATE vs NONE
- **问题**：Distiller 只拿到 Existing Skill 的 name + description，模型不知道新
  Procedure 是否已被现有 Skill 正文覆盖 → UPDATE vs NONE 判断无据（之前 learning-05b
  因此 0/3）
- **修复**（Progressive Disclosure，不改其他架构、不做 embedding/RAG）：
  - 两阶段蒸馏：① `_RELEVANCE_PROMPT` 用 cluster 摘要 + catalog(name+description)
    轻量筛选相关 Skill（≤3，可空）；② `skill_loader` 加载相关 Skill 完整正文
    （截断 4000 chars/个），随 `related_skills` 进入最终判断
  - 规则（结构性，非调分）：无相关 Skill → CREATE；正文已完整覆盖 → NONE；
    同一 Skill 但多个 Task 提供正文缺失的稳定新步骤/pitfalls/verification → UPDATE
- **效果（真实模型）**：
  - CREATE 可靠：learning-02 3/3
  - **NONE 可靠（正文已覆盖）**：learning-05c 3/3，reason 引用正文
  - UPDATE 仍不稳定：learning-05b 在 create 专项 / none(minor enrichment) / update
    三种结果间波动（模型行为，非管线 Bug）
- **成本增量**：calls 35→48 (+37%)、tokens 43,042→55,231 (+28%)、avg 1435→1674
  tokens/eval batch (+17%)；无关/无 Skill 场景成本不变

### Bad Case 9（Progressive Disclosure 暴露）：UPDATE 时模型常省略 description
- **现象**：真实模型正确返回 `action=update` + `existing_skill_name=debug-python`，
  但没返回 description → `_to_candidate` 转成 "" → `SkillCandidate` 要求非空 →
  **Candidate 构造失败**（candidate validation failed）
- **根因**：Distillation 输出契约（description 可缺省）与 SkillCandidate 数据模型契约
  （description 非空）不一致
- **修复**（只改 `_to_candidate`，不改 Eval / mining / distillation 判断逻辑）：
  - CREATE：模型必须提供 description，缺失 → 明确失败
  - UPDATE：有 description 用模型输出；无则继承 `existing_skill_name` 对应 Skill 的
    description；找不到 → 明确 `ValueError`（不静默兜底）
  - 最终 description 永不为空
- **回归测试 4 例**：UPDATE+desc=null→继承创建成功 / UPDATE+desc 非空→用模型输出 /
  UPDATE 指向不存在 skill→明确失败 / CREATE+desc 缺失→仍失败
- **真实模型复验（learning-05b）**：`action=update` + 无 description →
  **SkillCandidate 成功创建**（继承 catalog description，error=null），
  "candidate validation failed" 消失

---

## 4. Eval 效果提升汇总

| 指标 | 阶段一 (67%) | 阶段二 (87%) | 阶段三 (91%) |
|---|---|---|---|
| 场景 / runs | 9 / 27 | 10 / 30 | 11 / 33 |
| pass rate | 67% (18/27) | 87% (26/30) | 91% (30/33) |
| Pattern Detection Recall | —（未按新口径） | 0.93 (14/15) | 0.89 (16/18) |
| Action Accuracy | 1.00（依赖名字） | 0.75 (9/12) | 0.87 (13/15) |
| Positive Abstention | — | 0.33 (3/9) | 0.22 (2/9) |
| False Positive | 0% | 0% (0/6) | 0% (0/6) |
| Duplicate | 0% | 0% (0/3) | 0% (0/3) |
| Pitfall Recall | 0.25 | 0.50 | 0.50 |
| 总 calls / tokens / 时长 | 45 / 51,918 / 128.8s | 35 / 43,042 / 96.1s | 48 / 55,231 / 132.1s |
| avg tokens / eval batch | ~1,923（20-Task） | 1,435 | 1,674 |

**关键点（避免误读）**：
1. 阶段一 Action Accuracy=1.00 是**旧口径**（依赖名字匹配，只在产出候选时算）；
   阶段二换成正交口径（CREATE/UPDATE/NONE）后 0.75，**数字下降但度量更诚实**。
2. 阶段三 pass rate 提升主要来自：Human Gate 预置解耦 + learning-05c 稳定 NONE +
   模型在多轮中的随机波动；**UPDATE 仍未稳定**（模型倾向 create 专项或保守 none）。
3. 每个阶段都如实保留了失败样本（报告含真实模型输出与 reason），没有隐藏失败。

---

## 5. 核心结论（模型到底卡在哪）

- **Pattern Mining**：高度可靠（Precision/Recall=1.00；无关任务/机械操作从不聚类），
  偶发"同一 prompt 概率性返回空 clusters"（是少量 detection 失败的主因）。
- **Distillation**：CREATE / NONE 判断可靠且有据（正文已覆盖→NONE 3/3）；
  **UPDATE 是最弱一环**——模型面对"已存在相关 skill"在 create(专项化) / none(已覆盖或
  minor enrichment) / update 之间不稳定，取决于 description 是否隐含、证据厚度
  （模型要求可观察执行细节而非 plan 声明）、create vs update 的边界。
- 结论：**正文让 NONE/CREATE 判断有据；UPDATE 稳定性是后续要解决的真正难点**
  （模型行为层面，非管线 Bug）。

---

## 6. 阶段四（learning-10 收口：钉死语义 + 修 Eval 误判）

> 2026-08-18。learning-10 上一轮 3/3 FAIL（模型 3 次全 create 专项名
> fix-python-interpreter-mismatch），且 run2 pitfall recall 被 Eval 跨语言误判成 0.00。
> 本次只改两处，不改主架构。

| 指标 | 阶段四（本轮） | 阶段三（learning-10） |
|---|---|---|
| 场景 / runs | 1 / 3 | 1 / 3 |
| pass rate | 100% (3/3) | 0% (0/3) |
| Action Accuracy (UPDATE) | 1.00 (3/3) | 0.00 (0/3) |
| Cluster Precision / Recall | 1.00 / 1.00 | 1.00 / 1.00 |
| Trace deterministic checks | 全过（steps exact + evidence 关键词/禁词） | 全过 |
| Pitfall Recall | 1.00 (3/3) | run1/3=1.00、run2=0.00（Eval 误判） |
| 总 calls / tokens / 时长 | 9 / 20,179 / 30.3s | 9 / 19,599 / 32.4s |

**两个修复**：
1. `_DISTILLATION_PROMPT`：CREATE/UPDATE/NONE 判定从"body 无覆盖→create"改为
   **task family 语义**（同 family → NONE 或 UPDATE；不同 family 且独立复用价值 → CREATE）。
   模型三次 reason 都明确引用 "same task family as existing 'debug-python' ... therefore
   update"，说明 Prompt 语义修改直接生效（非测试特判、未加 hard-coded 名字）。
2. Eval pitfall 关键词改为**中英同义组**（`[[全局, global], [解释器, interpreter]]`）：
   concept-based recall，组内命中任一 alias 即算命中。修复了"模型输出英文 pitfalls、
   Eval 用中文关键词 substring"导致的跨语言误判。

**结论**：UPDATE vs CREATE 的稳定边界**可以**通过把"同 family 扩展现有 Skill"写进
Distillation 语义来修正（上一轮历史结论"UPDATE 稳定性是模型行为难点"在本场景被推翻
——它同时是 Prompt 语义问题）。新 Bad Case：无；仅候选文本风格波动（run1/3 procedure
带编号前缀，run2 无；verification 条数 1~4 不等）。

