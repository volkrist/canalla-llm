# CANALLA LLM 1.2.1 — ПОДКЛЮЧЕНИЕ ВТОРОГО РЕГИОНА RUNPOD К GATEWAY

Дата: 13.10.2026. Ветка `integration/canalla-1.2.1`. **Deploy выполнен.** Gateways работает, US-TX-3
сохранён, US-MO-1 подключён как второй placement, лишних Pod нет, расход не изменился.

## 1. Production Gateway до / после

| | До | После |
|---|---|---|
| Release (`current`) | `releases/1994c0ab2016` | **`releases/c0956b6ee939`** |
| Alembic revision | `0001_gateway_core` | **`0006_capacity_wait`** |
| `RUNPOD_DATACENTERS` | не задан (только primary) | `US-TX-3:uwgeaie5b0,US-MO-1:y92bt25ru8` |
| `RUNPOD_REPLICAS` | не задан | запись реплики MO-1 (561 байт) |
| `/health` | `ready:true`, version 1.2.0 | `ready:true`, version 1.2.0 |
| Placement'ов в политике | 1 (US-TX-3) | **2** (US-TX-3 первым, US-MO-1 вторым) |

## 2. Применённые миграции

`0002_stop_attempt`, `0003_ready_probe`, `0004_session_placement`,
`0005_installation_entitlements`, `0006_capacity_wait` — **все аддитивные**: nullable-столбцы, а
не-nullable (`role`, `wait_active`, `wait_attempts`, `wait_background`) — с `server_default`. Ни одна
строка не переписана. Данные до/после **идентичны**:

```
installations 41 · gateway_sessions 14 · gateway_compute 1 · enrollment_codes 44 · audit_events 233
roles: [('member', 41)]   активных (не revoked): 3   compute: state=stopped, wait_active=0
```

**REGYNA** — активна, `role=member`, version 1.2.0, `last_seen 2026-10-13T11:21:29Z` (сегодня):
доступ и enrollment не затронуты. Никто не получил operator-роль и никто не заблокирован.

## 3. Staging (бесплатно, до production)

| Набор | Результат |
|---|---|
| `apps/gateway` pytest | **260 passed** |
| `apps/backend` pytest | **923 passed, 6 skipped** |

Покрытие обязательных сценариев A–N существующими тестами: failover между placement'ами и
восстановление после рестарта (`test_multiregion`), durable waiting + backoff + отмена + «GPU
появилась» (`test_capacity_wait`, 13 тестов), бюджетные потолки и A10/A11 (`test_entitlements`, 12),
один Pod при гонке/повторе/неоднозначном create (`test_compute`), сохранение installations и
`role=member` по умолчанию (`test_migrations`), лимиты пользователя и цена (`test_compute`).

## 4. Реальная конфигурация регионов (без секретов)

```
RUNPOD_DATACENTERS=US-TX-3:uwgeaie5b0,US-MO-1:y92bt25ru8
RUNPOD_REPLICAS={"replicas":[{"volume_id":"y92bt25ru8","datacenter":"US-MO-1", …,"ready":true}]}
```

Эффективная политика, прочитанная **из работающего кода**:

```
PLACEMENTS: [('US-TX-3','uwgeaie5b0',False,'regional_network_volume',True),
             ('US-MO-1','y92bt25ru8',False,'regional_network_volume',True)]
REFUSALS: {}
POLICY: {"cloud_tiers":["SECURE"],"community_allowed":false,"community_blocked_by":"network_volume",
         "datacenters":[US-TX-3:uwgeaie5b0, US-MO-1:y92bt25ru8],"candidate_limit":3,
         "allocation_timeout_seconds":60,"operator_max_hourly_price_usd":null}
```

* Primary **не отключён**: он всегда первый, из настроек `runpod_network_volume_id`/`runpod_datacenter`
  (уже `uwgeaie5b0`/`US-TX-3`). Секция `US-TX-3:…` в `RUNPOD_DATACENTERS` — документированный no-op.
* Запись TX-3 **не выдумывалась** и не добавлялась (её наличие могло бы отвергнуть primary).
* `RUNPOD_RUNTIME_VERSION` оставлен **пустым** → версия рантайма не проверяется.
* Community выключен; операторского потолка цены нет → действует **пользовательский**
  `max_hourly_price`.

