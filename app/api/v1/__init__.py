from __future__ import annotations

from app.api.v1.folder_routes import folder_router
from app.api.v1.import_job_routes import import_job_router, kb_import_job_router
from app.api.v1.template_routes import template_router
from app.api.v1.upload_routes import upload_router

__all__ = ["folder_router", "import_job_router", "kb_import_job_router", "template_router", "upload_router"]
