# 待办：gpt-5 推理过程（Thinking）流式透传前端

> 状态：待开发  
> 创建：2026-05-28  
> 关联接口：`POST /v1/conversations/{conversation_id}/messages:stream`

## 背景

当前流式对话走 **OpenAI Chat Completions**（`chat.completions.create(stream=True)`），仅转发 `delta.content` 为 SSE 事件 `message.delta`。

对 **gpt-5** 等推理模型：

- 首字前会有较长等待（内部 reasoning），但 Chat Completions 流中 **不包含** thinking 文本；
- 前端无法展示「思考中 / 思考过程」，用户体验与 ChatGPT 不一致。

OpenAI **Responses API** 可流式返回推理摘要，例如 `response.reasoning_summary_text.delta`，之后才是正式回答。

## 目标

- 使用 gpt-5（及后续推理模型）时，将 **thinking / reasoning 过程** 实时通过 SSE 传给前端；
- 正式回答仍通过现有 `message.delta`（或明确区分的事件）流式输出；
- 非推理模型（如 `gpt-4o-mini`）保持现有 Chat Completions 路径，避免无谓改造。

## 现状（已实现）

| 能力 | 状态 |
|------|------|
| Chat Completions 流式正文 | ✅ `message.delta` |
| 知识库检索事件 | ✅ `retrieval.started` / `retrieval.completed` |
| 推理过程 SSE | ❌ 未实现 |
| Responses API 接入 | ❌ 未实现 |

## 建议方案（概要）

### 1. 路由与模型分流

- 在 `OpenAIChatService`（或新建 `OpenAIResponsesService`）中：
  - `gpt-5`、`o*` 等推理模型 → **Responses API** + `reasoning` 参数（如 `effort`、`summary`）；
  - 其它模型 → 继续 **Chat Completions**。

### 2. 新增 SSE 事件（需与前端对齐）

建议约定（名称可评审后定稿）：

| 事件 | 说明 |
|------|------|
| `reasoning.started` | 开始推理（可选，便于 UI 展示「思考中」） |
| `reasoning.delta` | 推理摘要增量，`data.delta` 为文本片段 |
| `reasoning.completed` | 推理阶段结束 |
| `message.delta` | 正式回答增量（保持现有语义） |

`message.created` / `usage.completed` / `message.completed` / `done` 保持不变。

### 3. 后端改动点（ checklist ）

- [ ] `app/services/openai_chat_service.py`：抽象流式输出为「推理块 + 正文块」，或新增 Responses 专用 client
- [ ] `app/services/conversation_service.py`：`stream_message` 消费推理流并 yield 对应 `SseEvent`
- [ ] `app/api/conversation_routes.py`：文档注释 / OpenAPI 描述新事件（若维护 schema）
- [ ] `app/core/config.py`：可选配置项（如 `OPENAI_REASONING_EFFORT`、`USE_RESPONSES_API_FOR_MODELS`）
- [ ] 持久化：是否在 `MessageRecord` 中保存 `reasoning` 字段（供历史消息回放）
- [ ] 测试：`tests/fake_openai_chat.py` 模拟 reasoning 事件；集成测试 mock Responses 流

### 4. 前端协作

- [ ] 确认 UI：思考区与回答区布局、是否可折叠
- [ ] 订阅 `reasoning.delta` 与 `message.delta`，勿再用「长时间无事件 = 卡住」误判
- [ ] 取消生成：cancel 时同时中止 Responses 流（与现有 `cancel_event` 对齐）

## 验收标准

1. `modelId=gpt-5` 流式请求时，在首个 `message.delta` **之前** 能收到至少一条 `reasoning.delta`（有推理摘要时）。
2. 正式回答仍逐字/增量到达，不改为「全文结束后一次性返回」。
3. `modelId=gpt-4o-mini` 行为与现网一致，无 regression。
4. 相关单元测试通过；`pytest tests/test_conversation_stream.py` 覆盖新事件顺序。

## 风险与说明

- **API 差异**：Chat Completions 与 Responses 的 usage、错误码、取消语义需分别处理。
- **延迟**：推理阶段仍会占用时间，但前端可展示进度，而不是空白等待。
- **隐私**：`reasoning.summary` 为摘要而非完整 chain-of-thought，需产品确认是否满足合规展示要求。
- **费用**：Responses + reasoning 的 token 计费与 Completions 不同，需运维/产品知晓。

## 参考

- 本地验证：Responses API 可出现 `response.reasoning_summary_text.delta`（见开发环境对 `gpt-5` 的探测）。
- 现有实现：仅读取 `chunk.choices[0].delta.content`（`openai_chat_service.py`）。

## 非本任务范围（可另开）

- `/v1/models` 列表与真实可调用模型对齐（Claude/Gemini 占位问题）
- 流式接口默认 `modelId` 从 `gpt-5` 改为 `gpt-4o-mini` 的争议项
