# CAN-RAG 前端 API 联调指南

> **受众**：前端工程师  
> **Base URL（本地）**：`http://127.0.0.1:8000`  
> **更新**：2026-05-30（含 embedding 模型、KB 文件列表修复）  
> **约定**：请求/响应 JSON 字段均为 **camelCase**；时间戳为 ISO 8601（UTC，`Z` 后缀）。

---

## 1. 通用约定

### 1.1 响应 Envelope

**成功（单对象）**：

```json
{
  "data": { },
  "requestId": "req_a1b2c3d4e5f6"
}
```

**成功（分页）**：

```json
{
  "data": [ ],
  "pagination": {
    "page": 1,
    "pageSize": 20,
    "total": 42,
    "hasMore": true
  },
  "requestId": "req_a1b2c3d4e5f6"
}
```

**失败**：

```json
{
  "error": {
    "code": "KB_NOT_FOUND",
    "message": "Knowledge base not found",
    "details": { "kbId": "kb_xxx" }
  },
  "requestId": "req_a1b2c3d4e5f6"
}
```

### 1.2 通用 Header

| Header | 说明 |
|--------|------|
| `Authorization: Bearer {accessToken}` | 需鉴权接口 |
| `Content-Type: application/json` | POST/PATCH  body |
| `Accept: text/event-stream` | SSE 流式问答 |
| `X-Request-Id` | 可选，链路追踪；响应会回显 |
| `Idempotency-Key` | 可选，创建导入任务等幂等 |

---

## 2. 鉴权

### 2.1 登录

```http
POST /v1/auth/login
Content-Type: application/json

{
  "email": "admin@example.com",
  "password": "admin123"
}
```

**响应 200**：

```json
{
  "data": {
    "accessToken": "eyJ...",
    "expiresIn": 3600,
    "user": {
      "id": "user_admin",
      "displayName": "Admin User",
      "email": "admin@example.com",
      "permissions": ["kb:read", "kb:create", "kb:file:upload", "kb:import", "kb:hit_test", "chat:send"]
    }
  },
  "requestId": "req_..."
}
```

Refresh Token 通过 **HttpOnly Cookie** 下发（`/v1/auth/refresh`）。

### 2.2 需要 Bearer Token 的接口

| 模块 | 路径前缀 | 权限示例 |
|------|----------|----------|
| 上传 | `/v1/uploads/*` | `kb:file:upload` |
| 导入任务 | `/v1/knowledge-bases/{kbId}/import-jobs`、`/v1/import-jobs/*` | `kb:import` |
| 文件夹 | `/v1/folders/*` | `folder:read` / `folder:write` |
| 模板 | `/v1/templates/*` | `template:read` / `template:write` |
| 当前用户 | `/v1/auth/me` | 任意有效 token |

**当前无需 Token**（联调友好，生产需加固）：

- 知识库 CRUD、文件 chunks、hit-test
- 会话 / 流式问答 `/v1/conversations/*`

---

## 3. 完整联调流程

```text
GET embedding-models + GET models（对话）
  → 登录 → 创建 KB（带 embeddingModelId）
  → presign → 本地落盘 → complete
  → 创建 import-job → 轮询 job 状态
  → GET files / list chunks → hit-test
  → 创建 conversation → messages:stream（带 knowledgeBaseIds）
```

**时序要点**：

1. presign 返回 `storageKey` 后，开发环境需将文件写入 `{LOCAL_UPLOAD_ROOT}/{storageKey}`（例：`app/storage/uploads/kb/{kbId}/{fileId}.pdf`）
2. import-job 创建后状态 `queued` → `running` → `completed`（约数秒，取决于 Poller/BackgroundTasks）
3. 问答前确保 import `completed` 且 chunks 非空
4. **向量模型**：创建 KB 的 `embeddingModelId` 须来自 `GET /v1/embedding-models`（勿用 `/v1/models` 对话模型）

---

