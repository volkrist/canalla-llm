"""Provider core reuse.

The Gateway must not carry a second, divergent copy of the RunPod client, the pod
schemas or the Pod bootstrap script: direct mode and shared mode have to talk to the
provider in exactly the same way (same image, same mounts, same model, same flags).

So this module imports the *existing* client backend package as a library:
``app.compute.runpod_api`` (REST v2 + read-only GraphQL balance), ``app.compute.schemas``
and ``app.compute.remote_runtime`` (sent to the Pod as source). The backend library root
comes from ``ALEX_BACKEND_LIB_DIR`` and otherwise defaults to the sibling
``apps/backend`` directory of this checkout.

Importing ``app.compute.*`` pulls in ``app.config`` (a settings *class*, not an instance)
and nothing else: no database engine, no FastAPI application, no user data.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from .errors import GatewayError

BACKEND_LIB_ENV = "ALEX_BACKEND_LIB_DIR"


def backend_lib_dir() -> Path | None:
    override = (os.environ.get(BACKEND_LIB_ENV) or "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None
    # apps/gateway/gateway/provider.py -> parents[2] == apps
    candidate = Path(__file__).resolve().parents[2] / "backend"
    return candidate if candidate.is_dir() else None


@lru_cache
def load_core():
    """Import the shared provider core once. Raises a stable code when it is missing."""
    lib = backend_lib_dir()
    if lib is not None and str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    try:
        from app.compute import runpod_api, runtime, schemas  # noqa: PLC0415 - deliberate late import
    except ImportError as error:  # pragma: no cover - deployment error path
        raise GatewayError(
            "gateway_unavailable",
            detail=(
                "Провайдерская библиотека Alex недоступна на сервере. "
                f"Задайте {BACKEND_LIB_ENV} с путём к apps/backend."
            ),
        ) from error
    return runpod_api, schemas, runtime


def provider_api(settings, transport=None):
    """The real RunPod client, configured from gateway settings (same attribute names)."""
    runpod_api, _, _ = load_core()
    return runpod_api.RunPodAPI(settings, transport)


def provider_error_messages() -> dict:
    runpod_api, _, _ = load_core()
    return dict(runpod_api.ERROR_MESSAGES)


def provider_error_class():
    runpod_api, _, _ = load_core()
    return runpod_api.RunPodError


def compute_preferences(**values):
    """Shared pod search/creation schema, so the Gateway validates exactly like direct mode."""
    _, schemas, _ = load_core()
    return schemas.ComputePreferences(**values)


def compact_ai(**values) -> tuple[str, str]:
    """The product's own compute-state -> AI-state mapping. Never re-implemented here."""
    _, _, runtime = load_core()
    return runtime.compact_ai(**values)


def pod_bootstrap_source() -> bytes:
    """Source of the Pod-side gateway, sent as the Pod startup argument.

    Read from the shared backend package so direct and shared mode deploy byte-identical
    Pod runtimes.
    """
    lib = backend_lib_dir()
    if lib is None:  # pragma: no cover - deployment error path
        raise GatewayError("gateway_unavailable", detail="Провайдерская библиотека Alex недоступна.")
    path = lib / "app" / "compute" / "remote_runtime.py"
    if not path.is_file():  # pragma: no cover - deployment error path
        raise GatewayError("gateway_unavailable", detail="Pod runtime Alex не найден на сервере.")
    return path.read_bytes()
