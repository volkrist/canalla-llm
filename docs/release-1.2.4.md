# Canalla LLM 1.2.4 — сборка, установка и приёмка (26.10.2026)

> **Публикация 26.10.2026:** H5 и H6 — живые **PASS**
> (`artifacts/acceptance-123/runs/live-20261026T110323`). Production-подпись
> **VALID** (`.sig`, key `9B328EFF111D1FB2`). **v1.2.4 опубликована:**
> https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4
> Разделы ниже, помеченные BLOCKED, фиксируют состояние **до** закрытия этих
> гейтов; актуальные результаты — в `artifacts/release-notes-v1.2.4.md` и
> `artifacts/REPORT-canalla-1.2.4-FINAL-2026-10-26.md`.

**Статус: СОБРАНО И УСТАНОВЛЕНО, НЕ ОПУБЛИКОВАНО.**
Публикация остановлена двумя незакрытыми гейтами: live H5/H6 и подпись апдейтера.
Этот документ фиксирует ровно то, что проверено, и не выдаёт BLOCKED за PASS.

## Артефакт и установка

| Параметр | Значение |
|---|---|
| Ветка | `release/canalla-1.2.4` (база `703a1cf4c3bcd299a41db0265ea724d8e5b35c3c`) |
| Версия продукта | `1.2.4` (frontend / backend / desktop / `tauri.conf.json`) |
| Установщик | `Canalla LLM_1.2.4_x64-setup.exe` |
| Размер | `95 465 293` B |
| SHA256 | `8db307489f0b5f0da077158e50d329a57d4fcd2c64707c3f149a2196f9c8de7e` |
| Установка поверх 1.2.4 | exit `0`, `%LOCALAPPDATA%\Programs\Canalla LLM` |
| Sidecar | `alex-backend.exe` SHA256 `caa706cade567ebac70b9ef2ad390895c4b6a14b42e1dae562b7dd739b7e989f` |
| Sidecar stamp | `product_version 1.2.4`, source digest `39899e993fd8f0cfebbcc4210b21998cb25b03589e75c7c0de8cf41711c41fe8` |
| Native host | `alex-host-loop.exe` SHA256 `9b80118842fbb49cacfcb89ee576634e29330298ede72a0313729d1bf55a29d1` |
| Tor | встроенный `0.4.9.14` из pinned-бандла `15.0.25` |
| Подпись апдейтера | **ОТСУТСТВУЕТ** — пароль production-ключа недоступен в этой среде |
| Authenticode | не подписан (сертификат не требуется заданием) |

### Сохранность данных (до/после установки)

- Проверенный backup продукта: `20261026T100723Z-manual`, `verified: true`, 2 файла,
  2 453 751 B, schema `0016`, app `1.2.4`.
- Чатов до установки: **31**; после установки: **31**.
- Устройство: то же `device_id` `294c9e2f-ba51-4a56-94b1-ad5b62bfbf42`, online.
- `computer_mode = ask`, Tor включён; Compute Preferences: `$2.00/ч`, `$2.00/сессия`,
  48 GB, автоматический выбор — сохранены.
- Canalla Cloud: `connected`; Computer / Tor / Memory: `ready`.

## Найденные и исправленные дефекты

