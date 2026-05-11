from __future__ import annotations

import sys
from typing import Any

from fastapi import APIRouter, Request


diagnostic_router = APIRouter(prefix="/test", tags=["Test"])


@diagnostic_router.get("/ping")
async def ping(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": request.app.title,
        "python": sys.version.split()[0],
        "routes": len(request.app.routes),
    }
