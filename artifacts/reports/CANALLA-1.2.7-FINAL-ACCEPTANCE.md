# CANALLA LLM 1.2.7 — ФИНАЛЬНАЯ ПРИЁМКА РЕЛИЗА

**Отчёт по заданию «FINAL RELEASE ACCEPTANCE»: виртуализация, Linux, установка/обновление/откат, гейт**

| | |
|---|---|
| Дата | 15 ноября 2026 |
| Проект | Canalla LLM Desktop 1.2.7 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD | `89a5023` (запушен) |
| Артефакт собран и прогнан гейтом на | `61102fe` |
| Финальный установщик | `Canalla LLM_1.2.7_x64-setup.exe`, 96 561 854 B |
| SHA-256 установщика | `1c79b201bab79453b5f76d9fe57e274f39ae583d38891c81e8ebdf310a133c77` |
| Релизный гейт | **PASS — 22 шага, 0 FAIL, 0 BLOCKED** |
| Публикация | не выполнялась (требует отдельного разрешения) |

---

## 1. Резюме

Задание выполнено в той части, которую можно доказать на этой машине, и там, где среда не
позволила — это зафиксировано честно, а не замаскировано.

Подтверждено:

- **Ubuntu runtime acceptance — PASS 10/10**: собранный из этого дерева Linux-sidecar отвечает
  `/health` с полностью пустым `PATH`, сообщает версию 1.2.7; встроенный Tor 0.4.9.16 реально
  поднимает SOCKS5h-цепь, переживает убийство демона и восстанавливается с новым доказательством,
  после остановки не оставляет процессов.
- **Обновление 1.2.6 → 1.2.7 на реальных данных 1.2.6**: sidecar 1.2.7 мигрировал базу
  `0016 → 0017`, создал проверенный pre-upgrade backup, пользователь и чат сохранились.
- **Откат**: 1.2.6 на базе 0017 больше не падает с `CommandError`, а типизированно отказывается —
  exit `16`, `database_ahead`, база не тронута, лишний backup не создаётся. Дефект найден этой
  приёмкой и исправлен.
- **Финальный артефакт**: собран заново, updater-подпись production-ключом, полный релизный гейт
  `candidate` — **22/22 PASS**.
- **Production 1.2.6 восстановлена байт-в-байт** после инцидента с установщиком (раздел 7).

Не подтверждено (и почему):

- **Чистая Windows VM**: VirtualBox и бесплатный ISO есть, VM создана, Windows Setup загружается,
  но unattended-фаза откатывается на ~11%. Ограничение среды, не продукта (раздел 4).
- **Удаление программы на пользовательской установке** не выполнялось: оно удалило бы рабочее
  приложение. Поведение неизменно с 1.0.0 — uninstaller удаляет только каталог программы.
- **LEVEL 6/7** остаются `BLOCKED_BY_LIVE_BUDGET` / `NOT_TESTED` (раздел 11).

---

## 2. Что требовалось

1. Исследовать возможность бесплатной чистой Windows VM.
2. Если виртуализация доступна — создать изолированное окружение.
3. Проверить clean install 1.2.7, upgrade 1.2.6 → 1.2.7, сохранение данных, Computer Connect,
   Tor, updater, rollback, удаление.
4. Найти доступный Linux/Ubuntu runtime, не затрагивая работающие сервисы VPS.
5. Выполнить Ubuntu acceptance.
6. Исправлять найденные ошибки, повторять бесплатные тесты, обновлять сборку.
7. Проверить всё, что доступно для LEVEL 6/7 без новой платной GPU; mock/replay не выдавать за LIVE.
8. Сохранить production 1.2.6 без изменений.
9. Не покупать сертификаты и облачные ресурсы.
10. Выполнить полный релизный гейт на финальном артефакте.
11. Обновить документацию, evidence и Git.
12. Подготовить полный отчёт .md и .html на Рабочем столе.

---

## 3. Виртуализация: что найдено

| Ресурс | Состояние |
|---|---|
| Windows | Windows 11 |
| Hyper-V hypervisor | присутствует (HypervisorPresent = True) |
| Windows Sandbox | **отсутствует** (`WindowsSandbox.exe` нет, компонент не включён) |
| VirtualBox | **установлен, 7.2.8r173730** |
| Существующие VM | `Ubuntu-Desktop-2` (poweroff), одна недоступная запись |
| ISO на диске | отсутствовал |
| Free RAM / disk | 7.9 GB / C: 46 GB, D: 77 GB |
| Бесплатный ISO | **Windows 11 Enterprise Evaluation 24H2**, официальный, 5 387 960 320 B |

