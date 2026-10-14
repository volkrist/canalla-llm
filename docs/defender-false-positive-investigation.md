# Defender и наши бинарники — разбор обнаружения (Canalla LLM)

Дата: 14.10.2026. Ветка `integration/canalla-1.2.1`. **Статус: НЕ объявляется false positive.**

## 1. Что именно обнаружил Defender

| | |
|---|---|
| Угроза | `Trojan:Win32/LummaStealer.DO!MTB` (ThreatID **2147979701**), severity **5** |
| Движок / сигнатуры | Engine `1.1.26090.9`, Signatures `1.461.20.0`, возраст 0 дней |
| Защита | RealTime = **True**, BehaviorMonitor = **True**, Tamper Protection = **True** |
| Детекций всего | **13**, все одного ThreatID, с 03.10.2026 18:59 по 14.10.2026 19:07 |
| Действие | `ActionSuccess = True` (файлы сняты/заблокированы), `!MTB` = облачно-эвристическая сигнатура |

Хронология (все ресурсы — **наши собственные** артефакты и их build-output):

```
03.10 18:59  …\Programs\Canalla LLM\alex-llm.exe  + ярлык + HKCU Run + HKCU Uninstall + процесс pid:16792
11.10 11:04…13:07  …\outputs\alex-llm\apps\desktop\src-tauri\{sidecar,target\release,target\debug}\alex-host-loop.exe
11.10 11:39  …\outputs\alex-llm\…\target\release\alex-llm.exe (×2)
11.10 13:07  …\outputs\Alex LLM_0.7.0.exe   (старый установщик 0.7.0)
14.10 19:00  …\Programs\Canalla LLM\sidecar\alex-host-loop.exe
14.10 19:07  …\Programs\Canalla LLM\alex-host-loop.exe (×2 — вызваны моими же попытками чтения файла)
```

**Ни один** ресурс из 13 не относится к постороннему ПО: единственное, что Defender ловит на этой
машине, — бинарники самого продукта. QuickScan (защита включена) новых находок не дал.

## 2. Следствие на диске

* `Programs\Canalla LLM\alex-llm.exe` — **удалён** (03.10), поэтому нет и записи деинсталляции.
* `Programs\Canalla LLM\sidecar\alex-host-loop.exe` — **удалён** (14.10).
* `Programs\Canalla LLM\alex-host-loop.exe` — **ещё лежит**, но любое чтение
  (`Get-FileHash`, `Get-AuthenticodeSignature`) отклоняется защитой:
  «Операция не была успешно завершена, так как файл содержит вирус…».

**Следствие: захешировать обвиняемые файлы на этой машине НЕВОЗМОЖНО** — одно удалено, другое
заблокировано на чтение. Восстанавливать из карантина запрещено заданием. Поэтому **сравнение
хэшей «локальный файл ↔ CI-артефакт» для обвиняемых файлов локально невыполнимо** — это не
«совпало», а «нечем сравнить».

## 3. Что удалось сравнить

Читаемые файлы (SHA256):

| Файл | Байт | SHA256 |
|---|---|---|
| установленная `sidecar\alex-backend\alex-backend.exe` | 23 741 190 | `3bf2dc6a7e65563d24ddb6a8afc9feb8eab027354abe046604939a69c802e0bb` |
| установленный `runtime\tor\tor.exe` | 10 222 592 | `60c45b01938c799862e511a9a5bab12f959a819c6264a24502edc342165f570c` |
| установленный `uninstall.exe` (prodver 1.2.0) | 90 338 | `9967bc371367e18aabad2d6bfc7b2152f9c508baeadbb47e7dbd92274f364851` |
| установщик **1.2.1** (CI, artifact `canalla-test-installer`) | 91 928 775 | **`24bc0a28e4b7f7252407be5aafcde239dfd02d7e7940d411bb35963f03fe8c29`** |
| установщик 1.2.0 (Desktop) | 95 340 864 | `61a99cfcb2fa768a5a4840431836909af2e705927daa4e489719b9943e3beac4` |
| установщик 1.2.0 (`%TEMP%\dlback`) | 92 459 017 | `2a6383ca3a43f3eedecbf4af0960b9a140581f726777a9a51da57d53e9428c64` |

Все — `NotSigned` (ожидаемо для тестовых сборок).

**Линия происхождения другая:** у CI 1.2.1 в логе сборки `alex-backend.exe` = **22 927 049** Б и
`tor.exe` = **10 237 952** Б (tor 15.0.25), а у установленной 1.2.0 — **23 741 190** и
**10 222 592** (tor 15.0.24). То есть обвиняемая установка — **не** наш артефакт 1.2.1.

### Хэши бинарников из CI (добавлены в лог сборки)

