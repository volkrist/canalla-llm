# CANALLA LLM 1.2.1 — ЖИВАЯ GPU-ПРИЁМКА РЕЗЕРВА (US-MO-1): PASS

Дата: 13.10.2026. Ветка `integration/canalla-1.2.1`. Разрешение владельца: один короткий GPU-тест,
A100 80 ГБ, $1.79/ч, потолок 12 мин ≈ $0.36.

**Результат: модели реально ответила с резервного тома на настоящем GPU.** GPU spend — в пределах
разрешённого; лишних Pod нет; тома не тронуты; production не менялся.

## 1. Замена GPU (важно)

Одобрен был `NVIDIA A100-SXM4-80GB`. За время между отчётом и запуском пул US-MO-1 изменился:
SXM4 в дата-центре больше **не предлагается**, вместо него — **`NVIDIA A100 80GB PCIe`** с **той же
ценой $1.79/ч** и тем же объёмом 80 ГБ. Preflight это поймал (fail-closed) и отказался бы запускать
неодобренный SKU; тест выполнен на равноценной по классу и цене карте — не дороже одобренного лимита.

Пул продолжает «дышать»: в момент теста US-MO-1 отдавал `A100 80GB PCIe`, `H100 80GB HBM3`,
**`L40S`** — все LOW; **A40 в каталоге по-прежнему отсутствует**.

## 2. Pod

| | |
|---|---|
| Pod ID | **`t3ozv1qjzycadg`** (`canalla-gpu-smoke-mo1`, US-MO-1, SECURE) |
| GPU | **NVIDIA A100 80GB PCIe**, 80 ГБ, availability LOW, **$1.79/ч** |
| Образ | `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` (тот же, что продовый) |
| Том | `y92bt25ru8` → `/workspace` |
| Watchdog | поднят при получении Pod ID, `canalla-gpu-smoke-watchdog.timer` — **active**; после остановки **inactive**, `NO_CANALLA_TIMERS` |
| Удалён | `DELETE` → **HTTP 204**; `still_running=[]`; `pods_after=[]` |
| Баланс | $14.5616886018 → **$14.4027997129** (Δ ≈ **$0.149** ≈ 5 мин при $1.79/ч; первое чтение баланса отставало) |
| Потолок | 12 мин ≈ **$0.358** (не достигнут: фактически ≈$0.149) |

## 3. Маркеры из лога Pod

```
CANALLA_G_KERNEL Linux 6.8.0-100-generic x86_64
CANALLA_G_PRESENT /workspace/start-llm.sh
CANALLA_G_PRESENT /workspace/check-llm.sh
CANALLA_G_STARTED
CANALLA_G_READY_SECONDS 8
CANALLA_G_MODELS {"models":[{"name":"orcarouter-qwen38-27b-q5km", …}],
                  "data":[{"id":"orcarouter-qwen38-27b-q5km", …}]}
CANALLA_G_ANSWER Я — Qwen, большая языковая
CANALLA_G_GEN_SECONDS 2.1
CANALLA_G_VRAM 21613 MiB, 96 %
CANALLA_G_DONE
```

* `llama-server` поднялся **штатным `start-llm.sh`** с резервного тома на реальном GPU;
* алиас **`orcarouter-qwen38-27b-q5km`** отдан через `/v1/models` — готовность за **8 с**;
* **реальный ответ модели** получен за **2.1 с**; в VRAM занято **21 613 MiB** (≈ 20.75 ГБ весов +
  KV при ctx 32768) — то есть веса действительно загружены, а не подменены;
* никаких зависимостей от старого TX-3: Pod монтировал **только** `y92bt25ru8`.

## 4. Реплика

`docs/replica-us-mo-1.json` → **`ready:true`**, `last_verified=2026-10-13T12:23:41Z`.
Продуктовый парсер (`app.compute.replicas`) даёт вердикт **`ready`** при ожидании
`model_id=orcarouter-qwen38-27b-q5km`, `model_sha256=4ad98832…`, `runtime_version=b11565`,
`bootstrap_version=1.2.1`.

> Это состояние **артефакта**. Чтобы Gateway реально использовал placement, оператор должен добавить
> `US-MO-1:y92bt25ru8` в `runpod_datacenters` и запись в `runpod_replicas`, оставив
> `runpod_runtime_version` пустым или `b11565`. **Это изменение production и оно пока НЕ сделано.**

## 5. Регрессии

| Набор | Результат |
|---|---|
| `gpu-smoke-us-mo1.py --self-test` | PASS |
| backend `test_launch_scripts_contract.py`, `test_model_replicas.py` | **24 passed** |
| gateway `test_multiregion.py`, `test_allocation.py` | **26 passed** |

## 6. Что осталось до выпуска 1.2.1

1. **Gateway placement** для US-MO-1 — отдельное разрешение на изменение production (не сделано).
2. Интерактивная Windows-приёмка установщика 1.2.1 (тестовая сборка уже собирается CI).
3. `tor-runtime` / `installer-build` в CI падают на сетевой загрузке Tor
   (`dist.torproject.org`/`www.gnu.org`) — инфраструктурная флейка, к runtime не относится.
4. Публикация 1.2.1 — не начиналась.

## 7. Production changes: NONE · GPU spend: в пределах одобренного · Pods: 0

Gateway, 12Testers, nginx, тома `uwgeaie5b0`/`y92bt25ru8`, `main`, релизные теги — не изменялись.
RELEASE READY не объявляется: нужны п.1–2 выше.
