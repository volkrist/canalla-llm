# Canalla LLM 1.2.6 — полный отчёт о выполнении задания

**Задание:** финальный LIVE-прогон T4 на настоящей Qwen → автоматический выпуск подписанной 1.2.6 → проверка обновления и данных.
**Дата выполнения:** 12 ноября 2026 (UTC).
**Репозиторий:** `volkrist/canalla-llm`, ветка `release/canalla-1.2.4`.
**Релизный SHA:** `0b7af14ba8116bf92666360f1a02c2ca5f7af375` (annotated tag `v1.2.6`).
**HEAD ветки после закрытия цикла:** `f6cd310` (только инструментарий релиза, evidence и отчёт; runtime-код не менялся).
**Продуктовый коммит сборки:** `b5b24f0` — с него `apps/` менялись только в двух тестовых файлах.
**Итог:** T4 LIVE **PASS**, 1.2.6 **PUBLISHED**, updater **PASS**, Pod остановлен, расход **$1.3637–$1.3734** из $1.45.

---

## 0. Резюме

| Вопрос | Ответ |
|---|---|
| T4 LIVE пройден? | **Да.** Реальная Qwen, L40S 48 GB, задача `758c3e1b…09fc` в состоянии `COMPLETED` |
| Сколько model-origin вызовов? | **3** (`read_file → write_file → read_file`), все подтверждены, все выполнены |
| Computer реально отключался? | **Да.** Приостановка процесса, `WAITING_DEVICE`, `host_offline`, heartbeat исчезал |
| Задача продолжилась после Resume? | **Да,** та же самая, с новыми подтверждениями после восстановления |
| Файлы? | `t4l_out.txt` побайтово равен `t4l.txt`, sha256 `1b88c6e7…c51d8`, `files_lost: []` |
| Релиз опубликован? | **Да.** GitHub Release `v1.2.6`, 3 ассета, анонимная загрузка сверена |
| Updater работает? | **Да.** 1.2.3/1.2.4/1.2.5 → 1.2.6 (200), 1.2.6 → 204, скачивание 95 540 283 байт с верным SHA |
| Данные сохранены? | **Да.** integrity `ok`, только рост: 2 / 54 / 120 / 59 |
| Pod? | **0 работающих**, `pods []` |
| Деньги? | **$1.3637** на момент сверки, **$1.3734** на контрольном чтении, лимит **$1.45** |

---

## 1. Исходная точка

К началу этого задания уже были подтверждены и **не перепроверялись** (регрессии нет):

- D2 LIVE — PASS;
- Long Autonomy LIVE — PASS, 21 model-origin вызов (8 ноября);
- T3 LIVE — PASS, 9 model-origin вызовов;
- T3 FREE, T4 FREE — PASS;
- Tor 16.0.1 / daemon 0.4.9.16 — PASS;
- Developer Gate, backend 1086 passed / 1 skipped, CI 7/7;
- подписанный кандидат 1.2.6 собран и установлен.

Открытым был **один** обязательный гейт — T4 LIVE. Именно он и был выполнен.

---

## 2. Хронология (UTC)

| Время | Событие |
|---|---|
| 10 ноя 11:17 | Открыт цикл координатора, зафиксирован стартовый баланс $10.1309 |
| 10–11 ноя | 5 Pod: T3L PASS, T4L FAIL (дефект harness), T4L+LA INTERRUPTED, T4L+LA MIXED, T4L+LA MIXED |
| 12 ноя 09:55 | Владелец поднял потолок до $1.45 и разрешил 6-й Pod; состояние обновлено (`pods_created=5`, `max_pods=6`) |
| 12 ноя 10:27:39 | Старт последнего T4 LIVE |
| 12 ноя 10:29:23 | Запущен процесс Computer (`pid 10312`), задача создана |
| 12 ноя 10:36:20 | Вердикт T4L **PASS**, Pod остановлен, `pods []` |
| 12 ноя 14:08:10 | GitHub Release `v1.2.6` опубликован (3 ассета) |
| 12 ноя 14:19 | Прогон координатора: шаги 01–12 PASS, шаг 13 FAIL (`EACCES` на sudo-цепочке) |
| 12 ноя 16:39 | Бэкап manifest 1.2.5, установка manifest 1.2.6 |
| 12 ноя 16:41 | Повторный прогон: 01–13 PASS, шаг 14 FAIL («Gateway отдаёт предыдущую версию») |
| 12 ноя 16:52–16:53 | Диагностика: имя файла на Gateway, кэш 404, UA/403; manifest пересобран с `?v=1.2.6` |
| 12 ноя 16:55:32 | Шаг 14 — PASS (идемпотентный SKIP: живой manifest уже правильный) |
| 12 ноя 16:57:10 | Шаг 15 (updater matrix) — **PASS** |
| 12 ноя 16:57:21 | Шаг 16 (health, pods) — **PASS** |
| 12 ноя 17:01 | Цикл закрыт: $1.3637 из $1.45, `pods_created=6`, `pods []` |
| 12 ноя 17:2x | CI на `4a3e214` — **7/7 success** |

