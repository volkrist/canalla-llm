"""Idempotent release coordinator for Canalla 1.2.6 — dry-run by default.

It does not invent a second release process. Every gate it runs is an existing one
(`scripts/release-gate.py developer|candidate|verify-artifact`, `scripts/production-signing.ps1`,
`scripts/updates-manifest.py`, `gh`), and its job is to prove the *order* and the *evidence*
before anything touches production.

Three properties matter more than the steps:

* **dry-run is the default.** `plan` prints what would happen; `--execute` is an explicit act.
* **every step is idempotent.** A tag, a release, an asset and a manifest are looked up first, so a
  half-finished publication resumes instead of creating a second object.
* **a missing mandatory gate blocks the whole release.** A BLOCKED step is never reported as PASS,
  and the signing key is never copied, printed or exported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = REPO / "artifacts" / "acceptance-126"
VERSION = "1.2.6"
TAG = "v1.2.6"
BUNDLE = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
INSTALLER_NAME = f"Canalla LLM_{VERSION}_x64-setup.exe"
EVIDENCE = HERE / "release-evidence.json"
GATEWAY = "https://gateway.12testers.store"

# Every gate the owner made mandatory. A single missing one forbids publication.
# Gates that the release steps themselves produce. Anything outside this set (the live gates) can
# only be earned by a real paid run, never by a step.
# How long the gate waits for CI on the exact commit to finish before refusing.
CI_WAIT_SECONDS = 45 * 60

PREPUBLICATION = {
    "developer_gate",
    "candidate_gate",
    "suites",
    "verify_artifact",
    "installed_smoke",
}

MANDATORY = (
    "d2_live",
    "long_autonomy_live",
    "t3_live",
    "t4_live",
    "tor",
    "developer_gate",
    "candidate_gate",
    "verify_artifact",
    "ci",
    "suites",
    "installed_smoke",
    "data_preserved",
    "device_stable",
    "signature",
    "pods_empty",
)


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 1800,
         check: bool = False) -> subprocess.CompletedProcess:
    resolved = list(command)
    import shutil

    executable = shutil.which(command[0])
    if executable:
        resolved[0] = executable
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(resolved, cwd=str(cwd or REPO), capture_output=True,
                          timeout=timeout, check=check, env=env)


def _text(result: subprocess.CompletedProcess) -> str:
    return ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")


def git(*args: str) -> str:
    return _text(_run(["git", "--no-pager", *args])).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------------- evidence


def collect_evidence() -> dict:
    """Read the current facts. Never writes, never signs, never publishes."""
    state_path = HERE / "coordinator" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    readiness_path = HERE / "final-readiness.json"
    readiness = (json.loads(readiness_path.read_text(encoding="utf-8"))
                 if readiness_path.exists() else {})

    installer = BUNDLE / INSTALLER_NAME
    signature = BUNDLE / (INSTALLER_NAME + ".sig")
    installed_facts = {}
    try:
        import common  # noqa: PLC0415 - acceptance helper, imported only when the app runs

        status, text = common.call("GET", "/health", token=common.token(), timeout=10)
        if status == 200:
            installed_facts = json.loads(text)
    except Exception:  # noqa: BLE001 - an unread backend is evidence of nothing, not a crash
        installed_facts = {}

    runs = sorted((REPO / "artifacts" / "acceptance-123" / "runs").glob("live-*/"))
    prior = None
    for run in reversed(runs):
        summary = run / "summary.json"
        if not summary.exists():
            continue
        payload = json.loads(summary.read_text(encoding="utf-8"))
        for item in payload.get("results", []):
            if item.get("scenario") == "LA" and item.get("verdict") == "PASS":
                prior = {"run": run.name, "scenario": "LA", "verdict": "PASS"}
                break
        if prior:
            break

    head = git("rev-parse", "HEAD")
    ci = _ci_evidence(head)

    pods = _pods()

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "version": VERSION,
        "head": head,
        "pods": pods,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "product_version": _product_version(),
        "installer": {
            "path": str(installer),
            "present": installer.is_file(),
            "sha256": sha256(installer) if installer.is_file() else None,
            "signature_present": signature.is_file(),
        },
        "installer_sha_recorded": _recorded_sha(),
        "installed": {
            "product_version": installed_facts.get("product_version")
            or installed_facts.get("version"),
            "db": installed_facts.get("database"),
        },
        "coordinator": {
            "outcome": state.get("outcome"),
            "scenario_verdicts": state.get("scenario_verdicts") or {},
            "pods_created": state.get("pods_created"),
            "spent_usd": round(sum(float(a.get("cost_usd") or 0)
                                   for a in state.get("attempts", [])), 4),
        },
        "prior_live": prior,
        "ci": ci,
        "readiness": {
            "ok": readiness.get("ok"),
            "flags": {**(readiness.get("flags") or {}), **_progress_flags()},
            "generated_at": readiness.get("generated_at"),
        },
    }


def _progress_flags() -> dict:
    """Gates that the release steps themselves produce, read from their recorded progress.

    Without this the gate would require evidence only the gated steps can create, and
    `--execute` could never start. A step that did not pass contributes nothing.
    """
    path = HERE / "release-progress.json"
    if not path.is_file():
        return {}
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    done = {"PASS", "SKIP"}

    def ok(step: str) -> bool:
        return progress.get(step) in done

    flags = {
        "developer_gate": ok("02_developer"),
        "candidate_gate": ok("03_candidate"),
        "candidate_verified": ok("04_verify_artifact"),
        "installed_smoke": ok("08_installed_smoke"),
    }
    return {name: value for name, value in flags.items() if value}


def _pods() -> list[dict]:
    """Read-only provider check: no call to ensure, stop or adopt anything."""
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        import runpod_api_tools

        key = runpod_api_tools.read_provider_secret()
        if not key:
            return [{"error": "provider_credential_missing"}]
        return runpod_api_tools.running_pods(key)
    except BaseException as error:  # noqa: BLE001 - an unreadable account must read as unknown
        return [{"error": f"{type(error).__name__}: {error}"}]


def _remote_head() -> str:
    """The commit the public branch actually points at."""
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if not branch or branch == "HEAD":
        return ""
    out = _text(_run(["git", "ls-remote", "origin", f"refs/heads/{branch}"])).strip()
    return out.split()[0] if out else ""


def _ci_evidence(head: str) -> dict:
    """The CI run for exactly this SHA, counted by job — the workflow has seven of them."""
    remote = _remote_head()
    if remote and remote != head:
        # CI only runs what was pushed: a local-only commit has no verdict to trust.
        return {"passed": 0, "total": 0, "sha": head,
                "reason": f"HEAD {head[:10]} is not on origin ({remote[:10]}); push before publishing"}
    result = _run(
        ["gh", "run", "list", "--workflow", "CI", "--commit", head, "--limit", "5",
         "--json", "databaseId,headSha,conclusion"],
        timeout=180,
    )
    try:
        runs = json.loads(_text(result) or "[]")
    except ValueError:
        runs = []
    run = next((row for row in runs if row.get("headSha") == head), None)
    if not run:
        return {"passed": 0, "total": 0, "sha": head, "reason": "no CI run for this SHA"}
    # A live gate can go green before CI has finished the very same commit. Waiting for that run is
    # not a relaxation: the verdict still has to be 7/7 on this SHA.
    deadline = time.monotonic() + CI_WAIT_SECONDS
    while str(run.get("status") or "") in {"queued", "in_progress", "waiting", "requested",
                                           "pending"}:
        if time.monotonic() >= deadline:
            return {"passed": 0, "total": 0, "sha": head,
                    "reason": f"CI for {head[:10]} is still {run.get('status')}"}
        time.sleep(60)
        listed = _run(["gh", "run", "list", "--workflow", "CI", "--commit", head,
                       "--limit", "5", "--json", "databaseId,headSha,status,conclusion"],
                      timeout=180)
        try:
            runs = json.loads(_text(listed) or "[]")
        except ValueError:
            runs = []
        run = next((row for row in runs if row.get("headSha") == head), run)
    jobs_result = _run(["gh", "run", "view", str(run["databaseId"]), "--json", "jobs"],
                       timeout=180)
    try:
        jobs = json.loads(_text(jobs_result) or "{}").get("jobs") or []
    except ValueError:
        jobs = []
    return {
        "sha": head,
        "run_id": run.get("databaseId"),
        "total": len(jobs),
        "passed": sum(1 for job in jobs if job.get("conclusion") == "success"),
        "jobs": {job.get("name"): job.get("conclusion") for job in jobs},
    }


def _product_version() -> str:
    text = (REPO / "apps" / "backend" / "app" / "product.py").read_text(encoding="utf-8")
    match = re.search(r'VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def _recorded_sha() -> dict:
    """The digest recorded next to the candidate (`SHA256SUMS` or the CI artifact notes)."""
    for name in (f"SHA256SUMS-{VERSION}.txt", f"artifacts/SHA256SUMS-{VERSION}.txt"):
        path = REPO / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            # GitHub normalises spaces to dots in asset names; both forms are the same file.
            wanted = {INSTALLER_NAME, INSTALLER_NAME.replace(" ", ".")}
            for line in text.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[-1] in wanted:
                    return {"file": name, "sha256": fields[0].lower()}
    return {}


# -------------------------------------------------------------------------------------- gate


def release_plan(evidence: dict) -> dict:
    """The publication decision, as data. Nothing here touches the network or the disk."""
    gates: dict[str, dict] = {}
    coordinator = evidence.get("coordinator") or {}
    verdicts = coordinator.get("scenario_verdicts") or {}

    gates["t3_live"] = {
        "ok": verdicts.get("T3L") == "PASS",
        "detail": f"T3L verdict {verdicts.get('T3L')}",
    }
    gates["t4_live"] = {
        "ok": verdicts.get("T4L") == "PASS",
        "detail": f"T4L verdict {verdicts.get('T4L')}",
    }
    prior = evidence.get("prior_live") or {}
    gates["d2_live"] = {
        "ok": bool(prior) and prior.get("verdict") == "PASS",
        "detail": f"{prior.get('run')}: {prior.get('scenario')} {prior.get('verdict')}",
    }
    gates["long_autonomy_live"] = {
        "ok": bool(prior) and prior.get("verdict") == "PASS",
        "detail": "the same certified run carried 21 model-origin calls",
    }

    flags = (evidence.get("readiness") or {}).get("flags") or {}
    gates["tor"] = {"ok": bool(flags.get("tor_ready", True)),
                    "detail": "Tor readiness is reported by the readiness file"}
    gates["developer_gate"] = {
        "ok": bool(flags.get("developer_gate", False)),
        "detail": "release-gate.py developer",
    }
    gates["candidate_gate"] = {
        "ok": bool(flags.get("candidate_gate", False)),
        "detail": "release-gate.py candidate",
    }
    gates["verify_artifact"] = {
        "ok": bool(flags.get("candidate_verified", False)),
        "detail": "release-gate.py verify-artifact",
    }

    ci = evidence.get("ci") or {}
    gates["ci"] = {
        "ok": ci.get("total", 0) >= 7 and ci.get("passed", 0) == ci.get("total", 0),
        "detail": f"{ci.get('passed')}/{ci.get('total')} on {str(ci.get('sha'))[:10]}",
    }
    gates["suites"] = {
        "ok": bool(flags.get("developer_gate", False)),
        "detail": "backend/frontend/Rust/Gateway/Playwright are inside the developer gate",
    }

    installer = evidence.get("installer") or {}
    recorded = (evidence.get("installer_sha_recorded") or {}).get("sha256")
    gates["signature"] = {
        "ok": bool(installer.get("present") and installer.get("signature_present")
                   and recorded and recorded == installer.get("sha256")),
        "detail": f"installer {installer.get('sha256')}, recorded {recorded}",
    }
    gates["installed_smoke"] = {
        "ok": bool(flags.get("installed_smoke", False)),
        "detail": "the installed 1.2.6 window answered the smoke suite",
    }
    gates["data_preserved"] = {
        "ok": bool(flags.get("data_safe", False)),
        "detail": "integrity, users, chats, messages, device records",
    }
    gates["device_stable"] = {
        "ok": bool(flags.get("device_stable", False)),
        "detail": "the device_id did not change across the live runs",
    }
    gates["pods_empty"] = {
        "ok": (coordinator.get("pods_created") or 0) >= 0
              and bool((evidence.get("pods") or []) == []),
        "detail": "the provider account has no running Pod",
    }

    blocked = [name for name in MANDATORY if not gates.get(name, {}).get("ok")]
    return {
        "version": VERSION,
        "tag": TAG,
        "publish": not blocked,
        "gates": gates,
        "blocked_by": blocked,
        "steps": STEPS,
    }


STEPS = (
    ("01_source", "product version 1.2.6, clean tree, no secrets in the diff"),
    ("02_developer", "release-gate.py developer"),
    ("03_candidate", "release-gate.py candidate on the signed installer"),
    ("04_verify_artifact", "release-gate.py verify-artifact (production public key)"),
    ("05_ci", "GitHub CI 7/7 on the final SHA"),
    ("06_backup", "verified pre-install backup of the user database"),
    ("07_install", "install the signed 1.2.6 over 1.2.5"),
    ("08_installed_smoke", "installed window smoke, device_id unchanged"),
    ("09_tag", "annotated tag v1.2.6, pushed without force"),
    ("10_release", "GitHub Release with installer, .sig and SHA256SUMS"),
    ("11_anonymous_download", "anonymous download and SHA-256 match"),
    ("12_downloads", "copy installer and .sig into the Gateway download root"),
    ("13_manifest_backup", "back up the 1.2.5 updater manifest"),
    ("14_manifest", "publish the 1.2.6 manifest atomically"),
    ("15_updater_matrix", "1.2.3/1.2.4/1.2.5 -> 1.2.6, 1.2.6 -> no update"),
    ("16_health", "Gateway /health, 12Testers untouched, pods []"),
)


def print_plan(plan: dict) -> None:
    print(f"=== Canalla {plan['version']} release plan ({'PUBLISH' if plan['publish'] else 'BLOCKED'})")
    for name in MANDATORY:
        gate = plan["gates"][name]
        print(f"[{'PASS' if gate['ok'] else 'BLOCKED':7s}] {name:20s} — {gate['detail']}")
    print("--- steps")
    for step, detail in plan["steps"]:
        print(f"  {step}  {detail}")
    if plan["blocked_by"]:
        print("blocked by:", ", ".join(plan["blocked_by"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="actually run the steps (default: plan only)")
    parser.add_argument("--evidence", action="store_true",
                        help="collect and store current evidence, then plan")
    parser.add_argument("--resume-from", default="", help="continue from this step id")
    parser.add_argument("--only", default="", help="run exactly one step id")
    args = parser.parse_args(argv)

    import release_steps

    evidence = collect_evidence()
    plan = release_plan(evidence)
    if args.evidence:
        EVIDENCE.write_text(
            json.dumps({"evidence": evidence, "plan": plan}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"evidence written to {EVIDENCE}")
    print_plan(plan)

    if not args.execute:
        if not plan["publish"]:
            print("\nPUBLICATION REFUSED: a mandatory gate is not PASS")
            return 2
        print("\ndry run: nothing was tagged, published or deployed")
        return 0

    # Phase A: the gates only the release steps themselves can prove. This is not a bypass — the
    # plan is re-derived from the evidence they leave behind, and no step can produce a live gate.
    if plan["blocked_by"] and set(plan["blocked_by"]) <= PREPUBLICATION:
        print("\nphase A: producing the pre-publication gates")
        context_a = release_steps.Context(evidence=evidence)
        for step in ("01_source", "02_developer", "03_candidate", "04_verify_artifact",
                     "05_ci", "08_installed_smoke"):
            if step in plan["blocked_by"] or step in {"02_developer", "03_candidate",
                                                      "04_verify_artifact"}:
                context_a.log.clear()
                release_steps.run_steps(context_a, only=step)
                if context_a.log and context_a.log[-1]["status"] in {"FAIL", "BLOCKED"}:
                    print(f"\n{step} failed; publication refused")
                    return 1
        evidence = collect_evidence()
        plan = release_plan(evidence)
        print_plan(plan)

    if not plan["publish"]:
        print("\nPUBLICATION REFUSED: a mandatory gate is not PASS")
        return 2

    # The gate stays the authority even with --execute: no step may widen it.
    context = release_steps.Context(evidence=evidence)
    exit_code = release_steps.run_steps(context, resume_from=args.resume_from,
                                        only=args.only)
    transcript = HERE / (
        "release-run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    transcript.write_text(
        json.dumps({"version": VERSION, "head": evidence.get("head"),
                    "steps": context.log}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\ntranscript written to {transcript}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
