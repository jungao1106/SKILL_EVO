# SWEGym -> 可迁移 SWE Skills / Skill Policy 的相关工作与方案定位

## 0. 结论

`papers/` 里有不少相近思路，但没有一篇完整覆盖我们想要的设定：

```text
在 SWEGym 上训练 / 演化
-> 得到可迁移产物
   1. 分离的 Skill Writer policy 和 Skill Evaluator policy
   2. repo-level skills
   3. SWE-like failure-mode skills
-> 在 SWE-Bench Verified / SWE-Bench Pro / Terminal-Bench 2.0 上提升 performance 和效率
```

也就是说，相关工作可以支撑我们的设计，但没有直接解决我们的 SWE 场景。这个 gap 是可以写成论文贡献的。

最接近的证据链是：

| 我们需要的组件 | 最相关论文 | 能支撑什么 | 还缺什么 |
| --- | --- | --- | --- |
| skill 作为可迁移外部产物 | Trace2Skill, SkillOpt, optimize_anything, AutoSkill, MUSE-Autoskill | 轨迹可以蒸馏成 portable textual skills，且能跨模型 / 任务迁移 | 大多不是 SWE-Bench / SWEGym |
| 两个 policy 分别学会写 skill / 评价 skill | Evolving-RL, SkillOS, SkillOpt, GEPA / optimize_anything | skill extractor / curator / optimizer 可以由下游 reward 训练或搜索 | SWE 场景下还没有系统验证；测试阶段不能再用 benchmark verifier 更新 |
| failure-mode skills | SkillRL, Trace2Skill, SkillOpt, SkillOS | 失败轨迹能被抽象成 failure-aware rules / SOP / corrective edits | 没有形成 SWE-specific failure taxonomy |
| SWE / terminal 场景 | optimize_anything coding-agent skills, EvoArena / SWE-Chain-Evo / Terminal-Bench-Evo, SkillOpt Codex / Claude Code harness | 代码 agent skills、软件演化、terminal workflow memory 都有相关证据 | 没有 SWEGym -> SWE-Bench 的 transfer protocol |
| 训练阶段 verifier-supervised selection，测试阶段 evaluator-gated update | Generalization Gap, SkillOpt, optimize_anything | 自演化如果只靠内部判断容易过拟合；训练阶段需要 executable verifier 学出 evaluator | SWEGym verifier 只作为训练监督；测试阶段只能用冻结的 `pi_e` |

## 1. 哪些工作最像我们的想法

### 1.1 Evolving-RL：最像“训练写 skill / 用 skill 的 policy”

Evolving-RL 把经验实例化成 textual skills，并让同一个 policy 同时扮演 extractor 和 solver：

```text
source rollout
-> extractor 生成 N 个 candidate skills
-> 每个 skill 在 K 个 related downstream tasks 上评估
-> downstream reward 反过来训练 extractor
-> skill-conditioned trajectories 训练 solver
```

这非常接近我们想要的：

```text
SWEGym source task trajectory
-> 生成 repo / failure-mode candidate skills
-> 在 held-out SWEGym related tasks 上评估
-> reward 训练或改进 skill writer / evaluator
-> 最终 skill repo 迁移到 SWE-Bench Verified
```

区别是 Evolving-RL 的实验在 ALFWorld / Mind2Web，不是 SWE。我们的 novelty 可以是把这个 evaluation-centric skill evolution 落到 SWE verifier 场景，并显式引入 repo skills 和 failure-mode skills。

### 1.2 SkillOS：最像“训练 Skill Curator 管理 SkillRepo”

SkillOS 把 frozen executor 和 trainable skill curator 解耦：

```text
frozen executor: 用 SkillRepo 解决任务
trainable curator: insert / update / delete skills
reward: later related tasks 的 downstream performance + skill quality + compactness
```

这和我们的方法非常契合：

```text
executor = GLM-5.1 / Pi Coding Agent
curator = skill writer / editor / evaluator
SkillRepo = repo skills + failure-mode skills
training groups = SWEGym 中同 repo / 同 failure mode 的 task streams
reward = SWE verifier resolve + efficiency + no regression + compactness
```

SkillOS 还能支撑一个重要论点：我们不一定要微调 PiAgent 本身，可以训练或优化外部 curator，让它写出适配 PiAgent 的 skills。

### 1.3 optimize_anything：最接近 coding-agent skill transfer

`optimize_anything` 里有 coding-agent skills 的实验：它优化 Bleve repo 的自然语言 instructions / best practices，评价器运行 coding agent，看是否能 resolve repository tasks，并把 agent trace、test outcome、resolution time 作为 side information。

这和我们最接近，因为它确实是 coding-agent skills：

```text
candidate skill
-> coding agent solves repo tasks
-> evaluator returns score + traces + errors + tests + time
-> optimizer revises skill
-> optimized skills generalize to unseen tasks and transfer across models
```

但它不是 SWE-Bench / SWEGym。我们的区别是：

- benchmark 更标准：SWE-Bench Verified / SWE-Bench Pro；
- training source 更明确：SWEGym；
- skill 层次更明确：task -> repo -> failure-mode；
- evaluation protocol 更严格：no-exact transfer，避免 task leakage。

### 1.4 SkillOpt：最像“把 skill 当作外部参数来训练”

SkillOpt 的核心是把 skill document 当成 frozen agent 的 external state：

```text
rollout batch
-> failure / success minibatch reflection
-> bounded add / delete / replace edits
-> held-out validation gate
-> rejected-edit buffer
-> slow / meta update
-> exported best_skill.md
```

这对我们的实验设计非常关键。它直接说明：

