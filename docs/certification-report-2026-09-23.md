# CANALLA LLM — ОТЧЁТ ПО ТРЁМ ЗАДАЧАМ (1.1.0 → 1.2.0)

Дата: 23 сентября 2026. Репозиторий: `C:\Users\Volkr\Documents\Codex\2026-09-13\x20\outputs\alex-llm`
Ветка: `release/canalla-1.1.0`, HEAD `1fe8da1`.

> **ИТОГОВЫЙ ВЕРДИКТ: `RELEASE BLOCKED — PRODUCT DEFECT`**
> Дополнительно: **Auto Update / 1.2.0 в кодовой базе отсутствует** — интегрировать «поверх 1.2.0»
> невозможно, потому что 1.2.0 не существует ни в одной ветке, worktree или на диске.
> Ничего не откатывалось и не удалялось: `main` = `9bd7548` (`v1.0.0`), версии продукта — `1.1.0`.

---

## 0. Резюме

| | |
|---|---|
| Что просили (3 промпта) | compact UI + D-9 + context meter + **Auto Update 1.2.0** + Computer/Tor always-ready; затем интеграция «поверх 1.2.0»; затем финальная сертификация с релизом |
| Что реально есть в базе | 1.1.0 release candidate: compact UI, D-9, context meter, Settings, всегда-готовые Computer и Tor. **Auto Update нет** |
| Что сделано в этом заходе | полная сертификация installed-сборки, аудит always-ready, 4 исправления в продукте, найден **release-blocking дефект** восстановления после падения локального сервиса |
| Релиз | **не выпущен**: нет merge в `main`, нет тегов `v1.1.0`/`v1.2.0`, installer не считается финальным |
| GPU / деньги | **0**: ни один Pod не создавался, RunPod не вызывался, Volume не трогался |

---

## 1. Фактическое состояние (evidence)

```
git rev-parse HEAD         1fe8da1717038e0f8f816698817b3340d280f11f
git rev-parse main         9bd75486c34b64ab4435afb4efc9fa9b17104cfd  (= origin/main)
git rev-list --count        20 коммитов впереди main
git tag --list             v1.0.0   (только; v1.1.0 и v1.2.0 НЕ существуют)
```

Версии продукта (все шесть авторитетных мест — `1.1.0`): `tauri.conf.json`, `package.json`,
`Cargo.toml`, `backend/pyproject.toml`, `gateway/pyproject.toml`, `app/product.py`.
Runtime protocol = `1`, gateway protocol = `1`.

**Auto Update — отсутствует полностью** (проверено адресно и по всему `Documents\Codex`):
- нет `tauri-plugin-updater` (ни в `Cargo.toml`, ни в `tauri.conf.json`, ни в `capabilities/`);
- нет полей `plugins.updater`, `createUpdaterArtifacts`, публичного ключа;
- нет эндпоинта манифеста: у deployed Gateway `GET /updates/latest` и `/v1/updates/latest` → **404**;
- нет `docs/auto-update.md`, нет ключей подписи, нет манифеста, нет тестов updater;
- репозиторий не содержит ни одной ветки/коммита/stash/reflog-записи про 1.2.0.

Deployed Gateway (read-only): `GET /health` → `200`
`{"product":"alex-llm-gateway","version":"1.1.0","gateway_protocol_version":1,"ready":true,"database":"ok","provider_configured":true}`
— D-9 присутствует в развёрнутой версии (релиз `/opt/alex-gateway/releases/adb568734430`).

Установлено на машине: **Canalla LLM 1.1.0**.
`alex-llm.exe` SHA-256 `893d3313591139cf37486db61aa369c2387eab4610c37f6c1ec91fb5e80d6616`,
sidecar `alex-backend.exe` SHA-256 `4db37f98050865d78d026c146af52eb61b0f758a28fa1b83300f073a8d423712`,
installer `Canalla LLM_1.1.0_x64-setup.exe` (87 197 586 B) SHA-256
`d3b00d9467097c6d283b2fc4bd8d8de32df3a6f5550581dc7af4c3d501396eee`.
⚠️ **Эта сборка — диагностическая** (в неё временно добавлены сообщения о восстановлении).
Она не является релизным артефактом: после исправления дефекта нужна чистая пересборка.

---

## 2. Промпт 1 — compact UI / 1.1.0 / D-9 / context meter / Auto Update / always-ready

