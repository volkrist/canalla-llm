"""The executable half of the 1.2.6 release: sixteen idempotent steps.

`release_coordinator.release_plan()` decides *whether* a release may happen. This module is only
*how*: each step looks up the object it would create before creating it, so a half-finished
publication resumes instead of producing a second tag, release, asset or manifest.

Everything that touches the outside world goes through a `Context`, so the whole sequence can be
driven by a fake in tests. Nothing here reads, prints or copies the signing key: it is only ever
passed as a path to `scripts/production-signing.ps1`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION = "1.2.6"
TAG = "v1.2.6"
BUNDLE = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
INSTALLER_NAME = f"Canalla LLM_{VERSION}_x64-setup.exe"
INSTALLER = BUNDLE / INSTALLER_NAME
SIGNATURE = BUNDLE / (INSTALLER_NAME + ".sig")
SUMS = REPO / "artifacts" / f"SHA256SUMS-{VERSION}.txt"
NOTES = REPO / "artifacts" / "updater" / f"notes-{VERSION}.txt"
MANIFEST_LOCAL = REPO / "artifacts" / "updater" / f"updates-manifest-{VERSION}.json"
KEY = Path.home() / ".canalla-updater" / "production" / "canalla-updater-production.key"
PY = REPO / "apps" / "backend" / ".venv" / "Scripts" / "python.exe"

GATEWAY = "https://gateway.12testers.store"
SSH_HOST = "distance"
DOWNLOADS_DIR = "/srv/canalla-downloads"
MANIFEST_PATH = "/var/lib/alex-gateway/updates/updates-manifest.json"
ASSET_NAMES = {
    "installer": f"Canalla.LLM_{VERSION}_x64-setup.exe",
    "signature": f"Canalla.LLM_{VERSION}_x64-setup.exe.sig",
    "sums": f"SHA256SUMS-{VERSION}.txt",
}

# The Gateway mirrors the artifact under the name the signature names (`file:` in the trusted
# comment), while GitHub sanitizes spaces into dots. Step 12 used to copy the sanitized name, so
# the manifest's signed URL 404'd; the two names are now kept apart on purpose.
GATEWAY_ASSETS = (INSTALLER_NAME, INSTALLER_NAME + ".sig")


def gateway_url() -> str:
    """The public artifact URL, with a per-release cache-buster.

    A CDN happily caches a 404 (Cloudflare did, for four hours, after step 12 published the file
    under another name). The query string makes each release a distinct cache key, so a corrected
    publication is immediately visible and no client is pinned to a dead URL.
    """
    return f"{GATEWAY}/downloads/{urllib.parse.quote(INSTALLER_NAME)}?v={VERSION}"


# ------------------------------------------------------------------------------------------ context


@dataclass
class Context:
    """Every external effect, injectable. The default implementation is the real one."""

    run: object = None
    http: object = None
    ssh: object = None
    scp: object = None
    now: object = None
    dry_run: bool = False
    evidence: dict = field(default_factory=dict)
    log: list = field(default_factory=list)
    shell: object = None

    def __post_init__(self):
        self.run = self.run or _run
        self.http = self.http or _http
        self.ssh = self.ssh or _ssh
        self.scp = self.scp or _scp
        self.now = self.now or (lambda: datetime.now(timezone.utc))
        self.shell = self.shell or _shell

    def git(self, *args: str) -> str:
        """Read-only git through the same port, so tests never touch the real repository."""
        return _git(*args)

    def record(self, step: str, status: str, detail: str, already: bool = False,
               exit_code: int | None = None) -> dict:
        entry = {
            "step": step,
            "status": status,
            "detail": detail[:600],
            "already": already,
            "exit_code": exit_code,
            "at": self.now().isoformat(),
        }
        self.log.append(entry)
        print(f"[{status:7s}] {step} — {detail[:200]}")
        return entry


def _shell(command: str, *, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, cwd=str(cwd or REPO), capture_output=True,
                          timeout=timeout)


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 3600,
         stdin: bytes | None = None) -> subprocess.CompletedProcess:
    resolved = list(command)
    executable = shutil.which(command[0])
    if executable:
        resolved[0] = executable
    return subprocess.run(resolved, cwd=str(cwd or REPO), capture_output=True, timeout=timeout,
                          input=stdin, env=dict(os.environ, PYTHONIOENCODING="utf-8",
                                               PYTHONUTF8="1"))


def _http(method: str, url: str, *, body: bytes | None = None, headers: dict | None = None,
          timeout: int = 300, auth: bool = False) -> tuple[int, bytes, dict]:
    # Headers *add* to the default user agent, never replace it. Passing only
    # `Cache-Control: no-cache` sent a request with no User-Agent at all, which Cloudflare
    # answers with 403 "error code: 1010" - the manifest probe then reported "no manifest" and
    # step 14 declared the Gateway still on the previous version while it was already serving
    # the new one.
    merged = {"User-Agent": "canalla-release/1.2.6", **(headers or {})}
    request = urllib.request.Request(url, data=body, method=method, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def _ssh(command: str, *, timeout: int = 300) -> subprocess.CompletedProcess:
    # `sudo -n` elevates one command, so a compound command left its second half unprivileged:
    # `test -f … && cp -p …` copied the root-owned updater manifest as the plain SSH user and
    # failed with EACCES. The whole command now runs inside one elevated shell.
    wrapped = "sudo -n sh -c " + shlex.quote(command)
    return subprocess.run(["ssh", SSH_HOST, wrapped], capture_output=True, timeout=timeout)


def _scp(local: Path, remote: str, *, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["scp", str(local), f"{SSH_HOST}:{remote}"], capture_output=True,
                          timeout=timeout)


def text(result) -> str:
    return ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ok(result) -> bool:
    return getattr(result, "returncode", 1) == 0


def _git(*args: str) -> str:
    return text(_run(["git", "--no-pager", *args])).strip()


# ---------------------------------------------------------------------------------------- steps


def source(ctx: Context) -> dict:
    version = ""
    product = REPO / "apps" / "backend" / "app" / "product.py"
    for line in product.read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION"):
            version = line.split("=", 1)[1].strip().strip('"')
            break
    if version != VERSION:
        return ctx.record("01_source", "FAIL", f"product version is {version!r}")
    # Only the inputs a build actually consumes. Runtime evidence under `artifacts/` and the
    # screenshot fixtures under `docs/screenshots` are not part of the released sources — and the
    # acceptance run keeps writing both while it works.
    dirty = _git("status", "--porcelain", "--untracked-files=no", "--", "apps", "scripts",
                 ".github", "docs", ":(exclude)docs/screenshots")
    if dirty:
        return ctx.record("01_source", "FAIL", f"tracked build inputs are modified: {dirty[:200]}")
    diff = text(_run(["git", "--no-pager", "diff", "v1.2.5..HEAD", "--", "apps", "scripts"]))
    # A fixture that *tests* the redaction of a credential is not a leaked credential. Only added
    # lines outside test files are scanned, and the file is tracked from the diff header.
    markers = ("begin private key", "sk-live", "rpa_", "runpod_api_key=")
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        name = current.lower()
        if (
            "/tests/" in name
            or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        ):
            continue
        lowered = line.lower()
        for marker in markers:
            if marker in lowered:
                return ctx.record(
                    "01_source", "FAIL",
                    f"a secret marker {marker!r} is in the added lines of {current or '?'}",
                )
    return ctx.record("01_source", "PASS",
                      f"{VERSION} at {ctx.git('rev-parse', '--short', 'HEAD')}")


def _gate(ctx: Context, name: str, step: str, arguments: list[str]) -> dict:
    result = ctx.run([str(PY), str(REPO / "scripts" / "release-gate.py"), name, *arguments])
    detail = text(result).strip().splitlines()
    tail = detail[-1] if detail else "no output"
    return ctx.record(step, "PASS" if _ok(result) else "FAIL", f"{name}: {tail}",
                      exit_code=getattr(result, "returncode", None))


def developer_gate(ctx: Context) -> dict:
    return _gate(ctx, "developer", "02_developer", [])


def candidate_gate(ctx: Context) -> dict:
    return _gate(ctx, "candidate", "03_candidate",
                 ["--installer", str(INSTALLER), "--version", VERSION])


def verify_artifact(ctx: Context) -> dict:
    return _gate(ctx, "verify-artifact", "04_verify_artifact",
                 ["--installer", str(INSTALLER), "--version", VERSION])


def ci_gate(ctx: Context) -> dict:
    """The same CI reader the plan uses, including its bounded wait for an in-flight run."""
    import release_coordinator

    head = _git("rev-parse", "HEAD")
    evidence = release_coordinator._ci_evidence(head)
    passed = int(evidence.get("passed") or 0)
    total = int(evidence.get("total") or 0)
    detail = evidence.get("reason") or f"{passed}/{total} jobs on {head[:10]}"
    if total < 7 or passed != total:
        return ctx.record("05_ci", "FAIL", detail)
    return ctx.record("05_ci", "PASS", f"{passed}/{total} jobs on {head[:10]}")


def backup(ctx: Context) -> dict:
    result = ctx.run(
        [str(PY), str(REPO / "artifacts" / "acceptance-release-126" / "pre_install_backup.py"),
         "--sidecar"]
    )
    output = text(result)
    record_path = REPO / "artifacts" / "acceptance-release-126" / "pre-install-backup.json"
    verified = False
    if record_path.is_file():
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
            verified = payload.get("status") == "PASS" or (payload.get("verify") or {}).get(
                "verified")
            detail = (payload.get("verify") or {}).get("id") or payload.get("status")
        except ValueError:
            detail = "unreadable record"
    else:
        detail = "no record written"
    if not _ok(result) or not verified:
        return ctx.record("06_backup", "FAIL", f"backup not verified: {output[-300:]}")
    return ctx.record("06_backup", "PASS", f"verified backup {detail}")


def install(ctx: Context) -> dict:
    expected = (ctx.evidence.get("installer") or {}).get("sha256")
    if INSTALLER.is_file() and expected and sha256(INSTALLER) != expected:
        return ctx.record("07_install", "FAIL", "installer digest changed after verification")
    installed = _installed_version()
    tree_sidecar = REPO / "apps" / "desktop" / "src-tauri" / "sidecar" / "alex-backend" / "alex-backend.exe"
    installed_sidecar = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Canalla LLM"
                         / "sidecar" / "alex-backend" / "alex-backend.exe")
    same_bytes = (
        tree_sidecar.is_file()
        and installed_sidecar.is_file()
        and sha256(tree_sidecar) == sha256(installed_sidecar)
    )
    if installed == VERSION and same_bytes:
        return ctx.record("07_install", "SKIP",
                          f"the installed build is already {installed} with the packaged sidecar",
                          already=True)
    result = ctx.shell(f'"{INSTALLER}" /S')
    if not _ok(result):
        return ctx.record("07_install", "FAIL", text(result)[-300:])
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if _installed_version() == VERSION:
            return ctx.record("07_install", "PASS", f"installed {VERSION}")
        time.sleep(5)
    return ctx.record("07_install", "FAIL", "the installed version did not become 1.2.6")


def installed_smoke(ctx: Context) -> dict:
    """The smoke starts its own isolated window, so the running app must be closed for it.

    The installed build is single-instance: with the owner's window open the smoke's launch is
    forwarded to it, its own CDP port never opens, and the run fails for a reason that has nothing
    to do with the product. The window is closed through the documented helper and reopened after.
    """
    helper = REPO / "artifacts" / "ensure-app-cdp.ps1"

    def app(*, stop: bool = False) -> None:
        if not helper.is_file():
            return
        command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)]
        if stop:
            command.append("-Stop")
        ctx.run(command, timeout=300)

    app(stop=True)
    try:
        result = ctx.run(["node", "e2e/installed-status.mjs"],
                         cwd=REPO / "apps" / "desktop", timeout=1200)
        output = text(result)
    finally:
        app()

    if not _ok(result):
        return ctx.record("08_installed_smoke", "FAIL", output[-400:])
    return ctx.record("08_installed_smoke", "PASS",
                      "INSTALLED-UI: PASS" if "INSTALLED-UI: PASS" in output
                      else (output.strip().splitlines()[-1][:200] if output.strip()
                            else "smoke passed"))


def tag(ctx: Context) -> dict:
    if ctx.git("rev-parse", "-q", "--verify", f"refs/tags/{TAG}"):
        remote = ctx.git("ls-remote", "--tags", "origin", TAG)
        if remote:
            return ctx.record("09_tag", "SKIP", f"{TAG} exists locally and on origin",
                              already=True)
        result = ctx.run(["git", "push", "origin", TAG])
        if not _ok(result):
            return ctx.record("09_tag", "FAIL", text(result)[-300:])
        return ctx.record("09_tag", "PASS", f"pushed the existing {TAG}")
    result = ctx.run(["git", "tag", "-a", TAG, "-m", f"Canalla LLM {VERSION}"])
    if not _ok(result):
        return ctx.record("09_tag", "FAIL", text(result)[-300:])
    pushed = ctx.run(["git", "push", "origin", TAG])
    if not _ok(pushed):
        return ctx.record("09_tag", "FAIL", text(pushed)[-300:])
    return ctx.record("09_tag", "PASS", f"annotated {TAG} pushed")


def _release_assets(ctx: Context) -> dict | None:
    result = ctx.run(["gh", "release", "view", TAG, "--json", "assets,tagName,isDraft"])
    try:
        payload = json.loads(text(result) or "{}")
    except ValueError:
        return None
    if not payload or not payload.get("tagName"):
        return None
    return payload


def release(ctx: Context) -> dict:
    existing = _release_assets(ctx)
    notes = NOTES.read_text(encoding="utf-8") if NOTES.is_file() else f"Canalla LLM {VERSION}\n"
    notes_file = REPO / "artifacts" / "updater" / f"release-body-{VERSION}.md"
    notes_file.write_text(notes, encoding="utf-8")
    if existing is None:
        created = ctx.run(["gh", "release", "create", TAG, "--title", f"Canalla LLM {VERSION}",
                           "--notes-file", str(notes_file)])
        if not _ok(created):
            return ctx.record("10_release", "FAIL", text(created)[-400:])
        existing = _release_assets(ctx) or {"assets": []}
    have = {row.get("name"): row.get("digest") for row in existing.get("assets", [])}
    uploads = []
    for name, path in (
        (ASSET_NAMES["installer"], INSTALLER),
        (ASSET_NAMES["signature"], SIGNATURE),
        (ASSET_NAMES["sums"], SUMS),
    ):
        if not path.is_file():
            return ctx.record("10_release", "FAIL", f"missing local asset {path.name}")
        digest = "sha256:" + sha256(path)
        if have.get(name) and have.get(name) != digest:
            return ctx.record(
                "10_release", "FAIL",
                f"asset {name} already published with a different digest; refusing to overwrite",
            )
        if not have.get(name):
            uploads.append(str(path))
    if not uploads:
        return ctx.record("10_release", "SKIP", "all three assets are already published",
                          already=True)
    result = ctx.run(["gh", "release", "upload", TAG, *uploads])
    if not _ok(result):
        return ctx.record("10_release", "FAIL", text(result)[-400:])
    return ctx.record("10_release", "PASS", f"uploaded {len(uploads)} asset(s)")


def anonymous_download(ctx: Context) -> dict:
    expected = (ctx.evidence.get("installer") or {}).get("sha256")
    base = f"https://github.com/volkrist/canalla-llm/releases/download/{TAG}/"
    status, body, _ = ctx.http("GET", base + ASSET_NAMES["installer"])
    if status != 200:
        return ctx.record("11_anonymous_download", "FAIL", f"installer download {status}")
    digest = hashlib.sha256(body).hexdigest()
    if expected and digest != expected:
        return ctx.record("11_anonymous_download", "FAIL",
                          f"downloaded digest {digest} != {expected}")
    sig_status, sig_body, _ = ctx.http("GET", base + ASSET_NAMES["signature"])
    if sig_status != 200 or sig_body != SIGNATURE.read_bytes():
        return ctx.record("11_anonymous_download", "FAIL", "the published .sig differs")
    return ctx.record("11_anonymous_download", "PASS",
                      f"{len(body)} bytes, anonymous download matches the signed digest")


def _remote_sha(ctx: Context, path: str) -> str | None:
    result = ctx.ssh(f"sha256sum '{path}'")
    if not _ok(result):
        return None
    line = text(result).strip().splitlines()
    return line[0].split()[0] if line else None


def downloads(ctx: Context) -> dict:
    results = []
    for name, local in ((INSTALLER_NAME, INSTALLER), (INSTALLER_NAME + ".sig", SIGNATURE)):
        digest = sha256(local)
        remote_path = f"{DOWNLOADS_DIR}/{name}"
        if _remote_sha(ctx, remote_path) == digest:
            results.append(f"{name}: present")
            continue
        staged = f"/tmp/{name}"
        copied = ctx.scp(local, staged)
        if not _ok(copied):
            return ctx.record("12_downloads", "FAIL", text(copied)[-300:])
        placed = ctx.ssh(f"install -m 644 -o root -g root '{staged}' '{remote_path}'")
        if not _ok(placed):
            return ctx.record("12_downloads", "FAIL", text(placed)[-300:])
        results.append(f"{name}: copied")
    status, body, _ = ctx.http("GET", gateway_url())
    if status != 200 or hashlib.sha256(body).hexdigest() != sha256(INSTALLER):
        return ctx.record("12_downloads", "FAIL", f"the public URL answers {status}")
    return ctx.record("12_downloads", "PASS", "; ".join(results))


def _live_manifest(ctx: Context) -> dict:
    """The manifest the Gateway serves, or {} when it cannot be read.

    A slow TLS handshake used to escape as an unhandled TimeoutError and abort the whole release
    between the manifest backup and the install. The probe retries briefly and then reports
    "unknown", which the steps already handle.
    """
    for attempt in range(3):
        try:
            status, body, _ = ctx.http(
                "GET", f"{GATEWAY}/updates/latest?target=windows&arch=x86_64&current_version=1.2.5",
                headers={"Cache-Control": "no-cache"},
            )
        except Exception:  # noqa: BLE001 - a probe must never end the release
            time.sleep(5 * (attempt + 1))
            continue
        if status != 200:
            return {}
        try:
            return json.loads(body)
        except ValueError:
            return {}
    return {}


def _live_entry(live: dict) -> dict:
    return (live.get("platforms") or {}).get("windows-x86_64") or {}


def _live_is_publishable(live: dict, expected_sha: str) -> bool:
    """The live manifest is exactly what this release would publish, URL included.

    Version and digest alone were not enough: a manifest pointing at an artifact URL that 404s
    looked "already correct" and was skipped, which is how step 14 reported success while the
    updater could not download anything.
    """
    entry = _live_entry(live)
    return (
        live.get("version") == VERSION
        and entry.get("sha256") == expected_sha
        and entry.get("url") == gateway_url()
    )


def manifest_backup(ctx: Context) -> dict:
    live = _live_manifest(ctx)
    expected_sha = sha256(INSTALLER)
    if _live_is_publishable(live, expected_sha):
        return ctx.record("13_manifest_backup", "SKIP", "the live manifest is already 1.2.6",
                          already=True)
    stamp = ctx.now().strftime("%Y%m%dT%H%MZ")
    target = f"{MANIFEST_PATH}.bak-{stamp}"
    result = ctx.ssh(f"test -f '{MANIFEST_PATH}' && cp -p '{MANIFEST_PATH}' '{target}'")
    if not _ok(result):
        return ctx.record("13_manifest_backup", "FAIL", text(result)[-300:])
    return ctx.record("13_manifest_backup", "PASS", f"backed up to {target}")


def manifest(ctx: Context) -> dict:
    live = _live_manifest(ctx)
    expected_sha = sha256(INSTALLER)
    if _live_is_publishable(live, expected_sha):
        return ctx.record("14_manifest", "SKIP", "the live manifest is already correct",
                          already=True)
    pub_date = ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    built = ctx.run(
        [str(PY), str(REPO / "scripts" / "updates-manifest.py"), "build",
         "--installer", str(INSTALLER), "--signature", str(SIGNATURE),
         "--version", VERSION, "--previous", "1.2.5",
         "--base-url", f"{GATEWAY}/downloads/", "--pub-date", pub_date,
         "--url-query", f"v={VERSION}",
         "--notes-file", str(NOTES), "--expect-sha256", expected_sha,
         "--out", str(MANIFEST_LOCAL)]
    )
    if not _ok(built):
        return ctx.record("14_manifest", "FAIL", text(built)[-400:])
    checked = ctx.run([str(PY), str(REPO / "scripts" / "updates-manifest.py"), "check",
                       "--manifest", str(MANIFEST_LOCAL), "--previous", "1.2.5",
                       "--next-version", "1.2.7"])
    if not _ok(checked):
        return ctx.record("14_manifest", "FAIL", text(checked)[-400:])
    staged = f"/tmp/updates-manifest-{VERSION}.json"
    copied = ctx.scp(MANIFEST_LOCAL, staged)
    if not _ok(copied):
        return ctx.record("14_manifest", "FAIL", text(copied)[-300:])
    installed = ctx.ssh(
        f"install -m 640 -o alex-gateway -g alex-gateway '{staged}' '{MANIFEST_PATH}.new'"
        f" && mv '{MANIFEST_PATH}.new' '{MANIFEST_PATH}'"
    )
    if not _ok(installed):
        return ctx.record("14_manifest", "FAIL", text(installed)[-300:])
    for _ in range(6):
        live = _live_manifest(ctx)
        if _live_is_publishable(live, expected_sha):
            return ctx.record("14_manifest", "PASS", f"the Gateway serves {VERSION}")
        time.sleep(10)
    return ctx.record("14_manifest", "FAIL", "the Gateway still serves the previous version")


def updater_matrix(ctx: Context) -> dict:
    signature = SIGNATURE.read_text(encoding="utf-8").strip()
    result = ctx.run(
        [str(PY), str(REPO / "scripts" / "updates-manifest.py"), "live",
         "--endpoint", f"{GATEWAY}/updates/latest",
         "--version", VERSION, "--previous", "1.2.5", "--next-version", "1.2.7",
         "--signature", signature, "--installer-sha256", sha256(INSTALLER),
         "--installer-name", INSTALLER_NAME]
    )
    if not _ok(result):
        return ctx.record("15_updater_matrix", "FAIL", text(result)[-500:])
    failures = []
    for older in ("1.2.3", "1.2.4"):
        status, body, _ = ctx.http(
            "GET", f"{GATEWAY}/updates/latest?target=windows&arch=x86_64&current_version={older}",
            headers={"Cache-Control": "no-cache"},
        )
        if status != 200 or json.loads(body).get("version") != VERSION:
            failures.append(f"{older} -> {status}")
    if failures:
        return ctx.record("15_updater_matrix", "FAIL", ", ".join(failures))
    return ctx.record("15_updater_matrix", "PASS", "1.2.3/1.2.4/1.2.5 -> 1.2.6, 1.2.6 -> 204")


def health(ctx: Context) -> dict:
    status, body, _ = ctx.http("GET", f"{GATEWAY}/health")
    if status != 200:
        return ctx.record("16_health", "FAIL", f"Gateway /health {status}")
    payload = json.loads(body)
    if not payload.get("ready"):
        return ctx.record("16_health", "FAIL", "Gateway not ready")
    no_update, _, _ = ctx.http(
        "GET", f"{GATEWAY}/updates/latest?target=windows&arch=x86_64&current_version={VERSION}"
    )
    if no_update != 204:
        return ctx.record("16_health", "FAIL", f"1.2.6 still sees an update ({no_update})")
    pods_result = ctx.run([str(PY), str(REPO / "artifacts" / "readonly-runpod-state.py")])
    try:
        pods = json.loads(text(pods_result)).get("pods") or []
    except ValueError:
        pods = [{"unreadable": True}]
    if pods:
        return ctx.record("16_health", "FAIL", f"{len(pods)} Pods are still running")
    return ctx.record("16_health", "PASS", "Gateway ready, 1.2.6 has no update, pods []")


def _installed_version() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
            return str(payload.get("version") or payload.get("product_version") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return ""


STEPS = (
    ("01_source", source),
    ("02_developer", developer_gate),
    ("03_candidate", candidate_gate),
    ("04_verify_artifact", verify_artifact),
    ("05_ci", ci_gate),
    ("06_backup", backup),
    ("07_install", install),
    ("08_installed_smoke", installed_smoke),
    ("09_tag", tag),
    ("10_release", release),
    ("11_anonymous_download", anonymous_download),
    ("12_downloads", downloads),
    ("13_manifest_backup", manifest_backup),
    ("14_manifest", manifest),
    ("15_updater_matrix", updater_matrix),
    ("16_health", health),
)


PROGRESS = REPO / "artifacts" / "acceptance-126" / "release-progress.json"

# The steps that *are* a named checkpoint. Recorded as they run, so a resumed release, or a
# post-mortem, can see the same truth the transcript has.
CHECKPOINT_FOR = {
    "04_verify_artifact": "SIGNATURE",
    "05_ci": "FINAL_CI",
    "08_installed_smoke": "INSTALLED_SMOKE",
    "10_release": "GITHUB_RELEASE",
    "14_manifest": "UPDATER",
    "16_health": "POST_RELEASE_VERIFY",
}


def _checkpoint(step: str, entry: dict) -> None:
    name = CHECKPOINT_FOR.get(step)
    if not name:
        return
    try:
        import checkpoints

        checkpoints.record(name, status=entry["status"], evidence=entry.get("detail") or "",
                           next_step="", blocker="" if entry["status"] in {"PASS", "SKIP"}
                           else (entry.get("detail") or "")[:300],
                           extra={"step": step})
    except BaseException:
        pass


def load_progress() -> dict:
    if not PROGRESS.exists():
        return {}
    try:
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def save_progress(ctx: Context) -> None:
    """The steps that *produce* a gate are recorded so the gate can see them on the next plan."""
    progress = load_progress()
    for entry in ctx.log:
        progress[entry["step"]] = entry["status"]
    PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def run_steps(ctx: Context, *, resume_from: str = "", only: str = "") -> int:
    names = [name for name, _ in STEPS]
    if only and only not in names:
        print(f"unknown step {only}; choose from {', '.join(names)}")
        return 2
    start = names.index(resume_from) if resume_from in names else 0
    failures = 0
    for name, function in STEPS[start:]:
        if only and name != only:
            continue
        entry = function(ctx)
        _checkpoint(name, entry)
        if entry["status"] in {"FAIL", "BLOCKED"}:
            failures += 1
            print(f"--- {name} did not pass; later steps are not executed")
            break
    save_progress(ctx)
    return 1 if failures else 0
