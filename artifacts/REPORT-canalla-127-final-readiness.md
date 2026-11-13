# Canalla 1.2.7 — final readiness before the LIVE Qwen run

**Дата:** 13 ноября 2026.
**Ветка:** `feature/canalla-1.2.7-autonomous-agent`.
**Исходный SHA (проверен, не из отчёта):** `07b6edf` — рабочее дерево синхронно с origin.
**Последний подтверждённый CI:** 7/7 + Linux gate на `b9d85a2`. На `07b6edf` CI ещё не
запускался: это report-only коммит, и его SHA не выдаётся за проверенный.
**Публичная версия:** 1.2.6 — не изменялась (ни файлы, ни установка, ни updater, ни manifest).

---

## 1. Итог

| Направление | Статус |
|---|---|
| Windows packaged host (release binary → UIA → WinForms) | **PACKAGED HOST PASS** |
| Computer Connect (pair / heartbeat / jobs / reconnect) | **PASS** |
| Изоляция identity (тест host при работающей установленной 1.2.6) | **PASS** |
| Device credential lifecycle (19 проверок) | **PASS** |
| Model provenance (цепочка + независимый валидатор) | **UNIT PASS (12/12)** |
| Cost policy (математика бюджета) | **UNIT PASS (6/6)** |
| Внешний watchdog (dry run, fake provider) | **PASS (18/18)** |
| Негативные сценарии (16) | **PASS** |
| Package inspection | **STAGING PACKAGE PASS** |
| Isolated installer E2E | **NOT PERFORMED** (сознательно) |
| Authenticode | **BLOCKED_BY_EXTERNAL_CERTIFICATE** |
| Qwen LIVE | **NOT VERIFIED** |
| Production 1.2.6 | **NOT TOUCHED** |
| Потрачено на GPU | **$0.00** |

---

## 2. Реальные дефекты, найденные и исправленные в этом этапе

### 2.1 Тест мог использовать production credential (критично)

В прошлом этапе `credential_target()` молча отображал нераспознанный
`ALEX_DEVICE_CREDENTIAL_TARGET` на `Alex LLM/device-credential`, из-за чего acceptance host
перезаписал credential установленного приложения и вызвал цепочку re-pairing
(`f3548022…` → `861dfefa…` → `56e4716c…` → `66276759…` → `6413489d…`).

Теперь добавлено явное разделение identity:

- acceptance-запуск помечается `ALEX_ACCEPTANCE_RUN=1`;
- без собственного target он получает `Alex LLM/unusable-acceptance-without-target`, **не**
  production;
- даже явно указанный production target в acceptance-режиме превращается в
  `Alex LLM/unusable-acceptance-production-target`;
- непригодное значение по-прежнему уходит в `Alex LLM/unusable-<hash>`.

Два новых Rust-теста: `an_acceptance_run_without_a_target_never_uses_production`,
`an_acceptance_run_refuses_the_production_target`. Все 40 тестов `alex-host-loop` проходят.

### 2.2 Бюджет был посчитан неверно

Прежняя формулировка «480 с × $1.09/ч = $0.1453 ≤ $0.15» игнорировала округление биллинга и
30 с на подтверждение остановки: при 480 с *работы* счёт мог составить ≈510 с ≈ $0.1545.

Введён `app/compute/cost_policy.py`, из которого теперь берут числа и план, и watchdog:

```
0.15 / (1.09/3600) = 495.4 s  →  480 s billed (вниз до минуты)
480 s billed                  =  $0.145333
480 − 30 (termination grace)  =  450 s usable
450 − 10 (watchdog margin)    =  440 s stop request
```

`check_rate()` отказывает до создания Pod, если реальная цена выбранной карты делает worst case
дороже бюджета.

### 2.3 Sidecar отставал от source

Полный suite падал на `test_sidecar_stamp` после изменения backend. Sidecar пересобран
(`scripts/build-backend-sidecar.ps1`), stamp актуален; `staging`-hook снова подтверждает его перед
бандлом.

