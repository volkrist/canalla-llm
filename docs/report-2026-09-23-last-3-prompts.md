# CANALLA LLM 1.2.0 — ОТЧЁТ ПО ТРЁМ ПОСЛЕДНИМ ПРОМПТАМ

**Дата отчёта:** 23 сентября 2026
**Репозиторий:** `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`
**Ветка:** `release/canalla-1.1.0`
**HEAD:** `f846b11` — `fix(desktop): the supervisor owns the sidecar lifecycle`
**`main`:** `9bd7548` = `origin/main` (release-работа ещё **не** влита; ветка впереди на **22** коммита, всего 172)
**Теги:** только `v1.0.0`. `v1.1.0` и `v1.2.0` **не существуют** → коллизии immutable-тега нет → итоговая версия остаётся **1.2.0** (переход на 1.2.1 не требуется)
**Версия в дереве:** `1.1.0` во всех шести местах (Tauri / Cargo / package.json / backend / Gateway / product.py) — bump ещё не делался
**Статус на момент отчёта:** работа не завершена; RELEASE ещё не разрешён

---

## 0. Прошлый прогон 9 ч 17 мин — разбор и вывод

### Что это было

Прогон такой длительности — это **одна команда, которая не завершилась**, а не «долгая работа». Прямые признаки:

1. Терминал инструмента работает в интерактивной pty, и любая команда, которая **читает stdin**, блокируется до таймаута — то есть висит бесконечно, если таймаут не задан. В заметках прошлой сессии этот отказ зафиксирован буквально: `python - <<'EOF' …` (heredoc) один раз **подвесил** инструмент.
2. Команда, которую вы просили пропустить, — ровно эта конструкция:

   ```
   python - <<'EOF' 2>/dev/null || apps/backend/.venv/Scripts/python.exe -c "..."
   EOF
   ```

   Это `python -` с heredoc на stdin, без `timeout_ms`. Такой запуск не завершается сам: оболочка отдаёт скрипт на stdin, а инструмент ждёт конца процесса. 9 ч 17 мин — это ожидание таймаута/закрытия сессии, а не 9 часов вычислений.
3. Второй вклад в длительность того же периода — марафон пересборок: ~10 циклов «PyInstaller sidecar → `cargo` release-линковка → NSIS-бандл → установка → installed e2e». Каждый цикл — единицы–десятки минут, плюс окно ожидания GPU на 20 минут, которое продукт прямо запрещает.

### Вывод (принятые правила, больше не повторяю)

1. **Никаких stdin-конструкций**: `heredoc`, `python -`, интерактивные команды. Патчи — только через редактор файлов.
2. **У каждой команды обязательный `timeout_ms`.** Долгая команда — это ошибка планирования, а не норма.
3. Сначала **детерминированный тест**, потом **одна** installed-проверка на гипотезу. Никаких «правка → 10 минут сборки → установка → проверка» по кругу.
4. Никаких повторов длинных циклов; GPU для этих задач не нужен вообще, окно ожидания при необходимости ≤ 60 с, иначе `BLOCKED EXTERNALLY — CAPACITY` и стоп.
5. Сначала факт (лог, тест, хэш), потом утверждение в отчёте.

### Отдельно: почему пропущенный патч был бы вредным

Пропущенная команда подменяла пин `EXPECTED_SIDECAR_SHA256` в `scripts/acceptance-post-release-1.1.0.py` на `3928787d…`. Проверено фактически:

| Артефакт | Фактический SHA256 |
|---|---|
| установленный `sidecar/alex-backend/alex-backend.exe` | `ffd8884d9ce01919ad60fdf862857af62171b2718092a0b8577dfcefa10a2cd3` |
| свежесобранный `apps/backend/dist/alex-backend/alex-backend.exe` | `ffd8884d9ce01919ad60fdf862857af62171b2718092a0b8577dfcefa10a2cd3` |

Текущий пин (`ffd8884d…`) **совпадает** и с установленной, и со собранной сборкой. Значение `3928787d…` не соответствует ни одной из них — патч сломал бы приёмку. Пин обновляется **один раз**, под финальную сборку, а не после каждой промежуточной.

---

## 1. Промпт №1 — COMPUTER + TOR: ALWAYS READY

