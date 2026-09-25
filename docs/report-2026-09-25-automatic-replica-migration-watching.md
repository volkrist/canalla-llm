# CANALLA LLM — AUTOMATIC REPLICA MIGRATION (WATCHING US-TX-3)

**Задание:** автоматически поймать ёмкость в US-TX-3, мигрировать модель на верифицированную
вторичную реплику, подготовить два production placement'а — без остановки на «BLOCKED».
**Дата:** 25.09.2026 · **Ветка:** `release/canalla-1.1.0`
**Платные ресурсы сейчас:** **НЕТ** (0 Pod'ов, 0 томов) · **потрачено за проход:** $0.00
**Релиз:** `main` не смёржен, тега нет, манифест не выложен, Canalla не пересобиралась.

---

# WATCHING US-TX-3

```
Watcher:              RUNNING (отдельный процесс, независим от этой сессии)
PID:                  см. `artifacts/runpod-tx3-watcher/watcher.pid` (на момент отчёта — 18072;
                      лаунчер venv-питона — 18572). PID намеренно не зашит: watcher перезапускается,
                      авторитетный источник — pid-файл и сам lock
Poll:                 каждые 120 секунд (только бесплатный catalogue/read-only)
Paid resources:       NONE
Secondary Volume:     NOT CREATED
Paid resource guard:  CPU-попытка не чаще одного раза в 15 минут; Pod-create только при
                      реальном сигнале каталога либо в рамках этого cooldown
Single-instance:      OS byte-range lock (watcher.lock) + pid-файл
```

Артефакты (все пути — от корня репозитория):

| Файл | Что содержит |
|---|---|
| `artifacts/runpod-tx3-watcher/state.json` | фаза, число поллов, кандидаты, манифест модели, выбранный ДЦ, том, копия, тест |
| `artifacts/runpod-tx3-watcher/watcher.log` | append-only журнал с UTC-таймстампами каждого шага |
| `artifacts/runpod-tx3-watcher/watcher.pid` | PID владельца (для наблюдения) |
| `artifacts/runpod-tx3-watcher/watcher.lock` | тот же lock-файл, который удерживает ОС, а не только запись pid |
| `artifacts/runpod-tx3-watcher/watcher.stdout.log`, `.stderr.log` | stdout/stderr отдельного процесса |
| `scripts/watch-tx3-migration-capacity.py` | сам watcher: `--once`, `--run`, `--status`, `--self-test`, цикл по умолчанию |

---

# ЧТО УЖЕ ПРОИЗОШЛО В ЭТОМ ПРОХОДЕ (живые факты, не предположения)

1. **Ёмкость в US-TX-3 действительно появляется и исчезает минутами.** В 12:20Z каталог показывал
   в US-TX-3 **NVIDIA GeForce RTX 4090 24 GB, $0.74/ч, сток LOW** — в пределах лимита $1.00/ч и
   полностью пригодную для миграции (любой VRAM, LLM на ней не запускается). Через ~15 минут, к
   12:35Z, её уже не было: `US-TX-3 candidates within $1.00/h: 0`.
2. **Попытка CPU всё ещё отклоняется** — `migration cpu refused: HTTP 400` (та же формулировка
   провайдера «no longer any instances available with the requested specifications»). Это не ошибка
   watcher'а: он вернулся в `watching`, что и записано в логе.
3. **Single-instance защита была дырявой и теперь починена.** Живая проверка показала **два** активных
   watcher'а одновременно: pid-файл — это check-then-write, и два процесса, запущенные с разницей в
   минуту, оба «выиграли» его. Два поллера означают две попытки создать платный Pod на одну и ту же
   ёмкость. Теперь:
   * lock — **реальный byte-range lock на `watcher.lock`**, удерживаемый ОС всё время жизни процесса;
   * проверено живьём: второй запуск отвечает `another watcher holds the lock — refusing to start a
     second one` и выходит с кодом 3 (в self-test это отдельная проверка из дочернего процесса);
   * `create_migration_pod` вообще не создаёт Pod, если в аккаунте уже жив **любой**
     `canalla-migrate-*` Pod, а «orphan», проживший больше 15-минутного бюджета, сначала
     **завершается**, и только потом рассматривается новый;
   * `--self-test` — детерминированное офлайн-доказательство этих правил: **11/11 PASS** (без сети,
     без единого платного вызова; POST на `/v2/pods` в self-test'е жёстко возвращает ошибку).
4. **Watcher перезапущен уже с кодом, включающим §R** (живой GPU-тест реплики), и продолжает поллинг.
   Запущен он теперь как **один** процесс на финальном коде этого прохода.

---

# ДЕТЕРМИНИРОВАННЫЕ ТЕСТЫ (офлайн, `--self-test`, 11/11 PASS)

```
PASS  age parses an ISO stamp
PASS  age rejects junk
PASS  only in-budget bookable US-TX-3 capacity is a candidate
PASS  secondary ranking: eligibility, primary exclusion, STANDARD last
PASS  secondary ranking needs real availability
PASS  secondary ranking prefers more eligible GPU types
PASS  price above the operator maximum is never a candidate
PASS  a datacenter without STANDARD volumes is not chosen
PASS  a live migration Pod is never doubled
PASS  an orphan past its budget is reaped once, not re-created
PASS  a second watcher is refused while one is live
self-test: 11/11 passed
```

Ограничения внутри теста проверяются на фиктивном каталоге: карта $0.74/ч в US-TX-3 — кандидат,
карта $1.60/ч — нет (выше лимита $1.00/ч для миграции), карта с `availability=NONE` — нет,
не-Secure карта — нет, карта в другом ДЦ — нет. Для secondary: ≥48 GB, ≤$2.00/ч, только bookable,
PRIMARY исключён, ДЦ без `STANDARD` — в конце списка и не выбирается.

Запуск: `python scripts/watch-tx3-migration-capacity.py --self-test` (exit 0 = все 11 PASS).

Рядом — бэкенд-гейты того же compute-слоя (origin, `/v1/models`-gating, стратегии, реплики):

```
pytest tests/test_allocation_candidates.py tests/test_cloud_demand.py tests/test_model_replicas.py
       tests/test_compute_preferences.py tests/test_cloud.py tests/test_compute.py
       tests/test_compute_connection.py -q
196 passed in 98.56s
```

---

# ЧТО WATCHER СДЕЛАЕТ САМ, КАК ТОЛЬКО ЁМКОСТЬ ПОЯВИТСЯ

Порядок ровно по заданию (E→N, плюс опционально R), каждый шаг с жёстким бюджетом:

| Шаг | Действие | Защита |
|---|---|---|
| E | **один** временный migration Pod в US-TX-3, том `uwgeaie5b0` → `/workspace`, образ alpine | CPU предпочтительно; иначе **самая дешёвая** Secure GPU ≤ **$1.00/ч**; llama.cpp не запускается, модель в VRAM не грузится |
| E | Измерение: точный путь/имя/байты/SHA256 GGUF, **содержимое** `start-llm.sh` и `check-llm.sh` (base64 в лог → файлы в артефактах), список файлов с размерами, бинарь и версия llama.cpp, параметры запуска, required bytes, file count | манифест сохраняется в `primary-manifest.json` — «unknown source without hash» больше не повторится |
| G/H/I | Свежий скан **всех** ДЦ: Secure, STANDARD-тома, ≥48 GB, ≤$2/ч, availability ≠ NONE. Победитель выбирается по: реальная доступность → **число разных подходящих GPU типов** → сильнейший сток → цена | прошлый выбор (US-NE-1/CA-MTL-3) не считается действующим |
| J | Проверка размера: required bytes против 50 GB | если не влезает — **STOP**, ничего не покупается, в отчёт пишется нужный размер |
| K | **Ровно один** `STANDARD` Network Volume 50 GB в выбранном ДЦ | ≤$3.50/мес; второй тома/Global Volume не создаются |
| L | Копирование через **аутентифицированный** короткоживущий HTTP-порт (busybox httpd, basic-auth, случайный пароль), `wget -c` — возобновляемо по каждому файлу | SSH-ключей в аккаунте нет (`{"keys": []}` — проверено), S3-API не покрывает US-TX-3, поэтому выбран этот путь; порт живёт минуты и закрывается вместе с Pod'ом |
| M | Обязательная сверка: SHA256 GGUF, байты, количество файлов, оба скрипта | только при полном совпадении `secondary replica = VERIFIED` |
| R | Живой тест **на одном** GPU ≤$2/ч в целевом ДЦ: монтирование нового тома, `start-llm.sh`, `/v1/models`, **одна** генерация, ответ непустой | только после VERIFIED; Pod завершается сразу после маркеров |
| N | Завершение обоих migration Pod'ов, проверка `running pods = 0` | никаких оставшихся платных Pod'ов |
| O | Запись `secondary-replica.json` (запись реплики) и `placements.env` (`RUNPOD_DATACENTERS`, `RUNPOD_REPLICAS`) | операторские ID не хардкодятся в исходники приложения — только в артефакт конфигурации |
| — | Watcher сам завершается, снимая pid-файл | одноразовая release-операция, не служба |

---

# ВТОРИЧНЫЙ ДЦ — СВЕЖИЙ СКАН (12:45Z, бесплатный, read-only)

| ДЦ | Карта | VRAM | $/ч | Сток | STANDARD тома |
|---|---|---|---|---|---|
| **US-NE-1** | RTX PRO 6000 MIG 2g.48gb | 48 GB | 1.09 | LOW | **да — кандидат** |
| US-MO-1 | L40S | 48 GB | 1.09 | LOW | нет → не может держать реплику |
| US-MD-1 | A100 SXM | 80 GB | 1.59 | LOW | нет → не может держать реплику |

Из 50 строк каталога (33 ДЦ) под фильтр «Secure, ≥48 GB, ≤$2.00/ч, availability ≠ NONE»
попадают три ДЦ, и только **US-NE-1** умеет `STANDARD` Network Volume — остальные два физически
не могут принять реплику. Это тот же вывод, что и в предыдущем скане, но watcher всё равно
пересканирует каталог **в момент миграции**: список меняется каждый час (US-NE-1 уже терял карту,
в 12:20Z в US-TX-3 был RTX 4090 $0.74/ч, к 12:35Z — уже нет).

---

# ЧТО WATCHER СДЕЛАЕТ САМ, КАК ТОЛЬКО ЁМКОСТЬ ПОЯВИТСЯ
# ЧЕГО WATCHER НЕ СДЕЛАЕТ НИКОГДА (fail-closed)

* не покупает том, пока модель не измерена и не подтверждено, что она влезает в 50 GB;
* не создаёт второй secondary, третий том или Global Volume;
* не поднимает два GPU-Pod'а одновременно (миграция — CPU или одна дешёвая карта; живой тест — один GPU);
* не превышает $1.00/ч и 15 минут на migration Pod, $2.00/ч и 20 минут на живой тест;
* не считает racing-отказ ёмкости фатальной ошибкой — возвращается в `watching`;
* не продолжает при неполном измерении или несовпадении хэшей: пишет `phase=error` и освобождает Pod'ы;
* не запускает llama.cpp на migration Pod'е.

---

# ТЕКУЩЕЕ СОСТОЯНИЕ АККАУНТА (проверено)

```
running pods:      0
volumes:           uwgeaie5b0 (US-TX-3, 50 GB STANDARD) — единственный, не тронут
spend this pass:   $0.00
```

**FINAL: WATCHING US-TX-3** — как только в US-TX-3 появится подходящий compute (CPU или любая Secure
GPU ≤ $1.00/ч), watcher выполнит шаги E–N и, при доступной ≥48 GB карте в целевом ДЦ, шаг R — без
дополнительных сообщений с вашей стороны. Результат появится в `state.json`, `watcher.log` и в
`primary-manifest.json` / `secondary-replica.json` / `placements.env`.
