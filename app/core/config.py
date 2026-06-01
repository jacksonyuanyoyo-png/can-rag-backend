from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置，支持 .env 与环境变量覆盖。"""

    FASTGPT_BASE_URL: str = ""
    FASTGPT_API_KEY: str = ""
    FASTGPT_CHAT_PATH: str = "/api/v1/chat/completions"

    OPENAI_API_KEY: str = ""
    # 兼容 OpenAI SDK / LangChain：需带 /v1 后缀（如 openai-proxy）
    OPENAI_BASE_URL: str = "https://api.openai-proxy.org/v1"
    OPENAI_CHAT_MODEL: str = "gpt-4.1-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
    OPENAI_VECTOR_STORE_NAME: str = "default-rag-store"
    VLM_ENABLED: bool = False
    CHAT_VISION_ENABLED: bool = True
    CHAT_VISION_MAX_IMAGES: int = 4
    CHAT_HISTORY_MAX_TURNS: int = 20
    VLM_MODEL: str = "gpt-4.1-mini"
    PDF_VLM_MODEL: str = "gpt-4.1-mini"
    VLM_TIMEOUT_SECONDS: float = 120.0
    VLM_MIN_IMAGE_BYTES: int = 5120
    VLM_MAX_IMAGE_BYTES: int = 8_000_000
    VLM_MAX_PDF_BYTES: int = 20_000_000
    PDF_AUTO_ENHANCE_MIN_CHARS: int = 100
    PDF_VLM_MIN_MARKDOWN_CHARS: int = 80
    PDF_RENDER_DPI: int = 200
    DATABASE_URL: str = ""

    LOCAL_UPLOAD_ROOT: str = str(_PROJECT_ROOT / "app" / "storage" / "uploads")
    LOCAL_METADATA_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "metadata" / "knowledge_bases.json")
    LOCAL_VECTOR_STORE_PATH: str = str(_PROJECT_ROOT / "app" / "storage" / "vector_store")
    HTTP_TIMEOUT_SECONDS: float = 60.0

    WEB_FETCH_MAX_BYTES: int = 5_242_880
    WEB_FETCH_TIMEOUT_SECONDS: float = 30.0
    WEB_MIN_CONTENT_CHARS: int = 200
    WEB_LINK_DENSITY_MAX: float = 0.35
    WEB_ENABLE_BROWSER_FALLBACK: bool = True
    WEB_USER_AGENT: str = (
        "Mozilla/5.0 (compatible; CAN-RAG/1.0; +https://www.fidelity.ca)"
    )

    DEFAULT_MAX_TOKENS: int = 4096
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 120
    RAG_EMBEDDING_DIMENSIONS: int = 256
    # auto：有 Key 且维度满足时用 OpenAI；hash：始终本地占位向量（无需有效 Key）
    RAG_EMBEDDING_BACKEND: str = "auto"
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

    @field_validator("OPENAI_API_KEY", mode="before")
    @classmethod
    def _normalize_openai_api_key(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip().strip('"').strip("'")

    @field_validator("RAG_EMBEDDING_BACKEND", mode="before")
    @classmethod
    def _normalize_embedding_backend(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if normalized in {"", "auto"}:
            return "auto"
        if normalized in {"hash", "openai"}:
            return normalized
        return normalized

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
