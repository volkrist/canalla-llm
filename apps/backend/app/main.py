from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import router as auth_router
from .chats import router as chats_router
from .config import get_settings
from .providers import make_provider

settings = get_settings()
app = FastAPI(
    title="Alex LLM API",
    version="0.1.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.state.provider = make_provider(settings)
app.state.generating = set()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth_router)
app.include_router(chats_router)


@app.get("/health")
async def health():
    return {"status": "ok", "provider": settings.llm_provider, "llm_ready": await app.state.provider.health()}
