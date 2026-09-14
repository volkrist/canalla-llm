import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import router as auth_router
from .chats import router as chats_router
from .compute.controller import RunPodController
from .compute.routes import admin as admin_router
from .compute.routes import router as compute_router
from .compute.runpod_api import RunPodError
from .config import get_settings
from .providers import make_provider
from .security import current_user

settings = get_settings()


@asynccontextmanager
async def lifespan(application):
    compute = getattr(application.state, "compute_override", None) or RunPodController(settings)
    application.state.compute = compute
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
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Alex LLM API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.state.provider = make_provider(settings)
app.state.provider_name = settings.llm_provider
app.state.generating = set()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(compute_router)
app.include_router(admin_router)


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
    available = await app.state.provider.health()
    return {
        "provider": settings.llm_provider,
        "available": available,
        "state": "ready" if available else "offline",
        "model": settings.llm_model,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.llm_provider, "llm_ready": await app.state.provider.health()}
