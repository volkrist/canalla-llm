# CANALLA LLM 1.2.1 — МОДЕЛЬ ЗАГРУЖЕНА НА РЕЗЕРВНЫЙ ТОМ (CPU POD)

Статус: **GGUF загружен и проверен по SHA256 на `y92bt25ru8`; оба runtime-скрипта записаны;
временный CPU Pod удалён и подтверждён gone. GPU не запускался.**

Ветка `integration/canalla-1.2.1`. Дата: 12.10.2026. Новый том не создавался, первичный не тронут.

## 1. Временный CPU Pod

| | |
|---|---|
| Pod ID | **`064c6wfd5ulis1`** |
| name | `canalla-reserve-copy` |
| datacenter | `US-MO-1` |
| flavor / vCPU / RAM | `cpu3c` / 4 / 8 GB |
| ставка | **$0.03/vCPU/ч** → **$0.12/ч** (подтверждено живым каталогом) |
| Network Volume | `y92bt25ru8` → `/workspace` |
| создан | **2026-10-12T11:45:25Z** |
| удалён (confirmed gone) | **2026-10-12T11:55:35Z** |
| длительность | **≈10 мин 10 с** |
| фактические расходы | баланс $14.8336 → **$14.8159**, т.е. **≈$0.018** |

## 2. Аварийная остановка (watchdog)

До/сразу после создания поднят **независимый** watchdog: systemd transient timer
`canalla-cpupod-watchdog.timer` (`/home/alex/watchdog.sh 064c6wfd5ulis1`), `--on-active=2700`,
**активен (waiting)**, trigger 12:30:32Z. Не зависит от SSH и агента, удаляет **только** этот Pod,
тома не трогает, журнал — `/home/alex/canalla-cpupod-watchdog.log`.
Под удалился раньше срока по подтверждённому штатному пути; после подтверждения timer снят
(`systemctl stop` + `reset-failed`) — активных canalla-таймеров нет.

## 3. Модель

| | |
|---|---|
| Источник | **`bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF`** (не gated) |
| Ревизия (pinned) | **`87d37daf5e5eb72a926d8b413e08809a57f1a120`** |
| Файл | `orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf` |
| Скачано байт | **20 752 787 712** (совпадает) |
| SHA256 скачанного | **`4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1`** |
| Ожидаемый LFS SHA256 | `4ad98832…` — **совпадает** |
| Итоговое имя на томе | `/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf` |

Маркеры: `CANALLA_R_MOUNT_OK`, `CANALLA_R_TOOLS_OK`, `…_SCRIPT_OK`×2, `CANALLA_R_BYTES 20752787712`,
`CANALLA_R_SHA256 4ad98832…`, `CANALLA_R_MODEL_OK`, `CANALLA_R_FINAL_BYTES 20752787712`.

## 4. Скрипты

Записаны дословно из репозитория по base64 (`artifacts/runpod-tx3-watcher/…`):
`/workspace/start-llm.sh` и `/workspace/check-llm.sh` (`…_SCRIPT_OK`).

## 5. llama.cpp — незакрытый пункт

`start-llm.sh` запускает `/workspace/llama.cpp/build/bin/llama-server`, которого **нет** в виде
исходников и чья **версия неизвестна**. Значит:
* GGUF и скрипты на томе — **воспроизводимы и проверены**;
* **побитовая реплика стека запуска не доказана**; это **новая конфигурация запуска**, требующая
  сборки llama.cpp (или чтения `uwgeaie5b0` при ёмкости `US-TX-3`) и реального теста запуска.

## 6. Replica record

`docs/replica-us-mo-1.json` (`volume_id=y92bt25ru8`, `ready:false`). Продуктовый парсер
(`app.compute.replicas`) читает его как вердикт **`partial`** ⇒ placement **не будет использован**
в автоматическом выборе. `ready:true` — только после доказанного запуска модели.

## 7. Финальная проверка

* `GET /v2/pods` → **`pods: []`** (активного оплачиваемого compute нет).
* Тома: `uwgeaie5b0` (US-TX-3) и `y92bt25ru8` (US-MO-1) — **оба на месте**.
* Расход за операцию ≈ **$0.018**.

## 8. Production changes: NONE; GPU spend: $0; Pods: 0
