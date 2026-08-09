# Opus 4.6-Low 字幕翻译对齐路线图

> **状态：暂停（2026-08-09）。** 用户随后将本轮工作简化为两个目标模型 × 两集全量字幕 × 10 个单轴采样臂的 40-case 数据采集，并授权将 8 份 Provider refusal 以 inspection rescue 形式补齐、主状态永久保留为 refusal。当前采集契约以 `pipeline/sampling_matrix.py`、相应测试与仓库 README 为准。本 map 和 Tickets 01—08 保留为 TQA 统一评价与后续规划的历史输入；其中非全量隔离筛选路线、旧依赖图和 spec-readiness gates 不约束已经执行的本轮全量采集，也不得被误读为当前 collector 规范。

## Destination

形成一套可让 `to-spec` 无需猜测即可生成 `ready-for-agent` Living spec 的决策与证据包：针对方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus`，冻结 TQA v1 重点检查清单、Low 非劣性规则、非全量隔离筛选契约、评分协议、provider-aware sampling 矩阵、复跑 / 停止规则、预算和生产边界；Wayfinder 在核心发现 tickets 全部 resolved 且 `Not yet specified` 已被 spec handoff 吸收后结束，不在本阶段执行正式参数筛选、holdout 或全流程确认。

## Notes

- 本地图使用仓库既有的本地 Markdown tracker；约定见 [`docs/agents/issue-tracker.md`](../../docs/agents/issue-tracker.md)。
- “目标模型”遵循 [`CONTEXT.md`](../../CONTEXT.md)：仅指方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus`，两者独立选择配置，不按 README 的六模型表解释，也不追求跨模型统一参数。
- 用户提供的评估材料由五份文件组成：[`字幕翻译质量评估框架_TQA_v1.md`](../../sample/字幕翻译质量评估框架_TQA_v1.md)、S01E03 的[双语样例 SRT](../../sample/A_French_Village_S01E03_翻译测试样例.srt)与[辅助说明](../../sample/A_French_Village_S01E03_翻译测试样例_说明.md)、S01E06 的[双语样例 SRT](../../sample/A_French_Village_S01E06_翻译测试样例.srt)与[辅助说明](../../sample/A_French_Village_S01E06_翻译测试样例_说明.md)；它们取代“从四份源 SRT 重新抽取诊断样本”的旧准备方案。
- 当前只读核对观察到：S01E03 样例 79 条、S01E06 样例 72 条；151 个 cue 的编号与时间码均精确对齐相应英文源 SRT，双语正文精确匹配英文与 Low 中文源条目。两份说明文件分别以相同 cue 编号、时间码和顺序一一对应。
- 两份 `*翻译测试样例.srt` 是模型译文的**重点检查清单**：保留 Low 中文与英文正文，并按原 cue 编号 / 时间顺序排列。两份 `*翻译测试样例_说明.md` 只是评估辅助信息，提供相同 cue 的 TQA 维度与说明，不是模型输入、独立测试项或正文真值来源。
- 重点检查清单虽然按时间顺序排列，但只包含稀疏检查 cue；相邻样例不必是原剧连续 cue。目标模型输入必须移除 Low 中文，并由 Ticket 03 决定如何加入不计分的连续 context cue，不能把稀疏检查项的直接相邻误称为完整语境。
- 当前生产 CLI 只能按 `cue_offset` / `max_cues` 取一个连续切片，随后把 cue ID 重排为 `0..n-1`；摘要也只读取该切片，并使用同一 sampling 配置。它没有“固定整集摘要 + 任意重点 cue / 窗口 + 复用摘要”的表达能力，故不能直接用来证明非全量筛选的参数效应。
- 当前工作假设是：参数组合的 coarse-to-fine **筛选**不需要翻译两集全量字幕；可以固定由整集英文产生的语境，再只翻译覆盖重点检查 cue 的连续窗口并仅评分重点 cue。该方案只能支持筛选与淘汰，不能替代冻结 holdout 或 finalist 的现有全流程确认；是否成立及最小工具边界由 Ticket 03 / 08 给出证据。
- TQA v1 已冻结九个核心维度、定向抽样方法和自查项，但尚未定义评分尺度、错误严重度、硬门禁、Low 非劣性阈值、盲评和分歧裁决；Ticket 06 负责操作化，不另起平行质量体系。
- Low 中文是参考锚点而非逐字答案。候选输出不得看到 Low 中文、说明文件或 holdout 裁决键；评估时允许有证据的等义改写或对参考瑕疵的改进。
- 筛选采用 [`CONTEXT.md`](../../CONTEXT.md) 定义的“隔离筛选”，finalist 再做“全流程确认”。Wayfinder 只冻结这两阶段的行为、证据和门槛；正式执行由后续 `to-tickets` 生成的 tracer-bullet tickets 承担。
- [`docs/translation_prompt.md`](../../docs/translation_prompt.md) 冻结；唯一允许的提示词变量是“坚决避免翻译腔（translationese）”与“用目标语言重写”的二选一，且只在 sampling finalist 上单独 A/B。
- 保持现有摘要、分批、JSON、校验、双语字幕与关 thinking 流程；除 `temperature` / `top_p` 外的模型参数不是优化变量。实验工具若确有必要，只能增加非生产适配层，不得改变生产默认行为。
- 当前代码而非 README 是采样行为的真值：CLI 省略参数时不向 API 发送字段；显式数值才发送。实验记录必须区分 sent 与 omitted，不能把 provider default 伪装为共同数值。
- 规划生命周期固定为：清除 Wayfinder 核心发现 tickets → `to-spec` 生成 `spec.md` → `to-tickets` 生成正式执行 tickets；不把正式模型筛选预先发布成 Wayfinder children。
- 旧 [`deep-grill-review.md`](deep-grill-review.md) 基于“六模型 + 尚待重新抽样”的旧路线图，已被本次输入和领域决定整体取代，不再构成当前路线的有效审核或 spec-readiness 证明。新的评审必须以本地图、两份重点检查 SRT、三份 TQA / 说明辅助材料和当时代码为输入。