### 2.4 Диагностика 401 (из прошлого этапа, подтверждена)

`fetch_jobs` теперь возвращает тело ответа backend вместо голого `unauthorized`; это позволило
найти дефект §2.1.

### 2.5 Provenance-дайджест не совпадал с ledger (нашёл бы только LIVE-прогон)

Ledger (`ToolRun.input_digest`) хэширует `{action, command, arguments, path}`, а первый вариант
provenance-дайджеста — только аргументы. Кросс-проверка валидатора сравнивает их напрямую, поэтому
настоящий LIVE-прогон получил бы `INVALID_PROVENANCE` при полностью корректной работе модели.
Дефект не проявлялся на mock/scripted прогонах: `classify()` возвращает `MOCK_MODEL` до
кросс-проверки. Исправлено: `arguments_digest(arguments, tool_name)` повторяет формулу ledger'а,
добавлен тест `test_provenance_arguments_digest_matches_the_ledger`.

---

## 3. Model provenance — проверяемая цепочка

Реализовано в `apps/backend/app/agent/provenance.py` и встроено в реальный цикл:

```
MODEL_RESPONSE   provider, model, response id, request id, объявленные tool_call ids
  → MODEL_TOOL_CALL   точный tool_call_id, имя инструмента, digest аргументов
    → TOOL_RESULT     executed=true, tool_run_id, digest результата
      → VERIFY_PASSED  независимая проверка (evidence_ok=true)
```

- Записи пишутся в существующий `task_events` и запечатаны HMAC-SHA256 секретом развёртывания;
  подмена поля ломает seal.
- Provenance сообщает **провайдер**, не вызывающая сторона: `llamacpp`/`canalla-cloud` —
  `live_model=True` и runtime `llama.cpp`/`gateway`; `mock` — `live_model=False`; адаптер, который
  ничего не объявил, — `unknown` и не считается live.
- `LlamaCppProvider` записывает `response.id` сервера, `GatewayProvider` — transport request id +
  `x-request-id`; ключи и токены не попадают ни в журнал, ни в digest.
- Оркестратор пишет `MODEL_TOOL_CALL` до исполнения и `TOOL_RESULT` после, используя digest
  ledger-строки; `verify_result` пишет `VERIFY_PASSED`/`VERIFY_FAILED` независимо от того, кто его
  вызвал — модель или контроллер.

Валидатор `classify()` возвращает ровно один из статусов:
`REAL_MODEL_CONFIRMED`, `MOCK_MODEL`, `SCRIPTED_PLANNER`, `INCOMPLETE_TRACE`,
`INVALID_PROVENANCE`. Неизвестный «live» адаптер — `INVALID_PROVENANCE`, не PASS.

**Проверки:** 12/12 (`tests/test_127_provenance.py`), включая положительные и отрицательные
сценарии, сквозную запись через оркестратор и совпадение provenance-дайджеста с ledger'ом. Это
проверки валидатора, **не** LIVE-прогон.

**В реальном packaged-host прогоне** journal содержит 15 model responses и 14 tool calls и
классифицируется как `MOCK_MODEL` — scripted planner не может быть прочитан как настоящая модель.

---

## 4. Windows packaged host — повторная приёмка

`artifacts/acceptance-127/packaged_host_e2e.py` (без fake-host, без прямого вызова `uia.ps1`):

```
изолированный backend
→ ToolExecutor
→ Computer Connect (pair, heartbeat, claim)
→ alex-host-loop.exe (release, 2 222 080 байт, sha256 46903241…)
→ встроенный uia.ps1
→ Windows UI Automation
→ скомпилированное WinForms-приложение
```

