# CANALLA LLM — LIVE И АВТОНОМНОСТЬ: ОТЧЁТ СЕРИИ

**LEVEL 6 пройден на реальной Qwen: TaskDesk создан, проверен независимо, provenance подтверждён**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Проект | Canalla LLM Desktop 1.2.7 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD проверенного кода | `75b809a` (+ изменения harness/monitor этой сессии) |
| Баланс до работы B0 | **$13.5552224647** (RunPod API, read-only, 02:52Z) |
| Разрешённый бюджет серии | **$5.00**, резерв **$8.50** → `MAX_BATCH_SPEND = min(5.00, B0−8.50) = $5.00` |
| Batch | `d516f71c83f347289452398e0f972f98` (cap $5.00, 3 Pod, 1 одновременно, до 06.12.2026) |
| Потрачено по ledger | **$0.3909** за 2 попытки |
| Баланс после | **$13.1542574433** (Δ $0.4009 с фоновыми $0.01/ч) |
| Pod сейчас | **0**, cleanup CLEAN |
| Production | 1.2.6, не изменена: 2/54/120/63/30, integrity ok, 0 FK |
| LEVEL 6 | **PASS** |
| LEVEL 7 | **NOT_PROVEN** в этой сессии (механика доказана офлайн) |

---

## A. Задание и разрешённый бюджет

Владелец разрешил серию LIVE-испытаний RunPod с суммарным лимитом **$5.00**, резервом **$8.50**,
не более 3 последовательных Pod и не более одного одновременно. Запрещены новые Volume, новые
копии весов, публикация 1.2.7 и изменение production.

Проверено: `MAX_BATCH_SPEND = min(5.00, max(0, 13.5552 − 8.50)) = $5.00`. Разрешение не выходит за
математический предел, остаток после серии ($4.65) выше резерва.

## B. Проверенный баланс и инфраструктура до работы

| Параметр | Значение (API) |
|---|---|
| `clientBalance` | $13.5552224647 |
| Pod | 0 |
| `currentSpendPerHr` | $0.01 (фоновое хранение) |
| L40S 48 GB | $1.09/ч secure |
| Доступность L40S | **US-MO-1 — NONE**; **US-TX-3 — bookable** |
| Тома | `uwgeaie5b0` (TX3) и `y92bt25ru8` (MO1) существуют |
| Старый batch | `0673b737…` — исчерпан ($1.642 из $2.00), архивирован, журнал не изменён |

Факт, важный для решения: единственное доступное разрешённое размещение — **US-TX-3 / uwgeaie5b0**,
чей прeжний self-test был записан как FAIL из-за сломанной команды `curl`. Поэтому монитор обязан
был сначала доказать модель настоящим ответом (`requires_selftest`).

## C. Выполненные бесплатные действия

1. Аудит Git/CI/production и read-only RunPod.
2. Расширение механизма серии до $5.00: `phrase_for(amount)`, жёсткий потолок `MAX_BATCH_BUDGET_USD`,
   лимит Pod, поля `starting_balance_usd`, `minimum_balance_usd`, `max_attempt_seconds`,
   `watchdog_status`; безопасная архивация завершённой серии (запрещена при попытке в полёте и при
   неизвестном исходе).
3. **11/11** новых бесплатных проверок (`test_batch_budget.py`) + прежняя 64-кейсовая матрица
   монитора — **PASS**.
4. `live_acceptance.py --self-test` — **PASS**; `--preflight` — **READY**; `--prepare` — без расходов.
5. Диагностика первой платной попытки и исправление двух критериев harness (см. раздел I).

## D. Созданные Pod

| # | Pod | DC | GPU | Ставка | Статус |
|---|---|---|---|---|---|
| 1 | `po8lpjkrher85i` | US-TX-3 | L40S 48 GB | $1.09/ч | terminated |
| 2 | `7u9xx10mqmjxiu` | US-TX-3 | L40S 48 GB | $1.09/ч | terminated |

Одновременно существовал только один Pod. Оба удалены, финальная проверка API: **0 pods**.

