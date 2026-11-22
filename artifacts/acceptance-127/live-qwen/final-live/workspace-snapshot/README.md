# TaskDesk

Локальное веб-приложение для управления задачами.

## Возможности
- Локальная база данных **SQLite** (файл `taskdesk/taskdesk.db`)
- Создание задач
- Список задач
- Поиск без учёта регистра (по названию и описанию)
- Изменение статуса задачи (`todo`, `in_progress`, `done`)
- Веб-интерфейс (Flask + vanilla JS)
- REST API (Flask)
- Автоматические тесты (pytest)

## Архитектура
- `taskdesk/db.py` — слой доступа к SQLite (stdlib `sqlite3`)
- `taskdesk/app.py` — Flask-приложение: API + шаблон UI
- `taskdesk/templates/index.html` — интерфейс
- `taskdesk/tests/test_api.py` — тесты через `app.test_client()`

## Запуск
```bash
python run.py            # http://127.0.0.1:5000
# или
python -m taskdesk       # то же самое
```
Порт можно задать переменной окружения `TASKDESK_PORT`.

## API
| Метод | Путь | Описание |
|---|---|---|
| GET | /api/health | проверка живости |
| GET | /api/tasks?q=...&status=... | список/поиск |
| POST | /api/tasks | создать `{"title","description"}` |
| GET | /api/tasks/<id> | одна задача |
| PATCH | /api/tasks/<id> | `{"status":"todo\|in_progress\|done"}` |
| DELETE | /api/tasks/<id> | удалить |

## Тесты
```bash
python -m pytest -q
```
