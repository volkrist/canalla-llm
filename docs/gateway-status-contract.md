# Контракт состояния подключения Gateway → интерфейс (1.2.1)

Документ для агента интерфейса/интегратора, который исправляет **A30–A32**. Описывает
машиночитаемый блок `connection` в `GET /compute/status` — аддитивный, только для чтения,
безопасный для опроса. Gateway не переписывался под интерфейс: добавлены только новые поля,
существующие не изменены.

## 1. Где это

`GET /compute/status` (Bearer-токен установки) возвращает payload, в котором уже были:
`state`, `ai`, `ai_label`, `revision`, `error_code`, `detail`, `message`, `configured`, `managed`,
`adopted`, `session`, `last_session`, `idle_deadline`, `startup_deadline`, `search`, `policy`,
`allocation`, `updated_at`. Добавлен один блок:

```json
"connection": {
  "operation_id": "op-abc…",
  "started_at": "2026-10-11T12:00:00+00:00",
  "startup_deadline": "2026-10-11T12:05:00+00:00",
  "last_success_at": null,
  "observed_at": "2026-10-11T12:00:04+00:00",
  "current_stage": "starting",
  "terminal_state": false,
  "typed_error": null,
  "readiness_valid_until": null
}
```

## 2. Поля

| Поле | Тип | Смысл |
|---|---|---|
| `operation_id` | `string \| null` | Идентификатор операции, владеющей текущей попыткой. Для поиска ёмкости это id из `POST /compute/ensure` / `POST /compute/stop`; без него состояние перехода не обещается. |
| `started_at` | `string \| null` (ISO-8601 UTC) | Начало текущей попытки: открытие поиска, иначе `intent_at`/`created_at`/`started_at` сессии. Никогда не внутрипроцессный таймер. |
| `startup_deadline` | `string \| null` | Дедлайн D-9 (`COMPUTE_STARTUP_TIMEOUT_SECONDS`, по умолчанию 300 с) от персистентного времени интента. `null`, когда фаза старта не идёт. |
| `last_success_at` | `string \| null` | Время последнего доказанного успеха, релевантного текущей фазе: момент подтверждённой готовности модели (`ready_probe_at`/`ready_at`). `null`, пока готовность не доказана. |
| `observed_at` | `string` | Момент, на который сервер построил этот снимок (`updated_at` control-строки). |
| `current_stage` | `string` | Производная фаза (см. §3). Клиент **не** должен выводить её сам из `state`. |
| `terminal_state` | `bool` | `true` — попытка завершена (успешно или с типизированной причиной), автоматического перехода больше не ожидается. `false` — идёт переход. |
| `typed_error` | `string \| null` | Типизированный код ошибки текущего состояния (то же, что `error_code`). |
| `readiness_valid_until` | `string \| null` | Момент истечения заявки о готовности (`ready_probe_at + COMPUTE_READY_TTL_SECONDS`, по умолчанию 30 с). Только для `ready`/`generating`. `null` в остальных фазах. |

## 3. Соответствие `state` → `current_stage`

| `state` | `current_stage` |
|---|---|
| `offline` | `idle` |
| `searching` | `searching` |
| `waiting_capacity` | `waiting` |
| `gpu_found` | `allocating` |
| `creating` | `creating` |
| `starting_pod` | `starting` |
| `loading_model` | `loading` |
| `ready` | `ready` |
| `generating` | `generating` |
| `stopping` | `stopping` |
| `stopped` | `stopped` |
| `create_unknown` | `unknown` |
| `external_compute` | `external` |
| `multiple_compute` | `error` |
| `error` | `error` |
| `not_configured` | `unconfigured` |

`terminal_state = true` для `idle`, `stopped`, `ready`, `generating`, `error`, `multiple_compute`,
`external_compute`, `unconfigured`; `false` для переходов
(`searching`/`allocating`/`creating`/`starting`/`loading`/`stopping`/`unknown`/`waiting`).

## 4. Примеры состояний

