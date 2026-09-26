# CANALLA LLM 1.2.0 — ОТЧЁТ ПО ДВУМ ПОСЛЕДНИМ ЗАДАЧАМ (26.09.2026)

**Задачи:** `FREE PRE-PUBLISH CLEANUP` → `FINAL LIVE ACCEPTANCE + PRODUCTION RELEASE`
**Ветка:** `release/canalla-1.1.0` · **HEAD:** `64ad39f` · **`main`:** `9bd7548` (= `origin/main`, не смёржен)
**Теги:** только `v1.0.0` · **Манифест:** НЕ опубликован · **Сборки:** не выполнялись (принятый артефакт не пересобирался)
**Деньги за оба прохода:** **$0.00** (Pod не создавался ни разу)

---

# ЗАДАЧА A — FREE PRE-PUBLISH CLEANUP

## A.1 Остановка временного kill-switch (§1) — PASS

| Проверка | Факт |
|---|---|
| Reaper остановлен | PIDs `12996`, `11168` |
| Python-процессы watcher/reaper после остановки | **0** |
| Lock освобождён | `take_lock()` → **True** (импорт модуля, реальная проверка байтового lock'а) |
| Watcher | STOPPED |
| Running Pods | **0** |
| Volumes | **1** — `uwgeaie5b0` (US-TX-3, 50 GB, STANDARD), не тронут |
| Код | reaper **остался в репозитории** (`scripts/canalla-pod-reaper.py`); в `.gitignore` добавлены его runtime-файлы (`reaper.log`, `reaper-state.json`, `reaper.lock`, stdout/stderr) |

## A.2 DOWNLOAD-BACK через публичный маршрут, с самого VPS (§2) — PASS

Скачивал **сам VPS** через публичный HTTPS-хост (не чтением файла с диска), затем временная копия удалена:

```
URL    https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe
GET    http=200  bytes=92493319  time=10.52s
SHA256 ae863e92041f1cd3ecf20748c4298b7b0566f599eb39321b285a40620b971929   ← равно принятому
GET .sig http=200 bytes=424   sha256(sig)=4219547a6059ff5433f9edd359936e9ee6f9e50d3fcb0e5834a6ac44a0f8faeb
       hosted .sig байт-в-байт равен принятому; подпись проверена ключом 9B328EFF111D1FB2 → PASS
CLEANUP temp copies deleted: none left
```

## A.3 READ-ONLY проверки (§3)

| Проверка | Результат |
|---|---|
| Gateway `/health` | **200** · `version 1.2.0` · `ready:true` · `database:ok` |
| Updater `/updates/latest` | **204** (и с `?target=…&current_version=…` — тоже 204) |
| Hosted installer SHA256 | `ae863e92…` = принятому |
| Hosted signature | **PASS** |
| Установленное приложение | **1.2.0** |
| Running RunPod Pods | **0** |

> Отдельно зафиксировано: первая проверка апдейтера напечатала ложный FAIL — **баг моей проверки**
> (`urllib` не бросает исключение на `204`). Перепроверено корректно: 204 везде.

## A.4 Подготовка публикации — БЕЗ выполнения (§4)

| Элемент | Значение |
|---|---|
| Release branch HEAD (на тот момент) | `383b8dbd314a7af0d2f948581d48226b42e201e8` |
| `main` / `origin/main` | `9bd75486c34b64ab4435afb4efc9fa9b17104cfd` |
| Тип слияния | **main — предок release → fast-forward возможен** |
| Коммит, который стал бы `v1.2.0` | ровно release HEAD (`383b8db…`) |
| Коммитов к слиянию | **111** |
| Working tree | чисто по отслеживаемым, кроме **предсуществующего** drift'а `docs/screenshots/0.4/*.png` (11 файлов, не коммитим) + 1 untracked scratch-хелпер |
| merge / push / tag / manifest | **не выполнялись** |

## A.5 Зафиксировано как единственный оставшийся гейт (§5)

В `docs/release-1.2.md` (коммит `383b8db`) записано: **LIVE AI + TOR ACCEPTANCE: PENDING** — отложено до
разрешения разовых **≈$0.30–0.40**; кандидат: **US-TX-3 · L40S 48 GB · $1.09/ч**. Pod не запускался.

### Итог задачи A

```
FREE PRE-PUBLISH GATES: PASS
DOWNLOAD-BACK: PASS · SIGNATURE: PASS · GATEWAY: PASS
UPDATER BEFORE RELEASE: 204 · INSTALLED VERSION: 1.2.0 · RUNNING PODS: 0
WATCHER: STOPPED · REAPER: STOPPED (lock released)
PUBLICATION: NOT STARTED
```

---

# ЗАДАЧА B — FINAL LIVE ACCEPTANCE + PRODUCTION RELEASE

## B.1 Гейты очистки перепроверены (§1) — PASS

Gateway 1.2.0 `ready`, `/updates/latest` 204, установленная версия 1.2.0, Pods 0, watcher/reaper — 0 процессов,
принятый артефакт на месте. Все восемь условий §1 выполнены → можно тратить авторизованные ~$0.40.

## B.2 Артефакт (§2) — принят без пересборки

`Canalla LLM_1.2.0_x64-setup.exe` · **92 493 319 B** · SHA256 **`ae863e92…b971929`** · подпись
**`9B328EFF111D1FB2`** = PASS · **Authenticode NOT SIGNED** · sidecar в бандле
`fa46d07945af48b95fece98d2e934e7d6e42836c3df84f17ebdadef3d841a997`. Ни installer, ни sidecar не пересобирались.

## B.3 Денежный и ёмкостный гейты (§3–§4)

| Гейт | Факт |
|---|---|
| Аккаунт способен запустить GPU-Pod | да (этим же аккаунтом ранее выполнялись измерения) |
| Ёмкость US-TX-3 | **NVIDIA L40S 48 GB · $1.09/ч · stock LOW · Secure · bookable** (≤$2/ч ✓) — каталог фликерный: один скан показал 0 кандидатов, следующий — снова 1 |
| Pod'ов создано | **0** (приёмка упала до старта compute) |
| Потрачено | **$0.00** |

## B.4 Живая приёмка (§5–§8) — **FAIL**

`node e2e/live-ai-acceptance.mjs --phase chat` против **установленной 1.2.0** (реальная политика, shared-режим,
без подмены пути):

```
PASS  the operator's session is restored (no first-run, no login)   [first-run buttons: 0]
ai before   {"state":"disconnected","code":"provider_unavailable",
             "text":"AI: Disconnected. Подходящих GPU сейчас нет в наличии."}
ai states   disconnected/provider_unavailable -> disconnected/snapshot_stale
FAIL  the chat started compute by itself (no Settings button, no resend)
FAIL  the model became Ready and the chip turned Connected      [null after 1 200 690 ms]
FAIL  the chat produced an answer                               [пустой ответ ассистента]
CHAT FAILED
```

## B.5 Причина — найдена в логе backend, точная

Единственная попытка оркестрации со стороны продукта **отвергнута валидацией**, а не ёмкостью:

```
2026-09-26 16:03:08,612 INFO httpx HTTP Request:
  POST https://gateway.12testers.store/compute/ensure   "HTTP/1.1 422 Unprocessable Entity"
```

Развёрнутый Gateway — это сборка **`/opt/alex-gateway/releases/6e2eca83e0c2` от 24.09 15:00** (проверено по
`readlink -f /opt/alex-gateway/current`, сервис `active`), то есть **старше** compute-коммитов этого цикла
(`origin`, стратегии, storage kinds, `/v1/models` gating — все от 25.09). Клиент 1.2.0 отправляет payload,
которого развёрнутый Gateway ещё не знает → 422 → compute не стартует → модель не становится Ready → ответа нет.
Это ровно тот риск, о котором предупреждает §12: «Deploy the CURRENT accepted final source.
**Do NOT deploy stale Gateway code**».

## B.6 Что сделано по правилам задания после провала (§11/§12/§21)

| Требование | Факт |
|---|---|
| Pod завершить | Завершать нечего — Pod не создавался |
| Running Pods = 0 | **0** (проверено провайдерски) |
| Приложение/процессы | harness сам закрыл приложение (`POST /runtime/shutdown 200`); alex/canalla процессов — **0** |
| Не публиковать | манифест **не публиковался**; `/updates/latest` = **204** |
| Не мёржить/не тегать | `main` = `9bd7548` (= origin/main), теги — только `v1.0.0` |
| Запись причины | `docs/release-1.2.md` + коммит **`64ad39f`** (полный текст FAIL'ов и строка 422) |

## B.7 Итог задачи B

```
RELEASE BLOCKED — LIVE ACCEPTANCE FAILED

причина : развёрнутый Gateway старше текущего исходника → 422 на POST /compute/ensure
деньги  : $0.00 (Pod не создавался)
cleanup : running Pods 0 · temporary release processes 0 · App закрыт harness'ом
publish : НЕ выполнена (ни merge, ни tag, ни manifest)
```

---

# ОСТАЛОСЬ РОВНО ЧЕТЫРЕ ДЕЙСТВИЯ

1. **Развернуть текущий исходник Gateway** на VPS (новая release-директория + атомарная смена `current` +
   `alembic upgrade head` + рестарт `alex-gateway`) — авторизовано §12; это устраняет 422.
2. Проверить Gateway: `/health` → 200, `version 1.2.0`, `ready true`; `/updates/latest` → **204**.
3. Повторить живую приёмку: `--phase chat` → `--phase tor` → `--phase stop`
   (одна GPU ≤$2/ч, ≤20 мин, **≈$0.30–0.40**, та же авторизация). Требуется: `origin=chat`, автоматический
   ensure без кнопок, `/v1/models` с алиасом `orcarouter-qwen38-27b-q5km`, непустая генерация; затем Tor
   (`tor_required`, managed Tor, SOCKS5h, свежее доказательство цепочки, DNS leak 0, clearnet 0).
4. Только после PASS: commit → merge `main` → push → аннотированный **`v1.2.0`** → push → **манифест последним
   действием** → read-only пост-проверка.

---

# АРТЕФАКТЫ И ФАКТЫ

| Что | Где / значение |
|---|---|
| Установщик | `apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.0_x64-setup.exe` · 92 493 319 B · `ae863e92…` |
| Публичный URL | `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.0_x64-setup.exe` (+ `.sig`) |
| Хостинг на сервере | `/srv/canalla-downloads/` (nginx `location /downloads/`), предыдущий кандидат заменён |
| Sidecar | `fa46d079…` · 23 571 704 B (и в бандле, и в установке) |
| Запись состояния релиза | `docs/release-1.2.md` (коммиты `383b8db`, `64ad39f`) |
| Доказательство провала | `%LOCALAPPDATA%\Alex LLM\logs\backend.log` — строка `POST … /compute/ensure "HTTP/1.1 422"` |
| Развёрнутый Gateway | `/opt/alex-gateway/releases/6e2eca83e0c2` (24.09 15:00) · сервис `active` |
| Ключ подписи | вне repo/логов: `C:\Users\Volkr\.canalla-updater\production\canalla-updater-production.key` (+ Credential Manager) |

# ДЕНЬГИ (за оба прохода)

| Позиция | USD |
|---|---|
| Инцидент 25–26.09 (утечка из-за упавшего парсера) | **6.3351** |
| После фикса, ранее (2 измерения + снятый orphan-Pod) | **≈0.38** |
| **Задача A + Задача B** | **0.00** (Pod'ов создано 0) |
| Хранение: один том `uwgeaie5b0` 50 GB STANDARD | ≈$3.50/мес |
| Вторичное хранилище | $0 (отложено) |
| Постоянная плата за GPU | отсутствует (оплата — только пока Pod жив) |

# ОГРАНИЧЕНИЯ (как есть)

* production 1.2.0 — **один** RunPod placement (US-TX-3 / `uwgeaie5b0`); при отсутствии совместимой ёмкости
  запуск AI падает **bounded typed capacity error** (≤60 с), а не «Connecting» навсегда;
* вторичный региональный failover — **deferred post-1.2.0** (баланс RunPod ниже минимума для тома);
* **Authenticode NOT SIGNED** (сертификата нет);
* Ubuntu — **Preview**, без production-манифеста;
* **v1.0.0 → 1.2.0**: авто-обновление криптографически невозможно (идентичность подписи создана 24.09, релиз
  1.0.0 — 22.09) → нужен ручной bootstrap; клиенты с продакшн-ключом обновляются штатно;
* Global Volume: публичный API не умеет подключать его (проверено 25.09) — вне 1.2.0.

```
ВЕРДИКТ ОБОИХ ПРОХОДОВ:

Задача A (FREE PRE-PUBLISH CLEANUP)              : PASS — все бесплатные гейты закрыты
Задача B (FINAL LIVE ACCEPTANCE + RELEASE)       : RELEASE BLOCKED — LIVE ACCEPTANCE FAILED
                                                   (422 от устаревшего развёрнутого Gateway)

PUBLICATION: NOT STARTED   ·   PODS: 0   ·   SPEND: $0.00
```
