# Canalla LLM 1.2.7 — автономное агентное ядро

**Задание:** реальная работающая Canalla по шести направлениям, одна согласованная архитектура, без публикации 1.2.7.
**Ветка:** `feature/canalla-1.2.7-autonomous-agent` (от `release/canalla-1.2.4` @ `b0b6efe`).
**База:** опубликованная **1.2.6** (tag `v1.2.6`, продуктовый коммит `b5b24f0`) — не изменялась.
**Модель в приёмке:** **scripted planner (MOCK MODEL)**. Настоящая Qwen не запускалась, GPU не создавался.

---

## 1. Что проверено перед разработкой

| Проверка | Факт |
|---|---|
| Git HEAD | `b0b6efe` (ветка `release/canalla-1.2.4`, upstream совпадает) |
| Разница с тегом | 5 коммитов, все — инструментарий релиза, evidence и отчёты; runtime-код не менялся |
| Установленное приложение | 1.2.6, `/health` → `ok`, `version: 1.2.6` |
| Production updater | `GET /updates/latest?current_version=1.2.6` → **204** (не менялся) |
| Pods | **`pods []`**, баланс $8.7186040616, расход $0 |
| Данные | integrity `ok`, users 2, chats 54, messages 120, paired_devices 59 |
| Device ID | `f3548022-a676-463c-9fc5-b05bbd5ac907` |
| Verified backup перед работой | `20261112T220302Z-manual`, `verified=True` |

Публичный релиз 1.2.6, его тег, ассеты, manifest и ключ подписи **не трогались**.

---

## 2. Архитектурное решение

Ядро 1.2.6 уже содержало `LocalTask`, автомат состояний, journal, checkpoints, `final_review`, `security_assessment` и исполнитель инструментов. Поэтому ничего не переписывалось: добавлен слой, которого не хватало.

```
USER GOAL
  → UNDERSTAND / PLAN     (существующий LocalTask + plan.py)
  → RESEARCH              (новый agent/research.py: SEARCH → READ → GAP → REQUERY)
  → EXECUTE               (существующий ToolExecutor + новые инструменты)
  → OBSERVE               (journal, facts, screenshot/HTTP/process)
  → VERIFY                (новый agent/verifier.py — независимая проверка)
  → FIX                   (новый agent/repair.py + существующий retry/plan revision)
  → RETEST → COMPLETED    (final_review теперь требует evidence, если verifier использовался)
```

### Новые модули

| Файл | Назначение |
|---|---|
| `apps/backend/app/agent/evidence.py` | `Evidence` + `EvidenceLedger`: что именно наблюдалось, с дедупликацией и сохранением в задачу |
| `apps/backend/app/agent/verifier.py` | независимые проверки: файл, sha256, содержимое, процесс, порт, HTTP, команда, строка БД, окно |
| `apps/backend/app/agent/research.py` | адаптивный research-loop с бюджетами, дедупликацией, состояниями источников и карантином prompt injection |
| `apps/backend/app/agent/webclient.py` | реальный HTTP-клиент: только http/https, публичные адреса, лимит байт, robots.txt |
| `apps/backend/app/agent/gui.py` | браузерный GUI-адаптер (Playwright, OBSERVE → ACT → VERIFY) + read-only проба окна Windows |
| `apps/backend/app/agent/repair.py` | отпечаток ошибки, журнал попыток, детектор отсутствия прогресса |
| `apps/backend/app/agent/queue.py` | durable очередь на существующей SQLite: enqueue/claim/release/recover |
| `apps/backend/app/tools/agent_tools.py` | инструменты `verify_result`, `deep_research`, `browser_gui`, `agent_queue` |
| `apps/backend/alembic/versions/0017_agent_queue.py` | аддитивная обратимая миграция (10 колонок + индекс) |

### Изменения в существующем коде (минимальные)

- `machine.py`: состояния `QUEUED` и `WAITING_USER` + разрешённые переходы; `PLANNING → QUEUED` для release.
- `plan.py`: `is_coding_request` — «создай приложение и исправь дефект» это сборка, а не ремонт чужого репозитория; `looks_like_autonomous` учитывает greenfield.
- `task.py`: план строится по `is_coding_request`; `final_review` блокирует COMPLETED при `evidence_failed`.
- `policy.py` / `orchestrator.py` / `executor.py`: новые capability доступны агенту, попадают в checkpoint и не обходят TOR_ONLY-маршрут.
- `models.py`: поля запроса/очереди/бюджетов (нормализованная цель, scope, permissions, artifacts, приоритет, claim).

