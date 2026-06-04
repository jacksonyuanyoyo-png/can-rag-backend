# CAN-RAG 前端 API 联调指南

> **受众**：前端工程师  
> **Base URL（本地）**：`http://127.0.0.1:8000`  
> **更新**：2026-05-31（含原文对照 Markdown 图片展示 `uploads/assets`）  
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
| 网页导入 | `/v1/knowledge-bases/{kbId}/web-imports` | `kb:file:upload` |
| 导入任务 | `/v1/knowledge-bases/{kbId}/import-jobs`、`/v1/import-jobs/*` | `kb:import` |
| 文件夹 | `/v1/folders/*` | `folder:read` / `folder:write` |
| 模板 | `/v1/templates/*` | `template:read` / `template:write` |
| 当前用户 | `/v1/auth/me` | 任意有效 token |

**当前无需 Token**（联调友好，生产需加固）：

- 知识库 CRUD、**文件删除**、文件 chunks、hit-test
- 会话 / 流式问答 `/v1/conversations/*`

权限码（生产 RBAC 参考）：`kb:file:delete`（单删 / 批量删）

---

## 3. 完整联调流程

```text
GET embedding-models + GET models（对话）
  → 登录 → 创建 KB（带 embeddingModelId）
  → 方式 A：presign → 本地落盘 → complete → 创建 import-job
  → 方式 B：POST web-imports（抓取网页 → 落盘 .md → 可选自动 import-job）
  → 轮询 job 状态
  → GET files / list chunks → hit-test
  → 创建 conversation → messages:stream（带 knowledgeBaseIds）
```

**时序要点**：

1. presign 返回 `storageKey` 后，开发环境需将文件写入 `{LOCAL_UPLOAD_ROOT}/{storageKey}`（例：`app/storage/uploads/kb/{kbId}/{fileId}.pdf`）
2. import-job 创建后状态 `queued` → `running` → `completed`（约数秒，取决于 Poller/BackgroundTasks）
3. 问答前确保 import `completed` 且 chunks 非空
4. **向量模型**：`embeddingModelId` 取 `GET /v1/models` 中 `tag === "embedding"` 的项，或 `GET /v1/embedding-models`

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
| `tag` | 固定为 `embedding`（与 `/v1/models` 中 embedding 项一致） |
| `dimensions` | 模型默认输出维度 |
| `status` | `active` / `deprecated` |

**后端行为**：导入与检索均使用该 KB 绑定的 embedding 模型；若未配置 `OPENAI_API_KEY` 或 `RAG_EMBEDDING_DIMENSIONS < 512`，自动回退本地 hash 占位向量。

### 4.0.1 模型统一列表（推理 + Embedding）

```http
GET /v1/models
```

**无需鉴权**。返回 **推理（对话）** 与 **embedding（向量）** 模型合并列表，通过 `tag` 区分用途：

| `tag` | 用途 | 典型 `id` |
|-------|------|-----------|
| `inference` | 会话 `messages:stream` 的 `modelId` | `gpt-4o-mini`、`gpt-5` |
| `embedding` | 创建 KB 的 `embeddingModelId` | `text-embedding-3-small` |

**响应 200 示例（节选）**：

```json
{
  "data": [
    {
      "id": "gpt-4o-mini",
      "name": "GPT-4o mini",
      "icon": "/models/openai.svg",
      "provider": "openai",
      "status": "active",
      "visibility": "system",
      "tag": "inference"
    },
    {
      "id": "text-embedding-3-small",
      "name": "text-embedding-3-small",
      "icon": "/models/openai.svg",
      "provider": "openai",
      "status": "active",
      "tag": "embedding",
      "dimensions": 1536,
      "maxInputTokens": 8191,
      "description": "性价比优先，适合大多数 RAG 场景"
    }
  ],
  "requestId": "req_..."
}
```

前端可按 `tag === 'inference'` / `tag === 'embedding'` 拆成两个下拉；亦可继续单独调用 `GET /v1/embedding-models`（仅 embedding，字段一致）。

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

### 5.1.3 删除知识库文件（含切片与向量）

删除指定文件及其全部索引数据，适用于 presign 上传、网页导入等走 Postgres `t_dim_kb_file` 的路径，也兼容仅存在于本地元数据 `documents` 的旧数据。

```http
DELETE /v1/knowledge-bases/{kbId}/files/{fileId}
```

**响应 200**：

```json
{
  "data": { "success": true },
  "requestId": "req_..."
}
```