## 4. 模型接口

### 4.0 向量模型列表（知识库创建下拉）

```http
GET /v1/embedding-models
```

**无需鉴权**。用于创建知识库时「向量模型」下拉，**不要**与对话模型 `/v1/models` 混用。

**响应 200**：

```json
{
  "data": [
    {
      "id": "text-embedding-3-small",
      "name": "text-embedding-3-small",
      "provider": "openai",
      "icon": "/models/openai.svg",
      "status": "active",
      "dimensions": 1536,
      "maxInputTokens": 8191,
      "description": "性价比优先，适合大多数 RAG 场景"
    },
    {
      "id": "text-embedding-3-large",
      "name": "text-embedding-3-large",
      "provider": "openai",
      "icon": "/models/openai.svg",
      "status": "active",
      "dimensions": 3072,
      "maxInputTokens": 8191,
      "description": "召回质量更高，维度更大（网关默认推荐）"
    },
    {
      "id": "text-embedding-ada-002",
      "name": "text-embedding-ada-002",
      "status": "deprecated",
      "dimensions": 1536,
      "maxInputTokens": 8191,
      "description": "旧版模型，仅兼容历史知识库"
    }
  ],
  "requestId": "req_..."
}
```

| 字段 | 说明 |
|------|------|
| `id` | 创建 KB 时传 `embeddingModelId` |
| `dimensions` | 模型默认输出维度 |
| `status` | `active` / `deprecated` |

**后端行为**：导入与检索均使用该 KB 绑定的 embedding 模型；若未配置 `OPENAI_API_KEY` 或 `RAG_EMBEDDING_DIMENSIONS < 512`，自动回退本地 hash 占位向量。

### 4.0.1 对话模型列表（聊天下拉）

```http
GET /v1/models?status=active
```

返回 `gpt-4o-mini`、`gpt-4o` 等 **Chat Completions** 模型，供 `messages:stream` 的 `modelId` 使用。

---

## 5. 关键接口明细

### 5.1 创建知识库

```http
POST /v1/knowledge-bases
Content-Type: application/json

{
  "name": "demo-kb",
  "description": "联调知识库",
  "embeddingModelId": "text-embedding-3-small"
}
```

- `embeddingModelId` **可选**；省略时使用服务端默认 `OPENAI_EMBEDDING_MODEL`（通常 `text-embedding-3-large`）
- 传 `gpt-4o` 等对话模型 ID 会 **422** `VALIDATION_ERROR`

**响应 201**：

```json
{
  "data": {
    "id": "ef7dc16a-3653-416a-b3bf-955b35befe0c",
    "name": "demo-kb",
    "description": "联调知识库",
    "fileCount": 0,
    "resourceType": "personal",
    "embeddingModelId": "text-embedding-3-small",
    "updatedAt": "2026-05-30T12:00:00.000000+00:00"
  },
  "requestId": "req_..."
}
```

### 5.1.1 知识库列表 / 删除

```http
GET /v1/knowledge-bases?page=1&pageSize=10&q=demo
DELETE /v1/knowledge-bases/{kbId}
```

列表项含 `fileCount`（来自 Postgres 文件表，已与上传/import 对齐）。

### 5.1.2 知识库文件列表

```http
GET /v1/knowledge-bases/{kbId}/files?page=1&pageSize=10
```

**响应 200**（摘要）：

```json
{
  "data": [
    {
      "id": "file_63aede53e9e34d3f95a93f227eabbf48",
      "name": "policy.pdf",
      "format": "pdf",
      "status": "available",
      "charCount": 592934,
      "uploadedAt": "2026-05-30T12:23:53.000000Z",
      "tags": null
    }
  ],
  "pagination": { "page": 1, "pageSize": 10, "total": 1, "hasMore": false }
}
```

`status`：`available`（已索引）/ `indexing`（导入中）/ `failed`

### 5.2 上传 Presign