### Что требовалось
Computer и Tor — не on-demand функции: автоматический запуск вместе с Canalla, восстановление после перезапуска приложения и Windows, сохранение pairing, постоянный heartbeat, авто-reconnect без ручного «Подключить»; Tor — обнаружение/управление SOCKS, реальная проверка circuit, авто-восстановление, fail-closed (никакого молчаливого clearnet); health и usage policy не смешиваются; зелёный статус — только по реальной проверке.

### Что сделано (закоммичено)

| Что | Где | Коммит |
|---|---|---|
| `TorService`: обнаружение (configured → 9050 → 9150), управляемый запуск, proof через реальный Tor (`IsTor: true`), persist `runtime/tor.json`, типизированные причины `tor_*`, отсутствие clearnet-fallback | `apps/backend/app/tools/tor/service.py` | в дереве ранее |
| 18 детерминированных Tor-тестов (fake listener/binary/spawn/proof), включая recovery → ready и повторный proof после рестарта | `apps/backend/tests/test_tor_service.py` | `a1dec59` |
| Контракт status/recovery: +7 случаев (3 Computer, 4 Tor); heartbeat не создаёт второе устройство | `apps/backend/tests/test_status_recovery.py` | `b31e159`, `b312d55` |
| Установочный гейт always-ready на реальной сборке: **21/21 PASS** | `apps/desktop/e2e/always-ready.mjs` | `7633f37` |
| Изоляция device-credential в смоуках | `apps/desktop/e2e/*` | `a604840` |
| Документация Tor и Computer | `docs/tor.md`, `docs/local-computer.md` | `18e73ae` |

### Доказательства (из принятого прогона)
- `e2e/always-ready.mjs`: после обычного запуска Computer «Готово», Tor «Готово» **с доказанным circuit**, popover'ы разделяют health и mode; после полного Quit + relaunch — та же сессия и **тот же `device_id`**, одно устройство, без повторного pairing.
- Операторская установленная 1.1.0: Computer `ready` по свежему heartbeat; Tor `ready` из persisted `runtime/tor.json` (`source: managed`, полный bootstrap ~30 с); после Quit нет orphan `tor.exe` и слушателя 9050.
- BasedPyright **0 errors / 0 warnings**; backend pytest **606 passed, 1 skipped**; Vitest **161**; cargo **18 + 51**.

### Что осталось невыполненным
- **Tor всё ещё зависит от внешнего runtime.** Если у пользователя нет Tor Browser / `tor.exe` в PATH / `Program Files\Tor` — Tor не станет Ready. Bundled (управляемый, поставляемый) Tor для Windows и Linux **не сделан**. Это прямое нарушение требования self-contained из промпта №3.
- Не оформлена отдельным случаем проверка конфликта с чужим Tor (чтобы не «захватывать» чужой процесс молча).
- Автозапуск Canalla при входе в Windows (и, соответственно, автоматический Ready после логина) не реализован.

---

## 2. Промпт №2 — CANALLA LLM 1.2.0: FINAL INTEGRATION + RELEASE CERTIFICATION

### Что требовалось
Не добавлять функциональность, а собрать всё уже сделанное в одну согласованную, проверенную и **установленную** версию; определить фактическое состояние; проверить version safety; прогнать полный бесплатный гейт; выпустить отчёт в заданном формате; не выпускать релиз при известном блокере.

### Что сделано
Зафиксировано фактическое состояние и version safety (`v1.2.0` не существует → итог 1.2.0; `v1.0.0` не тронут). Найден и закрыт **release-blocker**: локальный backend после падения не поднимался, Computer/Tor оставались в «Проверяем…». Четыре коммита:

1. `9bc01e0` — **все** вызовы локального backend ограничены по времени (один общий клиент, connect 3 с / request 5 с). До этого незакрытые вызовы держали IPC-пул WebView, из-за чего `invoke("ensure_backend")` «зависал» при живых остальных командах — это и была загадка deadlock'а.
2. `0cc9fdd` — неудачное чтение статуса помечается `stale`; чипы и панели больше не показывают «Готово» по устаревшему/проваленному снимку (баланс сохраняет собственную метку времени).
3. `1fe8da1` — frontend-watchdog `recoverBackend` (**bounded**, 20 с на вызов; попытки 0 / 2 / 6 с; сначала `ensure_backend`, затем `restart_backend`).
4. `f846b11` — **источник истины — Desktop/Rust supervisor**: `start_watchdog()` из `.setup()`, `observe_owned_child()`, `RESTART_LIMIT = 3`, интервал 2 с, `begin_shutdown()` (Quit не воскрешает процесс); бюджет рестартов общий для `ensure` и watchdog; тестовые швы `TEST_LAUNCH` / `FAKE_SIDECAR` / `SUPERVISOR_TEST_LOCK`.

