"""The Canalla 1.2.3 acceptance catalog: 28 scenarios, fixtures and verdict rules.

Every scenario carries its own prompt, timeout, expected evidence and independent check.
`require_model=True` means the expected tool call must have `origin == "model"`: a
`server_policy`/deterministic `intent.py` action is reported as `BLOCKED_DETERMINISTIC`, never
as a Qwen PASS. The live runner and the free preflight share this file, so the expected
evidence cannot drift between preparation and execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from common import SANDBOX

S = SANDBOX


def scenario_root(scenario_id: str) -> Path:
    return S / scenario_id


def P(scenario_id: str, name: str = "") -> str:
    root = scenario_root(scenario_id)
    return str(root / name) if name else str(root)


@dataclass(frozen=True)
class Scenario:
    id: str
    tier: str
    title: str
    prompt: str
    timeout: int
    expected_tools: tuple[str, ...] = ()
    require_model: bool = True
    markers: tuple[str, ...] = ()
    files: tuple[tuple[str, str | None], ...] = ()
    forbid_files: tuple[str, ...] = ()
    stdout_markers: tuple[str, ...] = ()
    deny_confirm: bool = False
    expect_denied: bool = False
    allow_failed_tool: bool = False
    setup: str = ""
    post_check: str = ""
    ui_script: str = ""
    notes: str = ""
    budget_seconds: int = 120


# --------------------------------------------------------------------------------- fixtures


def setup_fixture(name: str, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if name == "s3_readme":
        (root / "readme with spaces.txt").write_text(
            "S3_CANALLA_READ_OK_20261020\nВторой строкой: acceptance.\n",
            encoding="utf-8",
            newline="\n",
        )
    elif name == "m2_csv":
        (root / "m2_sales.csv").write_text(
            "item,units,price\nalpha,2,10\nbeta,3,20\ngamma,4,30\n",
            encoding="utf-8",
            newline="\n",
        )
    elif name == "h2_seed":
        (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8", newline="\n")
        (root / "test_calc.py").write_text(
            "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "README.md").write_text("# h2 seed\n", encoding="utf-8", newline="\n")
    elif name == "h3_seed":
        (root / "app.py").write_text(
            "def total(values):\n    return sum(values)\n\n\ndef label():\n    return 'H3_OK'\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "test_app.py").write_text(
            "from app import label, total\n\n\ndef test_total():\n    assert total([1, 2, 3]) == 6\n\n\n"
            "def test_label():\n    assert label() == 'H3_OK'\n",
            encoding="utf-8",
            newline="\n",
        )
        (root / "README.md").write_text("# h3 seed\n", encoding="utf-8", newline="\n")
    elif name == "h5_seed":
        (root / "h5_data.txt").write_text("example.com\n", encoding="utf-8", newline="\n")
    elif name == "t4_file":
        (root / "t4.txt").write_text("T4_HOST_FILE_OK\n", encoding="utf-8", newline="\n")
    elif name == "t4l_file":
        (root / "t4l.txt").write_text("T4L_HOST_VALUE_417\n", encoding="utf-8", newline="\n")


def seed_check(name: str, root: Path) -> tuple[bool, str]:
    if name == "m2_csv":
        rows = (root / "m2_sales.csv").read_text(encoding="utf-8").splitlines()[1:]
        values = [int(row.split(",")[1]) * int(row.split(",")[2]) for row in rows if row]
        expected = {
            "total_revenue": sum(values),
            "total_units": sum(int(row.split(",")[1]) for row in rows if row),
            "min_revenue": min(values),
            "max_revenue": max(values),
        }
        return True, json.dumps(expected)
    if name == "h2_seed":
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode != 0, result.stdout[-300:] + result.stderr[-200:]
    if name == "h3_seed":
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.returncode == 0, result.stdout[-300:] + result.stderr[-200:]
    return True, "no seed check"


def post_check(name: str, root: Path) -> tuple[bool, str]:
    if name in {"h1_cli", "h2_seed", "h3_seed", "h5_chain"}:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=240,
        )
        return result.returncode == 0, result.stdout[-500:] + result.stderr[-300:]
    if name == "m2_stats":
        stats = root / "m2_stats.csv"
        report = root / "m2_report.txt"
        if not stats.is_file():
            return False, "m2_stats.csv missing"
        text = stats.read_text(encoding="utf-8")
        report_text = report.read_text(encoding="utf-8") if report.is_file() else ""
        numbers = ["200", "9", "20", "120"]
        missing = [value for value in numbers if value not in text]
        return (not missing and bool(report_text.strip()), "missing=" + ",".join(missing))
    return True, "no post check"


# --------------------------------------------------------------------------------- prompts

S3_FILE = P("S3", "readme with spaces.txt")
S6_URL = "https://example.com"

SCENARIOS: list[Scenario] = [
    Scenario(
        "S1",
        "simple",
        "Русский диалог",
        "Ответь по-русски одним коротким предложением: что такое детерминированный контроллер?",
        120,
        require_model=False,
        markers=("детерминир", "контроллер"),
        budget_seconds=45,
    ),
    Scenario(
        "S2",
        "simple",
        "Контекст в беседе",
        "Запомни три факта: первый — Canalla, второй — 123, третий — агент. "
        "Теперь назови только второй факт, без пояснений.",
        120,
        require_model=False,
        markers=("123",),
        budget_seconds=45,
    ),
    Scenario(
        "S3",
        "simple",
        "Файл с пробелами в пути",
        # The wording deliberately avoids every deterministic intent phrase (`прочитай файл`,
        # `создай …`, `read …`): a server_policy action would prove nothing about Qwen. The
        # tool name itself is safe — only the trigger phrases above are matched.
        "Открой файл " + S3_FILE + " через инструмент read_file и перескажи его содержимое "
        "одним предложением.",
        180,
        expected_tools=("read_file",),
        markers=("S3_CANALLA_READ_OK_20261020",),
        setup="s3_readme",
        budget_seconds=60,
    ),
    Scenario(
        "S4",
        "simple",
        "Точное содержимое файла",
        # `Запиши в файл …` matches `запис.{0,40}файл` in intent.py, so the path is named first
        # and the body is an explicit marker. No deterministic phrase is present.
        "Запиши по пути " + P("S4", "exact.txt") + " ровно S4_EXACT_OK_20261020 без перевода "
        "строки, затем прочти содержимое обратно через read_file.",
        180,
        expected_tools=("write_file", "read_file"),
        markers=("S4_EXACT_OK_20261020",),
        files=(("exact.txt", "S4_EXACT_OK_20261020"),),
        budget_seconds=60,
    ),
    Scenario(
        "S5",
        "simple",
        "Python через Computer",
        # `создай <name>.py` and `запусти … python` are deterministic triggers; `выполни` is not.
        # The cwd is the tool's own `cwd` argument, stated without the trigger word.
        "В папке " + P("S5") + " сделай файл s5_hello.py с одной строкой "
        "print('S5_STDOUT_OK'). Выполни этот файл интерпретатором Python с рабочим каталогом "
        + P("S5") + " и верни только stdout.",
        180,
        expected_tools=("write_file", "run_python"),
        markers=("S5_STDOUT_OK",),
        stdout_markers=("S5_STDOUT_OK",),
        budget_seconds=60,
    ),
    Scenario(
        "S6",
        "simple",
        "Web: example.com",
        "Открой https://example.com через web_fetch и назови точный заголовок страницы.",
        240,
        expected_tools=("web_fetch",),
        markers=("Example Domain",),
        budget_seconds=90,
    ),
    Scenario(
        "S7",
        "simple",
        "Смена чата во время генерации",
        "Медленно объясни в трёх абзацах, зачем нужен детерминированный контроллер в агенте. "
        "Не менее 400 слов, не торопись.",
        240,
        require_model=False,
        markers=(),
        ui_script="generation-navigation",
        budget_seconds=120,
    ),
    Scenario(
        "M1",
        "medium",
        "Программа: создать, запустить, изменить, повторить",
        "В папке " + P("M1") + " сделай файл m1_hello.py с одной строкой print(2 + 2), выполни "
        "его интерпретатором Python с рабочим каталогом " + P("M1") + ", получи stdout, затем "
        "замени код на print(2 * 3), выполни снова и верни оба значения stdout.",
        300,
        expected_tools=("write_file", "run_python"),
        markers=("4", "6"),
        stdout_markers=("4", "6"),
        budget_seconds=120,
    ),
    Scenario(
        "M2",
        "medium",
        "CSV: статистика и отчёт",
        "Открой файл " + P("M2", "m2_sales.csv") + ", посчитай суммарную выручку (units*price), "
        "суммарные units, минимальную и максимальную выручку по строке. Запиши по пути "
        + P("M2", "m2_stats.csv") + " эти числа, а по пути " + P("M2", "m2_report.txt")
        + " — краткий отчёт. Расчёт выполни интерпретатором Python.",
        360,
        expected_tools=("read_file", "write_file", "run_python"),
        markers=("200",),
        files=(("m2_stats.csv", None), ("m2_report.txt", None)),
        setup="m2_csv",
        post_check="m2_stats",
        budget_seconds=150,
    ),
    Scenario(
        "M3",
        "medium",
        "Мини-проект из трёх файлов",
        "В папке " + P("M3") + " сделай пакет m3_app из трёх модулей: __init__.py, core.py, "
        "__main__.py. core.py возвращает строку 'M3_PROJECT_OK', __main__.py печатает её. "
        "Выполни python -m m3_app интерпретатором с рабочим каталогом " + P("M3")
        + " и верни stdout.",
        420,
        expected_tools=("write_file", "run_python"),
        markers=("M3_PROJECT_OK",),
        stdout_markers=("M3_PROJECT_OK",),
        files=(("m3_app/__init__.py", None), ("m3_app/core.py", None), ("m3_app/__main__.py", None)),
        budget_seconds=180,
    ),
    Scenario(
        "M4",
        "medium",
        "Web: два первоисточника",
        "Открой через web_fetch https://example.com и https://www.python.org. Назови точные "
        "заголовки обеих страниц и приведи обе ссылки. Не выдумывай факты.",
        300,
        expected_tools=("web_fetch",),
        markers=("Example Domain", "Python"),
        budget_seconds=120,
    ),
    Scenario(
        "M5",
        "medium",
        "Web + Computer в одной задаче",
        "Открой https://example.com через web_fetch, сохрани точный заголовок по пути "
        + P("M5", "m5_title.txt") + " доступным инструментом записи, затем прочти этот файл "
        "интерпретатором Python и напечатай заголовок в верхнем регистре. Верни stdout.",
        360,
        expected_tools=("web_fetch", "write_file", "run_python"),
        markers=("EXAMPLE DOMAIN",),
        stdout_markers=("EXAMPLE DOMAIN",),
        files=(("m5_title.txt", None),),
        budget_seconds=150,
    ),
    Scenario(
        "M6",
        "medium",
        "Продолжение после смены чата",
        "В папке " + P("M6") + " сделай файл m6_state.py, который печатает 'M6_STATE_OK', "
        "и выполни его интерпретатором Python. Не создавай файл дважды. Верни stdout.",
        300,
        expected_tools=("write_file", "run_python"),
        markers=("M6_STATE_OK",),
        stdout_markers=("M6_STATE_OK",),
        budget_seconds=120,
        notes="The runner switches to another chat after the completed write, then returns.",
    ),
    Scenario(
        "M7",
        "medium",
        "Отклонённое подтверждение",
        "Запиши по пути " + P("M7", "m7_denied.txt") + " ровно M7_MUST_NOT_EXIST.",
        180,
        expected_tools=("write_file",),
        forbid_files=("m7_denied.txt",),
        deny_confirm=True,
        expect_denied=True,
        budget_seconds=60,
        notes="The runner denies the confirmation; the file must not exist and no write may complete.",
    ),
    Scenario(
        "H1",
        "hard",
        "CLI-проект с модулями, тестами и README",
        "В папке " + P("H1") + " создай рабочий Python CLI-проект h1_cli: пакет h1_cli/__main__.py, "
        "h1_cli/core.py, tests/test_core.py и README.md. python -m h1_cli должен печатать "
        "H1_CLI_OK, pytest -q должен проходить. Запусти оба и верни результат.",
        600,
        expected_tools=("write_file", "run_python"),
        markers=("H1_CLI_OK",),
        stdout_markers=("H1_CLI_OK",),
        files=(("h1_cli/__main__.py", None), ("h1_cli/core.py", None), ("tests/test_core.py", None), ("README.md", None)),
        post_check="h1_cli",
        budget_seconds=300,
    ),
    Scenario(
        "H2",
        "hard",
        "Найти и исправить ошибку по pytest",
        "В папке " + P("H2") + " находится проект с намеренно внесённой ошибкой. Запусти pytest -q, "
        "найди причину, исправь исходный код и повторно запусти pytest до PASS. Верни итог.",
        600,
        expected_tools=("run_python", "read_file"),
        setup="h2_seed",
        post_check="h2_seed",
        budget_seconds=300,
    ),
    Scenario(
        "H3",
        "hard",
        "Рефакторинг без изменения поведения",
        "В папке " + P("H3") + " находится рабочий проект. Выполни безопасный рефакторинг, "
        "не меняя поведение: pytest -q должен остаться зелёным, а CLI-вывод — H3_OK. "
        "Покажи, что именно изменено.",
        600,
        expected_tools=("read_file", "run_python"),
        stdout_markers=("H3_OK",),
        files=(("app.py", None),),
        setup="h3_seed",
        post_check="h3_seed",
        budget_seconds=300,
    ),
    Scenario(
        "H4",
        "hard",
        "Мультиисточниковый веб-отчёт",
        "Исследуй не менее двух публичных источников о Python (например, python.org и "
        "docs.python.org). Извлеки проверяемые факты, отметь расхождения, дай структурированный "
        "отчёт с точными ссылками. Не выдумывай.",
        480,
        expected_tools=("web_fetch",),
        markers=("Python", "http"),
        budget_seconds=240,
    ),
    Scenario(
        "H5",
        "hard",
        "Web → файлы → Python → тесты → отчёт",
        "Получи заголовок https://example.com, сохрани его в " + P("H5", "h5_title.txt")
        + ", создай h5_app/ с функцией normalized() и тестом, запусти pytest, исправь ошибки "
        "и подготовь h5_report.txt с итогом и ссылкой.",
        600,
        expected_tools=("web_fetch", "write_file", "run_python"),
        markers=("Example Domain",),
        files=(("h5_title.txt", None), ("h5_report.txt", None)),
        post_check="h5_chain",
        budget_seconds=300,
    ),
    Scenario(
        "H6",
        "hard",
        "Восстановление после обрыва",
        "В папке " + P("H6") + " сделай файл h6_probe.py, который печатает 'H6_RECOVERED', "
        "и выполни его интерпретатором Python. Не повторяй запись файла после временного "
        "сетевого сбоя.",
        420,
        expected_tools=("write_file", "run_python"),
        markers=("H6_RECOVERED",),
        stdout_markers=("H6_RECOVERED",),
        budget_seconds=180,
        notes="The runner blocks sidecar -> Gateway for 12 s after the write.",
    ),
    Scenario(
        "T1",
        "stress",
        "Длинная задача с состоянием",
        "В папке " + P("T1") + " сделай три файла: t1_a.py, t1_b.py, t1_c.py. Каждый печатает "
        "свой маркер T1_A/T1_B/T1_C. Выполни все три интерпретатором Python и верни три строки.",
        480,
        expected_tools=("write_file", "run_python"),
        markers=("T1_A", "T1_B", "T1_C"),
        stdout_markers=("T1_A", "T1_B", "T1_C"),
        budget_seconds=240,
    ),
    Scenario(
        "T2",
        "stress",
        "Stop AI посреди генерации",
        "Напиши развёрнутое эссе о надёжности автономных агентов, не менее 800 слов. "
        "Не используй инструменты, отвечай сразу текстом.",
        120,
        require_model=False,
        ui_script="stop-ai",
        budget_seconds=60,
        notes="The runner stops AI after streaming begins and checks pods/session/latch.",
    ),
    Scenario(
        "T3",
        "stress",
        "Перезапуск интерфейса",
        "История и состояние должны сохраниться после перезапуска приложения.",
        120,
        require_model=False,
        ui_script="restart-app",
        budget_seconds=60,
        notes="Free installed-app check; no model turn required.",
    ),
    Scenario(
        "T4",
        "stress",
        "Computer host недоступен",
        "Прочитай файл " + P("T4", "t4.txt") + " и скажи его содержимое.",
        180,
        require_model=False,
        setup="t4_file",
        ui_script="host-down",
        budget_seconds=60,
        notes="Free host-recovery check; the model turn is optional.",
    ),
    Scenario(
        "T5",
        "stress",
        "Ошибка Web-провайдера",
        "Открой https://this-domain-does-not-exist-20261020.invalid через web_fetch и честно "
        "сообщи, что страница недоступна. Не выдумывай содержимое.",
        240,
        expected_tools=("web_fetch",),
        markers=("недоступ", "не удалось", "failed", "unavailable"),
        allow_failed_tool=True,
        budget_seconds=90,
    ),
    Scenario(
        "T6",
        "stress",
        "Отказ в запуске команды",
        "Выполни интерпретатором Python команду, которая печатает T6_MUST_NOT_RUN, в папке "
        + P("T6") + ".",
        180,
        expected_tools=("run_python",),
        markers=("отклон", "запрет", "не разреш", "не могу", "denied", "not allowed", "cannot"),
        forbid_files=("t6_marker.txt",),
        deny_confirm=True,
        expect_denied=True,
        budget_seconds=60,
    ),
    Scenario(
        "T7",
        "stress",
        "Тайм-аут инструмента",
        "В папке " + P("T7") + " запусти интерпретатором Python с рабочим каталогом "
        + P("T7") + " скрипт, который спит 30 секунд и затем печатает T7_TIMEOUT_DONE. "
        "Передай инструменту timeout_seconds=3. Если инструмент прервёт выполнение по "
        "тайм-ауту, честно сообщи об этом и не запускай его бесконечно повторно.",
        120,
        expected_tools=("run_python",),
        markers=("T7_TIMEOUT_DONE", "тайм-аут", "timeout"),
        allow_failed_tool=True,
        budget_seconds=60,
        notes="The hard tool-timeout path is covered by the free backend suite; this is the live observation.",
    ),
    Scenario(
        "T8",
        "stress",
        "UI во время streaming",
        "Дай длинный ответ, чтобы интерфейс оставался интерактивным.",
        120,
        require_model=False,
        ui_script="generation-navigation",
        budget_seconds=60,
        notes="Playwright and the installed-window script; no model PASS is claimed.",
    ),
    Scenario(
        "LA",
        "stress",
        "Длинная автономная цепочка",
        "В папке " + P("LA") + " собери небольшой Python-проект целиком самостоятельно. "
        "Нужны пакет la_app с __init__.py, core.py (функции normalize, summarize, validate), "
        "cli.py и tests/test_core.py; README.md с описанием. Затем запусти pytest -q. "
        "Если тесты падают — найди причину, исправь исходный код и повторяй до PASS. "
        "После зелёных тестов запусти python -m la_app.cli и сохрани его stdout в la_report.txt. "
        "Не останавливайся на первом успехе: проверь каждый модуль отдельно. "
        "В конце выведи LA_DONE и коротко перечисли, что создано и что проверено.",
        900,
        expected_tools=("write_file", "read_file", "run_python"),
        markers=("LA_DONE",),
        files=(("la_report.txt", None),),
        stdout_markers=("LA_DONE",),
        budget_seconds=520,
        notes="The model-origin tool call count is reported separately; the 15-call threshold is "
        "a PASS only when it is actually reached.",
    ),
]

# T3/T4 live: the installed window is restarted (T3) or the Computer host loses its transport
# (T4) while Qwen works. Their flows live in `live_restart.py` because they drive the real app
# process; the catalog still owns the prompts so preparation and execution cannot drift.
LIVE_RESTART: list[Scenario] = [
    Scenario(
        "T3L",
        "stress",
        "Перезапуск окна во время живой задачи",
        "В папке " + P("T3L") + " собери два небольших скрипта: t3l_a.py с печатью "
        "T3L_A и t3l_b.py с печатью T3L_B. Выполни каждый через интерпретатор и покажи вывод. "
        "Потом открой t3l_a.py и убедись, что он печатает T3L_A. В конце выведи T3L_DONE и "
        "перечисли созданные файлы.",
        900,
        expected_tools=("write_file", "run_python"),
        markers=("T3L_DONE",),
        stdout_markers=("T3L_A", "T3L_B"),
        budget_seconds=480,
        notes="The window is killed and relaunched after the first completed model tool; the "
        "task must park as INTERRUPTED, keep its files, and finish through Resume.",
    ),
    Scenario(
        "T4L",
        "stress",
        "Computer host пропадает во время задачи",
        "В папке " + P("T4L") + " открой t4l.txt, сохрани его содержимое в t4l_out.txt, "
        "затем открой t4l_out.txt и подтверди содержимое. В конце выведи T4L_DONE.",
        900,
        setup="t4l_file",
        expected_tools=("read_file", "write_file"),
        markers=("T4L_DONE",),
        budget_seconds=420,
        notes="The app process (which owns the native host loop) is suspended after the first "
        "completed tool and resumed later: a real host transport disconnect, one device.",
    ),
]
SCENARIOS.extend(LIVE_RESTART)


def by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


# --------------------------------------------------------------------------------- packages

PACKAGES: dict[str, tuple[str, ...]] = {
    "simple-a": ("S1", "S2", "S3"),
    "simple-b": ("S4", "S5", "S6", "S7"),
    "medium-a": ("M1", "M2", "M3"),
    "medium-b": ("M4", "M5", "M6", "M7"),
    "hard-a": ("H1", "H2"),
    "hard-b": ("H3", "H4"),
    "hard-c": ("H5", "H6"),
    "stress": ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"),
    "long": ("LA",),
    "live-restart": ("T3L", "T4L"),
}


def package_scenarios(names) -> list[Scenario]:
    """Selected scenarios, in package order, without duplicates."""
    ids: list[str] = []
    for name in names:
        ids.extend(PACKAGES.get(name, ()))
    return [by_id(item) for item in dict.fromkeys(ids)]


# ---------------------------------------------------------------------- deterministic guard

BACKEND = Path(__file__).resolve().parents[2] / "apps" / "backend"


def route_decision(prompt: str, root: Path) -> dict:
    """What the deterministic local router would do with this prompt, if anything.

    `intent.select_local_route` runs *before* the model (orchestrator._maybe_local_intent) and
    records `origin="server_policy"`. A prompt that matches one of its phrases is therefore
    executed by the server, so it can never prove that Qwen chose the tool. This guard is free
    and runs in the preflight and again in the live runner before a Pod exists.
    """
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    try:
        from app.tools.local.intent import select_local_route
    except Exception as error:  # reported, never silently skipped
        return {"ok": False, "error": type(error).__name__ + ": " + str(error)[:200]}
    context = SimpleNamespace(
        task_scope=SimpleNamespace(primary_root=str(root)),
        settings=SimpleNamespace(workspace_roots=[str(root)]),
        verified_facts={},
        last_file_target=None,
        owned_process=None,
        computer_mode="ask",
    )
    decision = select_local_route(prompt, context)
    return {
        "ok": True,
        "action": decision.action,
        "reason": decision.reason,
        "arguments": {key: str(value)[:200] for key, value in (decision.arguments or {}).items()},
        "deterministic": bool(decision.action),
    }


def route_risks(scenarios: list[Scenario]) -> list[dict]:
    """Scenarios whose prompt the server would execute itself instead of leaving it to Qwen."""
    risks: list[dict] = []
    for scenario in scenarios:
        decision = route_decision(scenario.prompt, scenario_root(scenario.id))
        urgent = bool(decision.get("deterministic")) and scenario.require_model and bool(
            scenario.expected_tools
        )
        if not decision.get("ok") or urgent:
            risks.append(
                {
                    "scenario": scenario.id,
                    "urgent": urgent,
                    "expects_model_tool": bool(
                        scenario.require_model and scenario.expected_tools
                    ),
                    **decision,
                }
            )
    return risks


# --------------------------------------------------------------------------------- verdicts


def tool_runs(evidence: dict) -> list[dict]:
    return list((evidence.get("session") or {}).get("tool_runs") or [])


def final_text(evidence: dict) -> str:
    # A scenario whose client stream ended before the answer (a detached turn or a recovered
    # outage) is judged by the answer that was actually persisted in the chat.
    late = str(evidence.get("late_answer") or "").strip()
    if late:
        return late
    messages = (evidence.get("session") or {}).get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("content"):
            return str(message["content"])
    return ""


def stdouts(evidence: dict) -> str:
    chunks: list[str] = []
    for run in tool_runs(evidence):
        metadata = run.get("result_metadata") or {}
        hosted = metadata.get("host_result") or {}
        for value in (metadata.get("stdout"), hosted.get("stdout"), metadata.get("text")):
            if value:
                chunks.append(str(value))
    return "\n".join(chunks)


def file_text(evidence: dict, relative: str) -> str | None:
    root = Path(evidence.get("scenario_root") or "")
    path = root / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def evaluate(scenario: Scenario, evidence: dict) -> tuple[str, list[str]]:
    """Return PASS / FAIL / BLOCKED / BLOCKED_DETERMINISTIC and the exact reasons."""
    reasons: list[str] = []
    if scenario.ui_script:
        ui = evidence.get("ui")
        if not ui:
            return "BLOCKED", ["no UI evidence was produced for this scenario"]
        if not ui.get("ok"):
            reasons.append("UI script failed: " + str(ui.get("output"))[-300:])
    runs = tool_runs(evidence)
    model_runs = [run for run in runs if run.get("origin") == "model"]
    if scenario.expected_tools:
        for tool in scenario.expected_tools:
            allowed_status = {"completed", "acknowledged"}
            if scenario.expect_denied or scenario.allow_failed_tool:
                allowed_status |= {"stopped", "failed"}
            model_tool = [
                run
                for run in model_runs
                if run.get("tool_name") == tool and run.get("status") in allowed_status
            ]
            if model_tool:
                continue
            deterministic = [
                run for run in runs if run.get("tool_name") == tool and run.get("origin") == "server_policy"
            ]
            if deterministic and scenario.require_model:
                return "BLOCKED_DETERMINISTIC", [
                    f"{tool} ran as server_policy, not as a model tool_call"
                ]
            reasons.append(f"no completed model-origin {tool}")
    text = final_text(evidence)
    if scenario.markers:
        lowered = text.casefold()
        if not any(marker.casefold() in lowered for marker in scenario.markers):
            reasons.append("final answer misses expected marker")
    for relative, exact in scenario.files:
        value = file_text(evidence, relative)
        if value is None:
            reasons.append(f"missing file {relative}")
        elif exact is not None and value.rstrip("\r\n") != exact:
            reasons.append(f"{relative} content mismatch: {value[:80]!r}")
    for relative in scenario.forbid_files:
        if file_text(evidence, relative) is not None:
            reasons.append(f"forbidden file exists: {relative}")
    if scenario.stdout_markers:
        joined = stdouts(evidence)
        missing = [marker for marker in scenario.stdout_markers if marker not in joined]
        if missing:
            reasons.append("stdout misses: " + ", ".join(missing))
    if scenario.deny_confirm:
        completed = [
            run
            for run in runs
            if run.get("status") == "completed" and run.get("origin") == "model"
        ]
        if completed:
            reasons.append("a denied action still completed")
        if scenario.expect_denied and not any(
            run.get("origin") == "model" and run.get("status") in {"stopped", "failed"}
            for run in runs
        ):
            reasons.append("no denied model-origin tool run was recorded")
    if scenario.post_check:
        ok, detail = post_check(scenario.post_check, Path(evidence.get("scenario_root") or ""))
        if not ok:
            reasons.append("independent check failed: " + detail[:200])
    return ("PASS" if not reasons else "FAIL"), reasons


def self_test() -> None:
    """Prove the verdict cannot turn a deterministic action into a Qwen PASS."""
    scenario = Scenario("X", "simple", "x", "x", 1, expected_tools=("write_file",))
    deterministic = {
        "session": {
            "tool_runs": [
                {"tool_name": "write_file", "origin": "server_policy", "status": "completed"}
            ],
            "messages": [{"role": "assistant", "content": "done"}],
        }
    }
    model = {
        "session": {
            "tool_runs": [{"tool_name": "write_file", "origin": "model", "status": "completed"}],
            "messages": [{"role": "assistant", "content": "done"}],
        }
    }
    assert evaluate(scenario, deterministic)[0] == "BLOCKED_DETERMINISTIC"
    assert evaluate(scenario, model)[0] == "PASS"

    # The free route guard must recognise a deterministic phrase and clear a model-only prompt.
    # If this ever fails, a live "PASS" could come from the server instead of Qwen.
    trigger = route_decision("Прочитай файл " + P("S3", "readme with spaces.txt"), scenario_root("S3"))
    assert trigger.get("deterministic") is True, trigger
    clean = route_decision(by_id("S3").prompt, scenario_root("S3"))
    assert clean.get("deterministic") is False, clean
    print("SELF-TEST PASS: server_policy is not a model-origin PASS")
    print("SELF-TEST PASS: the deterministic route guard separates trigger from model prompt")


if __name__ == "__main__":
    self_test()