```http
POST /v1/uploads/presign
Authorization: Bearer {token}
Content-Type: application/json

{
  "knowledgeBaseId": "kb_f99aa0e4-bf6e-4041-911c-ee5d467d8490",
  "files": [
    {
      "fileName": "policy.txt",
      "mimeType": "text/plain",
      "sizeBytes": 128
    }
  ]
}
```

**响应 201**：

```json
{
  "data": {
    "uploads": [
      {
        "uploadId": "upl_abc123",
        "fileId": "file_3d44a0cc844f47d4a4ef3230281eab96",
        "method": "PUT",
        "uploadUrl": "http://127.0.0.1:8000/dev-upload/...",
        "headers": {},
        "storageKey": "kb/f99aa0e4-bf6e-4041-911c-ee5d467d8490/file_3d44a0cc844f47d4a4ef3230281eab96.txt",
        "expiresAt": "2026-05-30T13:00:00.000000Z"
      }
    ]
  },
  "requestId": "req_..."
}
```

### 5.3 上传 Complete

```http
POST /v1/uploads/upl_abc123:complete
Authorization: Bearer {token}
Content-Type: application/json

{
  "fileId": "file_3d44a0cc844f47d4a4ef3230281eab96",
  "storageKey": "kb/f99aa0e4-bf6e-4041-911c-ee5d467d8490/file_3d44a0cc844f47d4a4ef3230281eab96.txt"
}
```

**响应 200**：

```json
{
  "data": {
    "fileId": "file_3d44a0cc844f47d4a4ef3230281eab96",
    "status": "uploaded"
  },
  "requestId": "req_..."
}
```

### 5.4 创建导入任务

见第 6 节分段配置示例。

**响应 201**（摘要）：

```json
{
  "data": {
    "id": "job_7c8d9e0f",
    "knowledgeBaseId": "kb_f99aa0e4-bf6e-4041-911c-ee5d467d8490",
    "fileIds": ["file_3d44a0cc844f47d4a4ef3230281eab96"],
    "status": "queued",
    "progress": 0,
    "stage": "upload",
    "errorCode": null,
    "errorMessage": null,
    "retryOf": null,
    "createdAt": "2026-05-30T12:01:00+00:00",
    "updatedAt": "2026-05-30T12:01:00+00:00"
  },
  "requestId": "req_..."
}
```

### 5.5 查询导入任务

```http
GET /v1/import-jobs/{jobId}
Authorization: Bearer {token}
```

完成后 `status` 为 `completed`，`stage` 为 `done`，`progress` 为 `100`。

### 5.6 列出文件 Chunks

```http
GET /v1/knowledge-bases/{kbId}/files/{fileId}/chunks?page=1&pageSize=20
```

**响应 200**：

```json
{
  "data": [
    {
      "dataId": "d000000",
      "text": "Retirement policy allows early exit at age 55.",
      "charCount": 45,
      "page": null,
      "chunkIndex": 0,
      "citation": {
        "file_name": "policy.txt",
        "page": null,
        "data_id": "d000000"
      },
      "indexes": [
        {
          "indexId": "d000000-000",
          "text": "文件：policy.txt\nRetirement policy allows early exit at age 55."
        }
      ]
    }
  ],
  "pagination": {
    "page": 1,
    "pageSize": 20,
    "total": 2,
    "hasMore": false
  },
  "requestId": "req_..."
}
```

> `citation` 内层为入库原始 snake_case；SSE citation 为 camelCase（见第 8 节）。

### 5.7 创建会话

```http
POST /v1/conversations
Content-Type: application/json

{
  "title": "Policy Q&A",
  "folder": null,
  "pinned": false
}
```

**响应 201**：

```json
{
  "data": {
    "id": "conv_abc",
    "title": "Policy Q&A",
    "updatedAt": "2026-05-30T12:05:00+00:00",
    "messageCount": 0,
    "preview": "",
    "pinned": false,
    "folder": null
  },
  "requestId": "req_..."
}
```

