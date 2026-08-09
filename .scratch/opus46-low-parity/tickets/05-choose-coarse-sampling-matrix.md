# 确定两提供商的粗筛参数矩阵

- Type: research — AFK
- State: open
- Claim: unclaimed
- Blockers: [冻结非全量隔离筛选实验契约与代码边界](03-freeze-experiment-contract.md), [审计既有参数优化产物的可复用性](04-audit-existing-runs.md), [把 TQA v1 操作化为评分与裁决协议](06-define-evaluation-rubric.md), [证明 TQA 执行夹具、隔离链路与成本](08-prove-tqa-harness-and-cost.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

基于方舟 / 阿里云实际参数语义、可复用历史证据、TQA 晋级规则和实测成本，`deepseek-v4-flash` 与 `qwen3.7-plus` 首轮各应测试哪些最小 `temperature` / `top_p` 组合？

## Why this decision matters

笛卡尔积会迅速放大成本，且同时改变两个采样参数难以归因。两个模型来自不同提供商，其 OMIT 语义、有效范围和回显证据不能强行统一；矩阵也必须与 TQA 的提前淘汰和复跑规则相连。

## In scope

- 为两个模型分别定义 provider-default / OMIT 观察臂、显式基线、temperature-only 点位和必要的 top_p-only 点位。
- 仅在有证据时加入少量 joint configuration，并标为确认臂而非主效应归因。
- 使用 Ticket 08 对“整集语境一次性成本 + 重点窗口逐配置翻译成本 + 重试”的实测拆分，不用理想化单请求估算或整集翻译成本替代。
- 冻结 coarse-to-fine 顺序、最少复跑、晋级差值、分歧加跑、早停和每模型 / 总项目预算。
- 不把提示词短语作为矩阵轴；它只在各模型 sampling finalist 上独立 A/B。

## Completion evidence

- 每个点都有目的、对照、预计成本、最大运行数、淘汰规则和可观察停止条件。
- Ark 与 Aliyun 分表记录，不把 `null`、OMIT 或 provider default 当作相同数值。
- 矩阵足够具体，可由 `to-spec` 无猜测地写入两个模型的执行与验收契约；正式运行 tickets 由后续 `to-tickets` 生成，任何超限都必须回到 HITL 批准。

## Resolution

Unresolved.
