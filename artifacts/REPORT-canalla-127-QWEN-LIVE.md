# Canalla 1.2.7 — отчёт о REAL QWEN LIVE ACCEPTANCE

> **Обновление 14.11.2026:** блокер «нет GPU в US-TX-3» больше не является блокером этого прогона.
> Найден и проверен второй региональный том (`y92bt25ru8`, US-MO-1) с **verified** копией модели,
> harness научился выбирать размещение по всем проверенным репликам, а провайдер открыл API
> глобальных томов. Подробности и цены — `artifacts/REPORT-canalla-127-storage-unblock.md`;
> актуальный план — `artifacts/acceptance-127/live-qwen/PLAN.md`.

## Попытка LIVE 2026-11-14 07:44–07:46 UTC

**HEAD:** `676c18d`. **Pod создано:** 0. **Расход:** $0.00.
**Вердикт:** **CAPACITY_UNAVAILABLE** — единственный непройденный гейт.

Запуск привязан к проверенной реплике `y92bt25ru8` (US-MO-1) и к карте **NVIDIA L40S**; первичный
том `uwgeaie5b0` (US-TX-3) **исключён** из выбора, потому что его измеренный manifest фиксирует
`serve_selftest: FAIL`. Тип размещения и SKU — пины, а не подсказки: `gpu_probe.py` отвергает
другой том (`PLACEMENT_NOT_APPROVED`), `live_acceptance.py` отказывается исполнять прогон на
другом томе без отдельного `--volume`.

| Гейт | Результат |
|---|---|
| Git branch/HEAD | `feature/canalla-1.2.7-autonomous-agent` / `676c18d`, синхронно с origin |
| RunPod API | доступен (каталог, тома, поды) |
| GPU в совместимом DC | **НЕТ**: L40S не имеет доступности в US-MO-1 (3 чтения за 42 с) |
| Цена в бюджете | L40S $1.09/ч, worst case 90 мин = $1.635 ✅ (но карта не бронируется) |
| Network Volume | `y92bt25ru8` существует, 50 GB, STANDARD, US-MO-1 |
| Model placement | `ready:true`, `last_verified 2026-10-13T12:23:41Z`, реальный ответ модели |
| Gateway | `/health` 200, `ready` |
| Изолированный backend | harness self-test **9/9 PASS** |
| Computer Connect | устройство online, совпадает с локальным |
| Production DB baseline | integrity `ok`, FK 0, users 2 / chats 54 / messages 120 / paired_devices 63 / local_tasks 30 |
| Device identity | `6413489d-6cae-4183-8446-a3b2339137dc` (не изменялась) |
| Watchdog | self-test **23/23 PASS** |
| Посторонние Pod | 0 |
| Stale acceptance процессы | 0 (работает только production sidecar) |
| Диск | 70.3 GB свободно |
| Mock вместо модели | нет: `LLM_PROVIDER=llamacpp`, provenance распознаёт live-провайдеров |

### Что именно показал каталог

`final-live/capacity-check.json`, три чтения (07:45:10, 07:45:31, 07:45:52 UTC):

| | |
|---|---|
| L40S $1.09 | доступен в **OC-AU-1, US-IL-1, US-TX-4**; в **US-MO-1 — NONE** |
| Совместимые карты в US-MO-1 | только **H100 80GB HBM3 $4.39/ч** (LOW) |
| Бюджет на H100 | $2.00 / $4.39 = **27 мин** максимум → ниже минимума 45 мин ⇒ `sufficient=false`, harness отказывается |
| Первичный том US-TX-3 | не используется: `serve_selftest: FAIL` (измерено 2026-09-26) |

Альтернативные DC с L40S не содержат проверенной копии модели, а Network Volume нельзя смонтировать
вне своего дата-центра. Поэтому ни один вариант не проходит одновременно «совместимость + готовность
модели + бюджет».

**Следующее действие:** повторить бесплатный read-only зонд, когда L40S вернётся в US-MO-1 (пул
менялся дважды за сутки), либо отдельно решить вопрос второй реплики/SKU — это платное
инфраструктурное действие и в текущем задании не выполняется.

## Постоянный мониторинг (установлен 14.11.2026)

Повторный ручной зонд больше не нужен: установлен автономный мониторинг.

