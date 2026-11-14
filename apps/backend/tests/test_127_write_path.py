"""The write path that lost the LIVE TaskDesk, end to end and without a model.

The evidence of run `c8966118` is narrow: the model called `write_file`, the call came back
`invalid_arguments`, no file existed and the task could not continue. This file drives the real
orchestrator, the real executor, the real policy and a host emulator with *scripted* decisions, so
the mechanics around that call are proven offline:

* a real source file well over 72 KB travels as one JSON argument and is written byte for byte;
* a call whose JSON was cut off is reported as `invalid_json` with an explanation, the loop keeps
  going, and the model's next, corrected call is executed;
* content over the write bound is `content_too_large` with the instruction to split the file;
* a large file can be built from bounded parts with `write_file mode=append` and ends up identical
  to writing it whole;
* unknown fields are named in the refusal, so the model can remove them.

The scripted planner is never a live model, and nothing here claims it is: this is the free gate
the paid LIVE runs behind.
"""

from __future__ import annotations

import asyncio
import json

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ExecutionContext, ToolExecutor
from app.tools.local.provider import MAX_WRITE_CONTENT_CHARS
from app.tools.orchestrator import ToolOrchestrator
from app.tools.policy import ToolLimits, WebSettings
from app.tools.registry import make_registry

PROMPT = (
    "Создай приложение TaskDesk в рабочей папке: FastAPI, SQLite, веб-интерфейс и тесты. "
    "Файлы пиши по частям, если они большие."
)

# A realistic module: Unicode, a docstring with quotes, backslashes, tabs, CRLF-free newlines and
# enough body to pass 72 KB. `json.dumps` will escape the quotes and backslashes on the way in.
PART = (
    '"""Тестовая задача: заметки, статусы и «умный» поиск.\n\n'
    'Путь C:\\\\Temp\\\\TaskDesk — обратные слэши и кавычки \'single\' и "double".\n'
    'columns:\n\tid\tint\n\ttitle\ttext\n"""\n'
    "def handler_%04d(payload: dict) -> dict:\n"
    "    \"\"\"Возвращает значение; юникод: задача, статус, дедлайн.\"\"\"\n"
    "    value = payload.get(\"title\", \"\")\n"
    "    return {\"title\": value, \"ok\": True, \"index\": %04d}\n\n"
)

TOTAL_PARTS = 220
BIG_SOURCE = "".join(PART % (index, index) for index in range(TOTAL_PARTS))
APPEND_PARTS = ["# part one — начало\n" * 400, "def second():\n    return 'вторая часть'\n" * 200, "# конец\n"]


def _call(name, arguments, call_id):
    if isinstance(arguments, str):
        raw = arguments
    else:
        raw = json.dumps(arguments, ensure_ascii=False)
    return {
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}
        ]
    }


class ScriptedWrites:
    """Only decides. Everything on disk is produced by the product."""

    supports_tools = True

    def __init__(self, root):
        self.root = root
        self.step = 0
        # What the "model" saw at the start of each turn: the previous tool result is the last
        # message. Recorded to prove the error text reaches the planner, not just the log.
        self.seen: dict[int, str] = {}

    async def plan_tools(self, messages, tools, usage):
        self.step += 1
        if self.step > 1 and messages:
            self.seen[self.step] = str(messages[-1].get("content") or "")
        root = self.root
        sequence = {
            # 1. the whole file in one call: > 72 KB even after UTF-8 encoding
            1: ("write_file", {"path": str(root / "big.py"), "content": BIG_SOURCE, "purpose": "large module"}),
            # 2. a truncated response: the JSON simply stops, exactly as a length-limited completion does
            2: ("write_file", '{"path": "' + str(root / "broken.py") + '", "content": "def broken('),
            # 3. over the content bound: the model must be told to split, not left guessing
            3: (
                "write_file",
                {"path": str(root / "too_big.py"), "content": "x" * (MAX_WRITE_CONTENT_CHARS + 10)},
            ),
            # 4-6. the same file built from bounded parts
            4: ("write_file", {"path": str(root / "parts.py"), "content": APPEND_PARTS[0]}),
            5: ("write_file", {"path": str(root / "parts.py"), "content": APPEND_PARTS[1], "mode": "append"}),
            6: ("write_file", {"path": str(root / "parts.py"), "content": APPEND_PARTS[2], "mode": "append"}),
            7: ("read_file", {"path": str(root / "parts.py")}),
            # 8. an unknown field is refused *by name*
            8: (
                "write_file",
                {"path": str(root / "extra.py"), "content": "ok", "unexpected_field": True},
            ),
            # 9. the corrected call, with a relative path the controller resolves into the workspace
            9: ("write_file", {"path": "fixed.py", "content": "print('исправлено')\n"}),
        }
        if self.step in sequence:
            name, arguments = sequence[self.step]
            return _call(name, arguments, f"step-{self.step}")
        return {"tool_calls": []}