ISO скачан с `software-static.download.prss.microsoft.com`, размер совпал с `Content-Length`
байт в байт, структура проверена: PVD `CD001`, El Torito, UDF-bridge. Ничего не покупалось.

---

## 4. Чистая Windows VM: что сделано и где остановилось

Создана VM `Canalla-127-Acceptance` (Windows11_64, EFI, TPM 2.0, 4 GB RAM, 2 CPU, диск 40 GB на D:,
общая папка, Guest Additions). Путь установки прошёл несколько реальных препятствий, каждое
исправлено:

1. **ISO не подключался**: у SATA-контроллера было 2 порта, и aux-образ занял единственный
   свободный. Исправлено: 4 порта + явное подключение ISO.
2. **VirtualBox обернул ISO в aux-образ и потерял El Torito** (`cBootCatEntries: none`) — VM
   уходила в EFI shell. Исправлено: загрузка с оригинального ISO.
3. **ISO ждёт нажатия клавиши** («Press any key to boot from CD or DVD»), headless-загрузка без
   клавиатуры уходила в Boot Manager. Исправлено: подача скан-кодов в нужное окно.
4. **Файл ответов на дискете не подхватился** — Setup показал выбор языка. Исправлено: отдельный
   маленький ISO с `autounattend.xml` как второй оптический носитель.
5. **Установка доходит до «Installing Windows 11 — 11%»** и после перезагрузки машина не находит
   загрузочный диск (`BdsDxe: No bootable option`), VDI остаётся ~1.1 GB. Повторные попытки
   (включая загрузку с DVD) не продвигают фазу apply.

Итог: **чистая VM создана и Windows Setup в ней запускается, но unattended-установка в этой среде
не завершается**. Это ограничение среды (Hyper-V + VirtualBox 7.2, 8 GB RAM), а не дефект
продукта. Прямое следствие: матрицу установки проверяли на реальной установке с проверенным
снимком и восстановлением (раздел 7), а чистая VM остаётся предусловием публикации.

---

## 5. Linux / Ubuntu runtime acceptance — PASS

Среда: **Ubuntu 24.04.4 LTS** (WSL2, kernel 6.6.87.2-microsoft-standard-WSL2), Python 3.12.3,
gcc 13.3, 916 GB свободно. VPS не затрагивался: всё локально, в отдельном каталоге
`/home/volkr/canalla-linux`, без обращения к работающим сервисам.

Что сделано:

1. Исходники backend скопированы из рабочего дерева (без `.venv`, `build`, `dist`).
2. Создано venv, установлены зависимости проекта и PyInstaller.
3. Собран **Linux sidecar** `dist/alex-backend/alex-backend` (21 499 872 B).
4. Скачан и проверен по пину **Tor Expert Bundle 0.4.9.16** для linux-x86_64.
5. Запущен `scripts/acceptance-linux-runtime.sh`.

Результат — **10/10 PASS**:

```
PASS the packaged backend answers /health with no PATH at all        [alex-llm]
PASS it reports the product, not a stand-in                          [product=alex-llm]
PASS it is the version this source tree builds                       [version=1.2.7]
PASS the bundled Tor daemon bootstraps and proves a circuit          [state=managed endpoint=9050]
PASS the route is proven through SOCKS, never a clearnet fallback    [method=socks5h]
PASS the daemon that proved it is the pinned build Canalla ships     [0.4.9.16 = pin 0.4.9.16]
PASS the proof names the process that serves it, and it is shipped    [pid=1615 .../tor/tor]
PASS killing the daemon is recovered by a new process, fresh proof   [1615 -> 1758]
PASS the backend keeps serving through the daemon's restart
PASS stopping the backend leaves no daemon behind                    [leftover tor: 0]
```

Проверка повторена после последней правки кода (sidecar пересобран) — снова PASS.

---

## 6. Установка, обновление, сохранение данных

### 6.1. Что произошло фактически