- skill 不应该 one-shot 生成后直接用；
- 每次 skill update 要有 bounded edit；
- 每个 candidate skill 要过 held-out gate；
- rejected edits 应该作为负反馈；
- optimizer-side meta skill 和 deployed skill 要分离。

我们的现有结果里，v0001 one-shot skills 从 370/500 掉到 356/500，正好能作为证据说明：SWE skills 需要 validation-gated evolution，而不是一次性轨迹总结。

### 1.5 Trace2Skill：最像“从大量轨迹蒸馏 portable skills”

Trace2Skill 的重要点不是 RL，而是 many-to-one consolidation：

```text
collect many success / failure trajectories
-> per-trajectory patches
-> hierarchical merge
-> one portable skill directory
```

它支持我们把 task skills 当成原料，而不是最终贡献：

```text
task skills = trajectory-local raw material
repo skills / failure-mode skills = consolidated transferable artifacts
```

这也能帮助回答“task-specific skills 和通用可迁移 skills 是否冲突”：不冲突，只要我们把 task skills 定位为 evolution raw material，而不是主张它们本身可迁移。

### 1.6 SkillRL：支持 general skills + task-specific skills + failure lessons

SkillRL 把 skill bank 分成 general skills 和 task-specific skills，并在 RL 过程中递归更新 skill bank。它说明层次化 skill library 是合理的。

对应到 SWE：

```text
general skills       -> SWE-like failure-mode skills
task-specific skills -> repo / task / stage skills
recursive evolution  -> SWEGym validation failures 驱动更新
```

我们和 SkillRL 的区别是：SWE 的 task-specific 不应该直接按 benchmark task 记忆，而应向 repo 和 failure-mode 两层晋升。

### 1.7 EvoArena：最接近 SWE / Terminal 的动态环境，但不是 skill policy

EvoArena 包含 Terminal-Bench-Evo 和 SWE-Chain-Evo。它证明 SWE / terminal agent 的真实困难之一是环境、repo、依赖、接口、测试规则会演化。EvoMem 使用 patch-based memory 支持 agents 在 SWE-Chain-Evo / Terminal-Bench-Evo 上更鲁棒。

它和我们的关系：

- 支持 repo evolution / terminal workflow 是合理场景；
- 支持 patch memory / versioned memory 在 SWE 中有价值；
- 但它没有训练 skill writer / evaluator，也没有把 skills 分成 task / repo / failure-mode。

所以它更适合作为 SWE 场景动机，而不是直接 baseline。

## 2. 我们的方法应该怎么重新定位

### 2.1 核心主张

建议把论文主张从：

```text
我们为每个 task 生成定制 skills，所以提升 SWE-Bench 表现
```

改成：

```text
SWE agent 的经验可以被演化成多层可迁移 skill state。
我们在 SWEGym 上使用 executable verifier 训练 / 优化 skill writing and selection policy，
把 task-local trajectories 蒸馏成 repo-level skills 和 cross-repo failure-mode skills，
并在 SWE-Bench Verified 等 held-out SWE benchmarks 上验证这些外部技能产物的迁移收益。
```

这样能避开两个主要风险：

- task-specific memory 被审稿人认为是 leakage 或 retry；
- skill-assisted gain 被认为只是“第二次机会”，不是可迁移能力。

### 2.2 Skill 的三层结构

最终应该把 skill stack 明确成：

```text
Task skills
  - 单个 SWEGym / SWE-Bench task 的轨迹经验
  - 只作为 raw material 或 exact-task upper bound

Repo skills
  - 同一 repo 中可复用的定位、测试、模块边界、API 约束
  - 允许迁移到同 repo 新任务

Failure-mode skills
  - 跨 repo 的 SWE agent 行为修正策略
  - 处理 no_diff / localization_drift / bad_validation / timeout 等通用失败模式

Skill Writer policy: pi_w
  - 决定写什么 skill、怎么改 skill、如何从 task evidence 抽象 repo/failure-mode candidate。

Skill Evaluator policy: pi_e
  - 决定 candidate skill 是否可用、是否过拟合、是否进入 SkillRepo。
  - 测试阶段只允许 pi_e 门控，不能用 benchmark verifier。
```

### 2.3 task-specific skills 和 transferable skills 是否冲突

不冲突，但必须换说法。

错误说法：

```text
每个 task 都有模型定制 skills，所以我们的 skills 是通用可迁移的。
```

正确说法：

```text
task skills 是最低层经验单元，类似 trajectory patches。
它们本身不作为 transferable claim。
transferable claim 来自两类晋升产物：
1. repo skills：由同 repo 多个 task skills 聚合而来；
2. failure-mode skills：由跨 repo 重复失败模式聚合而来。
```

实验中要把 exact-task mode 单独标成 upper bound / adaptation setting。主论文要重点报告 no-exact transfer。

## 3. 推荐的 SWEGym evolution 方案

### 3.0 训练产物和推理时产物的边界

按照新的设定，训练阶段结束后只保留三类冻结产物：

```text
1. Skill Writer policy: pi_w
   - 负责从 trajectory / repo history / failure signals 中生成 candidate skills。
   - 输出 task-level stage skills、repo skill candidates、failure-mode skill candidates 的草案。

2. Skill Evaluator policy: pi_e
   - 负责评价 candidate skills 是否可用、是否过拟合、trigger 是否可靠、风险是否可控。
   - 在测试阶段作为唯一门控；不能调用或读取 benchmark verifier。

3. SWE general skills / failure-mode skills
   - 跨 repo 的通用 SWE agent 行为规则。
   - 例如 no_diff、localization_drift、bad_validation、timeout、overbroad_edit 等。
```