---

## 3. T4 LIVE — сценарий и факты

Источник: `artifacts/acceptance-123/runs/live-20261112T102739/T4L.json` (`verdict: PASS`, `reasons: []`).

### 3.1 Ресурсы

| Параметр | Значение |
|---|---|
| Модель | настоящая Qwen через Canalla Cloud Gateway |
| GPU | NVIDIA **L40S 48 GB** |
| Дата-центр | **US-MO-1**, Network Volume `y92bt25ru8` |
| Время Pod | **89.0 с** (лимит 600 с, watchdog 510 с не потребовался) |
| Стоимость | **$0.0269** при фактическом тарифе $1.09/ч |
| Тариф в лимите | $1.09 ≤ $1.79 ✔ |

### 3.2 Задача

| | |
|---|---|
| Task ID | `758c3e1b-8116-45ac-a655-1274c55c09fc` |
| Создана | `READY`, запись в `local_tasks` есть |
| Состояние при разрыве | `WAITING_DEVICE`, `last_error: host_offline`, `current_step: inspect` |
| Итоговое состояние | **`COMPLETED`**, `completion_summary: Completed: Original request is addressed` |
| Model-origin инструменты | **3**, `tool_calls_used: 3` |

### 3.3 Инструменты и подтверждения

| # | run_id | Инструмент | allow | HTTP | Когда |
|---|---|---|---|---|---|
| 1 | `5ec1b174-8574-476e-8e07-6f37d91ec540` | `read_file` | true | 200 | до разрыва |
| 2 | `36f74a5e-e61d-41e7-8233-955973058b2f` | `write_file` | true | 200 | **после Resume** |
| 3 | `ff08902e-4c14-4718-aa72-88bff81a2be7` | `read_file` | true | 200 | **после Resume** |

`confirmation_failures: []` — ни один запрос не остался без ответа.

### 3.4 Разрыв и восстановление

| Шаг | Факт |
|---|---|
| Приостановка | `suspended=10312 watchdog=210` |
| Heartbeat | исчез; `online_during_offline: false` |
| Состояние | `WAITING_DEVICE` / `host_offline` (типизировано) |
| Ложный COMPLETED | отсутствует |
| Восстановление | `resumed=10312`, `online_after_resume: true`, `recovered: true` |
| Та же задача | да, тот же `task_id` |
| Device ID до/после | `f3548022-a676-463c-9fc5-b05bbd5ac907` / тот же |

### 3.5 Файлы и побочные эффекты

```
files_before:  _prompt.txt 0018861f…16d   t4l.txt 1b88c6e7…1d8
files_after:   те же + t4l_out.txt 1b88c6e7…1d8
files_lost:    []
repeated_payloads: []
duplicate_tools:   []
```

Содержимое `t4l_out.txt`: `T4L_HOST_VALUE_417` (с переводом строки), sha256
`1b88c6e7dbaea1f2cbf74f3a0a906d548c6886f4ed2ecec3b3ddcfd079bc51d8` — совпадает с исходным файлом.

### 3.6 Финальный ответ

`assistant_status: complete`; текст содержит `T4L_HOST_VALUE_417` и `T4L_DONE`, то есть модель прочитала записанный файл, а не «дописала» ответ.

