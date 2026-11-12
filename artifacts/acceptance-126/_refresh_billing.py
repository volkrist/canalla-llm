"""Refresh only the billing observation: the balance accrues storage slowly after a cycle closes."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent / "coordinator" / "state.json"
BALANCE_NOW = 8.7574929504
BALANCE_START = 10.1309
BUDGET = 1.45

state = json.loads(STATE.read_text(encoding="utf-8"))
measured = round(BALANCE_START - BALANCE_NOW, 4)
billing = dict(state.get("billing") or {})
billing.update(
    measured_at=datetime.now(timezone.utc).isoformat(),
    balance_now_usd=BALANCE_NOW,
    measured_spend_usd=measured,
    remaining_usd=round(BUDGET - measured, 4),
    note=(
        "The balance delta is authoritative. Repeated reads after the cycle closed differ by a "
        "few tenths of a cent (storage accrual), never by Pod time: pods [] on every read."
    ),
)
state["billing"] = billing
if isinstance(state.get("cycle_closed"), dict):
    state["cycle_closed"]["measured_spend_usd"] = measured
    state["cycle_closed"]["remaining_usd"] = round(BUDGET - measured, 4)
    state["cycle_closed"]["balance_now_usd"] = BALANCE_NOW
if isinstance(state.get("final_outcome"), dict):
    state["final_outcome"]["measured_spend_usd"] = measured
state["updated_at"] = billing["measured_at"]

tmp = STATE.with_suffix(".json.tmp")
tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
os.replace(tmp, STATE)
print(json.dumps({"measured_spend_usd": measured, "remaining_usd": billing["remaining_usd"],
                  "balance_now_usd": BALANCE_NOW}, indent=1))