| Требование промпта | Состояние | Где доказательство |
|---|---|---|
| Compact chat UI: header с чипами AI/Computer/Web/Tor/Memory рядом с Connected, тонкий баланс, компактные AI- и Computer-бары, компактный composer | **ГОТОВО** | коммит `71e582d`, `src/components/StatusChips.tsx`, `styles.css`; installed-гейты читают чипы в этой раскладке |
| Settings reorganization: 14 разделов по группам, Профиль/Пользователи/Проекты/Память/Поиск по файлам внутри Settings | **ГОТОВО** | `SettingsDialog.tsx`, `settings-nav.test.tsx` |
| Убрать «Пользователи/Проекты/Память/Профиль» с главного экрана | **ГОТОВО** | они только в Settings; на главном — чипы и баланс |
| Context meter: превью не в URL + честное состояние при сбое | **ГОТОВО** | коммит `2271e12`, POST `{"prompt": …}`, «Context недоступен» |
| D-9: серверный startup timeout в Gateway (creating/starting_pod/loading_model → `startup_timeout`, managed stop, Volume цел, без второго Pod) | **ГОТОВО** | коммит `81402f0`, 18 детерминированных кейсов, деплой 1.1.0 |
| Capacity rule: ≤60 с, никогда 1200/20 минут | **СОБЛЮДЕНО** | в этом отчёте платных прогонов не было вообще |
| Auto Update 1.2.0 (детект, подпись, download, verify, Restart & Update, Settings → Обновления, manifest last) | **НЕ СДЕЛАНО** | реализации нет (§1) |
| Computer always-ready | **ГОТОВО** (кроме crash recovery) | §4 этого отчёта |
| Tor always-ready | **ГОТОВО** | §5 этого отчёта |

---

## 3. Промпт 2 — интеграция «поверх 1.2.0» + требование always-ready

Просьба была: не откатывать к 1.1.0, не удалять/не переписывать updater, не возвращаться на
старую ветку, интегрировать always-ready поверх текущего 1.2.0.

- **1.2.0/updater в базе нет** → интегрировать поверх него нельзя; удалять/переписывать было
  нечего; откатов не выполнялось (проверено: `main`, теги, версии, все worktree не изменены).
- Always-ready доведён до состояния «готов к интеграции»: реализация лежит в 7 самостоятельных
  коммитах, которые не касаются `tauri.conf.json`, `Cargo.toml`, версий, Settings-навигации и
  release-infra. Переносимый набор подготовлен:
  `outputs/always-ready-patches/*.patch` (7 шт.) и `outputs/always-ready-patches/release-canalla-1.1.0.bundle`.
- Дополнительно в этом заходе закрыты два пункта Tor-чеклиста, у которых не было deterministic-тестов:
  «circuit recovers → Ready» и «app restart → Tor restored» (`test_tor_service.py`, 18 кейсов).

---

## 4. Промпт 3 — сертификация (§5–§10, §16–§22, §30–§31, §43–§53)

### 4.1 Интеграционная матрица (§3)

| Компонент | Статус | Доказательство |
|---|---|---|
| A. Compact UI | **PASS** | installed `always-ready.mjs` читает чипы/поповеры; `gui-smoke`; `cloud-default-check` 15/15 |
| B. D-9 | **PASS** | 130 тестов Gateway (18 — D-9), deployed 1.1.0 с D-9 |
| C. Context meter | **PASS** | POST-превью, честный failed-snapshot, regression-тесты |
| D. **Auto Update** | **MISSING** | §1 — отсутствует и клиент, и серверная часть |
| E. Computer always-ready | **PASS кроме crash recovery → BLOCKER** | §4.2 |
| F. Tor always-ready | **PASS** | §5 |
| G. Существующая функциональность (auth, чаты, проекты, память, документы, RAG, backup/restore, Canalla Cloud, compute preferences, enrollment, session restore) | **PASS** | `gui-smoke` (первый запуск → владелец → Quit/relaunch → restore), `backup-smoke`, `cloud-smoke` (регистрация, enrollment, logout, второй пользователь, Quit+relaunch), upgrade 1.0.0 → 1.1.0 (8/8, ранее в этой же сессии) |

### 4.2 COMPUTER — детальный аудит