**Детерминированная проверка вместо цикла сборок:** Rust-тест `watchdog_restarts_a_crashed_owned_sidecar` — kill → ровно одна замена, новый PID, `restarts == 1`, тишина после shutdown. `cargo test`: **18 + 52 passed**.

**Одна** installed-верификация: установка сборки и `e2e/always-ready.mjs` — ALL PASS, crash-фаза `21120 → 25456`, Computer и Tor вернулись в зелёный **без клика**.

Отчёт того этапа: `docs/certification-report-2026-09-23.md` (копия на Рабочем столе, `CANALLA-LLM-1.2.0-INTEGRATION-CERTIFICATION-ОТЧЁТ.md`), вердикт на тот момент — **RELEASE BLOCKED**.

### Что осталось невыполненным
- Полный бесплатный гейт (§21 промпта) целиком не прогонялся ни разу в одной сессии.
- Компактный UI / Settings (§18–§19) и D-9 / context meter (§16–§17) приняты ранее, но не пере-подтверждены на финальной сборке.
- Auto Update на тот момент отсутствовал полностью.

---

## 3. Промпт №3 — FINISH ALL MISSING WORK (self-contained Win/Ubuntu + crash recovery + Auto Update + 1.2.0)

### Что сделано

**A. Crash recovery — закрыт и подтверждён** (коммит `f846b11`, см. раздел 2): восстановление инициирует Desktop/Rust, а не «frontend по таймеру»; один экземпляр, bounded-рестарт, Quit не воскрешает процесс.

**B. Auto Update — реализован (в дереве, ещё не закоммичен).**

- Клиент: `tauri-plugin-updater` + `tauri-plugin-process` в `main.rs`; права в `capabilities/default.json`; `bundle.createUpdaterArtifacts: true`; `plugins.updater` с публичным ключом и endpoint'ом `https://gateway.12testers.store/updates/latest?target={{target}}&arch={{arch}}&current_version={{current_version}}`; `windows.installMode: "passive"`.
- Frontend: `src/lib/updates.ts` (разбор версий, `isNewerVersion`, `downloadUpdate` с прогрессом, `installDecision`), `src/hooks/useUpdates.ts` (фазы idle/checking/available/downloading/ready/error, периодическая проверка 4 ч), `src/components/UpdatesPanel.tsx` (секция Settings «Обновления», ручной «Проверить», «Перескачать», «Перезапустить и обновить»), `src/lib/busy.ts` (генерация / локальные задачи / backup / restore → **установка откладывается**, а не убивает операцию), `Settings.autoCheckUpdates` (по умолчанию `true`).
- Gateway: `apps/gateway/gateway/updates.py` + маршрут `GET /updates/latest` в `routes.py` (204 = «обновлений нет», 503 = манифест отвергнут), настройки `updates_manifest_path` / `updates_manifest_json`; поддерживаемые платформы `windows-x86_64`, `linux-x86_64`; отказ на downgrade, неизвестную платформу, отсутствие подписи и на поля, похожие на секрет.
- Ключи: тестовая пара — вне репозитория (`C:\Users\Volkr\.canalla-updater\canalla-updater-test.key` + `.key.pub`). Приватный ключ в репозитории отсутствует (проверено поиском `*.key`).
- **Честная граница:** автоматически обновляться смогут только сборки, **содержащие** updater, то есть 1.2.0+. 1.0.x/1.1.x → 1.2.0 — один раз вручную установщиком. Это обязано быть в release notes.

**Проверено прямо сейчас (сессия этого отчёта, без сборок и GPU):**

| Гейт | Результат |
|---|---|
| Gateway `pytest tests/test_updates.py` | **10 passed** |
| Gateway `ruff check .` | **All checks passed** |
| Gateway `ruff format --check .` | **29 files already formatted** |
| Frontend `vitest run src/lib/updates.test.ts` | **10 passed** |
| Frontend `vitest run` (весь) | **179 passed (17 файлов)** |
| `npx tsc -b` | чисто |
| `cargo check` (desktop) | чисто |

