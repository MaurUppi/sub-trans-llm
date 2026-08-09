# Claude Opus 4.6 两份系统提示词中的 translation 相关内容分析

## Answer（结论）

**严格口径下，两份文件都不存在要求 Claude 执行翻译任务的提示词。** 全文没有发现将源文本翻成目标语言、忠实保留语义、处理术语或指定译文格式等翻译工作流指令，也没有 `translation`、`translator`、`localization`、`multilingual`、`bilingual` 等命中。

**宽口径下，完整版存在与“回复语言选择”相关、但不等同于翻译任务的提示词；No tools 版不存在。** 完整版要求通常跟随当前查询所用语言、尊重明确的严格语言偏好，并要求 Visualizer 的加载文案与用户语言一致。这些规则会影响输出语言，却没有定义翻译输入、目标语言或翻译质量标准。

因此，如果目的是寻找可直接复用于本项目的翻译提示词，结论是：**两份文件均没有可直接提取的翻译模板**。完整版最多只能提供“如何选择回复语言”的外围行为规则。

## 研究问题、判定口径与来源边界

本报告回答的问题是：用户指定的两份公开文件中，是否存在与 `translation` 相关的提示词，以及这些内容能否视为真正的翻译指令。

判定分为两层：

1. **严格相关**：明确要求翻译、定义源语言或目标语言、规定译文质量/风格/格式，或描述本地化流程。
2. **宽泛相关**：决定使用哪种人类语言回复、跟随用户语言或应用语言偏好，但没有要求对给定内容进行翻译。

分析对象固定为公开仓库 `main` 在提交 `93c999115b300a6faac567830b0450a5478800cd` 的版本（读取日期：2026-08-09）：

- [Claude Opus 4.6 system prompt](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md)，本地核验为 3,731 行，SHA-256 `d3f06cd602688a21674a029a42a6db333a969398085d254b7b8de0679af8e874`。
- [Claude Opus 4.6 — No tools](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6-no-tools.md)，本地核验为 1,050 行，SHA-256 `cc9151e611b21a3a99f2646f03c3f97d009a09ff2f3f2fa4a88bc064e7e301c5`。

检索覆盖 `translate*`、`translation*`、`translator*`、`localize/localise*`、`localization/localisation*`、`multilingual`、`bilingual`、`language`、`locale`、`linguistic`，并人工检查每个命中的上下文。仅凭文件名或关键词命中不直接判定为翻译提示。

## Evidence（证据）

### 1. 完整版：没有翻译指令，但有回复语言选择规则

完整版把 `language` 列为用户行为偏好的一种，说明语言可能参与响应方式选择，但这只是偏好框架的一部分，并未定义翻译任务。[来源：完整版第 598–608 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L598-L608)

最接近 translation 的核心行为出现在偏好示例中：当用户虽声明母语为西班牙语、却用英语提问时，规则是“Follow the language of the query unless explicitly requested otherwise.”；如果用户明确要求只用日语，则这一严格偏好覆盖英语提问。它规定的是**选用何种语言作答**，不是把某段源文本翻译成另一种语言。[来源：完整版第 640–648 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L640-L648)

另一处宽泛相关规则仅作用于 Visualizer 工具的 `loading_messages`：加载文案应使用用户正在使用的语言。其作用域是工具 UI 状态文案，并非正文翻译或通用语言策略。[来源：完整版第 3264–3272 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L3264-L3272)

完整版唯一包含 `translat` 字符串的命中是地点搜索说明中的 “does not translate well”。这里的 `translate` 表示抽象请求不适合直接映射为搜索查询，随后要求拆分查询；与语言翻译无关。[来源：完整版第 2192–2204 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L2192-L2204)

文件中还出现“用户会普通话”“用户会西班牙语”“学习法语”等记忆示例。这些是个性化示例数据，不要求模型翻译内容，因此不计为 translation 提示词。[来源：完整版第 362–370 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L362-L370)

### 2. No tools 版：没有翻译指令，也没有回复语言选择规则

No tools 版没有出现完整版第 598–653 行的用户偏好框架，也没有 Visualizer 的同语言加载文案规则。全文中没有严格或宽泛意义上的 translation 行为指令。

它唯一的 `translat` 命中仍是地点搜索说明中的 “does not translate well”，上下文同样是在讲如何把宽泛地点请求拆成更适合检索的查询，并非语言翻译。[来源：No tools 版第 260–275 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6-no-tools.md#L260-L275)

No tools 版其余 `language` 命中都是 `Natural language search query` 或 `natural language` 行文格式，指自然语言而非某种目标语言，也没有形成翻译规则。[来源：No tools 版第 291–304 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6-no-tools.md#L291-L304)、[第 964–975 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6-no-tools.md#L964-L975)

### 3. 两个版本的差异

| 判断项 | 完整版 | No tools 版 |
|---|---:|---:|
| 明确执行翻译 | 无 | 无 |
| 源语言/目标语言规则 | 无 | 无 |
| 翻译质量、术语、格式要求 | 无 | 无 |
| 默认跟随查询语言 | 有 | 无 |
| 严格语言偏好可覆盖查询语言 | 有 | 无 |
| 工具 UI 文案跟随用户语言 | 有，仅 Visualizer | 无 |
| `translate` 字面命中 | 1 个，无关翻译 | 1 个，无关翻译 |

这里的差异不能简单归因于“是否启用工具”。两份文件本身记录的运行日期不同：完整版写的是 2026-05-22，而 No tools 版写的是 2026-02-18，说明它们可能同时包含版本时间差异。[来源：完整版第 3311–3317 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6.md#L3311-L3317)、[No tools 版第 1–5 行](https://github.com/asgeirtj/system_prompts_leaks/blob/93c999115b300a6faac567830b0450a5478800cd/Anthropic/claude-opus-4.6-no-tools.md#L1-L5)

## Limitations and unknowns（限制与未知项）

本报告只分析用户指定的第三方公开仓库文件，没有独立证明这些文本是 Anthropic 当前或完整的生产系统提示词。仓库可能存在截取、拼接、版本漂移或命名不准确，因此结论只能表述为“这两份指定文件中没有发现”，不能外推为 Claude Opus 4.6 产品内部绝不存在其他翻译指令。

关键词检索结合了人工上下文复核，可排除明显假阳性，但无法证明模型基础训练、未公开策略、动态注入提示或具体用户偏好不会产生翻译行为。

两份文件不是同一日期下仅切换工具开关的严格对照组，因此完整版独有的语言偏好规则不能确定是由工具配置本身带来的。

## Decision impact（对 translation 项目的影响）

不建议从这两份文件中声称提取到了 Claude Opus 4.6 的“翻译系统提示词”。如果要吸收可用设计，只能将完整版的语言选择逻辑作为外围路由规则：默认跟随当前请求语言，明确的长期语言约束优先；这不能替代本项目已有的字幕语义忠实度、术语表、时间轴、行长、语域和输出结构约束。

若后续要比较这些语言选择规则对翻译质量的影响，应把它们作为独立实验变量，而不是当作完整 translation prompt：例如分别测试“跟随当前查询语言”“固定目标语言偏好”和“显式翻译指令”，并保持模型、输入字幕、采样参数及质量评价标准一致。
