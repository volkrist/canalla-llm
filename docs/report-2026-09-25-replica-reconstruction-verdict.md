# CANALLA LLM — REPLICA RECONSTRUCTION VERDICT

**Задание:** можно ли воссоздать точную продакшн-реплику модели/runtime из авторитетных источников,
**не читая** первичный том `uwgeaie5b0`.
**Дата:** 25.09.2026 · **Ветка:** `release/canalla-1.1.0` · **HEAD:** `947976d`
**Потрачено:** **$0** (только чтение: репозиторий, история git, 6 worktrees, локальная машина, живой
каталог провайдера). Pod'ы не создавались, том не покупался, Canalla не пересобиралась.

---

# FINAL: NOT RECONSTRUCTABLE — MUST WAIT FOR US-TX-3 CAPACITY

Три критических артефакта существуют **только на томе** и не воспроизводимы из источников:

1. **сам GGUF** — нет ни источника, ни ревизии, ни хэша, ни размера ни в одном месте;
2. **`start-llm.sh`** — отсутствует в системе контроля версий;
3. **`check-llm.sh`** — отсутствует в системе контроля версий.

Без (2) и (3) даже скачанная «та же» модель не даст production-реплику: именно эти скрипты задают путь
к модели, флаги, порт и параметры загрузки. Без записанного хэша нельзя доказать, что байты совпадают с
тем, что продакшн реально обслуживает.

---

# MODEL

```
Exact model:      orcarouter/Qwen3.8-27B-Uncensored  (квант Q5_K_M, алиас orcarouter-qwen38-27b-q5km)
Exact GGUF:       /workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf
Source:           UNKNOWN — ни URL, ни HF-репозитория, ни организации в проекте не зафиксировано
Revision:         UNKNOWN
Expected bytes:   UNKNOWN
Expected SHA256:  UNKNOWN  (не изобретён — именно неизвестен)
```

Где вообще встречается идентичность модели (и чего там **нет**):

| Место | Что содержит | Источник загрузки / хэш |
|---|---|---|
| `AGENTS.md` | `orcarouter/Qwen3.8-27B-Uncensored` Q5_K_M, алиас | — |
| `apps/backend/app/compute/remote_runtime.py` | точный путь к GGUF (обязательное условие старта) | — |
| `apps/backend/app/config.py` | `llm_model = orcarouter-qwen38-27b-q5km` | — |
| `docs/architecture.md` §124 | «**User-provided reference** … The controller mounts that existing volume and invokes those existing scripts. **No model download or volume deletion is implemented.**» | — |
| `docs/*.md` (много сессий) | «volume `uwgeaie5b0` preserved» — том только монтировался | — |

Поиск вёлся по: рабочему дереву, `docs/`, `scripts/`, `apps/`, **всей истории git всех ветвей**
(`git log --all -S` по `Q5_K_M`, `start-llm.sh`, `hf_hub_download`, `huggingface`, `llama-server`),
**шести worktrees** (`alex-llm-eval`, `-product`, `-final-verify`, `-repair-verify`,
`-three-blockers-verify`), файлам с любым 64-hex хэшем. **Совпадений по источнику модели нет.**

---

# RUNTIME

```
llama.cpp version:            UNKNOWN (ни сборки, ни тега, ни даты; в отчётах не зафиксировано)
start-llm.sh:                 MISSING  (существует только на uwgeaie5b0)
check-llm.sh:                 MISSING  (существует только на uwgeaie5b0)
startup parameters:           известен только КОНТРАКТ, не содержимое скриптов:
                              --ctx-size == LLM_CONTEXT_WINDOW (32768), --parallel 1,
                              внутренний порт 8080, публикуется 9000/http через обёртку с ключом,
                              readiness = точный алиас модели из /v1/models
All required runtime reproducible:  NO
```

Проверка по существу: `git ls-files | grep -i "llm.sh\|\.sh$"` возвращает **только четыре
не относящихся к делу скрипта** (`stage-native-runtime.sh`, `alex-gateway-backup.sh`,
`docker-entrypoint.sh`, `acceptance-linux-runtime.sh`). `start-llm.sh` и `check-llm.sh` упоминаются
исключительно как **пути** в коде и документации — ни одного коммита с их содержимым нет,
ни в одной из шести рабочих копий их тоже нет.

Единственная детерминированная загрузка в продукте — это **embedding-модель для RAG**
(`app/documents/model_manifest.py`: `REPO` + `REVISION` + `ARTIFACTS`, скачивание через
`hf_hub_download`). Это **другой артефакт**: она не имеет отношения к обслуживаемому GGUF, и её
наличие не помогает воссоздать модель.

