# Canalla 1.2.7 — план LIVE Qwen прогона (бюджет $2.00)

**Статус:** подготовка завершена. Платный запуск **не выполнялся**.
**Разрешение владельца:** требуется отдельно; текст задания им не является.
**Внешний блокер на 2026-11-14:** в дата-центре Network Volume нет ни одной подходящей GPU.

Исполнитель: `artifacts/acceptance-127/live-qwen/live_acceptance.py`.
Подготовка: `--prepare` (read-only). Выбор GPU: `gpu_probe.py` (read-only).
Страховка: `scripts/live_qwen_watchdog.py` (внешний процесс, `SetThreadExecutionState`).

## Сценарий (один запрос, без подсказок по ходу)

TaskDesk: SQLite, создание/список/поиск без учёта регистра/статусы, веб-интерфейс, API, тесты,
локальный запуск. Модель сама выбирает архитектуру, инструменты, порядок и исправления.

## Рассчитанные пределы (`app/compute/cost_policy.py`)

| Параметр | Значение |
|---|---|
| Бюджет | **$2.00** (верхний предел, не цель) |
| Максимальное биллинговое время | **90 минут** (5400 с) |
| Целевое рабочее время | **80 минут** (4800 с) |
| Минимум для осмысленного запуска | **45 минут** (2700 с) |
| Termination grace | 30 с |
| Watchdog margin | 10 с |
| Stop request при 90 мин | 5360 с |
| Ожидание capacity | **60 с**, без `--capacity-timeout 1200` |

Две независимые границы, побеждает меньшая:

```
budget_billed = floor(2.00 / (rate/3600) / 60) * 60
max_billed    = min(budget_billed, 5400)
hard_wall     = max_billed - 30
stop_request  = hard_wall - 10
target_work   = min(4800, hard_wall)
```

Примеры (цена — только для расчёта, реальная берётся из каталога перед запуском):

| Карта | Цена/ч | Billed | Работа | Worst case |
|---|---|---|---|---|
| A40 48 GB | $0.59 | 5400 с (90 мин, лимит политики) | 80 мин | **$0.885** |
| L40S 48 GB | $1.09 | 5400 с | 80 мин | **$1.635** |
| A100 80 GB | $2.09 | 2880 с (48 мин) | 47.5 мин | **$1.986** |
| H100 80 GB | $2.89 | 2400 с (40 мин) | 39.5 мин | **$1.926** → ниже минимума 45 мин, **отказ** |

При $2.99 и дороже допустимое окно становится меньше 45 минут → `NO_MEANINGFUL_WORK_WINDOW`,
Pod не создаётся.

## Внешний блокер (проверено read-only 2026-11-14)

- Network Volume `uwgeaie5b0` (`orcarouter-storage`, 50 GB) — **US-TX-3**, type `STANDARD`
  (региональный, монтируется только в своём DC).
- В каталоге сейчас **нет ни одной NVIDIA Secure GPU ≥48 GB с ненулевой доступностью в US-TX-3**.
- L40S $1.09 доступен только в **US-TX-4**, A40 $0.59 — в CA-MTL-1/EU-SE-1, RTX 6000 Ada — в
  US-IL-1/US-KS-2/US-WA-1.
- Следовательно продукт ответит `no_compatible_gpu` / `gpu_unavailable`. Это **внешний**
  блокер: второй Volume и перезаливка ~20 GB весов не выполняются без отдельного решения.

`--live` проверяет это до создания Pod и завершается `CAPACITY_UNAVAILABLE` за секунды, без
ожидания мощности.

## Критерии PASS

1. `provenance.classify(...) == REAL_MODEL_CONFIRMED`.
2. `COMPLETED` + `verification.evidence_ok`.
3. Реальные файлы приложения.
4. Был проваленный тестовый прогон, затем прошедший (detect → fix → retest).
5. HTTP 200 и SQLite с непустой таблицей задач.
6. GUI-скриншоты: от модели (`browser_gui`) и независимый от harness (Playwright).
7. Нет дублей tool payload.
8. `pods == []` после прогона, расход ≤ $2.00.
9. Production 1.2.6 и device identity не изменились.

## Команды

```bat
python artifacts\acceptance-127\live-qwen\live_acceptance.py --self-test
python artifacts\acceptance-127\live-qwen\gpu_probe.py
python artifacts\acceptance-127\live-qwen\live_acceptance.py --prepare
python artifacts\acceptance-127\live-qwen\live_acceptance.py --plan --budget-usd 2.00
python artifacts\acceptance-127\live-qwen\live_acceptance.py --live --budget-usd 2.00 ^
       --confirm "I AUTHORIZE ONE PAID POD"
```

Без `--budget-usd` и без точной фразы `--live` не запускается; бюджет выше $2.00 отклоняется.

## Изоляция

`ALEX_ACCEPTANCE_RUN=1`, отдельные `ALEX_DEVICE_DIR`, `ALEX_DEVICE_CREDENTIAL_TARGET`,
`DATABASE_URL`, workspace, backend, host, журналы. Родительский процесс harness получает тот же
изолированный `DATABASE_URL` **до** импорта приложения, поэтому чтение журнала не может попасть в
production БД.

## Evidence (`final-live/`)

`preflight.json`, `authorization.json`, `cost-policy.json`, `gpu-selection.json`,
`watchdog-events.json`, `pod-lifecycle.json`, `model-identity.json`, `model-responses.jsonl`,
`tool-calls.jsonl`, `tool-execution.jsonl`, `provenance-verdict.json`, `task-state.json`,
`verification.json`, `database-check.json`, `http-check.json`, `gui-evidence/`,
`repair-evidence/`, `production-before.json`, `production-after.json`, `cleanup.json`,
`billing-summary.json`, `final-summary.json`.

Файлов с фиктивными данными нет: при отсутствии прогона `final-summary.json` честно говорит
`LIVE NOT RUN`.

## После прогона

- `pods == []` подтверждается провайдером, иначе статус `CRITICAL`, а не `SUCCESS`;
- отчёт `artifacts/REPORT-canalla-127-QWEN-LIVE.md` заполняется реальными фактами;
- `FULL LIVE AUTONOMY = VERIFIED` только при `REAL_MODEL_CONFIRMED` и всех критериях.
