# CANALLA LLM — LEVEL 7: ОТЧЁТ ПОПЫТКИ

**LEVEL 7 не доказан. Неподготовленный платный Pod остановлен, бюджет сохранён, серия исчерпана по лимиту Pod.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD | `55e5ecb` (проверенный код LEVEL 6) |
| Баланс до работы | **$13.1542574433** |
| Баланс после | **$13.0751163684** (Δ **$0.0791**, включая финальные списания Pod после удаления) |
| Batch | `d516f71c83f347289452398e0f972f98` — **exhausted по лимиту Pod** |
| Pod | **0**, биллинг остановлен (`currentSpendPerHr` 1.101 → **0.01**) |
| Production | 1.2.6, не изменена: 2/54/120/63/30, integrity ok, 0 FK |
| LEVEL 6 | **PASS** (доказательство не перезаписано) |
| LEVEL 7 | **BLOCKED_BY_BATCH_POD_LIMIT** (не пройден, не имитировался) |

---

## A. Цель

Доказать LEVEL 7 — реальная модель продолжает незавершённую задачу после настоящего прерывания —
в пределах остатка прошлой серии: $5.00 cap, резерв $8.50, **один оставшийся Pod-слот**.

## B. Фактический Git

`55e5ecb`, ветка `feature/canalla-1.2.7-autonomous-agent`, синхронна с origin.
LEVEL 6 evidence (`final-summary.json`, `provenance-verdict.json`, `http-check.json`,
`gui-evidence/independent-page.png`) — mtime 12:22, **не изменены**.

## C. Баланс до запуска

API read-only: **$13.1542574433**, 0 Pod, фон $0.01/ч. Доступный остаток серии $4.6091.

## D. Использованный бюджет

| Статья | Значение |
|---|---|
| Ledger серии до | $0.3909 |
| Неподготовленный Pod (см. раздел O) | **+$0.0389** |
| Ledger серии после | **$0.4298** |
| Остаток лимита | $4.5702 (но Pod-слоты исчерпаны) |
| Резерв $8.50 | не тронут |

## E. Бесплатные работы

- Аудит Git, состояния монитора, batch, API.
- Найдена штатная точка возобновления: `POST /tasks/{key}/resume` → `LocalTaskController.resume`
  → `stream_response(action="resume", resume_task_id=...)` — на ней и должен строиться LEVEL 7
  без второй системы оркестрации.
- Добавлены отсутствовавшие negative-тесты (раздел J).

## F. LIVE run / pod

Платного прогона LEVEL 7 **не было**. Созданный Pod `bgukpu9tqcrxwb` относился к повторной
LEVEL 6 попытке, запущенной автоматически (раздел O), и был остановлен инженером.

## G. Model provenance

Новых LIVE-генераций нет. Доказательство LEVEL 6 (модель `orcarouter-qwen38-27b-q5km`,
llamacpp, `REAL_MODEL_CONFIRMED`) сохранено и относится к run
`0ef569505ce04d73bdf5cb722949415e`.

## H. Checkpoint

Реализация live-resume harness в этой сессии не завершена: прерывание должно происходить внутри
уже сертифицированного `run_live`, чтобы использовать его Pod-жизненный цикл и watchdog. Черновик
подхода зафиксирован в разделе T; он не запускался, потому что непроверенный сценарий нельзя
тратить на последний Pod-слот.

## I. Controlled interruption

Не выполнялось (нет подготовленного harness).

## J. Restart и recovery

Не выполнялось. Офлайн-механика остаётся доказанной:
`test_127_recovery_e2e.py` (убитый процесс → второй процесс продолжает тот же task_id) и
`test_127_recovery_matrix.py` (5 контролируемых сбоев, одно `COMPLETED`).

## K. Продолжение моделью

Не выполнялось.

## L. Независимая проверка

Не выполнялось для LEVEL 7. Независимая проверка LEVEL 6 (12 тестов, HTTP 201, SQLite, GUI,
provenance, `VERIFY_PASSED`) не затронута.

## M. Финальный вердикт LEVEL 7

**BLOCKED_BY_BATCH_POD_LIMIT** — объективная причина: 3 из 3 разрешённых Pod израсходованы,
новый batch и новые Pod-слоты заданием запрещены. Ни offline-тест, ни SQLite-восстановление не
выданы за LEVEL 7.

## N. Дополнительные LIVE-сценарии

Не выполнялись: сначала требуется LEVEL 7, а Pod-слотов больше нет.

## O. Найденные ошибки и исправления

**Инцидент.** Остановленный в прошлой сессии монитор был перезапущен (планировщик/собственный цикл),
снова включил `monitoring_enabled`, создал Pod `bgukpu9tqcrxwb` в 07:28:08Z и начал **повторную
LEVEL 6** попытку (`AUTO_LIVE`), а не LEVEL 7.

