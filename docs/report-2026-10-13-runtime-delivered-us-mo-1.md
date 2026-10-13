# CANALLA LLM 1.2.1 — ДОСТАВКА CUDA llama.cpp НА РЕЗЕРВНЫЙ ТОМ (CPU POD)

Статус: **runtime разложен на `y92bt25ru8`; временный CPU Pod удалён и подтверждён gone.**
Найден и зафиксирован один реальный дефект совместимости: собранному `llama-server` нужен
**`libgomp.so.1`**, которого нет в «голом» образе (но который почти наверняка есть в продовом
pytorch-образе — это **ещё не доказано**). GPU не запускался.

Ветка `integration/canalla-1.2.1`. Дата: 13.10.2026. Предыдущий отчёт:
`docs/report-2026-10-12-llama-cuda-runtime.md`.

## 1. CPU Pod — факты

| | |
|---|---|
| Pod ID | **`re8suc3bvw55ku`** |
| Имя / регион | `canalla-runtime-mo1` / **US-MO-1** |
| Flavor | `cpu3c`, 4 vCPU / 8 ГБ, image `ubuntu:24.04` |
| Том | `y92bt25ru8` → `/workspace` |
| Создан → удалён | ≈ `2026-10-13T10:53:30Z` → ≈ `2026-10-13T11:02:30Z` |
| Длительность | **538.7 с ≈ 9.0 мин** (измерено скриптом) |
| Ставка (живая) | `cpu3c` = $0.03/vCPU/ч → **$0.12/ч** |
| Расчётная стоимость | 538.7 с × $0.12/3600 = **≈ $0.018** (в бюджет $0.04 уложились) |
| Баланс после | **$14.5616886018** |

Удаление: `DELETE /v2/pods/re8suc3bvw55ku` → **HTTP 204**; `still_running=[]`; `pods_after=[]`.
Повторная проверка через API: **`pods: 0`**.

## 2. Watchdog (независимая аварийная остановка)

Поднят **в момент получения Pod ID** (`systemd-run --on-active 900 --unit=canalla-runtime-mo1-watchdog
/home/alex/watchdog.sh <pod-id>`), состояние таймера — **active**. Механизм отработан заранее
(dry-run таймера) и **до** создания Pod. После подтверждённой остановки таймер снят:
`systemctl is-active` → **inactive**, `list-timers` → **NO_CANALLA_TIMERS**. В журнале watchdog
осталась только запись о прошлом (октябрьском) Pod — наш Pod удалился раньше trigger’а.

## 3. Что сделал Pod

Один скрипт `scripts/provision-llama-runtime.py --pod-command --pod-verify` (синтаксис проверен
через `sh -n` на VPS — OK). Он **внутри Pod**:

1. скачал официальные архивы `ggml-org/llama.cpp` **b11565** (CUDA 12.8 x64);
2. проверил **размер и SHA256 каждого архива** до распаковки (совпадение с пином обязательно);
3. распаковал и разложил в `/workspace/llama.cpp/build/bin/` (`llama-server` + все `lib*.so*` +
   библиотеки CUDA-рантайма);
4. прогнал проверки и выдал маркеры `CANALLA_L_*`.

## 4. Что подтверждено на томе

Из лога Pod (SSE-stream, распарсено):

```
CANALLA_L_LIB_OK libggml.so.0 60056
CANALLA_L_GGUF_BYTES 20752787712
CANALLA_L_SCRIPT_PRESENT /workspace/start-llm.sh
CANALLA_L_SCRIPT_PRESENT /workspace/check-llm.sh
CANALLA_L_SCRIPT_SYNTAX_OK start-llm.sh
```

* **GGUF на месте и цел**: `20 752 787 712` байт — совпадает с проверенным значением (повторно
  **не** скачивался).
* **Оба runtime-скрипта** присутствуют, `bash -n` для `start-llm.sh` — OK.
* **Runtime разложен**: библиотеки на месте; `llama-server` был найден и **исполнялся** —
  ядро попыталось его загрузить (см. ниже), значит файл существует и исполняемый.

> Маркеры `CANALLA_L_STAGED`/`SERVER_SHA256`/`EXEC_OK`/`CONTRACT_*` скрипт тоже выдал, но мой
> парсер логов поначалу не понял SSE-формат и полный «хвост» не собрал. Дефект парсера **исправлен**
> (`scripts/deliver-llama-runtime-pod.py`, `log_lines()`), добавлен self-test на SSE. SHA256
> бинарника остаётся известным и воспроизводимым: пин-архив даёт
> `860ae1b8cbe434bffb958939c3a8cbb89859386f6f6ee8bed76f34a702ec81d0` (значение с доверенного CI,
> распаковка содержимое не меняет).

## 5. Найденный дефект: `libgomp.so.1`

