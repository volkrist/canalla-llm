# Canalla LLM 1.2.0 — деплой текущего Gateway и подготовка живой приёмки (26.09.2026)

**Задача:** устранить release blocker `POST /compute/ensure → HTTP 422` (развёрнутый Gateway был старше
текущего исходника), исправить harness «20 минут ожидания» и дождаться ёмкости для живой приёмки.
**Итог прохода:** деплой **PASS**, контракт **PASS**, harness **исправлен**, живая приёмка — **WAITING FOR
US-TX-3 GPU CAPACITY**. Ничего не замержено, не помечено тегом и не опубликовано. Потрачено **$0.00**.

---

# §1 Предполётное состояние (rollback target зафиксирован)

| Что | Значение до деплоя |
|---|---|
| Release branch HEAD | `abeae1b3afed3ec126052b1a79ac21d4190e6fe2` |
| `current` → | `/opt/alex-gateway/releases/6e2eca83e0c2` (сборка 24.09 15:00) |
| Сервис | `alex-gateway` = **active**, `User=alex-gateway`, порт 9011, один worker |
| Окружение | `EnvironmentFile=/etc/alex-gateway/alex-gateway.env` + `/etc/alex-gateway/runpod.env` (секреты не печатались) |
| PYTHONPATH | `/opt/alex-gateway/current/gateway:/opt/alex-gateway/current/backend` |
| DB revision | `0001_gateway_core (head)` |
| `/health` | 200 · 1.2.0 · ready |
| `/updates/latest` | 204 (манифеста нет) |

Старая release-директория **не удалялась** — она остаётся целью отката.

# §2 Деплой текущего исходника

| Шаг | Факт |
|---|---|
| Staging | `tar.gz` собран локально из HEAD `abeae1b` (`gateway/`, `alembic/`, `alembic.ini`, `requirements.txt`, `backend/app/`), 312 958 B, без `__pycache__` |
| Новая release-директория | **`/opt/alex-gateway/releases/abeae1b3afed`** (root:alex-gateway 0750, dirs 0750/files 0640) |
| Зависимости | `pip install -r requirements.txt` в общий venv `/opt/alex-gateway/venv` — ok |
| Миграции | `alembic -c alembic.ini upgrade head` штатным способом (env из production-файлов) |
| Переключение | атомарно: `ln -sfn <new> current.new` → `mv -T current.new current` |
| Рестарт | `systemctl restart alex-gateway` → **active** |

# §3 Доказательство, что развёрнут именно текущий исходник

`current` → `/opt/alex-gateway/releases/abeae1b3afed`. Пять ключевых файлов **хеш-идентичны** локальному
дереву на `abeae1b`:

```
gateway/compute.py                     06de3103fbcdea096410c580784d8e8ad26134ce919b1092258cc20b96dcf308
gateway/routes.py                      18eb7f7de3b8b939dd485e4a3a1243a9156caae6cba8b7d8b6eaf6927eb513ef
gateway/provider.py                    2afbcd9b0f9721f7a62b0f402a7720d0884b40ff7743ab815c00c4ccd0a6ce79
backend/app/compute/candidates.py      db2b05b99c1925fca88cbebd8fd2b73ecf820692ee00ecc666e4acfae29a7f5b
backend/app/cloud/demand.py            c9c774f9b28f2eacfed1142b43f335eacb7725e3353a669b14f0da6c12279cc1
```

Плюс: `alembic current` → `0001_gateway_core (head)`; `/health` → 200 `version 1.2.0` `ready:true` `database:ok`;
`/updates/latest` → **204** (манифест по-прежнему не опубликован).

# §4 Harness fail-fast (только тест, production-семантика не менялась)

`apps/desktop/e2e/live-ai-acceptance.mjs`:

* было: одно ожидание `connected` с таймаутом **1 200 000 мс** (20 минут) — именно оно скрывало терминальный 422;
* стало: **две фазы** — `CONNECT_TRIGGER_WAIT_MS` (**150 с**) на появление перехода из `disconnected/<code>`
  (продукт обязан выйти из него в своём собственном bounded-окне 60 с) и, только если переход начался,
  `READY_WAIT_MS` (900 с) на реальную загрузку модели. Если перехода нет — сразу печатается пара
  `before -> after` и приёмка падает, не ожидая 20 минут;
* ожидание ответа ограничено: 900 с при `connected`, 30 с иначе;
* `prettier --check` и `node --check` — чисто.

# §5 Тесты и контрактный тест без создания compute

* Gateway: `pytest tests/test_compute.py tests/test_allocation.py tests/test_capacity.py tests/test_auth.py -q`
  → **83 passed**.
* **Контракт клиент→Gateway доказан схемой развёрнутого сервиса** (`GET /openapi.json` на loopback, read-only):

```
paths:   ['/compute/ensure', '/compute/status', '/compute/stop']
schema:  EnsureRequest
fields:  ['allow_community', 'auto_stop_minutes', 'gpu_id', 'max_hourly_price', 'min_vram_gb',
          'operation_id', 'origin', 'selection', 'session_budget', 'strategy', 'task_id']
```

  Клиент (`app/cloud/demand.py`) отправляет `max_hourly_price`, `session_budget`, `min_vram_gb`, `selection`,
  `strategy`, `gpu_id`, `allow_community` — **все присутствуют** в схеме, плюс `origin`, которого не было в
  старой сборке. Именно отсутствие `origin`/`strategy` в старой схеме и давало 422.
  Compute при этой проверке **не создавался** (только чтение схемы) — денег не потрачено.

# §6 Денежный и ёмкостный гейт — ёмкости нет

Read-only сканы каталога US-TX-3 (6 проверок, 10:37–10:53Z):

```
US-TX-3 rows in the catalogue: 0 (any price, any stock)   ×6
US-TX-3 candidates <= $2.00/h: 0
running pods = 0
```

Карта, которая была видна ранее (L40S 48 GB, $1.09/ч, LOW), из каталога **временно исчезла** — окно
ёмкости закрылось. Поэтому живая приёмка **не запускалась**: Pod не создавался, трат нет.

```
FINAL: WAITING FOR US-TX-3 GPU CAPACITY

Gateway deploy        : PASS  (releases/abeae1b3afed, health 200/1.2.0/ready, /updates/latest 204)
Deployed source       : PASS  (5 файлов хеш-идентичны HEAD abeae1b)
compute/ensure contract: PASS (EnsureRequest содержит все поля клиента, включая origin и strategy)
Gateway tests         : PASS  (83 passed)
Harness fail-fast     : FIXED (150 с вместо 20 минут)
Live AI + Tor         : NOT RUN — ёмкости в US-TX-3 нет
Spend                 : $0.00     ·    Pods: 0
Publication           : NOT STARTED (main, тег v1.2.0 и манифест не тронуты)
```

**Следующий шаг (без изменения плана):** как только в US-TX-3 появится Secure GPU ≥48 GB ≤$2/ч с
`availability != NONE` — запустить `--phase chat` → `--phase tor` → `--phase stop` (один Pod, ≤20 минут,
≈$0.30–0.40), затем §11–§17 задания: commit → merge в `main` → push → тег `v1.2.0` → push → манифест последним
действием → read-only пост-проверка.
