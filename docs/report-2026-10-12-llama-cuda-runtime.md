# CANALLA LLM 1.2.1 — CUDA RUNTIME llama.cpp ДЛЯ РЕЗЕРВНОГО US-MO-1

Статус: **runtime выбран, закреплён, воспроизводимо раскладывается и доказан на доверенном
Linux CI**. На резервный том он **ещё не доставлен** — доставка требует либо S3-ключа (действие
владельца), либо одного временного CPU Pod (требует согласия). GPU не запускался, тома не
изменялись.

Ветка `integration/canalla-1.2.1`. Дата: 12.10.2026. HEAD: **`ecf90e9`**.
Предыдущее состояние: `docs/report-2026-10-12-reserve-model-downloaded.md`,
`docs/model-source-1.2.1.md`.

## 1. Какую версию llama.cpp выбрал и почему

* Официальный **бинарный** канал llama.cpp — rolling-prerelease теги `bNNNNN` (несколько в день).
  Semver-релиз, который GitHub API отдаёт как `latest` (**v0.6.0**), содержит единственный ассет
  `nightly-tag.txt` — **бинарников в нём нет**. Поэтому пинуется rolling-сборка.
* Выбран **`b11565`**: опубликован `2026-10-12T09:15:54Z`, commit
  **`f9a95aeb2096d0c0218e0cd6e6d901cd37be798a`**.
* Платформа **`linux-x86_64`, CUDA 12.8** — **точно совпадает** с продовым образом GPU-Pod
  `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` и `runpod_min_cuda_version=12.8`.
* Почему новая сборка, а не копия прежнего бинарника: сборка llama.cpp есть на первичном томе
  `uwgeaie5b0`, но `US-TX-3` не даёт ёмкости её прочитать, а в git её нет. Поэтому резерв получает
  **свою воспроизводимую конфигурацию запуска** — это **не** побитовая реплика старого бинарника.

## 2. Источник, commit и SHA256

Официальный релиз `ggml-org/llama.cpp`: `https://github.com/ggml-org/llama.cpp/releases/tag/b11565`.

| Ассет | Размер, байт | SHA256 (официальный digest GitHub) |
|---|---|---|
| `llama-b11565-bin-ubuntu-cuda-12.8-x64.tar.gz` | 171 559 844 | `61b8cf4e49739afe4008b06a92390dff999e706e9e7e9749c015544a9f241627` |
| `cudart-llama-b11565-bin-ubuntu-cuda-12.8-x64.tar.gz` | 594 377 834 | `e6177074761d8be20b76ce165944f16b7a338c0b477d1ecbb240e38c743c1b90` |

Оба хэша **перепроверены живьём** (`sha256sum` скачанных файлов совпал с digest из API).
Подлинность берётся из **официального GitHub Releases API**: `scripts/verify-llama-runtime-pin.py`
перечитывает релиз и падает при любом расхождении тега, commit, размера или digest. Пересчёт
SHA256 скачанного файла — это **проверка целостности, а не доказательство подлинности**.
Зеркала не используются. Версия, которую сообщает сам бинарник: `version: 0.6.0-dev (build 11565,
commit f9a95aeb2)` — совпадает с пином.

## 3. CI job и фактический PASS/FAIL

Добавлен job **`llama-runtime`** (`ubuntu-latest`) в `.github/workflows/ci.yml`. Шаги: сверка пина
с официальным релизом → скачивание+проверка+раскладка → запуск `--version`/`--help` → прогон
**той же команды, что выполнит CPU Pod** (Linux, потому что Windows-хост не создаёт «висячие»
симлинки архива).

| Прогон | HEAD | llama-runtime | developer-gate | native-gate | frontend-e2e | installer-build | tor-runtime |
|---|---|---|---|---|---|---|---|
| `38248642469` | `f04c240` | **PASS** | PASS | PASS | PASS | PASS | FAIL (сеть) |
| `38251140132` | `ecf90e9` | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** | **PASS** |

Прогон `38251140132` (HEAD `ecf90e9`) — **полностью зелёный, все 6 job’ов PASS**.

Фактические строки из лога доверенного раннера (`llama-runtime`, job `114817449961`):

