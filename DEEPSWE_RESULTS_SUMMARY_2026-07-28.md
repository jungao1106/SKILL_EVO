# DeepSWE 结果汇总（GLM-5.2 / Claude Code）

> 汇总所有 DeepSWE 重跑结果。模型统一 GLM-5.2，Agent 统一 Claude Code。
> DeepSWE 数据集共 114 个任务；完整 run 为 113 题（1 题因环境问题常被跳过）。

## 关键结论

1. **没有完整的 No-Skills baseline（113 题）run**。现有 no-skills 结果只有两个小规模 run：small10（2/10）和 gapfill29（6/29），且 gapfill29 的任务集与主 113 题不重叠。**baseline 缺失是当前 DeepSWE 最大的数据缺口。**

2. **Frozen / TTS 的多次完整 113 题重跑，resolve 数差异很大（21 → 26 → 33 → 35）**，但任务级一致性低（三次 frozen 重跑都解的只有 7 个，并集 52 个）。差异主要来自 **verifier 基础设施的逐步修复**（preserve venv → isolate verifier → persist offline toolchains），而非技能效果变化。每次 infra 修复都让更多任务能正确通过验证。

3. 因此 DeepSWE 上的 frozen-vs-baseline 对比、TTS 增益，**目前都不可作为结论性数字**，需在最终 infra 上重跑 baseline + frozen + TTS 三段对齐后才能定稿。

## 一、Frozen SWEGym 技能库（v0201）完整 113 题重跑

均为 `use_skills=True`，技能库 v0201，全量 113 题。

| Run | 日期 | Infra 状态 | Resolved | Rate |
| --- | --- | --- | ---: | ---: |
| frozen_downstream | 07-11 | 原始 | 21 / 113 | 18.6% |
| infrafix_frozen | 07-14 | +preserve venv | 26 / 113 | 23.0% |
| infraiso_frozen | 07-15 | +isolate verifier | 33 / 113 | 29.2% |

> 趋势：infra 每修一层，resolve 上升一档。说明早期低分很大程度是 verifier/环境问题，不是 agent 能力问题。

## 二、TTS Gate 1 全量评测（113 题，技能库 v0201 + test-time 候选）

| Run | 日期 | Provider | Infra 状态 | Resolved | Rate |
| --- | --- | --- | --- | ---: | ---: |
| tts_evo gate001 | 07-11 | novita | 原始 | 13 / 88* | — |
| infrafix_tts gate001 | 07-14 | novita | +preserve venv | 35 / 113 | 31.0% |
| a5ba7ae_tts gate001 | 07-16 | novita | +persist toolchains | 35 / 113 | 31.0% |
| macaron_tts gate001 | 07-16 | macaron | +persist toolchains | 25 / 113 | 22.1% |

*07-11 那次只完成 88/113，不完整。

> novita 线在 infra 修复后稳定在 35/113；macaron 线（不同 provider）为 25/113。

## 三、TTS 多轮 gate 累计（subset-composition 口径）

### Macaron 线（最完整的 gate 序列）

| Gate | 本轮 subset recovered | 累计 resolved | 累计 rate |
| ---: | ---: | ---: | ---: |
| Gate 1（全量 113） | 25 | 25 / 113 | 22.1% |
| Gate 2 | 19 | 44 / 113 | 38.9% |
| Gate 3 | 6 | 50 / 113 | 44.2% |

### Novita 线（a5ba7ae，infra 修复后）

| Gate | 本轮 subset recovered | 累计 resolved | 累计 rate |
| ---: | ---: | ---: | ---: |
| Gate 1（全量 113） | 35 | 35 / 113 | 31.0% |
| Gate 2 | 2 | 37 / 113 | 32.7% |

> Novita 线 Gate 2 仅 recovered 2，且后续 gate 未跑完；Macaron 线跑到了 Gate 3（累计 50/113）。
> 注意：subset-composition 口径与全量口径不同，单列、不计入主实验对比。

## 四、No-Skills baseline（小规模，非完整）

| Run | 规模 | Resolved | Rate |
| --- | --- | ---: | ---: |
| small10_noskills (07-14) | 10 题 | 2 / 10 | 20.0% |
| gapfill29_noskills (07-16) | 29 题（与主 113 题不重叠） | 6 / 29 | 20.7% |

> 两个小规模 baseline 都在 ~20%，但规模太小、任务集不对齐，**不能作为 113 题完整 baseline 使用**。

## 五、Smoke / 调试 run（不作为结果）

| Run | 规模 | Resolved | 说明 |
| --- | --- | ---: | --- |
| small10_skills (07-12) | 10 题 | 0 / 9 (err=9) | infra 未修复，全错 |
| small10_skills (07-14) | 10 题 | 3 / 10 | infra 修复后 |
| small10_noskills (07-12) | 10 题 | 0 / 9 (err=9) | infra 未修复 |
| fixtest_noskills / skills | 5 题 | 1 / 5 | 调试用 |
| quarantine_force_internet | 113 题 | 0 / 0 (err=15) | 隔离实验，全失败 |

## 数据缺口与下一步

1. **补齐 No-Skills baseline 完整 113 题**（在最终 infra 上）——当前最大缺口。
2. **在最终 infra 上重跑 frozen + TTS Gate1**，与 baseline 三段对齐，才能给出 frozen 增益与 TTS 增益的结论性数字。
3. 当前可报告的稳定数字：novita 线 infra 修复后 frozen/TTS-Gate1 ≈ 33–35/113（29–31%）；macaron 线 TTS 累计到 Gate3 = 50/113（44%，subset 口径）。
4. 多次重跑任务级一致性低，需排查 verifier 噪声，必要时固定 verifier 版本后重跑。
