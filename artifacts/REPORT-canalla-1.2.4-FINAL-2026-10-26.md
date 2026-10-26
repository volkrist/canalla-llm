# Canalla LLM 1.2.4 — финальный отчёт (26.10.2026)

> **СТАТУС: ОПУБЛИКОВАНО.** Публичный GitHub Release `v1.2.4` создан, все три ассета
> скачаны с публичной страницы и проверены по SHA256. Updater-подпись валидна
> (production key `9B328EFF111D1FB2`). Платные ресурсы остановлены: `pods []`,
> watchdog `closed`, AI `Off`.

**Ветка:** `release/canalla-1.2.4`
**Релизный коммит (тег `v1.2.4`):** `1a055edac66e7ed33e3fb0021a9d6b1766bdcc90`
**Базовый коммит:** `e5624538c9bd5607aa5847e845aa6bef17debae1`
**CI:** run `40317130892` — **7/7 success** на `1a055ed`
**Установлено:** Canalla LLM **1.2.4** (FileVersion / ProductVersion `1.2.4`)
**Публикация:** **ДА** — https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4

---

## 0. Итог

| Пункт | Результат |
|---|---|
| **H4** | **PASS** (8 реальных источников, отчёт со ссылками) |
| **H5** | **PASS** (model-origin: web → файлы → Python → pytest → отчёт) |
| **H6** | **PASS** (fault proxy 6 с, без дублей, `H6_RECOVERED`) |
| **T2** | **PASS** (Stop посреди реального stream, Pod остановлен) |
| **T5 / T6 / T7** | **PASS** (typed error; отказ без обхода; tool timeout) |
| **T3 / T4 / T8** | бесплатные/установленные аналоги PASS; live-варианты — ограничение |
| **Подпись `.sig`** | **VALID** — 8/8 PASS, ключ `9B328EFF111D1FB2` |
| **CI** | **7/7 success** на `1a055ed` |
| **GPU** | **1 новый Pod** `a2gxzpwn9y4shs`, 237.7 с, ≈ **$0.077** |
| **Остаток** | **$6.5906868393**, `pods []`, watchdog **closed** |
| **v1.2.4 опубликована** | **ДА** |

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
Сценарий с внесённой ошибкой и её исправлением в этом прогоне не выполнялся.

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
- После теста fault proxy остановлен, `Restore` выполнен, обычная сеть восстановлена.

---

## 3. Подпись апдейтера — VALID

Восстановлено:

- Production private key: `C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key`
  (создан 24.09.2026 16:33, `production-signing.ps1 -Action NewIdentity`).
- Его public key id: **`9B328EFF111D1FB2`** — совпадает с `plugins.updater.pubkey`
  в `tauri.conf.json` и с ключом установленной 1.2.3/1.2.4.
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
  Ed25519 over BLAKE2b-512, signed name, URL, commit `1a055ed`.

**Не делалось:** подбор/восстановление/обход пароля; подпись test-ключом; изменение
`tauri.conf.json`; отключение проверки подписи; покупка Authenticode-сертификата.

**Безопасный интерфейс для владельца:** `artifacts/sign-updater-artifact.ps1`
(скрытый ввод пароля, передача только через переменную окружения дочернего процесса,
без записи в репозиторий и логи, с немедленной проверкой `verify-artifact`).

---

## 4. CI и бесплатные гейты

| Гейт | Результат |
|---|---|
| CI run `40317130892` | **7/7 success**, SHA `1a055ed` |
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
| `release-gate.py candidate` | **22 шага, 0 FAIL, 0 BLOCKED — PASS** |
| `release-gate.py verify-artifact` | **8/8 PASS** |
| Installed `always-ready.mjs` | **32/32 PASS** |

---

## 5. GPU и деньги

| Параметр | Значение |
|---|---|
| Новых Pod в live-прогоне | **1** (лимит пакета — 1, бюджет $0.60) |
| Pod ID | `a2gxzpwn9y4shs` |
| GPU | NVIDIA L40S 48 GB, \$1.09/ч, US-TX-3 |
| Подтверждённое время | **237.7 с** (`within_cap: true`, лимит 700 с) |
| Сценарии | H6 → H5, оба PASS |
| Стоимость этапа | **≈ \$0.077** |
| Баланс до | \$6.67718 |
| Баланс после (финальная проверка) | **\$6.5906868393** |
| `pods` | **`[]`** |
| Watchdog | **`closed`**, `armed: false` |
| AI | **Off**, `compute_state: stopped`, `session: null` |
| Reconnect после Stop | **нет** |

Примечание: `currentSpendPerHr` ещё некоторое время показывает `0.01` для уже
завершённого Pod — это известное поведение провайдера, подтверждённое ранее;
список Pod пуст, биллинг не растёт.

---

## 6. Установленная сборка и данные

