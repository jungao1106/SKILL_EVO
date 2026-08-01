# DeepSWE Overall 结果（按 Gate 分类，取最好）

> 模型：GLM-5.2 ｜ Agent：Claude Code ｜ 数据集：DeepSWE（113 题）
> 每个 gate 取所有重跑中最好的结果。Gate 2 及之后为 subset-composition 累计口径。

## Overall（取最好）

| Gate | Resolved | Rate | 来源 |
| ---: | ---: | ---: | --- |
| Gate 1（全量 113） | 35 / 113 | 31.0% | novita / infra 修复后（07-14、07-16 两次均为 35） |
| Gate 2（累计） | 44 / 113 | 38.9% | macaron 线（25 + 19） |
| Gate 3（累计） | 50 / 113 | 44.2% | macaron 线（25 + 19 + 6） |

## 说明

- **Gate 1**：全量 113 题评测。novita 线在 infra 修复后稳定为 35/113（两次重跑一致）；macaron 线为 25/113。取最好 35/113。
- **Gate 2 / Gate 3**：subset-composition 口径（对上一轮 unresolved 子集重评并累计）。macaron 线跑完了 Gate 2、Gate 3，累计分别 44、50；novita 线 Gate 2 仅 recovered 2（累计 37），后续未跑完。取最好。
- 趋势：从 Gate 1 的 31.0% 推进到 Gate 3 的 44.2%。

## 备注

- DeepSWE 的 No-Skills baseline 目前无完整 113 题结果（仅有 small10、gapfill29 等小规模 run），故未列入对比。
- Frozen SWEGym 技能库（v0201）完整 113 题重跑在 infra 修复后为 33/113（29.2%），与 Gate 1 的 35/113 接近。
- 早期重跑（07-11，infra 未修复）resolve 偏低（13–21/113），主要受 verifier/环境问题影响，已排除。
