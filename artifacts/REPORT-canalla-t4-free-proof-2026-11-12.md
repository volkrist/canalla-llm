# Canalla LLM 1.2.6 — бесплатное доказательство T4 и готовность к выпуску

**Дата:** 2026-11-12
**Ветка / worktree:** `release/canalla-1.2.4`, существующее рабочее дерево
**HEAD:** `965a5b7` (продуктовый коммит — `b5b24f0`)
**Версия:** 1.2.6, **не опубликована**
**Платные Pod в этом этапе:** не создавались, деньги не расходовались

---

## 1. HEAD и кандидат

| Параметр | Значение |
|---|---|
| HEAD | `965a5b7` (`Record the free T4 evidence and format for the gate`) |
| Продуктовый коммит | `b5b24f0` (`Prove the free T4 shape end to end`) |
| Installer | `Canalla LLM_1.2.6_x64-setup.exe` |
| SHA256 installer | `485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1` |
| Sidecar | `aec597860fd1d04a5236c06c7e3990473eef018570561ac86213f900731c7f5f` |
| Подпись | production key **9B328EFF111D1FB2**, Ed25519 over BLAKE2b-512 |
| `verify-artifact` | **8/8 PASS** на коммите `b5b24f0` |
| Installed smoke | **INSTALLED-UI: PASS** |
| CI | **CI + Linux gate green** на `965a5b7` |

Кандидат собран из `b5b24f0`; последний коммит изменил только тесты и evidence, поэтому
пересобирать installer не потребовалось (это проверено: `apps/` в последнем коммите — только
файлы тестов, sidecar-stamp проходит, 11/11).

## 2. Три дефекта: что проверено и что исправлено

### 2.1 Fixture (T4)

**Было:** `run_t4` очищал папку сценария и не создавал `t4l.txt`, хотя в каталоге стоит
`setup="t4l_file"`. Модель открывала каталог, где лежал только `_prompt.txt`.

**Стало:** очистка и посев вынесены в `prepare_t4_root(scenario, root)` — сначала очистка, **потом**
`catalog.setup_fixture`. Проверено:

* `test_prepare_seeds_the_fixture_after_clearing` — старый `t4l.txt` с неверными байтами заменяется
  каноническим, `_prompt.txt` сохраняется, каталог прошлой попытки удаляется;
* содержимое `T4L_HOST_VALUE_417\n`, SHA256 `sha256(T4L_HOST_VALUE_417\n)` сверяется;
* `test_the_checker_reads_the_same_value_the_fixture_writes` — проверка в `run_t4` и фикстура
  каталога читают одно и то же значение;
* путь — тот же workspace, что и в промпте (`assert str(root) in context.user_prompt`);
* файл доступен инструменту: реальный `read_file` через device queue в E2E (§4);
* фикстура переживает reconnect: E2E §4 + `files_lost: []` в прогоне установленного приложения.

### 2.2 Approver (T4)

**Было:** `stop_approver.set()` стоял сразу после `resume_window`, поэтому первый инструмент после
восстановления никто не подтверждал.

**Стало:** `stop` вызывается только после финального ответа. Проверено:

* `test_the_approver_answers_before_and_after_a_resume` — цикл отвечает на подтверждение до
  «разрыва» и на новое — после; `confirmation_id`-ы, `allow`, HTTP 200 записаны; после `stop` новый
  запрос не подтверждается (бесконечного процесса нет);
* `test_run_t4_stops_the_approver_only_after_the_answer` — структурная защита: ровно один
  `stop_approver.set()`, и он **после** `final_answer = answer_text(...)` и после `resume_window(`;
* авто-подтверждение по-прежнему разрешено только внутри изолированного sandbox
  (`common.safe_run(row, common.SANDBOX)`);
* в E2E §4 каждое подтверждение связано с конкретным tool run, а результат инструмента проверяется
  по статусу `completed` и файлу, а не по HTTP 200.

### 2.3 Автомат состояний (продукт)

**Было:** `observe_tool` при retryable-ошибке (`timeout`) пробовал `WAITING_CONFIRMATION → RETRYING`;
машина отказывала; `invalid_task_transition` вылетал в `chat_stream`, ответ становился `status=error`.

**Стало:** выбор целевого состояния зависит от текущего: если задача «припаркована»
(`WAITING_CONFIRMATION`, `WAITING_DEVICE`, `WAITING_LLM`, `WAITING_WORKSPACE`), она возвращается в
`EXECUTING` — этот переход **разрешён автоматом из всех четырёх состояний**; иначе, как и раньше, —
`RETRYING`. `_observe_transition` остаётся диагностическим предохранителем, а не механизмом.

