# Canalla LLM — закрытие 1.2.6: D2, Long Autonomy, T3 и T4

## Итоговый отчёт цикла 1.2.6 · 8 ноября 2026

Репозиторий: `volkrist/canalla-llm` · ветка `release/canalla-1.2.4` · рабочее дерево: существующее.
Работа велась в существующем агенте Zed №5, без нового Worktree, без Windows VM/Sandbox,
без force-push, без изменения production signing key и без изменения Compute Preferences владельца.

---

## 1. Итоговая таблица

| Гейт | Результат |
|---|---|
| D2 FREE | **PASS** — 25 тестов на реальном `POST /chats/{id}/stream` |
| D2 LIVE | **PASS** — run `live-20261108T131238`, задача `COMPLETED`, отчёт контроллера в БД |
| LONG AUTONOMY | **PASS** — 21 model-origin вызов, задача `COMPLETED`, pytest и CLI подтверждены независимо |
| MODEL-ORIGIN CALLS | **21** (цель 15–30, диапазон 18–25 достигнут) |
| T3 FREE | **PASS** — изолированный установленный UI + mock, полная механика |
| T3 LIVE | **BLOCKED** — harness упал на `TypeError` после первого инструмента; исправлено, но лимит 3 Pod исчерпан |
| T4 FREE | **PASS** — реальный suspend/resume native host, independent watchdog |
| T4 LIVE | **BLOCKED** — не запускался: лимит 3 Pod исчерпан предыдущими прогонами |
| BACKEND | **PASS** — 1023 passed, 1 skipped; Ruff clean |
| FRONTEND | **PASS** — vitest, tsc, Prettier, vite build; Playwright 21 passed |
| GATEWAY | **PASS** — Ruff + pytest |
| RUST | **PASS** — `cargo check --all-targets`; `cargo test` 128 passed, 2 ignored |
| PLAYWRIGHT | **PASS** — 21 passed |
| CI | **5/7 PASS** на `883245a`; `tor-runtime` и `installer-build` упали на изменении upstream Tor (см. §12) |
| DATA PRESERVATION | **PASS** — integrity ok, 59 paired (1 revoked), device_id не изменился |
| DEVICE ID | `f3548022-a676-463c-9fc5-b05bbd5ac907` → тот же |
| PODS | **3 Pod**, израсходовано **$0.2722**, все confirmed stopped, `pods []` |
| RELEASE 1.2.6 | **NOT PUBLISHED** — обязательные T3/T4 live не подтверждены; подписанный release candidate подготовлен |

---

## 2. Git

Фактический HEAD на старте: `b374bdda7230f7a5ae6ede8478d844535bfa69e2` (upstream
`origin/release/canalla-1.2.4`), `3ed7279` — предок HEAD. Сообщение владельца и приложенный отчёт
указывали разные коммиты; использован фактический HEAD. Коммиты цикла:

* D2: финальный ответ после tool-фазы публикуется один раз, после проверки статуса;
* D2: `tool_call`/эхо процесса (`pid`, `exit_code`) не становятся ответом;
* D2: COMPLETED-задача получает отчёт контроллера, а не дамп файла;
* T3/T4: подтверждённый инструмент переводит задачу в `EXECUTING` до результата — иначе рестарт
  во время длинного инструмента оставлял её в `WAITING_CONFIRMATION`;
* LA: новый challenge (CSV-пайплайн с двумя намеренными дефектами), 21 полезный вызов;
* T3/T4 harness: `expected_tools_any`, force-kill окна без graceful quit (не останавливает Pod),
  PID-адресный suspend, независимый resume-watchdog;
* версии desktop/backend/frontend/Tauri: **1.2.6** (кандидат);
* подписанный candidate installer.

---

## 3. D2 — что реально исправлено

`apps/backend/app/chat_stream.py`, `apps/backend/app/tools/local/public_text.py`:

1. **Буферизация финального ответа** для хода, где реально выполнялись инструменты. Ответ
   публикуется один раз, после всех проверок; больше нет «префикса до `<tool_call>`» в чате.
2. **Типизация финального completion**: `tool_call_in_final_answer`, `empty_model_answer`.
3. **Один bounded prose-only retry**; повтор оценивается по сырому completion.
4. **Честный статус после retry**: если повтор снова содержит protocol, публикуется
   `final_summary_for_task` — `completion_summary` для `COMPLETED`, `failure_summary` с реальной
   причиной для `FAILED`. Раньше в этом месте был общий текст «модель вызвала инструмент».
