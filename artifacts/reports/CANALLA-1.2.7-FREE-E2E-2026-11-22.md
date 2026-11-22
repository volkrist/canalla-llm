# CANALLA LLM — FREE LEVEL 7 E2E: ОТЧЁТ СЕССИИ

**Первый настоящий двухпроцессный прогон resume выполнен. Durable resume доказан по механике, финальный PASS не получен: найден дефект resume-пути. GPU не запускался.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD | **`e6099e3`** (запушен, origin синхронизирован) |
| Баланс RunPod | **$13.055671924**, Pod **0**, фон $0.01/ч |
| Расход этой сессии | **$0.00** (платных операций не было) |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Production 1.2.6 | не изменена: 2/54/120/63/30, integrity ok, 0 FK |
| LEVEL 6 | **PASS** (evidence не тронут) |
| LEVEL 7 | **NOT_PROVEN**: `LEVEL7_FREE_E2E = FAIL` |
| Monitor safety | **PASS** (8/8) |

---

## A. Цель сессии

Довести `--resume` до работающего сквозного сценария: fake inference → реальный backend → реальные
инструменты → checkpoint → настоящее убийство процесса → второй backend → штатный
`POST /tasks/{id}/resume` → продолжение → независимая проверка.

## B. Состояние до работы

`c263258` / `9ad46b4`: `--resume` в harness написан, но не исполнялся ни разу; fake-LLM сервера и
двухпроцессного раннера не существовало; монитор был исправлен (пауза + ARMED manifest), но
интеграция с реальным запуском не проверялась.

## C. Выполненные работы

### C.1. Fake OpenAI-compatible inference — реализован

`artifacts/acceptance-127/live-qwen/fake_llm.py` (stdlib, без GPU и API-ключей):

- `GET /v1/models` → 200 (health);
- `POST /v1/chat/completions`: `stream=false` → `choices[0].message.tool_calls` с `id`/`name`/
  `arguments`; `stream=true` → SSE `data: {...}` + `[DONE]`;
- сценарий выводится из **самого транскрипта**, а не только из счётчика: наличие результата
  `final.txt` → `verify_result`, флаг `verification_asked` (в файле состояния) → завершение;
- состояние (`fake-llm-state.json`) переживает смерть backend.

### C.2. Двухпроцессный E2E — реализован и запущен

`artifacts/acceptance-127/live-qwen/free_resume_e2e.py` делает всё на реальных компонентах:

1. поднимает fake-сервер и изолированную БД (alembic `upgrade head`, exit 0);
2. backend A на свободном порту, `/health` 200, регистрация 201;
3. `PUT /tools/preferences` (`computer_mode=trusted`, `workspace_roots`), запуск настоящего
   `alex-host-loop.exe` (спаривание, heartbeat, jobs);
4. стрим задачи, ожидание **реально завершённых** `ToolRun` (порог 3);
5. `terminate` backend A, ожидание выхода, проверка, что задача **не** COMPLETED;
6. backend B на том же порту/БД/data root + перезапуск host loop;
7. `POST /tasks/{task_id}/resume`, ожидание завершения;
8. сверка: тот же task_id, новый PID, новый прогресс, ровно одно `COMPLETED`, файлы, verifier.

## D. Результаты прогонов (реальные, не пересказ)

| Прогон | Что выяснилось |
|---|---|
| 1 | backend поднимался, но задача уходила в `WAITING_LLM`: не был задан `LLM_CONNECTION_MODE=static` |
| 2 | соединение исправлено, но задача завершалась без инструментов: промпт не включал автономный режим |
| 3 | задача в `WAITING_DEVICE`: без host loop локальные инструменты не исполняются |
| 4 | host добавлен; **3 реальных `write_file`**, kill, рестарт — но host умирал вместе с backend |
| 5 | host перезапускается вместе с backend; **новый прогресс после рестарта** есть, финал `FAILED` |
| 6–7 | уточнение сценария fake-модели; итог тот же: `FAILED / verification_required` |

Финальный прогон (run `17150c58…`):

| Проверка | Факт |
|---|---|
| completed tool runs до kill | **3** (`part1.txt`, `part2.txt`, `part3.txt`) |
| статус задачи перед kill | **EXECUTING** (не COMPLETED) |
| backend A | exit **1**, stream `ConnectionReset` |
| статус после смерти | **EXECUTING** (восстановим) |
| backend B | **новый PID 3572**, тот же порт/БД |
| host B | новый PID 7308 |
| `task_id` | **тот же** |
| новый прогресс после рестарта | **есть** (4-е действие — `final.txt`) |
| файлы | part1, part2, part3, final — все существуют |
| `COMPLETED` событий | **0** |
| вердикт | **FAIL** |

## E. Найденный дефект продукта (не теста)

