# 多层 Skill Evolution 设计：Task / Repo / Failure-Mode Skills

## 0. 重新定位

当前系统已经有 **task-specific stage skills**：

```text
skills/tasks/<version>/<repo>/<task>/<stage>/<skill>/SKILL.md
```

并且当前 PiAgent 的 skill memory 检索仍然是 `task_exact`。这能支撑 task-conditioned skill evolution，但不能充分支撑 transferable skills。

新的设计目标是引入三层 skill stack：

```text
Task skills
  -> Repo skills
  -> Failure-mode skills
```

其中：

- **task skills** 是最小经验单元；
- **repo skills** 是同一 repo 内多个 task skills 的可复用蒸馏；
- **failure-mode skills** 是跨 repo 的 agent 行为修正策略，用来处理 no-diff、错误定位、重复搜索、验证不足、timeout 等失败模式。

最终 policy 形式变成：

```text
pi(a_t | h_t, x; M, S_task, S_repo, S_failure)
```

其中 `M` 是冻结的 GLM-5.1 / PiAgent，`S_*` 是外部可进化 skill states。

## 1. 三层 Skill 的职责

### 1.1 Task Skills

**作用**：记录单个 SWE-Bench task 的具体轨迹经验。

粒度：

```text
task_id + stage
```

内容：

- issue 中的复现线索；
- 读过/改过的文件；
- 具体测试命令；
- 失败/成功轨迹中的关键证据；
- 五阶段控制点：`reproduce/localize/edit/validate/recover`。

使用边界：

- 只在 `exact-task` 协议中作为局部 adaptation 或 upper bound；
- 不直接声称 transferable；
- 作为 repo/failure-mode skill 的蒸馏原料。

### 1.2 Repo Skills

**作用**：同一 repo 内可迁移的程序性知识。

粒度：

```text
repo + stage + topic/path-pattern/failure-signature
```

例子：

```text
django / localize / migrations-autodetector
django / validate / auth-tests
matplotlib / edit / scale-locator-boundary
xarray / validate / focused-dataarray-dataset-tests
```

Repo skill 不应该包含某个 task 的 exact patch，而应该包含：

- 这个 repo 常见 owner modules；
- 常用 focused tests；
- repo-specific debugging route；
- 需要保留的 API / compatibility constraints；
- 常见错误定位陷阱；
- 适用条件和停止条件。

使用边界：

- 只能在同一 repo 的新 task 中检索；
- 必须先 inspect 当前 repo evidence，再读取或执行 skill；
- 如果 trigger 不匹配，必须忽略。

### 1.3 Failure-Mode Skills

**作用**：跨 repo 修正 agent 失败行为。

粒度：

```text
failure_mode + stage
```

推荐先定义这些 failure modes：

| Failure Mode | 主要影响阶段 | 典型信号 |
| --- | --- | --- |
| `no_diff` | edit / recover | agent 结束但没有 repo diff |
| `localization_drift` | localize / recover | 长时间搜索但没有 owner hypothesis |
| `wrong_owner_file` | localize / edit | patch 不在真正相关模块 |
| `bad_validation` | validate | 没有运行 focused tests 或测试命令无效 |
| `repeated_search` | localize / recover | 重复 grep/read 相同区域 |
| `timeout` | recover / validate | 工具调用过多、测试过宽 |
| `overbroad_edit` | edit | 改动范围过大、无关重构 |
| `environment_setup_error` | validate / recover | 依赖、路径、命令环境错误 |
| `patch_regression` | edit / validate | PASS_TO_PASS 失败或已有行为破坏 |

Failure-mode skill 是 agent-control skill，不是 repo patch recipe。它应写成：

```text
when signal appears -> collect evidence -> change behavior -> stop condition
```

例子：

```text
no_diff/recover:
If the agent has run tests or inspected files but no repository diff exists,
do not finish. State the current owner hypothesis, inspect the source file,
make the smallest persistent source edit, then run one focused verification.
```

## 2. 推荐目录结构

保留当前 task skills，不直接打破现有结构。新增 repo 和 failure-mode skill roots：

```text
skills/tasks/<version>/<repo>/<task>/<stage>/<skill>/SKILL.md
skills/repos/<version>/<repo>/<stage>/<skill>/SKILL.md
skills/failure_modes/<version>/<failure_mode>/<stage>/<skill>/SKILL.md
skills/indexes/<version>/skill_index.json
```

