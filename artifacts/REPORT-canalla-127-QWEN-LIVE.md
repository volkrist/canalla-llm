# Canalla 1.2.7 — отчёт о REAL QWEN LIVE ACCEPTANCE

> **Обновление 14.11.2026:** блокер «нет GPU в US-TX-3» больше не является блокером этого прогона.
> Найден и проверен второй региональный том (`y92bt25ru8`, US-MO-1) с **verified** копией модели,
> harness научился выбирать размещение по всем проверенным репликам, а провайдер открыл API
> глобальных томов. Подробности и цены — `artifacts/REPORT-canalla-127-storage-unblock.md`;
> актуальный план — `artifacts/acceptance-127/live-qwen/PLAN.md`.

> **Обновление 14.11.2026, 09:15 UTC:** оба оплаченных тома стали равноправными размещениями.
> Найден и исправлен настоящий блокер MO-1: `GET /v2/catalog/datacenters/US-MO-1` отвечает
> `networkVolumeTypes: []`, и `RunPodAPI.volume()` считал это несовместимостью — поэтому каждый
> запрос с тома `y92bt25ru8` заканчивался `stream_interrupted` ещё до создания Pod. Пустой список
> теперь «молчание каталога», отказ — только явный список без нужного типа. Grant расширен на оба
> размещения тем же `grant_id`; TX-3 переклассифицирован в `UNVERIFIED_NEEDS_LIVE_CHECK` и
> запускается только с самопроверкой модели внутри того же Pod. Подробности — в разделе
> «Сессия 14.11.2026 09:00–09:15 UTC» в конце отчёта.

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
| Расписание | проход **каждую минуту**, внутри прохода **две проверки с интервалом 30 с**; без даты окончания (`PT1M`, `StopAtDurationEnd=false`) |
| Режим | one-shot `monitor.py --pass`, без резидентного процесса |
| Проверяемые размещения | **оба тома**: `y92bt25ru8` (US-MO-1) и `uwgeaie5b0` (US-TX-3) |
| Один экземпляр | `MultipleInstances=IgnoreNew` + файловая блокировка |
| Состояние | `%LOCALAPPDATA%\CanallaMonitor\state.json`, `status.json`, `checks.jsonl`, `events.jsonl` |
| Разрешение на оплату | отдельный `authorization-grant.json` (вне репозитория), одноразовое |
| Self-test | **18/18 PASS** (нет разрешения → нет Pod; использованное разрешение → отказ; чужой SKU → отказ; 429 → UNKNOWN + Retry-After; TX-3 доступна, но не авторизует запуск; два чека в проходе; ошибка провайдера ≠ «Pod нет»; второй экземпляр отклонён) |
| Проверено вживую | 18+ последовательных чеков; интервалы **29–33 с**, включая собственные запуски планировщика |
| Разрешение владельца | **сохранено 08:25:50Z**, `grant_id 9606c90d…`, scope: модель + L40S + `y92bt25ru8` + US-MO-1, $2.00, 1 Pod, до 14.12.2026, `used=false` |
| Automatic LIVE | **ARMED** (GPU сейчас недоступна → `WAITING_GPU`, Pod не создавался) |
| Защита grant | каталог `%LOCALAPPDATA%\CanallaMonitor` — ACL без наследования: только Volkr, SYSTEM, Administrators |

Планировщик сам выполняет проходы; каждый проход делает три GET на чек (поды, список обоих томов,
каталог с доступностью сразу по двум DC). Фактические отметки: `08:39:27Z`, `08:39:59Z`,
`08:40:28Z`, `08:41:01Z`, `08:41:28Z`, `08:42:00Z`, `08:42:28Z` — дельты 29–33 с.

US-TX-3 проверяется и показывается, но **никогда не авторизует запуск**: его записанный
`serve_selftest: FAIL` и scope гранта (только US-MO-1) делают размещение негодным, даже когда там
появляется L40S. Бесплатный разбор старой записи: в `primary-raw-log.txt` непосредственно перед
маркером `CANALLA_M_SERVE_SELFTEST_FAIL` напечатан **help-вывод `curl`**, то есть падал сам вызов
проверки, а не модель; это дефект измерения, а не доказательство неработоспособности тома — но и не
доказательство обратного. Платных проверок для этого не выполнялось, данные тома не менялись.

Единственная искусственная зависимость убрана: `preflight.py` больше не требует открытого окна
1.2.6. Настоящее предусловие — установленный пакет версии 1.2.6, который читается из его
собственного `build-stamp.json`; изолированный прогон использует свой backend и headless browser.
Проверено с недоступным `/health`: `verdict READY`, `version_source: build-stamp`.

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

---

## Сессия 14.11.2026 09:00–09:15 UTC — оба тома, grant v2, найденный блокер MO-1

