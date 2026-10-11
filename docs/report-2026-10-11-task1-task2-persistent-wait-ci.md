# Canalla LLM 1.2.1 — Task 1 (постоянный поиск GPU) и Task 2 (доверенный Windows CI)

Дата: 11 октября 2026 · Ветка: `integration/canalla-1.2.1` · Продукт: Canalla LLM 1.2.0

Начальный HEAD: `d369c7a9a7acdf0d7772d0e00673409e9ffc61f1`
Итоговый HEAD: этот отчёт (см. `git log -1`).

Ни одного production-действия не сделано. GPU spend **$0**.

---

## 1. Task 1 — постоянное ожидание свободной GPU

Пользователь может оставить поиск включённым, когда подходящей свободной карты нет. Отдельная
попытка по-прежнему ограничена 60 с (`COMPUTE_SEARCH_TIMEOUT_SECONDS`); **ожидание между
попытками** не ограничено произвольно — его ведёт backoff Gateway (30 → 180 с), пока карта не
появится или пользователь не отменит. Намерение durable: переживает обрыв сети, перезапуск
Gateway, перезапуск приложения и временную ошибку провайдера.

### Код

| Слой | Файл | Что |
|---|---|---|
| Gateway | `gateway/compute.py` | состояние `waiting_capacity`, `_open_wait/_clear_wait/wait_due/wait_payload`, `cancel_wait`, `effective_state`, `connection_payload`, `status_payload.wait`, `_background_attempt` (сначала `_reconcile_locked` — правило одного Pod) |
| Gateway | `gateway/models.py`, `alembic/versions/0006_capacity_wait.py` | колонки `wait_*` на singleton `gateway_compute` (аддитивно) |
| Gateway | `gateway/config.py` | `compute_wait_backoff_seconds` (30), `compute_wait_backoff_max_seconds` (180) |
| Gateway | `gateway/routes.py` | `POST /compute/ensure` поля `wait_for_capacity`/`background_start`; `POST /compute/wait/cancel` |
| Backend | `app/cloud/demand.py` | клиент ведёт foreground-ожидание (`ensure`), background оставлен Gateway; `WAIT_POLL_SECONDS`; `waiting_capacity` — не model-bearing |
| Backend | `app/cloud/state.py`, `app/status/snapshot.py` | релей `wait` в chip |
| Backend | `app/compute/schemas.py` | preferences `wait_for_capacity`, `background_start` |
| Desktop | `src/lib/ai-connection.ts` | стадии `waiting`/`waiting_capacity` в `STAGE_TEXT`, `UNBOUNDED_STAGES` (нет клиентского дедлайна) |
| Desktop | `src/components/ComputePanel.tsx` | переключатель «Автоматически ожидать свободную GPU» и отдельное «Разрешить платный запуск, пока приложение закрыто» |

Docs: `docs/gateway-status-contract.md` §8, `docs/compute-preferences.md` §13.

### Безопасность и деньги

- `background_start` — **отдельное** явное разрешение на платный запуск при закрытом приложении;
  ожидание само его не даёт. Поиск в фоне бесплатен.
- Отмена (`/compute/wait/cancel`) не трогает чужую генерацию и не останавливает общий Pod.
- Отозванная установка не может быть запущена «припаркованным» ожиданием (`_background_attempt`).
- Пустой баланс / исчерпанный период-бюджет — типизированный отказ, не Pod.
- `_background_attempt` сначала реконсилит — второй managed Pod невозможен.

### Тесты

- `apps/gateway/tests/test_capacity_wait.py` — **13** (3 виртуальных часа, появление карты → ровно
  один Pod, отмена, цена выше лимита, foreground/background, реконнект, рестарт Gateway, ошибка
  провайдера, отзыв, мультитенантность, отсутствие hot loop, бюджет).
- `apps/backend/tests/test_cloud_demand.py` — **+6** (foreground-драйв, background не драйвит,
  policy_caps несёт/не выдумывает флаги, chip, аддитивность).

---

## 2. Task 2 — доверенный Windows CI

`gh` авторизован (`volkrist`). Ветка триггерит `.github/workflows/ci.yml` на push. Добавлены две
доверенные job'ы (не требуют ключа подписи):

- `native-gate` — `cargo check --all-targets` + `cargo test` (Rust был BLOCKED на недоверенном ПК).
- `frontend-e2e` — Playwright против реального backend-sidecar.

`release-candidate.yml` (проверка артефакта/подписи) не менялся.

### Результаты (HEAD `541a741`, run 38143626909)

