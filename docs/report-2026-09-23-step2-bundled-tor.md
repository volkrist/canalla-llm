# CANALLA LLM — STEP 2/5: BUNDLED MANAGED TOR — ПОЛНЫЙ ОТЧЁТ

**Дата:** 23 сентября 2026
**Репозиторий:** `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`
**Ветка:** `release/canalla-1.1.0`
**HEAD before:** `16633af` (конец STEP 1)
**HEAD after:** `829996c2bdf6873fff3740b7d4ebae254b63c979`
**Версия продукта:** `1.1.0` — **не поднималась** (STEP 2 это запрещает)
**`main` / `origin/main`:** `9bd7548` — не менялись. Тег не создавался. Production manifest не публиковался.
**GPU:** не использовался ни разу, Pod не создавался, расход 0.

```
a2a8b5e  build(tor): bundle a pinned Tor runtime              5 files, +383 −2
4dab4f3  feat(tor): prefer the Tor Canalla ships              3 files, +287 −50
68294a7  test(tor): certify the bundled runtime lifecycle     3 files, +520 −5
829996c  docs(tor): record the bundled runtime and its licence 2 files, +130 −18
```

---

## WINDOWS TOR

| Поле | Значение |
|---|---|
| **Bundled** | **YES** |
| **Version** | Tor **0.4.9.12** (`git-78923280eed3eff6`), из официального релиза Tor Browser **15.0.23**, expert bundle для `windows-x86_64` |
| **Official source** | `https://dist.torproject.org/torbrowser/15.0.23/tor-expert-bundle-windows-x86_64-15.0.23.tar.gz` |
| **SHA256 (архив)** | `231dad6b9cb401a54c260db7046965ef04e4f72ff071b140d423fb5da281ab1e` (22 432 027 байт) |
| **SHA256 (`tor.exe`)** | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` (10 222 592 байта) |
| **License** | **GPL-3.0**. Официальные сборки Tor Project компилируются с `--enable-gpl`; собственный код Tor — BSD-3-Clause, и `tor.txt` из бандла перечисляет компоненты. Подтверждено двумя способами: баннером `tor --version` и исходником (`src/app/config/config.c`, `#ifdef ENABLE_GPL`; `configure.ac`: *«allow the inclusion of GPL-licensed code, building a version of tor and libtor covered by the GPL rather than its usual 3-clause BSD license»*) |
| **Corresponding source** | `https://dist.torproject.org/tor-0.4.9.12.tar.gz` (сборка: `tor-browser-build`) — зафиксировано в пине |
| **Tor Browser required** | **NO** |
| **External `tor.exe` required** | **NO** |
| **Install/resource path** | `<install>\runtime\tor\tor.exe` (+ `geoip`, `geoip6`, тексты лицензий, `runtime.json` с происхождением и хэшами) |
| **Runtime data path** | `<data root>\tor\` — наш `torrc`, `torrc-defaults` и собственный `DataDirectory` демона; лог `<data root>\logs\tor.log` |
| **Proof / state path** | `<data root>\runtime\tor.json` (`verified`, `verified_at`, `host`, `port`, `source`, `method`, **`pid`**, **`tor_version`); exit-адрес не сохраняется никогда |
| **SOCKS endpoint strategy** | Свой порт: сконфигурированный (`tor_socks_port`, по умолчанию 9050) если свободен → иначе следующий свободный кандидат (9150) → иначе любой свободный loopback-порт. Порт, который слушает **чужой** процесс, не занимается никогда. Фактически использованный endpoint фиксируется в state (`managed_port`), конфликт виден как `port_conflict: true` |
| **Update policy** | Tor обновляется вместе с релизом Canalla (новая версия в пине). Во время работы ничего не скачивается; второго self-updater у Tor нет |

Что попадает в бандл: **только** демон, geoip-данные и тексты лицензий. Браузера, pluggable transports (`lyrebird`, `conjure-client`), control-порта и `tor-gencert` в установке нет.

---

## LINUX TOR

| Поле | Значение |
|---|---|
| **Runtime selected** | **YES** |
| **Version** | Tor **0.4.9.12** (та же сборка релиза 15.0.23, expert bundle `linux-x86_64`) |
| **Official source** | `https://dist.torproject.org/torbrowser/15.0.23/tor-expert-bundle-linux-x86_64-15.0.23.tar.gz` |
| **SHA256 (архив)** | `08d49de27f542b8f73e2014e064d8320562b5d20019c03d4725c5a5249d97985` (32 339 495 байт) |
| **License** | **GPL-3.0** (те же тексты; в Linux-архиве нет `zlib.txt`, поэтому он в пине не значится) |
| **Ready for STEP 4 packaging** | **YES** — пин, извлечение, состав файлов и **реальный запуск** проверены; пакет не собирался (по условию этапа) |

