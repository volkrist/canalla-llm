# Long-term memory and projects (0.4.0)

Chat History stores the conversation. Long-Term Memory stores user-curated facts used across chats. RAG/Documents would retrieve document chunks; it is not part of this release.

`memories`: id, owner user_id, category, content, importance, source_chat_id/source_message_id (nullable), project_id (nullable), is_pinned, is_active, created_at, updated_at, last_used_at, use_count. Ownership/project/category/active/pinned indexes support bounded retrieval. Content is limited to 3,000 characters, importance to 1–5. Categories: identity, preference, project, decision, fact, instruction, other.

`GET/POST /memory`, `PATCH/DELETE /memory/{id}` provide search (`q`), category filter, pagination, creation, editing, pin/unpin, disable/enable and permanent deletion. The owner always comes from JWT. All source chats, source messages and projects are validated against that owner; a message source automatically records its owned chat. Extra client-supplied fields such as user_id are rejected. Admin role does not bypass personal ownership.

The message action **Запомнить** opens an editable approval form with category, importance and optional project; nothing is saved until **Сохранить память**. Long messages are prefilled to the 3,000-character memory limit for review. **Использованная память** loads only the current user's surviving memory records linked to that assistant response through `message_contexts`. Deleted records are not retained as copied text in context audit rows.

`projects`: id, user_id, name, description, status (active/archived), created_at, updated_at. `GET/POST /projects`, `PATCH /projects/{id}` support management and archive/restore. `PATCH /chats/{id}` accepts a nullable project_id with ownership validation. Chats retain their history when a project is archived; new assignment to an archived project is rejected. No project hard-delete API is provided.

`GET/PATCH /profile` manages display_name (1–80 chars), custom_instructions (2,000 chars), use_memory (default on), relevant_memory (default on), max_memories (default 12; capped by server). Own email/created_at/updated_at are visible in Profile. Dates are returned in UTC with an explicit offset. General user lists do not expose these private profile settings.

Automatic memory capture is **off and unavailable**. `MemoryExtractor` and `MemoryCandidate` define a proposal-only contract for a future implementation; the only fake extractor is in tests. There is no regex-based automatic capture, no real-LLM extraction, no embeddings and no vector database. Future acceptance must go through ownership-validated CRUD; no production capture loop is installed.
