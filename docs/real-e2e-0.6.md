# Alex LLM 0.6.0 — real E2E, 2026-09-16

Результат: реальная интеграция OrcaRouter + Search/Fetch подтверждена частично. Полный набор E2E НЕ завершён. После указания пользователя не отправлять запросы агент новых запросов не отправлял; дальнейшие генерации выполнял пользователь. После сообщения об окончании проверки нажат Stop AI в Alex LLM.exe.

## RunPod

| № | Проверка | Результат |
|---|---|---|
| 1 | GPU | NVIDIA L40S, Secure Cloud, US-TX-3 |
| 2 | VRAM | 48 GB / 49152 MiB |
| 3 | Цена | $1.09/час |
| 4 | Pod | v7uwrj5jjqeir2; одна managed create; после Stop удалён контроллером |
| 5 | До Ready | 58.970 с от started_at; 60.342 с от создания записи сессии |
| 6 | Compute duration | 21 мин 46.979 с; учтено 1306 секунд |
| 7 | Estimated cost | $0.395428; actual supplier cost не предоставлен |
| 8 | Финальный GPU count | 0; остался ry246k5siqujgu / orcarouter-l40s / EXITED |

Сессия `9be38f26-3534-4dd4-8b3d-3c7529adb050`: 01:55:47.519000–02:17:34.497970 UTC, stopped/manual. Исходный бюджет $0.82, idle-stop 10 минут. Volume `uwgeaie5b0`, 50 GB STANDARD, US-TX-3 сохранён. Модель не скачивалась, Volume не изменялся. Compute сейчас $0/час; прежняя оценка хранения ~$3.50/месяц не является свежим billing statement.

Runtime прошёл mounting_storage → starting_llm → loading_model → ready. Проверки файлов выполнялись существующим runtime; отдельная независимая проверка хешей бинарника/скриптов не выполнялась. Публичный порт только 9000/http; raw 8080 не публиковался. Gateway /v1/models: без авторизации 401, с авторизацией 200 и точный alias.

## OrcaRouter и инструменты

| № | Проверка | Результат |
|---|---|---|
| 9 | Exact alias | orcarouter-qwen38-27b-q5km |
| 10 | Mock=false | PASS: все 7 usage records имеют provider=llamacpp |
| 11 | Baseline | Запрос FastAPI/Django остановлен до завершения; 6 других реальных ответов завершены |
| 12 | Streaming | Реальный SSE pipeline использован; независимое измерение постепенного отображения в UI не записано |
| 13 | TTFT | 13.469–93.077 с для завершённых запросов; включает planner/tool latency |
| 14 | Usage | 7 запросов: 6 completed, 1 stopped. Input 63892, output 5872, total 69764 для completed; stopped usage неизвестен |
| 15 | supports_tools | Реальные model-driven Search/Fetch выполнены через существующий orchestrator |
| 16 | Tool-call evidence | Python response 6dee3998-cd68-465f-8186-1969ed640d86: 2 planner calls, Search/Fetch, 6 source snapshots. Отдельный сырой первый tool_call не сохранён |
| 17 | JSON validity | Аргументы прошли существующую schema validation и были исполнены; raw payload отдельно не архивирован |
| 18 | Tool loop | PASS для выполненных Search/Fetch → final answer |
| 19 | Search | PASS, реальный TinyFish |
| 20 | Fetch | PASS, реальный TinyFish |
| 21 | Search count | 4 completed |
| 22 | Fetch count | 4 completed |
| 23 | Agent count | 0 |
| 24 | Browser count | 0 |
| 25 | Paid TinyFish actions | 0 Agent/Browser действий. Search/Fetch free capability подтверждена; actual cost в audit null, wallet ledger не проверялся |

Реальный ответ модели (цитата, не независимая проверка актуальности версии):

> Актуальная стабильная версия Python — **3.14.7** (выпуск от 5 августа 2026) по данным официального python.org [W4][W6].

Этот ответ: input 8902, output 831, total 9733; TTFT 38.677 с, duration ~40.05 с. W6: https://www.python.org/downloads/, title Download Python, fetched_at 2026-09-16T02:03:44.751978; excerpt присутствует.

