# CANALLA LLM 1.2.0 — RELEASE STATUS (interim)

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `2f72c46` · Версия продукта: **1.2.0**

**Статус:** вся инженерная часть финального этапа выполнена и измерена. Релиз остановлен на **одном**
входе, который нельзя угадать: production-идентичность подписи updater. Ниже — что готово, что
именно нужно и какие production-действия ждут подтверждения.

## 1. Scope релиза

| Цель | Статус |
|---|---|
| Windows x86_64 | PRODUCTION |
| Ubuntu 24.04 LTS x86_64 | PREVIEW / EXPERIMENTAL — desktop-приёмка **DEFERRED** (ограничение среды, не дефект) |

## 2. Коммиты

| Коммит | Что |
|---|---|
| `2903f08` | `feat(tor): route a named URL through Tor and fail closed` |
| `91ed948` | `feat(updater): bind the signed artifact to the release version` |
| `2f72c46` | `chore(release): bump Canalla LLM to 1.2.0` |

## 3. Что закрыто

**Natural-language Tor routing (было открыто).** «Открой example.com через Tor» раньше не тянул этот
URL детерминированно: контроллер подставлял поиск с целым промптом, а извлекались только `.onion`.
Теперь названные в запросе URL — первое действие; `tor_fetch` больше не отчитывается «completed» о
запуске, который не переслал ни байта: он отдаёт типизированный код после одной bounded-попытки
восстановления и одного повтора, а успешный запуск несёт circuit proof. Возможность, которую нельзя
локально маршрутизировать через Tor, отвечает `tor_route_unsupported`. Доказательство —
bytes-on-the-wire: локальный SOCKS5h-листенер записывает CONNECT, DNS-tripwire отвергает любой
не-loopback резолв.

**Version binding (было открыто как `requireSignedVersion`).** Подпись покрывала байты артефакта, а
версия жила отдельно в манифесте, поэтому старый валидно подписанный артефакт можно было выдать за
новый релиз. Trusted comment minisign сам покрыт подписью и называет артефакт: обновление
предлагается только если подписанное имя файла равно percent-decoded basename URL, а версия
манифеста — версии в этом имени. Клиент отказывает типизированно **до** скачивания байтов; Gateway
отвечает 503 на краю. Негативные тесты: чужое имя, несовпадающая версия, replay, downgrade,
неразбираемая подпись — и положительный контроль, доказывающий, что правило не выключило обновления.

**Version bump 1.1.0 → 1.2.0.** Восемь рукописных литералов + новый
`apps/backend/tests/test_version_consistency.py`, который привязывает их все к `app.product.VERSION`
(включая оба lockfile и pydantic-default Gateway) и требует, чтобы FastAPI строился из константы, а
не из литерала. Проверка появилась потому, что в прошлом релизе дрейф уже случился: `main.py`
объявлял одну версию, а `/health` отвечал другую.

## 4. Гейты — точные числа

| Гейт | Windows | Ubuntu 24.04 |
|---|---|---|
| BasedPyright | **0 errors, 0 warnings** (236 файлов) | — |
| Backend pytest | **650 passed, 1 skipped** | **646 passed, 5 skipped, 0 failed** |
| Backend ruff check / format | PASS (183 файла) | — |
| Backend Alembic (свежий SQLite → `0015`, check) | PASS | — |
| Gateway pytest / ruff / Alembic | **155 passed** / PASS (29 файлов) / check clean | — |
| Frontend Vitest | **248 passed** (21 файл) | **248 passed** |
| tsc / Prettier / Vite | clean / clean / PASS | — |
| Playwright | **20 passed** | — |
| Rust `cargo check --all-targets` | clean | clean |
| Rust `cargo test` | **93 passed** (21 + 58 + 14) | **101 passed** (25 + 62 + 14) |
| AI global state (`ai-connection.test.tsx`) | 28 passed (внутри 248) | — |
| Updater: Rust / Gateway / Vitest | 14 / 26 / 29 passed | — |
| Linux packaging guard | — | **5 passed** |
| Tor routing (новое) | — | 10 новых тестов, внутри 646 |
| Bundled Tor, установленный `.deb` | — | **9/9 PASS** |
| `npm audit --omit=dev` / `pip-audit` | 0 уязвимостей / no known vulnerabilities | — |

## 5. Артефакты, готовые к релизу

**Linux Preview (собран, подписан тестовым ключом, установлен, проверен):**

| | |
|---|---|
| Путь | `apps/desktop/src-tauri/target/release/bundle/deb/Canalla LLM_1.2.0_amd64.deb` |
| Размер | **294 748 704** байта |
| SHA256 | `fe1e7809c5f4431cb21658747c64b4cb0cc0e7827d163dcee8cc05901f0ee8f3` |
| Подпись | `Canalla LLM_1.2.0_amd64.deb.sig`, 416 Б, SHA256 `862e04c92fb522f3b650824746c880f19c9fc87ed98257fec29653ee9b3c58a5` |
| Проверка подписи | **`minisign 0.11`: «Signature and comment signature verified»**, trusted comment `file:Canalla LLM_1.2.0_amd64.deb` |
| Установлен | `canalla-llm 1.2.0 amd64` поверх 1.1.0 |

