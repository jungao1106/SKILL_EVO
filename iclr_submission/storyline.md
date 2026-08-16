## 1. 为什么需要 skills，但现有的有限——过于 general 或过于 specific

coding agent 每次解题留下执行证据，却被丢弃。回收成技能能让 agent 在不重训的情况下积累经验。
但现有技能两端都失效：

**过于 general。** Trace2Skill 指出手工技能"does not scale"，纯参数知识生成的技能
"miss critical operational pitfalls"；MUSE-Autoskill 批评现有做法把技能当
"isolated and static artifacts"。public skills 能给的只是过程先验（"跑测试""读 traceback"），
可安全复用但不改变行为——agent 本来就会跑测试。

**过于 specific。** Trace2Skill、AutoSkill 都是 trace→skill 的直接映射。
SkillRL 指出 raw trajectory "redundant and noise-heavy"，阻碍抽取可泛化模式。
SWE 轨迹里信息量最大的部分——文件路径、patch 形状、失败测试名——正是最不能写进技能的部分。
单条轨迹归纳，held-out 上的提升与"记住这道题"无法区分。

| 层级 | 例子 | 能否安全跨任务复用 | 是否改变行为 |
|---|---|---|---|
| 过程先验（public skills） | "run focused tests" | 安全 | ✗ 太泛 |
| repo 级技能（HSE） | "本 repo 改 API 需同步更新 stub" | 需限定同 repo | ✓ |
| 跨 repo behavior 技能（HSE） | "改前先查调用方，避免定位漂移" | 安全且通用 | ✓ |
| raw trajectory | "改 foo.py:42 让 test_bar 过" | ✗ 泄漏 | ✓ 但只对这题 |

两端之间缺的是中间层——由多条执行证据支撑、按复用范围分级沉淀的 repo 级与跨 repo behavior 技能。
HSE frozen skills 在下游有效，无效的是公共技能库里的 public skills——区别在抽象层级。

## 2. 非 coding 场景的机制为什么不能照搬

ALFWorld、WebShop、office workflow 的动作空间封闭且跨任务共享。
那里的"技能"是固定动作词表上的过程捷径（"要洗东西先去 sink"），能迁移是因为下一个任务仍在同一环境。
AutoSkill 抽象的用户偏好也是跨会话不变量。

SWE 每个任务实例本身就是一个新环境：新 repo、新构建系统、新测试框架，动作空间开放。
不存在"固定词表上的过程捷径"——repo A 有效的操作序列在 repo B 连符号都对不上。
唯一能跨环境成立的不变量是**失败模式的抽象**：不是"去 sink"，
而是"我倾向于没查清调用方就动手改"。这类知识描述的是 agent 自身的行为倾向，与 repo 无关。

## 3. 已有 coding 技能演化缺什么——泛化能力差，因为单轨迹生成、质量差、没有 evaluator

**技能不分层。** SkillOpt 把技能优化成单一 document；SkillOS 维护 flat SkillRepo；
MUSE-Autoskill 管理独立 skill 包。它们把"技能"当成扁平的可优化对象。
但一条 SWE 轨迹里混着两类复用范围不同的知识：repo 内的（入口路径、测试命令）只对同 repo 有意义，
跨 repo 的（定位漂移、弱验证、no-diff 退出）才是可迁移的行为模式。
扁平技能库要么把 repo 细节写进可迁移技能（泄漏），要么把行为模式淹没在 repo 细节里。

**单条轨迹直接生成技能，没有多证据聚合。** Trace2Skill、AutoSkill 都是 trace→skill 的直接映射。
一条轨迹里的信息既包含可复用的行为模式，也包含任务特定的细节（文件路径、patch 形状、失败测试名），
没有多证据聚合就把两者一起写进技能。技能好坏全凭生成端的自评，而自评会系统性高估——
尤其在 SWE 里"代码看起来对"和"测试通过"经常不一致。
没有独立的 evaluator 门控，单次幸运轨迹就能产出被部署的技能，在未来任务上引发回归。

**技能质量只靠下游测试分数判断，没有独立的评估能力。** 现有方法靠下游 benchmark 的测试结果
（held-out 分数、unit tests）决定技能去留，且都在评测集所在的 benchmark 上划 train/val/test：
SkillOpt 在同一个 benchmark 上划 train/selection/test 三份（原文："splits derived from the same
dataset seed"），selection split 筛技能、test split 报分数（"the selection split gates updates,
and the test split is used only for final reporting"）；GEPA 同样"adopt a standard
train/validation/test split"，在同一个 benchmark 上用 validation 筛参数、test 报分数；
SkillOS"train on its training split... evaluate on the corresponding test set"，训练和评测在同一个 benchmark。
它们没有独立的 evaluator——技能质量完全由下游 validator 的分数决定，
而下游 validator 同时是评测打分的依据，技能库是被评测分布筛选的产物。

## 4. 技能的生成与接受应该分离，且循环要落在训练集上

