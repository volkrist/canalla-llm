# CANALLA LLM — STEP 4A: ПЕРЕНОС DESKTOP НА UBUNTU 24.04 LTS

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · Версия продукта: **1.1.0** (не поднята)

**Итог в одну строку:** инженерный порт Linux выполнен — `cargo check` и `cargo test` зелёные на
Ubuntu 24.04, backend и Tor работают из установленного `.deb`, глобальный статус AI не тронут, — но
**приёмка на чистой Ubuntu-десктоп-VM не выполнена**, потому что такой среды на этой машине нет.

```
LINUX ENGINEERING PORT   = PASS
DESKTOP VM ACCEPTANCE    = PENDING (STEP 4B)
```

Это не ложный PASS и не BLOCKED: код готов и измерен, отсутствует ровно та среда, в которой нужно
проверять GUI, keyring, XDG-сессию и настоящий logout/login. Ничего из этого здесь не заявляется.

---

## 1. Состояние репозитория

| | |
|---|---|
| HEAD до | `272ac00` |
| HEAD после | `3f8498b` |
| `main` / `origin/main` | `9bd7548` — не тронуты |
| Теги | только `v1.0.0` → `v1.2.0` свободен |
| Рабочее дерево | чистое, кроме известного drift `docs/screenshots/0.4/*.png` (не коммитится) |
| Bump версии | **не делался** |

**Коммиты:**

| Коммит | Что |
|---|---|
| `d49fdb6` | `refactor(desktop): separate platform process lifecycle` |
| `4be6a64` | `fix(linux): let the host tools resolve POSIX paths` |
| `64b61f6` | `fix(updater): sign the fixture bytes that are committed` |
| `69fc7ab` | `build: pin checkout line endings for scripts and fixtures` |
| `6d97ef0` | `build(linux): package Canalla as a deb` |
| `939d9af` | `test(linux): guard the Linux bundle from a Windows machine` |
| `3f8498b` | `docs: record the Ubuntu port` |

Ничего из STEP 1–4 не потеряно: Auto Update, bundled Tor, Windows self-contained, Windows autostart,
single-instance, Computer always-ready, backend supervisor, глобальный AI-статус, компактный UI,
Settings, D-9, context meter, backup/restore — всё на месте и перепроверено (§9).

---

## 2. BASELINE

| | |
|---|---|
| Поддерживаемая ОС | **Ubuntu 24.04 LTS x86_64** |
| Ubuntu 22.04 | сознательно **не поддерживается** в 1.2.0 (glibc 2.35 против 2.39, WebKitGTK 4.1) |
| Измеренная среда | Ubuntu 24.04.4 LTS (noble) x86_64, WSL2 |
| Rust | 1.98.1 · Node 22.23.2 · glibc 2.39 · webkit2gtk-4.1 = 2.52.6 |
| Bundled Tor | 0.4.9.12 (linux-x86_64 expert bundle) |

Записано в `docs/linux-ubuntu.md` вместе со всеми числами этого отчёта.

---

## 3. COMPILE

| Гейт | Windows | Ubuntu 24.04 |
|---|---|---|
| `cargo check --all-targets` | **PASS**, 0 ошибок | **PASS**, **0 ошибок** (было 59 + 44) |
| `cargo test` | **91 passed** (21 + 58 + 12) | **99 passed** (25 + 62 + 12) |

Что было перенесено и как:

* Один контракт — `apps/desktop/src-tauri/src/platform/{mod,windows,linux}.rs`. Выбор платформы
  происходит **один раз** внутри модуля; в остальном дереве нет `target_os`-веток для этих тем.
* `windows.rs` — **дословно перемещённый** Win32-код. Ни одно поведение Windows не изменено ради
  того, чтобы Linux скомпилировался.
* `linux.rs` — те же имена функций с POSIX-реализацией.

