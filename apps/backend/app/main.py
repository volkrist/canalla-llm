import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import context_builder  # noqa: F401
from .auth import router as auth_router
from .chats import router as chats_router
from .compute.controller import RunPodController
from .compute.routes import admin as admin_router
from .compute.routes import router as compute_router
from .compute.runpod_api import RunPodError
from .config import get_settings
from .documents.limits import UploadLimit
from .documents.routes import router as documents_router
from .documents.service import reconcile_jobs
from .personal import router as personal_router
from .presence import PresenceManager
from .presence import router as presence_router
from .providers import LLMError, make_provider
from .security import current_user
from .tools.executor import reconcile_tools
from .tools.registry import make_registry
from .tools.routes import router as tools_router
from .tools.task_routes import router as tasks_router


class RedactTicket(logging.Filter):
    def filter(self, record):
        import re

        def redact(value):
            if not isinstance(value, str):
                return value
            return re.sub(r"\?[^\s\"']+", "?[redacted]", value)

        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        return True


for logger_name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(logger_name).addFilter(RedactTicket())

settings = get_settings()


@asynccontextmanager
async def lifespan(application):
    reconcile_jobs()
    reconcile_tools()
    application.state.tools = getattr(application.state, "tools_override", None) or make_registry()
    application.state.presence = PresenceManager(settings)
    application.state.presence.reconcile()
    presence_task = asyncio.create_task(application.state.presence.monitor())
    compute = getattr(application.state, "compute_override", None) or RunPodController(settings)
    application.state.compute = compute
    if compute.llm:
        application.state.provider = compute.llm

    async def queue_monitor():
        from .tools.local.continue_task import continue_pending
        from .tools.registry import make_orchestrator

        while True:
            await asyncio.sleep(2)
            try:
                await continue_pending(
                    make_orchestrator(application.state.tools),
                    application.state.provider,
                    host_online=True,
                )
            except Exception as error:
                logging.getLogger(__name__).warning("queue_monitor_failure type=%s", type(error).__name__)

    queue_task = asyncio.create_task(queue_monitor()) if settings.app_env != "test" else None
    try:
        await compute.recover()
    except RunPodError as error:
        logging.getLogger(__name__).warning("compute_recovery_failure code=%s", error.code)

    async def monitor():
        while True:
            await asyncio.sleep(settings.compute_poll_seconds)
            try:
                await compute.tick()
            except Exception as error:
                logging.getLogger(__name__).warning("compute_monitor_failure type=%s", type(error).__name__)

    task = asyncio.create_task(monitor()) if settings.compute_background_enabled else None
    try:
        yield
    finally:
        await application.state.tools.close()
        presence_task.cancel()
        with suppress(asyncio.CancelledError):
            await presence_task
        if queue_task:
            queue_task.cancel()
            with suppress(asyncio.CancelledError):
                await queue_task
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Alex LLM API",
    version="0.9.3",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.state.provider = make_provider(settings)
app.state.provider_name = settings.llm_provider
app.state.generating = set()
app.add_middleware(UploadLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Alex-Device-Id", "X-Alex-Device-Credential"],
)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(compute_router)
app.include_router(admin_router)
app.include_router(personal_router)
app.include_router(presence_router)
app.include_router(documents_router)
app.include_router(tools_router)
app.include_router(tasks_router)


@app.exception_handler(RunPodError)
async def runpod_error(request, error):
    return JSONResponse(
        status_code=error.status if error.status not in {401, 403, 404} else 502,
        content={"detail": str(error), "code": error.code, "request_id": str(uuid4())},
    )


@app.get("/llm/status")
async def llm_status(user=Depends(current_user)):
    if settings.llm_provider == "mock":
        return {"provider": "mock", "available": True, "state": "mock"}
    state = await app.state.provider.status()
    compute_state = app.state.compute.get_compute_status(user)["state"]
    if state == "offline" and compute_state in {
        "creating",
        "starting_pod",
        "starting_environment",
        "mounting_storage",
        "starting_llm",
        "connecting",
    }:
        state = "starting"
    return {
        "provider": settings.llm_provider,
        "available": state == "ready",
        "state": state,
        "model": settings.llm_model,
    }


@app.exception_handler(LLMError)
async def llm_error(request, error):
    return JSONResponse(
        status_code=503, content={"detail": str(error), "code": error.code, "request_id": str(uuid4())}
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "llm_ready": await app.state.provider.health(),
        "product": "alex-llm",
        "instance": os.environ.get("ALEX_BACKEND_INSTANCE") or None,
    }