统一的 `skill_index.json` 记录每个 skill 的元信息：

```json
{
  "skill_id": "repo:django:validate:auth-focused-tests:v0016",
  "scope": "repo",
  "repo": "django",
  "failure_mode": null,
  "stage": "validate",
  "path_patterns": ["django/contrib/auth/**", "tests/auth_tests/**"],
  "trigger": "Current issue touches auth forms, password reset links, user model, or auth backend behavior.",
  "source_task_ids": ["django__django-16139", "django__django-16631"],
  "support": {
    "positive": 4,
    "neutral": 8,
    "negative": 1
  },
  "quality_score": 0.78,
  "status": "active",
  "version": "v0016"
}
```

## 3. Skill Schema

每个 repo / failure-mode skill 建议统一成下面结构：

```yaml
---
name:
scope: task | repo | failure_mode | global
stage: reproduce | localize | edit | validate | recover
repo:
failure_mode:
status: candidate | active | disabled | rejected
quality_score:
source_task_ids:
risk_flags:
---

## Trigger

什么时候可以使用。

## Evidence To Collect

使用前必须确认哪些当前任务证据。

## Actions

具体操作策略。

## Stop Condition

什么时候停止使用，避免过度影响当前任务。

## Anti-Patterns

哪些情况下不要用。

## Support Evidence

来自哪些 task / transition / verifier 结果。
```

关键要求：

- repo skill 可以包含 repo-relative path pattern，但不能包含某个 task 的 gold patch；
- failure-mode skill 尽量不包含 repo path；
- 所有 skill 都必须有 trigger 和 stop condition；
- 所有 skill 都必须有 source evidence 和 negative evidence 计数。

## 4. 生成路径：Task Skills 如何晋升

### 4.1 Task -> Repo

Repo skill 来自同一 repo 内多个 task skills 的聚合。

候选生成规则：

```text
group by repo + stage + path cluster + issue/failure tokens
```

晋升条件建议：

| 条件 | 默认阈值 |
| --- | ---: |
| source task 数 | >= 3 |
| positive transitions | >= 2 |
| strong negative transitions | 0 或可解释 |
| exact task-id / gold patch leakage | 0 |
| selection split improvement | > current repo skill |

生成流程：

```text
task skills
  -> group by repo/stage/path/failure
  -> LLM distill into repo candidate
  -> remove task-specific details
  -> run selection tasks with no exact task skills
  -> accept as active repo skill or reject
```

### 4.2 Task -> Failure-Mode

Failure-mode skill 来自跨 repo 的重复失败模式。

候选生成规则：

```text
group by failure_mode + stage
```

晋升条件建议：

| 条件 | 默认阈值 |
| --- | ---: |
| source task 数 | >= 5 |
| source repo 数 | >= 3 |
| failure signal 一致 | >= 70% |
| 内容不依赖具体 repo/path | 必须 |
| no-exact selection improvement | > baseline |

生成流程：

```text
failed / recovered task traces
  -> classify failure_mode
  -> collect useful recover/validate/localize actions
  -> distill cross-repo control rule
  -> test on held-out tasks without exact task memory
  -> accept / rewrite / reject
```

## 5. 检索与注入策略

### 5.1 检索顺序

推荐 PiAgent 每次任务使用这个 retrieval stack：

```text
1. task_exact skills       only if protocol allows exact-task memory
2. repo skills             same repo, high trigger match
3. failure-mode skills     issue/runtime signal match
4. global SWE stage skills optional, lowest priority
```

在论文实验里要分两种协议：

| 协议 | 允许 task skills | 允许 repo skills | 允许 failure skills | 用途 |
| --- | --- | --- | --- | --- |
| Exact-task evolution | 是 | 是 | 是 | 当前主结果 / upper bound |
| No-exact transfer | 否 | 是 | 是 | 证明 transfer |
| Failure-only | 否 | 否 | 是 | 证明 failure-mode skills |
| Repo-only | 否 | 是 | 否 | 证明 repo skills |

### 5.2 排序公式

每个候选 skill 的 ranking score：

```text
score =
  scope_weight
  + semantic_overlap(issue, trigger/actions)
  + symbolic_overlap(paths, symbols, tests)
  + failure_signal_match
  + historical_utility
  - risk_penalty
  - token_cost_penalty
```

