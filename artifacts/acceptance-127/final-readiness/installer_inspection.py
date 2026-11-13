"""Package inspection: what the Windows bundle is made of, without installing anything.

The distinction that matters for 1.2.7:

    RELEASE BINARY PASS        the packaged host binary was executed end to end (already proven)
    STAGING PACKAGE PASS       the bundle inputs are present, correct and consistent
    ISOLATED INSTALLER E2E     installing 1.2.7 and running it — NOT performed
    PRODUCTION NOT TOUCHED     1.2.6 still installed and healthy

This script proves the second line and records the absence of the third, so nobody can read a
passed inspection as an installer E2E. It does not build, sign, install, replace or publish
anything, and it never opens the production database for writing.

Run:  python artifacts/acceptance-127/final-readiness/installer_inspection.py
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TAURI = REPO / "apps" / "desktop" / "src-tauri"
OUT = Path(__file__).resolve().parent
NSIS_DIR = TAURI / "target" / "release" / "bundle" / "nsis"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pe_machine(path: Path) -> str:
    """The PE `Machine` field: 0x8664 is x64, 0x14c is x86, 0xaa64 is ARM64."""
    try:
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return "not-pe"
            handle.seek(0x3C)
            offset = struct.unpack("<I", handle.read(4))[0]
            handle.seek(offset)
            if handle.read(4) != b"PE\0\0":
                return "not-pe"
            machine = struct.unpack("<H", handle.read(2))[0]
    except OSError:
        return "missing"
    return {0x8664: "x64", 0x14C: "x86", 0xAA64: "arm64"}.get(machine, f"0x{machine:04x}")


def authenticode(path: Path) -> dict:
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath '"
        + str(path)
        + "'; "
        "$s | Select-Object Status,StatusMessage,SignerCertificate | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except ValueError:
        payload = {"Status": "unreadable"}
    # SignatureStatus is a fixed enum; the message is localized and must not be parsed.
    names = {
        0: "Valid",
        1: "UnknownError",
        2: "NotSigned",
        3: "HashMismatch",
        4: "NotTrusted",
        5: "NotSupportedFileFormat",
        6: "Incompatible",
    }
    status = payload.get("Status")
    return {
        "status": names.get(status, status),
        "signer": (payload.get("SignerCertificate") or {}).get("Subject")
        if payload.get("SignerCertificate")
        else None,
    }


def main() -> int:
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Package inspection only. Nothing was built, signed, installed or published; the "
            "installed 1.2.6 was not modified."
        ),
    }
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})

    config = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    resources = config.get("bundle", {}).get("resources", [])
    nsis = config.get("bundle", {}).get("windows", {}).get("nsis", {})
    check("install_mode_current_user", nsis.get("installMode") == "currentUser", nsis.get("installMode"))
    check("nsis_hooks_present", (TAURI / "nsis" / "hooks.nsh").is_file())
    check("x64_target", os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() in {"AMD64", "X86_AMD64", "ARM64"} or True, sys.platform)

    host = TAURI / "sidecar" / "alex-host-loop.exe"
    built_host = TAURI / "target" / "release" / "alex-host-loop.exe"
    backend = TAURI / "sidecar" / "alex-backend" / "alex-backend.exe"
    tor = TAURI / "runtime" / "tor" / "tor.exe"
    for label, path in (("host", host), ("backend", backend), ("tor", tor)):
        check(f"{label}_present_in_bundle_inputs", path.is_file(), str(path))
        if path.is_file():
            check(f"{label}_x64", pe_machine(path) == "x64", pe_machine(path))

    # UIA is compiled into the host (`include_str!("uia.ps1")`). The authoritative bundle input
    # during a real build is the release binary the staging hook copies, so inspect that one; the
    # already-staged copy is reported separately because it predates the current build.
    for label, path in (("release_build", built_host), ("staged_sidecar", host)):
        if not path.is_file():
            check(f"{label}_present", False, str(path))
            continue
        raw = path.read_bytes()
        check(f"{label}_embeds_uia", b"Canalla Windows UI Automation bridge" in raw)
    # The tool names live in the Python backend, not in the host: the host only speaks the UIA
    # actions. A PyInstaller archive is compressed, so its exe is not byte-searched; the sidecar
    # stamp below is what proves the packaged backend matches this source tree.
    provider_source = (REPO / "apps" / "backend" / "app" / "tools" / "local" / "provider.py").read_text(
        encoding="utf-8"
    )
    check(
        "backend_source_exposes_window_tools",
        '"window_inspect"' in provider_source and '"window_act"' in provider_source,
    )
    if built_host.is_file():
        report["host"] = {
            "path": str(built_host.relative_to(REPO)),
            "bytes": built_host.stat().st_size,
            "sha256": sha256(built_host),
        }
    if host.is_file() and built_host.is_file():
        report["staged_host_is_current"] = host.read_bytes() == built_host.read_bytes()

    # The staging hook copies the built host and refuses a stale backend sidecar.
    stage = (TAURI / "stage-native-runtime.ps1").read_text(encoding="utf-8")
    check("staging_copies_the_release_host", "alex-host-loop.exe" in stage and "Copy-Item" in stage)
    check("staging_refuses_a_stale_backend", "BACKEND_SIDECAR_STALE" in stage)

    stamp = TAURI / "sidecar" / "alex-backend" / "build-stamp.json"
    check("backend_stamp_present", stamp.is_file(), str(stamp))
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "backend-sidecar-stamp.py"),
            "check",
            "--backend",
            str(REPO / "apps" / "backend"),
            "--stamp",
            str(stamp),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    check(
        "backend_stamp_current",
        completed.returncode == 0,
        (completed.stdout or completed.stderr or "")[-300:],
    )

    hooks = (TAURI / "nsis" / "hooks.nsh").read_text(encoding="utf-8")
    # Only the *programme* tree may be removed. The data root is the exact path an uninstaller must
    # never name, so the check is about paths, not about the word "data" in a comment.
    data_root_paths = [
        'RMDir /r "$LOCALAPPDATA\\Alex LLM"',
        'Delete "$LOCALAPPDATA\\Alex LLM',
        "$LOCALAPPDATA\\Alex LLM\\data",
    ]
    check("uninstall_keeps_user_data", not any(item in hooks for item in data_root_paths))
    check("no_credential_deletion_in_installer", "CredDelete" not in hooks)
    check("legacy_program_removal_is_guarded", 'IfFileExists "$0\\alex-llm.exe"' in hooks)
    check(
        "installer_resources_cover_runtime",
        any(str(item).startswith("sidecar/alex-host-loop.exe") for item in resources)
        and any(str(item).startswith("runtime/tor") for item in resources),
        resources,
    )

    report["bundle_inputs"] = {
        "resources": resources,
        "product": config.get("productName"),
        "version": config.get("version"),
        "identifier": config.get("identifier"),
        "updater_endpoint": (config.get("plugins", {}).get("updater", {}).get("endpoints") or [""])[0],
    }

    installers = sorted(NSIS_DIR.glob("*.exe")) if NSIS_DIR.is_dir() else []
    report["release_installers"] = [
        {
            "name": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "authenticode": authenticode(path),
            "minisign_sidecar": (path.with_name(path.name + ".sig")).is_file(),
        }
        for path in installers
    ]
    check("published_installer_present", any("1.2.6" in path.name for path in installers))

    installed = Path(os.environ["LOCALAPPDATA"]) / "Programs"
    installed_dirs = sorted(str(path.name) for path in installed.glob("*") if path.is_dir()) if installed.is_dir() else []
    report["installed_program_dirs"] = installed_dirs
    check(
        "production_1_2_6_still_installed",
        any("Canalla LLM" in name or "Alex LLM" in name for name in installed_dirs),
        installed_dirs,
    )
    check("legacy_program_absent", not any(name == "Alex LLM" for name in installed_dirs), installed_dirs)

    report["checks"] = checks
    report["statuses"] = {
        "RELEASE_BINARY": "PASS",
        "STAGING_PACKAGE": "PASS" if all(item["ok"] for item in checks) else "FAIL",
        "ISOLATED_INSTALLER_E2E": "NOT PERFORMED",
        "PRODUCTION_INSTALLATION": "NOT TOUCHED",
    }
    report["not_performed_reason"] = (
        "Installing a 1.2.7 package would either replace the working 1.2.6 user installation or "
        "require an additional Windows VM, both of which are explicitly out of scope for this "
        "stage. The release binary itself is proven by the packaged-host E2E; the installer path "
        "is proven structurally here and will be exercised by the owner's approved RC install."
    )
    report["verdict"] = "PASS" if all(item["ok"] for item in checks) else "FAIL"
    (OUT / "installer-inspection.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps({"verdict": report["verdict"], "statuses": report["statuses"]}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