```
LLAMA-PIN: PASS — llama.cpp b11565 (f9a95aeb2096, cuda 12.8, linux-x86_64); 2 assets match the official release digests
staged bin: 51 files, 10 symlinks
staged cudart: 3 files, 0 symlinks
staged llama.cpp b11565: 54 files, server sha256=860ae1b8cbe434bffb958939c3a8cbb89859386f6f6ee8bed76f34a702ec81d0
CONTRACT: PASS — version: 0.6.0-dev (build 11565, commit f9a95aeb2)
CANALLA_L_START
CANALLA_L_SERVER_SHA256 860ae1b8cbe434bffb958939c3a8cbb89859386f6f6ee8bed76f34a702ec81d0
CANALLA_L_OK
the Pod command staged and ran the pinned runtime
```

* Шаг pod-команды сам требует наличия маркеров `CANALLA_L_OK`,
  `CANALLA_L_SERVER_SHA256 860ae1b8…` и строки `build 11565` — иначе он падает; все три
  присутствуют. Хэш совпадает с тем, что даёт Python-путь.
* **`tor-runtime` в первом прогоне упал по сетевой причине, не по коду**: раннер не смог
  подключиться к `www.gnu.org` (`curl: (28) Failed to connect … port 443`) за текстом GPL; сверка
  пина Tor при этом прошла с **verified signature**. Во втором прогоне job зелёный — flake,
  к этому блоку не относится (Tor не менялся).

## 4. CUDA / runtime-зависимости

* `llama-server` — тонкий лаунчер (17 888 байт); его `NEEDED` — `libllama-server-impl.so`,
  `libstdc++`, `libgcc_s`, `libc`; `RUNPATH = $ORIGIN`.
* Тяжёлый CUDA-бэкенд `libggml-cuda.so` (176 МБ) подгружается **динамически**, поэтому
  загрузчику не нужен драйвер для `--version`/`--help`.
* Пара `cudart` даёт `libcudart.so.12`, `libcublas.so.12`, `libcublasLt.so.12`. Продовый образ
  `cu1281` их уже содержит; раскладка рядом с бинарником — defence in depth: с `$ORIGIN` пара
  грузится на любом хосте, что и доказывает GPU-less CI.
* Проверено на VPS **без драйвера**: `libcuda.so.1` отсутствует, при этом `--version` и `--help`
  дают exit 0. Итоговый размер раскладки ≈ **1.07 ГБ** (213 МБ бинарник+libs, 829 МБ CUDA libs).

## 5. Загружен ли бинарник на `y92bt25ru8`

**Нет.** Артефакт собран, разложен и проверен, но **на том не доставлен**: `start-llm.sh` ждёт
`/workspace/llama.cpp/build/bin/llama-server`, которого там пока нет. Причина и два безопасных
способа — в §11.

## 6. Совместимы ли `start-llm.sh` / `check-llm.sh`

**Да, без изменений.** Сверено с `common/arg.cpp` на точном commit пина:

* `-ngl, --n-gpu-layers N` принимает **`N`, `auto` или `all`** (`all` → `-2`) ⇒ `--n-gpu-layers all`
  корректен;
* `-fa, --flash-attn [on|off|auto]` ⇒ `--flash-attn on` корректен;
* `--ctx-size 32768`, `--parallel 1`, `--alias orcarouter-qwen38-27b-q5km`, `--jinja`,
  `--host 127.0.0.1`, `--port 8080` — без изменений; health `127.0.0.1:8080/v1/models`.

Регрессия: `apps/backend/tests/test_launch_scripts_contract.py` — **8 passed** (4 прежних + 4 новых,
кросс-сверка пина со скриптами).

## 7. Состояние replica ready

`docs/replica-us-mo-1.json`: `runtime_version="b11565"`, `runtime_path`, `runtime_sha256=860ae1b8…`,
`ready:false`. Продуктовый парсер (`app.compute.replicas`) даёт вердикт **`partial`** ⇒ placement
**не используется** автоматическим выбором. `ready:true` — только после доказанного запуска Qwen с
этого тома.

> **Требование к конфигурации.** У Gateway одно поле `runpod_runtime_version`. Чтобы запись резерва
> принималась, оно должно оставаться **пустым** либо равным **`b11565`**; иначе вердикт будет
> `wrong_runtime`. Первичный том записи не имеет и в `require_records` не входит, поэтому он не
> затрагивается.

## 8. Фактические дополнительные расходы

