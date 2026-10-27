import os

from fastapi import APIRouter
from sqlalchemy import text

from sonder.api.dependencies import execution_enabled
from sonder.infrastructure.database import Session

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    with Session() as db:
        db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "execution_enabled": execution_enabled(),
        "mode": "docker" if os.getenv("PLATFORM_ENV") == "docker" else "local-preview",
    }
