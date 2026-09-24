# CANALLA LLM 1.2.0 — ОТЧЁТ ПО ШАГАМ 4 И 5

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · Версия продукта: **1.1.0** (не поднята)

**Итог в одну строку:** шаг 4 выполнен наполовину — главный UX-дефект закрыт полностью (глобальный
статус = готовность AI), Linux-половина остановлена как **BLOCKED** с измеренными причинами; шаг 5
**не запускался**, потому что его предусловие (шаг 4 = PASS) не выполнено → **RELEASE BLOCKED**.
Ничего не выпущено: ни bump, ни merge, ни tag, ни манифест, ни production-ключ.

Это сводка для чтения. Подробные англоязычные версии лежат рядом и содержат те же факты:
`docs/report-2026-09-24-step4-ubuntu.md` и `docs/report-2026-09-24-step5-release-blocked.md`.

---

## 1. Состояние репозитория

| | |
|---|---|
| HEAD | `06c7c53` (`release/canalla-1.1.0`) |
| `main` / `origin/main` | `9bd7548` — совпадают, не тронуты |
| Теги | только `v1.0.0` → **`v1.2.0` свободен** |
| Рабочее дерево | чистое, кроме известного drift `docs/screenshots/0.4/*.png` (не коммитится) |
| Diff против `main` | 212 файлов, +19 587 / −1 305 строк (вся работа шагов 1–4) |

**Коммиты шага 4:**

| Коммит | Что |
|---|---|
| `51fe9a3` | `fix(ui): make global connection reflect AI readiness` |
| `854e8c2` | `test(status): prove a missing configuration starts no polling` |
| `65094d6` | `test(linux): stop asserting a Windows layout on Linux` |
| `73f9952` | `fix(tor): let the shipped daemon find its own libraries` |
| `0b9ec3f` | `test(linux): certify the shipped runtime on Linux` |
| `3a72df5` | `docs: record the Ubuntu baseline and the measured gap` |
| `43fd06a` | `docs: record the STEP 4 result` |

**Коммит шага 5:** `06c7c53` — `docs: record the release stage as blocked`

---

## 2. ШАГ 4 — что сделано

### 2.1 Глобальный статус подключения AI — главный результат, выполнен полностью

**Дефект был реальным и точно там, где описан:** `Workspace.tsx` рендерил `Connected` по живости
локального backend (`health ? "Connected" : "Offline"`). То есть шапка могла показывать `Connected`,
пока чип рядом говорил «AI Не настроено», а строка баланса — «RunPod не настроен».

**Что сделано.** Новый модуль `apps/desktop/src/lib/ai-connection.ts` — единственный владелец этого
слова. Он читает **собственное** состояние backend'а (`compact_ai`, `compute_state`, `configured`),
не изобретая второй словарь, и решает, сколько из этого можно показать зелёным:

| Случай | Поведение | Итог |
|---|---|---|
| AI не настроен | Disconnected (red) + CTA «Настроить AI» | PASS |
| Нет RunPod API key | Disconnected (red), «добавьте API key» | PASS |
| Ключ есть, Pod нет | Disconnected («AI выключен: GPU не запущен») | PASS |
| Provisioning: `searching`, `creating`, `starting_pod`, `mounting_storage` | Connecting (amber), **каждая стадия названа** | PASS |
| Pod работает, модель грузится (`loading_model`) | Connecting | PASS |
| Модель ответила (`ready`, `generating`) | Connected (green) | PASS |
| Модель перестала отвечать | Disconnected | PASS |
| Pod остановлен / terminated / stopping | Disconnected | PASS |
| Ошибка провайдера | Disconnected, сохраняется recovery backend'а | PASS |
| Устаревший snapshot (чтение не удалось) | **не Connected**, даже если до этого был зелёным | PASS |
| Восстановление | red → amber → green → red (тест-переход) | PASS |
| **Ложный Connected** | — | **НЕТ** |
| **Бесконечное «Проверяем…» без конфигурации** | — | **НЕТ** |

Дополнительные гарантии, тоже под тестами:

* **Amber — только реальный переход.** `degraded` становится amber, лишь если `compute_state` входит
  в `STARTING` самого backend'а; `create_unknown` и `external_compute` — красные, а не обещание.
