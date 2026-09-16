# Alex LLM — отчёты проверки

Текущий локальный этап: **0.7.0 WebRouter / Tor / Local Computer**. Исторические отчёты ниже относятся к указанным версиям. Платный OrcaRouter / TinyFish Agent / Browser E2E не выполнялся. GPU и RunPod не запускались.

## Проверка 0.7.0 — 16 сентября 2026

- Backend pytest: **202 passed, 1 skipped** (opt-in live TinyFish). Включены WebRouter, `origin=server_policy`, Tor SOCKS5h ATYP=0x03 без local DNS, device pairing, JWT-only host-result 401, Trusted vs process confirmation, forbidden tools, sanitized env, native Job Object parent+child Stop.
- Frontend: **19 unit passed**, TypeScript, Prettier и Vite — PASS.
- Playwright: **15 passed**. Force-web только в Auto; Tor Search provider not configured; Computer default Ask; planner не вызывает Agent; Browser Advanced по-прежнему с per-action confirmation (fakes, 0 paid calls).
- Ruff lint/format, Alembic 0008 check (temp DB), cargo check, npm audit (0), pip-audit (0) — PASS.
- Tauri optimized release + NSIS x64 — PASS. ProductVersion 0.7.0. Рядом лежат `Alex LLM_0.7.0.exe` и `Alex LLM_0.7.0_x64-setup.exe`; прежний `outputs/Alex LLM.exe` был занят запущенным процессом и не перезаписывался.
- Локальные smoke: LLM_PROVIDER=mock; TinyFish key absent (Search/Fetch live не запускался); Tor SOCKS closed (tor_fetch live не запускался); disposable workspace read/write; outside-root denied; secret `.env` denied; `python-ok`; `powershell-ok`; Job Object native test PASS. Destructive system actions не выполнялись.
- Paid TinyFish calls = 0. RunPod paid actions = 0. `COMPUTE_BACKGROUND_ENABLED=false` в тестах; рабочий `.env` остаётся mock.

Ограничения: production Agent по-прежнему fail-closed. Real OrcaRouter/GPU E2E не выполнялся. Локальный Tor не был настроен, поэтому live onion fetch не запускался. Device credential в Credential Manager / DPAPI, не в Git и не в renderer.

## Проверка 0.6.0 — 16 сентября 2026

- Backend: **181 passed, 1 skipped** (opt-in live test); отдельный read-only live TinyFish Search + Fetch: **1 passed**. Финальные изменения Browser также проверены 16 provider contract tests.
- Tauri optimized release + NSIS x64 — PASS. EXE и installer обновлены в `outputs`, ProductVersion 0.6.0. About UI берёт версию из package.json. Ярлык сохраняет прежний путь.
- Frontend: **17 unit passed**, TypeScript, Prettier и Vite — PASS.
- Playwright: **15 passed**: embedding prepare/cancel/retry, обычный чат, Files с реальными CPU embeddings, Web+RAG D/W sources, owner isolation, fake Agent confirmation/Stop, Browser typed confirmation/denial/retry, missing key/Off.
- Ruff lint/format, pip check, npm audit, pip-audit pinned dependency closure, cargo check — PASS. Known vulnerability count: 0.
- Alembic: clean install, 0001 → head с сохранением history, foreign keys и check — PASS. Рабочая SQLite: backup API → 0006 → 0007, все прежние row counts сохранены, foreign_key_check пуст.
- Pinned E5 artifacts: 135392183 bytes, SHA256 LFS / Git blob SHA1, query + passage CPU smoke, dimension 384. Использован проверенный legacy import без нового скачивания.
- RunPod официальный MCP read-only: ry246k5siqujgu EXITED, RUNNING GPU 0. Active managed sessions в рабочей БД 0. Volume uwgeaie5b0: 50 GB STANDARD, US-TX-3, без изменений.
- Секреты не обнаружены в Git; моделей/БД/.env среди tracked files нет. TINYFISH_API_KEY получен только backend credential provider; живой smoke вывел только PASS.

