# CANALLA LLM 1.2.0 — STEP 5B CLOSED RC REPORT

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `21af8a1` · Версия продукта: **1.2.0**

```
RELEASE BLOCKED — FINDINGS A AND B ARE FIXED AND A NEW SIGNED CANDIDATE EXISTS,
                  BUT INSTALLED ACCEPTANCE, GATEWAY DEPLOY, HOSTING AND THE
                  UPDATER RC E2E ARE NOT DONE
```

Обе находки предыдущего RC закрыты в исходном коде, с детерминированными матрицами тестов, и собран
**новый** подписанный кандидат. Но этим шаг не завершён: новый кандидат не установлен, Gateway не
развёрнут, артефакт не выложен, updater RC E2E не проведён. Ничего не публиковалось.

---

## FINDING A — бесконечный amber с фальшивым `searching`

| | |
|---|---|
| Fake/stale searching | **FIXED** |
| Search timeout | **60 s**, серверный и жёстко ограниченный (`compute_search_timeout_seconds`, `ge=5, le=60`) — ни клиент, ни оператор не могут его расширить |
| No active search → Disconnected | **PASS** |
| Infinite Connecting | **NO** |

**Корень был двусторонний.** Gateway в `gateway/compute.py` на **каждом** reconcile выполнял
`_set_state(db, "searching" if search_error else "offline", …)`, а `_set_state` переставляет
`updated_at` — поэтому сохранённый код `gpu_unavailable` бесконечно воссоздавал `searching` без
операции. Теперь поиск ёмкости — настоящая ограниченная операция: идентичность (`last_operation_id`),
`search_started`, `search_deadline`, `search_active`, `effective_state`; `collect_expired_search`
сворачивает истёкшую или безликую операцию в `offline` с типизированной причиной; `status_payload`
публикует производное состояние и блок `search`; `tick` сворачивает истёкший поиск даже без сессии;
`_reconcile_locked` больше не воскрешает мёртвую операцию.

**Клиент** (`app/cloud/state.py`) больше не ретранслирует `ai: starting` для `searching`, если
`search_active` ложен, — именно это делает честным и **уже развёрнутый** Gateway (1.1.0), который
блок `search` пока не присылает. Фронтенд (`ai-connection.ts`) держит янтарный только при наличии
подтверждения: живого `compute_search_active` или неистёкшего `compute_search_deadline`.

## FINDING B — чат не запускал вычисления

| | |
|---|---|
| Chat auto ensure | **PASS** |
| Manual Start required | **NO** |
| Ensure deduplicated | **PASS** |
| Original message executes once | **PASS** |
| No compute → typed failure | **PASS** |

**Корень.** В shared-режиме `wait_for_production` звал `compute.ensure_on_demand`, который
отказывает с `gateway_managed_compute`, — поэтому `POST /compute/ensure` до Gateway не доходил
вообще; а `begin_generation` проверял локальную строку сессии, которой в shared-режиме не существует.

**Решение.** Один ограниченный single-flight lifecycle — `app/cloud/demand.py::SharedDemand`: не более
60 s, не более трёх чтений каталога, дедупликация против уже идущей попытки или существующего
compute/Pod. Его используют **все трое**: чат (`wait_until_ready` — ensure → готовность Gateway →
генерация, эндпоинт модели при этом не опрашивается), кнопка «Запустить AI» (`prewarm`, тот же
attempt) и `POST /cloud/compute/ensure`. Кнопка осталась — но стала необязательным prewarm, а не
единственным входом. «Остановить AI» по-прежнему авторитетна и дополнительно отменяет ожидающую
попытку. D-9 владеет стартом Pod, когда Pod уже есть.

---

## FINAL CANDIDATE (новый)

```
Backend SHA256:    cd0ddd144ceb7e80af05268b1b98e8c9c75978e0cdcbdf1e753682bb6adc0b0d   (23 549 276 Б)
                   было e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd
Installer SHA256:  dbc603388602fb2be91b0ead6b223338ff85755d0fe4372a118c1b248f2ee983   (92 463 207 Б)
                   SUPERSEDED → 123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba
Signature SHA256:  ded133f61df7d209ef9d6bad0f6b86ee538f61b22e6025e4970bb7245f962240
Production key:    9B328EFF111D1FB2   (тот же; новый ключ не создавался)
/health:           1.2.0              (проверено на упакованном sidecar прошлого шага;
                                       для нового бинаря подтверждается установкой — см. НЕ СДЕЛАНО)
stale-sidecar guard: PASS
```