* **Configured ≠ healthy.** Состояние `configured` рендерится как Disconnected («Настроено, но
  готовность не подтверждена»).
* **Инфраструктура сохранена и переименована.** В поповере бейджа отдельные строки:
  `Backend: Готов / Не отвечает` и `Canalla Cloud: Подключено / Недоступно / …`. Слова
  Connected/Offline больше нигде не являются заголовком. Один источник (та же cloud-стора, что у
  панелей) — второго поллера нет.
* **Статус по-прежнему read-only.** Там, где состояние может изменить только compute, поповер
  объясняет, где действовать, и **кнопки не даёт**.

**Доказательства:** `apps/desktop/src/lib/ai-connection.test.tsx` — **28 новых тестов** (вся матрица
выше + пять кейсов независимости чипов по §6); переписанный Playwright-кейс
`app.spec.ts` — при оборванных `/health` **и** `/status` бейдж не имеет права сохранить зелёный
(`data-code="snapshot_stale"`), строка `Backend` уходит в «Не отвечает», после восстановления — снова
«Готов», а состояние снова принадлежит AI.

**Отдельно про §3 («не запускать бессмысленные циклы»):** backend и раньше не опрашивал RunPod без
ключа, но это **не было зафиксировано тестом**. Теперь зафиксировано: без ключа `start()` не создаёт
задачу и upstream не вызывается вообще (0 запросов), а настроенный баланс действительно запускает и
останавливает свой цикл.

### 2.2 Ubuntu — определённый baseline (измерено, не угадано)

| Параметр | Значение | Чем измерено |
|---|---|---|
| Проверенный дистрибутив | **Ubuntu 24.04.4 LTS (noble), x86_64** | `lsb_release -a` |
| WebKitGTK (жёсткая зависимость Tauri v2) | **webkit2gtk-4.1 = 2.52.6**, `javascriptcoregtk-4.1 = 2.52.6` | `pkg-config --modversion` после установки `libwebkit2gtk-4.1-dev` |
| glibc | **2.39** | отчёт `tor --version` |
| Rust, компилирующий дерево | **1.98.1** | `cargo --version` |
| Node для сборки фронтенда | **22.23.2** (vite 7.3.6 требует ≥ 20.19) | `node --version`, lock-файл |
| Secret storage | Secret Service (gnome-keyring/KWallet) — **отсутствует** | `which gnome-keyring-daemon secret-tool` |

**Важное следствие:** `.deb`, собранный на noble, линкует glibc 2.39 и **не пойдёт на 22.04** (2.35).
Выбор baseline — собрать на jammy ради 22.04 либо объявить 24.04 нижней границей — остаётся решением
для следующей попытки; сборка не делалась ни для одного из вариантов.

### 2.3 Ubuntu — что реально доказано (не словами)

Runtime-половина продукта не требует desktop-сессии, поэтому её удалось собрать и сертифицировать:

| Проверка | Результат |
|---|---|
| Pinned Tor runtime из официального expert bundle | **PASS** — `fetch-tor-runtime.py --platform linux-x86_64`, SHA256 сверен, 10 файлов |
| **Упакованный** backend (PyInstaller onedir, собран на Linux) | **PASS** — отвечает `/health` (`product: alex-llm`), будучи запущен с `PATH`, указывающим в **пустой каталог** |
| Bundled Tor: bootstrap + реальная цепь | **PASS** — `verified: true`, `method: socks5h`, `/proc/<pid>/exe == runtime/tor/tor`, версия 0.4.9.12 (pin 0.4.9.12) |
| Kill демона → восстановление | **PASS** — новый pid со свежим proof (`1622 → 1755`), backend продолжает обслуживать |
| Остановка backend → ни одного orphan | **PASS** — ни одного процесса `tor` |
| **`scripts/acceptance-linux-runtime.sh`** | **9/9 PASS** |

То, что доказано, закрывает §11 (sidecar), §13 (bundled Tor с его библиотеками) и §14 (acceptance
Tor: запуск → SOCKS → цепь → proof → ready, kill → recovery, quit → no orphan).

### 2.4 Дефекты, найденные и исправленные (найдены прогоном, а не чтением)

