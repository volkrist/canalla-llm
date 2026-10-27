# Checkpoints — сессия 27–28.10.2026

| # | Этап | Результат | Изменения файлов | Git SHA | GPU | Следующий шаг |
|---|---|---|---|---|---|---|
| 0 | Baseline | PASS | — | 8829df9 | — | discovery |
| 1 | Поиск Windows-стенда | BLOCKED | — | 8829df9 | — | статический аудит пакета |
| 2 | Fresh-install evidence | PARTIAL/BLOCKED | — | 8829df9 | — | updater |
| 3 | Installed updater E2E | BLOCKED (production-цепочка PASS) | — | 8829df9 | — | device |
| 4 | Auth/device regression | PASS (124 + 3 реальных запроса) | `device-auth-check.py` | 8829df9 | — | аудит строк |
| 5 | Реальное истечение токена | PASS (подписанный истёкший токен, реальный endpoint) | — | 8829df9 | — | аудит |
| 6 | Read-only аудит 59 строк | PASS (57 звеньев + 1 revoked + 1 current, 0 пересечений) | `device-audit-readonly.py` | 8829df9 | — | preflight |
| 7 | GPU preflight | PASS | — | 8829df9 | — | LA |
| 8 | LA прогон 1 | FAIL (веб-поиск внедрён) | — | 8829df9 | Pod 1, $0.2046 | fix D1 |
| 8b | Fix D1 + регрессия | PASS (20 тестов) | `web_router.py`, `test_web_router.py` | 7c7fb41 | — | rebuild sidecar |
| 9 | LA прогон 2 (с fix) | FAIL (12 вызовов, `verification_required`, обрывок ответа) | — | 7c7fb41 | Pod 2, $0.0933 | stop paid |
| 9b | Возврат sidecar к релизу | PASS (f9046b83) | `restore-released-sidecar.ps1` | 7c7fb41 | — | отчёт |
| 10 | Long autonomy | FAIL / COVERAGE INSUFFICIENT | — | 7c7fb41 | — | — |
| 11 | Full CI / candidate | CI запущен на 7c7fb41 | — | 7c7fb41 | — | — |
| 12 | Release | НЕ выпущен (D2 открыт, A/B/T3/T4 BLOCKED) | отчёт | 7c7fb41 | — | — |

Проверка orphan Pod после каждого этапа: `pods []`, watchdog `disarmed`, AI `off`.
