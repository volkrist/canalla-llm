# Canalla LLM 1.2.1 — интеграция четырёх веток (агент №5)

Дата: 11 октября 2026. Роль: главный агент-интегратор. Работа выполнялась в отдельном
worktree (`ruddy-jackal`), ветка `integration/canalla-1.2.1`, от базы `4b5f5c2`.

Итог одной строкой: **четыре ветки объединены без конфликтов и без потерь; мост
Gateway → backend → интерфейс (A30–A32) и явный конфликт идентификатора запроса (A03)
реализованы и покрыты тестами; единый прогон зелёный, кроме одного унаследованного
пробела окружения (иконки Linux) и Rust, заблокированного недоверенной средой.**

НЕ объявлено RELEASE READY. Ни одного production-действия не выполнено.

---

## 1. Исходный HEAD

`4b5f5c28a92fc06b9d63ba4243fbf0008c7ab2cf` — ветка `fix/canalla-1.2.1-p1`.

## 2. Итоговый HEAD ветки интеграции

`0c1b3d170dee32b3b99eceb1d758565375499d6e` — `integration/canalla-1.2.1` (запушена в
`origin`, отслеживается). `main` не тронут (`ae832c5d…` локально и в `origin`).

## 3. SHA четырёх объединённых веток

| Ветка | HEAD | Влита |
|---|---|---|
| `fix/1.2.1-gateway` | `ff38019c02eb92be824a42efefecda314af1a5ba` | да (ancestor of HEAD) |
| `fix/1.2.1-computer` | `57e40a4d261f4f07b80d9e55c571894f04430312` | да (ancestor of HEAD) |
| `fix/1.2.1-tor` | `9913e38bdf6010282ecb043de0a01896a4623039` | да (ancestor of HEAD) |
| `fix/1.2.1-connection-ui` | `b94383ab5e2e796cf54992962cffbd4289d1f159` | да (ancestor of HEAD) |

Дополнительные коммиты интегратора: `ba9244c` (мост A30–A32), `d550424` (A03), `63edbbc`
(формат Workspace), `0c1b3d1` (A32 таймауты). Все четыре ветки — предки `HEAD`
(`git merge-base --is-ancestor`), ни один коммит не потерян.

## 4. Конфликты и их решения

Конфликтов **не было**: все четыре merge прошли стратегией `ort` без conflict-маркеров.
Ветки писали в почти непересекающиеся множества файлов (gateway → `apps/gateway`,
tor → `apps/backend/app/tools/tor`, computer → `apps/backend/app/tools` + Rust + один
`Workspace.tsx`, connection-ui → `apps/desktop/src/lib/ai-connection*`). Итог: 64 файла,
+5270 / −340 относительно базы. 11 PNG в `docs/screenshots/0.4` не затронуты (пусто в diff).

Мелочь после слияния: `Workspace.tsx` (ветка computer) не проходил Prettier — отформатирован
отдельным коммитом. `apps/backend/app/compute/runpod_api.py` был не отформатирован ещё на
базе под Ruff 0.16; нормализация затронула только разделённый строковый литерал
`model_unavailable` (без семантики).

## 5. Схема миграций Gateway/backend

**Gateway** (`apps/gateway/alembic/versions`), линейная цепочка, один head:

```
0001_gateway_core → 0002_stop_attempt → 0003_ready_probe → 0004_session_placement
```

Свежий временный SQLite → `alembic upgrade head` (head `0004_session_placement`),
`alembic check` → «No new upgrade operations detected».

**Backend** (`apps/backend/alembic/versions`):

```
… → 0015_backup_events → 0016 (0016_host_claim)
```

Один head `0016`. `0016` **аддитивна**: 8 колонок на `tool_runs` (семь nullable + счётчик
`attempt_count` с server_default) и индекс `ix_tool_runs_status`; ни одна строка не
переписывается и не удаляется, `waiting_host` под 0015 остаётся claimable под новым
протоколом. Проверено на свежем DB: upgrade head + `alembic check` чисто; upgrade
`0014 → head` с verified `pre_upgrade` backup и неизменными счётчиками данных
(`test_upgrade.py::test_pending_migration_is_preceded_by_a_verified_backup`,
`counts(root) == before`); `0001 → head` сохраняет messages/chats/users/compute_control и
проходит `PRAGMA foreign_key_check` (`test_migrations.py`); fresh install → `0016` без backup.
Backup/restore compatibility сохранена (тесты restore CLI зелёные).

## 6. A03 — результат