1. **Демон Tor не находил свои библиотеки на Linux.** Windows-загрузчик ищет библиотеки рядом с
   исполняемым файлом, POSIX — нет. Демон умирал на `exec` с **exit 127** до старта Tor, а супервизор
   мог сообщить только `tor_managed_exited code=127` и уходил в backoff — что корректно и делал.
   **Исправлено:** `LD_LIBRARY_PATH` указывает на каталог рантайма **только на POSIX** (Windows
   получает прежнее окружение); плюс каталог рантайма резолвится в абсолютный путь один раз, при
   обнаружении. Тест проверяет обе платформы:
   `test_the_managed_daemon_can_find_the_libraries_beside_it`.
2. **Три теста требовали Windows-раскладку на Linux** — путь данных (продукт был прав: XDG), список
   `libraries` в пине Tor (неполное обещание, не дефект продукта) и триггер `chmod 0444`, который root
   игнорирует. Все три теперь под `sys.platform`/`geteuid`; ожидания для Windows не ослаблены.
3. **UX-дефект глобального статуса** (см. 2.1) — тоже найден и закрыт.

### 2.5 Измеренный разрыв: почему Ubuntu = BLOCKED

`cargo check` на Ubuntu 24.04 (все зависимости собрались; падает только наш крейт):

| Файл | Ошибок |
|---|---|
| `src/host.rs` | 19 |
| `src/process.rs` | 13 |
| `src/credential.rs` | 12 |
| `src/backend.rs` | 12 |
| `src/autostart.rs` | 3 |
| бинарники `alex-llm` / `alex-host-loop` | **59 / 44** (`81 × cannot find`, `13 × no method`, `9 × unresolved import`) |

Это не логические ошибки, а **отсутствующие платформенные реализации**: Win32 job object (владение и
убийство sidecar вместе с desktop), флаги создания процесса, `std::os::windows::ffi`, Credential
Manager, значение реестра `Run`. Python host tool loop тоже Windows-only — это ровно все **16**
падений Linux-прогона. **Ubuntu не «не упакован», а не портирован.**

Плюс независимо от кода: **desktop-окружения на этой машине нет.** Единственный Linux — headless
WSL2 (нет session manager, keyring, XDG-сессии, logon), Docker-движок не запущен, Hyper-V требует
elevation, VM-хоста нет. §19 брифа запрещает считать WSL desktop-приёмкой, поэтому приёмка
§19–§22 (установка, GUI, session/reboot, autostart, keyring) **не выполнялась**, и `.deb` никуда не
устанавливался.

---

## 3. ШАГ 5 — что сделано

### 3.1 Выполнена обязательная верификация (§0)

| Проверка | Факт |
|---|---|
| HEAD / ветка | `43fd06a` на `release/canalla-1.1.0` |
| `main` == `origin/main` | `9bd7548` == `9bd7548` |
| Теги | только `v1.0.0` → `v1.2.0` свободен |
| STEP 1 Auto Update | **есть** (`5648751`, `005f13f`, `16633af`) |
| STEP 2 bundled Tor | **есть** (`a2a8b5e`, `4dab4f3`, `68294a7`, `829996c`) |
| STEP 3 Windows self-contained / autostart / single instance | **есть** (`ac8164a`, `973c3bb`, `57a66e7`) |
| STEP 4 **Ubuntu package / secret-store / autostart** | **ОТСУТСТВУЕТ** |
| backend crash supervisor / Computer always-ready / global AI fix / compact UI / D-9 / context meter / backup-restore | **есть** (перечислены в отчёте по шагам 4–5, §0) |
| Linux-артефакты | `find . -name "*.deb" -o -name "*.AppImage"` → **пусто** |
| Единственный installer | промежуточный `Canalla LLM_1.1.0_x64-setup.exe`, 92 454 341 байт, SHA256 `b3761c0a…5689b` + `.sig` |
| Production updater-ключ | **нет** — в `~/.canalla-updater/` только `canalla-updater-test.key` и `.pub` |
| Gateway в проде | `/health` → **200**, `GET /updates/latest` → **404** (развёрнут Gateway 1.1.0, маршрута нет) |

### 3.2 Вердикт: RELEASE BLOCKED — две независимые причины

1. **Ubuntu-половины не существует** (см. 2.5): нет `.deb`, desktop-runtime не компилируется под
   Linux (59 + 44 ошибки), host tool loop не работает, secret store и XDG-autostart не реализованы.