训练阶段来自 SWEGym 的下面两类产物不进入最终推理初始状态：

```text
drop SWEGym task-level stage skills
drop SWEGym repo skills
```

这样可以避免一个关键审稿风险：模型在 SWEGym 上学到的 repo-specific/task-specific hints 被误认为直接迁移到 SWE-Bench test repos。最终对外声称的可迁移对象应是：

```text
policy to write skills: pi_w
policy to evaluate skills: pi_e
+ general SWE failure-mode skills
```

而不是 SWEGym 里具体 task 或 repo 的记忆。

信息边界要明确：

| 阶段 | Writer `pi_w` | Evaluator `pi_e` | verifier 使用 |
| --- | --- | --- | --- |
| SWEGym training | 学习如何写 candidate skills | 学习如何判断 skill utility / risk / leakage | 可以使用 SWEGym verifier 作为监督 |
| Test-time inference | 冻结，只生成 candidate skills | 冻结，只基于轨迹证据评价 candidate skills | 不允许使用 official verifier / resolved label 更新 |
| Final reporting | 不更新 | 不更新 | verifier 只用于离线报告最终分数 |

### 3.0.1 层级化、多时间尺度更新与自底向上传递

方法中必须明确：skill evolution 不是把所有 skill 和 policy 同频率重写。越靠近单个 task 的对象更新越快；越通用、越可能影响未来分布的对象更新越慢；Writer / Evaluator policy 只在训练阶段用 verifier 校准，测试阶段冻结。

更严格地说，skill 不是并列生成，而是逐层向上传递：

```text
rollout trace / failure trace
  -> task-level stage skill candidates, conditioned on one task trace
  -> task skill state, filtered by pi_e
  -> repo skill candidates, conditioned on multiple accepted task skills from the same repo
  -> repo skill state, filtered by pi_e and held-out same-repo evidence
  -> failure-mode skill candidates, conditioned on repeated repo-level patterns across repos
  -> failure-mode skill state, filtered by pi_e and cross-repo validation
  -> writer/evaluator policy updates, conditioned only on aggregated accepted/rejected histories
```

因此，repo skill 不能直接从单个 trace 生成；failure-mode skill 不能直接从单个 task 生成；Writer / Evaluator policy 不能因为一个 case 就更新。每一层只能读取下一层已经筛选过的对象，外加必要的聚合统计。

推荐把可演化对象组织成下面的层级：

| 层级 | 对象 | 更新速度 | 测试阶段是否更新 | 更新依据 |
| --- | --- | --- | --- | --- |
| L0 | rollout trace / failure trace | 每次 attempt | 是 | agent 轨迹、工具调用、公开测试输出 |
| L1 | task-level stage skills | 最快，每个 task / attempt | 是，但只能作为局部 evidence 或后续聚合原料 | frozen `pi_e` 的 label-free 评价 |
| L2 | repo skills | 中速，每个 repo batch | 是，但只在 test stream 的同 repo 历史内因果更新 | 多个同 repo task skills 的一致证据 |
| L3 | failure-mode skills | 慢速，每 `failure_mode_update_every_repo_updates` 次 repo-level update 触发 | 主结果中冻结；online setting 中最多临时缓存、不更新全局库 | 跨 repo 重复失败模式 |
| L4 | Skill Writer policy `pi_w` | 很慢，每 `policy_update_every_failure_mode_triggers` 次 failure-mode trigger | 否 | SWEGym verifier + evaluator feedback |
| L5 | Skill Evaluator policy `pi_e` | 很慢，每 `policy_update_every_failure_mode_triggers` 次 failure-mode trigger | 否 | label-free judgment 与 SWEGym verifier utility 的校准 |
| L6 | harness policy | 固定 | 否 | 人工定义的 case label / edit budget |

这种层级设计解决两个问题。第一，task skills 可以快速适应当前任务，但不会直接变成 transfer claim。第二，repo / failure-mode skills 和 `pi_w` / `pi_e` 需要更强证据门槛，避免 skill drift、prompt drift 和 harmful skill 进入全局记忆。

每个 skill / policy state 应至少记录下面的 metadata：

```json
{
  "level": "task | repo | failure_mode | writer_policy | evaluator_policy",
  "scope_key": "task_id | repo_slug | global",
  "version": "v0016",
  "support_count": 0,
  "positive_count": 0,
  "negative_count": 0,
  "ema_delta": 0.0,
  "confidence": 0.0,
  "status": "staging | active | frozen | rejected",
  "updated_at_step": 0
}
```

更新规则可以写成：

- **Trace -> Task skills**：每个 rollout 后由 `pi_w` 生成 task-level stage skill candidates。这里的条件只能是单个 task 的 trace、diff、公开测试输出、失败信号和 verifier reward。`pi_e` 负责筛选 candidate。Task skills 更新最快，但只作为当前 task 的局部适应和上层聚合原料，不直接构成 transfer claim。
- **Task skills -> Repo skills**：repo skill 只能从同 repo 多个 accepted task skills 中聚合，不能直接读取单条 raw trace 生成。它应该抽象同 repo 的定位入口、测试入口、模块边界、常见验证方式和失败恢复规则。晋升门槛建议包括 `support_count >= 5`、覆盖至少 3 个不同 task、负例率低于阈值、trigger 不是 exact task id 或 exact patch。
- **Repo skills -> Failure-mode skills**：failure-mode skill 只能从多个 repo skill / repo-level failure summaries 中归纳，不能直接从单个 task 或单个 repo 生成。它应表示跨 repo 的 agent 控制策略，例如 no-diff、localization drift、bad validation、timeout、overbroad edit。晋升门槛应包括跨 repo 支持数、失败签名一致性、validation 上无明显 regression。
- **Skill histories -> Writer policy `pi_w`**：`pi_w` 从空文档开始，只在训练 epoch 末更新。它不能记具体 task patch，而是学习如何把下层 evidence 写成更好的 trigger / action / evidence / stop condition，以及如何避免过拟合。
- **Skill histories -> Evaluator policy `pi_e`**：`pi_e` 从空文档开始，只在训练 epoch 末更新。它学习 label-free 风险判断、accept / reject / request-rewrite 阈值，并用 SWEGym verifier 事后校准 false accept / false reject。测试时 `pi_e` 冻结，作为 verifier-free gate。
- **Harness policy**：不学习，预先固定 case labels、更新步长、晋升门槛和膨胀预算。它是优化过程的边界条件，不是 TextGrad / RL 的目标。