**Реализован явный конфликт идентификатора.** До этого база (block 2) изолировала кэш
(`installation_id + request_id` + digest тела), но при совпадении id и **другом** теле
молча выбрасывала старую запись и запускала **новую генерацию под тем же id** — то, что
запрещено формулировкой A03.

Теперь:
- `gateway/inference.py::_lookup` при существующей неистёкшей записи с **другим digest**
  поднимает типизированную ошибку `gateway_request_conflict` (409) — не второй ответ и не
  чужой ответ;
- новый код добавлен в `gateway/errors.py` (CODES + DETAILS);
- backend его распознаёт, а не называет «AI выключен»: `cloud/client.py` (LOCAL_CODES),
  `cloud/provider.py` (409 читает код тела), `providers.py` (LLMError), `status/snapshot.py`
  (AI_ERRORS), `compute/runpod_api.py` (ERROR_MESSAGES).

Тесты (все зелёные):
- `test_the_same_id_with_a_different_request_is_a_conflict` — non-stream;
- `test_a_streaming_request_id_conflicts_on_a_different_body` — streaming;
- `test_two_installations_with_the_same_request_id_are_isolated` — разные installation;
- `test_a_concurrent_duplicate_never_starts_a_second_generation` (существующий) — не более
  одной upstream-генерации при конкурентном retry.

Продуктовый путь backend не задаёт `X-Alex-Request-Id` для потока, поэтому конфликт
недостижим при обычной работе и является защитным контрактом.

## 7. A30/A31/A32 — реальная интеграция

**Мост реализован.** Gateway добавляет блок `connection` в `GET /compute/status`
(`operation_id`, `started_at`, `startup_deadline`, `last_success_at`, `observed_at`,
`current_stage`, `terminal_state`, `typed_error`, `readiness_valid_until`), но цепочка до
интерфейса отсутствовала. Добавлено:

- `apps/backend/app/cloud/state.py::CloudAi.llm_public_status` — блок `connection`
  релеится в `diagnostic` без изменений (аддитивно; старый Gateway без блока → `null`);
- `apps/backend/app/status/snapshot.py::ai_status` — поля уходят в `details`, которые и
  читает `apps/desktop/src/lib/ai-connection.ts`;
- `apps/backend/app/compute/controller.py` — прямой режим отдаёт свой эквивалент
  (`observed_at`, `operation_id`, `last_success_at`), `current_stage` намеренно `None`
  (клиент выводит стадию из `compute_state`).

Свойства, которые это даёт (проверены тестами `test_cloud_demand.py`):
поля аддитивны; старый Gateway не падает; frontend читает настоящий `operation_id`;
deadline привязан к конкретной операции (`startup_deadline`); устаревший снимок не продлевает
ожидание (`observed_at` + `SNAPSHOT_FRESH_SECONDS=90`); Ready только со свежим подтверждением
(`readiness_valid_until`); недоступность Gateway не маскируется зелёным; истечение ожидания не
создаёт Pod (статус всегда read-only); неизвестный `create` не провоцирует слепой retry
(`terminal_state`/typed error, клиент показывает Retry, но не запускает compute).

## 8. A05/A07/A08 — совместимость

- **A05**: ветка computer добавила durable claim/lease ledger (`0016`) и нативный journal.
  `test_host_claim.py` (566 строк) покрывает: потерянный POST результата → побочный эффект
  **не** повторяется; потерянный ACK → result доставляется снова без второго исполнения;
  краш host после эффекта → `unknown`, не replay; повторный poll не выдаёт второй claim;
  истечение незапущенного claim освобождается, запущенный — нет; backend restart сохраняет
  in-flight задания; изоляция по device и пользователю. Реальные побочные эффекты — только на
  test fixtures.
- **A07**: `Workspace.tsx` читает токен на каждом проходе цикла, `online` — только успешный
  heartbeat, device record привязан к `user.id`. `test_local_device.py` зелёный.
- **A08**: Tor-fetch требует подтверждённый SOCKS до передачи URL; proof привязан к
  endpoint+процессу; неподтверждённый/просроченный/сменивший порт не получает URL
  (`test_tor_proof.py`). Always-ready сохранён (тесты `test_tor_service.py` зелёные).

## 9. A09/A12 — FakeRunPod (multi-region, без реального GPU)

`apps/gateway/tests/test_multiregion.py` (зелёный, `FakeRunPod`):
- `test_secondary_placement_failover_survives_a_restart` — primary недоступен → secondary
  доступен → **один** Pod → Ready → повторный ensure → restart Gateway → **тот же** Pod → Stop;