Реальная проверка рантайма в WSL Ubuntu 2 (без установки пакета):

```
Tor version 0.4.9.12 (git-78923280eed3eff6).
This build of Tor is covered by the GNU General Public License (…/gpl-3.0.en.html)
LISTEN 0 4096 127.0.0.1:9155 0.0.0.0:*
Bootstrapped 45% (requesting_descriptors) … 50% (loading_descriptors)
```

Процесс и временный каталог после проверки **удалены** (проверено: `pgrep -x tor` → 0, порт свободен).

**Заметка для STEP 4:** Linux-сборка зависит от `libcrypto.so.3`, `libssl.so.3`, `libevent-2.1.so.7`, которые лежат рядом с бинарником. Подтверждено фактически: без них `./tor --version` падает с `error while loading shared libraries: libevent-2.1.so.7`. Пакет обязан прописать `rpath`/`LD_LIBRARY_PATH` или обёртку — это часть работы STEP 4, не «мелочь».

---

## LIFECYCLE

| Проверка | Результат | Доказательство |
|---|---|---|
| Automatic start | **PASS** | обычный запуск установленного приложения: «Tor Готово» без единого клика |
| SOCKS health | **PASS** | popover: `SOCKS 127.0.0.1:9050`, `Порт отвечает: да` |
| Circuit proof | **PASS** | popover: `Цепь проверена: да`; proof — реальный SOCKS5h-запрос к `check.torproject.org/api/ip` с подтверждением `IsTor: true` |
| Crash recovery | **PASS** | убийство bundled-демона: `1884 → 7576` (первый прогон) и `7796 → 18380` (второй); `source=managed`, версия та же |
| App restart | **PASS** | после relaunch: тот же `device_id`, оба чипа снова зелёные, логина нет |
| Clean shutdown | **PASS** | `Quit` завершает процесс (bounded wait → kill), proof сбрасывается, `managed_port: null` |
| Orphan | **NO** | после прогонов: ни `alex-llm`, ни `alex-backend`, ни `alex-host-loop`, ни `tor`; порт 9050 свободен |
| Bootstrapping ≠ ошибка | **PASS** | холодный Tor читает свой `Bootstrapped NN%` из лога и показывается как «Подключается…», а не как ошибка; локально демон дошёл до `Bootstrapped 100% (done): Done` |

---

## PRIVACY