**Поиск ёмкости (живое окно 60 с)**
```json
{"state":"searching","search":{"operation_id":"op-1","active":true,"deadline":"…"},
 "connection":{"operation_id":"op-1","current_stage":"searching","terminal_state":false,
               "typed_error":"gpu_unavailable","startup_deadline":null,"readiness_valid_until":null}}
```
Amber «Подключаюсь…» законен **только** пока `search.active == true`. После дедлайна сервер
сам схлопывает окно: `state` → `offline`, `current_stage` → `idle`, `terminal_state` → `true`,
`typed_error` → `gpu_unavailable`

**Старт Pod → загрузка модели**
```json
{"state":"starting_pod","startup_deadline":"…+300s",
 "connection":{"current_stage":"starting","terminal_state":false,"startup_deadline":"…+300s"}}
```

**Готово (модель отвечает алиасом)**
```json
{"state":"ready","ai":"ready",
 "connection":{"current_stage":"ready","terminal_state":true,"typed_error":null,
   "last_success_at":"…","readiness_valid_until":"…+30s"}}
```
Пока `readiness_valid_until` не истёк, сервер сам перепроверит модель и обновит `last_success_at`.
Истёк без успешной перепроверки → `state` уходит из `ready` (см. ниже).

**Модель перестала отвечать (в пределах grace)**
```json
{"state":"loading_model","ai":"starting","error_code":"model_unavailable",
 "connection":{"current_stage":"loading","terminal_state":false,"typed_error":"model_unavailable"}}
```
Это честное «ещё подключаюсь», не зелёный. По истечении `COMPUTE_READY_GRACE_SECONDS` (120 с)
Pod останавливается: `state` → `stopped`, `terminal_state` → `true`, `typed_error` →
`model_unavailable`.

**Создание с неясным ответом**
```json
{"state":"create_unknown","ai":"waiting",
 "connection":{"current_stage":"unknown","terminal_state":false,"typed_error":"create_unknown"}}
```
Второй Pod не создаётся: Gateway наблюдает за провайдером и либо принимает существующий Pod, либо
закрывает интент по свидетельству провайдера.

**Внешний/не её compute**
```json
{"state":"external_compute","ai":"waiting",
 "connection":{"current_stage":"external","terminal_state":true}}
```

## 5. Рекомендации интегратору

* Рисуйте фазу из `connection.current_stage`, а не из `state`: это один словарь на обе стороны.
* «Подключаюсь…» показывайте только при `terminal_state == false`, и только для `searching` —
  при `search.active == true`. Никогда не обещайте переход, который снимок сам опровергает.
* Отсчёт дедлайна берите из `startup_deadline`/`search.deadline` (абсолютное UTC-время), а не из
  локального таймера: сервер — авторитет, клиент может исчезнуть.
* Зелёный статус держите в пределах `readiness_valid_until`; после него дождитесь следующего
  снимка, прежде чем снова показать «готово».
* Retry показывайте при `terminal_state == true` и `typed_error` из переходного набора
  (`gpu_unavailable`, `startup_timeout`, `model_unavailable`).
* Блок только для чтения: поля не влияют на compute, опрос ничего не запускает и не тратит.

## 6. Совместимость

Изменения аддитивны (`connection`, `session.datacenter`, `session.volume_id`,
`policy.operator_max_hourly_price_usd`). Старые поля сохранены без изменения семантики, кроме
`session.max_hourly_price_usd`: теперь это **разрешённый потолок** (максимум пользователя,
возможно ужесточённый оператором — A12), а не цена дешёвого кандидата. Фактическая цена —
`session.hourly_rate_usd`.

## 7. A10/A11 — entitlement и права Stop (1.2.1)

Поля аддитивны; старый клиент их не видит и продолжает работать.

**`caller`** (в `GET /compute/status` и в ответе `POST /compute/ensure`) — read-only блок того,
кто спрашивает и что ему реально разрешено:

```json
"caller": {
  "installation_id": "…",
  "role": "member",
  "max_hourly_price_usd": "1.00",
  "session_budget_usd": "1.00",
  "period_budget_usd": "5.00",
  "period_days": 30,
  "effective_max_hourly_price_usd": "0.52",
  "effective_session_budget_usd": "1.00"
}
```
`null` на любом из `max_hourly_price_usd`/`session_budget_usd`/`period_budget_usd` означает
«оператор не ограничивал» — продуктового потолка нет.

**`POST /compute/stop`** получает необязательное поле `scope`:

| `scope` | Кто может | Что делает |
|---|---|---|
| `global` (по умолчанию, как шлёт 1.2.0) | владелец сессии или оператор | останавливает общий managed Pod (durable Stop) |
| `own` | любая установка | только отзывает собственный спрос; Pod не трогается |

Отказ не-владельца/не-оператора — `gateway_stop_forbidden` (403), Pod остаётся неизменным.

**`POST /compute/emergency-stop`** — только оператор: глобальный Stop общего Pod; для обычной
установки — `gateway_stop_forbidden`. Аварийный Stop идёт тем же durable-путём, что и обычный
(RunPod 500/timeout сохраняет intent и повторяется).

**`POST /compute/cancel`** — отменяет собственные in-flight генерации вызывающей установки
(и только их). Под не трогается; генерация другой установки не затрагивается.

Роль и entitlement назначаются только локальным операторским CLI:
`gateway.cli set-role --installation-id … --role operator|member` и
`gateway.cli set-entitlement --installation-id … [--max-hourly-price …] [--session-budget …]
[--period-budget …] [--period-days …] | --clear`. Ни одно поле тела запроса не влияет ни на роль,
ни на entitlement.

## 8. A7/Task-1 — постоянное ожидание свободной GPU (1.2.1)

Когда подходящей свободной карты нет, пользователь может оставить поиск включённым. Отдельная
попытка по-прежнему ограничена (`compute_search_timeout_seconds`, 60 с), но *ожидание между
попытками* не ограничено произвольно: Gateway повторяет проверку на собственном backoff
(`compute_wait_backoff_seconds` → `compute_wait_backoff_max_seconds`) до появления карты или
отмены пользователем. Состояние `waiting_capacity` — реальный незавершённый переход, но **без Pod**.

Блок `wait` аддитивен (как `connection`), только для чтения:

```json
"wait": {
  "active": true,
  "operation_id": "op-abc…",
  "installation_id": "…",
  "started_at": "2026-10-11T12:00:00+00:00",
  "elapsed_seconds": 4242,
  "attempts": 9,
  "next_attempt_at": "2026-10-11T13:10:42+00:00",
  "reason": "gpu_unavailable",
  "background_start": false
}
```

| Поле | Тип | Смысл |
|---|---|---|
| `active` | `bool` | Идёт ли ожидание. `false`/`null` в остальных состояниях. |
| `operation_id` | `string \| null` | Операция, открывшая ожидание. |
| `installation_id` | `string \| null` | Чья установка держит намерение. |
| `started_at` | `string` | Когда ожидание открылось; не сбрасывается повторными попытками. |
| `elapsed_seconds` | `int` | Сколько уже ждём (по часам сервера). |
| `attempts` | `int` | Сколько раз Gateway уже попробовал (backoff — от этого числа). |
| `next_attempt_at` | `string` | Когда разрешена следующая попытка. |
| `reason` | `string` | Типизированная причина (`gpu_unavailable` / `price_limit` / `no_compatible_gpu` / код ошибки провайдера). |
| `background_start` | `bool` | Явное разрешение пользователя создать **платный** Pod при закрытом приложении. Ожидание само по себе его не даёт. |

**`POST /compute/ensure`** получает два необязательных поля запроса (оба по умолчанию `false`):
`wait_for_capacity` и `background_start`. Оба — предпочтения пользователя, сервер их не выдумывает.

**`POST /compute/wait/cancel`** — пользователь прекращает ожидание. Освобождает намерение и
запрещает Pod по нему; **не** трогает чужую активную генерацию и не останавливает общий Pod.

Пример:
```json
{"state":"waiting_capacity","ai":"starting","error_code":"gpu_unavailable",
 "wait":{"active":true,"elapsed_seconds":4242,"background_start":true},
 "connection":{"current_stage":"waiting","terminal_state":false,
               "typed_error":"gpu_unavailable","startup_deadline":null}}
```

Рекомендация интерфейсу: рисуйте ожидание из `wait.active` (фаза `waiting`), показывайте
`elapsed_seconds` и не навязывайте клиентский дедлайн — ожидание не ограничено. Если снимок
перестал обновляться (Gateway недоступен / приложение закрыто), ожидание перестаёт быть честным
переходом и уходит в типизированный отказ — как и любой другой устаревший снимок.