Ни один старый инструмент, маршрут или параметр не удалён.

---

## 3. Главная приёмочная проверка (Level 3, бесплатно)

`apps/backend/tests/test_127_autonomous_e2e.py::test_one_goal_research_free_build_run_gui_fix_and_verify`

Один пользовательский запрос: *«Создай TaskDesk для управления задачами: локальная база, создание, поиск, статусы, веб-интерфейс и тесты. Запусти, проверь интерфейс, найди дефект поиска, исправь и подтверди результат».*

Всё настоящее, кроме модели: backend, SQLite-задача, host-инструменты, pytest, uvicorn, Playwright, verifier.

| Шаг | Факт |
|---|---|
| Файлы | `main.py` (FastAPI + SQLite + HTML UI) и `test_main.py` созданы инструментом `write_file` |
| Первый прогон тестов | **FAIL** — поиск регистрозависимый (`return query in title`) |
| Диагностика | `read_file` реального исходника |
| Исправление | минимальный патч: `query.casefold() in title.casefold()` |
| Повторный прогон | **PASS** |
| Запуск | `python -m uvicorn main:app --port <free>` через `run_python wait=false` |
| Независимая проверка | `verify_result`: `port_open` ✅, `http_ok` ✅, `command` (pytest) ✅ |
| GUI | `browser_gui`: заполнение поля, клик «Добавить», поиск «alpha» → задача найдена; **4/4 шага**, **5 скриншотов** |
| Данные | `verify_result.db_row` ✅; в `tasks.db` реально лежит `(1, 'Alpha task', 'open')` |
| Код | `file_contains` подтверждает `casefold()` в файле |
| Завершение | задача **`COMPLETED`**, `verification.evidence_ok = true` |
| Остановка | `process_status` → `stop_process`; порт после остановки закрыт |

Устойчивость: **4 последовательных прогона PASS**. Один ранний прогон упал только потому, что рабочая папка была помещена внутрь репозитория — продукт корректно потребовал `git_reviewed` и не поставил COMPLETED. Это правило оставлено как есть.

### Исследование — отдельная проверка

`test_deep_research_tool_reads_real_pages_and_requeries`: инструмент `deep_research` читает **реальную локальную HTTP-страницу**, извлекает `version`, обнаруживает **пробел `license`** и сообщает его как gap. Текст страницы с «ignore all previous instructions…» **карантинируется** и никогда не исполняется как команда.

---

## 4. Матрица возможностей

| Направление | Статус | Доказательство | Ограничение |
|---|---|---|---|
| **Web Research** | CODE READY / **FREE PASS** | `test_127_agent_core.py` (адаптивность, дедуп, бюджет, карантин), `test_127_autonomous_e2e.py` (реальный HTTP) | LIVE PASS не заявляется; внешний поисковик используется при наличии провайдера |
| **App Build** | CODE READY / **FREE PASS** | Level 3: файлы → pytest fail → fix → pytest pass → uvicorn → HTTP | LIVE PASS не заявляется |
| **GUI Autonomy** | CODE READY / **FREE PASS (браузер)** / **PARTIAL** | 5 скриншотов, 4 проверенных действия | **нативный GUI сторонних Windows-программ (UI Automation) не реализован** |
| **Auto Fix** | CODE READY / **FREE PASS** | воспроизведение FAIL → минимальный патч → PASS + тесты журнала попыток | LIVE PASS не заявляется |
| **Durable Recovery** | CODE READY / **FREE PASS** / **PARTIAL** | claim/release/recover, `QUEUED` в автомате, идемпотентность; T4-возобновление сертифицировано в 1.2.6 | полный сценарий «прервать сборку посреди GUI и продолжить» в этом инкременте не прогонялся |
| **Security Assessment** | CODE READY / **FREE PASS** / **PARTIAL** | OSV/KEV, SAST-правила, config review, scope, retest-gate, `test_security_assessment.py` | нет активных адаптеров (Nmap/ZAP) и меток жизненного цикла findings |