Цепочка событий после resume: `RESUMED → RESUMED → MODEL_RESPONSE → FAILED`.
Поток `POST /tasks/{id}/resume` уходит сразу в генерацию ответа и **не выполняет планировочный
раунд**, поэтому модель не может запросить новый инструмент (в сценарии — `verify_result`).
Контроллер при этом **корректно** отказывается объявлять задачу выполненной: файлы изменены,
независимой проверки нет (`last_error = verification_required`).

Иными словами: **durable resume не позволяет продолжить работу инструментом после перезапуска**,
если прерывание произошло между изменениями и верификацией. Это именно тот класс дефекта, который
обязан ловить LEVEL 7.

## F. Исправленные ошибки этой сессии

1. Fake-сервер не доходил до backend — добавлен `LLM_CONNECTION_MODE=static`.
2. Задача не становилась автономной — промпт приведён к автономному сценарию.
3. `WAITING_DEVICE` — добавлены `ALEX_DEVICE_DIR`, `ALEX_DEVICE_CREDENTIAL_TARGET`, запуск host loop.
4. Host умирал после kill backend — E2E перезапускает host вместе с backend B.
5. Сценарий fake-модели «плыл» от лишнего планировочного запроса — переход на состояние из
   транскрипта + флаг `verification_asked`.
6. **Повреждённая git-ссылка ветки** после аварийной записи — восстановлена `update-ref` на
   существующий коммит, история не потеряна, push выполнен.

## G. Монитор: защита от повторного инцидента

Реализовано ранее в этой сессии и проверено: `monitor.paused` (отдельный файл, проверяется до
всего) и ARMED execution manifest (batch, scenario_id/hash, code_commit, лимит стоимости).
Тесты: **8/8 PASS**, включая точную репродукцию инцидента
`a_scheduler_restart_does_not_rearm_a_paused_monitor`.

Текущее состояние монитора: пауза установлена (`monitor.paused`), Pod не создаются.

## H. Тесты

| Проверка | Результат |
|---|---|
| `monitor/test_monitor_armed.py` | **8/8 PASS** |
| `live_acceptance.py --self-test` | **PASS** (30 проверок) |
| `free_resume_e2e.py` (полный прогон) | **FAIL** — см. раздел D/E |
| `test_127_level7_negative.py` | 3 passed (ранее) |
| `test_batch_budget.py` | 11/11 (ранее) |

## I. Деньги и инфраструктура

- Платных Pod: **0**, расход **$0.00**, баланс $13.055671924, резерв $8.50 не тронут.
- Ledger серии не изменялся: $0.4298, Pod 3/3, статус `PAID_BATCH_EXHAUSTED`.
- Новый batch не создавался, технический лимит не сбрасывался.
- GPU/Pod не запускались, watchdog не отключался.

## J. Production

Не изменялась. Проверка до и после: users 2, chats 54, messages 120, paired_devices 63,
local_tasks 30, `integrity_check = ok`, FK 0, alembic 0016.

## K. Git

```
e6099e3 Add the fake inference server and the free resume runner
9ad46b4 Checkpoint the LEVEL 7 free E2E plan and tool schemas
c263258 Fix the monitor restart incident and implement --resume
```

Force-push и destructive reset не выполнялись.

## L. Остаточные блокеры

1. **Resume без планировочного раунда** — исправление в `chat_stream`/`continue_task` (продукт).
2. **`LEVEL7_FREE_E2E`** — останется FAIL до исправления п.1; LIVE запрещён.
3. **Лимит Pod 3/3** — денежный остаток $4.57 есть, но технические слоты исчерпаны; расширение
   требует отдельного распоряжения (в последнем задании лимит снимался, но batch не пересоздавался).

## M. Следующий исполняемый шаг

Исправить resume-путь так, чтобы при `resume_task_id` с незавершённой верификацией выполнялся
планировочный раунд с инструментами. Регрессия — `free_resume_e2e.py`: он обязан дойти до
`verify_result`, одного `COMPLETED` и `verifier PASS`. Затем — подготовка ARMED manifest для LIVE
LEVEL 7 в пределах действующего денежного лимита.

## N. Воспроизведение

```bash
cd apps/backend
.venv/Scripts/python.exe ../../artifacts/acceptance-127/live-qwen/free_resume_e2e.py
cd ../../artifacts/acceptance-127/live-qwen/monitor
../../../../apps/backend/.venv/Scripts/python.exe test_monitor_armed.py
cd ../..
../../../../apps/backend/.venv/Scripts/python.exe live_acceptance.py --self-test
```

## O. Индекс evidence

| Файл | Содержание |
|---|---|
| `artifacts/acceptance-127/live-qwen/fake_llm.py` | fake inference |
| `artifacts/acceptance-127/live-qwen/free_resume_e2e.py` | двухпроцессный раннер |
| `%TEMP%\canalla-free-e2e-*\backend.log`, `host.log` | логи прогонов |
| `apps/backend/tests/test_127_level7_negative.py` | negative-матрица |
| `artifacts/agent-state.json` | machine-readable состояние |
