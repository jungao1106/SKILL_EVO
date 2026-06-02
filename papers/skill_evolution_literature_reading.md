# Skill Evolution 文献阅读 v5


- [2602.08234v1.pdf](2602.08234v1.pdf): **SkillRL: Evolving Agents via Recursive Skill-Augmented Reinforcement Learning**
- [2605.06614v1.pdf](2605.06614v1.pdf): **SkillOS: Learning Skill Curation for Self-Evolving Agents**
- [2605.23904v2.pdf](2605.23904v2.pdf): **SkillOpt: Executive Strategy for Self-Evolving Agent Skills**

## 一句话结论

三篇论文都把 skill 视为 agent 能从经验中进化的外部过程知识，但训练对象不同。SkillRL 训练“会使用 skill 的 agent policy”，并用 teacher 在 RL 中递归扩展 SkillBank；SkillOS 训练“管理 SkillRepo 的 skill curator”；SkillOpt 不训练 agent 或 curator，而是像优化器一样稳定地优化 skill 文档。

对当前 SKILLS_EVO 最有价值的方向是：短期采用 SkillOpt-style 的候选版本、验证门和 rejected buffer；中期引入 SkillOS-style 的 grouped task 和 operation curator；长期如果要训练模型本身使用 skills，再考虑 SkillRL-style cold-start SFT + skill-augmented RL。

## 1. SkillOS 精读

### 1.1 研究问题

SkillOS 面向 streaming tasks：agent 不断遇到新任务，不能每次从零开始，而应该从过去 trajectory 中沉淀 reusable skills，用于后续相关任务。论文认为瓶颈不在于“是否能写 skill”，而在于“如何持续管理 skill collection”：什么时候新增、什么时候更新、什么时候删除，以及这些操作是否真的能帮助未来任务。

### 1.2 系统结构

SkillOS 把系统拆成两个角色：

- Frozen Agent Executor：负责解决当前任务。它从 SkillRepo 中检索相关 skills，并在执行时使用。
- Trainable Skill Curator：负责在任务结束后读取 trajectory、结果和相关旧 skills，然后调用结构化操作更新 SkillRepo。

SkillRepo 中的 skill 使用 Markdown 形式，包含 YAML frontmatter 和正文说明。论文中的 curator 可执行三类操作：

- `insert_skill`: 写入新的 skill。
- `update_skill`: 修改已有 skill。
- `delete_skill`: 删除无效、冗余或有害 skill。

这个结构和我们当前的 `skills/tasks/<version>/<repo>/<task>/<stage>/<skill>/SKILL.md` 是兼容的，但 SkillOS 更强调 repo 级持续管理，而不是每次只追加一个新版本。

### 1.3 训练实例：related task groups

SkillOS 的关键设计是把训练样本组织成一组相关任务，而不是独立任务。组内任务按顺序执行：

1. 初始 SkillRepo 为空。
2. executor 执行第一个任务。
3. curator 根据 trajectory 更新 SkillRepo。
4. 后续相关任务使用更新后的 SkillRepo。
5. 用后续任务表现来评价前面 curation 是否有用。

这解决了 skill curation 的 delayed feedback 问题：一个 skill 是否有价值，不应该只看它是否“看起来合理”，而要看它是否提升未来相关任务。


### 1.4 Reward 设计

SkillOS 使用 GRPO 训练 curator，reward 由四部分组成：

- Task outcome reward：后续任务是否成功，衡量 skill 对未来任务的实际帮助。
- Function call reward：curator 的 insert/update/delete 是否格式合法、能否成功执行。
- Content quality reward：外部 judge 判断 skill 是否抽象、可复用、可执行、忠实于 trajectory。
- Compression reward：惩罚把整段 trajectory 原样塞进 SkillRepo，鼓励 concise skill。

这对我们非常直接：当前 SKILLS_EVO 的 summarizer 已经会基于 trace 生成 task/stage skills，但还缺少严格的 curation reward。至少可以先做非 RL 版本：

- outcome：用 SWE-Bench reward 或 smoke task reward。
- validity：检查 JSON schema、SKILL.md frontmatter、脚本语法、路径结构。
- quality：用 backbone model judge skill 的 abstraction、actionability、faithfulness。
- compression：限制 token/文件大小，避免把 trace 变成长日志。

### 1.5 实验结论

论文在 ALFWorld、WebShop 和 reasoning tasks 上验证。主要结论：

