# Canalla LLM 1.2.7 — завершение автономного агента

**Задание:** закрыть четыре оставшихся направления 1.2.7 — нативный Windows GUI, активная безопасность, сквозное восстановление, интеграция — без платных ресурсов и без публикации.
**Исходный Git SHA:** `897b0d074cc9abdb42c90514e96f5da75d3230f1` (ветка `feature/canalla-1.2.7-autonomous-agent`).
**Публичная версия:** 1.2.6 — не изменялась.

---

## 1. Что сделано

### 1.1 Нативный Windows GUI (UI Automation)

| Компонент | Файл |
|---|---|
| UIA-мост на стороне пользователя (PowerShell + .NET UIAutomation) | `apps/desktop/src-tauri/src/uia.ps1` |
| Операция хоста `window_inspect` / `window_act` (аргументы только через JSON-файл) | `apps/desktop/src-tauri/src/host.rs` |
| Инструменты модели `window_inspect` / `window_act` | `apps/backend/app/tools/local/provider.py` |
| Политика: разрешено в `trusted`, подтверждение в `ask` | `apps/backend/app/tools/policy.py` |

Возможности: список окон, ожидание окна, поиск элемента по `Name` / `AutomationId` / `ControlType` / классу, фокус, `InvokePattern`, `ValuePattern`, `SelectionItemPattern`, `ExpandCollapsePattern`, `ScrollPattern`, `WindowPattern.Close`, чтение текста, скриншот окна, `send_keys` (только при `allow_input`).

Ключевые инженерные решения, найденные на реальном приложении:

1. **WinForms/Win32 отдают `ControlType.Pane` без паттернов.** Добавлен поиск по классу (`WindowsForms10.EDIT…`, `Button`) и Win32-эквиваленты отсутствующих паттернов: `WM_SETTEXT`, `BM_CLICK`, `WM_CLOSE` — только после того, как UIA уже нашла конкретный контрол.
2. **`LegacyIAccessiblePattern` отсутствует в этой сборке UIAutomationClient** — обращение обёрнуто и заменено на `BM_CLICK`.
3. **Закрытое окно остаётся в дереве UIA** — окно считается окном, только если его процесс жив (`Get-Process`), иначе `close_window` «видел» фантом.
4. **PowerShell 5.1 писал stdout в кодовой странице консоли** — русские заголовки ломались; кодировка принудительно UTF-8.
5. **`SendMessageW` требует явного `EntryPoint`** — иначе `EntryPointNotFoundException`.
6. **Координатный клик не используется как основной путь** — только фолбэк при `allow_input=true`.

### 1.2 Активная безопасность и жизненный цикл findings

| Компонент | Файл |
|---|---|
| Сканер: заголовки, отражение, `/debug`, порты, TLS | `apps/backend/app/agent/security.py` |
| Инструмент `security_active` | `apps/backend/app/tools/agent_tools.py` |
| Гейт завершения: открытая находка блокирует COMPLETED | `apps/backend/app/tools/local/task.py` |

- Scope обязателен: цель, не перечисленная в `allowed_targets`, отклоняется **до** открытия сокета (`scope_denied`), без авторизации — `scope_not_authorized`.
- `POTENTIAL → CONFIRMED → FIXED`, плюс `FALSE_POSITIVE` и `REOPENED`. `CONFIRMED` ставится только пробой, которая воспроизвела проблему и сохранила evidence; `FIXED` — только повторной проверкой, которая её больше не воспроизводит; регрессия возвращает `REOPENED`.
- Nmap/ZAP не подменяются заглушками: адаптеры сообщают `available: false`, если бинарника нет.
- `final_review` не даёт завершить задачу с открытой подтверждённой находкой.

### 1.3 Сквозное восстановление

Реальный обрыв процесса: дочерний процесс поднимает настоящий backend, выполняет побочный эффект и вызывает `os._exit(17)` посреди задачи. Второй процесс стартует приложение заново, lifespan помечает брошенную задачу `INTERRUPTED`, и она продолжается с тем же `task_id`.

Найден и исправлен реальный дефект: **задача могла завершиться, не выполнив шаг проверки**. Контроллер помечал шаг `verify` выполненным, а `verification_stale` оставался `true`. Теперь:

- `plan_has_verification` фиксируется при сохранении плана;
- `_goal_satisfied` не считает цель достигнутой, пока `verification_stale` и в плане есть шаг проверки;
- `final_review` добавляет `verification_required` в этом же случае;
- `verification_stale` снимается **только** реальной проверкой: совпадающим digest при `read_file` после записи, прохождением тестов или успешным `verify_result`.

---

## 2. Приёмки и доказательства

### 2.1 Windows UIA — WINDOWS LIVE PASS (адаптер)

`apps/backend/tests/test_127_windows_uia.py` — на настоящем скомпилированном WinForms-приложении (`tests/fixtures/uia_acceptance_app.cs`):

| Шаг | Результат |
|---|---|
| Открыть приложение | `run_process`, окно найдено по заголовку |
| Ввести текст | `set_value` → `WM_SETTEXT`, значение прочитано обратно |
| Нажать Save | `invoke` → `BM_CLICK` |
| Сохранить файл | файл содержит `UIA_ACCEPTANCE_417` |
| Скриншот | `01-saved.png` (32 КБ) |
| Закрыть | `close_window`, окно исчезло |
| Открыть снова | второй процесс поднял документ и записал `.loaded` |
| Проверить содержимое | `.loaded` = `UIA_ACCEPTANCE_417`, `get_text` вернул то же |
| Скриншот | `02-reopened.png` (32 КБ) |

