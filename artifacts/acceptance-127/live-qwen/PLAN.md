# Canalla 1.2.7 — план LIVE Qwen прогона (бюджет $2.00)

**Статус:** идёт ограниченная серия платных попыток (batch). Один Pod за раз, общий потолок $2.00.
**Разрешение владельца:** `I AUTHORIZE A PAID LIVE BATCH WITH A TOTAL GPU BUDGET OF USD 2.00`
(запись: `batch-authorization.json` в `%LOCALAPPDATA%\CanallaMonitor\`). Внутри этого бюджета
разрешение на каждую следующую попытку не запрашивается.
**Обновлено 2026-11-14:** два размещения подтверждены как рабочие для L40S; доказанный
`MODEL_RESPONSE` + `list_directory` в US-TX-3. Дефект пути `write_file` исправлен и покрыт
бесплатным E2E. Следующая попытка выполняется планировщиком автоматически.

## Текущее состояние (после попытки `c8966118`)

| Факт | Значение |
|---|---|
| Реальная модель | **подтверждена**: 2 `MODEL_RESPONSE` (`llamacpp`, `live:true`) |
| Реальные tool calls | **1 исполненный** (`list_directory`), результат возвращён модели |
| TaskDesk | не создан: `write_file` → `invalid_arguments`, затем `llm_unavailable` |
| Уровень автономности | **LEVEL 2** (ответ + исполненный model tool call) |
| Потрачено на попытку | $0.1144 (`c8966118`), Pod `w1cflixatbtw7q` удалён |
| Мониторинг | задача Windows `Canalla LLM GPU Monitor`, 2 проверки/мин = 30 с, работает |
| Production 1.2.6 | не изменялась (users 2 / chats 54 / messages 120 / devices 63 / tasks 30) |

### Что исправлено после `c8966118`

* аргументы tool call: 400 000 символов, содержимое файла: 200 000;
* `write_file mode="append"` — безопасная запись частями с атомарным результатом (host +
  `WriteArgs`), потому что один ответ модели всё равно ограничен её выходным бюджетом;
* `invalid_json` / `content_too_large` / `invalid_arguments` различаются и несут понятное
  объяснение; цикл продолжается, и модель может исправить вызов;
* относительный путь (`main.py`) привязывается к рабочему каталогу, а не отвергается host'ом;
* аргументы сломанного tool call больше не попадают в следующий запрос к модели (иначе
  llama.cpp отвечал не-2xx — это и был `llm_unavailable`);
* planner получает до 2048 токенов, ограниченных окном 32768;
* acceptance больше не просит web-режим (TinyFish не настроен: каждый вызов — гарантированный отказ).

Бесплатное доказательство: `apps/backend/tests/test_127_write_path.py` (реальный executor,
реальный оркестратор, файл > 72 KB, восстановление после обрезанного JSON, сборка файла частями).

Исполнитель: `artifacts/acceptance-127/live-qwen/live_acceptance.py`.
Подготовка: `--prepare` (read-only). Выбор GPU: `gpu_probe.py` (read-only, **по всем размещениям**).
Страховка: `scripts/live_qwen_watchdog.py` (внешний процесс, `SetThreadExecutionState`).

## Что изменилось в выборе размещения

`gpu_probe.py` больше не знает один том. Он читает:

1. **все** Network Volumes аккаунта и дата-центр каждого (`GET /v2/network-volumes`);
2. реестр реплик (`docs/replica-*.json`) и спрашивает `app.compute.replicas`, содержит ли **этот**
   добавленный том именно эту модель, хеш и runtime;
3. каталог GPU один раз и отдаёт его продуктовой политике `app.compute.candidates`, где
   отказанная реплика пропускается, а причина остаётся в аудите.

Проверенные размещения на 2026-11-14:

| Том | DC | Реплика | Доступные совместимые GPU (на момент чтения) |
|---|---|---|---|
| `uwgeaie5b0` (`orcarouter-storage`) | US-TX-3 | **рабочая**: реальный ответ модели и `list_directory` в `c8966118` | L40S доступна периодически |
| `y92bt25ru8` (`orcarouter-storage-mo1`) | US-MO-1 | **verified**, real GPU answer 2026-10-13 | 03:52/04:15 — L40S LOW, A100 PCIe, A100 SXM, H100 SXM; **07:45 — только H100 $4.39** |

Доступность «дышит»: за 23 минуты L40S вернулся в US-TX-3, а к 07:45 ушёл из US-MO-1. Поэтому прогон
не полагается на конкретный DC: цена и доступность перечитываются **непосредственно перед** созданием
Pod, а при отсутствии карты ставится `CAPACITY_UNAVAILABLE` без ожидания мощности.

Политика этого прогона (пины, не подсказки):

* **том** — только `y92bt25ru8` или `uwgeaie5b0`; любой другой том даёт `PLACEMENT_NOT_APPROVED`;
* **карта** — только `NVIDIA L40S`; более дешёвая или дорогая карта не подставляется;
* оба размещения разрешены; при равной доступности предпочитается доказанная реплика, а кандидат
  без записи о реальном ответе проходит настоящий `serve_selftest` внутри того же Pod.

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

## Global volume: состояние на 2026-11-14

Провайдер **открыл** программный путь: в живом контракте есть `/v2/global-volumes`,
`/v2/global-volumes/{id}` и вид монтирования `global` (до 1 глобального тома на Pod, без привязки
к DC, и для GPU, и для CPU Pod). Это отменяет вывод от 25.09.2026, где API отвечал
`Network volume "<id>" not found`.

Но совместимость с llama.cpp **не доказана**, а статический анализ говорит «сейчас нет»:

* измеренный `start-llm.sh` пишет `llama-server.log` и `llama-server.pid` в корень тома — на
  объектном хранилище это нежелательная запись;
* у глобального тома нет блокировок, atomic rename, hard links и permission bits;
* mmap 20.8 GB GGUF поверх объектного слоя и startup latency **не измерялись**.

Поэтому `app.compute.storage.check_compatibility` для глобального режима возвращает
`storage_unverified` (или `storage_incompatible_runtime`, пока launch path пишет в том), а не
«готово». Путь к глобальному тому описан и оценён, но не выбран для этого прогона.

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
Выбранное размещение передаётся изолированному backend как `RUNPOD_NETWORK_VOLUME_ID` +
`RUNPOD_DATACENTER` + `RUNPOD_STORAGE_MODE`, поэтому backend не возвращается к дефолтному DC.

## Изоляция

`ALEX_ACCEPTANCE_RUN=1`, отдельные `ALEX_DEVICE_DIR`, `ALEX_DEVICE_CREDENTIAL_TARGET`,
`DATABASE_URL`, workspace, backend, host, журналы. Родительский процесс harness получает тот же
изолированный `DATABASE_URL` **до** импорта приложения, поэтому чтение журнала не может попасть в
production БД.

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
