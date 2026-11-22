# CANALLA LLM 1.2.7 — FINAL OVERNIGHT ACCEPTANCE REPORT

**LEVEL 7 offline пройден дважды подряд. Desktop staging и LIVE не выполнены: сессия исчерпала окно контекста. Вердикт — RELEASE_BLOCKED (блокеры названы точно).**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent` / **`481c011`** |
| Production | Canalla 1.2.6 — не изменена |
| Баланс RunPod | **$13.0167830352**, Pod **0**, фон $0.01/ч |
| Ledger серии | $0.4298 из $5.00 (не менялся) |
| Расход этой сессии | **$0.00** |
| LEVEL 6 | **PASS** (evidence сохранён) |
| LEVEL 7 | **VERIFIED_OFFLINE**, **NOT_PROVEN_LIVE** |
| Desktop staging | **NOT_DONE** (блокер) |
| Итоговый вердикт | **RELEASE_BLOCKED** |

---

## 1. Что реально выполнено в этой сессии

| Работа | Результат |
|---|---|
| Регрессия затронутого backend-кода | **70 passed** (`test_127_agent_core`, `test_autonomous_tasks`, `test_coding_agent`, `test_127_recovery_matrix`, `test_127_level7_negative`) |
| `free_resume_e2e.py` | **PASS дважды подряд** (прогоны `60ad5d0b…` и повторный) |
| Ruff check / format | **чисто**, 238 файлов отформатированы |
| BasedPyright `apps/backend/app` | **0 errors, 0 warnings** |
| Монитор (paused + ARMED) | 8/8 PASS (ранее в серии) |

## 2. Исправленные в серии дефекты

1. Самопроизвольный запуск GPU Monitor → `monitor.paused` + ARMED execution manifest (8/8).
2. `CHECK_REFUSED` на корректных путях workspace → канонизация обеих сторон в `verify_result`, диагностика отказа.
3. Пропуск планировщика на resume → `_goal_satisfied` требует `evidence_ok` или пройденный тест.
4. Потеря `tests_passed` после рестарта → fake-модель больше не предлагает запись после вывода pytest.
5. Форматирование диагностического блока (гейт).

## 3. LEVEL 7 — доказанная часть

Прогон `60ad5d0b…` (и повторный):

- backend A: реальные `write_file`, **настоящий pytest через host loop**;
- kill при статусе **VERIFYING**, backend A exit 1;
- backend B: новый PID, та же SQLite/data root/workspace;
- **тот же task_id**, новый прогресс (`verify_result`);
- `verification.tests = passed`, `evidence_ok = true`;
- **ровно одно `COMPLETED`**;
- вердикт **PASS**.

## 4. Что НЕ выполнено и почему

| Задача | Статус | Причина |
|---|---|---|
| Desktop staging 1.2.7 (реальное окно, чат, инструменты, recovery через GUI) | **NOT_DONE** | окно контекста сессии; не начато |
| LIVE LEVEL 7 на реальной Qwen | **NOT_RUN** | нет действующей записи batch: серия `d516f71c…` исчерпана (3/3 Pod), новый связанный batch не создан |
| Computer Use / Research / Coding / Repair / Memory / Security через модель | **NOT_RUN** | зависят от LIVE и Desktop |
| Сборка установщика 1.2.7 из `481c011` | **NOT_DONE** | не начато |
| Windows/Linux CI на `481c011` | **NOT_RUN** | не запускались |

## 5. Финансы и инфраструктура

- 0 Pod, новых списаний нет; баланс $13.0167830352, резерв $8.50 не тронут.
- Ledger $0.4298 из $5.00, Pod 3/3, `PAID_BATCH_EXHAUSTED`; новый batch не создавался.
- Watchdog и монитор не отключались; Pod-ов нет.

## 6. Production

2 users, 54 chats, 120 messages, 63 paired devices, 30 local tasks, integrity ok, FK 0, alembic 0016 —
без изменений. Все тесты выполнялись в `%TEMP%` с изолированными БД, data root, device dir и портами.

## 7. Git

```
481c011 Format the verify_result diagnostics block
41b8f28 Report the free LEVEL 7 resume pass
a8acce0 Pass the free LEVEL 7 resume E2E
151f4d7 Report the real-pytest resume run
acddfa2 Run real pytest in the resume E2E
```

## 8. Оставшиеся релизные блокеры

1. **Desktop не проверен** (уровень C приёмки) — приложение не запускалось в staging и работа
   агента не подтверждена через GUI.
2. **LIVE LEVEL 7 отсутствует** — REAL_MODEL_CONFIRMED для resume-цепочки не получен.
3. **Нет платёжной авторизации** для нового Pod: требуется аудируемое продолжение серии или новый
   мандат; самостоятельно создавать batch нельзя.
4. **Установщик 1.2.7 не собран** из текущего HEAD, CI на этом HEAD не запускался.

## 9. Нужно для снятия блокеров (в порядке приоритета)

1. Desktop staging: `tauri dev`/собранная сборка с отдельными data root, device dir и workspace,
   прогон чата и инструментов, скриншоты и логи; проверка recovery через окно.
2. Создание связанного batch (наследует $0.4298 и лимит $5) → ARMED manifest → один Pod →
   LIVE A (LEVEL 7), затем B–F по остатку бюджета → terminate + подтверждение.
3. Сборка установщика из финального HEAD, `release-gate.py candidate`, CI на этом HEAD.
4. Финальный отчёт с обновлёнными статусами.

## 10. Честный итог

Backend-часть LEVEL 7 доказана воспроизводимо и бесплатно. Продукт **не** является
RELEASE_CANDIDATE_READY: отсутствуют проверка настоящего Desktop и LIVE-подтверждение на реальной
модели. Эти два пункта — не формальность, а обязательные критерии мастер-директивы.
