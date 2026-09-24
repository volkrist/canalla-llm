# CANALLA LLM 1.2.0 — STEP 5B REPLACEMENT CANDIDATE REPORT

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `eea4de7` · Версия продукта: **1.2.0**

```
READY TO RESUME STEP 5B
```

Дефект «installer 1.2.0 содержит backend из дерева 1.1.0» устранён: guard добавлен, sidecar
пересобран из текущего дерева, сделана **одна** заменяющая production-сборка, она установлена поверх
предыдущего 1.2.0 и прошла installed acceptance. Старый кандидат **REJECTED / SUPERSEDED** и не
подлежит загрузке или публикации.

---

## ОБЯЗАТЕЛЬНЫЕ ПОЛЯ

```
OLD BACKEND SHA256:    a93149ac8aef6ad4fe5ff94de71ac3ad89654187b29729dbe50f95ead7bbc944
NEW BACKEND SHA256:    e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd

OLD INSTALLER SHA256:  e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a   (REJECTED)
NEW INSTALLER SHA256:  123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba

/health version:       1.2.0
stale-sidecar guard:   PASS
Tor natural-language E2E: NOT RUN live (причина ниже) — маршрутизационный код в артефакте доказан,
                          проводное доказательство детерминировано и зелёное
installed acceptance:  PASS
production signature:  PASS
user data preserved:   PASS
```

---

## 1. STALE-SIDECAR GUARD

| | |
|---|---|
| Инструмент | `scripts/backend-sidecar-stamp.py` (`write` / `check`) |
| Штамп | `apps/desktop/src-tauri/sidecar/alex-backend/build-stamp.json` — едет **внутри** поставляемого артефакта |
| Принудительный отказ | `BACKEND_SIDECAR_STALE`, exit code **3** — не warning |
| Где вызывается | Windows `beforeBundleCommand` → `stage-native-runtime.ps1`; Linux `stage-native-runtime.sh` |
| Кто пишет штамп | `scripts/build-backend-sidecar.ps1`, сразу после PyInstaller |

**Содержимое штампа (фактический, из установленного продукта):**

```json
{ "product": "alex-llm", "product_version": "1.2.0",
  "source_digest": "fbb275f263e204f620542f62a29520ba2efa3137484a03c566176758506483c4",
  "source_files": 135, "backend_exe": "alex-backend.exe",
  "backend_exe_sha256": "e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd" }
```

**Digest детерминирован и не завязан на mtime/размер.** Хешируется **содержимое** исходных входов
backend (`app/`, `alembic/`, `alembic.ini`, `alex-backend.spec`, `pyproject.toml`; `tests/` исключены —
тесты не поставляются), как `path \0 sha256(content)`, с нормализацией CRLF → LF, поэтому Windows- и
Linux-checkout одной ревизии дают один digest. Штамп дополнительно несёт хеш собранного бинаря, так
что не может описывать sidecar, подменённый после записи.

**Честная граница возможностей инструмента:** он доказывает, что *исходники не уехали вперёд* с
момента сборки, а не то, что бинарь скомпилирован именно из них. Штамп пишет сама сборка, поэтому в
нормальном процессе штамп не может существовать без свежего билда; но записать штамп «задним числом»
поверх старого бинаря технически возможно. Ровно поэтому в отчёте есть отдельное поведенческое
доказательство того, что в артефакте лежит именно текущая работа (§3).

**Guard действительно срабатывает — проверено на реальном дереве до пересборки:**

```
BACKEND_SIDECAR_STALE stamp_missing
exit code: 3
```

**Regression tests** — `apps/backend/tests/test_sidecar_stamp.py`, **9 passed**: свежий штамп → PASS;
неверная product_version → FAIL; изменённые исходники backend → FAIL; более старый бинарь sidecar →
FAIL; отсутствующий штамп → FAIL; валидный свежесобранный sidecar → PASS; смена только переводов
строк digest не меняет (ложного расхождения нет); изменение `tests/` сборку не инвалидирует; и тест
по реальному дереву «застейдженный sidecar актуален» — он же и поймал бы регресс.

---

## 2. SIDECAR REBUILT FROM THE CURRENT TREE

