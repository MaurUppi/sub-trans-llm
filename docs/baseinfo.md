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
```

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

DEFAULT_TEMPERATURE=1.0
DEFAULT_TOP_P=1.0
DEFAULT_MAX_OUTPUT_TOKENS=   # 可留空
```

## 关闭思考
```
Ark:    extra_body={"thinking": {"type": "disabled"}}
Aliyun: reasoning={"effort": "none"}   # 优先于 enable_thinking
```

# 注意：
- 代码语言： Python
- 统一使用: OpenAI SDK
- 思考开关: 必须禁用
- 优先选择: Responses API
- top_p=1.0
- temperature=1.0

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

## 思考关闭方式（实测有效）

| 厂商 | 正确关闭 | 注意 |
|---|---|---|
| Ark | `extra_body={"thinking": {"type": "disabled"}}` | 仅 `enable_thinking:false` 可能无效 |
| Aliyun | `reasoning={"effort": "none"}` | 优先于 `enable_thinking`；默认 effort 可能很高 |

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