5. **Эхо инструмента не является отчётом**: `Процесс запущен, pid=…`, `Процесс завершён,
   exit_code=0` и подобные строки заменяются отчётом контроллера.
6. **COMPLETED-задача с неадекватным ответом** получает отчёт контроллера (файлы, процессы,
   статус), а не дамп содержимого одного файла. Это дефект, обнаруженный в первом LA-прогоне
   Pod 1: ответ был README вместо сводки.
7. **FAILED-задача** сохраняет правдивое предложение модели и добавляет реальный исход и список
   выполненных действий; пустой пузырь невозможен.
8. **SSE и БД совпадают**: последний delta-текст равен сохранённому сообщению; пустой завершённый
   ответ не отправляется.

### D2 FREE

`apps/backend/tests/test_d2_final_answer.py` — **25 passed**. Проверены через реальный stream
route: обычный текст, raw protocol, human prefix + protocol, сломанный tool call, цитата protocol,
tool echo (`pid`/`exit_code`), COMPLETED, FAILED `tests_not_verified`, `verification_required`,
пустой completion, потерянный первый токен, сохранение в БД, отсутствие protocol leakage.
Дополнительно `test_093_confirmation.py` проверяет переход `WAITING_CONFIRMATION → EXECUTING` и
`recover_interrupted → INTERRUPTED`.

### D2 LIVE

* Run ID: `live-20261108T131238` (LA).
* Backend: staged sidecar `source_digest=a7288b1b65615676998a7a6f415978981566ea9e4f1cd2120fe1d3e3ad1cb4fb`,
  `alex-backend.exe sha256=f43027175b83aef4eab434331b7379947127887b9335f1e90457f1239e698216`
  (исходники после последнего D2-исправления), установлен на время прогона.
* Qwen выполнил 21 model-origin вызов (`MODEL_ORIGIN_CALLS=21`, `INTERNAL_CALLS=0`,
  `HARNESS_CALLS=0`, `RETRIES=0`, `DENIED_CALLS=0`), задача **COMPLETED**.
* Реальные файлы: `shop/parser.py`, `shop/rules.py`, `shop/report.py`, `shop/cli.py`, `README.md`,
  `out/orders.json`, `out/report.md`.
* Независимая проверка: pytest **4 passed**; CLI stdout
  `LA2_DONE valid=7 skipped=6 total_usd=431.89`; payload совпадает с независимым расчётом.
* Финальный ответ в БД (647 символов) — отчёт контроллера: «Задача завершена… Что выполнено
  инструментами: … создан файл … запущен процесс … прочитан файл …». Нет `<tool_call>`,
  нет голого `pid=…`, нет пустого ответа. UI и БД показывают один и тот же текст.
* Длительность LA: 262 000 мс; общая сессия Pod 3: 348.8 с.
* Стоимость Pod 3 (по Gateway на stop): `$0.11445`.

---

## 4. Long autonomy

Новый challenge — не «сделай 20 вызовов», а реальный мини-проект:

* `SPEC.md` с точным контрактом;
* `data/orders.csv` (BOM, quoted comma, blank/malformed/duplicate/invalid rows);
* `data/rates.csv`;
* `shop/parser.py` и `shop/rules.py` с двумя намеренными дефектами;
* `tests/test_parser.py` и `tests/test_rules.py`, которые нельзя менять;
* требуется создать `shop/report.py`, `shop/cli.py`, `README.md`, запустить pytest, CLI, снова
  pytest, проверить JSON/Markdown.

Результат: **21 model-origin вызов**, задача **COMPLETED**, все постусловия выполнены.
Количество вызовов не форсировалось harness'ом: все 21 — `origin=model`.

`apps/backend/tests/test_long_autonomy.py::test_a_twenty_step_mixed_cycle_is_not_stopped_early`
— детерминированный FREE integration test на 20 смешанных решений (list/read/write/patch/
run_python/повторный тест/финальная проверка). Скрытого потолка 14 нет: `max_calls=40`,
`hard_max_calls=100`; тест подтверждает ≥20 выполненных вызовов и финальную перепроверку после
последней записи.

---

## 5. T3 — перезапуск окна

### T3 FREE — PASS

`artifacts/acceptance-126/free_restart_selftest.py t3` (изолированный data root, реальный
установленный `alex-llm.exe`, реальный backend и Computer, mock-модель):

