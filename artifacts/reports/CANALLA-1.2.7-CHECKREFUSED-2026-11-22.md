# CANALLA LLM — CHECK_REFUSED ИСПРАВЛЕН, RESUME УПИРАЕТСЯ В ТРЕБОВАНИЕ ТЕСТОВ

**`verify_result` больше не отклоняет файлы workspace (`ok=true`). Открыт следующий барьер LEVEL 7: кодирующая задача обязана иметь пройденный прогон тестов.**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent` / **`653c967`** |
| Баланс RunPod | **$13.0362274796**, Pod **0**, фон $0.01/ч |
| Расход сессии | **$0.00** |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Production 1.2.6 | не изменена |
| `LEVEL7_FREE_E2E` | **FAIL** (барьер сместился, дефект guard'а закрыт) |
| LEVEL 7 | **NOT_PROVEN** |

---

## A. Цель блока

Устранить `CHECK_REFUSED` в `verify_result`, не ослабляя guard, и довести
`free_resume_e2e.py` до полного PASS.

## B. Диагностика CHECK_REFUSED (факты из tool ledger)

Добавлена диагностика (по требованию): для каждого отказа записываются `kind`, исходный `target`,
канонический путь, доверенные `roots`, `task_id` и причина. Пример из прогона:

```
target    = "part1.txt"
canonical = "...\apps\backend\part1.txt"
roots     = ["...\Temp\canalla-free-e2e-<id>\workspace"]
reason    = outside_workspace_roots
```

Причина: `write_file` принимал файл, а `verify_result` сравнивал **несопоставимые формы путей** —
`os.path.commonpath` не разрешает junction'ы и чувствителен к регистру/разделителям, а цель к тому
же была относительной и резолвилась от cwd backend.

## C. Исправление (без ослабления безопасности)

`apps/backend/app/tools/agent_tools.py`:

- единая канонизация обеих сторон: `normcase(realpath(abspath(...)))`;
- сравнение вхождения осталось прежним — файл вне workspace по-прежнему получает `CHECK_REFUSED`;
- отказ теперь несёт полную диагностику (цель, канонический путь, roots, task_id);
- fake-модель получает реальный workspace от раннера и передаёт **абсолютные** цели.

**Подтверждение:** в tool ledger `verify_result` теперь `ok=True`, `refused_detail=[]`.

## D. Что показали прогоны после исправления

| Прогон | Итог |
|---|---|
| кодирующий промпт | 3 `write_file` до kill, задача `EXECUTING`, kill, рестарт, тот же `task_id`, 5 завершённых runs, **`verify_result ok=true`**, финал `FAILED / verification_required` |
| файловый промпт (эксперимент) | задача **COMPLETED за 2 действия** — до kill, поэтому для resume непригодна |

Состояние задачи в последнем кодирующем прогоне:

```
status=FAILED  error=verification_required
verification.evidence_ok = True   tests = stale
plan_has_verification = True      verification_stale = False
```

## E. Оставшийся барьер (продуктовое правило, не дефект)

`LocalTaskController.final_review()` требует для кодирующего запроса `verification.tests == "passed"`.
Это корректное и намеренное правило: приложение не считается готовым без прогона тестов.
Сценарий fake-модели пишет файлы и вызывает `verify_result`, но **не запускает тесты**, поэтому
контроллер отказывает в `COMPLETED`.

Ослаблять критерий нельзя. Нужно расширить сценарий: fake-модель пишет тестовый файл и запускает
`run_python` с pytest, после чего `tests` становится `passed` и задача может завершиться.

## F. Сопутствующие проверки

- **Прерванный `write_file`**: один вызов завершился `backend_interrupted`, затем повторный —
  `completed`; файл создан один раз, дубликата нет. Поведение корректное, нужна регрессия.
- **Двойное `RESUMED`**: зафиксировано, но не подтверждено как дефект; проверка на конкурирующие
  resume-запросы ещё не написана.

## G. Тесты

| Проверка | Результат |
|---|---|
| `free_resume_e2e.py` | **FAIL** — см. раздел E |
| `monitor/test_monitor_armed.py` | **8/8 PASS** |
| `live_acceptance.py --self-test` | **PASS** (30) |
| `test_127_level7_negative.py` | 3 passed |
| `test_batch_budget.py` | 11/11 |

## H. Деньги, Pod, production

- 0 Pod, расход $0.00, баланс $13.0362274796, резерв $8.50 не тронут.
- Ledger $0.4298 из $5.00, Pod 3/3, `PAID_BATCH_EXHAUSTED`; новый batch не создавался.
- Production: 2/54/120/63/30, integrity ok, FK 0, alembic 0016 — без изменений.

## I. Git

```
653c967 Compare workspace paths canonically in verify_result
b2be584 Report the resume fix and the remaining workspace guard defect
515a354 Require a real check before a changed task looks finished
```

## J. Следующий исполняемый шаг

1. Добавить в fake-сценарий шаг тестов: записать `test_smoke.py` и выполнить `run_python` с
   `python -m pytest` (сначала сверить схему аргументов `run_python`).
2. Прогнать E2E до `tests=passed`, `verify_result ok=true`, одного `COMPLETED`, verifier PASS.
3. Регрессии: прерванный tool call (нет дубля файла), конкурирующие resume-запросы.
4. После зелёного E2E — ARMED manifest и LIVE LEVEL 7 в пределах действующего бюджета.
