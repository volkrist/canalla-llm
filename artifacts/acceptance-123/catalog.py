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


# ------------------------------------------------------------------- LA mini project seed
#
# The first 1.2.6 LA challenge ("build la_app") was finished by the model in 12-14 useful calls.
# That is a real task, but it does not exercise the 15+ call regime the release gate asks for.
# This seed is a real, coherent order-processing project with a spec, two data files, a package
# that is deliberately broken in two places and fixed tests that describe the contract. Reading
# the data, reading the four modules, running the failing suite, fixing the parser and the rules,
# creating report/cli/README, re-running the suite, running the CLI, reading its output and
# verifying it is naturally more than fifteen model-origin calls. No call is requested merely to
# raise a counter.

LA_SPEC = """# Mini order pipeline (acceptance)

Read this spec before writing code. Do not modify `data/orders.csv` or `data/rates.csv`.

## Inputs

* `data/orders.csv` — header `id,customer,quantity,unit_price,currency`
* `data/rates.csv` — header `currency,rate_to_usd`

The orders file is UTF-8 with BOM, may contain quoted commas, blank lines and malformed rows.

## Package `shop`

### parser.py

* `read_rates(path) -> dict[str, Decimal]` — keys uppercase (`USD`, `EUR`, ...).
* `read_orders(path, rates) -> tuple[list[dict], list[dict]]`.
  * A valid order is `{id, customer, quantity (int), unit_price (Decimal), currency (uppercase)}`.
  * Use the `csv` module. Strip whitespace from every field; the currency is case-insensitive.
  * Skip, without crashing: blank lines, rows with the wrong column count (`malformed_row`),
    an empty customer (`empty_customer`), a duplicate id, keeping the first (`duplicate_id`),
    quantity <= 0 (`invalid_quantity`), unit_price < 0 (`invalid_quantity`), and a currency that
    is not in the rates (`unknown_currency`).
  * Each skipped item is `{line, id, reason}` where `line` is the 1-based file line.

### rules.py

* `discount_for(subtotal) -> Decimal` — fraction: `>= 200` → `0.10`, `>= 100` → `0.05`, else `0`.
* `convert(amount, currency, rates) -> Decimal` — amount multiplied by the rate, rounded HALF_UP
  to 2 places.
* `price_order(order, rates) -> dict` with `subtotal_usd`, `discount_usd`, `total_usd` (Decimal).
  Round each money value HALF_UP to 2 places.

### report.py

* `build_payload(orders, skipped, rates) -> dict`
* `render_report(payload) -> str` — Markdown that contains these three lines (with the real values):
  `- Valid orders: <n>`, `- Skipped rows: <n>`, `- Total USD: <amount>`.

### cli.py

```
python -m shop.cli --input data/orders.csv --rates data/rates.csv \\
  --output out/orders.json --report out/report.md
```

It writes both files and exits 0. `out/orders.json` is:

```json
{"orders": [{"id", "customer", "quantity", "unit_price", "currency",
             "subtotal_usd", "discount_usd", "total_usd"}],
 "summary": {"valid": 0, "skipped": 0, "total_usd": "0.00"}}
```

Every money value is a string with exactly two decimals. Orders are sorted by id.
The CLI prints a short summary that ends with `LA2_DONE`.

### README.md

How to run the CLI and the tests, and what the pipeline does.

## Verification order (do all of it)

1. `python -m pytest -q` — it fails on the seeded defects; fix the source, not the tests.
2. `python -m shop.cli --input data/orders.csv --rates data/rates.csv --output out/orders.json --report out/report.md`
3. `python -m pytest -q`

Do not modify `data/*.csv`, `tests/test_parser.py` or `tests/test_rules.py`, and do not delete a
failing test to make the suite pass. Finish with a short report and the token `LA2_DONE`.
"""

LA_RATES = "currency,rate_to_usd\nUSD,1.00\nEUR,1.10\nGBP,1.25\n"

LA_ORDERS = (
    "\ufeffid,customer,quantity,unit_price,currency\n"
    "A-1001,Acme,2,49.50,USD\n"
    "A-1002,Acme,2,60.00,USD\n"
    'A-1003,"Smith, Jones",1,120.00,EUR\n'
    "A-1004,Beta,3,10.00,GBP\n"
    "A-1005,,1,5.00,USD\n"
    "A-1001,Duplicate,1,1.00,USD\n"
    "A-1007,Zero,0,10.00,USD\n"
    "A-1008,Negative,-1,10.00,USD\n"
    "A-1009,Free,1,0.00,USD\n"
    "A-1010,Unknown,1,10.00,JPY\n"
    "A-1011,Spaced, 2 , 15.50 , usd\n"
    "A-1012,Quote,1,19.99,GBP\n"
    "A-1013,Missing,columns\n"
)

