# Canalla LLM 1.2.0 — состояние релиза (26.09.2026)

**Ветка:** `release/canalla-1.1.0` · **HEAD на момент записи:** см. `git log -1`
**Публикация:** **НЕ выполнена** — манифест не опубликован, `main` не смёржен, тег `v1.2.0` не создан.
**Решение о placement'ах:** 1.2.0 выходит с **ОДНИМ** production placement'ом. Вторичный том отложен
(баланс RunPod ниже платформенного минимума для создания Network Volume).

## Production placement 1.2.0

| Параметр | Значение |
|---|---|
| DC | `US-TX-3` |
| Network Volume | `uwgeaie5b0` (STANDARD, 50 GB) |
| Модель | `/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf` |
| Байты | `20752787712` |
| SHA256 | `4ad98832b3d376dd274df5bba59dd34834f0f401593e1463f38fd5fa219293b1` |
| `start-llm.sh` | 957 B · `d91b7e36e3298a7df7e70d851e3c83ab3363e836721832dea0158de90f0db6d8` |
| `check-llm.sh` | 264 B · `9f26c444eed3d49d55d0e6f781708f19b836bc47185d5421e7534c0187bf7be5` |
| Конфигурация | `RUNPOD_DATACENTERS` **не задан**, `runpod_replicas=""` → placement = ДЦ тома (дефолт) |

## Приёмка, выполненная в этом проходе

| Гейт | Результат |
|---|---|
| Backend `pytest tests` | **780 passed**, 1 skipped; stale-sidecar guard после пересборки — **PASS** (`test_sidecar_stamp.py`: 11 passed) |
| Gateway `pytest tests` | **200 passed** |
| Desktop frontend `vitest` | **274 passed** (22 файла) |
| Desktop rust `cargo test` | **95 passed** (21 + 58 + 16), 0 failed |
| Скрипты | watcher `25/25`, reaper `16/16`, finish-replica `9/9` |
| Sidecar (пересобран один раз) | `product_version 1.2.0` · exe SHA256 `fa46d07945af48b95fece98d2e934e7d6e42836c3df84f17ebdadef3d841a997` · 23 571 704 B · stamp **PASS** |
| Sidecar standalone (урезанный PATH) | `/health` → `version 1.2.0`; `PYTHON_REQUIRED NO`; `REPO_REQUIRED NO`; network_route присутствует |
| Установщик | `Canalla LLM_1.2.0_x64-setup.exe` · **92 493 319 B** · SHA256 **`ae863e92041f1cd3ecf20748c4298b7b0566f599eb39321b285a40620b971929`** |
| Подпись апдейтера | продакшн-ключ **`9B328EFF111D1FB2`** · **FILE SIGNATURE: PASS** (Ed25519 над BLAKE2b-512 установщика) |
| Authenticode | **NOT SIGNED** (сертификата нет — не заявляем обратное) |
| Установка поверх существующей | exit 0 · `Programs\Canalla LLM\alex-llm.exe` = **1.2.0** · данные `%LOCALAPPDATA%\Alex LLM\` не тронуты |
| Установленная приёмка (Computer/Tor) | **ALWAYS READY PASS** — зелёные при запуске, после рестарта и после падения sidecar; одно устройство, тот же `device_id`, credential isolation соблюдена |
| Установленный `/health` | `version 1.2.0`, модель не поднята → AI Disconnected (корректно) |
| Gateway (развёрнут) | `/health` **200**, `{"version":"1.2.0","ready":true,"database":"ok"}`; `/updates/latest` → **204** (манифест не опубликован) |
| Хостинг артефакта | `/srv/canalla-downloads/` (nginx `/downloads/`): установщик + `.sig`; предыдущий кандидат 1.2.0 заменён; stale `SHA256SUMS.txt` удалён; **SHA256 на сервере == принятому локальному** |

## НЕ выполнено (и почему)

| Шаг | Состояние |
|---|---|
| **Живая AI-приёмка + Tor (единственный оставшийся release-gate)** | **PENDING — отложена до разрешения оператора на разовые ≈$0.30–0.40 трат на RunPod.** Наблюдаемый кандидат: **US-TX-3 · L40S 48 GB · $1.09/ч** (в пределах ≤$2/ч). Pod не запускался |
| Живая AI-приёмка на GPU (секция 6 задания) | **НЕ ЗАПУСКАЛАСЬ** — не влезла в этот проход. Ёмкость есть: L40S 48 GB, $1.09/ч, LOW в US-TX-3 (≤$2/ч ✓). Отмечать PASS нельзя |
| Tor на том же Pod'е | **НЕ ЗАПУСКАЛСЯ** (нет Pod'а из предыдущего пункта) |
| Обратная выгрузка артефакта (download-back) | Не завершилась: 92 MB через Cloudflare с этой машины идут слишком медленно. Серверный `sha256sum` совпал с локальным |
| Git: commit → merge `main` → push → tag `v1.2.0` | **НЕ ВЫПОЛНЕНО** |
| Публикация updater-манифеста (последнее мутирующее действие) | **НЕ ВЫПОЛНЕНО** |
| Пост-публикационная проверка | **НЕ ВЫПОЛНЕНА** |

## Историческая оговорка про апдейты

Продакшн-идентичность подписи создана 24.09.2026, а `v1.0.0` выпущен 22.09.2026 — значит установки
**1.0.0** несут другой (до-продакшн) публичный ключ и **не могут** криптографически авто-обновиться на
1.2.0: им нужен ручной bootstrap-переход. Клиенты, собранные текущим деревом (ключ `9B328EFF111D1FB2`),
обновляются с проверкой подписи штатно.

## Порядок оставшихся действий (строго)

1. живая AI-приёмка + Tor на **одном** GPU Pod (≤$2/ч, ≤20 мин), затем немедленная остановка, AI = Disconnected;
2. завершить download-back проверку артефакта;
3. commit финального состояния → merge в `main` → push → аннотированный тег `v1.2.0` → push тега;
4. **последним** — опубликовать манифест 1.2.0 (Windows) и выполнить read-only пост-проверку.