| | |
|---|---|
| Планировщик | Windows Task Scheduler, задача **`Canalla LLM GPU Monitor`** |
| Расписание | каждые **10 минут**, без даты окончания (`PT10M`, `StopAtDurationEnd=false`) |
| Режим | one-shot `monitor.py --once`, без резидентного процесса |
| Один экземпляр | `MultipleInstances=IgnoreNew` + файловая блокировка |
| Состояние | `%LOCALAPPDATA%\CanallaMonitor\state.json`, `status.json`, `checks.jsonl`, `events.jsonl` |
| Разрешение на оплату | отдельный `authorization-grant.json` (вне репозитория), одноразовое |
| Self-test | **13/13 PASS** (включая: нет разрешения → нет Pod; использованное разрешение → отказ; ошибка провайдера ≠ «Pod нет»; второй экземпляр отклонён) |
| Проверено вживую | два одновременных запуска: один выполнил проверку, второй вернул `another_instance_running` |
| Первый платный запуск | **NOT ARMED** — разрешение владельца не выдано, Pod не создавался |

Каждый проход читает только четыре вещи: список Pod, том `y92bt25ru8`, доступность и цену
`NVIDIA L40S` в US-MO-1 и состояние сохранённого разрешения. Полный preflight выполняется только
когда GPU появилась и разрешение действительно. При исчезновении GPU между обнаружением и запуском
разрешение восстанавливается, а состояние возвращается в `WAITING_GPU`.

**Дата проверки:** 14 ноября 2026.
**Ветка:** `feature/canalla-1.2.7-autonomous-agent`.
**Исходный SHA:** `054decc` (CI 7/7 + Linux gate на `cfd7e14`).
**Итоговый SHA с зелёным CI:** `57e09dc` — **CI 7/7 success + Linux gate success**.
**Версия Canalla:** 1.2.6 (публичная, не изменялась).
**Статус LIVE:** **НЕ ЗАПУЩЕН** — нет подтверждения владельца на платный запуск и нет доступной
GPU в дата-центре модели. Платный Pod не создавался, расход **$0.00**.

---

## 1. Итог одной таблицей

| Пункт | Результат |
|---|---|
| Настоящая Qwen подключена | **НЕТ** (нет Pod) |
| Provider | RunPod → llama.cpp, конфигурация проверена в коде |
| Модель | `orcarouter-qwen38-27b-q5km`, context 32768 |
| GPU | **нет доступной** ≥48 GB в US-TX-3 |
| Стоимость GPU | $0.00 (ничего не создано) |
| Время работы | 0 с |
| Pod count | **0** (подтверждено провайдером) |
| Model responses | 0 (LIVE) |
| Model tool calls | 0 (LIVE) |
| Provenance verdict | `NOT RUN`; валидатор — 12/12 UNIT PASS |
| App build / HTTP / DB / GUI | **N/A** — приложения не было |
| Auto repair | **N/A** |
| Independent verification | **N/A** |
| Task state | **N/A** |
| Cleanup | **CLEAN** (0 Pod было до и после) |
| Production integrity | **OK**, не изменена |
| Autonomy level | **LEVEL 0 — NOT STARTED** |
| LIVE verdict | **CAPACITY_UNAVAILABLE / WAITING OWNER + CAPACITY** |
| RC readiness | `RC BLOCKED PENDING LIVE QWEN + CERTIFICATE` |

---

## 2. Что именно проверено бесплатно

### 2.1 Preflight — READY

`final-live/preflight.json`:

- Gateway `/health` = 200, `ready`, version 1.2.3;
- установленная Canalla 1.2.6: `/health` 200;
- Pods: `[]`;
- баланс RunPod: **$8.4464**;
- device `6413489d-6cae-4183-8446-a3b2339137dc` online, совпадает с локальным;
- Tor: `verified=true`, `socks5h`;
- диск: достаточно;
- watchdog self-test: PASS;
- provenance-модуль: live-провайдеры распознаны.

### 2.2 GPU — внешний блокер

`final-live/gpu-selection.json` (только `GET`-запросы к каталогу и тому):

- Network Volume `uwgeaie5b0` (`orcarouter-storage`, 50 GB) — **US-TX-3**, `STANDARD`
  (региональный: монтируется только в своём дата-центре).
- В каталоге **0** NVIDIA Secure GPU ≥48 GB с ненулевой доступностью в **US-TX-3**.
- Совместимые карты есть в других DC:

| GPU | Цена/ч | Где есть | В US-TX-3 |
|---|---|---|---|
| A40 48 GB | $0.59 | CA-MTL-1, EU-SE-1 | нет |
| L40 48 GB | $0.82 | — | — |
| L40S 48 GB | $1.09 | **US-TX-4** | нет |
| RTX 6000 Ada 48 GB | $1.09 | US-IL-1, US-KS-2, US-WA-1 | нет |
| A100 80 GB PCIe | $2.09 | CA-MTL-3 | нет |

Это ровно тот случай, для которого продукт имеет типизированный `CAPACITY_UNAVAILABLE`:
запуск не начинается, мощность не ожидается, второй Pod не создаётся.

Без отдельного решения владельца нельзя ни пересоздать Volume в другом DC, ни перезаливать
~20 GB весов. Поэтому LIVE заблокирован внешне.

