from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import get_settings
from app.core.database import normalize_psycopg_url, is_database_configured
from tests.fake_openai_chat import FakeOpenAIChatService


@pytest.fixture
def fake_openai_chat_service() -> FakeOpenAIChatService:
    return FakeOpenAIChatService()


@pytest.fixture(autouse=True)
def _patch_openai_chat_service(
    fake_openai_chat_service: FakeOpenAIChatService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.conversation_service.OpenAIChatService",
        lambda *_args, **_kwargs: fake_openai_chat_service,
    )


@pytest.fixture(scope="session")
def database_url() -> str:
    settings = get_settings()
    if not is_database_configured(settings):
        pytest.skip("DATABASE_URL 未配置，跳过数据库仓储测试。")
    return settings.DATABASE_URL


@pytest.fixture
def db_connection(database_url: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        normalize_psycopg_url(database_url),
        row_factory=dict_row,
    )
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.execute("ROLLBACK")
        conn.close()
