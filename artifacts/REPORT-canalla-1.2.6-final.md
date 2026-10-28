# Canalla LLM — закрытие D2, T3, T4 и Long Autonomy
## Итоговый отчёт цикла 1.2.6 · 28.10.2026

Репозиторий: `volkrist/canalla-llm` · ветка `release/canalla-1.2.4`
Работа: существующее дерево, без нового Worktree, без force-push, без Windows VM/Sandbox.

---

## 1. Краткий вердикт

| Направление | Вердикт |
|---|---|
| D2 (обрывок вместо ответа) | **FIXED** — 4 причины найдены и закрыты, 19 регрессий зелёные |
| D2 live (model-origin) | **PARTIAL** — живой прогон завершил задачу (COMPLETED, 14 вызовов), но ответ был эхо инструмента; исправление эха добавлено **после** прогона и живой повтор заблокирован capacity |
| Long autonomy 15–30 | **FAIL** — 14 model-origin вызовов (нужно ≥15) |
| T3 (перезапуск окна) | **BLOCKED** — harness реализован и проверен бесплатно; живой прогон упал из-за дефекта самого harness (нет автоподтверждения tools), дефект исправлен, повтор не оплачен |
| T4 (обрыв Computer) | **BLOCKED** — harness реализован, живой прогон не выполнен (бюджет/ёмкость) |
| Windows v1.2.6 | **НЕ ОПУБЛИКОВАН** — обязательные гейты не пройдены |
| Данные / устройство | **PASS** — device_id неизменен, 59 строк, 0 удалений |
| Очистка Pod | **PASS** — `pods []`, watchdog `disarmed`, AI `off` |

---

## 2. Git

| | |
|---|---|
| Исходный SHA | `851ddd6` (= `origin/release/canalla-1.2.4`, тег `v1.2.5` на `8829df9`) |
| Итоговый SHA | `3ed7279` |
| Коммиты цикла | `a3f0e56` D2 finalization · `81ba299` forced re-test · `f97ca0d` controller completion + failed annotation · `3cb23c1` T3/T4 harness · `a18b09e` tool-echo report · `e7ca675` log level · `76e8faa` Linux-safe regression · `3ed7279` buffer only after real tools |
| Push | обычный fast-forward, без force |
| Теги `v1.2.3`/`v1.2.4`/`v1.2.5` | не изменялись |

---

## 3. Первопричина D2 (точная)

Финальный ответ — это обычное текстовое дополнение (`stream_chat` не передаёт `tools`),
поэтому после инструментальной фазы слабая модель продолжает писать свой tool-protocol
**текстом**. Старый код:

1. стримил ответ по токенам и на каждом токене вызывал `strip_tool_protocol` — всё после
   маркера молча отбрасывалось, а префикс уходил в чат как готовый ответ;
2. не проверял тип финального completion (валидный вызов / мусор / обычный текст);
3. закрывал задачу (`conclude`) **до** генерации прозы, поэтому FAILED-задача получала
   обрывок без объяснения;
4. не различал «нормальный ответ» и «эхо одного tool-результата».

Живое подтверждение: `live-20261028T142941` — задача `COMPLETED`, 14 настоящих
model-origin вызовов, проект собран и проверен, а в чат ушло `Процесс запущен, pid=13696`.

---

## 4. Исправление D2

`apps/backend/app/chat_stream.py`, `apps/backend/app/tools/local/public_text.py`:

* **Буферизация финального ответа для tool-турна.** Ни один delta не уходит в чат, пока ответ
  не проверен: отозвать отправленный префикс невозможно.
* **Типизация финального completion** (`carries_tool_protocol`, `final_answer_problem`):
  `tool_call_in_final_answer` / `empty_model_answer` / чисто.
* **Один ограниченный prose-only retry** (`FINAL_PROSE_PROMPT`), который прямо запрещает
  JSON/XML/`<tool_call>` и требует честно сказать, если проверка не пройдена. Повтор
  оценивается по **сырому** completion, иначе второй protocol-блок выглядел бы как чистый префикс.
* **Вызов инструмента из ответа никогда не исполняется**: approvals и sandbox живут в
  оркестраторе.
* **FAILED-задача больше не врёт**: `annotate_failed_answer` сохраняет правдивое предложение
  модели и добавляет реальный исход + список выполненных действий; `acknowledges_failure`
  не дублирует текст, если модель уже назвала ошибку.
* **COMPLETED-задача с эхом** (`is_tool_echo`): вместо `Процесс запущен, pid=…` публикуется
  отчёт контроллера (`completion_summary`) с реальными файлами и процессами. Эхо — это одна
  короткая фраза без пояснения; любая добавленная мысль оставляет ответ как есть.

---

## 5. Регрессии D2 (бесплатно, все зелёные)

`apps/backend/tests/test_d2_final_answer.py` — 13 тестов, включая реальный SSE-стрим
`POST /chats/{id}/stream`:

| # | Проверка |
|---|---|
| D2-01 | обычный финальный ответ публикуется без изменений |
| D2-02 | tool call вместо ответа — префикс не публикуется |
| D2-03 | human prefix + валидный tool call → prose-retry |
| D2-04 | human prefix + сломанный tool block → честная сводка |
| D2-05 | несколько последовательных ответов (retry) |
| D2-06 | tool block без текста — не пустой пузырь |
| D2-18 | цитата с маркером в тексте не исполняется |
| D2-20 | FAILED-задача не отдаёт обрывок (`annotate_failed_answer`) |
| — | `is_tool_echo`, `completion_summary`, `acknowledges_failure`, пустой ответ |

`apps/backend/tests/test_long_autonomy.py` — 3 теста: 16 последовательных model-calls
выполняются все (нет скрытого потолка 12), запись после теста форсирует перепроверку,
папка без git не гоняет `git_status` вечно.

Полный backend-прогон: **1009 passed, 1 skipped**.
Developer gate: **9/9 PASS** (ruff, форматирование, backend pytest, gateway ruff+pytest,
vitest, tsc, prettier, vite build).

---

## 6. Long autonomy

Найдены две причины остановки на 12 вызовах (не лимит: `max_calls=40`):

1. `next_forced_action` решал «нужна ли перепроверка» по **имени последнего инструмента**, а не
   по фактической устарелости. После `write_file` модель сделала `list_directory`, перепроверка
   не форсировалась, и `final_review` справедливо закрыл задачу как `verification_required`.
   → правило переведено на собственный флаг контроллера (`verification_stale`/`tests=stale`).
2. Планировщик может замолчать **до** того, как контроллер закрыл свои шаги. Добавлен
   пост-цикловый проход: если `next_forced_action` ещё что-то требует и бюджет есть, этот шаг
   выполняется один раз в том же Pod.

Живой результат `live-20261028T142941`:

* **14 model-origin вызовов** (2 `list_directory`, 6 `write_file`, 6 `run_process`);
* задача **COMPLETED** (`Original request is addressed; All affected tests pass; …`);
* файлы: `la_app/__init__.py`, `core.py`, `cli.py`, `tests/test_core.py`, `README.md`, `la_report.txt`;
* pytest реально запускался (`.pytest_cache`), CLI-вывод сохранён в `la_report.txt`;
* финальный ответ — эхо `Процесс запущен, pid=13696` (исправлено после прогона, живой повтор — BLOCKED).

**Вердикт: TASK PASS по существу, LONG-AUTONOMY COVERAGE INSUFFICIENT (14 < 15).**

---

## 7. T3 — перезапуск реального окна

Реализовано (`artifacts/acceptance-123/live_restart.py`, `apps/desktop/ui-task.mjs`,
`artifacts/ensure-app-cdp.ps1`):

1. задача печатается в установленное окно через CDP (создаётся новый чат — пользовательский
   диалог не трогается);
2. harness ждёт первый **завершённый model-origin** tool;
3. окно закрывается (`alex-llm.exe` + owned sidecar), backend действительно падает;
4. окно поднимается заново, backend возвращается, задача должна быть `INTERRUPTED`;
5. продолжение — кнопка **Resume** в UI (или тот же endpoint `POST /tasks/{id}/resume`,
   какой путь использован — записывается в evidence);
6. проверяются файлы, дубликаты tool-run'ов, финальный ответ, device_id.

Бесплатный self-test механики: **PASS** (`artifacts/final-acceptance/restart-selftest-2026-10-28.json`):
запуск окна, CDP, отправка, остановка, backend down/up, возврат, device_id `f3548022…`.

Живой прогон `live-20261028T133742`: **FAIL** — harness не подтверждал `waiting_confirmation`,
поэтому модель не получила ни одного инструмента. Дефект harness исправлен (фоновый
approver внутри acceptance-sandbox), но повторный живой прогон не оплачен (capacity).

**T3 live: BLOCKED.**

---

## 8. T4 — обрыв Computer host

Native host живёт **внутри** процесса `alex-llm.exe` (отдельного `alex-host-loop.exe` в
установленной сборке нет), поэтому честный разрыв транспорта — это приостановка процесса
(`NtSuspendProcess`) без убийства: sidecar продолжает стримить, backend перестаёт получать
heartbeat и claim'ы, затем процесс возобновляется, `device_id` не меняется.

Реализовано: `artifacts/suspend-app.ps1` + `live_restart.run_t4` (suspend → typed state →
resume → завершение, проверка дубликатов и файлов). Живой прогон не выполнен: бюджет ушёл на
LA/T3, затем capacity исчезла.

**T4 live: BLOCKED.**

---

## 9. Прочие дефекты

| Дефект | Слой | Статус |
|---|---|---|
| D1: слово «проверь» делало локальную задачу web-запросом | backend/router | FIXED ранее (`7c7fb41`) |
| Модель останавливалась после `write_file` → `list_directory`, проверка «устарела» | backend/task | FIXED (`81ba299`, `f97ca0d`) |
| FAILED-задача показывала обрывок как ответ | backend/stream | FIXED (`a18b09e`) |
| COMPLETED-задача показывала эхо инструмента | backend/stream | FIXED (`a18b09e`) |
| INFO-лог оркестратора ломал uvicorn-форматтер в e2e (`ValueError: expected 5, got 0`) | backend/logging | FIXED (`e7ca675`, debug) |

