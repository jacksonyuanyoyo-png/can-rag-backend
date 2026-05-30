from __future__ import annotations

from app.core.config import Settings
from app.core.errors import BusinessError, ErrorCode
from app.domain.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    LoginResponseData,
    LogoutResponseData,
    RefreshResponseData,
    UserMePublic,
    UserPublic,
)
from app.services.auth.dev_users import find_dev_user_by_id, verify_dev_credentials
from app.services.auth.jwt_tokens import create_access_token, decode_access_token
from app.services.auth.refresh_store import InMemoryRefreshStore


class AuthService:
    """鉴权服务：优先 PostgreSQL 用户仓储，未配置 DATABASE_URL 时回退内存开发用户。"""

    def __init__(
        self,
        settings: Settings,
        refresh_store: InMemoryRefreshStore | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        self._settings = settings
        self._refresh_store = refresh_store or InMemoryRefreshStore()
        self._user_repository = user_repository

    @property
    def refresh_cookie_name(self) -> str:
        return self._settings.AUTH_REFRESH_COOKIE_NAME

    @property
    def refresh_token_ttl_seconds(self) -> int:
        return self._settings.AUTH_REFRESH_TOKEN_EXPIRE_SECONDS

    def login(self, *, email: str, password: str) -> tuple[LoginResponseData, str]:
        user = self._verify_credentials(email, password)
        if user is None:
            raise BusinessError(ErrorCode.AUTH_INVALID_CREDENTIALS)

        access_token = self._create_access_token(user.id)
        refresh_session = self._refresh_store.issue(
            user_id=user.id,
            ttl_seconds=self.refresh_token_ttl_seconds,
        )
        return (
            LoginResponseData(
                access_token=access_token,
                expires_in=self._settings.AUTH_ACCESS_TOKEN_EXPIRE_SECONDS,
                user=self._to_user_public(user),
            ),
            refresh_session.token,
        )

    def me(self, *, access_token: str) -> UserMePublic:
        user = self._user_from_access_token(access_token)
        return UserMePublic(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            permissions=user.permissions,
            team_id=user.team_id,
        )

    def refresh(self, *, refresh_token: str | None) -> tuple[RefreshResponseData, str | None]:
        if not refresh_token:
            raise BusinessError(ErrorCode.AUTH_REFRESH_EXPIRED)

        rotated = self._refresh_store.rotate(
            refresh_token,
            ttl_seconds=self.refresh_token_ttl_seconds,
        )
        if rotated is None:
            raise BusinessError(ErrorCode.AUTH_REFRESH_EXPIRED)

        access_token = self._create_access_token(rotated.user_id)
        return (
            RefreshResponseData(
                access_token=access_token,
                expires_in=self._settings.AUTH_ACCESS_TOKEN_EXPIRE_SECONDS,
            ),
            rotated.token,
        )

    def logout(self, *, refresh_token: str | None) -> LogoutResponseData:
        if refresh_token:
            self._refresh_store.revoke(refresh_token)
        return LogoutResponseData(success=True)

    def resolve_user_id(self, *, access_token: str) -> str:
        payload = decode_access_token(access_token, secret=self._settings.AUTH_JWT_SECRET)
        return str(payload["sub"])

    def _verify_credentials(self, email: str, password: str) -> User | None:
        if self._user_repository is not None:
            return self._user_repository.verify_credentials(email, password)
        dev_user = verify_dev_credentials(email, password)
        if dev_user is None:
            return None
        return User.from_dev_user(dev_user)

    def _user_from_access_token(self, access_token: str) -> User:
        user_id = self.resolve_user_id(access_token=access_token)
        user = self._find_user_by_id(user_id)
        if user is None:
            raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)
        return user

    def _find_user_by_id(self, user_id: str) -> User | None:
        if self._user_repository is not None:
            return self._user_repository.find_by_id(user_id)
        dev_user = find_dev_user_by_id(user_id)
        if dev_user is None:
            return None
        return User.from_dev_user(dev_user)

    def _create_access_token(self, user_id: str) -> str:
        return create_access_token(
            user_id=user_id,
            secret=self._settings.AUTH_JWT_SECRET,
            expires_in_seconds=self._settings.AUTH_ACCESS_TOKEN_EXPIRE_SECONDS,
        )

    @staticmethod
    def _to_user_public(user: User) -> UserPublic:
        return UserPublic(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            permissions=user.permissions,
        )
