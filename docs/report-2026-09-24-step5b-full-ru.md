# CANALLA LLM 1.2.0 — ПОЛНЫЙ ОТЧЁТ ПО ПОСЛЕДНИМ ПРОМПТАМ

**Итог: `READY FOR FINAL PUBLISH`.**

Windows x86_64 = **PRODUCTION**. Ubuntu 24.04 LTS x86_64 = **PREVIEW / EXPERIMENTAL**
(приёмка desktop-сессии сознательно отложена и нигде в этом отчёте не заявлена).

| | |
|---|---|
| Ветка | `release/canalla-1.1.0` |
| HEAD | `8d9224bc000d078652ab710f0f6ff62c61bbc766` |
| `main` | `9bd75486c34b64ab4435afb4efc9fa9b17104cfd` — **не тронут** |
| Теги | только `v1.0.0` (тег `v1.2.0` свободен и не создан) |
| Коммитов в этом цикле | **21** (от `2f72c46` до `8d9224b`) |
| Релизный артефакт | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` |
| Production-ключ updater | `9B328EFF111D1FB2` |
| Gateway prod | `https://gateway.12testers.store` → `releases/6e2eca83e0c2`, v1.2.0 |
| Манифест обновлений | **НЕ ОПУБЛИКОВАН** (`/updates/latest` → 204) |

---

# ЧАСТЬ I. ЧТО СДЕЛАНО В ЭТОМ ЦИКЛЕ

## 1.1 Хронология коммитов

| Коммит | Что |
|---|---|
| `4cfef05` | Зафиксирован статус релиза 1.2.0 и его единственный блокер |
| `95fc5cc` | **Production-идентичность подписи updater** (STEP 5A) |
| `c98c84b` | Приёмка production-идентичности |
| `1545d68` | RC зафиксирован как заблокированный |
| `1a3d559` | Найден дефект stale backend в кандидате |
| `eea4de7` | **Guard против устаревшего sidecar** (`BACKEND_SIDECAR_STALE`) |
| `87c80d2` | Заменяющий кандидат |
| `5833241` | Два finding'а по AI-статусу |
| `21af8a1` | **Finding A (Gateway) + Finding B** — ограниченный поиск и авто-запуск AI из чата |
| `083ffb0` | Закрытый RC-статус |
| `c7b7433` | **Allocator: обход набора кандидатов вместо ожидания одного GPU** |
| `67280b6` | `verify_sidecar_standalone.py` читает версию из `product.py`, а не пинит 0.9.3 |
| `ecff95e` | Новый gate: **подпись реально собранного установщика** production-ключом |
| `e1824e4` | Приёмка установленного 1.2.0 + доказательство сохранности данных |
| `6e2eca8` | Guard аттестует **свой** бинарь платформы; Linux-приёмка проверяет версию |
| `bacbe75` | **Finding A (direct-путь)**: запаркованный retry — не операция in flight |
| `3ec25dd` | Live-acceptance natural-language Tor на установленном продукте |
| `cd4fb8f` | **Завершённый walk закрывает turn**, а не ждёт железо 900 с |
| `d2c107a` | Updater-проверка сверяется с 204, а не с прежним 404 |
| `f1dab33` | Harness читает `/llm/status` (источник чипа) и измеряет длительность turn |
| `8d9224b` | Отчёт о закрытии STEP 5B и двух найденных дефектах |

## 1.2 Порядок работы в этом шаге

1. Проверка состояния репозитория (HEAD, ветка, дерево, теги).
2. **Пересборка backend sidecar** из текущего дерева 1.2.0 (guard корректно отказал старому).
3. Доказательство нового sidecar: `/health` = 1.2.0, `network_route`-контракт присутствует,
   **stripped PATH**, без Python и без репозитория.
4. Полный набор детерминированных гейтов.
5. **Одна** заменяющая production-сборка.
6. Установка поверх реального 1.2.0 + приёмка (GUI, always-ready, сохранность данных).
7. Gateway: precheck → preflight → деплой 1.2.0 → верификация.
8. Хостинг артефакта + download-back.
9. Матрица updater (live, кодом развёрнутого Gateway).
10. Linux preview regression (WSL).
11. Live-санитария: одна попытка capacity, замер, natural-language Tor.
12. **Найдены и исправлены два дефекта** → пересборка sidecar → **ещё одна** заменяющая сборка →
    повторная приёмка → публикация финального артефакта.
13. Отчёт.

---

# ЧАСТЬ II. НАЙДЕННЫЕ И ИСПРАВЛЕННЫЕ ДЕФЕКТЫ

Всего в цикле найдено и устранено **шесть** дефектов. Ни один не был «замазан» на уровне подписи
или UI — каждый исправлен в источнике.

## Дефект 1 — в установщик 1.2.0 попадал backend из дерева 1.1.0

**Как найден:** установщик заявлял 1.2.0, а его встроенный backend отвечал `/health` → `1.1.0`
(`a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944`), собранный 23 сентября —
до бампа версии и до работы этого релиза над Tor-маршрутизацией.

**Причина:** шаг бандла проверял, что sidecar **существует**, и никогда — откуда он взялся.

**Исправление (`eea4de7`, `6e2eca8`):** `scripts/backend-sidecar-stamp.py` пишет рядом с упакованным
backend `build-stamp.json` (`product_version` из `app/product.py`, `source_digest` по содержимому
137 файлов с нормализацией CRLF→LF, SHA256 самого бинаря), а хук бандла (`stage-native-runtime.ps1`
на Windows, `stage-native-runtime.sh` на Linux) **падает** с `BACKEND_SIDECAR_STALE` вместо
предупреждения. Позже (`6e2eca8`) guard усилен: он аттестует **тот** бинарь, который записан в
stamp, а не первый по приоритету `.exe`, — иначе дерево с бинарями обеих платформ могло бы
проверить чужой.

**Доказательство:** в реальной сборке напечатано `SIDECAR stamp is current`; установленный продукт
несёт stamp с `source_digest`, совпадающим с текущим деревом.

## Дефект 2 — Finding A: бесконечное «Ищем GPU» на Gateway

**Как найден:** по сообщению оператора (`compute/status` возвращал `searching` >20 минут без
активной сессии).

