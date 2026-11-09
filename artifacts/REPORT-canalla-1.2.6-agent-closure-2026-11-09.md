# Canalla LLM 1.2.6 — автономный агент, установленный UI, security assessment и закрытие Tor-блокера

Дата: 9 ноября 2026. Ветка `release/canalla-1.2.4`. Итоговый код: `ac7c368` (CI 7/7).
Платные Pod в этом цикле: **0** (capacity недоступна, ожидание не оплачивалось).

Отчёт о фактическом состоянии. Всё, что не доказано, помечено как PARTIAL или BLOCKED.

---

## Итоговая таблица

| Гейт | Результат |
|---|---|
| TOR | **PASS** — pin 16.0, подпись Tor Project проверена, CI `tor-runtime` PASS |
| CI | **PASS** — 7/7 на `ac7c368`, Linux gate success |
| STATUS UI | **PASS** — установленный 1.2.6 (тестовая сборка), 18 проверок, 5 скриншотов |
| WEB RESEARCH | **PARTIAL** — Search/Fetch/Read/Used считаются честно; многостраничное продолжение ограничено бюджетами и зависит от модели |
| RESEARCH-BUILD-RUN-VERIFY | **PARTIAL** — цепочка реализована и покрыта mock-e2e; live-прогон на Qwen в этом цикле не проводился (capacity) |
| SECURITY ASSESSMENT | **PASS (free)** — scope + OSV advisories + static review + retest, 19 тестов; live на Qwen не требовался |
| T3 LIVE | **BLOCKED** — `gpu_unavailable`, Pod не создан |
| T4 LIVE | **BLOCKED** — тот же внешний блокер, попытка не тратилась |
| DATA PRESERVATION | **PASS** |
| RELEASE 1.2.6 | **NOT PUBLISHED** |

---

## 1. Git HEAD и CI

Коммиты этого цикла:

| SHA | Что |
|---|---|
| `c630524` | Repin Tor to the supported 16.0 bundle |
| `577ac35` | Prove the installed status UI on 1.2.6 |
| `bde80de` | Add an authorized security assessment workflow |
| `c95787d` | Count a source as used only when cited |
| `ac7c368` | Format the security retry call |

`origin/release/canalla-1.2.4` = `ac7c368`. Push обычный, без force.

CI на `ac7c368`: **7/7 success** — `developer-gate`, `native-gate`, `frontend-e2e`,
`tor-runtime`, `installer-build`, `llama-runtime`, `llama-runtime-image`.
Linux gate (Ubuntu: rust + backend): **success**.

---

## 2. Tor supply-chain blocker

**Причина 306 байт.** Не редирект и не защитная страница: каталог
`https://dist.torproject.org/torbrowser/15.0.25/` удалён, и сервер отдаёт
`404 text/html` размером ровно 306 байт. В каталоге `torbrowser/` остался только `16.0`
(и alpha), то есть 15.0.25 больше не поддерживается.

**Исправление (без ослабления проверок).**

- `scripts/tor-runtime.json` перепинен на **16.0**:
  - windows bundle `23 537 200` байт, sha256 `2ee123e9167318a3742778ff704e75f01d4780f05a14522d451fe011011077c4`;
  - linux bundle `33 506 382` байта, sha256 `cc950ebe9acb80188325c3a2cd8817def7dd7237d6fdcd861bb87b234a0cb2fd`;
  - signed checksums `sha256 2ae772b24308e9bdb5174fbcf26dcfd7e760db513642038a2cc5d57316e56823`;
  - daemon `0.4.9.15`, `corresponding_source` → `tor-0.4.9.15.tar.gz`.
- Подпись проверена реально: импортирован ключ по fingerprint
  `EF6E286DDA85EA2A4BA7DE684E2C6E8793298290`, `gpg --verify` дал
  **Good signature** от Tor Browser Developers, primary key совпал с пином.
  `scripts/verify-tor-runtime-pin.py` → `TOR-PIN: PASS — 16.0 (0.4.9.15); signature verified`.
- `scripts/fetch-tor-runtime.py` теперь отвергает не-gzip ответ до проверки размера:
  сообщение называет причину («the host returned an error page»), а не бессмысленное
  «306 bytes, expected 22507599».