| Механизм | Windows | Linux |
|---|---|---|
| Владение потомком | Job Object (`kill-on-close`) | process group (`process_group(0)`) + `PR_SET_PDEATHSIG` в `pre_exec` |
| «Живой?» | `OpenProcess` + exit code | `/proc/<pid>/stat`, читается **состояние** (зомби = мёртв) |
| Остановка дерева | `taskkill /T /F` | `SIGTERM` группе → `SIGKILL` через ~3 с |
| Секреты | Credential Manager (+ DPAPI-fallback) | Secret Service (`secret-tool`) |
| Autostart | `HKCU\…\Run` | `$XDG_CONFIG_HOME/autostart/canalla-llm.desktop` |
| Данные | `%LOCALAPPDATA%\Alex LLM` | `$XDG_DATA_HOME/alex-llm` |

---

## 4. PROCESS SUPERVISION

| Требование | Результат | Чем доказано |
|---|---|---|
| spawn backend | **PASS** | `backend::tests::watchdog_restarts_a_crashed_owned_sidecar` (Linux) |
| recovery после падения | **PASS** | тот же тест: watchdog поднимает новый процесс |
| clean shutdown | **PASS** | 9/9 Linux runtime acceptance: «stopping the backend leaves no daemon behind» |
| orphan protection | **PASS** | три доказательства на уровне ядра, ниже |

POSIX не имеет kill-on-close объекта, поэтому гарантия собрана из двух механизмов, которые есть, и
**ни один не заявлен без доказательства**:

| Тест | От чего защищает |
|---|---|
| `an_owned_child_is_its_own_process_group` — читает поле `pgrp` из `/proc/<pid>/stat` | потомок, оставшийся в группе самого desktop: тогда `terminate_tree` сигналил бы desktop |
| `stopping_an_owned_child_takes_the_processes_it_started` — shell запускает фоновый процесс, затем останавливается группа | осиротевший внук, то есть Tor-демон, переживший запустивший его sidecar |
| `an_armed_child_does_not_outlive_the_parent_that_died` — тест **перезапускает собственный бинарь** как родителя, который вооружает потомка и умирает, не убрав за собой | `PR_SET_PDEATHSIG`, который не был вооружён или вооружён не на том процессе. Единственный честный способ проверить parent-death signal — дать настоящему родителю умереть |

Чужие процессы не убиваются: сигнал адресуется только собственной группе процесса.

---

## 5. COMPUTER

| | |
|---|---|
| Linux-реализация | **YES** — архитектура та же, что подтверждена для Windows в STEP 3: production host живёт **in-process внутри desktop**, `alex-host-loop` остаётся headless/E2E-вариантом |
| Автоматический Ready | **NOT GUI TESTED** |
| Pairing | **NOT GUI TESTED** (код и тесты есть, GUI нет) |
| Heartbeat | **NOT GUI TESTED** |
| Same device | **NOT GUI TESTED** |

Тесты, которые на Linux проходят и покрывают credential/device-путь:
`gateway::tests::enrollment_is_stored_securely_and_never_returned`,
`enrollment_environment_makes_the_backend_shared`,
`packaged_production_is_shared_even_without_enrollment`,
`disconnect_removes_the_credential_even_when_the_gateway_is_unreachable`.

Ни один из них не заменяет клик-free Ready в живом окне — это STEP 4B.

---

## 6. SECRET STORAGE

| | |
|---|---|
| Реализация | **Secret Service** (GNOME Keyring или совместимый провайдер) через `secret-tool`; атрибуты `service=canalla-llm`, `target=<target>` |
| Plaintext fallback | **NO** |
| Типизированный отказ | `secure_storage_unavailable` (`platform::SECURE_STORAGE_UNAVAILABLE`) при отсутствии шины, заблокированной коллекции или отсутствующем `secret-tool` |
| Тесты | **PASS** |

`secret-tool store` возвращает **0 даже когда не сработал** («Cannot create an item in a locked
collection»), поэтому отображение идёт по его stderr: заблокированное/недоступное хранилище →
типизированный отказ, любая другая ошибка несёт свой текст.

`no_store_means_a_typed_refusal_and_no_file` проверяет контракт в обе стороны: либо round-trip
работает, либо возвращается отказ **и на диск не попадает ничего**.

---

## 7. AUTOSTART