- SkillOS 相比 no memory 和 memory-based baselines 整体更强。
- 在 agentic tasks 上收益更大，因为 procedural regularities 更明显。
- RL-trained 8B curator 甚至能超过直接用 Gemini-2.5-Pro 做 curator，说明 skill curation 不是单纯模型越大越好，而是要和 executor 行为对齐。
- grouped task stream 和 auxiliary rewards 都重要。去掉 grouping 降幅最大，说明 skill 必须在相关任务链上验证。
- curator 行为会从早期大量 insert，逐渐转向 update/delete，说明成熟的 SkillRepo 不是无限追加，而是持续压缩、重写和去噪。

### 1.6 对 SKILLS_EVO 的启发

当前我们已经有 versioned memory 和 task/stage skill，但更像 SkillOS-base 或 one-shot curator。下一步可以引入 SkillOS 式操作层：

- 把每次 summary 输出从“完整 task_stage_skills”改成结构化 operations：
  - insert stage skill
  - update stage skill
  - delete stale stage skill
  - attach/update script resource
- 每个 operation 都记录 `reason`、`source_trace`、`expected_future_use`。
- 只有通过 validation gate 的 operation 才进入 active version。
- 未通过的 operation 进入 rejected buffer，后续 summarizer 读取，避免重复犯错。

## 2. SkillOpt 精读

### 2.1 研究问题

SkillOpt 认为 skill 文档本身应该像模型权重一样被优化。不同于 SkillOS 管理一个 SkillRepo，SkillOpt 更聚焦于一个 skill artifact 的受控训练：冻结 target model 和 harness，只优化外部 skill 文档。

论文的核心观点是：skill optimization 不应该是 loose self-revision，而应该具备类似深度学习优化器的纪律性。

对应关系如下：

| 深度学习概念 | SkillOpt 中的文本优化概念 |
| --- | --- |
| parameter | skill document |
| gradient direction | trajectory-derived edit direction |
| learning rate | textual edit budget |
| validation check | held-out validation gate |
| training schedule | batch/minibatch/schedule/gate |
| optimizer memory | rejected-edit buffer + meta skill |

### 2.2 优化流程

SkillOpt 的 loop 可以概括为：

1. 用当前 skill 让 frozen target model 在 train split 上 rollout。
2. 收集 trajectories、tool calls、observations、command outputs、verifier feedback。
3. optimizer model 对成功和失败轨迹做 minibatch reflection。
4. 生成结构化 add/delete/replace edits。
5. 合并、去重、排序 edits。
6. 根据 textual learning rate `Lt` 只应用前 `Lt` 个 edits。
7. 在 selection split 上验证 candidate skill。
8. 只有严格提升 validation score 才接受，否则进入 rejected-edit buffer。
9. 每个 epoch 做 slow/meta update，总结哪些 edit pattern 有效、哪些失败，但 meta 只给 optimizer 用，不部署给 executor。

这个设计比“总结 trace 然后立刻写入 active memory”稳得多，因为它有明确的 propose-test-reject 闭环。

### 2.3 关键机制

**Bounded text updates**

每一步只允许有限数量的 add/delete/replace。这个文本学习率防止模型一次性大改 skill，避免抹掉已有有用规则，也降低 overfit。

**Held-out validation gate**

candidate skill 必须在 selection split 上严格超过当前 skill 才会被接受。tie 也拒绝。这个策略很保守，但能保证 active skill 不会悄悄漂移。

**Rejected-edit buffer**

失败的 edit 不直接丢弃，而是记录失败模式、尝试过的 edit 和分数下降。后续 optimizer 会读这个 buffer，避免重复提出同样无效的修改。

**Slow/meta update**

fast update 学当前 batch；slow/meta update 学跨 epoch 的长期趋势。meta skill 只作为 optimizer-side memory，不进入部署给 agent 的 skill。

**Harness-agnostic deployment**

SkillOpt 通过 adapter 把 direct chat、Codex、Claude Code 等 harness 统一成“skill in, scored trajectory out”。这点和 SKILLS_EVO 的 Harbor/PiAgent/E2B 架构高度相符。

### 2.4 实验结论

论文报告 SkillOpt 在 6 个 benchmark、7 个 target model、3 种 execution harness 上评测：