为了避免 skills 和 policy 膨胀，每层都需要硬预算：

| 层级 | 单次更新预算 | 总量预算 | 删除 / 压缩规则 |
| --- | --- | --- | --- |
| Task skills | 每个 task 最多 1-3 条 stage edits | 每个 task 最多 5 个 stage skill slots | 重复、无 reward 证据、无 stop condition 的 skill 进入 inactive |
| Repo skills | 每个 repo batch 最多 1 条新 skill 或 2 条 rewrite | 每个 repo 每个 stage 最多 3 条 active skills | 低支持数、trigger 冲突、validation 无收益则合并或降级 |
| Failure-mode skills | 每个 failure-mode trigger 最多 1-2 条新 skill；trigger 由 repo-level update 计数触发 | 每个 failure mode 每个 stage 最多 2 条 active skills | 跨 repo 负例升高则禁用或改为 diagnostic |
| Writer policy | 每个 epoch 最多 3 条 policy bullet 更新 | policy 文档不超过 2 页 | 删除过时规则，优先改写而不是追加 |
| Evaluator policy | 每个 epoch 最多 3 条 rubric 更新 | policy 文档不超过 2 页 | false accept 相关规则优先，重复规则合并 |

对应的训练 schedule 是：

```text
For each training task:
  run PiAgent rollout
  collect trace / diff / tests / public logs / verifier reward
  pi_w drafts task-level stage skills
  pi_e scores candidates with label-free evidence
  store accepted task skills in staging buffer

Every repo batch:
  cluster accepted task skills by repo
  pi_w drafts repo skill candidates
  pi_e gates repo candidates
  promote only if evidence threshold passes

Every failure_mode_update_every_repo_updates repo-level updates:
  cluster repeated failure signatures from recent repo-level summaries
  pi_w drafts failure-mode candidates only from cross-repo evidence
  pi_e gates failure-mode candidates
  promote only if cross-repo evidence threshold passes

End of training epoch:
  calibrate pi_e with verifier-known utility
  update pi_w from accepted / rejected skill histories
  run repo-isolated validation
  freeze the next SkillRepo / policy version only if validation does not regress

Test-time:
  freeze pi_w, pi_e, and training-derived general skills
  generate only test-local task skills and test-stream repo skills
  never use test verifier for updating
```

因此，本文的方法不是“生成更多 skills”，而是 **hierarchical evidence-gated skill evolution**：task-level skills 快速吸收单任务经验，repo skills 中速沉淀同仓库规律，failure-mode skills 慢速沉淀跨仓库 agent 控制策略，而 Writer / Evaluator policy 只在 verifier-supervised training 中低频校准并在测试时冻结。

### 3.0.2 Fixed Harness Policy: case labels, update clocks, and step sizes

All learned policies start from empty documents, but they are not allowed to define their own reward labels, promotion thresholds, or update intensity. The harness policy is fixed by the experimenter and is not learned. Its role is to map verifier transitions and runtime diagnostics into update labels, step sizes, promotion gates, and anti-inflation budgets.

This preserves the main reason why work-on-test / retry can be effective: paired `previous -> current` attempts on the same task provide high-signal causal feedback. The method still prevents this local feedback from becoming a global rule directly. Evidence must move upward through task skills, repo-level batches, and cross-repo failure-mode gates.

Fixed case labels:

| Case | Definition | Meaning | Primary use |
| --- | --- | --- | --- |
| Strong Positive | Previous attempt failed and current attempt passed; `0 -> 1` | The current skill/update is likely causally helpful. | Keep, compress, and pass upward as evidence. |
| Weak Positive | Previous attempt passed and current attempt still passed; `1 -> 1`, especially if faster, shorter, or more stable | The update did not break success and may improve efficiency or stability. | Slightly increase confidence; use as a stable anchor. |
| Strong Negative | Previous attempt passed and current attempt failed; `1 -> 0` | The current skill/update is likely harmful. | Disable, roll back, rewrite, and penalize false accept. |
| Weak Negative | Previous attempt failed and current attempt still failed; `0 -> 0` | No visible utility yet, but not necessarily harmful. | Small rewrite or keep inactive. |
| Diagnostic | Missing reward, timeout, setup error, verifier error, upload error, or sandbox/runtime blocker | Runtime risk should not pollute semantic skills. | Update recover/validate safeguards and runtime risk judgment only. |

Cold-start training has no paired previous attempt. In that case, solved traces map to Weak Positive and unsolved traces map to Weak Negative unless a runtime diagnostic is present.

These labels determine update step sizes by level. Lower levels can move faster; higher levels are deliberately conservative:

| Case | Task skill update | Repo skill update | Failure-mode update | `pi_w` update | `pi_e` update |
| --- | --- | --- | --- | --- | --- |
| Strong Positive | Insert or rewrite at most 1-3 stage skills. | Add support only; promote after accumulated batch evidence passes. | Add pattern evidence only after repo-level confirmation. | After a policy window of failure-mode triggers, add or rewrite at most 1 positive writing rule. | After a policy window of failure-mode triggers, add or rewrite at most 1 positive evaluation rule. |
| Weak Positive | Compress or raise confidence only. | Add stability evidence only. | Usually no semantic update. | Usually no update. | Use as a false-reject calibration anchor. |
| Strong Negative | Disable or rewrite at most 2 related task skills. | Reduce support or mark review if a repo skill fired. | Add negative evidence; do not create a new global rule. | After a policy window of failure-mode triggers, add at most 1 avoid rule. | After a policy window of failure-mode triggers, prioritize false-accept penalties. |
| Weak Negative | Rewrite at most 1 stage skill or keep inactive. | Do not promote; record neutral/negative evidence. | Do not promote. | No update unless repeated failures aggregate. | May update abstain/request-rewrite conditions. |
| Diagnostic | Add at most 1 recover/validate guard. | No semantic repo update. | Update runtime/failure taxonomy only if repeated across repos. | No semantic writing update. | Update runtime risk judgment. |

Default training hyperparameters:

```json
{
  "repo_update_batch_size": 5,
  "failure_mode_update_every_repo_updates": 4,
  "failure_mode_min_repo_support": 3,
  "max_task_skill_edits_per_task": 3,
  "max_repo_skill_updates_per_batch": 2,
  "max_failure_mode_updates_per_trigger": 2,
  "policy_max_bullet_updates": 3
}
```

Fixed budgets:

```text
Task layer:
  strong_positive: insert/update <= 3 stage skills
  weak_positive: compress/confidence only
  strong_negative: disable/rewrite <= 2 stage skills
  weak_negative: rewrite <= 1 stage skill or inactive
  diagnostic: add <= 1 recover/validate guard

Repo layer:
  update clock: every repo_update_batch_size task-level events from the same repo
  strong_positive: +1 support; promote only if accumulated support threshold passes
  weak_positive: +0.25 stability support
  strong_negative: -2 support or mark review
  weak_negative: +0 neutral/negative evidence
  diagnostic: no semantic update

Failure-mode layer:
  update clock: every failure_mode_update_every_repo_updates repo-level updates
  strong_positive: +1 pattern support only after repo-level confirmation
  weak_positive: +0.25 stability support
  strong_negative: add negative evidence; do not create a new global rule
  weak_negative: no promotion
  diagnostic: update runtime/failure taxonomy only if repeated across repos
  promotion gate: at least failure_mode_min_repo_support distinct repos

Policy layer:
  pi_w / pi_e update only after a fixed number of failure-mode trigger windows
  max 3 bullet edits per policy update window
  prefer rewriting existing rules over appending new ones
  no task id, exact patch, private path, or hidden verifier detail
```

The important design choice is that repo skills do not wait until every task from a repo has finished. They update in small same-repo batches. Failure-mode skills also do not update by epoch; they update after a fixed number of repo-level updates, which bounds context size and prevents a single large epoch summary from dominating the global failure taxonomy.

建议训练产物里显式保存：

```text
training/harness_policy.md
training/generator_policy.md
training/evaluator_policy.md
training/skill_update_events.jsonl
training/promotion_decisions.jsonl
training/policy_state.json
```

其中 `harness_policy.md` 固定不学习；`generator_policy.md` 和 `evaluator_policy.md` 从空文档开始，只根据 failure-mode trigger windows 小步更新。

### 3.1 数据划分

建议使用三个集合：

```text
Evolution train: SWEGym train
Selection / validation: SWEGym held-out 或 train 内按 repo / failure mode 留出的 validation tasks
Final test: SWE-Bench Verified full set
Optional OOD: SWE-Bench Pro, Terminal-Bench 2.0
```

关键约束：

- final test 不允许使用 exact test-task skill；
- final test task 的 trajectory 不得进入 skill generation；
- repo skills 可以来自相同 repo 的 training tasks，但必须不包含 exact patch / exact task id；
- failure-mode skills 必须跨 repo 生成，不能绑定某个文件路径或 gold patch。

### 3.2 Evolution loop

推荐主循环：

```text
1. Run no-skill PiAgent on SWEGym train tasks.
2. Collect trajectories, diffs, verifier reports, tool traces, test logs, timeout/no-diff signals.
3. Classify each trajectory by stage and failure mode, then assign fixed harness case labels from previous/current verifier transitions.
4. Trace-to-task update:
   - pi_w drafts task-level stage skills conditioned only on one task trace;
   - pi_e accepts / rejects / requests rewrite with label-free evidence;
   - harness policy decides update step size from strong/weak positive/negative labels.
5. Task-to-repo update:
   - cluster accepted task skills within the same repo;
   - pi_w drafts repo candidates only from accepted task skills and repo summaries;
   - pi_e promotes only if same-repo evidence threshold passes.
6. Repo-to-failure-mode update:
   - cluster repeated repo-level failure patterns across repos;
   - pi_w drafts failure-mode candidates only from cross-repo summaries;
   - pi_e promotes only if cross-repo support and validation constraints pass.
7. Evaluate candidate skills on related held-out SWEGym tasks with verifier supervision:
   - no exact task memory;
   - same PiAgent / GLM-5.1;
   - same timeout / verifier.
8. Accept a skill only if it improves held-out utility:
   reward = resolve_delta + efficiency_gain - regression_penalty - token_cost_penalty.
9. Skill Evaluator pi_e learns from accepted / rejected candidates:
   - downstream utility;
   - trigger correctness;
   - leakage or over-specificity risk;
   - harmful-skill probability.
10. Apply hierarchical update clocks:
   - update task skills per rollout;
   - promote repo skills only after same-repo evidence threshold;
   - promote failure-mode skills only after cross-repo evidence threshold;
   - update pi_w / pi_e only after a fixed number of failure-mode triggers.
11. Update SkillRepo, rejected-edit buffer, and policy calibration logs.
12. Repeat until validation gain saturates.
13. Freeze pi_w, pi_e, and SWE general skills before evaluating on SWE-Bench Verified.
```

