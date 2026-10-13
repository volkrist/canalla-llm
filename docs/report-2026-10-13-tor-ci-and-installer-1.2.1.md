# Canalla LLM 1.2.1 — Tor CI, кэш рантайма и установщик 1.2.1

Дата: 13–14.10.2026. Ветка `integration/canalla-1.2.1`. **HEAD `83202e7`.**

## 1. Настоящая причина красных job'ов

**Не сетевой сбой.** Tor Project **удалил каталог релиза 15.0.24**:

| Проверка | Результат |
|---|---|
| `…/15.0.24/sha256sums-signed-build.txt` | отдаёт **404 HTML** (306 байт, Apache «Not Found») |
| `…/15.0.24/tor-expert-bundle-windows-x86_64-15.0.24.tar.gz` | **HTTP 404** |
| Каталог `…/torbrowser/` | живут только **15.0.20** и **15.0.25** |

Поэтому `tor-runtime` падал на `TOR-PIN: FAIL — signed checksums file is c1fcd9ff…, pin says 00cf0ce9…`
(файл скачался — но это HTML-страница 404, её SHA256 и не мог совпасть), а `installer-build` — сразу
после «downloading …15.0.24.tar.gz» (404). Ранее я называл это «сетевой флейкой» — **это было
неверно**, причина в ротации релиза Tor.

## 2. Что исправлено

1. **Перепин на 15.0.25** (`scripts/tor-runtime.json`): новая контрольная сумма файла подписей
   (`539e778f…`), оба бандла (win `c1988c70…` 22 507 599 Б; linux `19cf1872…` 32 448 097 Б),
   демон **0.4.9.14**, `corresponding_source` → `tor-0.4.9.14.tar.gz`. Подпись проверяется против
   того же официального fingerprint.
2. **Кэш проверенных архивов** (`fetch-tor-runtime.py --cache DIR` + `actions/cache` в
   `tor-runtime` и `installer-build`): после первого успешного прогона pipeline **вообще не
   обращается** к `dist.torproject.org` и `www.gnu.org`. Кэш не является доверием: попадание
   перепроверяется по размеру и SHA256 из пина, а испорченный архив **отбрасывается и
   перекачивается** (проверено локально).
3. **Явные ошибки сохранены**: несовпадение пина — по-прежнему жёсткий FAIL, подпись не отключена,
   зеркал нет, PASS без Tor невозможен.

## 3. Результаты CI (прогон `38476189049`, HEAD `83202e7`)

| Job | Результат |
|---|---|
| **tor-runtime** | **PASS** |
| **installer-build** | **PASS** |
| developer-gate | PASS |
| native-gate | PASS |
| llama-runtime | PASS |
| llama-runtime-image | PASS |
| frontend-e2e | PASS |

**Все 7 из 7 зелёные** — впервые за всю ветку. Локально до пуша: gateway **260 passed**,
backend **925 passed / 5 skipped**.

## 4. Версия 1.2.1

Поднята во всех **девяти** рукописных местах, которые проверяет `test_version_consistency.py`:
`app/product.py`, `tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, `package.json`,
`package-lock.json` (×2), `apps/backend/pyproject.toml`, `apps/gateway/pyproject.toml`,
`apps/gateway/gateway/config.py` (− `--version 1.2.0` в CI-проверке release-gate).

Фактические подтверждения из установки в CI:

```
Canalla LLM_1.2.1_x64-setup.exe                     (91 928 775 байт)
installed to C:\Users\runneradmin\AppData\Local\Programs\Canalla LLM
sidecar\alex-backend\alex-backend.exe               22 927 049 байт
{"product":"alex-llm","version":"1.2.1","runtime_protocol_version":1,…}
```

То есть установщик, установленный клиент и **установленный sidecar** согласованно называют 1.2.1
(stale-sidecar guard пройден). Обновление поверх 1.2.0 не ломается: тот же product id, имя
`Canalla LLM`, каталог `%LOCALAPPDATA%\Programs\Canalla LLM`, а 1.2.1 > 1.2.0 для updater'а.

## 5. Где тестовый установщик

* GitHub Actions → прогон **`38476189049`** → artifact **`canalla-test-installer`**
  (**`Canalla LLM_1.2.1_x64-setup.exe`**, ≈91.9 МБ, не истёк);
* локально скачан для проверки в
  `%TEMP%\installer121\Canalla LLM_1.2.1_x64-setup.exe`.

Это **тестовая** сборка: production-ключ обновлений не читался, подпись не создавалась,
публикации нет (release-gate корректно отказал неподписанной сборке).

## 6. Готовность к интерактивной приёмке

Установщик готов; сценарий — **`docs/acceptance-windows-1.2.1.md`** (14 пунктов: установка поверх
1.2.0, сохранность данных, Computer/Tor always-ready, sidecar, Gateway, честный Connected/Offline,
поиск GPU в двух регионах, Stop/Cancel, отсутствие самопроизвольных платных запусков).

**GUI-приёмка НЕ выполнялась** — headless CI её не заменяет; платный GPU для неё не нужен.

## 7. Открытые блокеры

1. **Интерактивная Windows-приёмка 1.2.1** — не выполнена (нужен человек за консолью).
2. **Расхождение версии на Gateway.** Production Gateway сейчас = **1.2.0**, а запись реплики в
   репозитории = **1.2.1**. Ожидание реплики берёт `settings.version`, поэтому **текущий** Gateway
   будет считать запись 1.2.1 несовместимой (`wrong_runtime`) и **пропустит US-MO-1** (fail-closed;
   US-TX-3 не затронут). Лечится редеплоем Gateway на `83202e7` — **не выполнялся** (запрещено).
   Production-копия `RUNPOD_REPLICAS` осталась с `1.2.0`, пока Gateway не обновлён.
3. **Публикация 1.2.1** — не начиналась; `main` и теги не трогались.

## Production changes: NONE в этом шаге. GPU spend: $0.