---

## 4. Три дефекта T4 — что именно проверено

### 4.1 Fixture создаётся после cleanup

- `prepare_t4_root` очищает workspace, затем вызывает `catalog.setup_fixture`;
- доказательство в `T4L.json`: `files_before.t4l.txt` существует **до** первого вызова модели;
- содержимое и sha256 зафиксированы; путь совпадает с workspace инструмента;
- после reconnect файл на месте (`files_after`), `files_lost: []`.

### 4.2 Approver живёт до конца задачи

- подтверждение №1 — до разрыва;
- подтверждения №2 и №3 — **после Resume**, каждое со своим run id;
- `stop_approver` не срабатывает раньше финального ответа: все три подтверждения получены;
- бесконечного процесса не осталось (`confirmation_failures: []`, задача `COMPLETED`);
- авто-подтверждение работает только внутри изолированного тестового scope.

### 4.3 Автомат состояний, а не подавленное исключение

Проверялось не «исключение не вылетело», а работоспособность автомата:

1. состояние задачи остаётся корректным (`WAITING_DEVICE` → `EXECUTING` → `COMPLETED`);
2. ложного `COMPLETED` нет — он наступил после реального чтения файла;
3. разрешение пользователя не потеряно (3 ответа approver);
4. инструмент не потерян (`tool_calls_used: 3`);
5. бесконечного `WAITING_CONFIRMATION` нет;
6. retryable-ошибка передана типизированно (`last_error: host_offline`);
7. восстановление прошло разрешённым путём;
8. повторных side effects нет (`repeated_payloads: []`);
9. модель получила понятный результат инструмента (финальный ответ содержит содержимое файла);
10. задача дошла до `COMPLETED`.

---

## 5. Публикация 1.2.6 — все 16 шагов координатора

| Шаг | Результат | Детали |
|---|---|---|
| 01_source | PASS | продукт 1.2.6 на `0b7af14` |
| 02_developer | PASS | `RELEASE GATE: PASS` |
| 03_candidate | PASS | `RELEASE GATE: PASS` (кандидат подписан) |
| 04_verify_artifact | PASS | **8/8**, production public key |
| 05_ci | PASS | **7/7** на `0b7af14` |
| 06_backup | PASS | verified backup `20261112T154958Z-manual` |
| 07_install | SKIP | установлена та же 1.2.6 с тем же sidecar |
| 08_installed_smoke | PASS | `INSTALLED-UI: PASS` |
| 09_tag | SKIP | `v1.2.6` уже локально и на origin |
| 10_release | SKIP | три ассета уже опубликованы |
| 11_anonymous_download | PASS | 95 540 283 байт, digest совпал |
| 12_downloads | PASS | installer и `.sig` на месте на Gateway |
| 13_manifest_backup | PASS | `.bak-20261112T1639Z`, затем `.bak-20261112T1652Z` |
| 14_manifest | PASS | Gateway отдаёт 1.2.6, URL привязан к подписанному имени |
| 15_updater_matrix | PASS | 1.2.3/1.2.4/1.2.5 → 1.2.6, 1.2.6 → 204 |
| 16_health | PASS | Gateway `ready`, pods `[]` |

Все **15 обязательных гейтов** — PASS, план `publish=yes`, повторных Release/тегов/ассетов не создано.

---

## 6. Дефекты инструментария релиза

Ни один из них не был дефектом продукта, поэтому пересборка и переподпись не потребовались. Все исправлены и покрыты самотестами.

### 6.1 `sudo -n cmd && cmd` — повышение прав только у первой команды

`13_manifest_backup` падал с `EACCES`: `cp` выполнялся уже без root. Исправлено обёрткой всей строки в `sudo -n sh -c '<команда>'`.

### 6.2 Имя файла на Gateway ≠ подписанное имя

Шаг 12 копировал `Canalla.LLM_1.2.6_x64-setup.exe` (точки — так GitHub санирует пробелы), а manifest собирал URL из подписанного имени `Canalla LLM_…` (пробел). Публичный URL → **404**.
Исправлено: на Gateway кладётся подписанное имя, «санитизированное» осталось только для GitHub (`GATEWAY_ASSETS`).

