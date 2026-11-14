# CANALLA LLM 1.2.7 — AUTONOMOUS EXECUTION REPORT

**Дата:** 2026-11-14, 16:40 UTC (сессия 14:00–16:40 UTC).
**Ветка:** `feature/canalla-1.2.7-autonomous-agent`.
**HEAD на момент отчёта:** `87915e4` (плюс коммит этого отчёта).
**Режим:** автономный, в пределах разрешённой владельцем серии платных попыток.

---

## A. PROJECT

| Параметр | Значение |
|---|---|
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD до сессии | `ab686a1` |
| HEAD на момент отчёта | `87915e4` |
| Незакоммиченные изменения кода | нет (только этот отчёт и evidence-файлы) |
| Push | не выполнялся в этой сессии (см. «Следующий шаг») |

Коммиты сессии (каждый — исправление, найденное платным прогоном, плюс бесплатный тест):

| Коммит | Что исправлено |
|---|---|
| `f75f68e` | путь `write_file`: append-запись частями, типизированные отказы, якорение относительных путей, ограничение эха аргументов; ограниченная серия авторизаций (`batch.py`) |
| `c062a7c` | один Pod остаётся одним (транзиентный 404 не закрывает сессию), run-scoped Pod id, cooldown между попытками |
| `59c4ccc` | acceptance получает 80 минут / 80 tool calls вместо 30/40 |
| `5187682` | provenance хеширует то же, что ledger; evidence переживает `datetime`; host использует интерпретатор проекта |
| `f98ff52` | проверка append-записи сравнивается с итоговым файлом |
| `7868f0c` | пустой список Pod подтверждается вторым чтением; verifier находит модуль приложения |
| `3a3b232` | актуализирован PLAN |
| `7039dbf` | scratch задачи не требует подтверждения |
| `91b838f` | verifier: workspace как единственный import root, factory/WSGI, реальный POST до проверки SQLite; `sources_missing` не блокирует coding |
| `b9ca690` | исправленная проверка считается пройденной (latest per kind/target) |
| `d7957dd` | append-ветка определяет свой digest (иначе `provider_unavailable`) |
| `254a722` | актуализирован учёт серии |
| `87915e4` | тесты проекта запускаются из его собственного корня |

---

## B. INFRASTRUCTURE

| Параметр | Значение |
|---|---|
| Network Volume A | `uwgeaie5b0`, US-TX-3, `STANDARD`, 50 GB — **рабочая**: реальные ответы модели |
| Network Volume B | `y92bt25ru8`, US-MO-1, `STANDARD`, 50 GB — **VERIFIED** (реальный ответ 2026-10-13 + ответы этой серии) |
| GPU | NVIDIA L40S 48 GB, $1.09/ч (перечитывается перед каждой попыткой) |
| Мониторинг | Windows Task `Canalla LLM GPU Monitor`, `PT1M`, 2 проверки за проход = **30 с**, `IgnoreNew`, `StartWhenAvailable`, без даты окончания |
| Размещения | US-TX-3 / `uwgeaie5b0`, US-MO-1 / `y92bt25ru8`; другой том/карта отвергаются |
| Capacity | доступность «дышит»: L40S появляется и исчезает в обоих DC; ожидание ограничено 60 с |

---

## C. AUTHORIZATION

| Параметр | Значение |
|---|---|
| batch_id | `0673b737b3f243ed8985dd62c899dbb1` |
| Фраза владельца | `I AUTHORIZE A PAID LIVE BATCH WITH A TOTAL GPU BUDGET OF USD 2.00` |
| Потолок | **$2.00**, один Pod одновременно |
| Создано Pod | **9** за серию (плюс инцидент двух Pod в попытке `1f89625c`, исправлен) |
| Потрачено (журнал) | **$1.6420** |
| Потрачено (фактическое падение баланса) | **$1.6773** (`6.963115356 → 5.2857780163`) |
| Остаток лимита | **$0.358** |
| Требуется на полезное окно | **$0.413** (20 мин при $1.09/ч + запас) |
| Состояние серии | **PAID_BATCH_EXHAUSTED** (остатка не хватает на полезное окно; новых Pod не создаётся) |
| Просроченные/повторно использованные grant | нет; каждый дочерний grant одноразовый и архивируется |

---

## D. REAL LIVE