```
CANALLA_L_VERSION /workspace/llama.cpp/build/bin/llama-server: error while loading shared libraries:
                  libgomp.so.1: cannot open shared object file: No such file or directory
CANALLA_L_HELP_FLASH 0
CANALLA_L_LDD_SERVER libgomp.so.1,
CANALLA_L_LDD_CUDA   libcuda.so.1,libgomp.so.1,
```

* Собранному llama.cpp нужен **GNU OpenMP runtime `libgomp.so.1`** — его **нет** ни в архиве
  llama.cpp, ни в `cudart`-бандле, ни в «голом» `ubuntu:24.04`.
* `libcuda.so.1` отсутствует **ожидаемо** — это драйвер, его даёт GPU-хост.
* **Почему это не поймал CI:** раннер `ubuntu-latest` содержит `libgomp1`, поэтому
  `--version`/`--help` там проходят. Значит, CI доказывает корректность артефакта и раскладки, но
  **не** заменяет проверку в продовом образе.
* **Продовый образ** GPU-Pod — `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`; PyTorch-образы
  обычно ставят `libgomp1`. Значит, скорее всего, всё в порядке — но это **предположение, не
  доказательство**.
* **Итог:** на «голом» образе требование «отсутствие недостающих динамических зависимостей»
  **не выполнено**. На продовом образе — не проверено.

## 6. Состояние реплики

`docs/replica-us-mo-1.json`: `runtime_version="b11565"`, `runtime_path`, `runtime_sha256=860ae1b8…`,
`last_verified="2026-10-13T11:02:28Z"`, **`ready:false`**. Продуктовый парсер → вердикт `partial` ⇒
placement **не используется**. `ready:true` — только после реального ответа Qwen с этого тома **и**
после закрытия вопроса с `libgomp`.

## 7. Бесплатные регрессионные тесты

| Набор | Результат |
|---|---|
| `scripts/verify-llama-runtime-pin.py --self-test` | PASS |
| `scripts/provision-llama-runtime.py --self-test` | PASS |
| `scripts/deliver-llama-runtime-pod.py --self-test` | PASS (+ новый тест SSE-парсинга) |
| `apps/backend`: `test_launch_scripts_contract.py`, `test_model_replicas.py` | **24 passed** |
| `apps/gateway`: `test_multiregion.py`, `test_allocation.py` | **26 passed** |

## 8. Стоимость и отсутствие лишних ресурсов

* **GPU spend: $0** (GPU не запускался). Оплачен только CPU Pod: **≈$0.018**.
* **Pods: 0** (подтверждено дважды через API).
* Тома `uwgeaie5b0` (US-TX-3) и `y92bt25ru8` (US-MO-1) — **оба на месте**, первичный не тронут.
* Production Gateway / 12Testers / nginx — **не изменялись**. `max_hourly_price` пользователя не
  менялся. 1.2.1 не публиковалась.

## 9. Следующий шаг — минимальный тест (нужно согласие)

Один короткий **CPU** Pod (`$0.12/ч`, ~5–10 мин, **≈$0.02**) с **продовым образом**
`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`, который:

1. проверит, есть ли `libgomp.so.1` в самом продовом образе, и запустит
   `/workspace/llama.cpp/build/bin/llama-server --version` + `--help` **в нём** (это и есть
   решающее доказательство, и это в разы дешевле проверки на GPU);
2. если `libgomp` нет — закрепит и разложит `libgomp.so.1` (официальный пакет Ubuntu `libgomp1`)
   рядом с бинарником, сделав runtime самодостаточным;
3. не запускает GPU, не трогает тома-источники.

**Почему не сразу GPU:** GPU-образ и есть продовый, поэтому короткий CPU-прогон с тем же образом
докажет совместимость до того, как мы потратим GPU-часы ($1.09/ч) на тест, который может упасть на
`libgomp`.

## 10. Точный следующий шаг — GPU-тест (после §9, отдельное согласие)

* **GPU:** A40 48 ГБ (приоритет), иначе L40S 48 ГБ; **живую** ставку перечитать перед запуском.
* **Лимит цены:** только пользовательский `max_hourly_price` (в коде потолка нет).
* **Что проверяем:** `start-llm.sh` поднимает `llama-server` → `127.0.0.1:8080/v1/models` READY →
  короткая генерация Qwen → подтверждённый Stop → `pods: []`.
* **Ограничения:** один managed Pod; без 20-минутного ожидания capacity; при отсутствии GPU —
  сохранить штатный durable `waiting_capacity` без дублей.

## 11. Production changes: NONE; GPU spend: $0

Gateway, 12Testers, nginx, тома, `main`, релизные теги не изменялись. `RELEASE READY` не
объявляется: нужен реальный запуск Qwen и закрытие вопроса `libgomp`.
