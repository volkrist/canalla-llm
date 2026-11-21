"""Scenario G, honestly: project memory survives a backend restart.

Three independent processes share nothing but the SQLite file:

1. `write` creates a project, a chat history and four memories (two project, one general, one for a
   different project) plus a memory owned by another account, then exits;
2. `read` starts fresh, types a short new prompt and builds the model context — it must contain the
   project decisions without the user repeating them, must not contain the other project's or the
   other account's memory;
3. `forget` deletes one memory and a final `read` must no longer see it.

The point is that no Python object survives between the phases, so a passing assertion cannot be an
in-memory cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app.context_builder import _supersedes, words
from app.database import SessionLocal
from app.models import Memory, User

BACKEND = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).resolve().parent / "helpers" / "memory_worker.py"
PROMPT = "Добавь поле priority в задачи, продолжай проект"


def _run(*args: str) -> dict:
    command = [sys.executable, str(WORKER), *args]
    finished = subprocess.run(
        command, cwd=str(BACKEND), capture_output=True, text=True, timeout=180, env=dict(os.environ)
    )
    assert finished.returncode == 0, (finished.returncode, finished.stdout[-2000:], finished.stderr[-2000:])
    return json.loads(finished.stdout)


def _contents(result: dict) -> str:
    return "\n".join(item["content"] for item in result["memories"])


def test_project_memory_survives_a_restart_and_is_scoped():
    ids = _run("--phase", "write")
    payload = json.dumps(ids, ensure_ascii=False)

    after_restart = _run("--phase", "read", "--ids", payload, "--prompt", PROMPT)
    text = _contents(after_restart)

    assert "FastAPI and SQLite" in text, text
    assert "pytest from the project root" in text, text
    assert "prefers Russian explanations" in text, text
    assert "Django and PostgreSQL" not in text, "another project's memory leaked into the context"
    assert "private secret phrase" not in text, "another account's memory leaked into the context"

    assembled = "\n".join(message["content"] for message in after_restart["messages"])
    assert "FastAPI and SQLite" in assembled, "the project memory never reached the model messages"
    assert PROMPT in assembled
    assert "Build the desk" in assembled, "the conversation history did not survive the restart"

    # Reading with a real prompt marks the memories as used — and that counter is durable too.
    usage = _run("--phase", "usage", "--ids", payload, "--needle", "FastAPI and SQLite")
    assert usage["use_count"] >= 1, usage

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "memory@example.com"))
        assert user is not None
        rows = db.scalars(select(Memory).where(Memory.user_id == user.id)).all()
        assert len(rows) == 4
        assert sum(row.use_count for row in rows) >= 1


def test_a_deleted_memory_is_gone_after_the_next_restart():
    ids = _run("--phase", "write")
    payload = json.dumps(ids, ensure_ascii=False)
    decision = _run("--phase", "usage", "--ids", payload, "--needle", "FastAPI and SQLite")
    assert decision["id"]

    forgotten = _run("--phase", "forget", "--ids", payload, "--memory-id", decision["id"])
    assert forgotten["deleted"] is True

    after_restart = _run("--phase", "read", "--ids", payload, "--prompt", PROMPT)
    text = _contents(after_restart)
    assert "FastAPI and SQLite" not in text
    # The other project decision is still there: deletion removed one row, not the project.
    assert "pytest from the project root" in text


def test_memory_is_off_for_a_user_who_disabled_it():
    ids = _run("--phase", "write")
    payload = json.dumps(ids, ensure_ascii=False)
    with SessionLocal() as db:
        user = db.get(User, ids["user"])
        user.use_memory = False
        db.commit()

    after_restart = _run("--phase", "read", "--ids", payload, "--prompt", PROMPT)
    assert after_restart["memory_count"] == 0


def test_a_restated_fact_supersedes_but_two_facts_of_one_project_do_not():
    # A correction shares most of the sentence.
    assert _supersedes(words("User lives in Kazan"), words("User lives in Moscow"))
    # Two different decisions of the same project share only the name and a preposition.
    assert not _supersedes(
        words("Alpha uses FastAPI and SQLite with a tasks CRUD API"),
        words("Alpha tests run with pytest from the project root"),
    )
    # Never drop on a single shared word.
    assert not _supersedes(words("deploy on Friday"), words("deploy on Monday"))