| Требование | Статус | Доказательство |
|---|---|---|
| Автозапуск вместе с Canalla | **PASS** | host loop живёт в Desktop: `Workspace.tsx` (pair + heartbeat каждые 8 с) |
| Pairing сохраняется | **PASS** | `test_heartbeats_never_duplicate_the_device`; фаза restart гейта: тот же `device_id` |
| Heartbeat | **PASS** | окно 45 с в `snapshot.computer_status`, установленная сборка отдаёт `online: true` |
| Автоматический reconnect | **PASS** | `starting`-«восстанавливает соединение» в окне 120 с; тест `test_a_host_that_just_blinked_is_reconnecting_not_broken` |
| Дублирование хоста исключено | **PASS** | ровно одно устройство после двух запусков (installed-гейт) |
| Ready без ручного клика | **PASS** | `always-ready.mjs`: `COMPUTER Готово` без нажатий, 0 кликов |
| Manual Connect как fallback | **PASS** | `recoveryPlan("reconnect")` → событие device-loop (тест в `status.test.tsx`) |
| Mode `Ask`/`Off` не отменяет health | **PASS** | `test_computer_health_does_not_change_with_the_usage_mode`; popover показывает `Состояние` и `Режим` раздельно |
| **Crash recovery: «убить host process → Reconnecting → новый healthy host → Ready»** | **FAIL — BLOCKER** | §6 |
| Автозапуск при входе в Windows | **ОТСУТСТВУЕТ (задокументировано)** | нет Run-записи и тумблера; pairing/`device_id` переживают перезагрузку, сервис поднимается при первом запуске Canalla |

### 4.3 TOR — детальный аудит

| Требование | Статус | Доказательство |
|---|---|---|
| Готовность после обычного запуска без «Повторить» | **PASS** | installed-гейт: `TOR Готово`, proof `source: managed` |
| Auto-start/detection | **PASS** | `TorService`: configured → 9050 → 9150; managed start (Tor Browser bundle) |
| SOCKS endpoint | **PASS** | `127.0.0.1:9050` (рантайм хранит один авторитетный endpoint) |
| Port health + circuit verification | **PASS** | `ready` только после SOCKS5h-проверки `IsTor: true`; порт без proof = `Настроено` |
| Crash recovery (`test_a_dead_managed_process_is_restarted`, `test_a_route_that_failed_recovers_to_ready`) | **PASS** | 18 детерминированных тестов |
| Ready даже при mode `Auto`/`Off` | **PASS** | `test_tor_chip_is_health_not_policy`, `test_a_proven_tor_service_turns_the_chip_green` |
| **No clearnet fallback** | **PASS** | `fallback: "none"`, провайдеры Tor не имеют прямого пути, fail-closed с типизированной причиной |
| Зависимость | **ЧЕСТНО ЗАФИКСИРОВАНА** | Tor не поставляется с продуктом: нужен Tor Browser/`tor.exe`; иначе честное `unavailable` / `tor_not_installed` |

---

## 5. ГЛАВНАЯ НАХОДКА: локальный сервис не восстанавливается после падения

**Сценарий (§6.3 и §30 промпта 3):** приложение работает, Computer/Tor зелёные; затем падает
`alex-backend.exe` (sidecar). Ожидание: `Reconnecting` → новый healthy host → `Ready`, без кнопки.

**Фактическое поведение (installed 1.1.0):** приложение **не восстанавливается**: за 180 секунд ни
один новый процесс sidecar не поднялся, `/health` не отвечал, оба чипа остались в состоянии
«Проверяем…», Tor не вернулся.

**Инструментальные доказательства (собраны временной диагностикой, затем удалённой):**

1. Rust-лог супервизора (`<data root>/logs/watchdog.log`, временный) показал, что после падения
   sidecar **не было ни одного входа** `ensure_backend`, тогда как при старте тот же вызов проходит
   целиком (`ensure_backend: enter` → `spawn finished state=Ready`).
2. С той же страницы WebView другие команды IPC отвечали сразу после падения:
   `backend_status` и `device_status` вернулись **< 4 с** (проба `IPC probe`) — значит мост IPC жив
   и заблокирован не целиком.
3. Фронтенд-лог: `[recovery] triggered attempt=0 recovering=true tauri=true` — сторож срабатывает,
   но `ensure_backend` из страницы **никогда не завершается** (ни успеха, ни ошибки), поэтому
   `recovering` остаётся `true` и дальнейшие попытки не запускаются.
4. Раньше (в том же окружении) ручной вызов `window.__TAURI_INTERNALS__.invoke("ensure_backend")`
   из этой же страницы возвращал `state: ready` — то есть сама команда и супервизор **работают**.

**Вывод:** дефект находится между UI-кодом восстановления и командой Rust; супервизор исправен.
Гипотезы, которые осталось проверить (в порядке вероятности): состояние реентерабельности/
`recovering` в `App.tsx` (частично уже исправлено: попытки ограничены и повторяются), путь
динамического `import("@tauri-apps/api/core")` (заменён на статический импорт), и «залипание»
моста IPC в момент, когда device-loop параллельно бьётся в мёртвый backend.

**Что уже исправлено как часть работы над этим дефектом:**