**FULL LIVE AUTONOMY = NOT VERIFIED** — настоящая Qwen не запускалась.

---

## 5. Регрессии и качество

| Проверка | Результат |
|---|---|
| Полный backend suite | **1102 passed, 1 skipped, 0 failed** (9 мин 31 с) |
| Целевые наборы 1.2.7 | 15 новых тестов + 52 существующих — PASS |
| `alembic upgrade head` + `alembic check` | head **0017**, «No new upgrade operations detected» |
| Backup / migrations / upgrade | 46 тестов PASS после обновления ожидаемой ревизии |
| Sidecar stamp | пересобран: digest `1483037752d0…`, exe `71cdb00b4efe…`; 11 тестов PASS |
| **CI на ветке** | **7/7 success** на `62da633` (developer-gate, native-gate, frontend-e2e, installer-build, tor-runtime, llama-runtime, llama-runtime-image) |
| Linux gate | **success** на `88f26d7` и последующих |
| Plan/task regression | `test_autonomous_tasks.py`, `test_coding_agent.py`, `test_123_agent_runtime.py` — PASS |

Изменения схемы аддитивны и обратимы: `downgrade()` удаляет только новые колонки; данные 1.2.6 не переписываются.

---

## 6. Безопасность самого агента

- Веб-текст — **данные**: инструкции со страницы карантинируются, попадают в отчёт как находка и никогда не меняют workspace, бюджет или политику.
- `verify_result` отказывает в путях **вне** workspace-корней (`CHECK_REFUSED`), а не проверяет их молча.
- HTTP-клиент: только `http/https`, запрет приватных/loopback-адресов (кроме явного тестового флага), лимит байт, robots.txt.
- `deep_research` под маршрутом `TOR_ONLY` блокируется типизированной ошибкой, а не уходит в прямой выход.
- Скриншоты GUI пишутся только внутрь workspace.
- Ключи, токены и пароли в аргументах инструментов по-прежнему отсекаются `sensitive_arguments`.

---

## 7. Чего в этом инкременте нет (честно)

1. **Нативный GUI сторонних Windows-приложений.** Есть только браузерный адаптер и read-only перечисление окон. UI Automation (control patterns, invoke/set-value, DPI/мультимонитор) — следующий шаг: он требует расширения native host, а не эмуляции.
2. **Активная проверка безопасности** (Nmap/ZAP) и метки `POTENTIAL/CONFIRMED/FALSE_POSITIVE/FIXED` — не реализованы.
3. **Полный сценарий восстановления сборки** (прервать посреди GUI, перезапустить приложение, продолжить) — механика очереди проверена, сквозной сценарий нет.
4. **LIVE Qwen** — не запускался: нет разрешения на платный Pod, расход $0.
5. **Публикация 1.2.7** — не выполнялась и не планировалась без отдельного разрешения.

---

## 8. Состояние после работы

| | |
|---|---|
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| Финальный SHA с зелёным CI | `62da633` |
| Публичный релиз 1.2.6 | не изменён |
| Production updater | не изменён (по-прежнему 1.2.6) |
| Подпись / ключ | не менялись |
| Pods | `pods []` |
| Расход | **$0** |
| Данные | integrity ok, 2 / 54 / 120 / 59 |
| Device ID | `f3548022-a676-463c-9fc5-b05bbd5ac907` |
| Network Volumes | не трогались |

---

## 9. Следующий шаг

1. Нативный Windows GUI (UI Automation через host) + сценарий «сторонняя программа: открыть → изменить → сохранить → закрыть → открыть снова».
2. Активная безопасность в подтверждённом scope + метки findings + retest после исправления.
3. Сквозное восстановление сборки: прервать посреди GUI → перезапуск приложения → Resume с тем же `task_id`, без повторных side effects.
4. Только после этого — подготовка кандидата 1.2.7 и **отдельное** разрешение владельца на LIVE Qwen и публикацию.

**Главный результат:** пользовательская задача «создай → запусти → проверь GUI → найди дефект → исправь → подтверди» реально выполнена на настоящих инструментах, а не описана; её результат подтверждён независимым verifier'ом, скриншотами и строкой в базе созданного приложения.