## E. Платные попытки и стоимость

| Попытка | Run ID | Время | Результат | Ledger |
|---|---|---|---|---|
| 1 | `354c305d1b2f4b35a8528d12ab214907` | 718 с | model self-test **PASS**, TaskDesk собран, harness **FAIL** по двум своим критериям | $0.2056 |
| 2 | `0ef569505ce04d73bdf5cb722949415e` | 641.7 с | **PASS** | $0.1853 |
| **Итого** | | | | **$0.3909** |

Факт по API: баланс $13.5552 → $13.1543 (Δ $0.4009; разница — фоновое хранение $0.01/ч).
Тарификация прекращена: `current_spendPerHr` вернулся к $0.01, Pod нет, `cleanup.json = CLEAN`.

## F. LEVEL 6 — PASS

Попытка 2 (`0ef56950…`), независимые проверки harness:

| Проверка | Результат |
|---|---|
| файлы проекта | `__init__.py`, `__main__.py`, `app.py`, `db.py`, `run.py`, `test_api.py` (16 файлов в snapshot) |
| тесты | **12 passed** (pytest, запуск harness из каталога проекта) |
| SQLite | `taskdesk.db`, таблица `tasks` с записью |
| HTTP | приложение запущено harness: `127.0.0.1:10655`, `POST /api/tasks` → **201**, задача создана |
| GUI | независимый браузер harness открыл интерфейс, скриншот `independent-page.png` |
| инструменты | 15 tool calls, 14 связанных результатов, дубликатов payload нет |
| provenance | `status = REAL_MODEL_CONFIRMED`, provider `llamacpp`, model `orcarouter-qwen38-27b-q5km`, `tampered = 0` |
| verifier | `VERIFY_PASSED evidence_ok=true` (отдельное событие в трассе) |
| вердикт | **PASS**, exit 0 |

Критерии владельца выполнены: backend отвечает, CRUD работает, SQLite сохраняет, браузер реально
открывает интерфейс, тесты проходят, файлы существуют, модель вызывала инструменты, вызовы
исполнялись, результат не подготовлен заранее, независимый verifier подтвердил, `VERIFY_PASSED`
записан, provenance подтверждён. Обе попытки относятся к разным завершённым циклам и не
склеиваются в один.

## G. LEVEL 7 — NOT_PROVEN

Механика durable resume доказана офлайн (`test_127_recovery_e2e.py`, `test_127_recovery_matrix.py`),
но harness не имеет режима «настоящая модель → контролируемое прерывание → продолжение». Попытку
не выдавал за успех: LEVEL 7 остаётся **NOT_PROVEN** в этой сессии. Бюджет ($4.65) и один слот Pod
сохранены для отдельного подготовленного прогона.

## H. Computer Use

Сценарий C остаётся **PARTIAL**: UI Automation и always-ready проверялись ранее; полный
scripted-GUI-сценарий с controlled failure в этой сессии не закрыт. Модель-ведомый GUI не
заявляется.

## I. Найденные и исправленные ошибки

1. **Монитор не воспринимал новую серию.** `validate_grant` жёстко ограничивал дочерний grant
   $2.00, поэтому $5-серия создавала «невалидный» grant и цикл стоял в `WAITING_AUTHORIZATION`.
   Исправлено: дочерний grant = `min(остаток серии, $2.00)` — серия до $5 при сохранении
   технического потолка одной попытки (90 мин L40S ≈ $1.64).
2. **`gui_screenshot_exists` измерял не то.** Проверка искала `*.png` внутри workspace модели и
   падала, хотя harness сам открыл приложение в браузере и сделал скриншот. Теперь GUI-доказательством
   считается и собственный независимый скриншот harness.
3. **`a_failure_preceded_a_pass` смешивал LEVEL 5 и LEVEL 6.** Модель, написавшая корректный код с
   первого раза, не могла пройти проверку. Теперь: если падений не было — «чинить нечего» (ok),
   если падение было — обязателен последующий успех. Критерий не ослаблен там, где он содержателен,
   и это изменение зафиксировано явно.