## Decisions so far

暂无 resolved Wayfinder tickets；目标模型、Low 非劣性、评估材料角色、规划生命周期和旧评审失效均作为上游 standing constraints 记录在 Notes，待相应 ticket resolution 只追加经证据关闭的新决定。

## Open tickets

- [定义 TQA 下的 Low 非劣性与人工裁决](tickets/01-define-opus-low-parity.md) — HITL：冻结通过、并列、失败和最终裁决权。
- [校验并固化两集 TQA 诊断样例](tickets/02-curate-diagnostic-sample.md) — AFK：验证两份重点检查清单及三份辅助材料，建立可追溯 manifest，并冻结 dev / holdout 与 English-only 派生规则。
- [冻结非全量隔离筛选实验契约与代码边界](tickets/03-freeze-experiment-contract.md) — AFK：判断不全量翻译能否完成参数筛选，并决定固定整集语境、重点检查 cue、连续 context cue 和元数据如何落地。
- [审计既有参数优化产物的可复用性](tickets/04-audit-existing-runs.md) — AFK：只按新契约判断历史证据能否复用。
- [把 TQA v1 操作化为评分与裁决协议](tickets/06-define-evaluation-rubric.md) — AFK：补齐硬门禁、严重度、尺度、盲评和非劣性判定。
- [重新评审修订后的实验设计](tickets/07-review-revised-experiment-design.md) — AFK：生成新的 deep-grill 结论，旧评审不作为输入裁决。
- [证明 TQA 执行夹具、隔离链路与成本](tickets/08-prove-tqa-harness-and-cost.md) — Prototype / HITL 预算门：先本地证明，再按获批上限做最小真实调用。
- [确定两提供商的粗筛参数矩阵](tickets/05-choose-coarse-sampling-matrix.md) — AFK：基于真实链路成本和 provider 语义冻结最小矩阵。

## Dependency graph

- Ticket 01 + Ticket 02 → Ticket 06。
- Ticket 02 → Ticket 03 → Ticket 04。
- Ticket 03 + Ticket 04 + Ticket 06 → Ticket 07 → Ticket 08。
- Ticket 03 + Ticket 04 + Ticket 06 + Ticket 08 → Ticket 05。
- Ticket 05 resolved 后，核心发现图清除；将下列后续阶段意图交给 `to-spec`，而不是在 Wayfinder 中继续创建执行 tickets。