### 3.3 推理阶段的 test-time evolution

如果推理阶段还要针对 test set 继续 evolution，建议把它定义成一个单独的 **online test-time evolution** protocol，而不是混在 frozen transfer 主结果里。

推理初始状态：

```text
frozen Skill Writer pi_w
frozen Skill Evaluator pi_e
frozen SWE general skills / failure-mode skills
empty test-time task skill memory
empty test-time repo skill memory
```

然后按一个固定顺序处理 test tasks：

```text
for task_i in test_stream:
    1. Retrieve only:
       - frozen general / failure-mode skills
       - repo skills generated from previous test tasks in the same repo
       - task skills from previous test tasks, only as raw evidence for aggregation

    2. Run PiAgent on task_i.

    3. After task_i finishes, collect:
       - trajectory
       - diff
       - agent-visible tool outputs and test outputs, if the agent itself ran them
       - runtime signals: no_diff, timeout, repeated_search, bad_validation, etc.
       - no official benchmark verifier result

    4. Use frozen Skill Writer pi_w to generate:
       - task-level stage skills for task_i
       - candidate repo skills if enough previous same-repo evidence exists

    5. Update test-time memory:
       - task skills are stored as trajectory-local evidence
       - repo skills are accepted only if frozen Skill Evaluator pi_e approves
```

关键限制：

- `task_i` 生成的 task-level skill 不能在同一次 first-attempt evaluation 中帮助 `task_i`；
- `task_i` 的 task-level skill 可以帮助后续同 repo / 同 failure-mode 的 tasks；
- repo skill 只能由已经处理过的 test tasks 聚合，不能使用未来 task；
- 如果允许对同一个 task retry，需要单独报告为 `test-time adaptation / retry`，并配 same-budget retry baseline；
- 不能使用 gold patch、隐藏答案、official verifier、resolved label、未来任务结果；
- test-time skill 是否保留、合并、晋升，只能依赖冻结的 Skill Evaluator `pi_e` 和 agent 可见轨迹证据。

因此推荐报告两条曲线：

| Protocol | 初始 skills | 测试中是否更新 task/repo skills | 能 claim 什么 |
| --- | --- | --- | --- |
| Frozen Transfer | frozen `pi_w` + frozen `pi_e` + frozen general skills | 否 | SWEGym 训练出的 skill writer/evaluator 和 general skills 可迁移 |
| Online Test-Time Evolution | frozen `pi_w` + frozen `pi_e` + frozen general skills | 是，因果更新；无 test verifier 更新 | writer/evaluator 能在新 benchmark 上继续快速构建 task/repo memory |

### 3.4 test-time task skills 和 repo skills 的职责

推理阶段生成的 test-time task-level stage skills 不应该作为最终直接注入对象无限增长。它们更适合作为 repo skill 的原料：

```text
test task trajectory
-> task-level stage skills
-> cluster by repo/stage/path/failure signal
-> generate candidate repo skill
-> frozen Skill Evaluator pi_e scores quality / risk / trigger
-> accept / reject / rewrite
```

test-time repo skills 则可以作为真正参与后续推理的 memory：

```text
repo skill = 当前 test benchmark 中同 repo 已处理任务的可复用程序性知识
```

它的触发条件必须是当前任务可观察证据，而不是 task id：

```text
allowed:
  "If this Django task touches migration autodetector logic and tests mention field deconstruction..."

not allowed:
  "For django__django-xxxxx, edit file y with patch shape z."
```

### 3.5 Online test-time evolution 的评价方式

因为 online evolution 对 test set 有顺序敏感性，必须固定并报告顺序策略：

```text
default: benchmark original order
robustness: 3 random orders
optional: grouped-by-repo order as an upper bound
```

测试阶段的更新判据必须固定为：

```text
candidate skill accepted iff frozen Skill Evaluator pi_e approves it.
No official SWE-Bench verifier result, resolved label, hidden test result,
or later-task success/failure may be used for memory updates.
```

benchmark verifier 只能在整个推理协议结束后离线运行，用于报告最终 resolved count。

主表建议这样拆：

| Setting | 说明 |
| --- | --- |
| No Skills | 无 skill，一次通过 |
| Frozen General Skills | 只用训练后冻结的 SWE general/failure-mode skills |
| Frozen Writer/Evaluator + Online Task Skills | 测试中由 `pi_w` 生成 task-level stage skills，由 `pi_e` 评价，只用于后续聚合 |
| Frozen Writer/Evaluator + Online Repo Skills | 测试中由历史 task skills 晋升 repo skills，由 `pi_e` 门控 |
| Frozen Writer/Evaluator + Online Task+Repo Skills | 完整 online evolution；不使用 test verifier 更新 |
| Same-budget Online Retry | 同样多的测试时机会，但不写结构化 skills |

最终写法要区分：