Каждая попытка — один Pod, один дочерний grant, отдельный run id и отдельная запись расхода.

| run | DC | Pod | Итог | Расход | Что доказано |
|---|---|---|---|---|---|
| `99a3da76` | MO-1 | `xg9wrvvga72p1e` | FAIL | $0.2192 | Pod, backend, stream; первый ход съеден ложным `install_software` (5 мин) |
| `1f89625c` | TX-3 | `31bvyg4wn8fwyi` | FAIL | $0.0557 | selftest PASS; **инцидент: второй Pod** из-за транзиентного 404 (исправлено) |
| `a2a6ed2f` | MO-1 | `dvg008bsozv5wi` | FAIL | $0.2283 | **26 MODEL_RESPONSE, 25 tool calls**, реальный проект; harness упал на `datetime` в evidence |
| `4a6f3cb4` | TX-3 | `j9qkl5pxf5a8y7` | FAIL | $0.2789 | 15 ответов; подтверждение установки остановило поток на 5 мин; приложение с реальным дефектом БД |
| `29b149b8` | TX-3 | `37feoxqzwx3cfu` | FAIL | $0.4024 | **provenance REAL_MODEL_CONFIRMED**: 25 ответов, 24 вызова, 20 связанных результатов; GUI-скриншоты; SQLite 2 строки; `verify_result` 8/8; отказ только из-за `sources_missing` и «старых» failed-записей |
| `73815fb5` | TX-3 | `8clyj33ykuqjqv` | FAIL | $0.3143 | регрессия append (`provider_unavailable`) — исправлена |
| `e1845955` | TX-3 | `mjo8ak4ys9igdw` | **Task COMPLETED** | $0.1432 | приложение создано, запущено, проверено; см. раздел E |

Финальная попытка `e1845955`:
* 18 `MODEL_RESPONSE` (`llamacpp`, `live:true`), 17 исполненных tool calls, 17 связанных результатов;
* модель написала пакет `taskdesk/{__init__,app/api,models,views,run}.py`, 3 файла тестов, README;
* `python -m pytest -q` из `taskdesk/`: **14 passed**;
* приложение поднято (`run.py`), `GET /api/health → 200 {"app":"TaskDesk","status":"ok"}`;
* задача **COMPLETED** с критериями: запрос выполнен, тесты проходят, git просмотрен, изменения ограничены.

---

## E. TASKDESK (независимая проверка, run `e1845955`)

| Проверка | Результат | Доказательство |
|---|---|---|
| Файлы приложения | **PASS** | 9 `.py`: `taskdesk/{__init__,app,api,models,views,run}.py`, `taskdesk/tests/{test_api,test_persistence,test_views}.py` |
| Тесты написаны | **PASS** | 3 файла тестов |
| Тесты проходят | FAIL (артефакт harness) | из `taskdesk/` — **14 passed**; harness запускал из корня workspace и получал ImportError. Исправлено (`87915e4`) |
| SQLite создан | **PASS** | `taskdesk.db` |
| Строка задачи в SQLite | **PASS** | независимый `POST /api/tasks → 201`; в таблице `tasks` 1 строка |
| HTTP | **PASS** | `application_serves_http: 127.0.0.1:30865 via taskdesk.app.__init__`, `/ → 200` |
| GUI (независимый Playwright) | **PASS** | `independent_gui_load: True`, `gui-evidence/independent-page.png` |
| GUI (модель) | 5 шагов | `gui/01-goto.png … 05-expect_text.png` |
| Отсутствие дублей payload | **PASS** | — |
| provenance | INCOMPLETE_TRACE | модель не вызвала `verify_result` в этой попытке (в `29b149b8` provenance был **REAL_MODEL_CONFIRMED**) |

Приложение TaskDesk реализует: создание/список/поиск/статус/описание/дедлайн/удаление,
SQLite-хранилище, HTML/JS-интерфейс, `GET /api/health`.

---

## F. AUTONOMY

