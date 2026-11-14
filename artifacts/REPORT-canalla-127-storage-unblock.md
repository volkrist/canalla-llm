# Canalla LLM 1.2.7 — STORAGE UNBLOCK + подготовка REAL QWEN LIVE

**Дата:** 14 ноября 2026.
**Ветка:** `feature/canalla-1.2.7-autonomous-agent`.
**Исходный SHA:** `2ad1848`.
**Продуктовый SHA этого этапа:** `1b2d93b` (+ `36648c4` — форматирование без изменения поведения).
**Публичная версия:** 1.2.6 — **не изменялась**.
**Платные действия:** **0 Pod, 0 новых томов, $0.00 GPU.**

---

## 1. Итог одной таблицей

| Пункт | Результат |
|---|---|
| Причина исходного блокера | модель лежала на **одном** региональном томе (`uwgeaie5b0`, US-TX-3), а harness умел смотреть только его DC |
| Состояние US-TX-3 | в 03:52 — **0** совместимых GPU; в 04:15 — L40S вернулся (LOW). Пул «дышит» |
| Второй региональный том | `y92bt25ru8`, US-MO-1, реплика **verified** (реальный ответ Qwen 2026-10-13) |
| Global Volumes | провайдер **открыл API**: `/v2/global-volumes`, mount kind `global`, без привязки к DC |
| Совместимость с llama.cpp | региональный — **доказана**; глобальный — **не доказана** (`mmap`/latency не измерялись), а текущий launch path ещё и пишет в том |
| Выбранная архитектура для LIVE | **B** — проверенная региональная реплика; выбор DC — по живой доступности |
| GPU для LIVE | L40S 48 GB, **$1.09/ч**, worst case 90 мин = **$1.635** при бюджете $2.00 |
| Перенос данных | план готов (`rsync` в одном CPU Pod), **не выполнялся** |
| Стоимость переноса | **$0.0117–$0.0701** одной операцией + опциональная GPU-проверка $0.2725 |
| Ежемесячное хранение | измерено: **$7.029 / 30 дней** за два тома ($0.07/GB-мес) |
| Production 1.2.6 | **не изменена**: integrity ok, device `6413489d-…`, users 2 / chats 54 / messages 120 / paired_devices 63 / local_tasks 30 |
| Настоящая Qwen | **не запускалась** в этом этапе (`NOT RUN`) |
| Готовность к LIVE | **READY_FOR_OWNER_AUTHORIZATION** |
| CI | **7/7 success** на `36648c4` + **Linux gate success** (§14) |

---

## 2. Исходный и итоговый SHA

| | |
|---|---|
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| Исходный HEAD | `2ad1848` (в синхроне с origin) |
| Продуктовый коммит этапа | `1b2d93b` — «Serve the model from any verified placement»; `36648c4` — форматирование |
| Предыдущий зелёный CI | `57e09dc`: CI 7/7 + Linux gate |
| CI на `36648c4` | **7/7 success** (developer-gate, native-gate, frontend-e2e, installer-build, tor-runtime, llama-runtime, llama-runtime-image) |
| Linux gate на `36648c4` | **success** |
| Публикация / тег / manifest / updater | **не трогались** |

---

## 3. Причина исходного GPU-блокера (подтверждена и уточнена)

Отчёт `artifacts/REPORT-canalla-127-QWEN-LIVE.md` называл блокером «нет GPU в US-TX-3». Это верно,
но **неполно**: в аккаунте уже был второй региональный том с **проверенной** копией той же модели, и
GPU в его дата-центре был доступен. Блокером был не провайдер, а **однотомная модель мира в
acceptance harness**:

* `gpu_probe.py` читал `GET /v2/network-volumes/uwgeaie5b0` и фильтровал каталог по его DC;
* `live_acceptance.py` жёстко передавал `RUNPOD_NETWORK_VOLUME_ID=uwgeaie5b0` и не задавал
  `RUNPOD_DATACENTER` (дефолт `US-TX-3`);
* продукт при этом **умел** несколько размещений (`app/compute/candidates.py`), но никто не
  передавал ему второй том.

То есть «внешний блокер» был наполовину **tool integration bug**. Исправлено в коде, а не обойдено.

---

## 4. Актуальная доступность GPU (read-only, 14.11.2026)

