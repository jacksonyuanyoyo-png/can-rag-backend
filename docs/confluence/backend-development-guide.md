# CAN-RAG 后端开发指南

> **受众**：后端工程师、架构师  
> **仓库**：`CAN-RAG-BackEnd`  
> **更新**：2026-05-30  
> **Confluence**：MCP 无写权限时，将本文件从 `docs/confluence/` 手动导入。

---

## 1. 项目概述与本地启动

CAN-RAG 是基于 **FastAPI** 的知识库 RAG 网关，当前能力包括：

- 文档上传 → 异步导入 → 解析 / 分段 / 向量化 / 入库
- **FastGPT 风格** `data`（原文块）↔ 多 `index`（检索向量）模型
- 可配置分段策略（default / custom / page；whole 暂未开放）
- 问答侧 RAG 检索 + **编号引用（citation）** 注入 LLM
- 文档内图片经 **VLM** 生成描述后作为独立 data/index 入库

**技术栈**：Python 3.13、FastAPI、PostgreSQL + pgvector、psycopg3、Hash 确定性 embedding（`postgres_pgvector`）或 LangChain PGVector（`langchain_pgvector`）。

### 1.1 启动步骤

```bash
# 1. PostgreSQL + pgvector
docker compose up -d postgres

# 2. 环境与依赖
cp .env.example .env
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. 推荐 .env（与 docker/postgres/postgres.env 一致）
# DATABASE_URL=postgresql+psycopg://rag:rag_dev_change_me@127.0.0.1:5432/rag
# RAG_BACKEND=postgres_pgvector
# RAG_EMBEDDING_DIMENSIONS=256

# 4. 应用迁移（首次或升级库结构）
psql "postgresql://rag:rag_dev_change_me@127.0.0.1:5432/rag" -f app/migrations/0002_core_kb_tables.sql
psql "postgresql://rag:rag_dev_change_me@127.0.0.1:5432/rag" -f app/migrations/0003_domain_business_tables.sql
psql "postgresql://rag:rag_dev_change_me@127.0.0.1:5432/rag" -f app/migrations/0004_kb_data_index.sql

# 5. 启动 API（lifespan 会启动 ImportJobPoller，默认 5s 轮询 queued 任务）
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**健康检查**：

| 端点 | 说明 |
|------|------|
| `GET /test/ping` | 进程存活 |
| `GET /test/postgres` | DB / pgvector / schema 状态 |
| `GET /docs` | OpenAPI（业务路由已挂载 `/v1/*`） |

**开发账号**（内存用户，勿用于生产）：`admin@example.com` / `admin123`，权限含 `kb:import`、`kb:file:upload` 等（见 `app/services/auth/dev_users.py`）。

---

## 2. 摄取流水线架构

```mermaid
flowchart TB
  subgraph upload["上传阶段"]
    A[POST /v1/uploads/presign] --> B[客户端写入 LOCAL_UPLOAD_ROOT]
    B --> C[POST /v1/uploads/{id}:complete]
    C --> D[(app.t_dim_kb_file)]
  end

  subgraph job["导入任务"]
    E[POST /v1/knowledge-bases/{kbId}/import-jobs] --> F[(app.t_fact_import_job)]
    F --> G{ImportJobPoller / BackgroundTasks}
    G --> H[ImportJobWorker.run_job]
  end

  subgraph pipeline["单文件处理 build_process_file"]
    H --> I[KbFileResolver 读 storage_key]
    I --> J[get_parser_for → parse]
    J --> K[ChunkingService.split_and_index]
    K --> L[HashEmbeddingService.embed]
    L --> M[PgVectorStore.upsert_data_index]
  end

  subgraph storage["持久化"]
    M --> N[(app.t_fact_kb_data)]
    M --> O[(app.t_fact_kb_index)]
    M --> P[更新 t_dim_kb_file.status]
    M --> Q[累加 t_dim_knowledge_base 计数]
  end

  upload --> job
  D --> I
```

**关键源码**：

| 环节 | 模块 |
|------|------|
| 轮询 / 执行 | `app/services/import_job_runner.py`（`ImportJobPoller`、`build_process_file`） |
| 状态机 | `app/services/import_job_worker.py`（`ImportJobWorker`） |
| 分段 + 多 index | `app/services/rag/chunking_service.py` |
| 入库检索 | `app/services/rag/pipeline.py`（`index_data` / `search_data`） |
| PG 读写 | `app/repositories/kb_data_index_repository.py`、`app/services/rag/vector_store.py` |

---

## 3. FastGPT 风格 data ↔ index 多向量模型

与「一段文本一个向量」的单层模型不同，本仓库采用 **两层结构**（对齐 FastGPT 数据集概念）：

| 层级 | 表 | 标识 | 用途 |
|------|-----|------|------|
| **Data** | `app.t_fact_kb_data` | `data_id`（如 `d000000`） | 面向用户的原文块；列表/对照页展示；citation 的 `chunkId` 通常对应此 ID |
| **Index** | `app.t_fact_kb_index` | `index_id`（如 `d000000-000`） | 面向检索的向量行；一条 data 可拆成多个 index（`index_size` 控制） |

**关系**：

- `t_fact_kb_index` 通过 `(kb_id, file_id, data_id)` 外键关联 `t_fact_kb_data`
- 检索在 **index** 上做余弦相似度（HNSW），再 **按 data_id 聚合** 取最佳分数，返回 data 级 `SearchHit`
- 写入时：`ChunkingService.build_indexes()` 对每个 `DataChunk` 按 `index_size` 二次切分，并可加「文件：」「标题：」前缀（`metaFilename` / `metaHeadings`）再 embed

**ID 生成示例**（`pipeline.index_data`）：

- Data：`d{chunk_index:06d}` → `d000000`
- Index：`d{data_chunk_index:06d}-{index_in_data:03d}` → `d000000-000`
- 图片：`img{page:04d}-{index:03d}` + `-000` index

---

## 4. 分段策略与 ChunkingOptions

### 4.1 策略总览

| `strategy` | 行为 | API 状态 |
|------------|------|----------|
| `default` | 全文递归切分，`maxChunkSize` 默认取 `RAG_CHUNK_SIZE`，`overlap` 默认 `RAG_CHUNK_OVERLAP` | ✅ |
| `custom` | 需 `custom.mode`：`paragraph` / `length` / `separator` | ✅ |
| `page` | 按 `ParsedBlock.page` 分块，超长块再切 | ✅ |
| `whole` | 整文件不分块 | ❌ API 与运行时均拒绝 |

`custom.mode` 与 `ChunkingService.split()` 映射见 `app/services/rag/chunking_service.py`。

### 4.2 ChunkingOptions 字段表（HTTP camelCase）

定义：`app/api/schemas/import_job.py` → 持久化为 `ChunkingConfig`（`app/domain/import_job.py`）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `strategy` | `default` \| `custom` \| `whole` \| `page` | 否（默认 `default`） | `whole` 返回 `IMPORT_INVALID_OPTIONS` |
| `custom.mode` | `paragraph` \| `length` \| `separator` | `strategy=custom` 时必填 | |
| `paragraph.useModel` | boolean | 否 | 预留，当前分段逻辑未调用模型 |
| `paragraph.maxDepth` | int > 0 | `mode=paragraph` 时必填 | 控制递归分隔符深度 |
| `length.chunkSize` | int > 0 | `mode=length` 时必填 | 目标块长 |
| `length.overlap` | int ≥ 0 | `mode=length` 时必填 | 须 **< chunkSize** |
| `length.maxChunkSize` | int > 0 | `mode=length` 时必填 | 须 **≥ chunkSize** |
| `separator.separators` | string[] | `mode=separator` 时必填 | 多级分隔后再按 max 切 |
| `indexSize` | 256 \| 512 \| 1024 | 否（默认 512） | 单条 data 内 index 最大字符数；须 ≤ `length.maxChunkSize`（若同时传 length） |
| `metadata.includeFileName` | boolean | 否（默认 true） | index 文本前缀「文件：xxx」 |
| `metadata.includeHeadings` | boolean | 否（默认 false） | index 文本前缀「标题：xxx」 |

任务创建时 `chunking` 优先于顶层 `chunkStrategy` / `metadata`（见 `CreateImportJobRequest._apply_chunking_precedence`）。配置 JSON 存入 `app.t_fact_import_job_option.chunking_config`。

---

## 5. 异步 Worker 状态机

### 5.1 任务级（ImportJob）

**状态**（`ImportJobStatus`）：

```mermaid
stateDiagram-v2
  [*] --> queued
  queued --> running: Worker 接管
  queued --> cancelled
  running --> completed: 全部文件无首错
  running --> failed: 任一文件异常
  running --> cancelled
  completed --> [*]
  failed --> [*]
  cancelled --> [*]
```

**阶段**（`ImportJobStage`，顺序固定）：`upload` → `parse` → `chunk` → `embed` → `index` → `done`

`ImportJobWorker.run_job` 逻辑摘要：

1. 若已处于终态（`completed` / `failed` / `cancelled`），直接返回
2. `queued` → `running`，`stage=parse`
3. 依次将 stage 设为 `chunk`、`embed`、`index`（当前实现中 parse/chunk/embed 在同一次 `process_file` 内完成，stage 主要用于对外展示）
4. 逐文件调用 `process_file`：解析 → `pipeline.index_data` → 返回 **data 条数** 累加到 `chunk_count`
5. 全部成功 → `completed` + `stage=done` + `progress=100`，并 `KbCountSink` 更新 KB 计数
6. 任一失败 → `failed`，`error_code=IMPORT_FILE_FAILED`，已成功文件仍可能部分累加计数

### 5.2 文件级（ImportJobFileStatus → DB）

| Worker 状态 | `t_dim_kb_file.status` |
|-------------|-------------------------|
| RUNNING | `indexing` |
| COMPLETED | `ready` |
| FAILED | `failed` |

由 `KbFileStatusSink` 写入（`import_job_runner.py`）。

### 5.3 Poller

- **`ImportJobPoller`**：`asyncio` 后台任务，默认每 **5s** 调用 `fetch_queued_job_ids`（`status=queued`），再 `worker.run_job`
- **创建任务时**：`import_job_routes` 同时通过 `BackgroundTasks` 触发一次 `run_job`（与 Poller 可并存，终态任务会短路）
- 无 `DATABASE_URL` 时：不装配 worker/poller，导入任务仅入队不执行

---

## 6. 问答 RAG 消费侧

### 6.1 知识库绑定

- 流式 / 同步发消息可传 `knowledgeBaseIds`；非空时会 `bind_knowledge_bases` 到会话
- 未传则使用会话已绑定的 `conversation.knowledge_base_ids`

### 6.2 检索

`ConversationService._retrieve_citations` → 对每个 KB 调用 `KnowledgeBaseService.search(..., kb_id=...)` → 内部 `RagPipeline.search_data`（pgvector 上对 index 检索，按 data 聚合）。

### 6.3 引用归因与 LLM 上下文

**SSE / 响应中的 citation 对象**（`_citation_from_hit`）：

| 字段 | 含义 |
|------|------|
| `index` | 1-based 编号，供模型在句末标 `[1]` |
| `kbId` | 知识库 ID（检索后写入 `hit.citation.kb_id`） |
| `fileId` | 文件 ID |
| `chunkId` | 对应 `data_id` |
| `page` | PDF 等页码，可空 |
| `score` | 相似度分数 |
| `snippet` | 召回文本（data 级） |
| `fileName` | 文件名 |
| `type` | `text` 或 `image` |
| `storageKey` | 图片类型时可选 |

**Prompt 组装**（`_build_chat_messages`）：若有 citations，插入一条 `system` 消息，按 `来源[n]（文件：…）:\n{snippet}` 拼接；要求模型在事实句末标注 `[n]`。

流式事件顺序见《前端 API 联调指南》第 6 节。

> **注意**：流式结束后 citation 挂在内存中的 `MessageRecord`，**尚未**写入 `t_fact_message_citation`（表已在 0003 迁移中创建）。

---

## 7. 多模态（VLM 图片描述入库）

流程（`pipeline._HashRagPipeline.index_data`）：

1. 解析器（PDF/DOCX/PPTX 等）产出 `ParsedDocument.images`（含 `storage_key`、`page`）
2. 对每个图片：`ImageStore` 读字节 → `VlmService.describe_image`（需配置 `OPENAI_API_KEY` 等）
3. 描述文本作为新 **DataRecord** + **IndexRecord**（`type: image` 写入 citation）
4. `data_id` 形如 `img0001-000`，与文本块共用 `upsert_data_index`

VLM 失败或文件缺失时跳过该图，不阻断整文件导入。

---

## 8. 数据库表（0003 / 0004 核心）

### 8.1 0003 `domain_business_tables`（节选）

| 表 | 说明 |
|----|------|
| `app.t_dim_user` / `team` / `role` | 用户与权限骨架 |
| `app.t_dim_model` | 模型目录 |
| `app.t_dim_folder` / `t_dim_template` | 文件夹、模板 |
| `app.t_dim_conversation` / `t_fact_message` | 会话与消息（演进中） |
| `app.t_fact_message_citation` | 消息引用落库（**待业务写入**） |
| `app.t_fact_import_job` / `_file` / `_option` | 导入任务及 `chunking_config` |

### 8.2 0004 `kb_data_index`

| 表 | 说明 |
|----|------|
| `app.t_fact_kb_data` | 原文块；`UNIQUE(kb_id, file_id, data_id)` |
| `app.t_fact_kb_index` | `vector(256)` + HNSW；`UNIQUE(kb_id, file_id, index_id)` |
| `ALTER t_fact_import_job_option` | 增加 `chunking_config jsonb` |

0002 已包含 `t_dim_knowledge_base`、`t_dim_kb_file` 等 KB 核心维表。

启动时各 Repository 的 `ensure_schema()` 可建表子集；**完整约束与迁移版本**仍以 `app/migrations/*.sql` 为准。

---

## 9. 测试命令

```bash
# 全量单元测试
.venv/bin/python -m pytest -q

# 会话 / 流式 / citation 相关
.venv/bin/python -m pytest tests/test_conversation_stream.py tests/test_conversations_messages_api.py -q

# 导入与分段
.venv/bin/python -m pytest tests/test_import_job_worker.py tests/test_chunking_service.py tests/test_index_data_pipeline.py -q

# 端到端冒烟（需已启动 uvicorn + DB + 本地写入 upload 文件）
.venv/bin/python scripts/smoke_api.py --base http://127.0.0.1:8000
```

**冒烟结果（2026-05-30）**：**12/13 PASS**。`import` / `chunks` / `hit-test` / `stream citations` 均 OK；**`GET /v1/knowledge-bases/{kbId}/files` 列表可能为空**（见下文），但 `.../files/{fileId}/chunks` 有数据。

---

## 10. 已知限制与后续

| 项 | 现状 | 后续 |
|----|------|------|
| **文件列表为空** | `list_files` 读 JSON 元数据 `metadata.documents`；上传/导入写 PG `t_dim_kb_file` + 向量表，**两源未完全同步** | 列表改为查 `t_dim_kb_file` 或与 PG 对齐 |
| **消息 / citation DB** | 会话在 `ConversationRepository`（内存/JSON）；citation 仅 SSE 与内存消息 | 写入 `t_fact_message` / `t_fact_message_citation` |
| **strategy=whole** | API 校验拒绝；`ChunkingService` 亦抛错 | 产品确认后开放 |
| **paragraph.useModel** | 字段存在未使用 | 接入标题/段落模型分段 |
| **RAG_BACKEND=local** | 无 `index_data` / `search_data` | 生产统一 `postgres_pgvector` |
| **OpenAI** | 问答与 VLM 依赖 key；测试可用 `tests/fake_openai_chat.py` | 公司网关统一配置 |

---

## 附录：配置速查

| 变量 | 典型值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+psycopg://rag:...@127.0.0.1:5432/rag` | 启用 PG 导入、data/index |
| `RAG_BACKEND` | `postgres_pgvector` | `local` / `langchain_pgvector` |
| `RAG_CHUNK_SIZE` | `800` | default 策略 data 块大小 |
| `RAG_CHUNK_OVERLAP` | `120` | default 重叠 |
| `RAG_EMBEDDING_DIMENSIONS` | `256` | 须与 `t_fact_kb_index.embedding` 一致 |
| `LOCAL_UPLOAD_ROOT` | `app/storage/uploads` | presign 落盘目录 |
| `OPENAI_API_KEY` | — | 问答 / VLM（可选 mock） |
