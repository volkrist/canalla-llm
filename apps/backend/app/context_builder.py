"""Deterministic, bounded personal context. No model calls or automatic capture."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timezone

from fastapi import Depends, Query
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Chat, Memory, Message, Project, User, now
from .personal import owned, router
from .security import current_user


@dataclass
class MemoryCandidate:
    content: str
    category: str = "fact"
    importance: int = 3


class MemoryExtractor(ABC):
    @abstractmethod
    async def extract(self, user_message, assistant_message, existing_memories) -> list[MemoryCandidate]:
        """Return proposals only; acceptance must use ownership-checked CRUD."""
        raise NotImplementedError


def words(text):
    return set(re.findall(r"[^\W_]{3,}", text.casefold())) - {"как", "что", "это", "для", "the", "and", "you"}


class MemoryRetriever:
    def retrieve(self, db, user, chat, prompt, settings):
        if not user.use_memory:
            return []
        rows = db.scalars(
            select(Memory)
            .where(
                Memory.user_id == user.id,
                Memory.is_active.is_(True),
                or_(Memory.project_id.is_(None), Memory.project_id == chat.project_id),
            )
            .order_by(Memory.is_pinned.desc(), Memory.importance.desc(), Memory.updated_at.desc())
            .limit(1000)
        ).all()
        query = words(prompt)
        scored = []
        for row in rows:
            overlap = len(query & words(row.content))
            project_match = bool(chat.project_id and row.project_id == chat.project_id)
            if not row.is_pinned and (not user.relevant_memory or not (overlap or project_match)):
                continue
            age = max(0, (now() - row.updated_at.replace(tzinfo=timezone.utc)).total_seconds() / 86400)
            score = 100 * row.is_pinned + 40 * project_match + 10 * overlap + row.importance + 1 / (1 + age)
            scored.append((row, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        chosen, size = [], 0
        for row, score in scored:
            if len(chosen) >= min(user.max_memories, settings.memory_max_items):
                break
            if size + len(row.content) > settings.memory_max_chars:
                continue
            chosen.append((row, score))
            size += len(row.content)
        return chosen


class ContextBuilder:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.retriever = MemoryRetriever()

    def build(self, db, user, chat, current, track=False):
        if chat.user_id != user.id or current.chat_id != chat.id:
            raise ValueError("Context ownership mismatch")
        selected = self.retriever.retrieve(db, user, chat, current.content, self.settings)
        project = (
            db.scalar(
                select(Project).where(
                    Project.id == chat.project_id, Project.user_id == user.id, Project.status == "active"
                )
            )
            if chat.project_id
            else None
        )
        messages = [{"role": "system", "content": self.settings.global_system_prompt}]

        def context(label, content):
            if content:
                messages.append({"role": "user", "content": f"[{label}: user-provided context]\n{content}"})

        context("Profile and custom instructions", user.display_name + "\n" + user.custom_instructions)
        pinned = [(m, score) for m, score in selected if m.is_pinned]
        project_memories = [(m, score) for m, score in selected if not m.is_pinned and m.project_id]
        general = [(m, score) for m, score in selected if not m.is_pinned and not m.project_id]
        context("Pinned memories", "\n".join(m.content for m, _ in pinned))
        context(
            "Project",
            (project.name + "\n" + project.description)[: self.settings.context_project_chars]
            if project
            else "",
        )
        context("Relevant project memories", "\n".join(m.content for m, _ in project_memories))
        context("Relevant general memories", "\n".join(m.content for m, _ in general))
        rows = db.scalars(
            select(Message)
            .where(
                Message.chat_id == chat.id, Message.id != current.id, Message.created_at <= current.created_at
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(100)
        ).all()
        history, size = [], 0
        for row in rows:
            if not row.content:
                continue
            if size + len(row.content) > self.settings.context_history_chars:
                break
            history.append({"role": row.role, "content": row.content})
            size += len(row.content)
        messages.extend(reversed(history))
        messages.append({"role": "user", "content": current.content})
        ids = [m.id for m, _ in selected]
        if track and ids:
            db.execute(
                update(Memory)
                .where(Memory.user_id == user.id, Memory.id.in_(ids))
                .values(use_count=Memory.use_count + 1, last_used_at=now())
            )
        return {
            "messages": messages,
            "memory_ids": ids,
            "memories": [{"id": m.id, "content": m.content, "score": score} for m, score in selected],
            "project": project.name if project else None,
            "recent_message_count": len(history),
            "budgets": {
                "system": len(self.settings.global_system_prompt),
                "profile_max": 2081,
                "memory_max": self.settings.memory_max_chars,
                "project_max": self.settings.context_project_chars,
                "history_max": self.settings.context_history_chars,
                "current_max": 32000,
            },
            "total_chars": sum(len(m["content"]) for m in messages),
        }


@router.get("/chats/{key}/context-preview")
def preview(
    key: str,
    prompt: str = Query("", max_length=32000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    chat = owned(db, Chat, key, user.id)
    current = Message(id="preview", chat_id=key, role="user", content=prompt, created_at=now())
    return ContextBuilder().build(db, user, chat, current)
