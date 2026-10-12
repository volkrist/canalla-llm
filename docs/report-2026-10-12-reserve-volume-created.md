# CANALLA LLM 1.2.1 — РЕЗЕРВНЫЙ ТОМ СОЗДАН; КОПИЯ МОДЕЛИ ЗАБЛОКИРОВАНА НА US-TX-3

**Статус: один резервный Network Volume создан и подтверждён. Копия модели НЕ выполнена —
блокирована отсутствием ёмкости в `US-TX-3`. Платных Pod'ов не создано.**

Ветка `integration/canalla-1.2.1`, исходный HEAD `40282d9`. Дата: 12.10.2026.

---

## 1. Проверка ресурсов (до создания, read-only)

| Проверка | Результат |
|---|---|
| Network Volumes до | только `uwgeaie5b0` (US-TX-3, 50 GB STANDARD) — резервного тома не было |
| Pods | **`0`** |
| Баланс (GraphQL `myself.clientBalance`) | **$14.83** |
| `currentSpendPerHr` | $0.005 |
| `US-MO-1` network volume types | **`['STANDARD']`** — создание подтверждено |
| Схема create | `POST /v2/network-volumes {name, size(10–4096), dataCenter, type}` |
| Тариф STANDARD | **$0.07/GB-мес** (документированный) |

## 2. Созданный том

| | |
|---|---|
| **volume_id** | **`y92bt25ru8`** |
| name | `orcarouter-storage-mo1` |
| datacenter | **`US-MO-1`** |
| size / type | **50 GB / STANDARD** |
| HTTP create | **201** |
| Подтверждение | повторный `GET /v2/network-volumes` @ 11:29:30Z показывает оба тома |
| Стоимость | **+$3.50/мес** (итого хранение $7.00/мес) |
| Первичный `uwgeaie5b0` | **не изменён** |

## 3. Копирование модели — БЛОКИРОВКА

Требуются три артефакта: GGUF + `start-llm.sh` + `check-llm.sh`.

**GGUF — источник НАЙДЕН (новое):** публичный HuggingFace-репозиторий
**`orcarouter/Qwen3.8-27B-Uncensored-GGUF`**, файл **`Qwen3.8-27B-Uncensored-Q5_K_M.gguf`,
19 535 701 376 байт ≈ 18.2 GiB**. Это снимает пункт «источник модели неизвестен» из
`docs/report-2026-09-25-replica-reconstruction-verdict.md` **для весов**. **50 GB достаточно**
(модель 18.2 GiB + скрипты + состояние llama.cpp).

**`start-llm.sh` / `check-llm.sh` — только на первичном томе.** Продукт их **не генерирует**
(`remote_runtime.py` и `runpod_api.py` только `test -f` и `bash` их запускают; в git и во всех
worktrees содержимого нет). Их можно прочитать **только** через compute, смонтировавший
`uwgeaie5b0`.

**Почему это блокирует:**

* **CPU-Pod'ов в `US-TX-3` НЕТ.** Живой каталог `GET /v2/catalog/cpus` (product=POD): 16 ДЦ,
  `US-TX-3` отсутствует; serverless-CPU — 15 ДЦ, `US-TX-3` тоже нет.
* **GPU в `US-TX-3` сейчас НЕТ** (проверка 11:29:30Z — ни одного оффера ≥48 GB, ни любой карты).
* `US-TX-3` не входит в 15 S3-совместимых ДЦ ⇒ S3-ключом первичный том не прочитать.

Это ровно то, что уже зафиксировал `docs/report-2026-09-25-replica-reconstruction-verdict.md`
(«NOT RECONSTRUCTABLE — MUST WAIT FOR US-TX-3 CAPACITY»). Предположение задания о «временном CPU
Pod в TX-3» технически невозможно.

## 4. Варианты копирования и точные цены (НЕ выполнены — нужно разрешение)

| # | Способ | Нужен Pod | Цена | Что даёт |
|---|---|---|---|---|
| 1 | VPS скачивает GGUF из HF → S3-upload в `y92bt25ru8` (`s3api-us-mo-1.runpod.io`) | **нет** | **$0 compute**; владелец должен создать **S3 API key** в консоли (`Credentials → S3 API Keys`) | только веса |
| 2 | **CPU-Pod в `US-MO-1`** качает GGUF из HF прямо на том | да, CPU | **cpu3c $0.03/vCPU** (минимум 2 vCPU → **$0.06/ч**); 4 vCPU 8 GB = **$0.12/ч**; ≈20–30 мин ⇒ **$0.02–$0.06** | только веса |
| 3 | **GPU-Pod в `US-TX-3`** (когда появится ёмкость): монтирует первичный том, копирует GGUF+оба скрипта на `y92bt25ru8` по плану репозитория (HTTP `busybox httpd` → `wget -c`, **без SSH и без S3-ключа**; второй Pod — CPU в `US-MO-1`) | да, GPU + CPU | L40S **$1.09/ч** (когда в наличии); ≈15–25 мин ⇒ **≈$0.3–$0.5** | **полная реплика** (веса + скрипты) |

**Полная реплика возможна только вариантом 3** (пока в `US-TX-3` не появится карта). Варианты 1–2
дают только веса и ускоряют будущую полную копию.

**GPU-Pod создавать только после появления ёмкости в `US-TX-3`; 20-минутного ожидания нет.**

## 5. Replica record — подготовлен, но `ready:false`

`ready:true` **не ставится**: скрипты не скопированы и SHA256 не сверён с первичным томом (прочитать
его нельзя без TX-3 compute). Шаблон (значения заполняются после копии):

```
{"volume_id":"y92bt25ru8","datacenter":"US-MO-1",
 "model_id":"<llm_model>","model_path":"/workspace/models/orcarouter-qwen38/orcarouter_Qwen3.8-27B-Uncensored-Q5_K_M.gguf",
 "model_sha256":"<SHA256 копии>","model_bytes":<bytes>,
 "runtime_version":"<runpod_runtime_version>","bootstrap_version":"<gateway version>",
 "last_verified":"<ISO8601Z>","ready":false}
```

## 6. Placement — подготовлен, НЕ включён

```
RUNPOD_DATACENTERS=US-TX-3:uwgeaie5b0,US-MO-1:y92bt25ru8
RUNPOD_REPLICAS=<record выше>
```

Не применяется на production: развёрнутый Gateway (release `1994c0ab2016`, 26.09) старше нашего кода
и не читает `RUNPOD_DATACENTERS`/`RUNPOD_REPLICAS`; включение — только при деплое 1.2.1. FakeRunPod
уже подтверждён (26 passed) для второго placement.

## 7. Что дальше (бесплатно)

Проверять ёмкость `US-TX-3` read-only:

```
python scripts/secondary-dc-discovery.py --exclude NONE
```

Как только `US-TX-3` появится — вариант 3 (измерение + копия с фиксацией размера и SHA256).
Если владелец создаст S3 API key — параллельно возможен вариант 1 (веса, $0).

## 8. Production changes: NONE; GPU spend: $0; Pods: 0