- CI: cache key `tor-runtime-16.0`, проверка версии демона обновлена на `0.4.9.15`.
- `tests/test_tor_bundle.py` больше не требует трёх компонентов в номере релиза
  (16.0 — валидный номер), проверяется форма `\d+(\.\d+){1,3}`.
- Механизм неизменяемости сохранён: `--cache` с повторной проверкой размера и SHA256 на каждом
  запуске; несовпадение — hard FAIL; новый checksum не принимается автоматически.

Проверено локально: архив скачан, sha256 совпал с подписанным файлом, демон запущен и ответил
`Tor version 0.4.9.15`; `tests/test_tor_bundle.py` + `tests/test_tor_service.py` — 43 passed.

---

## 3. CI 7/7

`tor-runtime` и `installer-build` теперь зелёные; `installer-build` собрал тестовый NSIS
(`Canalla LLM_1.2.6_x64-setup.exe`, sha256 `d9f092c0015b645c45ba0105f16fdab9165802278bd9482c0456f1fb413a0021`)
и корректно **отказался** принимать неподписанный артефакт (`artifact.signature — missing
signature`), как и требует production-гейт.

Особенность процесса: один push (`bde80de`) упал на `ruff.format.backend`, потому что форматирование
`orchestrator.py` осталось в рабочем дереве. Исправлено коммитом `ac7c368`; повторный CI — 7/7.
Это ошибка процесса, не продукта.

---

## 4. Установленный UI — реальные скриншоты

Сценарий: `apps/desktop/e2e/installed-status.mjs` — настоящее окно установленного
`alex-llm.exe` через CDP, изолированные data root / device / credential, без GPU.

Проверено на установленной сборке 1.2.6 (тестовый installer):
`Работает · Run pytest · 3/3 · последняя активность только что`,
`Ждём вычислительные ресурсы · 42 с назад · offline`,
`Не удалось завершить · tests_not_verified · выполнено 2 из 3`,
`Лимит страниц: 10 из 10 страниц · 3 из 12 загрузок · 1 из 5 поисков`,
`Завершено · Проект создан, 5 тестов пройдено.`
18 проверок PASS, включая отсутствие «Завершено» при 3/3 и отсутствие Stop у завершённой задачи.

Скриншоты (в репозитории):
`artifacts/acceptance-release-126/installed-status-{verifying,waiting-resource,partial,page-limit,completed}.png`.

Важно: на машине сейчас стоит **официальная 1.2.5** (возвращена после приёмки), потому что 1.2.6
не опубликован. Evidence снят с временной тестовой сборки 1.2.6, собранной из этого дерева;
данные пользователя при установке/откате не менялись.

---

## 5. Исправленные статусы задач

Единый источник — `apps/backend/app/tools/local/status.py`; UI читает `phase` и больше ничего не
домысливает.

- `phase`, `terminal`, `waiting`, `current_action`, `completed_count`, `worked_count`,
  `remaining_count`, `last_progress_at`, `idle_seconds`, `stalled`, `stop_reason`, `error_code`,
  `recoverable`, `resume_available`, `limit`, `budgets`.
- `completed` — только когда задачу закрыл контроллер. 3/3 шагов при идущей проверке остаются
  `verifying`; провал с реальной работой — `partial`; без работы — `failed`; Stop — `cancelled`.
- Ожидание compute отличается от падения модели (`waiting_resource` vs `waiting_model`).
- Композер и плейсхолдер пустого ответа не показывают второй, спорящий статус.
- Монотонные метки журнала (два события в одну микросекунду больше не переставляются).
- UI: компактная строка, раскрываемые детали, таймлайн из `task_events`, Resume только когда
  backend это разрешает.

---

## 6. Web: Search / Fetch / Read / Used

- **Search** и **Fetch** считаются по завершённым `ToolRun`, раздельно web/Tor и по происхождению
  (`model` / `server_policy`).
- **Read** — реально полученный текст: каждый сетевой запуск пишет
  `result_metadata.web = {requested, opened, failed}`; задача агрегирует их в
  `web.pages_requested / pages_opened / pages_failed`.
