# Fidelity RAG FastAPI

基于 FastAPI 的 FastGPT + OpenAI + 本地 RAG 网关工程骨架。

当前项目处于新项目初始化阶段，**只对外暴露测试接口**，避免在 API 文档中提前出现未定型的知识库接口。FastGPT、OpenAI Vector Stores、本地 RAG、知识库持久化等能力已经按内部模块分层预留，后续业务接口稳定后再从 `app/api/` 显式对外暴露。

## 环境

复制环境变量模板并填写：

```bash
cp .env.example .env
```

主要变量：

| 变量 | 说明 |
|------|------|
| `FASTGPT_BASE_URL` | FastGPT 服务根 URL |
| `FASTGPT_API_KEY` | 可选，鉴权头 Bearer |
| `FASTGPT_CHAT_PATH` | 对话接口路径，默认 `/api/v1/chat/completions`（按你方部署调整） |
| `OPENAI_API_KEY` | 调用 OpenAI API |
| `OPENAI_BASE_URL` | 默认 `https://api.openai.com/v1` |
| `OPENAI_VECTOR_STORE_NAME` | 创建 vector store 未传 `name` 时的默认名 |
| `LOCAL_UPLOAD_ROOT` | 本地文档根目录（默认可用 `app/storage/uploads`） |
| `LOCAL_METADATA_PATH` | 本地知识库元数据 JSON 文件路径 |
| `LOCAL_VECTOR_STORE_PATH` | 本地开发向量索引目录 |
| `RAG_CHUNK_SIZE` | 本地 RAG chunk 长度 |
| `RAG_CHUNK_OVERLAP` | 本地 RAG chunk 重叠长度 |
| `RAG_EMBEDDING_DIMENSIONS` | 本地 hash embedding 维度 |

## 安装与启动

### 公司电脑：使用 pyenv（推荐）

如果公司电脑已经安装 `pyenv`，建议在项目目录内使用本仓库的 `.python-version` 固定 Python 版本，并用标准库 `venv` 创建项目虚拟环境。

```bash
cd /path/to/Fidelity-RAG-fastapi

# 1. 确认 pyenv 可用
pyenv --version

# 2. 安装本项目指定的 Python 版本
# .python-version 当前为 3.13.13
pyenv install -s 3.13.13

# 3. 让当前项目目录使用该 Python 版本
pyenv local 3.13.13
python --version

# 4. 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate

# 5. 安装依赖
python -m pip install --upgrade pip
pip install -r requirements.txt

# 6. 启动服务
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

启动后可用以下命令验证服务：

```bash
curl http://127.0.0.1:8000/test/ping
```

### 本机：使用 Conda 前缀环境

如果你希望继续使用当前本机的 Conda 环境，可以这样启动：

```bash
cd /path/to/Fidelity-RAG-fastapi
conda activate "/path/to/Fidelity-RAG-fastapi/.venv"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

也可以用 `environment.yml` 创建命名环境：

```bash
conda env create -f environment.yml
conda activate fidelity-rag
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器打开：`http://127.0.0.1:8000/docs`，当前只应看到 **Test** 分组下的 `/test/ping`。

## 当前内部能力

- `app/domain/`：知识库、文档、后端类型、检索结果等领域模型。
- `app/repositories/`：知识库元数据持久化，当前使用 JSON 文件，后续可替换为数据库。
- `app/services/rag/`：本地 RAG 管线，包含 chunk、hash embedding、JSON vector store、rerank、citation。
- `app/services/backends/`：统一后端协议，用于后续接入 `local`、`fastgpt`、`openai`、`hybrid`。
- `app/services/fastgpt_client.py`：FastGPT HTTP 客户端封装。
- `app/services/openai_client.py`：OpenAI Files / Vector Stores REST 客户端封装。
- `app/services/knowledge_base_service.py`：知识库用例服务，协调仓储、文档存储与 RAG 管线。

详细开发说明见 [`docs/development.md`](docs/development.md)。
