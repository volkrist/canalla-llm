#!/usr/bin/env python3
"""Provision the local embedding model on a trusted machine.

The File-search and RAG acceptance needs the *real* model, not a mock: three Playwright specs
(`files.spec.ts` x2, `web-tools.spec.ts`) fail honestly with «Embedding service unavailable» when
it is absent. This script installs it exactly the way the product does — never a shortcut:

* the pinned HuggingFace repository and revision from ``app/documents/model_manifest.py``;
* every artifact verified by size and hash (``EmbeddingModelManager``);
* an embedding smoke test over the loaded model before it is marked ready.

It needs no GPU, no provider credential and no signing key. By default it writes to the platform
data root (``%LOCALAPPDATA%\\Alex LLM`` on Windows), which is where the packaged app and the
Playwright e2e backend both look for it. ``--data-dir`` overrides that; if ``ALEX_LLM_DATA_DIR``
is already set it is honoured.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "apps" / "backend"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="", help="data root to install into")
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds to wait for READY")
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["ALEX_LLM_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("JWT_SECRET", "embedding-provisioning-not-a-real-secret-" + "a" * 24)

    sys.path.insert(0, str(BACKEND))
    from app.data_paths import embedding_home  # noqa: E402
    from app.documents.model_manager import ACTIVE, EmbeddingModelManager  # noqa: E402
    from app.documents.model_manifest import REVISION  # noqa: E402

    manager = EmbeddingModelManager()
    home = embedding_home(manager.settings)
    status = manager.status()
    if status["ready"]:
        print(f"EMBEDDING-MODEL: already ready at {home}")
        return 0

    print(f"EMBEDDING-MODEL: preparing {REVISION} into {home} ({manager.total} bytes)")
    manager.prepare()
    deadline = time.monotonic() + args.timeout
    last = ""
    while time.monotonic() < deadline:
        status = manager.status()
        state = str(status["state"])
        if state != last:
            last = state
            print(f"EMBEDDING-MODEL: {state} {status['downloaded_bytes']}/{status['total_bytes']} bytes")
        if status["ready"]:
            print(f"EMBEDDING-MODEL: PASS — ready at {home}")
            return 0
        if state not in ACTIVE and state not in {"READY"}:
            print(f"EMBEDDING-MODEL: FAIL — {state}: {status.get('error')}")
            return 1
        time.sleep(2.0)

    print("EMBEDDING-MODEL: FAIL — timed out waiting for READY")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
