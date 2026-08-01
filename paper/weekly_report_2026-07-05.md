# Weekly Report - 2026-07-05

## 本周与上周的不同点

| 项目 | 上周 | 本周 |
| --- | --- | --- |
| Skill library 使用 | 主要使用 frozen SWEGym skill library | 增加 test-time skills scaling：writer 从 benchmark trace 中 提 evidence，进而形成，repo skills，successful & failurere experience，evaluator 在无 verifier 输入下裁决 这些 candidate |
| Verifier 边界 | 容易混淆“verifier 用于进化”和“verifier 用于报告” | 明确：test-time evolution 不向 writer/evaluator 输入 verifier；verifier 只在 gate 完成后报告 recovered，并构造下一轮 unresolved subset，并且这部分实验值作为 test time skill scaling，不作为主实验结果。 |
| 主实验设置 | 存在 在benchmarkark 上 进行ev |明 确：主实验是从 swegym 上范化出来的 通用 swe skills （上周提到的 failure-mode skills） 直接用于swebench verified 评测 （对应下表的 frozen swegym skill library） |


## 最新实验结果

当前完成的 GLM-5.2 / Pi / SWE-Bench Verified 结果如下：

| Setting | Trials | Resolved | Rate  | 说明 |
| --- | ---: | ---: | ---: | --- |
| No Skills baseline | 500 | 368 | 73.6%  | 不使用 skill library |
| Frozen SWEGym skill。library | 500 | 376 | 75.2% | 直接使用 frozen downstream skills |
| TTS Gate 1 full eval | 500 | 379 | 75.8% | 初始 online gate，全量评测 |
| TTS Gate 2 subset-composed | 500 | 410  | 82.0%  | 第一轮 TTS EVO |
| TTS Gate 3 subset-composed | 500 | 423  | 84.6%  |  第二轮 TTS EVO|

## SWEGym Val Gate Resolve Rate

![SWEGym validation gate resolve rate](../run_logs/swegym_skill_evo/swegym_novita_glm52_c15_resume_merged_20260630_071956/validation/plots/validation_progress_to_accepted_v0040_projected_15_to_69_8.svg)

当前运行状态：

- 八轮控制器已启动，目标为 gates 2..8。
- Gate 2 已完成：额外 31 unresolved = 90。
- Gate 3 已完成：额外 13 unresolved = 77。
- Gate 4 已从 77 个 unresolved tasks 开始运行。
- 之前的 Gate 2 full-500 rerun 不作为主结果，因为它不符合 subset-composition 协议。

## 本周产出

- 明确并实现了 downstream test-time skill evolution 的 subset-composition 计分方式。
- 启动了 SWE-Bench Verified 上的八轮 TTS evolution。
- 将论文中的 downstream protocol、current pilot result、limitation 和 implementation notes 同步到 `paper/main.tex`。
- 新增本周周报，记录协议变化、修改原因和最新实验结果。

## 下周建议

1. 等待 Gate 4..8 完成，补齐 cumulative recovery 曲线。
2. 对 recovered cases 做定性分析：哪些 failure-mode/session skills 真正起作用。
3. 只做 main results 时，优先稳定 GLM-5.2 / Pi 的完整曲线，再扩展到其他 model/harness cell。
