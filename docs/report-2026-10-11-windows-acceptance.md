# Canalla LLM 1.2.1 — Windows acceptance: Tor pin, real Playwright and the test installer

Дата: 11 октября 2026 · Ветка: `integration/canalla-1.2.1` · Продукт: Canalla LLM 1.2.0

Начальный HEAD этого этапа: `9e1f37f` · Итоговый HEAD: этот отчёт (см. `git log -1`).

Ни одного production-действия не сделано. GPU spend **$0**.

---

## 1. Tor runtime — исправленный pin, источник и проверка подлинности

**Что качает `fetch-tor-runtime.py`:** официальный **Tor Expert Bundle** — только демон `tor`
(без браузера, без pluggable transports, без control-порта).

**Доступные версии:** на `dist.torproject.org/torbrowser/` остались `15.0.20`, `15.0.24`,
`16.0a13`; пина `15.0.23` больше нет (HTTP 404). Выбран **15.0.24**.

**Какой Tor внутри:** официальный блог Tor Project о релизе 15.0.24 (29 сентября 2026) говорит
дословно «Updated Tor to 0.4.9.13». Поэтому `daemon_version = 0.4.9.13`,
`corresponding_source = https://dist.torproject.org/tor-0.4.9.13.tar.gz`.

**Откуда взяты хэши (не из скачанного файла):** из официального файла контрольных сумм
`https://dist.torproject.org/torbrowser/15.0.24/sha256sums-signed-build.txt`
(его же SHA256 `00cf0ce985321c897eb817e97304a89b971b86bee906ccf262d1ee1b60e51721` тоже закреплён).

| Платформа | файл | bytes | SHA256 (official) |
|---|---|---|---|
| windows-x86_64 | `tor-expert-bundle-windows-x86_64-15.0.24.tar.gz` | 22 440 870 | `e9dc6ccc93cd6afa507193f4de284d6424233ff5102155cd2c94b259e8a22b65` |
| linux-x86_64 | `tor-expert-bundle-linux-x86_64-15.0.24.tar.gz` | 32 348 376 | `8e012ec6815d7899cb64011582e2dade88e74119c6661068a2a3252de0ccd7f2` |

**Проверка подписи (не просто совпадение хэша):** `scripts/verify-tor-runtime-pin.py` скачивает
detached-подпись `sha256sums-signed-build.txt.asc`, берёт ключ Tor по отпечатку с
`keys.openpgp.org`, сверяет фактический отпечаток и проверяет подпись. Результат доверенного
прогона:

```
TOR-PIN: PASS — 15.0.24 (0.4.9.13); 2 platform pins match the official signed checksums; signature verified
Good signature from "Tor Browser Developers (signing key) <torbrowser@torproject.org>"
Primary key fingerprint: EF6E 286D DA85 EA2A 4BA7 DEAD … 4E2C 6E87 9329 8290
```

**Запуск автономно:** CI скачивает, проверяет и раскладывает рантайм, затем запускает демона без
чего-либо, установленного пользователем:

```
staged tor 15.0.24 (windows-x86_64): 8 files, binary tor.exe
Tor version 0.4.9.13 (git-3c575400909efe65).
```

Самостоятельная установка Canalla сохранена: Tor приходит внутри продукта, ручная установка не
нужна. Приватный ключ подписи, зеркала, production secrets — не использовались.

## 2. Playwright — 20/20 на доверенном раннере

Проблема была не в UI, а в отсутствующей embedding-модели. Теперь e2e-bundle ставит **настоящую**
модель через штатный менеджер продукта: закреплённая ревизия HF
`761b726dd34fb83930e26aab4e9ac3899aa1fa78`, каждый артефакт проверяется по размеру и хэшу,
затем embedding smoke. Моки не подставлялись.

```
EMBEDDING-MODEL: PASS — ready at …\.embedding-model\models\embeddings\multilingual-e5-small
20 passed (3.0m)
```

Кэш `actions/cache` по ключу ревизии ускоряет повторные прогоны; кэш безопасен, потому что
менеджер всё равно проверяет хэши и делает smoke при загрузке. No GPU, no secret.

Дополнительно: `app.spec.ts` был флейки (гонка при захвате «остановленного» сообщения), которую
выявил более медленный настоящий backend; тест теперь ждёт появления второго сообщения.

## 3. Тестовый Windows installer — собран и установлен

`scripts/build-desktop.ps1` получил ключ `-TestBuild`: та же сборка, что релизная (PyInstaller
sidecar + stamp, native host, bundled Tor, вшитый Gateway URL, NSIS), но **ключ подписи не
читается и подпись не создаётся**.

```
Canalla LLM_1.2.0_x64-setup.exe  SIZE 91892066  SHA256 1880b06e…deddedd
```

Артефакт `canalla-test-installer` загружен из run (91 898 523 байта).