- `test_a_lost_secondary_create_is_recovered_not_duplicated` — неоднозначный create на
  secondary → задержка видимости провайдера → durable resolver → второй Pod не создаётся
  (create count == 1);
- `test_two_managed_pods_across_placements_are_reported_and_left_alone` — глобальный инвариант
  «не более одного managed Pod» между регионами; чужие Podы не удаляются.

A12 — потолок цены хранится отдельно от цены кандидата (gateway branch); проверен
allocation/multiregion-тестами. **Оговорка:** доступность L40S 48 GB в CA-MTL-1/EU-SE-1 не
подтверждена и не считается гарантированной по одному октябрьскому снимку.

## 10. Общие тесты и точные числа (единый прогон на `HEAD` 0c1b3d1)

| Гейт | Результат |
|---|---|
| Gateway pytest | **231 passed** |
| Gateway Ruff check / format | PASS / 37 файлов formatted |
| Gateway Alembic (fresh temp SQLite → head) | head `0004_session_placement`, `check` чисто |
| Backend pytest | **897 passed, 6 skipped** |
| Backend Ruff check / format | PASS / 199 файлов formatted |
| Backend Alembic (fresh temp SQLite → head) | head `0016`, `check` чисто |
| Frontend Vitest | **293 passed, 1 failed** — `linux-packaging.test.ts`, отсутствуют untracked PNG-иконки (унаследовано, не регрессия) |
| Frontend TypeScript (`tsc -b`) | clean (exit 0) |
| Frontend Prettier | 1 warn — `e2e/live-ai-acceptance.mjs` (падает и на базе, унаследовано) |
| Frontend Vite build | PASS (2269 модулей) |
| Playwright | **NOT RUN** (нужен собранный desktop + браузеры; недоверенная среда) |
| Rust `cargo check` / `cargo test` | **NATIVE BLOCKED** (см. §11) |
| Packaged Gateway URL guard (`scripts/verify-packaged-gateway-url.py --self-test`) | **PASS** (ловит отсутствующий URL, принимает вшитый) |

Числа четырёх агентов не складывались: это один прогон на объединённом коммите.
6 skip в backend — opt-in live/root-only сценарии. Ни один тест не удалён ради зелени.

## 11. Rust — статус: NATIVE BLOCKED

Компьютер-агент добавил Rust-изменения (`host.rs`, `credential.rs`, `process.rs`, `fs_guard.rs`,
`alex_host_loop.rs`), но `cargo check`/`cargo test` на этой машине **не запускались**. Причина —
недоверенная среда (§12): Defender ранее зафиксировал `Trojan:Win32/LummaStealer.DO!MTB` с
`DidThreatExecute=True`, ложное срабатывание не доказано, а сам `cargo test` собирает и
**исполняет** ровно те бинарники, которые флагаются. Официальный статус — **BLOCKED-BY-DEFENDER**,
а не условный PASS. Полный native/release gate требует доверенной Windows VM или CI.
Toolchain присутствует (`C:\Users\Volkr\.cargo\bin`), но не используется.

## 12. Статус безопасности Windows

Машина остаётся **недоверенной**. Свидетельства Phase 0 (11 окт): детекция только на наших
собственных артефактах, dropper-артефактов нет, но exclusion-лист не проверен, полный скан не
делался. Вывод: разрешена только безопасная работа с исходниками. В этом проходе **не**
выполнялось: нативная сборка/исполнение Rust, подписание, production credentials, восстановление
подозрительных EXE, изменение Defender. Не создавалось Pods/Volumes, не менялись 12Testers и
production secrets.

## 13. ID, закрытые полностью (код + регрессионный тест в едином прогоне)

A01, A02, A03, A04, A05, A06, A07, A08, A09, A12, A13, A15, A16, A17, A18, A19, A30, A31, A32.

## 14. ID, закрытые частично

- **A25** — добавлен packaged-guard на production Gateway URL в релизном пути
  (`build-desktop.ps1`, `production-signing.ps1` вшивают `https://gateway.12testers.store`
  и запускают guard); обязательного воспроизводимого artifact-gate в отслеживаемом CI нет.
- **A06/A30/A31** — сам фикс — это Gateway + frontend; прямой режим получает клиентские
  бюджеты стадии, но не имеет локального readiness-TTL (Ready в direct-режиме без
  `readiness_valid_until`).

## 15. A10/A11 — план (НЕ исправлены)

Агент Gateway их не реализовал; объявлять исправленными нельзя. Публичный multi-user rollout
остаётся заблокированным. Точные условия из аудита — «клиент управляет расходом общего
баланса» и «любая installation останавливает общий Pod». План (без произвольных hard caps):

