# CANALLA LLM — LEVEL 7 RESUME: ИСПРАВЛЕНИЕ И РЕЗУЛЬТАТ ПРОГОНА

**Пропуск планировщика устранён, resume-цикл теперь вызывает `verify_result`. Открыт следующий дефект: `CHECK_REFUSED` на путях workspace. LIVE не запускался.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD | **`515a354`** (запушен, origin синхронизирован) |
| Баланс RunPod | **$13.0362274796**, Pod **0**, фон $0.01/ч |
| Расход этой сессии | **$0.00** (платных операций не было) |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Production 1.2.6 | не изменена |
| `LEVEL7_FREE_E2E` | **FAIL** (прогресс: resume вызывает `verify_result`) |
| LEVEL 7 | **NOT_PROVEN** |

---

## A. Цель блока

Устранить дефект, из-за которого resumed-задача не получала планировочного раунда, довести
`free_resume_e2e.py` до `verify_result` → один `COMPLETED` → verifier PASS.

## B. Что исправлено

### B.1. Пропуск планировщика — исправлено (проверено прогоном)

**Симптом:** `RESUMED → RESUMED → MODEL_RESPONSE → FAILED`, `last_error = verification_required`,
`COMPLETED = 0`, хотя файлы созданы и задача продолжалась.

**Причина (в коде, не в отчёте):** `ToolOrchestrator._goal_satisfied()` возвращал `True` по фактам
файлов. В `prepare()` это выставляет `skip_planner = True`, цикл планирования не запускается,
`verify_result` моделью не запрашивается. Контроллер затем корректно отказывает в завершении.

**Исправление** (`apps/backend/app/tools/orchestrator.py`): задача, изменившая файлы, считается
«goal satisfied» только если `verification.evidence_ok == true` **или** `verification.tests == "passed"`.

**Подтверждение эффекта:** после исправления в прогоне `271f0ded…` resumed-фаза выполнила
**на 2 инструмента больше** (`completed_runs` 3 → 5), и среди них — реальный `verify_result`.

### B.2. Fake inference: абсолютные пути

`fake_llm.py` теперь собирает цели проверки из абсолютных путей в транскрипте (рекурсивный обход
запроса), а не из относительных имён, которые резолвились от cwd backend.

## C. Результат последнего прогона (факты, не пересказ)

| Проверка | Факт |
|---|---|
| Backend A | `/health` 200, регистрация 201, host loop спарен |
| Реальные инструменты до kill | **3** `write_file`: `part1.txt`, `part2.txt`, `part3.txt` |
| Статус перед kill | **EXECUTING** |
| Backend A | exit **1**, stream → `ConnectionReset` |
| Статус после смерти | **EXECUTING** (восстановим) |
| Backend B | другой PID (15104), тот же порт/БД/data root |
| Host B | новый PID (1900) |
| `task_id` | **тот же** (`271f0ded-184c-4d4b-94f0-92c977ee87ea`) |
| Новый прогресс после рестарта | **есть** (3 → 5 завершённых tool runs) |
| `verify_result` | **выполнен** (это и есть новый tool call после restart) |
| Файлы | part1, part2, part3, final — все существуют |
| `COMPLETED` событий | **0** |
| Вердикт | **FAIL** |

## D. Открытый дефект (блокирующий PASS)

Из tool ledger последнего прогона:

```
verify_result  completed  ok=False
evidence: {'total': 2, 'ok': 0, 'failed': 2,
           'kinds': {'CHECK_REFUSED': {'ok': 0, 'failed': 2}}}
```

Обе проверки отклонены guard'ом workspace-roots в `verify_result` (`_roots` / `_inside`), поэтому
`evidence_ok = false`, задача завершилась `FAILED / verification_required`.

Это не дефект сценария: реальные файлы лежат в workspace задачи, но guard на resume-пути их не
признаёт. Требуется либо включить `context.workspace.primary_root` в `_roots`, либо нормализовать
пути перед сравнением (как уже сделано в `security_assessment.scope` после Linux-гейта).

## E. Сопутствующие наблюдения

- `write_file final.txt` один раз завершился `failed / backend_interrupted` (прерванный tool call),
  затем `completed`. Файл создан **один раз**, дубликата нет — поведение корректное, нужно закрепить
  регрессией.
- Двойное событие `RESUMED` (два подряд) зафиксировано, но не подтверждено как дефект: требует
  отдельной проверки на дублирование при повторном resume.

## F. Тесты

| Проверка | Результат |
|---|---|
| `free_resume_e2e.py` (полный прогон) | **FAIL** — см. раздел D |
| `monitor/test_monitor_armed.py` | **8/8 PASS** (ранее в этой сессии) |
| `live_acceptance.py --self-test` | **PASS**, 30 проверок |
| `test_127_level7_negative.py` | 3 passed |
| `test_batch_budget.py` | 11/11 |

## G. Деньги и инфраструктура

- Платных Pod: **0**, расход **$0.00**, баланс $13.0362274796, резерв $8.50 не тронут.
- Ledger серии не изменялся: $0.4298, Pod 3/3, `PAID_BATCH_EXHAUSTED`.
- Новый batch не создавался, лимиты не сбрасывались, watchdog не отключался.

## H. Production

Не изменялась: users 2, chats 54, messages 120, paired_devices 63, local_tasks 30, integrity ok,
FK 0, alembic 0016. Тесты работали в `%TEMP%` с изолированной БД, data root, device dir и портами.

## I. Git

```
515a354 Require a real check before a changed task looks finished
71d3f5b Report the free LEVEL 7 E2E session
e6099e3 Add the fake inference server and the free resume runner
9ad46b4 Checkpoint the LEVEL 7 free E2E plan and tool schemas
```

Force-push и destructive reset не выполнялись.

## J. Следующий исполняемый шаг

1. Диагностика `CHECK_REFUSED`: вывести отклонённые цели и `_roots(context)` в `verify_result`.
2. Исправить сопоставление путей на resume-пути.
3. Прогнать `free_resume_e2e.py` до `ok=true`, одного `COMPLETED`, verifier PASS.
4. Регрессия на прерванный tool call (нет дубликата файла) и на двойное `RESUMED`.
5. Только после зелёного E2E — ARMED manifest и LIVE LEVEL 7 в пределах действующего бюджета.

## K. Воспроизведение

```bash
apps/backend/.venv/Scripts/python.exe artifacts/acceptance-127/live-qwen/free_resume_e2e.py
cd artifacts/acceptance-127/live-qwen/monitor && ../../../../apps/backend/.venv/Scripts/python.exe test_monitor_armed.py
cd ../.. && ../../../../apps/backend/.venv/Scripts/python.exe live_acceptance.py --self-test
```

## L. Правило отчётности

Отчёт создаётся после каждого существенного блока, без напоминаний: `.md` + самодостаточный
офлайн `.html` на Рабочем столе и побайтовые копии в `artifacts/reports/`, с проверкой хешей.