| | |
|---|---|
| Механизм | **XDG user-session**: `$XDG_CONFIG_HOME/autostart/canalla-llm.desktop` (fallback `~/.config/autostart`) |
| Содержимое | `Type=Application`, `Name=Canalla LLM`, `Exec="<путь>"`, `X-GNOME-Autostart-enabled=true` |
| Никакого root-сервиса / system-wide демона | подтверждено |
| Toggle ON → register | **PASS** (`the_autostart_entry_round_trips_through_the_xdg_file`) |
| Toggle OFF → remove | **PASS** (там же: файл удаляется, повторное удаление не падает) |
| UI читает состояние ОС, а не JSON | **PASS** (`an_entry_a_user_disabled_is_reported_as_off`: `=false` в файле читается как «выключено») |
| Реальный login | **NOT RUN at STEP 4A** |

---

## 8. TOR (Linux)

| | |
|---|---|
| Bundled | **YES** — Tor **0.4.9.12** из pinned expert bundle |
| Shared libraries | `libcrypto.so.3`, `libssl.so.3`, `libevent-2.1.so.7` (+ `LD_LIBRARY_PATH` для POSIX, из STEP 4) |
| Runtime acceptance | **9/9 PASS против УСТАНОВЛЕННОГО `.deb`**, не против дерева исходников |
| Recovery | **PASS** — kill → новый pid → свежий proof → Ready |
| Orphan | **PASS** — остановка backend не оставляет `tor` |

Что именно доказал прогон на установленном пакете:

```
sidecar  /usr/lib/Canalla LLM/sidecar/alex-backend/alex-backend
tor      /usr/lib/Canalla LLM/runtime/tor
PASS  the packaged backend answers /health with no PATH at all            [alex-llm]
PASS  it reports the product, not a stand-in
PASS  the bundled Tor daemon bootstraps and proves a circuit             [state=managed endpoint=9050]
PASS  the route is proven through SOCKS, never a cleartext fallback      [method=socks5h]
PASS  the daemon that proved it is the pinned build Canalla ships        [0.4.9.12]
PASS  the proof names the process that serves it                         [exe=…/runtime/tor/tor]
PASS  killing the daemon is recovered by a new process, with a fresh proof  [42307 → 42450]
PASS  the backend keeps serving through the daemon's restart
PASS  stopping the backend leaves no daemon behind
```

---

## 9. DEB

| | |
|---|---|
| Собран | **YES** |
| Путь | `apps/desktop/src-tauri/target/release/bundle/deb/Canalla LLM_1.1.0_amd64.deb` |
| Размер | **294 746 692** байта |
| SHA256 | `1c52e24633d67f62d243b67ec9fc132b3beb11041b870738136cb99d91c980d2` |
| Пакет | `canalla-llm` 1.1.0 `amd64`, Installed-Size 711 625 KB |
| Промежуточный артефакт | **да** — это не релиз 1.2.0 |
| Установлен и проверен | **YES** — `apt install` + 9/9 acceptance против установленного дерева |

**Содержимое проверено в самом пакете, а не в дереве исходников:**

* `usr/bin/alex-llm`, `usr/bin/alex-host-loop`
* `usr/share/applications/Canalla LLM.desktop`
* `usr/share/icons/hicolor/{32x32,64x64,128x128,256x256@2,512x512}/apps/alex-llm.png`
* `usr/lib/Canalla LLM/sidecar/alex-backend/{alex-backend,_internal/…}`
* `usr/lib/Canalla LLM/runtime/tor/{tor, libcrypto.so.3, libssl.so.3, libevent-2.1.so.7, geoip,
  geoip6, runtime.json, gpl-3.0.txt, openssl.txt, libevent.txt, tor.txt}`

**Честная заметка про `Depends`:** в поле три пункта повторяются
(`libwebkit2gtk-4.1-0, libgtk-3-0, libayatana-appindicator3-1, librsvg2-2, libsecret-tools,
libwebkit2gtk-4.1-0, libgtk-3-0`). Так работает бандлер Tauri: он выводит зависимости из
скомпонованных библиотек и дописывает свою пару после списка из конфигурации. Повтор пункта в
`Depends` допустим и безвреден, `apt` разрешает его без замечаний; править сгенерированный control
ради косметики не стали. `libsecret-tools` — единственная зависимость, которую продукт не может
поставить сам (клиент Secret Service), и она объявлена.