| Job | Результат | Ссылка |
|---|---|---|
| developer-gate | **PASS — 9 шагов, 0 FAIL, 0 BLOCKED** | https://github.com/volkrist/canalla-llm/actions/runs/38143626909/job/114482348894 |
| native-gate | **PASS — cargo check + cargo test 111 passed / 0 failed** | https://github.com/volkrist/canalla-llm/actions/runs/38143626909/job/114482348789 |
| frontend-e2e | **17 passed / 3 failed** (см. блокеры) | https://github.com/volkrist/canalla-llm/actions/runs/38143626909/job/114482349041 |

`cargo test`: 29 + 66 + 16 = **111 passed, 0 failed**.

### Найденные и исправленные дефекты (то, ради чего нужен доверенный CI)

1. **Rust не компилировался** (`host.rs:392 crate::auth::auth_restore` — `auth` есть только в desktop-
   бинаре, не в `alex-host-loop`) → refresh сессии вынесен в общий `src/session.rs` (зависит только
   от `credential`, общий single-flight на Tauri-restore и host-refresh).
2. **`reset_journal` терял блокировку** (`let _ = …lock()`, `deny(let_underscore_lock)`) → привязана
   к области видимости.
3. **`release-gate.py` падал на недоверенном CI** (`text=True` декодировал UTF-8 как cp1252 →
   `stdout=None`) → явный `encoding="utf-8", errors="replace"`.
4. **Windows-runner кодирует пайпы в ANSI** → Python-child с русским текстом падал
   `UnicodeEncodeError` (это был один из «тестовых» провалов) → job запускает Python в UTF-8
   (`PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`).
5. **Две Job-Object-пробы** не проходили в песочнице GitHub (`CREATE_BREAKAWAY_FROM_JOB` запрещён
   раннером) → честная проверка возможности: skip с точной причиной вместо ложного FAIL. На
   доверенном Windows-хосте тесты по-прежнему выполняются и проходят.
6. **stale e2e-ассерт** `management.spec.ts` (ожидал `gpu_id`, который панель намеренно сбрасывает в
   automatic с `0d29282`) → ассерт исправлен на доказательство сброса.

### Блокеры Task 2

- **Tor pin 404.** `scripts/tor-runtime.json` пинит `https://dist.torproject.org/torbrowser/15.0.23/…`,
  а на dist.torproject.org остались только `15.0.20`, `15.0.24`, `16.0a13`. `fetch-tor-runtime.py`
  не может застейджить рантайм → сборка установщика невозможна. **Не обновлял пин** (supply-chain,
  требует оператора). Native-gate не зависит от пина (создаёт путь ресурса, как это делает
  `build.rs` для sidecar).
- **Playwright 3/20** требуют локальной embedding-модели (`Embedding service unavailable.
  Подготовьте локальную модель.`): `files.spec.ts` ×2 и `web-tools.spec.ts` (RAG). Чистый раннер
  её не имеет. Нужна доверенная Windows-среда с provisioned-моделью
  (`%LOCALAPPDATA%\Alex LLM\models\embeddings\multilingual-e5-small\active.json`) или CI-шаг,
  который её ставит.
- **Production installer:** не собирался и не подписывался (LummaStealer на первом ПК; нет Tor-пина).
  `TEST BUILD PASS` достигнут для developer/native gate; `PRODUCTION SIGNING` — BLOCKED.

---

## 3. Итоговые числа

| Набор | Локально (untrusted) | Доверенный CI |
|---|---|---|
| Gateway pytest | 260 passed | PASS |
| Backend pytest | 912 passed, 6 skipped | PASS (2 Job-пробы skip на раннере) |
| Frontend Vitest / tsc / prettier / vite | 298 passed / clean / PASS / PASS | PASS |
| Rust cargo check / cargo test | **BLOCKED** (нет cargo) | **PASS / 111 passed** |
| Playwright | — | 17 passed, 3 environment-blocked |
| Gateway/Backend Ruff · Alembic | PASS · gateway 0006, backend 0016, `alembic check` clean | PASS |
| release-gate `--self-test` | PASS | PASS |

## 4. Что осталось до 1.2.1

1. Владелец: обновить Tor-пин на существующую версию и подтвердить SHA256 (supply-chain).
2. Доверенная среда с embedding-моделью — прогнать полный Playwright (20/20).
3. После (1): собрать unsigned installer на доверенном раннере; затем подписать из доверенной
   среды отдельным согласованным действием. Production signing на первом ПК запрещён.
4. Закрыть/подтвердить открытые A20–A29, V01–V04 (решения зафиксированы в
   `docs/report-2026-10-11-final-acceptance.md`).

## 5. Изменения и запреты

- Production changes: **NONE**. Paid GPU spend: **$0**.
- Не менялись: `main`, теги, published updater manifest, 12Testers, production Gateway,
  существующие installation credentials, Volume `uwgeaie5b0`.
- Merge в main / tag / deploy / publish — **не делалось**.
