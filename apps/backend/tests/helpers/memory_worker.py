"""A separate process for the memory-across-restart matrix.

The parent test never shares Python state with this worker: one process writes a project and its
memory, exits, and a second process builds the model context from the same SQLite file. That is
the durable half of scenario G — if retrieval only worked because an object was still in memory,
the second process could not see it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import select  # noqa: E402

from app.context_builder import ContextBuilder  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Chat, Memory, Message, Project, User  # noqa: E402


def write() -> dict:
    with SessionLocal() as db:
        user = User(email="memory@example.com", password_hash="x", display_name="Owner")
        other = User(email="other@example.com", password_hash="x", display_name="Other")
        db.add_all([user, other])
        db.flush()

        alpha = Project(user_id=user.id, name="Alpha Desk", description="FastAPI + SQLite task desk")
        beta = Project(user_id=user.id, name="Beta Kit", description="an unrelated project")
        db.add_all([alpha, beta])
        db.flush()

        chat = Chat(user_id=user.id, project_id=alpha.id, title="Alpha work")
        db.add(chat)
        db.flush()
        db.add_all(
            [
                Message(chat_id=chat.id, role="user", content="Build the desk"),
                Message(chat_id=chat.id, role="assistant", content="Scaffolded the package layout"),
            ]
        )
        current = Message(chat_id=chat.id, role="user", content="(draft)")
        db.add(current)
        db.add_all(
            [
                Memory(
                    user_id=user.id,
                    project_id=alpha.id,
                    category="decision",
                    content="Alpha uses FastAPI and SQLite with a /tasks CRUD API.",
                    importance=5,
                ),
                Memory(
                    user_id=user.id,
                    project_id=alpha.id,
                    category="task",
                    content="Alpha tests run with pytest from the project root.",
                    importance=4,
                ),
                Memory(
                    user_id=user.id,
                    project_id=None,
                    category="preference",
                    content="The owner prefers Russian explanations and short answers.",
                    importance=4,
                    is_pinned=True,
                ),
                Memory(
                    user_id=user.id,
                    project_id=beta.id,
                    category="decision",
                    content="Beta uses Django and PostgreSQL.",
                    importance=5,
                ),
                Memory(
                    user_id=other.id,
                    project_id=None,
                    category="decision",
                    content="Another account keeps a private secret phrase.",
                    importance=5,
                ),
            ]
        )
        db.commit()
        return {
            "user": user.id,
            "other": other.id,
            "alpha": alpha.id,
            "beta": beta.id,
            "chat": chat.id,
            "current": current.id,
        }


def read(ids: dict, prompt: str) -> dict:
    with SessionLocal() as db:
        user = db.get(User, ids["user"])
        chat = db.get(Chat, ids["chat"])
        current = db.get(Message, ids["current"])
        current.content = prompt
        db.flush()
        built = ContextBuilder().build(db, user, chat, current, track=True)
        db.commit()
        return {
            "memory_ids": built["memory_ids"],
            "memories": built["memories"],
            "memory_count": built["memory_count"],
            "history_chars": built["history_chars"],
            "messages": built["messages"],
        }


def forget(ids: dict, memory_id: str) -> dict:
    with SessionLocal() as db:
        row = db.get(Memory, memory_id)
        if row is None:
            return {"deleted": False}
        if row.user_id != ids["user"]:
            return {"deleted": False, "reason": "owner_mismatch"}
        db.delete(row)
        db.commit()
        return {"deleted": True}


def memory_of(ids: dict, needle: str) -> dict:
    with SessionLocal() as db:
        row = db.scalar(
            select(Memory).where(Memory.user_id == ids["user"], Memory.content.icontains(needle))
        )
        return {"id": row.id if row else "", "use_count": row.use_count if row else 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["write", "read", "forget", "usage"])
    parser.add_argument("--ids", default="{}")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--memory-id", default="")
    parser.add_argument("--needle", default="")
    args = parser.parse_args()
    ids = json.loads(args.ids or "{}")
    if args.phase == "write":
        result = write()
    elif args.phase == "read":
        result = read(ids, args.prompt)
    elif args.phase == "forget":
        result = forget(ids, args.memory_id)
    else:
        result = memory_of(ids, args.needle)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