Сборка `scripts/build-backend-sidecar.ps1` (PyInstaller, `alex-backend.spec`), затем штамп.

| | Старый (в отгруженном 1.2.0) | Новый |
|---|---|---|
| Размер | 23 528 424 | **23 536 323** |
| SHA256 | `a93149ac…` | **`e2e87ad2…`** |
| Датирован | 23 сен 21:27 | 24 сен (пересобран сейчас) |

**Доказательство, что новый sidecar содержит текущую работу:**

1. **`/health` → `"version":"1.2.0"`** — запуск сразу после сборки, изолированный корень данных:
   `{"status":"ok","provider":"llamacpp","llm_ready":false,"product":"alex-llm","version":"1.2.0",
   "runtime_protocol_version":1,"instance":null}`
2. **Маршрутизационный код в артефакте доказан поведенчески** — из OpenAPI **запущенного
   упакованного** бинаря: поле `network_route` присутствует в теле `POST /tools/execute`
   (`properties: ['chat_id', 'name', 'arguments', 'network_route']`). Это то самое поле, которое
   добавила работа по Tor-маршрутизации 24 сентября; старый бинарь его не имел.
3. **Staged sidecar ≠ старый**: `e2e87ad2…` ≠ `a93149ac…`, и **установленный** sidecar совпал со
   свежим: `e2e87ad2…`.

---

## 3. ОДНА ЗАМЕНЯЮЩАЯ PRODUCTION BUILD

| | |
|---|---|
| Инсталлятор | `Canalla LLM_1.2.0_x64-setup.exe` |
| Размер | **92 463 450** байт |
| SHA256 | **`123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba`** |
| Updater signature | `…x64-setup.exe.sig`, 424 байта, SHA256 `29a41b0e5a62f0dd819e31d502fe40491535c42ff19192ca6982b53907685b30` |
| Production key | **та же** идентичность, новый ключ не создавался |
| Embedded client pubkey | **`9B328EFF111D1FB2`** |
| Подпись (независимо) | **`minisign 0.11`**: «Signature and comment signature verified» |
| Version binding | **PASS** — подписанный trusted comment: `timestamp:1790242693  file:Canalla LLM_1.2.0_x64-setup.exe` |
| Bundled Tor | **0.4.9.12** (pin `scripts/tor-runtime.json`) |
| Backend source stamp | **current** (guard exit 0 после сборки) |
| Guard при bundling | прошёл внутри сборки — иначе сборка упала бы |

Компоненты новой сборки:

```
alex-llm.exe         51388abc19df0bb619157a57e8f7a61f1d4502c2dd06711081491fd6f88ef6a2   5 961 728
alex-backend.exe     e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd  23 536 323
alex-host-loop.exe   1f3b4ec6ff126637bd9e9582025c0e12c1e787860ad85218bbf3e3ae90ca2934
tor.exe              60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c  10 222 592
```

---

## 4. INSTALLED ACCEPTANCE (замена поверх установленного 1.2.0)

| Проверка | Результат |
|---|---|
| Установка `/S` поверх текущего 1.2.0 | **PASS** — exit 0, реестр `Canalla LLM 1.2.0` |
| Установлен именно свежий backend | **PASS** — `e2e87ad2…` на месте, штамп едет внутрь артефакта |
| **`/health` из установленного продукта** | **PASS — `"version":"1.2.0"`** |
| Backend healthy | **PASS** — `status: ok`, `product: alex-llm`, protocol 1 |
| Tor Ready + circuit proof | **PASS** — `verified: true`, `source: managed`, `method: socks5h`, `0.4.9.12`, свежий proof |
| Tor source = bundled | **PASS** — демон запущен из каталога продукта |
| Single instance (второй запуск) | **PASS** — по одному процессу каждого типа, дубликата нет |
| Backend kill → recovery | **PASS** — PID 5820 → **9716**, `/health` вернулся, Tor переподтверждён (свежий proof, новый pid), без клика |
| Tor kill → recovery | **PASS** — PID 13912 → **15724**, свежий circuit proof, SOCKS:9050 слушает новый процесс, без Retry |
| Autostart | **PASS (ON)** — `HKCU\…\Run` → `Canalla LLM` = `"…\Programs\Canalla LLM\alex-llm.exe"`, корректный установленный exe |
| Выход | **PASS** — ни процессов, ни слушателей на 8000/9050 |
| **Данные сохранены** | **PASS** — 16/16 категорий, `device_id 908c2242-…` не изменился |