| Бинарник в CI-сборке 1.2.1 | Байт | SHA256 |
|---|---|---|
| `alex-llm.exe` | 5 956 608 | `a4d66d5c70c9e82b864a0f0e1a74ec2a71f5583e2829d070f05193fe55cd88b6` |
| `sidecar\alex-backend\alex-backend.exe` | 22 927 049 | `9e6a11455a64c249f0692b2b51af96977e331eab1c98ba0cc2cdbc6c4031a700` |
| `runtime\tor\tor.exe` | 10 237 952 | `8c38513b3c4584154158e7c584f4144849dc679a64cd679553d72dad28230c54` |
| **`sidecar\alex-host-loop.exe`** | **2 193 920** | **`7bb29e9a86638d53c9561f0313639c10dc34c2a4519210d4ffebc6a7719a4653`** |

Сравнение с обвиняемой установкой:

| | Обвиняемая (1.2.0, локальная линия) | CI 1.2.1 | Вердикт |
|---|---|---|---|
| `alex-host-loop.exe` | 2 167 808 Б (хэш **не читается**, Defender блокирует) | 2 193 920 Б, `7bb29e9a…` | **размеры разные** — это не тот же файл |
| `alex-backend.exe` | `3bf2dc6a…` | `9e6a1145…` | **хэши разные** |
| `tor.exe` | `60c45b01…` (15.0.24) | `8c38513b…` (15.0.25) | **хэши разные** |

Ни один CI-хэш не совпадает с обвиняемым файлом, и это ожидаемо: обвиняемая сборка старше.
`tor.exe` из CI, кстати, совпал с официальным `tor.exe` из бандла 15.0.25 — независимая сходимость.

## 4. Решающая проверка (защита включена)

Штатный on-demand скан CI-артефакта 1.2.1:

```
Start-MpScan -ScanType CustomScan -ScanPath "…\installer121\Canalla LLM_1.2.1_x64-setup.exe"
→ DETECTIONS_NAMING_THE_CI_ARTIFACT = 0
```

**Артефакт, который мы собираемся публиковать, не назван ни одной детекцией.** Оговорка: скан
проверил сам NSIS-файл, а не распакованные внутренности (локально нечем распаковать — 7-Zip нет).
Полный прогон CI на `3763444` при этом **7/7 PASS**, включая `installer-build`, который и печатает
хэши выше.

## 5. Вывод (без преждевременных ярлыков)

* Версия «это подтверждённый false positive» **не объявляется** — нужен разбор Microsoft.
* Факты: срабатывает только на **наших** файлах; CI-артефакт 1.2.1 чист; обвиняемая линия —
  старее (1.2.0 + локальные сборки 11.10).
* Две гипотезы, обе не исключены:
  1. ложно-положительная ML-сигнатура `!MTB` на неподписанных Tauri/Rust/PyInstaller бинарниках;
  2. компрометация **локального** сборочного хоста (или его окружения), из-за которой локальные
     сборки действительно заражены — тогда это не касается CI-артефактов, но требует расследования
     и очистки машины.

## 6. Заявка в Microsoft Security Intelligence (подготовлена, НЕ отправлена)

Отправляет **владелец** (я не отправляю сторонние файлы без согласия). Форма:
`https://www.microsoft.com/en-us/wdsi/filesubmission` → *Software developer* → **«I believe this
is a false positive»** → upload the file.

Что приложить: **CI-сборку** `alex-host-loop.exe` (её хэш теперь печатает CI — см. §7), а не файл с
заблокированной машины. Сопроводительный текст:

```
Detection: Trojan:Win32/LummaStealer.DO!MTB (ThreatID 2147979701), severity 5
Engine 1.1.26090.9 / Signatures 1.461.20.0, MTB (cloud ML)
Product: Canalla LLM (Tauri/Rust desktop app, PyInstaller sidecar), unsigned test build
Reproducible: yes, on unsigned builds of alex-llm.exe and alex-host-loop.exe
Detection is limited to our own binaries; no third-party file on the host is flagged.
We build the shipped artifact on GitHub-hosted runners (clean), not on the affected host.
Please confirm false positive or identify the actual malicious behaviour.
```

Если Microsoft подтвердит FP — можно просить добавление в allow-list по имени/издателю, а по-хорошему
публиковать **подписанные** сборки.

## 7. Что добавлено в CI для будущего сравнения

Сборка установщика теперь печатает `sha256` для `alex-llm.exe`, `sidecar\alex-backend\alex-backend.exe`,
`runtime\tor\tor.exe` и **`sidecar\alex-host-loop.exe`** — то есть у каждого артефакта появится
хэш обвиняемого файла, пригодный и для сравнения, и для заявки в Microsoft.