| Проверка | Результат |
|---|---|
| Fail closed | **PASS** — код маршрутизации не менялся; покрытие существующее: `test_tor_only_policy_rejects_clearnet` (`test_093_reliability.py:624`) и `test_fail_closed_invalid_socks` (`test_tor_browser.py:198`, ожидает код `tor_unavailable`) |
| Clearnet fallback | **NO** — Tor-трафик идёт только через loopback SOCKS5h (ATYP `0x03`, хостнейм не резолвится локально); fallback отсутствует в коде и не появляется в новом |
| Foreign Tor hijacked | **NO** — чужой процесс не убивается, не перенастраивается, не присваивается и не объявляется «нашим»: если он **доказывает** маршрут, он используется как внешний (`managed: false`); если не доказывает — Canalla поднимает свой демон на **свободном** порту, чужой листенер остаётся как был |
| Port conflict handled | **PASS** — детерминированные тесты (`port_available`, «порт чужого не занимается») + `port_conflict` в snapshot |
| Свой конфиг | `torrc` целиком наш: `ClientOnly 1`, `SocksPort` только на loopback, `DataDirectory` в нашем data root, `Log notice file` в наши логи, `GeoIPFile`/`GeoIPv6File` из бандла, **`SafeSocks 1`** (локальный резолв отклоняется Tor'ом). Machine-wide torrc не читается: передаётся `-f <наш torrc> --defaults-torrc <наш файл> --ignore-missing-torrc` |
| Секреты | Демон не получает ни Gateway-креденшелы, ни RunPod-ключи, ни пользовательские секреты: спавнится с закрытыми stdin/stdout/stderr и без наследования наших env-переменных |

---

## TESTS

| Гейт | Было (STEP 1) | Стало | Комментарий |
|---|---|---|---|
| Backend pytest | 606 passed / 1 skipped | **627 passed, 1 skipped** | +21: 12 новых Tor-сервисных + 9 ресурсных |
| `test_tor_service.py` | 18 | **30** | bundled-путь, порты, конфликты, конфиг, backoff, shutdown |
| `test_tor_bundle.py` | — | **9** | пин, официальный хост, SHA256, состав файлов, `tauri.conf.json`, `.gitignore`, staged == pin |
| BasedPyright | 0 / 0 | **0 errors, 0 warnings, 0 notes** | 1 ошибка была найдена и исправлена (`__doc__` — `str | None`) |
| Ruff check / format | PASS | **PASS** (181 файл) | |
| Frontend Vitest | 188 | **188** | фронтенд в STEP 2 не менялся |
| TypeScript / Prettier | чисто | **чисто** | `tsc -b` + `npm run format:check` |
| Vite build | OK | **OK** | собрался внутри сборки установщика |
| Rust `cargo test` | 18 + 52 + 12 | **18 + 54 + 12 = 84** | +2 новых теста разрешения runtime-пути |
| Gateway pytest | 148 | **148** | gateway в STEP 2 не менялся |
| **Updater regression** | — | **PASS** | Rust updater 12, Gateway updates 18, Vitest updates 19 — всё зелёное |

Проверки не ослаблялись: единственная правка существующего assert — расширение точного набора ключей proof-файла (`pid`, `tor_version`) при сохранении исходного смысла («exit-адрес не хранится»), плюс тест «мы никогда не заявляем чужим процесс как своим».

---

## INSTALLED CHECK

| Поле | Значение |
|---|---|
| **Performed** | **YES** — одна сборка (`scripts/build-desktop.ps1` + подпись), затем `apps/desktop/e2e/always-ready.mjs` |
| Tor Browser installed/required | на машине установлен, но **не использован**: маршрут отдан bundled-демону |
| Bundled Tor Ready | **PASS** (`binary.source = bundled`, `runtime_version = 0.4.9.12`) |
| Kill → recovery | **PASS** |
| Первый прогон | 35 PASS / 3 FAIL — все три про чтение списка устройств **одним сэмплом** (гонка в харнессе, не продукт: чип Computer был зелёным, устройство появлялось секундой позже) |
| Исправление | в харнессе добавлено ожидание списка (`waitForDevices`), проверки не ослаблены |
| Второй прогон (та же сборка, без пересборки) | **38/38 PASS**, включая: bundled-демон установлен, маршрут отдан ему, версия из пина, kill → новый процесс → «Готово», relaunch → тот же `device_id`, crash sidecar → восстановление |

Артефакты и полные хэши этой сборки:

| Артефакт | Размер, байт | SHA256 |
|---|---|---|
| `bundle/nsis/Canalla LLM_1.1.0_x64-setup.exe` | 92 438 363 | `c401d79de616d55088dc5da165d6676a8dbd66938ae2e209db5668bd65bb6c07` |
| `…setup.exe.sig` (подпись updater-артефакта) | 424 | `62326e486354e0ebfca182f9fe4a443ae7d7a8ccb3272d4f9dee62383eb95e11` |
| установленный `alex-llm.exe` | — | `786e3e26fea1baee6f917cf2461ae9229c3ae8983425e00067b7d1a4e7054dce` |
| установленный `sidecar/alex-backend/alex-backend.exe` | — | `9eb66ba4381a5ccd0ceb3e0ab8e8cade23506fad6f150d702657ed43cf3e50d7` |
| установленный `alex-host-loop.exe` | — | `051866108ca276f4c1651dd8d1aa8148e6d6c33675063bc39893c056537b7da8` |
| установленный `runtime/tor/tor.exe` | 10 222 592 | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` (совпадает с пином) |

Установщик вырос с ~87,2 МБ до **92,4 МБ**: это bundled Tor (демон ~10 МБ, geoip/geoip6 ~26 МБ в распаковке, сжимаются в установщике).

---

## ЧТО ИМЕННО ИЗМЕНИЛОСЬ В КОДЕ

1. **Разрешение демона** — `find_tor_binary` теперь: явный override (`tor_binary_path`) → **bundled** (`bundled_tor_binary`) → Tor Browser → `tor` в PATH → `Program Files\Tor`. Путь к бандлу приходит от Desktop через `ALEX_TOR_RUNTIME_DIR` (`backend.rs`), а для packaged-backend есть резервный поиск `<install>/runtime/tor` рядом с собственным exe (есть Rust-тест на оба кандидата).
2. **Свой конфиг** — вместо длинной командной строки пишется `torrc` (и `torrc-defaults`) в data root; спавн — `tor -f <torrc> --defaults-torrc <файл> --ignore-missing-torrc`. Пути пишутся нативно (Tor считает `C:/…` относительным путём — это было видно в логе как warning и исправлено).
3. **Политика портов** — `port_available()` (листенер + реальный bind), `_managed_port()` (первый свободный кандидат, иначе свободный loopback), `_probe_order()` (свой endpoint проверяется первым, чтобы чужой листенер не заставил поднять второй процесс), `port_conflict` в state.
4. **Приоритет bundled** — если ни один endpoint не доказывает маршрут, а рантайм у нас есть, поднимается **свой** демон; чужой листенер не трогается. Окно ожидания чужого endpoint сокращено до `min(5 s, tor_startup_timeout_seconds)`, чтобы не задерживать self-contained путь.
5. **Bounded recovery** — `_restarts` + `RESTART_BACKOFF_SECONDS = (2, 5, 15, 30)`; успешный proof сбрасывает счётчик. Crash-loop больше не может «крутиться».
6. **Чистое завершение** — `_stop_managed` делает `terminate()` → ограниченное ожидание (`process.wait(timeout=8)`) → `kill()`; после Quit не остаётся ни слушающего порта, ни демона.
7. **Состояние** — snapshot и proof теперь несут `runtime_version`, `managed_port`, `port_conflict`, `pid`: видно, **какой** демон и **какой** процесс обслуживают маршрут.
8. **Сборка** — `scripts/build-desktop.ps1` вызывает `fetch-tor-runtime.py` перед каждым бандлом и не даёт сборке встать на интерактивный пароль (см. «Находки»).

---

## FILES / SUPPLY CHAIN

**Pin:** `scripts/tor-runtime.json` — версия релиза, лицензия и её примечание, соответствующий исходник, ссылка на build scripts, тексты лицензий (с SHA256), и по каждой платформе: `url`, `bytes`, `sha256`, `binary`, `libraries`, `data`, `notices`, `daemon_version`.

**Стейджинг:** `scripts/fetch-tor-runtime.py` — единственное место, которое скачивает. Поддерживает `--platform`, `--dest`, `--archive`, `--verify-only`, `--clean`, `--print`. Отказывается стейджить при несовпадении размера или SHA256 и при несовпадении текста лицензии; удаляет «лишние» файлы прошлого стейджинга; пишет `runtime.json` с хэшем каждого файла. Для окружений, где `urllib` не имеет сети, есть проверенный fallback на `curl`/`wget` (хэш всё равно проверяется).

**Вывод `--verify-only` (фактический):**
```
tor 15.0.23 (GPL-3.0) windows-x86_64: …/tor-expert-bundle-windows-x86_64-15.0.23.tar.gz
  sha256=231dad6b9cb401a54c260db7046965ef04e4f72ff071b140d423fb5da281ab1e bytes=22432027
  stages tor/tor.exe, data/geoip, data/geoip6, docs/tor.txt, docs/openssl.txt,
         docs/libevent.txt, docs/zlib.txt
  stages gpl-3.0.txt from https://www.gnu.org/licenses/gpl-3.0.txt sha256=3972dc97…
```

**Per-file SHA256 в установленном `runtime/tor/runtime.json`:**

| Файл | SHA256 |
|---|---|
| `tor.exe` | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` |
| `geoip` | `25a69c1dc1d946bfdb0b1ba628db36e9668c51639963c2ee2afda7dc857f66a5` |
| `geoip6` | `0a3b61ba326550d66a4c805563be25e28f1d59e5cdfc06b091bfdd9ea2c8f998` |
| `tor.txt` | `5dc29ea302cc75db6db4a7c88d7b5d4638b4e0f5f1d2e6d21ce71f707470b766` |
| `openssl.txt` | `7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a` |
| `libevent.txt` | `ff02effc9b331edcdac387d198691bfa3e575e7d244ad10cb826aa51ef085670` |
| `zlib.txt` | `e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2` |
| `gpl-3.0.txt` | `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986` |

**NOTICE / лицензии:** `docs/third-party-notices.md` (репозиторий) и сами тексты внутри установки — `<install>\runtime\tor\tor.txt`, `openssl.txt`, `libevent.txt`, `zlib.txt`, `gpl-3.0.txt`.

---

## ЧЕГО СОЗНАТЕЛЬНО НЕ ДЕЛАЛОСЬ (условия этапа)

- Windows login autostart — не делался.
- Ubuntu-пакет (`deb`/AppImage) — не собирался.
- Bump версии 1.1.0 → 1.2.0 — не делался.
- Release build 1.2.0, production signing, production manifest — не делались.
- Merge в `main`, тег — не делались.
- GPU не использовался.

---

## НАХОДКИ И ЛОВУШКИ (без прикрас)

1. **Интерактивный пароль сборки.** `tauri build` при `createUpdaterArtifacts: true` требует ключ; при ключе **без пароля** CLI **спрашивает пароль**, читая консоль напрямую — перенаправление stdin не помогает. Один такой запуск стоил ограниченного **30-минутного таймаута**; это ровно тот механизм, который в прошлый раз держал прогон 9 ч 17 мин. Обработка: скрипт вызывает подпись Tauri только когда заданы и ключ, и пароль; иначе собирает без неё и подписывает **сам установщик** (`tauri signer sign -f <key> -p ""`). Корректность проверена по исходнику плагина: клиент обновлений принимает как `.exe`, так и zip с ним (`extract_exe`/`extract_zip`), а подпись — это подпись над теми же байтами. **Рекомендация для STEP 5:** использовать ключ **с паролем**, тогда штатный путь Tauri останется неинтерактивным.
2. **Гонка в installed-харнессе.** Три FAIL первого прогона — односэмпловое чтение списка устройств до завершения pairing. Продукт был здоров (чип зелёный, устройство появилось позже, id сохранился). Исправлено в тесте ожиданием; проверки не ослаблены.
3. **Tor теперь GPL-3.0.** Официальные сборки Tor Project помечены как GPL. Мы поставляем тексты лицензий и указываем соответствующий исходник — это обязательство обязано быть в release notes финального релиза (release-legal item, не «техническая мелочь»).
4. **Устаревший пин sidecar-хэша.** `scripts/acceptance-post-release-1.1.0.py` фиксирует `ffd8884d…`, а этот промежуточный билд даёт `9eb66ba4…`. Пиновка обновляется **один раз** под финальную сборку 1.2.0 (как договорились); приёмочный прогон STEP 2 харнесса на неё не опирается.
5. **Linux тянет свои shared-библиотеки** — без `rpath`/обёртки демон не стартует (проверено).
6. **Размер.** geoip/geoip6 — 26 МБ в распаковке; установщик вырос на ~5 МБ. Альтернатива (без geoip) ухудшила бы bootstrap-поведение; решение осознанное и зафиксировано.

---

## REMAINING FOR STEP 3

1. Windows autostart при входе в систему + toggle в Settings + удаление записи при uninstall.
2. Ubuntu: `.deb`/AppImage, Linux secret storage (не plaintext), XDG-autostart, приёмка на чистой VM — с учётом названных shared-библиотек.
3. Windows clean-profile acceptance на машине **без** Tor Browser (STEP 2 доказал, что bundled-демон используется даже там, где Tor Browser есть).
4. Документация релиза: упомянуть GPL-обязательство и однократную ручную установку 1.2.0.

---

## КАК ПОВТОРИТЬ

```bash
# Стейдж рантайма (проверка пина без изменений на диске)
apps/backend/.venv/Scripts/python.exe scripts/fetch-tor-runtime.py --verify-only
# Скачать + проверить + разложить (Windows-хост)
apps/backend/.venv/Scripts/python.exe scripts/fetch-tor-runtime.py --print
# Linux-рантайм (стейджится на Linux-сборщике или в отдельный каталог)
apps/backend/.venv/Scripts/python.exe scripts/fetch-tor-runtime.py --platform linux-x86_64 --dest DIR

# Тесты
cd apps/backend && .venv/Scripts/python.exe -m pytest -q tests/test_tor_service.py tests/test_tor_bundle.py
cd apps/backend && .venv/Scripts/python.exe -m pytest -q
cd apps/desktop/src-tauri && cargo test
cd ../../.. && npx basedpyright

# Установочная проверка (одна сборка)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-desktop.ps1
powershell -NoProfile -Command "Start-Process -FilePath '<installer>' -ArgumentList '/S' -Wait"
cd apps/desktop && node e2e/always-ready.mjs
```

---

## FINAL

**PASS**

Tor перестал быть внешней зависимостью: Canalla поставляет собственный запиненный демон (Windows и Linux), запускает его сама, доказывает маршрут через него, восстанавливает после падения, корректно завершает при Quit и никогда не трогает чужой Tor. Доказано 39 детерминированными тестами, полным бесплатным гейтом (627 backend / 148 gateway / 188 frontend / 84 Rust, BasedPyright 0/0) и одним installed-прогоном на реальной сборке (38/38).
