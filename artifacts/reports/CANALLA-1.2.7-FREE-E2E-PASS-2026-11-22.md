# CANALLA LLM — LEVEL7_FREE_E2E = PASS

**Бесплатный двухпроцессный resume пройден полностью: pytest → kill → restart → verify_result → одно COMPLETED.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent` / **`a8acce0`** |
| Баланс RunPod | $13.0167830352, Pod **0**, фон $0.01/ч |
| Расход сессии | **$0.00** |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Production 1.2.6 | не изменена |
| **`LEVEL7_FREE_E2E`** | **PASS** |
| LEVEL 7 | **NOT_PROVEN** (нужен LIVE с настоящей моделью) |

---

## A. Первопричина потери `tests_passed` (доказана, а не предположена)

`verification.tests` становится `passed` только через `run_python` с `-m pytest` и `exit_code=0`;
любой последующий `write_file` сбрасывает флаг в `stale`.

После рестарта fake-модель, ориентируясь на **счётчик вызовов планировщика**, «доезжала» на шаг
назад и повторяла запись уже после pytest — продукт корректно помечал проверку устаревшей.
Счётчик дрейфовал на один вызов, потому что планировщик вызывается не только на каждый tool call.

**Исправление:** fake-модель принимает решение по **транскрипту**. Как только в разговоре есть
собственный вывод pytest (`test session starts`), новые записи не предлагаются — остаётся только
независимая проверка. Это не ручная запись `passed` в БД: флаг по-прежнему ставит продукт по
реальному `exit_code` pytest.

## B. Результат полного прогона

| Критерий | Факт |
|---|---|
| Backend A | запущен, host спарен, `/health` 200 |
| Реальные инструменты | `app.py`, `test_smoke.py`, `part3.txt`, `final.txt` |
| Настоящий pytest | выполнен через host loop, `exit_code=0` |
| Статус на момент kill | **VERIFYING** |
| Backend A | exit 1, stream `ConnectionReset` |
| Backend B | новый PID 16180 |
| Host B | новый PID 18424 |
| `task_id` | **тот же** (`60ad5d0b-…`) |
| Новый прогресс после рестарта | **есть** (6 завершённых tool runs, `verify_result`) |
| `verification.tests` | **passed** |
| `evidence_ok` | **true** |
| `COMPLETED` событий | **1** |
| Созданные файлы | `app.py`, `test_smoke.py`, `part3.txt`, `final.txt` |
| Вердикт | **PASS** |

## C. Что проверяет тест (не фиктивность)

- Настоящий pytest исполняется на хосте и его `exit_code` управляет `tests_passed`.
- `test_smoke.py` проверяет поведение (`add(2,3) == 5`), а не существование файла.
- Kill происходит только после фиксации pytest, при статусе `VERIFYING`.
- После рестарта новое действие (`verify_result`) исполняется реально, `task_id` сохраняется.
- `COMPLETED` ровно одно.
- Любая последующая запись сделала бы `tests` снова `stale` — механизм не отключён.

## D. Ранее закрытые дефекты (в этой серии)

1. Пропуск планировщика на resume (`_goal_satisfied` по фактам файлов) — исправлено.
2. `CHECK_REFUSED` на корректных путях workspace — канонизация обеих сторон в `verify_result`.
3. Потеря `tests_passed` после рестарта — сценарий больше не пишет после pytest.

## E. Тесты

| Проверка | Результат |
|---|---|
| `free_resume_e2e.py` | **PASS** |
| `monitor/test_monitor_armed.py` | 8/8 PASS |
| `live_acceptance.py --self-test` | PASS (30) |
| `test_127_level7_negative.py` | 3 passed |
| `test_batch_budget.py` | 11/11 |

## F. Деньги / Pod / production

0 Pod, $0.00, баланс $13.0167830352, резерв $8.50 не тронут, ledger $0.4298 из $5.00.
Production: 2/54/120/63/30, integrity ok, FK 0, alembic 0016.

## G. Git

```
a8acce0 Pass the free LEVEL 7 resume E2E
151f4d7 Report the real-pytest resume run
acddfa2 Run real pytest in the resume E2E
```

## H. Следующие шаги (по мастер-директиве)

1. Негативные регрессии к E2E: pytest FAIL, запись после теста сбрасывает проверку, потерянный
   checkpoint, повторный/конкурентный resume, прерванный tool call, двойное COMPLETED.
2. Desktop staging 1.2.7: настоящий запуск приложения, чат, инструменты, отображение задачи и
   восстановление через GUI (уровень C приёмки).
3. LIVE LEVEL 7 на настоящей Qwen при подтверждённом бюджете и ARMED manifest, затем удаление Pod
   и подтверждение прекращения тарификации.
4. Computer Use, Research, Coding, Repair, Memory, Security — с проверкой через Desktop.