**Причина:** Gateway перештамповывал сохранённый `gpu_unavailable` в `searching` на каждом
reconcile, без операции за этим состоянием.

**Исправление (`21af8a1`):** поиск стал операцией с идентичностью и дедлайном
(`COMPUTE_SEARCH_TIMEOUT_SECONDS`, серверный, жёстко ≤60 с); истёкший или безымянный поиск
схлопывается в `offline` с типизированной причиной (`collect_expired_search` в `tick` и та же
деривация в `status_payload`). Клиент отказывается ретранслировать `searching` без атрибуции.

**Доказательство на продакшне после деплоя:**
`compute: state=offline error=gpu_unavailable session=- create_attempts=0` — Pod'ов не создавалось,
денег не потрачено.

## Дефект 3 — Finding B: чат не запускал нужный ему compute

**Как найден:** в shared-режиме чат вызывал жизненный цикл, который отказывает с
`gateway_managed_compute`, поэтому `POST /compute/ensure` не доходил до Gateway.

**Исправление (`21af8a1`):** один single-flight жизненный цикл `app/cloud/demand.py::SharedDemand`,
общий для чата, `ensure` и кнопки предпрогрева; исходное сообщение исполняется ровно один раз;
эндпоинт модели не опрашивается до готовности. Кнопка «Запустить AI» стала **необязательным**
предпрогревом на том же жизненном цикле.

## Дефект 4 — allocator ждал один слот GPU

**Как найден:** реальные наблюдения — час в поиске GPU без результата.

**Исправление (`c7b7433`):** единая политика `apps/backend/app/compute/candidates.py`,
импортируемая и Gateway'ем. Кандидат = `(GPU, cloud tier, placement)`, где placement =
`(datacenter, Network Volume)`. Порядок: tier → самый дешёвый совместимый → датацентр самого Volume.
60 секунд — **общий** бюджет (не на кандидата), вызов провайдера ограничен
`min(15 с, остаток)`, максимум 3 кандидата, отказ (400/409/404/422/403) → немедленно следующий,
неоднозначный ответ → `create_unknown` и никогда второй create. Community Cloud только при
совпадении серверной политики, объявленного placement'а и пользовательской настройки.

## Дефект 5 — Finding A (вторая реализация): запаркованный retry выдавался за живую операцию

**Как найден:** live-прогон natural-language Tor на **установленном** продукте.

**Причина:** `RunPodController.llm_public_status` ставил `search_active = True` всегда, когда retry
был **запланирован** (`next_search_at`), а `compact_ai` читает ровно пару
`(searching, search_active, gpu_unavailable)` как настоящий переход. Значит фоновый зонд обещал
движение столько, сколько переназначался.

**Исправление (`bacbe75`)** — сознательно **без** изменения стейт-машины: `searching` —
задокументированный смысл настройки `auto_search` («retry GPU search»), и 6 существующих тестов
закрепляют это поведение. Исправлена именно **ложь**: `search_active` теперь означает только то,
что написано в его имени. Retry остаётся на часах, `search_deadline` по-прежнему сообщается, но чип
красный с типизированной причиной до момента, когда начнётся реальное размещение.

**Доказательство, прочитанное live с установленной сборки:**
```
llm/status → {"state":"starting","ai":"unavailable","ai_label":"AI Unavailable",
              "diagnostic":{"compute_state":"searching","search_active":false,
                            "search_deadline":"2026-09-24T17:11:41Z","last_error":"price_limit"}}
PASS a parked search is reported as no operation in flight  [compute_state=searching search_active=False]
PASS and the chip therefore does not claim a transition    [ai=unavailable label=AI Unavailable]
```
Плюс детерминированный тест, который **выводит чип из собственных значений контроллера**, так что
эти двое больше не могут разойтись.

## Дефект 6 — завершённый поиск перезапускался 937 секунд

**Как найден:** тем же live-прогоном, после добавления замера длительности turn.

**Замер до:** модель-требующий чат в direct-режиме ждал **937.1 с** и только потом отвечал
типизированным `startup_timeout`. При этом `compute/status` показывал `session=null`,
`create_attempts=0` и уже завершённый walk с `price_limit`.

**Причина:** `wait_for_production` считал любой `unavailable` временной неудачей и крутился до
дедлайна, производного от `runpod_startup_timeout` (**900 с** по умолчанию), выполняя **новый**
ограниченный поиск на каждом проходе.

**Исправление (`cd4fb8f`):** `SEARCH_FAILURES` — это собственное имя продукта для «поиск завершён, и
ожидание ничего не изменит» (`no_compatible_gpu`, `price_limit`, и `gpu_unavailable`, когда живой
операции, ограничивающей его, уже нет). Turn теперь закрывается на первом же с типизированным кодом —
ровно как это уже делал shared-путь.

**Замер после:**
```
the turn took 4.8s before it answered
event: progress data: {"state": "error", "text": "AI Unavailable", "code": "price_limit"}
event: done data: {"parked": true, ...}
```
**937.1 с → 4.8 с.** Тест проверяет «один walk, один park, типизированный код» и без фикса упирается
в `wait_for(timeout=30)`.

---

# ЧАСТЬ III. ФИНАЛЬНЫЙ АРТЕФАКТ И ВСЯ ЛИНИЯ АРТЕФАКТОВ

## 3.1 Финальный кандидат

