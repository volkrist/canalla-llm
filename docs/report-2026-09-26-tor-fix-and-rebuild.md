# Canalla LLM 1.2.0 — production Tor fetch: фикс, пересборка, хостинг (26.09.2026)

**Задача:** вернуть production-путь `«Открой https://example.com через Tor»`: явный URL в промпте обязан
фетчиться серверной политикой даже там, где модель не умеет вызывать инструменты (shared-режим), при
неизменной границе безопасности и нулевом clearnet-fallback.

**Итог прохода:** фикс реализован и покрыт тестами (включая доказательство «без фикса падает»), sidecar
пересобран один раз, выпущен **новый** установщик с production-подписью, установка поверх выполнена,
всегда-готовность Computer/Tor подтверждена, публичный хостинг заменён и download-back проверен, Gateway
решено **не** передеплоивать (исходники байт-идентичны). Остался единственный платный гейт — живая
приёмка (ёмкость US-TX-3), дежурство вооружено.

---

## 1. Корень дефекта (был доказан кодом)

| Место | Факт |
|---|---|
| `apps/backend/app/cloud/provider.py` | `class GatewayProvider: supports_tools = False` — в production (shared) режиме модель не вызывает инструменты |
| `apps/backend/app/tools/orchestrator.py` (было, строка 160) | ранний возврат `if not provider.supports_tools and not paid_needed and not local_needed:` → `web_status: unavailable/model_tools_unsupported` + заметка «No web results available: tool calling unsupported.» |
| там же (было, строка 397) | детерминированная серверная политика `tor_fetch` для URL из промпта жила **ниже** этого возврата → в shared-режиме недостижима |

Следствие: живой Tor-turn отвечал типизированным отказом, хотя Tor-сервис был готов и с proof.

## 2. Фикс (узкий, без расширения границы)

* политика вынесена в **один** метод `ToolOrchestrator._server_policy_tor_url(context, prompt, tor_intent, notes)`
  (никакого дублирования: тот же метод вызывается и на обычном пути);
* при `provider.supports_tools = False` политика вычисляется **до** проверки tool-возможностей; если она
  отработала, ответ строится из её результата (`ContextBuilder.with_web(history, insert_at, context.sources,
  policy_notes)`) — модель не спрашивают о tool-calls вообще;
* **произвольные** model-driven инструменты в shared-режиме по-прежнему запрещены: без детерминированной
  политики путь до отказа не менялся;
* отказ политики остаётся типизированным и fail-closed: `tor_unavailable` / `unsafe_url`, `clearnet = 0`.

## 3. Тесты (`apps/backend/tests/test_tor_research.py`, 6 новых)

| Тест | Что доказывает |
|---|---|
| `test_shared_mode_still_fetches_a_named_tor_url` | shared-провайдер (`supports_tools = False`) + «Открой http://example.com/ через Tor» → `tor_fetch` выполнен, `origin=server_policy`, `metadata.transport=tor-socks5h`, `socks.atyp=3`, `local_dns=False`, `route=TOR_ONLY`, `verified_chain=True`, источник дошёл до контекста ответа (`web-sources`, канал `tor`) |
| `test_shared_mode_tor_unavailable_is_typed_and_never_clearnet` | нет слушателя → `status=failed`, `error_code=tor_unavailable`, ровно одно ограниченное восстановление, `clearnet_runs=[]`, локальных DNS-обращений 0 |
| `test_shared_mode_route_that_cannot_serve_the_url_fails_typed` | маршрут не может отдать URL (https через HTTP-only прокси) → типизированный отказ, имя ушло как имя (ATYP 3), clearnet 0 |
| `test_shared_mode_still_refuses_model_driven_tools` | произвольный prompts без URL → инструментов 0, заметка «tool calling unsupported» на месте |
| `test_shared_mode_ordinary_chat_is_untouched` | обычный чат не затронут: инструментов 0, ответ модели |
| `test_shared_mode_keeps_the_existing_tor_url_safety` | `127.0.0.1`, `localhost`, `file://` → ни одного запроса, clearnet 0 |

**Доказательство, что тесты действительно фиксируют дефект:** с временно откаченным фиксом
(`git checkout` на HEAD-файл) падают ровно три «fetch»-теста и проходят три «граница»-теста:

```
FAILED test_shared_mode_still_fetches_a_named_tor_url
FAILED test_shared_mode_tor_unavailable_is_typed_and_never_clearnet
FAILED test_shared_mode_route_that_cannot_serve_the_url_fails_typed
3 failed, 3 passed
```

После возврата фикса — 6 passed. Полный backend: **787 passed, 1 skipped** (stale-sidecar guard снова
зелёный), `ruff check` / `ruff format --check` — чисто.

## 4. Sidecar (собран один раз)

| | |
|---|---|
| Размер | **23 741 190** B |
| SHA256 | **`3bf2dc6a7e65563d24ddb6a8afc9feb8eab027354abe046604939a69c802e0bb`** |
| `product_version` | `1.2.0` |
| `source_digest` | `18c21bd1e46cbeb882775ea16555106915ca360d1b871a0cefdf43ce9bef6271` (138 файлов) |
| Штамп | записан сборкой; bundle-шаг подтвердил `backend sidecar is current` / `SIDECAR stamp is current` |
| Предыдущий | `fa46d07945af48b95fece98d2e934e7d6e42836c3df84f17ebdadef3d841a997` — **SUPERSEDED** |

## 5. Установщик (один новый кандидат)

