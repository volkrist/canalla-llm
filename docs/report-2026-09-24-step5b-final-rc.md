# CANALLA LLM 1.2.0 — STEP 5B FINAL RC REPORT

Дата: 24 сентября 2026 · Ветка: `release/canalla-1.1.0` · HEAD `87c80d2` · Версия продукта: **1.2.0**

```
RELEASE BLOCKED — GATEWAY DEPLOY, HOSTING AND UPDATER E2E NOT DONE;
                  PLUS TWO OPEN FINDINGS ON AI COMPUTE STATUS
```

Этот шаг не доведён до конца: часть §7–§13 (деплой Gateway, хостинг, download-back, updater RC E2E)
**не выполнена**, и при приёмке вскрылись две находки про статус AI-вычислений, которые требуют решения
до публикации. Ниже — то, что реально проверено, и то, что осталось.

---

## CURRENT CANDIDATE

```
Installer SHA256:  123c8cedd16757503b4c9118b5df2054d01c93b1507274c6ed032a00e3865eba   (пересборки не было)
Updater sig SHA256: 29a41b0e5a62f0dd819e31d502fe40491535c42ff19192ca6982b53907685b30
Backend SHA256:    e2e87ad2805e2c292bf5f5c1a2a274faf11d04ce96ad05a833c175e4c3f4eebd
/health:           1.2.0
stale-sidecar guard: PASS  (exit 0 на реальном дереве)
production key:    9B328EFF111D1FB2 (в клиенте)
installed:         Canalla LLM 1.2.0 (реестр)
```

Пересборки в этом шаге не было — расхождений артефакта нет.

---

## GUI (установленный 1.2.0, живое окно, CDP + Playwright, реальный корень данных)

| Проверка | Результат | Доказательство |
|---|---|---|
| **AI Disconnected** | **NOT APPLICABLE → см. находку A** | установка **зачислена в Canalla Cloud** (`Canalla Cloud: Подключено`, `RunPod key: да`), поэтому продукт показывает не «не настроено», а `connecting`/`starting` — «Ищем GPU» |
| AI never green | **PASS** | за 8 запусков ни разу `data-state="connected"` и ни разу текст `Connected` |
| **Computer Ready** | **PASS** | `.status-chip[aria-label="Computer: Готово"]`, `state-ready`, «Сопряжён да · Отклик в норме» |
| **Tor Ready** | **PASS** | `.status-chip[aria-label="Tor: Готово"]`; popover: «цепь проверена», «Метод socks5h», «Порт отвечает да», «Откат none» |
| **Memory** | **PASS** | `.status-chip[aria-label="Memory: Готово"]`, «Память включена · Записей 2 · Лимит 12» |
| **Updater Settings** | **PASS** | Settings → «Обновления»: кнопка «Проверить обновления» видима, «Канал: stable» |
| Реакция UI на потерю здоровья | **PASS** | после убийства sidecar: значок → `disconnected`/`snapshot_stale`, `[data-testid="status-error"]` виден, все пять чипов `data-stale="true"`; затем backend поднялся **сам**, чипы вернулись в «Готово», сессия выжила |
| Сессия из реального корня данных | **PASS** | workspace восстановлен без логина, аккаунт `volkrist2222…` |
| **Autostart ON/OFF/ON** | **PASS** | ON → значение `Canalla LLM` = установленный exe; OFF → значение **удалено** продуктом; ON → **восстановлено**; чекбокс совпадает с реестром в каждом полюсе, включая свежий старт при отсутствующем значении (продукт регистрирует его сам) |

Пиксельная перепроверка скриншотов подтвердила, что зелёный `#9fc499` и красный `#f2b4ac` реально
рисуются (steady state: зелёный ×96, янтарный ×73, красный ×0; деградация: красный ×48).

---

## НАХОДКА A — AI-чип показывает «Ищем GPU» бесконечно, при том что никто не ищет

| Факт | Значение |
|---|---|
| Состояние чипа | `connecting` / `starting`, `aria-label="AI: Connecting…. Ищем GPU"`, янтарный |
| Popover | «Compute **searching**» |
| Управляемая сессия | **отсутствует** («Остановить AI» `disabled`) |
| Длительность | 481 с во время запроса, затем всё ещё `searching` через ~11 и ~23 мин |
| Переходы Pod | наблюдалось **только `searching`** — никаких `creating`/`starting_pod`/`loading_model` |
| Pod-ы / траты | **0 Pods, $0** |

Gateway продолжает отвечать `compute/status → 200 ... searching` при отсутствии активной сессии.
Это ровно тот класс «залипшего ярлыка», который уже документирован в `AGENTS.md` для `generating`, но
здесь он виден на глобальном значке AI, а требование релиза прямо говорит: **«Не показывать Connecting
бесконечно. Каждая стадия bounded timeout»** (STEP 4 §2/§5, повторено в §2 этого шага).

Чип не врёт о *наличии* модели (зелёного нет), но заявляет **переход, которого не происходит**.

## НАХОДКА B — чат-запрос не запускает вычисления

`POST /compute/ensure` существует (`apps/backend/app/cloud/client.py:191`,
`apps/backend/app/cloud/routes.py:50`) и достижим только как **отдельное действие пользователя**
(кнопка «Запустить AI» в Settings → Canalla Cloud). Никакой путь чата его не вызывает — grep по
`app/cloud/provider.py` не находит ни одного триггера «нужна модель».

Наблюдение в логе backend за время живого теста:

```
POST /chats/…/stream 200
GET https://gateway.12testers.store/v1/models      → HTTP/1.1 409 Conflict   (повторяется)
GET https://gateway.12testers.store/compute/status → HTTP/1.1 200 OK         (каждые ~8 с)
```

**Нет** ни `POST /compute/ensure`, ни `POST /v1/chat/completions`. То есть при отправке сообщения
без запущенного Pod продукт не просит compute и получает `409` — и это, в отличие от находки A,
может быть как задумано (пользователь сначала жмёт «Запустить AI»), так и дефектом «model-needed task
may start one managed Pod». **Требует решения владельца продукта до публикации.** Я не стал менять
поведение чата: это изменение продуктовой логики, а не релизная правка.

---

## TOR NATURAL-LANGUAGE

| | |
|---|---|
| Live LLM available | **NO** |
| Capacity wait | 60-секундное окно политики закрылось без `gpu_unavailable`; один запрос шёл 481 с (состояние было `searching`, останавливаться было не на чем) — **одна попытка, ноль повторов**, Pod не создавался |
| Natural-language request | **BLOCKED EXTERNALLY — CAPACITY** |
| TOR REQUIRED / SOCKS5h / circuit proof on the run / bytes | **NOT OBTAINED** — ни одно действие не выполнилось: `tor_fetch` runs = 0, `result_metadata` не записан, ответ ассистента пуст (**0 символов**) |
| DNS leak | **NOT ESTABLISHED live** (нет живого запроса); детерминированно — tripwire на оба резолвера |
| Cleartext fallback | **0** — но честно: это «ничего не загружалось вовсе», а не «Tor-запрос обслужен через Tor» |
| Failure path | **NOT RUN** (по условию — только после успешного живого прогона) |

Промпт `Открой https://example.com через Tor` был отправлен и сохранён в `messages`; Tor при этом был
**готов** (приложение само подняло bundled-демон, `tor_start_managed source=bundled port=9050`, свежий
proof, pid 18100) — но готовность Tor не является доказательством маршрута, и я это так и не подаю.

**Детерминированное проводное доказательство зелёное и сохранено** —
`pytest tests/test_tor_transport.py tests/test_tor_research.py -q` → **32 passed**, в том числе:
`test_clearnet_host_is_fetched_over_socks5h_without_local_dns` (ATYP 3, `local_dns False`,
`attempts == []`), `test_closed_tor_listener_fails_the_fetch_with_a_typed_code` (typed
`tor_unavailable`, ровно одно bounded восстановление), `test_named_clearnet_url_is_fetched_through_tor_over_socks5h`
(`transport="tor-socks5h"`, `route="TOR_ONLY"`, cleartext-запусков ноль).

---

## GATEWAY / HOSTING / UPDATER — НЕ ВЫПОЛНЕНО

| Раздел | Статус |
|---|---|
| §7 precheck | **сделано ранее**: `/health` 200, `/updates/latest` **404**, `current` → `adb568734430`, откат READY |
| §8 деплой Gateway 1.2.0 | **NOT DONE** |
| §9 хостинг артефакта | **NOT DONE** |
| §10 download-back | **NOT DONE** |
| §11 updater RC E2E | **NOT DONE** (зависит от деплоя: endpoint вшит в клиент) |
| §12 failure matrix | детерминированные уровни зелёные (Rust 15, Gateway 25, Vitest 37); установочный уровень **NOT DONE** |
| §13 busy state | **NOT DONE** установочно (детерминированное покрытие есть в наборе) |
| §14 Linux regression | **NOT RE-RUN** — общий исходный код в этом шаге не менялся |
| §16 манифест (только подготовка) | не готовился в этом шаге |
| §18 security re-check | приватный ключ/пароль в репозиторий не попадали (проверялось в 5A); **старый кандидат нигде не хостится** |

---

## PRODUCTION STATE

```
Gateway deployed:            NO   (остаётся 1.1.0, /updates/latest → 404)
Artifact hosted:             NO
main merged:                 NO
tag v1.2.0:                  NO
stable production manifest:  NOT PUBLISHED
old rejected candidate:      никогда не загружался
GPU:                         0 Pods, $0
```

Cleanup: процессов `alex-llm` / `alex-backend` / `alex-host-loop` / `tor` — нет; слушателей на
8000/9050 — нет; autostart оставлен **ON** с корректным путём; окно закрывалось штатно.
Временные файлы харнессов лежат вне репозитория (`%TEMP%\canalla-verify\`).

---

## FINAL

```
RELEASE BLOCKED

reason 1  §8-§11 не выполнены: Gateway 1.2.0 не развёрнут, артефакт не выложен,
          download-back и установочный updater RC E2E не проведены
reason 2  НАХОДКА A: глобальный AI-чип показывает бесконечный `Connecting… «Ищем GPU»`,
          пока Gateway отдаёт `compute/status = searching` без активной сессии —
          прямое нарушение требования «каждая стадия bounded timeout»
reason 3  НАХОДКА B: чат-запрос не вызывает `compute/ensure`, поэтому без нажатия
          «Запустить AI» сообщение падает на `409` от `/v1/models`. Нужно решение:
          это задумано или дефект «model-needed task may start one managed Pod»?
reason 4  последний открытый продуктовый гейт — живой natural-language Tor E2E —
          BLOCKED EXTERNALLY — CAPACITY (0 Pods, $0), детерминированное
          проводное доказательство зелёное (32 passed)

green    installer 123c8ced… без пересборки, guard PASS, /health 1.2.0,
         GUI: Computer/Tor/Memory/Updater/Autostart — PASS, AI никогда не зелёный,
         потеря здоровья видна в UI и восстанавливается сама
```