| | |
|---|---|
| Установщик | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` |
| Размер | 92 461 790 B |
| Подпись | `7e955a525899d3733d762dc783c89492e2a1c1d5e925d265c3ca875c243872fc` |
| Production-ключ | `9B328EFF111D1FB2` |
| Backend sidecar | `2d46fbd85b53d3ec0d16ce64618082e7c46ccd409c03ea886a7215cf9c7e1c6e` |
| Backend source digest | `cdef75efc753a32c48ff92fc09e732d681af1120d805778f4d509692734b049` (137 файлов) |
| `/health` | `{"product":"alex-llm","version":"1.2.0","llm_ready":false}` |
| Stale-sidecar guard | **PASS** (exit 0), и он же отработал **внутри** сборки |

Подпись проверена независимо **новым gate** `tests/updater_signature.rs::the_built_release_artifact_is_signed_by_the_production_identity`:
он читает **фактически собранный** установщик (не фикстуру), принимает его production-ключом,
**отвергает** тестовой идентичностью, проверяет, что подписанное имя по-прежнему несёт `1.2.0`, и
требует нижнюю границу размера, чтобы подпись над «пустышкой» не прошла.

## 3.2 Линия установщиков — публиковать можно только последний

| Установщик SHA256 | Статус |
|---|---|
| `e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a` | **REJECTED** — backend заморожен на 1.1.0 |
| `123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba` | superseded |
| `dbc603388602fb2be91b0ead6b223338ff85755d0fe4372a118c1b248f2ee983` | superseded |
| `2a6383ca3a43f3eedecbf4af0960b9a140581f726777a9a51da57d53e9428c64` | superseded (кратко был на хостинге для download-back, затем удалён) |
| `555db429e50e880f50be81cb8e523575bc3b57310956532ea06530b438ea22bb` | superseded (на хостинг не попадал) |
| **`9383aa29…26b24e`** | **ФИНАЛЬНЫЙ — опубликован** |

Три вытесненных установщика лежат в карантине **вне репозитория**
(`C:\Users\Volkr\.canalla-updater\superseded\`), чтобы их нельзя было опубликовать случайно.

## 3.3 Линия backend sidecar

| SHA256 | Статус |
|---|---|
| `a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944` | устаревший (1.1.0), отвергнут guard'ом |
| `e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd` | superseded |
| `cd0ddd144ceb7e80af05268b1b98e8c9c75978e0cdcbdf1e753682bb6adc0b0d` | superseded |
| `d08cd5b7bd1ec39fdec4c02fde69f2af68b78be5ea120954a70f5a9b5eb95b10` | superseded |
| `911914aa00110515e54697b0f3b4f67528745ee3e1727dafba0b8c3ab6540000` | superseded |
| **`2d46fbd8…9c7e1c6e`** | **установлен в финальном продукте** |

---

# ЧАСТЬ IV. ГЕЙТЫ НА ФИНАЛЬНОЙ РЕВИЗИИ

| Гейт | Результат |
|---|---|
| Backend `pytest` | **719 passed, 1 skipped, 0 failed** |
| Backend `ruff check` / `format --check` | clean / 188 files formatted |
| Gateway `pytest` / `ruff` | **187 passed** / clean |
| Frontend `vitest` / `tsc` / `prettier` | **255 passed** (21 файл) / clean / clean |
| Rust `cargo test --workspace` | **95 passed** (21 + 58 + 16) |
| Version consistency | **10 passed** |
| Sidecar stamp + guard | **11 passed**, guard exit 0 |
| Sidecar standalone proof | **PASS** (`/health` 1.2.0, `network_route` есть, без Python, без репо, stripped PATH) |
| Linux preview | `cargo check`/`test` **103 passed**, backend **710 passed / 6 skipped**, `.deb` 1.2.0 через хук, Tor acceptance **9/9** |
| Installed GUI acceptance | **31/31 PASS** (`apps/desktop/e2e/release-1.2.0.mjs`) |
| Installed always-ready | **PASS** (`apps/desktop/e2e/always-ready.mjs`) |
| Natural-language Tor | маршрутизация **PASS** / live-turn типизированно отказал |
| Data preservation | **PRESERVATION PASS** (`scripts/installed-data-preservation.py`) |

## 4.1 Новый тестовый инструментарий, созданный в этом цикле

| Файл | Назначение |
|---|---|
| `scripts/backend-sidecar-stamp.py` | Provenance упакованного backend; `BACKEND_SIDECAR_STALE` = exit 3 |
| `scripts/installed-data-preservation.py` | Инвентаризация **без единого секрета**, backup через собственный `BackupService`, сравнение по таблицам |
| `scripts/acceptance-tor-natural-language.py` | Live natural-language Tor на установленном продукте |
| `apps/desktop/e2e/release-1.2.0.mjs` | GUI-приёмка: AI-статус, чипы, updater, автозапуск, single instance, чистый выход |
| `apps/backend/tests/test_sidecar_stamp.py` | 11 тестов guard'а |
| `apps/backend/tests/test_allocation_candidates.py` | 17 тестов allocator'а + честность запаркованного retry |
| `apps/gateway/tests/test_allocation.py` | 21 тест политики кандидатов |
| `apps/backend/tests/test_on_demand_ai.py` | +1 тест: завершённый walk закрывает turn |
| `apps/desktop/src-tauri/tests/updater_signature.rs` | +1 gate: подпись **реально собранного** артефакта |
| `docs/gpu-allocation.md` | Топология хранения, политика кандидатов |

---

# ЧАСТЬ V. INSTALLED ACCEPTANCE

Установка выполнена **поверх реального 1.2.0**; корень данных оператора не стирался,
`ALEX_LLM_DATA_DIR` на него не переключался.

| Проверка | Результат |
|---|---|
| Версия | 1.2.0 (согласованы `/health`, метаданные установщика, stamp) |
| Backend | **PASS** — установленный sidecar `2d46fbd8…`, digest `cdef75ef…` |
| AI-статус | **PASS** — `Disconnected` / `AI Unavailable`; никогда `Connected`, никогда «Проверяем состояние…» |
| Computer | **PASS** — `Готово` при обычном запуске, без кнопки |
| Tor | **PASS** — `Готово`, `socks5h`, managed, `pid 27328` → установленный `runtime\tor\tor.exe`, daemon 0.4.9.12 = пин |
| Memory | **PASS** — `Готово` |
| Single instance | **PASS** — 1 desktop / 1 backend / 1 Tor; второй запуск выходит с кодом 0 и передаёт управление; первое окно остаётся тем, что обслуживает UI |
| Backend recovery | **PASS** — sidecar убит (pid 25044) → новый pid 8800, `/health` отвечает, Computer и Tor снова ready, тот же `device_id` |
| Tor recovery | **PASS** — daemon убит (28272 → 28660), свежий managed-процесс, свежий proof, чип снова зелёный |
| Autostart ON/OFF/ON | **PASS** — реальное значение `HKCU\…\Run`: записано, удалено, восстановлено; переключатель совпадал с ОС каждый раз; **собственное значение оператора возвращено** |
| Data preserved | **PASS** — 31/31 таблиц идентичны, `device_id 908c2242-65c4-4dd9-bfc3-c168de3a37c4` не изменился, ключи настроек сохранены, 5 backup'ов сохранены |

Прогоны выполнялись с **изолированным** корнем данных, изолированным каталогом устройства и
изолированными именами учётных данных.

### Верифицированные backup'ы перед каждой установкой

| ID | Метка | Размер | Verified |
|---|---|---|---|
| `20260924T091331Z-manual` | `pre-1.2.0-upgrade` | 540 919 B | **True** |
| `20260924T135353Z-manual` | `pre-1.2.0-replacement` | 540 919 B | **True** |
| `20260924T160426Z-manual` | `pre-1.2.0-final-candidate` | 540 919 B | **True** |
| `20260924T170821Z-manual` | `pre-1.2.0-final` | 540 919 B | **True** |

---

# ЧАСТЬ VI. GATEWAY 1.2.0

## 6.1 Precheck (до изменений)

| | |
|---|---|
| `current` | `→ releases/adb568734430` |
| Сервис | `alex-gateway` active, запущен 2026-09-22 12:35 |
| `/health` | **200**, `{"version":"1.1.0","ready":true,"database":"ok"}` |
| `/updates/latest` | **404** (маршрута нет) |
| `/downloads/` | 404 (каталога нет) |
| Rollback | переуказать `current` на `adb568734430` — **READY** |

## 6.2 Деплой

Релизный артефакт: 136 файлов, `sha256 9843c4433b94a0c477c8f08b6733a607a0efbfbae8fa1fef8e4a4454cf1aca8e`,
304 002 B. Диff против развёрнутого 1.1.0: **+4 файла, 0 удалений** —
`backend/app/cloud/demand.py`, `backend/app/compute/candidates.py`,
`backend/app/tools/tor/service.py`, `gateway/updates.py`.

Секреты: ни одного файла `.env`/`.key`/`.pem`/`.db`, ни одного литерального ключа; `jwt_secret` —
поле конфигурации с пустым дефолтом, читается из `/etc/alex-gateway/alex-gateway.env`.

**Preflight до переключения:** new-tree импортируется и приложение конструируется
(`gateway.main.app`, `gateway.updates`, `gateway.compute`, `app.compute.candidates`,
`app.cloud.demand`, `app.tools.tor.service`). `requirements.txt` байт-идентичен → pip не трогали.
Alembic head не изменился (`0001_gateway_core`) → миграция — no-op. Перезапущен **только**
`alex-gateway`.

## 6.3 Postcheck

| | |
|---|---|
| `current` | `→ releases/6e2eca83e0c2` |
| Сервис | active, `Application startup complete` |
| `/health` | **200**, `{"version":"1.2.0","ready":true,"database":"ok"}` |
| `/updates/latest` | **204**, не 404 |
| Fake searching | **NO** — `state=offline error=gpu_unavailable session=- create_attempts=0` |
| Rollback | **READY** (не использован) |

## 6.4 Live-матрица `/updates/latest` без манифеста

10 комбинаций параметров, включая `target=../../etc/passwd` — **все 204**, ни одного 5xx.

## 6.5 Валидация реального production-манифеста кодом развёрнутого Gateway

Манифест **подготовлен и провалидирован, но не активирован**.

| Случай | Результат |
|---|---|
| `current_version=1.1.0` | **200** с телом манифеста |
| `current_version=1.2.0` | 204 (та же версия → нет обновления) |
| `current_version=2.0.0` | 204 (даунгрейд невозможен) |
| `linux-x86_64` / `darwin-aarch64` / `windows-aarch64` | 204 (Linux не предлагается) |
| `bad_signature` / `empty_signature` | **REFUSED** `updates_manifest_entry_incomplete` |
| `version_not_in_name` (1.2.1) | **REFUSED** `updates_manifest_entry_unbound` |
| `signed_name ≠ served file` | **REFUSED** `updates_manifest_entry_unbound` |
| `insecure_url` (http) | **REFUSED** `updates_manifest_entry_insecure` |
| `bad_digest` | **REFUSED** `updates_manifest_entry_digest` |
| `private_looking` (`api_key`) | **REFUSED** `updates_manifest_not_public:api_key` |
| `replay_as_2_0_0` | **REFUSED** `updates_manifest_entry_unbound` |
| `no_windows_entry` | 204 |
| пустой / отсутствующий `pub_date` | **поле опускается** в отдаваемом теле (не отдаётся пустым) — именно так, как требует клиент |

---

# ЧАСТЬ VII. ХОСТИНГ И DOWNLOAD-BACK

| | |
|---|---|
| URL | `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe` |
| Скачанный SHA256 | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` |
| Совпадает с принятым | **PASS** — байт-идентично (`cmp`) и для установщика, и для `.sig` |
| Production-подпись | **PASS** — скачанные байты и есть проверенные (ключ `9B328EFF111D1FB2`) |
| Version binding | **PASS** — trusted comment `file:Canalla LLM_1.2.0_x64-setup.exe` |
| Листинг каталога | **403** |
| `POST` на артефакт | **403** |
| Path traversal | **404** |
| Вытесненные артефакты | **404** (проверены `1.1.0` и оба пост-деплойных кандидата) |
| Приватный ключ / секрет в каталоге | **нет** |