Проверено (не «подавлением исключения»):

| Требование | Тест | Результат |
|---|---|---|
| 1. состояние остаётся корректным | `..._returns_to_executing` | задача в `EXECUTING` |
| 2. нет ложного COMPLETED | тот же тест + `conclude` отдельно | `COMPLETED` только после `conclude` |
| 3. разрешение не теряется | run помечен `failed/timeout`, confirmation не «висит» | PASS |
| 4. инструмент не теряется | `TOOL_COMPLETED` + результат модели `status=timeout` | PASS |
| 5. нет вечного WAITING_CONFIRMATION | `EXECUTING` + `conclude → COMPLETED` | PASS |
| 6. retryable-ошибка доходит до исполнителя | `test_a_tool_timeout_returns_to_the_model...` | `status=timeout` |
| 7. исполнитель выбирает разрешённый путь | `EXECUTING`/`RETRYING` по состоянию | PASS |
| 8. нет повторного side effect | E2E: 3 уникальных digest-а | PASS |
| 9. модель получает понятный результат | packed `{status: timeout}` | PASS |
| 10. задача может завершиться | `conclude → COMPLETED` + E2E | PASS |

Отрицательные тесты: `test_a_completed_task_is_never_resurrected_by_a_late_error` (терминальное
состояние не оживает, пишется `TRANSITION_REJECTED`) и `test_observing_the_same_failure_twice_is_stable`
(повтор не ломает состояние, `retry_count` растёт). Всего в файле **5 тестов**, все PASS.

## 3. Полный бесплатный T4 E2E

### 3.1 Точная форма T4L на реальном backend + реальной очереди устройств

`apps/backend/tests/test_126_t4_e2e.py` — модель скриптовая, всё остальное настоящее (FastAPI,
SQLite, `chat`-инфраструктура, device queue с claim-ами, confirmation API, executor, state machine,
journal). Сценарий: `read_file t4l.txt` → `write_file t4l_out.txt` → `read_file t4l_out.txt` →
финал.

Результат: **2/2 PASS**:
* `t4l_out.txt` создан на диске и содержит `T4L_HOST_VALUE_417`;
* исходный `t4l.txt` не изменён;
* три model-origin run-а, все `completed`, digest-ы уникальны (повторов нет);
* каждое подтверждение — HTTP 200 и связано с конкретным run-ом (`read_file`, `write_file`);
* задача: `COMPLETED`;
* `host.handled == [read_file, write_file, read_file]`.

### 3.2 Установленное приложение: разрыв и Resume

`artifacts/acceptance-126/free_restart_selftest.py t4` на реальном установленном окне:
`first_tool`, `executing_at_disconnect`, `typed_offline` (`WAITING_DEVICE`), `heartbeat_returned`,
`offline_not_success`, `same_task`, `terminal`, `answer_saved`, `no_duplicate_payloads`,
`device_stable`, `confirmation_observed` — **PASS** (два прогона подряд).
`t3` — **PASS**.

**Время:** T4 — **96 с**, T3 — **118 с** (включая старт окна, 60-секундный инструмент, разрыв и
Resume). При сессии 480 с это оставляет ~380 с на размышления Qwen.

**Какие слои покрыты:** в 3.2 — реальный процесс/окно/backend, настоящие подтверждения и Resume; в
3.1 — точные инструменты, файл и переход в `COMPLETED`. Слой, который ни один бесплатный прогон не
воспроизводит полностью, — это настоящая Qwen на Gateway (только он требует Pod); он будет
единственным новым элементом платного прогона.

## 4. Регрессии, которые тоже прогнаны

| Проверка | Результат |
|---|---|
| Backend (полный) | **1086 passed, 1 skipped** (единственный FAIL — штатный страж «sidecar устарел», затем sidecar пересобран и страж 11/11 PASS) |
| `test_126_*` (4 файла) | 17 passed |
| `test_harness_contracts.py` | 7/7 |
| `coordinator_selftest` / `release_steps_selftest` | 51/51 / 73/73 |
| SQLite storm (24 потока) | PASS: 565 опросов, 52 задания, `database_busy` нет, ход 2.6 с |
| `harness-check.py` (F821/F811/F823) | PASS, 8 файлов |
| Preflight | 10/10 флагов PASS |

## 5. Данные и устройства

`integrity ok`, users 2, chats 52, messages 116, paired_devices 59,
`device_id f3548022-a676-463c-9fc0-b05bbd5ac907` (не менялся), `computer_mode=ask`.
Ничего не откатывалось и не удалялось.