---

## 10. Тесты

| Набор | Результат |
|---|---|
| Backend pytest (полный) | 1009 passed, 1 skipped |
| D2 + long autonomy (новые) | 30 passed |
| Rust native gate | PASS (CI) |
| Gateway ruff + pytest | PASS |
| Frontend vitest / tsc / prettier / vite build | PASS |
| Developer gate | **9/9 PASS** |
| Linux gate | см. §12 |
| CI на итоговом SHA | см. §12 |

---

## 11. Установленная сборка и данные

| Параметр | Значение |
|---|---|
| Установленная версия | **1.2.5** (возвращена к опубликованной сборке) |
| backend sha256 | `f9046b83bb94db3b75e6224d1fa27fc6eae75fd302a0b28f130eeb6f1e63f953` |
| backend source_digest | `b9aef542a9e334621953ccb919eaaf608ddead1c3c80f9947d6f240c5deef49a` (141 файл) |
| desktop sha256 | `153dc62bd0c85dfeb48857064f8154a5a1c4e8f8c60f1c36c8963a9257f61e45` |
| native host sha256 | `262a1f8428a3a98df19b53dbdeed899f3d6516335bc4833f461c69d66f1300d6` |
| Tor sha256 | `8c38513b3c4584154158e7c584f4144849dc679a64cd679553d72dad28230c54` |
| AI / Computer / Tor / Memory | off / ready / ready / ready |
| device_id | `f3548022-a676-463c-9fc5-b05bbd5ac907` (не менялся) |
| `paired_devices` | 59 (1 revoked) — без изменений |
| SQLite integrity | ok |
| users / chats / messages | 2 / 43 / 86 |

Рост counts объясняется полностью: 3 acceptance-чата (LA ×2, T3L ×1) × 2 сообщения.
Пользовательские чаты не тронуты, вручную удалённые не восстанавливались.

---

## 12. CI на итоговом SHA

`3ed7279` — финальный SHA.

* CI (`CI`, run 40718678329): **7/7 success** — llama-runtime-image, native-gate, developer-gate,
  tor-runtime, llama-runtime, installer-build, frontend-e2e.
* Linux gate (run 40718678345): **success**.
* Промежуточные прогоны падали дважды и оба раза по делу: (1) docstring/format — исправлено,
  (2) `frontend-e2e`: буферизация финального ответа убирала первый delta, и кнопка
  `Stop generation` не появлялась — исправлено ограничением буфера «только если инструменты
  реально выполнялись»; локальный Playwright 21/21.

---

## 13. Деньги

| # | GPU | $/час | Старт (UTC) | Итог |
|---|---|---|---|---|
| 1 | NVIDIA L40S | 1.09 | 13:16 | LA, 418.6 с, stop подтверждён |
| 2 | NVIDIA A100-SXM4-80GB | 1.79 | 13:37 | T3L, harness-дефект, stop подтверждён |
| 3 | NVIDIA L40S | 1.09 | 14:29 | LA, 185 с, stop подтверждён |

* Попытки 13:33/13:53/14:51 — **без Pod** (`price_limit`), платного ожидания не было.
* Баланс: `5.1859041848` → `4.6252731459` (после полного расчёта отложенных начислений),
  израсходовано **$0.5606** (лимит $1.50).
* `pods []`, `active_session=null`, watchdog **disarmed**, AI **off**, reconnect не выполнялся.
* Compute Preferences владельца не менялись.

---

## 14. GitHub / Release

**v1.2.6 НЕ выпущен.** Обязательные условия не выполнены: long autonomy < 15 вызовов,
T3/T4 live не подтверждены. Тег не создавался, release assets не менялись, production
updater manifest не изменялся (продолжает отдавать 1.2.5), подпись и ключ не трогались.

---

## 15. Точные незакрытые ограничения

1. **Long autonomy**: 14 model-origin вызовов при цели 15–30. Сценарий LA не требует 15 шагов
   естественно; нужен более объёмный challenge (не искусственные вызовы).
2. **D2 live**: исправление эха не проверено живым прогоном (capacity).
3. **T3 live**: harness исправлен, но живой прогон не повторён.
4. **T4 live**: harness готов, живой прогон не выполнен.
5. **Capacity**: `price_limit` на стороне Gateway; менять политику владельца запрещено.
6. **Frontend provenance**: отдельного штампа frontend по-прежнему нет.

---

## 16. Итоговая таблица

| Гейт | Статус |
|---|---|
| D2 regression | **PASS** (30 тестов, developer gate 9/9) |
| D2 live | **PARTIAL** (задача завершена, 14 вызовов, ответ-эхо исправлен после прогона) |
| Long autonomy 15–30 | **FAIL** (14) |
| T3 real | **BLOCKED** |
| T4 real | **BLOCKED** |
| Data preservation | **PASS** |
| Device stability | **PASS** |
| CI | **PASS** — 7/7 на `3ed7279`, Linux gate success |
| Release 1.2.6 | **NOT PUBLISHED** |
| Pod cleanup | **PASS** |