### Найденный и исправленный дефект совместимости

Ожидание Gateway держит `bootstrap_version = settings.version` = **`1.2.0`**, а наша запись реплики
говорила `1.2.1` → вердикт `wrong_runtime` → **резерв молча не использовался бы** при «здоровом»
fail-closed гейте. Запись приведена к `1.2.0`, и добавлен тест
(`test_the_shipped_reserve_record_matches_this_build`), который ловит такое расхождение в CI.

## 5. Статус deployment

**ВЫПОЛНЕН.** Остановка сервиса → переключение symlink на `c0956b6ee939` → `alembic upgrade head`
(0001→0006) → восстановление владельца БД → старт. Затем отдельным шагом включены два placement'а и
сервис перезапущен. Проверено, что **сам процесс сервиса** получил переменные
(`/proc/<pid>/environ`: `RUNPOD_DATACENTERS` верный, `RUNPOD_REPLICAS` 561 байт и парсится JSON).

Downtime — две короткие паузы (миграция ~2 с; включение конфигурации ~2 с).

## 6. US-TX-3 и US-MO-1 как placement

* Публичный endpoint: `https://gateway.12testers.store/health` → **200**, `ready:true`, `database:ok`.
  (403 при проверке из Python был только блокировкой Cloudflare по User-Agent, не регресс: curl и
  клиентский UA получают 200 и с VPS, и извне.)
* Оба placement'а в политике, `REFUSALS` пуст ⇒ резервная реплика принята и кандидаты из US-MO-1
  допускаются, при этом US-TX-3 остаётся первым.
* nginx/12Testers не тронуты (vhost mtime 24.09, `nginx` active).

## 7. Пользовательские лимиты

`operator_max_hourly_price_usd: null` (операторского потолка нет) ⇒ цену определяет **пользователь**
в Canalla; `max_hourly_price`/`session budget`/idle-stop не менялись. Тесты подтверждают: клиент не
может поднять бюджет выше выданного оператором, а самый дешёвый подходящий GPU внутри лимита
выигрывает. `roles: [('member', 41)]` — никому не выдано лишних прав.

## 8. Отсутствие новых оплачиваемых Pod

```
PODS: 0 []   VOLUMES: 2 (uwgeaie5b0 US-TX-3, y92bt25ru8 US-MO-1)
BALANCE: 14.4027997129   ← не изменился за деплой
```

Compute не создавался: `gateway_compute.state=stopped`, `wait_active=0`. GPU-тест **не запускался**.

## 9. Backup и rollback

Сделано до изменений:

* БД: консистентный online-бэкап через SQLite backup API →
  `/var/lib/alex-gateway/backups/gateway-20261013T124102Z-pre-multiregion.db`
  (`integrity_check=ok`, 41/14/1/44/233).
* Конфиги и unit: `/var/backups/alex-gateway-pre-multiregion-20261013T124102Z/` (только root, 0600) +
  `…-pre-multiregion-enable.env` (копия env перед включением регионов).
* Указатель деплоя: `current → releases/1994c0ab2016` (все прошлые релизы сохранены).

**Rollback (подготовлен, НЕ выполнялся):** вернуть symlink на `1994c0ab2016` → при необходимости
восстановить файл БД из бэкапа → `systemctl restart`. Миграции аддитивные, поэтому предыдущий релиз
работает и на новой схеме — откат бинарника сам по себе безопасен.

## 10. Git HEAD

`c0956b6` (исправление записи реплики + регрессионный тест). Деплоенный релиз =
`/opt/alex-gateway/releases/c0956b6ee939`.

## 11. Открытые блокеры

1. **CI:** `tor-runtime` и `installer-build` падают на сетевой загрузке Tor
   (`dist.torproject.org`/`www.gnu.org`) — инфраструктурная флейка, не связана с Gateway.
2. **Windows installer 1.2.1** — интерактивная приёмка не выполнена.
3. **Публикация 1.2.1** — не начиналась.
4. Не выполнена живая проверка клиентом (enrollment/`/compute/status` с реальной установки) — для
   неё нужен реальный клиент; статические + CLI-проверки пройдены, compute не создавался.

## Production changes: Gateway 1994c0ab2016 → c0956b6ee939 + 5 миграций + 2 placement'а. GPU spend: $0.