`linux-x86_64` production-запись в манифест **не попадёт**.

## 6. ЧТО БЛОКИРУЕТ РЕЛИЗ

### 6.1 Production-идентичность подписи updater (§18–19) — единственный жёсткий блокер

Публичный ключ вшивается в клиент (`tauri.conf.json` → `plugins.updater.pubkey`) и **необратимо**
определяет, какие артефакты сможет устанавливать выпущенный 1.2.0. Сейчас там тестовый ключ
(`…B79FF90B52D00F24`), и он не должен стать production-идентичностью. Private key обязан быть
password-protected, лежать вне репозитория и никогда не попадать в логи/вывод/документы.

Нужно **одно** из двух:

* **(A) У вас уже есть production-ключ.** Укажите путь к `.key` вне репозитория и способ передать
  пароль неинтерактивно (рекомендую: имя переменной окружения, которую вы экспортируете в shell
  сборки, либо target в Windows Credential Manager). Я не буду его печатать и не сохраню в репозитории.
* **(B) Ключа нет — генерирую я.** Тогда решите, как обращаться с паролем:
  * **(B1)** вы задаёте пароль — он окажется в переписке, что противоречит «never printed»;
  * **(B2, рекомендую)** я генерирую ключ со случайным паролем и кладу пароль **сразу** в Windows
    Credential Manager, нигде его не выводя; в документации остаётся только имя target, а сборка
    читает пароль оттуда.

Честная цена ротации: `tauri-plugin-updater` пинит **один** публичный ключ в собранном клиенте.
Сменив ключ сейчас, мы фиксируем его на весь 1.2.x; смена в будущем потребует ручной переустановки
инсталлятора у пользователей. Ключ B79F…, уже вшитый в 1.1.0, не сможет обновлять 1.2.0 — первая
установка 1.2.0 всё равно ручная, и текст в `UpdatesPanel` это уже говорит.

### 6.2 Production-действия, ждущие подтверждения

Всё это необратимо или затрагивает живые сервисы, поэтому делаю только по вашему «да»:

| # | Действие | Что именно |
|---|---|---|
| 1 | **Deploy Gateway 1.2.0** (§21) | SSH `distance` (162.0.216.58) доступен, права есть. Сейчас `/updates/latest` → **404**, манифеста на сервере нет, каталога `downloads` нет. Деплой = rsync кода + pip + alembic + restart живого `alex-gateway.service` (в нём RunPod master key). Откат — возврат симлинка `current` на `adb568734430`. |
| 2 | **Хостинг артефактов** (§22) | Нужно решение по каталогу и URL. Фикстура предполагает `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe`; каталога пока нет — создам и добавлю раздачу в nginx. |
| 3 | **Upgrade 1.1 → 1.2** (§27) | Обновит **вашу реальную** установку `Canalla LLM 1.1.0` in place. Данные (`%LOCALAPPDATA%\Alex LLM`: backups, data, device.json, documents, logs, models, runtime, tasks, tor) сохраняются — до/после снимаю счётчики и ID. Подтвердите, что это можно делать сейчас. |
| 4 | **Merge + tag** (§39–40) | `release/canalla-1.1.0` → `main`, push, annotated tag `v1.2.0`, push tag. |
| 5 | **Production manifest** (§41) | Последнее мутирующее действие, только Windows-запись, только после загруженных и сверенных артефактов. |

### 6.3 Не блокеры, зафиксировано честно

* **Authenticode: NOT SIGNED.** Code-signing сертификата нет ни в `CurrentUser\My`, ни в
  `LocalMachine\My` (проверено). Windows SDK есть, подписывать нечем. SmartScreen предупреждение
  возможно — в release notes это будет сказано прямо.
* **Ubuntu Desktop acceptance: DEFERRED.** WSL/инженерные доказательства засчитаны только как
  Preview. Никаких GUI/keyring/login заявлений.
* **GPU: не использовался.** Ни одного Pod, ноль трат; опциональная live-проверка не запускалась.
* `scripts/acceptance-post-release-1.1.0.py` оставлен замороженным как артефакт сертификации 1.1.0
  (его проверки версии и хеша sidecar относятся именно к 1.1.0 и не являются гейтом 1.2.0).

## 7. Матрица цели

```
Source gates (free)         = ALL PASS  (см. §4)
Windows production build    = BLOCKED — ждёт production-ключ (§6.1)
Windows installed acceptance = NOT RUN — ждёт сборки
Upgrade 1.1 → 1.2           = NOT RUN — baseline установлен, готов
Updater E2E                 = NOT RUN — ждёт сборки и хостинга
Gateway route deploy        = READY   — ждёт подтверждения (§6.2 п.1)
Artifact hosting            = READY   — ждёт решения (§6.2 п.2)
Merge / tag                 = NOT RUN — ждёт зелёного релиза
Production manifest         = NOT PUBLISHED (и будет последним)
```