| Уровень | Статус | Доказательство |
|---|---|---|
| 1 — реальная модель отвечает | **PROVEN** | `MODEL_RESPONSE` с `live:true` в каждой попытке |
| 2 — модель вызывает и использует инструменты | **PROVEN** | исполненные `list_directory`, `write_file`, `run_python`, `patch_file`, `browser_gui` |
| 3 — несколько зависимых действий | **PROVEN** | write → run → test → patch → run → browser → verify |
| 4 — модель создаёт и запускает приложение | **PROVEN** | `e1845955`: проект создан, запущен, HTTP 200, независимый браузер и SQLite |
| 5 — модель исправляет реальную ошибку | **PROVEN в рамках попытки** | `29b149b8`: падавшие проверки → патчи → `verify_result` 8/8; `a_failure_preceded_a_pass=true` |
| 6 — независимая проверка доказывает завершение | **НЕ ПОДТВЕРЖДЁН** | строгий гейт не пройден: `tests_pass` (артефакт harness, исправлен) и provenance INCOMPLETE |
| 7 — durable resume с реальной моделью | **НЕ ПРОВЕРЯЛСЯ** | требует отдельного платного окна |

Важно: LEVEL 6 не заявлен, потому что все независимые проверки и provenance
`REAL_MODEL_CONFIRMED` не совпали в одном завершённом прогоне. Это честная оценка, а не
недооценка: приложение работало, но строгий критерий приёмки — конъюнкция всех условий.

---

## G. RECOVERY (найденные и исправленные дефекты)

1. **`install_software` по любому «установи»** — ставил OhMyPosh вместо зависимостей и блокировал
   первый ход на 5 минут. Теперь только явно названный пакет. Тест: `test_093_reliability`.
2. **Второй Pod при транзиентном 404** — сессия закрывалась как `external_stop`, следующий ensure
   покупал замену. Теперь 404/EXITED подтверждаются двумя повторными чтениями. Тесты:
   `test_compute::test_a_transient_missing_pod_is_not_an_external_stop` (и подтверждённый случай).
3. **Ложный CLEAN** — один пустой список Pod принимался за завершение. Теперь второе чтение.
4. **`INVALID_PROVENANCE` на 25 реальных вызовах** — ledger хешировал валидированный payload,
   provenance — сырой JSON. Теперь одинаковая нормализация. Тест: `test_127_provenance`.
5. **Крэш evidence на `datetime`** — прогон с 26 ответами становился `CRASHED`. Теперь `default=str`.
6. **Host запускал тесты системным Python без pytest/FastAPI** — `python -m pytest` → exit 4.
   Теперь интерпретатор проекта; self-test проверяет импорты.
7. **Verifier не находил приложение** — требовал `main.py`; теперь ищет модуль по исходникам и
   умеет `create_app()`/WSGI/ASGI.
8. **Verifier shadowing** — тесты проекта импортировали `app` из продукта (editable install),
   потому что проверка шла из корня. Теперь тесты запускаются из корня проекта (каталог с `tests/`).
9. **Ожидание подтверждения без событий** — поток умирал по read-timeout; теперь progress каждые 15 с.
10. **`sources_missing` блокировал coding** при выключенном web — теперь требуется только при
    реально использованном research или для research-задачи. Тест: `test_autonomous_tasks`.
11. **Историческая failed-проверка навсегда блокировала COMPLETED** — теперь решает последняя
    запись по (kind, target), история сохраняется. Тест: `test_127_agent_core`.
12. **Регрессия append** — после успешной записи `provider_unavailable` из-за неопределённого
    `expected_sha256`. Исправлено; тест теперь падает и на `provider_unavailable`.
13. **Относительный путь** отвергался host'ом — якорится в workspace.
14. **Ненастроенный web-провайдер** рекламировался модели — acceptance работает с `web_mode: off`.

Все перечисленное покрыто бесплатными тестами; ни одно исправление не проверялось платным прогоном
повторно без необходимости.

---

## H. MONEY

| Статья | Значение |
|---|---|
| Баланс до серии | $6.963115356 |
| Баланс после серии | $5.2857780163 |
| Фактическое списание | **$1.6773373397** |
| GPU по журналу (сумма попыток) | **$1.6420** |
| Разница | $0.0353 — округление/фон |
| Pod создано | **9** (все остановлены) |
| Подтверждение остановки | `cleanup.json: pod_count 0, verdict CLEAN` после каждой попытки; финальная проверка API — 0 Pod |
| Network Volume | не удалялись, не создавались |
| Остаток лимита владельца | $0.358 — меньше $0.413, необходимых на полезное окно |

Финальная проверка провайдера: `pod_count: 0`, `current_spend_per_hr: 0.01` (0.01 — фоновое
хранилище, не GPU).

