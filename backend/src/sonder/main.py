"""FastAPI application composition and HTTP lifecycle."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sonder.api.routes import events, files, health, messages, projects, runs, settings
from sonder.infrastructure.database import engine


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    engine.dispose()


def create_app() -> FastAPI:
    application = FastAPI(title="Sonder", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(","),
    )
    for module in (health, settings, projects, messages, files, runs, events):
        application.include_router(module.router)
    return application


app = create_app()
