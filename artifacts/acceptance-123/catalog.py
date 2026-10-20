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
        "Прочитай файл " + S3_FILE + " через read_file и перескажи его содержимое одним предложением.",
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
        "В папке " + P("S4") + " создай файл exact.txt с содержимым ровно S4_EXACT_OK_20261020 "
        "и затем прочитай его обратно через read_file.",
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
        "В папке " + P("S5") + " создай s5_hello.py с кодом print('S5_STDOUT_OK') и запусти "
        "его через run_python (cwd " + P("S5") + "). Верни только stdout.",
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
        "Медленно объясни в трёх абзацах, зачем нужен детерминированный контроллер в агенте.",
        240,
        require_model=False,
        markers=(),
        ui_script="generation-navigation",
        budget_seconds=90,
    ),
    Scenario(
        "M1",
        "medium",
        "Программа: создать, запустить, изменить, повторить",
        "В папке " + P("M1") + " создай m1_hello.py с кодом print(2 + 2), запусти через run_python "
        "(cwd " + P("M1") + "), получи stdout, измени код на print(2 * 3), снова запусти и "
        "верни оба значения stdout.",
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
        "Прочитай " + P("M2", "m2_sales.csv") + ", посчитай суммарную выручку (units*price), "
        "суммарные units, минимальную и максимальную выручку по строке. Создай "
        + P("M2", "m2_stats.csv") + " с этими числами и " + P("M2", "m2_report.txt")
        + " с кратким отчётом. Используй run_python для расчёта.",
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
        "В папке " + P("M3") + " создай пакет m3_app из трёх файлов: __init__.py, core.py, "
        "__main__.py. core.py возвращает строку 'M3_PROJECT_OK', __main__.py печатает её. "
        "Запусти python -m m3_app через run_python (cwd " + P("M3") + ") и верни stdout.",
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
        "Открой https://example.com через web_fetch, сохрани точный заголовок в "
        + P("M5", "m5_title.txt") + " через write_file, затем через run_python прочитай файл "
        "и напечатай заголовок в верхнем регистре. Верни stdout.",
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
        "В папке " + P("M6") + " создай m6_state.py, который печатает 'M6_STATE_OK', и запусти "
        "его через run_python. Не создавай файл дважды. Верни stdout.",
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
        "В папке " + P("M7") + " создай m7_denied.txt с текстом M7_MUST_NOT_EXIST.",
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
        "В папке " + P("H6") + " создай h6_probe.py, который печатает 'H6_RECOVERED', и запусти "
        "его через run_python. Не повторяй write_file после временного сетевого сбоя.",
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
        "В папке " + P("T1") + " создай три файла: t1_a.py, t1_b.py, t1_c.py. Каждый печатает "
        "свой маркер T1_A/T1_B/T1_C. Запусти все три через run_python и верни три строки.",
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
        "Напиши очень длинное эссе о надёжности автономных агентов, не менее 2000 слов.",
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
        "Запусти через run_python команду, которая печатает T6_MUST_NOT_RUN, в папке " + P("T6") + ".",
        180,
        expected_tools=("run_python",),
        forbid_files=("t6_marker.txt",),
        deny_confirm=True,
        budget_seconds=60,
    ),
    Scenario(
        "T7",
        "stress",
        "Тайм-аут инструмента",
        "Запусти Python-скрипт, который спит 5 секунд и печатает T7_TIMEOUT_DONE; дождись "
        "завершения или корректно сообщи о тайм-ауте.",
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
]


def by_id(scenario_id: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


# --------------------------------------------------------------------------------- verdicts


def tool_runs(evidence: dict) -> list[dict]:
    return list((evidence.get("session") or {}).get("tool_runs") or [])


def final_text(evidence: dict) -> str:
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
    if scenario.ui_script and not evidence.get("ui"):
        return "BLOCKED", ["UI evidence is produced by the separate free UI script"]
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
        elif exact is not None and value != exact:
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
    print("SELF-TEST PASS: server_policy is not a model-origin PASS")


if __name__ == "__main__":
    self_test()
