# CANALLA LLM — AUTOMATIC REPLICA MIGRATION · CONTROLLED RECOVERY (MEASURE-ONLY)

**Задание:** после инцидента с утечкой денег — безопасная стабилизация, независимый kill-switch и
**одно контролируемое живое измерение** primary-модели, без второго тома, без копирования и без GPU-теста.
**Дата:** 26.09.2026 · **Ветка:** `release/canalla-1.1.0`
**Релиз:** `main` не смёржен, тега нет, манифест не выложен, Canalla не пересобиралась.

## ДЕНЬГИ — раздельно, без смешивания

| Позиция | USD |
|---|---|
| **Инцидент 2026-09-25/26** (4 migration Pod'а, утечка из-за упавшего парсера) | **6.3351** |
| **После исправления** (kill-switch + measure-only, всё, что потрачено дальше) | **0.00** |
| Хранилище: один том `uwgeaie5b0` 50 GB STANDARD | ~$3.50/мес (было и остаётся) |

Строка «$0.00 за проход» в прежней версии отчёта была неверной по смыслу: она относилась к текущему
проходу и **скрывала** реальную стоимость инцидента. Здесь эти две величины разделены явно.

---

# ИНЦИДЕНТ 2026-09-26 — реальная ёмкость, падение на парсинге, утечка денег (найден и закрыт)

**Что произошло.** Watcher поймал **реальную GPU-ёмкость в US-TX-3 четыре раза** и каждый раз создал
migration Pod (HTTP 201) **в том же полле, без ожидания CPU-cooldown** — правило этого прохода
подтверждено живьём, а не только тестом:

| Время (UTC) | Событие |
|---|---|
| 17:56:35 | GPU signal → `migration pod created (gpu)` |
| 23:45:22 | GPU signal → `migration pod created (gpu)` |
| 23:54:37 | GPU signal → `migration pod created (gpu)` |
| 00:39:50 | GPU signal → `migration pod created (gpu)` |

**Почему конвейер не пошёл дальше.** Провайдер отдаёт лог-строки JSON-обёрнутыми
(`{"line": "CANALLA_M_...", "ts": "..."}`), а старый парсер резал строку по маркеру и вызывал
`int()` на хвосте: `int('20752787712","ts":"2026-09-25T17:59:18.492513685Z"}')` → **ValueError**.
Исключение убивало прогон ровно тогда, когда Pod уже существовал и биллился.

**Почему Pod оставался жив.** Guard исключал из проверки `source_pod` (свой Pod) — и после падения
этот Pod навсегда считался «моим»: первый жил **~5.9 часа**, последний — **~2 часа**. Из-за того же
исключения в окне 23:45–23:52 одновременно жили **два** Pod (старый «мой» + новый).

**Точная стоимость** (`GET /v2/billing/pods`, не оценка):

| Pod | USD |
|---|---|
| `azarnbr5bwrqqq` (17:56 → 23:52) | **4.4058** |
| `b0cdj5zffr6l45` (00:39 → завершён вручную 02:38) | **0.9844** |
| `e3ajeux21b0nmf` | 0.6483 |
| `jqqvfjgfcueuri` | 0.2962 |
| `rre5me05emix3e` (CPU) | 0.0003 |
| **ИТОГО за проход** | **6.3351** |

Снимок, сделанный до ручной остановки orphan'а, показывал **6.0869** — за минуты до завершения Pod
доначислил $0.2485. Обе цифры — из `GET /v2/billing/pods`, без оценок; итоговая может дозакрыться
небольшим лагом биллинга.

Это превышение заявленных границ миграции ($1.00/ч, ≤15 мин на Pod) — оно произошло не по решению,
а из-за падения конвейера; Pod `b0cdj5zffr6l45` снят вручную в 02:38Z, начисление остановлено.

**После фикса — ноль**: за период с 02:38Z не создано ни одного Pod'а (`new pod ids created after
02:38Z today: []`), поэтому дополнительная стоимость равна **$0.00**.

Это превышение заявленных границ миграции ($1.00/ч, ≤15 мин на Pod) — оно произошло не по решению,
а из-за падения конвейера; Pod `b0cdj5zffr6l45` снят вручную, начисление остановлено.

**Что исправлено (тот же проход):**

* `log_lines()` / `log_value()` — JSON-обёртка разворачивается, числа и SHA256 берутся строгим
  токеном; парсер больше **не бросает исключений** (он работает, пока Pod жив и биллится), то же
  применимо к стадии копирования;
* **владение больше не считается доказательством**: любой живой `canalla-*` Pod, найденный на старте
  прогона, сначала **освобождается**, и только потом решается вопрос о новом (том хранит данные —
  деньги тратит только Pod);
* стадия измерения обёрнута: любое исключение → Pod освобождается немедленно, `phase=watching`,
  запись `last_error`;
* обработчик ошибок в цикле тоже освобождает Pod'ы («a run that raised owns nothing»);
* `canalla-copy-*` (Pod копирования) подчиняется тем же правилам;
* `--self-test`: **19/19 PASS**, включая JSON-обёрнутый лог, освобождение брошенного Pod'а, запрет
  дублирования и отказ второму watcher'у.

**После инцидента:** watcher перезапущен на исправленном коде (один экземпляр, lock удерживается),
`running pods = 0`, том `uwgeaie5b0` не тронут, `phase=watching`. Цель прохода — измеренная модель,
второй том и верифицированная реплика — **по-прежнему не достигнута**: четыре пойманные ёмкости
ушли на упавший конвейер, а не на миграцию.

---

# CONTROLLED RECOVERY — ЧТО СЕЙЧАС ЗАПУЩЕНО

```
watcher (measure-only):  RUNNING  · PID 15880 (файл watcher.pid) · poll каждые 120 с
reaper (kill-switch):    RUNNING  · PID 3200  · проверка каждые 30 с, TTL 900 с
running pods:            0 (account-wide)
secondary volume:        NOT CREATED
mode:                    measure-only — том/копия/GPU-тест в этом проходе НЕ выполняются
```

Артефакты (все пути — от корня репозитория):

| Файл | Что содержит |
|---|---|
| `artifacts/runpod-tx3-watcher/state.json` | фаза, поллы, кандидаты, манифест модели, факты об измеренном Pod'е |
| `artifacts/runpod-tx3-watcher/watcher.log` | append-only журнал с UTC-таймстампами каждого шага |
| `artifacts/runpod-tx3-watcher/watcher.pid` | PID владельца (для наблюдения; авторитет — lock) |
| `artifacts/runpod-tx3-watcher/watcher.lock` | OS byte-range lock, удерживаемый процессом |
| `artifacts/runpod-tx3-watcher/reaper.log` | журнал независимого kill-switch'а |
| `artifacts/runpod-tx3-watcher/reaper-state.json` | что reaper видел в последнем проходе |
| `artifacts/runpod-tx3-watcher/reaper-incident.json` | пишется только при инциденте (>1 Pod) |
| `artifacts/runpod-tx3-watcher/measurement-pod.json` | факты измерения: Pod, GPU, ДЦ, $/ч, createdAt/terminatedAt, секунды, биллинг, манифест |
| `scripts/watch-tx3-migration-capacity.py` | watcher: `--once`, `--run`, `--status`, `--self-test`, `--measure-only`, цикл |
| `scripts/canalla-pod-reaper.py` | независимый kill-switch: `--self-test`, `--once`, цикл |

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
5. **GPU-сигнал каталога больше не ждёт CPU-cooldown.** Правило зафиксировано явно: если живой
   каталог показывает в US-TX-3 **любую** Secure GPU ≤ $1.00/ч с `availability != NONE`, попытка
   создать migration Pod с этой картой делается **в том же poll cycle**, и CPU-флavour в этом
   случае вообще не запрашивается (`allow_cpu=False`) — cooldown относится только к слепым CPU-probe.
   Если карта исчезла между scan и `POST /v2/pods`, watcher получает capacity-ошибку, возвращается в
   `watching` и **не повторяет POST в этом полле** — следующая попытка только через 120 с. Это и есть
   причина, по которой правка сделана до появления ёмкости: окно в US-TX-3 живёт минутами.

---

# ДЕТЕРМИНИРОВАННЫЕ ТЕСТЫ (офлайн, без сети и без денег)

## watcher — `--self-test`, 25/25 PASS

```
PASS  age parses an ISO stamp
PASS  age rejects junk
PASS  only in-budget bookable US-TX-3 capacity is a candidate
PASS  secondary ranking: eligibility, primary exclusion, STANDARD last
PASS  secondary ranking needs real availability
PASS  secondary ranking prefers more eligible GPU types
PASS  price above the operator maximum is never a candidate
PASS  a datacenter without STANDARD volumes is not chosen
PASS  an abandoned Pod is released, then exactly one new attempt is made
PASS  an orphan past its budget is reaped and never duplicated
PASS  a GPU signal attempts exactly the card, never the CPU
PASS  the attempt mounts the primary volume in its own datacenter
PASS  a refused attempt never retries inside the same poll
PASS  a blind probe still tries one CPU flavour
PASS  a JSON-wrapped log stream still yields the manifest
PASS  a plain log stream still yields the same manifest
PASS  an abandoned copy Pod is released too, then one attempt
PASS  a raised run releases its Pod and returns to watching
PASS  no watcher running: the lock is free for exactly one new one
PASS  measure-only: Pod terminated, nothing purchased
PASS  a timeout while measuring releases the Pod
PASS  a malformed log stream stops before any purchase and releases the Pod
PASS  a truncated SHA log stream stops before any purchase and releases the Pod
PASS  an exception during the copy releases BOTH Pods
PASS  two live canalla Pods: both released, nothing created
self-test: 25/25 passed
```

Последние шесть — это **safety drill**: настоящий конвейер (`run_pipeline`/`_migrate`) прогоняется
против фиктивного провайдера, поэтому проверяется именно то, что исполняется в бою: `finally`-гард,
правило «освободить до создания», инцидент «>1 Pod», отсутствие покупки при неполном измерении.

## reaper (независимый kill-switch) — `--self-test`, 10/10 PASS

```
PASS  a fresh migration Pod is left alone
PASS  a Pod past the 15-minute TTL is terminated
PASS  the TTL boundary is inclusive-safe
PASS  a copy Pod obeys the same TTL
PASS  two live canalla Pods are an incident and both are released
PASS  no canalla Pod is a no-op
PASS  an unreadable createdAt fails closed
PASS  foreign Pods are never touched
PASS  an already exited Pod is not a live Pod
PASS  our own Pods are read from provider fields only
reaper self-test: 10/10 passed
```

Ограничения внутри тестов проверяются на фиктивном каталоге: карта $0.74/ч в US-TX-3 — кандидат,
карта $1.60/ч — нет (выше лимита $1.00/ч для миграции), карта с `availability=NONE` — нет,
не-Secure карта — нет, карта в другом ДЦ — нет. Для secondary: ≥48 GB, ≤$2.00/ч, только bookable,
PRIMARY исключён, ДЦ без `STANDARD` — в конце списка и не выбирается.

Запуск: `python scripts/watch-tx3-migration-capacity.py --self-test` и
`python scripts/canalla-pod-reaper.py --self-test` (exit 0 = все PASS).

Рядом — бэкенд-гейты того же compute-слоя (origin, `/v1/models`-gating, стратегии, реплики):

```
pytest tests/test_allocation_candidates.py tests/test_cloud_demand.py tests/test_model_replicas.py
       tests/test_compute_preferences.py tests/test_cloud.py tests/test_compute.py
       tests/test_compute_connection.py -q
196 passed in 98.56s
```

---

# ЧТО ДЕЛАЕТ WATCHER В РЕЖИМЕ MEASURE-ONLY

Полный конвейер (E→R) сохраняется в коде, но **не используется** до успешного контролируемого
измерения. Сейчас разрешено ровно следующее:

| Шаг | Действие | Защита |
|---|---|---|
| 1 | Читать каталог US-TX-3 каждые 120 с (бесплатно), ждать сколько нужно | ни одного платного вызова до реального сигнала |
| 2 | **Один** migration Pod в US-TX-3, том `uwgeaie5b0` → `/workspace`, образ alpine | GPU-сигнал → попытка в том же полле; слепая CPU-попытка — не чаще 1 раза в 15 мин; ≤ **$1.00/ч** |
| 3 | Измерение: точный путь/имя/байты/SHA256 GGUF, **содержимое** `start-llm.sh` и `check-llm.sh` (base64 в лог → файлы в артефактах), список файлов, бинарь и версия llama.cpp, required bytes, file count | манифест → `primary-manifest.json`; размер сверяется с 50 GB |
| 4 | **Немедленное завершение Pod'а** сразу после измерения | `finally`-гард в конвейере + независимый reaper (TTL 900 с) — два разных механизма |
| 5 | Запись фактов: Pod ID, GPU, ДЦ, $/ч, createdAt/terminatedAt, секунды, биллинг, манифест | `measurement-pod.json` + `state.measurement` |
| 6 | Watcher **сам завершается** в фазе `measured` | одноразовая операция: никаких повторных сигналов и повторных трат |

**Чего в этом проходе НЕ происходит:** второй Network Volume, копирование, `llama.cpp`, живой GPU-тест,
финальная приёмка. Это следующие шаги — только после PASS измерения.

---

# НЕЗАВИСИМЫЙ KILL-SWITCH (`scripts/canalla-pod-reaper.py`)

Отдельный процесс, который не импортирует ни парсер, ни state machine, ни владение Pod'ами и не
читает `state.json` — только `GET /v2/pods` и часы. Каждые 30 секунд:

| Правило | Действие |
|---|---|
| живой `canalla-migrate-*` / `canalla-copy-*` старше **15 минут** (по `createdAt` провайдера) | **terminate**, без исключений |
| возраст прочитать нельзя | **terminate** (fail-closed: деньги важнее, том сохраняет данные) |
| **больше одного** живого `canalla-*` Pod'а в аккаунте | **terminate всех**, запись `reaper-incident.json`, **STOP** (exit 2, без авто-повторов) |
| Pod'ы с чужими именами | никогда не трогаются |

Замечание на будущее: когда фаза копирования будет снова разрешена, она по замыслу держит **два**
Pod'а (источник + получатель). Правило «не больше одного» этому противоречит — значит, при возврате
копирования его придётся пересмотреть осознанно, а не «на ходу». До тех пор правило строгое.

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

# ЧЕГО WATCHER НЕ СДЕЛАЕТ НИКОГДА (fail-closed)

* не покупает том и не копирует ничего в режиме measure-only;
* не создаёт второй secondary, третий том или Global Volume;
* не поднимает два Pod'а одновременно — это теперь и инцидент в watcher'е, и TTL-правило в reaper'е;
* не превышает $1.00/ч и 15 минут на migration Pod ($2.00/ч и 20 минут — только на будущий живой тест);
* не оставляет Pod живым ни на одном пути: `finally`-гард в конвейере + независимый reaper снаружи;
* не считает racing-отказ ёмкости фатальной ошибкой — возвращается в `watching`;
* не продолжает при неполном измерении или несовпадении хэшей: пишет `phase=error` и освобождает Pod;
* не запускает llama.cpp на migration Pod'е;
* не переживает ошибку молча: `last_error` и `pods_after` пишутся в `state.json`.

---

# ТЕКУЩЕЕ СОСТОЯНИЕ АККАУНТА (проверено на 02:46Z)

```
running pods:      0 (account-wide, canalla Pods: 0)
volumes:           uwgeaie5b0 (US-TX-3, 50 GB STANDARD) — единственный, не тронут
spend, инцидент:   $6.3351 (снимок до остановки orphan'а: $6.0869)
spend, после фикса: $0.00 — ни одного Pod'а с 02:38Z
```

**FINAL: WATCHING US-TX-3 (MEASURE-ONLY)** — как только в US-TX-3 появится mount-capable compute
(CPU или любая Secure GPU ≤ $1.00/ч; VRAM не важен, LLM не запускается), watcher выполнит **только**
измерение и сразу завершит Pod; затем он сам остановится в фазе `measured`. Результат появится в
`measurement-pod.json`, `primary-manifest.json`, `state.json` и `watcher.log`.

Приёмка (§8 задания): измерение = PASS, SHA256 получен, скрипты получены, runtime получен, Pod завершён
**автоматически**, runtime < 15 минут, Pod'ов = 0, второго Pod'а не было, стоимость — в пределах центов.
Пока ёмкости нет — это **не FAIL**, а ожидание: единственный ненулевой результат в этом проходе —
подготовленная защита и ноль трат.
