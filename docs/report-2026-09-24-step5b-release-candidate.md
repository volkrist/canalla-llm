# CANALLA LLM 1.2.0 — STEP 5B RELEASE CANDIDATE REPORT

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `1545d68` · Версия продукта: **1.2.0**

```
RELEASE BLOCKED — THE PACKAGED BACKEND IS STALE (BUILT FROM THE 1.1.0 TREE)
```

**Почему:** установочная приёмка сработала ровно так, как задумана, и поймала реальный дефект
сборки. Установленный 1.2.0 несёт backend, замороженный **23 сентября 21:27**, то есть собранный из
дерева 1.1.0 — до bump версии и до работы по Tor-маршрутизации. Product сообщает о себе неправду
(`/health` → `"version":"1.1.0"`) и не содержит главной функции этого релиза.

Это **не** дефект данных и не дефект установщика: апгрейд 1.1 → 1.2 прошёл, данные целы на 16/16.
Дефект в том, **что именно мы собирались отдать пользователю**.

---

## §0 OFF-MACHINE SIGNING BACKUP

| | |
|---|---|
| Completed | **YES** — подтверждено оператором |
| Signing password in operator's password manager | **YES** |
| Backup recovery passphrase stored separately | **YES** |
| Secrets printed into chat or logs | **NO** |

Гейт снят, работа продолжена.

---

## §1 VERIFIED BEFORE TOUCHING ANYTHING

| Проверка | Ожидание | Факт | Итог |
|---|---|---|---|
| Ветка / HEAD | `release/canalla-1.1.0` / `c98c84b` | то же | **PASS** |
| `main` / `origin/main` | `9bd7548`, не тронуты | `9bd7548` / `9bd7548` | **PASS** |
| Теги | только `v1.0.0` | только `v1.0.0` | **PASS** |
| Версия продукта | `1.2.0` | `1.2.0` | **PASS** |
| Ключ updater в клиенте | `9B328EFF111D1FB2` (production) | `9B328EFF111D1FB2` | **PASS** |
| Тестовый ключ в конфиге | отсутствует | отсутствует | **PASS** |
| **SHA256 кандидата** | `e13ba09c…f3676a` | `e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a` | **PASS (точно)** |

Пересборки до приёмки не было; кандидат — тот же, что принят в 5A.

---

## §3–§4 PRE-UPGRADE BACKUP (штатный механизм Canalla)

Создано **до** установки, при остановленном продукте, тем же кодом, которым пользуется продукт
(`BackupService.create_now()`), а не копированием файлов:

| | |
|---|---|
| Backup id | `20260924T091331Z-manual` |
| Label | `pre-1.2.0-upgrade` |
| Files / bytes | **2** / **540 919** |
| Documents | 0 |
| Schema revision | `0015` |
| **Verified** | **True** (`BackupService.verify()` — глубокая проверка, не «файл существует») |

Побочный факт, который стоит знать: манифест бэкапа записал `app_version: 1.2.0`, потому что бэкап
делался кодом из текущего дерева, а не бинарём установленного 1.1.0. Содержимое при этом — реальные
данные 1.1.0. На сохранность это не влияет, но в отчёте это лучше сказать прямо.

Существовавшие ранее бэкапы сохранены: `20260921T132741Z-pre_upgrade` (0.9.3) на месте.

---

## §5 INSTALL 1.2.0 OVER REAL 1.1.0

| | |
|---|---|
| Инсталлятор | `Canalla LLM_1.2.0_x64-setup.exe`, production-подпись |
| Запуск | `"/S"` — тихий, exit code **0** |
| Реестр до / после | `Canalla LLM 1.1.0` → **`Canalla LLM 1.2.0`** |
| `alex-llm.exe` до / после | `4e5e72bd…` → `c42273c6…` (бинарь действительно заменён) |
| Корень данных | `%LOCALAPPDATA%\Alex LLM` — **не тронут**, оба бэкапа на месте |