* `waiting_confirmation` наблюдался и был подтверждён acceptance-approver (HTTP 200, approved);
* реальный `list_directory`/`git_status`/`run_python` выполнен;
* окно закрыто force-kill **во время EXECUTING** (не в `WAITING_CONFIRMATION`);
* backend действительно лёг и поднялся;
* задача стала **INTERRUPTED**; в UI появилась кнопка **Resume**, нажата через CDP;
* восстановлен тот же `task_id`, файлы целы, завершённый payload не повторён;
* финальный ответ сохранён с честной пометкой `tests_not_verified`;
* `device_id` не изменился; cleanup вернул процессы.

### T3 LIVE — BLOCKED

Run `live-20261108T131238` (Pod 3). LA прошёл; T3L стартовал, но harness упал на

```
TypeError: wait() takes 1 positional argument but 3 were given
artifacts/acceptance-123/live_restart.py:341
```

Это дефект harness, не продукта: `wait()` объявлен с keyword-only `seconds/interval`, а вызов был
позиционным. Исправлено сразу после прогона (`wait(..., seconds=120, interval=1.0)`), harness
компилируется, FREE-механика T3 остаётся зелёной. Повторный платный T3 не запускался: лимит
владельца — **3 Pod за всё задание**, и все три уже израсходованы (два LA-прогона и комбинированный
LA+T3).

Остаточный acceptance-task T3L был остановлен через `stop_stale_tasks`; активных acceptance-задач
нет.

---

## 6. T4 — обрыв Computer/native host

### T4 FREE — PASS

`free_restart_selftest.py t4` (реальный установленный процесс, изолированные данные):

* первый инструмент завершён, затем native run в полёте;
* процесс приостановлен по **PID** (не по имени), с независимым watchdog;
* backend зафиксировал `WAITING_DEVICE`, device `online=false`, heartbeat остановился;
* ложного SUCCESS не было; ответ во время offline пуст;
* resume вернул heartbeat; та же задача завершилась;
* повторных payload/побочных эффектов нет; `device_id` тот же;
* `finally` гарантирует resume; `watchdog_selftest.py` отдельно доказал, что watchdog
  восстанавливает процесс без harness, а отсутствующий/неизвестный PID отклоняется.

### T4 LIVE — BLOCKED

Не запускался: после Pod 3 лимит **3 Pod** исчерпан. T4 не подменялся бесплатным прогоном.

---

## 7. Тесты

| Набор | Результат |
|---|---|
| Backend pytest (полный) | **1023 passed, 1 skipped** |
| D2 stream + confirmation + long autonomy | **39 passed** (25 D2 + 5 confirmation + 4 LA/20-call + others) |
| Ruff check / format (backend) | PASS |
| Gateway Ruff / pytest | PASS |
| Frontend vitest / tsc / Prettier / vite build | PASS |
| Playwright | **21 passed** |
| Rust `cargo check --all-targets` | PASS |
| Rust `cargo test` | **128 passed, 2 ignored** (37 + 75 + 16) |
| Developer gate | **9/9 PASS** |
| LA fixture self-test | PASS (seeded fails; reference solution passes; tampered test detected) |
| T3/T4 free | PASS / PASS |
| Watchdog free | PASS |
| Candidate gate | 21/22 PASS; `rust.test` остановлен `rustc-LLVM ERROR: IO failure on output stream: no space on device` (диск C: был заполнен), не ошибкой теста |
| verify-artifact | **PASS** |

Примечание по диску: на C: было 320 MB свободно; удалён воспроизводимый
`src-tauri/target/debug` (19 GB) и старые локальные installer'ы. Для повторного `cargo test` в
candidate gate места всё равно не хватило; отдельный `cargo test` (debug) до этого прошёл
128/128, а `cargo check` на финальном дереве — PASS.

---

## 8. Деньги и Pod

| # | GPU | $/час | Назначение | Итог |
|---|---|---|---|---|
| 1 | NVIDIA L40S | 1.09 | LA (первый challenge) | 21 вызов, функционально PASS; отчёт-дамп файла → исправлено D2; stop подтверждён |
| 2 | NVIDIA L40S | 1.09 | LA (повтор) | 12 вызовов, `invalid_arguments` на write + `llm_unavailable`; честный FAILED; stop подтверждён |
| 3 | NVIDIA L40S | 1.09 | LA + T3 + T4 | **LA PASS, 21 вызов**; T3 harness TypeError; T4 не стартовал; stop подтверждён |