**批量删除**（部分失败仍返回 200，见 `failed` 列表）：

```http
POST /v1/knowledge-bases/{kbId}/files:batch-delete
Content-Type: application/json

{
  "fileIds": ["file_3d44a0cc844f47d4a4ef3230281eab96", "file_missing"]
}
```

**响应 200**：

```json
{
  "data": {
    "succeeded": ["file_3d44a0cc844f47d4a4ef3230281eab96"],
    "failed": [
      {
        "fileId": "file_missing",
        "code": "FILE_NOT_FOUND",
        "message": "File not found"
      }
    ]
  },
  "requestId": "req_..."
}
```

**后端清理范围**（按实现顺序）：

| 层级 | 说明 |
| --- | --- |
| 切片正文 | `app.t_fact_kb_data` 中该 `fileId` 全部行 |
| 向量索引 | `app.t_fact_kb_index`（FK 级联，随 data / 文件行删除） |
| 文件元数据 | `app.t_dim_kb_file`、关联 `t_fact_upload_object` |
| 落盘文件 | `{LOCAL_UPLOAD_ROOT}/{storageKey}`；PDF 增强生成的同目录 `.md` |
| 页图资源 | 切片 Markdown / `citation.storage_key` 引用的 `kb_images/*` |
| 本地 JSON 模式 | `knowledge_bases.json` 中 `documents` 条目 + `rag_chunks` / 本地 vector JSON |

**常见错误**：

| code | HTTP | 场景 |
| --- | --- | --- |
| `FILE_NOT_FOUND` | 404 | `fileId` 不存在或不属于该 `kbId` |
| `FILE_IN_USE` | 409 | 文件正在导入（`status` 为 `parsing` / `chunking` / `indexing`，或元数据 `import_status` 为 `pending` / `running`） |
| `KB_NOT_FOUND` | 404 | 知识库不存在 |

**curl 示例**：

```shell
curl -s -X DELETE "$BASE/v1/knowledge-bases/$KB_ID/files/$FILE_ID" | jq .
```

> 鉴权：与知识库其它文件接口一致，当前联调环境多数未强制 Bearer；生产建议要求 `kb:file:delete`。

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

### 5.3.1 网页 URL 导入（Web Import）

从公网 URL 抓取 HTML、抽取正文为 Markdown、注册知识库文件，并可一步创建导入任务。抽取为**通用管线**（Trafilatura → Readability 兜底 → 可选 Playwright 渲染），无站点定制选择器。

```http
POST /v1/knowledge-bases/{kbId}/web-imports
Authorization: Bearer {token}
Content-Type: application/json
Idempotency-Key: {optional}
```

**请求体**：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `url` | string (URL) | 是 | — | 仅支持 `http`/`https`；禁止 localhost 与内网地址（SSRF 防护） |
| `autoImport` | boolean | 否 | `true` | 为 `true` 时自动创建 `import-job` 并在后台执行 |
| `useBrowserFallback` | boolean | 否 | 服务端 `WEB_ENABLE_BROWSER_FALLBACK` | 静态 HTML 抽取质量不足时，用 Playwright 渲染后再抽 |
| `chunkStrategy` | string | 否 | `default` | 与 import-jobs 一致；有 `chunking` 时被覆盖 |
| `chunking` | object | 否 | — | 与 **§6** `import-jobs` 的 `chunking` 结构相同 |
| `parsing` | object | 否 | — | 含 `webUseBrowserFallback`（与 `useBrowserFallback` 二选一，请求级优先） |

**推荐请求（Insights 类文章页）**：

```json
{
  "url": "https://www.fidelity.ca/en/insights/articles/government-grants-resp/",
  "autoImport": true,
  "useBrowserFallback": false,
  "chunking": {
    "strategy": "default",
    "indexSize": 512,
    "metadata": {
      "includeFileName": true,
      "includeHeadings": true
    }
  },
  "parsing": {
    "webUseBrowserFallback": false
  }
}
```

**响应 201**：

```json
{
  "data": {
    "fileId": "file_a1b2c3d4e5f6",
    "fileName": "Heres-how-to-get-government-grants-into-your-RESP.md",
    "storageKey": "kb/{kbId}/file_a1b2c3d4e5f6.md",
    "sourceUrl": "https://www.fidelity.ca/en/insights/articles/government-grants-resp/",
    "extractionMethod": "trafilatura",
    "importJobId": "job_7c8d9e0f"
  },
  "requestId": "req_..."
}
```