| Шаг | Факт |
|---|---|
| Ввод | `UIA_PACKAGED_417`, прочитано обратно |
| Save | файл `uia_out.txt` содержит значение |
| Закрытие / повторное открытие | окно исчезло, второй процесс прочитал `.loaded` |
| Независимая проверка | `file_contains` + `file_exists` → `evidence_ok = true` |
| Reconnect | host остановлен и запущен заново, снова paired, job выполнен |
| Provenance | 15 model responses, 14 tool calls, `MOCK_MODEL` |
| Задача | `COMPLETED` |
| Мусор | `alex-host-loop` = 0, `AcceptanceApp` = 0 |
| Credential | `Canalla Acceptance/packaged-uia` создан и удалён (CredDelete) |

**WINDOWS PACKAGED HOST PASS** — повторён на новом бинаре и новом backend.

---

## 5. Identity isolation и device lifecycle

### 5.1 Изоляция при работающей установленной 1.2.6

`artifacts/acceptance-127/final-readiness/identity_isolation.py`:

- отдельная БД, `ALEX_DEVICE_DIR`, `ALEX_DEVICE_CREDENTIAL_TARGET=Canalla Acceptance/identity-…`,
  `ALEX_ACCEPTANCE_RUN=1`;
- host спарился со своим backend, получил **свой** device id (`062c72df…`), heartbeat есть;
- reconnect: после остановки и перезапуска — тот же device id;
- установленная 1.2.6 всё время отвечала `200`, версия 1.2.6, device `6413489d…` не изменился;
- production: `users/chats/messages/local_tasks` и набор device id (включая revoked) **без
  изменений**, `integrity ok`, `device.json` не изменился;
- cleanup: Credential Manager entry удалён и не читается, процессов не осталось, temp удалён.

Вердикт: **PASS** (`identity-isolation.json`).

### 5.2 Жизненный цикл device credential

`device_lifecycle.py` — 19/19 проверок на реальном backend:

pair → heartbeat → jobs → испорченный credential (401) → неизвестное устройство (401) → устройство
другого пользователя (401/403) → re-pair (старая строка revoked, новая работает) → revoke (401) →
re-pair после revoke → список содержит только активное устройство → `integrity ok` → revoked rows
сохранены → в БД только SHA-256 (64 символа) → плейнтекста credential в файле БД нет → temp удалён.

Вердикт: **PASS** (`device-lifecycle.json`).

Ограничение: `device.json` переезжает по `ALEX_DEVICE_DIR`, credential — по
`ALEX_DEVICE_CREDENTIAL_TARGET`; это разные переменные, и harness обязан задавать обе.

---

## 6. Производственные данные и identity

| Показатель | Start | End |
|---|---|---|
| users | 2 | 2 |
| chats | 54 | 54 |
| messages | 120 | 120 |
| paired_devices | 63 | 63 |
| local_tasks | 30 | 30 |
| integrity | ok | ok |
| foreign_key_check | пусто | пусто |
| active device | `6413489d…` | `6413489d…` |
| `device.json` id | `6413489d…` | `6413489d…` |

История device id (`f3548022…` → … → `6413489d…`) сохранена как есть: revoked-строки не удалялись,
старый id не восстанавливался, новых re-pairing не было. Изменение identity из прошлого этапа
описано честно и остаётся решением владельца, а не «исправленным» фактом.

Установленное приложение: `/health` 200, версия **1.2.6**.

---

## 7. Package inspection и установщик

`installer-inspection.json`:

| Проверка | Результат |
|---|---|
| `installMode` | `currentUser` |
| NSIS hooks | есть; pre-install чистит только `sidecar`/`runtime`, post-install убирает legacy `Programs\Alex LLM` под guard `alex-llm.exe` |
| Uninstall | удаляет только программу и Run-ключ; **не** трогает `%LOCALAPPDATA%\Alex LLM` и Credential Manager (`CredDelete` отсутствует) |
| Bundle inputs | `sidecar/alex-backend`, `sidecar/alex-host-loop.exe`, `runtime/tor` присутствуют |
| PE | host, backend, tor = **x64** |
| UIA | `uia.ps1` встроен в release host и в staged host |
| Staging | копирует release host и отказывает при stale sidecar |
| Sidecar stamp | актуален после пересборки |
| Release installers | 1.2.5 и 1.2.6, оба с minisign `.sig` |

