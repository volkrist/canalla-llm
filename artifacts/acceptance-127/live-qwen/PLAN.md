# Canalla 1.2.7 — план единственного LIVE Qwen прогона

**Статус:** подготовка завершена, платный запуск **не выполнялся**.
**Разрешение владельца:** требуется отдельно.

Исполнитель: `artifacts/acceptance-127/live-qwen/live_acceptance.py` (self-test / preflight / plan /
live). Страховка: `scripts/live_qwen_watchdog.py` (внешний процесс).

## Цель

Доказать, что настоящая Qwen (а не scripted planner) самостоятельно проходит цикл:

```
RESEARCH → PLAN → BUILD → RUN → TEST → GUI → FIX → RETEST → VERIFY → COMPLETE
```

на одной пользовательской задаче, с реальными инструментами и реальными side effects.

## Сценарий (один запрос, без подсказок по ходу)

> «Создай на моём компьютере приложение TaskDesk для управления задачами: локальная база,
> добавление, поиск, статусы, простой веб-интерфейс и тесты. Запусти его, проверь интерфейс,
> найди и исправь дефект, подтверди результат.»

Всё остальное — дело модели: она выбирает инструменты, план и порядок. Harness задаёт только
цель, workspace, бюджет и критерии PASS.

## Жёсткие пределы (из `app/compute/cost_policy.py`)

| Параметр | Значение | Почему |
|---|---|---|
| GPU Pod | **не более 1**, один за раз | правило цикла; второй Pod — аварийная остановка |
| Тариф | $1.09/ч (L40S 48 GB), проверяется по факту на Pod | каталог может измениться |
| Биллинговое окно | **480 с** (округление вверх до минуты) | 480/3600 × 1.09 = **$0.14533** ≤ $0.15 |
| Рабочее окно (hard wall) | **450 с** | 480 − 30 с на подтверждение остановки |
| Watchdog | stop на **440 с**, проверка каждые 5 с | 10 с запаса до hard wall |
| Резерв бюджета | $0.004667 | плюс storage тома ~$0.001 за прогон |
| Ожидание capacity | **не более 60 с**, без `--capacity-timeout 1200` | правило 1.2.6 |
| Повторный Pod | **запрещён** при FAIL | один прогон |

Проверка модели расходов:

```
budget 0.15 / (1.09/3600) = 495.4 s  -> 480 s (округление вниз до минуты)
480 s billed = $0.145333
480 - 30 (termination grace) = 450 s usable
450 - 10 (watchdog margin)   = 440 s stop request
```

Прежняя формулировка «480 с работы = $0.1453 ≤ $0.15» игнорировала 30 с остановки: при 480 с
*работы* счёт мог составить 510 с ≈ $0.1545. Теперь 480 с — это **биллинговое** окно, а работать
можно 450 с.

## Критерии PASS (все обязательны)

1. `provenance.classify(...) == REAL_MODEL_CONFIRMED` — именованный live-провайдер, response id,
   связанные tool calls, ledger-дайджесты, независимая верификация.
2. Файлы приложения созданы model-origin вызовами.
3. `pytest` реально запускался: был **проваленный** прогон и затем **прошедший**.
4. Приложение реально отвечает по HTTP (harness поднимает его сам и проверяет `/`).
5. GUI реально управлялся, остались скриншоты.
6. Задача `COMPLETED`, `verification.evidence_ok = true`.
7. Нет дублей tool payload (`input_digest`).
8. Pod остановлен, `pods == []`, расход ≤ $0.15.
9. Пользовательские данные и device_id установленной 1.2.6 не изменились.

**FAIL** — если хотя бы один пункт не подтверждён фактом.

## Что проверяется ДО Pod (бесплатно)

`python artifacts/acceptance-127/live-qwen/preflight.py` (read-only):

- Gateway `/health` = ready, `pods []`;
- баланс RunPod читается, стартовое значение записано;
- установленная 1.2.6 жива, `/health` = 200, версия совпадает;
- device online, Computer Ready, Tor Ready;
- policy бюджет **feasible** (см. таблицу);
- watchdog self-test PASS;
- provenance-модуль импортируется и знает live-провайдеров;
- свободное место на диске.

Любой пункт `BLOCKED` → Pod **не создаётся**.

## Команды

```bat
python artifacts\acceptance-127\live-qwen\live_acceptance.py --self-test
python artifacts\acceptance-127\live-qwen\live_acceptance.py --preflight
python artifacts\acceptance-127\live-qwen\live_acceptance.py --plan
python artifacts\acceptance-127\live-qwen\live_acceptance.py --live --confirm "I AUTHORIZE ONE PAID POD"
```

Без точной фразы `--live` отказывается запускаться. Внешний watchdog запускается harness'ом
раньше отправки сообщения, ждёт появления Pod не более 90 с и останавливает его на 440 с даже
если backend завис.

## Изоляция

- отдельный каталог данных, отдельная БД, свободный порт;
- `ALEX_ACCEPTANCE_RUN=1`, `ALEX_DEVICE_CREDENTIAL_TARGET=Canalla Acceptance/live-<nonce>`,
  `ALEX_DEVICE_DIR` во временном каталоге;
- установленная 1.2.6 не останавливается, не переспаривается и не мигрирует;
- namespace удаляется после прогона.

## Evidence

```
artifacts/acceptance-127/live-qwen/
  preflight.json          бесплатная проверка
  self-test.json          проверка самого harness и бюджета
  run/summary.json        трейс, provenance verdict, проверки, Pod, расход
  run/watchdog.json       что видел и когда остановил внешний watchdog
```

## После прогона

- `pods == []` подтверждено;
- фактический расход = дельта баланса за вычетом фонового storage тома;
- отчёт `artifacts/REPORT-canalla-127-final-readiness.md` обновляется реальными фактами;
- статус меняется на `FULL LIVE AUTONOMY = VERIFIED` **только** при `REAL_MODEL_CONFIRMED`
  и всех критериях PASS.
