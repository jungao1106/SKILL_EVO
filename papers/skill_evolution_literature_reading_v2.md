# Skill Evolution 文献阅读 v2：Training/Evaluation Framing

相关文档：

- [2602.08234v1.pdf](2602.08234v1.pdf): **SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning**
- [2605.06614v1.pdf](2605.06614v1.pdf): **SkillOS: Learning Skill Curation for Self-Evolving Agents**
- [2605.23904v2.pdf](2605.23904v2.pdf): **SkillOpt: Executive Strategy for Self-Evolving Agent Skills**
- [skill_evolution_literature_reading.md](skill_evolution_literature_reading.md): 原始文献阅读和 5.4 stage-specific skill 标准

## 0. v2 一句话结论

相比于把问题表述为“优化 skills”或“训练一个 skill curator”，更合适的抽象是把整个系统拆成 **training 阶段** 和 **evaluation 阶段**。

在 training 阶段，我们用标准 RL 风格的数据流训练两个外部 agent：

- **Curator agent** 学会在 training data 上为每个 task 写、改、删五阶段 skills。
- **Reward agent** 学会在没有真实标签的情况下评价当前 skills 是否值得接受，并用真实标签作为训练时的 oracle 校准信号。

在 evaluation 阶段，curator agent 与 reward agent 都被冻结，直接用于具体下游任务。它们不再访问真实标签，而是在任务内部定制并优化五阶段 skills，再把最终 skill pack 注入 executor。

因此，5.4 中的五阶段标准应该被提升为最上层 harness contract：

```text
每个 task = reproduce skill
          + localize skill
          + edit skill
          + validate skill
          + recover skill
```

这个 contract 不再是可选的文档组织方式，而是我们定义的 task-level optimization harness。

## 1. 对三篇论文的重新定位

### 1.1 SkillRL 提供“training”视角

SkillRL 的核心不是简单存 memory，而是用成功和失败轨迹蒸馏 skills，再通过 SFT + RL 让 policy 学会使用 skills。它对我们最重要的启发是：

- skill 需要来自经验蒸馏，而不是 raw trajectory replay。
- 成功和失败都应该产生训练信号。
- 只给模型塞 skill 不够，需要让某个 policy 学会稳定使用 skill。

但我们当前不一定要训练 executor model 的参数。我们可以把类似的 training 思路转移到 curator/reward 的文本策略上：训练它们如何写 skill、如何评价 skill、如何把失败变成下一轮改进信号。

### 1.2 SkillOS 提供“curator RL”视角

SkillOS 把 frozen executor 和 trainable curator 分开。curator 通过 insert/update/delete 管理 SkillRepo，并用 grouped tasks 和 composite reward 训练。

这与我们的 v2 方向最接近，但还需要两点改造：

- SkillOS 主要训练 curator，而我们认为还必须训练 reward agent。因为 evaluation 阶段没有真实标签，reward agent 是验证门和监督信号的来源。
- SkillOS 的 SkillRepo 更新粒度需要落到 SWE task 的五阶段 harness 上。curator 不是自由地维护任意 skill collection，而是在五个 stage slot 中做 bounded operations。

### 1.3 SkillOpt 提供“parameter-free TextGrad”视角

SkillOpt 不训练模型权重，而是把 skill document 当作外部文本状态，用 bounded edits、validation gate、rejected buffer 和 slow/meta update 做稳定优化。

v2 中我们保留这个思想，但把优化对象扩展成两个层次：

- training 阶段：用 TextGrad 优化 curator/reward 的文本策略。
- evaluation 阶段：用冻结后的 curator/reward 优化具体 task 的五阶段 skill 文本。

也就是说，SkillOpt-style 的 parameter-free 优化仍然成立，但不应该只优化最终 skills；它还应该优化产生和评价 skills 的两个文本 agent。

## 2. 最上层 harness：五阶段 task skill

我们同意原文档 5.4 的 stage-specific skill 标准，并把它固定为每个 task 的最上层结构。

每个 task 的 skill state 记为：

```text
S_x = {
  s_reproduce,
  s_localize,
  s_edit,
  s_validate,
  s_recover
}
```

每个 stage skill 必须回答不同问题：