### 5.8 流式问答

```http
POST /v1/conversations/{conversationId}/messages:stream
Content-Type: application/json
Accept: text/event-stream

{
  "content": "What is the retirement age?",
  "modelId": "gpt-4o-mini",
  "knowledgeBaseIds": ["kb_f99aa0e4-bf6e-4041-911c-ee5d467d8490"]
}
```

---

## 6. 分段配置 import-jobs 示例

### 6.1 default + indexSize

```http
POST /v1/knowledge-bases/{kbId}/import-jobs
Authorization: Bearer {token}
Content-Type: application/json

{
  "fileIds": ["file_3d44a0cc844f47d4a4ef3230281eab96"],
  "chunkStrategy": "default",
  "chunking": {
    "strategy": "default",
    "indexSize": 512,
    "metadata": {
      "includeFileName": true,
      "includeHeadings": false
    }
  }
}
```

### 6.2 custom length 模式

```http
POST /v1/knowledge-bases/{kbId}/import-jobs
Authorization: Bearer {token}
Content-Type: application/json

{
  "fileIds": ["file_3d44a0cc844f47d4a4ef3230281eab96"],
  "chunking": {
    "strategy": "custom",
    "custom": { "mode": "length" },
    "length": {
      "chunkSize": 400,
      "overlap": 50,
      "maxChunkSize": 800
    },
    "indexSize": 256,
    "metadata": {
      "includeFileName": true,
      "includeHeadings": true
    }
  }
}
```

**校验失败示例**（422）：

```json
{
  "error": {
    "code": "IMPORT_INVALID_OPTIONS",
    "message": "Validation failed",
    "details": {}
  },
  "requestId": "req_..."
}
```

---

## 7. SSE 流式问答事件序列

连接建立后，服务端先发送 preamble（`: connected`），随后按序推送：

| 顺序 | event | data 摘要 |
|------|-------|-----------|
| 1 | `message.created` | `{ conversationId, userMessageId, assistantMessageId }` |
| 2 | `retrieval.started` | `{ messageId, knowledgeBaseIds }`（无 KB 绑定时跳过 2–3） |
| 3 | `retrieval.completed` | `{ messageId, citations: [...] }` |
| 4 | `message.delta` | `{ messageId, delta: "单字符" }`（多次，打字机效果） |
| 5 | `usage.completed` | `{ messageId, usage: { promptTokens, completionTokens, totalTokens } }` |
| 6 | `message.completed` | `{ messageId, status: "completed", content: "全文" }` |
| 7 | `done` | `{}` |

**失败 / 取消**：可能出现 `message.failed` 后直接 `done`；取消时 `message.completed.status` 为 `cancelled`，可能无 `usage.completed`。

**示例原始 SSE 片段**：

```text
event: message.created
data: {"conversationId":"conv_abc","userMessageId":"msg_u1","assistantMessageId":"msg_a1"}

event: retrieval.started
data: {"messageId":"msg_a1","knowledgeBaseIds":["kb_f99aa0e4-bf6e-4041-911c-ee5d467d8490"]}

event: retrieval.completed
data: {"messageId":"msg_a1","citations":[{"index":1,"kbId":"kb_...","fileId":"file_...","chunkId":"d000000","page":null,"score":0.87,"snippet":"Retirement policy...","fileName":"policy.txt","type":"text"}]}

event: message.delta
data: {"messageId":"msg_a1","delta":"The"}

event: usage.completed
data: {"messageId":"msg_a1","usage":{"promptTokens":120,"completionTokens":45,"totalTokens":165}}

event: message.completed
data: {"messageId":"msg_a1","status":"completed","content":"The retirement age is 55 [1]."}

event: done
data: {}
```

---

## 8. Citation 对象与深链

