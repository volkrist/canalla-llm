# Canalla LLM 1.2.0 — живая приёмка выполнена: **AI PASS, Tor ЗАБЛОКИРОВАН продуктом** (26.09.2026)

**Вердикт: `RELEASE BLOCKED — LIVE TOR ACCEPTANCE FAILED`.** Ёмкость US-TX-3 появилась, приёмка прошла
целиком, **живой AI-путь подтверждён**, но natural-language Tor-запрос в production-режиме (shared)
**не может выполнить выборку** — продукт сам отвечает типизированным отказом
«No web results available: tool calling unsupported.» Манифест не публикуется, `main` и теги не тронуты.

---

## 1. Прогон (окно ёмкости 14:23:10Z)

| | |
|---|---|
| Ёмкость | NVIDIA L40S 48 GB · US-TX-3 · **$1.09/ч** (watcher поймал на 9-м опросе при 60-секундном интервале) |
| Pod | **`45q0fi57ms3ram`** (`alex-gw-36104ce5-…`), создан продуктом 14:23:42.670Z |
| Модель Ready | **14:24:26.613Z — 44 секунды** после создания |
| Том | `uwgeaie5b0` (US-TX-3), бюджет сессии урезан Gateway до баланса |
| Ответ чата | **«Готово»** (реальная генерация, TTFT через Gateway) |
| Остановлен | **14:25:03Z** (watcher, провайдер-сайд), сессия закрыта `provider_missing` 14:25:11Z |
| Стоимость | 88 с · оценка **$0.026644** |

## 2. Что PASS (живые доказательства из `acceptance-all.log`)

```
PASS the operator's session is restored (no first-run, no login)
  ai before   {"state":"disconnected","code":"off", ...}
  ai states seen: disconnected/off -> connecting/starting -> connected/ready
PASS the chat started compute by itself (no Settings button, no resend)
PASS Connecting appeared only while a transition was real
PASS the model became Ready and the chip turned Connected  [connected after 59690 ms]
PASS the chat produced an answer  [1 assistant messages, 6 chars]
  answer: Готово
  tor chip before  Tor: Готово (states: ready)
PASS the bundled Tor became ready by itself (no button, no setup)
PASS the Tor chip is green on a proof, not on an open port  [state=ready label=Tor: Готово]
PASS the proof is a managed SOCKS5h round trip and still fresh
     [{"verified":true,"method":"socks5h","source":"managed","port":9050,"version":"0.4.9.12","age_seconds":76}]
PASS the Tor request produced an answer  [2 assistant messages, 65 chars]
```

То есть: **честный переход `Disconnected → Connecting → Connected`** (после утреннего фикса ложной
зелёной), модель реально загрузилась, чат получил настоящий ответ, Tor-сервис поднялся сам и имеет
свежий proof `socks5h` от управляемого демона.

## 3. Что FAIL и почему (корень доказан кодом, не догадкой)

Ответ на Tor-запрос:

```
Не удалось: «No web results available: tool calling unsupported.»
FAIL the answer carries the Tor sources the fetch returned  [{"tor":0,"summary":""}]
```

Цепочка в исходниках:

| Место | Факт |
|---|---|
| `apps/backend/app/cloud/provider.py` | `class GatewayProvider: supports_tools = False` — в **production (shared)** режиме провайдер объявляет, что инструменты недоступны. Флаг пришёл вместе с самим shared-режимом (`1ebc6ed`, 21.09) |
| `apps/backend/app/tools/orchestrator.py:160` | ранний возврат: `if not provider.supports_tools and not paid_needed and not local_needed:` → `web_status: unavailable/model_tools_unsupported` + заметка «No web results available: tool calling unsupported.» |
| `apps/backend/app/tools/orchestrator.py:397` | серверная политика `tor_fetch` для URL из промпта (`«Открой example.com через Tor» must not depend on the model deciding to call tor_fetch`) живёт **ниже** раннего возврата → в shared-режиме **недостижима** |