Изменение nginx: **одна** локация `/downloads/` (`alias`, `autoindex off`, `limit_except GET HEAD`,
`types`), **+17 строк, 0 удалений**, один hunk `@@ -23,6 +23,23 @@`.
Бэкап конфигурации: `/root/alex-gateway.conf.bak-before-1.2.0-downloads`. `nginx -t` — успешно,
затем `reload` (не restart).

---

# ЧАСТЬ VIII. МАТРИЦА UPDATER

| Случай | Результат |
|---|---|
| Та же версия | **PASS** — live на установленной сборке: проверка завершается, `Доступная версия: нет`, обновление не предлагается, приложение остаётся рабочим |
| Валидное обновление | **PASS** — провалидированный манифест отдаётся `200` клиенту 1.1.0 |
| Плохая подпись / повреждённый артефакт | **PASS** — отказ на сервере (`updates_manifest_entry_incomplete`) и на клиенте (`a_tampered_package_is_refused`, `a_truncated_package_is_refused`) |
| Несовпадение версии | **PASS** — отказ как unbound |
| Replay | **PASS** — старый валидно подписанный артефакт, выдаваемый за новый, отвергается |
| Даунгрейд | **PASS** — `2.0.0 → 204`; клиент никогда не предлагает равную или старую версию |
| Чужая платформа / архитектура | **PASS** — `linux-x86_64`, `darwin-aarch64`, `windows-aarch64` → 204 |
| 404 | **PASS** — наблюдался live до деплоя, затем жёстко заменён контрактом 204 |
| 500 / timeout / offline / прерванная загрузка | **PASS по построению и покрытию сюитами**; приложение остаётся рабочим во всех наблюдённых случаях |
| Отложенность при занятости | **PASS** — кнопка установки отключена при `manual || busy`, панель объясняет перенос |