### 8.1 字段说明（SSE / 同步消息 `citations[]`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `index` | number | 从 1 开始，对应模型回答中的 `[1]` |
| `kbId` | string | 知识库 ID |
| `fileId` | string | 文件 ID |
| `chunkId` | string | 对应 data 层 `dataId`（如 `d000000`） |
| `page` | number \| null | 页码 |
| `score` | number | 检索分数 |
| `snippet` | string | 召回片段 |
| `fileName` | string | 文件名 |
| `type` | `"text"` \| `"image"` | 文本块或图片描述 |
| `storageKey` | string? | 图片资源键（`type=image` 时） |

### 8.2 原文对照深链（前端路由建议）

```text
/knowledge-bases/{kbId}/files/{fileId}/chunks/{chunkId}
```

- `chunkId` = citation.`chunkId` = API 路径中的 `dataId`
- 打开页时调用 **§9.2** `GET .../chunks/{dataId}?context=1` 展示前后文

---

## 9. 原文对照页 API

### 9.1 分页列表 Chunks

```http
GET /v1/knowledge-bases/{kbId}/files/{fileId}/chunks?page=1&pageSize=10&q=policy
```

Query：`q` 按文本子串过滤；`status` 参数保留但未使用。

### 9.2 单块 + 上下文

```http
GET /v1/knowledge-bases/{kbId}/files/{fileId}/chunks/d000000?context=2
```

**响应 200**：

```json
{
  "data": {
    "target": {
      "dataId": "d000000",
      "text": "Retirement policy allows early exit at age 55.",
      "charCount": 45,
      "page": null,
      "chunkIndex": 0,
      "citation": { "file_name": "policy.txt", "data_id": "d000000" },
      "indexes": [{ "indexId": "d000000-000", "text": "..." }]
    },
    "context": {
      "before": [],
      "after": [
        {
          "dataId": "d000001",
          "chunkIndex": 1,
          "page": null,
          "text": "Second paragraph."
        }
      ]
    }
  },
  "requestId": "req_..."
}
```

`context` 查询参数范围 `0–10`，表示目标块前后各取 N 条 data。

### 9.3 单文件详情（可选）

```http
GET /v1/knowledge-bases/{kbId}/files/{fileId}
```

---

## 10. Hit-Test 接口

```http
POST /v1/knowledge-bases/{kbId}/hit-test
Content-Type: application/json

{
  "query": "retirement age",
  "topK": 5,
  "filters": {
    "fileIds": ["file_3d44a0cc844f47d4a4ef3230281eab96"]
  }
}
```

**响应 200**：

```json
{
  "data": {
    "results": [
      {
        "fileId": "file_3d44a0cc844f47d4a4ef3230281eab96",
        "chunkId": "d000000",
        "score": 0.912,
        "snippet": "Retirement policy allows early exit at age 55.",
        "page": null
      }
    ],
    "latencyMs": 12
  },
  "requestId": "req_..."
}
```

- `topK` 范围：**1–50**
- `filters.fileIds` 可选；传则校验文件属于该 KB

---

## 11. 常见错误码

| code | HTTP | 场景 |
|------|------|------|
| `AUTH_TOKEN_MISSING` | 401 | 未带 Bearer |
| `AUTH_TOKEN_INVALID` / `AUTH_TOKEN_EXPIRED` | 401 | Token 无效或过期 |
| `AUTH_FORBIDDEN` | 403 | 无 `kb:import` 等权限 |
| `VALIDATION_ERROR` | 422 | 请求体校验失败 |
| `KB_NOT_FOUND` | 404 | 知识库不存在 |
| `FILE_NOT_FOUND` | 404 | 文件不存在 |
| `IMPORT_JOB_NOT_FOUND` | 404 | 导入任务不存在 |
| `IMPORT_INVALID_OPTIONS` | 400 | 分段参数非法 |
| `IMPORT_CONCURRENCY_LIMIT` | 409 | 并发导入超限 |
| `KB_HAS_RUNNING_IMPORT` | 409 | KB 已有运行中任务 |
| `HIT_TEST_EMPTY_QUERY` | 400 | query 为空 |
| `HIT_TEST_INVALID_TOPK` | 400 | topK 超出 1–50 |
| `HIT_TEST_INDEX_NOT_READY` | 409 | 索引未就绪 |
| `CONVERSATION_NOT_FOUND` | 404 | 会话不存在 |
| `MESSAGE_ALREADY_RUNNING` | 409 | 已有流式生成中 |
| `MESSAGE_GENERATION_FAILED` | 500 | LLM 调用失败 |
| `INTERNAL_ERROR` | 500 | 未捕获异常 |

