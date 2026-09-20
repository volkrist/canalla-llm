import os
import secrets
import sys
from pathlib import Path


def default_root() -> Path:
    override = (os.environ.get("ALEX_LLM_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Alex LLM"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Alex LLM"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "alex-llm"


def application_data(settings):
    if settings.alex_llm_data_dir:
        return Path(settings.alex_llm_data_dir).expanduser().resolve()
    return default_root()


def embedding_home(settings):
    return application_data(settings) / "models/embeddings/multilingual-e5-small"


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.resolve().as_posix()


def jwt_path(root: Path | None = None) -> Path:
    return (root or default_root()) / "runtime" / "jwt.secret"


def ensure_layout(root: Path | None = None) -> dict[str, Path]:
    base = root or default_root()
    layout = {
        "root": base,
        "data": base / "data",
        "documents": base / "documents",
        "models": base / "models" / "embeddings",
        "logs": base / "logs",
        "runtime": base / "runtime",
        "db": base / "data" / "alex.db",
        "jwt": base / "runtime" / "jwt.secret",
        "lock": base / "runtime" / "backend.lock",
        "shutdown": base / "runtime" / "shutdown.token",
    }
    for key in ("data", "documents", "models", "logs", "runtime"):
        layout[key].mkdir(parents=True, exist_ok=True)
    return layout


def load_or_create_jwt(path: Path | None = None) -> str:
    target = path or jwt_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        secret = target.read_text(encoding="utf-8").strip()
        if len(secret) >= 48:
            return secret
    secret = secrets.token_urlsafe(48)
    target.write_text(secret, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return secret


def load_or_create_runtime_token(path: Path | None = None) -> str:
    target = path or (default_root() / "runtime" / "shutdown.token")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        token = target.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    target.write_text(token, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return token