LA_PARSER = '''"""Read the order CSVs. Seeded with two real defects on purpose."""

from decimal import Decimal


def read_rates(path):
    rates = {}
    with open(path, encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            currency, rate = line.strip().split(",")
            rates[currency.upper()] = Decimal(rate)
    return rates


def read_orders(path, rates):
    orders, skipped = [], []
    with open(path, encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            if not line.strip():
                continue
            order_id, customer, quantity, price, currency = line.strip().split(",")
            orders.append(
                {
                    "id": order_id,
                    "customer": customer,
                    "quantity": int(quantity),
                    "unit_price": Decimal(price),
                    "currency": currency.upper(),
                }
            )
    return orders, skipped
'''

LA_RULES = '''"""Business rules. The two stubs below are the second seeded defect."""

from decimal import Decimal


def discount_for(subtotal):
    return Decimal("0")


def convert(amount, currency, rates):
    return Decimal(amount)


def price_order(order, rates):
    subtotal = Decimal(order["quantity"]) * Decimal(order["unit_price"])
    discount = subtotal * discount_for(subtotal)
    return {
        "subtotal_usd": subtotal,
        "discount_usd": discount,
        "total_usd": subtotal - discount,
    }
'''

LA_TEST_PARSER = '''from decimal import Decimal

from shop.parser import read_orders, read_rates


def test_rates_are_normalized():
    rates = read_rates("data/rates.csv")
    assert rates["USD"] == Decimal("1.00")
    assert rates["EUR"] == Decimal("1.10")
    assert rates["GBP"] == Decimal("1.25")


def test_orders_order_and_edge_cases():
    rates = read_rates("data/rates.csv")
    orders, skipped = read_orders("data/orders.csv", rates)
    assert [order["id"] for order in orders] == [
        "A-1001",
        "A-1002",
        "A-1003",
        "A-1004",
        "A-1009",
        "A-1011",
        "A-1012",
    ]
    by_id = {order["id"]: order for order in orders}
    assert by_id["A-1003"]["customer"] == "Smith, Jones"
    assert by_id["A-1011"]["currency"] == "USD"
    assert by_id["A-1011"]["quantity"] == 2
    assert by_id["A-1011"]["unit_price"] == Decimal("15.50")
    assert len(skipped) == 6
    reasons = {item["id"]: item["reason"] for item in skipped}
    assert reasons["A-1005"] == "empty_customer"
    assert reasons["A-1001"] == "duplicate_id"
    assert reasons["A-1007"] == "invalid_quantity"
    assert reasons["A-1008"] == "invalid_quantity"
    assert reasons["A-1010"] == "unknown_currency"
    assert reasons["A-1013"] == "malformed_row"
'''

LA_TEST_RULES = '''from decimal import Decimal

from shop.rules import convert, discount_for, price_order


def test_discount_bands():
    assert discount_for(Decimal("99.99")) == Decimal("0")
    assert discount_for(Decimal("100")) == Decimal("0.05")
    assert discount_for(Decimal("199.99")) == Decimal("0.05")
    assert discount_for(Decimal("200")) == Decimal("0.10")


def test_convert_and_price_order():
    rates = {"EUR": Decimal("1.10"), "USD": Decimal("1.00")}
    assert convert(Decimal("100"), "EUR", rates) == Decimal("110.00")
    order = {
        "id": "A-1",
        "customer": "x",
        "quantity": 2,
        "unit_price": Decimal("60.00"),
        "currency": "USD",
    }
    priced = price_order(order, rates)
    assert priced["subtotal_usd"] == Decimal("120.00")
    assert priced["discount_usd"] == Decimal("6.00")
    assert priced["total_usd"] == Decimal("114.00")
'''

LA_SEED_FILES = {
    "SPEC.md": LA_SPEC,
    "data/rates.csv": LA_RATES,
    "data/orders.csv": LA_ORDERS,
    "shop/__init__.py": "",
    "shop/parser.py": LA_PARSER,
    "shop/rules.py": LA_RULES,
    "tests/test_parser.py": LA_TEST_PARSER,
    "tests/test_rules.py": LA_TEST_RULES,
}