Действия: монитор и harness остановлены, Pod удалён через API (`terminate` → 204), ноль Pod
подтверждён, биллинг вернулся к $0.01/ч, расход $0.0389 записан в ledger как
`ABORTED_BY_ENGINEER`, `reconciliation_status = clean`. Серия переведена в `exhausted` по лимиту
Pod (3/3) — без подделки счётчиков и без нового batch.

**Вывод для следующей сессии:** монитор нельзя оставлять способным к автономному запуску без
подготовленного сценария; остановка задачи должна сопровождаться проверкой, что она не
перезапускается триггером.

## P. Тесты и CI

| Проверка | Результат |
|---|---|
| `tests/test_127_level7_negative.py` (новые) | **3 passed** |
| ruff check / ruff format (новый файл) | чисто |
| Ранее в сессии | Windows CI 7/7, Linux gate PASS, developer gate 9/9 — на `fa43168` |

Новые negative-тесты:

| Случай | Где доказан |
|---|---|
| A duplicate actions | `progress.should_block` → `already_applied` (`test_autonomous_tasks`) |
| B interrupted tool call | `test_127_recovery_matrix::a_tool_run_that_never_returned…` |
| C lost model connection | `WAITING_LLM` не затирается recovery (та же матрица) |
| D double completion | `finish()` → одно `COMPLETED` (та же матрица) |
| E missing checkpoint | `test_127_level7_negative::a_task_without_a_checkpoint…` — READY, не COMPLETED |
| F budget termination | `test_batch_budget.py` (11/11), `PAID_BATCH_EXHAUSTED` |
| G restart during verification | `verifier_is_runnable_after_an_interruption…` + «повтор не превращает FAIL в PASS» |
| H production isolation | `production-before/after.json` в LIVE (2/54/120/63/30, integrity ok, 0 FK) |

## Q. Финальный баланс

**$13.0751163684** (последнее наблюдение API; Δ к началу сессии $0.0791). В ledger на момент остановки записано $0.0389; остаток разницы — финальные списания Pod, опубликованные RunPod после удаления, и фоновое хранение. Резерв $8.50 не тронут. Подписки/сертификаты/Volume не покупались.

## R. Подтверждение удаления Pod

`terminate` → HTTP 204; `pods = []`; повторная проверка через 90 с: `current_spendPerHr = 0.01`,
`pod_count = 0`. Статус — **CLEAN** (подтверждён API, не предположение).

## S. Production

До и после: users 2, chats 54, messages 120, paired_devices 63, local_tasks 30, integrity `ok`,
FK 0. Установленная 1.2.6, её device credential и база не изменялись.

## T. Следующие задачи

1. **Подготовить live-resume внутри `run_live`** (одна сессия, один Pod):
   - новый флаг `--resume` в `live_acceptance.py`;
   - обёртка `on_event`: после N успешных tool calls поток прерывает **изолированный backend**
     (`backend.terminate()`), пишет `interruption.json` (task_id, status, checkpoint, digests);
   - рестарт того же backend (тот же `work`, `env`, `port`), ожидание `/health`;
   - `POST /tasks/{task_id}/resume`, сбор нового SSE-потока;
   - проверки: новый `MODEL_RESPONSE` + успешный tool call после рестарта, тот же `task_id`,
     ровно одно `COMPLETED`, отсутствие повторного побочного эффекта;
   - независимая проверка файлов/тестов/HTTP/SQLite после продолжения.
2. Провести этот сценарий офлайн с фейковым LLM (полный прогон без денег), затем один раз — на
   реальной модели.
3. Для реального запуска потребуется новое разрешение владельца на **новый batch** (текущий
   исчерпан по Pod-лимиту) либо явное расширение лимита Pod; самовольно этого не делать.
4. Продолжить бесплатные задачи: Computer Use E2E, расширение verifier, память проектов.

---

## Индекс доказательств

| Файл | Содержание |
|---|---|
| `artifacts/acceptance-127/live-qwen/final-live/final-summary.json` | LEVEL 6 PASS (не изменён) |
| `.../provenance-verdict.json` | `REAL_MODEL_CONFIRMED` |
| `artifacts/acceptance-127/live-qwen/monitor/batch-spend.jsonl` | журнал: 3 Pod, $0.4298, `ABORTED_BY_ENGINEER` |
| `C:\Users\Volkr\AppData\Local\CanallaMonitor\state.json` | монитор остановлен, `BATCH_EXHAUSTED` |
| `apps/backend/tests/test_127_level7_negative.py` | 3 новых negative-теста |
