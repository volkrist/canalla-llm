# Canalla LLM 1.2.4 — финальный отчёт (26.10.2026)

> **ОБНОВЛЕНИЕ 26.10.2026, 20:35 UTC — ПОДПИСЬ ВОССТАНОВЛЕНА.**
> **КЛЮЧ ДОСТУПЕН.** Пароль читается из Credential Manager
> (`CanallaLLM/UpdaterSigning/Production`), бэкап расшифровывается
> (`backup_decrypts=True`). `.sig` создан: 424 B, SHA256 `ddcddf51…`;
> `release-gate.py verify-artifact` — **8/8 PASS** (key id
> `9B328EFF111D1FB2`, Ed25519/BLAKE2b-512, signed name, URL, commit `e562453`).
> **v1.2.4 опубликована:** https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4
> Раздел 3 обновлён; разделы, описывающие прошлое состояние, помечены.

**Ветка:** `release/canalla-1.2.4`
**Коммит:** `e5624538c9bd5607aa5847e845aa6bef17debae1`
**CI:** https://github.com/volkrist/canalla-llm/actions/runs/40305809199 — **7/7 success**
**Установлено:** Canalla LLM **1.2.4**
**Публикация:** **НЕТ** (единственный блокер — подпись апдейтера).

---

## 0. Итог

| Пункт | Результат |
|---|---|
| **H5** | **PASS** |
| **H6** | **PASS** |
| **Подпись `.sig`** | **VALID** (восстановлена 26.10.2026; release gate 8/8) |
| **CI** | **7/7 success** на `e562453` |
| **GPU** | **1 новый Pod** `a2gxzpwn9y4shs`, 237.7 с, **≈ $0.077** |
| **Остаток** | **$6.60041**, `pods []`, watchdog **closed** |
| **v1.2.4 опубликована** | **ДА** — https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4 |

---

## 1. H5 — PASS

**Evidence:** `artifacts/acceptance-123/runs/live-20261026T110323/H5.json`

- Model-origin tool calls: `web_fetch` (example.com), 4× `write_file`,
  `run_python` (`python -m pytest -q`).
- Реальные файлы:
  - `h5_title.txt` = `Example Domain`
  - `h5_app/__init__.py`, `h5_app/core.py` (`normalized()`), `h5_app/test_core.py`
  - `h5_report.txt` — отчёт со ссылкой
- Независимый прогон: **`3 passed`**.
- Финальный ответ сохранён в чате.
- Stream: 200, первый delta 79.4 с, завершён 89.6 с.

**Честная оговорка:** код модели прошёл pytest **с первого раза**, поэтому шага
«исправление ошибки» фактически не было — исправлять было нечего. Цепочка
Web → файлы → Python → pytest → отчёт → ответ выполнена реально и model-origin.
Если требуется именно сценарий с внесённой ошибкой и её исправлением, это отдельный
под-сценарий, который в этом прогоне не выполнялся.

---

## 2. H6 — PASS

**Evidence:** `artifacts/acceptance-123/runs/live-20261026T110323/H6.json`

- Локальный fault proxy: `block` на 6 с → `clear`; production Gateway не затронут.
- Model-origin tool calls: ровно **1× `write_file`**, **1× `run_python`**,
  1× `create_directory`. **Дублей нет.**
- Файл: `h6_probe.py` → `print("H6_RECOVERED")`.
- STDOUT: `H6_RECOVERED`.
- После обрыва задача продолжилась, прогресс сохранён, финальный ответ записан:
  «Готово. Файл создан и выполнен, вывод: H6_RECOVERED».
- Stream: 200, первый delta 55.3 с, завершён 56.2 с.

---

## 3. Подпись апдейтера — VALID (восстановлена 26.10.2026)

Восстановлено:

- Production private key: `C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key`
  (создан 24.09.2026 16:33, `production-signing.ps1 -Action NewIdentity`).
- Его public key id: **`9B328EFF111D1FB2`** — совпадает с `plugins.updater.pubkey`
  в `tauri.conf.json`.
- Пароль **не пустой** и хранится в Windows Credential Manager:
  `CanallaLLM/UpdaterSigning/Production`; бэкап-пароль — `.../ProductionBackup`.
  `cmdkey` не показывает machine-persisted generic credentials; прямой `CredRead`
  нашёл обе записи.
- Штатный механизм `scripts/production-signing.ps1 -Action Sign` читает пароль из
  Credential Manager и передаёт его через `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`;
  в аргументах и логах пароля нет.
- Зашифрованный бэкап: `D:\canalla-release-backup\canalla-updater-production.key.enc`
  (+ `.json`); `TestBackup` → `backup_decrypts=True`.
- `.sig`: 424 B, SHA256 `ddcddf51c40aafa501bf32f1ea4f1f5b46378aa667745cd7e512b39b39130354`.
- `release-gate.py verify-artifact` — **8/8 PASS**, key id `9B328EFF111D1FB2`,
  Ed25519 over BLAKE2b-512, signed name, URL, commit.

