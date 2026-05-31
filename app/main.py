from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.conversation_routes import conversation_router
from app.api.diagnostic_routes import diagnostic_router
from app.api.http_common import ApiError, api_error_handler
from app.api.knowledge_base_routes import knowledge_base_router
from app.api.v1.auth_routes import auth_router
from app.api.v1.folder_routes import folder_router
from app.api.v1.import_job_routes import import_job_router, kb_import_job_router
from app.api.v1.knowledge_base_hit_test import hit_test_router
from app.api.v1.model_routes import model_router
from app.api.v1.template_routes import template_router
from app.api.v1.upload_routes import upload_router
from app.api.v1.web_import_routes import web_import_router
from app.core.bootstrap import wire_app_state
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.responses import REQUEST_ID_HEADER, attach_request_id, new_request_id

V1_ROUTERS = (
    auth_router,
    model_router,
    conversation_router,
    folder_router,
    template_router,
    knowledge_base_router,
    hit_test_router,
    upload_router,
    web_import_router,
    kb_import_job_router,
    import_job_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    wire_app_state(app, get_settings())
    poller = getattr(app.state, "import_job_poller", None)
    if poller is not None:
        await poller.start()
    try:
        yield
    finally:
        if poller is not None:
            await poller.stop()


app = FastAPI(title="Fidelity RAG Gateway", lifespan=lifespan)


@app.middleware("http")
async def inject_request_id(request: Request, call_next):
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming.strip() if incoming and incoming.strip() else new_request_id()
    request.state.request_id = request_id
    response = await call_next(request)
    return attach_request_id(response, request_id)


register_exception_handlers(app)
app.add_exception_handler(ApiError, api_error_handler)
app.include_router(diagnostic_router)
for router in V1_ROUTERS:
    app.include_router(router)
