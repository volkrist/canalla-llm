# Alex LLM 0.2.0 — проверка этапа 2

Дата: 14 сентября 2026. Изменён существующий проект `volkrist/alex-llm`; стек Tauri 2 / React / FastAPI сохранён.
Отчёт предыдущей версии сохранён в [verification-v1.md](verification-v1.md).

1. **Чаты.** Переименование, удаление с подтверждением, поиск названий, закрепление/открепление. Закреплённые идут первыми, затем сортировка по обновлению.
2. **Экспорт.** Markdown/JSON одного или всех собственных диалогов. Backend проверяет владельца. В Windows используется системный Save dialog, в браузере — download.
3. **Сообщения.** Копирование, редактирование своего текста, изменение с повторной отправкой, перегенерация/Retry ответа. Повторная генерация линейно удаляет последующую историю.
4. **Streaming.** Постепенный SSE, «Alex думает…», Stop и сохранение частичного ответа. Пустой прерванный ответ остаётся доступным для Retry. Повторная отправка блокируется.
5. **Composer.** Автоматическая высота, Enter/Shift+Enter/Ctrl+Enter; черновики разделены по backend, пользователю и диалогу. Настройка Enter проверена в браузере.
6. **Навигация.** Ctrl+N, Ctrl+K, Ctrl+comma, Esc; контекстное меню чатов, время сообщений, кнопка прокрутки вниз. Новые ответы не уводят читателя от просматриваемого текста.
7. **Markdown.** GFM, таблицы, код, подсветка синтаксиса, Copy code. Raw HTML не исполняется. HTTP(S)-ссылки открываются системным браузером по нажатию.
8. **Настройки.** Общие, чат, AI/Compute, данные, дополнительные. Тема Dark/System, размер текста, русскоязычный UI, автопрокрутка/время/технические сведения. Backend URL вынесен в дополнительные параметры.
9. **RunPod-клиент.** `app/compute/runpod_api.py`: официальный REST v2, фиксированный API host, тайм-ауты, структурная валидация и безопасные ошибки. Runtime не использует MCP.
10. **Выбор GPU.** NVIDIA Secure, существующий Volume в US-TX-3, configurable VRAM/цена/бюджет. Автоматически — самый дешёвый доступный вариант; вручную — выбранная GPU. Недоступные/дорогие варианты отключены.
11. **Подтверждение цены.** Стоимость часа/минуты/10/30/60 минут, отдельное замечание о storage, срок предложения. Перед create — повторная проверка. Fallback ограничен тремя кандидатами не дороже подтверждённого.
12. **Контроллер.** `app/compute/controller.py`: DB lease, committed create intent, idempotency, deterministic Pod name, recovery. Неопределённый create не повторяется автоматически. Существующий Pod не дублируется.
13. **Готовность.** Реальные состояния поставщика и свежие фазовые маркеры existing startup/check scripts. Нет фиктивных процентов/TPS. Устаревшие logs не означают Ready.
14. **Время и стоимость.** Supplier started_at отдельно от ready_at; backend считает elapsed/estimated cost. Историческая ставка сохраняется. Actual cost запрашивается отдельно; отсутствие данных остаётся null.
15. **Бюджет/автостоп.** Достигнутый бюджет запрещает новую генерацию; остановка ждёт текущую. Idle timeout применяется после Ready без активной генерации. Network Volume не удаляется. Внешний Pod не останавливается автоматически.
16. **Пользователи/админ.** ADMIN_EMAILS только на backend; ALLOW_USER_COMPUTE_START=false по умолчанию. Личное использование за день/неделю/месяц/всё время, история сессий; админ видит пользователей, общие сессии/расходы и управление compute.
17. **Миграция.** Alembic 0002 добавляет роли, поля чатов/сообщений, compute_control/sessions/events/quotes/preferences и generation_usage. Проверено сохранение данных 0001 → 0002, чистая БД в E2E и `alembic check`. Рабочая SQLite обновлена после backup API.
18. **Безопасность.** RunPod key отсутствует в frontend/exe; хранится только в backend env. Без ключа Not configured, mock работает. Ownership новых chat/export/edit endpoints и admin permissions покрыты тестами. CORS и JWT не ослаблены.
19. **Проверки.** 39 backend pytest, 8 frontend unit tests; Ruff, Prettier, TypeScript/Vite, cargo check, Tauri release и NSIS build. Браузерные сценарии: базовый чат/изоляция, mobile/offline, новые функции чатов, test-only compute UI. Нативный WebView2 smoke проверяет регистрацию, streaming, Copy, Stop, историю, удаление и выход. npm audit: 0 vulnerabilities.
20. **Артефакты.** `../Alex LLM.exe` и `../Alex LLM Setup.exe` относительно корня проекта. Ярлык `C:\Users\Volkr\Desktop\Alex LLM.lnk` указывает на обновлённый exe. Пользователю desktop не нужны Node/Rust/Python; нужен доступный backend и WebView2. Backend остаётся отдельным сервисом.
21. **Границы проверки.** Реальный платный запуск RunPod не выполнялся. Живые startup scripts/model/image и фактический billing не проверены; использованы HTTP mocks. Installer собран, интерактивная установка/удаление не проверялась. PostgreSQL не запускался. Native save dialog/open-browser не включены в автоматический smoke; browser export проверен. Приложение не подписано. Нет подключения реального LLM к чату, Memory/RAG/агентов/LoRA.

## Существенные ограничения

RunPod API не предоставляет атомарную гарантию max-price для create. При изменении цены между проверкой и созданием возможна короткая платная сессия до защитной остановки.
Бюджет — ограничитель по polling, не предоплаченный потолок: текущая генерация и задержки API могут дать превышение.
Мониторинг/автостоп работает, пока работает backend. Chat recovery требует одного backend worker.
Оценка стоимости — не счёт RunPod; хранение оплачивается отдельно, текущий тариф API не сообщает.

Чат остаётся `LLM_PROVIDER=mock` по заданию. Test-only UI fixtures находятся только в Playwright; production-переключателя вымышленных состояний нет.
Официальный контракт и команды настройки: [runpod-controller.md](runpod-controller.md). API/структура: [architecture.md](architecture.md).

## Снимки экранов

Ниже GPU-состояния с суффиксом `test` получены через Playwright interception, а не через запуск платного Pod.

![Вход](screenshots/login.png)
![Чат](screenshots/chat.png)
![Настройки](screenshots/settings.png)
![Использование](screenshots/usage.png)
![GPU offline — тест](screenshots/gpu-offline-test.png)
![GPU search — тест](screenshots/gpu-search-test.png)
![GPU price confirmation — тест](screenshots/gpu-confirm-test.png)
![GPU starting — тест](screenshots/gpu-starting-test.png)
![GPU ready — тест](screenshots/gpu-ready-test.png)

SHA256 executable: `C23977A9E2B13991912DD21C6335A1F55952AE76346F62D4658BDA4CFEA8E1AA`.
SHA256 installer: `D6958D5AD93F421AB3661A47F54F5A48529B1D1C68DB8C21BE4C15AE3E428105`.
