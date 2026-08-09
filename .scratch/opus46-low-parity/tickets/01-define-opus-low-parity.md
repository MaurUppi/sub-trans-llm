# 定义 TQA 下的 Low 非劣性与人工裁决

- Type: grilling — HITL
- State: open
- Claim: claimed
- Blockers: none
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

以 TQA v1、两集测试样例和 Low 中文为参考锚点时，什么最低规则足以让 `deepseek-v4-flash` 或 `qwen3.7-plus` 的候选配置被判为“Low 非劣性通过”，以及谁对边界案例拥有最终裁决权？

## Why this decision matters

TQA v1 定义了诊断维度和样例选择方法，但没有定义错误严重度、通过阈值或候选相对 Low 的非劣性规则。若先看候选输出再定标准，自然改写、参考瑕疵和真实误译会被按结果需要重新解释。

## Already fixed

- 目标模型只有方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus`，分别选择配置。
- Low 是参考锚点，不是逐字答案；文本相似度不能单独证明质量。
- 隔离筛选与全流程确认是两个不同证据阶段。

## In scope

- 定义不可补偿的失败：结构破坏、漏译/增译、关键语义、指代、历史事实和角色关系等。
- 决定 Low 本身疑似有误、候选等义改写或候选更优时如何记录和裁决。
- 区分“进入下一轮”“TQA dev 通过”“Low 非劣性 holdout 通过”和“获准全流程确认”。
- 冻结并列、失败、证据不足和边界案例的最低语义。
- 明确最终人工裁决者、是否需要第二判断以及分歧如何解决。

## Completion evidence

- 通过实时 HITL 对话记录一条明确决定，并将原话或无歧义转述写入本 ticket。
- 决定能直接约束 Ticket 06 的评分协议，并由后续 `to-spec` 写入正式 holdout ticket 的判定契约。
- 不把两个目标模型之间的胜负当作各自相对 Low 的非劣性结论。

## Resolution

Unresolved.