---

## §6 FIRST 1.2.0 START

| Проверка | Результат |
|---|---|
| Процессы | **ровно по одному**: `alex-llm` (2212), `alex-backend` (16492), `tor` (15892) — и ни одного `alex-host-loop`, что верно: production-хост живёт in-process |
| Свежий Tor proof | **PASS** — `verified: true`, `source: managed`, `method: socks5h`, `tor_version 0.4.9.12`, `pid 15892`, wiring на `127.0.0.1:9050` |
| Tor source = bundled | **PASS** — демон запущен из `%LOCALAPPDATA%\Programs\Canalla LLM\…`, не системный |
| **Версия продукта** | **FAIL — `/health` отвечает `"version":"1.1.0"`** при установленном 1.2.0 |
| Чипы (AI/Computer/Memory) | **NOT READ** — `/status` требует сессию (`Session expired`), а GUI-харнесс не запускался |
| Updater в Settings | **NOT READ** (то же ограничение) |
| Корректный выход | **PASS** — после закрытия окна ни процессов, ни слушателей на 8000/9050 |

---

## §7 DATA PRESERVATION (после установки)

**16 из 16 категорий совпали с baseline побайтово по счётчикам:**

```
users 2 · chats 8 · messages 34 · projects 1 · memories 2 · documents 0
paired_devices 2 · compute_preferences 1 · compute_sessions 3 · compute_events 17
tool_runs 8 · message_contexts 17 · generation_usage 17 · web_source_snapshots 24
local_tasks 0 · auth_sessions 3 · schema revision 0015
device_id 908c2242-65c4-4dd9-bfc3-c168de3a37c4  (не изменился)
```

Установщик не мигрировал схему (0014 → 0015 было ещё на 0.9.3), поэтому апгрейд 1.1 → 1.2 — это
замена бинарей с сохранением данных, и она прошла безупречно.

---

## ДЕФЕКТ, КОТОРЫЙ ЭТО БЛОКИРУЕТ

**Симптом.** Установленный 1.2.0 отвечает `/health` → `"version":"1.1.0"`.

**Корень.** Установленный и застейдженный sidecar — **один и тот же файл**:

| | Путь | Дата | SHA256 |
|---|---|---|---|
| Установленный | `…\Programs\Canalla LLM\sidecar\alex-backend\alex-backend.exe` | **23 сен 21:27** | `a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944` |
| Стейджинг в репозитории | `apps/desktop/src-tauri/sidecar/alex-backend/alex-backend.exe` | **23 сен 21:27** | `a93149ac…` (идентичен) |

При этом исходники менялись **24 сентября**: `app/product.py` — 15:17 (`VERSION = "1.2.0"`),
`app/tools/tor/provider.py` — 15:29. То есть sidecar собран из дерева **1.1.0** и не содержит ни bump
версии, ни работы по natural-language Tor routing.

**Механизм пропуска.** `beforeBundleCommand` (`stage-native-runtime.cmd` → `.ps1`) проверяет
**наличие** sidecar и валит сборку при его отсутствии, но никогда не проверяет его **происхождение**.
`build.rs` по той же логике проверяет только существование файла в release-сборке. Свежий бинарь
против старого дерева ничем не перекрыт — поэтому «сборка прошла успешно» и «артефакт корректный»
здесь расходятся.

**Масштаб.** Это не косметика: `/health` — публичный контракт продукта (его читает `gui-smoke.mjs`,
его видит пользователь, на него опирается апгрейд-приёмка), а §11 этого шага (живой
natural-language Tor E2E) на таком бинаре провалился бы по построению.

---

## ПЛАН УСТРАНЕНИЯ (по §2: сначала детерминированный тест, потом ОДНА замена сборки)