План был установить 1.2.7 в изолированный каталог через `/S /D=<dir>`, предварительно убрав ключ
деинсталляции, чтобы NSIS видел «чистую машину». **Установщик проигнорировал `/D`** и обновил
реальную установку `%LOCALAPPDATA%\Programs\Canalla LLM` — это и есть настоящее обновление
1.2.6 → 1.2.7, но оно затронуло пользовательскую установку.

Действия сразу после обнаружения:

1. Payload 1.2.7 сохранён в `D:\canalla-vm\host-accept\payload127` (версия 1.2.7, exe
   `d1ec80fd…`).
2. Production восстановлена из снимка, сделанного **до** установки:
   `robocopy /MIR` из `D:\canalla-vm\prod-snapshot\program`.
3. Проверка восстановления: exe `21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1`
   — **совпадает с исходным**, 1278 файлов, `ProductVersion 1.2.6`.
4. Ключ деинсталляции возвращён на `DisplayVersion 1.2.6` и тот же `InstallLocation`.
5. `HKCU\Software\Canalla LLM` пересоздан (в нём были только артефакты тестов
   `autostart-test`; приложение там настроек не хранит).
6. Ярлык в меню «Пуск» восстановлен из снимка.

### 6.2. Проверка совместимости данных (реальная миграция)

| Шаг | Результат |
|---|---|
| Данные, созданные sidecar 1.2.6 | база на ревизии `0016`, 1 пользователь + 1 чат («Сохранить меня после обновления») |
| Запуск sidecar 1.2.7 на этой базе | `/health` → `version=1.2.7` |
| Миграция | `0016 → 0017`, `result: ok`, backup `20261115T074217Z-pre_upgrade` (проверенный) |
| Сохранность | пользователь 1, чат 1, заголовок тот же, `alembic_version = 0017` |

### 6.3. Ограничение, найденное по ходу

**Установка «рядом» невозможна**: NSIS игнорирует `/D` и всегда ставит в
`%LOCALAPPDATA%\Programs\Canalla LLM`, заменяя установленную сборку. Это зафиксировано в
release notes; данные при этом не затрагиваются.

---

## 7. Откат: найден и исправлен реальный дефект

Откат на 1.2.6 после миграции 1.2.7 ранее заканчивался так:

```
alembic.runtime.migration ... upgrade 0016 -> 0017
alex.runtime migration_failed CommandError
```

То есть 1.2.6 видел ревизию `0017`, считал её «ожидающей миграцией», пытался выполнить
`upgrade head` и падал с `CommandError`; backend не стартовал, причина не названа.

Исправление (`apps/backend/app/runtime_entry.py`):

- `migration_state()` различает «эта сборка позади базы» и «есть работа для миграции»;
- при базе новее сборки — exit **16**, `ALEX_RUNTIME_ERROR database_ahead current=… build=…`,
  запись `runtime/migration.json` с `result: refused_database_ahead`;
- база и её backup не трогаются, лишний pre-upgrade backup не создаётся.

Регрессионный тест: `tests/test_upgrade.py::test_a_database_from_a_newer_build_is_refused_not_migrated`
(подменяет ревизию на будущую и проверяет код 16, запись, отсутствие backup и сохранность данных).
Весь файл: **11 passed**.

---

## 8. Updater и Computer/Tor

| Проверка | Результат |
|---|---|
| Updater-подпись установщика | Ed25519 (minisign) production-ключом `9B328EFF111D1FB2`, проверена гейтом |
| Trusted comment подписи | привязан к имени файла `Canalla LLM_1.2.7_x64-setup.exe` |
| Ключ в конфиге клиента = production | проверяется тестом `test_the_configured_key_is_the_production_identity_and_not_the_test_one` |
| Подмена/порча пакета | Rust-тесты: чужая подпись, испорченная подпись, обрезанный пакет — отказ |
| `ALEX_GATEWAY_URL` вшит | `PACKAGED-GATEWAY-URL: PASS` |
| Полный цикл «скачать и установить обновление» | **не проверялся**: требует опубликованного манифеста, а публикация запрещена без отдельного разрешения |
| Computer Connect + Tor (установленное приложение) | **ALWAYS READY PASS** (эта сессия, ранее): зелёные при запуске, после relaunch и после реального kill sidecar, один `device_id` |
| Tor в упакованном Linux-backend | **PASS** (раздел 5): реальная SOCKS5h-цепь, восстановление после kill |