| № | Слой | Дефект | Исправление | Регрессия |
|---|---|---|---|---|
| 1 | Backend (`app/cloud/routes.py`) | `/cloud/compute/stop` перезаписывал авторитетный stop-ответ ответом `/compute/wait/cancel`, который мог ещё описывать `generating`. Чип показывал `AI Ready / generating` после Stop, а `last_error` оставался `gateway_unavailable`. | Stop-ответ сохраняется; ответ wait-cancel используется только ради side-effect; успешный typed stop очищает устаревшую ошибку. | `test_the_stop_answer_is_authoritative_when_cancel_wait_is_stale` |
| 2 | Frontend (`Workspace.tsx`, `lib/stop-ai.ts`) | «Остановить AI» не прерывал активный ответ. Gateway (намеренно) ждёт завершения генерации, поэтому Pod продолжал биллиться. | Stop AI сначала прерывает генерацию, затем останавливает compute; порядок закреплён тестом. | `lib/stop-ai.test.ts` |
| 3 | Acceptance harness (`runner.py`) | T2 пропускался как «UI script not requested», хотя его Stop-flow не требует UI-скрипта. | T2 исключён из UI-скипа. | `runner.py --self-test` |
| 4 | Acceptance harness | T2 сэмплировал Pod/watchdog через 8 с и объявлял корректную остановку FAIL. | Опрос до 120 с до `pods []` + `confirmed_stopped/closed`, затем отдельная проверка отсутствия reconnect. | evidence `live-20261025T225917` |
| 5 | Acceptance harness | Проверка `"pods=[]" in json.dumps(...)` никогда не совпадала: helper печатает `pods []`. | `pods_empty()` для всех проверок. | `runner.py --self-test` |
| 6 | Acceptance harness | `print()` падал с `UnicodeEncodeError` на заголовке H5 (`→`) в cp1251-консоли; последний Pod был потрачен до запуска сценариев. | stdout/stderr переконфигурированы с `errors="replace"`. | ручная проверка + self-test |
| 7 | Acceptance harness | `--scenario H6 H5` исполнял каталоговый порядок (H5 раньше H6). | Явный список сценариев задаёт порядок исполнения. | `runner.py --self-test` |
| 8 | Acceptance harness | T7 не задавал sandbox-путь, и модель изобретала `C:\Users\alex\Desktop\test_project`; harness справедливо отклонял запуск, а сценарий ложно проходил по слову «тайм-аут». | T7 задаёт папку и `timeout_seconds=3` для 30-секундного скрипта; подтверждён реальный timeout. | evidence `live-20261025T222725` |
| 9 | Acceptance harness | `safe_run` ограничивал подтверждения каталогом сценария, а не всем выделенным sandbox. | Граница — `Canalla Acceptance`; вне неё по-прежнему отказ. | `runner.py --self-test` |
| 10 | Acceptance harness | `fault-proxy-app.ps1` содержал абсолютный путь чужого worktree. | Путь вычисляется от `$PSScriptRoot`. | H6 fault-proxy probe |
| 11 | Staging | Sidecar в дереве был `1.2.0`, Tor — `15.0.24`, оба не совпадали с пином. | Sidecar пересобран (`1.2.4`), Tor перекачан на `15.0.25`. | `test_sidecar_stamp`, `test_tor_bundle` |
| 12 | Versioning | Тесты требовали равенства версии Gateway и desktop, хотя Gateway деплоится отдельно. | Gateway package/config проверяются друг против друга; desktop-версия не форсирует Gateway. | `test_version_consistency`, `test_model_replicas` |

Gateway **не изменялся и не переразвёртывался**. Его правило «manual stop ждёт завершения
активной генерации» сохранено; продукт теперь снимает генерацию сам (дефект 2).

## Live-приёмка Qwen

| Сценарий | Вердикт | Доказательство |
|---|---|---|
| H4 — мультиисточниковое исследование | **PASS** | `runs/live-20261025T221419/H4.json`: model-origin `web_search` + 3×`web_fetch`, 8 реальных источников, отчёт со ссылками; Pod 495.3 с, `within_cap` |
| H5 — Web → файлы → Python → тесты → отчёт | **BLOCKED** | 8-й Pod был израсходован harness-crash (дефект 6) до запуска сценария; лимит 8 Pod исчерпан |
| H6 — восстановление после сетевого обрыва | **BLOCKED** | тот же 8-й Pod; fault-proxy self-test/probe PASS, но live-прогона нет |
| T2 — Stop AI посреди генерации | **PASS** | `runs/live-20261025T225917/T2.json`: первый delta на 114.9 с, abort, Pod подтверждён остановленным через 187.5 с от создания, `pods []`, `no_reconnect=true`, статус `AI Waiting/stopping` → `AI Off` |
| T5 — отказ Web-провайдера | **PASS** | `runs/live-20261025T222330/T5.json`: model-origin `web_fetch` → typed failure; ответ «страница недоступна», содержимое не выдумано |
| T6 — отказ в разрешении | **PASS** | `runs/live-20261025T222330/T6.json`: `run_python` отклонён, статус `stopped`, файла нет, модель признала отказ |
| T7 — тайм-аут инструмента | **PASS** | `runs/live-20261025T222725/T7.json`: 30-секундный скрипт, `timeout_seconds=3`, процесс прерван по тайм-ауту |
| T3 — перезапуск UI/восстановление | **PASS (free)** | installed `always-ready.mjs`: relaunch без логина, та же сессия/устройство, Computer/Tor снова ready. Live-перезапуск **посреди** платной задачи не выполнялся (нет Pod) |
| T4 — Computer host down | **PASS (free, ограниченно)** | installed crash-recovery E2E: sidecar убит, Computer/Tor восстановлены автоматически, heartbeat и то же устройство; host smoke PASS. In-process host не имеет отдельного kill-switch, отдельный `alex-host-loop.exe` в установленной сборке не запускается |
| T8 — UI во время streaming | **PASS (free)** | Playwright `generation-navigation` + installed-window navigation; live-поток Qwen в этом проходе не проверялся |