2. **Обязательные стенды §12/§13/§15 недоступны:** pristine Windows VM, реальный logout/login и
   чистая Ubuntu desktop VM. На этой машине их взять негде, а PASS без них был бы выдуман
   (правило 13 — «No fake PASS»).

### 3.3 Что НЕ делалось — и почему

| Пункт шага 5 | Почему не делался |
|---|---|
| §5 bump 1.1.0 → 1.2.0 | release-акт; предусловие шага не выполнено |
| §6–7 production updater-ключ и non-interactive подпись | нужен пароль, которым владеет оператор; плюс нет артефактов для подписи |
| §8 деплой маршрута `/updates/latest` | нужен доступ к VPS оператора |
| §9 хостинг артефактов | артефактов 1.2.0 не существует |
| §11 финальные сборки Windows/Linux | Linux не собирается (см. 2.5) |
| §12–15 pristine VM, login, upgrade, чистая Ubuntu | стендов нет физически |
| §17–18 updater E2E, `requireSignedVersion` | нет ни ключа, ни маршрута, ни артефактов |
| §19 Authenticode | сертификата нет; подделывать нечем и не будем |
| §20–22 хеши, манифест, release notes | манифест публикуется ПОСЛЕДНИМ, после тега и проверенных артефактов |
| §26 merge → main, tag `v1.2.0` | только после PASS всех гейтов |

Единственное, что сделано по шагу 5 — верификация состояния и зафиксированный вердикт
(коммит `06c7c53`).

---

## 4. Гейты (последнее полное измерение, HEAD `43fd06a`)

| Гейт | Результат |
|---|---|
| BasedPyright | **0 errors, 0 warnings** |
| Backend pytest (Windows) | **630 passed, 1 skipped** (+3 новых теста) |
| Backend ruff check / format | PASS / 181 файл |
| Backend pytest (Ubuntu 24.04) | 606 passed, **16 failed**, 8 skipped — все 16 = непореченный host tool loop |
| Gateway pytest | **148 passed** |
| Frontend Vitest | **230 passed** (19 файлов; +28 новых) |
| Frontend tsc / Prettier / Vite | clean / clean / PASS |
| Playwright | **20 passed** (включая переписанный offline-recovery) |
| Rust `cargo test` | **88 passed** (host 18, desktop 58, updater signature 12) |
| npm audit --omit=dev | **0 уязвимостей** |
| Tor suite | **83 теста** |
| Linux runtime acceptance | **9/9 PASS** |
| Linux packaging / secret-store / XDG-autostart тесты | **не написаны** — самих фич нет |

---

## 5. Файлы

**Создано:**

| Файл | Строк |
|---|---|
| `apps/desktop/src/lib/ai-connection.ts` | 262 |
| `apps/desktop/src/lib/ai-connection.test.tsx` | 497 (28 тестов) |
| `apps/desktop/src/components/AiConnectionBadge.tsx` | 142 |
| `scripts/acceptance-linux-runtime.sh` | 175 |
| `docs/linux-ubuntu.md` | 157 |
| `docs/report-2026-09-24-step4-ubuntu.md` | — |
| `docs/report-2026-09-24-step5-release-blocked.md` | 241 |

**Изменено:** `Workspace.tsx`, `styles.css`, `e2e/app.spec.ts`, `e2e/status.spec.ts`,
`e2e/native-smoke.mjs`, `e2e/native-offline.mjs`, `app/tools/tor/service.py`,
`tests/test_tor_service.py`, `tests/test_status_recovery.py`, `tests/test_runtime_paths.py`,
`tests/test_tor_bundle.py`, `tests/test_upgrade.py`.

---

## 6. Открытые пункты (§4 брифа) — классифицированы, ни один не забыт молча

