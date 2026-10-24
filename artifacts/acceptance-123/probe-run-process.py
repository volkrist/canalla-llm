"""Free probe: stream the events of one explicit run_process call (no model, no GPU)."""

import json
import os
import time
import urllib.request

import common

TOKEN = common.token()
ROOT = str(common.SANDBOX / "PROBE")
os.makedirs(ROOT, exist_ok=True)

status, text = common.call("POST", "/chats", token=TOKEN, body={"title": "run_process stream probe"})
chat = json.loads(text)["id"]
request = urllib.request.Request(
    common.BASE + "/tools/execute",
    data=json.dumps(
        {
            "chat_id": chat,
            "name": "run_process",
            "arguments": {
                "executable": "python",
                "argv": ["-c", "print(1 + 1)"],
                "cwd": ROOT,
                "timeout_seconds": 30,
                "purpose": "free probe",
            },
        }
    ).encode("utf-8"),
    method="POST",
)
request.add_header("Content-Type", "application/json")
request.add_header("Authorization", "Bearer " + TOKEN)

started = time.monotonic()
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        event, data = "", ""
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event: "):
                event, data = line[7:].strip(), ""
            elif line.startswith("data: "):
                data += line[6:]
            elif line == "" and event:
                elapsed = round((time.monotonic() - started) * 1000)
                try:
                    value = json.loads(data) if data else {}
                except ValueError:
                    value = {"raw": data[:200]}
                print(elapsed, event, json.dumps(value, ensure_ascii=False)[:400])
                if event == "tool" and value.get("status") == "waiting_confirmation":
                    print("  confirm ->", common.confirm(TOKEN, value["id"], True))
                event, data = "", ""
except Exception as error:  # noqa: BLE001 - the probe reports, never hides
    print("PROBE ERROR", type(error).__name__, str(error)[:200])
finally:
    common.call("DELETE", "/chats/" + chat, token=TOKEN)