def test_the_model_can_write_a_real_file_and_recover_from_a_bad_call(client, auth, tmp_path):
    from tests.fake_host import FakeHost

    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [str(root)]},
    )
    paired = client.post("/tools/devices/pair", headers=headers, json={"platform": "windows"}).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id

    async def emit(event, value):
        return None

    context = ExecutionContext(
        user_id,
        chat_id,
        generation_id,
        ToolLimits(max_calls=40, hard_max_calls=60),
        emit,
        mode="off",
        computer_mode="trusted",
        settings=WebSettings(computer_mode="trusted", workspace_roots=[str(root)], default_mode="off"),
        user_prompt=PROMPT,
        host_online=True,
    )
    host = FakeHost(client, device_headers, root).start()
    planner = ScriptedWrites(root)
    try:
        orchestrator = ToolOrchestrator(make_registry(), ToolExecutor(make_registry()))
        asyncio.run(orchestrator.prepare(planner, [{"role": "user", "content": PROMPT}], 0, context, {}))
    finally:
        host.close()

    # -- 1. the large file is on disk, byte for byte, including Unicode and escapes
    assert (root / "big.py").is_file(), planner.seen.get(2)
    written = (root / "big.py").read_text(encoding="utf-8")
    assert written == BIG_SOURCE
    assert len(written.encode("utf-8")) > 72 * 1024
    assert "задача, статус, дедлайн" in written
    assert "C:\\\\Temp\\\\TaskDesk" in written
    assert "write_file" in host.handled

    # -- 2. the truncated call was refused as invalid JSON, never written
    assert not (root / "broken.py").exists()
    assert '"error": "invalid_json"' in planner.seen[3]
    assert "not valid JSON" in planner.seen[3]

    # -- 3. over the bound: a typed code plus the instruction to split
    assert not (root / "too_big.py").exists()
    assert '"error": "content_too_large"' in planner.seen[4]
    assert "Split the file into smaller writes" in planner.seen[4]

    # -- 4-6. the bounded parts compose to exactly the intended file
    assert (root / "parts.py").read_text(encoding="utf-8") == "".join(APPEND_PARTS)
    appended = [
        text for text in planner.seen.values() if '"tool": "write_file"' in text and "mode" in text
    ]
    assert appended, "the append calls reached the model as real tool results"
    assert not any("write_verification_failed" in text for text in planner.seen.values())
    # The host writes the merged file before the executor's own bookkeeping runs. A crash in that
    # bookkeeping (an undefined expected digest) still left the right bytes on disk and reported
    # `provider_unavailable`, so the file assertion alone cannot catch it.
    assert not any("provider_unavailable" in text for text in planner.seen.values()), [
        text for text in planner.seen.values() if "provider_unavailable" in text
    ]

    # -- 7. the model could read back what it wrote
    assert '"text"' in planner.seen[8]
    assert "вторая часть" in planner.seen[8]

    # -- 8. an unknown field is named, and the file is not created by a refused call
    assert not (root / "extra.py").exists()
    assert '"error": "invalid_arguments"' in planner.seen[9]
    assert "unexpected_field" in planner.seen[9]

    # -- 9. the corrected call with a relative path lands inside the workspace
    assert (root / "fixed.py").is_file(), planner.seen.get(10)
    assert (root / "fixed.py").read_text(encoding="utf-8") == "print('исправлено')\n"

    # -- the host really executed the writes (a model message alone is never evidence)
    assert host.handled.count("write_file") >= 5, host.handled
    assert host.handled.count("read_file") >= 1, host.handled