**Сборка:** `tauri.linux.conf.json` (target `deb`, PNG-иконки, ресурсы с Linux-именами без `.exe`,
никакого Windows-материала) + `stage-native-runtime.sh` как `beforeBundleCommand`, который
выкладывает pinned Tor и **валит сборку**, называя отсутствующее, если нет sidecar или host loop.
`--no-sign` в этой сборке намеренно: подпись updater на этом этапе тестовая, и сборка не должна
ждать интерактивный пароль.

---

## 10. UPDATER LINUX

| | |
|---|---|
| Артефакт | `Canalla LLM_1.1.0_amd64.deb` + `Canalla LLM_1.1.0_amd64.deb.sig` (416 байт) |
| Platform | `linux-x86_64` |
| Подпись | тестовым ключом, **неинтерактивно**: `npx tauri signer sign -f <key> -p "" <deb> < /dev/null` |
| Криптографическая проверка | **PASS** — независимой реализацией `minisign 0.11`: `Signature and comment signature verified`, trusted comment `timestamp:1790225105  file:Canalla LLM_1.1.0_amd64.deb` |
| Platform filtering | **PASS** — `SUPPORTED_PLATFORMS = ("windows-x86_64", "linux-x86_64")`; тест Gateway утверждает, что ответ Linux-клиенту содержит **только** `linux-x86_64` (никогда Windows-запись), `linux-aarch64` → 204 |
| Production-ключ | **НЕ использован** |
| Production manifest | **НЕ опубликован** |

**Ключевая находка про Linux-обновление.** Артефактом обновления на Linux является **сам `.deb`**,
без AppImage-обёртки. Проверено по исходникам `tauri-plugin-updater-2.12.0/src/updater.rs`:
`install_inner` выбирает `Installer::Deb` → `install_deb` → пишет `package.deb` во временный
каталог → `dpkg -i` с повышением прав через `pkexec`, затем графический sudo (`zenity`/`kdialog`),
затем терминальный `sudo`. Значит, установленная из `.deb` Canalla может обновиться сама, и
`linux-x86_64`-запись манифеста указывает на `.deb` — ровно как в контрактной фикстуре STEP 1.

**Заметка в STEP 5.** Trusted comment подписи несёт `file:` (имя артефакта, а значит версию в имени),
но не отдельное поле версии — это то же хвост `requireSignedVersion`, который STEP 1 оставил
открытым и который STEP 5 §18 просил закрыть решением. Здесь он не менялся.

---

## 11. Четыре реальных дефекта Linux (не ошибки компиляции)

Сделать так, чтобы дерево скомпилировалось, было простой половиной. Четыре вещи на Linux были
**сломаны**, а не отсутствовали:

1. **`fs_guard.rs` отклонял любой абсолютный путь на POSIX.** Охранник спрашивал, начинается ли путь
   с буквы диска (`C:`), — на Linux так не начинается ни один путь, поэтому его же правило
   отбрасывало всё. Теперь вопрос звучит «это локальный абсолютный путь?»: буква диска на Windows,
   ведущий `/` на POSIX. Файловые инструменты впервые работают на Linux; Windows-тесты по-прежнему
   проверяют windows-написание.
2. **`git.rs` искал Git только в `C:\Program Files\Git\…`.** POSIX кладёт `git` в `PATH` — то же
   правило, по которому работает собственная оболочка пользователя. Windows-кандидаты там всё ещё
   проверяются первыми.
3. **Фикстура updater была CRLF в рабочем дереве и LF в репозитории.** `.gitattributes` говорит
   `* text=auto eol=lf`, закоммиченный blob — 264 байта LF, а в рабочем дереве файл разросся до 269
   байт CRLF, **при этом подпись была сделана по CRLF-байтам**. Тест проходил на машине, где файл
   создавали, и падал на свежем Linux-checkout. Исправлено материализацией закоммиченных байтов и
   **переподписью**; контрактная фикстура Gateway получила новую подпись и новый sha256. Это была
   мина для любого будущего checkout, а не Linux-специфичная проблема — поэтому добавлено правило
   `*.bin binary`, чтобы Git больше никогда не переписывал байты фикстуры.
