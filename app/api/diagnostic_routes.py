from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.core.database import check_database


diagnostic_router = APIRouter(prefix="/test", tags=["Test"])


@diagnostic_router.get("/ping")
async def ping(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": request.app.title,
        "python": sys.version.split()[0],
        "routes": len(request.app.routes),
    }


@diagnostic_router.get("/postgres")
async def postgres(request: Request) -> dict[str, Any]:
    settings = getattr(request.app.state, "settings", get_settings())
    return check_database(settings)