| GPU | VRAM | $/ч | Где есть (на момент чтения) |
|---|---|---|---|
| A40 | 48 | 0.59 | CA-MTL-1, EU-SE-1 |
| RTX A6000 | 48 | 0.69 | EU-SE-1 |
| L40 | 48 | 0.82 | — |
| RTX PRO 5000 | 48 | 0.96 | — |
| **L40S** | 48 | **1.09** | EU-NL-1, EUR-IS-2, **US-MO-1**, US-NC-1, **US-TX-3 (04:15)** |
| RTX 6000 Ada | 48 | 1.09 | US-IL-1, US-WA-1 |
| A100 PCIe | 80 | 2.09 | EU-RO-1, **US-MO-1** |
| A100 SXM | 80 | 2.19 | EUR-IS-1, US-KS-2, US-MD-1, **US-MO-1**, US-WA-1 |

Ключевое наблюдение: **за 23 минуты US-TX-3 прошёл путь «0 карт» → «L40S LOW»**. Одиночный DC не
является планом; поэтому прогон выбирает размещение по живой доступности и перечитывает цену прямо
перед созданием Pod.

---

## 5. Global Volumes: что реально изменилось

После 25.09.2026 живой контракт (`https://api.runpod.io/v2/openapi.json`) содержит:

| Факт | Значение |
|---|---|
| Пути | `GET/POST /v2/global-volumes`, `GET/PATCH/DELETE /v2/global-volumes/{id}` |
| Вид монтирования | `mounts.global` (плюс `network`, `persistent`) |
| Лимиты | до **4** network + ровно **1** global на Pod; `persistent` несовместим с ними |
| Дата-центр | у глобального тома **нет** ограничения; работает на GPU **и CPU** Pod |
| S3-совместимый API | **нет** — содержимое читается только из Pod |
| ФС | объектная: нет блокировок, atomic rename, hard links, permission bits; eventual consistency |
| Запросы | Class A (запись/листинг) $0.005/1000, Class B (чтение/метаданные) $0.0005/1000 |
| Хранение | оплачивается, но **ставка за GB-мес на прочитанных страницах не опубликована** |

Вывод 25.09.2026 («API не умеет адресовать глобальный том») **устарел**. Обновление зафиксировано в
`docs/gpu-allocation.md` §8.

В аккаунте уже есть глобальный том `cmugq2bsv000007js3sa47w6j` (`physical_olive_yak`, создан
25.09.2026). Его **содержимое неизвестно** (`MODEL_INVENTORY_PARTIAL`): без Pod его не прочитать, а
Pod в этом этапе запрещён.

---

## 6. Совместимость с llama.cpp (статический анализ + один проверенный запуск)

| Требование | Региональный том | Глобальный том |
|---|---|---|
| чтение одного GGUF целиком | да | да (документированный сценарий) |
| mmap по умолчанию | **проверено** | **не измерено** |
| file locks / atomic rename / symlinks / permission bits | не нужны | не поддерживаются — и не нужны |
| запись в каталог модели | нет | нет |
| launch path (`start-llm.sh`) | пишет `llama-server.log` и `.pid` в корень тома — норма | **запись в объектный том** — нежелательно |
| KV cache | GPU VRAM | GPU VRAM |
| runtime (64 файла, ~1.2 GB) | на томе | на объектном слое — медленный small-file старт |

Поэтому `app.compute.storage.check_compatibility` возвращает:

* `regional_network_volume` → **ready** (доказано реальным ответом 2026-10-13);
* `global_volume` + запись в том → **storage_incompatible_runtime**;
* `global_volume` + read-only launch → **storage_unverified** с причинами
  `mmap_on_object_storage_unmeasured`, `startup_latency_unmeasured`, `eventual_consistency`;
* `local_model_copy` → **ready**, если локального диска хватает на модель + 10 % резерва.

**Ничто в отчёте не утверждает, что глобальный том уже работает с llama.cpp.** Это требует одного
GPU Pod и отдельного разрешения.

---

## 7. Сравнение четырёх архитектур

| | A: только TX-3 | **B: региональная реплика MO-1** | C: глобальный том, всё дерево | D: глобальные веса + локальный runtime |
|---|---|---|---|---|
| Привязка к DC | жёсткая | жёсткая к DC тома | **нет** | **нет** |
| GPU сейчас | 0 (в 03:52) | **L40S, A100** | любая в каталоге | любая в каталоге |
| Перенос | 0 | **0** | ~22.9 GB | ~20.8 GB |
| Стоимость переноса | $0 | **$0** | ≈$0.01–0.07 | ≈$0.01–0.07 + доставка runtime |
| Хранение/мес | $3.50 | **$7.03 (уже платится)** | ставка не опубликована | ставка не опубликована |
| Совместимость | доказана | **доказана** | не доказана | не доказана |
| Вердикт | **BLOCKED** | **READY NOW** | POSSIBLE_UNVERIFIED | POSSIBLE_UNVERIFIED |