- 在 52 个 model/benchmark/harness cell 上 best or tied-best。
- GPT-5.5 direct chat 平均相对 no-skill 提升 +23.5。
- Codex harness 上 GPT-5.5 平均提升 +24.8。
- Claude Code harness 上 GPT-5.5 平均提升 +19.1。
- learned skills 可以跨 model、跨 harness、跨相近 benchmark transfer。
- 最终 `best_skill.md` 通常很短，约 300 到 2000 tokens，说明有效 skill 不需要无限膨胀。

Ablation 显示最重要的是：

- bounded edit budget
- validation gate
- rejected-edit buffer
- slow/meta update

batch size 和 schedule 影响相对小，说明核心收益来自“受控文本优化闭环”，不是某个脆弱超参。

### 2.5 对 SKILLS_EVO 的启发

SkillOpt 对当前项目的启发比 SkillOS 更工程化，尤其适合我们现在的 minimal inference scaffold：

- 不要让新 summary 自动成为 active。应该先生成 candidate version。
- 用一个小 selection set 跑 PiAgent + E2B verifier，只有 reward 改善才激活。
- 每次 memory update 应记录 edit report：
  - proposed edits
  - applied edits
  - skipped edits
  - validation score before/after
  - rejection reason
- task/stage skill 的更新应使用 bounded operations，而不是重写整个目录。
- generated scripts 必须进入 validation：
  - Python: `compile()` 和可选 smoke run。
  - Shell: `bash -n`。
  - SWE helper: 不允许硬编码本次 sandbox 绝对路径或 secret。
- optimizer-side meta memory 和 deployed skill memory 应分离。前者帮助生成技能，后者才注入 PiAgent。

## 3. SkillRL 精读

### 3.1 研究问题

SkillRL 关注的问题不是“怎样训练一个 curator 管理 skills”，而是“怎样让 agent policy 真正学会使用 skills，并在 RL 中和 SkillBank 一起进化”。它批评传统 memory-based agent 直接存 raw trajectories：轨迹冗长、噪声大、token 成本高，而且 agent 并不会因此学会抽象复用经验。

SkillRL 的核心主张是：经验迁移需要抽象。成功和失败轨迹都应该被 teacher model 蒸馏成结构化 skills，再用这些 skills 指导 policy learning。

### 3.2 整体流程

SkillRL 分成四步：

1. **Experience-based skill distillation**
   - 用 base model 在环境中 rollout，收集成功轨迹 `T+` 和失败轨迹 `T-`。
   - teacher model 分别处理两类轨迹。
   - 成功轨迹提取成功策略、关键决策点、可泛化行为模式。
   - 失败轨迹提取 failure lesson：失败点、错误推理/动作、应该怎么做、如何避免类似失败。

2. **Hierarchical SkillBank construction**
   - 把蒸馏结果组织成两层 SkillBank：
     - `General Skills Sg`: 所有任务通用的探索、状态管理、目标跟踪、错误恢复等。
     - `Task-Specific Skills Sk`: 按任务类别组织的专门技能，例如 ALFWorld 的 heat/cool/clean/look，WebShop 的商品类别。
   - 每个 skill 包含 concise name、principle、when to apply。

3. **Cold-start SFT**
   - 论文认为 base model 不会天然读 skill、用 skill。
   - 所以先让 teacher 生成 skill-augmented reasoning traces，显式展示如何检索、解释、应用 skills。
   - 然后对 base model 做 SFT，得到能使用 skills 的初始 policy。

4. **Recursive skill-augmented RL**
   - SFT 后用 GRPO 训练 policy。
   - 每个 task 执行前检索 skills：general skills 总是放入 context，task-specific skills 用 embedding similarity 检索 Top-K。
   - 每隔 validation epoch，检查哪些 task category 成功率低于阈值。
   - 对这些低成功率类别收集失败轨迹，用 teacher 生成新 skills 或 refine existing skills，再加入 SkillBank。

因此 SkillRL 的 evo 是：

```text
policy improves -> 遇到新失败模式 -> teacher 扩展 SkillBank -> policy 用新 SkillBank 继续 RL
```

### 3.3 训练对象和 reward

SkillRL 训练的是 **agent policy `πθ`**，不是 curator。

SkillBank 会变化，但变化来自 teacher model 对 validation failures 的分析，而不是一个被 RL 训练出来的 curator policy。

RL reward 很简单：

```text
Ri = r(trajectory) ∈ {0, 1}
```

也就是 task success binary reward。GRPO 对同一 task 下多条 sampled trajectories 算相对 advantage。这个 reward 和 SkillOS 的 composite curator reward 不同；SkillOS 的 reward 是为了训练 curator，所以包含 function-call validity、content quality、compression 等辅助项。

