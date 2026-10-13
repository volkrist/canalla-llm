# CANALLA LLM 1.2.1 — RUNTIME ПРОВЕРЕН В ПРОДОВОМ ОБРАЗЕ (libgomp закрыт)

Дата: 13.10.2026. Ветка `integration/canalla-1.2.1`.

## Ответы по пунктам

| Вопрос | Ответ |
|---|---|
| **libgomp есть / нет** | **ЕСТЬ** в продовом образе: `/usr/lib/x86_64-linux-gnu/libgomp.so.1` (352304 байта) |
| **production-образ проверен** | **ДА** — на доверенном GitHub Actions runner, внутри Docker |
| **Недостающие зависимости** | у `llama-server` — **ни одной**; у `libggml-cuda.so` — только `libcuda.so.1` (драйвер, даётся NVIDIA runtime при реальном GPU) |
| **CI** | **PASS** (`llama-runtime-image`); также PASS `llama-runtime`, `developer-gate`, `native-gate`, `frontend-e2e` |
| **Доп. расходы нужны** | **НЕТ** — RunPod Pod не потребовался, $0 |
| **Runtime готов к GPU-тесту** | **ДА** (реального запуска модели ещё не было) |
| **Итоговый HEAD** | **`d375699`** (проверка образа — `7ab403e`, манифест — `d375699`) |

## Как проверено

Новый доверенный job `llama-runtime-image` (ubuntu-latest): берёт **закреплённый** runtime b11565 из
архивов (SHA256 проверен), поднимает Docker, `docker pull runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`,
монтирует runtime в `/runtime:ro` и запускает `scripts/probe-runtime-in-image.sh` **внутри** образа.

Фактический вывод из лога раннера:

```
CANALLA_IMG_OS=Ubuntu 24.04.3 LTS
CANALLA_IMG_GLIBC=ldd (Ubuntu GLIBC 2.39-0ubuntu8.6) 2.39
CANALLA_IMG_CUDA_DIRS=/usr/local/cuda-12.8/ /usr/local/cuda-12/ /usr/local/cuda/
CANALLA_IMG_LIBGOMP_PRESENT 352304
CANALLA_IMG_LDD_SERVER=[]
CANALLA_IMG_LDD_CUDA=[libcuda.so.1 ]
CANALLA_IMG_VERSION=version: 0.6.0-dev (build 11565, commit f9a95aeb2)
CANALLA_IMG_OK
```

Пробник падает громко при любой недостающей библиотеке, кроме `libcuda.so.1`, и проверяет glibc,
пути CUDA, версию и весь контракт запуска (`--model --alias --n-gpu-layers --ctx-size --parallel
--flash-attn --jinja --host --port`). Его **отрицательный путь** проверен на VPS до пуша: без
смонтированного runtime он сообщает о каждой дырке и выходит с кодом 1.

## Вывод

`libgomp.so.1` был дефектом **«голого»** `ubuntu:24.04`, а не сборки. Продовый образ его содержит,
поэтому:

* runtime остаётся **точно** закреплённым официальным релизом — ничего дополнительно ставить не нужно;
* **никакого производного образа и никакой сторонней библиотеки**;
* запись добавлена в манифест `scripts/llama-runtime.json` → `verified_in_image: pass`.

`docs/replica-us-mo-1.json` остаётся **`ready:false`** до реального ответа модели.

## Полный bootstrap (без GPU)

| Что | Состояние |
|---|---|
| GGUF на `y92bt25ru8` | есть, 20 752 787 712 байт (проверено в CPU Pod, повторно не скачивался) |
| `llama-server` + `.so` | разложены в `/workspace/llama.cpp/build/bin/` (64 файла, включая CUDA libs) |
| `start-llm.sh` / `check-llm.sh` | присутствуют, `bash -n` OK, контракт ctx 32768 / ngl all совпадает |
| запуск с драйвером NVIDIA | всё, что нужно, есть в образе; не хватает только `libcuda.so.1`, который даёт реальный GPU-хост |
| зависимость от старого TX-3 | **нет** — Pod монтировал только `y92bt25ru8` и нашёл на нём и модель, и runtime |

## Замечание, не относящееся к задаче

`tor-runtime` и `installer-build` в двух последних прогонах падают на **сетевом** шаге загрузки Tor
(`dist.torproject.org` / `www.gnu.org`) — та же инфраструктурная флейка, не связанная с runtime
llama.cpp (в прогоне `38251140132` оба были зелёные). При необходимости перезапущу job’ы.

## Следующий шаг — один короткий GPU-тест в US-MO-1 (нужно разрешение)

Живой каталог на 13.10.2026 (read-only):

| GPU | VRAM | Secure $/ч | Community $/ч | availability в US-MO-1 |
|---|---|---|---|---|
| **NVIDIA A100-SXM4-80GB** | 80 ГБ | **$1.79** | $1.39 | **LOW** |
| NVIDIA H100 80GB HBM3 | 80 ГБ | $3.99 | $2.69 | MEDIUM |

* A40 и L40S в каталоге **отсутствуют**; карты 48 ГБ (RTX A6000 $0.59, L40 $0.82) в US-MO-1 сейчас
  недоступны. Поэтому фактический выбор — **A100 SXM 80 ГБ** (при пользовательском лимите ≥ $1.79).
* Максимальную цену по-прежнему задаёт пользователь в Canalla; в коде потолка нет.
* **Бюджет:** 10 минут ≈ **$0.30**, 12 минут ≈ **$0.36**; рекомендую потолок теста **12 минут**.
* **Аварийная остановка:** один managed Pod; независимый watchdog как у CPU Pod; штатный Stop в
  `finally`; подтверждение `pods: []`; при недоступности GPU — штатный durable `waiting_capacity`
  без дублей и без 20-минутного ожидания.
* **Критерии успеха:** `/v1/models` отдаёт алиас `orcarouter-qwen38-27b-q5km` (READY) за ≤ startup
  deadline; короткий chat-completion возвращает осмысленный ответ; затем Stop и `pods: []`.

GPU без нового разрешения **не запускается**.