- `reproduce`: 如何最小化复现，什么现象算复现成功，是否需要 helper script。
- `localize`: 从复现证据到 owner code 的搜索路径，如何排除噪声路径，何时停止搜索。
- `edit`: 最小 patch 原则，需要保留的 API/行为，哪些层不应该碰。
- `validate`: 最小验证命令、回归命令、验证失败时如何判断是 patch 错还是 test 选择错。
- `recover`: 无意义 rollout、重复搜索、无 diff、测试环境坏掉、验证卡住时如何复位。

这五个 stage 不只是文档标题，而是 harness 的控制面。executor 每次解题时都应该被这些 stage skill 引导；curator 每次更新时也只能围绕这五个 stage 做 insert/update/delete/attach_script 等操作。

## 3. Training 阶段总体目标

training 阶段的目标不是直接得到某个 benchmark 的最终 skill，而是得到两个可迁移的文本 agent：

```text
C* = trained curator agent
R* = trained reward agent
```

其中：

- `C*` 学会如何从 task、trajectory、失败、成功和旧 skill 中产生高质量五阶段 skill updates。
- `R*` 学会如何在没有真实标签的情况下，预测一个 candidate skill state 是否会提升下游 executor 表现。

training data 中可以访问真实标签或 verifier outcome，但这些标签只用于训练和校准，不应该进入 evaluation 阶段的 reward prompt 或 skill content。

## 4. Training 数据组织

训练样本不应该是孤立 task，而应该是 grouped task streams，继承 SkillOS 的 delayed feedback 思想。

一个训练 group 可以写成：

```text
G = (x_1, x_2, ..., x_n)
```

group 内 task 可以根据以下信号聚类：

- repo 相同或相近。
- touched paths 相近。
- issue keyword / failure signature 相近。
- test command 或 exception 类型相近。
- stage failure 类型相近，例如 reproduce 卡住、localize 误入无关模块、validate 选择错误测试。

每个 task 的 training record 至少包含：

```text
{
  task_id,
  problem_statement,
  optional_initial_repo_signals,
  current_five_stage_skills,
  rollout_trajectory,
  candidate_skill_operations,
  label_or_verifier_outcome,
  label_hidden_view
}
```

其中 `label_or_verifier_outcome` 只给 oracle training 使用；`label_hidden_view` 是 reward agent evaluation 时可见的输入视图，必须去除真实答案、最终标签和 hidden test 结论。

## 5. 两个 reward 视图：oracle 与 label-free

reward agent 的训练必须区分两个视图。

### 5.1 Oracle reward view

oracle view 可以访问真实标签或 verifier outcome，用来判断 candidate skill state 的真实价值：

```text
R_oracle(x, S_candidate) -> true utility
```

在 SWE-Bench 场景中，true utility 可以来自：

- 最终 verifier reward。
- hidden/public test 通过情况。
- patch 是否真正解决 issue。
- 与 gold behavior 的一致性。

oracle view 只用于 training，不部署。

### 5.2 Label-free reward view

evaluation 阶段没有真实标签，所以部署的 reward agent 必须工作在 label-free view：

```text
R_text(x, S_current, S_candidate, trajectory_without_label) -> predicted utility
```

它能看的内容包括：

- task statement。
- 当前五阶段 skills。
- candidate operations。
- executor trajectory。
- command output。
- public tests 或可运行验证结果。
- static validity checks。
- rejected buffer 和已知失败模式。

它不能看：

- hidden label。
- gold patch。
- final hidden test answer。
- 任何由真实标签直接泄漏出的自然语言描述。

training 的关键就是让 `R_text` 在去除真实标签后仍然尽可能逼近 `R_oracle` 的排序和接受/拒绝决策。

## 6. Curator agent 的训练目标

curator agent 的 action 不是自由生成一份大文档，而是生成结构化 skill operations：

```text
insert_stage_skill
update_stage_skill
delete_stage_skill
attach_script
delete_script
```

每个 operation 必须绑定 stage：

```text
stage in {reproduce, localize, edit, validate, recover}
```

curator 的训练目标是最大化后续 task 或当前 task 后续 rollout 的真实/预测收益：

```text
maximize E[ task_return | C_text, five_stage_skill_state ]
```

