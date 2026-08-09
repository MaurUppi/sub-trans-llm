# 把 TQA v1 操作化为评分与裁决协议

- Type: research — AFK
- State: open
- Claim: unclaimed
- Blockers: [定义 TQA 下的 Low 非劣性与人工裁决](01-define-opus-low-parity.md), [校验并固化两集 TQA 诊断样例](02-curate-diagnostic-sample.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

如何在不改写 TQA v1 九维体系的前提下，补齐可重复的错误严重度、硬门禁、评分尺度、盲评、复跑聚合和 Low 非劣性判定？

## Why this decision matters

TQA v1 已回答“测哪些难点、如何定向抽样”，但尚未回答“怎样给候选打分并晋级”。九维覆盖率也不等于九维权重；样例中的法律、军事、医学、哲学和标题等辅助标签不能未经定义就变成新的核心维度。

## In scope

- 将两份独立说明文件中的九个核心维度映射到 manifest；辅助主题标签只用于定位专业风险和分层报告。
- 定义 cue / 多 cue 语义单元、错误类型、严重度、硬失败和可补偿质量差异。
- 分离结构完整性、关键语义门禁与自然度等软质量；文风不能补偿漏译、增译或事实错误。
- 为候选与 Low 建立匿名 ID、随机顺序、参考可见时点、评审表和解盲流程。
- 定义单次评分、复跑聚合、维度分层、并列 / 分歧 / 证据不足和加评规则。
- 自动检查只负责 ID、时间码、条数、格式、缺失和统计辅助；不把 BLEU 或字符相似度当作质量真值。
- 落实 Ticket 01 的“进入下一轮 / dev 通过 / holdout Low 非劣性通过”三级阈值。

## Completion evidence

- 一份版本化 rubric 明确尺度、严重度、门禁、阈值、盲化、复跑和裁决流程。
- 用重点检查清单中的代表性 cue 及其辅助说明做无候选配置参与的校准示例，证明不同标签的解释一致。
- 明确哪些判断可自动化、哪些必须 HITL，以及参考疑似有误时如何留痕。
- 协议可直接约束 Ticket 08 的 proof，并由 `to-spec` 写入后续正式筛选、A/B、holdout 与全流程确认的验收，不需要在看见候选后补规则。

## Resolution

Unresolved.
