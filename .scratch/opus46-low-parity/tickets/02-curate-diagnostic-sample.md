# 校验并固化两集 TQA 诊断样例

- Type: task — AFK
- State: open
- Claim: unclaimed
- Blockers: none
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

如何把两份只含中英正文的重点检查 SRT，以及 TQA v1 与两份独立辅助说明，固化为可追溯、可盲化、不会把 Low 答案泄漏给目标模型的检查清单、manifest、English-only 执行视图和 dev / holdout 划分？

## Why this decision matters

重点检查 cue 的筛选、原 cue 编号恢复和时间排序已经完成，无需重新抽样或重新映射 ID。准备工作的风险已变为跨文件一致性与执行隔离：重点检查 SRT 同时包含 Low 中文和英文，说明文件只辅助解释 TQA 维度；检查 cue 又是稀疏的。若直接把双语 SRT 作为翻译输入，会泄漏答案；若把相邻检查 cue 当作原剧连续对白，则会制造虚假语境。

## Current observations to verify and freeze

- 四份源 SRT 的英文 / 中文条数分别为 747 / 747 与 647 / 647，ID 与时间码逐项对齐。
- S01E03 样例 79 条，S01E06 样例 72 条；151 个样例 cue 的编号、时间码、英文和 Low 中文正文当前均与源 SRT 精确匹配，且按原 cue ID 严格递增。
- 两份 `_说明.md` 分别包含 79 / 72 个辅助条目，与重点检查 SRT 的 cue 编号、时间码和顺序一一相同；它们只提供 TQA 维度、说明和便于人工定位的摘录，不充当正文真值来源。
- TQA v1 的九个核心维度之外，说明标签还含标题、法律、军事、医学、哲学等辅助主题词；核心维度与辅助标签不能混作覆盖率分母。

## In scope

- 记录 TQA v1、两份重点检查 SRT、两份辅助说明和四份源 SRT 的 SHA-256、条目数、编码与来源角色。
- 为每个重点检查 cue 记录 `episode`、原 cue ID、时间码、英文、Low 中文，以及从辅助说明关联的九维核心标签、辅助标签和测试说明；不再引入与原 cue ID 重复的“样例 ID”。
- 验证 SRT ↔ 说明 ↔ 英文源 ↔ Low 中文源的编号、时间、顺序和正文关系；偏差只记录，不静默修改用户文件。
- 由双语重点检查 SRT 派生保留原 cue ID、时间码和顺序的 English-only 检查集；候选调用中排除 Low 中文、说明、摘录和裁决键。
- 明确重点检查 cue 与为真实局部语境加入但不计分的 context cue 如何区分；是否需要全量翻译及具体连续窗口策略交给 Ticket 03。
- 在看到任何新候选输出前，基于覆盖与跨集迁移风险冻结 dev / holdout 划分，并说明为何采用整集切分或分层切分。
- 把两份重点检查 SRT 和三份 TQA / 说明辅助材料视为版本化输入；派生产物单独命名和留痕，不覆盖它们。

## Completion evidence

- 一份可审计 manifest 与覆盖报告能从任一重点检查 cue 定位到双语 SRT、辅助说明和四份源 SRT 的对应条目，同时明确说明文件不是正文真值来源。
- English-only 执行视图与盲评裁决键分离；检查证明目标模型输入不含 Low 中文、说明或摘录。
- dev / holdout、遗漏维度和稀疏检查 cue 的上下文风险已在候选运行前冻结。
- 未调用任何模型，未修改两份重点检查 SRT、三份辅助材料或四份源 SRT。

## Resolution

Unresolved.
