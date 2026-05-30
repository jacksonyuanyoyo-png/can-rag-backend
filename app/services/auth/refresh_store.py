from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass(slots=True)
class RefreshSession:
    token: str
    user_id: str
    expires_at: int


class InMemoryRefreshStore:
    """开发环境内存 Refresh Token 存储，进程重启后失效。"""

    def __init__(self) -> None:
        self._sessions: dict[str, RefreshSession] = {}

    def issue(self, *, user_id: str, ttl_seconds: int) -> RefreshSession:
        token = secrets.token_urlsafe(32)
        session = RefreshSession(
            token=token,
            user_id=user_id,
            expires_at=int(time.time()) + ttl_seconds,
        )
        self._sessions[token] = session
        return session

    def get(self, token: str) -> RefreshSession | None:
        session = self._sessions.get(token)
        if session is None:
            return None
        if int(time.time()) >= session.expires_at:
            self._sessions.pop(token, None)
            return None
        return session

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)

    def rotate(self, old_token: str, *, ttl_seconds: int) -> RefreshSession | None:
        session = self.get(old_token)
        if session is None:
            return None
        self.revoke(old_token)
        return self.issue(user_id=session.user_id, ttl_seconds=ttl_seconds)
