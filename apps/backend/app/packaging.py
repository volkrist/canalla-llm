"""Packaged vs source resource resolver. Mutable user data stays in data_paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGED_CORS = '["https://tauri.localhost","tauri://localhost"]'


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_packaged() -> bool:
    return os.environ.get("ALEX_PACKAGED", "").strip() == "1" or is_frozen()


def app_resource_root() -> Path:
    override = (os.environ.get("ALEX_APP_RESOURCE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def apply_runtime_environment() -> None:
    if not is_packaged():
        return
    os.environ["ALEX_PACKAGED"] = "1"
    os.environ.setdefault("APP_ENV", "production")
    os.environ.setdefault("CORS_ORIGINS", PACKAGED_CORS)
    os.environ.setdefault("LLM_PROVIDER", "llamacpp")
    os.environ.setdefault("LLM_CONNECTION_MODE", "runpod")


def alembic_config():
    from alembic.config import Config

    root = app_resource_root()
    ini = root / "alembic.ini"
    if not ini.is_file():
        ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    scripts = root / "alembic"
    if not scripts.is_dir():
        scripts = ini.parent / "alembic"
    if not ini.is_file() or not scripts.is_dir():
        raise FileNotFoundError("alembic_resources_missing")
    config = Config(str(ini))
    config.set_main_option("script_location", str(scripts.resolve()))
    return config


def package_file(*parts: str) -> Path:
    candidates = [
        app_resource_root().joinpath(*parts),
        Path(__file__).resolve().parents[1].joinpath(*parts),
    ]
    if parts and parts[0] == "app":
        candidates.append(Path(__file__).resolve().parent.joinpath(*parts[1:]))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError("/".join(parts))