- Установлена **1.2.4**; `alex-llm.exe` → FileVersion `1.2.4`, ProductVersion `1.2.4`.
- Версии согласованы: `product.py` `VERSION = "1.2.4"`, desktop/frontend/`tauri.conf.json` `1.2.4`.
- Чатов: **31 до** приёмки; **33 после** (добавлены 2 acceptance-чата H6 и H5).
- Устройство: тот же `device_id` `294c9e2f-ba51-4a56-94b1-ad5b62bfbf42`, online.
- Computer / Tor / Memory: **ready**; Canalla Cloud: **connected**.
- Pre-install backup: `20261026T100723Z-manual`, `verified: true`.
- Удалённые вручную чаты **не восстанавливались**.
- `computer_mode = ask`; Compute Preferences сохранены.
- Credential Manager (session / provider / gateway) не затронут.

---

## 7. Публикация — выполнена

**Tag:** `v1.2.4` (annotated, `53f8744…`) → `1a055edac66e7ed33e3fb0021a9d6b1766bdcc90`
**Release:** https://github.com/volkrist/canalla-llm/releases/tag/v1.2.4
(draft = false, prerelease = false)

| Asset | Размер | SHA256 |
|---|---|---|
| `Canalla.LLM_1.2.4_x64-setup.exe` | 95 465 293 B | `8db307489f0b5f0da077158e50d329a57d4fcd2c64707c3f149a2196f9c8de7e` |
| `Canalla.LLM_1.2.4_x64-setup.exe.sig` | 424 B | `ddcddf51c40aafa501bf32f1ea4f1f5b46378aa667745cd7e512b39b39130354` |
| `SHA256SUMS-1.2.4.txt` | 200 B (LF) | `7c3bd84905f8b77b1975a5769f1a0218b16067f2c4852ba4f58521a3d8e3a11b` |

Проверка публичной загрузки (все три файла скачаны с публичной страницы):

- `sha256sum -c` по `SHA256SUMS-1.2.4.txt` → `.exe: OK`, `.sig: OK`.
- Digest ассетов в GitHub API совпадает с локальными файлами.
- `.sig` проверена production pubkey и `release-gate.py verify-artifact` (8/8).
- GitHub переименовывает пробел в имени установщика в точку
  (`Canalla LLM_…` → `Canalla.LLM_…`); байты файла при этом не меняются, и
  `artifact.signed_name` по-прежнему связывает подпись с исходным именем
  `Canalla LLM_1.2.4_x64-setup.exe`.
- `SHA256SUMS-1.2.4.txt` хранится с LF. Одна из ранних загрузок отдавалась из
  CDN-кэша со старыми CRLF-байтами; повторная загрузка без кэша (200 B, LF)
  проверяется на Linux `sha256sum -c` без ошибок.

**v1.2.3 не изменялась** (опубликована 2026-10-25, draft = false).

Authenticode не подменяет Tauri updater `.sig`; сертификат не приобретался.

---

## 8. Честные ограничения

- H5: pytest прошёл с первого раза, отдельного шага «исправление ошибки» не было.
- H6: обрыв был реальным (6 с, только backend→Gateway), production Gateway не тронут.
- T3/T4/T8 в live-варианте посреди платной генерации не выполнялись; их бесплатные
  аналоги (installed relaunch, crash recovery, Playwright) зелёные.
- Authenticode отсутствует; SmartScreen может предупредить.
- Ubuntu не собиралась и не тестировалась; чистая Windows VM (O) — **BLOCKED**
  (VM недоступна). Как PASS не заявляется.
- Остаточный риск Gateway: сторонний API-клиент, вызывающий Stop без abort своего
  stream, оставит Pod до конца генерации. Штатный UI прерывает поток.
- Автообновление: installer и `.sig` валидны, установленные 1.2.3/1.2.4 доверяют
  ключу `9B328EFF111D1FB2`, но **манифест обновления на production Gateway не
  изменялся** (Gateway не трогали). Публикация манифеста — отдельное действие
  оператора.

---

## 9. Индекс доказательств

- H5: `artifacts/acceptance-123/runs/live-20261026T110323/H5.json`
- H6: `artifacts/acceptance-123/runs/live-20261026T110323/H6.json`
- Summary: `artifacts/acceptance-123/runs/live-20261026T110323/summary.json`
- Installer: `apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.4_x64-setup.exe`
- Signing script: `artifacts/sign-updater-artifact.ps1`
- CI: https://github.com/volkrist/canalla-llm/actions/runs/40317130892
- Release doc: `docs/release-1.2.4.md`
- Release notes: `artifacts/release-notes-v1.2.4.md`

---

## 10. Что проверено в финальном проходе (после публикации)

1. Тег `v1.2.4` аннотированный, указывает на `1a055ed`; локальный HEAD, `origin`
   и тег совпадают.
2. CI `40317130892`: 7 job'ов, все `success`, на `1a055ed`.
3. Публичные ассеты: все три присутствуют, `state: uploaded`, digest совпадает.
4. Установщик и `.sig` скачаны с публичной страницы и проверены `sha256sum -c` → OK.
5. `release-gate.py verify-artifact` на оригинальном installer → **8/8 PASS**.
6. Установленная сборка: FileVersion/ProductVersion `1.2.4`, версии всех
   компонентов согласованы.
7. RunPod: `pods []`, баланс `$6.5906868393`, новых Pod не создавалось.
8. Watchdog: `closed`, `armed: false`; AI `Off`; reconnect отсутствует.

*Платные ресурсы в финальном проходе не использовались.*