---

# HISTORICAL EVIDENCE

```
Original provisioning method:   UNKNOWN — вне проекта. docs/architecture.md называет том
                                «user-provided»; за всю историю продукт его только монтировал.
Current volume contents reproducible:  NO
Unknown critical files:         orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf,
                                /workspace/start-llm.sh, /workspace/check-llm.sh
```

Дополнительные проверки (все — только чтение):

| Где искали | Результат |
|---|---|
| История git (все ветви, `-S` по ключевым словам) | никаких скриптов провижининга, только упоминания путей |
| Шесть worktrees | те же файлы кода/доков, провижининга нет |
| Локальная машина: любые `*.gguf` | **ноль** файлов |
| Локальная машина: любые файлы >5 GB | только `ext4.vhdx` (WSL) и `Ubuntu-Desktop.vdi` (VirtualBox) |
| HF-кэш (`~/.cache/huggingface/hub`) | отсутствует |
| PowerShell history (фильтр без секретов) | только посторонние строки про `volumes:` из docker-compose |
| Все 64-hex хэши в репозитории | относятся к бинарям (установщик, sidecar, host-loop, gateway) — **ни одного к модели** |

---

# SECONDARY

```
Current best DC:      CA-MTL-3  (ИЗМЕНИЛСЯ: US-NE-1 потерял доступную карту в течение часа)
Available GPU:        NVIDIA A100 80GB PCIe, 80 GB, $1.59/h, stock=LOW, STANDARD volumes ✓
50 GB sufficient:     UNKNOWN (размер реплики не измерен)
Expected storage:     $0 сейчас; $3.50/мес за 50 GB STANDARD, когда разблокируем
```

Живая перепроверка (read-only) подтвердила ваше предупреждение о нестабильности ёмкости:

| ДЦ | Карта | $/ч | Сток | Network Volume | Итог |
|---|---|---|---|---|---|
| **CA-MTL-3** | A100 PCIe 80 GB | 1.59 | LOW | **STANDARD** | **лучший сейчас** |
| US-NE-1 (прошлый выбор) | — | — | **карт ≥48 GB ≤$2 нет** | STANDARD | больше не годится |
| EU-SE-1 | A40 48 GB | 0.49 | LOW | нет (`[]`) | не годится |
| EUR-IS-2 | L40 48 GB / PRO 6000 MIG | 0.82 / 1.09 | LOW | нет (`[]`) | не годится |
| US-MD-1 | A100 SXM 80 GB | 1.59 | LOW | нет (`[]`) | не годится |

Вывод §7 соблюдён: **перед любой будущей покупкой ДЦ перепроверяется заново**, прошлый выбор не
считается действующим.

---

# ЧТО СДЕЛАЛО БЫ РЕПЛИКУ ВОСПРОИЗВОДИМОЙ

Ровно три возможности, по возрастанию усилий:

1. **У вас есть первоисточник** — URL/HF-репозиторий с ревизией, или сам файл модели на другом
   носителе/машине. Тогда: скачать в новый том в выбранном ДЦ + воссоздать два скрипта (см. п. 2) +
   записать хэш в `RUNPOD_REPLICAS`.
2. **Содержимое `start-llm.sh` и `check-llm.sh`** — их можно прочитать **только** через compute,
   смонтировавший том: консоль RunPod не имеет файлового браузера тома, а S3-совместимый API работает
   лишь в 15 перечисленных ДЦ, и **US-TX-3 в список не входит**. То есть этот пункт тоже упирается в
   ёмкость US-TX-3.
3. **Дождаться ёмкости в US-TX-3** — тогда измерение и копирование проходят точно и без догадок.

Практический вывод: **пункт 3 остаётся единственным надёжным путём**, а пункт 1 может его ускорить,
если у вас есть исходники модели вне RunPod.

---

# ЧТО ДАЛЬШЕ (ничего платного)

1. **Не покупать вторичный том** — размер реплики не измерен (§6 задания: сначала отчёт, покупка
   только после).
2. **Проверять US-TX-3 бесплатно** — одна read-only команда:
   ```
   python scripts/secondary-dc-discovery.py --exclude NONE
   ```
   Как только US-TX-3 появится в каталоге — измерение и копирование по готовому плану
   (HTTP `busybox httpd` → `wget -c`, без SSH, S3-ключа и ручных действий).
3. **Если у вас есть модель вне RunPod** — сообщите путь/URL, и я соберу реплику в выбранном ДЦ, после
   чего останется только воссоздать два скрипта (п. 2 выше).

`running pods = 0` · том не куплен · `uwgeaie5b0` не тронут · `main`/тег/манифест без изменений.
