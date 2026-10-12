# Проверяемый источник модели и контракт запуска (1.2.1)

Статус: **источник GGUF и оба runtime-скрипта теперь известны и проверяемы.**
Незакрытым остаётся третий артефакт — собранный `llama-server`.

## 1. GGUF (веса)

| | |
|---|---|
| Репозиторий | **`bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF`** |
| Файл | **`orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf`** |
| Размер | **20 752 787 712 байт** |
| LFS SHA256 | **`4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1`** |
| Ревизия (commit) | `87d37daf5e5eb72a926d8b413e08809a57f1a120` |
| Лицензия | `apache-2.0` |
| Доступ | **не gated** (`"gated": false`) — скачивается без токена |

**Почему именно этот источник считается продакшн-источником:**
* имя файла **точно совпадает** с обязательным путём в `remote_runtime.py`
  (`/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf`), включая
  префикс `orcarouter_` — это соглашение именования bartowski, а не gated-репозитория
  `orcarouter/Qwen3.8-27B-Uncensored-GGUF` (там файл называется `Qwen3.8-27B-Uncensored-Q5_K_M.gguf`,
  19 535 701 376 байт — **другой** файл);
* размер **20 752 787 712** байт побайтово совпадает с записанным в самом репозитории маркером
  `CANALLA_M_GGUF_BYTES=20752787712` (`scripts/watch-tx3-migration-capacity.py`, `finish-secondary-replica.py`).

> **Оговорка.** Это подтверждает размер и происхождение, но **не** доказывает побайтовое равенство
> копии на `uwgeaie5b0`, пока первичный том не прочитан. LFS SHA256 выше — авторитетный хэш
> **источника**; при появлении хэша оригинала выполняется дополнительная сверка.

Gated-репозиторий `orcarouter/Qwen3.8-27B-Uncensored-GGUF` **не используется**: он требует
авторизации и содержит другой файл.

## 2. Runtime-скрипты — уже в репозитории

`artifacts/runpod-tx3-watcher/start-llm.sh` и `artifacts/runpod-tx3-watcher/check-llm.sh` содержат
**фактический** контракт запуска:

* сервер: `/workspace/llama.cpp/build/bin/llama-server`;
* модель: `/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf`;
* флаги: `--alias orcarouter-qwen38-27b-q5km --n-gpu-layers all --ctx-size 32768 --parallel 1
  --flash-attn on --jinja --host 127.0.0.1 --port 8080`;
* health: `curl -sf http://127.0.0.1:8080/v1/models`; pid в `/workspace/llama-server.pid`.

Регрессия контракта: `apps/backend/tests/test_launch_scripts_contract.py`.

## 3. Незакрытый третий артефакт

`start-llm.sh` выполняет **`/workspace/llama.cpp/build/bin/llama-server`** — собранный llama.cpp на
томе. Его **версия неизвестна** и в git его нет. Поэтому:

* загрузка GGUF + запись двух скриптов — **воспроизводимо** (нужен только файл модели);
* **побайтовая реплика стека запуска не доказана** без llama.cpp с первичного тома.

Полный эквивалент требует либо чтения `uwgeaie5b0` (ёмкость `US-TX-3`), либо сборки llama.cpp с
неизвестной версией — это **новая независимая конфигурация запуска**, а не побитовая реплика.

> **Обновление (12.10.2026).** Третий артефакт закрыт: `scripts/llama-runtime.json` пинует
> официальную сборку `ggml-org/llama.cpp` **b11565** (commit `f9a95aeb…`, CUDA 12.8, linux-x86_64),
> а `scripts/provision-llama-runtime.py` скачивает её, проверяет по размеру и SHA256 и
> раскладывает в `/workspace/llama.cpp/build/bin/`. Это по-прежнему **не** побитовая реплика
> старого бинарника, а новая воспроизводимая конфигурация запуска; см.
> [docs/report-2026-10-12-llama-cuda-runtime.md](report-2026-10-12-llama-cuda-runtime.md).