### 3.4 Skill retrieval

SkillRL 的 retrieval 是层次化的：

- `General Skills Sg` 总是包含，作为基础策略。
- `Task-Specific Skills Sk` 通过 task description 和 skill embedding 相似度检索：

```text
Sret = TopK({s in Sk : sim(ed, es) > delta}, K)
```

论文实现中：

- task-specific Top-K retrieval: `K = 6`
- collection/evolution threshold: `delta = 0.4`
- validation interval: 每 5 steps
- max new skills per evolution: `3`

### 3.5 实验设置

Base model:

```text
Qwen2.5-7B-Instruct
```

Teacher model:

```text
OpenAI o3
```

评测任务：

- ALFWorld
- WebShop
- 七个 search-augmented QA：
  - NQ
  - TriviaQA
  - PopQA
  - HotpotQA
  - 2Wiki
  - MuSiQue
  - Bamboogle

Search QA 中，SkillRL 主要在 NQ 和 HotpotQA 上训练，再测 in-domain 和 OOD。

训练超参：

- Cold-start SFT:
  - learning rate `1e-4`
  - batch size `16`
  - epochs `3`
  - SFT examples: ALFWorld `7500`，WebShop `2400`
- RL:
  - GRPO
  - learning rate `1e-6`
  - batch size `64`
  - KL coef `0.01`
  - epoch/steps `150`
  - max prompt length `6000`
  - max response length `1024`

### 3.6 实验结论

主结果：

- ALFWorld overall:
  - GRPO: `77.6`
  - SkillRL: `89.9`
- WebShop success:
  - GRPO: `66.1`
  - SkillRL: `72.7`

论文强调，因为 SkillRL 也是基于 GRPO，ALFWorld 上从 `77.6` 到 `89.9` 的提升主要来自 skill augmentation，而不是优化器不同。

Ablation：

- 完整 SkillRL: ALFWorld `89.9`, WebShop `72.7`
- 去掉 hierarchical structure: `76.8`, `61.4`
- 用 raw trajectories 替代 Skill Library: `61.7`, `50.2`
- 去掉 Cold-start SFT: `65.2`, `46.5`
- 去掉 Dynamic Evolution: `84.4`, `70.3`

结论：

- distilled skill 明显优于 raw trajectory memory。
- cold-start SFT 非常关键，否则 base policy 不会稳定使用 skills。
- hierarchical general + task-specific skills 很重要。
- dynamic evolution 有正收益，但在这篇 ablation 里不是最大项。

### 3.7 对 SKILLS_EVO 的启发

SkillRL 对当前 SKILLS_EVO 的启发主要是长期方向，不是最小推理脚手架的第一步。

短期可以借鉴：

- 成功轨迹和失败轨迹都要总结。失败不只是丢掉，而是生成 recover/localize/validate 的 failure lessons。
- 区分“基础阶段技能”和“task-specific 技能”。不过在 SWE 中不应该做全局泛化污染，可以落成 repo/task/stage 层级。
- 检索时不要只看 task title，可以用 issue text、touched paths、test commands、failure signature 做 embedding/BM25 混合检索。
- 脚本和 stage skill 也应被检索，而不是只注入 memory prompt。

长期如果要训练模型：

- 先做 skill-augmented traces，教 backbone 如何读 `SKILL.md`、何时调用 helper script、何时 stop/recover。
- 再用 GRPO/其他 RL 在 SWE-Bench style reward 上训练 policy。
- 但这会改变当前“只做 inference scaffold”的边界，工程成本远高于 SkillOpt-style gate。

## 4. 三篇论文横向对比

