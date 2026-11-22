# CANALLA LLM — АВТОНОМНЫЕ ВОЗМОЖНОСТИ: ОТЧЁТ СЕССИИ

**Бесплатный поиск без ключа, память через настоящий перезапуск, безопасность find → fix → recheck**

| | |
|---|---|
| Дата | 22 ноября 2026 |
| Проект | Canalla LLM Desktop 1.2.7 |
| Ветка | `feature/canalla-1.2.7-autonomous-agent` |
| HEAD проверенного кода | `fa43168` (отчёт опубликован следующим коммитом) |
| Артефакт релиза | `Canalla LLM_1.2.7_x64-setup.exe`, 96 568 902 B, `ec47412e…` (не менялся) |
| Расходы этой сессии | **$0.00** — 0 Pod, платных операций не было |
| Production | 1.2.6, не изменена: 2/54/120/63/30, integrity ok, 0 FK, alembic `0016` |
| CI на этом HEAD | Windows CI `44967663631` — **success, 18m07s, 7/7 job'ов**; Linux gate `44966302721` — **success 7m43s** |

---

## A. Что требовалось

Продолжить разработку автономно, без запроса разрешений на уже разрешённое, без платных
операций и без изменения production: усилить существующую систему оркестрации, durable resume,
реальный интернет-поиск, память, Computer Use, кибербезопасность, updater; всё проверять
собственными тестами и честно маркировать статусы; после каждого блока — отчёт `.md` + офлайн
`.html` на Рабочем столе и побайтовые копии в `artifacts/reports/`.

---

## B. Состояние до работы (проверено, не пересказано)

| Параметр | Факт |
|---|---|
| Ветка / HEAD | `feature/canalla-1.2.7-autonomous-agent`, `fcaaf60`, синхронна с origin |
| Незакоммиченное | только прежние тестовые артефакты и скриншоты (не трогал) |
| Production | 1.2.6, exe `21b50e46…`, БД 2/54/120/63/30, integrity ok, 0 FK |
| Pod / баланс | 0 Pod, `PAID_BATCH_EXHAUSTED`, остаток лимита $0.358 |
| CI на `a11eab8` | Windows 7/7 PASS (17m36s), Linux gate PASS (7m53s) |
| `web_search` без ключа | `provider_not_configured` — два рекламируемых инструмента были мертвы |
| Память | реализована, но нет доказательства восстановления контекста после перезапуска |
| Безопасность | сканер есть, сценарий «нашёл → исправил → перепроверил» не был зафиксирован |

---

## C. Что сделано (требование → результат → доказательство)

### C.1. Реальный поиск без платного ключа — **PASS (LIVE)**

Проблема: `web_search` и `web_fetch` были привязаны к TinyFish. На установке без ключа модель
видела два инструмента, которые умели только отвечать `provider_not_configured`, и была обязана
объяснять пользователю проблему с креdentials вместо ответа на вопрос.

Сделано:

- `apps/backend/app/tools/search/providers.py` — четыре адаптера официальных бесплатных API:
  Wikipedia (MediaWiki), arXiv (Atom), Stack Exchange, GitHub;
- `apps/backend/app/tools/search/chain.py` — выбор провайдеров по типу вопроса, параллельный
  запуск, дедупликация по каноническому URL, чередование источников, типизированные ошибки
  (`rate_limited`, `timeout`, `forbidden`, …), учёт `include_domains` / `exclude_domains`;
- `apps/backend/app/tools/search/provider.py` — `web_search` (платный провайдер, если ключ есть,
  иначе цепочка) и `web_fetch` (платный reader или собственный robots-aware клиент продукта);
- `deep_research` использует ту же цепочку, поэтому исследование работает без ключа;
- `registry.py`: описание инструментов больше не обещает провайдера, которого нет.

Доказательство:

- offline: `tests/test_free_search.py` — **17 passed** (разбор реальных форматов API через
  `httpx.MockTransport`, типизированный rate limit, дедупликация, фильтры, отказ от выдуманных
  источников, fallback-выбор провайдера);
- **LIVE**: `tests/test_live_web_smoke.py -m live_web` — **5 passed** по настоящей сети без ключа;
  реальные результаты сохранены в `artifacts/acceptance-search/free-search-live.json`:

| Запрос | Ответили | Пример источника |
|---|---|---|
| `FastAPI dependency injection` | stackexchange, github, wikipedia | `https://stackoverflow.com/questions/77746126/…` |
| `retrieval augmented generation` (research) | arxiv, wikipedia | `https://arxiv.org/abs/2506.06962v3` |

### C.2. Память проекта через настоящий перезапуск — **PASS (механика)**

Сделано: `apps/backend/tests/helpers/memory_worker.py` + `tests/test_127_memory_resume.py`.
Три независимых процесса делят **только файл SQLite**: один создаёт проект, историю чата и четыре
памяти (две проектные, одна закреплённая общая, одна чужого проекта, одна чужого аккаунта) и
завершается; второй строит контекст модели после «перезапуска»; третий удаляет запись; четвёртый
перезапуск подтверждает удаление.

Проверено: проектные решения попадают в сообщения модели без повторного объяснения пользователя;
память чужого проекта и чужого аккаунта не попадает; `use_count` растёт и сохраняется; при
`use_memory = false` выборка пуста; удаление одной памяти не затрагивает остальные.

**Найден и исправлен дефект:** `_drop_superseded` в `app/context_builder.py` считал два общих слова
«тем же фактом», поэтому два решения одного проекта («Alpha uses FastAPI…» и «Alpha tests run with
pytest…») молча теряли более старое. Теперь замена требует, чтобы общий словарь составлял не менее
половины короткой записи: исправление («Kazan» → «Moscow») по-прежнему заменяет, а другой факт
сохраняется.

Доказательство: `tests/test_127_memory_resume.py` — **4 passed**, включая юнит-тест самого правила.

### C.3. Безопасность: найдено → исправлено → перепроверено — **PARTIAL (движок VERIFIED)**

Сделано: `apps/backend/tests/test_127_security_repair.py` — заведомо уязвимый локальный проект
сканируется **собственным** движком продукта (`security_assessment.audit`), находятся:

| Правило | CWE | Где |
|---|---|---|
| SQL собирается строкой | CWE-89 | `app/db.py` |
| секрет в исходнике | CWE-798 | `app/config.py` (в отчёте маскирован) |
| TLS проверка отключена | CWE-295 | `app/http.py` |
| `shell=True` | CWE-78 | `app/run.py` |
| динамический `eval` | CWE-95 | `app/run.py` |
| debug включён | CWE-489 | `app/run.py` |

Затем применяется исправление и **тот же** движок перепроверяет дерево: четыре high-правила
исчезают, исправленное дерево сканируется чисто, а отчёт отказывается называть дерево с high-
находками чистым. Дополнительно проверено, что scope не пропускает путь вне workspace.

Честная граница: само исправление здесь **scripted**; разговор модели «нашла → объяснила →
исправила» — это LIVE-утверждение, и оно не делается.

### C.4. Матрица recovery — пять контролируемых сбоев — **PASS (механика)**

`apps/backend/tests/test_127_recovery_matrix.py` фиксирует каждый сбой отдельной строкой:

| Сбой | Ожидаемое безопасное состояние |
|---|---|
| вызов инструмента не вернулся (рестарт backend) | `ToolRun` → `failed / backend_interrupted`, задача → `INTERRUPTED`, но не завершена и не переиграна |
| задача ждёт inference (`WAITING_LLM`) | старт-реконсиляция её **не трогает**: окно повтора сохраняется |
| claim мёртвого воркера | задача возвращается в `QUEUED` **вместе с `digests_completed`** — выполненное действие не повторяется |
| повторный `finish` | ровно одно событие `COMPLETED`, конфликтующее требование — `FINISH_IGNORED` |
| пауза пользователя | `PAUSED` не превращается в `INTERRUPTED`: это решение, а не сбой |

**Найден и исправлен дефект:** `LocalTaskController.finish()` добавлял событие завершения даже
для уже терминальной задачи. Два пути завершения (tool loop и поток ответа, либо возобновлённый
процесс, гоняющийся с погибшим) могли дать два «готово» по одной задаче. Теперь второй вызов
возвращается сразу и фиксирует только конфликт как `FINISH_IGNORED`.

### C.5. Регрессии и статический анализ

