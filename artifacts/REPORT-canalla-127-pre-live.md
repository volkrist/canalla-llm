# Canalla 1.2.7 — pre-LIVE отчёт

**Дата:** 13 ноября 2026.
**Ветка:** `feature/canalla-1.2.7-autonomous-agent`.
**Исходный SHA:** `78333cdd1da6836bca9b42ca190a8b67424f09e0`.
**Публичная версия:** 1.2.6 — не изменялась.

---

## 1. Итог

| Гейт | Результат |
|---|---|
| **Windows packaged host E2E** | **PASS** — полный путь backend → ToolExecutor → Computer Connect → `alex-host-loop.exe` → UIA → WinForms |
| Computer Connect | pair, heartbeat, claim jobs, повторное подключение после перезапуска — работают |
| Windows UIA | ввод, сохранение, закрытие, повторное открытие, чтение — подтверждены файлом и скриншотами |
| Qwen LIVE readiness | **READY** (preflight без блокеров), запуск не выполнялся |
| Watchdog / cost limits | подготовлены: 480 с, $0.145, стоп на 430 с |
| Authenticode | **NotSigned** — план получения доверенной подписи описан, покупка не выполнялась |
| Git / CI | итог `b9d85a2`, **CI 7/7 success** |

---

## 2. Packaged native host E2E

`artifacts/acceptance-127/packaged_host_e2e.py` — без fake-host и без прямого вызова `uia.ps1`.

Цепочка: изолированный backend (uvicorn, отдельная БД) → `ToolExecutor` → HTTP-протокол устройства
(`/tools/devices/jobs`, `/heartbeat`, `/result`) → **собранный `alex-host-loop.exe`** (2 217 472 байт,
release, из текущего дерева) → встроенный `uia.ps1` → Windows UI Automation → скомпилированное
WinForms-приложение.

| Шаг | Наблюдение |
|---|---|
| Запуск host | штатная пара `ALEX_BACKEND_URL` + `ALEX_TOKEN`, pair через продукт |
| Обнаружение окна | `wait_window` → `Canalla Acceptance App` |
| Ввод текста | `set_value` → `UIA_PACKAGED_417`, прочитано обратно |
| Save | `invoke` → `BM_CLICK` |
| Файл | `uia_out.txt` = `UIA_PACKAGED_417` |
| Закрытие | `close_window` → окно исчезло |
| Повторное открытие | второй процесс поднял приложение |
| Чтение данных | `get_text` = `UIA_PACKAGED_417`, `.loaded` совпадает |
| Независимая проверка | `verify_result`: `file_contains`, `file_exists` — `evidence_ok = true` |
| Задача | **COMPLETED** |
| Скриншоты | `01-saved.png`, `02-reopened.png` |
| Reconnect | host остановлен, запущен заново → снова paired, job выполнен |
| Мусор после теста | `alex-host-loop` = 0, `AcceptanceApp` = 0 |

```
verdict: PASS
task_state: COMPLETED
evidence_ok: true
saved_document: UIA_PACKAGED_417
reloaded_document: UIA_PACKAGED_417
```

Evidence: `artifacts/acceptance-127/packaged-host/`.

---

## 3. Найденный и исправленный дефект (важно, с последствием для identity)

**Симптом.** При первом прогоне host получал `401 {"detail":"device_auth"}` от собственного backend,
задача уходила в `WAITING_DEVICE`.

**Причина.** `credential_target()` принимал `ALEX_DEVICE_CREDENTIAL_TARGET` только с префиксом
`Alex LLM/`, иначе **молча** возвращал production-цель `Alex LLM/device-credential`. Наш harness
указывал `Canalla Acceptance/packaged-uia`, значение игнорировалось, и host писал/читал credential
реальной установки.

**Последствие.** Установленное приложение обнаружило несовпадение credential и пере-спарилось:
`f3548022-a676-463c-9fc5-b05bbd5ac907` → `861dfefa…` → `56e4716c…` → `66276759…` → `6413489d…`
(каждое предыдущее помечено `revoked_at`). Цепочка остановилась после исправления.

**Что не пострадало.** Пользовательские данные: `users 2, chats 54, messages 120`, `integrity ok`.
`local_tasks` не тронуты. Старые записи устройств сохранены в `paired_devices` (revoked), ничего не
удалено. Credential прежнего устройства восстановить нельзя — сервер хранит только его хеш.

**Исправление.**
- любой безопасный target принимается как есть (включая `Canalla Acceptance/…`);
- непригодное значение больше **никогда** не отображается на production-цель — оно превращается в
  `Alex LLM/unusable-<hash>`;
- добавлен regression-тест `an_acceptance_target_with_another_prefix_is_honoured`.

