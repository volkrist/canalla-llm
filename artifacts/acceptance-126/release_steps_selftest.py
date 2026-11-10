"""Free proofs that the release steps are idempotent and that the gate cannot be bypassed.

No network, no git write, no tag, no publication: the fake context records every command it would
have run, and the assertions are about what is *not* in that log.

    python artifacts/acceptance-126/release_steps_selftest.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import release_coordinator  # noqa: E402
import release_steps  # noqa: E402

FAILURES: list[str] = []
CHECKS = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"[PASS] {label}")
        return
    print(f"[FAIL] {label} — {detail}")
    FAILURES.append(label)


class Result:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeContext(release_steps.Context):
    """A world where nothing exists unless the test says it does."""

    def __init__(self, **answers):
        super().__init__()
        self.commands: list[list[str]] = []
        self.shells: list[str] = []
        self.remotes: list[str] = []
        self.http_calls: list[str] = []
        self.answers = answers
        self.run = self._run
        self.http = self._http
        self.ssh = self._ssh
        self.scp = self._scp
        self.shell = self._shell
        self.now = lambda: datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

    def _run(self, command, **_):
        self.commands.append(list(command))
        joined = " ".join(str(item) for item in command)
        if "gh run list" in joined:
            return Result(stdout=json.dumps(self.answers.get("runs", [])).encode())
        if "gh run view" in joined:
            return Result(stdout=json.dumps({"jobs": self.answers.get("jobs", [])}).encode())
        if "gh release view" in joined:
            payload = self.answers.get("release")
            return Result(returncode=0 if payload else 1,
                          stdout=json.dumps(payload or {}).encode())
        if "ls-remote" in joined:
            return Result(stdout=self.answers.get("remote_tag", "").encode())
        if "rev-parse -q --verify refs/tags" in joined:
            return Result(returncode=0 if self.answers.get("local_tag") else 1,
                          stdout=b"abc\n" if self.answers.get("local_tag") else b"")
        if "updates-manifest.py" in joined:
            return Result(returncode=self.answers.get("manifest_exit", 0))
        if "readonly-runpod-state.py" in joined:
            return Result(stdout=json.dumps({"pods": self.answers.get("pods", [])}).encode())
        return Result(returncode=self.answers.get("default_exit", 0))

    def _http(self, method, url, **_):
        self.http_calls.append(f"{method} {url}")
        return self.answers.get("http", {}).get(url, (404, b"", {}))

    def git(self, *args):
        result = self._run(["git", *args])
        return ((result.stdout or b"") + (result.stderr or b"")).decode(
            "utf-8", "replace").strip()

    def _ssh(self, command, **_):
        self.remotes.append(command)
        return Result(returncode=self.answers.get("ssh_exit", 0))

    def _scp(self, local, remote, **_):
        self.remotes.append(f"scp {local} {remote}")
        return Result()

    def _shell(self, command, **_):
        self.shells.append(command)
        return Result()


def evidence(**overrides) -> dict:
    base = {
        "version": "1.2.6",
        "head": "40cdf46",
        "branch": "release/canalla-1.2.4",
        "dirty": False,
        "product_version": "1.2.6",
        "installer": {"present": True, "sha256": "ab" * 32, "signature_present": True},
        "installer_sha_recorded": {"sha256": "ab" * 32},
        "installed": {"product_version": "1.2.6"},
        "coordinator": {
            "outcome": "READY_FOR_RELEASE",
            "scenario_verdicts": {"T3L": "PASS", "T4L": "PASS", "LA": "PASS"},
            "pods_created": 3,
            "spent_usd": 0.31,
        },
        "prior_live": {"run": "live-20261108T131238", "scenario": "LA", "verdict": "PASS"},
        "ci": {"passed": 7, "total": 7, "sha": "40cdf46"},
        "readiness": {
            "ok": True,
            "flags": {
                "t3_free_ready": True,
                "t4_free_ready": True,
                "candidate_verified": True,
                "data_safe": True,
                "device_stable": True,
                "gpu_watchdog_ready": True,
                "release_pipeline_ready": True,
                "budget_available": True,
                "developer_gate": True,
                "candidate_gate": True,
                "installed_smoke": True,
                "tor_ready": True,
            },
        },
        "pods": [],
    }
    base.update(overrides)
    return base


def test_full_evidence_publishes() -> None:
    plan = release_coordinator.release_plan(evidence())
    check("a fully certified evidence set may publish", plan["publish"] is True,
          str(plan["blocked_by"]))


def test_every_mandatory_gate_can_block() -> None:
    for gate in release_coordinator.MANDATORY:
        data = evidence()
        if gate == "t3_live":
            data["coordinator"]["scenario_verdicts"]["T3L"] = "FAIL"
        elif gate == "t4_live":
            data["coordinator"]["scenario_verdicts"]["T4L"] = "FAIL"
        elif gate in {"d2_live", "long_autonomy_live"}:
            data["prior_live"] = {"verdict": "FAIL"}
        elif gate == "tor":
            data["readiness"]["flags"]["tor_ready"] = False
        elif gate == "developer_gate":
            data["readiness"]["flags"]["developer_gate"] = False
        elif gate == "candidate_gate":
            data["readiness"]["flags"]["candidate_gate"] = False
        elif gate == "verify_artifact":
            data["readiness"]["flags"]["candidate_verified"] = False
        elif gate == "ci":
            data["ci"] = {"passed": 6, "total": 7, "sha": "40cdf46"}
        elif gate == "suites":
            data["readiness"]["flags"]["developer_gate"] = False
        elif gate == "installed_smoke":
            data["readiness"]["flags"]["installed_smoke"] = False
        elif gate == "data_preserved":
            data["readiness"]["flags"]["data_safe"] = False
        elif gate == "device_stable":
            data["readiness"]["flags"]["device_stable"] = False
        elif gate == "signature":
            data["installer_sha_recorded"] = {"sha256": "00" * 32}
        elif gate == "pods_empty":
            data["pods"] = [{"id": "x", "status": "RUNNING"}]
        plan = release_coordinator.release_plan(data)
        check(f"a broken {gate} refuses publication", plan["publish"] is False)
        check(f"and the refusal names {gate}", gate in plan["blocked_by"],
              str(plan["blocked_by"]))


def test_tag_step_is_idempotent(tmp: Path) -> None:
    ctx = FakeContext(local_tag=True, remote_tag="abc\trefs/tags/v1.2.6")
    entry = release_steps.tag(ctx)
    check("an existing tag is a skip", entry["status"] == "SKIP" and entry["already"])
    check("no tag is created twice",
          not any("tag" in " ".join(cmd) and "-a" in cmd for cmd in ctx.commands),
          str(ctx.commands))


def test_release_step_is_idempotent(tmp: Path) -> None:
    installer = tmp / release_steps.INSTALLER_NAME
    signature = tmp / (release_steps.INSTALLER_NAME + ".sig")
    sums = tmp / f"SHA256SUMS-{release_steps.VERSION}.txt"
    installer.write_bytes(b"installer")
    signature.write_bytes(b"signature")
    sums.write_bytes(b"sums")
    original = (release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS)
    release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS = installer, signature, sums
    try:
        digest = "sha256:" + hashlib.sha256(b"installer").hexdigest()
        ctx = FakeContext(release={
            "tagName": "v1.2.6",
            "assets": [
                {"name": release_steps.ASSET_NAMES["installer"], "digest": digest},
                {"name": release_steps.ASSET_NAMES["signature"],
                 "digest": "sha256:" + hashlib.sha256(b"signature").hexdigest()},
                {"name": release_steps.ASSET_NAMES["sums"],
                 "digest": "sha256:" + hashlib.sha256(b"sums").hexdigest()},
            ],
        })
        entry = release_steps.release(ctx)
        check("a complete release is a skip", entry["status"] == "SKIP" and entry["already"])
        check("no release is created twice",
              not any("release" in " ".join(cmd) and "create" in cmd for cmd in ctx.commands),
              str(ctx.commands))
        check("no asset is uploaded twice",
              not any("upload" in cmd for cmd in ctx.commands))

        ctx = FakeContext(release={
            "tagName": "v1.2.6",
            "assets": [{"name": release_steps.ASSET_NAMES["installer"], "digest": "sha256:dead"}],
        })
        entry = release_steps.release(ctx)
        check("a published asset with another digest is refused, never overwritten",
              entry["status"] == "FAIL")
    finally:
        release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS = original


def test_manifest_step_is_idempotent() -> None:
    digest = "ab" * 32
    url = ("https://gateway.12testers.store/updates/latest"
           "?target=windows&arch=x86_64&current_version=1.2.5")
    live = {"version": "1.2.6", "platforms": {"windows-x86_64": {"sha256": digest}}}
    ctx = FakeContext(http={url: (200, json.dumps(live).encode(), {})})
    ctx.evidence = {"installer": {"sha256": digest}}
    original = release_steps.sha256
    release_steps.sha256 = lambda path: digest
    try:
        entry = release_steps.manifest(ctx)
        check("an already-correct manifest is a skip",
              entry["status"] == "SKIP" and entry["already"])
        check("no manifest build runs again",
              not any("updates-manifest.py" in " ".join(cmd) for cmd in ctx.commands),
              str(ctx.commands))
    finally:
        release_steps.sha256 = original


def test_resume_from_starts_at_the_named_step() -> None:
    digest = "ab" * 32
    url = ("https://gateway.12testers.store/updates/latest"
           "?target=windows&arch=x86_64&current_version=1.2.5")
    live = {"version": "1.2.6", "platforms": {"windows-x86_64": {"sha256": digest}}}
    ctx = FakeContext(http={url: (200, json.dumps(live).encode(), {})})
    ctx.evidence = {"installer": {"sha256": digest}}
    original = release_steps.sha256
    release_steps.sha256 = lambda path: digest
    try:
        release_steps.run_steps(ctx, resume_from="13_manifest_backup",
                                only="13_manifest_backup")
    finally:
        release_steps.sha256 = original
    called = [entry["step"] for entry in ctx.log]
    check("resume starts at the named step", called == ["13_manifest_backup"], str(called))
    check("earlier steps are not repeated",
          not any(name.startswith(("01_", "02_", "09_", "10_")) for name in called), str(called))


def test_a_failure_aborts_the_sequence() -> None:
    ctx = FakeContext(default_exit=0)
    original = release_steps.STEPS
    calls: list[str] = []

    def ok(name):
        def step(c):
            calls.append(name)
            return c.record(name, "PASS", "ok")
        return step

    def bad(name):
        def step(c):
            calls.append(name)
            return c.record(name, "FAIL", "boom")
        return step

    release_steps.STEPS = (("01_a", ok("01_a")), ("02_b", bad("02_b")),
                           ("03_c", ok("03_c")))
    try:
        code = release_steps.run_steps(ctx)
    finally:
        release_steps.STEPS = original
    check("a failing step makes the run fail", code == 1)
    check("nothing after the failure runs", calls == ["01_a", "02_b"], str(calls))


def test_source_has_no_unsafe_flags() -> None:
    source = (HERE / "release_steps.py").read_text(encoding="utf-8")
    for forbidden in ("--force", "push --tags", "git push -f", "--clobber"):
        check(f"release_steps.py never uses {forbidden!r}", forbidden not in source)
    for assignment in ('API_KEY = "', 'SECRET = "', 'TOKEN = "', 'PASSWORD = "'):
        check(f"release_steps.py assigns no {assignment.strip()!r}", assignment not in source)
    check("the signing key is only ever a path",
          "KEY = Path.home()" in source and "KEY.read" not in source
          and ".read_text()" not in source.split("KEY = Path.home()")[1][:200])


def test_a_changed_installer_is_refused(tmp: Path) -> None:
    """The byte the signature covers must still be the byte on disk when we install or publish."""
    installer = tmp / release_steps.INSTALLER_NAME
    signature = tmp / (release_steps.INSTALLER_NAME + ".sig")
    installer.write_bytes(b"installer")
    signature.write_bytes(b"signature")
    original = (release_steps.INSTALLER, release_steps.SIGNATURE)
    release_steps.INSTALLER, release_steps.SIGNATURE = installer, signature
    try:
        ctx = FakeContext()
        ctx.evidence = {"installer": {"sha256": "00" * 32}}
        entry = release_steps.install(ctx)
        check("an installer that changed after verification is refused",
              entry["status"] == "FAIL", entry["detail"])
    finally:
        release_steps.INSTALLER, release_steps.SIGNATURE = original


def test_a_partially_uploaded_release_resumes(tmp: Path) -> None:
    installer = tmp / release_steps.INSTALLER_NAME
    signature = tmp / (release_steps.INSTALLER_NAME + ".sig")
    sums = tmp / f"SHA256SUMS-{release_steps.VERSION}.txt"
    installer.write_bytes(b"installer")
    signature.write_bytes(b"signature")
    sums.write_bytes(b"sums")
    original = (release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS)
    release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS = installer, signature, sums
    try:
        digest = "sha256:" + hashlib.sha256(b"installer").hexdigest()
        ctx = FakeContext(release={
            "tagName": "v1.2.6",
            "assets": [{"name": release_steps.ASSET_NAMES["installer"], "digest": digest}],
        })
        entry = release_steps.release(ctx)
        uploads = [
            " ".join(cmd) for cmd in ctx.commands if "upload" in cmd
        ]
        check("a partially uploaded release is completed, not recreated",
              entry["status"] == "PASS", entry["detail"])
        check("only the missing assets are uploaded", len(uploads) == 1, str(uploads))
        check("no second release is created",
              not any("release" in " ".join(cmd) and "create" in cmd for cmd in ctx.commands))
    finally:
        release_steps.INSTALLER, release_steps.SIGNATURE, release_steps.SUMS = original


def test_a_manifest_failure_is_reported_and_backed_up(tmp: Path) -> None:
    url = ("https://gateway.12testers.store/updates/latest"
           "?target=windows&arch=x86_64&current_version=1.2.5")
    ctx = FakeContext(http={url: (200, json.dumps({"version": "1.2.5"}).encode(), {})})
    ctx.answers["manifest_exit"] = 1
    entry = release_steps.manifest_backup(ctx)
    check("the previous manifest is backed up before the change",
          entry["status"] == "PASS" and "cp -p" in " ".join(ctx.remotes), entry["detail"])
    entry = release_steps.manifest(ctx)
    check("a manifest that cannot be built is a FAIL, never a silent success",
          entry["status"] == "FAIL", entry["detail"])


def test_prepublication_gates_can_be_produced_but_live_gates_cannot() -> None:
    for gate in ("developer_gate", "candidate_gate", "suites", "verify_artifact",
                 "installed_smoke"):
        check(f"{gate} is a pre-publication gate", gate in release_coordinator.PREPUBLICATION)
    for gate in ("t3_live", "t4_live", "d2_live", "long_autonomy_live", "ci", "signature",
                 "pods_empty"):
        check(f"{gate} can never be produced by a release step",
              gate not in release_coordinator.PREPUBLICATION)

    progress = release_coordinator.HERE / "release-progress.json"
    saved = progress.read_text(encoding="utf-8") if progress.exists() else None
    try:
        progress.write_text(json.dumps({
            "02_developer": "PASS", "03_candidate": "PASS",
            "04_verify_artifact": "SKIP", "08_installed_smoke": "FAIL",
        }), encoding="utf-8")
        flags = release_coordinator._progress_flags()
        check("a passed step contributes its gate", flags.get("developer_gate") is True)
        check("a skipped step also counts as done", flags.get("candidate_verified") is True)
        check("a failed step contributes nothing", "installed_smoke" not in flags)
    finally:
        if saved is None:
            progress.unlink(missing_ok=True)
        else:
            progress.write_text(saved, encoding="utf-8")


def main() -> int:
    tests = [
        test_full_evidence_publishes,
        test_every_mandatory_gate_can_block,
        test_tag_step_is_idempotent,
        test_release_step_is_idempotent,
        test_manifest_step_is_idempotent,
        test_resume_from_starts_at_the_named_step,
        test_a_failure_aborts_the_sequence,
        test_source_has_no_unsafe_flags,
        test_prepublication_gates_can_be_produced_but_live_gates_cannot,
        test_a_changed_installer_is_refused,
        test_a_partially_uploaded_release_resumes,
        test_a_manifest_failure_is_reported_and_backed_up,
    ]
    for test in tests:
        print(f"--- {test.__name__}")
        try:
            with tempfile.TemporaryDirectory(prefix="canalla-release-") as tmp:
                parameters = test.__code__.co_varnames[:test.__code__.co_argcount]
                if parameters:
                    test(Path(tmp))
                else:
                    test()
        except Exception as error:  # noqa: BLE001 - a crashing test is a failing test
            check(f"{test.__name__} raised", False, f"{type(error).__name__}: {error}")
    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    print("SELF-TEST", "PASS" if not FAILURES else "FAIL")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
