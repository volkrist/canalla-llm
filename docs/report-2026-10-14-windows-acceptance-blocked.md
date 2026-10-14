# Canalla LLM 1.2.1 — Windows-приёмка: BLOCKED (Windows Defender)

Дата: 14.10.2026. Ветка `integration/canalla-1.2.1`. HEAD `0f4b6d6`.

**Итог: интерактивная GUI-приёмка на этой машине ЗАБЛОКИРОВАНА.** Windows Defender активно
удаляет **наши собственные** неподписанные бинарники как `Trojan:Win32/LummaStealer.DO!MTB`.
Отключать Defender или добавлять исключения задание прямо запрещает, поэтому PASS не объявляется.

## 1. Установщик (Задача 1)

| Проверка | Результат |
|---|---|
| Файл существует | **PASS** — `C:\Users\Volkr\AppData\Local\Temp\installer121\Canalla LLM_1.2.1_x64-setup.exe`, 91 928 775 Б |
| Версия 1.2.1 | **PASS** — из ресурсов самого файла: `ProductName=Canalla LLM`, `ProductVersion=1.2.1`, `FileVersion=1.2.1` |
| SHA256 | **PASS** — `24bc0a28e4b7f7252407be5aafcde239dfd02d7e7940d411bb35963f03fe8c29` |
| Происхождение | **PASS** — получен `gh run download` из artifact `canalla-test-installer` прогона **38476189049** (HEAD `83202e7`), т.е. это именно CI-артефакт |
| Содержит sidecar и Tor | **PASS (по CI-установке)** — в установленном дереве: `sidecar\alex-backend\alex-backend.exe` 22 927 049 Б и `runtime\tor\tor.exe` 10 237 952 Б (ровно tor 15.0.25). Локально NSIS-содержимое не перечислить: 7-Zip на машине отсутствует |
| Совместимость с 1.2.0 | **PASS** — тот же путь `%LOCALAPPDATA%\Programs\Canalla LLM`, то же имя продукта `Canalla LLM`, product id не менялся |
| Подпись | `NotSigned` — **ожидаемо** для тестовой сборки (production-ключ не читался) |

## 2. Данные и путь назад (Задача 2)

| | |
|---|---|
| Текущая установленная версия | **1.2.0** (`uninstall.exe` → ProductVersion `1.2.0`; sidecar 23 741 190 Б и tor.exe 10 222 592 Б — сборка старше CI-шной) |
| История и настройки | `%LOCALAPPDATA%\Alex LLM\data\alex.db` (634 880 Б, sha256 `4944feb8…`) + `alex.db-wal` (4 132 392 Б) + `-shm` + `device.json` |
| Backup | **`C:\Users\Volkr\canalla-1.2.0-acceptance-backup-20261014T100225Z`** — 53 файла, 193 995 488 Б, **совпадает** с оригиналом побайтово по сумме и числу файлов |
| Возврат к 1.2.0 | **`C:\Users\Volkr\Desktop\Canalla LLM_1.2.0_x64-setup.exe`** — 95 340 864 Б, ProductVersion 1.2.0, sha256 `61a99cfc…` |
| Ничего не удалено | подтверждено: только копирование |

## 3. Почему GUI-приёмка заблокирована (Задача 3)

Детекции Defender, называющие установленный продукт:

```
03.10.2026 18:59  threat=2147979701  Trojan:Win32/LummaStealer.DO!MTB  action=True
  file:  …\Programs\Canalla LLM\alex-llm.exe
  startup: …\Start Menu\Programs\Canalla LLM.lnk
  runkey:  HKCU\…\Run\Canalla LLM
  uninstall: HKCU\…\Uninstall\Canalla LLM
  process: pid:16792
14.10.2026 19:00  threat=2147979701  Trojan:Win32/LummaStealer.DO!MTB  action=True
  file:  …\Programs\Canalla LLM\sidecar\alex-host-loop.exe
```

Последствия, видимые на диске:

* в каталоге установки **нет `alex-llm.exe`**, а `alex-host-loop.exe` удалён сегодня;
* в HKCU/HKLM **нет записи деинсталляции** — Defender вычистил её 03.10 вместе с exe и Run-ключом;
* `DisableRealtimeMonitoring = False`, Tamper Protection включён (включён и реального времени).

То есть на этой машине установка 1.2.1 приведёт к тому же: Defender снимет `alex-llm.exe` и
`alex-host-loop.exe` немедленно, и все 14 пунктов приёмки станут невыполнимыми. Скорее всего это
**ложное срабатывание** на неподписанных PyInstaller/Rust-бинарниках (та же сигнатура ловится и в
наших собственных build-output'ах с 11.10), но проверять это «обходом защиты» задание запрещает.

**Все 14 пунктов GUI-приёмки: BLOCKED** — не FAIL продукта, а невозможность проверки на этой машине.

## 4. Что нужно, чтобы разблокировать (решение владельца, без обхода защиты)

1. **Чистая машина/VM** без этой истории детекций — приёмка по
   `docs/acceptance-windows-1.2.1.md`, установщик из artifact `canalla-test-installer`.
2. Либо **подписанный** установщик (production signing) — тогда сигнатура и репутация снимают
   ложное срабатывание естественным путём, без отключения Defender.
3. Либо владелец сам, осознанно и вручную, решает вопрос с Defender на СВОЕЙ машине — я этого не
   делаю и не предлагаю обходить.

## 5. Готовность Gateway 1.2.1 (Задача 5, read-only, деплой НЕ выполнялся)

| Пункт | Состояние |
|---|---|
| version | production **1.2.0**; сборка/репозиторий **1.2.1** |
| bootstrap_version (ожидание реплики) | production env: `1.2.0`; репозиторий: `1.2.1` → **при редеплое запись надо обновить вместе с Gateway** |
| runpod_runtime_version | **пусто** → версия рантайма не проверяется (можно оставить странным/пустым) |
| RUNPOD_REPLICAS | содержит ровно одну запись — US-MO-1 `y92bt25ru8`; запись TX-3 **не** добавляется (иначе primary отвергнется) |
| Network Volumes | `uwgeaie5b0` (US-TX-3) и `y92bt25ru8` (US-MO-1) — оба целы |
| Старый клиент | `gateway_protocol_version = 1` не менялся; `/health` форма та же → 1.2.0-клиент совместим |
| A10/A11 | `roles: [('member', 41)]` — миграция `0005` никого не повысила; entitlement-потолки действуют |
| migration 0006 | применена (`alembic_version = 0006_capacity_wait`) |

Готово к редеплою Gateway на `0f4b6d6` (или `83202e7`) — **но только по отдельному подтверждению**.
До редеплоя US-MO-1 будет **пропускаться** (fail-closed: ожидание 1.2.0 ≠ запись 1.2.1), US-TX-3 не
затрагивается.

## 6. Итог

| | |
|---|---|
| Тестовый установщик 1.2.1 | **корректен** (версия, размер, происхождение, содержимое по CI) |
| Данные | **сохранены**, бэкап проверен, путь возврата к 1.2.0 зафиксирован |
| GUI-приёмка | **BLOCKED** (Windows Defender удаляет наши неподписанные бинарники) |
| Исправления кода | **не требовались** (дефектов продукта не найдено) |
| Готовность к Gateway deploy | **готова**, ждёт подтверждения |
| Готовность к публикации 1.2.1 | **не готова** — нужна GUI-приёмка на чистой машине или подписанный установщик |

**Production changes: NONE. GPU/CPU spend: $0. Публикация: не начиналась.**
