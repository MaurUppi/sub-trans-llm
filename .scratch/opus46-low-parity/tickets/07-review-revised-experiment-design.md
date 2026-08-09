# 重新评审修订后的实验设计

- Type: grilling — AFK review
- State: open
- Claim: unclaimed
- Blockers: [冻结非全量隔离筛选实验契约与代码边界](03-freeze-experiment-contract.md), [审计既有参数优化产物的可复用性](04-audit-existing-runs.md), [把 TQA v1 操作化为评分与裁决协议](06-define-evaluation-rubric.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

修订后的 TQA 母本、隔离筛选契约、Low 非劣性协议和收敛路径，是否已经关闭会让实验不可归因、不可盲评、泄漏答案或无界消耗 token 的主要失败方式？

## Why this decision matters

旧 `deep-grill-review.md` 的输入范围与当前路线不同，不能作为有效审核。计费 prototype 前需要一次基于当前文件和当时代码的新鲜压力测试。

## In scope

- 完整读取修订后的 map、Tickets 01—04 与 Ticket 06 的 resolution、Ticket 05 当前约束、TQA v1、两份双语样例、两份独立说明、manifest / 派生规则和相关代码。
- 独立挑战答案泄漏、稀疏检查 cue 的上下文伪造、dev / holdout 污染、整集语境冻结、非全量筛选的参数归因、评分可补偿性、复跑、成本和停止条件。
- 把代码事实、设计推断与尚无证据的风险分开。
- 产出新的 `deep-grill-review-v2.md`；不得把旧评审复制改名后视为新证据。

## Completion evidence

- 新评审给出 `支持 / 须修订 / 拒绝` 裁决、可观察失败场景、负责人和关闭条件。
- 所有阻断项回写为前向修订；不篡改旧评审历史。
- 在阻断项关闭前不批准 Ticket 08 的任何计费调用。

## Resolution

Unresolved.