### 6.3 Cloudflare закэшировал 404 на 4 часа

`cf-cache-status: HIT`, `Age: 179`, `Cache-Control: max-age=14400` — даже после исправления имени чистый URL оставался мёртвым.
Исправлено: каждый релиз получает собственный cache-buster `?v=1.2.6`. Подпись привязана к **имени файла**, а не к query, поэтому проверка `_assert_version_bound` не нарушается.

### 6.4 Проба manifest уходила без `User-Agent`

`_http` **заменял** заголовки: запрос с одним `Cache-Control: no-cache` уходил без UA, Cloudflare отвечал `403 error code 1010`, шаг 14 сообщал «Gateway всё ещё отдаёт предыдущую версию», хотя версия уже была 1.2.6.
Исправлено: заголовки объединяются с дефолтным `User-Agent`.

### 6.5 `--signature`: содержимое против пути

Шаг 15 передавал base64-содержимое подписи, скрипт пытался открыть его как файл → `OSError: [Errno 22]` на Windows. Раньше не проявлялось, потому что шаг 15 никогда не запускался.
Исправлено: принимается и содержимое, и путь.

### 6.6 Self-test писал в боевой прогресс

`release_steps_selftest.py` сохранял фиктивные шаги (`01_a`, `02_b`) в `release-progress.json`. Исправлено: тест пишет во временный файл; боевой прогресс очищен и содержит ровно 16 шагов.

---

## 7. Updater — полная матрица

**Manifest:** версия 1.2.6, `pub_date 2026-11-12T16:53:11Z`,
URL `https://gateway.12testers.store/downloads/Canalla%20LLM_1.2.6_x64-setup.exe?v=1.2.6`,
sha256 `485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1`.

```
[PASS] windows-x86_64 current=1.2.5 -> 200   (клиент видит 1.2.6)
[PASS] windows-x86_64 current=1.2.6 -> 204   (сама 1.2.6 обновления не видит)
[PASS] windows-x86_64 current=1.2.7 -> 204   (downgrade запрещён)
[PASS] linux-x86_64   current=1.2.5 -> 204   (Linux-артефакта нет)
[PASS] windows-x86_64 current=1.2.5 -> 200   (повтор стабилен)
[PASS] served version 1.2.6
[PASS] served signature is the .sig content
[PASS] served sha256 is the installer digest
[PASS] url serves the signed name "Canalla LLM_1.2.6_x64-setup.exe"
[PASS] downloaded artifact 95540283 bytes, sha256 matches
```

Дополнительно шаг 15 подтвердил `1.2.3 → 200` и `1.2.4 → 200`.
Endpoint `/updates/latest` не кэшируется (`cf-cache-status: DYNAMIC`).

---

## 8. Артефакты

| | |
|---|---|
| Installer | `Canalla LLM_1.2.6_x64-setup.exe`, 95 540 283 байт |
| SHA-256 | `485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1` |
| Подпись | minisign, ключ **9B328EFF111D1FB2**; `.sig` sha256 `d2f90538…5dca` |
| Sidecar | `aec597860fd1d04a5236c06c7e3990473eef018570561ac86213f900731c7f5f` |
| GitHub Release | `https://github.com/volkrist/canalla-llm/releases/tag/v1.2.6` (не draft, не prerelease) |
| Ассеты | installer, `.sig`, `SHA256SUMS-1.2.6.txt` |

`SHA256SUMS-1.2.6.txt` содержит корректные дайджесты installer и `.sig` (ранее был устаревший).

---

## 9. Данные, устройство, сервисы

| | |
|---|---|
| `PRAGMA integrity_check` | `ok` |
| users / chats / messages / paired_devices | **2 / 54 / 120 / 59** |
| baseline цикла | 2 / 52 / 116 / 59 — только рост, ничего не удалено |
| `computer_mode` | `ask` |
| Device ID | `f3548022-a676-463c-9fc5-b05bbd5ac907` |
| Установленная версия | 1.2.6, `/health` → `status ok`, `version 1.2.6` |
| Computer / Tor / Memory | в preflight цикла PASS (19/19); после релиза отдельная проба не перезапускалась |
| Tor | 16.0.1 / daemon 0.4.9.16 |