---

## 12. curl / fetch 代码片段

### 12.1 curl：登录 + 创建 KB + 导入

```bash
BASE=http://127.0.0.1:8000

TOKEN=$(curl -s -X POST "$BASE/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"admin123"}' \
  | jq -r '.data.accessToken')

KB=$(curl -s -X POST "$BASE/v1/knowledge-bases" \
  -H "Content-Type: application/json" \
  -d '{"name":"curl-demo","description":"test"}')
KB_ID=$(echo "$KB" | jq -r '.data.id')

curl -s -X POST "$BASE/v1/uploads/presign" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"knowledgeBaseId\":\"$KB_ID\",\"files\":[{\"fileName\":\"a.txt\",\"mimeType\":\"text/plain\",\"sizeBytes\":10}]}" \
  | jq .
```

### 12.2 fetch：SSE 流式消费

```javascript
const base = "http://127.0.0.1:8000";
const convId = "conv_abc";

const res = await fetch(`${base}/v1/conversations/${convId}/messages:stream`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  },
  body: JSON.stringify({
    content: "What is the retirement age?",
    modelId: "gpt-4o-mini",
    knowledgeBaseIds: ["kb_f99aa0e4-bf6e-4041-911c-ee5d467d8490"],
  }),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const parts = buffer.split("\n\n");
  buffer = parts.pop() ?? "";

  for (const block of parts) {
    const lines = block.split("\n");
    let event = "message";
    let data = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) event = line.slice(7);
      if (line.startsWith("data: ")) data = line.slice(6);
    }
    if (event === "retrieval.completed") {
      console.log("citations", JSON.parse(data).citations);
    }
    if (event === "message.delta") {
      process.stdout.write(JSON.parse(data).delta);
    }
    if (event === "done") console.log("\n[stream end]");
  }
}
```

### 12.3 fetch：Hit-Test

```javascript
const hit = await fetch(
  `${base}/v1/knowledge-bases/${kbId}/hit-test`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: "retirement", topK: 3 }),
  }
).then((r) => r.json());

console.log(hit.data.results);
```

---

## 附录：已知联调注意点

| 现象 | 说明 |
|------|------|
| 向量下拉出现 GPT-4o | 应调用 `GET /v1/embedding-models`，勿用 `/v1/models` |
| 导入仍用 hash 向量 | 配置 `OPENAI_API_KEY` 且 `RAG_EMBEDDING_DIMENSIONS≥512`（生产建议 1536） |
| 文件落盘路径 | `storageKey` 形如 `kb/{kbId}/{fileId}.pdf`，非 `{知识库名称}/` 目录 |
| presign 后 404 | 需将文件写入 `app/storage/uploads/{storageKey}` |
| import 一直 queued | 检查 `DATABASE_URL`、Postgres、Poller 日志 |
| 无 citation | 确认 `knowledgeBaseIds` 非空且 import 已完成 |
| OpenAI 错误 | 检查 `OPENAI_API_KEY` 与 embedding 维度配置 |

**环境变量（真实 OpenAI 向量）**：

```env
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
RAG_EMBEDDING_DIMENSIONS=1536
```

**冒烟脚本**：`.venv/bin/python scripts/smoke_api.py --base http://127.0.0.1:8000`
