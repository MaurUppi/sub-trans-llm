# Temperature / top_p 测试 handoff

## 范围

本文只保存本轮 `temperature` / `top_p` 参数测试的对话决定、采集状态和续接边界。仓库结构、实现细节和完整参数表不在此重复，按下方路径读取。

## 已确认的实验决定

- 目标模型仅为方舟 `deepseek-v4-flash` 与阿里云 `qwen3.7-plus`，两个 Provider 可并行采集；每个模型内部按 case 顺序执行。
- 测试源为 [sample](.) 中的 `S01E03` 与 `S01E06` 两集完整英文字幕。每个 case 全量翻译，固定 50 cue/批、`batch_jobs=1`。
- 一次只改变一个采样参数，重点测试 `temperature`；`temperature` 与 `top_p` 不做笛卡尔积。精确 10 臂矩阵见 [`README.md`](../README.md#冻结-40-case-参数矩阵) 和采集根目录的 `matrix.json`。
- 用户明确确认 `temperature=OMIT, top_p=OMIT` 是第 10 个观察臂。`OMIT` 只表示字段未发送，不代表两个 Provider 使用相同数值。
- 两模型 × 两集 × 10 臂，共 40 个 case、560 个主翻译批。输出文件名必须直观呈现模型、剧集、temperature 和 topP。
- 每集摘要只生成一次并冻结复用；所有 case 使用同一 prompt 和 [`docs/Un_Village_francais_Glossary.md`](../docs/Un_Village_francais_Glossary.md)。每次请求必须保存 sampling 的 `sent/omitted` wire evidence。
- 先完成全部参数数据采集，再统一评价；不在采集过程中逐臂打分或改变矩阵。
- Low 译文被用户定义为“正确且最佳”的质量基线。统一评价建议先做匿名主评，用户拥有最终决定权；每一轮评价数据都必须保存。

### 翻译测试样例的用途

- [`A_French_Village_S01E03_翻译测试样例.srt`](A_French_Village_S01E03_翻译测试样例.srt) 和 [`A_French_Village_S01E06_翻译测试样例.srt`](A_French_Village_S01E06_翻译测试样例.srt) 是参数组合所产出模型译文的**重点检查项目**。文件仅保留 Low 中文与英文正文，并沿用相应全量英文字幕的原 cue 编号和时间码。统一评价时以这些 cue 定位候选模型的对应译文；它们不是本轮模型调用的输入字幕。
- [`A_French_Village_S01E03_翻译测试样例_说明.md`](A_French_Village_S01E03_翻译测试样例_说明.md) 和 [`A_French_Village_S01E06_翻译测试样例_说明.md`](A_French_Village_S01E06_翻译测试样例_说明.md) 是前述重点检查 cue 的**辅助信息**。说明文件按相同时间顺序逐条记录 cue 编号、时间、TQA 标准维度与测试说明，以及便于人工定位的中英摘录。
- `_说明.md` 只在评价阶段帮助理解检查重点，不是模型输入、不是独立测试项目，也不替代重点检查 SRT 或 Low/英文源字幕作为正文对应依据。

## 已完成的采集状态

权威状态文件：[`out/opus46-low-parity-full-matrix-20260809/progress.json`](../out/opus46-low-parity-full-matrix-20260809/progress.json)（`out/` 被 Git 忽略，仅在当前工作区保存）。当前记录为：

- 40/40 case 已结束：32 个原始全量结果完成，8 个为 `provider_refusal`；无 `failed`、`running` 或 `pending`。
- 8 个 refusal 全部来自 `qwen3.7-plus` / S01E03，Provider code 为 `DataInspectionFailed`；普通 repair 最终只缺 cue `119`、`122` 中的一条或两条。
- Qwen S01E03 的两个原始成功臂是 `OMIT/OMIT` 和 `temperature=OMIT, top_p=1.0`。其余 8 臂的准确 case ID 见 `progress.json`，不要从文件名以外猜测参数。
- 用户已明确授权 inspection rescue，并接受以“32 个完整原始结果 + 8 个 Provider refusal/rescue”结束本轮采集。8 个 rescue 均已完成，双语字幕仍存入普通 `bilingual/` 目录。
- rescue 输出永久带 `__inspection-rescue.srt`，必须保留主状态 `provider_refusal`；它们不属于纯粹的原始 50-cue 输出，评价和汇总时不得与 32 个原始成功结果混为同一 provenance。
- `out/opus46-low-parity-full-matrix-20260809/TQA-evaluation/` 仅用于采集完成后的结果评审；其中脚本没有参与 40-case 参数采集，也不属于 `pipeline.sampling_matrix` 的调用链。

## 仍需谨慎解释的问题

用户提出：“Qwen S01E03 十次中八次 `DataInspectionFailed`，两次成功是否只是偶发成功？”

当前证据不足以把两次成功定性为“偶发”或“稳定”：两次成功和八次失败没有独立复跑；失败又集中在同一集、同一 Provider 及 cue `119/122`，更像 Provider 输出检查边界随生成结果变化，而不是普通翻译质量失败。后续不得用 2/10 直接推断某个参数规避审核，也不得把 refusal 当作参数质量劣势。除非用户另行授权，不再追加采集来回答这一问题。

## 下一会话建议顺序

1. 先读取 `progress.json`、`matrix.json`、各 case 的 `case.json`，以主状态和 sampling wire evidence 为准；不要仅看最终 SRT 是否存在。
2. 进行或复核统一评价时，基于 40 份双语输出建立匿名候选映射，单独保存原始成功与 inspection rescue provenance；不要让参数文件名进入主评界面。
3. 用两份重点检查 SRT 定位评分 cue，用对应 `_说明.md` 辅助 TQA 判断；Low 基线和说明信息只在评价阶段使用。
4. 每轮评价单独落盘，解盲后再按模型和参数臂汇总；最终裁决交给用户。
5. 本轮采集已经按用户授权结束。任何复跑、新参数点或额外 Provider 调用都需要新的明确授权。

## 关键引用

- 当前实现与操作说明：[`README.md`](../README.md)
- 采集器：[`pipeline/sampling_matrix.py`](../pipeline/sampling_matrix.py)
- inspection rescue：[`pipeline/inspection_rescue.py`](../pipeline/inspection_rescue.py)
- 参数语义证据：[`docs/deepseek-qwen-temperature-top-p-defaults.md`](../docs/deepseek-qwen-temperature-top-p-defaults.md)
- TQA 框架：[`字幕翻译质量评估框架_TQA_v1.md`](字幕翻译质量评估框架_TQA_v1.md)
- 本轮代码提交：`c27f952 feat: add frozen subtitle sampling workflow`

## Suggested skills

- `$systematic-debugging`：仅在用户授权继续调查 8 次 refusal 与 2 次原始成功的稳定性时使用，先基于现有 case 证据形成可证伪假设。
- `$spreadsheets`：需要建立匿名评分表、逐轮保存评价数据和解盲汇总时使用。