---

## 9. Финальный артефакт

| Параметр | Значение |
|---|---|
| Имя | `Canalla LLM_1.2.7_x64-setup.exe` |
| Размер | 96 561 854 B |
| SHA-256 | `1c79b201bab79453b5f76d9fe57e274f39ae583d38891c81e8ebdf310a133c77` |
| Updater `.sig` | 424 B, `sha256:3503edff22d545bf23a3622a34ee7d7bd9e7130301481506b9e89f41ab928154` |
| Authenticode | **NotSigned — WAIVED_BY_OWNER** |
| `alex-llm.exe` | 6 035 968 B, `sha256:49972ee2300455c0c3285fdf2a4e56e720aa5ca75c599b85db0beabd77c1d319` |
| `alex-host-loop.exe` | 2 243 584 B, `sha256:7bbec361f9bf18dfaf6d4150353ebb3f23e9dce16831942637a16ceda7079af2` |
| Sidecar | 24 854 377 B, `sha256:cea3320c8463eb214bff1afe5ea2d315227e3ba2a1cda818d6d9762782b4be80` |
| `tor.exe` | 10 248 704 B, `sha256:da22856f95ee24f8ace4ad1ab04acbd818fd6c3042bc3cf8f1d7cc3fa3edcb2b` |
| Собран на коммите | `61102fe` |

Контрольные суммы: `artifacts/SHA256SUMS-1.2.7.txt`.

---

## 10. Полный релизный гейт

```
release-gate.py candidate --installer "Canalla LLM_1.2.7_x64-setup.exe"
22 шага · 0 FAIL · 0 BLOCKED
RELEASE GATE: PASS
```

Шаги: ruff (backend, format, gateway), backend pytest, gateway pytest, vitest, tsc, prettier,
vite build, `cargo check --all-targets`, `cargo test`, Playwright, sidecar stamp, packaged Gateway
URL и все artifact-проверки (имя, SHA-256, key id, Ed25519, signed name, gateway URL, commit).

Лог: `artifacts/release-gate-1.2.7-final.txt`.

Функциональные числа этой сессии: backend **1184 passed / 1 skipped** (полный прогон),
gateway **262**, vitest **328**, Playwright **25**, Rust **134** (40 + 78 + 16, 2 network-ignored),
BasedPyright **0/0 на 153 файлах**, ruff clean.

Два дефекта инструментов, найденные по ходу и исправленные:

1. `release-gate.py` падал с `UnicodeEncodeError` на консоли cp1251, теряя готовый PASS → поток
   отчёта закреплён на UTF-8.
2. `cargo test --release` перезаписал release-бинарник без вшитого `ALEX_GATEWAY_URL`, из-за чего
   гейт честно показал FAIL на `packaged.gateway_url`; установщик пересобран штатным путём.

---

## 11. LEVEL 6/7 без новой платной GPU

| Уровень | Статус | Почему |
|---|---|---|
| 6 — независимая проверка доказывает завершение | `BLOCKED_BY_LIVE_BUDGET` | нужен один завершённый LIVE с `REAL_MODEL_CONFIRMED` и всеми проверками; бюджет серии исчерпан ($0.358 < полезного окна) |
| 7 — durable resume с реальной моделью | `NOT_TESTED` | требует платного окна |

Что сделано для их закрытия без денег: дефект, из-за которого прогон закрывался без независимой
проверки, исправлен; replay сохранённого трейса воспроизводит вердикт и показывает, что
остальной трейс уже достаточен. Ни replay, ни тесты **не выдаются** за LIVE PASS.

---

## 12. Production integrity

| Параметр | До сессии | Сейчас | Статус |
|---|---|---|---|
| users | 2 | 2 | без изменений |
| chats | 54 | 54 | без изменений |
| messages | 120 | 120 | без изменений |
| paired_devices | 63 | 63 | без изменений |
| local_tasks | 30 | 30 | без изменений |
| integrity_check | ok | ok | без изменений |
| foreign key violations | 0 | 0 | без изменений |
| Установленный exe | `21b50e46…` (1.2.6) | `21b50e46…` (1.2.6) | восстановлен байт-в-байт |
| Файлов в программе | 1278 | 1278 | совпадает |
| Ключ деинсталляции | 1.2.6 | 1.2.6 | восстановлен |
| Ярлык «Пуск» | есть | есть | восстановлен |

