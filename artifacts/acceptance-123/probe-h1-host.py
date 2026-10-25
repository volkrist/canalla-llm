"""Free host proof for H1: the real Computer host runs the created project's CLI and tests.

No model, no GPU. It calls `/tools/execute` exactly like the model's tool call would, waits for
the Computer confirmation, approves it inside the sandbox and prints the real stdout.
"""

import json
import os
import time
import urllib.request

import common

TOKEN = common.token()
ROOT = r"C:\Users\Volkr\Canalla Acceptance\H1"


def run_call(name, arguments):
    status, text = common.call("POST", "/chats", token=TOKEN, body={"title": f"host probe {name}"})
    chat = json.loads(text)["id"]
    request = urllib.request.Request(
        common.BASE + "/tools/execute",
        data=json.dumps({"chat_id": chat, "name": name, "arguments": arguments}).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + TOKEN)

    started = time.monotonic()
    result = {}
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            event, data = "", ""
            for raw in response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("event: "):
                    event, data = line[7:].strip(), ""
                elif line.startswith("data: "):
                    data += line[6:]
                elif line == "" and event:
                    try:
                        value = json.loads(data) if data else {}
                    except ValueError:
                        value = {"raw": data[:200]}
                    if event == "tool" and value.get("status") == "waiting_confirmation":
                        common.confirm(TOKEN, value["id"], True)
                    if event == "tool_result":
                        result = value
                    if event == "error":
                        result = {"event": "error", "value": value}
                    event, data = "", ""
    except Exception as error:  # noqa: BLE001 - the probe reports, never hides
        return {"probe_error": f"{type(error).__name__}: {error}"}
    finally:
        common.call("DELETE", "/chats/" + chat, token=TOKEN)
    return result


def main():
    os.makedirs(ROOT, exist_ok=True)
    cli = run_call(
        "run_process",
        {
            "executable": "python",
            "argv": ["-m", "h1_cli"],
            "cwd": ROOT,
            "timeout_seconds": 60,
            "purpose": "free host proof: run the created CLI",
        },
    )
    print("CLI", json.dumps(cli, ensure_ascii=False)[:500])
    tests = run_call(
        "run_process",
        {
            "executable": "python",
            "argv": ["-m", "pytest", "-q"],
            "cwd": ROOT,
            "timeout_seconds": 120,
            "purpose": "free host proof: run the project tests",
        },
    )
    print("PYTEST", json.dumps(tests, ensure_ascii=False)[:500])
    cli_ok = "H1_CLI_OK" in str((cli.get("metadata") or {}).get("stdout") or "")
    tests_ok = (tests.get("metadata") or {}).get("exit_code") == 0
    print("H1 HOST PROOF:", "PASS" if cli_ok and tests_ok else "FAIL")


if __name__ == "__main__":
    main()
