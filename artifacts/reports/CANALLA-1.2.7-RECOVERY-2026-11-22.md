# CANALLA LLM — ВОССТАНОВЛЕНИЕ АВТОНОМНОСТИ: ОТЧЁТ

**Причина самозапуска монитора найдена и закрыта; ARMED-manifest; `--resume` реализован в harness. Платный запуск не выполнялся: fake-LLM E2E ещё не пройден.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent` / `9501eea` (+ изменения этой сессии) |
| ENGINEERING_STATE | **IMPLEMENTING_LEVEL_7** (продолжается) |
| LIVE_AUTHORIZATION_STATE | **BATCH_EXHAUSTED_BY_POD_LIMIT** |
| Баланс / Pod | $13.0751 / **0 Pod**, фон $0.01/ч |
| Ledger серии | $0.4298 из $5.00, Pod 3/3 |
| LEVEL 6 | **PASS** (не изменён) |
| LEVEL 7 | **NOT_PROVEN** — harness готов к офлайн-E2E, LIVE не запускался |
| Production | 1.2.6, не изменена: 2/54/120/63/30, integrity ok, 0 FK |

---

## A. Цель

Исправить причину самопроизвольного платного запуска, реализовать LEVEL 7 resume внутри
существующего harness и не блокировать инженерную работу финансовым состоянием.

## B. Причина инцидента (найдена в коде, не в догадке)

`install_monitor.ps1` регистрирует задачу с `<Repetition><Interval>PT1M</Interval>`,
`StartWhenAvailable=true` и `RestartOnFailure` PT1M. Пауза хранилась в `state.json` — том самом
файле, который каждый проход монитора **перезаписывает из памяти**. Процесс, уже работавший в
момент паузы, записал обратно `monitoring_enabled: true`, минутный триггер поднял новый экземпляр,
и тот купил Pod для уже пройденного LEVEL 6.

**Исправление (код):**

1. `monitor.paused` — отдельный файл паузы. Проверяется **до всего** в `run_check` и в
   `automation_mode`; не может быть перезаписан состоянием. Пишется `--disable`, удаляется
   `--enable`; переживает рестарт процесса, планировщика и перезагрузку.
2. **ARMED execution manifest** (`execution-manifest.json`) — без него дочерний grant не создаётся
   вообще. Проверяются: `state == ARMED`, `batch_id`, `scenario_id`, `scenario_hash` (sha256
   `live_acceptance.py`), `code_commit == git HEAD`, `max_cost <= authorized_cost`.
3. Монитор больше не может купить Pod «потому что GPU видна и деньги есть».

## C. Бесплатные работы этой сессии

| Работа | Результат |
|---|---|
| Регрессия инцидента + manifest | **8/8 PASS** (`test_monitor_armed.py`) |
| `--resume` в `live_acceptance.py` | реализован, компилируется |
| `live_acceptance.py --self-test` после изменений | **PASS** (30 проверок) |
| Деньги/API | 0 Pod, баланс подтверждён, ledger не изменён |

Новые проверки монитора:

- `no_armed_manifest_means_no_grant`
- `a_manifest_from_another_commit_is_refused`
- `a_changed_harness_is_refused`
- `a_running_manifest_cannot_start_again`
- `a_manifest_above_the_authorized_cost_is_refused`
- `an_armed_manifest_for_this_batch_creates_one_child`
- **`a_scheduler_restart_does_not_rearm_a_paused_monitor`** (точная репродукция инцидента)
- `enabling_clears_the_pause_file`

## D. Реализация `--resume` (код, не план)

В `live_acceptance.py` добавлены:

- флаг `--resume` и `--resume-after-tools`;
- `_interrupt_and_resume(...)`: ждёт N **реально завершённых** `ToolRun`, пишет checkpoint
  (task_id, status, число digests, счётчик tool runs), завершает **изолированный** backend
  (`terminate` → `wait(30)` → `kill`), фиксирует `exit_code` и статус задачи после смерти,
  поднимает новый backend с той же БД, тем же data root, тем же workspace и портом, ждёт `/health`,
  затем вызывает штатный `POST /tasks/{task_id}/resume` и собирает SSE-поток;
- `resume-evidence.json` с interruption/restart/resume;
- `resume_checks`: checkpoint получен, процесс завершён, задача не была COMPLETED, backend поднят,
  **тот же task_id**, **новый прогресс после рестарта**; при невыполнении вердикт —
  `RESUME_FAILED`, а не PASS.

Используется именно существующая цепочка продукта
`POST /tasks/{key}/resume → LocalTaskController.resume → stream_response(action="resume")`;
вторая система оркестрации не создавалась.

## E. Что ещё не сделано и почему LIVE не запускался

Обязательный **fake-LLM E2E двумя процессами** (раздел 6 задания) не выполнен: нужен локальный
OpenAI-совместимый сервер, детерминированно выдающий tool calls до и после прерывания. Без него
`LEVEL7_FREE_E2E = NOT_RUN`, а значит LIVE запрещён и собственным preflight, и заданием.

Платный запуск не выполнялся: 0 Pod создано, 0 денег потрачено в этой сессии.

## F. Финансы и лимит

Исходное разрешение задавало **денежный потолок $5.00**, резерв $8.50 и «не более одного
одновременного Pod». Лимит «3 Pod» — техническая настройка серии, а не денежное полномочие.
Тем не менее самостоятельно расширять лимит я не стал: это изменение полномочий, а не инженерия.
Исторические журналы не изменялись, 3/3 не сбрасывался, новый batch не создавался.

- Баланс: **$13.0751163684**, резерв $8.50 не тронут.
- Ledger: $0.4298, Pod 3/3 → `PAID_BATCH_EXHAUSTED`.

## G. Уровни автономности

| Уровень | Статус |
|---|---|
| 1–5 | PROVEN (прежние LIVE) |
| 6 | **PASS** (run `0ef56950…`, не изменён) |
| 7 | **NOT_PROVEN**: код готов, офлайн-E2E ещё не пройден, LIVE не запускался |

## H. Следующие шаги (уже выбранные, без запроса подтверждения)

1. Локальный fake OpenAI-сервер + E2E двумя backend-процессами (SQLite/HTTP/SSE/tools/kill/resume/
   verifier). Прогнать негативные случаи из раздела 6 задания.
2. После `LEVEL7_FREE_E2E = PASS` — выставить `execution-manifest.json` в `ARMED` и запустить
   один Pod только при действующем разрешении на новый batch/лимит.
3. Продолжать бесплатные направления: Computer Use E2E, verifier, память.

## I. Git

Изменены: `monitor.py` (пауза + manifest), `live_acceptance.py` (`--resume`),
`test_monitor_armed.py` (новый), evidence этой сессии. Force-push и destructive reset не выполнялись.

## J. Воспроизведение

```bash
cd artifacts/acceptance-127/live-qwen/monitor
../../../../apps/backend/.venv/Scripts/python.exe test_monitor_armed.py   # 8/8 PASS
cd ../..
../../../../apps/backend/.venv/Scripts/python.exe live_acceptance.py --self-test  # PASS
../../../../apps/backend/.venv/Scripts/python.exe live_acceptance.py --resume --live  # только при ARMED
```
