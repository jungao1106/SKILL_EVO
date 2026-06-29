# 训练与评测流程

## 核心范式

本项目的核心范式是：

```text
Writer = evidence / skill proposer
Evaluator = verifier-proxy learner
Verifier = training-time reward signal and validation gate
```

训练阶段可以访问 SWE-Gym verifier。Verifier 不负责写 skill，也不提供 hidden solution；它只提供 outcome supervision：reward、diagnostic、regression / non-regression。

测试阶段没有 verifier。Test-time skill evolution 使用训练好的 evaluator 替代 verifier，对 benchmark 上产生的 candidate skill 给出 proxy reward / accept-reject decision。

## 统一术语

不要把 task 层产物叫 skill。统一使用下面三层：

```text
task level：task evidence
repo level：repo-level skill candidate / accepted repo skill
failure-mode level：failure-mode repair skill candidate / accepted failure-mode skill
```

### Task Evidence

Task evidence 是单个 task trace 的结构化证据，不是 skill。

它记录：

```text
task id / repo
problem summary
reward / previous reward
case label
exception / diagnostic
selected skills
edited paths / touched paths
test commands
tool sequence
failure signature
public validation signals
```

Task evidence 的作用是给 repo-level 和 failure-mode-level 归纳提供原材料。它不应该直接写成可检索的 `SKILL.md`。

### Repo-Level Skill

Repo-level skill 是从同一个 repo 的多个 task evidence 中归纳出的 repo-scoped candidate。

它回答的问题是：

```text
在这个 repo 里，遇到类似 public evidence 时，应该如何定位、编辑、验证或恢复？
```

Repo-level skill 仍然是 repo-specific。它主要作为训练期中间抽象层和 failure-mode 总结的支撑；跨 benchmark 主推理默认不部署 SWE-Gym repo skills。

### Failure-Mode Repair Skill

Failure-mode repair skill 是从多个 repo 中重复出现的失败模式归纳出的 transferable skill。

它回答的问题是：

```text
当 agent 在不同 repo 中反复出现同类失败时，应该如何恢复？
```

Failure-mode skill 不能包含 repo-specific path、task id、exact patch、hidden verifier detail。它是主要可迁移产物。

## 状态对象

`memory` 是版本化状态容器，不是单纯的技能文件。

建议状态包含：

```text
evidence_memory：task evidence、repo clusters、failure clusters
candidate_state：writer 产生的 repo / failure-mode candidates
evaluator_state：evaluator decisions、proxy reward、calibration events
skill_state：accepted repo skills、accepted failure-mode skills、general seed skills
decision_log：accept / reject / revise / rollback 的证据与原因
policy_state：writer policy、evaluator policy、harness policy
```

`SKILL.md` 只是 accepted skill 的导出 artifact。Task evidence 和 rejected / memory-only candidate 不应伪装成 `SKILL.md`。

## 训练阶段

训练数据：

```text
SWE-Gym 500-task subset
train：474 条
validation：26 条
```

训练阶段的奖励信号来自 SWE-Gym verifier：

```text
reward = verifier_result.rewards.reward
```

Verifier reward 用于三件事：

```text
1. 给 task evidence 打 outcome label。
2. 校准 evaluator，让 evaluator 学习 verifier 的判断边界。
3. 在 validation gate 中决定 candidate state 是否 accept / rollback。
```

### Case Label

如果有 previous run，可以形成 verifier transition label：

```text
strong_positive：previous < 1 且 current >= 1
weak_positive：current >= 1，但没有明确 paired improvement
weak_negative：current < 1
strong_negative：previous >= 1 且 current < 1
diagnostic：exception 或 missing reward
```

这些 label 是训练期 evaluator 学习 verifier 的监督信号之一。

## Writer

Writer 负责提取和总结，不负责 accept/reject。

Writer 的职责：

```text
1. 从单个 solver trace 提取 task evidence。
2. 从同 repo 的 task evidence cluster 总结 repo-level skill candidate。
3. 从跨 repo 的 repeated failure evidence 总结 failure-mode repair skill candidate。
```

Writer 不负责判断：

```text
support 是否足够
candidate 是否 active
candidate 是否 promote
candidate 是否 memory-only
是否最终写入 skill library
```

这些由 evaluator 和 validation gate 负责。

### Writer 输出

Repo candidate 示例：

```json
{
  "level": "repo",
  "repo": "conan-io__conan",
  "name": "conan-build-tooling-localize-validate",
  "trigger": "...",
  "evidence_gate": "...",
  "actions": ["..."],
  "validation_hint": "...",
  "abort_condition": "...",
  "support_summary": "..."
}
```

Failure-mode candidate 示例：

```json
{
  "level": "failure_mode",
  "failure_signature": "localization-drift",
  "name": "recover-from-localization-drift",
  "trigger": "...",
  "actions": ["..."],
  "stop_condition": "...",
  "support_summary": "..."
}
```

### Writer Policy 更新条件

Writer policy 可以更新，但它更新的是“如何产生更好的 candidate”，不是“如何判断 candidate 是否可接受”。