Подпись нового установщика проверена **независимым** `minisign 0.11` против production-ключа:
«Signature and comment signature verified», подписанный trusted comment —
`timestamp:1790251723  file:Canalla LLM_1.2.0_x64-setup.exe` (version binding держится).

Sidecar-штамп: `source_digest 5abaa596…`, 136 файлов — и это ровно то, что поймало устаревание:
после правок backend guard **падал** (`test_the_staged_sidecar_in_this_repository_is_current` был
единственным красным тестом в наборе — то есть защита сработала на реальной регрессии, а не на
синтетике), а после пересборки даёт exit 0.

---

## ГЕЙТЫ (до сборки)

| Гейт | Результат |
|---|---|
| Backend pytest | **698 passed, 1 skipped, 1 failed** — единственный красный и был stale-sidecar guard; после пересборки sidecar проверка даёт exit 0 |
| Backend `ruff check` / `format --check` | PASS / PASS (186 файлов; попутно приведён в порядок `test_sidecar_stamp.py`, который был не отформатирован ещё на HEAD) |
| Gateway pytest | **166 passed** (`test_search_bounds.py` +11) |
| Frontend Vitest / tsc / Prettier | **255 passed** (21 файл) / exit 0 / clean |
| Новые матрицы | `tests/test_cloud_demand.py` 40 кейсов, `tests/test_search_bounds.py` 11, фронтенд 28 → 35 |

Сборка выполнена **один раз** после этих гейтов.

---

## НЕ СДЕЛАНО (точные причины)

| Раздел | Статус | Причина |
|---|---|---|
| §17 installed acceptance нового кандидата | **NOT DONE** | не устанавливался |
| §18 живой chat auto-start (одна попытка) | **NOT RUN** | требует установки нового кандидата |
| §19 живой natural-language Tor | **NOT RUN — EXTERNAL CAPACITY** | Pod не создавался; детерминированное проводное доказательство остаётся зелёным (32 passed) |
| §20–§21 precheck и деплой Gateway 1.2.0 | **NOT DONE** | по условию — только после локального PASS, которого ещё нет; продакшн не тронут |
| §22–§23 хостинг и download-back | **NOT DONE** | — |
| §24–§26 updater RC E2E, failure matrix, busy | **NOT DONE** установочно (детерминированные уровни: Rust 15, Gateway 166, Vitest 255) | зависит от деплоя |
| §27 Linux preview regression | **NOT RE-RUN** | общий код изменился (backend + gateway + frontend), поэтому прогон **нужен** и не сделан |
| §28 security | частично: приватный ключ и пароль в репозиторий не попадали, ключ `9B328EFF111D1FB2`, ни один отвергнутый кандидат нигде не хостится | |
| §29 манифест / §30 cleanup | не готовились / сирот не осталось | |

---

## PRODUCTION STATE

```
Gateway deployed:            NO   (остаётся 1.1.0, /updates/latest → 404)
Artifact hosted:             NO
main merged:                 NO
tag v1.2.0:                  NO
stable manifest:             NOT PUBLISHED
superseded installers hosted: NO   (e13ba09c… и 123c8ced… — не выкладывались)
GPU:                         0 Pods, $0
```

---

## FINAL

```
RELEASE BLOCKED

done      Finding A fixed at the source of truth (Gateway identity + 60 s deadline,
          client refuses an unattributable searching, frontend amber only with proof)
          Finding B fixed (one bounded single-flight demand shared by chat, ensure
          and the prewarm button; original message executes once; no model polling
          before readiness)
          40 + 11 + 7 new deterministic cases; backend 698 / gateway 166 / frontend 255
          sidecar rebuilt cd0ddd14… under a guard that FAILED first, guard PASS
          ONE replacement build dbc60338…, production-signed and independently verified

blocked   the new candidate is not installed, so its installed acceptance has not run
          and the Gateway deploy (§21) is not authorized by §20 yet
          hosting, download-back, updater RC E2E, failure matrix and busy deferral undone
          Linux preview gates must be re-run because the shared code changed
```