- **Used** — теперь честный: `GET /messages/{id}/web-sources` помечает `used` только если ответ
  действительно содержит метку `[W1]`/`[T1]`. Панель источников показывает
  «использовано в ответе N из M» и помечает каждый использованный источник.
- Если поиск был, а страниц открыто 0 — интерфейс прямо пишет «источники найдены, страницы не
  прочитаны»; сниппет не выдаётся за прочитанный документ.
- Один ограниченный бесплатный `web_fetch` выполняется контроллером, если модель только искала;
  он помечен `server_policy` и не засчитывается как работа модели.

**PARTIAL:** многостраничное продолжение существует (модель может вызывать `web_fetch` до
бюджета), но автоматической «второй партии» по достижении мягкого лимита нет. Это ограничение
описано, а не замаскировано.

---

## 7. Лимиты и причины

| Лимит | Значение | Почему |
|---|---|---|
| `tools_task_max_fetch` | 12 | Загрузки в задаче |
| `tools_task_max_pages` | 36 | 12 загрузок × до 3 URL — согласовано с fetch |
| `tools_task_hard_pages` | 60 | Жёсткий предел |
| `tools_max_chars` | 20 000 | Извлечённый текст |
| `tools_task_max_calls` | 40 | Вызовы инструментов в задаче |
| `tools_task_max_files` | 20 | Изменённые файлы |

Раньше задача получала `max_pages = 10` (per-call значение), поэтому легальные 12 загрузок
упирались в безымянный лимит. Tor больше не списывает бюджет прямой сети. Сообщение об
исчерпании теперь содержит реальные счётчики.

---

## 8. Research → Build → Run → Verify

Цепочка существует: план (`default_plan`), файлы с проверкой SHA, `run_python`/`run_process` с
реальным stdout/exit code, pytest, `final_review` с `verification_required`/`verification_stale`,
автоисправление по `next_forced_action`. Покрыта mock-e2e (`test_mock_e2e_fix_and_verify`,
`test_web_plus_coding_mock_e2e`) и LA-сценарием прошлого цикла (21 model-origin вызов).

**PARTIAL:** live-прогон «изучи → собери → запусти → проверь» на Qwen в этом цикле не выполнялся:
capacity недоступна. Ни один mock не выдан за live.

---

## 9. Тестовое приложение и запуск

Новых пользовательских проектов этот цикл не создавал (платных прогонов не было). Free-проверки
используют изолированный sandbox `C:\Users\Volkr\Canalla Acceptance`; T3/T4 self-test создают там
свои workspace-копии, запускают настоящий Python/pytest и удаляют их.

---

## 10. GUI-проверки

- Установленное окно: навигация, композер, панель статуса, скриншоты (§4).
- GUI-сценарий «нажать в приложении» на реальном Qwen в этом цикле **не проводился**: нет Pod.
  Прежние H-сценарии (T2/T5–T8) не повторялись.

---

## 11. Security assessment

Новый продукт-срез `apps/backend/app/security_assessment/`:

- **Scope** (`scope.py`): цели — только разрешённые workspace roots; путь вне roots отклоняется
  (`security_target_out_of_scope`); внешний URL без явного подтверждения права отклоняется
  (`security_target_not_authorized`); активные сетевые проверки не реализованы и об этом сказано.
  Запрещённые классы действий перечислены явно.
- **Advisories** (`advisories.py`): OSV (`/v1/querybatch` + `/v1/vulns/{id}`) — точная проверка
  версии, фиксированные версии, CVE-алиасы. Недоступность сервиса — типизированный
  `advisory_lookup_unavailable`, а не «чисто».
- **Audit** (`audit.py`): правила shell=True, eval/exec, SQL-конкатенация, verify=False, pickle,
  yaml.load, MD5/SHA1 для паролей, debug=True, секреты в исходниках. У каждой находки файл, строка,
  CWE, redacted evidence и совет. Тестовый код понижается до low; литералы и плейсхолдеры не
  считаются находками.
- **Workflow** (`security_flow.py` + orchestrator): оценка выполняется контроллером **через уже
  разрешённые** `list_directory`/`read_file`, без прямого чтения файлов backend-ом; после любой
  записи оценка помечается устаревшей и **перезапускается** до финального ответа. Задача не может
  закрыться с открытой находкой high/critical или без проверенных advisories.