После исправления повторный прогон прошёл с изолированным target: установленное приложение
сохранило своё устройство (`6413489d…`, online), новых записей не появилось.

**Текущее состояние identity:** `6413489d-6cae-4183-8446-a3b2339137dc`, `device_matches_local: true`,
`device_online: true`. Это изменение зафиксировано честно: исходный `f3548022…` заменён цепочкой
пере-спариваний, вызванной дефектом harness-пути.

---

## 4. LIVE Qwen readiness

`artifacts/acceptance-127/live-qwen/preflight.py` (read-only) → **READY**, блокеров нет:

| Проверка | Значение |
|---|---|
| Gateway `/health` | 200, `ready` |
| Production updater | 204 для 1.2.6 — не менялся |
| Pods | `[]`, 0 |
| Баланс RunPod | $8.5727707286 |
| Установленное приложение | 1.2.6, `/health` 200 |
| Данные | 2 / 54 / 120, integrity ok |
| Device | online, совпадает с локальным |
| Диск | 70.4 ГБ свободно |
| Бюджет | cap $0.15, худший случай 480 с ≈ $0.1453 |

План: `artifacts/acceptance-127/live-qwen/PLAN.md`. Ключевое: один Pod, ≤480 с, watchdog на 430 с,
остановка и `pods []`, критерии PASS по `origin=model`, файлам, тестам, HTTP, GUI, исправлению,
`evidence_ok`, отсутствию дублей. **Платный запуск не выполнялся.**

---

## 5. Authenticode

`Get-AuthenticodeSignature` для `Canalla LLM_1.2.6_x64-setup.exe`
(sha256 `485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1`) → **NotSigned**.

Updater-подпись (minisign, ключ `9B328EFF111D1FB2`) — отдельный механизм и остаётся в силе; она
защищает цепочку обновления, но не установщик в глазах Windows/SmartScreen.

Что нужно для доверенной Authenticode-подписи:

1. **Сертификат от публичного CA** (OV или EV Code Signing). Требуется проверка личности/компании;
   покупка и выпуск — только владельцем.
2. **Приватный ключ на FIPS 140-2 Level 2 hardware**: USB-токен (SafeNet eToken) или облачный HSM
   (Azure Trusted Signing, DigiCert KeyLocker, SSL.com eSigner). Файловый `.pfx` с 2023 года не
   выдаётся для публичного доверия.
3. **Подпись**: `signtool sign /fd SHA256 /tr <RFC3161 URL> /td SHA256 /a <installer>`; для HSM —
   через провайдера CA. Timestamp обязателен.
4. **Проверка**: `Get-AuthenticodeSignature` = `Valid`, `signtool verify /pa /all`.
5. **Репутация SmartScreen**: EV даёт её сразу, OV набирается по мере загрузок.
6. **Пайплайн**: подписывать NSIS-установщик (и при желании sidecars) реальным сертификатом; ключ —
   только в HSM/секрете CI, никогда в репозитории.

Ограничение остаётся: **установщик не подписан**, и это не «решённая проблема».

---

## 6. Проверки этого этапа

| Проверка | Результат |
|---|---|
| Packaged host E2E | **PASS** (см. §2) |
| Rust unit tests (`cargo test --bin alex-host-loop`) | **38 passed** |
| Python acceptance subset (UIA, security, recovery, integrated build) | **7 passed** |
| `cargo check` / release build host | OK |
| Preflight | READY |
| Полный backend suite | не перезапускался: Python-код в этом этапе не менялся (последний зелёный — 1106 passed) |

---

## 7. Git и CI

- Итоговый SHA: **`b9d85a2`**; CI **7/7 success** (developer-gate, native-gate, frontend-e2e, installer-build, tor-runtime, llama-runtime, llama-runtime-image).
- Изменения этапа: `apps/desktop/src-tauri/src/credential.rs`, `src/host.rs`,
  regression-тест, `artifacts/acceptance-127/*`, этот отчёт.
- 1.2.6, updater, manifest, release assets, ключи подписи, пользовательские данные — **не изменялись**.

---

## 8. Оставшиеся блокирующие проблемы

1. **LIVE Qwen не проводился** — нужен отдельный разрешение владельца и один Pod.
2. **Authenticode отсутствует** — нужен сертификат и HSM; без него публичный релиз остаётся
   неподписанным для Windows.
3. **Смена device_id** установленного приложения (см. §3) — identity не восстановима; требуется
   решение владельца, считать ли это приемлемым для RC. Пользовательские данные целы.
4. Notepad как «чужое» приложение не использовался: приёмка шла на контролируемом WinForms-стенде
   (детерминированные селекторы). UIA-путь для Notepad тот же, но отдельного прогона нет.

Пункты 1–2 и есть граница между «готово к LIVE» и «готово к релизу».