Хэши: `Canalla LLM_1.2.6_x64-setup.exe` — 95 540 283 байт,
sha256 `485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1`.

**Статусы разделены:**
`RELEASE BINARY PASS` · `STAGING PACKAGE PASS` · `ISOLATED INSTALLER E2E NOT PERFORMED` ·
`PRODUCTION INSTALLATION NOT TOUCHED`.

Почему installer E2E не выполнялся: это либо установка 1.2.7 поверх/рядом с рабочей 1.2.6, либо
новая Windows VM — и то, и другое прямо запрещено заданием. Структурная проверка пакета не
выдаётся за установку.

---

## 8. Автономность: контракт модели и инструментов

`model-contract.json` снят с реальных объектов приложения (без обращения к модели):

- модель `orcarouter-qwen38-27b-q5km`, окно 32768 — только server-side;
- `LlamaCppProvider`/`GatewayProvider`: `supports_tools=true`, `live_model=true`;
- mock: `supports_tools=true`, `live_model=false`;
- 80 инструментов, включая `write_file`, `run_process`, `run_python`, `window_inspect`,
  `window_act`, `verify_result`, `deep_research`, `browser_gui`, `security_active`, `agent_queue`;
- состояния задачи: `PLANNING`, `EXECUTING`, `WAITING_DEVICE`, `WAITING_CONFIRMATION`,
  `WAITING_LLM`, `INTERRUPTED`, `VERIFYING`, `COMPLETED`, `FAILED`;
- завершение принадлежит `LocalTaskController.conclude`, а `verification_stale` +
  `plan_has_verification` не дают закрыть задачу без проверки;
- tool call, не подтверждённый ledger-дайджестом, не считается выполненным.

---

## 9. Watchdog и контроль расходов (dry run)

`scripts/live_qwen_watchdog.py` — **внешний** процесс: он видит Pod, даже если агент завис.

Проверено на fake provider (18/18):

| Сценарий | Ожидание | Факт |
|---|---|---|
| Нормальный Pod | stop на 440 с, подтверждение удаления | OK, billed ≈460 с, $0.1393 |
| Нет мощности | быстрый `CAPACITY_UNAVAILABLE`, Pod не создан | OK |
| Поздний Pod (появился на 30-й секунде) | всё равно stop внутри hard wall | 440 с |
| Дорогая карта ($1.79/ч) | `PRICE_ABOVE_BUDGET` до работы, Pod остановлен | OK |
| Второй Pod | `MULTIPLE_PODS`, оба остановлены | OK |
| Провайдер не подтверждает удаление | `TERMINATION_UNCONFIRMED`, без ложного cleanup | OK |
| Отказ terminate | громкая ошибка, Pod не объявлен удалённым | OK |
| Capacity wait | ≤60 с, `1200` нигде нет | OK |

**WATCHDOG DRY RUN PASS.** Реальный Pod не создавался.

---

## 10. Негативные сценарии (16/16 PASS)

| # | Сценарий | Ожидание | Доказательство |
|---|---|---|---|
| 1 | Qwen недоступна | WAITING_LLM, без ложного COMPLETED | `test_a_dead_provider_is_a_model_wait_with_a_real_idle_time` |
| 1.1 | обрыв провайдера после инструмента | результат сохранён, typed failure | `test_a_gateway_outage_after_a_tool_keeps_the_result_and_finishes` |
| 2 | инструмент вернул ошибку | модель получает ошибку, нет ложного успеха | `test_stale_patch_is_conflict` |
| 3 | Computer Connect отключён | WAITING_DEVICE, checkpoint, reconnect | `test_device_disconnect_waits` |
| 4 | поддельный tool result | seal/verifier не подтверждают | `test_a_tampered_record_fails_the_seal` |
| 4.1 | файл объявлен, но отсутствует | verification failed | `test_verifier_checks_real_files_and_digests` |
| 5 | падение процесса между записью и проверкой | тот же task_id, реальное содержимое | `test_a_killed_process_leaves_a_task_that_a_new_process_resumes` |
| 5.1 | restart | завершённый tool не повторяется | `test_restart_does_not_rerun_completed_tools` |
| 6 | дефект не исправлен | stall detection, bounded retries | `test_repair_fingerprint_ignores_noise_and_detects_stall` |
| 6.1 | retry loop | ограничен | `test_the_recovery_retry_schedule_is_bounded` |
| 7 | context budget | лимит честный, длинная задача не теряет цель | `test_summarize_never_hides_an_overflow`, `test_the_loop_runs_at_least_fifteen_model_calls` |
| 8 | стоимость GPU выше лимита | отказ до Pod | `test_a_pricier_pod_is_refused_before_creation` |
| 9 | установленная Canalla работает параллельно | production не меняется | `identity-isolation.json` |
| 10 | сбой harness | только свои ресурсы удалены | `device-lifecycle.json` |
| 11 | watchdog | остановка и подтверждение | `watchdog-dry-run.json` |

