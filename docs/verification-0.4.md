# Alex LLM 0.4.0 — итог этапа Presence / Memory / ContextBuilder

1. **Версия:** 0.4.0, поверх `236ef7ee77db839faed5a04753f76bb1a2176196`. SHA релиза указан в итоговом ответе и `git log -1`.
2. **Presence:** реальный WebSocket, durable `presence_sessions`, серверная агрегация состояния; один backend worker.
3. **WS authentication:** Bearer JWT используется только для POST выдачи random ticket. Ticket живёт 45 секунд, хранится как SHA-256, потребляется один раз атомарным delete. Origin проверяется; production требует WSS; URL query скрыт в логах.
4. **Heartbeat:** 20 секунд, configurable; activity отправляется не чаще раза в 25 секунд.
5. **Idle:** 300 секунд без активности, heartbeat продолжается.
6. **Offline:** 75 секунд без свежего heartbeat; краткий disconnect имеет grace period. Frontend при потере связи показывает Presence unavailable.
7. **Несколько устройств:** пользователь остаётся online/idle, пока есть хотя бы одна свежая сессия. Startup сбрасывает старые соединения и tickets, сохраняя last-seen history.
8. **Using AI:** count незавершённых GenerationUsage, включая mock. Существующий try/finally закрывает generation на complete/Stop/error; параллельные чаты считаются независимо. Frontend не задаёт Using AI.
9. **Privacy:** другие пользователи и администратор через Presence получают только opaque presence key, display_name, connection status, using_ai, last_seen. Нет email, внутренних user/chat IDs, prompts, сообщений, настроек или памяти. Hide-presence toggle в MVP не добавлен.
10. **Профиль:** display_name, updated_at, custom_instructions, use_memory, relevant_memory, max_memories. Старые email/created_at сохранены. Собственный профиль показывает UTC-даты в локальном часовом поясе.
11. **Memory schema:** owner, content/category/importance, source chat/message, optional project, pin/active, timestamps, last_used_at/use_count. См. [схему памяти](memory.md).
12. **Категории:** identity, preference, project, decision, fact, instruction, other.
13. **CRUD:** просмотр, создание, изменение, удаление с UI-подтверждением, pin/unpin, disable/enable, поиск, категория, пагинация. Backend ограничивает content и importance.
14. **Save to Memory:** действие «Запомнить» открывает редактор; сохранение выполняется только после проверки и нажатия «Сохранить память». Источник проверяется на принадлежность пользователю.
15. **Projects:** owner, name, description, active/archived, created_at/updated_at. Создание, изменение, archive/restore; hard delete не добавлен.
16. **Chat → project:** компактный picker, nullable project_id. Backend проверяет владельца и active status; история при архивировании не удаляется.
17. **MemoryRetriever:** deterministic, без embeddings и вызова LLM. Только активная память владельца из общего/current-project scope; другие проекты исключены даже для pinned.
18. **Relevance:** pinned 100 + project match 40 + overlap words ×10 + importance 1–5 + recency 0–1; stable ID для равенства. Неприкреплённая общая память без пересечения слов исключается. Кандидатов максимум 1,000.
19. **ContextBuilder:** отдельный backend-сервис, общий путь send/resend/regenerate перед LLMProvider. Использование памяти фиксируется транзакционно; preview ничего не генерирует и не увеличивает use_count.
20. **Порядок:** system → profile/custom instructions → pinned memory → project → relevant project memory → relevant general memory → recent history → current message ровно один раз. Личные данные передаются как user-context, не как system authority.
21. **Бюджеты:** default 12 memories / 6,000 chars; history 24,000 chars и максимум 100 сообщений; project 3,000 chars; instructions 2,000; current prompt 32,000. Небольшой фиксированный overhead заголовков учитывается отдельно. Это char limits, не точный tokenizer. [Подробности](context-builder.md).
22. **Изоляция:** JWT определяет владельца, клиентский user_id запрещён; проверяются source chat/message/project. Preview и used-memory API доступны только владельцу чата. Админ не получает обход ownership.
23. **Миграция:** 0004_presence_memory_projects. Проверены clean install, upgrade с заполненными users/chats/messages и foreign_key_check. На рабочей SQLite до/после сохранены все 5 прежних пользователей; старых чатов/сообщений было 0. До upgrade создана резервная копия. Alembic check — PASS.
24. **API:** GET/PATCH /profile; GET/POST /projects; PATCH /projects/{id}; PATCH /chats/{id} (project_id); GET/POST /memory; PATCH/DELETE /memory/{id}; GET /messages/{id}/memory; GET /chats/{id}/context-preview; GET /presence; POST /presence/ws-ticket; WS /ws/presence.
25. **Backend:** 84 pytest-сценария. Новые проверки: 22 для памяти, ownership, sources, budgets, actual mock-provider context, tickets/TTL/reuse, WebSocket, heartbeat, multiple devices, idle/offline/restart, Using AI и cleanup. Все проходят. Два предупреждения upstream Starlette/AnyIO остаются.
26. **Frontend:** 13 unit tests — PASS, включая относительное время last seen.
27. **E2E:** 7 Playwright scenarios — PASS. Старые chat/compute fixtures сохранены; новые проверяют личные панели, source approval, project picker, context preview, реальный localhost WebSocket и отдельно управляемые UI fixtures idle/offline/reconnect/logout.
28. **Статические проверки:** Ruff check/format, TypeScript, Vite production build, Prettier — PASS. Vite сообщает прежнее предупреждение о размере JS bundle >500 kB; сборку оно не блокирует.
29. **npm audit:** 0 уязвимостей.
30. **Windows:** cargo check, optimized Tauri release и NSIS installer — PASS. Native WebView2 smoke подтверждает HTTPS origin, реальный localhost Presence Online, streaming mock, copy, Stop, сохранение/удаление истории и logout. Подпись и интерактивный install/uninstall не проверялись.
31. **Приложение:** `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\Alex LLM.exe`. Существующий ярлык на рабочем столе обновлён на 0.4.0.
32. **Installer:** `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\Alex LLM Setup.exe`.
33. **Скриншоты:** [Users / Online](screenshots/0.4/users-online.png), [Idle fixture](screenshots/0.4/users-idle-test-fixture.png), [Offline fixture](screenshots/0.4/users-offline-test-fixture.png), [Using AI](screenshots/0.4/using-ai.png), [Profile](screenshots/0.4/profile.png), [Memory list](screenshots/0.4/memory-list.png), [Add](screenshots/0.4/add-memory.png), [Edit](screenshots/0.4/edit-memory.png), [Projects](screenshots/0.4/projects.png), [Chat picker](screenshots/0.4/chat-project-picker.png), [Context preview](screenshots/0.4/context-preview.png), [Native Presence](screenshots/0.4/native-presence.png). Idle/Offline screenshots явно обозначены как test fixtures; Online/Using AI и Native использовали реальный локальный backend.
34. **RunPod:** финальная read-only проверка выполняется после push; её live-результат указан в итоговом ответе. Приложение оставлено в mock, backend process запущен с пустым RunPod key override и без compute background loop.
35. **Платные действия этапа:** 0. Не создавались/не запускались Pods, не менялись Volume/модель, не включался автопоиск, не вызывался реальный llama.cpp. Секреты, БД и runtime caches не входят в commit.
36. **Следующий этап:** отдельно разрешённый real OrcaRouter paid E2E и проверка памяти на модели; далее RAG/files и другие системы по отдельному запросу. Automatic capture и Redis fanout не реализованы: есть proposal-only интерфейс MemoryExtractor/MemoryCandidate, fake extractor только в tests. Никакой следующий этап не начат.