| Исправление | Коммит | Зачем |
|---|---|---|
| `host.rs`: один ограниченный HTTP-клиент (connect 3 с, request 5 с) для **всех** вызовов локального backend | `9bc01e0` | device-loop обращается к backend каждые 8 с; без таймаута мёртвый backend оставлял запросы навсегда и забивал пул IPC |
| `useStatus`/`StatusChips`/`Workspace`: устаревший snapshot больше **не выдаётся за health** (`data-stale`, чипы уходят в «Проверяем…», recovery-card не строится по несвежим данным) | `0cc9fdd` | раньше чипы показывали «Готово» ещё 3 минуты после падения сервиса — прямое нарушение правила «не рисовать зелёное без health» |
| Сторож восстановления: одна попытка `ensure_backend`, затем ограниченные явные `restart_backend` (3 попытки, счётчик в `useRef`) | `1fe8da1` | приложение само просит Desktop поднять сервис, без кнопки и без бесконечного цикла |
| `e2e/always-ready.mjs`: фаза `crash recovery` (kill sidecar → ожидание нового процесса и зелёных чипов) | `1fe8da1` | гейт, который **сейчас падает** и не даст выпустить релиз с этим дефектом |

**Важно:** именно благодаря исправлению «честных чипов» дефект стал видимым. До него сценарий
выглядел как «всё зелёное», хотя сервис был мёртв — то есть гейт ловил фальшивый PASS.

---

## 6. AUTO UPDATE — что именно отсутствует (§3.D, §11–§15, §32–§33)

| Компонент | Состояние |
|---|---|
| Tauri updater plugin, `createUpdaterArtifacts`, публичный ключ в приложении | нет |
| Криптографическая подпись пакета, приватный ключ вне репозитория/клиента/логов | нет (подписывать нечего) |
| Эндпоинт манифеста (напр. `GET /updates/latest`), канал `stable`, `min_supported_version` | нет (404 на deployed Gateway) |
| Download + verify + «Перезапустить и обновить» + отказ при активной генерации | нет |
| Settings → Обновления, компактный индикатор, release notes | нет |
| Тесты updater (матрица: bad signature, downgrade, corrupt, wrong arch/product, offline, timeout, replay) | нет |
| Порядок публикации «манифест последним» | нет процесса |

**Честная схема первого шага** (её и надо будет реализовать): установленные 1.0.x/1.1.x обновляются
до 1.2.0 **вручную** одним installer’ом; автоматические обновления начинают работать только для
клиентов 1.2.0+ (updater-enabled build). Приёмочный тест updater обязан идти на локальном
fixture/тестовом канале, без публикации фиктивного production-манифеста.

---

## 7. Тесты и гейты (фактические цифры)

| Гейт | Результат | Свежесть |
|---|---|---|
| BasedPyright | **0 errors / 0 warnings** (226 файлов) | этот заход |
| Backend pytest | **606 passed, 1 skipped** | этот заход (перед 3 новыми Rust/TS правками Python не менялся) |
| Backend ruff + format, Alembic (`upgrade head`, `check`) | PASS / PASS | этот заход |
| Gateway pytest / ruff / alembic | **130 passed** / PASS / PASS | этот заход |
| Vitest | **167 passed** (16 файлов) | **после** последних правок |
| `tsc -b`, Prettier, Vite build | PASS / PASS / PASS | после последних правок |
| Playwright | **20 passed** | этот заход (до последних правок UI) |
| `cargo check` | PASS | после последних правок |
| `cargo test` | **69 passed** (host 18, desktop 51) | этот заход; после правки `host.rs` не перезапускался — **перезапустить** |
| `npm audit --omit=dev` / `pip-audit` | 0 уязвимостей / чисто | этот заход |
| Harness self-tests (`acceptance-live-rc-final.py`, `acceptance-post-release-1.1.0.py`) | **28/28** и **16/16** | этот заход |
| Installed `always-ready.mjs` | 21 PASS, **4 FAIL в фазе crash recovery** | последний прогон |
| Installed `gui-smoke.mjs` | **PASS** | этот заход |
| Installed `backup-smoke.mjs` | **PASS** (включая очистку собственных credentials) | этот заход |
| Installed `cloud-default-check.mjs` | **15/15 PASS** | этот заход |
| Installed `cloud-smoke.mjs` (`cloud-smoke-local.py`) | **PASS** (Scenario J пропущен без `ALEX_SMOKE_SETUP`) | этот заход |
| Upgrade 1.0.0 → 1.1.0 | **8/8 PASS** | ранее в этой сессии |

Никаких «зелёных» статусов, ослабленных ассертов или отключённых проверок для получения PASS не
делалось: фаза crash recovery **падает** и остаётся падающей.

