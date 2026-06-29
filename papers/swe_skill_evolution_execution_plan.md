# SWE Skill Evolution 详细执行方案

## 0. 环境约定

所有命令默认在仓库根目录执行：

```bash
cd /vePFS-Mindverse/user/intern/jungao/SKILLS_EVO
source /root/miniforge3/etc/profile.d/conda.sh
conda activate base
```

当前环境检查结果：

```text
Python 3.13.13
```

固定实验设置：

| 项 | 设置 |
| --- | --- |
| Model | GLM-5.1 |
| Agent | Pi Coding Agent / PiAgent |
| Dataset | SWE-Bench Verified full set |
| Harness | Harbor + E2B + SWE-Bench verifier |
| Baseline | No-Skills + Public SWE Skills |
| Skill mode | StageSkill: reproduce / localize / edit / validate / recover |

## 1. 当前已有结果

### 1.1 Full-set No-Skills 基线

当前 full SWE-Bench Verified 500 题 no-skills 结果：

| Setting | Resolved | Rate |
| --- | ---: | ---: |
| No-Skills | 370/500 | 74.0% |

这已经满足验收要求：

```text
No-Skills full-set score >= 74%
```

### 1.2 早期 v0001 结果：负结果 / 设计教训

早期 v0001 是从 no-skills baseline 直接生成五阶段 skills 后，在同 shard 上重新评测。五个 100-task shard 汇总如下：

| Shard | No-Skills | v0001 Skill Eval | Delta |
| --- | ---: | ---: | ---: |
| 001-100 | 67 | 64 | -3 |
| 101-200 | 72 | 65 | -7 |
| 201-300 | 79 | 79 | 0 |
| 301-400 | 78 | 78 | 0 |
| 401-500 | 74 | 70 | -4 |
| Total | 370 | 356 | -14 |

结论：

```text
naive one-shot task skill generation 不够，甚至会伤害表现。
必须加入 verifier-grounded selection、失败子集定向演化、reward/curator policy、以及 bounded stage edits。
```

### 1.3 当前有效结果：v0012-v0015

后续版本不是重跑所有 500 题，而是在 no-skills 失败子集上做 skill-assisted rerun，并把新增 resolved 加回 no-skills full-set 基线。

| Version | Failure-subset Trials | Newly Resolved | Full-set Aggregate | Rate | Delta vs No-Skills |
| --- | ---: | ---: | ---: | ---: | ---: |
| v0012 | 118 | +17 | 387/500 | 77.4% | +17 |
| v0013 | 113 | +17 | 387/500 | 77.4% | +17 |
| v0014 | 119 | +23 | 393/500 | 78.6% | +23 |
| v0015 | 120 | +24 | 394/500 | 78.8% | +24 |

当前最好结果：

```text
No-Skills: 370/500, 74.0%
StageSkill v0015 assisted: 394/500, 78.8%
Gain: +24 tasks, +4.8 percentage points
```

这满足验收要求：

```text
Skill-assisted evaluation > No-Skills
```

## 2. 总体流程

最终流程分成 8 个阶段：

```text
Stage 0: 环境与配置检查
Stage 1: No-Skills full-set baseline
Stage 2: Public SWE Skills baseline
Stage 3: Trace collection and skill generation
Stage 4: One-shot StageSkill evaluation
Stage 5: Failure-subset skill evolution
Stage 6: v0012-v0015 style assisted evaluation
Stage 7: ablation / transfer / no-exact checks
Stage 8: final report and release package
```

每个阶段都要记录：

- command；
- config；
- job directory；
- active skill version；
- model / agent / dataset；
- timeout / tools / concurrency；
- resolved count；
- errors / timeout / no-diff；
- selected skills；
- result table。

## 3. Stage 0：环境与配置检查

目标：确认环境、模型 API、PiAgent、E2B、SWE-Bench harness 都可用。

### 3.1 激活环境

```bash
source /root/miniforge3/etc/profile.d/conda.sh
conda activate base
python --version
```

验收：

```text
Python 可用；当前环境为 base。
```

### 3.2 检查 API 兼容

```bash
python scripts/check_openai_compat.py
python scripts/check_tinker_models.py
```

验收：

```text
GLM-5.1 provider 可调用；
OpenAI-compatible API 或 tinker provider 配置正确。
```

### 3.3 PiAgent smoke test

```bash
python scripts/run_benchmark.py \
  --no-skills \
  --n-tasks 1 \
  --job-name smoke_pi_noskills
```

验收：

```text
能启动 Harbor / E2B；
PiAgent 能进入 SWE-Bench task sandbox；
result.json 和 verifier/report.json 正常生成。
```

## 4. Stage 1：No-Skills Full-set Baseline

目标：建立固定 no-skills 基线，作为所有 skill-assisted 结果的参照。

命令模板：

