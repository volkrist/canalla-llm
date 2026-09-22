"""Test-only ``Settings`` factory.

pydantic-settings fills required fields from the environment and accepts the
``_env_file`` switch at runtime; the ``__init__`` pydantic synthesises exposes neither
to a type checker, so the constructor is reached through an explicitly typed callable.
"""

from collections.abc import Callable
from typing import Any, cast

from app.config import Settings


def make_settings(**values: Any) -> Settings:
    """Build ``Settings`` the way the app does, with no values invented."""
    return cast("Callable[..., Settings]", Settings)(**values)