**Рекомендация:** немедленный LIVE — **B** (ноль миграции, ноль новых ресурсов, проверенная копия,
мощность есть). Стратегически — **C**, затем **D**, если старт на объектном хранилище окажется
медленным. Ни C, ни D не объявляются совместимыми до одного измеряющего Pod.

---

## 8. Фактическая структура модели

| | |
|---|---|
| GGUF | `/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf` |
| Размер | **20 752 787 712** байт (Q5_K_M, один файл, не split) |
| SHA-256 | `4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1` |
| Том `uwgeaie5b0` | 6 377 файлов, **22 902 211 482** байт; `serve_selftest: FAIL` (зафиксирован, причина не снята) |
| Runtime | `llama.cpp/build/bin/llama-server`, `llama-cli`, `llama-bench`, версия **b11565** |
| Скрипты | `start-llm.sh`, `check-llm.sh` в корне тома |
| Том `y92bt25ru8` | та же модель и runtime, `ready:true`, `last_verified 2026-10-13T12:23:41Z`; проверен реальным ответом (готовность 8 с, 21 613 MiB VRAM) |

---

## 9. План копирования (черновик, не выполнялся)

| Этап | Действие |
|---|---|
| A — inventory | **сделано**: файлы, размеры, SHA-256 (`model-inventory.json`) |
| B — destination | глобальный том `cmugq2bsv000007js3sa47w6j`, раскладка 1:1 с источником |
| C — transfer | **один CPU Pod** в US-MO-1 с обоими томами; `rsync --archive /workspace/ /workspace-global/` |
| D — verify | `find | wc -l`, `du -sb`, `sha256sum` GGUF → совпадение с пин-хешем обязательно |
| E — loading | один GPU Pod с глобальным монтированием: только это докажет/опровергнет `mmap` |
| F — cutover | **не в этом задании**; региональный том остаётся дефолтом |

Старый том `uwgeaie5b0` (и реплика) **не удаляются, не форматируются и не изменяются**.

---

## 10. Стоимость

### One-time (только если выбран глобальный путь)

| Статья | Значение |
|---|---|
| CPU Pod `cpu5c`, 2 vCPU, $0.07/ч | 10 мин → **$0.0117**; 30 мин → **$0.0350** |
| Request classes (floor / per-file 6 377) | **$0.000006** / **$0.035074** |
| **Итого одной операцией** | **$0.0117 – $0.0701** |
| Опциональная GPU-проверка (L40S, 15 мин) | **$0.2725** |

### Хранение

| | |
|---|---|
| Измерено (30 дней, два тома) | **$7.0292**; $0.11666667/том/день = **$3.50/том-мес** |
| Ставка | **$0.07/GB-мес** (подтверждена счётом: 50 GB × 0.07 = 3.50) |
| Глобальный том, ставка | **не опубликована** |
| Сценарий 22.9 GB @ $0.07/GB-мес | $1.6030 / 30 дней |
| Сценарий 22.9 GB @ $0.10/GB-мес | $2.2900 / 30 дней |
| Экономия при отказе от двух региональных (сценарии) | $5.43 / $4.74 в месяц |

### GPU LIVE

| | |
|---|---|
| Бюджет | **$2.00** |
| L40S $1.09/ч, 90 мин billed | **$1.635** worst case |
| Запас | **$0.365** |
| Минимум смысла | 45 мин; окно = 80 мин |

---

## 11. Стратегия загрузки модели

1. `app.compute.storage.resolve(...)` собирает монтирования из конфигурации: региональный том →
   `/workspace`; глобальный → `/workspace`, рядом с network → `/workspace-global`; правка пути
   глобального тома сдвигает network на `/workspace-2` (поведение провайдера).
2. Из корня выводятся `model_path` и `runtime_path` — больше нет жёсткого `/workspace`.
3. `create_pod` передаёт `ALEX_LLM_ROOT` и `ALEX_LLM_MODEL_PATH` в Pod.
4. `remote_runtime.py` читает их; дефолтные значения байт-в-байт совпадают с исторической раскладкой.
5. Комбинация, которую нельзя смонтировать, **отвергается до вызова провайдера** (ничего не создано —
   нечему биллинговаться).