训练期 writer policy 的监督信号来自 candidate 的后续反馈：

```text
evaluator decision：accept / reject / revise / memory_only
evaluator risk_flags
verifier reward / case label
validation gate accept / rollback
candidate 是否最终进入 repo / failure-mode library
candidate 是否导致 regression
```

Writer policy 不应该每个 task 更新。它只在窗口级别或硬安全风险出现时更新。

更新触发条件：

```text
accepted_pattern：
  某类 writer candidate 多次被 evaluator 接受，并且 verifier / validation 没有反驳。
  更新方向：以后优先生成这种 trigger / evidence_gate / action / stop_condition 结构。

rejected_pattern：
  某类 writer candidate 多次被 evaluator 拒绝。
  常见原因：generic、over-specific、missing evidence gate、hidden/verifier leakage、exact patch。
  更新方向：以后避免这种写法。

revise_pattern：
  evaluator 多次要求 revise。
  常见原因：missing trigger、missing stop condition、support_summary 不清楚、actions 不可执行、
  repo-specific 内容混入 failure-mode。
  更新方向：给 writer 增加格式和内容约束。

regression_pattern：
  writer candidate 被接受后导致 validation rollback 或 strong_negative。
  更新方向：减少这类 candidate 的生成倾向，要求更强 evidence gate 和更保守 scope。

hard_safety：
  出现 task id leakage、hidden verifier detail、oracle patch、private path、exact patch memorization。
  更新方向：立即加入 hard constraint。
```

Writer policy 的输出可以是 `writer_policy.md` 中的规则增量：

```text
Prefer:
- Summarize repo candidates around repeated public evidence gates, not one-off paths.
- For failure-mode candidates, write process-level repair actions and remove repo-specific names.

Avoid:
- Do not include task ids, exact patches, hidden verifier details, or private paths.
- Do not emit failure-mode candidates from a single repo.
- Do not write generic advice without trigger, evidence gate, action, and stop condition.
```

Writer policy 和 evaluator policy 的区别：

```text
writer policy：更新“怎么写 candidate”
evaluator policy：更新“怎么判断 candidate 是否像 verifier-positive”
```

测试期默认冻结永久 writer policy。若启用 test-time skill evolution，最多维护 bounded session-level writer notes，不写回训练库。

## Evaluator

Evaluator 是 verifier-proxy learner。它不是 writer，也不是最终 skill。

训练阶段，evaluator 学习：

```text
trace + candidate + public evidence -> verifier-like outcome
```

也就是学习预测：

```text
proxy_reward
confidence
accept / reject / revise / memory_only
risk_flags
```

训练期 evaluator 可以使用 verifier outcome 作为监督信号。测试期 evaluator 不能访问 verifier，只能根据 public trace signal 输出 proxy reward。

### Evaluator 输入

训练期输入：

```text
candidate skill
evidence cluster
public trace signals
verifier reward
previous/current reward transition
diagnostic / exception
validation gate decision
```

测试期输入：

```text
candidate skill
evidence cluster
public trace signals
```

测试期不能输入：

```text
benchmark hidden verifier reward
hidden tests
oracle patch
final benchmark score
```

### Evaluator 输出

固定 JSON：

```json
{
  "decision": "accept | reject | revise | memory_only",
  "proxy_reward": 0.73,
  "confidence": 0.81,
  "risk_flags": [],
  "reason": "..."
}
```

### Evaluator 更新条件

Evaluator policy 不应该每个 task 更新。它只在窗口事件或硬风险出现时更新。

更新触发条件：

```text
false_accept：evaluator 接受 candidate，但 verifier reward 差、validation regression 或 diagnostic 反驳。
false_reject：evaluator 拒绝 candidate，但同类 pattern 多次 verifier-positive。
repeated_support：同类 candidate 多次被 public evidence + verifier 支持。
repeated_contradiction：同类 candidate 多次 verifier-negative 或 diagnostic。
hard_safety：出现 task id leakage、hidden verifier detail、oracle patch、private path、exact patch memorization。
```

训练期可以把这些事件写入：

```text
evaluator_calibration.jsonl
```

然后低频更新 `evaluator_policy.md`。

测试期不更新永久 evaluator policy；最多维护 bounded session calibration。

## Skill Evolution Loop

训练 loop 可以实现为：

```text
S_t = 当前 skill state / evaluator state

for each train batch:
  1. 用当前 accepted skills 跑 SWE-Gym train tasks。
  2. Writer 从 trace 中提取 task evidence。
  3. 按 repo 聚合 task evidence，形成 repo evidence clusters。
  4. Writer 从 repo cluster 总结 repo skill candidate。
  5. Evaluator 学习 verifier，并裁决 repo candidate。
  6. 被接受的 repo candidate 写入 accepted repo skills。
  7. 将 repo evidence / accepted repo skills 放入 cumulative failure pool。
  8. 当某个 failure signature 获得跨 repo 支持时，writer 总结 failure-mode repair candidate。
  9. Evaluator 裁决 failure-mode candidate。
  10. 被接受的 failure-mode candidate 写入 accepted failure-mode skills。
  11. 周期性跑 held-out SWE-Gym validation。
  12. validation gate 通过则 accept candidate state，否则 rollback。
```