1. **Детерминированный guard, чтобы это не повторилось.** Стейджинг должен валить сборку, если
   происхождение sidecar не соответствует дереву. Предлагаю два уровня:
   * штамп сборки: `build-backend-sidecar.ps1` пишет `sidecar/build-stamp.json` с версией продукта и
     хешем исходников, а стейдж-шаг сравнивает штамп с `app/product.py` и **отказывает** при
     расхождении (`BACKEND_SIDECAR_STALE`);
   * тест на это — рядом с существующим `apps/backend/tests/test_sidecar_packaging.py`, который уже
     проверяет `VERSION in text` для sidecar-логов.
2. **Пересобрать sidecar из текущего дерева** (PyInstaller, `alex-backend.spec`) и заново
   застейджить в `apps/desktop/src-tauri/sidecar/`.
3. **ОДНА заменяющая сборка** кандидата, той же production-подписью `9B328EFF111D1FB2`.
4. **Повторить приёмку:** установка → первый запуск (версия **1.2.0** в `/health`) → §22-проверки
   чипов через GUI-харнесс → §8–§13 (single instance, восстановление, живой Tor E2E, fail-closed,
   autostart).

Замена установленного сейчас 1.2.0 на исправленный кандидат безопасна: данные не мигрируют, а
верифицированный бэкап `20260924T091331Z-manual` уже лежит в корне данных.

---

## §8–§21 — ЧТО НЕ ВЫПОЛНЕНО И ПОЧЕМУ

| Раздел | Статус | Причина |
|---|---|---|
| §8 single instance (второй запуск) | **NOT RUN** | осмысленно только на исправленном кандидате; на текущем 1.2.0 наблюдалось по одному процессу каждого типа |
| §9 backend recovery | **NOT RUN** | то же |
| §10 Tor recovery | **NOT RUN** | то же |
| §11 natural-language Tor E2E | **NOT RUN** | на этом бинаре провалился бы по построению (нет кода маршрутизации); детерминированное доказательство в кодовой базе — 10 тестов, включая запись CONNECT локальным SOCKS5h-листенером и DNS-tripwire |
| §12 Tor failure E2E | **NOT RUN** | то же |
| §13 autostart ON/OFF/ON | **NOT RUN** | ждёт исправленного кандидата |
| §14 Gateway precheck | **PASS (read-only)** | `/health` **200** (v1.1.0, ready, db ok); `/updates/latest` **404**; сервис active; `current` → `adb568734430`; манифеста на сервере нет; **rollback READY** |
| §15 Gateway deploy | **NOT RUN** | по условию — только после PASS локальной приёмки |
| §16 hosting | **NOT RUN** | то же |
| §17 download-back verify | **NOT RUN** | то же |
| §18 updater RC E2E | **NOT RUN** | то же; детерминированные уровни зелёные (Rust 15, Gateway 25, Vitest 32) |
| §19 busy state | **NOT RUN** | то же |
| §20–21 Linux preview / final gates | **NOT RE-RUN** | исходный код этого шага не менялся (в 5A: Rust Linux 101, backend Linux 646/0 failed, packaging guard 5, Tor 9/9) |

---

## PRODUCTION STATE

```
main merged:                      NO
v1.2.0 tag:                       NO
stable production manifest:       NOT PUBLISHED
Gateway deployed:                 NO
Artifact hosted / uploaded:       NO
GPU:                              NOT USED (0 Pods, $0)
```

---

## FINAL

```
RELEASE BLOCKED — THE PACKAGED BACKEND IS STALE (BUILT FROM THE 1.1.0 TREE)

what worked      install 1.1 -> 1.2 (exit 0, registry 1.2.0, binary replaced)
                 data preservation 16/16, device_id unchanged, schema 0015
                 verified pre-upgrade backup through the product's own mechanism
                 bundled managed Tor ready with a fresh socks5h circuit proof
                 clean quit, no orphans
what failed      the shipped backend reports version 1.1.0 and predates the release's
                 own Tor routing work; the build stages a sidecar and never checks
                 where it came from
what is untouched  nothing in production: no deploy, no upload, no merge, no tag, no manifest
```
