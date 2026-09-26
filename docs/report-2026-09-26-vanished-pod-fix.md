# Canalla LLM 1.2.0 — исчезнувший Pod: фикс, деплой и живая проверка (26.09.2026)

**Задача:** закрыть найденный при подготовке живой приёмки дефект — клиент показывал **зелёное
«Connected»**, когда Pod'а не существовало вовсе.

**Итог:** дефект исправлен (`a0faebe`), Gateway передеплоен из нового HEAD (`releases/1994c0ab2016`),
фикс подтверждён **живьём**: шлюз сам закрыл зависшую сессию, бейдж установленной 1.2.0 вернулся в
честное `Disconnected`. Денег не потрачено, Pod'ов нет.

---

# §1 Что было не так

| Факт | Значение |
|---|---|
| Pod `693b6vb185vmi8` исчез у провайдера | ~11:53Z (после освобождения) |
| Gateway | контрольная строка оставалась `generating`, `error_code=not_found`, `active_session_id` заполнен — **до 13:32** |
| Клиент (установленная 1.2.0) | бейдж `connected/ready` «AI отвечает» при `GET /v1/models` → **503** |
| Причина | `tick()` при ошибке чтения Pod (`not_found`) записывал код ошибки и **выходил**; сессию закрывала только реконсиляция, а её дёргал лишь `ensure` |

Нарушалось правило продукта «статус не может заявлять больше, чем доказала подсистема»: интерфейс
обещал готовую модель, которой не существовало. Дополнительно это мешало живой приёмке: переход
`Disconnected → Connecting → Connected` невозможно наблюдать, если стартовая точка уже зелёная.

# §2 Исправление

`apps/gateway/gateway/compute.py`, `tick()`: ответ провайдера `not_found` трактуется как
**доказательство**, а не транзиентная ошибка чтения → вызывается `_reconcile_locked()`, которая
закрывает сессию как `provider_missing` и снимает активную сессию с контрольной строки.

Тесты:

* новый `test_the_tick_finalizes_a_pod_the_provider_no_longer_knows` — заглушка провайдера отвечает
  **404** для Pod'а, которого нет в списке (как в реальном API), и `tick()` обязан привести состояние
  к `stopped` / `provider_missing` / `not_found`;
* тестовый двойник (`tests/conftest.py`) больше не возвращает `pods[0]` для любого id — это и было
  причиной, по которой путь «Pod исчез» в тестах не воспроизводился.

```
gateway pytest           201 passed
gateway ruff check       All checks passed
gateway ruff format      clean (32 files)
```

# §3 Деплой текущего исходника

| Шаг | Факт |
|---|---|
| Staging | `tar.gz` из HEAD `1994c0ab2016` (`gateway/`, `alembic/`, `alembic.ini`, `requirements.txt`, `backend/app/`), **313 136 B**, SHA256 `3b98b54bb9ee4af9e00dfc593202790beb3cc77eb45b51d8ff90a33e72918049` |
| Доставка | `scp` → VPS, SHA256 на сервере совпал |
| Новая release-директория | `/opt/alex-gateway/releases/1994c0ab2016` (root:alex-gateway, dirs 0750 / files 0640) |
| Зависимости | `pip install -r requirements.txt` в общий venv — ok |
| Миграции | `alembic -c alembic.ini upgrade head` (env из production-файлов) → `0001_gateway_core (head)` |
| Переключение | атомарно: `ln -sfn … current.new` → `mv -T current.new current` |
| Рестарт | `systemctl restart alex-gateway` → **active** |
| Откат | предыдущая директория `abeae1b3afed` **не удалена** |

Хеш ключевого файла на сервере совпадает с локальным:

```
gateway/compute.py  a002cac3a176b1f121b3f4ff2fc5f3d14ae095f0a9bb9c09f2f24330629b1a69
```

# §4 Проверки после деплоя

| Проверка | Результат |
|---|---|
| `current` → | `/opt/alex-gateway/releases/1994c0ab2016` |
| `/health` (loopback и публично) | **200** · `version 1.2.0` · `ready:true` · `database:ok` · `provider_configured:true` |
| `/updates/latest` | **204** (манифест по-прежнему не опубликован) |
| `alembic current` | `0001_gateway_core (head)` |
| Служба | `active` |

# §5 Живое доказательство фикса (без денег)

| Что | До фикса | После деплоя |
|---|---|---|
| Контрольная строка шлюза | `generating`, `active_session_id` заполнен, `error_code=not_found` | **`stopped`**, активная сессия снята, `error_code=not_found` |
| Сессия | `state=generating`, `stopped_at` пуст | **`stopped_at=13:32:16`**, `stop_reason=provider_missing` |
| Audit | — | событие `stopped` / `provider_missing` / `not_found` |
| Бейдж установленной 1.2.0 | `connected/ready` «AI отвечает» (ложь: `/v1/models` → 503) | **`disconnected/off`** «AI выключен: GPU не запущен» |
| Готовность к следующему запуску | — | `create_attempts = 0` (бюджет попыток чист) |

Строка-итог из бесплатной фазы наблюдения (`--phase observe`, exit 0):

```
PASS the operator's session is restored (no first-run, no login)
  ai before   {"state":"disconnected","code":"off","text":"Disconnected",
               "label":"AI: Disconnected. AI выключен: GPU не запущен"}
OBSERVE PASS
```

# §6 Нюанс, который стоит знать

Для сессии, пережившей исчезновение Pod'а, оценка стоимости в данных шлюза стала **верхней границей**:
`billable_seconds` = 6195 (до момента, когда шлюз заметил пропажу) → `estimated_usd` = 1.875708, тогда как
Pod реально жил ~246 секунд (≈ $0.075 по $1.09/ч). Это следствие того, что сессия висела открытой
**до** фикса; провайдерский биллинг остаётся источником истины. После фикса «зависание» ограничено одним
тиком (секунды), поэтому оценки снова честные — менять семантику `_finalize`/`estimate` перед релизом
намеренно не стал.

# §7 Эффект для релиза

* Правило «никаких ложных зелёных статусов» теперь выполняется и в случае исчезнувшего Pod'а.
* Живая приёмка начнётся из **настоящего** `Disconnected`, поэтому переходы
  `Disconnected → Connecting → Connected` наблюдаемы и станут доказательством, а не совпадением.
* Публикация по-прежнему заблокирована до единственной платной приёмки (ёмкости US-TX-3 нет, траты $0).

```
Gateway deploy     : PASS  (releases/1994c0ab2016 · health 200/1.2.0/ready · /updates/latest 204)
Vanished-Pod fix   : PASS  (a0faebe · 201 gateway tests · живое закрытие сессии 13:32:16)
False green status : GONE  (бейдж установленной 1.2.0 → disconnected/off)
Live AI + Tor      : WAITING FOR US-TX-3 GPU CAPACITY  (Pod'ов 0, траты $0)
Publication        : NOT STARTED
```
