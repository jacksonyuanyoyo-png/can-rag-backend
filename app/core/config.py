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
    OPENAI_VECTOR_STORE_NAME: str = "default-rag-store"

    LOCAL_UPLOAD_ROOT: str = str(_PROJECT_ROOT / "app" / "storage" / "uploads")
    LOCAL_METADATA_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "metadata" / "knowledge_bases.json")
    LOCAL_VECTOR_STORE_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "vector_store")
    HTTP_TIMEOUT_SECONDS: float = 60.0

    DEFAULT_MAX_TOKENS: int = 4096
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 120
    RAG_EMBEDDING_DIMENSIONS: int = 256

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


def get_settings() -> Settings:
    return Settings()
