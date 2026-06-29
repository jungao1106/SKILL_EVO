# StageSkill Storyline

## 1. 背景

大语言模型 agent 正在越来越多地被用于软件工程任务。它们可以阅读代码仓库、搜索文件、编辑代码、运行测试，并为真实的 GitHub issue 生成补丁。SWE-Bench Verified 这类 benchmark 使这一场景尤其重要，因为任务是否成功可以通过可执行测试和 patch-level verifier 来判断，而不是只依赖模型自评。

与此同时，当前大多数 coding agent 仍然更像是“一次性求解器”。Agent 在一个软件工程任务上成功或失败之后，它的执行轨迹中通常包含大量有价值的过程性经验，例如：

- 问题是如何被复现的；
- 哪些文件、函数或符号与问题相关；
- 哪个编辑操作真正解决了问题；
- 哪个验证命令是有用的；
- 哪些搜索路径或修复尝试是误导性的；
- agent 是如何从 no-diff、timeout、错误定位或错误验证中恢复的。

但是，这些经验通常不会被转化为稳定、可复用、能够改善后续 agent 行为的形式。

## 2. 为什么 Skills 是自然的载体

Skills 是外化过程性知识的一种自然接口。相比直接更新模型权重，skills 具有几个优势：

- 可读；
- 可编辑；
- 可版本化；
- 可审计；
- 容易注入到不同 agent 中；
- 不需要重新训练基础模型，更新成本较低。

因此，skills 很适合作为 agent evolution 的载体。我们可以保持 coding agent 本身冻结不变，只优化影响其行为的外部 skill state。

## 3. 现有 Coding Skills 的问题

本文的关键动机是：现有 coding skills 还没有被证明是一种稳定提升端到端 SWE 性能的干预方式。

更准确地说：

```text
现成的、公共的、静态的 coding skills 在端到端软件工程任务上的平均收益通常有限；
当它们与具体任务、代码仓库或 agent 行为不匹配时，甚至可能降低性能。
```

这并不是说 skills 没有价值，而是说盲目注入静态 skills 是一种高方差干预：

- 有些 skills 能提供有用的过程性指导；
- 许多 skills 不会改变最终 pass rate；
- 一些 skills 会引入过时假设、无关流程或上下文干扰；
- 对某个仓库或任务分布有用的 skill，可能会伤害另一个仓库或任务分布上的表现。

在软件工程场景中，这个问题尤其严重。因为一小段错误的过程性建议，就可能把 agent 引向错误文件、错误测试、过度重构或过时 API。

## 4. 支持该动机的已有观察

已有实证结果表明，公共或静态 coding skills 的效用通常较弱且不稳定。

例如，SWE-Skills-Bench 评估了 public SWE skills 在真实 GitHub project tasks 上的效果。结果显示，许多 skills 几乎没有带来 pass-rate 提升，部分 skills 甚至会降低性能。报告中的失败机制包括上下文干扰、模板锚定和版本不匹配。

这一观察说明：

```text
Skills 不应该被视为天然有益的上下文。
它们应该根据真实任务反馈被选择、验证和更新。
```

## 5. 重要反例与启发

同时，也存在重要反例。Trace2Skill、GEPA / gskill 和 optimize_anything 都表明，当 coding-agent skills 不是静态公共知识，而是从真实执行经验中产生时，它们可以更加有效。

Trace2Skill 是一个直接相关的例子。它不再依赖人工编写或公共收集的 skills，而是从 agent 的成功和失败轨迹中抽取局部 lesson，再经过分层合并，形成更一致的 skill directory。这个方向说明：轨迹中确实包含可以被提炼为 skills 的过程性知识。

GEPA / gskill 和 optimize_anything 进一步表明，当 coding-agent skills 满足以下条件时，它们可以显著提升性能：

- 从真实轨迹中学习；
- 针对特定仓库或任务分布；
- 通过任务反馈进行优化；
- 在部署前经过验证。

这些工作并不是都直接在 SWE-Bench Verified 或 SWE-Bench Pro 上做主实验，但它们共同揭示了一个重要原则：

```text
问题不是 skills 能不能帮上忙。
问题是如何把噪声大、静态且有时有害的 skills，
转化为任务相关、反馈验证过的 skill artifacts。
```

这导向本文的中心动机：

```text
SWE agent evolution 不应该盲目累积 coding skills。
它应该在真实软件工程任务的可执行反馈下演化 skills。
```

## 6. 我们的方法应解决什么

基于上述动机，本文不应只是再做一个“给 agent 注入 skills”的系统。更关键的问题是：如何避免 skills 变成新的噪声源。

如果只从某个 task 的轨迹中生成 skill，再在同一个 task 上使用，它可以证明 trajectory-derived skills 能改变 agent 行为，但这仍然更接近 task-specific memory 或 work-on-test adaptation。不足以说明 skills 具备更一般的迁移能力。

因此，我们需要在当前 task-level Skill evolution 的基础上，补上两类更 general 的 skills 和一个 skill 管理机制。

当前 framework 已经能做：

```text
task trajectory
  -> task-level stage skills
  -> task-conditioned skill-assisted evaluation
  -> verifier-based update
```

但它还缺少从 task-specific 经验走向更一般 skill 的中间层。因此，需要在现有框架中新增：

- **repo-level general skills**：同一代码仓库内跨任务可复用的经验；
- **failure-mode general skills**：跨仓库共享的失败模式处理策略；
- **skill management mechanism**：决定 skill 何时生成、提升、合并、压缩、降级、禁用或删除。

也就是说，task-specific skills 不应被视为最终目标，而应被视为 skill evolution 的原始经验单元。系统首先从单个任务轨迹中提取 task skills，然后通过 skill 管理机制判断哪些经验只应保留在 task 层，哪些经验应该被提升为 repo-level 或 failure-mode general skills。