* Баланс на старте (фактический): **$10.8718949**; после: **$10.5996680417**;
  израсходовано **$0.2722269** (лимит $1.20).
* `pods []`, `current_spend_per_hr 0.01`, watchdog **disarmed**, AI **off**, reconnect не выполнялся.
* Compute Preferences владельца не менялись (`max_hourly_price=2`, `session_budget=2` —
  унаследованные настройки; они не увеличивались и не уменьшались).
* Capacity не ожидалась платно; все три Pod — NVIDIA L40S ($1.09/ч), ≤600 с, stop с резервом.

---

## 9. Release candidate

Построен и подписан **кандидат 1.2.6**, но **не опубликован**:

* `Canalla LLM_1.2.6_x64-setup.exe`
* SHA256 **`68af927ba5bb4fc010118278054b770320c172952edc3f75bc1263daa02d1f73`**
* `.sig` — production key **9B328EFF111D1FB2**, Ed25519/BLAKE2b-512, подпись проверена.
* `release-gate.py verify-artifact`: PASS (8/8, key identity, signature, gateway URL).
* Backend sidecar кандидата: `product_version=1.2.6`,
  `source_digest=d4dcbb382e471cbbc99eee638d0585efdfc18a1d8e067a937236554310292d81`,
  `sha256=a01a81f3bc54a94b773e5a84ee9569bd0dc7c0c3a911d6267ee669471d6bcefe`.
* Production updater manifest **не изменялся** (продолжает отдавать 1.2.5); тег v1.2.6 не создавался,
  GitHub Release не публиковался, старые assets не заменялись.

Установленная сборка возвращена к официальной **1.2.5**:
`alex-backend.exe sha256=f9046b83bb94db3b75e6224d1fa27fc6eae75fd302a0b28f130eeb6f1e63f953`,
`source_digest=b9aef542a9e334621953ccb919eaaf608ddead1c3c80f9947d6f240c5deef49a`.
Staged sidecar кандидата под старой версией не оставлен.

---

## 10. Данные и провенанс

* Verified SQLite backup перед работой: `artifacts/acceptance-126/pre-work-20261108T114908Z.db`,
  `integrity_check=ok`, sha256 `0d0381b9f3b1560988bdb98e366c9c258c50a3383a4341e63006125a14841c54`
  (локальный backup; в репозиторий не коммитился).
* До: users 2, chats 44, messages 100, paired 59 (1 revoked), device
  `f3548022-a676-463c-9fc5-b05bbd5ac907`.
* После: users 2, chats 48, messages 108, paired 59 (1 revoked), тот же device, integrity ok.
  +4 чата / +8 сообщений — это acceptance-чаты LA/T3L (по 1 user + 1 assistant, часть с повторным
  resume); пользовательские чаты не тронуты, вручную удалённые не восстанавливались,
  59 исторических device-записей не удалялись.
* `computer_mode=ask`, `workspace_roots=[]` — без изменений; Compute Preferences не менялись.
* Подсистемы после восстановления: AI **off**, Computer **ready**, Tor **ready** (цепь проверена),
  Memory **ready**, Web **configured**, Cloud **connected**.