---

## 12. Изменения в Canalla

| Файл | Что сделано |
|---|---|
| `apps/backend/app/compute/storage.py` | **новый**: виды хранилища, разрешение монтирований и путей, типизированные вердикты совместимости, проекции стоимости |
| `apps/backend/app/compute/runpod_api.py` | `create_pod(storage_type=…)` → `mounts.global`/`network`, `ALEX_LLM_ROOT`/`MODEL_PATH`, отказ до запроса; `startup_command(root=…)` |
| `apps/backend/app/compute/remote_runtime.py` | пути из env, дефолт прежний; логи читаются из фактического корня |
| `apps/backend/app/compute/controller.py` | `primary_placement()` из режима хранения; `storage` в `allocation_policy_payload`; `storage_type` в `create_pod` |
| `apps/backend/app/config.py` | `RUNPOD_STORAGE_MODE`, `RUNPOD_GLOBAL_VOLUME_ID`, `RUNPOD_GLOBAL_MOUNT_PATH` + fail-closed валидаторы |
| `artifacts/acceptance-127/live-qwen/gpu_probe.py` | все Network Volumes, реплики, продуктовая политика, типизированные причины |
| `artifacts/acceptance-127/live-qwen/live_acceptance.py` | выбранное размещение передаётся в изолированный backend |
| `docs/gpu-allocation.md` | §8: новый контракт global volumes и вердикты совместимости |
| `artifacts/acceptance-127/storage-unblock/` | 14 артефактов + два read-only probe-скрипта |

Production-дефолт остаётся `regional_network_volume`; глобальный режим — только явная настройка.

---

## 13. Новые тесты

| Набор | Результат |
|---|---|
| `apps/backend/tests/test_storage_modes.py` (новый) | **31 passed** |
| `test_compute`, `test_allocation_candidates`, `test_model_replicas`, `test_launch_scripts_contract` | **108 passed** |
| `apps/gateway/tests` (полный) | **262 passed** |
| `apps/backend` полный | **1160 passed, 1 skipped** (sidecar stamp — ожидаемо после правок backend) |
| После пересборки sidecar: `test_sidecar_stamp` | **11 passed** |
| Ruff check/format | чисто |
| `live_acceptance.py --self-test` | **9/9 PASS** |
| `live_qwen_watchdog.py --self-test` | **23/23 PASS** |

20 обязательных негативных сценариев сопоставлены с конкретными тестами в
`storage-unblock/storage-tests.json` (включая «нет запроса к провайдеру при отказе», «нехватка
локального диска», «неизвестный вид хранилища», «конфликт путей», «две модели»).

---

## 14. CI

| | |
|---|---|
| CI `workflow_dispatch` на `36648c4` | **7/7 success** (run 43557984527) |
| Linux gate на push `36648c4` | **success** (run 43557361772) |
| Предыдущий зелёный | `57e09dc` — CI 7/7 + Linux gate |
| Первая попытка на `1b2d93b` | developer-gate **FAIL** — только `ruff format` в `runpod_api.py`; Linux gate **FAIL** на `test_agent_build_e2e` (флейк: тот же тест прошёл на `36648c4` без изменений в нём) |
| После `36648c4` | оба гейта зелёные |

---

## 15. Производственные данные (read-only, до и после)

| Показатель | Значение |
|---|---|
| Версия установленного приложения | **1.2.6**, `/health` 200 |
| `integrity_check` | **ok** |
| foreign key violations | **0** |
| users / chats / messages | 2 / 54 / 120 |
| paired_devices / local_tasks | 63 / 30 |
| device identity (`device.json` и БД) | **6413489d-6cae-4183-8446-a3b2339137dc** |
| updater | `204` на запрос обновления (не изменён) |
| re-pair | **не выполнялся** |

Ни один скрипт этого этапа не писал в production БД, не менял credential и не переспаривал устройство.

---

## 16. Инфраструктурные риски

1. **Global volume — бета.** Поведение и цены могут измениться; при балансе $0 том удаляется через
   15 дней.
2. **`mmap` на объектном хранилище не измерен** — главный технический риск варианта C.
3. **Ставка хранения глобального тома не опубликована** — экономию нельзя обещать, только
   сценарии.
4. **Пул GPU «дышит»** — 03:52 → 04:15 это доказали. Поэтому выбор размещения всегда живой.
5. **`serve_selftest: FAIL` на первичном томе** остаётся открытой записью: причина не снята, GPU не
   запускался. Реплика MO-1 проверена отдельно.