---

# ЧАСТЬ IX. TOR ROUTING

| | |
|---|---|
| Natural language | **PASS (детерминированно, на уровне wire)** |
| TOR REQUIRED | **PASS** — `route: TOR_ONLY`, `origin: server_policy` (сервер классифицирует промпт, модель не спрашивают) |
| SOCKS5h | **PASS** — `transport: tor-socks5h`, `socks.method: socks5h` |
| Circuit proof | **PASS** — `verified_chain: true` + `verified_at` **на самом run**, не только на чипе |
| DNS leak | **NO** — `socks.atyp == 3` (имя дошло до прокси как имя), `socks.local_dns is False`, DNS-tripwire зафиксировал **ноль** попыток разрешения |
| Clearnet fallback | **0** — ни один clearnet-фейк не вызван, `clearnet_runs(runs) == []` |
| Fail closed | **PASS** — `tor_unavailable`, ограниченное (≤60 с) восстановление, ноль clearnet, ноль DNS; инструмент, не умеющий уважать локальный Tor, отвечает `tor_route_unsupported` |

**Детерминированное доказательство:**
`tests/test_tor_research.py::test_named_clearnet_url_is_fetched_through_tor_over_socks5h` прогоняет
реальный промпт «Открой http://example.com/ через Tor» через реальный стрим против фейкового
SOCKS5h-сервера и DNS-tripwire. Плюс `test_bare_host_is_normalised_to_https_and_retried_once_inside_tor`,
`test_tor_unavailable_fails_the_action_without_clearnet_fallback`,
`test_explicit_clearnet_tool_under_a_tor_route_is_typed_unsupported`. Всего **80** Tor-тестов.

**Live:** на установленном продукте Tor поднялся сам (`state=managed`, endpoint 9050, `socks5h`,
0.4.9.12 = пин), proof валиден до и после запроса, обслуживающий процесс подтверждён через ОС
(`pid 27328` → установленный `runtime\tor\tor.exe`). Сам turn завершился типизированным отказом
**до** выполнения инструментов, потому что модель-требующий turn гейтится готовностью модели, и
выполнил **ноль** clearnet-вызовов.

---

# ЧАСТЬ X. LIVE AI

| | |
|---|---|
| Попыток capacity | **1** |
| Ожидание capacity | **≤60 с**, walk завершился `price_limit` |
| Pod создан | **NO** |
| Chat automatic start | **PASS** — turn сам выполнил попытку размещения (Finding B) |
| Generation | **NOT RUN** — внешняя доступность: ничего совместимого внутри собственного максимума $0.52/ч |
| Live Tor request | **NOT RUN — EXTERNAL CAPACITY** |

**Pod'ов создано: 0. Утечек Pod'ов: 0. Потрачено: $0.**

---

# ЧАСТЬ XI. LINUX PREVIEW

| | |
|---|---|
| Статус | **PREVIEW / EXPERIMENTAL** |
| Новый Linux sidecar | `fd4e975ec900096be129d1abfda3ca44fdc972caef1aa91c125943bb7393390e` (22 619 136 B) |
| Stamp | `product_version 1.2.0`, `source_digest cdef75ef…`-эквивалент для Linux-дерева, 137 файлов |
| Хук бандла | **PASS** — `staged the bundled Tor runtime, the backend sidecar and the host loop` |
| `.deb` | `Canalla LLM_1.2.0_amd64.deb`, 180 918 826 B, `sha256 5d0d3947bd7618bd4369a3fb7fd6eb84b5270510721b763fdb1b194448eacd7b` |
| Payload | `build-stamp.json` рядом с backend, backend `fd4e975e…`, host loop, `runtime/tor/{tor,runtime.json}`, **ноль** `.exe` |
| Tor acceptance | **9 PASS / 0 FAIL**, установленный `/health` = 1.2.0 |
| Production linux-манифест | **NOT PUBLISHED** |

**Важно:** первый собранный в этом цикле `.deb` нёс **устаревший** backend (`/health` → 1.1.0).
Это не дефект репозитория и не дефект guard'а — Linux-хук **вызывает** guard, и guard корректно
отказал (`BACKEND_SIDECAR_STALE stamp_missing`, exit 1); `.deb` был собран старой копией хука,
оставшейся в WSL-дереве после моего неполного sync. Я это исправил: досинхронизировал хуки,
пересобрал sidecar, `.deb` и повторно прогнал приёмку. Дополнительно я **добавил проверку версии** в
`acceptance-linux-runtime.sh`, потому что до этого он принимал устаревший backend 1.1.0 — все девять
его проверок проходили.

---

# ЧАСТЬ XII. БЕЗОПАСНОСТЬ

| | |
|---|---|
| Production-ключ | `9B328EFF111D1FB2` |
| Приватный ключ в репозитории | **NO** |
| Пароль в репозитории или логах | **NO** (пароль генерируется программно, живёт в Windows Credential Manager, `CanallaLLM/UpdaterSigning/Production`, ни разу не напечатан) |
| Приватный ключ в установщике | **NO** |
| Тестовый ключ для production-артефакта | **NO** — закреплено `the_configured_key_is_the_production_identity_and_not_the_test_one` и новым gate'ом на собранном артефакте |
| Вытесненные установщики на хостинге | **NO** |
| Устаревший sidecar в финальном продукте | **NO** |
| Секреты в манифесте обновлений | Отвергаются (`updates_manifest_not_public`) |
| RunPod master key | Только на Gateway (`/etc/alex-gateway/runpod.env`, 0640 root:alex-gateway); не в Desktop, не в installer, не в логах |
| Баланс и статусы | READ ONLY: никогда не запускают, не усыновляют и не останавливают compute |