**Про device_id.** В брифе и части прошлых отчётов стоит `…9fc0-b05bbd5ac907`. Измеренное значение в `device.json`, в preflight от 27 октября и в T4 LIVE — `…9fc5-…`. Это опечатка в тексте, а не смена идентичности. Машиночитаемые `checkpoints.json` и `readiness.json` исправлены.

---

## 10. Деньги и Pod

| | |
|---|---|
| Pod за цикл | **6** (лимит 6) |
| Pod сейчас | **0** (`pods: []`) |
| Баланс на старте | $10.1309 |
| Баланс (сверка 17:01Z) | $8.7672151726 → расход **$1.3637** |
| Баланс (контрольное чтение) | $8.7574929504 → расход **$1.3734**, остаток $0.0766 |
| Лимит | **$1.45** |
| Сумма по строкам попыток | $0.7525; остальное — гранулярность биллинга и поздние начисления |

### Все попытки цикла

| # | Сценарий | GPU | ДЦ | Сек | $ | Вердикт |
|---|---|---|---|---|---|---|
| 1 | T3L | L40S | US-TX-3 | 244.3 | 0.0740 | PASS |
| — | T4L | L40S | US-TX-3 | — | — | NO_CAPACITY (Pod не создан) |
| 2 | T4L | A100 80GB PCIe | US-MO-1 | 491.0 | 0.2441 | FAIL (дефект harness) |
| 3 | T4L+LA | L40S | US-MO-1 | 48.0 | 0.0145 | INTERRUPTED_BY_CRASH |
| 3 | T4L+LA | L40S | US-MO-1 | 486.1 | 0.1472 | MIXED |
| — | T4L+LA | A100 80GB PCIe | US-MO-1 | — | — | без Pod |
| 5 | T4L+LA | A100-SXM4-80GB | US-MO-1 | 494.3 | 0.2458 | MIXED |
| — | T4L+LA | A100-SXM4-80GB | US-MO-1 | — | — | без Pod |
| **6** | **T4L** | **L40S** | **US-MO-1** | **89.0** | **0.0269** | **T4L PASS** (LA не успел) |

Счётчики не сбрасывались, новый цикл не открывался. Промежуточное закрытие «5 из 5 / $1.0058» сохранено в `cycle_closed_history`.

---

## 11. CI

`0b7af14` (релизный SHA) — **7/7**:

```
developer-gate        success
llama-runtime-image   success
llama-runtime         success
installer-build       success
native-gate           success
tor-runtime           success
frontend-e2e          success
```

`4a3e214` (финальный SHA с инструментарием и отчётом) — те же **7/7** `success`.
Linux gate на `0b7af14` — `success`.

---

## 12. Регрессионные самотесты

Сегодня, после правок инструментария:

- `release_steps_selftest.py` — **73/73 PASS**;
- `coordinator_selftest.py` — **57/57 PASS**;
- бесплатный T4 E2E (реальный backend, скриптовая модель) — **PASS, 11/11** проверок;
- backend 1086 passed / 1 skipped — зафиксировано ранее в цикле, сегодня не перезапускалось (runtime-код не менялся).

---

## 13. Что не делалось и почему

- **D2 / Long Autonomy / T3 LIVE не повторялись** — регрессии не было, деньги не тратились.
- **Не пересобирался установщик** — runtime-исходники не менялись с `b5b24f0`; `git diff b5b24f0..HEAD -- apps/` содержит только два тестовых файла.
- **Не создавался второй Pod** и не запускалось платное ожидание capacity.
- **Network Volumes, 12Testers, Compute Preferences, credentials, device_id** не затрагивались.
- **1.2.7 не разрабатывалась** — по условию задания до завершения 1.2.6.

---

## 14. Остаточные риски