Ограничения: production Agent заблокирован из-за отсутствия enforceable read-only/per-action approval в supplier API. Его fake/SSE/cancel contracts проходят, но это не реальная Agent E2E. Browser typed/CDP lifecycle покрыт fake/contract tests; платный supplier запуск не выполнялся. LlamaCpp tools покрыты HTTP contracts, реальный OrcaRouter tool calling ещё не проверен. PostgreSQL live, интерактивная установка/удаление NSIS и подпись приложения не проверялись. Warnings: Starlette/httpx deprecation, Vite chunk >500 kB, Windows WebSocket teardown 10054; все проверки завершились успешно.

## Сохранённый отчёт 0.2.0 — этап 2

Дата: 14 сентября 2026. Изменён существующий проект `volkrist/alex-llm`; стек Tauri 2 / React / FastAPI сохранён.
Отчёт предыдущей версии сохранён в [verification-v1.md](verification-v1.md).

1. **Чаты.** Переименование, удаление с подтверждением, поиск названий, закрепление/открепление. Закреплённые идут первыми, затем сортировка по обновлению.
2. **Экспорт.** Markdown/JSON одного или всех собственных диалогов. Backend проверяет владельца. В Windows используется системный Save dialog, в браузере — download.
3. **Сообщения.** Копирование, редактирование своего текста, изменение с повторной отправкой, перегенерация/Retry ответа. Повторная генерация линейно удаляет последующую историю.
4. **Streaming.** Постепенный SSE, «Alex думает…», Stop и сохранение частичного ответа. Пустой прерванный ответ остаётся доступным для Retry. Повторная отправка блокируется.
5. **Composer.** Автоматическая высота, Enter/Shift+Enter/Ctrl+Enter; черновики разделены по backend, пользователю и диалогу. Настройка Enter проверена в браузере.
6. **Навигация.** Ctrl+N, Ctrl+K, Ctrl+comma, Esc; контекстное меню чатов, время сообщений, кнопка прокрутки вниз. Новые ответы не уводят читателя от просматриваемого текста.
7. **Markdown.** GFM, таблицы, код, подсветка синтаксиса, Copy code. Raw HTML не исполняется. HTTP(S)-ссылки открываются системным браузером по нажатию.
8. **Настройки.** Общие, чат, AI/Compute, данные, дополнительные. Тема Dark/System, размер текста, русскоязычный UI, автопрокрутка/время/технические сведения. Backend URL вынесен в дополнительные параметры.
9. **RunPod-клиент.** `app/compute/runpod_api.py`: официальный REST v2, фиксированный API host, тайм-ауты, структурная валидация и безопасные ошибки. Runtime не использует MCP.
10. **Выбор GPU.** NVIDIA Secure, существующий Volume в US-TX-3, configurable VRAM/цена/бюджет. Автоматически — самый дешёвый доступный вариант; вручную — выбранная GPU. Недоступные/дорогие варианты отключены.
11. **Подтверждение цены.** Стоимость часа/минуты/10/30/60 минут, отдельное замечание о storage, срок предложения. Перед create — повторная проверка. Fallback ограничен тремя кандидатами не дороже подтверждённого.
12. **Контроллер.** `app/compute/controller.py`: DB lease, committed create intent, idempotency, deterministic Pod name, recovery. Неопределённый create не повторяется автоматически. Существующий Pod не дублируется.
13. **Готовность.** Реальные состояния поставщика и свежие фазовые маркеры existing startup/check scripts. Нет фиктивных процентов/TPS. Устаревшие logs не означают Ready.
14. **Время и стоимость.** Supplier started_at отдельно от ready_at; backend считает elapsed/estimated cost. Историческая ставка сохраняется. Actual cost запрашивается отдельно; отсутствие данных остаётся null.
15. **Бюджет/автостоп.** Достигнутый бюджет запрещает новую генерацию; остановка ждёт текущую. Idle timeout применяется после Ready без активной генерации. Network Volume не удаляется. Внешний Pod не останавливается автоматически.
16. **Пользователи/админ.** ADMIN_EMAILS только на backend; ALLOW_USER_COMPUTE_START=false по умолчанию. Личное использование за день/неделю/месяц/всё время, история сессий; админ видит пользователей, общие сессии/расходы и управление compute.
17. **Миграция.** Alembic 0002 добавляет роли, поля чатов/сообщений, compute_control/sessions/events/quotes/preferences и generation_usage. Проверено сохранение данных 0001 → 0002, чистая БД в E2E и `alembic check`. Рабочая SQLite обновлена после backup API.
18. **Безопасность.** RunPod key отсутствует в frontend/exe; хранится только в backend env. Без ключа Not configured, mock работает. Ownership новых chat/export/edit endpoints и admin permissions покрыты тестами. CORS и JWT не ослаблены.
19. **Проверки.** 39 backend pytest, 8 frontend unit tests; Ruff, Prettier, TypeScript/Vite, cargo check, Tauri release и NSIS build. Браузерные сценарии: базовый чат/изоляция, mobile/offline, новые функции чатов, test-only compute UI. Нативный WebView2 smoke проверяет регистрацию, streaming, Copy, Stop, историю, удаление и выход. npm audit: 0 vulnerabilities.
20. **Артефакты.** `../Alex LLM.exe` и `../Alex LLM Setup.exe` относительно корня проекта. Ярлык `C:\Users\Volkr\Desktop\Alex LLM.lnk` указывает на обновлённый exe. Пользователю desktop не нужны Node/Rust/Python; нужен доступный backend и WebView2. Backend остаётся отдельным сервисом.
21. **Границы проверки.** Реальный платный запуск RunPod не выполнялся. Живые startup scripts/model/image и фактический billing не проверены; использованы HTTP mocks. Installer собран, интерактивная установка/удаление не проверялась. PostgreSQL не запускался. Native save dialog/open-browser не включены в автоматический smoke; browser export проверен. Приложение не подписано. Нет подключения реального LLM к чату, Memory/RAG/агентов/LoRA.