def _seed_la_project(root: Path) -> None:
    for relative, content in LA_SEED_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _money(value) -> str:
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def la_expected() -> dict:
    """The independent answer, computed without importing the model's code."""
    from decimal import ROUND_HALF_UP, Decimal

    rates = {"USD": Decimal("1.00"), "EUR": Decimal("1.10"), "GBP": Decimal("1.25")}
    source = [
        ("A-1001", "Acme", 2, "49.50", "USD"),
        ("A-1002", "Acme", 2, "60.00", "USD"),
        ("A-1003", "Smith, Jones", 1, "120.00", "EUR"),
        ("A-1004", "Beta", 3, "10.00", "GBP"),
        ("A-1009", "Free", 1, "0.00", "USD"),
        ("A-1011", "Spaced", 2, "15.50", "USD"),
        ("A-1012", "Quote", 1, "19.99", "GBP"),
    ]
    orders = []
    total = Decimal("0")
    for order_id, customer, quantity, price, currency in source:
        subtotal = (Decimal(quantity) * Decimal(price) * rates[currency]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        fraction = Decimal("0.10") if subtotal >= 200 else Decimal("0.05") if subtotal >= 100 else Decimal("0")
        discount = (subtotal * fraction).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        amount = (subtotal - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total += amount
        orders.append(
            {
                "id": order_id,
                "customer": customer,
                "quantity": quantity,
                "unit_price": _money(price),
                "currency": currency,
                "subtotal_usd": _money(subtotal),
                "discount_usd": _money(discount),
                "total_usd": _money(amount),
            }
        )
    return {
        "orders": sorted(orders, key=lambda item: item["id"]),
        "summary": {"valid": len(orders), "skipped": 6, "total_usd": _money(total)},
    }


@dataclass(frozen=True)
class Scenario:
    id: str
    tier: str
    title: str
    prompt: str
    timeout: int
    expected_tools: tuple[str, ...] = ()
    # Groups of equivalent tools. A real task may use `write_file` or `patch_file`, and
    # `run_python` or `run_process`: the requirement is that the model did the work, not which
    # of two valid tools it chose.
    expected_tools_any: tuple[tuple[str, ...], ...] = ()
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
    elif name == "la_project":
        _seed_la_project(root)


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
    if name == "la_project":
        return _check_la_project(root)
    return True, "no post check"


def _check_la_project(root: Path) -> tuple[bool, str]:
    """Independent verification of the LA mini project.

    Nothing here imports `shop`: the expected payload is computed in `la_expected`, the seeded
    tests and data are hash-checked, and the CLI the model wrote is executed in a fresh output
    directory. A model that deletes or rewrites a test cannot turn this into a pass.
    """
    problems: list[str] = []
    for relative in (
        "shop/parser.py",
        "shop/rules.py",
        "shop/report.py",
        "shop/cli.py",
        "README.md",
        "out/orders.json",
        "out/report.md",
    ):
        if not (root / relative).is_file():
            problems.append("missing " + relative)
    for relative in ("data/orders.csv", "data/rates.csv", "tests/test_parser.py", "tests/test_rules.py"):
        expected = LA_SEED_FILES[relative]
        actual = (root / relative).read_text(encoding="utf-8") if (root / relative).is_file() else ""
        if hashlib.sha256(actual.encode("utf-8")).hexdigest() != hashlib.sha256(
            expected.encode("utf-8")
        ).hexdigest():
            problems.append("seeded file was modified: " + relative)
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if tests.returncode != 0:
        problems.append("pytest failed: " + (tests.stdout[-300:] + tests.stderr[-200:]))
    verify_json = root / "out" / "verify_orders.json"
    verify_md = root / "out" / "verify_report.md"
    verify_json.parent.mkdir(parents=True, exist_ok=True)
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "shop.cli",
            "--input",
            "data/orders.csv",
            "--rates",
            "data/rates.csv",
            "--output",
            str(verify_json),
            "--report",
            str(verify_md),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if cli.returncode != 0:
        problems.append("cli failed: " + (cli.stdout[-200:] + cli.stderr[-200:]))
    expected_payload = la_expected()
    for relative in ("out/orders.json", str(verify_json.relative_to(root))):
        path = root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            problems.append(relative + " is not JSON: " + str(error)[:120])
            continue
        if payload != expected_payload:
            problems.append(relative + " does not match the independent result")
    report_text = (root / "out" / "report.md").read_text(encoding="utf-8") if (root / "out" / "report.md").is_file() else ""
    for line in (
        "- Valid orders: 7",
        "- Skipped rows: 6",
        "- Total USD: 431.89",
    ):
        if line not in report_text:
            problems.append("report misses: " + line)
    if "LA2_DONE" not in cli.stdout and "LA2_DONE" not in cli.stderr:
        problems.append("cli did not print LA2_DONE")
    return (not problems, "; ".join(problems)[:600] or "ok")


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
        "В папке " + P("LA") + " находится небольшой Python-проект с двумя намеренно "
        "сломанными модулями и файлом SPEC.md с точной спецификацией. Начни со спецификации, "
        "изучи data/orders.csv и data/rates.csv, разберись в тестах tests/test_parser.py и "
        "tests/test_rules.py, исправь shop/parser.py и shop/rules.py, создай shop/report.py, "
        "shop/cli.py и README.md строго по спецификации. Затем выполни ровно такой порядок: "
        "(1) python -m pytest -q; (2) python -m shop.cli --input data/orders.csv --rates "
        "data/rates.csv --output out/orders.json --report out/report.md; (3) python -m pytest -q. "
        "Если проверки падают — найди настоящую причину, исправь исходный код и повторяй до PASS. "
        "Не меняй data/*.csv и не удаляй и не переписывай данные тесты. Перед финальным ответом "
        "убедись, что созданные JSON и Markdown соответствуют значениям из спецификации. "
        "В конце выведи LA2_DONE и коротко перечисли, какие файлы созданы и какие проверки прошли.",
        1200,
        expected_tools=(),
        expected_tools_any=(
            ("list_directory",),
            ("read_file",),
            ("write_file", "patch_file"),
            ("run_python", "run_process"),
        ),
        markers=(),
        files=(("out/orders.json", None), ("out/report.md", None)),
        stdout_markers=("LA2_DONE",),
        setup="la_project",
        post_check="la_project",
        budget_seconds=480,
        notes="The model-origin tool call count is reported separately. The seeded project needs "
        "both modules fixed, report.py and cli.py created, the failing suite run twice and the "
        "CLI output verified; 18-25 useful calls are expected naturally, never requested.",
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
        "Потом открой t3l_a.py и убедись, что он печатает T3L_A. Затем запусти интерпретатором "
        "Python с рабочим каталогом этой папки команду -c \"import time; time.sleep(10); "
        "print('T3L_SLOW_OK')\" и дождись её завершения: это проверка, что длинный процесс "
        "переживает перезапуск окна. В конце выведи T3L_DONE и перечисли созданные файлы.",
        1200,
        expected_tools=(),
        expected_tools_any=(("write_file", "patch_file"), ("run_python", "run_process")),
        markers=("T3L_DONE",),
        stdout_markers=("T3L_A", "T3L_B", "T3L_SLOW_OK"),
        budget_seconds=480,
        notes="The window is killed while a native run is in flight (the 10 s sleep), so the "
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


def origin_counts(evidence: dict) -> dict:
    """Model-origin calls are the only ones the long-autonomy gate counts.
    `server_policy` is the controller's own decision, and a harness confirmation is not a model
    call at all. Reporting them separately keeps an efficient model from looking like a broken
    one and keeps a chatty server from looking like an autonomous one.
    """
    runs = tool_runs(evidence)
    counts = {
        "MODEL_ORIGIN_CALLS": 0,
        "INTERNAL_CALLS": 0,
        "HARNESS_CALLS": 0,
        "RETRIES": 0,
        "DENIED_CALLS": 0,
    }
    for run in runs:
        origin = str(run.get("origin") or "")
        status = str(run.get("status") or "")
        if origin == "model":
            counts["MODEL_ORIGIN_CALLS"] += 1
        elif origin in {"server_policy", "internal"}:
            counts["INTERNAL_CALLS"] += 1
        else:
            counts["HARNESS_CALLS"] += 1
        if status in {"stopped", "waiting_confirmation", "failed"} and origin != "model":
            continue
        if status in {"failed"}:
            counts["RETRIES"] += 1
        if status in {"stopped"}:
            counts["DENIED_CALLS"] += 1
    counts["COMPLETED_MODEL_CALLS"] = sum(
        1
        for run in runs
        if run.get("origin") == "model" and run.get("status") in {"completed", "acknowledged"}
    )
    return counts


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
    for group in scenario.expected_tools_any:
        allowed = {"completed", "acknowledged"}
        if scenario.expect_denied or scenario.allow_failed_tool:
            allowed |= {"stopped", "failed"}
        if any(run.get("tool_name") in group and run.get("status") in allowed for run in model_runs):
            continue
        reasons.append("no completed model-origin tool from " + "|".join(group))
    text = final_text(evidence)
    # D2 applies to every answer: raw protocol, a tool echo or an empty bubble is never a PASS.
    if "<tool_call>" in text:
        reasons.append("tool protocol leaked into the final answer")
    if scenario.id == "LA":
        stripped = text.strip()
        if len(stripped) < 40:
            reasons.append("final answer is missing or too short")
        if not any(
            marker in stripped
            for marker in (
                "Задача завершена",
                "Задача завершилась с ошибкой",
                "тест",
                "pytest",
                "файл",
                "out/orders.json",
            )
        ):
            reasons.append("final answer does not report the work or the controller status")
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