6. **Community Cloud** по-прежнему выключен; глобальный том это не меняет.

---

## 17. Подтверждение отсутствия платных действий

| | До | После |
|---|---|---|
| Pods | **0** | **0** |
| Новые тома | — | **0** |
| Перенос данных | — | **не выполнялся** |
| Баланс | $8.4269 | $8.4172 |

Разница **$0.0097** — это фоновое хранение двух уже существующих томов
(`currentSpendPerHr = $0.01`), а не расход испытания. GPU-расход: **$0.00**.

---

## 18. Что нужно от владельца

### Обязательное — один LIVE-прогон

```
Разрешаю один платный LIVE-прогон Qwen с бюджетом не более $2.00 и одним GPU Pod.
```

После этой фразы (и только после неё):

```bat
python artifacts\acceptance-127\live-qwen\live_acceptance.py --live --budget-usd 2.00 ^
       --confirm "I AUTHORIZE ONE PAID POD"
```

Скрипт сам откажется, если Pod уже есть, если цена ломает бюджет, если окно < 45 минут или если
preflight не READY.

### Отдельное (только если нужен глобальный путь)

```
Разрешаю один CPU Pod в US-MO-1 на срок до 30 минут и не более $0.10
для переноса модели на существующий Global Volume cmugq2bsv000007js3sa47w6j,
без удаления и изменения томов uwgeaie5b0 и y92bt25ru8.
```

Затем — отдельное разрешение на **один GPU Pod** (~15 мин, ≈$0.27) для проверки загрузки GGUF с
глобального тома.

---

## 19. Definition of done

| Пункт | Статус |
|---|---|
| Проблема US-TX-3 подтверждена/актуализирована | **да** (§4) |
| Global Volumes изучены | **да** (§5) |
| Возможности аккаунта проверены | **да** (2 network + 1 global, billing) |
| Совместимость с llama.cpp исследована | **да**, глобальная — **не доказана** (§6) |
| Альтернативы сравнены честно | **да** (§7) |
| Стоимость переноса рассчитана | **да** (§10) |
| Ежемесячная стоимость рассчитана | **да** (§10) |
| Способ доступа к модели найден | **да** (региональная реплика; перенос через Pod) |
| План переноса подготовлен | **да**, не выполнялся (§9) |
| Проверка целостности предусмотрена | **да** (SHA-256 + file count + du) |
| GPU selector поддерживает режимы хранения | **да** |
| Бюджет LIVE сохранён | **да** ($2.00, worst case $1.635) |
| Watchdog сохранён | **да** (23/23) |
| Production identity не изменилась | **да** (§15) |
| Тесты новых изменений пройдены | **да** (§13) |
| CI подтверждён | **да** — 7/7 + Linux gate (§14) |
| Блокеры перечислены | **да** (§16) |
| Следующее действие конкретно | **да** (§18) |

---

## 20. Артефакты

```
artifacts/acceptance-127/storage-unblock/
  inspect_contract.py               read-only: живой OpenAPI
  provider_inventory.py             read-only: тома, каталог, billing, планы продукта
  build_artifacts.py                сборка производных артефактов
  baseline.json                     аккаунт, тома, каталог, планы аллокации
  runpod-storage-capabilities.json  контракт: пути, mounts, лимиты
  global-volume-availability.json   существующий глобальный том (содержимое неизвестно)
  storage-architecture-comparison.json
  model-inventory.json              GGUF, хеш, оба тома
  model-compatibility.json          требования llama.cpp по видам хранилища
  gpu-catalog.json                  совместимые карты и их DC
  gpu-selection.json                выбор для LIVE
  migration-plan.json               этапы A–F, rollback
  migration-cost.json               one-time и опции
  monthly-storage-cost.json         измеренное и сценарии
  storage-tests.json                20 сценариев → тесты
  live-readiness.json               READY_FOR_OWNER_AUTHORIZATION
  summary.json                      итог
artifacts/acceptance-127/live-qwen/
  gpu_probe.py                      multi-placement, read-only
  live_acceptance.py                передаёт выбранное размещение
  final-live/gpu-selection.json     последний зонд (оба размещения verified)
  final-live/authorization.json     READY_FOR_OWNER_AUTHORIZATION
```

Секретов в артефактах нет: только id, цены, счётчики, хеши и состояние.
