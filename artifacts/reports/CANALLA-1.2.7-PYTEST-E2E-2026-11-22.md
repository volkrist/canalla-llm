# CANALLA LLM — РЕАЛЬНЫЙ PYTEST В RESUME E2E: ОТЧЁТ

**pytest действительно выполняется через настоящий host loop. Остаток: после рестарта `verification.tests` возвращается в `stale`.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent` / **`acddfa2`** |
| Баланс RunPod | $13.0362274796, Pod **0**, фон $0.01/ч |
| Расход сессии | **$0.00** |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Production 1.2.6 | не изменена |
| `LEVEL7_FREE_E2E` | **FAIL** (ближе: pytest исполняется, evidence_ok=true) |
| LEVEL 7 | **NOT_PROVEN** |

---

## A. Что установлено по схемам (не по предположению)

- `verification.tests = "passed"` ставится **только** в `LocalTaskController._capture_facts`: нужен
  `run_python`/`run_process`/`run_powershell`, чей `argv` содержит `-m pytest` (или purpose
  упоминает pytest), и `exit_code == 0`; тогда `tests_passed = True`, `verification_stale = False`.
- Любой последующий `WRITE_TOOLS` сбрасывает `tests_passed = False` и `verification_stale = True`.
- `final_review()` для кодирующего запроса требует `verification.tests == "passed"`.
- Схема `run_python`: `argv`, `cwd`, `timeout_seconds`, `purpose`, `wait`.

## B. Что реализовано

`fake_llm.py` — детерминированный сценарий:

1. `write_file app.py` (`def add(a, b)`);
2. `write_file test_smoke.py` (реальная проверка `add(2,3) == 5`);
3. `write_file part3.txt`, `write_file final.txt`;
4. `run_python {argv:["-m","pytest","-q"], cwd:<workspace>, purpose:"verify project tests"}`;
5. `verify_result` по абсолютным путям workspace;
6. завершение.

Планировщик отвечает с задержкой 1.2 с, чтобы финальная проверка не успевала проскочить до kill.

`free_resume_e2e.py` — прерывание по **факту** завершённого pytest (прямой read-only запрос к
изолированной SQLite, опрос 0.2 с), а не по счётчику запусков.

## C. Фактический результат прогона

| Проверка | Факт |
|---|---|
| Backend A | запущен, host спарен, регистрация 201 |
| До kill | 6 завершённых tool runs, включая **настоящий pytest** |
| Статус на момент kill | **VERIFYING** |
| Backend A | exit 1, stream `ConnectionReset` |
| Backend B | новый PID 16548, та же БД/data root |
| Host B | новый PID 15664 |
| `task_id` | тот же (`b6eab8c9-…`) |
| Новый прогресс после рестарта | **есть** (6 runs, verify_result) |
| `evidence_ok` | **true** |
| `verification.tests` | **stale** |
| `COMPLETED` | **0** |
| Вердикт | **FAIL** |

## D. Оставшийся дефект

После рестарта `tests` возвращается в `stale`, хотя pytest уже завершился успешно. `stale`
выставляется только записью (`WRITE_TOOLS`), значит после рестарта произошёл write, либо
`tests_passed` не был зафиксирован в checkpoint и был потерян при восстановлении контекста.

Следующий шаг диагностики (точный): сравнить `facts["tests_passed"]` и `row.checkpoint` до kill и
после рестарта; проверить, не входит ли `tests_passed`/`verification_stale` в сохраняемый checkpoint.
Если флаг не переживает рестарт — сохранять его в checkpoint вместе с `digests_completed` (это
штатное место для восстановимого состояния, а не ручная запись `passed` в БД).

## E. Тесты

| Проверка | Результат |
|---|---|
| `free_resume_e2e.py` | **FAIL** (см. раздел D) |
| `fake_llm.py` | syntax ok, сервер отвечает |
| `monitor/test_monitor_armed.py` | **8/8 PASS** |
| `live_acceptance.py --self-test` | **PASS** (30) |
| `test_127_level7_negative.py` | 3 passed |
| `test_batch_budget.py` | 11/11 |

## F. Деньги / Pod / production

- 0 Pod, $0.00, баланс $13.0362274796, резерв $8.50 не тронут.
- Ledger $0.4298 из $5.00, Pod 3/3, `PAID_BATCH_EXHAUSTED`.
- Production: 2/54/120/63/30, integrity ok, FK 0, alembic 0016.

## G. Git

```
acddfa2 Run real pytest in the resume E2E
75628fb Report the CHECK_REFUSED fix and the test-suite barrier
653c967 Compare workspace paths canonically in verify_result
```

## H. Следующий исполняемый шаг

1. Проверить, переживает ли `tests_passed` / `verification_stale` рестарт (сравнить `facts` и
   `checkpoint` до/после).
2. Если нет — включить их в сохраняемое состояние задачи (штатный checkpoint), не подменяя
   инструментальную проверку.
3. Прогнать E2E до `tests=passed`, одного `COMPLETED`, verifier PASS.
4. После зелёного E2E — ARMED manifest и LIVE LEVEL 7 в пределах действующего бюджета.
