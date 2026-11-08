"""Free self-test of the 1.2.6 LA mini-project fixture and its independent checker.

It proves three things without a GPU and without a model:

1. the seeded project really fails (`_check_la_project` is not vacuous);
2. a correct reference implementation makes the seeded tests and the independent checker pass;
3. editing a provided test or data file is detected instead of being accepted.

Run from `artifacts/acceptance-123` with the backend venv:
    python ../../artifacts/acceptance-126/la_fixture_selftest.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACC = HERE.parent / "acceptance-123"
sys.path.insert(0, str(ACC))
import catalog  # noqa: E402

ROOT = Path(r"C:\Users\Volkr\Canalla Acceptance\_la_selftest")
PY = sys.executable


def seed() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    catalog.setup_fixture("la_project", ROOT)


def solve() -> None:
    (ROOT / "shop" / "parser.py").write_text(
        '''
import csv
from decimal import Decimal


def read_rates(path):
    rates = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            currency = (row.get("currency") or "").strip().upper()
            rate = (row.get("rate_to_usd") or "").strip()
            if currency and rate:
                rates[currency] = Decimal(rate)
    return rates


def read_orders(path, rates):
    orders, skipped, seen = [], [], set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for line_number, row in enumerate(reader, start=2):
            if not row or not any((cell or "").strip() for cell in row):
                continue
            if len(row) != 5:
                skipped.append({"line": line_number, "id": row[0].strip(), "reason": "malformed_row"})
                continue
            order_id, customer, quantity, price, currency = (cell.strip() for cell in row)
            currency = currency.upper()
            if not customer:
                skipped.append({"line": line_number, "id": order_id, "reason": "empty_customer"})
                continue
            if order_id in seen:
                skipped.append({"line": line_number, "id": order_id, "reason": "duplicate_id"})
                continue
            if currency not in rates:
                skipped.append({"line": line_number, "id": order_id, "reason": "unknown_currency"})
                continue
            try:
                quantity_value = int(quantity)
                price_value = Decimal(price)
            except (ValueError, ArithmeticError):
                skipped.append({"line": line_number, "id": order_id, "reason": "invalid_quantity"})
                continue
            if quantity_value <= 0 or price_value < 0:
                skipped.append({"line": line_number, "id": order_id, "reason": "invalid_quantity"})
                continue
            seen.add(order_id)
            orders.append(
                {
                    "id": order_id,
                    "customer": customer,
                    "quantity": quantity_value,
                    "unit_price": price_value,
                    "currency": currency,
                }
            )
    return orders, skipped
''',
        encoding="utf-8",
    )
    (ROOT / "shop" / "rules.py").write_text(
        '''
from decimal import ROUND_HALF_UP, Decimal


def _round(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def discount_for(subtotal):
    subtotal = Decimal(subtotal)
    if subtotal >= 200:
        return Decimal("0.10")
    if subtotal >= 100:
        return Decimal("0.05")
    return Decimal("0")


def convert(amount, currency, rates):
    return _round(Decimal(amount) * rates[currency.upper()])


def price_order(order, rates):
    amount = convert(Decimal(order["quantity"]) * Decimal(order["unit_price"]), order["currency"], rates)
    discount = _round(amount * discount_for(amount))
    return {
        "subtotal_usd": amount,
        "discount_usd": discount,
        "total_usd": _round(amount - discount),
    }
''',
        encoding="utf-8",
    )
    (ROOT / "shop" / "report.py").write_text(
        '''
import json
from decimal import Decimal

from .rules import price_order


def _money(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def build_payload(orders, skipped, rates):
    priced, total = [], Decimal("0")
    for order in sorted(orders, key=lambda item: item["id"]):
        values = price_order(order, rates)
        total += values["total_usd"]
        priced.append(
            {
                "id": order["id"],
                "customer": order["customer"],
                "quantity": order["quantity"],
                "unit_price": _money(order["unit_price"]),
                "currency": order["currency"],
                "subtotal_usd": _money(values["subtotal_usd"]),
                "discount_usd": _money(values["discount_usd"]),
                "total_usd": _money(values["total_usd"]),
            }
        )
    return {"orders": priced, "summary": {"valid": len(priced), "skipped": len(skipped), "total_usd": _money(total)}}


def render_report(payload):
    summary = payload["summary"]
    return (
        "# Orders report\\n\\n"
        f"- Valid orders: {summary['valid']}\\n"
        f"- Skipped rows: {summary['skipped']}\\n"
        f"- Total USD: {summary['total_usd']}\\n"
    )
''',
        encoding="utf-8",
    )
    (ROOT / "shop" / "cli.py").write_text(
        '''
import argparse
import json
from pathlib import Path

from .parser import read_orders, read_rates
from .report import build_payload, render_report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--rates", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    rates = read_rates(args.rates)
    orders, skipped = read_orders(args.input, rates)
    payload = build_payload(orders, skipped, rates)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_report(payload), encoding="utf-8")
    print(
        f"valid={payload['summary']['valid']} skipped={payload['summary']['skipped']} "
        f"total={payload['summary']['total_usd']} LA2_DONE"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    (ROOT / "README.md").write_text("# Orders\n\nRun the CLI and pytest.\n", encoding="utf-8")
    subprocess.run(
        [
            PY,
            "-m",
            "shop.cli",
            "--input",
            "data/orders.csv",
            "--rates",
            "data/rates.csv",
            "--output",
            "out/orders.json",
            "--report",
            "out/report.md",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    failures = 0

    seed()
    before_ok, before_detail = catalog._check_la_project(ROOT)
    print("[%s] seeded project fails the independent check — %s" % ("PASS" if not before_ok else "FAIL", before_detail[:160]))
    failures += 0 if not before_ok else 1

    tests = subprocess.run([PY, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True)
    print("[%s] seeded tests fail before the fix" % ("PASS" if tests.returncode != 0 else "FAIL"))
    failures += 0 if tests.returncode != 0 else 1

    solve()
    after_ok, after_detail = catalog._check_la_project(ROOT)
    print("[%s] reference solution passes — %s" % ("PASS" if after_ok else "FAIL", after_detail[:200]))
    failures += 0 if after_ok else 1

    payload = json.loads((ROOT / "out" / "orders.json").read_text(encoding="utf-8"))
    expected = catalog.la_expected()
    print("[%s] output payload matches the independent result" % ("PASS" if payload == expected else "FAIL"))
    failures += 0 if payload == expected else 1

    (ROOT / "tests" / "test_rules.py").write_text("# deleted\n", encoding="utf-8")
    tampered_ok, tampered_detail = catalog._check_la_project(ROOT)
    detected = (not tampered_ok) and "seeded file was modified: tests/test_rules.py" in tampered_detail
    print("[%s] editing a provided test is detected — %s" % ("PASS" if detected else "FAIL", tampered_detail[:160]))
    failures += 0 if detected else 1

    print("SELF-TEST", "PASS" if not failures else "FAIL")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
