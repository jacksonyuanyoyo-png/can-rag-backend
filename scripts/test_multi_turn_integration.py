#!/usr/bin/env python3
"""多轮对话集成自测：PostgreSQL 持久化 + 重启后历史是否仍在 LLM 上下文中。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.database import is_database_configured
from app.core.bootstrap import _build_conversation_repository
from app.services.conversation_service import ConversationService
from tests.fake_openai_chat import FakeOpenAIChatService


class CapturingChat(FakeOpenAIChatService):
    def __init__(self) -> None:
        super().__init__()
        self.last_messages: list[dict] = []

    async def stream(self, *, messages, model, cancel_event=None):
        self.last_messages = list(messages)
        async for item in super().stream(
            messages=messages, model=model, cancel_event=cancel_event
        ):
            yield item


async def _stream_turn(service: ConversationService, conv_id: str, content: str) -> None:
    async for _event in service.stream_message(
        conversation_id=conv_id,
        content=content,
        model_id="gpt-4o-mini",
    ):
        pass


def _text_messages(messages: list[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role", "")
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            out.append((role, content))
    return out


async def main() -> int:
    settings = get_settings()
    if not is_database_configured(settings):
        print("SKIP: DATABASE_URL 未配置，无法验证 PostgreSQL 多轮持久化。")
        return 0

    repo = _build_conversation_repository(settings)
    chat = CapturingChat()
    service = ConversationService(
        repo,
        settings=settings,
        chat_service=chat,
        knowledge_base_service=None,
    )

    conv = service.create_conversation(title="Multi-turn integration")
    conv_id = conv.id
    print(f"1. 创建会话: {conv_id}")

    await _stream_turn(service, conv_id, "我叫小明，请记住我的名字。")
    turn1_msgs = _text_messages(chat.last_messages)
    print(f"2. 第一轮 LLM 消息数: {len(turn1_msgs)}")

    await _stream_turn(service, conv_id, "我今年 30 岁，请记住我的年龄。")
    turn2_msgs = _text_messages(chat.last_messages)
    print(f"3. 第二轮 LLM 消息数: {len(turn2_msgs)}")
    turn2_users = [text for role, text in turn2_msgs if role == "user"]
    turn2_assistants = [text for role, text in turn2_msgs if role == "assistant"]
    if not any("小明" in text for text in turn2_users):
        print("FAIL: 第二轮上下文中未包含第一轮用户消息「小明」")
        return 1
    if not turn2_assistants:
        print("FAIL: 第二轮上下文中未包含第一轮 assistant 回复")
        return 1
    print("   OK: 第二轮已带上第一轮 user + assistant 历史")

    # 模拟进程重启：新 Service + 新仓储连接，同一 conversationId
    repo2 = _build_conversation_repository(settings)
    chat2 = CapturingChat()
    service2 = ConversationService(
        repo2,
        settings=settings,
        chat_service=chat2,
        knowledge_base_service=None,
    )
    reloaded = service2.require_conversation(conv_id)
    print(f"4. 重启后加载消息条数: {len(reloaded.messages)}")
    if len(reloaded.messages) < 4:
        print(f"FAIL: 期望至少 4 条消息（两轮各 user+assistant），实际 {len(reloaded.messages)}")
        return 1

    await _stream_turn(service2, conv_id, "请复述我的名字和年龄。")
    turn3_msgs = _text_messages(chat2.last_messages)
    print(f"5. 第三轮 LLM 消息数: {len(turn3_msgs)}")
    all_user_text = " ".join(text for role, text in turn3_msgs if role == "user")
    if "小明" not in all_user_text:
        print("FAIL: 重启后第三轮未带上历史中的「小明」")
        return 1
    if "30" not in all_user_text and "三十" not in all_user_text:
        print("FAIL: 重启后第三轮未带上历史中的年龄信息")
        return 1
    print("   OK: 重启后第三轮仍包含前两轮用户内容")

    listed = service2.list_messages(conv_id)
    print(f"6. GET messages 条数: {len(listed)}")
    if len(listed) < 6:
        print(f"FAIL: 三轮后 list_messages 期望至少 6 条，实际 {len(listed)}")
        return 1

    print("\n全部通过：多轮对话 + PostgreSQL 持久化 + 重启后历史可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