```bash
python scripts/run_skill_evo_verified.py \
  --run-name skill_evo_verified_glm51_full_v0001 \
  --dataset swe-bench/swe-bench-verified@2 \
  --provider openai \
  --all-verified \
  --concurrency 20 \
  --agent-timeout-sec 900 \
  --skip-training \
  --skip-eval
```

实际执行可以按 5 个 shard 跑：

```text
001-100
101-200
201-300
301-400
401-500
```

产物：

- `jobs/*_baseline_noskills/`
- `analysis/evolution/*/evaluation/score_report.md`
- `analysis/evolution/*/evaluation/score_report.json`

验收：

```text
No-Skills full-set resolved >= 370/500。
```

当前已完成：

```text
No-Skills = 370/500 = 74.0%
```

## 5. Stage 2：Public SWE Skills Baseline

目标：提供静态 skill 对照，回答“公共 SWE skills 是否已经足够”。

设置：

- 使用导师提供或公共可获取的 SWE skills 目录；
- 不允许写入 test task-specific hints；
- 不允许使用 no-skills trajectory 生成的新 skills；
- 只作为 static skill baseline。

命令模板：

```bash
python scripts/run_benchmark.py \
  --use-skills \
  --n-tasks 500 \
  --job-name verified_glm51_public_swe_skills \
  --dataset swe-bench/swe-bench-verified@2
```

如果 public skills 目录需要显式指定，则固定环境变量：

```bash
export PI_SKILLS_ROOT=/path/to/public_swe_skills
export PI_USE_SKILL_HARNESS_MEMORY=false
```

验收：

```text
得到 Public SWE Skills resolved count；
同 No-Skills 使用相同 model / agent / dataset / timeout / tools / concurrency。
```

注意：

```text
Public SWE Skills 不等于 StageSkill evolution；
它只回答静态公共技能是否能提升 PiAgent。
```

## 6. Stage 3：Trace Collection and Skill Generation

目标：从 no-skills trajectories 生成五阶段 StageSkill memory。

输入：

- no-skills job directories；
- `agent/trajectory.json`；
- `agent/sharegpt.json`；
- `verifier/report.json`；
- `result.json`。

生成：

```text
skills/tasks/<version>/<repo>/<task>/<stage>/<skill>/SKILL.md
```

五阶段：

```text
reproduce
localize
edit
validate
recover
```

命令模板：

```bash
python scripts/update_skill_harness_memory.py \
  --job-dir jobs/<noskills_job_dir> \
  --summarize-with-backbone
```

或者使用集成 runner：

```bash
python scripts/run_skill_evo_verified.py \
  --run-name verified_glm51_round1 \
  --dataset swe-bench/swe-bench-verified@2 \
  --provider openai \
  --all-verified \
  --concurrency 20 \
  --summarize-with-backbone \
  --skip-baseline
```

验收：

- 每个 task 生成五阶段 skill card；
- 没有 sandbox/private path leakage；
- skills 不能包含 gold patch；
- `skills/tasks/VERSIONS.json` 中 active version 正确；
- skill memory 可被 PiAgent 检索。

## 7. Stage 4：One-shot StageSkill Evaluation

目标：复现 v0001 设定，验证 naive one-shot 是否有效。

设置：

- 使用 Stage 3 生成的 first-round StageSkill；
- 对同一 full set 或 shard 做 skill-assisted evaluation；
- 与 No-Skills 对比。

当前结果：

```text
v0001: 356/500，比 No-Skills 低 14 题。
```

结论：

```text
one-shot skill generation 不是最终方法；
它是必要的负结果，用来说明需要 evolution / reward policy / bounded edit。
```

报告中要写：

- 哪些 shard 回退；
- 哪些 stage 容易产生 harmful hints；
- generic localize/edit advice 是否造成错误；
- validate/recover 是否缺失。

## 8. Stage 5：Failure-subset Skill Evolution

目标：只针对 no-skills 失败任务进行定向 skill-assisted rerun 和 policy update。

输入：

- no-skills full-set 结果；
- unresolved / error / timeout task list；
- 当前 skill version；
- reward policy；
- curator policy。

流程：

```text
1. 从 No-Skills full set 中抽取失败任务。
2. 用当前 skill version 在失败任务上 rerun。
3. 统计 newly resolved。
4. 根据 transition 生成 policy update：
   - 0 -> 1: preserve / distill
   - 1 -> 0: delete / disable / major rewrite
   - 0 -> 0: targeted rewrite
   - error -> 1: preserve validate/recover guard
5. 生成下一版 skills/tasks/vXXXX。
```

命令参考：

```bash
python scripts/run_skill_evo_from_noskills_iterations.py \
  --baseline-job-dir jobs/<full_noskills_job_dir> \
  --skill-version-id v0012 \
  --concurrency 10
```

或者对已有 version 只做评估：

```bash
python scripts/run_skill_evo_eval_only.py \
  --baseline-job-dir jobs/<full_noskills_job_dir> \
  --skill-version-id v0015 \
  --use-skills
```

验收：