| Проверка | Результат |
|---|---|
| `test_093_reliability`, `test_123_agent_runtime`, `test_local_device`, `test_127_agent_core`, `test_web_research_gap` | **69 passed** |
| `test_coding_agent`, `test_autonomous_tasks` | **48 passed** |
| `test_091_reliability`, `test_context_usage`, `test_personal_presence`, `test_127_agent_core` | **81 passed** |
| `test_autonomous_tasks` (после правки памяти) | **35 passed** |
| `test_autonomous_tasks`, `test_127_agent_core`, `test_127_recovery_e2e`, `test_127_autonomous_e2e` (после правки `finish`) | **52 passed** |
| `test_127_recovery_matrix` | **5 passed** |
| Локальный `release-gate.py developer` на `fa43168` | **9 steps · 0 FAIL · 0 BLOCKED — PASS** |
| Windows CI в GitHub Actions на `fa43168` | **7/7 job'ов PASS** (`developer-gate`, `native-gate`, `frontend-e2e`, `tor-runtime`, `llama-runtime`, `llama-runtime-image`, `installer-build`) |
| Linux gate на `fa43168` | **PASS, 7m43s** |
| Staged sidecar | пересобран из нового источника: digest `fbd7f1a0…`, exe sha256 `16477198…`, 24 873 833 B |

Что нашли сами прогоны (не только локальные тесты):

- Linux gate отверг `test_127_security_repair` — и это был настоящий переносимый дефект в
  `assert_target_allowed` (нормализовалась только цель), а не ошибка теста; исправлено;
- Windows developer gate в CI проверяет не только `ruff check`, но и `ruff format`: пять новых
  файлов были неотформатированы, шаг красный; исправлено (`1 FAIL → 0 FAIL`);
- локальный developer gate дополнительно требует, чтобы staged sidecar соответствовал исходникам:
  он был устаревшим, поэтому sidecar пересобран и `check` теперь возвращает `backend sidecar is current`.
| `ruff check` по изменённым файлам | **All checks passed** |
| BasedPyright `apps/backend/app` | **0 errors, 0 warnings** |

---

## D. Исправленные дефекты

1. **Мёртвые `web_search` / `web_fetch` без ключа** — заменены на провайдер-независимую цепочку с
   бесплатными официальными API и собственным reader'ом продукта.
2. **`_drop_superseded` терял разные факты одного проекта** — правило замены переведено с «два
   общих слова» на отношение общего словаря к короткой записи (≥ 0.5).
3. **Модель видела недоступный провайдер как обычный инструмент** — описание инструментов теперь
   соответствует реальному поведению (платный путь — оптимизация, не условие работы).
4. **Двойное «готово» по одной задаче** — `finish()` больше не добавляет событие завершения
   терминальной задаче; конфликт фиксируется как `FINISH_IGNORED`.
5. **Scope проверки безопасности отвергал путь внутри workspace на POSIX** — цель
   нормализовалась, а корень — нет (найдено Linux gate). Теперь нормализуются обе стороны.
6. **Тест `test_missing_key_chat_still_completes` кодировал старый дефект** — ожидал
   `provider_not_configured` для отсутствующего платного ключа. Контракт обновлён: бесплатная
   цепочка запускается, а при недоступности всех бесплатных провайдеров причина —
   `search_unavailable`, и чат всё равно завершается.

---

## E. Независимые доказательства

- живые сетевые прогоны (`-m live_web`, 5 passed) и сохранённые реальные URL/заголовки;
- кросс-процессные проверки памяти: между фазами не переживает ни один Python-объект;
- сканирование уязвимого проекта и повторное сканирование после исправления **одним и тем же**
  движком;
- пять контролируемых сбоев recovery, каждый — на реальных строках SQLite и реальной
  реконсиляции старта (`reconcile_tools`, `recover_interrupted`), включая правило одного завершения;
- отчёт сканера отказывается объявлять дерево чистым при наличии high-находок;
- CI на предыдущем HEAD: 7/7 job'ов Windows + Linux gate — без изменений в этой сессии.

---

## F. Деньги и инфраструктура

| Параметр | Значение |
|---|---|
| Платные GPU Pod в этой сессии | **0** |
| Расход | **$0.00** |
| Batch | `0673b737…`, `PAID_BATCH_EXHAUSTED`, остаток лимита **$0.358** |
| Монитор GPU | работает, read-only |
| Новые платные ресурсы | не создавались |