---

## 11. Computer Connect и Tor

- **Computer Connect:** установленное устройство `6413489d…` online; packaged host пары/сердцебиение/
  reconnect подтверждены; после тестов дочерних процессов не осталось.
- **Tor:** `runtime/tor.json` → `verified=true`, `method="socks5h"`, `tor_version=0.4.9.16`,
  процесс `tor` запущен (1). Always-ready не пере-сертифицировался: изменения этапа его не
  затрагивают, а credential/startup-путь проверен regression-тестами.

---

## 12. Authenticode

`Get-AuthenticodeSignature` для 1.2.5 и 1.2.6 → **NotSigned** (`SignatureStatus.NotSigned`).
Minisign-подпись updater (`9B328EFF111D1FB2`, `.sig` рядом с установщиком) существует и
продолжает защищать цепочку обновления, но **не заменяет** Authenticode.

Два независимых статуса:

- **CRYPTOGRAPHIC UPDATE SIGNATURE** — есть (minisign, публичный ключ в `tauri.conf.json`);
- **WINDOWS AUTHENTICODE SIGNATURE** — `BLOCKED_BY_EXTERNAL_CERTIFICATE`.

Что нужно (покупка не выполнялась): OV/EV Code Signing сертификат от публичного CA; приватный ключ
на FIPS 140-2 Level 2 hardware (USB-токен или облачный HSM: Azure Trusted Signing, DigiCert
KeyLocker, SSL.com eSigner); `signtool sign /fd SHA256 /tr <RFC3161> /td SHA256`; обязательный
timestamp; проверка `Get-AuthenticodeSignature = Valid` и `signtool verify /pa /all`; ключ только
в HSM/секрете CI. EV обычно даёт репутацию SmartScreen сразу, OV набирает её по мере загрузок —
гарантии «исчезнут все предупреждения» нет ни у одного варианта.

---

## 13. Free real-model test

Проверено: Ollama установлен, но **список моделей пуст**; GGUF-файлов и `llama-server.exe` в
`%LOCALAPPDATA%`, `Documents`, `~/.ollama`, `C:\models` нет.

**FREE REAL-MODEL TEST = NOT AVAILABLE.** Модели не скачивались, платный endpoint не поднимался,
другая модель за Qwen не выдавалась.

---

## 14. Готовность к LIVE Qwen

`preflight.py` → **READY**, блокеров нет:

| Проверка | Значение |
|---|---|
| Gateway `/health` | 200, ready |
| Updater для 1.2.6 | 204, не менялся |
| Pods | `[]` |
| Баланс | $8.563 (снижение на ~$0.01 — фоновый storage Network Volume, не GPU) |
| Установленная 1.2.6 | 200, версия совпадает |
| Device / Computer | online, совпадает с локальным |
| Tor | verified socks5h |
| Диск | достаточно |
| Budget policy | feasible, worst case $0.145333 |
| Watchdog self-test | PASS |
| Provenance | live-провайдеры распознаны |