推荐默认权重：

| Signal | 权重 |
| --- | ---: |
| exact task match | +10 |
| repo match | +4 |
| path/symbol overlap | +2 |
| failure-mode match | +3 |
| positive support | +log(1 + positives) |
| strong negative support | -3 each |
| generic / stale risk | -5 |

### 5.3 Prompt Budget

不要把所有 skill 全塞进 prompt。推荐：

```text
task skills: max 2
repo skills: max 3
failure-mode skills: max 2
global skills: max 1
total rendered prompt <= 3000-4000 chars
```

同时把完整 skill 文件打包进 sandbox，让 agent 在 trigger 匹配后再读。

## 6. Runtime 使用规则

PiAgent prompt 应明确：

```text
Repo skills and failure-mode skills are weak, evidence-gated priors.
Inspect the current repository before following any skill.
If the trigger does not match concrete current evidence, ignore the skill.
Task instruction and repository evidence override all skills.
```

Failure-mode skills 最适合动态触发：

- no diff rescue；
- repeated search；
- timeout approaching；
- invalid test command；
- validation failed but patch likely correct；
- no owner file after N inspections。

当前实现暂时没有真正的 dynamic retrieval，可以先用两步近似：

1. 初始 prompt 列出 top-k failure-mode skills；
2. 每个 failure-mode skill 有清楚 trigger，让 agent 在运行中自己判断是否读取。

后续可以在 harness 层加入 runtime event hook：

```text
event: no_diff / repeated_search / timeout_warning / bad_validation
  -> retrieve failure-mode skill
  -> append rescue prompt
```

## 7. Evolution：三层如何一起更新

### 7.1 Rollout 日志必须记录 skill exposure

每次 evaluation 要记录：

```json
{
  "task_id": "...",
  "repo": "django",
  "selected_skills": [
    {
      "skill_id": "repo:django:validate:auth-focused-tests:v0016",
      "scope": "repo",
      "stage": "validate",
      "rendered_in_prompt": true,
      "file_read_by_agent": true,
      "trigger_matched": true
    }
  ],
  "outcome": 1,
  "transition": "0->1",
  "failure_after": null
}
```

区分三种 credit：

| 情况 | 如何记 credit |
| --- | --- |
| skill 只出现在 prompt，但未读 | 只记 retrieval exposure，不给强 credit |
| skill 被读，trigger 匹配 | 可以做 outcome attribution |
| skill 被读但 trigger 不匹配 | 记 retrieval false positive |

### 7.2 Transition 到更新规则

| Transition | Task Skill | Repo Skill | Failure-Mode Skill |
| --- | --- | --- | --- |
| `0 -> 1` | preserve / distill | 增加 positive support；可推广 | 增加 positive support；保留触发规则 |
| `1 -> 1` | stable anchor | 增加 neutral/stable support | 增加 neutral/stable support |
| `1 -> 0` | disable / rewrite harmful stage | 强负例；收窄 trigger 或降级 | 强负例；若跨 repo 多次出现则 disable |
| `0 -> 0` | targeted rewrite | weak negative；补 stop condition | weak negative；补 recover/validate guard |
| `error -> 1` | preserve validate/recover | 加环境/验证 guard | 加 environment_setup recovery |
| `1 -> error` | disable risky stage | 强负例 | 强负例 |

### 7.3 Bottom-up 和 Top-down

Bottom-up：

```text
task rollout
  -> task skill
  -> repo candidate / failure-mode candidate
  -> selection gate
  -> active repo/failure skill
```

Top-down：

```text
repo/failure skill used in new rollout
  -> transition feedback
  -> support stats update
  -> rewrite / split / disable / promote
```

### 7.4 Promotion / Demotion / Split / Merge

操作空间：

```text
promote_task_to_repo
promote_task_to_failure_mode
merge_repo_skills
split_repo_skill_by_path_cluster
rewrite_trigger
add_stop_condition
disable_skill
demote_to_task_only
```

何时 split：

- 一个 repo skill 同时覆盖多个 path clusters；
- selection 上一部分任务收益、一部分任务回退；
- trigger 太宽导致 false positive。

何时 demote：

- repo skill 只对单个 task 有效；
- exact path / patch shape 无法安全泛化；
- no-exact selection 无收益。

## 8. 推荐实现步骤

### Phase 1：不改行为，只补元数据

目标：