| | |
|---|---|
| Файл | `Canalla LLM_1.2.0_x64-setup.exe` |
| Размер | **95 340 864** B |
| SHA256 | **`61a99cfcb2fa768a5a4840431836909af2e705927daa4e489719b9943e3beac4`** |
| Подпись | `…exe.sig`, 424 B; **VERIFIED** — Ed25519 над BLAKE2b-512, ключ **`9B328EFF111D1FB2`** (trusted comment покрывает имя файла), контроль на старом установщике тоже проходит |
| Desktop `alex-llm.exe` (установленный, после NSIS-патча) | `47e9b7fa6c6dff59d8571f9d376c75ef1814430926cd9121d3dc8e66cff69531` |
| Authenticode | **NOT SIGNED** |
| Предыдущий | `ae863e92…b971929` (92 493 319 B) — **SUPERSEDED**, перенесён в `superseded/` на сервере |

Сборка одна: `production-signing.ps1 -Action Build` (ключ вне репозитория, пароль из Credential Manager,
не печатался и не записывался).

## 6. Установка поверх и installed-приёмка

* инсталлятор: exit 0; данные `%LOCALAPPDATA%\Alex LLM\` не тронуты;
* установленный sidecar **совпадает** с новым: `3bf2dc6a…`, установленный штамп несёт новый `source_digest`;
* `e2e/always-ready.mjs` → **ALWAYS READY PASS**: Computer «Готово» и Tor «Готово» на нормальном запуске,
  после перезапуска (тот же `device_id`, одно устройство) и после убийства sidecar (новый процесс,
  `/health` отвечает, сессия переживает падение);
* `--phase observe` (реальная установка, реальный корень данных) → сессия восстановлена, AI честно
  `disconnected/off`, Computer «Готово», Tor поднимается сам.

Отдельно отмечено и исправлено в harness: продукт single-instance, и запуск, «проглоченный» ещё
выходившим окном, показывал «Локальный сервер недоступен» — теперь запуск ждёт освобождения окна и
повторяется один раз (с дампом DOM при неудаче).

## 7. Хостинг и download-back

| Шаг | Результат |
|---|---|
| Выложено | новый `Canalla LLM_1.2.0_x64-setup.exe` (95 340 864 B, SHA `61a99cfc…`) + `.sig` в `/srv/canalla-downloads/` |
| Старое | перенесено в `/srv/canalla-downloads/superseded/…SUPERSEDED-ae863e92.exe` (не рекламируется) |
| Download-back (публичный HTTPS, с VPS) | `http=200 bytes=95340864`, SHA256 **совпал** |
| Подпись скачанного | **VERIFIED** (production-ключ) |
| Временные копии | удалены на сервере и локально |

## 8. Gateway — передеплой **не** нужен

| Проверка | Результат |
|---|---|
| `gateway/**` (14 файлов) | **байт-идентичны** локальному дереву (сводный дайджест отличается только порядком сортировки в разных локалях; пофайловые хеши совпали) |
| Backend-модули, которые импортирует Gateway (`app/compute/{runpod_api,runtime,schemas,candidates,replicas}.py`) | **идентичны** |
| Решение (§15) | оставить развёрнутый `/opt/alex-gateway/releases/1994c0ab2016`, зафиксировать равенство хешей |
| `/health` | 200 · `1.2.0` · `ready:true` · `database:ok` |
| `/updates/latest` | **204** (манифест не опубликован) |

Изменился только клиентский backend (`app/tools/orchestrator.py`), который Gateway не импортирует.

## 9. Финальная регрессия

| Набор | Результат |
|---|---|
| backend pytest | **787 passed, 1 skipped** |
| gateway pytest | **201 passed** (+ целевые `test_migrations.py`+`test_cli.py` — 16 passed) |
| frontend vitest | **274 passed** (22 файла) |
| desktop rust `cargo test` | **95 passed** (21 + 58 + 16) |
| installed `e2e/always-ready.mjs` | **PASS** |
| sidecar stamp (`test_sidecar_stamp.py`, bundle-шаг) | **PASS** |

Честное наблюдение: два прогона gateway-набора, запущенные **одновременно** с тяжёлой сборкой
(`cargo test --release`), дали тайминговые падения в `test_migrations.py` / `test_cli.py`; те же файлы
в изоляции и на «тихом» повторном прогоне проходят (16 passed, затем весь набор 201 passed). Это
существующая чувствительность набора к нагрузке, не регрессия этой правки (её область — backend, который
gateway не импортирует).

## 10. Что осталось — единственный гейт

Живая приёмка на ёмкости US-TX-3: один Pod, `--phase all` (чат → Tor → Stop), при этом Tor-запрос обязан
**реально выполнить fetch** и вернуть источник. Дежурство: watcher (60 с, `--poll-seconds 60`), Pod-ов 0,
траты $0.

При PASS → §23–§27: merge в `main` → push → аннотированный `v1.2.0` → push → pre-manifest gate →
публикация манифеста последним действием → read-only пост-проверка.

```
TOR FIX            : DONE    (f0468af, 6 новых тестов, 3 из них падают без фикса)
SIDECAR            : 3bf2dc6a… · 23 741 190 B   (fa46d079… SUPERSEDED)
INSTALLER          : 61a99cfc… · 95 340 864 B   (ae863e92… SUPERSEDED) · подпись VERIFIED
INSTALLED ACCEPT   : PASS    (install-over, sidecar hash, всегда-готовность Computer/Tor)
HOSTING            : PASS    (download-back 200/SHA/подпись)
GATEWAY            : UNCHANGED (исходники байт-идентичны) · health 200/1.2.0/ready · /updates/latest 204
REGRESSION         : backend 787 · gateway 201 · frontend 274 · rust 95 · always-ready PASS
LIVE ACCEPTANCE    : WAITING FOR US-TX-3 GPU CAPACITY  (Pod-ов 0, траты этого прохода $0)
PUBLICATION        : NOT STARTED
```
