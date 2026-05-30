from __future__ import annotations

from app.core.errors import BusinessError, ErrorCode
from app.domain.template import Template, TemplateScope, build_snippet
from app.repositories.template_repository import (
    TemplateNameDuplicatedError,
    TemplateNotFoundError,
    TemplateRepository,
)


class TemplateService:
    """聊天模板用例服务。"""

    def __init__(self, repository: TemplateRepository) -> None:
        self._repository = repository

    def list_templates(self, *, owner_user_id: str) -> list[Template]:
        return self._repository.list_by_owner(owner_user_id)

    def get_template(self, template_id: str) -> Template:
        return self._require_template(template_id)

    def create_template(
        self,
        *,
        owner_user_id: str,
        name: str,
        content: str,
        snippet: str | None = None,
        scope: TemplateScope = TemplateScope.PERSONAL,
    ) -> Template:
        normalized_name = name.strip()
        normalized_content = content.strip()
        if not normalized_name:
            raise BusinessError(ErrorCode.VALIDATION_ERROR, "Template name cannot be empty")
        if not normalized_content:
            raise BusinessError(ErrorCode.VALIDATION_ERROR, "Template content cannot be empty")

        resolved_snippet = snippet.strip() if snippet is not None else build_snippet(normalized_content)
        try:
            return self._repository.create(
                owner_user_id=owner_user_id,
                name=normalized_name,
                content=normalized_content,
                snippet=resolved_snippet,
                scope=scope,
            )
        except TemplateNameDuplicatedError as exc:
            raise BusinessError(ErrorCode.TEMPLATE_NAME_DUPLICATED) from exc

    def update_template(
        self,
        template_id: str,
        *,
        name: str | None = None,
        content: str | None = None,
        snippet: str | None = None,
        scope: TemplateScope | None = None,
    ) -> Template:
        normalized_name = name.strip() if name is not None else None
        normalized_content = content.strip() if content is not None else None
        normalized_snippet = snippet.strip() if snippet is not None else None

        if normalized_name is not None and not normalized_name:
            raise BusinessError(ErrorCode.VALIDATION_ERROR, "Template name cannot be empty")
        if normalized_content is not None and not normalized_content:
            raise BusinessError(ErrorCode.VALIDATION_ERROR, "Template content cannot be empty")

        try:
            return self._repository.update(
                template_id,
                name=normalized_name,
                content=normalized_content,
                snippet=normalized_snippet,
                scope=scope,
            )
        except TemplateNotFoundError as exc:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND) from exc
        except TemplateNameDuplicatedError as exc:
            raise BusinessError(ErrorCode.TEMPLATE_NAME_DUPLICATED) from exc

    def delete_template(self, template_id: str) -> None:
        try:
            self._repository.delete(template_id)
        except TemplateNotFoundError as exc:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND) from exc

    def _require_template(self, template_id: str) -> Template:
        template = self._repository.get_by_id(template_id)
        if template is None:
            raise BusinessError(ErrorCode.TEMPLATE_NOT_FOUND)
        return template
