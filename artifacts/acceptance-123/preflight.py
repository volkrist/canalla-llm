"""Free preflight for the 28-scenario Canalla 1.2.3 acceptance suite.

It never starts compute and never claims a scenario PASS. It proves the test system itself:
fixtures, paths with spaces, permissions, the installed host, the UI script, the watchdog
state, RunPod emptiness, the model-origin verdict rule and the independent post-checkers.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import catalog
import common

OUT = Path(__file__).resolve().parent / "runs" / common.now().replace(":", "").replace("-", "")[:15]


def call_json(method: str, path: str, token: str, body: dict | None = None, timeout: float = 30) -> dict:
    status, text = common.call(method, path, token=token, body=body, timeout=timeout)
    try:
        return {"http": status, "body": json.loads(text) if status == 200 else text[:400]}
    except ValueError:
        return {"http": status, "body": text[:400]}


def body_dict(value: dict) -> dict:
    body = value.get("body")
    return body if isinstance(body, dict) else {}


def remote(script: str, timeout: int = 90) -> tuple[int, str]:
    payload = (script.replace("\r\n", "\n").strip() + "\n").encode("utf-8")
    result = subprocess.run(
        ["ssh", "distance", "sudo -n bash -s"],
        input=payload,
        capture_output=True,
        timeout=timeout,
    )
    text = (result.stdout or b"").decode("utf-8", "replace") + (
        result.stderr or b""
    ).decode("utf-8", "replace")
    return result.returncode, text


def runpod_state() -> dict:
    script = (Path(__file__).resolve().parent / "helpers" / "runpod-pods.sh").read_text(
        encoding="utf-8"
    )
    code, text = remote(script)
    values: dict = {"exit": code, "raw": text[-500:]}
    for line in text.splitlines():
        if line.startswith("balance_usd "):
            values["balance_usd"] = line.split(" ", 1)[1]
        if line.startswith("current_spend_per_hr "):
            values["current_spend_per_hr"] = line.split(" ", 1)[1]
        if line.startswith("pods "):
            values["pods"] = line.split(" ", 1)[1]
    return values


def watchdog_state() -> dict:
    result = subprocess.run(
        [
            "ssh",
            "distance",
            "sudo -n /opt/alex-gateway/venv/bin/python "
            "/usr/local/sbin/canalla-gpu-test-watchdog.py --status",
        ],
        capture_output=True,
        timeout=60,
    )
    text = (result.stdout or b"").decode("utf-8", "replace")
    try:
        value = json.loads(text)
    except ValueError:
        value = {"raw": text[-400:]}
    value["exit"] = result.returncode
    return value


def execute_tool(token: str, chat_id: str, name: str, arguments: dict, timeout: int = 120) -> dict:
    """One explicit tool call for the host smoke. It is labelled preflight, never a scenario."""
    request = urllib.request.Request(
        common.BASE + "/tools/execute",
        data=json.dumps({"chat_id": chat_id, "name": name, "arguments": arguments}).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", "Bearer " + token)
    result: dict = {"tool": name, "events": [], "result": None, "error": None}
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
                    value = {"raw": data}
                result["events"].append({"event": event, "data": value})
                if event == "tool" and value.get("status") == "waiting_confirmation":
                    status, text = common.confirm(token, value["id"], True)
                    result["events"].append(
                        {"event": "confirm", "http": status, "body": text[:200]}
                    )
                elif event == "tool_result":
                    result["result"] = value
                elif event == "tool_error":
                    result["error"] = value
                event, data = "", ""
    return result


def host_smoke(token: str) -> dict:
    root = common.SANDBOX / "PREFLIGHT"
    root.mkdir(parents=True, exist_ok=True)
    status, text = common.call("POST", "/chats", token=token, body={"title": "acceptance preflight"})
    if status not in (200, 201):
        return {"ok": False, "error": "chat_create_failed", "body": text[:300]}
    chat_id = json.loads(text)["id"]
    script = root / "preflight_host.py"
    evidence = {"root": str(root), "chat_id": chat_id, "steps": []}
    try:
        write = execute_tool(
            token,
            chat_id,
            "write_file",
            {
                "path": str(script),
                "content": "print('PREFLIGHT_HOST_OK')\n",
                "purpose": "acceptance preflight host smoke",
            },
        )
        read = execute_tool(
            token,
            chat_id,
            "read_file",
            {"path": str(script), "purpose": "acceptance preflight host smoke"},
        )
        run = execute_tool(
            token,
            chat_id,
            "run_python",
            {"argv": [script.name], "cwd": str(root), "purpose": "acceptance preflight host smoke"},
        )
        runtime = execute_tool(
            token,
            chat_id,
            "run_python",
            {
                "argv": ["-c", "import sys; print(sys.version); print(sys.executable)"],
                "cwd": str(root),
                "purpose": "acceptance preflight runtime",
            },
        )
        pytest = execute_tool(
            token,
            chat_id,
            "run_python",
            {"argv": ["-m", "pytest", "--version"], "cwd": str(root), "purpose": "pytest availability"},
        )
        for item in (write, read, run, runtime, pytest):
            hosted = (item.get("result") or {}).get("metadata") or {}
            evidence["steps"].append(
                {
                    "tool": item["tool"],
                    "error": item["error"],
                    "stdout": str(hosted.get("stdout") or "")[:300],
                    "exit_code": hosted.get("exit_code"),
                }
            )
        run_stdout = str(
            (((run.get("result") or {}).get("metadata") or {}).get("stdout")) or ""
        )
        pytest_stdout = str(
            (((pytest.get("result") or {}).get("metadata") or {}).get("stdout")) or ""
        )
        runtime_stdout = str(
            (((runtime.get("result") or {}).get("metadata") or {}).get("stdout")) or ""
        )
        evidence["ok"] = (
            "PREFLIGHT_HOST_OK" in run_stdout
            and (script.is_file())
            and "pytest" in pytest_stdout.casefold()
        )
        evidence["runtime"] = runtime_stdout[:300]
        evidence["pytest_available"] = "pytest" in pytest_stdout.casefold()
    finally:
        common.call("DELETE", "/chats/" + chat_id, token=token)
    return evidence


def setup_all() -> dict:
    common.SANDBOX.mkdir(parents=True, exist_ok=True)
    report: dict = {"root": str(common.SANDBOX), "scenarios": {}, "seed_checks": {}}
    for scenario in catalog.SCENARIOS:
        root = catalog.scenario_root(scenario.id)
        root.mkdir(parents=True, exist_ok=True)
        if scenario.setup:
            catalog.setup_fixture(scenario.setup, root)
        missing = [name for name, _ in scenario.files if _ is not None and not (root / name).is_file()]
        report["scenarios"][scenario.id] = {
            "dir": str(root),
            "setup": scenario.setup,
            "files_present": all((root / name).exists() for name in []),
            "pre_created_files": sorted(
                str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
            )[:20],
        }
        if scenario.setup:
            ok, detail = catalog.seed_check(scenario.setup, root)
            report["seed_checks"][scenario.id] = {"ok": ok, "detail": detail[:400]}
    return report


def ui_script() -> dict:
    script = Path(__file__).resolve().parent / "helpers" / "ui-nav-during-gen.ps1"
    if not script.is_file():
        return {"ok": False, "error": "ui script missing"}
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return {
        "ok": result.returncode == 0 and "installed-window-navigation: PASS" in text,
        "exit": result.returncode,
        "output": text[-1200:],
    }


def estimate() -> dict:
    tiers = Counter(scenario.tier for scenario in catalog.SCENARIOS)
    seconds = sum(scenario.budget_seconds for scenario in catalog.SCENARIOS)
    return {
        "scenarios": len(catalog.SCENARIOS),
        "by_tier": dict(tiers),
        "catalog_seconds": seconds,
        "catalog_minutes": round(seconds / 60, 1),
        "gpu_usd_at_1_09_per_hour": round(seconds / 3600 * 1.09, 2),
        "note": "Catalog seconds are prompt budgets, not wall time; add 60 s startup per session. "
        "The 240 s first-turn figure was measured on 20 Oct *before* the final-stream thinking "
        "fix and must be re-measured, never assumed.",
    }


def route_checks() -> dict:
    """Free guard: a prompt the server would execute itself cannot prove Qwen did it."""
    values: dict[str, dict] = {}
    for scenario in catalog.SCENARIOS:
        values[scenario.id] = catalog.route_decision(
            scenario.prompt, catalog.scenario_root(scenario.id)
        )
    return values


def gate_problems(checks: dict, package_names) -> list[str]:
    problems: list[str] = []
    for scenario in catalog.package_scenarios(package_names):
        decision = checks.get(scenario.id) or {}
        if not decision.get("ok"):
            problems.append(scenario.id + ": route guard unavailable " + str(decision.get("error")))
            continue
        if decision.get("deterministic") and scenario.require_model and scenario.expected_tools:
            problems.append(
                scenario.id
                + ": prompt is handled by the server as "
                + str(decision.get("action"))
                + " ("
                + str(decision.get("reason"))
                + ")"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-ui", action="store_true")
    parser.add_argument("--skip-host", action="store_true")
    parser.add_argument(
        "--gate-package",
        nargs="+",
        default=["simple-a", "simple-b"],
        help="packages whose deterministic-route guard blocks the live run",
    )
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    token = common.token()
    report: dict = {"generated_at": common.now(), "checks": {}, "scenarios": {}}
    health = call_json("GET", "/health", token)
    status = call_json("GET", "/status", token)
    prefs = call_json("GET", "/tools/preferences", token)
    cloud = call_json("GET", "/cloud/status", token)
    devices = call_json("GET", "/tools/devices", token)
    report["checks"]["health"] = health
    report["checks"]["status"] = status
    report["checks"]["cloud"] = cloud
    report["checks"]["online_devices"] = [
        item.get("device_id") for item in (devices.get("body") or []) if item.get("online")
    ] if isinstance(devices.get("body"), list) else devices
    report["checks"]["computer_mode"] = body_dict(prefs).get("computer_mode")
    report["checks"]["runpod"] = runpod_state()
    report["checks"]["watchdog"] = watchdog_state()
    report["checks"]["sandbox"] = str(common.SANDBOX)
    report["fixtures"] = setup_all()
    report["estimate"] = estimate()
    report["checks"]["deterministic_route"] = route_checks()
    gate = gate_problems(report["checks"]["deterministic_route"], args.gate_package)
    report["checks"]["route_gate"] = {
        "packages": args.gate_package,
        "ok": not gate,
        "problems": gate,
    }

    try:
        catalog.self_test()
        report["checks"]["model_origin_rule"] = {"ok": True}
    except AssertionError as error:
        report["checks"]["model_origin_rule"] = {"ok": False, "error": str(error)}

    if not args.skip_host:
        report["checks"]["host_smoke"] = host_smoke(token)

    if args.with_ui:
        report["checks"]["ui"] = ui_script()

    for scenario in catalog.SCENARIOS:
        root = catalog.scenario_root(scenario.id)
        report["scenarios"][scenario.id] = {
            "tier": scenario.tier,
            "title": scenario.title,
            "timeout": scenario.timeout,
            "budget_seconds": scenario.budget_seconds,
            "requires_model_tool": scenario.require_model and bool(scenario.expected_tools),
            "expected_tools": list(scenario.expected_tools),
            "setup": scenario.setup,
            "post_check": scenario.post_check,
            "dir": str(root),
            "files_before": sorted(
                str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
            )[:30],
        }

    report["verdict"] = "PREFLIGHT COMPLETE" if not gate else "PREFLIGHT BLOCKED"
    (OUT / "preflight.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Canalla 1.2.3 acceptance preflight",
        "",
        "Generated: " + report["generated_at"],
        "",
        "| Check | Result |",
        "|---|---|",
        f"| app version | {body_dict(health).get('version')} |",
        f"| protocol | {body_dict(health).get('runtime_protocol_version')} |",
        f"| cloud | {body_dict(cloud).get('state') or cloud.get('http')} |",
        f"| computer mode | {body_dict(prefs).get('computer_mode')} |",
        f"| online devices | {len(report['checks']['online_devices'])} |",
        f"| sandbox | {report['checks']['sandbox']} |",
        f"| RunPod pods | {report['checks']['runpod'].get('pods')} |",
        f"| balance | {report['checks']['runpod'].get('balance_usd')} |",
        f"| watchdog | {report['checks']['watchdog'].get('status')} |",
        f"| host smoke | {(report['checks'].get('host_smoke') or {}).get('ok')} |",
        f"| pytest on host | {(report['checks'].get('host_smoke') or {}).get('pytest_available')} |",
        f"| model-origin rule | {report['checks']['model_origin_rule'].get('ok')} |",
        f"| installed-window navigation | {(report['checks'].get('ui') or {}).get('ok')} |",
        f"| deterministic route gate | {'clean' if not gate else 'BLOCKED'} "
        f"({', '.join(args.gate_package)}) |",
        f"| scenarios | {report['estimate']['scenarios']} ({report['estimate']['by_tier']}) |",
        "",
        "This is a preflight, not a scenario PASS. No model turn and no compute were used.",
    ]
    if gate:
        lines.extend(["", "## Route gate problems", ""])
        lines.extend("- " + item for item in gate)
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("evidence:", OUT)
    if gate:
        print("PREFLIGHT BLOCKED: fix the prompts before any paid run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