改进后的 framework 应包含三层 skill 粒度：

| Skill 粒度 | 含义 | 作用 |
| --- | --- | --- |
| Task Skill | 单个 SWE task 的具体经验 | 作为局部适应和原始经验单元 |
| Repo-level General Skill | 同一代码仓库内可复用的经验 | 支撑同 repo 跨任务迁移 |
| Failure-mode General Skill | 跨仓库共享的失败模式处理策略 | 支撑 no-diff、错误定位、timeout、错误验证等通用恢复能力 |

对应地，skill evolution 不应该只是追加 task skills，而应该包含一个管理层：

```text
task trace
  -> task-level stage skill
  -> skill manager
  -> keep as task skill / promote to repo-level skill / promote to failure-mode skill
  -> retrieve and evaluate
  -> merge / compress / demote / rewrite / disable
```

这可以直接回应本文最初的动机：静态 skills 可能伤害性能，因此系统不能盲目累积 skills，而必须根据任务反馈动态管理 skills。

## 7. 更强的 Method Storyline

最终方法部分可以围绕四个模块展开，但重点应放在“在现有 task-level framework 上增加 general skills 和管理机制”。

第一，**Trace-to-StageSkill Extraction**。系统从 SWE agent 的执行轨迹中提取结构化证据，包括任务描述、工具调用、触达文件、编辑文件、测试命令、patch、verifier reward 和失败信号。然后将这些证据压缩为五阶段 task skills：

```text
reproduce -> localize -> edit -> validate -> recover
```

第二，**General Skill Construction**。系统从多个 task-level skills 中发现可复用模式，构造两类 general skills：

- `repo-level general skills`：例如某个 repo 中常见的测试入口、配置约束、模块边界、API 兼容性规则；
- `failure-mode general skills`：例如 no-diff 后如何恢复、重复搜索时如何重新定位、timeout 时如何收窄验证、测试命令错误时如何切换策略。

第三，**Skill Management Mechanism**。系统不把所有 task skills 都直接当作通用技能使用，而是根据跨任务反馈决定 skill 的生命周期：

- 只对单个任务有用的经验保留为 task-local memory；
- 在同一 repo 多次有用的经验提升为 repo-level general skill；
- 在多个 repo 中反复出现的失败处理策略提升为 failure-mode general skill；
- 导致 regression 的 skill 被降级、重写或禁用；
- 过宽、过长或过时的 skill 被压缩、拆分或删除。

第四，**Utility-aware Skill Retrieval and Evaluation**。在执行新任务时，系统不再只做 exact task-id retrieval，而是同时考虑 task skill、repo-level general skill 和 failure-mode general skill。检索依据包括当前任务的 repo、stage、path/symbol overlap、failure signature 和历史 reward。检索时应该优先选择与当前解题阶段和失败模式匹配的 skills，并过滤掉风险较高的 skills。

每次 skill-assisted rollout 之后，系统根据 verifier reward 和 transition 判断 skill 的作用：

```text
0 -> 1: skill 可能有帮助，应保留或提升；
1 -> 0: skill 可能有害，应重写、降级或禁用；
0 -> 0: skill 未能带来帮助，应做有界修改或保持 inactive；
1 -> 1: skill 不一定需要改动，可作为稳定锚点。
```

这种方法的重点不是生成更多 skills，而是让 skills 经过真实 SWE feedback 的筛选和演化，逐渐从 task-specific 经验变成更可靠的 repo-level 或 failure-mode-level 控制策略。

最终，本文的方法主线可以概括为：

```text
我们不是盲目收集 coding skills，
而是把 SWE 轨迹转化为阶段化 skill candidates，
再通过 verifier feedback 决定哪些 skills 应该被保留、提升、重写或禁用。
```

## 8. 每轮迭代应该更新什么

每一轮 evolution 不应该只是重新生成一批 task skills，而应该同时更新四类对象：

| 迭代对象 | 更新频率 | 作用 |
| --- | --- | --- |
| Task-level Stage Skills | 每轮更新 | 从单个任务轨迹中提取五阶段经验，作为原始经验单元 |
| Repo-level General Skills | 满足证据后提升 | 提炼同一 repo 内跨任务可复用的定位、编辑、验证规则 |
| Failure-mode General Skills | 满足跨任务证据后提升 | 提炼 no-diff、timeout、错误定位、错误验证等通用恢复策略 |
| Skill Manager / Retrieval Policy | 根据聚合反馈更新 | 决定哪些 skills 被使用、提升、合并、降级、禁用或过滤 |

推荐的一轮迭代流程是：

```text
run current skills
  -> collect reward / trace / patch / tests / failure signature
  -> update task-level stage skills
  -> mine repo-level and failure-mode candidates
  -> promote / merge / compress / demote / disable skills
  -> evaluate with verifier feedback
  -> update retrieval and management policy
  -> freeze next skill version
```

不同对象不应以相同频率更新。Task-level skills 可以高频小步更新；repo-level 和 failure-mode skills 需要多个任务的正负证据后再提升；skill manager 和 retrieval policy 应根据聚合统计低频更新，避免 prompt drift 和 skill drift。

每个 skill 至少应记录以下元信息：

```yaml
level: task | repo | failure_mode
stage: reproduce | localize | edit | validate | recover
source_tasks:
positive_cases:
negative_cases:
retrieval_triggers:
risk_flags:
promotion_or_demotion_reason:
disabled:
```

这样，每轮迭代回答的不是“能不能生成更多 skills”，而是：

```text
这条 skill 在真实 rollout 中帮了谁、害了谁、为什么？
```