**HEAD:** `b0370c3` (ветка `feature/canalla-1.2.7-autonomous-agent`, запушена).
**Pod создано:** 0. **Расход на вычисления:** $0.00.

### 1. Настоящая причина прошлых неудач (найдена и исправлена)

Попытка 08:53–08:55 UTC дошла до изолированного backend и закончилась
`stream_interrupted` без задачи и без Pod. Причина не в модели и не в конфигурации:
`RunPodAPI.volume()` сравнивал тип тома с `networkVolumeTypes` из
`GET /v2/catalog/datacenters/US-MO-1`, а этот каталог отвечает **пустым списком**. Пустой список
трактовался как несовместимость → `RunPodError("volume_mismatch", 409)` → `_search` не мог даже
получить котировку → исключение вне `LLMError` → общее сообщение «Ответ прерван». Ни один Pod при
этом не создавался и деньги не тратились.

Исправление: пустой (или отсутствующий) список — «молчание каталога», отказ остаётся только для
явного списка без нужного типа. Существующая проверка datacenter не ослаблена. Настоящая
несовместимость по-прежнему отсекается ходом кандидатов (`placement_rejected`).

Проверено бесплатно и на реальном API:

| Проверка | Результат |
|---|---|
| `apps/backend/tests/test_volume_catalogue.py` (4 случая) | **4 passed** |
| `tests/test_compute.py` + `test_on_demand_ai.py` + `test_allocation_candidates.py` | **114 passed** |
| `volume_compat_check.py` (product `RunPodAPI.volume()`, оба тома) | **READY** |
| `quote_preflight.py` (volume + catalogue + policy, оба DC) | оба тома читаются; карта в момент проверки отсутствовала |

### 2. Два равноправных размещения

`placement_records.py` — единственный источник правды о том, что доказывает запись каждого тома:

| Том | DC | Состояние модели | Почему |
|---|---|---|---|
| `y92bt25ru8` | US-MO-1 | **VERIFIED** | запись реплики после настоящего ответа модели |
| `uwgeaie5b0` | US-TX-3 | **UNVERIFIED_NEEDS_LIVE_CHECK** | записанный `serve_selftest: FAIL` получен из сломанной команды `curl` (в логе перед маркером — справка `curl`), ассеты (GGUF, размер, SHA-256, скрипты, llama.cpp) согласованы |

TX-3 больше не `FAILED` и не исключён: он выбирается, когда карта есть только там, но тогда
`live_acceptance.py` запускается с `--self-test-mode` — **тот же Pod** сначала обязан ответить на
реальный короткий запрос и подтвердить `provider=llamacpp`, `model` и `pod_id`, и только PASS
продолжает TaskDesk. FAIL останавливает Pod и завершает прогон; второй Pod не создаётся.

### 3. Grant расширен без второго разрешения

`authorize.py` пишет схему 2 (`allowed_placements`). Изменение scope — это **upgrade того же
grant**, а не новый файл: сохранены `grant_id 9606c90de0064e3783e8688a66fc2ec7`, `created_at`,
`expires_at 2026-12-14`, `used:false`. Запись идёт через проверенный временный файл и атомарную
замену под замком монитора; легаси-ключи одного размещения удалены. Повторный вызов не создаёт
второе разрешение, использованный grant не оживляется.

### 4. Самопроверки (бесплатно)

- `monitor.py --self-test` — **31/31 PASS**: матрица A–L (нет карты в обоих DC → `WAITING_GPU`;
  только MO-1 → MO-1; только TX-3 → `AUTO_LIVE_WITH_MODEL_SELFTEST` + `--self-test-mode`; оба →
  VERIFIED первым; FAIL самопроверки TX-3 → терминально, второй Pod невозможен; PASS → тот же Pod;
  использованный grant → отказа; цена вне бюджета → отказа; недоступный провайдер → `UNKNOWN`, не
  «чисто»; перезапуск → без дубля; «Pod мог быть создан» → сначала состояние провайдера;
  production вне пути записи).
- `live_acceptance.py --self-test` — **16/16 PASS**, включая офлайн-проверку подтверждения
  `compute.start`: prompt подтверждается ровно один раз и только с его digest, а прерванный поток
  или `error` никогда не считаются PASS.
- `watchdog --self-test` — PASS (входит в `preflight.py`).

### 5. Реальная автоматика (не имитация)

Windows Task `Canalla LLM GPU Monitor`: **Ready**, `LastTaskResult 0`, `NextRunTime` заполнен,
`PT1M` без даты окончания, `IgnoreNew`, `StartWhenAvailable`, `RestartOnFailure ×3`. Один проход —
две проверки, интервал измеряется от старта проверки до старта следующей:

```
09:10:28, 09:10:58, 09:11:28, 09:11:58, 09:12:28, 09:12:58  (по 30 с)
```

В 09:12:57 монитор реально увидел карту и **сам** запустил harness. Harness перепроверил каталог
перед созданием Pod, карта уже исчезла → `CAPACITY_UNAVAILABLE`, 7.8 с, **0 Pod, $0.00**, grant
автоматически возвращён в ARMED, мониторинг продолжился. Это ровно то поведение, которое
требовалось: без слепого `create_pod`, без 20-минутного ожидания и без сожжённого разрешения.

### 6. Что ждёт карту прямо сейчас

| Поле | Значение |
|---|---|
| Monitoring | **RUNNING**, каждые 30 с, без ограничения по времени |
| US-TX-3 | GPU `NONE`, модель `UNVERIFIED_NEEDS_LIVE_CHECK`, `live_eligible: false` |
| US-MO-1 | GPU `NONE`, модель `VERIFIED`, `live_eligible: false` |
| Authorization | **ARMED**, unused, 2 placements, $2.00, 1 Pod |
| Watchdog | готов (`--self-test` PASS, стартует до Pod) |
| Pods | **0** |
| Баланс | $8.3686 (только storage ~$0.01/ч) |
| Production 1.2.6 | users 2 / chats 54 / messages 120 / paired 63 / local_tasks 30, integrity `ok`, device `6413489d…` — **без изменений** |

При появлении L40S в любом из двух DC монитор сам выполнит preflight, запустит
`live_acceptance.py` (для TX-3 — с самопроверкой модели), остановит Pod, сохранит evidence
(включая `backend.log`, `host.log`, `watchdog.log` и `compute-confirmations.json`) и больше не
создаст ни одного Pod: grant одноразовый.

---

## Сессия 14.11.2026 11:00–11:35 UTC — захват GPU: задержка, лог-баг, учёт

**HEAD:** `71c50b7`. **Pod:** 1 (создан автоматически в 11:28:22). **Grant:** использован.

### 1. Почему предыдущий захват не удался

Две независимые задержки и один настоящий дефект backend.

**Дефект логирования.** `RedactTicket` склеивал сообщение в `record.msg` и обнулял `record.args`,
но форматтер access-логов uvicorn распаковывает из `args` ровно пять полей. Каждая запись доступа
падала с `ValueError: not enough values to unpack (expected 5, got 0)`, и `logging` печатал полный
traceback на каждый запрос: одна попытка дала **152 323 строки** лога за ~6 минут. Backend был занят
печатью ошибок вместо запуска compute. Исправлено: access-записи сохраняют пять полей и
редактируются по отдельности (`tests/test_runtime_log_filter.py`, 3 теста).

**Повторная бесплатная работа.** Монитор запускал полный `preflight.py` (~2–3 с), harness повторял
его ещё раз и дважды перечитывал тот же каталог GPU. При окне доступности в десятки секунд это и
было проигранной гонкой.

### 2. Замер задержки (реальные автоматические попытки)

| Этап | 11:25:30 (карта ушла) | 11:28:00 (карта удержана) |
|---|---|---|
| T1 обнаружение (observation) | 11:25:30 | 11:27:58 |
| T2 preflight | из кеша, 0.0 с | из кеша, 0.0 с |
| T3 grant claimed | 11:25:30 | 11:28:00 |
| T4 harness launch | 11:25:30 | 11:28:00 |
| T5 первый запрос (может создать Pod) | 11:25:36 (**6 с**) | ~11:28:05 |
| T6 ответ провайдера | `price_limit`, Pod не создан | Pod `enrm4o02n87wkq` создан 11:28:22 |

`DETECTION_TO_CREATE_LATENCY`: **0.0 с** от обнаружения до запуска harness, **6 с** до первого
запроса (из них 4 с — холодный старт uvicorn), **~24 с** до `createdAt` Pod (остальное — RunPod).
До исправления та же цепочка занимала ~13 с и использовала каталог, прочитанный секундами раньше.

Что убрано: повторный `preflight` (кеш 900 с, обновляется на проходе после обнаружения карты),
повторные чтения каталога (`--prepared` с observation этого прохода), alembic (шаблонная БД с
проверкой ревизии: 16 мс вместо 3.2 с).

### 3. Учёт: один Pod = использованное разрешение

Старый монитор читал общий `final-summary.json`. Проба/следующая попытка перезаписывала файл, и
реально созданный Pod выглядел как «ничего не создано» — разрешение возвращалось. За утро баланс
снизился на **$1.158** (≈64 GPU-минуты), то есть Pod-ы создавались, а grant восстанавливался.

