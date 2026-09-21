"""Gateway application assembly.

Run with ``uvicorn gateway.main:app``. The schema is owned by Alembic (its own history in
``apps/gateway/alembic``); ``/health`` reports ``missing_schema`` instead of inventing
tables, so a deployment can never silently serve an unmigrated database.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .compute import ComputeAuthority
from .config import GatewaySettings, get_settings
from .errors import GatewayError, error_body, error_response
from .inference import InferenceProxy
from .routes import router
from .security import limiter_for

logger = logging.getLogger(__name__)


def create_app(
    settings: GatewaySettings | None = None, *, api=None, sessions=None, background=True
) -> FastAPI:
    settings = settings or get_settings()
    if sessions is None:
        from .database import SessionLocal

        sessions = SessionLocal

    authority = ComputeAuthority(settings, api=api, sessions=sessions)
    proxy = InferenceProxy(settings, authority, queue=getattr(authority, "queue", None))
    authority.queue = proxy.queue

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.authority.initialize()
        app.state.authority.prune_operations()
        tasks = []
        if background and app.state.background_tasks_enabled:
            tasks.append(asyncio.create_task(app.state.authority.serve()))
            tasks.append(asyncio.create_task(app.state.authority.balance.start()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await app.state.authority.balance.stop()

    app = FastAPI(title="Alex Cloud Gateway", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = sessions
    app.state.authority = authority
    app.state.proxy = proxy
    app.state.limiter = limiter_for(settings)
    app.state.background_tasks_enabled = background
    app.include_router(router)

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, error: GatewayError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        if error.status >= 500:
            logger.warning("gateway_error code=%s request_id=%s", error.code, request_id)
        return error_response(error, request_id=request_id)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        # No stack traces, no internal paths and no secrets cross this boundary.
        logger.exception("unhandled_gateway_error")
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=500,
            content=error_body("gateway_internal_error", request_id=request_id),
        )

    return app


class LazyApp:
    """ASGI entry point for ``uvicorn gateway.main:app``.

    The application is built on first request so that importing this module never
    requires a fully configured environment. ``create_app()`` stays the explicit entry
    point used by tests and by the operator tooling.
    """

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def instance(self) -> FastAPI:
        if self._app is None:
            self._app = create_app()
        return self._app

    async def __call__(self, scope, receive, send):
        await self.instance()(scope, receive, send)


app = LazyApp()