1. **LA LIVE** — гейт PASS сохранён из прогона 8 ноября; дополнительная попытка 12 ноября не успела по времени. Это не релизный гейт, но при следующей работе над автономностью стоит перепрогнать.
2. **Authenticode** установщика по-прежнему отсутствует — подписывается только updater-артефакт (minisign).
3. **Отрицательный кэш CDN** — нейтрализован per-release query, но при будущих публикациях важно не переиспользовать URL без cache-buster.
4. **Имена ассетов** различаются намеренно: GitHub — точки, Gateway — пробелы (подписанное имя). Это зафиксировано в коде и самотестах.
5. **WM-07 / CD-08** — известные ограничения, не затрагивались.

---

## 15. Итоговая таблица

| Гейт | Результат |
|---|---|
| D2 LIVE | PASS (сохранён) |
| LA LIVE | PASS (8 ноя, 21 вызов); опциональный повтор 12 ноя — FAIL |
| T3 LIVE | PASS (сохранён, 9 вызовов) |
| T4 FREE | PASS (11/11) |
| **T4 LIVE** | **PASS** — L40S, `COMPLETED`, 3 инструмента |
| TOR | PASS |
| CI | PASS — 7/7 на `0b7af14` и `4a3e214` |
| SIGNATURE | VALID — `9B328EFF111D1FB2`, verify-artifact 8/8 |
| INSTALLED SMOKE | PASS |
| DATA | PASS — 2 / 54 / 120 / 59, integrity ok |
| DEVICE | PASS — `f3548022-…-9fc5-…` |
| PODS | **STOPPED** (`pods []`) |
| GPU SPEND | **$0.0269** последний Pod; **$1.3637–$1.3734** за цикл из $1.45 |
| RELEASE 1.2.6 | **PUBLISHED** |
| UPDATER | **PASS** |

---

## 16. Воспроизведение

```bash
# планирование (только чтение)
apps/backend/.venv/Scripts/python.exe artifacts/acceptance-126/release_coordinator.py

# продолжить/дозапустить шаги (идемпотентно)
export PATH="/c/Users/Volkr/.cargo/bin:$PATH"
apps/backend/.venv/Scripts/python.exe artifacts/acceptance-126/release_coordinator.py --execute

# живая матрица updater
apps/backend/.venv/Scripts/python.exe scripts/updates-manifest.py live \
  --version 1.2.6 --previous 1.2.5 --next-version 1.2.7 \
  --signature "apps/desktop/src-tauri/target/release/bundle/nsis/Canalla LLM_1.2.6_x64-setup.exe.sig" \
  --installer-sha256 485ee23eb5149b46a23a16595442931a17bbb0fd290d553751095c08d981d1b1 \
  --installer-name "Canalla LLM_1.2.6_x64-setup.exe"

# состояние цикла и чекпоинты
apps/backend/.venv/Scripts/python.exe artifacts/acceptance-126/checkpoints.py status
apps/backend/.venv/Scripts/python.exe artifacts/readonly-runpod-state.py
```

---

## 17. Индекс доказательств

| Что | Где |
|---|---|
| Вердикт T4 LIVE | `artifacts/acceptance-123/runs/live-20261112T102739/T4L.json` |
| Бесплатный T4 E2E | `%USERPROFILE%\Canalla Acceptance\free-126\last-result.json` |
| Прогоны координатора | `artifacts/acceptance-126/release-run-2026*.json` |
| Прогресс шагов | `artifacts/acceptance-126/release-progress.json` |
| Чекпоинты | `artifacts/acceptance-126/checkpoints.json` |
| Состояние/бюджет | `artifacts/acceptance-126/coordinator/state.json` |
| Живая матрица updater | `artifacts/acceptance-126/_updater-live.txt` |
| Живой manifest | `artifacts/acceptance-126/_manifest-final.json` |
| Инвентарь данных | `artifacts/acceptance-126/_data-now.json` |
| Краткий отчёт | `artifacts/REPORT-canalla-126-final-live-release.md` |

---

**Вывод.** Задание выполнено полностью: единственный оставшийся обязательный LIVE-гейт (T4 на настоящей Qwen) пройден с проверяемыми доказательствами, подписанная Canalla LLM 1.2.6 опубликована и раздаётся updater'ом, пользовательские данные и device_id сохранены, платные ресурсы остановлены, расход в пределах разрешённого потолка. Следующий этап (1.2.7) не начат и не опубликован — требует отдельного разрешения.