---

## G. Состояние production

Не изменялась. Проверено read-only по `%LOCALAPPDATA%\Alex LLM\data\alex.db`:
users 2, chats 54, messages 120, paired_devices 63, local_tasks 30, `integrity_check = ok`,
`foreign_key_check = 0`, alembic `0016` (1.2.6). Установщик 1.2.7 не пересобирался.

---

## H. Git

```
fa43168 Make the new tests pass the formatter gate
342a679 Normalise both sides of the security scope check
10aa1e7 Record the recovery matrix in the capability inventory
e36c97f Stop a second finish from reporting a second completion
e7e4b1d Report the free search, memory and security work
f6a1de3 Record the capability work in the agent state
87c6745 Pin the security find-fix-recheck loop to a fixture
c470693 Prove project memory across a restart, fix supersession
e22715b Give web search and fetch a free provider chain
fcaaf60 Record the final CI verdict on the gated commit
```

Всё в `origin/feature/canalla-1.2.7-autonomous-agent`; force-push не выполнялся.

---

## I. Уровни автономности

| Уровень | Статус |
|---|---|
| 1–5 | **PROVEN** предыдущими LIVE-прогонами (не переоценивались) |
| 6 | **BLOCKED_BY_LIVE_BUDGET** |
| 7 | **NOT_TESTED** (механика durable resume доказана, LIVE с моделью — нет) |

Никакой unit/integration тест в этом отчёте не повышен до LIVE-доказательства.

---

## J. Что осталось и почему

1. **LEVEL 6/7** — нужен один платный LIVE; бюджет исчерпан ($0.358 < минимального окна).
2. **Модель-ведомый research/repair** — инфраструктура и живой поиск готовы, но прогон модели
   требует LIVE.
3. **Платный TinyFish-путь** — нужен ключ владельца; бесплатный путь теперь полноценный.
4. **Computer Use сценарий C** — UIA и always-ready проверены ранее; повтор с реальной моделью
   требует LIVE.
5. **Полный updater E2E** — нужен опубликованный манифест; публикация не разрешена.
6. **Чистая Windows VM** — недоступна бесплатно (VirtualBox на 8 ГБ не завершает установку).

Следующая автоматически выбранная задача: добавить интеграционный тест Computer Use (сценарий C)
на изолированном тестовом приложении и подготовить один платный LIVE как единое окно для
LEVEL 6/7 (когда бюджет появится).

---

## K. Воспроизведение

```bash
cd apps/backend
.venv/Scripts/python.exe -m pytest tests/test_free_search.py -q
.venv/Scripts/python.exe -m pytest tests/test_live_web_smoke.py -m live_web -q
.venv/Scripts/python.exe -m pytest tests/test_127_memory_resume.py -q
.venv/Scripts/python.exe -m pytest tests/test_127_security_repair.py -q
.venv/Scripts/python.exe -m ruff check app tests
npx basedpyright apps/backend/app
gh workflow run CI --repo volkrist/canalla-llm --ref feature/canalla-1.2.7-autonomous-agent
```

---

## L. Индекс доказательств

| Файл | Содержание |
|---|---|
| `artifacts/acceptance-search/free-search-live.json` | реальные ответы бесплатных поисковых API |
| `apps/backend/tests/test_free_search.py` | 17 offline-тестов цепочки |
| `apps/backend/tests/test_live_web_smoke.py` | 5 живых сетевых тестов |
| `apps/backend/tests/test_127_memory_resume.py` + `tests/helpers/memory_worker.py` | память через перезапуск |
| `apps/backend/tests/test_127_security_repair.py` | find → fix → recheck |
| `apps/backend/tests/test_127_recovery_matrix.py` | пять контролируемых сбоев recovery |
| `docs/agent-capabilities.md` | обновлённая матрица возможностей A–H |
| `artifacts/agent-state.json` | machine-readable состояние и следующий шаг |

---

## M. Правило отчётности

Отчёт создан автоматически, без запроса подтверждения, и без создания agent skill: `.md` и
самодостаточный офлайн `.html` на Рабочем столе, побайтовые копии в `artifacts/reports/`, хеши
сверены. Это правило записано в глобальный `AGENTS.md` и действует для всех последующих сессий.
