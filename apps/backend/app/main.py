import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import context_builder  # noqa: F401
from .auth import router as auth_router
from .chats import router as chats_router
from .cloud.client import CloudError
from .cloud.provider import GatewayBalanceSource, GatewayProvider
from .cloud.routes import router as cloud_router
from .cloud.state import CloudAi, CloudState
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
from .product import PRODUCT, RUNTIME_PROTOCOL_VERSION, VERSION
from .providers import LLMError, make_provider
from .runtime_log import RedactTicket
from .security import current_user
from .status.balance import RunPodBalanceService
from .status.routes import router as status_router
from .tools.executor import reconcile_tools
from .tools.registry import make_registry
from .tools.routes import router as tools_router
from .tools.task_routes import router as tasks_router

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
    cloud = getattr(application.state, "cloud_override", None) or CloudState(settings)
    application.state.cloud = cloud
    if cloud.shared:
        # Production shared mode: inference and balance come from Alex Cloud, and the local
        # RunPod credential is deliberately ignored (it stays as the private/direct path).
        application.state.provider = GatewayProvider(settings, cloud.client)
        application.state.ai_status_source = CloudAi(cloud)
        application.state.balance = getattr(
            application.state, "balance_override", None
        ) or RunPodBalanceService(GatewayBalanceSource(cloud.client), cloud.active, lambda: None)
        await cloud.start()
    else:
        application.state.ai_status_source = compute
        application.state.balance = getattr(
            application.state, "balance_override", None
        ) or RunPodBalanceService(compute.api, compute.gpu_active, compute.active_session)
    if settings.balance_background_enabled:
        await application.state.balance.start()

    async def queue_monitor():
        from .compute.demand import resume_parked_demand
        from .tools.local.continue_task import continue_pending
        from .tools.registry import make_orchestrator

        while True:
            await asyncio.sleep(2)
            try:
                await resume_parked_demand(application.state.compute)
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
        cloud = getattr(application.state, "cloud", None)
        if cloud is not None:
            await cloud.stop()
        if queue_task:
            queue_task.cancel()
            with suppress(asyncio.CancelledError):
                await queue_task
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await application.state.balance.stop()


app = FastAPI(
    title="Canalla LLM API",
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
app.include_router(status_router)
app.include_router(cloud_router)
app.include_router(documents_router)
app.include_router(tools_router)
app.include_router(tasks_router)


def ai_status_source():
    """The owner of AI status: the local controller in direct mode, Alex Cloud in shared."""
    return getattr(app.state, "ai_status_source", None) or app.state.compute


@app.exception_handler(RunPodError)
async def runpod_error(request, error):
    return JSONResponse(
        status_code=error.status if error.status not in {401, 403, 404} else 502,
        content={"detail": str(error), "code": error.code, "request_id": str(uuid4())},
    )


@app.exception_handler(CloudError)
async def cloud_error(request, error):
    status_code = {
        "gateway_not_connected": 503,
        "gateway_unavailable": 503,
        "gateway_busy": 429,
        "gateway_queue_full": 429,
        "installation_revoked": 409,
        "gateway_auth_failed": 409,
        "gateway_protocol_mismatch": 409,
        "gateway_budget_denied": 409,
    }.get(error.code, 502)
    return JSONResponse(
        status_code=status_code,
        content={"detail": str(error) or error.code, "code": error.code, "request_id": str(uuid4())},
    )


@app.get("/llm/status")
async def llm_status(user=Depends(current_user)):
    payload = ai_status_source().llm_public_status(user)
    if settings.llm_provider == "llamacpp" and settings.llm_connection_mode == "static":
        from .compute.runtime import LABELS

        state = await app.state.provider.status()
        payload["state"] = state
        payload["available"] = state == "ready"
        payload["ai"] = (
            "ready" if state == "ready" else "starting" if state == "loading_model" else "unavailable"
        )
        payload["ai_label"] = LABELS.get(payload["ai"], payload.get("ai_label"))
    return payload


@app.post("/runtime/shutdown")
async def runtime_shutdown(request: Request):
    import hmac

    expected = os.environ.get("ALEX_RUNTIME_TOKEN") or ""
    got = request.headers.get("X-Alex-Runtime-Token") or ""
    if not expected or len(expected) != len(got) or not hmac.compare_digest(expected, got):
        raise HTTPException(403, "Forbidden")
    if settings.alex_ai_mode == "shared":
        # Shared compute is owned by Alex Cloud: one installation quitting never stops a
        # Pod other installations may be using. The Gateway applies the global idle rule.
        return {"ok": True, "stopped": False, "managed": "gateway"}
    result = await request.app.state.compute.shutdown_managed("app_quit")
    return {"ok": True, "stopped": result.get("stopped"), "managed": result.get("managed")}


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
        "product": PRODUCT,
        "version": VERSION,
        "runtime_protocol_version": RUNTIME_PROTOCOL_VERSION,
        "instance": os.environ.get("ALEX_BACKEND_INSTANCE") or None,
    }