4. **Windows-only инструменты хоста на Linux отвечали «не найдено».** У `registry_op`, `service_op`,
   `service_named` и `run_powershell` нет смысла на Linux. Теперь они отвечают типизированным
   `unsupported_platform` вместо того, чтобы делать вид, что инструмент есть, и падать позже.

Дополнительно `.gitattributes` получил `*.sh text eol=lf`: CRLF-шелл-скрипт на Linux — не скрипт
вовсе, он падает с сообщением о ненайденном файле (это уже случалось ранее в этой работе).

---

## 12. Python-половина: 16 падений → 0

Шестнадцать Linux-падений STEP 4 были **одной** причиной плюс три допущения в тестах:

| Группа | Тестов | Итог |
|---|---|---|
| `test_093_reliability.py` (8), `test_autonomous_tasks.py` (7), `test_091_reliability.py` (1) | 16 | **исправлено**: файловые инструменты хоста теперь разрешают POSIX-пути |
| `test_runtime_paths.py` | 1 | на Linux проверяет XDG-раскладку, на Windows — `%LOCALAPPDATA%` |
| `test_tor_bundle.py` | 1 | список `libraries` из пина — часть обещания |
| `test_upgrade.py` | 1 | детерминированный триггер (`chmod 0444`) не останавливает root; skip **с этой причиной** |