Бесплатность Search/Fetch: [Search billing](https://docs.tinyfish.ai/search-api/reference#billing), [Fetch billing](https://docs.tinyfish.ai/fetch-api/reference#billing).

## Web, context, Stop, security

| № | Проверка | Результат |
|---|---|---|
| 26 | Auto mode | Приветствие без tools наблюдалось. Парный тест обычного/свежего вопроса НЕ выполнен |
| 27 | Off mode | Локальный regression PASS; real E2E НЕ выполнен |
| 28 | Source snapshots | 24 сохранённых snapshots; повторное чтение не увеличило ToolRun count |
| 29 | W citations | W4/W6 Python-ответа соответствуют snapshots; поведение W99 покрывается локальной проверкой, не этим real E2E |
| 30 | URLs | У проверенного Python-ответа ссылки из сохранённых источников; все ответы на выдуманные URL не проверялись |
| 31 | Memory + Web | В generation snapshot выбрана 1 preference memory (59 символов), русский краткий ответ с W citations |
| 32 | Project + Web | НЕ проверено: project=null в реальных snapshots |
| 33 | RAG + Web | НЕ проверено: document_count=0 |
| 34 | Combined ContextBuilder | Project + Memory + RAG + Web в одном real запросе НЕ проверен |
| 35 | Budgets | Для наблюдавшихся Memory/history/Web snapshots соблюдены; combined scenario не проверен |
| 36 | Stop during tool loop | PARTIAL: Stop во время planner. Client closed 02:00:01.527206, provider closed 02:00:01.548409 UTC. Активный Search/Fetch ещё не начался; его реальная отмена НЕ проверена |
| 37 | ToolRun cleanup | Завершённых Search/Fetch 8, зависших в наблюдавшейся сессии нет; active generations=0 |
| 38 | Presence cleanup | После Stop active generations=0; визуальный Using AI во время работы отдельно не зафиксирован |
| 39 | Stop AI | PASS: кнопка в реальном EXE, session stopped, stopped_at и estimated cost сохранены, RunPod GPU=0 |
| 40 | Agent blocked | Disabled, 0 вызовов; локальные policy tests PASS. Реальный запрещённый model tool_call не наблюдался |
| 41 | Browser blocked | Disabled, 0 вызовов; локальные policy tests PASS. Реальный запрещённый model tool_call не наблюдался |
| 42 | Cross-user isolation | PASS: source endpoint owner=200, другой authenticated user=404 |
| 43 | Prompt injection policy | Локальный regression PASS; отдельный malicious web real E2E не выполнялся |

Web On не гарантировал вызов tool в каждом пользовательском запросе: некоторые ответы имели 0 источников. Один planner достиг лимита 1200 токенов. Это ограничение наблюдавшегося поведения, а не подтверждение полноценного Auto routing. Отдельный real UI сценарий web provider failure не проведён; локальные missing-key/failure/security проверки покрывают часть поведения.

## Fix, Git и оставшаяся работа

| № | Проверка | Результат |
|---|---|---|
| 44 | Изменённые файлы | app/chat_stream.py, tests/test_web_integration.py, этот отчёт |
| 45 | Tests | 62 relevant backend tests PASS: tools_core, web_integration, gateway, personal_presence. Ruff check и format check двух Python файлов PASS. Frontend не менялся; frontend/typecheck/production build в этом финальном проходе повторно не запускались |
| 46 | Commit | Исправление и отчёт входят в commit; точный SHA указан в итоговом сообщении и Git history |
| 47 | Push | Итог проверяется после commit и приводится в итоговом сообщении |
| 48 | Git clean | Итог проверяется после push и приводится в итоговом сообщении |
| 49 | Ограничения | Непроверенные сценарии перечислены выше; полный E2E не объявляется PASS |
| 50 | До paid Agent test | Завершить Auto/Off, Project/RAG/combined context, UI streaming/Presence, Stop активного tool, UI failure и citation edge cases; затем отдельное разрешение на paid Agent с исполнимыми ограничениями и бюджетом. Сейчас Agent/Browser не запускать |

Исправлена ошибка сопоставления prompt_tokens/completion_tokens планировщика с input_tokens/output_tokens GenerationUsage. Раньше planner добавлялся в total, но не в input/output. Regression проверяет суммы 130/30/160. Шесть завершённых usage rows этой сессии исправлены только по сохранённому reported planner usage; total не менялся, неизвестные stopped tokens остались null. Перед исправлением создана локальная SQLite backup вне Git. Backend перезапущен после остановки GPU; .env и frontend не изменялись.

Локальные evidence-файлы находятся вне репозитория в workspace/work: real06-final-evidence.json, real06-source-evidence.json, real06-runtime-phases.json, real06-usage-corrections.json. Они содержат данные теста и не публикуются вместе с кодом.