| Пункт | Статус |
|---|---|
| WM-07 TinyFish Browser live lifecycle | **DOCUMENTED NON-BLOCKING LIMITATION** |
| CD-08 REAL stale-SHA coverage | **DOCUMENTED NON-BLOCKING LIMITATION** |
| `client_version` metadata на Gateway | **DOCUMENTED NON-BLOCKING LIMITATION** — только метаданные, никогда не источник полномочий |
| sticky-метка `generating` | **DOCUMENTED NON-BLOCKING LIMITATION** — косметика, снимается на следующем изменении состояния |
| over-window draft не обрезается | **DOCUMENTED NON-BLOCKING LIMITATION** — upstream отказывает, клиент видит типизированный `gateway_unavailable` |
| лаг телеметрии spend у провайдера | **DOCUMENTED NON-BLOCKING LIMITATION** — поведение внешнего провайдера |
| Web-чип `configured` vs реальный health | **DOCUMENTED NON-BLOCKING LIMITATION** — дешёвой честной пробы нет; чип намеренно не заявляет «Готово» |
| `requireSignedVersion` (связывание артефакта и заявленной версии) | **OPEN DECISION** — minisign подписывает байты артефакта; версия — метаданные манифеста. Решить в релизном этапе: доказать связывание тестом либо задокументировать митигацию |
| политика Authenticode | **OPEN DECISION** — сейчас NOT SIGNED, SmartScreen может предупреждать. Заявить явно, не фабриковать |

---

## 7. Cleanup, GPU, секреты

| Пункт | Результат |
|---|---|
| Орфаны процессов | **нет** (Windows и WSL проверено) |
| Временные материалы сборки | **удалены** — копия в WSL снесена (диск 6.6 → 4.0 GB); инструментарий в WSL оставлен (описан) |
| GPU / Pods | **0** созданных Pod, **$0.00** расхода, максимальное ожидание capacity — 0 секунд |
| Секреты | **не утекли**; созданных production-ключей нет, в репо ничего ключеподобного не отслеживается |
| Версия / тег / манифест | не тронуты: 1.1.0, `v1.0.0`, манифест не опубликован |

---

## 8. Порядок выхода из блокировки

1. **Портировать Linux desktop**: `process.rs` (группа процессов + parent-death signal вместо job
   object), `host.rs`, `credential.rs` (Secret Service), `backend.rs`, `autostart.rs` (XDG) и Python
   host tool loop; добавить `tauri.linux.conf.json` (deb-таргет, PNG-иконки, платформенные имена
   ресурсов, без Windows-батника) и выбрать baseline (jammy ради 22.04 либо 24.04 как floor).
2. **Получить стенды**: одну чистую Windows VM и одну чистую Ubuntu desktop VM — гейты установки и
   login-цикла иначе не пройти.
3. **Задеплоить маршрут `/updates/latest`** (204/no-update вместо 404) и подготовить HTTPS-хостинг
   артефактов.
4. **Создать production updater-идентичность** (пароль, вне репо, подпись без интерактивных
   промптов) и перепроверить crypto-тесты на новом публичном ключе.
5. **Решить и задокументировать** `requireSignedVersion` и Authenticode.
6. **Затем** порядок §23 целиком: bump → гейт → сборки → подписи → хеши → pristine-установки →
   login-циклы → upgrade → Tor E2E → natural-language Tor routing → updater E2E → выгрузка →
   проверка хешей → docs → merge → tag `v1.2.0` → **манифест последним**.

Пункты 1–2 — основной объём работы; пункты 3–5 требуют вашего доступа (VPS, пароль,
решение по сертификату), а не инженерии.

---

## 9. Отчёты на рабочем столе

| Файл | О чём |
|---|---|
| `CANALLA-LLM-1.2.0-ОТЧЁТ-ШАГИ-4-И-5.md` | этот сводный отчёт |
| `CANALLA-LLM-1.2.0-STEP4-UBUNTU-ОТЧЁТ.md` | шаг 4 подробно |
| `CANALLA-LLM-1.2.0-FINAL-RELEASE-ОТЧЁТ-BLOCKED.md` | шаг 5 подробно |
| `CANALLA-LLM-1.2.0-STEP1-AUTO-UPDATE-ОТЧЁТ.md` | шаг 1 |
| `CANALLA-LLM-1.2.0-STEP2-BUNDLED-TOR-ОТЧЁТ.md` | шаг 2 |
| `CANALLA-LLM-1.2.0-STEP3-WINDOWS-SELF-CONTAINED-ОТЧЁТ.md` | шаг 3 |
| `CANALLA-LLM-1.2.0-ОТЧЁТ-ПО-3-ПРОМПТАМ.md` | сводка по ранним промптам |
