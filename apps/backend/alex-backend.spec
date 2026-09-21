# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

backend = Path(SPECPATH)

datas = [
    (str(backend / "alembic"), "alembic"),
    (str(backend / "alembic.ini"), "."),
    (str(backend / "app" / "compute" / "remote_runtime.py"), "app/compute"),
]
binaries = []
hidden = []
for package in (
    "app",
    "uvicorn",
    "fastapi",
    "starlette",
    "alembic",
    "sqlalchemy",
    "pydantic",
    "pydantic_settings",
    "anyio",
    "httpx",
    "httpcore",
    "websockets",
    "httptools",
    "multipart",
):
    hidden += collect_submodules(package)

hidden += [
    "pwdlib",
    "pwdlib.hashers.argon2",
    "argon2",
    "jwt",
    "email_validator",
    "filelock",
    "pypdf",
    "docx",
    "numpy",
    "psycopg",
    "psycopg_binary",
]

for package in ("onnxruntime", "fastembed", "tokenizers"):
    try:
        hidden += collect_submodules(package)
        datas += collect_data_files(package)
        binaries += collect_dynamic_libs(package)
    except Exception:
        hidden.append(package)

a = Analysis(
    [str(backend / "sidecar_main.py")],
    pathex=[str(backend)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff", "tkinter", "matplotlib", "IPython", "notebook", "playwright.__main__"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="alex-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="alex-backend",
)