Артефакты: `artifacts/acceptance-127/gui-uia/`.

**Статус:** сам UIA-адаптер проверен на живой Windows-программе. Проводка через **упакованный** Rust-хост подтверждена `cargo check` и интеграцией с fake-host; сквозной прогон установленного `alex-host-loop.exe` в этой сессии не выполнялся.

### 2.2 Активная безопасность — FREE INTEGRATION PASS

`apps/backend/tests/test_127_security_active.py` + `artifacts/acceptance-127/capture.py`:

| | |
|---|---|
| До исправления | **6 CONFIRMED** находок: 4 отсутствующих заголовка, отражение параметра (`CWE-79`), открытый `/debug` |
| После исправления | **0 открытых, 6 FIXED** |
| Вне scope | `192.0.2.10` → `scope_denied`, запросов не было |
| Без авторизации | `scope_not_authorized` |
| Lifecycle | CONFIRMED → FIXED только после retest; повторная регрессия → REOPENED |

Артефакты: `artifacts/acceptance-127/security/before.json`, `after.json`.

### 2.3 Восстановление — FREE INTEGRATION PASS

`apps/backend/tests/test_127_recovery_e2e.py` + `artifacts/acceptance-127/recovery/summary.json`:

```
interrupt_exit_code = 17      (реальный os._exit посреди задачи)
resume_exit_code    = 0
task_id             = один и тот же
status_after_resume = COMPLETED
content             = "one\ntwo\n"
files               = ["steps.txt"]        (дублей нет)
events              = … RESUMED … COMPLETED
```

---

## 3. Тесты

| Проверка | Результат |
|---|---|
| Полный backend suite | **1106 passed, 1 skipped**; единственный FAIL — устаревший sidecar stamp, после пересборки **11/11 PASS** |
| Новые приёмочные тесты 1.2.7 | **20 passed** (agent core, integrated build, UIA, security, recovery) |
| Целевые регрессии (build/autonomous/coding/transition/security) | **95 passed** |
| `ruff check` / `ruff format --check` (весь backend + alembic) | чисто, 235 файлов отформатированы |
| `cargo check --bin alex-host-loop` | OK |
| Sidecar | пересобран, `b36ddcbe4d2d…`, stamp-test 11/11 |

---

## 4. Матрица готовности

| Направление | Статус | Доказательство |
|---|---|---|
| Web Research | CODE READY / FREE PASS | `agent/research.py`, реальный HTTP |
| App Build | CODE READY / FREE PASS | TaskDesk: fail → fix → run → HTTP → COMPLETED |
| GUI (браузер) | CODE READY / FREE PASS | 5 скриншотов, 4 проверенных действия |
| **GUI (Windows native)** | **CODE READY / WINDOWS LIVE PASS (адаптер)** | WinForms: ввод → сохранение → закрытие → повторное открытие → точное содержимое |
| Auto Fix | CODE READY / FREE PASS | воспроизведение дефекта + повторный прогон |
| Durable Recovery | CODE READY / **FREE INTEGRATION PASS** | реальный `exit 17` → второй процесс → тот же task id |
| **Security (active)** | CODE READY / **FREE INTEGRATION PASS** | 6 findings → fix → 0 open / 6 fixed |
| Integration | CODE READY / FREE PASS | один цикл: research → build → run → GUI → fix → verify |
| **QWEN LIVE** | **NOT VERIFIED** | платных прогонов не было |

---

## 5. Ограничения

1. **Настоящая Qwen не запускалась** — бесплатный прогон, `$0`, Pod не создавался.
2. **Упакованный native host** не прогонялся сквозь установленное приложение; проводка проверена `cargo check` + fake-host. Это следующий шаг перед LIVE.
3. **UIA на «чужих» приложениях**: проверено на собственном WinForms-стенде (реальная сторонняя программа, но контролируемая). Notepad не использовался — он нестабилен по локали и версии; UIA-путь для него тот же.
4. **Nmap/ZAP** — только детект доступности; активные адаптеры не реализованы. Встроенные проверки (порт, HTTP, TLS) работают без внешних бинарей.
5. **Активная безопасность** ограничена HTTP-поверхностью; инъекции/аутентификация вне scope этого инкремента.
6. **Windows Authenticode** установщика отсутствует — отдельное релизное ограничение.

---

## 6. Безопасность и production

- `workspace isolation`, `sensitive_arguments`, `TOR_ONLY`, prompt-injection quarantine, независимый verifier — сохранены.
- Активные проверки возможны только по явно перечисленным целям; по умолчанию тесты используют loopback.
- Пользовательские данные: `integrity ok`, 2 / 54 / 120 / 59, `device_id` `f3548022-…-9fc5-…` без изменений.
- `pods []`, баланс не расходовался, платных ресурсов не создавалось.
- Опубликованный `v1.2.6`, updater, manifest, ассеты и ключ подписи **не изменялись**; 1.2.7 не публиковалась.

---

## 7. Следующий шаг

1. Прогнать UIA через **упакованный** `alex-host-loop.exe` (сборка + установленное приложение) — это единственный незакрытый Windows-гейт.
2. Подготовить минимальный LIVE Qwen-сценарий: один Pod, ≤600 с, бюджет ≤ $0.15, остановка по watchdog, критерии PASS — один цикл research → build → GUI → fix → verify.
3. Только после отдельного разрешения — кандидат 1.2.7 и публикация.