| 维度 | SkillRL | SkillOS | SkillOpt | 对 SKILLS_EVO 的建议 |
| --- | --- | --- | --- | --- |
| 优化对象 | skill-augmented agent policy | SkillRepo 管理策略 | 单个 skill 文档 | 短期不要训练 policy，先优化 task/stage skill artifact |
| agent executor | 训练，经过 SFT + RL | 冻结 | 冻结 | 保持 PiAgent/backbone 固定，避免混入 executor 训练变量 |
| curator/optimizer | teacher model 生成/扩展 SkillBank | RL-trained curator | frontier optimizer model | 先用 backbone/Novita GLM5.1 做非 RL optimizer，再积累数据 |
| 更新方式 | validation failure 触发新增/refine skills | insert/update/delete | bounded add/delete/replace | 统一成 skill operation schema |
| 反馈 | task success binary reward 训练 policy | 后续相关任务表现 + auxiliary rewards | held-out validation score | 用 SWE-Bench verifier reward 做 gate |
| 记忆 | hierarchical SkillBank: general + task-specific | SkillRepo | current/best/rejected/meta | SWE 中落成 repo/task/stage-specific，不做混乱全局技能 |
| 防膨胀 | skill distillation 压缩 raw trajectories | compression reward | edit budget + compact artifact | 限制每个 stage skill 长度和脚本数量 |
| 失败利用 | failure trajectory 蒸馏 failure lessons | curator 根据 trajectory 更新 repo | rejected-edit buffer 作为 negative feedback | recover/localize/validate 应吸收失败 rollout |
| 泛化 | 跨任务和 OOD search QA | 跨 executor/跨任务 | 跨 model/跨 harness/跨 benchmark | 评估同 repo related tasks 和跨模型 endpoint transfer |

## 5. 对当前 SKILLS_EVO 的落地方案

### 5.1 当前状态对照

当前 SKILLS_EVO 已经具备这些基础：

- versioned memory: `analysis/skill_harness_memory.json`
- task card: `analysis/task_skills/<version>/<repo>/<task>.json`
- task/stage skill: `skills/tasks/<version>/<repo>/<task>/<stage>/<skill>/SKILL.md`
- optional script resource: `scripts/*.py` or `scripts/*.sh`
- PiAgent 在 `--use-skills` 时可读取 active memory 和 task skill pack。
- summarizer 使用 trace 中记录的 backbone/provider。

这已经接近 SkillOpt 的“external skill state”与 SkillOS 的“frozen executor + skill curator”框架。但还缺少验证闭环。

### 5.2 建议的下一版架构

建议把 evolution memory 拆成四类状态：

```text
analysis/
  skill_harness_memory.json          # active accepted memory
  skill_harness_candidates.jsonl     # candidate proposals
  skill_harness_rejections.jsonl     # rejected edits and score drops
  skill_harness_optimizer_meta.json  # optimizer-side guidance, not injected to PiAgent

skills/
  tasks/
    <active_version>/...
    <candidate_version>/...
```

每次 update 走这个流程：

1. 从一个 job 的 traces 中抽取 task evidence。
2. 对每个 task 生成 stage-level candidate operations。
3. 对 operation 做 schema/compactness/script validity 检查。
4. 应用 bounded edit budget，生成 candidate skill version。
5. 在 selection tasks 上跑 harness verifier。
6. 如果 score 严格提升，激活 candidate version。
7. 如果没有提升，写入 rejected buffer。
8. 定期生成 optimizer meta，只给下一次 summarizer/optimizer 读取。

### 5.3 Operation schema 草案

```json
{
  "operation": "insert_stage_skill | update_stage_skill | delete_stage_skill | attach_script | delete_script",
  "task_id": "matplotlib__matplotlib-14623",
  "repo": "matplotlib",
  "stage": "reproduce | localize | edit | validate | recover",
  "target_skill": "skill-name",
  "patch": {
    "add": [],
    "delete": [],
    "replace": []
  },
  "script_patch": {
    "path": "scripts/helper.py",
    "content": ""
  },
  "evidence": {
    "source_job": "",
    "trace_step_ids": [],
    "reward": 1.0,
    "touched_paths": [],
    "test_commands": []
  },
  "expected_effect": "what future rollout should do better",
  "risk": "what could become overfit or harmful"
}
```

### 5.4 Stage-specific skill 标准

每个 SWE task 的五阶段 skill 应各自回答不同问题：

- reproduce：如何最小化复现，什么现象算复现成功，是否需要 helper script。
- localize：从复现证据到 owner code 的搜索路径，何时停止搜索。
- edit：最小 patch 原则、避免触碰的层、需要保留的 API/行为。
- validate：最小验证命令、回归命令、验证失败时如何判断是 patch 错还是 test 选择错。
- recover：无意义 rollout、重复 grep、反复失败、无 diff、测试环境坏掉时如何复位。

### 5.5 Skill/script 的质量门槛

建议把质量检查变成硬约束：

- `SKILL.md` 必须有 `name` 和 `description` frontmatter。
- 每个 stage skill 必须有 trigger、actions、evidence、stop condition。
- Python 脚本必须 `compile()` 通过。
- Shell 脚本必须 `bash -n` 通过。
- 脚本不能含 API key、绝对私有路径、一次性 sandbox ID。
- 单个 stage skill 默认不超过 1500 tokens。
- 单个 task 默认最多保留 1 到 3 个 distilled scripts。