## Repo-Level 聚合

Repo-level candidate 应从同 repo 多个 task evidence 中产生。

Basic cluster signals：

```text
repeated owner paths / modules
repeated edited paths
repeated test commands
repeated public validation pattern
repeated failure signature
positive / negative / diagnostic case counts
```

推荐不要再使用：

```text
active_stage_skill_count > 0
```

作为 repo aggregation 的必要条件。Task layer 是 evidence，不是 skill。Repo candidate 是否可接受由 evaluator 判断。

## Failure-Mode 聚合

Failure-mode candidate 应从 cumulative cross-repo failure pool 中产生，而不是只看最近几个 repo updates。

建议结构：

```text
failure_pool[signature][repo] = repo evidence / accepted repo skill summaries
```

触发条件交给 evaluator 裁决，但 writer 的候选输入应该至少包含：

```text
failure_signature
support_repos
repo summaries
public evidence summary
negative / diagnostic evidence
```

Failure-mode skill 必须是过程性 repair skill，不能是 repo-specific knowledge。

## Validation Gate

Validation gate 决定 candidate state 是否被接受。

Validation 使用 held-out SWE-Gym validation tasks，可以访问 SWE-Gym verifier。

主要指标：

```text
effective_resolved_rate = resolved_valid_trials / effective_valid_trials
diagnostic_rate = diagnostic_trials / total_trials
```

接受条件：

```text
effective_resolved_rate 不下降，或下降在容忍范围内
diagnostic_rate 不超过阈值
effective_valid_trials 足够
没有明显 regression
```

失败时：

```text
candidate state 标记为 rejected
active state rollback 到上一个 accepted version
validation decision 写入 decision_log
```

## Diagnostic 处理

以下情况标记为 diagnostic：

```text
missing verifier reward
harness exception
BuildException
AddTestsDirError
AgentTimeoutError
provider / runtime failure
interrupted run
```

Diagnostic 进入 task evidence 和 evaluator calibration，但不应作为语义 skill 的正例。它可以用于学习 evaluator 的风险判断和 runtime guard。

## Test-Time Skill Evolution

Test-time skill evolution 是可选扩展设置。

测试期没有 verifier，因此使用训练好的 evaluator 替代 verifier：

```text
writer 继续从 benchmark trace 提取 evidence / 生成 candidate
evaluator 输出 proxy_reward / accept-reject decision
accepted candidate 只进入 temporary session skill library
```

测试期只能使用 public trace signals：

```text
public tests
lint / typecheck
patch exists
localized diff
runtime error
no-diff
repeated failed commands
repeated failure signature
```

测试期不能使用：

```text
hidden verifier reward
hidden tests
final benchmark score
oracle patch
```

测试期默认只写：

```text
skills/session/
```

不写回训练库，不更新永久 evaluator policy。

## 推理阶段

主实验的 frozen transfer setting 使用：

```text
accepted failure-mode skills
general SWE seed skills
frozen evaluator only if running test-time evolution
```

主实验不使用：

```text
SWE-Gym task evidence
SWE-Gym repo skills
SWE-Gym verifier labels
validation verifier labels
rejected candidates
diagnostic-only traces
```

如果不启用 test-time skill evolution，则 downstream benchmark 上完全不更新 skill state。

## 当前代码修改方向

当前实现中的对应模块：

```text
1. scripts/update_skill_harness_memory.py
   - task level 默认不再写 task-stage SKILL.md。
   - 输出 task evidence / task_events.jsonl。

2. scripts/run_swegym_skill_evo_loop.py
   - repo aggregation 不再依赖 active_stage_skill_count。
   - repo_skill_decision 拆成 repo cluster -> writer candidate -> evaluator decision。
   - failure-mode 聚合从 recent window 改成 cumulative failure_pool。

3. 新增 agents/skill_writer.py
   - extract_task_evidence
   - write_repo_candidate
   - write_failure_mode_candidate

4. 新增 agents/skill_evaluator.py
   - evaluate_candidate
   - append_calibration_event
   - update_policy_from_window

5. skill directory
   - accepted SKILL.md 只保存 accepted repo / failure-mode / general skills。
   - task evidence 和 rejected candidates 存在 run_logs/evidence 与 run_logs/candidates。
```

推荐目录：

```text
run_logs/.../training/evidence/task_events.jsonl
run_logs/.../training/evidence/repo_clusters.jsonl
run_logs/.../training/evidence/failure_clusters.jsonl
run_logs/.../training/candidates/repo_candidates.jsonl
run_logs/.../training/candidates/failure_mode_candidates.jsonl
run_logs/.../training/evaluator/evaluator_decisions.jsonl
run_logs/.../training/evaluator/evaluator_calibration.jsonl
skills/accepted/_repos/
skills/accepted/_failure_modes/
skills/session/
```
