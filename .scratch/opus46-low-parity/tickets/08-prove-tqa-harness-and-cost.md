# 证明 TQA 执行夹具、隔离链路与成本

- Type: prototype — HITL budget gate
- State: open
- Claim: unclaimed
- Blockers: [冻结非全量隔离筛选实验契约与代码边界](03-freeze-experiment-contract.md), [把 TQA v1 操作化为评分与裁决协议](06-define-evaluation-rubric.md), [重新评审修订后的实验设计](07-review-revised-experiment-design.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

冻结的 English-only 重点检查夹具和非全量隔离筛选链路，能否在不泄漏 Low / 辅助说明、不改变生产默认行为的前提下复用整集语境、只翻译获准窗口、回填重点检查 cue，并给出真实可用的 token / 时间 / 重试成本？

## In scope

- 先用本地 fixture / fake client 证明整集语境与翻译输入分离、剧情顺序、连续 context cue、重点检查 cue 回填、元数据和失败处理。
- 检查目标模型请求中不存在 Low 中文、辅助说明、holdout 裁决键或稀疏检查项造成的虚假邻接。
- 只有用户批准模型、点位、样本范围和硬预算后，才做最小真实调用；未经批准保持本地 proof。
- 分别记录整集语境一次性成本与每个参数配置的窗口翻译输入 / 输出 token、时间、重试、完整性和成本，不只记录合计。
- 若需要实验工具，以测试约束非生产边界，不修改现有生产默认流程。

## Completion evidence

- 本地 proof 与最小真实调用（若获批）都有可复验命令、哈希、元数据和结构校验结果。
- 候选输出能无歧义映射回重点检查 cue，context-only 输出不会进入分数；证据可区分整集通读与整集翻译。
- 得到足以约束 Ticket 05 的非全量单点成本区间及相对全量成本比例；未真实调用时明确标为估算，不伪称实测。

## Resolution

Unresolved.