Исполнитель: `artifacts/acceptance-127/live-qwen/live_acceptance.py`
(`--self-test` / `--preflight` / `--plan` / `--live --confirm "I AUTHORIZE ONE PAID POD"`).
Без точной фразы платный режим не стартует. Сценарий — TaskDesk (база, добавление, поиск, статусы,
веб-интерфейс), один запрос, без подсказок по ходу.

Критерии PASS: `REAL_MODEL_CONFIRMED`, `COMPLETED` + `evidence_ok`, реальные файлы, прошедшие
тесты после проваленного прогона, HTTP-ответ приложения, скриншот GUI, отсутствие дублей,
`pods == []`, расход ≤ $0.15, production не изменён.

**Платный запуск не выполнялся. FULL LIVE AUTONOMY = NOT VERIFIED.**

---

## 15. RC readiness

`rc-readiness.json`: версия `1.2.6` во всех четырёх поверхностях (`product.py`, `pyproject.toml`,
`package.json`, `tauri.conf.json`); ветка — feature; тегов на HEAD нет; установщика 1.2.7 нет.

`RC STAGING READY — BLOCKED PENDING LIVE QWEN + CERTIFICATE`.

Не выполнялось: bump на 1.2.7, сборка установщика 1.2.7, публикация, изменение manifest/updater,
подпись.

Nmap/ZAP adapters: **NOT IMPLEMENTED / OPTIONAL** — основную LIVE-приёмку не блокируют, встроенные
active checks продолжают работать (6 confirmed → 6 fixed → 0 open в прошлом этапе).

---

## 16. Проверки

| Проверка | Результат |
|---|---|
| Backend suite (полный, один прогон) | **1124 passed, 1 skipped** |
| Rust `cargo test --bin alex-host-loop` | **40 passed** |
| Packaged host E2E | **PASS** (повторён) |
| Identity isolation | **PASS** |
| Device lifecycle | **19/19 PASS** |
| Provenance tests | **12/12 PASS** |
| Cost policy tests | **6/6 PASS** |
| Negative matrix | **16/16 PASS** |
| Watchdog dry run | **PASS (18 checks)** |
| Installer inspection | **PASS** |
| Ruff check / format | чисто (backend) |
| Preflight | **READY** |

Полный suite запускался один раз; после него менялись только артефакты и отчёты.

---

## 17. Git и CI

- Ветка: `feature/canalla-1.2.7-autonomous-agent`.
- Изменённые файлы этапа:
  `apps/backend/app/agent/provenance.py` (новый), `apps/backend/app/compute/cost_policy.py` (новый),
  `apps/backend/app/providers.py`, `apps/backend/app/cloud/provider.py`,
  `apps/backend/app/tools/orchestrator.py`, `apps/backend/app/tools/executor.py`,
  `apps/backend/app/tools/local/journal.py`, `apps/desktop/src-tauri/src/credential.rs`,
  тесты `test_127_provenance.py`, `test_127_cost_policy.py`, `test_127_autonomous_e2e.py`,
  `scripts/live_qwen_watchdog.py`, `docs/model-provenance.md`,
  `artifacts/acceptance-127/**`.
- Не изменялись: версия, updater, manifest, release assets, ключи подписи, production БД.
- CI на итоговом SHA фиксируется после push; до этого ни один SHA не называется «зелёным».

---

## 18. Артефакты

```
artifacts/acceptance-127/final-readiness/
  baseline-start.json / baseline-end.json
  identity-isolation.json
  device-lifecycle.json
  model-contract.json
  model-provenance.json
  watchdog-dry-run.json
  autonomy-negative-tests.json
  installer-inspection.json
  rc-readiness.json
  summary.json
  identity_isolation.py / device_lifecycle.py / model_contract.py / model_provenance.py
  installer_inspection.py / rc_readiness.py / baseline.py / negative_scenarios.py / summary.py
artifacts/acceptance-127/packaged-host/        summary.json + два скриншота + сохранённый документ
artifacts/acceptance-127/live-qwen/            preflight.json, self-test.json, PLAN.md, live_acceptance.py
docs/model-provenance.md
```