```text
Frozen Transfer answers:
Can SWEGym train transferable general skills plus a Skill Writer / Skill Evaluator pair?

Online Test-Time Evolution answers:
Can the learned writer and evaluator rapidly build new task/repo skills on a new SWE benchmark stream without verifier feedback?
```

### 3.6 如果真的要叫 RL

如果没有更新模型参数，建议不要强称 RL，而叫：

```text
verifier-grounded text-space optimization
LLM-as-optimizer for skill evolution
```

如果要做 RL，建议把 writer 和 evaluator 拆成两个 policy，而不是一个 curator：

```text
Writer pi_w state:
  current SkillRepo + SWEGym task group trajectories + verifier feedback

Writer pi_w action:
  draft_task_skill / draft_repo_skill / draft_failure_skill / rewrite_skill

Evaluator pi_e state:
  candidate skill + source evidence + current SkillRepo + verifier-labeled outcomes from SWEGym

Evaluator pi_e action:
  accept / reject / request_rewrite / assign_risk / assign_trigger_score

Training reward:
  for pi_w: downstream utility of skills approved by pi_e
  for pi_e: agreement with SWEGym verifier-grounded utility, false-positive penalty, false-negative penalty

SkillRepo operation:
  insert_skill / update_skill / delete_skill / merge_skill / split_skill / rewrite_trigger

Reward features:
  downstream resolved count on SWEGym validation tasks
  + efficiency improvement
  + content quality
  + compactness
  - regression
  - leakage / over-specificity penalty

optimizer:
  GRPO / DPO over writer/evaluator outputs

executor:
  frozen GLM-5.1 PiAgent
```

这样就和 SkillOS / Evolving-RL 对齐：训练的是写 skill 的 policy 和评价 skill 的 policy，而不是 PiAgent 的 coding policy。

注意：verifier feedback 只存在于 SWEGym training。测试阶段 `pi_w` 和 `pi_e` 参数冻结，不能继续用 SWE-Bench verifier 做 reward。

## 4. Failure-mode skills 应该怎么设计

推荐先固定 8 类 SWE-like failure modes：

| Failure mode | 触发信号 | Skill 目标 |
| --- | --- | --- |
| `no_diff` | agent 结束但 repo 没有 diff | 阻止空提交，强制形成最小持久源码修改 |
| `localization_drift` | 反复 search/read 但没有 owner hypothesis | 收敛到 owner module 和证据链 |
| `wrong_owner_file` | patch 落在无关 wrapper / test helper | 回到 failing behavior 的真实实现路径 |
| `bad_validation` | 未跑 focused tests，或测试命令无效 | 建立最小验证闭环 |
| `repeated_search` | 重复 grep 同一关键词/文件 | 改变 query strategy，转向 symbols/tests |
| `timeout` | 工具调用过多，测试过宽 | 限制搜索宽度，优先 focused test / diff |
| `overbroad_edit` | 大范围重构、无关格式化 | 收敛到 minimal patch |
| `patch_regression` | PASS_TO_PASS 失败 | 加 regression guard 和 rollback rule |

Failure-mode skill 的格式应该是：

```text
Trigger -> Evidence to collect -> Corrective actions -> Stop condition -> Anti-patterns
```

它不应该写成：

```text
在 django 的某个文件里这样改
```

而应该写成：

```text
当当前轨迹出现 no_diff 时，不要结束。先陈述 owner hypothesis，
打开最可能的源码文件，做最小持久修改，然后运行一个 focused verifier。
如果仍不能定位，输出当前阻塞原因，而不是提交空结果。
```

## 5. 实验设计

### 5.1 主实验

主实验建议分成两组，不要混在同一 claim 里。

第一组是 **Frozen Transfer**，训练结束后不再更新 test-time task/repo memory：

| Setting | 训练数据 | 测试数据 | 允许 exact task skill | 目的 |
| --- | --- | --- | --- | --- |
| No Skills | 无 | SWE-Bench Verified | 否 | 基线 |
| Public SWE Skills | 公共 SWE skills | SWE-Bench Verified | 否 | 静态公共 skill baseline |
| Raw Trace Retrieval | SWEGym train traces | SWE-Bench Verified | 否 | 证明不是简单检索轨迹 |
| One-shot Trace2Skill / StageSkill | SWEGym train | SWE-Bench Verified | 否 | 证明 one-shot 蒸馏不足 |
| SkillOpt-style Global SWE Skill | SWEGym train/val | SWE-Bench Verified | 否 | 单一全局 skill optimizer baseline |
| Frozen General Skills | SWEGym train/val | SWE-Bench Verified | 否 | 训练后冻结的 SWE general / failure-mode skills |
| Frozen Writer/Evaluator + Frozen General Skills | SWEGym train/val | SWE-Bench Verified | 否 | 证明 skill writer/evaluator 和 general skills 本身可迁移 |

第二组是 **Online Test-Time Evolution**，训练产物仍然冻结，但允许在 test stream 上因果地产生新的 task/repo skills：

| Setting | 初始状态 | test-time 更新 | 目的 |
| --- | --- | --- | --- |
| Online Task Skills Only | frozen `pi_w` + frozen `pi_e` + frozen general skills | 生成 task-level stage skills，但只帮助后续聚合 | 测试 learned writer/evaluator 能否抽取新 benchmark 经验 |
| Online Repo Skills Only | frozen `pi_w` + frozen `pi_e` + frozen general skills | 从已完成 test tasks 晋升 repo skills | 测试新 benchmark 内 same-repo adaptation |
| Online Task+Repo Skills | frozen `pi_w` + frozen `pi_e` + frozen general skills | task skills + repo skills 因果更新 | 完整 test-time evolution |
| Same-budget Online Retry | 不写 skills | 给同等 retry / compute | 排除只是多一次机会 |
| Grouped-by-Repo Upper Bound | frozen `pi_w` + frozen `pi_e` + frozen general skills | 按 repo 顺序加强历史复用 | 分析 online repo learning 的上限 |

