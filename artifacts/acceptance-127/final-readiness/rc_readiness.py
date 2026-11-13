"""Release-candidate readiness: what is prepared, and what still needs the owner.

This is deliberately not a release. It records the version surfaces, the evidence that each 1.2.7
capability has a real check, and the two gates the release policy will not let us skip:

    FULL LIVE AUTONOMY   not verified (no real model turn yet)
    AUTHENTICODE         blocked by an external certificate

A `PASS` here means "the staging inputs are consistent"; it never means "ready to publish".

Run:  python artifacts/acceptance-127/final-readiness/rc_readiness.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
NSIS = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"


def read(path: Path, pattern: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def artifact(name: str, key: str = "verdict") -> dict:
    path = OUT / name
    if not path.is_file():
        return {"path": f"artifacts/acceptance-127/final-readiness/{name}", "present": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"path": f"artifacts/acceptance-127/final-readiness/{name}", "present": True, "readable": False}
    return {"path": f"artifacts/acceptance-127/final-readiness/{name}", "present": True, key: payload.get(key)}


def main() -> int:
    versions = {
        "backend_product": read(REPO / "apps" / "backend" / "app" / "product.py", r'VERSION = "([^"]+)"'),
        "backend_pyproject": read(REPO / "apps" / "backend" / "pyproject.toml", r'(?m)^version = "([^"]+)"'),
        "desktop_package": read(REPO / "apps" / "desktop" / "package.json", r'"version":\s*"([^"]+)"'),
        "tauri": read(REPO / "apps" / "desktop" / "src-tauri" / "tauri.conf.json", r'"version":\s*"([^"]+)"'),
    }
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip()
    tags = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"], cwd=str(REPO), capture_output=True, text=True
    ).stdout.strip().split()

    installers = sorted(path.name for path in NSIS.glob("*.exe")) if NSIS.is_dir() else []
    evidence = {
        "packaged_host": {
            "path": "artifacts/acceptance-127/packaged-host/summary.json",
            "present": (REPO / "artifacts" / "acceptance-127" / "packaged-host" / "summary.json").is_file(),
        },
        "identity_isolation": artifact("identity-isolation.json"),
        "device_lifecycle": artifact("device-lifecycle.json"),
        "model_contract": artifact("model-contract.json"),
        "model_provenance": artifact("model-provenance.json"),
        "watchdog_dry_run": artifact("watchdog-dry-run.json"),
        "negative_scenarios": artifact("autonomy-negative-tests.json"),
        "installer_inspection": artifact("installer-inspection.json"),
    }

    checks: list[dict] = []

    def check(name: str, ok: bool, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})

    check("one_version_everywhere", len(set(versions.values())) == 1, versions)
    check("version_is_the_released_one", versions.get("tauri") == "1.2.6", versions.get("tauri"))
    check("on_the_feature_branch", branch == "feature/canalla-1.2.7-autonomous-agent", branch)
    check("no_release_tag_on_head", not tags, tags)
    check(
        "no_1_2_7_installer_was_built",
        not any("1.2.7" in name for name in installers),
        installers,
    )
    for key, item in evidence.items():
        if "verdict" in item:
            check(f"evidence_{key}", item["verdict"] == "PASS", item)
        else:
            check(f"evidence_{key}", item.get("present", False), item)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": {"branch": branch, "head": head, "tags_at_head": tags},
        "versions": versions,
        "installers_in_the_bundle_dir": installers,
        "evidence": evidence,
        "checks": checks,
        "staging_inputs_ready": all(item["ok"] for item in checks),
        "gates": {
            "FULL_LIVE_AUTONOMY": {
                "status": "NOT VERIFIED",
                "blocker": "one authorised Pod and one real Qwen task trace",
            },
            "WINDOWS_AUTHENTICODE": {
                "status": "BLOCKED_BY_EXTERNAL_CERTIFICATE",
                "blocker": "OV/EV code-signing certificate with a FIPS 140-2 L2 key",
            },
            "NMAP_ZAP_ADAPTERS": {
                "status": "NOT IMPLEMENTED / OPTIONAL",
                "blocker": None,
            },
        },
        "rc_status": "RC STAGING READY — BLOCKED PENDING LIVE QWEN + CERTIFICATE",
        "not_performed": [
            "no version bump to 1.2.7",
            "no installer built for 1.2.7",
            "no publication, no production manifest or updater change",
            "no signature applied",
        ],
    }
    report["verdict"] = "PASS" if report["staging_inputs_ready"] else "FAIL"
    (OUT / "rc-readiness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({"verdict": report["verdict"], "rc_status": report["rc_status"]}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
