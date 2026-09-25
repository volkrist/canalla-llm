# CANALLA LLM — GLOBAL VOLUME PRODUCTION PROOF

**Дата:** 25.09.2026 · **Ветка:** `release/canalla-1.1.0` · **HEAD на входе:** `41cd07b`
**Платные ресурсы за шаг:** **0 Global Volumes, 0 Network Volumes, 0 Pod'ов, $0**
**Релиз:** не смёржен, тега нет, стабильный манифест не выложен, установщик не пересобирался.

**ИТОГ: BLOCKED — RUNPOD GLOBAL VOLUME AUTOMATION NOT YET EXPOSED.**

Это не мнение и не «клиент не умеет». Это результат проверки **живого контракта провайдера**:
скачанной схемы `https://api.runpod.io/v2/openapi.json` (HTTP 200, 255 104 байта, `openapi 3.1.0`,
`info.version 2.0.0`, 38 путей), живой документации провайдера и справочника CLI. Второй региональный
том **не создан** (и не будет создан по этой инструкции), текущий `uwgeaie5b0` **не удалён**.

Проверка воспроизводима одной командой — когда провайдер откроет поддержку, она об этом скажет
сама:

```
python scripts/check-runpod-global-volume.py
```

Её живой вывод на 25.09.2026 (exit=1):

```
contract: 3.1.0 · api v2.0.0 · 38 paths
volume paths: ['/v2/billing/network-volumes', '/v2/network-volumes', '/v2/network-volumes/{id}']
mount kinds: ['network', 'persistent'] · template mounts: ['persistent']
volume types: ['STANDARD', 'HIGH_PERFORMANCE']
global-volume markers: {'globalvolume': 0, 'global-volume': 0, 'global_volume': 0, 'storagevolume': 0, 'storage-volume': 0}
graphql introspection blocked: True
CLI global-volume command: False
SUPPORTED AUTOMATION: NO — not exposed yet: REST v2 has no global-volume path, schema or enum value;
no global-volume CLI command; templates reject a network mount (so they cannot carry any volume)
```

---

## GLOBAL VOLUME

| | |
|---|---|
| **Доступен в консоли** | **YES** — Storage → New volume → Global volume (бета) |
| **Создан** | **NO** — создать его программно нечем (см. AUTOMATION), а консольного сеанса у меня нет |
| **ID** | — (ресурса нет) |
| **Pricing** | Цена **запросов опубликована** (Class A запись/листинг $0.005 за 1 000; Class B чтение/метаданные $0.0005 за 1 000), **цена хранения $/GB-месяц НЕ опубликована** |
| **Stored bytes** | — |
| **Model SHA256** | — (не измерялся: копирование не начиналось) |
| **Migration** | **NOT RUN** |

### Почему цена не «ясно доступна» — точная причина

Провайдер публикует таблицу цен на хранение, и **строки для Global Volume в ней нет**:

| Тариф (живая страница биллинга) | Цена | Период |
|---|---|---|
| Volume disk (работающие Pod'ы) | $0.10/GB/месяц | посекундно |
| Volume disk (остановленные Pod'ы) | $0.20/GB/месяц | посекундно |
| **Network volumes (до 1 ТБ)** | **$0.07/GB/месяц** | ежечасно |
| Network volumes (свыше 1 ТБ) | $0.05/GB/месяц | ежечасно |

Страница Global Volumes даёт только цену **запросов** и прямо говорит, что хранение тарифицируется
**отдельно**. Страница high-performance storage отправляет за ценой **в консоль**: «Exact pricing
varies by data center. Check the volume creation flow in the console for current rates.» То есть
условие §6 «если цена явно доступна и разумна — создать один Global Volume» **не выполнено**: цена
хранения Global Volume в документации отсутствует.

---

## AUTOMATION

| Механизм | Результат | Доказательство |
|---|---|---|
| **REST v2** | **NO** | В живом OpenAPI **нет пути** для глобальных томов (38 путей; storage/template пути: `/v2/network-volumes`, `/v2/network-volumes/{id}`, `/v2/billing/network-volumes`, `/v2/templates`, `/v2/templates/{id}`, `/v2/catalog/templates`). По всему файлу: `globalvolume` — **0**, `global-volume` — **0**, `storagevolume` — **0**, `storage_volume` — **0**. Единственные «global»-поля — `globalNetworking`/`PodGlobalNetworking` (приватная сеть Pod↔Pod, не хранилище). |
| **REST v2: как Pod вообще принимает хранилище** | только два вида | Схема `Mounts`: `persistent` (host-local, «Deprecated», не переживает отказ хоста, **запрещён на CPU-Pod'ах**) или `network` (`NetworkMount`, `maxItems: 1`). Схема `NetworkMount.volumeId`: «ID of an existing **NetworkVolume in the same data center as the pod**». Третьего вида монтирования в контракте **нет**. |
| **REST v2: что можно создать** | только региональный том | `POST /v2/network-volumes` — пример тела: `{"name","dataCenter","size","type"}`; `VolumeType` = enum **`STANDARD | HIGH_PERFORMANCE`** (значения `GLOBAL` нет). Глобальный том требует датацентра, которого в контракте нет. |
| **GraphQL** | **NO** | Интроспекция на сервере **выключена**: `{"errors":[{"message":"GraphQL introspection is not allowed by Apollo Server…","extensions":{"validationErrorCode":"INTROSPECTION_DISABLED"}}]}`. Документированный вход развёртывания Pod (`podFindAndDeployOnDemand`) содержит только `volumeInGb` + `volumeMountPath` (host-local) и `containerDiskInGb`; ни `networkVolume`, ни каких-либо global-полей в документации GraphQL нет. |
| **CLI / SDK** | **NO** | `runpodctl network-volume` — подкоманды `list/get/create/update/delete`; создание: `runpodctl network-volume create --name … --size … --data-center-id "US-GA-1"`. Команды для глобальных томов в справочнике **нет вообще**. |
| **Template-based deployment** | **NO — и это самый жёсткий факт** | Живая схема, `TemplateMounts`, дословно: «Storage mounts attached to a template. Templates support only a **single persistent mount** today; **any `network` property is rejected with 422** by the schema validator.» То есть шаблон не может сохранить **даже региональный** том, а значит и глобальный тем более. Путь §4 закрыт не догадкой, а собственными словами контракта. |
| **Другие поддерживаемые механизмы** | **не найдено** | Создание описано только как консольный поток; другого опубликованного механизма нет. |
| **Автоматическое создание Pod с Global Volume** | **FAIL** | — |
| **Mechanism** | — | **публичного механизма нет** |

### Чего я не делал и почему (§3, §5)

Инспекция **консольного** запроса (как консоль представляет ресурс) требует авторизованного
браузерного сеанса оператора. У меня его нет, получить его «из браузера» я не могу и не буду:
инструктировано не красть учётные данные, и браузерная автоматизация против консоли RunPod по §5
**не является допустимой производственной зависимостью**. Поэтому вопрос исследован там, где ответ
является контрактом: живая схема API, живая документация, справочник CLI, схема шаблонов.

### Что видно про консольный Pod (единственный смежный факт)

Ресурс `Pod` в живом API **открывает** `mounts` (поля ресурса: `actions, cloud, cluster, cost, cpu,
createdAt, cudaVersion, dataCenterId, globalNetworking, gpu, id, locked, mounts, name, runtime, ssh,
startedAt, status, template`). Но та же схема `Mounts` описывает только `persistent`/`network`, и
`additionalProperties: false` — то есть **как публичный API отобразил бы глобальное монтирование,
контрактом не определено**. Проверить это можно только имея реальный глобальный том (его у нас нет).

---

## LLAMA.CPP

| | |
|---|---|
| Direct Global Volume load | **NOT RUN** — глобального тома не существует, тестировать нечего |
| mmap | **NOT RUN** (и остаётся **недокументированным**: хранилище объектное, «not a fully POSIX compliant file system», без блокировок, atomic rename, hard links и permission bits) |
| Load time | — |
| Local staging required | **UNKNOWN** (см. ниже оценку запросов) |
| Generation | **NOT RUN** |

Мы **не** создавали Pod только чтобы «попробовать» объектный том: сравнивать было бы нечего, а §9
предписывает сначала получить путь автоматизации. Позиция честная: совместимость `mmap` с глобальным
томом **не проверена и не может считаться данной**; при 20+ ГБ GGUF и объектном бэкенде это главный
технический риск варианта. Наш собственный `flock` (в `/tmp`) от семантик тома не зависит.

---

## MULTI-DC

| | |
|---|---|
| Datacenters considered | Живое ранжирование не выполнялось: автоматического пути нет, а Pod создавать нечем |
| Selected GPU | — |
| Selected DC | — |
| **Automatic (кросс-ДЦ без закрепления)** | **PASS в политике, NOT RUN живьём.** `Placement.storage_type = global_volume` уже **не** закрепляет датацентр, а кандидаты строятся по **всем** датацентрам, которые сам каталог считает доступными (реализовано и покрыто тестами в `1d02d84`) |
| **BALANCED без «привязки к ДЦ из-за хранилища» (§11)** | **PASS by construction**: при одном логическом placement позиция placement одинакова у всех кандидатов, поэтому порядок решают доступность (фильтр + сток) и цена — ровно то, что требует §11 |
| One Pod only | **PASS** (single-flight и правило «один Pod» не менялись) |
| Search ≤ 60 sec | **PASS** (общий бюджет 60 с не менялся) |

**Важно:** архитектурно требование §10/§11 уже выполнено на уровне политики — если провайдер откроет
подключение глобального тома, включение сводится к одному значению (`storage_type`) и одному
placement'у, без переписывания аллокатора. Это и было смыслом абстракции из прошлого шага.

---

## STORAGE COST (§17)

| Вариант | Хранение | Запросы | Итого оценка |
|---|---|---|---|
| **Два региональных тома 50 GB** (отменено) | 2 × 50 × $0.07 = **$7.00/мес** | — | **$7.00/мес** |
| **Один существующий региональный 50 GB** (`uwgeaie5b0`) | **$3.50/мес** | — | **$3.50/мес** |
| **Один Global Volume** | **unknown** — цены $/GB-месяц в документации нет | Class A $0.005/1 000, Class B $0.0005/1 000 | **unknown** |

Оценка **запросной** части на одну холодную загрузку модели (сама цена запросов известна, размер
файла — нет): при чтении 1 МиБ блоками ~20 GiB → ≈20 500 чтений → **≈$0.01**; при чтении 128 КиБ
блоками → ≈164 000 чтений → **≈$0.08**. То есть запросы на загрузку — центы, но повторяются на
**каждом** холодном старте (idle-стоп — 10 минут, значит холодные старты часты). Стоимость
миграции/GPU-теста: **$0 потрачено**, ничего не запускалось.

| | |
|---|---|
| Old regional Volume | **still retained** (`uwgeaie5b0`, US-TX-3, не тронут) |
| Final persistent volume count | **1** (без изменений) |

---

## VERDICT

**BLOCKED — RUNPOD GLOBAL VOLUME AUTOMATION NOT YET EXPOSED**

Все пять условий §16 выполнены и доказаны выше: живой REST v2 не поддерживает; GraphQL не
поддерживает; CLI/SDK не поддерживает; `templateId` не сохраняет хранилище (шаблоны отвергают
`network` с 422); иного поддерживаемого механизма не найдено. Второй региональный том **не создан**.

---

## ОДИН ДЕШЁВЫЙ ТЕСТ, КОТОРЫЙ МОЖЕТ ЭТО РАЗБЛОКИРОВАТЬ (и он не нарушает §5)

Единственная недоказанная возможность — что живая реализация принимает **глобальный** id в
**документированном** поле `mounts.network.volumeId`, хотя описание поля говорит «NetworkVolume в том
же датацентре». Это проверяется **публичным** REST-вызовом, без приватных эндпоинтов и без браузера:

1. **Вы** создаёте один Global Volume в вашей консоли (Storage → New volume → Global volume) — это
   ваше действие, у меня нет консольного доступа. Цена хранения будет показана вам там же, в
   консоли, до подтверждения.
2. Вы сообщаете мне его **ID** (ID не является секретом; ключи не нужны).
3. Я делаю **один** ограниченный вызов публичного API: `POST /v2/pods` с
   `mounts.network = [{volumeId: <global id>, path: "/workspace"}]` и смотрю ответ:
   * **принят** → автоматизация существует через документированный API: дальше переносим модель,
     проверяем `mmap`/время загрузки и включаем `storage_type=global_volume`;
   * **отказан** (`400/404/422`) → вопрос закрыт окончательно, остаёмся на одном региональном томе
     (текущее состояние, $3.50/мес) до появления поддержки в API.

Пока вы этого не сделаете, правильная позиция — **ничего не менять**: текущий региональный том
исправно работает, живой ИИ-тест блокирован внешней ёмкостью провайдера, а не хранилищем.

---

## RELEASE STATE

| | |
|---|---|
| `main` смёржен | **NO** |
| тег `v1.2.0` | **NO** |
| стабильный манифест | **NOT PUBLISHED** |
| Пересборка sidecar/установщика (§18) | **НЕ выполнена** — по §18, пока архитектура хранилища не решена |
| Второй региональный том | **НЕ создан** |
| `uwgeaie5b0` | **НЕ удалён** |
| Потрачено | **$0** |