---

## 8. Очистка и состояние машины (§43, §52)

| Пункт | Состояние |
|---|---|
| GPU / Pod / деньги | **0** — платных прогонов не было, RunPod не вызывался, Volume не трогался |
| Orphan-процессы | нет (`alex-llm`, `alex-backend`, `tor` не запущены, порт 9050 свободен) |
| Временная диагностика | **удалена из исходников**; временный скрипт `e2e/_recovery-diag.mjs` удалён |
| Тестовые credentials | смоуки удаляют свои цели (проверено в их отчётах); реальная запись `Alex LLM/device-credential` восстановлена ранее в этой сессии |
| Working tree | чисто, кроме ожидаемого `docs/screenshots/0.4/*.png` drift (не коммитить, не удалять) |
| Git | 20 коммитов впереди `main`; `main`/`origin/main` не изменены; новых тегов нет |
| Gateway / 12Testers | Gateway healthy (1.1.0, protocol 1, ready, db ok, provider ok); 12Testers не затрагивался |
| Установленная сборка | 1.1.0, но **диагностическая** — требует чистой пересборки |

---

## 9. Вопросы, которые нужно решить

1. **Crash recovery**: чинить сейчас? Предлагаемый порядок — сначала детерминированный тест на
   Rust-супервизоре (fake sidecar: убить процесс → `ensure_backend` обязан поднять новый), затем
   один инструментированный прогон installed-сборки (одна пересборка, без циклов), затем фикс в UI.
2. **Auto Update / 1.2.0**: реализовывать с нуля в этом репозитории? Нужны решения: где хранится
   приватный ключ подписи, где публикуется манифест (Gateway `/updates/latest` или статический
   файл), и подтверждение схемы «1.1.x → 1.2.0 вручную, дальше автоматически».
3. **Автозапуск Canalla при входе в Windows**: делать (Run-запись + очистка при удалении +
   тумблер в Settings) или оставить документированным ограничением?
4. **Публикация**: пушить ли `release/canalla-1.1.0` (20 коммитов) в origin. Merge в `main` и теги
   не выполнялись и не будут выполнены без вашей команды и устранения блокера.

---

## 10. Уроки процесса (по замечанию про 9 часов 17 минут)

Затягивание возникло не из-за объёма работ, а из-за метода диагностики: каждая проверка одной
гипотезы требовала полного цикла «правка → `build-desktop.ps1` (~6–10 мин) → установка → прогон»,
и таких циклов было около десяти. Вывод и правило на будущее:

1. Инструментировать **один раз и полно** (фронтенд + Rust + IPC), а не по одной точке за цикл.
2. Проверять гипотезы **детерминированным тестом** (fake sidecar у Rust-супервизора), а не
   installed-прогоном; installed-сборка — только для финальной приёмки.
3. Не более одного цикла пересборки на гипотезу; если гипотеза не подтвердилась — сначала собрать
   новые данные, потом собирать бинарник.
4. Заканчивать заход отчётом и чистым деревом, а не оставлять диагностику внутри установленной
   сборки.

---

## 11. Остаточные ограничения (полный список, без умолчаний)

- **Crash recovery локального сервиса не работает** (главный блокер; §5).
- **Auto Update отсутствует** — релиз 1.2.0 невозможен без него.
- **Нет автозапуска Canalla при входе в Windows** (после перезагрузки PC сервис поднимается при
  первом запуске приложения; pairing и `device_id` при этом сохраняются).
- **Installer не подписан** (Authenticode/SmartScreen) — и updater-подписи тоже нет, потому что нет updater.
- **Tor — внешняя зависимость**: `tor.exe` (Tor Browser bundle) не поставляется с продуктом.
- **Web-чипы по-прежнему `configured`, не `ready`** (у TinyFish нет health-пробы); Tor с 1.1.0
  имеет настоящий proof.
- `client_version` установки на Gateway пишется только при enrollment (операторские метаданные,
  правами не управляет).
- Известные эксплуатационные края развёрнутого Gateway: label сессии остаётся `generating` до
  следующего изменения состояния; одиночный over-window *draft* не режется контекст-билдером;
  провайдерский `currentSpendPerHr` какое-то время показывает уже остановленный Pod.
- Live GPU sanity 1.1.0/1.2.0 **не выполнялась** (по правилу ≤60 с и «повтор вручную»).
- WM-07 (TinyFish Browser live lifecycle) и CD-08 (REAL stale-SHA) остаются открытыми.
- `docs/screenshots/0.4/*.png` — исторический drift: не коммитить и не удалять.
