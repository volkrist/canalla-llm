# CANALLA LLM 1.2.0 — STEP 5B RELEASE CANDIDATE REPORT

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `c98c84b` · Версия продукта: **1.2.0**

```
RELEASE BLOCKED — OFF-MACHINE SIGNING KEY BACKUP REQUIRED
```

**Почему:** §0 этого шага — предусловие, которое может подтвердить только оператор, и подтверждения
нет. Офф-машинной копии зашифрованного приватного ключа подписи не существует (в STEP 5A это было
записано как `PENDING`). Пока её нет, потеря этой машины означает потерю возможности выпускать
обновления для 1.2.x — поэтому шаг остановлен **до** любой мутации, ровно как предписано
(«If off-machine backup has NOT been completed: STOP … Do nothing else»).

Ничего не изменено: установка не запускалась, апгрейд 1.1 → 1.2 не выполнялся, Gateway не трогался,
артефакт никуда не выгружался, пересборки не было.

---

## OFF-MACHINE SIGNING BACKUP

| | |
|---|---|
| Completed (encrypted key copy off this computer) | **NO — не подтверждено** |
| Signing password in operator's password manager | **NOT CONFIRMED** |
| Backup recovery passphrase stored separately | **NOT CONFIRMED** |
| Secrets printed into chat or logs | **NO** |
| Private key / password in repository | **NO** |

Известные места, из которых оператору нужно сделать три копии (значения мне присылать не нужно):

| Что | Где лежит сейчас |
|---|---|
| Зашифрованный приватный ключ | `D:\canalla-release-backup\canalla-updater-production.key.enc` + `.enc.json` (AES-256-CBC, PBKDF2-SHA256 200 000) |
| Пароль подписи | Credential Manager, target `CanallaLLM/UpdaterSigning/Production` |
| Парольная фраза бэкапа | Credential Manager, target `CanallaLLM/UpdaterSigning/ProductionBackup` |

---

## VERIFIED BEFORE THE STOP (§1)

| Проверка | Ожидание | Факт | Итог |
|---|---|---|---|
| Ветка | `release/canalla-1.1.0` | `release/canalla-1.1.0` | **PASS** |
| HEAD | `c98c84b` | `c98c84b` | **PASS** |
| `main` / `origin/main` | не тронуты (`9bd7548`) | `9bd7548` / `9bd7548` | **PASS** |
| Теги | только `v1.0.0` | только `v1.0.0` | **PASS** |
| Рабочее дерево | чистое (кроме drift скриншотов) | чистое | **PASS** |
| Версия продукта | `1.2.0` | `1.2.0` | **PASS** |
| Ключ updater в клиенте | production `9B328EFF111D1FB2` | `9B328EFF111D1FB2` | **PASS** |
| Тестовый ключ `B79FF90B52D00F24` в конфиге | отсутствует | отсутствует | **PASS** |
| **SHA256 кандидата** | `e13ba09c…f3676a` | `e13ba09c369a351a513dcc22b3d3182cefaab3f4cbd7dcb0432e266878f3676a` | **PASS (точное совпадение)** |

Пересборка не выполнялась: расхождения артефакта нет, причины менять код нет (§2 соблюдён).

---

## PRE-UPGRADE DATA INVENTORY (read-only baseline, §3)

Снято **только чтение**, без содержимого и без секретов. Это эталон, с которым сверяется 1.1.0 → 1.2.0.