具体拆成以下目标：

- **Outcome utility**: candidate skills 是否提高 executor 成功率。
- **Stage coverage**: 五个 stage 是否都有明确 trigger、actions、evidence、stop condition。
- **Operation validity**: operation schema 是否合法，路径和 frontmatter 是否正确。
- **Faithfulness**: skill 是否忠实于 trajectory 和 task evidence，不编造不存在的 repo 事实。
- **Compactness**: skill 是否压缩经验，而不是复制长日志。
- **Non-overfit**: skill 是否避免硬编码一次性 sandbox、绝对路径、hidden label 或 task-specific 答案。
- **Recover value**: 是否能减少重复搜索、无 diff、错误测试、验证环境卡死等 SWE agent 常见失败。

curator 的 RL loop 可以写成：

```text
for each grouped training stream G:
  initialize five-stage skill state S_0
  for each task x_i in G:
    sample K curator operation sets O_i^1 ... O_i^K from C_text
    apply each O_i^k to produce candidate skill state S_i^k
    run executor or replay/scored harness with S_i^k
    score candidates by R_oracle and R_text
    convert high/low advantage cases into textual critiques
    TextGrad-update C_text
```

这里的 “policy update” 不是模型参数更新，而是 TextGrad 对 curator 的文本状态做 edit。

## 7. Reward agent 的训练目标

reward agent 的目标不是替代 verifier，也不是写漂亮评价，而是成为一个 **label-free skill acceptor**。

它应该输出：

```text
{
  "score": 0-1,
  "decision": "accept | reject | abstain",
  "stage_scores": {
    "reproduce": ...,
    "localize": ...,
    "edit": ...,
    "validate": ...,
    "recover": ...
  },
  "risk": "...",
  "textgrad_feedback_for_curator": "..."
}
```

reward agent 的核心训练损失不是一个可微标量模型，而是一组可由 TextGrad 驱动的文本目标：

- **Pairwise ranking**: 如果 candidate A 的 oracle outcome 优于 candidate B，reward agent 应该在 label-free view 下偏好 A。
- **False accept penalty**: 错误接受有害 skill 的代价高于错误拒绝一个可能有用的 skill。
- **Calibration**: `accept` 应该对应较高真实成功概率，低置信度时应该 `abstain`。
- **Stage attribution**: reward agent 不只给总分，还要指出是哪个 stage 的 skill 导致收益或风险。
- **Anti-leakage**: 任何依赖真实标签、gold patch、hidden test 结论的评价规则都应被惩罚。
- **Curator-useful feedback**: 输出必须能反过来作为 curator 的 TextGrad 监督信号，而不是空泛评论。

reward agent 的训练 loop 可以写成：

```text
for each training comparison:
  observe label-free inputs:
    x, S_current, S_candidate_a, S_candidate_b, trajectories_without_labels
  predict preference and accept/reject decisions with R_text
  compare with oracle outcomes from labels/verifier
  identify false accept, false reject, misattribution, leakage risk
  TextGrad-update R_text rubric, evidence rules, calibration rules
```

## 8. Parameter-free TextGrad：到底更新什么

parameter-free 的意思是：不更新 LLM 权重，不训练一个隐藏的 dense reward model，不把真实标签编进部署 prompt。所有可学习状态都必须是可读、可审计、可版本化的文本。

### 8.1 Curator agent 的可优化文本状态

curator agent 应优化以下文本对象：

```text
C_text = {
  curator_system_prompt,
  five_stage_skill_templates,
  operation_selection_policy,
  evidence_distillation_policy,
  edit_budget_policy,
  script_attachment_policy,
  accepted_rejected_examples,
  curator_meta_memory
}
```

解释如下：

- `curator_system_prompt`: curator 的角色、目标、禁止事项和输出格式。
- `five_stage_skill_templates`: 五个 stage 各自应该包含哪些字段和 stop condition。
- `operation_selection_policy`: 何时 insert、何时 update、何时 delete、何时保持不变。
- `evidence_distillation_policy`: 从 trajectory 中抽取哪些证据进入 skill，哪些噪声必须丢弃。
- `edit_budget_policy`: 每轮最多改几个 stage，如何避免一次性重写全部 skill。
- `script_attachment_policy`: 何时生成 helper script，脚本需要满足哪些验证门槛。
- `accepted_rejected_examples`: 训练中成功和失败 operation 的少量文本例子。
- `curator_meta_memory`: 长期总结，例如“哪些 skill 写法经常过拟合”“哪些 recover 规则最有用”。