## 6. 与 SWE-Bench 场景的特殊关系

SWE-Bench 和论文中的 WebShop/ALFWorld/SpreadsheetBench 相比，有几个特点：

- reward 稀疏但可信，最终 verifier reward 比 LLM judge 更强。
- trajectory 很长，必须压缩成 stage evidence，否则 prompt 会爆。
- skill 更容易过拟合到 repo/path，所以需要 repo/task/stage 层级和 validation gate。
- scripts 很有价值，尤其是 reproduction helper、targeted test runner、log parser、patch checker。
- recover 阶段特别重要，因为 SWE agent 常见失败不是不会写代码，而是陷入错误定位、重复搜索、无 diff、无验证。

因此，SKILLS_EVO 不应该追求“总结出一个全局 SWE skill”，而应该追求：

- 每个 task 有 stage-specific skill。
- 每个 stage skill 有明确 stop condition。
- 每次 evolution 有 candidate/active/rejected 版本关系。
- 每个 skill 是否激活由 harness reward 决定。

## 7. 推荐迭代路线

### Phase 1: 非 RL 的 SkillOpt-style gate

目标：不训练 curator，只用 backbone 生成候选 skill，再通过 verifier 接受/拒绝。

要做：

- 加 `candidate_version`。
- 加 `selection_job`。
- 加 `rejected-edit buffer`。
- 加 bounded edit budget。
- 加 edit report。

这是最适合当前 SKILLS_EVO 的下一步，因为工程成本最低，能立刻利用现有 Harbor/E2B/PiAgent harness。

### Phase 2: Task grouping

目标：让 skill 的价值在 related tasks 上被评估，而不是只看原 task。

要做：

- 用 repo、issue keywords、touched paths、test commands、exception/reward 建立 task similarity。
- 每次 evolution 不只读一个 task，而是读一个 related group。
- earlier task 生成的 skill 在 later task 上验证。

这是 SkillOS 最核心的迁移点。

### Phase 3: Curator operation learning

目标：从 one-shot summarizer 进化到 operation curator。

要做：

- 固定 operation schema。
- 收集 accepted/rejected operations。
- 用这些数据做 SFT 或 preference/RL。
- curator 学会 insert/update/delete，而不是只会 append。

### Phase 4: Cross-model/cross-harness transfer

目标：验证 skill artifact 是否真的有迁移性。

可测：

- Novita GLM5.1 生成的 skill 是否帮助 GPT5.5 executor。
- GPT5.5 生成的 skill 是否帮助 GLM5.1 executor。
- PiAgent skill 是否能迁移到 Codex/Claude Code 风格 harness。

## 8. 最重要的工程结论

1. 当前 task/stage-specific 方向是对的，不需要全局 SWE skill。
2. 只追加 skill 会膨胀，必须支持 update/delete/reject。
3. active memory 不能等于 latest generated memory，应该等于 validation-gated best memory。
4. summarizer 的输入不应该是所有 raw traces，而应该是压缩后的 task evidence、trace-selected resources、rejected buffer、optimizer meta。
5. script 是 skill 的一等资源，但必须可执行、可验证、可删除。
6. recover stage 应作为 SWE agent controllability 的核心对象，而不是附属说明。
7. 最小可落地方案是 SkillOpt-style：bounded edits + held-out gate + rejected buffer。

## 9. 建议下一步 PR/实现任务

优先级从高到低：

1. 新增 `scripts/propose_skill_edits.py`：从 traces 生成 operation-level candidate edits。
2. 新增 `scripts/apply_skill_edits.py`：按 bounded budget 生成 candidate version。
3. 新增 `scripts/validate_skill_version.py`：用 selection tasks 跑 Harbor/PiAgent/E2B，输出 before/after reward。
4. 新增 `analysis/skill_harness_rejections.jsonl`：记录被拒绝的 edit 和失败原因。
5. 修改 `update_skill_harness_memory.py`：默认生成 candidate，不直接 activate。
6. 修改 `agents/skill_harness_memory.py`：只读取 active accepted version。
7. 新增 quality judge：检查 abstraction、actionability、faithfulness、compactness、script validity。

这条路线能把当前 SKILLS_EVO 从“trace summarization scaffold”推进到“可验证的 skill evolution scaffold”。