| 响应字段 | 说明 |
|----------|------|
| `fileId` | 知识库文件 ID，后续 chunks / hit-test / citation 使用 |
| `fileName` | 由页面标题或 URL 路径生成的 `.md` 文件名；重名时自动加 `-2`、`-3` 后缀 |
| `storageKey` | 落盘路径，形如 `kb/{kbId}/{fileId}.md` |
| `sourceUrl` | 抓取后的最终 URL（含重定向） |
| `extractionMethod` | `trafilatura` / `readability` / `browser+trafilatura` 等 |
| `importJobId` | `autoImport=true` 且服务已装配 worker 时返回；否则省略 |

**后端行为摘要**：

1. 校验 URL → `httpx` 抓取 HTML（默认最大 5MB，超时 30s）
2. Trafilatura 抽正文为 Markdown；质量门禁不通过则 Readability
3. 仍不通过且开启浏览器兜底时，Playwright 渲染后再抽（需服务端安装 `playwright`）
4. 写入 `{LOCAL_UPLOAD_ROOT}/{storageKey}`，MIME 为 `text/markdown`
5. 插入 `t_dim_kb_file`，状态 `uploaded`
6. `autoImport=true` 时创建 import-job（分段配置与 **§6** 相同），后台 `run_job`

**与文件上传流程对比**：

| 步骤 | 文件上传 | 网页导入 |
|------|----------|----------|
| 获取 fileId | presign | web-imports 响应 |
| 写入存储 | 客户端 PUT 到 dev-upload | 服务端自动写入 |
| 触发索引 | 手动 POST import-jobs | `autoImport: true` 时自动 |

**常见错误**：

| code | HTTP | 场景 |
|------|------|------|
| `IMPORT_PARSE_FAILED` | 400 | URL 非法、抓取失败、正文过短、不支持的内容类型 |
| `FILE_DUPLICATED` | 409 | 同名文件已存在（自动重命名前若冲突） |
| `KB_NOT_FOUND` | 404 | 知识库不存在 |
| `AUTH_FORBIDDEN` | 403 | 无 `kb:file:upload` |

**环境变量（服务端）**：

```env
WEB_FETCH_MAX_BYTES=5242880
WEB_FETCH_TIMEOUT_SECONDS=30
WEB_MIN_CONTENT_CHARS=200
WEB_LINK_DENSITY_MAX=0.35
WEB_ENABLE_BROWSER_FALLBACK=true
# 动态页兜底（可选）: pip install playwright && playwright install chromium
```

**curl 示例**：

```bash
curl -s -X POST "$BASE/v1/knowledge-bases/$KB_ID/web-imports" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.fidelity.ca/en/insights/articles/government-grants-resp/",
    "autoImport": true,
    "chunking": {
      "strategy": "default",
      "metadata": { "includeFileName": true, "includeHeadings": true }
    }
  }' | jq .
```

`autoImport=true` 时，用响应中的 `importJobId` 轮询 **§5.5** `GET /v1/import-jobs/{jobId}`，直至 `status=completed`。

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

多轮对话：续聊时必须复用同一 `conversationId`；服务端会从已持久化的历史消息组装 LLM 上下文（配置 `DATABASE_URL` 时写入 PostgreSQL）。

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

### 8.0 知识片段在库里的存储格式

导入后，**分段正文**写入 Postgres `app.t_fact_kb_data.text`（**纯文本字段，无单独 MIME 列**）：

| 来源 | `text` 内容形态 |
|------|------------------|
| PDF（`pdfEnhancement=true`） | **Markdown**：`## Page n`、正文、图示 `kb_images/{uuid}.png`、`**图示内容**` 段落等 |
| DOCX / MD 上传 | **Markdown**：内嵌图示路径 `kb_images/...` |
| 纯 TXT | 多为纯文本；`textFormat` 为 `plain` |
| VLM 独立图块 | `type=image` 的 citation，正文为图片描述文字 |

向量检索用 `app.t_fact_kb_index`（`text` 为带文件名前缀的索引文本，结构仍多为 Markdown）。

**前端渲染**：列表/引用请优先用响应里的 **`markdown`** 字段（图片路径已改写为 `/v1/uploads/assets/...`），勿只对 `text`/`snippet` 做 `white-space: pre`。

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
| `storageKey` | string? | 图片资源键：`type=image` 时必有；文本 chunk 内含 Markdown 图片路径（kb_images/ 前缀）时也可能带首图 key，用于缩略图/深链 |
| `textFormat` | `"markdown"` \| `"plain"` | 是否按 Markdown 渲染 |
| `markdown` | string | 建议用于 `react-markdown` 的正文（图链已改写为 `/v1/uploads/assets/...`） |
| `hasImages` | boolean | 是否含 `kb_images/` 图 |
| `imageKeys` / `imageAssets` | array? | 全部图示 storageKey 与可请求路径 |
| `chunkViewPath` / `fileViewPath` | string? | 切片详情 / 文件详情 API 相对路径 |