### 2.3 Стоимость для нового лимита $2.00

`app/compute/cost_policy.py` расширен, старые $0.15-числа не изменились.

| Параметр | Значение |
|---|---|
| MAX LIVE BUDGET | $2.00 |
| MAX BILLABLE | 90 мин (5400 с) |
| TARGET WORK | 80 мин (4800 с) |
| MIN WORK | 45 мин (2700 с) |
| Termination grace | 30 с |
| Watchdog margin | 10 с |

Примеры (реальная цена перепроверяется перед созданием Pod):

| Карта | Цена/ч | Billed | Работа | Worst case |
|---|---|---|---|---|
| A40 | $0.59 | 5400 с | 80 мин | **$0.885** |
| L40S | $1.09 | 5400 с | 80 мин | **$1.635** |
| A100 80 GB | $2.09 | 2880 с | 47.5 мин | **$1.986** |
| H100 | $2.89 | 2400 с | 39.5 мин | $1.926 → **отказ** (<45 мин) |

`check_rate()` на созданном Pod отказывает, если фактическая цена ломает worst case.

### 2.4 Watchdog — реальный read-only прогон

`scripts/live_qwen_watchdog.py --live` (без Pod, без создания):

- подключился к RunPod, прочитал список Pod;
- 5 секунд ждал появления Pod (bounded, не capacity-очередь);
- вернул `CAPACITY_UNAVAILABLE`, `pods_remaining = 0`, `estimated_cost = 0`;
- `sleep_prevented = true` (`SetThreadExecutionState`);
- баланс до и после — **$8.44638184** (без изменений).

Dry-run (fake provider) — **23/23 PASS**, включая новые случаи:

- временная ошибка чтения провайдера не превращается в «нет Pod»;
- полностью недоступный провайдер → `PROVIDER_UNREACHABLE`, `pods_remaining = null`,
  а не ложный CLEAN;
- смерть основного runner не останавливает watchdog;
- второй Pod → `MULTIPLE_PODS`, оба останавливаются;
- отказ terminate и неотключаемый Pod → `TERMINATION_UNCONFIRMED`;
- $2-политика: 5400 с billed, 4800 с target, worst case ≤ $2.

### 2.5 Изоляция production

`final-live/authorization.json → production` и `production-baseline`:

| Показатель | Значение |
|---|---|
| users | 2 |
| chats | 54 |
| messages | 120 |
| paired_devices | 63 |
| local_tasks | 30 |
| integrity | **ok** |
| foreign keys | 0 нарушений |
| активное устройство | `6413489d-…` |
| `device.json` | `6413489d-…` |
| установленная версия | 1.2.6 |

Ни один тест этого этапа не писал в production БД, не менял credential и не переспаривал
устройство.

### 2.6 Проверки кода

- `tests/test_127_cost_policy.py` — **11/11 PASS** (включая новый $2-конверт).
- `tests/test_127_provenance.py` — **12/12 PASS**.
- `tests/test_sidecar_stamp.py` — PASS после пересборки sidecar.
- Rust host — 40/40 PASS (не менялся в этом этапе).
- Ruff check/format — чисто.
- **CI на `57e09dc`: 7/7 success** (developer-gate, native-gate, frontend-e2e, installer-build,
  tor-runtime, llama-runtime, llama-runtime-image) + **Linux gate success**.

---

## 3. Что изменено в коде этого этапа

1. **`app/compute/cost_policy.py`** — два независимых потолка (деньги и 90-минутная политика),
   `target_work_seconds`, `min_work_seconds`, `sufficient`, `budget_limited`/`time_limited`.
   Старый план $0.15 не изменился.
2. **`scripts/live_qwen_watchdog.py`**:
   - `--max-billable-minutes` (90) и `--min-work-minutes` (45);
   - `ProviderError` вместо молчаливого `[]`: потеря связи не выдаётся за «Pod нет»;
   - `PROVIDER_UNREACHABLE` с `pods_remaining = null`;
   - `SetThreadExecutionState` — не даёт хосту watchdog уснуть на время сессии;
   - `--runner-pid` и событие `runner-gone` (watchdog продолжает работать);
   - dry-run расширен до 23 проверок.
3. **`live_acceptance.py`**:
   - обязательный `--budget-usd`, потолок $2.00, 90/80/45 минут;
   - `--prepare` — бесплатная подготовка и `authorization.json`;
   - GPU-гейт **до** создания Pod;
   - **исправлена изоляция**: родительский процесс harness теперь применяет изолированный
     `DATABASE_URL` до импорта приложения (раньше чтение журнала могло уйти в production БД);
   - независимая GUI-проверка Playwright и SQLite-проверка;
   - запись artifacts `final-live/*`.