### Что ещё НЕ сделано
1. **Bundled managed Tor** (Windows + Linux): pinned version, provenance, SHA256, license/NOTICE, собственный data directory/SocksPort, конфликт-чек; снятие зависимости от Tor Browser.
2. **Self-contained Windows**: доказательство на чистом профиле, что не нужны Python / Node / Rust / Tor Browser, и что Computer и Tor становятся Ready **без клика** — на сборке, где уже включён updater. Сейчас установлена сборка **до** updater'а.
3. **Ubuntu как first-class**: `.deb`/AppImage, системные зависимости как declared deps пакета, Linux secret storage (не plaintext), XDG-autostart, приёмка на чистой VM. (WSL2 Ubuntu и Docker 29.2.1 на машине есть.)
4. **Автозапуск Canalla при входе в Windows** + toggle в Settings (default ON) + удаление записи при uninstall.
5. **Bump 1.1.0 → 1.2.0** в шести местах + Linux-пакет + updater-метаданные + docs.
6. **Финальная чистая сборка** Windows (и Linux), полные SHA256, updater-артефакты и подписи.
7. **Clean install + upgrade acceptance** (1.1.0 → 1.2.x) с сверкой данных по записям, а не «приложение открылось».
8. **Update test matrix** end-to-end на установленном клиенте (bad signature / corrupt / wrong platform / downgrade / offline / timeout / прерванная загрузка / 404 / 500). Сейчас это покрыто unit-тестами обеих сторон, но не установленным клиентом.
9. Документация релиза + классификация открытых хвостов (WM-07, CD-08, `client_version`, Web health).
10. Merge → `main`, tag `v1.2.0`, публикация production manifest **последней**.

---

## 4. Текущее фактическое состояние (evidence)

```
branch : release/canalla-1.1.0
HEAD   : f846b11b40e6376eb8311558d24d1ed4aa163ef3
main   : 9bd75486c34b64ab4435afb4efc9fa9b17104cfd
origin/main: 9bd75486c34b64ab4435afb4efc9fa9b17104cfd
tags   : v1.0.0
ahead  : 22 коммита
```

**Артефакты последней сборки (измерено сейчас):**

| Артефакт | Размер, байт | SHA256 |
|---|---|---|
| `Canalla LLM_1.1.0_x64-setup.exe` (23.09 17:55) | 87 200 000 | `e1e7e4d7558cc0c096fcdaf254ab2de219bb2bea96671f60b7e92ffa9cb9da97` |
| Установленный `alex-llm.exe` (23.09 17:52) | 5 514 240 | `fc9e2ae18640366ad4e305ea4b71dedf7ab34e7aff9497b4d765add1e5759526` |
| Собранный `target/release/alex-llm.exe` (23.09 17:55) | 5 514 240 | `f8de4a6887f5108cc9ade46850a68aec71d7137e19d2ccbc11f30ba054190ceb` |
| Sidecar `alex-backend.exe` (установленный = собранный) | 23 524 092 | `ffd8884d9ce01919ad60fdf862857af62171b2718092a0b8577dfcefa10a2cd3` |
| `alex-host-loop.exe` | 2 160 128 | `ff6a0957764c8af69340ec3a790f5601466a2bf09e2f504cc04d06abb433b415` |

Отсюда два важных факта:

- **установленное приложение ≠ последняя собранная сборка** (`fc9e2ae1…` против `f8de4a68…`), то есть сборка с updater'ом **ещё ни разу не устанавливалась и не проверялась** — это осознанная незакрытая позиция, а не «всё уже работает»;
- 87,2 МБ у установщика — сам по себе признак, что внешний Tor runtime в бандл ещё не добавлен.

**Бесплатные гейты на текущем дереве** (сводно): backend pytest 606 passed / 1 skipped, BasedPyright 0/0, ruff/alembic чисто, Gateway 130 + 10 новых по обновлениям, Vitest 179, tsc чисто, cargo check чисто, cargo test 18 + 52, installed `e2e/always-ready.mjs` ALL PASS, смоуки GUI/backup/cloud/cloud-default PASS.

**GPU:** не использовался, Pod'ов не создавалось, расход 0.

---

## 5. Незакоммитная работа в дереве (сохранить, не откатывать)

Изменено (17 файлов кода, + эффект на тесты): `apps/desktop/package.json`, `package-lock.json`, `src-tauri/Cargo.toml`, `Cargo.lock`, `src-tauri/src/main.rs`, `src-tauri/capabilities/default.json`, `src-tauri/tauri.conf.json`, `src/App.tsx`, `src/components/SettingsDialog.tsx`, `src/components/Workspace.tsx`, `src/lib/settings.ts`, `src/types.ts`, `src/lib/settings-nav.test.tsx`, `src/lib/backup.test.tsx`, `src/lib/cloud.test.tsx`, `apps/gateway/gateway/config.py`, `apps/gateway/gateway/routes.py`.

