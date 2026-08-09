# 冻结非全量隔离筛选实验契约与代码边界

- Type: research — AFK
- State: open
- Claim: unclaimed
- Blockers: [校验并固化两集 TQA 诊断样例](02-curate-diagnostic-sample.md)
- Map: [Opus 4.6-Low 字幕翻译对齐路线图](../map.md)

## Question

参数组合筛选是否可以不翻译两集全量字幕；若可以，如何在不改变生产默认流程的前提下，用固定整集语境、English-only 重点检查集、连续 context cue 和可审计元数据隔离比较 `temperature` / `top_p`，并让 finalist 最终回到现有流程确认？

## Why this decision matters

现有 CLI 先切片并把 cue ID 重排为 `0..n-1`，摘要只看到切片，且同一组 sampling 同时作用于摘要与翻译；它也不能直接复用冻结的整集摘要或选择任意稀疏 cue。重点检查清单虽按原时间顺序排列，却不是连续剧情。直接运行只能证明“稀疏 / 局部输入上的整条 pipeline 配置差异”，不能证明固定整集语境下的翻译 sampling 效应。

## Evidence to inspect

- `main.py`、`model_client.py`、`pipeline/` 的真实切片、摘要、翻译、校验和落盘行为。
- `docs/translation_prompt.md`、摘要 / Glossary 拼装、重点检查 manifest、两份辅助说明与 English-only 派生视图。
- `scripts/quality_report_d.py` 与现有产物能否证明 sampling wire intent 和全部固定控制变量。

## In scope

- 比较并裁决三种主张：整集翻译；固定整集语境后只翻译重点 cue 的连续窗口；直接翻译稀疏检查集。明确各自能支持的结论和淘汰其中不成立的方案。
- 冻结输入、prompt、Glossary、固定整集语境、局部上下文、batch、thinking、输出协议、重试与重点检查 cue 集。
- 决定如何从原剧情顺序形成“计分的重点检查 cue + 不计分的连续 context cue”，避免把稀疏检查项的直接相邻伪装成原剧连续对白。
- 若采用非全量筛选，定义整集英文只读摘要如何一次生成、哈希、冻结和复用，并确保候选 sampling 只作用于翻译请求；若无法可靠隔离，则明确退回何种更昂贵方案。
- 为固定摘要、输入视图、instructions、模型解析 ID 和所有控制变量定义哈希与元数据。
- 区分参数 `sent` / `omitted`；OMIT 只记录 provider default / 有效值未知，不推断共同默认数值。
- 对“现有 CLI 足够”“新增非生产 wrapper”或“需要最小注入点”给出代码证据和最窄边界。
- 定义“整集已通读”“重点窗口已翻译”“整集已翻译”三种证据状态，以及隔离筛选、冻结 holdout 与全流程确认分别能支持什么主张，禁止相互替代。

## Completion evidence

- 对“参数筛选是否需要全量翻译”给出明确的 `可以 / 不可以 / 仅可用于初筛` 结论、代码证据、成本边界和失败条件。
- fixed-controls 表、允许变量表、运行命名、元数据 schema 和重点检查结果回填规则完整。
- 对实验工具需求有证据化判断；任何新增工具都不改变生产默认行为和现有输出契约。
- 仅凭产物即可复核候选看到的输入、固定整集语境、sampling wire intent、重点检查 cue 和 context-only cue 集。

## Resolution

Unresolved.
