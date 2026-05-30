from __future__ import annotations

from uuid import uuid4

import pytest

from app.repositories.user_repository import (
    UserEmailDuplicatedError,
    UserRepository,
    hash_password,
    verify_password,
)


@pytest.fixture
def user_repo(database_url: str, db_connection) -> UserRepository:
    repo = UserRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


def test_password_hash_roundtrip() -> None:
    stored = hash_password("secret123")
    assert verify_password("secret123", stored)
    assert not verify_password("wrong", stored)


def test_verify_credentials_and_permissions(user_repo: UserRepository) -> None:
    suffix = uuid4().hex[:8]
    user_id = f"user_{suffix}"
    email = f"db-user-{suffix}@example.com"
    role_id = f"role_{suffix}"
    perm_id = f"perm_{suffix}"

    user_repo.create_user(
        user_id=user_id,
        email=email,
        display_name="DB User",
        password_hash=f"plain:pass123",
        default_team_id="team_test",
    )
    user_repo.upsert_permission(permission_id=perm_id, code="kb:read", domain="kb")
    user_repo.upsert_role(role_id=role_id, code="member", name="Member")
    user_repo.link_role_permission(role_id=role_id, permission_id=perm_id)
    user_repo.grant_role(user_id=user_id, role_id=role_id, team_id="team_test")

    user = user_repo.verify_credentials(email, "pass123")
    assert user is not None
    assert user.id == user_id
    assert user.team_id == "team_test"
    assert user.permissions == ["kb:read"]

    assert user_repo.verify_credentials(email, "wrong") is None
    assert user_repo.find_by_id(user_id) == user
    assert user_repo.find_by_email(email) == user


def test_duplicate_email_raises(user_repo: UserRepository) -> None:
    suffix = uuid4().hex[:8]
    email = f"dup-{suffix}@example.com"
    user_repo.create_user(
        user_id=f"user_{suffix}_1",
        email=email,
        display_name="First",
        password_hash="plain:pass123",
    )

    with pytest.raises(UserEmailDuplicatedError):
        user_repo.create_user(
            user_id=f"user_{suffix}_2",
            email=email,
            display_name="Second",
            password_hash="plain:pass123",
        )