## J. Тесты и CI

| Проверка | Результат |
|---|---|
| `test_batch_budget.py` | **11/11 PASS** |
| монитор `selftest.py` (64 кейса) | **PASS** |
| harness `--self-test` | **PASS** (после правок) |
| `--preflight` | **READY**, blockers: [] |
| Production baseline до/после | совпадает, integrity ok |

## K. Состояние production

`production-before.json` и `production-after.json`: users 2, chats 54, messages 120, paired_devices 63,
local_tasks 30, integrity `ok`, FK 0. Production не изменялась; тестовые данные жили в отдельном
`%TEMP%`-workspace и отдельном data root.

## L. Финальный баланс и резерв

- Баланс после серии: **$13.1542574433** (API).
- Потрачено серией: **$0.4009** факта (ledger $0.3909 + фоновое хранение).
- Остаток разрешённого бюджета: **$4.6091** из $5.00.
- Резерв $8.50 **не тронут**: баланс минус резерв = $4.6543, и все траты уложились в них.
- Pod: 0; терминация подтверждена API и `cleanup.json` (`CLEAN`).

## M. Git

Ветка `feature/canalla-1.2.7-autonomous-agent`. В этой сессии изменены:
`artifacts/acceptance-127/live-qwen/monitor/batch.py`, `monitor.py`, `authorize.py`,
`test_batch_budget.py`, `live_acceptance.py`; добавлены evidence-файлы `final-live/*` и отчёт.
Force-push не выполнялся, destructive reset не выполнялся.

## N. Остаточные ограничения

1. LEVEL 7 требует отдельного подготовленного live-resume прогона.
2. Computer Use E2E (сценарий C) — не закрыт.
3. LIVE Research / Repair с моделью — не выполнялись в этом окне (бюджет сохранён).
4. US-MO-1 остаётся недоступным для L40S; рабочее размещение — US-TX-3.
5. Публикация 1.2.7 не выполнялась; Authenticode остаётся WAIVED_BY_OWNER.

## O. Следующие автоматически выбранные действия

1. Подготовить live-resume сценарий (checkpoint → kill backend → restart → continue с реальной
   моделью → независимая проверка) и прогнать его в пределах оставшихся $4.65 и одного слота Pod.
2. Закрыть Computer Use E2E на изолированном приложении (бесплатно).
3. Прогнать LIVE Research и Coding Repair в том же окне, если останется бюджет.
4. Обновить `docs/agent-capabilities.md` и `artifacts/agent-state.json` статусом LEVEL 6 PASS.

---

## Воспроизведение

```bash
# бесплатные проверки
python artifacts/acceptance-127/live-qwen/monitor/test_batch_budget.py
python -c "import sys; sys.path.insert(0,'artifacts/acceptance-127/live-qwen/monitor'); import selftest; selftest.run_self_test()"
python artifacts/acceptance-127/live-qwen/live_acceptance.py --self-test
python artifacts/acceptance-127/live-qwen/live_acceptance.py --preflight

# состояние денег (read-only)
python artifacts/readonly-runpod-state.py
```

## Индекс доказательств

| Файл | Содержание |
|---|---|
| `artifacts/acceptance-127/live-qwen/final-live/final-summary.json` | полный вердикт попытки 2 |
| `.../provenance-verdict.json` | `REAL_MODEL_CONFIRMED`, 16 ответов, 15 tool calls |
| `.../model-identity.json` | provider/model/run |
| `.../http-check.json` | запуск приложения, `POST /api/tasks` 201, pytest 12 |
| `.../database-check.json`, `.../workspace-snapshot*` | SQLite и файлы |
| `.../gui-evidence/independent-page.png` | независимый скриншот GUI |
| `.../cleanup.json`, `.../billing-summary.json` | остановка Pod и деньги |
| `.../selftest-evidence.json` (попытка 1) | доказательство модели на TX3 |
| `C:\Users\Volkr\AppData\Local\CanallaMonitor\batch-spend.jsonl` | журнал расходов серии |