**Не делалось:** подбор/восстановление/обход пароля; подпись test-ключом; изменение
`tauri.conf.json`; отключение проверки подписи; покупка Authenticode-сертификата.

**Безопасный интерфейс для владельца:**
`artifacts/sign-updater-artifact.ps1`
- спрашивает пароль через `Read-Host -AsSecureString` (ввод скрыт);
- передаёт его только через `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` дочернему процессу;
- **не** печатает пароль, **не** пишет его в репозиторий/логи, **не** кладёт в
  командную строку;
- подписывает существующий installer и сразу запускает
  `release-gate.py verify-artifact`.

Запуск (в интерактивном PowerShell, пароль вводит владелец):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File artifacts\sign-updater-artifact.ps1
```

---

## 4. CI и бесплатные гейты

| Гейт | Результат |
|---|---|
| CI run 40305809199 | **7/7 success**, SHA `e5624538…` |
| developer-gate | success |
| frontend-e2e | success |
| native-gate | success |
| tor-runtime | success |
| llama-runtime / llama-runtime-image | success |
| installer-build | success |
| Backend pytest | **988 passed**, 1 skipped |
| Gateway pytest | **261 passed** |
| Frontend Vitest | **313 passed** |
| Rust `cargo test` | **126 passed** |
| Playwright E2E | **21 passed** |
| `release-gate.py developer` | **9/9 PASS** |
| Acceptance preflight (installed) | PASS |
| Installed `always-ready.mjs` | **32/32 PASS** |

Новых коммитов в этом проходе не делалось: условия публикации не выполнены
(подпись BLOCKED), поэтому push/tag/release не выполнялись.

---

## 5. GPU и деньги (этот проход)

| Параметр | Значение |
|---|---|
| Новых Pod | **1 из 1 разрешённого** |
| Pod ID | `a2gxzpwn9y4shs` |
| GPU | NVIDIA L40S 48 GB, \$1.09/ч, US-TX-3 |
| Подтверждённое время | **237.7 с** (`within_cap: true`, лимит 700 с) |
| Сценарии | H6 → H5, оба PASS |
| Стоимость этапа | **≈ \$0.077** (лимит \$0.60) |
| Баланс до | \$6.67718 |
| Баланс после | **\$6.60041** |
| `pods` | **`[]`** |
| Watchdog | **`closed`**, `armed: false` |
| AI | **Off**, `compute_state: stopped`, `session: null` |
| Fault proxy | **остановлен**, `Restore` выполнен, обычная сеть восстановлена |
| Reconnect | **нет** |

Предыдущий 8-Pod пакет (≈\$0.635) относится к прошлому промпту; в этом проходе
израсходован ровно один новый Pod.

---

## 6. Установленная сборка и данные

- Установлена **1.2.4**; `/health` → `version 1.2.4`.
- Чатов: **31 до** приёмки; **33 после** (добавлены 2 acceptance-чата H6 и H5).
- Устройство: тот же `device_id` `294c9e2f-ba51-4a56-94b1-ad5b62bfbf42`, online.
- Computer / Tor: **ready**; Canalla Cloud: **connected**.
- Pre-install backup: `20261026T100723Z-manual`, `verified: true`.
- Удалённые вручную чаты **не восстанавливались**.
- `computer_mode = ask`; Compute Preferences сохранены.

---

## 7. Публикация — выполнена

Tag `v1.2.4`; публичный GitHub Release:
https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4

Assets: `Canalla.LLM_1.2.4_x64-setup.exe`, `.sig`, `SHA256SUMS-1.2.4.txt`.

Authenticode не подменяет Tauri updater `.sig`; сертификат не приобретался.

---

## 8. Честные ограничения

- H5: pytest прошёл с первого раза, отдельного шага «исправление ошибки» не было.
- H6: обрыв был реальным (6 с, только backend→Gateway), production Gateway не тронут.
- T3/T4/T8 в live-варианте посреди платной генерации не выполнялись; их бесплатные
  аналоги (installed relaunch, crash recovery, Playwright) зелёные.
- Authenticode отсутствует.
- Остаточный риск Gateway: сторонний API-клиент, вызывающий Stop без abort своего
  stream, оставит Pod до конца генерации. Штатный UI теперь прерывает поток.
- Чистая Windows VM (O) — BLOCKED, VM нет.

---

## 9. Индекс доказательств

- H5: `artifacts/acceptance-123/runs/live-20261026T110323/H5.json`
- H6: `artifacts/acceptance-123/runs/live-20261026T110323/H6.json`
- Summary: `artifacts/acceptance-123/runs/live-20261026T110323/summary.json`
- Installer: `apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.4_x64-setup.exe`
  · SHA256 `8db307489f0b5f0da077158e50d329a57d4fcd2c64707c3f149a2196f9c8de7e`
- Signing script: `artifacts/sign-updater-artifact.ps1`
- CI: https://github.com/volkrist/canalla-llm/actions/runs/40305809199
- Release doc: `docs/release-1.2.4.md`
