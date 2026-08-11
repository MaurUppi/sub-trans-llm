# 配置存放位置

**密钥与模型 ID 一律写在项目根目录 `.env`，禁止 hardcode 进 Python。**

- 模板：`.env.example`
- 本地实际配置：`.env`（已加入 `.gitignore`）
- 加载：`model_client.py` 启动时自动 `load_dotenv`

## Model ref docs:
```
# Ark
https://console.volcengine.com/ark/region:cn-beijing/model/detail?name=glm-5-2
https://console.volcengine.com/ark/region:cn-beijing/model/detail?name=deepseek-v4-pro
https://console.volcengine.com/ark/region:cn-beijing/model/detail?name=deepseek-v4-flash


# Aliyun
https://www.qianwenai.com/models/qwen3.8-max
https://www.qianwenai.com/models/qwen3.7-plus
https://www.qianwenai.com/models/qwen3.7-max
OpenAI Responses接口兼容: "https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api"
结构化输出:"https://help.aliyun.com/zh/model-studio/qwen-structured-output"
```

本地留存的官方文档（结论以这些为准，2026-08-05 复核）：

- `docs/火山方舟_结构化输出(beta)_1782549049.pdf`
- `docs/火山方舟_创建Response_1784703795.pdf`
- `docs/阿里云-结构化输出.md`
- `docs/阿里云-OpenAI兼容-Responses.md`
- `docs/阿里云-OpenAI兼容-Responses创建响应.md`（参数表最权威，含「未列出参数一律忽略」原则）

> base_url 备注：阿里云新文档一律给业务空间维度地址
> `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，
> 本项目 `.env` 用的是旧的 `https://dashscope.aliyuncs.com/compatible-mode/v1`，
> 六模型实测仍可用，暂不改动；若将来报鉴权/路由错误，先查这一条。

# Chat Completions API 官方规范复核（检索日期：2026-08-11）

## Answer