---

# ЧАСТЬ XIII. СООТВЕТСТВИЕ ТРЕБОВАНИЯМ ПРОМПТОВ

## STEP 5/5 — Final Canalla LLM 1.2.0

| Требование | Статус |
|---|---|
| Windows = production, Ubuntu = preview | Выполнено и нигде не размыто |
| Не добавлять OpenClaw | Canalla остаётся владельцем LLM, промпта, Memory, RunPod, Tor, Computer, tool routing, настроек, security-инструментов и финального ответа |
| Глобальный AI-статус (red/amber/green) | Исправлен в двух местах (дефекты 2 и 5), доказан live |
| Windows-приёмка без VM | Выполнена на текущем хосте с изолированными корнем данных, WebView-профилем, PATH, credential targets |
| Отсутствие ручных зависимостей | Доказано: sidecar отвечает с **пустым PATH**, без Python/Node/Rust |
| Windows autostart | ON/OFF/ON через Settings, реальная запись реестра, значение оператора восстановлено |
| Single instance | 1 desktop / 1 backend / 1 Tor, второй запуск передаёт управление |
| Backend recovery | Без кнопки, новый PID, `/health`, Computer и Tor ready, тот же device |
| Tor recovery | Новый PID, SOCKS, реальный circuit proof, Ready, без внешнего Tor |
| Natural-language Tor | Детерминированно **PASS** на wire; live заблокирован отсутствием модели |
| Tor fail-closed | **PASS**, ноль clearnet |
| DNS leak rule | **PASS** (ATYP 3, `local_dns=false`, tripwire пуст) |
| Version bump 1.1.0 → 1.2.0 во всех местах | Выполнено ранее; consistency-тест **10 passed** |
| Ubuntu preview, без изобретения desktop-доказательств | Соблюдено |
| Linux-регирессия в доступном окружении | Выполнена в WSL, см. Часть XI |
| Linux `.deb` версии 1.2.0 | Собран, помечен Preview |
| Production Linux auto update НЕ включать | Манифест Windows-only, linux-записей нет |
| Production signing identity | Создана в STEP 5A, использована во всех сборках |
| Не публиковать manifest, не мержить, не тегать | Соблюдено |

## STEP 5A — Production signing identity

| | |
|---|---|
| Создан **новый** ключ, тестовый не переиспользован | **YES** |
| Приватный ключ вне репозитория | `C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key` |
| Пароль защищён и не напечатан | Windows Credential Manager, `CanallaLLM/UpdaterSigning/Production` |
| Шифрованный backup | `D:\canalla-release-backup\canalla-updater-production.key.enc` (+`.enc.json`), пароль восстановления — отдельный target `CanallaLLM/UpdaterSigning/ProductionBackup` |
| **Off-machine backup** | **Подтверждён оператором** |
| Recovery-тест | **PASS** (ключ открывается паролем из Credential Manager, тестовый артефакт подписывается и проверяется) |
| Test public key убран из production-конфига | **PASS** — в `tauri.conf.json` `9B328EFF111D1FB2` |
| Non-interactive подпись | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; отказ `production_signing_secret_unavailable`; без prompt'ов, без fallback на пустой пароль/тестовый ключ/неподписанный артефакт |
| Version-binding регрессия | **PASS** (см. Часть VIII) |

## STEP 5B — Final Windows release acceptance + production infra

| Требование | Статус |
|---|---|
| Подтверждение off-machine backup | Есть |
| Не пересобирать без причины | Соблюдено: каждая из трёх пересборок вызвана доказанным дефектом |
| Pre-upgrade inventory | Выполнена (без секретов) |
| Verified backup | 4 верифицированных backup'а, см. Часть V |
| Установка поверх реального 1.1.0 → 1.2.0 | Выполнена ранее (миграция + 16/16 категорий) |
| Первый запуск 1.2.0 | Все проверки пройдены |
| Сохранность данных | **PRESERVATION PASS** на каждом шаге |
| Single instance / recovery / autostart | **PASS** |
| Natural-language Tor E2E | Wire-level **PASS**; live — внешняя блокировка |
| Tor failure E2E | **PASS** детерминированно |
| Gateway precheck / deploy / rollback | См. Часть VI |
| Hosting + download-back | См. Часть VII |
| Updater RC E2E + failure matrix | См. Часть VIII |
| Linux preview regression | См. Часть XI |
| Authenticode | **NOT SIGNED** — сертификата нет; SmartScreen может предупредить, это заявлено в release notes |
| No GPU beyond one ≤60 s attempt | Соблюдено |
| Стоп перед merge/tag/publish | Соблюдено |

## STEP 5B CONTINUATION (Gateway + hosting + updater E2E)

Все пункты выполнены, кроме честно заявленных ограничений (§LIMITATIONS). Отдельно:
`/updates/latest` возвращает **204**, а не 404 — как и требовалось; и проверено, что
`compute/status` **не** остаётся в фальшивом `searching`.

## STEP 5B FINAL FIX + RC CLOSEOUT (Finding A/B)

| | |
|---|---|
| Finding A: fake/stale searching | **FIXED** (Gateway + direct) |
| Finding A: infinite Connecting | **NO** |
| Finding B: chat auto ensure | **PASS** |
| Manual Start required | **NO** |
| Ensure deduplicated | **PASS** |
| Original message executes once | **PASS** |
| No compute → typed failure | **PASS** (`price_limit`, 4.8 с) |
| Deterministic A/B matrices | `test_search_bounds.py` (11), `test_cloud_demand.py` (40), `test_allocation_candidates.py` (17), `test_on_demand_ai.py` (+1) |
| No `/v1/models` spam | Модельный эндпоинт не опрашивается до готовности |
| Deterministic gates before GPU/build | Соблюдено |
| Backend sidecar rebuild + guard | Выполнено, guard **PASS** |
| One replacement installer | Выполнено (по одной на каждый доказанный дефект) |
| Same production key | `9B328EFF111D1FB2`, новый ключ не создавался |
| Superseded installers never hosted | Соблюдено |
| Linux remains Preview | Соблюдено |
| Stable manifest unpublished, no merge/tag | Соблюдено |

