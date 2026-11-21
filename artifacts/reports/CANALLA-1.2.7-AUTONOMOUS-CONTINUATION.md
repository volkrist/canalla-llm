# CANALLA LLM — АВТОНОМНАЯ РАЗРАБОТКА: ОТЧЁТ СЕССИИ

**Продолжение разработки 1.2.7: приёмка, два исправленных дефекта продукта, Windows CI, инвентаризация возможностей**

| | |
|---|---|
| Дата | 21 ноября 2026 |
| Проект | Canalla LLM Desktop 1.2.7 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD на момент отчёта | `a11eab8` |
| Артефакт собран и прогнан гейтом на | `e78ca91` |
| Установщик | `Canalla LLM_1.2.7_x64-setup.exe`, 96 568 902 B |
| SHA-256 | `ec47412e2dd01ca2b0438447ff1fa512ec606031c30b845f5b2ec63deed4d103` |
| Релизный гейт | **PASS — 22 шага, 0 FAIL, 0 BLOCKED** |
| Расходы этой сессии | **$0.00** (платных операций не было) |

---

## A. Исходная цель

Владелец потребовал продолжать разработку автономно: завершить доступную приёмку 1.2.7 без новых
расходов и вмешательства в production, затем развивать Canalla как универсального агента —
исследование, браузер, управление компьютером, программирование, самоисправление, память,
безопасность — с честными доказательствами и без остановки после первого этапа.

---

## B. Состояние до работы (проверено, не пересказано)