## Существенные ограничения

RunPod API не предоставляет атомарную гарантию max-price для create. При изменении цены между проверкой и созданием возможна короткая платная сессия до защитной остановки.
Бюджет — ограничитель по polling, не предоплаченный потолок: текущая генерация и задержки API могут дать превышение.
Мониторинг/автостоп работает, пока работает backend. Chat recovery требует одного backend worker.
Оценка стоимости — не счёт RunPod; хранение оплачивается отдельно, текущий тариф API не сообщает.

Чат остаётся `LLM_PROVIDER=mock` по заданию. Test-only UI fixtures находятся только в Playwright; production-переключателя вымышленных состояний нет.
Официальный контракт и команды настройки: [runpod-controller.md](runpod-controller.md). API/структура: [architecture.md](architecture.md).

## Снимки экранов

Ниже GPU-состояния с суффиксом `test` получены через Playwright interception, а не через запуск платного Pod.

![Вход](screenshots/login.png)
![Чат](screenshots/chat.png)
![Настройки](screenshots/settings.png)
![Использование](screenshots/usage.png)
![GPU offline — тест](screenshots/gpu-offline-test.png)
![GPU search — тест](screenshots/gpu-search-test.png)
![GPU price confirmation — тест](screenshots/gpu-confirm-test.png)
![GPU starting — тест](screenshots/gpu-starting-test.png)
![GPU ready — тест](screenshots/gpu-ready-test.png)

SHA256 executable: `C23977A9E2B13991912DD21C6335A1F55952AE76346F62D4658BDA4CFEA8E1AA`.
SHA256 installer: `D6958D5AD93F421AB3661A47F54F5A48529B1D1C68DB8C21BE4C15AE3E428105`.

## 0.4.0

Current local Presence/Memory/ContextBuilder checks are recorded in [verification-0.4.md](verification-0.4.md). Earlier GPU preflight observations in this document are historical and do not establish a successful real-model E2E.


## 0.5.0 — Files / RAG, 15 сентября 2026