**Установка и проверка на раннере:**
```
installed to C:\Users\runneradmin\AppData\Local\Programs\Canalla LLM
  alex-llm.exe                              5 956 608 bytes
  sidecar\alex-backend\alex-backend.exe    22 927 049 bytes
  runtime\tor\tor.exe                      10 227 200 bytes
PACKAGED-GATEWAY-URL: PASS — alex-llm.exe carries https://gateway.12testers.store
{"status":"ok","provider":"mock","llm_ready":true,"product":"alex-llm","version":"1.2.0",
 "runtime_protocol_version":1,"instance":null}
```

То есть: чистая установка, запуск **установленного** sidecar (без Python) и ответ `/health`
подтверждены. `runtime/tor/tor.exe` лежит в установленном дереве.

## 4. Release gate — работает как задумано

На неподписанном тестовом инсталляторе production-гейт **корректно отказывает**:
```
[PASS] artifact.sha256 — 1880b06e…
[FAIL] artifact.signature — missing signature: …setup.exe.sig
RELEASE GATE: FAIL
```
Это доказывает, что гейт — настоящий гейт. Никакого объявления production release gate
пройденным на неподписанном установщике нет.

## 5. Windows CI — ссылки на реальные прогоны

Прогон HEAD `6187ef2` — **все пять job'ов успешны**:
https://github.com/volkrist/canalla-llm/actions/runs/38146632128

| Gate | Job | Результат |
|---|---|---|
| Developer (pytest/Ruff/Alembic/Vitest/tsc/Prettier/Vite) | [`114491258006`](https://github.com/volkrist/canalla-llm/actions/runs/38146632128/job/114491258006) | **PASS** |
| Rust `cargo check` + `cargo test` | [`114491258155`](https://github.com/volkrist/canalla-llm/actions/runs/38146632128/job/114491258155) | **PASS** |
| Playwright (реальная модель) | [`114491258171`](https://github.com/volkrist/canalla-llm/actions/runs/38146632128/job/114491258171) | **PASS 20/20** |
| Tor bundled runtime (pin + подпись + запуск демона) | [`114491258179`](https://github.com/volkrist/canalla-llm/actions/runs/38146632128/job/114491258179) | **PASS** |
| Test installer build + install smoke | [`114491258182`](https://github.com/volkrist/canalla-llm/actions/runs/38146632128/job/114491258182) | **PASS** |

После этого добавлены исключительно робастность-правки (retry сетевых загрузок после одного
транзиентного сбоя `gnu.org`), проверяемые следующим прогоном.

## 6. Что осталось (точные блокеры)

- **GUI always-ready / recovery на установленном приложении** (Computer «Готово», Tor «Готово»,
  восстановление после сбоя, честный AI Offline до готовности модели) требует **интерактивной
  Windows-сессии**: GitHub-раннер без рабочего стола не открывает WebView2-окно. Нужна чистая
  Windows VM. Сценарий: установить артефакт `canalla-test-installer`, запустить приложение,
  `apps/desktop/e2e/always-ready.mjs` (изоляция `ALEX_DEVICE_CREDENTIAL_TARGET`), проверить
  зелёные Computer/Tor без кнопок и после перезапуска.
- **Upgrade поверх установки 1.2.0:** независимого проверенного артефакта предыдущей версии на CI
  нет (текущий тестовый инсталлятор — тоже 1.2.0), поэтому это переустановка, а не upgrade.
  BLOCKED до появления отдельного предыдущего артефакта; тест — синтетические данные.
- **Production signing:** BLOCKED (на первом ПК открыт `Trojan:Win32/LummaStealer.DO!MTB`,
  `DidThreatExecute=True`; ключ подписи там не используется). Подпись — отдельным согласованным
  действием из доверенной среды.
- **Установочная GUI-приёмка** (clean install GUI, сохранность чатов/документов/памяти) — на VM.

## 7. Изменения и запреты

- Production changes: **NONE**. GPU spend: **$0**.
- Не создавались Pod/Volume, не менялись production Gateway, 12Testers, реальные установки и
  credentials; Defender не отключался; merge/tag/deploy/publish не делались.
- Новые/изменённые файлы: `scripts/tor-runtime.json`, `scripts/verify-tor-runtime-pin.py`,
  `scripts/provision-embedding-model.py`, `scripts/fetch-tor-runtime.py`,
  `scripts/build-desktop.ps1`, `apps/backend/tests/test_tor_bundle.py`,
  `apps/desktop/e2e/app.spec.ts`, `.github/workflows/ci.yml`.

## 8. Следующий этап (не публикация)

A. Короткая проверка настоящей Qwen на RunPod. B. Global Volume и стоимость. C. При
непригодности Global Volume — регион резервного Network Volume по доступности и цене.
D. Автоматический запуск и переключение. E. Только после этого — 1.2.1.

**RELEASE READY не объявляется.**