### 8.2 回答完成后的 `sources`（`message.completed` / `retrieval.completed`）

流式结束时的 `data.sources` 供底部「知识分段 + 来源文件」面板使用：

```json
{
  "segments": [
    {
      "index": 1,
      "snippet": "原始召回文本",
      "markdown": "改写图链后的 Markdown",
      "textFormat": "markdown",
      "hasImages": true,
      "chunkViewPath": "/v1/knowledge-bases/{kbId}/files/{fileId}/chunks/{chunkId}",
      "fileViewPath": "/v1/knowledge-bases/{kbId}/files/{fileId}",
      "fileName": "policy.docx",
      "score": 0.91
    }
  ],
  "files": [
    {
      "kbId": "...",
      "fileId": "...",
      "fileName": "...",
      "mimeType": "application/vnd...",
      "storageKey": "kb/{kbId}/{fileId}.docx",
      "fileViewPath": "/v1/knowledge-bases/{kbId}/files/{fileId}",
      "segmentIndexes": [1, 2]
    }
  ],
  "figures": [
    { "ref": 1, "storageKey": "kb_images/....png", "assetUrl": "/v1/uploads/assets/kb_images/....png" }
  ],
  "render": {
    "assistantContent": "markdown",
    "segmentContent": "markdown",
    "imagePathPrefix": "/v1/uploads/assets/"
  }
}
```

- **助手正文** `content`：模型生成的 Markdown/纯文本（引用 `[1]`、`见图[1]`）；配图请结合 `sources.figures` 或解析 `citations[].markdown`。
- **分段卡片**：用 `segments[].markdown` + `chunkViewPath` 跳转原文对照。
- **来源文件**：点击 `files[].fileViewPath` 查看文件元数据。

### 8.3 原文对照深链（前端路由建议）

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

**每条切片除 `text` 外还返回**（与库内 Markdown 一致）：

| 字段 | 说明 |
|------|------|
| `textFormat` | `markdown` 或 `plain` |
| `markdown` | 供 Markdown 组件渲染（图链为 `/v1/uploads/assets/...`） |
| `hasImages` | 是否含嵌入图 |

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

**响应新增字段**（用于左栏「真源文件」预览，非 PDF 增强稿）：

| 字段 | 说明 |
|------|------|
| `sourceFileUrl` | 源文件流地址，相对路径：`/v1/knowledge-bases/{kbId}/files/{fileId}/raw` |
| `storageKey` | 落盘键（上传/网页导入场景），形如 `kb/{kbId}/{fileId}.pdf` |

### 9.3.1 源文件预览 / 下载原文（真文件）

```http
GET /v1/knowledge-bases/{kbId}/files/{fileId}/raw?disposition=inline
```

| Query | 默认 | 说明 |
|-------|------|------|
| `disposition` | `inline` | `inline`：浏览器内预览；`attachment`：触发下载（「下载原文」按钮） |

- 返回 **上传时的原文件字节流**（`t_dim_kb_file.storage_key` 指向的路径）。
- **不会**返回 PDF 增强生成的同目录 `{fileId}.md`。
- `Content-Type` 来自库内 `mimeType` 或按扩展名推断（如 `application/pdf`、`application/vnd.openxmlformats-officedocument.wordprocessingml.document`、`text/markdown`）。
- 文件不存在于磁盘：`404` + `RESOURCE_NOT_FOUND`；`fileId` 无效：`404` + `FILE_NOT_FOUND`。

**前端拼接完整 URL**：

```typescript
export function kbSourceFileUrl(
  apiBase: string,
  kbId: string,
  fileId: string,
  options?: { download?: boolean }
): string {
  const base = apiBase.replace(/\/$/, "");
  const q = options?.download ? "?disposition=attachment" : "";
  return `${base}/v1/knowledge-bases/${kbId}/files/${fileId}/raw${q}`;
}
```

**按格式渲染（左栏原文对照）**：