- Предварительный compute commit: `1a6cb8654b68b93240d886cc61ef175bf7613208`, отдельно запушен в main; после него clean. Cleanup commit: `73e020b`.
- Used Memory отображает сохранённые backend metadata конкретного generation: project, выбранная память/категории/символы, история, полный размер и текущий запрос ровно один раз. Старые записи без metadata показывают неизвестные значения.
- Compute UI загружает canonical preferences; не допускает поиска до их загрузки, показывает сохранённые и активные лимиты отдельно. Значение 0.82 может существовать как сохранённое предпочтение пользователя, но не является скрытым hard ceiling.
- TTFT UX: sending → ожидание первого ответа → обработка запроса после 8 секунд → streaming → completed/stopped/error. Реальный elapsed, без процентов. Timestamps/TTFT nullable; upstream_cancel_confirmed остаётся null без отдельного доказательства. Отмена до первого токена покрыта тестом.
- PDF/DOCX/TXT/MD → безопасное local storage → extraction → token-aware chunks → реальные локальные CPU embeddings → ownership/project filtered cosine retrieval → ContextBuilder → MockLLM → persisted source panel. [Архитектура RAG](rag.md), [Файлы и ограничения](files.md).
- Backend: 120 pytest PASS, включая три реальных CPU-теста RU/EN/KO; файловые проверки включают ограничение parser memory. Ruff check/format PASS. Frontend: 13 unit PASS; TypeScript, Vite, Prettier PASS. Playwright: 10 PASS, существующие функции и три файловых сценария.
- npm audit: 0 vulnerabilities. pip-audit: no known vulnerabilities (локальный пакет alex-llm-backend отсутствует на PyPI и проверяется исходниками/тестами). Обновлён локальный инструмент pip, исходно устаревший; зависимости приложения без известных findings.
- Alembic: clean install, 0005→0006, upgrade реальной локальной БД после SQLite backup PASS. Все 14 прежних таблиц сохранили количество строк; foreign_key_check PASS; alembic check без новых операций.
- CPU benchmark: quantized E5, 384 dimensions; cache 135,392,183 bytes; cold first batch 3.0151 s; warm query embedding 0.0059 s; TXT 2,900 bytes indexing 0.3121 s, Ready. RU/EN/KO queries selected the correct document among three examples. Порог по умолчанию откалиброван до 0.72: русский запрос к английскому источнику даёт 0.753, нерелевантные примеры 0.671/0.652. Это небольшая локальная выборка, не общий benchmark качества или worst-case latency.
- Read-only RunPod: только `ry246k5siqujgu` / `orcarouter-l40s` / EXITED; RUNNING GPU 0. Active managed sessions 0, search offline. Network Volume `uwgeaie5b0`, STANDARD 50 GB, US-TX-3 сохранён. Paid mutations 0.
- Ограничения: single backend worker; exact local vector scan; OCR и selected-file-only mode не реализованы; panel показывает переданные источники, не гарантирует цитирование моделью. Нет inline citation rewriting, cloud storage или pgvector. Real OrcaRouter RAG остаётся для отдельно разрешённого этапа.
- Известные предупреждения: Vite bundle >500 kB; два deprecation warnings TestClient/AnyIO; Windows asyncio иногда пишет connection reset при закрытии тестового WebSocket. Проверки завершаются успешно. Интерактивная установка NSIS не выполнялась; приложение не подписано.

![Источник PDF](screenshots/0.5/source.png)
![Индексация в Files](screenshots/0.5/indexing.png)

- cargo check PASS; Tauri 0.5.0 release PASS; NSIS x64 installer PASS. EXE и Setup обновлены в outputs, существующий Desktop shortcut указывает на EXE 0.5.0. Рабочий backend /health сообщает mock, API version 0.5.0, compute monitor отключён.
- EXE SHA256: `306A6B8BA0C9AFA22911946947532B2D0C6D9E5E87BEF8B59530E49FD8E92F11`.
- Installer SHA256: `9109D06655FA65D5F01611AC488C5552FC151E8A7F5FD98AD0AB89D55212D02C`.