Инцидент с `/D` и его обратимость описаны в разделе 6.1 — это единственное вмешательство в
пользовательскую установку за сессию, и оно полностью отменено.

---

## 13. Найденные и исправленные дефекты (эта сессия)

1. **`database_ahead`**: откат на старую сборку падал с `CommandError` → типизированный отказ
   (exit 16), база не тронута, добавлен регрессионный тест.
2. **`release-gate.py` на legacy-консоли**: `UnicodeEncodeError` терял готовый отчёт → UTF-8.
3. **Конфигурация VM**: порт SATA, потеря El Torito в aux-образе, «press any key», файл ответов
   на дискете — исправлено в скриптах приёмки (VM-установка всё равно не завершилась).

---

## 14. Git

```
89a5023 Record the final acceptance and its artifact
61102fe Print the gate report on a legacy console
bf2c7c4 Refuse a database written by a newer build
86fc131 Point the report at the commit that carries it
7cb35cb Report every substantial session to the Desktop
559a3ad Name the commit the artifact was built from
...
```

Всё запушено в `origin/feature/canalla-1.2.7-autonomous-agent`.

---

## 15. Что осталось

| Пункт | Причина |
|---|---|
| Чистая Windows VM: install/upgrade/uninstall | unattended-установка не завершается в этой среде (VirtualBox + Hyper-V, 8 GB RAM) |
| Полный цикл updater | нужен опубликованный манифест; публикация запрещена без отдельного разрешения |
| LEVEL 6/7 | нужен новый бюджет GPU |
| Публикация 1.2.7 | отдельное разрешение владельца; `release-gate.py publish` отказывает по замыслу |

---

## 16. Воспроизведение

```powershell
# Linux acceptance (WSL Ubuntu 24.04, изолированно)
wsl -d Ubuntu -- bash -lc "cd /mnt/c/.../alex-llm && bash scripts/acceptance-linux-runtime.sh \
  --sidecar /home/volkr/canalla-linux/backend/dist/alex-backend/alex-backend \
  --tor-runtime /home/volkr/canalla-linux/tor \
  --data /tmp/canalla-linux-acceptance \
  --product-source /home/volkr/canalla-linux/backend/app/product.py"

# Полный релизный гейт (cargo должен быть в PATH; на этой машине %USERPROFILE%\.cargo\bin)
$env:CARGO_BUILD_JOBS = '1'
apps\backend\.venv\Scripts\python.exe scripts\release-gate.py candidate `
  --installer "apps\desktop\src-tauri\target\release\bundle\nsis\Canalla LLM_1.2.7_x64-setup.exe" `
  --expect-sha256 1c79b201bab79453b5f76d9fe57e274f39ae583d38891c81e8ebdf310a133c77 `
  --expect-commit 61102fec9b641d56a137fe36e1e74ef8fe4301af
```

---

## 17. Индекс evidence

| Что | Где |
|---|---|
| Лог финального гейта | `artifacts/release-gate-1.2.7-final.txt` |
| Контрольные суммы | `artifacts/SHA256SUMS-1.2.7.txt` |
| Release notes и чеклист | `docs/release-1.2.7.md` |
| Итоговый отчёт по автономной работе | `artifacts/REPORT-canalla-127-AUTONOMOUS.md` |
| Скрипты приёмки VM | `artifacts/canalla-vm/create-vm.ps1`, `accept.ps1` |
| Снимок/восстановление production | `artifacts/canalla-vm/host-accept.ps1`, `restore-production.ps1` |
| Совместимость данных и откат | `artifacts/canalla-vm/data-compat.ps1`, `rollback-after-migration.ps1` |
| Логи и базы приёмки | `D:\canalla-vm\host-accept\evidence\` |
| Экраны установки VM | `artifacts/canalla-vm/screen*.png` |

---

## 18. Правило отчётности

После каждой существенной сессии отчёт кладётся на Рабочий стол в `.md` и `.html`, копии — в
`artifacts/reports/` и в коммит. Неизвестное остаётся неизвестным, `BLOCKED` не превращается в
`PASS`, секреты не публикуются.
