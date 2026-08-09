# 审计既有参数优化产物的可复用性

- Type: research — AFK
- State: open
- Claim: unclaimed
- Blockers: [冻结非全量隔离筛选实验契约与代码边界](03-freeze-experiment-contract.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

`out/` 中与 `deepseek-v4-flash` 或 `qwen3.7-plus` 有关的默认、temperature-optimized 与其他全量/烟测产物，哪些满足新冻结的 TQA 隔离筛选契约，可以复用于本轮参数搜索而不重新消耗 token？

## Why this decision matters

仓库已经存在 Qwen `t0.7`、DeepSeek `t1.3` 等命名产物，但文件名不是配置证明。复用有效产物能显著降低成本，复用不可比产物则会污染结论。

## In scope

- 只读核对 `instructions.txt`、`episode_summary.txt`、`input.json`、`meta.json`、batch 报告、模型 ID 和输出完整性。
- 检查 sampling 是否由报告、命令记录或响应回显证明；不能从文件名猜测。
- 按“可直接复用 / 仅作旁证 / 不可复用”分类。
- 不能映射到冻结 manifest、固定整集语境和 English-only 重点检查夹具的历史运行，不得直接进入候选统计；完整全量产物最多按契约抽取对应重点检查 cue。

## Completion evidence

- 每个候选运行有路径、证据、缺口和复用判定。
- 结论遵守已冻结的实验契约，且不修改、移动或重新生成历史产物。
- 两个目标模型分别判定，不以其他模型的历史表现代替证据。

## Resolution

Unresolved.