TextGrad 对 curator 的更新形式是自然语言 critique 到文本 edit：

```text
false operation / low-return candidate
  -> identify missing rule or harmful rule
  -> rewrite curator prompt/template/meta-memory
  -> next rollout samples better operations
```

curator 不应该优化：

- executor 模型参数。
- hidden test 标签。
- 一个全局 SWE 万能 skill。
- 未经验证的长 trajectory 摘要。
- 不受五阶段约束的任意 memory。

### 8.2 Reward agent 的可优化文本状态

reward agent 应优化以下文本对象：

```text
R_text = {
  reward_system_prompt,
  label_free_evidence_rubric,
  pairwise_comparison_policy,
  stage_scoring_rubric,
  accept_reject_abstain_policy,
  calibration_notes,
  anti_leakage_policy,
  reward_meta_memory
}
```

解释如下：

- `reward_system_prompt`: reward agent 的角色、输出 schema 和保守性要求。
- `label_free_evidence_rubric`: 没有真实标签时，哪些证据可以支持接受 skill。
- `pairwise_comparison_policy`: 如何比较两个 candidate skill state。
- `stage_scoring_rubric`: 五个 stage 分别如何打分，如何定位风险。
- `accept_reject_abstain_policy`: 什么时候接受，什么时候拒绝，什么时候因为证据不足而弃权。
- `calibration_notes`: 从训练中学到的置信度校准规则。
- `anti_leakage_policy`: 防止 gold answer、hidden label、oracle 结论进入部署评价。
- `reward_meta_memory`: 哪些判断模式曾经 false accept 或 false reject。

TextGrad 对 reward 的更新形式是：

```text
label-free prediction disagrees with oracle outcome
  -> identify why the rubric preferred the wrong candidate
  -> rewrite evidence rubric / comparison policy / abstain rule
  -> reduce future false accept and miscalibration
```

reward agent 不应该优化：

- 一个带隐式权重的黑盒 reward model。
- 只能在有真实标签时工作的 evaluator。
- 直接泄漏 training labels 的答案模板。
- 与五阶段 skill 无关的泛泛代码质量 judge。

## 9. Training 中 curator 与 reward 的相互作用

两个 agent 的关系可以理解为 actor-critic，但 critic 是文本化、可审计、label-free 的。

```text
curator proposes skill operations
reward predicts whether to accept
oracle uses labels/verifier judge true outcome during training
reward learns to imitate oracle without labels
curator learns from reward feedback and oracle advantage
```

更具体地：

1. Curator 生成多个 candidate skill states。
2. Reward agent 在 label-free view 下排序这些 candidates。
3. Oracle reward 在 training 中给出真实 outcome。
4. 如果 reward 排序错了，TextGrad 更新 reward rubric。
5. 如果 curator 反复生成低 oracle-return 的 edits，TextGrad 更新 curator prompt/template/meta-memory。
6. 更新后的 reward 给 curator 提供更稳定的监督信号。

这使得 training 阶段形成闭环：

```text
C_text -> candidate five-stage skills -> executor rollout
       -> R_text label-free score -> accept/reject signal
       -> oracle calibration during training
       -> TextGrad updates for R_text and C_text
```

## 10. Evaluation 阶段流程

训练完成后，部署的是：

```text
C*
R*
```

evaluation task `x_test` 上没有真实标签。流程为：

1. 初始化五阶段 skill state，可以为空，也可以从相似 training tasks 检索 seed skills。
2. executor 使用当前 `S_x` 解题，产生 trajectory。
3. curator `C*` 从 trajectory 中提出 bounded operations。
4. reward `R*` 在 label-free view 下评价 candidate skill state。
5. 只有通过 validity gate 和 reward gate 的 candidate 才被接受。
6. 重复若干轮，得到最终 `S_x_best`。
7. executor 使用 `S_x_best` 做最终 attempt。
8. benchmark hidden label 只用于最终报告，不用于步骤 1 到 7。