Итог: в production-режиме **весь tool-слой** (web, Tor, локальные инструменты) недоступен, поэтому
обещание «Tor-запрос не зависит от решения модели» сейчас не выполняется. Это отказ **fail-closed** —
clearnet-fallback не произошёл, DNS-утечки нет, типизированное сообщение вместо выдумки.

**Прецедент:** в STEP 5B (24.09) живой Tor-turn тоже завершился типизированным отказом до выполнения
инструментов; детерминированное wire-доказательство Tor (`TOR_ONLY`, `socks5h`, proof, DNS 0, clearnet 0)
лежит в `docs/report-2026-09-24-step5b-full-ru.md` §ЧАСТЬ IX. То есть natural-language Tor-fetch
**никогда не был принят живьём** — и сегодня выяснилась его точная структурная причина.

## 4. Два пробела в harness (не дефекты продукта) — исправлены

| Пробел | Следствие | Исправление |
|---|---|---|
| `checkTorRoute(...)` вызывался без `await` | Tor-проверки печатались после строк об остановке (гонка вывода) | `await` добавлен |
| «Остановить AI» искали на экране чата | `FAIL the product offers a Stop AI control` — кнопка живёт в Settings → Canalla Cloud | harness сам открывает Settings через штатное `Ctrl+,` и вкладку «Canalla Cloud» |

Коммит `ea3c868`. (Очистка Pod'а в любом случае гарантирована watcher'ом — провайдер-сайд: Pod освобождён,
`pods = 0`.)

## 5. Деньги

| Статья | Сумма |
|---|---|
| Исторический инцидент | **$6.3351** |
| Этот прогон (Pod `45q0fi57ms3ram`, 88 с) | **$0.026644** (оценка Gateway); по балансу пока $0.0097 (биллинг отстаёт) |
| Всего за сегодня по балансу ($1.8894 → $1.60906) | **$0.2803** |
| Постоянное хранилище | ≈$3.50/мес |
| Сейчас | Pod'ов **0**, расход аккаунта $0.005/ч (простой), watcher **остановлен** (после реального провала — без повторных трат) |

## 6. Решение, которое нужно от оператора (дальше без него не двигаюсь)

**(a) Выпустить 1.2.0 с задокументированным ограничением.** Тогда в release-нотах и в отчёте честно
фиксируется: production (shared) режим пока **без tool-слоя**; Tor-сервис всегда готов и доказан
(`managed`, `socks5h`, свежий proof), чат работает, но запросы, требующие web/Tor-инструментов,
отвечают типизированным «tool calling unsupported» (fail-closed). **Живая Tor-приёмка при этом NOT MET**,
а не PASS.

**(b) Довести Tor до реального выполнения.** Минимальный честный шаг — выполнять **серверную политику**
`tor_fetch` для URL, названного в промпте, **до** проверки `supports_tools` (она не зависит от модели),
и отдавать модели уже полученные источники. Это правка backend + тесты + **пересборка sidecar** (новый
хеш) → новый установщик → повторная приёмка с одним Pod. Объём: небольшая правка + полная цепочка
артефактов заново.

**Моя рекомендация:** если Tor-в-production входит в обещание 1.2.0 (а в вашем задании §15/§17 он
обязателен) — идти путём **(b)**, предварительно посмотрев мою оценку правки; если вы сознательно
понижаете §15/§17 до ограничения — путь **(a)**, и я сразу выпускаю 1.2.0 с честной формулировкой.

```
LIVE AI CHAT        : PASS   (реальный ответ «Готово», честные переходы, модель Ready за 44 с)
TOR SERVICE         : PASS   (managed, socks5h, свежий proof, чип зелёный)
TOR FETCH (live)    : FAIL   (shared-режим без tool-слоя: «tool calling unsupported»)
CLEANUP             : PASS   (Pods = 0, watcher остановлен, без повторных трат)
PUBLICATION         : NOT STARTED (main / тег v1.2.0 / манифест не тронуты)
VERDICT             : RELEASE BLOCKED — решение (a) или (b) за оператором
```