4. **`gpu_probe.py`** — read-only выбор GPU по каталогу и DC тома, `reference` для отчёта.
5. **`preflight.py`** — бюджет $2.00 и проверка `sufficient`.
6. **PLAN.md** — новый конверт, реальные цены, описание блокера.

---

## 4. Найденные проблемы

| # | Проблема | Классификация | Статус |
|---|---|---|---|
| 1 | Нет GPU ≥48 GB в US-TX-3 (Volume региональный) | INFRASTRUCTURE FAILURE (external) | блокер LIVE |
| 2 | Harness читал журнал из родительского `DATABASE_URL` (риск production БД) | TOOL INTEGRATION BUG | **FIXED IN CODE** |
| 3 | Watchdog считал недоступный провайдер за «0 Pod» | RESOURCE/INFRASTRUCTURE BUG | **FIXED IN CODE** |
| 4 | Стоимость для $2/90 мин не была выражена политикой | CODE GAP | **FIXED IN CODE** |
| 5 | Нет подтверждения владельца на $2.00 | EXPECTED USER APPROVAL | ожидание |

Проблем модели, tool calling, parser, policy, executor, GUI, verification, recovery и provenance
в этом этапе не обнаружено — потому что настоящая модель не запускалась. Никакой бесплатный тест
не выдаётся за LIVE.

---

## 5. Уровни автономности

| Уровень | Статус |
|---|---|
| LEVEL 1 — REAL MODEL CONNECTED | не достигнут (нет Pod) |
| LEVEL 2 — REAL MODEL TOOL EXECUTION | не достигнут |
| LEVEL 3 — REAL MULTI-STEP | не достигнут |
| LEVEL 4 — AUTONOMOUS BUILD AND RUN | не достигнут |
| LEVEL 5 — AUTONOMOUS ERROR REPAIR | не достигнут |
| LEVEL 6 — INDEPENDENTLY VERIFIED COMPLETION | не достигнут |
| LEVEL 7 — DURABLE RESUME WITH REAL MODEL | не достигнут |

Mock/scripted доказательства прошлых этапов (packaged host, e2e build/repair/recovery) остаются
в силе как **free integration pass**, но **не повышаются** до LIVE-уровней.

**Максимальный подтверждённый уровень этого этапа: LEVEL 0 — NOT STARTED.**

---

## 6. Что нужно для запуска

1. **Подтверждение владельца** на бюджет $2.00 и один Pod (фраза в `authorization.json`).
2. **Доступная GPU ≥48 GB в US-TX-3** на момент запуска (или решение владельца о другом
   размещении/Volume — это отдельная инфраструктурная операция).
3. После появления мощности:

```bat
python artifacts\acceptance-127\live-qwen\live_acceptance.py --live --budget-usd 2.00 ^
       --confirm "I AUTHORIZE ONE PAID POD"
```

Скрипт сам откажется, если Pod уже есть, если цена ломает бюджет, если окно <45 минут или если
preflight не READY.

---

## 7. Cleanup и деньги

- Pod создано: **0**.
- Pod осталось: **0** (подтверждено `GET /v2/pods` до и после).
- Потрачено: **$0.00**.
- Network Volume не удалялся и не изменялся.
- Временные каталоги тестов не создавались (LIVE не начинался).
- Баланс изменился только на фоновый storage существующего Volume (−$0.01 за часы, не за тест).

---

## 8. Оставшиеся блокеры до 1.2.7 RC

1. LIVE Qwen — **не выполнен** (нет разрешения + нет мощности).
2. Authenticode — **BLOCKED_BY_EXTERNAL_CERTIFICATE**.
3. Isolated installer E2E — не выполнялся (требует установки 1.2.7/VM).
4. Identity установленной 1.2.6 (`6413489d…` после инцидента) — решение владельца, не дефект
   этого этапа.

---

## 9. Артефакты

```
artifacts/acceptance-127/live-qwen/final-live/
  preflight.json                 READY, 0 Pod, $8.4464
  authorization.json             BLOCKED — CAPACITY_UNAVAILABLE, production snapshot
  cost-policy.json               reference A40 $0.59 → $0.885 / 90 мин
  gpu-selection.json             0 карт в US-TX-3, полный список DC
  watchdog-events.json           реальный read-only прогон: 0 Pod, $0, sleep prevented
  billing-summary.json           расход $0.00
  cleanup.json                   CLEAN (0 Pod до и после)
  final-summary.json             LIVE NOT RUN
  self-test.json                 9/9 PASS (guards + бюджет)
artifacts/acceptance-127/final-readiness/watchdog-dry-run.json   23/23 PASS
```

Секретов в артефактах нет: только id, цены, счётчики, хэши и состояние.
