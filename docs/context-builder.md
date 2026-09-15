# ContextBuilder (0.4.0)

Context assembly is exclusively in the backend, before LLMProvider streaming. It is shared by send, edit/resend and regenerate. The provider transport is unchanged. MockLLMProvider continues to produce its explicitly labelled local template; tests inspect the context rather than pretending that the template has memory intelligence.

Exact message order:
1. Global backend system prompt (`system`).
2. Display name and custom instructions (`user` context).
3. Selected pinned memories (`user` context).
4. Active current project name/description (`user` context).
5. Relevant current-project memories (`user` context).
6. Relevant general memories (`user` context).
7. Recent stored chat messages in chronological order.
8. Current user message exactly once.

Only the global prompt gets system authority. Personal context sections are explicitly labelled user-provided information. This separation does not claim perfect prompt-injection immunity from an arbitrary future model; personal text cannot change backend authentication, global settings or authorization.

MemoryRetriever uses case-folded Unicode words of at least three characters and a small stop-word list. Eligibility: active owner memory, general or current-project scope. Other-project memory is excluded even when pinned. A non-pinned item requires keyword overlap or a current-project match. Turning off automatic relevant memory leaves only pinned items; turning off long-term memory excludes all memories.

Explainable score: `100*pinned + 40*project_match + 10*overlapping_words + importance + 1/(1+age_days)`. Ties use stable IDs. At most 1,000 candidates are examined, ordered by pin, importance and recency. Selection respects both item and character limits; oversized items are skipped. The context groups preserve the conceptual order above, independent of score ordering.

| Budget | Default / hard limit |
|---|---:|
| Global system text | max 4,000 chars |
| Profile/instructions | name 80 + instructions 2,000 chars |
| MEMORY_MAX_ITEMS | default 12; configurable 1–20; also capped by user setting |
| MEMORY_MAX_CHARS | default 6,000; configurable max 12,000 |
| CONTEXT_PROJECT_CHARS | default 3,000; configurable max 6,000 |
| CONTEXT_HISTORY_CHARS | default 24,000; configurable max 64,000; at most 100 messages |
| Current message | max 32,000 chars |

These are character budgets, not exact tokenizer counts. Fixed section labels/separators add small bounded overhead. With defaults, content stays below about 68,000 characters including the maximum current prompt; system configuration cannot be overridden by frontend settings. History is trimmed by whole recent messages, keeping the most recent contiguous suffix. Future real-model E2E must validate actual tokenizer/context-window and response headroom; no GPU capacity claim is made here.

Selected memories increment last_used_at/use_count transactionally when a generation context is accepted. Preview has no provider call and no usage writes. `GET /chats/{id}/context-preview?prompt=...` returns selected owned memories/scores, current project, recent count, exact structured messages and character budgets; only the chat owner can use it. Technical UI exposes this preview. Query strings are redacted in backend logs; reverse proxies must do the same.

Migration 0004 adds profiles, presence_sessions/tickets, projects, memories and message_contexts plus Chat.project_id. SQLite migration uses additive columns instead of rebuilding user/chat tables, preserving existing dependent rows. Projects are archived rather than deleted. Models use portable SQLAlchemy types; PostgreSQL live deployment is not part of this local stage.