Теперь harness пишет `run-<id>.json`, пока его изолированная БД ещё существует; монитор читает
только запись своего run-id, id Pod из lifecycle-файла или **движение баланса**. Чужой summary не
является доказательством; при полном отсутствии денежной правды попытка считается `UNKNOWN`
(разрешение не возвращается). `authorize.py --consume` фиксирует факт использования.

### 4. Текущее состояние (11:35 UTC)

- Pod `enrm4o02n87wkq` (L40S 48 GB, **$1.09/ч**), создан **11:28:22**, статус `RUNNING`.
- Watchdog `live_qwen_watchdog.py` запущен **до** запроса, лимиты `$2.00` / 90 billable минут.
- Backend, host-loop и harness живы; Task Scheduler держит проход (IgnoreNew), следующие триггеры
  отклоняются — второй Pod невозможен.
- Grant `9606c90d…` — **used**, second Pod запрещён.
- Оба DC проверяются каждые 30 с; 42 бесплатных проверки монитора и 117 backend-тестов зелёные.
- Результат TaskDesk на момент отчёта ещё не записан: модель загружается/работает.

Если прогон завершится ошибкой, Pod будет остановлен watchdog-ом, evidence сохранён, а повторный
платный запуск потребует нового явного разрешения владельца.

---

## LIVE daa28fd1 — завершение, причина, стоимость

**Pod:** `enrm4o02n87wkq` (L40S 48 GB, $1.09/ч), создан **11:28:22**, удалён.
**Стоимость:** **$0.0957** (≈5.3 GPU-минуты), баланс 7.1931 → 7.0971; `currentSpendPerHr` = $0.01
(только storage). **Второго Pod не было.** Grant `9606c90d…` — `used`.

### Что произошло

| Время (UTC) | Событие |
|---|---|
| 11:27:58 | проверка увидела L40S в US-MO-1 |
| 11:28:00 | claim grant + запуск harness (`detection_to_launch = 0.0 s`) |
| 11:28:01–06 | template DB 16 мс, backend готов за 4 с |
| 11:28:06 | первый запрос (единственный, который может создать Pod) |
| 11:28:22 | RunPod создал Pod `enrm4o02n87wkq` |
| 11:28:22–11:33:1x | загрузка модели (L40S, GGUF 20.7 GB) |
| ~11:33:15 | модель готова, началась генерация |
| **11:33:45** | **`TimeoutError: timed out` в `_sse_stream`** → процесс завершился |

`_sse_stream` открывал поток с `urlopen(timeout=30)`. Это таймаут **на чтение**, а не дедлайн
прогона, и первый токен после холодной загрузки 27B-модели занял больше 30 секунд. Исключение
никем не ловилось, поэтому прогон умер ровно в момент, когда модель стала доступна, — уже после
оплаты Pod. Итоговый вердикт в `run-daa28fd1….json`: `INCOMPLETE` (процесс не успел записать
summary).

### Исправлено (бесплатно, до следующей попытки)

1. **Read timeout 30 → 300 с** (`SSE_READ_TIMEOUT_SECONDS`) — это детектор зависания; настоящий
   предел задают дедлайн прогона и watchdog.
2. **Потерянный хвост потока — evidence, не падение:** `TimeoutError/OSError/HTTPException`
   превращаются в маркер `stream_read_error`, после чего вердикт считается по БД и workspace.
3. **Любое непойманное исключение теперь пишет `verdict: CRASHED` + причину** в `final-summary.json`
   (раньше оставался только `INCOMPLETE` без диагностики).
4. **Watchdog получает `--runner-pid`** — в evidence появляется `runner-gone`, если harness умер.
5. **Терминальный исход больше не снижает cadence:** проход снова делает две проверки, 30 с
   (раньше после `FAILED` оставалась одна проверка в минуту).

Проверки: `monitor --self-test` — **43/43 PASS**, `live_acceptance --self-test` — **25/25 PASS**,
backend-тесты (logging/volume/compute/on-demand/allocation) — **117 passed**. `startup_probe`:
backend готов за 4 с, template 31 мс, **0 logging errors**.

### Чего LIVE не подтвердил

Нет ни одного `MODEL_RESPONSE`, ни одного tool call, ни одного файла TaskDesk: поток не прожил
первой генерации. Поэтому уровень автономности не изменился, и это **не PASS**.

### Состояние сейчас

- Pod: **0**, тарификация вычислений остановлена (spend/hr $0.01 — storage томов).
- Grant: **used**, повторное использование невозможно; `automatic_live = DISARMED`.
- Мониторинг: **RUNNING**, 30-секундный cadence, оба DC, заново вооружён не будет.
- Production 1.2.6: users 2 / chats 54 / messages 120 / paired 63 / local_tasks 30, `ok`,
  device `6413489d…` — **без изменений**.

Следующий платный прогон требует **нового явного разрешения владельца**; автоматика его не выдаёт.