evaluation 阶段优化的是具体 task 的五阶段 skill 文本：

```text
S_x_best = optimize_text(
  initial_S_x,
  curator=C*,
  reward=R*,
  harness=five_stage_executor_harness
)
```

这里的优化仍然是 parameter-free 的：所有变化都体现在 task skill 文本、script resources、accepted/rejected buffers 中。

## 11. 建议的 artifact 结构

可以把 training 和 evaluation artifact 分开。

```text
analysis/
  training/
    curator_policy.md
    reward_policy.md
    curator_meta_memory.md
    reward_meta_memory.md
    reward_calibration_cases.jsonl
    accepted_curator_ops.jsonl
    rejected_curator_ops.jsonl

  evaluation/
    <benchmark>/
      <task_id>/
        skill_state_v000/
        skill_state_v001/
        reward_decisions.jsonl
        curator_ops.jsonl
        final_skill_pack/

skills/
  tasks/
    <task_id>/
      reproduce/<skill>/SKILL.md
      localize/<skill>/SKILL.md
      edit/<skill>/SKILL.md
      validate/<skill>/SKILL.md
      recover/<skill>/SKILL.md
```

其中 `curator_policy.md` 和 `reward_policy.md` 是训练后冻结的 agent text policy；`skills/tasks/<task_id>/...` 是 evaluation 阶段为具体 task 生成和优化的外部 skill state。

## 12. 最后问题回答：curator agent 和 reward agent 应该优化什么

最短答案：

**curator agent 优化的是“如何产生五阶段 skill operations 的文本策略”，以及 evaluation 时每个 task 的五阶段 skill state。**

更具体地，training 阶段 curator 应通过 TextGrad 优化：

- curator system prompt。
- 五阶段 skill 模板。
- insert/update/delete 的选择规则。
- evidence distillation 规则。
- bounded edit budget 规则。
- helper script 生成和验证规则。
- accepted/rejected operation examples。
- curator meta-memory。

它的目标不是写看起来合理的 skill，而是选择能提升下游 task return 的 bounded operations。

**reward agent 优化的是“无真实标签时评价五阶段 skill state 的文本 rubric 和 acceptor 策略”。**

更具体地，training 阶段 reward 应通过 TextGrad 优化：

- reward system prompt。
- label-free evidence rubric。
- pairwise skill comparison policy。
- stage-wise scoring rubric。
- accept/reject/abstain policy。
- calibration notes。
- anti-leakage policy。
- reward meta-memory。

它的目标不是复述真实标签，而是在去除真实标签后仍能逼近 oracle verifier 对 candidate skills 的排序，并给 curator 提供可用的监督反馈。

因此，parameter-free TextGrad 的核心优化对象不是模型权重，而是：

```text
C_text: curator 的文本 policy / template / meta-memory
R_text: reward 的文本 rubric / comparator / calibration / meta-memory
S_x: evaluation task 的五阶段 skill state
```

训练完成后，`C_text` 和 `R_text` 固定为 `C*`、`R*`；下游 evaluation 中只继续优化 `S_x`，即每个具体 task 的 reproduce/localize/edit/validate/recover 五阶段 skills。

## 13. 工程含义

这套 framing 会改变当前 SKILLS_EVO 的优先级。

第一优先级不是马上追求更多 skill 文件，而是建立训练闭环：

- 生成多个 candidate skill operations。
- 记录 label-free reward 判断。
- 用真实 verifier outcome 校准 reward agent。
- 用 reward 和 oracle 的差异产生 TextGrad feedback。
- 更新 curator/reward 的文本 policy。

第二优先级才是在 evaluation task 内使用冻结后的 `C*` 和 `R*` 做 skill customization。

这样可以同时吸收三篇论文的优势：

- SkillRL 的 training/evolution 思想。
- SkillOS 的 curator RL 和 grouped tasks。
- SkillOpt 的 parameter-free text optimization、validation gate、rejected buffer。

最终系统不再是“trace summarizer”，而是：

```text
训练时学习 curator/reward 文本策略；
评测时用冻结 curator/reward 优化每个 task 的五阶段 skills。
```