另外可以保留一个非主 claim 的 upper bound：

| Setting | 训练数据 | 测试数据 | 允许 exact task skill | 目的 |
| --- | --- | --- | --- | --- |
| Exact Task Skills | SWE-Bench trajectories | SWE-Bench same tasks | 是 | upper bound / adaptation，不作为 transfer 主结果 |

### 5.2 当前已有结果如何放

当前已有结果应该作为 pilot / upper-bound evidence：

```text
No-Skills SWE-Bench Verified: 370/500 = 74.0%
v0001 one-shot StageSkill: 356/500 = 71.2%
v0015 failure-subset assisted aggregate: 394/500 = 78.8%
```

论文里要谨慎写：

- v0001 证明 naive one-shot skill 可能伤害 agent；
- v0015 证明 skill-assisted rerun 有潜力；
- 但 v0015 不是严格 no-exact transfer；
- 真正主结果应来自 SWEGym -> SWE-Bench Verified 的 no-exact evaluation。

### 5.3 指标

主指标：

```text
resolved count / resolve rate
```

效率指标：

```text
wall-clock time
tokens
tool calls
number of files read
number of tests run
time to first diff
repeated search count
timeout rate
no_diff rate
```

安全和泛化指标：

```text
regression count on no-skill solved tasks
PASS_TO_PASS failure rate
skill false-positive retrieval rate
accepted / rejected skill edits
skill prompt token cost
cross-repo gain for failure-mode skills
same-repo held-out gain for repo skills
```

## 6. Ablation 设计

至少做这些：

| Ablation | 删除什么 | 说明什么 |
| --- | --- | --- |
| w/o repo skills | 只用 failure-mode skills | repo-level knowledge 是否必要 |
| w/o failure-mode skills | 只用 repo skills | cross-repo control rules 是否必要 |
| w/o train verifier gate | 训练时 candidate skills 直接加入 | SWEGym verifier-supervised selection 是否避免 harmful skills |
| w/o test evaluator gate | 测试时 candidate skills 直接加入 | 冻结 `pi_e` 是否能替代 test verifier 做安全门控 |
| w/o side information | 只用 resolved score，不用 trace/test/failure label | diagnostic feedback 是否关键 |
| w/o rejected-edit buffer | 不记录失败 skill edits | 负反馈是否减少重复坏更新 |
| one-shot generation | 不做 iterative evolution | evolution 是否必要 |
| raw trajectory retrieval | 不蒸馏，只检索轨迹 | abstraction 是否优于记忆 |
| exact-task disabled | 禁用 task skills | 是否还有 transfer |
| static retrieval | 初始 top-k skills，不做 runtime trigger | failure-mode dynamic trigger 是否有用 |
| no efficiency reward | reward 只看 resolve | 是否会用更长轨迹换成功率 |

## 7. 最终 storyline

建议论文 storyline 写成：

```text
1. SWE coding agents are still episodic: each task leaves behind rich traces, but the agent does not learn stable reusable procedures.

2. Existing skill-evolution work shows that textual skills can be portable, and recent RL / optimizer work shows that skill writers and evaluators can be optimized by downstream reward. However, these ideas are mostly validated outside real SWE benchmarks, or only as repo-local coding-agent skills.

3. SWE is a particularly suitable domain for skill evolution because it has executable verifiers, repeated repo structure, and recurring agent failure modes.

4. We propose a multi-level SWE SkillRepo learned from SWEGym: task-local trajectories are first distilled into task-stage skills, accepted task skills are then aggregated into repo skills, and repeated repo-level patterns are further distilled into cross-repo failure-mode skills. Two policies are trained separately from empty documents: a Skill Writer learns to draft skills, and a Skill Evaluator learns to accept, reject, score risk, and request rewrites. The system uses a fixed harness policy with strong/weak positive/negative labels and hierarchical update clocks: task skills update quickly, repo and failure-mode skills require multi-task evidence, and writer/evaluator policies update only after fixed failure-mode trigger windows before being frozen for test-time use.

5. We evaluate strict no-exact transfer on SWE-Bench Verified, with public SWE skills, raw trajectory retrieval, one-shot skills, and same-budget retry as baselines.

6. The key claim is not that task-specific hints solve benchmark tasks. The claim is that SWEGym can train transferable SWE general skills plus a writer/evaluator pair that improves frozen coding agents on held-out SWE tasks, while reducing common failure modes and execution cost.
```

## 8. 对审稿人最重要的防线

必须提前防住这些拒稿点：

1. **不是 task leakage**：主结果必须 no-exact transfer，task skills 只作为 raw material / upper bound。
2. **不是 second try**：必须有 same-budget retry baseline。
3. **不是 public skills 已经能做到**：必须跑 Public SWE Skills baseline。
4. **不是 one-shot summarization**：v0001 negative result + one-shot baseline 要保留。
5. **不是只涨 resolved 不管成本**：必须报告效率指标。
6. **不是 skill 越多越好**：训练时要有 verifier-supervised selection；测试时要有冻结 evaluator gate、rejected edits、compactness reward。
7. **不是泛泛 agent skill**：failure-mode taxonomy 必须 SWE-specific，并能对应 no_diff、bad_validation、timeout 等实际失败。