1. **Установка/пользователь** — `installation_id` остаётся единицей аутентификации; `user_id`/
   `email` — локальная идентичность, не облачная.
2. **Права оператора** — ввести на Gateway entitlement (`installation.role` или таблица
   `entitlements`), отличный от аутентификации.
3. **user preference** — существующие `max_hourly_price`/`session_budget` (это личная политика).
4. **server entitlement** — административная квота installation (например, число сессий за
   период и потолок на installation).
5. **Общий spending ceiling** — глобальный предел расхода deployment.
6. **Учёт и атомарное резервирование** — ledger списаний и резервирование бюджета в одной
   транзакции с существующим lease/committed intent.
7. **Собственный cancel** — отмена/relinquish **своего** запроса, отдельно от остановки Pod.
8. **Глобальный emergency Stop** — административный endpoint/entitlement, отделённый от
   пользовательской остановки.
9. **audit/revoke** — аудит решений и отзыв installation.

Эффективная политика должна вычисляться как
`min(user preference, installation entitlement, global available quota)` и **отдаваться
read-only** в `GET /compute/status → policy`. Это отдельный обязательный следующий этап.

## 16. Остаточные проблемы

- **A10/A11** — не исправлены (см. §15); блокируют публичный multi-user.
- **A14** — автостарт из чата по-прежнему не передаёт `auto_stop_minutes` (`policy_caps` в
  `cloud/demand.py` его не включает; ручной ensure — включает). Значение интерфейса может не
  совпасть с серверной политикой.
- **A20–A24, A26–A29, V01–V04** — не входили в четыре ветки; не адресованы (backup в event
  loop, чтение файла в RAM, атомарность refresh, общий inference timeout, ёмкость rate limiter,
  offline-install, updater TOCTOU, потеря части ответа, multi-worker, power-loss restore,
  redaction traceback, произвольный backend_url).
- **UX очереди**: стадия `queueing` определена в клиентском словаре (бюджет 120 s), но Gateway
  не публикует очередь инференса в `compute/status`, поэтому «ожидаем очередь» на чипе пока не
  отображается; согласованы только бюджеты (см. A32), не событие.
- **Окружение**: `linux-packaging.test.ts` красный из-за отсутствующих untracked PNG-иконок;
  `e2e/live-ai-acceptance.mjs` не отформатирован (оба — унаследовано от базы).
- **Windows installer / Authenticode / off-machine backup** updater-ключа — вне этого прохода.

## 17. Production changes: NONE

Не деплоился Gateway, не менялся nginx/12Testers, не создавался/не удалялся Volume, ничего не
публиковалось, не собирался и не публиковался installer, manifest не обновлялся, `main` не
мержится, тег не создавался. Изменений на production нет.

## 18. Paid GPU spend: $0

Не создавалось Pods и Volumes, не запускался GPU, не выполнялся live acceptance.

## 19. Точная инструкция следующему этапу

1. **Native gate в доверенной среде** (VM/CI): `cargo check --all-targets`, `cargo test`
   (host protocol, device credential, process output tests) на `integration/canalla-1.2.1`.
   До этого Rust остаётся `BLOCKED-BY-DEFENDER`.
2. **Playwright** на собранном desktop; Vitest уже зелёный (293/1 унаследованный).
3. **A10/A11** — реализовать по плану §15 отдельным этапом; до этого **не** приглашать
   недоверенных пользователей и не выходить на публичный multi-user.
4. **A14** — включить `auto_stop_minutes` в `policy_caps` и показать принятую сервером политику.
5. **A25** — сделать релизный entrypoint обязательным gate в отслеживаемом CI (artifact
   обязателен, production-key match обязателен, установленная приёмка обязательна).
6. **Installed acceptance 1.2.1** и `apps/desktop/e2e/always-ready.mjs` — только после доверенной
   сборки и санкции на апгрейд рабочей установки; обязательно `ALEX_DEVICE_CREDENTIAL_TARGET`.
7. **Gateway redeploy** (immutable release dir + atomic `current` + `alembic upgrade head` до
   `0004_session_placement`) — только с санкцией; в этом проходе не делался.
8. Не удалять `uwgeaie5b0`. Не считать L40S 48 GB гарантией в новых регионах.

Критерий выполнен: один объединённый код без потерянных изменений, без конфликтов контрактов,
с проверенными регрессионными сценариями. CANALLA 1.2.1 **НЕ** объявляется RELEASE READY.
