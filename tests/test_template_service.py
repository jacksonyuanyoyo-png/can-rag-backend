from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.errors import BusinessError, ErrorCode
from app.repositories.template_repository import TemplateRepository
from app.services.template_service import TemplateService


@pytest.fixture
def template_repo(database_url: str, db_connection) -> TemplateRepository:
    repo = TemplateRepository(database_url, connection=db_connection)
    repo.ensure_schema()
    return repo


@pytest.fixture
def template_service(template_repo: TemplateRepository) -> TemplateService:
    return TemplateService(template_repo)


def test_create_list_update_delete_template(template_service: TemplateService) -> None:
    owner_user_id = f"user_{uuid4().hex[:8]}"

    created = template_service.create_template(
        owner_user_id=owner_user_id,
        name="Bug Report",
        content="**Description:**\nIssue details",
    )
    assert created.name == "Bug Report"
    assert created.snippet.startswith("**Description:**")

    templates = template_service.list_templates(owner_user_id=owner_user_id)
    assert len(templates) == 1
    assert templates[0].id == created.id

    updated = template_service.update_template(
        created.id,
        name="Bug Report v2",
        content="Updated content",
    )
    assert updated.name == "Bug Report v2"
    assert updated.content == "Updated content"

    template_service.delete_template(created.id)
    assert template_service.list_templates(owner_user_id=owner_user_id) == []


def test_create_duplicate_name_raises_business_error(template_service: TemplateService) -> None:
    owner_user_id = f"user_{uuid4().hex[:8]}"
    template_service.create_template(
        owner_user_id=owner_user_id,
        name="Daily Standup",
        content="What did you do yesterday?",
    )

    with pytest.raises(BusinessError) as exc_info:
        template_service.create_template(
            owner_user_id=owner_user_id,
            name="Daily Standup",
            content="Another template",
        )
    assert exc_info.value.code == ErrorCode.TEMPLATE_NAME_DUPLICATED


def test_update_missing_template_raises_not_found(template_service: TemplateService) -> None:
    with pytest.raises(BusinessError) as exc_info:
        template_service.update_template("tmpl_missing", name="New Name")
    assert exc_info.value.code == ErrorCode.TEMPLATE_NOT_FOUND


def test_delete_missing_template_raises_not_found(template_service: TemplateService) -> None:
    with pytest.raises(BusinessError) as exc_info:
        template_service.delete_template("tmpl_missing")
    assert exc_info.value.code == ErrorCode.TEMPLATE_NOT_FOUND
