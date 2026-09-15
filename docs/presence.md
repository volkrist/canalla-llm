# Presence (0.4.0)

Authenticated users see display names, connection state (`online`, `idle`, `offline`), `using_ai` and UTC last-seen timestamps. The public `key` is an opaque presence-only digest, not the internal user ID. No email, prompt, chat ID/title, personal settings or memory content is included; the same projection applies to admins. A hide-presence toggle is not implemented in this MVP.

`POST /presence/ws-ticket` requires the existing Bearer JWT. It issues a random ticket (45-second TTL); only its SHA-256 digest is stored in `presence_tickets`. A conditional database delete consumes it exactly once. Outstanding tickets are limited to five per user; expired tickets are removed. The ticket grants only a presence connection, never access to other APIs. Authentication failures use the existing login flow. Browser reconnect obtains a new ticket and uses exponential backoff with jitter, capped at about 30 seconds. Logout closes the socket and cancels reconnect.

`/ws/presence?ticket=...` checks Origin and accepts localhost WS in development; production requires WSS. Uvicorn access/error log filters redact query strings, including tickets and context-preview queries. Configure any reverse proxy to omit query strings as well. Never enable request-body logging for personal APIs.

Defaults (backend configurable):

| Setting | Default |
|---|---:|
| PRESENCE_HEARTBEAT_SECONDS | 20 seconds |
| PRESENCE_TICKET_SECONDS | 45 seconds |
| PRESENCE_IDLE_SECONDS | 300 seconds |
| PRESENCE_OFFLINE_SECONDS | 75 seconds |

Frontend activity events (keyboard, pointer press, wheel) are throttled to once per 25 seconds. Heartbeats continue during idle. The server keeps `presence_sessions` with user, connection time, heartbeat, activity and disconnect timestamps. DB heartbeat writes are throttled; idle/offline is computed from UTC timestamps. All sessions are aggregated so closing one device cannot mark another connected device offline. A short disconnect retains the last heartbeat grace period. Startup invalidates all old connection leases and tickets without replacing historical last-seen timestamps.

`using_ai` is derived from the count of unfinished `generation_usage` rows, including MockLLMProvider. It is not accepted from the client. Existing generation `try/finally` cleanup decrements the effective count on completion, Stop and error; parallel chats remain Using AI until all finish. This avoids a second, drift-prone counter.

WebSocket sends an initial snapshot, then changed-user events: `user_online`, `user_offline`, `user_idle`, `user_active`, `user_ai_started`, `user_ai_stopped`. The two-second monitor reads aggregates but does not rewrite heartbeat state each second or broadcast full snapshots each second. `GET /presence?offset=&limit=` is paginated. The UI marks Presence unavailable after transport failure instead of displaying stale Online status.

This is a **single backend worker** implementation. Tickets and sessions are durable; socket ownership/fanout live in that worker. Horizontal scaling requires shared pub/sub (for example Redis), process-aware leases and cross-worker invalidation. Redis was not added. Use `--workers 1`.