- **UI**: строка «Безопасность: N проблем высокого риска · файлов … · пакетов … · advisories …»,
  список находок с severity/файлом/строкой и отметка, была ли повторная проверка.

Тесты: `apps/backend/tests/test_security_assessment.py` — 19 тестов (scope, внешняя цель,
запрещённая проверка, правила, redaction, парсинг зависимостей, advisory-фикстура, offline,
fix → retest → COMPLETED, открытая находка → FAILED, out-of-scope → FAILED).

**Ограничения, честно:** активные сетевые проверки (сканеры портов, эксплуатация) не реализованы
намеренно; CISA KEV/NVD напрямую не опрашиваются — OSV агрегирует их и CVE-алиасы; авто-фикс
выполняет модель, а не «магический» патчер.

---

## 12. Найденные и исправленные дефекты

1. **Tor pin** указывал на удалённый каталог → 404/306 байт. Перепинен на 16.0 с проверенной
   подписью; добавлена защита от не-gzip.
2. **`ruff.format` в CI** — форматирование осталось вне коммита. Исправлено.
3. **Статус `partial` считал пропущенные шаги работой.** Пропущенный шаг — не выполненная работа:
   теперь `partial` только при реальном `COMPLETED`-шаге или вызовах инструментов.
4. **Scope не сохранялся** (мутация detached-объекта без commit) → задача не видела security
   contract. Переписано на явную сессию с commit.
5. **`verdict()` проверял `security_scope` раньше ошибки scope**, поэтому отказ по внешней цели
   мог закрыться как COMPLETED. Порядок исправлен.
6. **`eval("1 + 1")` считался находкой** — литеральный аргумент теперь распознаётся.
7. **Фраза «мой проект» авторизовала любой внешний URL.** Убрана; для внешних целей нужны явные
   формулировки права.
8. **`used` не считался** — источник считался использованным по факту загрузки. Теперь только по
   цитированию.

---

## 13. T3 FREE / LIVE

- **FREE: PASS.** `artifacts/acceptance-126/free_restart_selftest.py t3` на установленном окне,
  mock-модель, изолированные данные: инструмент выполнен, окно убито, backend поднят, Resume
  через штатную кнопку, `same task_id`, файлы сохранены, повторных payload нет, device стабилен,
  cleanup выполнен.
- **LIVE: BLOCKED.** `POST /cloud/compute/ensure` → `waiting_capacity`, `last_error=gpu_unavailable`.
  Pod **не создан** (`managed:false`, `session:null`), платного ожидания не было, watchdog снят,
  AI остановлен. Попытка заняла ~30 с и не стоила ничего.

## 14. T4 FREE / LIVE

- **FREE: PASS.** `free_restart_selftest.py t4`: heartbeat исчез, backend зафиксировал typed state,
  transport восстановлен, heartbeat вернулся, device тот же, side effects не повторились.
- **LIVE: BLOCKED.** Тот же внешний блокер capacity; отдельная попытка не запускалась, чтобы не
  тратить право на Pod впустую.

## 15. D2 / LA

Ранее подтверждены live (D2 — корректный финальный ответ; LA — 21 model-origin вызовов). В этом
цикле не повторялись: регрессии не обнаружено, платного права на повтор не тратилось.

---

## 16. Результаты наборов

| Набор | Результат |
|---|---|
| Backend | developer gate PASS; 1062 теста собрано (`pytest -q` в CI — PASS) |
| Gateway | 262 passed |
| Frontend vitest | 327 passed (29 файлов) |
| TypeScript / Prettier / Vite | clean / clean / ok |
| Playwright | 25 passed (в CI и локально) |
| Rust | 128 passed, 2 ignored (`37 + 75 + 16`) |
| Developer gate | 9/9 PASS (локально и в CI) |
| Tor bundle/service | 43 passed |
| Installed UI | 18 проверок PASS |

---

## 17. Установленная версия и digests

- На машине: **официальная 1.2.5** (`/health` → `"version":"1.2.5"`), sidecar
  `f9046b83bb94db3b75e6224d1fa27fc6eae75fd302a0b28f130eeb6f1e63f953`.