## ADDITIONAL RELEASE BLOCKER — RunPod capacity allocation

| | |
|---|---|
| Не ждать один слот GPU | **Реализовано**: набор кандидатов |
| Совместимость выводится из требований модели | 48 ГБ VRAM floor, политика пользователя, стоимость |
| Поиск в нескольких локациях | placement'ы, датацентр Volume первым |
| Secure/Community | Community только при трёх условиях сразу, off by default |
| Общий бюджет 60 с (не на кандидата) | Реализовано |
| Быстрый отказ → следующий кандидат | Реализовано |
| Stopped Pod / redeploy | Не разрушает данные: модель и скрипты на Network Volume |
| Топология хранения | Описана в `docs/gpu-allocation.md` |
| Стейт-машина | `searching` = реальная операция с `started_at` и неистёкшим дедлайном |
| Retry behaviour | Нет горячего цикла; новый запрос пользователя = один новый ограниченный цикл |
| Дедупликация | Одна попытка размещения на compute-идентичность (лейз в БД) |
| Наблюдаемость | Не-секретный allocation-диагностик в audit trail и в `allocation` |
| Тест-матрица | 21 + 17 тестов |
| Live-санитария | **1 попытка**, ≤60 с, результат `price_limit`, 0 Pod'ов, 0 утечек |
| Release requirement: больше не сидеть час в «Ищем GPU» | **Доказано замером: 937.1 с → 4.8 с** |

## План по stale-sidecar guard

| Требование | Статус |
|---|---|
| Build-stamp рядом с packaged backend | `build-stamp.json`: `product_version`, `source_digest`, `source_files`, `backend_exe`, `backend_exe_sha256` |
| Guard сравнивает stamp с текущим деревом | **Да**, и с `product.py` тоже |
| FAIL BUILD, а не warning | Exit 3 + `throw` в хуке бандла |
| Digest детерминирован, не по mtime/размеру | SHA-256 по содержимому 137 файлов, CRLF→LF |
| Regression tests | **11 тестов** |
| Пересборка sidecar из текущего дерева | Выполнена (трижды, по числу дефектов) |
| `/health` → 1.2.0 | **Да**, доказано |
| Staged sidecar ≠ старый | `d08cd5b7…` → `911914aa…` → `2d46fbd8…` |
| Deterministic gates → **одна** сборка | Соблюдено |
| Та же production identity | `9B328EFF111D1FB2` |
| Старый установщик признан REJECTED | `e13ba09c…` + все последующие в карантине |
| После сборки проверено | версия, `/health`, ключ, подпись, version binding, bundled Tor, stamp — **всё PASS** |
| Установка поверх текущего 1.2.0 без потери данных | **PASS** |
| Не выполнять Gateway deploy / upload / merge / tag до отчёта | Соблюдено (деплой и upload сделаны отдельно, после приёмки, как разрешено следующими промптами) |

---

# ЧАСТЬ XIV. ОГРАНИЧЕНИЯ — ЗАЯВЛЕНЫ, А НЕ СКРЫТЫ

1. **`Restart & Update` на установленном клиенте не нажимался.** Для этого нужен **более новый**
   артефакт, подписанный production-ключом, то есть сборка тестовой 1.2.1; устанавливать её означало
   бы оставить оператора на фальшивом релизе. Всё по обе стороны от этого шага доказано —
   валидатор и binding-правила Gateway live, криптографическая приёмка клиента против **реального
   собранного артефакта** и **реального production-ключа**, путь «нет обновления» live на
   установленной сборке, — но кнопку я не нажимал и не заявляю обратного.
2. **Natural-language Tor-запрос не выполнен против живой модели.** Модель-требующий turn гейтится
   готовностью модели, а единственная попытка capacity завершилась `price_limit` без Pod'ов.
   Маршрутизация доказана на wire, включая ноль локального DNS и ноль clearnet.
3. **Linux-артефакты — Preview.** `.deb` пересобран самосогласованным на 1.2.0, приёмка 9/9, но
   desktop-сессия, GUI, keyring и Secret Service desktop не заявлены и не проверялись.
4. **Развёрнутое дерево Gateway несёт старый `backend/app/compute/controller.py`.** Оба фикса после
   деплоя меняют файлы, которые Gateway **не импортирует** (`gateway/provider.py` импортирует только
   `app.compute.runpod_api`, `runtime`, `schemas`, `candidates`), поэтому shared-поведение идентично.
   Байтовая согласованность дерева потребовала бы передеплоя — риск трогать продакшн ради
   неимпортируемого файла не оправдан.
5. **Authenticode не подписан** — сертификата нет. SmartScreen может показать предупреждение.
6. **REAL backend restart с живым Pod'ом** по-прежнему не протестирован live.
7. **WM-07 TinyFish Browser** и **CD-08 REAL stale-SHA** остаются закрытыми ограничениями.
8. **LoRA не начиналась** и не будет до отдельной задачи.
9. **Проверка на «чистой ВМ» не выполнялась** — приёмка шла на текущем Windows-хосте с максимально
   жёсткой изоляцией (изолированный корень данных, WebView-профиль, stripped PATH, изолированные
   credential targets, отсутствие Python/Node/Rust на пути продукта). Это **не** pristine VM.

---

# ЧАСТЬ XV. ЧТО НЕ СДЕЛАНО (СОЗНАТЕЛЬНО)

```
merge main:                          НЕТ
тег v1.2.0:                          НЕТ
стабильный production manifest:      НЕ ОПУБЛИКОВАН (/updates/latest → 204)
содержимое манифеста:                ПОДГОТОВЛЕНО И ПРОВАЛИДИРОВАНО, не активировано
linux-запись в production manifest:  ОТСУТСТВУЕТ
Authenticode:                        НЕ ПОДПИСАН
```

Четыре оставшихся необратимых действия — merge `main`, тег `v1.2.0`, публикация стабильного
манифеста и анонс загрузки — ждут **отдельной авторизации**.

---

# ЧАСТЬ XVI. КЛИНИНГ И ГИГИЕНА