Новые (7): `apps/desktop/src/lib/updates.ts`, `src/lib/updates.test.ts`, `src/lib/busy.ts`, `src/hooks/useUpdates.ts`, `src/components/UpdatesPanel.tsx`, `apps/gateway/gateway/updates.py`, `apps/gateway/tests/test_updates.py`.

Плюс известный drift `docs/screenshots/0.4/*.png` — **не коммитить и не откатывать**, это исторический шум.

---

## 6. Честные ограничения (не скрываю)

- **Windows Authenticode: NOT SIGNED.** Сертификата нет → SmartScreen будет предупреждать. Updater-подпись — другое и не заменяет Authenticode.
- **Updater-ключ сейчас тестовый**, не production. Production-пара должна быть создана и храниться вне репозитория; в релизных заметках указывать публичный ключ, никогда приватный.
- **Production manifest не публиковался** и endpoint пока отвечает «обновлений нет» (204). Это правильно: манифест публикуется последним, после проверки артефакта.
- **Tor зависит от внешнего runtime** — требование self-contained не выполнено.
- **Ubuntu: ни одного пакета не собрано**, приёмки нет.
- **Автозапуск при входе в Windows** не реализован.
- Открытые хвосты: WM-07 (TinyFish Browser live lifecycle) и CD-08 (REAL stale-SHA) — открыты, классификация «FIX NOW / DOCUMENTED» ещё не оформлена; sticky-подпись `generating`; один draft сверх окна контекста; лаг телеметрии расхода провайдера; Web-чип показывает `configured` (дешёвого health-probe нет — семантика остаётся честной); `client_version` на Gateway — метаданные, не security authority.
- Живая GPU-сертификация 1.2.0 не проводилась и для перечисленного выше не требуется.

---

## 7. Итоговая оценка по трём промптам

| Блок | Статус |
|---|---|
| Computer always-ready (health, heartbeat, pairing, один host, авто-recovery) | **PASS** на установленной 1.1.0 (гейт 21/21, crash-recovery подтверждён) |
| Tor always-ready (обнаружение, circuit proof, авто-recovery, fail-closed) | **PASS по логике**, но с внешней зависимостью runtime → **не PASS по требованию self-contained** |
| Crash recovery backend/sidecar | **PASS** (Rust supervisor + детерминированный тест + installed проверка) |
| Auto Update — клиент и Gateway-endpoint | **Реализовано, тесты зелёные, установленно не проверено, не закоммичено** |
| Bundled Tor | **НЕ СДЕЛАНО** |
| Windows self-contained приёмка | **НЕ СДЕЛАНО** |
| Ubuntu сборка и приёмка | **НЕ СДЕЛАНО** |
| Автозапуск при входе | **НЕ СДЕЛАНО** |
| Версия 1.2.0 / финальная сборка / hashes / manifest | **НЕ СДЕЛАНО** (версия в дереве 1.1.0; `v1.2.0` свободен) |

**Вердикт текущего состояния:** `RELEASE BLOCKED — WORK IN PROGRESS` (не финальный вердикт; блокер crash recovery закрыт, остаются перечисленные пункты). Merge в `main`, tag и публикация манифеста — только после их закрытия.

---

## 8. Порядок дальнейших шагов (по одному, с проверкой)

1. Зафиксировать Auto Update в коммитах (`feat(gateway)` + `feat(desktop)` + `test`), не смешивая с другими правками.
2. Bundled Tor runtime для Windows (pinned, SHA256, NOTICE) + привязка `TorService` к нему + тесты; затем — Linux-вариант.
3. Автозапуск при входе (Windows и Linux) + toggle в Settings + удаление при uninstall.
4. Ubuntu: сборка `.deb`/AppImage, secret storage, XDG-autostart, приёмка на чистой VM.
5. Bump 1.1.0 → 1.2.0 во всех местах + docs.
6. Полный бесплатный гейт (§21) одной сессией.
7. **Одна** финальная сборка → clean install + upgrade (1.1.0 → 1.2.x) → installed приёмка (Computer, Tor, updater на локальном фикстуре) → полные SHA256 → подпись артефактов.
8. Документация релиза, merge → `main`, tag `v1.2.0`, и только затем — production manifest.