**$0.** Новых оплачиваемых ресурсов не создавалось. Единственный расход этого этапа — сетевой
трафик GitHub на доверенный CI/VPS.

## 9. Остались ли активные Pod

**Нет.** В этом блоке Pod не создавались. Тома `uwgeaie5b0` (US-TX-3) и `y92bt25ru8` (US-MO-1) —
оба на месте. Суммарные реальные расходы проекта на эти этапы остаются ≈$0.018 (прежний CPU Pod).

## 10. Точная стоимость и условия следующего живого GPU-теста

Тест **не запускается** без отдельного подтверждения. Условия:

* **GPU:** A40 48 ГБ (приоритет) либо L40S 48 ГБ; **живую** ставку перечитать перед запуском
  (прежние наблюдения: A40 ≈$0.59/ч, L40S ≈$1.09/ч — это **не** фиксированные тарифы).
* **Лимит цены — только пользовательский** `max_hourly_price`; в коде фиксированного потолка нет.
* **Длительность:** одна короткая попытка (загрузка Qwen + короткая генерация + Stop), потолок
  бюджета задаётся `session budget` пользователя; без 20-минутного ожидания capacity.
* **Останов:** штатный Stop + независимый контроль отсутствия Pod; всё через один managed Pod.
* Критерии: `llama-server` READY, реальный ответ Qwen, подтверждённая остановка и `pods: []`.

## 11. Доставка runtime на том — две опции (выбор за владельцем)

**Опция A — S3 API (бесплатно, но нужен ключ владельца).**
`y92bt25ru8` в `US-MO-1` S3-совместим, но **управляемого API для S3-ключей нет**: живьём проверены
`/v2/s3keys`, `/v1/s3keys`, `/v2/account/s3keys` → **404**. Значит, ключ создаётся вручную в консоли
RunPod (**Settings → S3 API Keys**, дата-центр `US-MO-1`), после чего доставка идёт с доверенного
VPS (`https://s3api-us-mo-1.runpod.io/`, bucket = `y92bt25ru8`). Ключ **не печатать и не копировать
на недоверенный Windows**.

**Опция B — один временный CPU Pod (требует согласия на оплату).**
Команда **готова**: `python scripts/provision-llama-runtime.py --pod-command` печатает
самодостаточный POSIX-скрипт (пин внутри, проверка размера и SHA256 до распаковки, маркеры
`CANALLA_L_*`). Прогоняется на Linux-CI.

| | |
|---|---|
| Flavor | `cpu3c`, 4 vCPU / 8 ГБ, US-MO-1 |
| Ставка | **$0.12/ч** (подтверждённый ранее живой тариф) |
| Время | ≈10–15 мин (два архива, 765 МБ) |
| Верхний бюджет | **≈$0.04** |
| Том | `y92bt25ru8` → `/workspace` (**не** удалять) |
| Аварийная остановка | независимый systemd-timer + `/home/alex/watchdog.sh <pod-id>` (уже отработан) |

Опция B не требует ни S3-ключа, ни переноса архивов с VPS: Pod скачивает официальные архивы сам и
раскладывает их прямо на том.

## 12. Изменения кода

* `scripts/llama-runtime.json` — пин (тег, commit, оба ассета, контракт запуска).
* `scripts/verify-llama-runtime-pin.py` — сверка пина с официальным релизом (+`--self-test`).
* `scripts/provision-llama-runtime.py` — скачивание/проверка/раскладка, запуск проверок
  (+`--self-test`, `--pod-command`).
* `.github/workflows/ci.yml` — новый job `llama-runtime`.
* `apps/backend/tests/test_launch_scripts_contract.py` — кросс-сверка пина со скриптами.
* `docs/replica-us-mo-1.json` — `runtime_version=b11565`, `ready:false`.
* `docs/model-source-1.2.1.md` — отметка о закрытии третьего артефакта.

Коммиты: `f04c240` (пин + tooling + CI + tests), `ecf90e9` (pod-команда, доказанная на Linux CI).
**Итоговый HEAD: `ecf90e9`** (запушен в `origin/integration/canalla-1.2.1`).

## 13. Production changes: NONE; GPU spend: $0

Gateway, 12Testers, nginx, тома, `main` и релизные теги не изменялись. Публикация 1.2.1 не
выполнялась. `RELEASE READY` не объявляется: нужен реальный запуск Qwen с `y92bt25ru8`.