| `format` / `mimeType` | 建议 |
|-----------------------|------|
| `pdf` | `<iframe src={url}>` 或 `react-pdf` / PDF.js，`disposition=inline` |
| `docx` | `fetch(url)` → `ArrayBuffer` → `docx-preview` 等 |
| `md` / `markdown` | `fetch(url)` → `text()` → Markdown 组件（勿用 chunks 里的增强文本） |
| `txt` | `fetch` + `<pre>` 或 iframe |

也可先 `GET .../files/{fileId}` 读 `sourceFileUrl` 与 `mimeType`，再请求该 URL。

### 9.4 原文对照：Markdown 图片如何展示（PDF 增强）

**默认行为**：创建 import-job 时若未传 `parsing.pdfEnhancement`，后端默认为 **`true`**（PDF 按页 VLM 转 Markdown + `kb_images/` 截图）。需配置有效 `OPENAI_API_KEY`；若要关闭可显式传 `"pdfEnhancement": false`。

PDF 开启 `pdfEnhancement` 后，切片与落盘 Markdown 中会出现 **标准 Markdown 图片语法**，路径为后端 `storage_key`，**不是**完整 HTTP URL：

```text
（切片 text 示例，两行）
第1行：Markdown 图片 — alt=Page 1，url=kb_images/385be452-6ea1-4c0f-9268-549ffaffca03.png
第2行：**图示内容**：键位布局：84 键紧凑排列……
```

若前端用纯文本渲染，用户只会看到「Markdown 图片行 + kb_images/ 路径」原文（见联调截图）。**必须**用 Markdown 渲染器，并把 `kb_images/...` 解析为资源 API 地址。

#### 9.4.1 图片资源 API

```http
GET /v1/uploads/assets/{storagePath}
```

| 项 | 说明 |
|----|------|
| `storagePath` | 与 Markdown 括号内一致，例如 `kb_images/385be452-6ea1-4c0f-9268-549ffaffca03.png`；路径含 `/` 时按段 `encodeURIComponent` |
| 鉴权 | 当前实现 **不要求** `Authorization`（与 presign/complete 不同）；若后续加固，请与后端确认后统一在 `<img>` 请求中带 Bearer |
| 成功 | `200`，`Content-Type` 为 `image/png` 等 |
| 失败 | `404` + `RESOURCE_NOT_FOUND`（文件未落盘或 key 错误） |

**完整 URL 示例**（本地）：

```text
http://127.0.0.1:8000/v1/uploads/assets/kb_images/385be452-6ea1-4c0f-9268-549ffaffca03.png
```

#### 9.4.2 推荐：统一解析函数

在应用内配置 `apiBase`（与联调 Base URL 相同），所有 Markdown / citation 图片都走同一函数：

```typescript
/** Markdown 图片 src → 可请求的 URL */
export function resolveUploadAssetUrl(
  apiBase: string,
  src: string | undefined | null
): string | null {
  if (!src?.trim()) return null;
  const trimmed = src.trim();
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  if (trimmed === "placeholder" || trimmed === "{placeholder}") return null;
  const key = trimmed.startsWith("/") ? trimmed.slice(1) : trimmed;
  if (!key.startsWith("kb_images/") && !key.startsWith("kb/")) return null;
  const base = apiBase.replace(/\/$/, "");
  const encoded = key.split("/").map((seg) => encodeURIComponent(seg)).join("/");
  return `${base}/v1/uploads/assets/${encoded}`;
}
```

> `kb/` 前缀用于将来扩展；当前 PDF 增强页图均在 `kb_images/{uuid}.png`。

#### 9.4.3 React（react-markdown）绑定示例

```tsx
import ReactMarkdown from "react-markdown";

type Props = { markdown: string; apiBase: string };

export function KbMarkdownPreview({ markdown, apiBase }: Props) {
  return (
    <ReactMarkdown
      components={{
        img: ({ src, alt }) => {
          const url = resolveUploadAssetUrl(apiBase, src);
          if (!url) {
            return (
              <span className="text-muted" title={src ?? ""}>
                [{alt ?? "图片"}]
              </span>
            );
          }
          return (
            <img
              src={url}
              alt={alt ?? ""}
              loading="lazy"
              style={{ maxWidth: "100%", height: "auto" }}
            />
          );
        },
      }}
    >
      {markdown}
    </ReactMarkdown>
  );
}
```

#### 9.4.4 Vue 3 绑定思路

使用 `markdown-it` + 自定义 `image` 规则，或在渲染后对 `img[src^="kb_images/"]` 批量改写 `src`：