Секретов, ключей, токенов и cookie в артефактах нет: только device id, хэши, счётчики и
идентификатор acceptance-таргета (не секрет).

---

## 19. Фактический расход

| Статья | Сумма |
|---|---|
| GPU Pod | **$0.00** (не создавался) |
| Платные API | **$0.00** |
| Баланс RunPod | $8.5728 → $8.5630 (фоновый storage тома, не GPU) |

---

## 20. Release readiness matrix

| Направление | Статус | Evidence | Limitations | Next action |
|---|---|---|---|---|
| WEB RESEARCH | FREE INTEGRATION PASS | `test_127_agent_core.py` research tests | TinyFish off | — |
| APP BUILD | FREE INTEGRATION PASS | `test_127_autonomous_e2e.py` | scripted planner | LIVE run |
| BROWSER GUI | FREE INTEGRATION PASS | screenshots в e2e | headless Chromium | LIVE run |
| WINDOWS GUI | WINDOWS LIVE PASS | packaged-host summary | WinForms fixture | — |
| PACKAGED HOST | PACKAGED HOST PASS | packaged-host summary | release binary | — |
| COMPUTER CONNECT | WINDOWS LIVE PASS | pair/heartbeat/reconnect + online | no login autostart (by design) | — |
| TOR | WINDOWS LIVE PASS | `tor.json` verified socks5h | не пере-сертифицировался | — |
| AUTO FIX | FREE INTEGRATION PASS | repair tests + failure→pass criterion | LIVE repair pending | LIVE run |
| DURABLE RECOVERY | FREE INTEGRATION PASS | `test_127_recovery_e2e.py` | live-Pod restart not tested | future |
| SECURITY ASSESSMENT | FREE INTEGRATION PASS | `test_127_security_active.py` | Nmap/ZAP optional | optional |
| MODEL TOOL CALLING | CODE READY | model-contract.json | real model pending | LIVE run |
| MODEL PROVENANCE | UNIT PASS (12/12) | model-provenance.json | LIVE verdict pending | LIVE run |
| CONTEXT MANAGEMENT | UNIT PASS | context tests | real overflow untested | LIVE run |
| COST CONTROL | UNIT PASS | cost policy tests | rate re-checked live | LIVE run |
| WATCHDOG | FREE INTEGRATION PASS | watchdog-dry-run.json | fake provider | LIVE run |
| INSTALLER | STAGING PACKAGE PASS | installer-inspection.json | installer E2E not performed | owner RC install |
| AUTHENTICODE | BLOCKED | NotSigned | external certificate | owner purchase |
| QWEN LIVE | NOT VERIFIED | preflight READY | needs one Pod | owner approval |
| RC READINESS | PARTIAL | rc-readiness.json | LIVE + cert | after both |

---

## 21. Оставшиеся блокеры

1. **LIVE Qwen не запускался** — нужен один Pod и разрешение владельца.
2. **Authenticode** — внешний сертификат + HSM; без него публичный релиз остаётся неподписанным.
3. **Isolated installer E2E** — требует установки 1.2.7 (нельзя без разрешения) или VM (запрещено).
4. **Identity установленной 1.2.6** изменилась в прошлом этапе (`6413489d…`); новые изменения её
   не трогают. Решение, считать ли это приемлемым для RC, — за владельцем.
5. Nmap/ZAP adapters не реализованы (не блокируют 1.2.7).

---

## 22. Что требует разрешения владельца

1. Один ограниченный LIVE Qwen прогон: **≤ 1 Pod, ≤ 450 с работы, ≤ 480 с billed, ≤ $0.15**,
   внешний watchdog на 440 с, автопроверка `pods == []`.
2. Покупка OV/EV Code Signing сертификата и выбор HSM.
3. Установка RC 1.2.7 для isolated installer E2E (или явный отказ от неё).
4. Публикация 1.2.7 — только после LIVE и сертификата.
