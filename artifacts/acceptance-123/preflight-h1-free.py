"""Free H1 readiness check on the real installed host.

It creates the exact five files through `write_file`, runs the CLI and pytest through
`run_process` with a bare `python`, and verifies the bytes independently. This is a host
readiness check, not a model PASS: the live H1 run still has to be performed by Qwen.
"""

import hashlib
import json
import os
import shutil
import time
import urllib.request

import common

TOKEN = common.token()
ROOT = r"C:\Users\Volkr\Canalla Acceptance\H1"

FILES = {
    "h1_cli/__init__.py": '"""h1_cli package."""\n\n__all__ = ["core"]\n',
    "h1_cli/core.py": (
        '"""Core logic for the h1_cli package."""\n\n'
        'BANNER = "H1_CLI_OK"\n\n\n'
        'def build_banner() -> str:\n'
        '    """Return the canonical CLI banner string."""\n'
        "    return BANNER\n"
    ),
    "h1_cli/__main__.py": (
        '"""CLI entrypoint: python -m h1_cli prints H1_CLI_OK."""\n\n'
        "from h1_cli.core import build_banner\n\n\n"
        "def main() -> int:\n"
        "    print(build_banner())\n"
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    ),
    "tests/test_core.py": (
        '"""Tests for h1_cli.core."""\n\n'
        "from h1_cli.core import BANNER, build_banner\n\n\n"
        "def test_banner_constant():\n"
        '    assert BANNER == "H1_CLI_OK"\n\n\n'
        "def test_build_banner_returns_banner():\n"
        "    assert build_banner() == \"H1_CLI_OK\"\n"
    ),
    "README.md": "# h1_cli\n\nPrints `H1_CLI_OK`.\n",
}


def call_tool(name, arguments, timeout=180):
    status, text = common.call("POST", "/chats", token=TOKEN, body={"title": f"free preflight {name}"})
    chat = json.loads(text)["id"]
    request = urllib.request.Request(
        common.BASE + "/tools/execute",
        data=json.dumps({"chat_id": chat, "name": name, "arguments": arguments}).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + TOKEN)

    result = {}
    confirmations = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
                        confirmations += 1
                        common.confirm(TOKEN, value["id"], True)
                    if event == "tool_result":
                        result = value
                    if event in {"tool_error", "error"}:
                        result = {"event": event, "value": value}
                    event, data = "", ""
    finally:
        common.call("DELETE", "/chats/" + chat, token=TOKEN)
    return result, confirmations


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT, exist_ok=True)

    results = {}
    confirmations = 0
    for relative, content in FILES.items():
        path = os.path.join(ROOT, relative.replace("/", os.sep))
        result, approved = call_tool(
            "write_file",
            {
                "path": path,
                "content": content,
                "purpose": "free H1 readiness: create project file",
            },
        )
        confirmations += approved
        results[relative] = result

    cli, approved = call_tool(
        "run_process",
        {
            "executable": "python",
            "argv": ["-m", "h1_cli"],
            "cwd": ROOT,
            "timeout_seconds": 60,
            "purpose": "free H1 readiness: run the CLI",
        },
    )
    confirmations += approved
    tests, approved = call_tool(
        "run_process",
        {
            "executable": "python",
            "argv": ["-m", "pytest", "-q"],
            "cwd": ROOT,
            "timeout_seconds": 120,
            "purpose": "free H1 readiness: run the tests",
        },
    )
    confirmations += approved

    created = {name: os.path.isfile(os.path.join(ROOT, name.replace("/", os.sep))) for name in FILES}
    hashes = {}
    for name in FILES:
        path = os.path.join(ROOT, name.replace("/", os.sep))
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                hashes[name] = hashlib.sha256(handle.read()).hexdigest()

    cli_stdout = (cli.get("metadata") or {}).get("stdout") or ""
    test_stdout = (tests.get("metadata") or {}).get("stdout") or ""
    test_exit = (tests.get("metadata") or {}).get("exit_code")
    checks = {
        "all five files created through write_file": all(created.values()) and all(
            not (settings or {}).get("event") for settings in results.values()
        ),
        "run_process python exists (exit 0)": (cli.get("metadata") or {}).get("exit_code") == 0,
        "stdout is H1_CLI_OK": "H1_CLI_OK" in cli_stdout,
        "pytest passed": test_exit == 0 and "passed" in test_stdout,
        "computer confirmations were requested": confirmations >= 7,
    }
    print(json.dumps({"checks": checks, "created": created, "hashes": hashes,
                      "cli": cli, "pytest": tests, "confirmations": confirmations},
                     ensure_ascii=False, indent=2)[:6000])
    print("H1 FREE PREFLIGHT:", "PASS" if all(checks.values()) else "FAIL")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