**生成和接受必须分开。** 把生成和接受拆成两个角色：writer 负责起草，evaluator 负责打分门控，
技能只有通过 evaluator 的 support/risk 检查才被接受。writer 和 evaluator 必须协同演化：
writer 产出新形态的技能，evaluator 的判据要跟着更新才能正确评估它们，
否则用过时的标准拒绝新形态技能，或接受已过时的技能。

**循环要落在训练集上。** 现有方法把整个生成→验证→接受的循环放在评测集所在的 benchmark 上：
SkillOpt 在同一个 benchmark 上划 train/selection/test 三份（原文："splits derived from the same
dataset seed"，"the selection split gates updates, and the test split is used only for final reporting"）；
GEPA 同样"adopt a standard train/validation/test split"，在同一个 benchmark 上用 validation 筛参数、
test 报分数；SkillOS"train on its training split... evaluate on the corresponding test set"。
验证和评测确实分开了，但它们同分布（同 benchmark 同 repo 集）——
技能在验证份上好→测试份上好，可能是同分布泛化，不是跨 repo 迁移；
更关键的是技能库是被这个 benchmark 的测试反复筛选出来的。

我们的设定不同：co-evolution 在 SWEGym 训练集上做，下游用 SWE-bench Verified 评测，
两者 repo 集不重叠。训练期用 SWEGym 的 verifier 校准 evaluator、门控候选状态——
这和现有方法一样用测试筛技能，但循环在 SWEGym 上闭合，
技能库被 SWEGym 的测试筛选，而不是被 SWE-bench Verified 的测试筛选。
下游 test-time evolution 不用 verifier 筛技能，技能去留只由 evaluator 的 proxy reward 决定，
verifier 只在事后报告 resolved 数。

这样 co-evolution 没有泄漏风险：训练期的 verifier 信号来自 SWEGym（独立 repo 集），
下游的评测信号完全不进入技能更新回路。现有方法在评测集上做 co-evolution，
即使划了三份，技能库仍是被评测集分布筛选的产物；我们在训练集上做 co-evolution，
评测集只读不写。

## 5. 我们的方法（HSE）

**分层沉淀，不是分层检索。** 轨迹先落成 task evidence（公开信号的结构化记录，不直接成技能）；
evidence 在同一 repo 内多条聚合（≥2 条）才升为 repo 级技能；
同一失败签名跨 ≥2 repo 复现才升为跨 repo behavior 技能。
promote 条件是 support 计数，沉淀过程本身把"复用范围"作为门槛。

**迁移两块，不是一块。**
1. frozen library：SWEGym 训练期沉淀出的通用 skills（repo 级 + 跨 repo behavior），冻结后直接用于下游。
2. TTS 能力：生成技能（writer）和评估技能（evaluator）的 policy 本身。
   下游用这两项能力做 test-time evolution，而不是把训练期的具体技能原样搬过去。
   现有方法只迁移一个静态技能库，没有把"生成与评估的能力"作为可迁移物。

**evaluator 打分。** proxy reward = 基线 + positive_support + support_tasks + repo_support +
event_support，有 risk flag（insufficient_positive_repo_support、false_accept_risk）。
penalize false accepts first：已解出 case 回归时先 disable 再提升 confidence。
writer/evaluator policy 是从 accepted/rejected 历史学的 calibration 规则
（"2 个 failure-mode trigger 后更新""penalize false accepts first"），不是 RL 训练的权重。

**verifier boundary。** 训练期 verifier 打标签、校准 evaluator、在 repo 隔离的 validation
split 上门控候选状态；下游期任何官方 verifier 标签、隐藏测试、gold patch 都不进技能更新回路，
verifier 只在事后报告 resolved 数。这使"迁移"成为可审计的结论。

## 6. Introduction

1. **开场**：coding agent 每次解题留下执行证据，却被丢弃 → 回收成技能能让 agent 在不重训的情况下积累经验。
   但现有技能过于 general（public skills 不改变行为）或过于 specific（raw trajectory 泄漏）。
2. **非 coding 机制不能照搬**（§2）：封闭动作空间里技能是"关于环境的知识"，
   SWE 每题换环境，可迁移的只能是"关于 agent 自身失败倾向的知识"。
3. **已有 coding 技能演化缺什么**（§3）：技能不分层（repo 细节和跨 repo 行为混在一起）、
   单条轨迹直接生成技能（没有多证据聚合，任务特定细节泄漏进技能）、
   技能质量只靠下游测试分数判断（没有独立的评估能力，测试分数兼任评测）。
4. **生成与接受应分离，循环落在训练集上**（§4）：现有方法把生成和接受混在一起、
   且循环跑在评测集上（SkillOpt 在 test set 上划三份做 co-evolution）。
   问题：技能库被评测分布筛选，"有效"是筛选的产物。
   解法：writer/evaluator co-evolution，循环在 SWEGym 训练集上闭合，下游不用 verifier 筛技能。
5. **我们的方法**（§5）：分层沉淀 + 迁移两块（frozen library + TTS 能力）+ co-evolution + verifier boundary。
6. **贡献**：分层 evidence-gated 技能、verifier-boundary 协议、可运行系统、评测协议。