本项目可以在同一个 OpenAI Python SDK 客户端上为阿里云百炼与火山引擎方舟实现
Chat Completions：两家都要求把厂商 `base_url` 和 API Key 传给 `OpenAI(...)`，再调用
`client.chat.completions.create(model=..., messages=...)`。HTTP 方式均为
`POST <base_url>/chat/completions`，并使用 `Authorization: Bearer <API_KEY>` 与
`Content-Type: application/json`。但 Chat 与 Responses **不是只换一个方法名**：输入、输出
token 上限字段、文本提取路径、usage 字段和流式事件都不同，必须在客户端内部做显式映射。
[百炼 Chat API 参考](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、
[百炼 OpenAI Chat 兼容指南](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、
[方舟 Chat API 参考](https://www.volcengine.com/docs/82379/1494384)、
[方舟 OpenAI SDK 兼容指南](https://www.volcengine.com/docs/82379/1330626)。

## Evidence

### 服务地址、鉴权与 OpenAI SDK

| 厂商 | OpenAI SDK `base_url` | HTTP Chat endpoint | 鉴权 | 官方 SDK 调用 |
|---|---|---|---|---|
| 火山方舟（北京） | `https://ark.cn-beijing.volces.com/api/v3` | `POST https://ark.cn-beijing.volces.com/api/v3/chat/completions` | `Authorization: Bearer $ARK_API_KEY` | `client.chat.completions.create(model=..., messages=[...])` |
| 阿里云百炼（北京，推荐新域名） | `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | `POST https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions` | `Authorization: Bearer $DASHSCOPE_API_KEY` | `client.chat.completions.create(model=..., messages=[...])` |

方舟的 endpoint、Bearer header 和 `model`/`messages` 请求示例见
[对话（Chat）API](https://www.volcengine.com/docs/82379/1494384)；`OpenAI(base_url=..., api_key=...)`
示例见[兼容 OpenAI SDK](https://www.volcengine.com/docs/82379/1330626)。百炼的地域 endpoint、
Bearer header、非流式/流式 cURL 与 Python SDK 示例见
[OpenAI Chat 接口兼容](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。
百炼官方说明北京与新加坡的旧 `dashscope` 域名目前仍可用，但建议迁移到业务空间专属域名；
因此现有 `.env` 不是当场失效，只是后续应单独安排域名迁移。

最小非流式调用形态如下；密钥仍只从环境变量读取：

```python
from openai import OpenAI

client = OpenAI(api_key=api_key, base_url=base_url)
completion = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": instructions},
        {"role": "user", "content": prompt},
    ],
)
text = completion.choices[0].message.content
```

### 请求体、返回结构与流式输出

- `model` 与 `messages` 是两家 Chat 请求的核心字段；`messages` 按对话顺序排列，系统指令应作为
  `role="system"` 消息，而不是 Responses 的独立 `instructions` 字段。
  [百炼 Chat API 参考](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、
  [方舟 Chat API 参考](https://www.volcengine.com/docs/82379/1494384)。
- 非流式文本都从 `choices[0].message.content` 读取；`finish_reason` 至少需要处理 `stop` 与
  `length`，方舟还明确列出 `content_filter` 与 `tool_calls`。token 用量字段是
  `usage.prompt_tokens`、`usage.completion_tokens`、`usage.total_tokens`，并可带缓存或
  reasoning 明细。
  [百炼返回对象](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、
  [方舟返回对象](https://www.volcengine.com/docs/82379/1494384)。
- 设置 `stream=True` 后，两家 Chat 都按 chunk 返回，正文增量位于
  `choices[].delta.content`。百炼以 `stream_options={"include_usage": True}` 让**最后一个**
  chunk 携带用量；方舟的 `include_usage=True` 会在 `data: [DONE]` 前追加一个
  `choices=[]` 的 usage chunk，另有非 OpenAI 标准的 `chunk_include_usage=True` 可让每个
  chunk 带累计用量。不能把方舟的逐块 usage 行为假设成百炼也支持。
  [百炼流式参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、
  [方舟流式参数](https://www.volcengine.com/docs/82379/1494384)。

### Chat 与 Responses 的实现映射

| 语义 | Chat Completions | Responses API |
|---|---|---|
| endpoint | `/chat/completions` | `/responses` |
| 输入 | `messages=[{"role": ..., "content": ...}]` | `input`（字符串或消息数组）+ 可选 `instructions` |
| 输出上限 | 新实现优先 `max_completion_tokens`；百炼已把 `max_tokens` 标为即将废弃，方舟同时列出两者 | `max_output_tokens` |
| 非流式文本 | `choices[0].message.content` | SDK 便捷属性 `output_text`，原始结构在 `output[]` |
| 完成/截断 | `choices[0].finish_reason`（如 `stop`/`length`） | 顶层 `status` 与 `incomplete_details` |
| usage | `prompt_tokens` / `completion_tokens` / `total_tokens` | `input_tokens` / `output_tokens` / `total_tokens` |
| 流式文本 | `choices[].delta.content` | `response.output_text.delta` 事件的 `delta` |
| 多轮上下文 | 调用方重传完整 `messages` | 可用 `previous_response_id` 由服务端关联 |

以上迁移关系由百炼官方迁移指南直接给出；该指南同时说明 Responses 提供内置工具、更灵活的
输入与 `previous_response_id` 上下文管理。
[百炼 Chat → Responses 迁移指南](https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api)。
方舟则分别公开 Chat 与 Responses API：Responses 的 endpoint 为
`POST https://ark.cn-beijing.volces.com/api/v3/responses`，请求使用 `input`，返回顶层
`status`、`output[]` 与 `usage.input_tokens/output_tokens`。
[方舟创建 Response](https://www.volcengine.com/docs/82379/1569618)。

### 关闭思考不能跨模式照搬参数

| 厂商 | Chat Completions | Responses API |
|---|---|---|
| 方舟 | `extra_body={"thinking": {"type": "disabled"}}` | `extra_body={"thinking": {"type": "disabled"}}` |
| 百炼 | 对当前 Qwen 3.7 目标模型使用 `extra_body={"enable_thinking": False}` | 使用 `reasoning={"effort": "none"}`；其优先级高于将逐步废弃的 `enable_thinking` |

方舟官方 OpenAI SDK 示例明确通过 `extra_body` 传 `thinking.type=disabled`。
[方舟 OpenAI SDK 兼容指南](https://www.volcengine.com/docs/82379/1330626)。百炼 Chat 参数表明确
`enable_thinking` 适用于 Qwen 3.7 且在 Python SDK 中须放入 `extra_body`；Responses 参数表则
推荐 `reasoning.effort`，并说明只处理其文档明确列出的参数。
[百炼 Chat 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、
[百炼 Responses 参数与兼容限制](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)。

### Responses 支持范围不是 Chat 支持范围的同义词

- 百炼 Chat 文档覆盖 Qwen 大语言模型等更广的模型族；截至检索日，其 Responses 兼容指南
  另行列出支持模型。当前项目的 `qwen3.7-plus`、`qwen3.7-max`、`qwen3.8-max` 都在该
  Responses 列表中，但不能据此推断所有 Chat 模型都支持 Responses。
  [百炼 Responses 支持模型](https://help.aliyun.com/zh/model-studio/compatibility-with-openai-responses-api)。
- 方舟官方 API 目录同时提供 Chat 与 Responses endpoint，但本次查看的通用 API 页面没有承诺
  “每个 Chat 模型都支持全部 Responses 参数”。切换模式仍需按具体模型/版本核验能力，尤其是
  `thinking`、结构化输出与工具调用。
  [方舟 Chat API](https://www.volcengine.com/docs/82379/1494384)、
  [方舟 Responses API](https://www.volcengine.com/docs/82379/1569618)。

## Limitations and unknowns

1. 本次只以 2026-08-11 可见的阿里云百炼与火山引擎方舟官方文档为证据，没有把 OpenAI
   原站行为或第三方 SDK 行为外推给云厂商兼容接口；兼容接口以后可能继续调整字段和默认值。
2. 官方通用 API 参考不能替代具体模型卡。每个模型/快照对 `max_completion_tokens`、
   `reasoning_effort`、结构化输出、工具调用的支持仍可能不同；未知能力必须在 smoketest 中
   实测，不能用另一模型的成功结果代替。
3. Chat 与 Responses 的 token 计数口径和思考预算并非所有模型都完全一致。实现可以归一化
   元数据字段名，但不应声称两种 API 的计费或有效采样过程逐 token 等价。
4. 百炼 Responses 明确规定未列出的 OpenAI 参数会被忽略；“请求成功”不能单独证明某个兼容
   参数生效。该限制不能反向推断到 Chat，Chat 应以它自己的参数表为准。

## Decision impact

- `--APImode` 的默认值可设为 Chat Completions，但内部必须为两种 API 保留独立的请求构造与
  响应解析分支；只在分支外暴露统一的文本、usage、完成状态和原始响应接口。
- 将系统指令映射为 Chat 的首个 `system` message，将翻译输入映射为后续 `user` message；
  Responses 分支继续使用 `instructions` + `input`。
- Chat 的输出预算应使用 `max_completion_tokens`，Responses 继续使用
  `max_output_tokens`；不要把同一个关键字原样发送给两个 endpoint。
- smoketest 必须至少断言：正文非空、未因 `length` 截断、思考已关闭、usage 可解析；如测流式，
  还要分别覆盖 Chat chunk 与 Responses event 的拼接路径。
- 2026-08-05 下文“优先 Responses / 维持现状”的结论是当时结构化输出实验的历史建议；当前
  2026-08-11 任务已将产品默认改为 Chat Completions。旧实验数据仍可用于解释能力差异，但不再
  定义默认 API 模式。

## .env 字段说明
```
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 不要带 /responses
ARK_API_KEY=...
ALI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALI_API_KEY=...

MODEL_DEEPSEEK_V4_FLASH=deepseek-v4-flash-260425
MODEL_DEEPSEEK_V4_PRO=deepseek-v4-pro-260425
MODEL_DOUBAO_SEED_2_1_TURBO=doubao-seed-2-1-turbo-260628
MODEL_QWEN37_PLUS=qwen3.7-plus
MODEL_QWEN37_MAX=qwen3.7-max
MODEL_QWEN38_MAX=qwen3.8-max

DEFAULT_MAX_OUTPUT_TOKENS=   # 可留空
```

### 采样参数：默认不发送（2026-08-05 变更）

`temperature` / `top_p` **不再从 `.env` 读取**，默认也**不写入请求体**，直接用服务端默认。
要调就在命令行显式给值：

```bash
python3 main.py run --model qwen3.8-max in.srt --temperature 0.2 --top-p 0.8
```

- `.env` 的 `DEFAULT_TEMPERATURE` / `DEFAULT_TOP_P` 已废弃，留着也不生效。
- `--no-temp` / `--no-top-p` 两个开关已删除——现在「不传」就是默认，不需要开关。
- 理由：采样条件藏在 `.env` 里会让六模型对比实验的变量不可见且容易漂移；
  显式写在命令行里，run 的 meta 里也留得下痕迹。
- `0` 是合法值，会照发，不会被当成「没给」。

### 官方对 temperature / top_p 的定位（2026-08-06 补记）

两家都支持这两个参数，且**都不是被静默忽略的那一类**——阿里云 Responses
「创建响应」参数表明确列出了 `temperature` 与 `top_p`（对照 `response_format`
根本没出现在表里，属于文档开头「未提及的参数会被忽略」的范围）。

| 参数 | Ark（火山方舟） | 阿里云（百炼） |
|---|---|---|
| `temperature` | 0 – 2.0 | \[0, 2) |
| `top_p` | 0 – 1.0 | (0, 1.0\] |

**两家口径一致：建议只设其中一个。** 阿里云在参数表两处、以及《文本生成-概述》
「控制回复多样性」一节都写了「为准确评估参数效果，建议每次只调整一个」。
理由是两者都作用在同一个 token 概率分布上，叠加后效果无法归因。

#### 官方按场景的 temperature 建议

阿里云《文本生成-概述》给出的 `SCENARIO_CONFIGS`：

| 场景 | temperature | top_p |
|---|---|---|
| 创造性写作 | 0.9 | 0.95 |
| 代码生成 | 0.2 | 0.8 |
| 事实性问答 | 0.1 | 0.7 |
| **翻译** | **0.3** | **0.8** |

火山方舟《语言模型》只给定性描述，方向一致：低温「严谨保守，适合有标准答案的
任务，如事实问答、代码生成、逻辑推理」；高温「随机、有创意，适合写诗歌故事」。
字幕翻译属于前者一侧但需保留口语自然度，**0.3 是合理起点**。

#### 是否移除 `top_p`？——不移除，改为警告

官方建议「只设其一」，但代码里删掉 `top_p` 是过度反应：

1. 官方自己的 `SCENARIO_CONFIGS` 翻译档就同时给了 (0.3, 0.8)。硬互斥会让
   **官方推荐配置无法复现**。
2. 六模型质量消融需要自由度。删掉一个参数就等于替未来的实验做了决定。
3. 两个 API 都合法接受，删掉是本地自我设限，不是遵从文档。

因此保留两个参数，**同时显式指定时在 stderr 打一条警告**
（`main.warn_if_both_sampling`），把官方建议传达到位而不阻断。
日常用法：**只调 `--temperature`，`top_p` 留给服务端默认。**

```bash
# 推荐（翻译场景）
python3 main.py run --model qwen3.8-max in.srt --temperature 0.3
# 复现官方配置：可行，但会打一条 warning
python3 main.py run --model qwen3.8-max in.srt --temperature 0.3 --top-p 0.8
```

## 输出校验：契约 vs 成片质量（2026-08-05 新增）

结构化输出既然不用（见下文调查结论），格式与对齐就全靠本地校验。两层分工不同：

| 层 | 模块 | 违反后果 |
|---|---|---|
| **契约** | `pipeline/validate.py` + `pipeline/src_align.py` | error → 触发重试 / 拆批 |
| **成片质量** | `pipeline/subtitle_check.py` | 只度量，不阻断 |

### src 回显对齐（原 warning，已改为 error）

成片原文**始终取本地 `Cue.text`**，模型回显的 `src` 从不进产物——所以 `src` 唯一的
用途就是验证「这条译文确实对应这条原文」。**字幕翻译最危险的事故是整批错位**：
键齐全、`tr` 非空、JSON 合法，但第 37 条的译文其实是第 36 条的。
`json_schema` / `json_object` 对此**零作用**，只能靠回显比对。

原实现把不匹配一律记为 warning，`ok` 仍为 True，错位会直接进 `bilingual.srt`。
现按严重度分级（`pipeline/src_align.py`）：

| 判定 | 含义 | 处理 |
|---|---|---|
| `ok` | 归一化后相等（NFKC+小写+去标点+压空白） | 静默 |
| `drift` | 相似度 ≥ `SRC_DRIFT_THRESHOLD`(0.85) | warning |
| `misaligned` | 归一化后**恰好等于本批另一条**的原文 | **error** |
| `mismatch` | 既不像本条也不是别条 | **error** |
| `missing` | 没回显 | warning |

- 「Marcel ?」→「Marcel?」这类标点/空白变体判 `ok`，不产生噪音。
- 重复台词（字幕里很常见）先判自身相等，不会被索引误判为错位。
- error 会自动复用既有的重试 / 拆批链路（`pipeline/retry.py`）。
- 需要退回旧行为：`validate_response(..., strict_src=False)`，
  或改 `pipeline/config.py: STRICT_SRC_DEFAULT`。

### 译文侧字幕约束（CPS / 行长 / 行数）

Stage A 的 rules 引擎只管**英文原文**的行长与切分，中文译文出来后此前没有再校验。
`pipeline/subtitle_check.py` 补上，默认阈值取 Netflix 简体中文规范：

| 项 | 默认 | 常量 |
|---|---|---|
| 阅读速度 | 9 字/秒 | `ZH_MAX_CPS` |
| 每行字数 | 16 | `ZH_MAX_CHARS_PER_LINE` |
| 行数 | 2 | `ZH_MAX_LINES` |
| 单条时长 | 0.833 – 7.0 秒 | `ZH_MIN/MAX_DURATION_SEC` |

**刻意做成度量而非硬门禁**：译文偏快偏长片子照样能出，不该阻断；而本仓库要跑
六模型质量消融（`docs/quality_ablation_plan.md`），「超速条数占比」「p95 CPS」
这类数字横向对比才有区分度，做成 pass/fail 反而把信息压没了。
聚合结果进 `meta.json` 的 `subtitle_quality`，超标时同时在 `validate.warnings` 留一行。

## 关闭思考
```
Ark Chat / Responses: extra_body={"thinking": {"type": "disabled"}}
Aliyun Chat:           extra_body={"enable_thinking": False}
Aliyun Responses:      reasoning={"effort": "none"}  # 优先于 enable_thinking
```

# 注意：
- 代码语言： Python
- 统一使用: OpenAI SDK
- 思考开关: 必须禁用
- 默认 API 模式：Chat Completions（2026-08-11 起）；Responses 作为显式兼容模式保留
- `top_p`：默认 OMIT，不写入请求；仅显式指定时发送
- `temperature`：默认 OMIT，不写入请求；仅显式指定时发送

# max_output_tokens / max_tokens 调查结论（2026-08-05 实测）

> Responses API 参数名是 **`max_output_tokens`**（不是 Chat 的 `max_tokens` / `max_completion_tokens`）。

## 六模型支持情况

> 下表 **最大输入 / 最大输出** 取自厂商文档（绝对上限）；**省略 max_out 时默认** 为本项目 Responses API 实测回显。  
> 输入+输出一般共享同一上下文窗口；思考模式下思维链也计入输出预算。

| 模型 | 厂商 | 上下文窗口 | 最大输入 | 最大输出 | max_output_tokens | 省略时默认 | 备注 |
|---|---|---:|---:|---:|---|---|---|
| deepseek-v4-flash-260425 | Ark | **1024k (1M)** | **1024k** | **384k** | ✅ | **32768**（实测回显） | 方舟文档：最大回答默认 4k、上限 384k；最大思维链 384k。过小会 `incomplete` / `reason=length` |
| deepseek-v4-pro-260425 | Ark | **1024k (1M)** | **1024k** | **384k** | ✅ | **32768** | 同上（与 Flash 同档上下文/输出上限） |
| doubao-seed-2-1-turbo-260628 | Ark | **256k** | **256k** | **256k** | ✅ | **32768** | 方舟文档：最大回答默认 4k、上限 256k；最大思维链 256k |
| qwen3.7-plus | Aliyun | **1000k (1M)** | **991808** | **131072 (128k)** | ✅ | `null`（走模型最大） | 思考模式：最大输入 983616、最大输出 131072、最大思维链 262144；**min max_output_tokens=16** |
| qwen3.7-max | Aliyun | **1000k (1M)** | **991808** | **131072 (128k)** | ✅ | `null` | 同 Plus 的上下文限制表（百炼模型信息页） |
| qwen3.8-max | Aliyun | **1000k (1M)** | **991808** | **131072 (128k)** | ✅ | `null` | 同 3.7-plus/max 的上下文限制表 |
| **Claude Opus 4.6**（仅参考，非本项目） | Anthropic | **1M** | **1M**（与上下文同窗） | **128k** | ✅（API: `max_tokens`） | 由请求指定 | 输入+输出共享 1M window；官方 2026-02 起；长上下文计费以 Anthropic pricing 为准 |

**文档来源（便于复核）**

| 来源 | 说明 |
|---|---|
| [火山方舟 · 模型列表](https://www.volcengine.com/docs/82379/1330310) | deepseek-v4-*-260425：上下文/最大输入 1024k，最大回答 384k；doubao-seed-2-1-turbo-260628：256k / 256k / 256k |
| [DeepSeek 官方 · 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) | V4 Flash/Pro：上下文 1M，输出最大 384K（与方舟一致） |
| [百炼 · qwen3.7-plus](https://help.aliyun.com/zh/model-studio/qwen3-7-plus) / [qwen3.7-max](https://help.aliyun.com/zh/model-studio/qwen3-7-max) / [qwen3.8-max](https://help.aliyun.com/zh/model-studio/qwen3-8-max) | 上下文 1M；最大输入 991808；最大输出 131072 |
| [Anthropic · Opus 4.6](https://www.anthropic.com/news/claude-opus-4-6) / [Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows) | 1M 上下文；单次最大输出 128k |

## 是否会影响结果？

**会，但只在「撞上限」时影响内容；正常未截断时不改采样结果。**

| 场景 | 影响 |
|---|---|
| 不传 / 设得足够大，且输出未触顶 | **不影响**文本内容。只是上限，不保证凑满，也不改变 temperature 采样分布 |
| 设得过小，输出被截断 | **影响**：`status=incomplete`，`incomplete_details.reason=length`，译文可能半截、漏行、JSON 残缺 |
| **开启思考** + 预算偏小 | **严重影响**：思考 token 与回答共享 `max_output_tokens` 预算；思考耗尽后可能只剩 0 字符回答（本项目历史教训） |
| 计费 | **不传更大的 max 不会多收费**——按实际生成 token 计费，不是按上限预扣 |

## 本项目建议

- **思考必须关**（见下），因此翻译场景下 max 主要防「长批次截断」
- 字幕批次翻译：建议显式设 `max_output_tokens`（如 8192，与历史 Chat `max_tokens=8192` 对齐），避免默认策略随厂商变更
- 连通性烟测：可用 `max_output_tokens=16` + 极短 prompt，单次个位数 token
- 阿里云：`max_output_tokens >= 16`

# 结构化输出 json_schema 调查结论（2026-08-05 实测）

> 探测脚本：`python3 scripts/probe_json_schema.py [--control]`
> 先实测、后与官方文档对账（2026-08-05 复核）。文档与实测**完全一致**，
> 官方文档见 `docs/火山方舟_结构化输出(beta)_1782549049.pdf`、
> `docs/火山方舟_创建Response_1784703795.pdf`、`docs/阿里云-结构化输出.md`、
> `docs/阿里云-OpenAI兼容-Responses.md`。

## 参数写法（两套 API 字段名不同）

Responses API — schema 平铺在 `text.format` 内，**没有** `json_schema` 这层嵌套：

```python
client.responses.create(
    model=..., input=..., instructions=...,
    text={"format": {
        "type": "json_schema",
        "name": "subtitle_batch",
        "schema": {...},
        "strict": True,
    }},
)
```

Chat Completions — schema 包在 `response_format.json_schema` 里，**多一层**：

```python
client.chat.completions.create(
    model=..., messages=[...],
    response_format={"type": "json_schema", "json_schema": {
        "name": "subtitle_batch",
        "schema": {...},
        "strict": True,
    }},
)
```

### `strict` 放在哪一层？（文档里有两种写法，不是矛盾）

- **OpenAI 兼容接口**（本项目走的就是这条）：`strict` 在 `json_schema` **里面**，
  与 `name`/`schema` 平级。阿里云《结构化输出》OpenAI-compat 示例即此写法，
  实测也是这个写法被强制执行。
- **DashScope 原生 SDK**（`dashscope.MultiModalConversation.call`）：`strict` 在
  `response_format` **外层**，与 `json_schema` 平级（该文档概览表与原生示例用的是这种）。

本项目只用 OpenAI SDK，因此一律取「里面」那种。

## 六模型实测矩阵

「强制」= 负对照判据：schema 里放一个 **prompt 从未提及**的必填字段
`zzz_schema_probe`，输出里出现它才证明是按 schema 约束解码，而不是模型碰巧
听了 prompt 的话。这一步是关键——不做负对照会得到完全相反的结论。

| 模型 | 厂商 | Responses `text.format` | Chat `response_format` |
|---|---|---|---|
| deepseek-v4-flash | Ark | ✅ 强制 | ⚠️ **间歇 400**（5 次 1 次 `InvalidParameter: response_format.type not valid`） |
| deepseek-v4-pro | Ark | ✅ 强制 | ✅ 强制 |
| doubao-seed-2-1-turbo | Ark | ✅ 强制 | ✅ 强制 |
| qwen3.7-plus | Aliyun | ❌ **静默忽略** | ✅ 强制 |
| qwen3.7-max | Aliyun | ❌ **静默忽略** | ✅ 强制 |
| qwen3.8-max | Aliyun | ❌ **静默忽略** | ✅ 强制 |

**最危险的一格是阿里云的「静默忽略」**：请求 200、无任何 warning，schema 就是不生效。
qwen3.7-plus 在 `strict=True` 且 schema 要求 `{src, tr}` 的情况下，直接回了扁平字符串
`{"12":"马塞尔？",...}`——契约被破坏而调用方毫无察觉。qwen3.8-max 连测 3 次全部忽略，
稳定复现，不是抖动。

### 文档对账（每一格都有官方依据）

| 实测结果 | 官方依据 |
|---|---|
| Ark 三个模型 Responses `text.format` ✅ | 《结构化输出(beta)》给出的正是 `text.format = {type, name, strict, schema}` 写法，与探针逐字一致；《创建 Response》参数表列有 `text.format.type`（text/json_schema/json_object）、`.name`、`.schema`、`.strict`。支持范围：**250615 及之后版本**的大语言模型 |
| 阿里云三个模型 Chat `response_format` ✅ | 《结构化输出》明确 JSON Schema 模式支持模型为「Qwen3.7-Plus 系列、Qwen3.7-Max 系列、Qwen3.8-Max 系列」——正好是我们这三个 |
| 阿里云 Responses **静默忽略** ❌ | **官方明文写了这个行为**。《OpenAI兼容-Responses创建响应》「兼容性说明与限制」原文：「**请求将仅处理本文档明确列出的参数，任何未提及的 OpenAI 参数都会被忽略**」；而该文档的请求体参数表里**没有** `text` / `text.format` / `response_format` / `json_schema`（`text` 只作为**响应**字段出现）。《结构化输出》全文检索 `responses.create` = **0 次**，全部示例走 `chat.completions`。即：阿里云结构化输出**只在 Chat 端存在**，Responses 端未实现，未列出的参数按规定被丢弃——所以 200 而不生效 |

### 文档补充的两条限制（实测未覆盖）

- **Ark 结构化输出仍是 beta**，官方原文「受资源与平台负载影响，服务可用性可能随访问
  情况产生波动，**请谨慎在生产环境使用**」。这独立支持下面第 4 条「维持现状」。
- **Ark 不支持的部署形态**：在线推理（TPM 保障包）不支持结构化输出；
  doubao-seed-1.8 之前版本通过模型单元部署时同样不支持。
- **阿里云要求开结构化输出时不要设 `max_tokens`**（会截断成非法 JSON）。这与下面第 3 条
  是同一件事的两面：schema 管语法，不管截断。

## json_object 实测（2026-08-05）：覆盖率比 json_schema 更差，2/6

> 探测脚本：`python3 scripts/probe_json_object.py`

判据是**反向对照**：instructions 明令「一句英文散文，禁止 JSON 与花括号」，
再加 `text={"format":{"type":"json_object"}}`。输出被迫成 JSON = 端上真强制；
仍是散文 = 参数被静默忽略。（不做这个反向对照就只能测出「模型听不听话」。）

| 模型 | 厂商 | `json_object` | 对比 `json_schema` |
|---|---|---|---|
| deepseek-v4-flash | Ark | ✅ 强制（覆盖了 prompt） | ✅ 强制 |
| deepseek-v4-pro | Ark | ✅ 强制（覆盖了 prompt） | ✅ 强制 |
| doubao-seed-2-1-turbo | Ark | ❌ **静默忽略**（3/3 复现） | ✅ 强制 |
| qwen3.7-plus | Aliyun | ❌ 静默忽略 | ❌ 静默忽略 |
| qwen3.7-max | Aliyun | ❌ 静默忽略 | ❌ 静默忽略 |
| qwen3.8-max | Aliyun | ❌ 静默忽略 | ❌ 静默忽略 |

> **doubao 那一格是文档没写的坑**：同一模型 `json_schema` 强制、`json_object` 忽略，
> 而方舟文档把两者列在同一张「支持模型」表下。再次印证该能力仍是 beta。

不采用 json_object 的理由（比 json_schema 更硬）：

1. 覆盖率更差（2/6 vs 3/6），保证还更弱——只管「是合法 JSON」，不管键集与字段。
2. 边际收益≈0：它实际只挡代码围栏和前言废话，而 `json_repair.strip_code_fence`
   已免费处理这两样。
3. 挡不住截断，`repair.py` 一行省不掉。
4. **会污染消融实验**：见 `docs/quality_ablation_plan.md`。开一个「2 个模型生效、
   4 个静默无效」的参数，六模型就不再走同一条路径，测出的格式合规率差异
   将不再是模型能力差异。做对比实验，一致性优先于局部最优。

## 对本项目的直接结论（2026-08-05 历史实验结论）

> 本节解释当时为何在结构化输出实验中建议维持 Responses 路径；它已被上文
> 2026-08-11 的 Chat Completions 默认模式决策取代，不再定义当前默认 API 模式。

1. **不存在「一套写法通吃六模型」**。要上 json_schema，必须按 provider 分叉：
   Ark 走 Responses `text.format`，阿里云走 Chat `response_format`。
   而后者会**丢掉 `reasoning={"effort":"none"}` 这个已验证的关思考开关**（chat 端只能退回
   `enable_thinking:false`，本仓库文档已记其「可能无效」）——用格式保证换回思考风险，
   得不偿失。
2. **schema 必须按批动态生成**：输出契约是 `{cue_id: {src, tr}}`，键随批次变化；
   strict 模式要求 `properties` 全枚举 + `required` 全列 + `additionalProperties:false`，
   所以 schema 只能由本批 `input_map.keys()` 现生成。30 键规模实测通过（Ark Responses
   与 Ali Chat 均 30/30 键齐全）。
3. **json_schema 不能替代 `max_output_tokens` 与截断重试**：30 条批次在
   `max_output_tokens=1024` 下照样被截断成半截 JSON——schema 约束的是**语法**，
   不阻止 `length` 截断。`pipeline/repair.py` 仍然必要。
4. 综合建议：**维持现状**（Responses + prompt 契约 + `json_repair`/`validate` 兜底）。
   `json_schema`（3/6）与 `json_object`（2/6）都做不到「一套配置六模型行为一致」，
   而它们能给的保证已被现有四层防御覆盖。六模型走完全相同的代码路径，
   这对质量消融实验是硬要求。
   json_schema 只在 Ark 三个模型上是净收益，阿里云侧要么无效、要么得牺牲关思考；
   且 Ark 侧该能力官方自述仍为 beta、不建议用于生产。
   若将来只跑 Ark 且 beta 转正，可考虑对 Ark 分支启用 Responses `text.format`。

## 思考关闭方式（实测有效）

| 厂商 | 正确关闭 | 注意 |
|---|---|---|
| Ark | `extra_body={"thinking": {"type": "disabled"}}` | 仅 `enable_thinking:false` 可能无效 |
| Aliyun | `reasoning={"effort": "none"}` | 优先于 `enable_thinking`；**默认档位就是 `xhigh`** |

**官方文档已确认（《OpenAI兼容-Responses创建响应》参数表）：**

- Ark：「**未传入 `thinking` 时，模型默认开启深度思考**；如需关闭，需显式将子字段 `type`
  设置为 `disabled`」。取值 `enabled` / `disabled` / `auto`。
- 阿里云：`reasoning.effort` 共 7 档递增 `none` < `minimal` < `low` < `medium` < `high`
  < `xhigh` < `max`，**默认值 `xhigh`**——即不传就是次高档思考。
  「`reasoning.effort` 的优先级高于 `enable_thinking`，建议优先使用 `reasoning.effort`，
  `enable_thinking` 后续将不再支持。」`enable_thinking` 还是非 OpenAI 标准参数，
  Python SDK 得靠 `extra_body` 传。
  → 本项目用 `reasoning={"effort":"none"}` 是文档推荐写法，且是**唯一**长期可用的写法。
  （`xhigh`/`max` 两档仅华北2（北京）与新加坡可用，与我们无关。）

两家的 `max_output_tokens` 都把**思维链算进输出预算**（Ark 原文：「计入的输出 token
包含模型回答与思维链内容之和」）。这就是关思考在本项目属于硬性前提、而非优化项的原因。

### 六模型验证结果（2026-08-05，`python3 scripts/smoke_thinking.py --control`）

判据三项全过：`status=completed` + `reasoning_tokens=0` + 无 `reasoning` output item。
正路径经 `model_client.call`（即线上代码路径），对照组绕过它不传关闭参数。

| 模型 | 关思考（线上路径） | 对照组 reasoning_tokens |
|---|---|---:|
| deepseek-v4-flash | ✅ 0 | 172 |
| deepseek-v4-pro | ✅ 0 | 172 |
| doubao-seed-2-1-turbo | ✅ 0 | 484 |
| qwen3.7-plus | ✅ 0 | **537** |
| qwen3.7-max | ✅ 0 | 173 |
| qwen3.8-max | ✅ 0 | 104 |

**通过 6/6。** 对照组证明两个关闭参数确实起作用——不传时一道 1 句话的算术题就烧掉
100–537 个思维链 token；字幕批次（`max_output_tokens=8192`）下这足以吃光输出预算。

> 附带观察：`deepseek-v4-pro` 虽然 `reasoning_tokens=0`，但会把推理过程写进**正文**
> （无视 "Reply with only the number"）。这是指令遵循问题，不是思考开关问题；
> 翻译主流程靠 JSON 契约约束输出，暂不受影响。

## 代码入口

- 统一调用模块：`model_client.py`（密钥只读 `.env`）
- 烟测：`python3 model_client.py`

## S01E03 整集一次发送可行性（基于本集实测规模）

| 项 | 数值 |
|---|---|
| Cue 数 | **747** |
| 全文件 | ~56KB / ~56k 字符 |
| 输入粗估 | ~**16k tokens**（裸 SRT）+ prompt |
| 纯中文输出粗估 | ~**18k–36k tokens**（分词差异大） |
| 双语（时间码 + EN + ZH）粗估 | ~**29k–40k tokens** |

结论摘要：
1. **输入侧**：六模型上下文远大于 ~20k，**可以**整集塞进一次请求。
2. **`max_output_tokens=32768` 双语一次返回**：**不建议视为可行**。双语输出贴近/超过 32k，截断风险高；纯中文也偏紧。
3. **更合理分批**：按 cue **30–50 条/批**（历史经验 30）；双语若仍用 32k 上限，技术上可到 100–150，但质量与漏译风险上升。输出格式尽量「只回译文行 + 序号/时间码」，由本地拼 SRT。