## Not yet specified

以下是 Living spec 必须覆盖、随后由 `to-tickets` 切成 tracer-bullet tickets 的执行阶段意图；它们目前不是 open Wayfinder tickets，也不授权模型调用：

- 分别对 `deepseek-v4-flash` 与 `qwen3.7-plus` 按冻结矩阵执行非全量 coarse-to-fine 隔离筛选：只翻译覆盖重点检查 cue 的获准连续窗口，复跑和淘汰均只使用冻结评分集。
- 仅在各模型 sampling finalist 上比较原提示词短语与“用目标语言重写”，语义硬门禁优先于自然度。
- 在冻结 holdout 上分别判断两个完整 finalist 的 Low 非劣性、稳定性和 dev 过拟合。
- 由用户基于质量、成本和失败模式批准或拒绝各 finalist 的全流程确认范围与预算。
- 只按获准范围执行现有真实处理流程确认，并把隔离结果、全流程结果、成本和剩余风险并列留痕。
- 正式 ticket 的数量、粒度、blocking edges 和验收标准由 `to-spec` 的最终内容经 `to-tickets` 生成，不复用已撤回的 09—16 编号。

当 Tickets 01—08（含 05 / 06）全部 resolved 后，`to-spec` 应把以上阶段变成明确的 Solution、User Stories、Implementation Decisions 与 Testing Decisions；handoff 更新随后清空本节，不把它作为残留 fog 带入实施。

## Spec-readiness gates

- 两份重点检查 SRT、三份 TQA / 说明辅助材料、四份源 SRT 和派生 manifest / English-only 夹具均有哈希和一一追溯关系。
- cue ID、时间码、剧情顺序、核心维度与辅助主题标签不混用；dev / holdout 在候选输出出现前冻结。
- 目标模型调用不含 Low 中文、说明文件或 holdout 裁决键；评分方能按盲化协议恢复重点检查 cue 的参考与 TQA 辅助信息。
- 非全量隔离筛选所用整集语境、连续 context cue、重点检查 cue 集、Glossary、prompt、批配置与 sampling wire intent 可由产物证明；产物能区分“整集已通读”和“整集已翻译”。
- TQA 硬门禁、错误严重度、质量尺度、“筛选通过”与“Low 非劣性通过”两级阈值已冻结。
- provider-aware matrix、复跑、晋级、早停、单模型预算与总预算已确定；OMIT 只表示 provider default / 有效值未知。
- 提示词短语不与 coarse sampling 同时变化，且自然度收益不能补偿语义、漏译或结构失败。
- 新 deep-grill 评审已关闭所有 spec blocker；最小 harness / 成本证据证明非全量筛选可以正确回填重点检查 cue，并明确其不能替代的 holdout / 全流程边界。
- `Not yet specified` 的每个执行阶段都能被 `to-spec` 明确表达，且不需要在 spec 阶段重新访谈或猜测。

## Out of scope

- 修改用户提供的 TQA v1、两份重点检查 SRT、两份辅助说明、四份源 SRT 或 Low 参考译文来迎合候选输出。
- 把 Low 中文、辅助说明或稀疏检查 cue 的虚假邻接直接发送给目标模型。
- 改造现有摘要、分批、并发、repair、SRT/JSON 协议、前处理或双语字幕生产流程。
- 优化 thinking/reasoning、`max_output_tokens`、batch size、penalty、seed、top_k 或其他非 `temperature` / `top_p` 参数。
- 对 [`docs/translation_prompt.md`](../../docs/translation_prompt.md) 做除指定短语替换以外的重写、扩写或规则删改。
- 在核心发现图、新评审与预算门尚未清除前执行正式计费矩阵、holdout 或全流程确认。
- 把字符串相似度、单次主观偏好、历史目录名或旧 deep-grill 结论当作 Low 非劣性证据。
- 更换模型、供应商、API，扩大到其他模型，或引入新的生产翻译流程。