```text
newly resolved > 0；
regression 不进入 full-set aggregate；
每个新增 resolved 都能追溯到 verifier/report.json。
```

当前已完成：

| Version | Newly Resolved on Failure Subset | Aggregate |
| --- | ---: | ---: |
| v0012 | +17 | 387/500 |
| v0013 | +17 | 387/500 |
| v0014 | +23 | 393/500 |
| v0015 | +24 | 394/500 |

## 9. Stage 6：Final Skill-assisted Evaluation

目标：形成最终可报告的 skill-assisted score。

当前主结果：

```text
No-Skills full set: 370/500
v0015 resolves 24 additional no-skills failures
Final aggregate: 394/500
```

报告格式：

| Setting | Resolved | Rate | Delta |
| --- | ---: | ---: | ---: |
| No-Skills | 370/500 | 74.0% | +0 |
| Public SWE Skills | TBD | TBD | TBD |
| One-shot StageSkill v0001 | 356/500 | 71.2% | -14 |
| StageSkill-Evo v0012 | 387/500 | 77.4% | +17 |
| StageSkill-Evo v0013 | 387/500 | 77.4% | +17 |
| StageSkill-Evo v0014 | 393/500 | 78.6% | +23 |
| StageSkill-Evo v0015 | 394/500 | 78.8% | +24 |

注意写法：

```text
v0015 是 failure-subset assisted aggregate，不是完全独立重跑 500 题的 no-exact transfer 结果。
```

## 10. Stage 7：Ablation 与风险控制

至少做以下三组。

### 10.1 Same-budget Retry

目的：排除“只是第二次机会”的解释。

设置：

- 对 no-skills 失败子集给同等第二次机会；
- 不注入 StageSkill；
- 可注入上一轮失败 summary，但不能注入结构化五阶段 skill；
- 比较 newly resolved。

验收：

```text
StageSkill-Evo newly resolved 明显高于 same-budget retry。
```

### 10.2 Public SWE Skills

目的：证明自动生成 / 演化 skills 相比静态公共 skills 有增益。

设置：

- 同一 GLM-5.1；
- 同一 PiAgent；
- 同一 SWE-Bench Verified；
- 同一 timeout / concurrency。

验收：

```text
Public SWE Skills 结果完整记录；
若效果弱，说明静态 skills 不足；
若效果强，则 StageSkill-Evo 必须超过或解释互补收益。
```

### 10.3 Stage Ablation

目的：证明五阶段结构不是装饰。

优先顺序：

```text
Flat memory
Remove validate
Remove recover
No exact task memory
```

验收：

- `Remove validate` 若下降，说明 focused validation 有价值；
- `Remove recover` 若下降，说明从 no-diff / timeout / wrong localization 恢复是关键；
- `No exact task memory` 用于区分 task-conditioned adaptation 与 true transfer。

## 11. Stage 8：最终报告结构

最终报告按这个结构写：

1. Problem：SWE agent 是一次性求解器，轨迹经验没有稳定复用。
2. Method：Trace2Skill / StageSkill，把轨迹转成五阶段 skills。
3. Baselines：No-Skills、Public SWE Skills、One-shot StageSkill。
4. Evolution：failure-subset rerun + reward/curator policy + bounded edits。
5. Results：370/500 -> 394/500。
6. Negative result：v0001 one-shot skills 低于 no-skills。
7. Ablation：same-budget retry、public skills、stage removal。
8. Failure analysis：错误检索、generic skill、stale path、validate/recover 缺失。
9. Limitations：当前主结果是 task-conditioned / failure-subset assisted aggregate，不是 no-exact held-out transfer。

## 12. 每一步产物 Checklist

| 阶段 | 必须产物 |
| --- | --- |
| Stage 0 | 环境检查日志、provider check 日志 |
| Stage 1 | no-skills job dirs、score report、500 task result table |
| Stage 2 | public skills job dirs、score report |
| Stage 3 | `skills/tasks/<version>`、memory JSON、skill samples |
| Stage 4 | one-shot StageSkill score report |
| Stage 5 | failure-subset task list、v0012-v0015 jobs、newly resolved table |
| Stage 6 | final aggregate table |
| Stage 7 | ablation tables |
| Stage 8 | final report、失败案例、复现命令 |

## 13. 最小验收标准

项目通过需要满足：

```text
1. No-Skills full set >= 74%。
2. Skill-assisted final aggregate > No-Skills。
3. 至少有 No-Skills、Public SWE Skills、One-shot StageSkill、StageSkill-Evo 四组结果。
4. 能解释 v0001 为什么失败，以及 v0012-v0015 为什么提升。
5. 所有结果有 job dir / verifier report 可追溯。
```

当前状态：

```text
No-Skills: 370/500, achieved.
One-shot StageSkill v0001: 356/500, achieved as negative result.
StageSkill-Evo v0015: 394/500, achieved.
Public SWE Skills: still needs full evaluation if not already available.
Same-budget retry / no-exact transfer: still needed for stronger paper claim.
```
