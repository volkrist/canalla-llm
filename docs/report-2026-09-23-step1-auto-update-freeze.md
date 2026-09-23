# CANALLA LLM 1.2.0 — ОТЧЁТ: STEP 1/5 — FREEZE AND CERTIFY EXISTING AUTO UPDATE WORK

**Дата:** 23 сентября 2026
**Репозиторий:** `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`
**Ветка:** `release/canalla-1.1.0`
**HEAD до этапа:** `f846b11` — `fix(desktop): the supervisor owns the sidecar lifecycle`
**HEAD после этапа:** `16633af` — `test(updater): cover update validation and failure modes`
**`main` / `origin/main`:** `9bd7548` (release-работа **не влита**)
**Теги:** только `v1.0.0` (`v1.1.0` и `v1.2.0` не существуют → итоговая версия остаётся **1.2.0**)
**Версия продукта:** `1.1.0` — **не менялась** (шаг 7 это запрещает)
**Вердикт этапа:** **PASS** (одна явная оговорка: установочная приёмка updater'а вынесена в следующий шаг, там нужна новая сборка)

---

## 1. Задача этапа (что требовалось)

1. Защитить существующую незакоммиченную работу Auto Update (desktop + gateway), ничего не потерять.
2. Проверить **фактическую** реализацию, а не «компилируется значит правильно».
3. Проверить безопасность подписи: приватный ключ только вне репозитория, без утечек в git/логи/installer/frontend/Gateway/docs.
4. Прогнать unit/static тесты: Gateway, frontend, Rust, BasedPyright.
5. Создать self-contained локальную фикстуру обновлений и проверить матрицу из 13 кейсов (включая 404/500/timeout/offline).
6. Закоммитить updater отдельными логичными коммитами, не смешивая с Tor/autostart/Linux.
7. **Не** поднимать версию, **не** ставить тег, **не** вливать в `main`, **не** публиковать production manifest.
8. Дать короткий отчёт в заданном формате.

Всё перечисленное выполнено; по ходу ревью найден и исправлен реальный дефект интеропа (раздел 4).

---

## 2. Защита работы (шаг 1 промпта)

Зафиксировано до начала правок: `git status`, `git diff`, `git diff --stat`. Подтверждён полный набор файлов updater'а — **ни один не потерян**:

**Gateway (4 + 1 новый):** `gateway/config.py`, `gateway/routes.py`, `gateway/updates.py` (новый), `tests/test_updates.py` (новый), `tests/fixtures/updates-response-1.2.0.json` (новый, контрактная фикстура).

**Desktop (17 изменённых + 4 новых):** `package.json`, `package-lock.json`, `src-tauri/Cargo.toml`, `src-tauri/Cargo.lock`, `src-tauri/src/main.rs`, `src-tauri/capabilities/default.json`, `src-tauri/tauri.conf.json`, `src/App.tsx`, `src/components/SettingsDialog.tsx`, `src/components/Workspace.tsx`, `src/components/BackupPanel.tsx`, `src/lib/settings.ts`, `src/types.ts`, `src/lib/settings-nav.test.tsx`, `src/lib/backup.test.tsx`, `src/lib/cloud.test.tsx`, `.gitignore`; новые: `src/lib/updates.ts`, `src/hooks/useUpdates.ts`, `src/components/UpdatesPanel.tsx`, `src/lib/busy.ts`.

**Не тронуто:** `docs/screenshots/0.4/*.png` (исторический drift — остаётся как есть, не коммитится и не откатывается).

---

## 3. Ревью реализации (шаг 2 промпта)

Проверялось по факту, а не по факту компиляции.

| Что проверено | Где | Как доказано |
|---|---|---|
| Плагин обновлений и плагин перезапуска зарегистрированы | `src-tauri/src/main.rs` | `.plugin(tauri_plugin_updater::Builder::new().build())`, `.plugin(tauri_plugin_process::init())` |
| Права (capabilities) выданы ровно нужные | `capabilities/default.json` | `updater:default`, `process:allow-restart` |
| Артефакты обновления собираются | `tauri.conf.json` | `bundle.createUpdaterArtifacts: true` |
| Публичный ключ вшит в клиент | `tauri.conf.json` → `plugins.updater.pubkey` | декодирование base64 → `untrusted comment: minisign public key: B79FF90B52D00F24` + совпадение с тестовым `.pub` |
| Endpoint и его параметры | `tauri.conf.json` | `https://gateway.12testers.store/updates/latest?target={{target}}&arch={{arch}}&current_version={{current_version}}` |
| Режим установки | `tauri.conf.json` | `windows.installMode: "passive"` (NSIS без интерактива) |
| Раздел «Обновления» в Settings | `SettingsDialog.tsx`, `settings-nav.test.tsx` | секция в группе «Система», присутствует в EXPECTED навигации |
| Автоматическая проверка | `hooks/useUpdates.ts`, `App.tsx` | один владелец таймера на приложение, интервал 4 ч, включается настройкой `autoCheckUpdates` (по умолчанию `true`), первый чек после монтирования |
| Прогресс загрузки | `lib/updates.ts`, `UpdatesPanel.tsx` | события `Started`/`Progress`/`Finished` → проценты, статус `downloading` |
| «Перезапустить и обновить» | `lib/updates.ts` | `pending.install()` → `relaunch()`; пакет берётся из сохранённого handle, не из второго чека |
| Установка не убивает активную работу | `lib/busy.ts`, `Workspace.tsx`, `BackupPanel.tsx`, `UpdatesPanel.tsx` | реестр busy: `generation`/`local-task`/`backup`/`restore`; кнопка disabled + отказ в `installDecision` |
| Никакого блока запуска | `hooks/useUpdates.ts`, `lib/updates.ts` | проверка асинхронная, ошибка — только сообщение в состоянии |
| Gateway отдаёт манифест | `gateway/routes.py:119` | `GET /updates/latest` → 200 с телом, 204 «нет обновлений», 503 «манифест отвергнут» |
| Фильтр платформы/архитектуры | `gateway/updates.py` | поддерживаются `windows-x86_64`, `linux-x86_64`; всё остальное → 204, никогда чужой артефакт |
| Запрет downgrade | `gateway/updates.py` | `is_newer(version, current)`; равная/старшая у клиента → 204 |
| Запрет неподписанного | `gateway/updates.py` | нет/битая подпись → 503 `updates_manifest_entry_incomplete:<platform>` |
| Запрет приватных данных | `gateway/updates.py` | ключи вида `secret/api_key/token/password/private_key/credential` → 503 `updates_manifest_not_public:…` |
| Формат ответа читается **родным** типом клиента | Rust-гейт | `RemoteRelease` (публичный тип `tauri-plugin-updater`) парсит зафиксированное тело Gateway |

---

## 4. Дефекты, найденные ревью, и их исправления

1. **`pub_date` ломал бы каждое обновление.** Клиент (`tauri-plugin-updater`) читает `pub_date` как RFC 3339: `OffsetDateTime::parse(..)`; при непустой, но неразбираемой строке весь ответ отвергается («Could not fetch a valid release JSON from the remote»). Gateway же отдавал `""`, если даты в манифесте нет. Теперь сервер **валидирует** дату и **опускает** поле, если её нет. Регрессионные тесты: `test_a_manifest_without_a_pub_date_is_served_without_one`, `test_a_pub_date_the_client_cannot_parse_is_refused` (Python) и Rust-тест `an_empty_pub_date_would_not_parse_which_is_why_the_endpoint_omits_it`.
2. **Подпись «валидная» лишь по подстроке.** В первой версии проверки сервер искал `trusted comment:` в декодированном тексте подписи — а эта подстрока входит внутрь `untrusted comment:`, поэтому усечённая подпись проходила как «валидная». Исправлено: обязательна строка `untrusted comment:` в начале и `\ntrusted comment:` со начала строки. Тест `test_a_signature_that_is_not_a_minisign_file_is_refused` покрывает четыре формы брака (путь файла вместо содержимого, обрезанная строка, sha256 вместо подписи, комментарий без комментария).
3. **URL и digest не проверялись.** Клиент сам отказывается от небезопасного протокола, а `Url`-парсер отвергает относительный путь. Теперь Gateway требует `https://` и валидный `sha256` (64 hex), если он указан: сломанный манифест отвергается на сервере, а не «публикуется сломанным».
4. **Мёртвый код в UI.** Слушатель события `alex-open-updates` в `SettingsDialog.tsx` никем не отправлялся (индикатора обновления в шапке нет). Удалён; при добавлении индикатора слушатель вернётся вместе с отправителем.
5. **Отложенная установка была правдой только для генерации.** Кнопка и логика ссылались на backup/restore, но метку `busy` ставила только генерация. Теперь `BackupPanel.act()` помечает `backup` (создание/проверка) и `restore` (восстановление), поэтому утверждение «установка ждёт безопасной точки» верно для всех перечисленных операций.

---

## 5. Безопасность подписи (шаг 3 промпта)

**Приватный ключ находится только вне репозитория:**
`C:\Users\Volkr\.canalla-updater\canalla-updater-test.key` (+ `.key.pub`), создан `23.09 18:00`, без пароля, тестовый.

Что проверено фактически:

| Проверка | Результат |
|---|---|
| `*.key` / `*.key.pub` под репозиторием (без `node_modules`) | **нет ни одного файла** |
| Маркер приватного ключа (`minisign secret`, `BEGIN PRIVATE`) в отслеживаемых файлах | **не найден** |
| Маркер в собранном клиенте `alex-llm.exe` | **0 совпадений** |
| Маркер в установщике `Canalla LLM_1.1.0_x64-setup.exe` | **0 совпадений** |
| `.gitignore` | добавлены `*.key`, `*.key.pub` (страховка от случайного коммита) |

**Что лежит в репозитории (только публичное):**

- публичный ключ в `tauri.conf.json` (base64 от minisign `.pub`);
- фикстура пакета `tests/fixtures/updater/canalla-update-fixture.bin` (269 байт, не установщик);
- её подпись `…bin.sig` (416 байт, base64 — ровно то, что уходит в поле `signature` манифеста);
- `sha256` фикстуры: `0e242fb9b6507939812857aa514e4fd0e45042b53dc4211376fcbc245814a66c`.

**Декодированное содержимое фикстуры подписи** (создана `npx tauri signer sign` тестовым ключом):

```
untrusted comment: signature from tauri secret key
RUQkD9BSC/mft7VVkubCGEBOgi1mIcQXyGykITYbFHTviVnmBg//k0fQ3J0lwg1guBZsuJxLhdpwIwPLmODDbC6UiBiiwCGTDAI=
trusted comment: timestamp:1790156158	file:canalla-update-fixture.bin
KL+8Fd3e0IXq6szgK4ZdNrk8YTp/IsxtKuWq0YZM4/JdABmUuTmI5zPinLPQPBAEoPKb+ZRiMT6XEY4osQveBw==
```

Ключевой id подписи совпадает с ключом из `tauri.conf.json` — именно поэтому приём проходит (и именно это проверяет Rust-гейт).

**Операционная заметка (и урок прошлого прогона):** `npx tauri signer sign` **спрашивает пароль интерактивно**, если не передать `-p`. Первый запуск подвис на приглашении `Password:` и был снят таймаутом. Правильная форма:

```
npx tauri signer sign -f "C:/Users/Volkr/.canalla-updater/canalla-updater-test.key" -p "" tests/fixtures/updater/canalla-update-fixture.bin < /dev/null
```

---

## 6. Тестовый гейт (шаг 4 промпта)

| Гейт | Результат |
|---|---|
| Gateway `pytest` (полный) | **148 passed** (до этапа было 130; файл обновлений — **18 passed**) |
| Gateway `ruff check` | **All checks passed** |
| Gateway `ruff format --check` | **29 files already formatted** |
| Frontend `vitest run` (полный) | **188 passed** (17 файлов; `updates.test.ts` — **19**) |
| Frontend `tsc -b` | **чисто** |
| Frontend Prettier (`npm run format:check`) | **All matched files use Prettier code style** |
| Frontend `vite build` | **OK** (production-бандл собран) |
| Rust `cargo test` | **82 passed**: host 18 + desktop 52 + `updater_signature` **12** |
| BasedPyright (весь репозиторий) | **0 errors, 0 warnings, 0 notes** |
| Backend `pytest` (не менялся, перепроверен) | **606 passed, 1 skipped**; ruff check/format PASS (180 файлов) |

Тесты не ослаблялись: единственная правка существующего assert — уточнение (`"signature" in detail` → точное `updates_manifest_entry_incomplete:windows-x86_64`).

---

## 7. Локальная фикстура обновлений и матрица (шаг 5 промпта)

Фикстура состоит из трёх частей и полностью самодостаточна (сеть и GPU не нужны):

1. **Пакет и подпись** — `apps/desktop/src-tauri/tests/fixtures/updater/…bin(.sig)`, подписаны тестовым ключом.
2. **Тело ответа Gateway** — `apps/gateway/tests/fixtures/updates-response-1.2.0.json`, сгенерировано **самим кодом Gateway** (не написано руками), содержит реальную подпись и реальный sha256 фикстуры; Python-тест утверждает равенство с ответом сервера, Rust-тест парсит те же байты `RemoteRelease`.
3. **Манифест** — собирается в тестах Gateway (файл или inline JSON), в том числе с браком.

| № | Кейс | Где доказан | Результат |
|---|---|---|---|
| 1 | same version → no update | Gateway 204 + клиент «none» | PASS |
| 2 | newer version → available | Gateway 200 + клиент «available» + парсинг `RemoteRelease` | PASS |
| 3 | older version → reject | Gateway 204 (сервер) + клиент игнорирует non-newer | PASS |
| 4 | bad signature → reject | Rust: изменённый байт / другой ключ / чужая подпись | PASS |
| 5 | missing signature → reject | Gateway 503 + Rust (пустая и «не base64» подпись) | PASS |
| 6 | bad hash → reject | Gateway 503 на неверный `sha256`; целостность пакета обеспечивает подпись (Rust: обрезанный/изменённый пакет) | PASS |
| 7 | wrong platform → reject | Gateway 204 (`darwin-x86_64`); Rust: `download_url("darwin-aarch64")` → ошибка | PASS |
| 8 | wrong arch → reject | Gateway 204 (`windows-aarch64`, `linux-aarch64`, пустой arch) | PASS |
| 9 | invalid JSON → fail safely | Gateway 503 `updates_manifest_unreadable`; клиент → состояние «error», приложение работает | PASS |
| 10 | 404 → app continues | клиентский слой: реальный текст плагина → нейтральное сообщение, запуск не блокируется | PASS |
| 11 | 500 → app continues | то же | PASS |
| 12 | timeout → app continues | «operation timed out» → «Сервер обновлений недоступен. Canalla продолжает работать.» | PASS |
| 13 | offline → app continues | «dns error» / «network is unreachable» → то же сообщение | PASS |
| — | interrupted download | та же ветка `downloadUpdate` → `ok:false`, версия не меняется | PASS |

**Границы честности этой матрицы.** Кейсы 1–9 доказаны на обеих сторонах (сервер и клиентская политика) и, для подписи, криптографически. Кейсы 10–13 доказаны на **клиентском слое политики** с реальными текстами ошибок плагина — сам сетевой транспорт плагина и установленный клиент не проверялись: установленное приложение собрано **до** появления плагина. Это первый пункт следующего шага и он не скрывается.

---

## 8. Коммиты (шаг 6 промпта)

| Коммит | Заголовок | Содержимое |
|---|---|---|
| `5648751` | `feat(gateway): serve signed update metadata` | `gateway/updates.py`, `config.py`, `routes.py`, `tests/test_updates.py`, `tests/fixtures/updates-response-1.2.0.json` (5 файлов, +508/−1) |
| `005f13f` | `feat(desktop): add a secure application updater` | плагины, конфиг Tauri, права, `main.rs`, `App.tsx`, `SettingsDialog.tsx`, `Workspace.tsx`, `BackupPanel.tsx`, `UpdatesPanel.tsx`, `useUpdates.ts`, `busy.ts`, `updates.ts`, настройки/типы, обновления тестов-фикстур, `.gitignore` (21 файл, +945/−10) |
| `16633af` | `test(updater): cover update validation and failure modes` | `src/lib/updates.test.ts`, `src-tauri/tests/updater_signature.rs`, фикстуры подписи (4 файла, +501) |

Tor/autostart/Linux-работа в эти коммиты не попадала. Одна честная оговорка по структуре: `[dev-dependencies]` (`base64`, `minisign-verify`) лежат в коммите `005f13f`, потому что делить `Cargo.toml` по строкам между коммитами — лишний риск; на продукт они не влияют.

---

## 9. Что сознательно не делалось (шаг 7 промпта) и состояние дерева

- Версия **не** поднималась: `1.1.0` во всех местах (Tauri, Cargo, package.json, backend, Gateway, product.py).
- Тег **не** создавался; `v1.0.0` не тронут; `v1.1.0`/`v1.2.0` по-прежнему не существуют.
- Merge в `main` **не** делался: `main` = `origin/main` = `9bd7548`, ветка впереди.
- Production manifest **не публиковался**: endpoint отвечает 204 «обновлений нет».

Состояние рабочего дерева после этапа: чисто, кроме двух задокументированных пунктов — исторический drift `docs/screenshots/0.4/*.png` (11 файлов, не коммитить/не откатывать) и отчёт предыдущего шага `docs/report-2026-09-23-last-3-prompts.md` (untracked, создан по запросу).

---

## 10. Остаточные блокеры и следующий шаг

1. **Установленная сборка с updater'ом не собрана и не установлена.** Единственная сборка, где updater реально работает, ещё не существует; приёмочный прогон (check → download → verify → «Перезапустить и обновить» → новая версия → данные сохранены) требует одной чистой сборки и установки.
2. **Production-пары ключей нет.** В клиент вшит тестовый публичный ключ, значит любой публикуемый артефакт должен быть подписан этим же ключом; создание production-пары и её хранение — решение релизного шага.
3. **Хранилище артефактов не готово.** URL в контрактной фикстуре (`…/downloads/Canalla%20LLM_1.2.0_x64-setup.exe`) пока ничего не отдаёт: нужен nginx-location и сама выгрузка.
4. **Артефакты `.sig` и их манифест не вписаны в релизную процедуру** (порядок: сборка → подпись → хэши → проверка → выгрузка → проверка скачивания → манифест последним).
5. **Хвост про `requireSignedVersion`.** Подпись несёт `timestamp` и `file:` в trusted comment, но не версию, поэтому защита «объявленная версия = подписанная версия» выключена (по умолчанию). Включать её имеет смысл только вместе с проверкой, что артефакт подписывается так, что версия извлекается.
6. Вне этого этапа остаются: bundled Tor, Ubuntu-пакет и приёмка, автозапуск при входе, bump 1.2.0, финальная сборка и хэши, публикация манифеста.

---

## 11. Как повторить этот гейт

```bash
# Gateway
cd apps/gateway
../backend/.venv/Scripts/python.exe -m pytest -q
../backend/.venv/Scripts/python.exe -m ruff check .
../backend/.venv/Scripts/python.exe -m ruff format --check .

# Frontend
cd apps/desktop
npx vitest run
npx tsc -b
npm run format:check
npx vite build

# Rust (включая криптографию обновлений)
cd apps/desktop/src-tauri
/c/Users/Volkr/.cargo/bin/cargo.exe test

# Типы Python
cd ../../..
npx basedpyright

# Пересоздать фикстуру подписи (только тестовым ключом, пароль пустой, stdin закрыт)
cd apps/desktop/src-tauri
npx tauri signer sign -f "C:/Users/Volkr/.canalla-updater/canalla-updater-test.key" -p "" \
  tests/fixtures/updater/canalla-update-fixture.bin < /dev/null
```

---

## 12. Итог

```
HEAD before:  f846b11
HEAD after:   16633af
commits:      5648751, 005f13f, 16633af
files:        30 (gateway 5, desktop 21, tests 4)

Gateway tests:      148 passed (+ ruff, + format)
Frontend tests:     188 passed (+ tsc, + Prettier, + vite build)
Rust:               82 passed (18 + 52 + 12)
BasedPyright:       0 errors / 0 warnings / 0 notes

private key absent from repo:   YES (ключ вне репозитория, утечек не найдено, .gitignore усилен)
local updater matrix:           PASS (13/13 на сервере и в клиентской политике; подпись — криптографически)
production manifest:            NOT PUBLISHED
version bump / tag / merge:     не выполнены (как требует шаг 7)

remaining blockers:  установочная приёмка updater'а (нужна сборка),
                     production-ключ, хост артефактов, релизная процедура подписи,
                     requireSignedVersion, Tor/Ubuntu/autostart/bump/manifest
FINAL: PASS
```

Оговорка, которая не прячется: «PASS» относится к сохранению, ревью, тестовому покрытию и коммиту существующей реализации. Работа updater'а на **установленном** клиенте ещё не доказана — это следующий шаг, и он начинается с одной чистой сборки и установки.