| Параметр | Факт |
|---|---|
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent`, `d5a072e`, синхронна с origin |
| Незакоммиченное | только прежние артефакты/скриншоты (не трогал) |
| Production | установлена 1.2.6, exe `21b50e46…`, БД 2/54/120/63/30, integrity ok, 0 FK |
| Pod / баланс | 0 Pod, `PAID_BATCH_EXHAUSTED`, остаток лимита $0.358 |
| Мониторинг | Windows-задача работает, 30 с, GPU AVAILABLE |
| CI | публичный репозиторий, Actions включены, `workflow_dispatch` работает |
| Токен `gh` | scopes: `gist`, `read:org`, `repo` — **без `workflow`** |
| Zed | 1.26.2, `provider: deepseek`, `model: deepseek-flash`, thinking on, effort high |

---

## C. Что сделано

### C.1. Бесплатная Windows-среда: реальный CI на `windows-latest`

Вместо новой тяжёлой локальной VM (запрещена заданием и не работает на 8 ГБ RAM) использован
существующий workflow `CI`, который имеет `workflow_dispatch` и job `developer-gate` на
`windows-latest`.

- `gh workflow run CI --ref feature/canalla-1.2.7-autonomous-agent` — запуск на нашей ветке.
- Результат: **7/7 job'ов PASS** — `developer-gate`, `native-gate`, `frontend-e2e`,
  `tor-runtime`, `llama-runtime`, `llama-runtime-image`, **`installer-build`** (реальная сборка
  NSIS на чистой Windows-машине).
- Повторный прогон после исправления uninstall: run `44907058233`, 18m01s, **success**.
- Финальный прогон на `a11eab8`: run `44913607831` — **success, 17m36s, 7/7 job'ов PASS**
  (`developer-gate` 17m32s, `installer-build` 9m56s, `frontend-e2e` 7m28s, `native-gate` 5m30s,
  `llama-runtime-image` 5m46s, `tor-runtime` 2m15s, `llama-runtime` 40s).
- `Linux gate` (Ubuntu 24.04, нативный Rust + сборка) на `a11eab8`: run `44913594316` —
  **success, 7m53s** (на `4bc117e` было 7m05s).

### C.2. Матрица чистой установки, первого запуска и удаления

Чистая VM невозможна, поэтому применён безопасный метод: рабочая установка **переименована**
(не удалена), установщик ставит 1.2.7 в штатный путь, тест работает с **изолированным data root**
и **изолированным device credential**, после чего всё восстанавливается байт-в-байт.

| Проверка | Результат |
|---|---|
| Чистая установка 1.2.7 | **PASS** — exit 0, `ProductVersion 1.2.7`, 1088 файлов, `runtime\tor\tor.exe` и `alex-host-loop.exe` на месте, ключ реестра 1.2.7 |
| Первый запуск | **PASS** — `/health` → `version=1.2.7`, `product=alex-llm`, data root создан |
| Закрытие и повторный запуск | **PASS** — `/health` 1.2.7 снова, база сохранилась |
| Отсутствие оставшихся процессов | **PASS** — 0 процессов `alex-*` |
| Удаление | **PASS** — каталог программы удалён, ключ деинсталляции удалён, data root сохранён, процессов нет |
| Восстановление рабочей установки | **PASS** — exe `21b50e46…`, 1278 файлов, реестр 1.2.6, ярлык на месте |

### C.3. Updater

| Проверка | Результат |
|---|---|
| Production-подпись установщика | **PASS** — Ed25519/minisign, ключ `9B328EFF111D1FB2` |
| Привязка подписи к имени файла | **PASS** — trusted comment |
| Вшитый адрес Gateway | **PASS** — `PACKAGED-GATEWAY-URL: PASS` |
| Отказ от чужой/испорченной подписи | **PASS** — Rust-тесты (`updater_signature.rs`, 16 passed, 2 network-ignored) |
| Полный цикл «скачать и установить» | **PARTIAL** — требует опубликованного манифеста; публикация не разрешена |

### C.4. Linux

- WSL2 Ubuntu 24.04 acceptance уже был PASS 10/10; в этой сессии он не выдаётся за Ubuntu Desktop.
- `Linux gate` в GitHub Actions (нативная компиляция и тесты на ubuntu-24.04) — **success**.
- Ubuntu Desktop-специфика (DBus/Secret Service, GUI, logout/login, автозапуск) не проверялась:
  подходящей бесплатной среды нет — зафиксировано как PARTIAL, а не как PASS.

### C.5. Инвентаризация возможностей и матрица сценариев

Создан `docs/agent-capabilities.md`: 84 зарегистрированных инструмента, классификация
VERIFIED / IMPLEMENTED_UNVERIFIED / MISSING / PARTIAL, и матрица A–H:

| # | Сценарий | Статус |
|---|---|---|
| A | Research с проверяемыми источниками | **PARTIAL** (live fetch — VERIFIED, поиск нужен ключ) |
| B | Сборка приложения | **PASS** (реальный LIVE + бесплатные тесты) |
| C | Управление установленной программой | **PARTIAL** (UIA и always-ready проверены, живой моделью не повторялось) |
| D | Починка сломанного проекта | **PASS** |
| E | Восстановление после прерывания | **PASS (механика)**, LEVEL 7 — NOT_TESTED |
| F | Проверка безопасности | **PARTIAL** (offline-проверки PASS, реальная цель не сканировалась) |
| G | Память проекта после перезапуска | **PARTIAL** |
| H | Нет capacity / inference недоступен | **PASS** |

### C.6. Модель Zed

Проверено read-only: Zed **1.26.2**, `agent.default_model = { provider: deepseek, model:
deepseek-flash, enable_thinking: true, effort: high }`. Это ровно требуемая конфигурация
DeepSeek V4.1 Flash; изменений не потребовалось, секреты не читались и не записывались.

---

## D. Исправленные дефекты

1. **Uninstall оставлял каталог программы при работающем Tor.** Молчаливое удаление не могло
   удалить `runtime\tor\tor.exe`: каталог и живой демон оставались, а запись деинсталляции
   исчезала. Добавлен `NSIS_HOOK_PREUNINSTALL`, который останавливает **только** `alex-llm`,
   `alex-backend`, `alex-host-loop` и `tor`, чей образ лежит внутри `$INSTDIR`, ждёт закрытия
   дескрипторов, после чего каталог удаляется. Первая версия хука убивала собственный
   деинсталлятор — это поймано прогоном и исправлено фильтром по именам.
2. **Research не работал на чистой машине.** Каждый HTTPS-запрос падал с
   `unable to get local issuer certificate`: упакованный backend использовал корневое хранилище
   хоста. Клиент исследования теперь строит SSL-контекст из запиненного `certifi`, а
   opt-in live-тест реально скачивает публичную страницу и проверяет, что `DeepResearch`
   записал источник.
3. Ранее в этой же линии: `database_ahead` вместо `CommandError` при откате; UTF-8 в отчёте
   релизного гейта.

---

## E. Проведённые тесты

| Проверка | Результат |
|---|---|
| Релизный гейт `candidate` (финальный артефакт) | **22 шага, 0 FAIL, 0 BLOCKED — PASS** |
| Windows `CI` (GitHub, `windows-latest`) | **7/7 jobs PASS** (два прогона) |
| `Linux gate` (GitHub, ubuntu-24.04) | **success**, 7m05s |
| Живой HTTPS-смоук исследования | **2 passed** (реальная страница + запись источника) |
| Offline-тесты research/Tor | **26 passed** |
| Upgrade/backup/recovery | **11 passed** (`test_upgrade.py`) |
| Ruff (backend, gateway, format) | clean |
| BasedPyright (поставляемый код) | **0 ошибок, 0 предупреждений, 153 файла** |
| Матрица чистой установки | 6/6 проверок PASS (см. C.2) |

---

## F. Независимые доказательства

- Лог гейта: `artifacts/release-gate-1.2.7-research-fix.txt` (22 шага, artifact SHA-256 и commit
  совпадают).
- Контрольные суммы: `artifacts/SHA256SUMS-1.2.7.txt`.
- CI: `gh run view 44907058233` (Windows, success), `44913607831` (финальный HEAD).
- Матрица установки: `D:\canalla-vm\clean-install\evidence\clean-install.log` и `restore.log`.
- Инвентаризация: `docs/agent-capabilities.md`.
- Состояние работ: `artifacts/agent-state.json` (машиночитаемое, переживает перезапуск сессии).

---

## G. Инфраструктура

| Что | Состояние |
|---|---|
| Production 1.2.6 | восстановлена после каждого теста: exe `21b50e46…`, 1278 файлов, реестр 1.2.6 |
| Gateway | `https://gateway.12testers.store`, active, 0 Pod |
| Мониторинг | Windows-задача, 30 с, `PAID_BATCH_EXHAUSTED`, ничего не покупает |
| GitHub Actions | CI и Linux gate зелёные; новых workflow не добавлялось (см. ограничения) |
| Zed | 1.26.2, DeepSeek V4.1 Flash |