## 6. Деньги и Pod

| Параметр | Значение |
|---|---|
| Баланс RunPod | **$9.0861733685** |
| Расход цикла (по балансу) | **$1.0447** |
| Лимит цикла | $1.10 |
| Остаток | **$0.0553** |
| Pod за цикл | **5 из 5** |
| Активных Pod | **0** (`pods []`) |
| Стоящий платёж | ~$0.01/ч (storage), из-за него остаток тает и без Pod |

Новый платный запуск **запрещён** до отдельного разрешения: и Pod-лимит, и деньги исчерпаны.
Предложение владельцу: следующий совокупный потолок **$1.45** и **6 Pod** за цикл (то есть один
дополнительный). Это предложение, а не разрешение.

## 7. READY_FOR_FINAL_T4

`artifacts/acceptance-126/ready-for-final-t4.json` — 19 флагов, все подтверждены реальными
проверками:

```
candidate_valid · signature_valid · ci_pass · t3_live_preserved · t4_free_pass
fixture_verified · approver_after_resume_pass · task_state_machine_pass
database_concurrency_pass · stream_finalization_pass · harness_static_checks_pass
installed_smoke_pass · data_safe · device_stable · watchdog_ready
budget_guard_ready · evidence_collector_ready · no_active_pods · no_duplicate_coordinator
→ READY_FOR_FINAL_T4 = true, spend_allowed_now = false
```

## 8. Диагностика платного прогона

До старта проверено, что сохраняются: backend log, stderr/stdout раннера (координатор пишет
`output_tail`), task journal (`task_events`), state transitions, confirmation events, device
heartbeat, model-origin tools, Gateway events, `assistant.status`, строка `local_tasks`, reconnect,
таймстемпы, результаты инструментов, файл результата и SHA256, финальный verdict.

Добавлено и проверено: если сценарий раннера падает с исключением, `runner.scenario_failure`
пишет `<SCENARIO>.json` с `verdict: ERROR`, причиной и traceback (тест
`test_a_scenario_that_raises_still_leaves_evidence`), а `summary.json` по-прежнему пишется в
`finally`. Свободный harness пишет `last-result.json`.

## 9. Состояние координатора

`finished`, `outcome = LIVE_FAILED_T4L`, `pods_created = 5`, `budget_usd = 1.10`,
`billing.measured_spend_usd = 1.0447`, монитор не запущен, дубликата нет. T3L PASS сохранён.

## 10. T4 LIVE — фактический статус

**FAIL** по реальному evidence `live-20261111T174431` (A100-SXM4-80GB, 494.3 с, $0.2458):
механизм восстановления доказан (задача, 4 model-origin вызова, 3 подтверждения, `WAITING_DEVICE`,
возврат heartbeat, та же задача, без повторов, device стабилен), но финального `COMPLETED` не было —
из-за трёх дефектов, перечисленных в §2.

Нового PASS нет и не заявляется: платных запусков после исправлений не было.

## 11. Итоговая таблица

| Гейт | Статус |
|---|---|
| D2 LIVE / LA LIVE / T3 LIVE | PASS (без регрессии, не повторялись) |
| T3 FREE / T4 FREE на текущем артефакте | PASS / PASS |
| Fixture / Approver / State machine | PASS (тесты, §2) |
| T4 LIVE | **FAIL** (реальный прогон 2026-11-11; исправления доказаны бесплатно) |
| TOR / CI 7/7 / installed smoke | PASS |
| DATA / DEVICE | PASS / PASS |
| SIGNATURE | VALID (`9B328EFF111D1FB2`) |
| PODS | **STOPPED** (0) |
| GPU SPEND | **$1.0447** за цикл, баланс $9.0861733685 |
| RELEASE 1.2.6 | **NOT PUBLISHED** |
| UPDATER | UNCHANGED (1.2.5) |
| READY_FOR_FINAL_T4 | **true** |
| Следующий этап (1.2.7) | не начат: начнётся только после публикации 1.2.6 |

## 12. Следующий шаг

1. Владелец выдаёт отдельное разрешение: **один** Pod, потолок **$1.45** (6 Pod за цикл).
2. Монитор запускается тем же способом (`run-monitor.ps1` с новыми числами): он ждёт размещаемую
   GPU, поднимает **один** Pod, проводит T4 LIVE по уже проверенному пайплайну, останавливает Pod и
   при PASS **сам** вызывает `release_coordinator.py --execute` (тег `v1.2.6` → GitHub Release →
   manifest → post-release).
3. Публикация запрещена при любом непройденном гейте.