```text
让现有 task skills 带上 scope/source/support/failure_mode 元数据。
```

改动：

- 在 `update_skill_harness_memory.py` 输出中加入 `failure_mode`；
- 给每个 stage skill 加 `scope: task`；
- 记录 `source_task_id`、`repo`、`stage`、`quality_score`、`risk_flags`。

验收：

```text
现有 task_exact retrieval 不变；
v0015 结果不受影响。
```

### Phase 2：离线生成 repo / failure candidates

新增脚本：

```text
scripts/build_multilevel_skill_library.py
```

输入：

```text
skills/tasks/v0015
jobs/*v0012-v0015*
analysis/evolution/*score_report*
```

输出：

```text
skills/repos/v0016/...
skills/failure_modes/v0016/...
skills/indexes/v0016/skill_index.json
```

验收：

- 每个 repo 至少生成 localize/validate/recover 候选；
- 每个 failure-mode 至少生成 recover/validate 候选；
- 没有 exact task leakage。

### Phase 3：新增多层检索

当前 `agents/skill_harness_memory.py` 只有：

```text
retrieval_scope = task_exact
```

需要新增：

```text
retrieve_multilevel_memory(task_text, allow_task_exact: bool)
```

返回：

```json
{
  "retrieval_scope": "task_repo_failure",
  "selected_task_skills": [],
  "selected_repo_skills": [],
  "selected_failure_mode_skills": [],
  "prompt": "..."
}
```

验收：

- exact-task 协议下：task + repo + failure 都可用；
- no-exact 协议下：task skills 被禁用，只用 repo/failure；
- metadata 写进 `pi-metadata.json`。

### Phase 4：Selection gate

不能让 repo/failure skills 生成后直接 active。

设置：

```text
train: build skills
selection: decide active / disabled / rewrite
test: final report
```

至少先在 SWE-Bench Verified 上做：

```text
train 300 / selection 100 / test 100
```

验收：

```text
repo/failure skill 必须在 no-exact selection 上不低于 baseline 才能 active。
```

### Phase 5：Evolution loop

每一轮：

```text
1. run PiAgent with active multilevel skills
2. collect outcome + skill exposure
3. update support stats
4. propose edits for risky skills
5. evaluate candidates on selection
6. accept / reject / disable
7. write new version
```

## 9. 实验矩阵

最低实验矩阵：

| Setting | Task Skills | Repo Skills | Failure Skills | 目的 |
| --- | --- | --- | --- | --- |
| No Skills | 否 | 否 | 否 | baseline |
| Task-only exact | 是 | 否 | 否 | 当前 upper bound |
| Repo-only no-exact | 否 | 是 | 否 | repo transfer |
| Failure-only no-exact | 否 | 否 | 是 | failure-mode transfer |
| Repo + Failure no-exact | 否 | 是 | 是 | true transfer |
| Task + Repo + Failure | 是 | 是 | 是 | best task-conditioned |

必须报告：

- resolved count；
- `0 -> 1` improvements；
- `1 -> 0` regressions；
- retrieval false positives；
- skill read rate；
- trigger match rate；
- no-diff / timeout / bad-validation 变化；
- per-repo gain；
- per-failure-mode gain。

## 10. 论文叙事如何变化

原来的说法：

```text
我们有 task-specific skills，并在 exact-task setting 中提升。
```

新的说法：

```text
Task skills are the atomic experience records.
Repo skills and failure-mode skills are promoted, verifier-gated abstractions
that enable transfer beyond exact task memory.
```

中文：

```text
task skills 是经验原子；
repo skills 是同一代码库内的可迁移软件工程知识；
failure-mode skills 是跨代码库的 agent 行为修正策略；
三者通过 verifier transition 共同参与 evolution。
```

## 11. 最重要的设计原则

1. **task skill 不等于 transferable skill**：它是原料，不是最终泛化证据。
2. **repo skill 必须有 repo scope**：不能跨 repo 乱用。
3. **failure-mode skill 必须是行为控制策略**：不能写成具体代码补丁。
4. **promotion 必须经过 no-exact selection gate**。
5. **所有 skill 更新都要记录 negative evidence**。
6. **retrieval false positive 比 false negative 更危险**：宁可少用，不要乱用。
7. **skill 参与 evolution 的前提是被读且 trigger 匹配**：只出现在 prompt 里不能强归因。