* Активных acceptance-задач нет. Остаётся один пользовательский `INTERRUPTED` task
  (`c006fd33…`, `s:\`) — он не трогался и не возобновлялся.

---

## 11. Что оказалось дефектом продукта, а что harness

**Продукт:**

1. D2: финальный ответ мог быть префиксом tool protocol / эхом инструмента / дампом файла.
2. D2: после второго protocol-блока публиковался общий текст вместо статуса задачи.
3. T3/T4: подтверждённый инструмент оставлял задачу в `WAITING_CONFIRMATION`, поэтому рестарт
   во время длинного run не помечал её `INTERRUPTED`. Исправлено: `EXECUTING` до результата.
4. LA: при `invalid_arguments` модель сдавалась; задача завершалась честным FAILED (это корректно),
   но повторный прогон показал, что один сбой планировщика не должен превращаться в ложный успех.

**Harness:**

1. `expected_tools` требовал одновременно `patch_file` и `run_python`, хотя `write_file` и
   `run_process` — легитимные инструменты. Добавлены группы `expected_tools_any`.
2. `wait()` вызывался позиционно в `live_restart.py` → `TypeError` в T3 LIVE.
3. `ensure-app-cdp.ps1` закрывал окно graceful, а Tauri на graceful exit останавливает managed
   Pod. Теперь acceptance-окно останавливается force-kill; Pod переживает рестарт.
4. `suspend-app.ps1` приостанавливал все `alex-llm.exe` по имени. Теперь только явный PID,
   с проверкой StartTime и независимым watchdog.
5. `common.safe_run` не разрешал `git_status`/`git_diff` внутри sandbox — добавлены read-only
   инструменты.

---

## 12. Git и CI

Кодовый SHA этого цикла: **`883245a`** (`Close D2 and expand the 1.2.6 acceptance coverage`).
CI run [42490812164](https://github.com/volkrist/canalla-llm/actions/runs/42490812164): **5/7 success**.

| Job | Result |
|---|---|
| developer-gate | **PASS** |
| native-gate (`cargo check` + `cargo test`) | **PASS** |
| frontend-e2e (Playwright) | **PASS** |
| llama-runtime | **PASS** |
| llama-runtime-image | **PASS** |
| tor-runtime | **FAIL — external** |
| installer-build | **FAIL — external** |

Причина обоих FAIL — upstream Tor, а не код:

* `tor-runtime`: `TOR-PIN: FAIL — signed checksums file is c1fcd9ffa24c834156a081f0d391d332a1bf260bfaf7fd2a2a98d496e7fc28ea, pin says 539e778fa5ae4e9e061e5721a4d15108b085fd5f26e45629ba587264f021f564`.
  Tor Project изменил подписанный файл checksums после того, как pin 1.2.5 был зафиксирован.
* `installer-build`: тот же upstream, `tor-expert-bundle-windows-x86_64-15.0.25.tar.gz is 306 bytes, expected 22507599`
  (URL 15.0.25 больше не отдаёт архив). Локальный candidate собран из уже проверенного и
  застейдженного Tor runtime 1.2.5; сам runtime не менялся.

Финальный отчётный коммит добавляет только этот отчёт; код и версия не меняются. Pin Tor не
обновлялся: это отдельное supply-chain изменение, не входящее в задание D2/LA/T3/T4.

* Ветка: `release/canalla-1.2.4`.
* Push: обычный fast-forward, без force.
* Теги `v1.2.3`/`v1.2.4`/`v1.2.5` не двигались.

---

## 13. Оставшиеся блокеры (точные)

1. **T3 LIVE** — `TypeError` harness в `live_restart.py:341` на Pod 3. Исправлено после прогона.
   Повтор требует **нового Pod**, а лимит владельца — 3 Pod за задание; израсходованы все 3.
2. **T4 LIVE** — не стартовал по той же причине: лимит Pod исчерпан. T4 FREE PASS.
3. **Rust `cargo test` в candidate gate** — `no space on device` на C: (после удаления debug
   target свободно ~9.7 GB, полный debug-build требует больше). Отдельный `cargo test` ранее:
   128 passed, 2 ignored; `cargo check --all-targets` на финальном дереве PASS.
4. **Публикация 1.2.6** — запрещена условием владельца: T3/T4 live не подтверждены. Кандидат
   собран и подписан, но не выпущен; updater manifest не менялся.
5. **CI 7/7** — локально и в GitHub Actions недостижим без обновления Tor pin: upstream изменил
   signed checksums и перестал отдавать архив 15.0.25. Пять независимых job'ов (developer,
   native, frontend-e2e, llama-runtime, llama-runtime-image) — PASS.

---

## 14. Ссылки на evidence

* `artifacts/acceptance-126/readiness.json` — pre-GPU manifest.
* `artifacts/acceptance-126/free-evidence.json` — T3/T4 free результаты.
* `artifacts/acceptance-126/free_restart_selftest.py`, `watchdog_selftest.py`,
  `la_fixture_selftest.py`.
* `artifacts/acceptance-123/runs/live-20261108T131238/LA.json` — D2/LA live (локально,
  каталог `runs/` gitignored).
* `artifacts/acceptance-123/runs/live-20261108T131238/summary.json` — сессия, stop, watchdog.
* `artifacts/REPORT-canalla-1.2.6-closure.md` — этот отчёт.

---

**Итог:** D2 и Long Autonomy закрыты на реальной Qwen (21 вызов, COMPLETED, независимые
проверки). T3/T4 закрыты бесплатно на реальном установленном приложении и реальном Computer, но
платные live-прогоны заблокированы исчерпанием разрешённых 3 Pod после дефекта harness. v1.2.6
не опубликован; подписанный кандидат и все безопасные исправления подготовлены.