| Категория | Baseline (1.1.0, сейчас) |
|---|---|
| users | **2** |
| chats | **8** |
| messages | **34** |
| projects | **1** |
| memories | **2** |
| documents | **0** |
| paired_devices | **2** |
| device_id | `908c2242-65c4-4dd9-bfc3-c168de3a37c4` |
| compute_preferences | **1** |
| compute_sessions / events / control | **3 / 17 / 1** |
| tool_runs | **8** |
| message_contexts / generation_usage | **17 / 17** |
| web_source_snapshots | **24** |
| local_tasks | **0** |
| auth_sessions | **3** |
| alembic_version | **1** (одна строка — текущая ревизия) |
| Корень данных | `%LOCALAPPDATA%\Alex LLM\` — `backups, data, device.json, documents, logs, models, runtime, tasks, tor` живы, WAL писался в 17:47 |

Данные настоящие и рабочие, а не пустой профиль: апгрейд предстоит реальный.

| | |
|---|---|
| Verified pre-upgrade backup created via Canalla's own mechanism | **NOT RUN** (§4 не достигнут) |

---

## UPGRADE

| | |
|---|---|
| 1.1 → 1.2 | **NOT RUN** |
| Verified pre-upgrade backup | **NOT RUN** |
| users / chats / messages / projects / memory / documents | **NOT RUN** |
| credentials | **NOT RUN** |
| device_id | **NOT RUN** (baseline зафиксирован) |
| settings | **NOT RUN** |
| compute preferences | **NOT RUN** |

Установка 1.2.0 поверх реальной 1.1.0 не проводилась. Это не техническая невозможность: шаг
остановлен предусловием §0, а сам апгрейд по условиям шага разрешён только после верифицированного
бэкапа (§3–§4), который тоже не создавался — я не трогаю живую установку, пока открыт стоп-гейт.

---

## WINDOWS INSTALLED

| | |
|---|---|
| Version | **1.1.0** (установленная; 1.2.0 не устанавливался) |
| Backend / AI status / Computer / Tor / Circuit | **NOT RUN** |
| Single instance | **NOT RUN** |
| Backend recovery / Tor recovery | **NOT RUN** |
| Autostart | **NOT RUN** |

Для справки, что уже доказано на этом артефакте в STEP 5A (не заменяет установочную приёмку):
сборка выполнена production-ключом, подпись установщика проверена независимым `minisign 0.11`
(«Signature and comment signature verified»), подписанный trusted comment —
`file:Canalla LLM_1.2.0_x64-setup.exe`, то есть version binding держится на реальном артефакте.

---

## TOR ROUTING

| | |
|---|---|
| Natural language («Открой https://example.com через Tor») | **NOT RUN** (нужна установленная 1.2.0) |
| TOR REQUIRED / SOCKS5h / Circuit proof | **NOT RUN** |
| DNS leak | **NOT RUN** |
| Clearnet fallback | **NOT RUN** |

Детерминированное доказательство маршрута уже существует в кодовой базе и было зелёным на этом же
дереве: локальный SOCKS5h-листенер записывает CONNECT, DNS-tripwire отвергает любой не-loopback
резолв, `tor_fetch` отдаёт типизированный код вместо «completed» о запуске, не переславшем ни байта
(10 тестов в составе backend-набора). Живой E2E по-прежнему требует установленного продукта.

---

## GATEWAY

| | До деплоя (замерено сейчас, read-only) | После деплоя |
|---|---|---|
| `/health` | **200** — `version: 1.1.0`, `protocol: 1`, `ready: true`, `database: ok`, `provider_configured: true` | **NOT RUN** |
| `/updates/latest` | **404** — ровно ожидаемое пред-деплойное состояние | **NOT RUN** |
| Сервис | `alex-gateway` — **active** | — |
| Текущий релиз | `/opt/alex-gateway/current` → `/opt/alex-gateway/releases/adb568734430` | — |
| Манифест на сервере | **отсутствует** (`/etc/alex-gateway`, `/opt/alex-gateway` — ни update-, ни manifest-файлов) | — |
| Rollback | **READY** — откат = вернуть симлинк `current` на `adb568734430`; доступные релизы: `9bd75486c34b`, `adb568734430`, `ce97f67a37c4`, `e0987a9d3ff0df07dd6aa9828a15ed05ec59b0e3` | **NOT USED** |

Важное следствие для будущего деплоя: манифеста на сервере нет, поэтому **после** выката маршрута
`/updates/latest` обязан отвечать **204** («нет обновления»), а не 404 — и это будет проверяться
именно так. Production-манифест при этом не публикуется.

Деплой **не выполнялся**: он авторизован только после локальной установочной приёмки 1.2.0 (§15),
которой ещё не было.

---

## HOSTING

| | |
|---|---|
| Production URL | **не создан** |
| Windows artifact uploaded | **NO** |
| Directory listing | не создавался |
| Downloaded SHA256 | **NOT RUN** |
| Matches local `e13ba09c…f3676a` | **NOT RUN** |
| Signature verify (`9B328EFF111D1FB2`) | **NOT RUN** |

---

## UPDATER RC E2E

| Кейс | Результат |
|---|---|
| same version · valid update · bad signature · version mismatch | **NOT RUN** |
| replay · downgrade · corrupt · wrong platform | **NOT RUN** |
| 404 · 500 · timeout · offline · interrupted | **NOT RUN** |
| busy deferral (generation / local task / backup / restore) | **NOT RUN** |

Детерминированные уровни этого набора зелёные на текущем дереве (Rust `updater_signature` 15,
Gateway `test_updates` 25, Vitest updater 32 — включая отказ на плохой подписи, несовпадение версии,
replay и downgrade, а также положительные контроли). Установочный E2E ждёт своей очереди по плану.

---

## LINUX

| | |
|---|---|
| Статус | **PREVIEW / EXPERIMENTAL** — Ubuntu 24.04 LTS x86_64, desktop-приёмка DEFERRED (ограничение среды) |
| Engineering regression в STEP 5B | **NOT RE-RUN** (в 5A: Rust Linux 101 passed, backend Linux 646 passed / 0 failed, packaging guard 5, Tor runtime acceptance 9/9 на установленном `.deb` 1.2.0) |
| Production Linux manifest entry | **NOT PUBLISHED** |

---

## PRODUCTION STATE

```
main merged:                    NO
v1.2.0 tag:                     NO
stable production manifest:     NOT PUBLISHED
Gateway deployed:               NO
Artifact hosted:                NO
Real 1.1 -> 1.2 operator upgrade: NO
GPU:                            NOT USED (0 Pods, $0)
```

---

## UNBLOCKING CONDITIONS

Шаг снимается с блокировки ровно тремя действиями оператора (значения паролей мне присылать не
нужно — достаточно подтверждения «готово»):

1. Скопировать `D:\canalla-release-backup\canalla-updater-production.key.enc` **и** `.enc.json` за
   пределы этой машины (менеджер паролей, внешний носитель, другая машина).
2. Перенести значение из Credential Manager `CanallaLLM/UpdaterSigning/Production` в свой менеджер
   паролей (посмотреть: `Панель управления → Диспетчер учётных данных → Учётные данные Windows →
   … → Показать`).
3. То же для `CanallaLLM/UpdaterSigning/ProductionBackup` — **отдельно** от файла бэкапа.

После подтверждения порядок работ: §3–4 бэкап данных и его верификация → §5–7 установка 1.2.0 и
сверка с baseline → §8–13 single instance, восстановление, живой Tor E2E, autostart → §14–17
Gateway, хостинг, сверка хеша → §18–19 updater RC E2E и busy-state → кандидатный отчёт.

Merge, tag и production-манифест в STEP 5B не выполняются по определению шага.

---

## FINAL

```
RELEASE BLOCKED — OFF-MACHINE SIGNING KEY BACKUP REQUIRED

verified and green   repo state · version 1.2.0 · production key 9B328EFF111D1FB2 ·
                     candidate sha256 e13ba09c…f3676a (exact match) · Gateway precheck
                     (/health 200, /updates/latest 404, rollback READY)
mutated              nothing — no install, no upgrade, no deploy, no upload, no rebuild
reason               an off-machine copy of the production signing key does not exist,
                     and only the operator can create it
```
