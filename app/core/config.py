from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置，支持 .env 与环境变量覆盖。"""

    FASTGPT_BASE_URL: str = ""
    FASTGPT_API_KEY: str = ""
    FASTGPT_CHAT_PATH: str = "/api/v1/chat/completions"

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
    OPENAI_VECTOR_STORE_NAME: str = "default-rag-store"
    VLM_ENABLED: bool = False
    VLM_MODEL: str = "gpt-4o-mini"
    VLM_TIMEOUT_SECONDS: float = 60.0
    VLM_MIN_IMAGE_BYTES: int = 5120
    VLM_MAX_IMAGE_BYTES: int = 8_000_000
    PDF_AUTO_ENHANCE_MIN_CHARS: int = 100
    PDF_RENDER_DPI: int = 150
    DATABASE_URL: str = ""

    LOCAL_UPLOAD_ROOT: str = str(_PROJECT_ROOT / "app" / "storage" / "uploads")
    LOCAL_METADATA_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "metadata" / "knowledge_bases.json")
    LOCAL_VECTOR_STORE_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "vector_store")
    HTTP_TIMEOUT_SECONDS: float = 60.0

    DEFAULT_MAX_TOKENS: int = 4096
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 120
    RAG_EMBEDDING_DIMENSIONS: int = 256
    RAG_BACKEND: str = "local"

    MODELS_JSON: str = ""
    LOCAL_MODELS_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "metadata" / "models.json")

    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "fidelity-rag"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    AUTH_JWT_SECRET: str = "dev-jwt-secret-change-me"
    AUTH_ACCESS_TOKEN_EXPIRE_SECONDS: int = 1800
    AUTH_REFRESH_TOKEN_EXPIRE_SECONDS: int = 60 * 60 * 24 * 7
    AUTH_REFRESH_COOKIE_NAME: str = "refresh_token"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # pydantic-settings: expose resolved upload directory
    @property
    def upload_root_resolved(self) -> Path:
        p = Path(self.LOCAL_UPLOAD_ROOT)
        return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()

    @property
    def metadata_path_resolved(self) -> Path:
        p = Path(self.LOCAL_METADATA_PATH)
        return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()

    @property
    def vector_store_path_resolved(self) -> Path:
        p = Path(self.LOCAL_VECTOR_STORE_PATH)
        return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()

    @property
    def models_path_resolved(self) -> Path:
        p = Path(self.LOCAL_MODELS_PATH)
        return p.resolve() if p.is_absolute() else (_PROJECT_ROOT / p).resolve()


def get_settings() -> Settings:
    return Settings()
