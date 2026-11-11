"""Does one tool timeout end the whole turn?

The live T4 run ended with `generation_failed type=ToolError` exactly when `search_files` hit its
60 s timeout, and the assistant message became `error`. If `_run` really lets a `ToolError` escape,
a single slow tool kills a turn that could have continued — a product defect, not a harness one.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.tools.contracts import ToolError
from app.tools.orchestrator import ToolOrchestrator


class ExplodingExecutor:
    def __init__(self, code: str) -> None:
        self.code = code

    async def execute(self, name, arguments, context, origin="model"):
        raise ToolError(self.code)


def _context():
    async def emit(event, value):
        return None

    return SimpleNamespace(
        user_prompt="В папке X найди t4l.txt",
        limits=SimpleNamespace(calls=0, max_calls=24, max_seconds=600, remaining=600, consume=lambda *a: None),
        settings=SimpleNamespace(workspace_roots=["C:\\work"]),
        task_id=None,
        user_id="u",
        chat_id="c",
        secrets=(),
        sources=[],
        completed_tool_calls=0,
        computer_mode="ask",
    )


def test_a_tool_timeout_is_reported_to_the_model_not_raised():
    orchestrator = ToolOrchestrator(registry=None, executor=ExplodingExecutor("timeout"))
    notes: list[str] = []
    packed = asyncio.run(
        orchestrator._run("search_files", json.dumps({"root": "C:\\work", "query": "x"}), _context(), notes, "model")
    )
    assert isinstance(packed, dict), "a tool failure must become a result, not an exception"
    assert packed.get("status") == "timeout"
    assert notes, "the model must be told what happened"