Правились `app/tools/local/{paths,scope,targets,intent,facts,locks,secrets}.py`: `native_path()`
понимал только обратные слэши, нормализатор и проверка вложенности сравнивали с `\`, каталог
scratch предполагал Windows-корень данных, а `assert_local_path` отказывал абсолютному POSIX-пути.

**8 skip'ов на Linux — все разобраны, production-падений под ними нет:**

| Skip | Сколько | Почему |
|---|---|---|
| `test_local_embeddings_cpu.py` | 3 | кэш эмбеддингов не подготовлен в этой среде (тест не скачивает) — свойство окружения, не платформы |
| `test_windows_job.py` | 1 | Job Objects — механизм Windows; POSIX-эквивалент проверяется тремя Rust-тестами (§4) |
| `test_tor_browser.py` | 1 | тот же Job Objects-путь |
| `test_upgrade.py` | 1 | выполняется под root: файл, доступный только для чтения, остаётся записываемым |
| `test_backup.py` | 1 | windows-форма пути |
| `test_live_web.py` | 1 | живой поиск требует явного opt-in (так же на Windows) |

---

## 13. TESTS

| Гейт | Число |
|---|---|
| Backend pytest — Windows | **630 passed, 1 skipped** |
| Backend pytest — Ubuntu 24.04 | **623 passed, 0 failed**, 8 skipped |
| Backend Ruff check / format — Windows | PASS (181 файл) |
| Backend Alembic (свежий SQLite → `0015`, `alembic check`) | PASS, «No new upgrade operations detected» |
| Gateway pytest | **148 passed** |
| Gateway Ruff check / format | PASS (29 файлов) |
| Gateway Alembic (свежий SQLite → `0001_gateway_core`, check) | PASS |
| Frontend Vitest | **235 passed** (20 файлов) |
| TypeScript (`tsc -b`) | clean |
| Prettier (`npm run format:check`) | clean |
| Vite build | PASS |
| Playwright | **20 passed** |
| Rust `cargo check --all-targets` — Windows / Linux | clean / **clean, 0 ошибок** |
| Rust `cargo test` — Windows | **91 passed** (21 + 58 + 12) |
| Rust `cargo test` — Ubuntu 24.04 | **99 passed** (25 + 62 + 12) |
| BasedPyright (`pyrightconfig.json`, mode standard) | **0 errors, 0 warnings** (234 файла) |
| Tor (runtime acceptance на установленном `.deb`) | **9/9 PASS** |
| Updater regression — Rust | 12 passed |
| Updater regression — Gateway (`test_updates.py`) | 18 passed |
| Linux packaging guard (Vitest) | 5 passed |
| `npm audit --omit=dev` | 0 уязвимостей |
| `pip-audit` | no known vulnerabilities (локальный пакет пропущен) |

**Заметка о форматировании Rust.** `cargo fmt` **не применялся ко всему крейту**: проверка показала,
что HEAD и без моих правок не является rustfmt-clean под текущей версией rustfmt, поэтому
общесистемный reformat дал бы ~900 строк несвязанного шума внутри порта. Новые файлы написаны
в rustfmt-стиле, но отдельный `cargo fmt` по крейту — это самостоятельное решение, а не часть
Linux-порта.

---

## 14. ENVIRONMENT ACCEPTANCE

**Ubuntu desktop clean VM: NOT RUN**

Причина, измеренная, а не предположенная:

* единственный Linux на машине — **headless** WSL2-контейнер: нет менеджера сессии, нет дисплея, нет
  keyring, нет ничего, что читает `~/.config/autostart`;
* Docker engine не запущен, Hyper-V требует повышения прав, VirtualBox отсутствует — то есть
  **десктопной VM нет нигде**.

Поэтому в этом отчёте **не заявляются**: первый запуск из `.deb` в реальной сессии, keyring после
логина, фактическая запись учётных данных в Secret Service «как у пользователя», срабатывание
автозапуска при входе, Ready без клика в живом окне, глобальный badge AI, панель обновлений.

Rust-набор тестов на Linux прогонялся под временной сессией `dbus-run-session` + разблокированным
`gnome-keyring-daemon` — это проверяет **путь кода**, но не является приёмкой десктопа, и в отчёте
так и обозначено.

Настоящий список того, что осталось доказать, лежит в `docs/linux-ubuntu.md` §6.

---

## 15. ЧТО ОСТАЛОСЬ — STEP 4B и STEP 5

**STEP 4B (только то, что требует живой Ubuntu-десктоп):**

1. Установить `.deb` на чистую Ubuntu 24.04 desktop и запустить без dev-toolchain.
2. Computer → Ready без клика; Tor → Ready с proof; single instance при конкуренции автозапуска и
   ручного запуска.
3. Keyring: учётные данные в Secret Service, ни одного секрета в файле.
4. Настоящий logout/login: Canalla стартует один раз, backend один, Tor один.
5. Убить backend и Tor — восстановление без клика; quit не оставляет orphan-процессов.
6. Глобальный badge AI: при ненастроенном провайдере **красный Disconnected**.

**STEP 5 (переносится целиком):** bump 1.1.0 → 1.2.0 во всех authoritative-местах, production
updater-ключ (отдельный от тестового), деплой `/updates/latest` на Gateway, хостинг артефактов,
финальные сборки Windows и Linux, pristine Windows VM, реальный login, апгрейд 1.1 → 1.2,
Auto Update E2E, решение по `requireSignedVersion` (§10), решение по Authenticode, манифест
**последним**, merge, tag `v1.2.0`.

---

## 16. CLEANUP

| | |
|---|---|
| Orphan-процессы (`tor`, `alex-backend`) | **NO** |
| Копия приватного тест-ключа в Linux | **удалена** (`/root/.signing` уничтожен) |
| Временные данные приёмки (`/tmp/canalla-*`) | удалены |
| Тестовый keyring | удалён |
| Пакет `canalla-llm` 1.1.0 в WSL | **оставлен намеренно** — это не рабочая машина, а одноразовая среда сборки |
| Дерево сборки `/root/canalla` в WSL | **оставлено намеренно**: там лежит собранный `.deb` и `.deb.sig` (в репозиторий 294 МБ не кладутся) |
| Рабочее дерево в Windows | чистое, кроме известного drift `docs/screenshots/0.4/*.png` |
| Версия / merge / tag / манифест / production-ключ / GPU | не трогались |

---

## 17. FINAL

```
ENGINEERING PORT       = PASS
VM ACCEPTANCE          = PENDING — no desktop VM available

UBUNTU 24.04 LTS x86_64 является baseline 1.2.0, и это уже не «Tauri вообще
умеет Linux»: установленный .deb поднял packaged backend без интерпретатора
на PATH и bundled Tor с реальным socks5h-proof, а desktop компилируется,
тестируется и владеет своими процессами на POSIX.
```