Ранее подтверждённые S1–S7, M1–M7, H1–H3 и T1 не перепрогонялись платно; их evidence
сохранён в `artifacts/acceptance-123/runs/`.

## Бесплатные гейты

| Гейт | Результат |
|---|---|
| Backend pytest | **988 passed**, 1 skipped |
| Backend Ruff / format | PASS |
| Gateway pytest | **261 passed** |
| Gateway Ruff / format | PASS |
| Frontend Vitest | **313 passed** (28 файлов) |
| Frontend Prettier / tsc / Vite build | PASS |
| Rust `cargo test` | **126 passed** (36 host + 74 desktop + 16 updater) |
| Playwright E2E | **21 passed** |
| `release-gate.py developer` | **9 steps · 0 FAIL · 0 BLOCKED — PASS** |
| Acceptance preflight (installed) | PASS: version 1.2.4, cloud connected, computer_mode ask, 1 device, pods `[]`, host smoke, pytest on host, UI navigation |
| Installed `always-ready.mjs` | **PASS 32/32** — launch, relaunch, Tor daemon kill/recovery, sidecar crash recovery, same `device_id`, one device |
| Artifact name / SHA256 | PASS |
| Artifact signature | **FAIL** — `.sig` отсутствует |

## Деньги и Pod

| Параметр | Значение |
|---|---|
| Pod за проход | **8 из 8** (включая 8-й, потерянный на harness-crash) |
| GPU | NVIDIA L40S 48 GB, `$1.09/ч`, US-TX-3 |
| Баланс до | `$7.31256` |
| Баланс после | `$6.67718` |
| Потрачено | **≈ $0.635** (лимит $2.00) |
| `pods` | `[]` |
| watchdog | `closed` |
| Максимальная подтверждённая длительность Pod | 495.3 с (`within_cap: true`) |

## Блокеры публикации

1. **H5 и H6 не имеют live model-origin PASS.** Лимит 8 Pod исчерпан; 8-й Pod был
   израсходован ошибкой кодировки harness до запуска сценариев. Ошибка исправлена, но
   новый Pod запрещён условиями задания.
2. **Нет подписи апдейтера.** Production-ключ `9B328EFF111D1FB2` присутствует, но его
   пароль недоступен в этой среде; `release-gate.py verify-artifact` даёт
   `artifact.signature — missing signature`. Без `.sig` автообновление не примет артефакт.
3. **Authenticode отсутствует** — по условиям задания сертификат не приобретался.
4. **T3/T8 в live-варианте** (перезапуск UI посреди платной генерации, UI во время реального
   потока Qwen) не выполнялись без GPU; бесплатные аналоги зелёные, но это не заменяет
   live-доказательство.
5. **Остаточный риск Gateway:** сторонний API-клиент, вызывающий `/compute/stop` без
   прерывания своего потока, оставит Pod до конца генерации. Штатный UI теперь прерывает
   поток; контракт Gateway не менялся.

## Готовность

- **Локальное использование установленной 1.2.4: ДА** — данные сохранены, Computer/Tor/Memory
  ready, H4/T2/T5/T6/T7 и все бесплатные гейты зелёные.
- **Публичный релиз 1.2.4: НЕТ** — H5/H6 BLOCKED и отсутствует подпись апдейтера.
