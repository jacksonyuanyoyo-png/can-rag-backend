# 开发手册

本文面向后续参与开发的同事，说明当前工程目录、开发方式和新增功能时应遵守的边界。

## 项目定位

当前项目是 FastGPT + OpenAI + 本地 RAG 的 FastAPI 网关骨架。现阶段只暴露 `/test/ping`，知识库、RAG、OpenAI、FastGPT 相关能力都放在内部模块中，等产品接口稳定后再开放到 `app/api/`。

## 目录职责

```text
app/
  api/                 # 对外 HTTP 路由。当前只保留 test_routes.py
  core/                # 配置、常量、后续可放日志与安全配置
  domain/              # 领域模型，不依赖 FastAPI
  repositories/        # 持久化访问层，当前 JSON，后续可替换 DB
  services/
    backends/          # 统一知识库后端协议，适配 local / fastgpt / openai / hybrid
    rag/               # 本地 RAG 管线：chunk、embedding、vector store、rerank、citation
    fastgpt_client.py  # FastGPT HTTP 客户端
    openai_client.py   # OpenAI REST 客户端
    knowledge_base_service.py
  storage/             # 本地开发数据目录，不应依赖为生产持久化方案
docs/                  # 开发文档
tests/                 # 自动化测试
```

## 开发原则

- 对外接口必须放在 `app/api/`，接口稳定前不要挂载到 `app/main.py`。
- 路由只做参数校验、依赖注入、响应转换；业务逻辑下沉到 `services/`。
- 知识库元数据不要放内存，必须通过 `repositories/` 持久化。
- FastGPT、OpenAI、本地 RAG 不要在路由里直接分支；新增能力时优先实现 `services/backends/base.py` 中的协议。
- Python 包导入必须使用绝对路径，例如 `from app.services.rag.pipeline import RagPipeline`。
- 测试接口只用于部署验证，不承载业务逻辑。

## RAG 管线

当前内置两种可切换实现（由 `RAG_BACKEND` 控制）：

1. `local`（默认）：开发期可运行实现。
2. `langchain_pgvector`：生产导向实现（LangChain + OpenAIEmbeddings + PGVectorStore）。

### local 管线

`local` 模式流程：

1. `TextChunker` 将文档切块。
2. `HashEmbeddingService` 生成确定性向量，避免开发期依赖外部 embedding 服务。
3. `JsonVectorStore` 持久化 chunk 向量。
4. `SimpleReranker` 对召回结果做轻量重排。
5. `RagPipeline.search()` 返回带 citation 的 `SearchHit`。

### langchain_pgvector 管线

`langchain_pgvector` 模式流程：

1. `RecursiveCharacterTextSplitter` 进行切块。
2. `OpenAIEmbeddings` 生成向量（推荐 `text-embedding-3-large`）。
3. `PGEngine` 管理数据库连接池。
4. `PGVectorStore` 按知识库名称写入独立表（`kb_<name>`）。
5. 通过 `similarity_search_with_relevance_scores` 返回检索结果。

### LangSmith tracing

通过以下环境变量启用追踪：

- `LANGSMITH_TRACING=true`
- `LANGSMITH_API_KEY=<your-key>`
- `LANGSMITH_PROJECT=fidelity-rag`
- `LANGSMITH_ENDPOINT=https://api.smith.langchain.com`（非默认区域时改为对应 endpoint）

`RagPipeline` 的索引与检索调用已打点，可在 LangSmith 查看 run tree。

生产阶段还可进一步替换：

- embedding：OpenAI embedding、FastGPT embedding 或公司内部 embedding 服务。
- vector store：OpenAI Vector Stores、Qdrant、pgvector、Milvus 或公司标准向量库。
- rerank：bge-reranker、Cohere Rerank、公司内部 rerank 服务。

## 新增业务 API 的流程

1. 在 `app/domain/` 定义或复用领域模型。
2. 在 `app/repositories/` 补充持久化逻辑。
3. 在 `app/services/` 实现用例服务。
4. 在 `app/api/` 新建路由文件，只调用 service。
5. 在 `app/main.py` 显式 `include_router()`。
6. 在 `tests/` 增加 OpenAPI 与路由行为测试。

## 本地 PostgreSQL + pgvector（Docker）

推荐使用官方维护的现成镜像 [pgvector/pgvector](https://hub.docker.com/r/pgvector/pgvector)（在官方 `postgres` 镜像上预装扩展，用法与 `postgres` 相同）。旧仓库 `ankane/pgvector` 已归档，请勿再使用。

1. 账号与库名在 `docker/postgres/postgres.env`（可改密码等）。若本机 `5432` 已被占用，在项目根目录建 `.env` 并写一行 `POSTGRES_PORT=5433`（或其它端口），再执行 `docker compose up`。
2. 启动：`docker compose up -d postgres`
3. 应用连接串：`postgresql+psycopg://rag:rag_dev_change_me@127.0.0.1:5432/rag`
4. 首次建库会通过 `docker/postgres/init/01-enable-pgvector.sql` 执行 `CREATE EXTENSION vector;`。若你手动新建了其他数据库，需在该库中再执行一次 `CREATE EXTENSION vector;`。
5. 验证：`docker exec -it can-rag-postgres psql -U rag -d rag -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"`

当前本地 `.env` 使用 `RAG_BACKEND=postgres_pgvector`，即用项目内置 `HashEmbeddingService` 生成确定性向量，再写入 PostgreSQL 的 `app.rag_chunks` 表。该模式不依赖 OpenAI key，适合先验证 PostgreSQL/pgvector 集成。若后续切换到 `RAG_BACKEND=langchain_pgvector`，需配置 `OPENAI_API_KEY` 并使用匹配模型的 embedding 维度。

应用启动后可访问 `GET /test/postgres` 查看数据库、pgvector 扩展与应用 schema 初始化状态。

更换 PostgreSQL 大版本时，修改 `docker-compose.yml` 中的镜像 tag（例如 `pg17`、`pg18`），并注意数据卷兼容性。

## pyenv 启动

```bash
cd /path/to/Fidelity-RAG-fastapi
pyenv install -s 3.13.13
pyenv local 3.13.13
python --version

python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt

python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
curl http://127.0.0.1:8000/test/ping
```

## 测试

```bash
pytest
```

当前测试重点：

- `/test/ping` 可用。
- OpenAPI 中不暴露未定型的知识库接口。
