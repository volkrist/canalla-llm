"""Single-worker WebSocket presence with durable session aggregation and one-use tickets."""

import asyncio
import hashlib
import secrets
from contextlib import suppress
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import case, delete, func, or_, select, update

from .compute.models import GenerationUsage
from .config import get_settings
from .database import SessionLocal
from .models import PresenceSession, PresenceTicket, User, now
from .security import current_user

router = APIRouter(tags=["presence"])


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value else None


class PresenceManager:
    def __init__(self, settings=None, clock=now):
        self.settings = settings or get_settings()
        self.clock = clock
        self.clients = {}
        self.previous = {}

    def reconcile(self):
        with SessionLocal() as db:
            db.execute(
                update(PresenceSession).values(
                    disconnected_at=self.clock() - timedelta(seconds=self.settings.presence_offline_seconds)
                )
            )
            db.execute(delete(PresenceTicket))
            db.commit()

    def ticket(self, user_id):
        token = secrets.token_urlsafe(32)
        with SessionLocal() as db:
            db.execute(delete(PresenceTicket).where(PresenceTicket.expires_at <= self.clock()))
            if (
                db.scalar(
                    select(func.count()).select_from(PresenceTicket).where(PresenceTicket.user_id == user_id)
                )
                >= 5
            ):
                raise HTTPException(429, "Слишком много запросов подключения")
            db.add(
                PresenceTicket(
                    digest=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=user_id,
                    expires_at=self.clock() + timedelta(seconds=self.settings.presence_ticket_seconds),
                )
            )
            db.commit()
        return token

    def consume(self, token):
        if not token or len(token) > 128:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with SessionLocal() as db:
            row = db.get(PresenceTicket, digest)
            if not row or utc(row.expires_at) <= self.clock():
                return None
            user_id = row.user_id
            result = db.execute(
                delete(PresenceTicket)
                .where(PresenceTicket.digest == digest, PresenceTicket.expires_at > self.clock())
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return user_id if result.rowcount == 1 else None

    def connect(self, user_id):
        with SessionLocal() as db:
            row = PresenceSession(
                user_id=user_id,
                connected_at=self.clock(),
                last_heartbeat_at=self.clock(),
                last_activity_at=self.clock(),
            )
            db.add(row)
            db.commit()
            return row.id

    def touch(self, sid, activity=False):
        with SessionLocal() as db:
            row = db.get(PresenceSession, sid)
            if row and row.disconnected_at is None:
                if (self.clock() - utc(row.last_heartbeat_at)).total_seconds() >= 5:
                    row.last_heartbeat_at = self.clock()
                if activity and (self.clock() - utc(row.last_activity_at)).total_seconds() >= 20:
                    row.last_activity_at = self.clock()
                db.commit()

    def disconnect(self, sid):
        with SessionLocal() as db:
            row = db.get(PresenceSession, sid)
            if row:
                row.disconnected_at = self.clock()
                db.commit()

    def snapshot(self):
        at = self.clock()
        with SessionLocal() as db:
            users = db.scalars(select(User).order_by(User.created_at, User.id)).all()
            cutoff = at - timedelta(seconds=self.settings.presence_offline_seconds)
            live = or_(PresenceSession.disconnected_at.is_(None), PresenceSession.disconnected_at > cutoff)
            aggregates = db.execute(
                select(
                    PresenceSession.user_id,
                    func.max(PresenceSession.last_heartbeat_at),
                    func.max(PresenceSession.last_activity_at),
                    func.max(case((live, PresenceSession.last_heartbeat_at), else_=None)),
                ).group_by(PresenceSession.user_id)
            ).all()
            sessions = {
                uid: (utc(seen), utc(activity), utc(alive)) for uid, seen, activity, alive in aggregates
            }
            counts = dict(
                db.execute(
                    select(GenerationUsage.user_id, func.count())
                    .where(GenerationUsage.completed_at.is_(None))
                    .group_by(GenerationUsage.user_id)
                ).all()
            )
            return [
                {
                    "key": hashlib.sha256(("presence:" + user.id).encode()).hexdigest()[:24],
                    "display_name": user.display_name,
                    "status": "offline"
                    if user.id not in sessions
                    or not sessions[user.id][2]
                    or (at - sessions[user.id][2]).total_seconds() >= self.settings.presence_offline_seconds
                    else "idle"
                    if (at - sessions[user.id][1]).total_seconds() >= self.settings.presence_idle_seconds
                    else "online",
                    "using_ai": counts.get(user.id, 0) > 0,
                    "last_seen": sessions[user.id][0].isoformat() if user.id in sessions else None,
                }
                for user in users
            ]

    async def publish(self):
        rows = self.snapshot()
        for row in rows:
            before = self.previous.get(row["key"])
            # Heartbeat timestamps alone do not fan out updates every heartbeat.
            if before and all(before[k] == row[k] for k in ("status", "using_ai", "display_name")):
                continue
            kind = "user_" + row["status"]
            if before and before["using_ai"] != row["using_ai"]:
                kind = "user_ai_started" if row["using_ai"] else "user_ai_stopped"
            elif before and before["status"] == "idle" and row["status"] == "online":
                kind = "user_active"
            for ws in list(self.clients.values()):
                with suppress(Exception):
                    await asyncio.wait_for(ws.send_json({"type": kind, "user": row}), 2)
        self.previous = {row["key"]: row for row in rows}

    async def monitor(self):
        while True:
            await asyncio.sleep(2)
            await self.publish()


@router.post("/presence/ws-ticket")
def ticket(request: Request, user: User = Depends(current_user)):
    manager = request.app.state.presence
    return {
        "ticket": manager.ticket(user.id),
        "expires_in": manager.settings.presence_ticket_seconds,
        "heartbeat_seconds": manager.settings.presence_heartbeat_seconds,
    }


@router.get("/presence")
def presence(
    request: Request,
    user: User = Depends(current_user),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return request.app.state.presence.snapshot()[offset : offset + limit]


@router.websocket("/ws/presence")
async def websocket(ws: WebSocket):
    manager = ws.app.state.presence
    origin = ws.headers.get("origin")
    if origin and origin not in manager.settings.cors_origins:
        await ws.close(code=4403)
        return
    if manager.settings.app_env == "production" and ws.url.scheme != "wss":
        await ws.close(code=4403)
        return
    user_id = manager.consume(ws.query_params.get("ticket"))
    if not user_id:
        await ws.close(code=4401)
        return
    await ws.accept()
    sid = manager.connect(user_id)
    manager.clients[sid] = ws
    try:
        await ws.send_json(
            {
                "type": "snapshot",
                "users": manager.snapshot(),
                "self_key": hashlib.sha256(("presence:" + user_id).encode()).hexdigest()[:24],
            }
        )
        await manager.publish()
        while True:
            raw = await asyncio.wait_for(ws.receive_text(), manager.settings.presence_offline_seconds)
            if len(raw) > 256:
                await ws.close(code=4400)
                break
            import json

            try:
                data = json.loads(raw)
            except ValueError:
                await ws.close(code=4400)
                break
            if (
                not isinstance(data, dict)
                or data.get("type") not in {"heartbeat", "activity"}
                or set(data) != {"type"}
            ):
                await ws.close(code=4400)
                break
            manager.touch(sid, data["type"] == "activity")
            await ws.send_json({"type": "ack"})
            await manager.publish()
    except (WebSocketDisconnect, TimeoutError):
        pass
    finally:
        manager.clients.pop(sid, None)
        manager.disconnect(sid)
        await manager.publish()