### Чего в этой приёмке нет — и почему

| Не проверено | Точная причина |
|---|---|
| Autostart OFF → ON (цикл переключения) | Тоггл живёт в Settings, нужен GUI; механизм покрыт детерминированно (`login_read/write` + unit-тесты), в реальном реестре подтверждено состояние ON и правильный путь |
| Чипы AI / Computer / Memory в живом окне | `/status` требует сессию (`Session expired`), а GUI-харнесс в этом шаге не запускался; косвенно: Computer и Tor живы (heartbeat, свежий proof), AI по `llm_ready: false` — Disconnected |
| **Живой natural-language Tor E2E** | Нужен работающий LLM: без провайдера чат не доходит до маршрутизации, а Pod — это GPU, запрещённый условиями шага. Что доказано вместо: маршрутизационный код **в артефакте** (`network_route` из OpenAPI запущенного бинаря), и проводное доказательство в наборе (локальный SOCKS5h-листенер пишет CONNECT, DNS-tripwire отвергает любой не-loopback резолв, `tor_fetch` отдаёт типизированный код вместо ложного «completed») |
| §14–§19 (Gateway deploy, hosting, download-back, updater RC E2E, busy state) | Прямо отложены условием шага: «После исправления НЕ выполнять пока» |

---

## 5. ЛИНЕЙКА ГЕЙТОВ ПЕРЕД СБОРКОЙ

| Гейт | Результат |
|---|---|
| `test_sidecar_stamp.py` (новый guard) | **9 passed** |
| Backend: stamp + sidecar packaging + version consistency + Tor-наборы | **99 passed** |
| Rust `updater_signature` | **15 passed** |
| Gateway `test_updates.py` | **25 passed** |
| Frontend: updater + packaging guard | **37 passed** |

Сборка выполнена **один раз** после PASS этих гейтов. Rebuild loop не выполнялся.

---

## 6. DATA PRESERVATION

```
users 2 · chats 8 · messages 34 · projects 1 · memories 2 · documents 0
paired_devices 2 · compute_preferences 1 · compute_sessions 3 · compute_events 17
tool_runs 8 · message_contexts 17 · generation_usage 17 · web_source_snapshots 24
local_tasks 0 · auth_sessions 3 · schema revision 0015
device_id 908c2242-65c4-4dd9-bfc3-c168de3a37c4
```
Все 16 категорий совпали с baseline; различий нет. Успешный тест 1.1 → 1.2 сохранён как evidence
(`20260924T091331Z-manual`, verified, 2 файла, 540 919 Б) — искусственный откат на 1.1.0 не делался.

---

## 7. PRODUCTION STATE

```
Gateway deployed:            NO
nginx changed:               NO
Artifact uploaded:           NO
main merged:                 NO
v1.2.0 tag:                  NO
stable production manifest:  NOT PUBLISHED
GPU:                         NOT USED (0 Pods, $0)
old defective candidate:     REJECTED / SUPERSEDED — must not be uploaded or published
```

---

## FINAL

```
READY TO RESUME STEP 5B

fixed      the build now refuses a backend from an older source tree
           (BACKEND_SIDECAR_STALE, exit 3, 9 regression tests)
rebuilt    backend from the current 1.2.0 tree, staged, and proven by behaviour
           /health -> 1.2.0 and network_route present in the packaged binary
replaced   one new production-signed installer 123c8ced… (old e13ba09c… rejected)
accepted   installed over the previous 1.2.0: 1.2.0, Tor ready with a circuit proof,
           single instance, backend and Tor recovery, autostart ON, clean quit,
           all 16 data categories and the device identity preserved
open       live natural-language Tor E2E (needs an LLM; GPU forbidden here),
           autostart OFF/ON toggle cycle (needs the GUI), and §14-§19, deferred by instruction
```
