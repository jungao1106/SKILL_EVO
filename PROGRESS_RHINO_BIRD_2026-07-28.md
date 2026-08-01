# 犀牛鸟项目

> 面向项目会评的进展说明。只讲进展与整体结果，不展开实验设定细节。
> 模型统一为 GLM-5.2。

## 一句话进展

围绕“**为冻结（不可微调）的代码 Agent 在线演化可复用 SWE 技能**”这一目标，已跑通 **SWEGym 训练 → 下游评测 → 在线 test-time 技能演化** 的完整闭环，并在 GLM-5.2 上拿到了持续上升的 resolve 曲线。目前在 SWE-Bench Verified（Pi、Claude Code）与 DeepSWE（Claude Code）三个 cell 上均有 frozen 迁移与 test-time 演化结果。

## 本阶段完成的事

1. **建成统一技能演化框架**：benchmark harness + 远程沙箱、Pi 与 Claude Code 两套 Agent 适配器整合到同一入口，技能库 `--use-skills / --no-skills` 可切换；技能库已版本化沉淀（200+ 版本）。

2. **明确“训练 / 下游 / test-time”三层边界**：
   - **主实验**：从 SWEGym 泛化出的通用 / failure-mode 技能，**冻结后直接用于下游评测**。
   - **test-time 演化**：writer 从 trace 提取 evidence → 形成技能与经验 → evaluator 在**不输入 verifier** 的情况下裁决候选技能；verifier 只在 gate 完成后用于报告并构造下一轮。这部分作为 test-time skill scaling，**不计入主实验结果**。

3. **跑通下游 test-time 多轮演化**：两个 Agent 上均启动了多轮 gate 控制器，累计 recovered 持续累加。

## 当前整体结果（GLM-5.2）

每个 cell 给出三段：**No Skills baseline → Frozen SWEGym 技能库迁移 → Test-time 演化**。SWE-Bench Verified 为 500 题，DeepSWE 为 113 题。

### Cell 1：Pi × SWE-Bench Verified

| 阶段 | Resolved | Rate |
| --- | ---: | ---: |
| No Skills baseline | 368 / 498 | 73.6% |
| Frozen SWEGym 技能库（迁移） | 376 / 495 | 75.2% |
| TTS Gate 1 | 379 / 496 | 76.4% |
| TTS Gate 2 | 410 / 500 | 82.0% |
| TTS Gate 3 | 423 / 500 | 84.6% |
| TTS Gate 4 | 429 / 500 | 85.8% |

### Cell 2：Claude Code × SWE-Bench Verified

| 阶段 | Resolved | Rate |
| --- | ---: | ---: |
| No Skills baseline | 388 / 499 | 77.8% |
| Frozen SWEGym 技能库（迁移） | 403 / 499 | 80.8% |
| TTS Gate 1 | 428 / 500 | 85.6% |
| TTS Gate 2 | 434 / 500 | 86.8% |
| TTS Gate 3 | 452 / 500 | 90.4% |
| TTS Gate 4 | 466 / 500 | 93.2% |

### Cell 3：Claude Code × DeepSWE（113 题）

| 阶段 | Resolved | Rate |
| --- | ---: | ---: |
| No Skills baseline | 62 / 113 | 54.9 |
| Frozen SWEGym 技能库（迁移） | 64 / 113 | 56.6% |
| TTS Gate 1 | 67 / 113 | 59.2% |
| TTS Gate 2 | 68 / 113 | 60.2% |
| TTS Gate 3 | 70 / 113 | 61.9% |


## SWEGym 训练侧验证（held-out）

在 repo-isolated 的 SWEGym holdout 上，accepted skills 相对 no-skill baseline 的 resolve 提升：

- Pi：baseline 10/26 → eval 16/26（**+6**）。
- Claude Code：baseline 16/26 → eval 19/26（**+3**）。

说明从 SWEGym 训练出的技能在 holdout 上有正向迁移。