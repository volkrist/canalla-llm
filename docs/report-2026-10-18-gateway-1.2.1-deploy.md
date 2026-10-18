# Gateway 1.2.0 → 1.2.1 — production deploy

Дата: 18.10.2026, ветка `integration/canalla-1.2.1`. **Deploy выполнен.**
Pods: 0 · новые GPU/CPU ресурсы: 0 · публикация Windows-релиза: не выполнялась.

## 1. До / после

| | До | После |
|---|---|---|
| Release (`current`) | `releases/c0956b6ee939` | **`releases/78cf9309066b`** |
| `/health` version | 1.2.0 | **1.2.1** |
| Protocol | 1 | 1 |
| Alembic | `0006_capacity_wait` | `0006_capacity_wait` (новых миграций нет) |
| `RUNPOD_REPLICAS` bootstrap | `1.2.0` | **`1.2.1`** |
| `RUNPOD_DATACENTERS` | US-TX-3 + US-MO-1 | без изменений |

Дельта кода между `c0956b6` и `78cf930` в Gateway/backend — только строки версии:
`gateway/config.py` и `backend/app/product.py`. Requirements не изменились.

## 2. Артефакт

`git archive` из HEAD `78cf9309066b`, SHA256 `ee1db1ccd4e4bb8906da86f81741ecdb9b29d084ff5cdf5a8be0ae0f7888155d`,
145 файлов; в релизе лежит `MANIFEST.sha256`. Права `root:alex-gateway`, каталоги 750, файлы 640.

## 3. Preflight

До переключения, на новом релизе и новой env:

```
GATEWAY VERSION: 1.2.1
PLACEMENTS: [('US-TX-3', 'uwgeaie5b0'), ('US-MO-1', 'y92bt25ru8')]
REPLICAS: [('y92bt25ru8', '1.2.1', 'b11565', True)]
REFUSALS: {}
```

## 4. Переключение

`systemctl stop` → атомарная замена symlink `current` → атомарная замена
`/etc/alex-gateway/alex-gateway.env` (rename) с единственным изменением
`"bootstrap_version":"1.2.0"` → `"1.2.1"` → `systemctl start`. Downtime ≈ 1 с.
При отсутствии `/health` скрипт автоматически вызывал rollback.

## 5. Проверки после deploy

| Проверка | Результат |
|---|---|
| `/health` loopback и публичный | 200, `version 1.2.1`, `protocol 1`, `ready:true`, `database:ok` |
| Process env | `RUNPOD_DATACENTERS` оба региона; `bootstrap_version 1.2.1` |
| Placements | **US-TX-3 + US-MO-1**, оба разрешены |
| `REFUSALS` | `{}` — `wrong_runtime` для US-MO-1 нет |
| Операторский потолок | не задан (`0` = unset) → действует пользовательский `max_hourly_price` |
| БД | revision 0006; 41 installations, 14 sessions, 1 compute `stopped`, 44 кода, 233 audit — без изменений |
| Роли | `[('member', 41)]` — без изменений |
| Pods | **0** |
| Volumes | `y92bt25ru8` (US-MO-1) и `uwgeaie5b0` (US-TX-3), 50 GB каждый |
| nginx / 12Testers | nginx active, `nginx -t` успешен, `12testers.store` отвечает 301 |
| Клиент Canalla 1.2.1 | `/health` 1.2.1, Computer `ready`, Tor `ready`, баланс доступен, `active_session=null` |
| Совместимость старых клиентов | protocol остался 1; клиент проверяет только `gateway_protocol_version` |
| Free tests | `test_capacity_wait` + `test_compute` + `test_entitlements` — **77 passed** |

## 6. Backup и rollback

* БД: `/var/lib/alex-gateway/backups/gateway-20261018T134147Z-pre-1.2.1.db`,
  `integrity_check=ok`, счётчики совпадают.
* Конфиги: `/var/backups/alex-gateway-pre-1.2.1-20261018T134147Z/` (0700, root) —
  `alex-gateway.env`, `runpod.env`, unit, `requirements.txt`, tar прежнего релиза.
* Rollback: `sudo /var/backups/alex-gateway-pre-1.2.1-20261018T134147Z/rollback.sh` —
  stop → symlink на `c0956b6ee939` → возврат env → start → проверка `/health`.
  БД не откатывается: новых миграций не было.

## 7. HEAD

Deployed release: `78cf9309066b` (commit `78cf930`). Ветка `integration/canalla-1.2.1`;
`main` и теги не менялись.
