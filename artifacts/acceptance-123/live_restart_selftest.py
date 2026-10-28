"""Free self-test of the T3/T4 mechanics: window launch, CDP, restart, backend lifecycle.

It never needs a model: it proves that the harness can start the installed window, type into it,
stop it, bring it back, and read the task state afterwards. Only the parts that need Qwen stay
for the paid run.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import live_restart as lr  # noqa: E402


def main() -> int:
    token = common.token()
    report: dict = {}

    report["window_initial"] = lr.ensure_window()
    report["cdp_ready"] = lr.cdp_ready()
    report["backend_ready"] = lr.health_ok()

    before = {str(row.get("id")) for row in lr.chats(token)}
    probe = Path(common.SANDBOX) / "_t3_probe.txt"
    probe.write_text("Проверка механики перезапуска: ответь ОК.", encoding="utf-8")
    report["send"] = lr.node("send", str(probe))[:2]
    time.sleep(6)
    chat_id = lr.newest_chat_id(token, exclude=before)
    report["chat_created"] = bool(chat_id)
    report["state_before_stop"] = lr.node("state")[1]

    report["stop"] = lr.stop_window()
    report["backend_went_down"] = bool(lr.wait(lambda: not lr.health_ok(), seconds=60))
    report["relaunch"] = lr.ensure_window()
    report["backend_came_up"] = bool(lr.wait(lambda: lr.health_ok() or None, seconds=180))
    time.sleep(8)
    report["state_after_relaunch"] = lr.node("state")[1]

    device = json.loads((Path(common.ROOT) / "device.json").read_text(encoding="utf-8"))
    report["device_id"] = device.get("device_id")

    if chat_id:
        status, _ = common.call("DELETE", "/chats/" + chat_id, token=token)
        report["probe_chat_deleted"] = status

    ok = bool(
        report["cdp_ready"]
        and report["chat_created"]
        and report["backend_went_down"]
        and report["backend_came_up"]
        and report["device_id"]
    )
    report["verdict"] = "PASS" if ok else "FAIL"
    out = Path("artifacts/final-acceptance/restart-selftest-2026-10-28.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("cdp_ready", "chat_created", "backend_went_down", "backend_came_up", "verdict")}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