| | |
|---|---|
| Процессы | 0 (`alex-llm`, `alex-backend`, `tor`) |
| Pod'ы | 0 создано, 0 утечек, $0 потрачено |
| Тестовые серверы обновлений | нет |
| Активный RC-манифест | нет |
| Фальшивая RC-версия на машине | нет |
| Временный подписной материал | нет (приватный ключ никуда не копировался) |
| Автозапуск оператора | возвращён к исходному значению |
| Данные и device identity оператора | без изменений |
| Дерево git | чистое, кроме известного дрейфа `docs/screenshots/0.4/*.png` (не трогал) |
| Вытесненные установщики | в карантине вне репозитория, сняты с хостинга |

---

# ЧАСТЬ XVII. СОБСТВЕННЫЕ ОШИБКИ В ПРОЦЕССЕ И ИХ ИСПРАВЛЕНИЕ

Привожу открыто, потому что они влияли на доказательства:

1. **Неполный sync в WSL** — не были скопированы `stage-native-runtime.*`, поэтому Linux-хук в
   WSL-дереве был старой копией без guard. Следствие: субагент сообщил «guard не вызывается» (неверно
   для репозитория) и собрал `.deb` с устаревшим backend. **Исправлено**: досинхронизировал хуки,
   доказал, что guard на Linux вызывается и отказывает (`BACKEND_SIDECAR_STALE`, exit 1), пересобрал
   sidecar, `.deb` и прогнал приёмку заново.
2. **Три бага в собственных harness'ах.** `[role="dialog"]` — CSS-атрибут, а у нативного `<dialog>`
   его нет; переключатель автозапуска живёт в секции «Общие», а не «Дополнительно»; утверждение
   updater'а требовало сообщения об ошибке, что было верно при 404, но не при корректном 204.
   **Исправлено**: селекторы приведены к реальной разметке, утверждение — к реальному контракту.
3. **Проверка версии в Linux-приёмке отсутствовала.** `acceptance-linux-runtime.sh` проверял
   `product=alex-llm`, но **не** версию, поэтому устаревший backend 1.1.0 проходил все девять
   проверок. **Исправлено**: скрипт читает `VERSION` из `app/product.py`.
4. **Перезаписал существующий отчёт** `docs/report-2026-09-24-step5b-closed-rc.md`. **Исправлено**:
   восстановил из git, свой текст перенёс в `…-closeout-final.md`.
5. **Неудачная формулировка в guard'е.** `backend_executable()` предпочитал `.exe` безусловно, из-за
   чего дерево с бинарями обеих платформ могло аттестовать чужой. **Исправлено**: stamp называет один
   бинарь, проверка сверяет **тот же** файл, stamp без имени отвергается, добавлен `--binary`.

---

# ЧАСТЬ XVIII. ВОСПРОИЗВОДИМОСТЬ

```bash
# Дерево и версия
git rev-parse HEAD                      # 8d9224bc000d078652ab710f0f6ff62c61bbc766
git rev-parse main                      # 9bd75486c34b64ab4435afb4efc9fa9b17104cfd

# Гейты
cd apps/backend && .venv/Scripts/python.exe -m pytest -q                 # 719 passed, 1 skipped
cd apps/gateway && ../backend/.venv/Scripts/python.exe -m pytest -q      # 187 passed
cd apps/desktop && npm run test && npx tsc -b && npm run format:check    # 255 / clean / clean
cd apps/desktop/src-tauri && cargo test --workspace                      # 95 passed

# Provenance упакованного backend
python scripts/backend-sidecar-stamp.py check \
  --backend apps/backend \
  --stamp apps/desktop/src-tauri/sidecar/alex-backend/build-stamp.json    # exit 0

# Sidecar автономно
apps/backend/.venv/Scripts/python.exe scripts/verify_sidecar_standalone.py

# Приёмка на установленном продукте
cd apps/desktop && node e2e/release-1.2.0.mjs        # 31/31
cd apps/desktop && node e2e/always-ready.mjs         # PASS
apps/backend/.venv/Scripts/python.exe scripts/acceptance-tor-natural-language.py
apps/backend/.venv/Scripts/python.exe scripts/installed-data-preservation.py inventory \
  --data-root "C:/Users/Volkr/AppData/Local/Alex LLM"

# Единственная production-сборка (одна на доказанный дефект)
powershell -File scripts/production-signing.ps1 -Action Build \
  -KeyPath "C:/Users/Volkr/.canalla-updater/production/canalla-updater-production.key"
```

Полезные пути:

| | |
|---|---|
| Корень данных продукта | `%LOCALAPPDATA%\Alex LLM\` |
| Установленный продукт | `%LOCALAPPDATA%\Programs\Canalla LLM\` |
| Приватный ключ updater | `C:\Users\Volkr\.canalla-updater\production\` |
| Карантин вытесненных сборок | `C:\Users\Volkr\.canalla-updater\superseded\` |
| Шифрованный backup ключа | `D:\canalla-release-backup\` |
| Артефакт на VPS | `/srv/canalla-downloads/` |
| Бэкап конфигурации nginx | `/root/alex-gateway.conf.bak-before-1.2.0-downloads` |
| Gateway на VPS | `/opt/alex-gateway/{current,releases,venv}` |

---

# ЧАСТЬ XIX. ФИНАЛЬНЫЙ СТАТУС

| | |
|---|---|
| Продукт | Canalla LLM **1.2.0** |
| Windows x86_64 | **PRODUCTION** — принят |
| Ubuntu 24.04 x86_64 | **PREVIEW / EXPERIMENTAL** |
| Финальный установщик | `9383aa29ee465ca4ed996615edeb6ef6b436582bb1410ec8ae90220ab626b24e` |
| Подпись | `7e955a525899d3733d762dc783c89492e2a1c1d5e925d265c3ca875c243872fc` |
| Backend | `2d46fbd85b53d3ec0d16ce64618082e7c46ccd409c03ea886a7215cf9c7e1c6e` |
| Gateway prod | v1.2.0, `releases/6e2eca83e0c2`, `/updates/latest` → 204 |
| Артефакт на хостинге | `9383aa29…`, download-back байт-идентичен |
| Манифест | подготовлен и провалидирован, **не опубликован** |

## READY FOR FINAL PUBLISH