---

## I. PRODUCTION

| Параметр | Baseline | Сейчас | Изменение |
|---|---|---|---|
| users | 2 | 2 | нет |
| chats | 54 | 54 | нет |
| messages | 120 | 120 | нет |
| paired_devices | 63 | 63 | нет |
| local_tasks | 30 | 30 | нет |
| integrity | ok | ok | нет |
| device identity | `6413489d-6cae-4183-8446-a3b2339137dc` | тот же | нет |
| Установленная версия | 1.2.6 | 1.2.6 | не переустанавливалась |

Acceptance работал на изолированной БД (`%TEMP%\canalla-live-*\live.db`) и изолированном device
credential; production не читался и не писался.

---

## J. RELEASE

| Проверка | Статус |
|---|---|
| Backend targeted suite | **238 passed** (compute, tools, agent, coding, tasks, timeouts) |
| 1.2.7 suite (agent core, provenance, write path, recovery, UIA, autonomous e2e, final answer, task status) | **75 passed** |
| Full autonomous E2E с Playwright | **PASS** |
| Monitor self-test | **65/65 PASS** |
| Live acceptance self-test | **30/30 PASS** (включая реальный интерпретатор проекта) |
| Watchdog self-test | **PASS** |
| Rust host build (`cargo build --release --bin alex-host-loop`) | **OK** |
| Windows | проверено (все прогоны — Windows 11) |
| Ubuntu/Linux | **не запускалось в этой сессии** |
| Installer / подпись | не собирались; **Authenticode — внешний блокер** (нет действительного сертификата) |
| Публикация 1.2.7 | **не выполнялась** (запрещено без отдельного разрешения) |

---

## K. FINAL RESULT

**Цель достигнута частично, и это зафиксировано честно.**

* **REAL QWEN подтверждена**: настоящие ответы, настоящие tool calls, реальный проект.
* **TaskDesk создан и работал**: HTTP 200, независимый браузер отрисовал страницу, независимый
  `POST /api/tasks` вернул 201 и строка сохранилась в SQLite; задача **COMPLETED**.
* **Строгий LIVE PASS не заявлен**: в одном завершённом прогоне не совпали два условия:
  `tests_pass` (артефакт harness, исправлен `87915e4`) и provenance (`verify_result` не вызван в
  этой попытке; в `29b149b8` он был `REAL_MODEL_CONFIRMED`).
* **Уровень автономности: LEVEL 4 подтверждён; LEVEL 5 подтверждён в рамках попытки; LEVEL 6 и 7
  не заявлены.**
* **Деньги**: $1.6773 фактически, все Pod остановлены, остаток лимита $0.358 — меньше полезного
  окна.
* **Production 1.2.6 не изменена.**
* **Фоновый мониторинг продолжает работать** (30 с, без Zed), платные попытки заблокированы
  исчерпанием полезного остатка.

### Следующий шаг

1. Для ещё одной попытки нужно **новое решение владельца** (дополнительный бюджет ≥ $0.50 или
   новый batch): остатка $0.358 не хватает на 20-минутное окно.
2. Код готов к этой попытке: verifier находит приложение и запускает тесты из его корня,
   append исправлен, `sources_missing` не блокирует build, исправленная проверка не блокирует
   завершение.
3. В той попытке модели стоит явно пройти `verify_result` (продуктовый независимый verifier) —
   это единственное недоказанное звено provenance в завершённом прогоне.
4. Release readiness: собрать installer и закрыть Authenticode (внешний сертификат), прогнать
   Linux-часть CI; публикация — только по отдельному разрешению.

### Evidence

`artifacts/acceptance-127/live-qwen/final-live/`:
`run-e184595544d24f14a140bd9298e81ea1.json`, `final-summary.json`, `http-check.json`,
`database-check.json`, `model-responses.jsonl`, `tool-calls.jsonl`, `tool-execution.jsonl`,
`provenance-verdict.json`, `task-state.json`, `selftest-evidence.json`, `gui-evidence/`,
`cleanup.json`, `billing-summary.json`, `logs/`.

`%LOCALAPPDATA%\CanallaMonitor\`: `batch-authorization.json`, `batch-spend.jsonl` (12 записей),
`checks.jsonl`, `status.json`, `state.json`.