---

## H. Финансы

| Статья | Значение |
|---|---|
| Платные операции этой сессии | **нет** |
| Pod создано | **0** |
| Остаток бюджета серии | $0.358 (`PAID_BATCH_EXHAUSTED`) |
| Баланс провайдера | только фоновое хранилище (~$0.01/час) |

---

## I. Production и пользовательские данные

| Параметр | Значение |
|---|---|
| users / chats / messages / devices / tasks | 2 / 54 / 120 / 63 / 30 |
| integrity_check | ok |
| foreign key violations | 0 |
| Установленный exe | `21b50e462156b9f109303623e4c67225d4e44daad981d8f439a83d16c8cafaf1` (1.2.6) |
| Файлов в программе | 1278 |

Пользовательская установка дважды переименовывалась для чистой установки и дважды
восстанавливалась; каждый раз проверялись хеш exe, число файлов, версия и ключ реестра.

---

## J. Git

```
a11eab8 Record the research fix and the final gated artifact
e78ca91 Give research the CA bundle the product ships
ae7fa84 Record the clean-install matrix and the capability inventory
4bc117e Stop the product's own processes before uninstalling
d5a072e Report the final release acceptance
```

Всё запушено в `origin/feature/canalla-1.2.7-autonomous-agent`; force-push не выполнялся.

---

## K. Уровень автономности и ограничения

| Уровень | Статус |
|---|---|
| 1–4 | **PROVEN** (реальный LIVE, 18 ответов, 17 tool calls, TaskDesk) |
| 5 | **PROVEN в рамках попытки** |
| 6 | **BLOCKED_BY_LIVE_BUDGET** — нужен один реальный прогон, бюджет исчерпан |
| 7 | **NOT_TESTED** — механика durable resume доказана, LIVE с моделью требует бюджета |

Внешние ограничения, зафиксированные этой сессией:

1. **Новый Windows-workflow в CI добавить нельзя**: у токена `gh` нет scope `workflow`, а
   `workflow_dispatch` регистрирует только workflow, существующие на default-ветке (`main`).
   Поэтому чистая установка проверена на хосте с полным снимком/восстановлением, а не в CI.
2. **Чистая Windows VM** не завершает unattended-установку на этой машине (VirtualBox +
   Hyper-V, 8 ГБ RAM) — повторять не стал, как и требовало задание.
3. **LIVE-поиск** требует ключа TinyFish; live *fetch* уже доказан.
4. **Публикация** не выполнялась.

---

## Расхождения между прежними отчётами и проверенным состоянием

- Прежний отчёт фиксировал «uninstall не выполнялся» — теперь выполнен и **нашёл дефект**,
  который исправлен и перепроверен.
- Прежний отчёт считал research проверенным только offline — теперь есть живой HTTPS-прогон, и он
  вскрыл отсутствие CA-бандла в упакованном продукте.
- Хеш установщика менялся в этой сессии трижды (исправление uninstall, затем CA-бандл); актуален
  `ec47412e…`, все промежуточные значения сохранены в логах гейта.
- `docs/release-1.2.7.md` и `artifacts/SHA256SUMS-1.2.7.txt` обновлены под финальный артефакт.

---

## Что дальше (без остановки работы)

Следующие безопасные шаги, которые не требуют новых разрешений: расширение живой
research-проверки на реальный поисковый провайдер при появлении ключа; наполнение durable-resume
сценария для реальной модели (готово к запуску в рамках одного платного окна); дальнейшая
инвентаризация Memory и Security с добавлением недостающих тестов. Платный LIVE и публикация
остаются за владельцем.