- Staged sidecar 1.2.6 из финальных исходников:
  `5f44cad88268b00a5499fb4456ed315676d415ebe2aef7871b56b953bf2422c0` (23 848 835 байт).
- Тестовый installer, на котором проверялся новый UI:
  `Canalla LLM_1.2.6_x64-setup.exe` sha256 `184506548a2ebf0e7d16f3c7f2b69a35c0523a1b0be5e4a3dbefd62c1218fe07`
  (не подписывался production-ключом, не публиковался).
- Tor: pin 16.0, демон `0.4.9.15`, `tor.exe` sha256
  `1607d98f5d13859077b1b349c55beba76b09acc788f0431fd824d11966fb1074`.

---

## 18. Сохранность пользовательских данных

`%LOCALAPPDATA%\Alex LLM\data\alex.db` до и после: `integrity_check = ok`, users 2, chats 48,
messages 108, paired_devices 59 (1 revoked), `device_id` `f3548022-a676-463c-9fc0-b05bbd5ac907`,
`computer_mode = ask`, workspace_roots не менялись. Перед установкой кандидата создан и проверен
штатный backup: `20261109T113316Z-manual`, `verified: true`, app_version 1.2.5,
schema 0016, 5 218 551 байт (`artifacts/acceptance-release-126/pre-install-backup.json`).

---

## 19. Device ID и paired_devices

Device ID: `f3548022-a676-463c-9fc0-b05bbd5ac907` (не изменился). `paired_devices`: 59, из них
1 отозван. Исторические записи не удалялись и не отзывались.

---

## 20. RunPod

`pods []`, новый Pod не создавался, новых расходов **$0**. Баланс до и после:
`10.3545285228` → `10.3545285228`. `current_spend_per_hr 0.01` — остаточное начисление
завершённого ранее Pod (документированное поведение провайдера). Watchdog disarmed, AI off.

---

## 21. GitHub Release

**Нет.** Тег `v1.2.6` не создавался, assets не загружались. Причина: обязательные live-гейты
T3/T4 не пройдены (внешняя capacity), а публиковать релиз при непройденном гейте запрещено.

---

## 22. Production updater

Не менялся. Manifest по-прежнему указывает на 1.2.5; backup manifest и rollback не трогались.
Подписывающий ключ `9B328EFF111D1FB2` не менялся; `verify-artifact` в CI корректно отверг
неподписанный тестовый инсталлятор.

---

## 23. Что действительно готово к использованию

- Tor runtime обновляется воспроизводимо и безопасно; CI полностью зелёный.
- Статус задачи больше не врёт: 3/3 шагов не означают COMPLETED, ожидание модели/ресурсов
  называется своим именем, Stop/Resume согласованы с backend.
- Web-счётчики честные, включая «найдено, но не прочитано» и «использовано в ответе».
- Security assessment работает на локальном проекте: scope, advisories по точным версиям,
  статические находки с evidence, исправление и обязательный retest.
- Установленный UI проверен на реальном окне и выглядит корректно.

## 24. PARTIAL / BLOCKED

- **T3 LIVE / T4 LIVE — BLOCKED**: `gpu_unavailable`. Внешний блокер провайдера; ни код, ни
  harness не виноваты — self-test и preflight чистые.
- **WEB RESEARCH — PARTIAL**: автоматического продолжения исследования после мягкого лимита нет.
- **RESEARCH-BUILD-RUN-VERIFY — PARTIAL** в смысле live-доказательства: бесплатные прогоны есть,
  Qwen-прогон в этом цикле не выполнялся.
- **Security assessment** не включает активные сетевые проверки и прямые запросы к CISA KEV/NVD
  (используется OSV, который их агрегирует).

---

### Что нужно, чтобы закрыть 1.2.6

1. Дождаться доступной capacity (≥48 GB, ≤$1.79/ч, один Pod, ≤600 с).
2. Прогнать `runner.py --live --confirm-paid --scenario T3L` и затем `T4L` в том же бюджете.
3. При PASS — собрать и подписать production installer, установить, проверить данные/device,
   создать тег `v1.2.6`, Release и атомарно обновить updater manifest.

Ничего из этого не объявлено выполненным авансом.