```typescript
import MarkdownIt from "markdown-it";

const md = new MarkdownIt();
const defaultRender =
  md.renderer.rules.image ??
  ((tokens, idx, options, _env, self) =>
    self.renderToken(tokens, idx, options));

md.renderer.rules.image = (tokens, idx, options, env, self) => {
  const src = tokens[idx].attrGet("src");
  const resolved = resolveUploadAssetUrl(env.apiBase, src);
  if (resolved) tokens[idx].attrSet("src", resolved);
  return defaultRender(tokens, idx, options, env, self);
};

// 渲染：md.render(text, { apiBase })
```

#### 9.4.5 文件预览三栏如何接 API

| UI 区域 | 建议数据源 | 渲染方式 |
|---------|------------|----------|
| **原文对照**（左） | **推荐**：`GET .../files/{fileId}/raw` 预览真源文件（§9.3.1）；切片配图仍可用 chunks / `KbMarkdownPreview` | PDF/Word/Markdown 用对应查看器；勿把增强 `.md` 当 PDF 原文件 |
| **切片信息**（中） | `GET .../files/{fileId}/chunks` → `data[].text` | 每条切片卡片内同样 `KbMarkdownPreview` |
| **切片知识点**（右） | `GET .../chunks/{dataId}?context=1` → `data.target.text` | 同上 |

列表接口片段示例（字段以实际为准）：

```json
{
  "dataId": "d000000",
  "text": "<Markdown 图片行 kb_images/385be452-....png>\\n\\n**图示内容**：\\n……",
  "page": 1,
  "citation": {
    "file_name": "Mini84....pdf",
    "page": 1,
    "data_id": "d000000",
    "storage_key": "kb_images/385be452-6ea1-4c0f-9268-549ffaffca03.png"
  }
}
```

`citation.storage_key`（JSON 为 snake_case，SSE citations 为 camelCase `storageKey`）可在**不解析 Markdown** 时直接用作缩略图：

```typescript
const thumbUrl = citation.storageKey
  ? resolveUploadAssetUrl(apiBase, citation.storageKey)
  : null;
```

#### 9.4.6 对话引用中的配图

流式/同步消息的 `citations[]` 在 `type === "image"` 或带 `storageKey` 时，侧边引用卡建议：

1. `snippet` 展示文字（含 **图示内容** 要点）；
2. `storageKey` → `resolveUploadAssetUrl` → `<img>` 缩略图；
3. 点击跳转原文对照深链（§8.2）：`/knowledge-bases/{kbId}/files/{fileId}/chunks/{chunkId}`。

#### 9.4.7 联调自检

```bash
# 将 KEY 换成切片里括号中的路径
curl -sI "http://127.0.0.1:8000/v1/uploads/assets/kb_images/385be452-6ea1-4c0f-9268-549ffaffca03.png" | head -5
# 期望 HTTP/1.1 200 与 image/png
```

| 现象 | 处理 |
|------|------|
| 仍显示 Markdown 图片原文（kb_images 路径） | 未走 Markdown 渲染，或 img 组件未改写 src |
| 图片裂图 404 | import 未完成、旧任务仍是 `placeholder`、或 key 与落盘不一致 → 重新导入并确认 `app/storage/uploads/kb_images/` 存在该文件 |
| `placeholder` | 历史数据，需对该 PDF 重新 `pdfEnhancement: true` 导入 |

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
| `FILE_IN_USE` | 409 | 文件正在导入，不可删除 |
| `IMPORT_JOB_NOT_FOUND` | 404 | 导入任务不存在 |
| `IMPORT_PARSE_FAILED` | 400 | 网页抓取/正文抽取失败 |
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
| 网页导入失败 | 确认 URL 为公网 https；查看 `IMPORT_PARSE_FAILED` 详情；SPA 页可设 `useBrowserFallback: true` |
| 网页导入无 importJobId | `autoImport=false` 或未配置 `DATABASE_URL` / worker；需手动 POST import-jobs |
| 无 citation | 确认 `knowledgeBaseIds` 非空且 import 已完成 |
| 原文对照只显示 Markdown 图片原文 | 按 **§9.4** 用 `/v1/uploads/assets/` 渲染 Markdown 图片 |
| OpenAI 错误 | 检查 `OPENAI_API_KEY` 与 embedding 维度配置 |

**环境变量（真实 OpenAI 向量）**：

```env
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-large
RAG_EMBEDDING_DIMENSIONS=1536
```

**冒烟脚本**：`.venv/bin/python scripts/smoke_api.py --base http://127.0.0.1:8000`
