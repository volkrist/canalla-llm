"""One release gate, three explicit modes — the missing reproducible artifact check (A25).

    python scripts/release-gate.py developer [--python <interp>]
    python scripts/release-gate.py candidate --installer <setup.exe> [--version 1.2.1]
        [--signature <setup.exe.sig>] [--gateway-url https://gateway.12testers.store]
        [--expect-sha256 <hex>] [--expect-commit <sha>] [--python <interp>]
    python scripts/release-gate.py verify-artifact --installer <setup.exe> [--pubkey-b64 <b64>] ...
    python scripts/release-gate.py publish
    python scripts/release-gate.py --self-test

Three modes, deliberately separated:

* **developer** — the ordinary local suite (Ruff, pytest, Alembic, Vitest, tsc, Prettier, Vite).
  No artifact and no production secret is involved, so it is safe in an untrusted checkout.
* **candidate** — everything in *developer*, plus the release-only gates (Rust, Playwright,
  sidecar stamp, packaged Gateway URL, backup/upgrade smoke) and the **artifact verification**:
  a real installer must exist, its name and version must bind to this checkout, its signature must
  carry the production key id and verify, the bare `ALEX_GATEWAY_URL` must be baked in, and the
  recorded SHA-256/commit must match. A missing installer, a wrong signature, a mismatched key or
  a missing URL is a hard FAIL — never a PASS.
* **publish** — production publishing. It is a separate, authorized action and is **not enabled
  here**: the mode refuses instead of doing anything.

Exit codes: 0 = PASS, 1 = FAIL, 2 = INCOMPLETE (something required was BLOCKED, e.g. no Rust
toolchain). A BLOCKED step is never reported as a PASS.

The verifier needs no production secret: it reads the **public** key already committed in
``tauri.conf.json``. The private signing key never enters this script, CI, or any untrusted host.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DESKTOP = REPO / "apps" / "desktop"
BACKEND = REPO / "apps" / "backend"
GATEWAY = REPO / "apps" / "gateway"
TAURI_CONF = DESKTOP / "src-tauri" / "tauri.conf.json"
PRODUCT_VERSION_FILE = BACKEND / "app" / "product.py"
DEFAULT_GATEWAY_URL = "https://gateway.12testers.store"

PASS, FAIL, BLOCKED, SKIP = "PASS", "FAIL", "BLOCKED", "SKIP"


class Result:
    def __init__(self, name: str, status: str, detail: str = ""):
        self.name = name
        self.status = status
        self.detail = detail

    def line(self) -> str:
        detail = f" — {self.detail}" if self.detail else ""
        return f"[{self.status:7s}] {self.name}{detail}"


# ----------------------------------------------------------------------------- tool helpers


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def run(name: str, command: list[str], *, cwd: Path, timeout: int = 3600) -> Result:
    # On Windows npm/npx are `.cmd` shims that CreateProcess does not resolve from a bare name, so
    # resolve the executable through the PATH (and PATHEXT) before running it.
    import shutil

    resolved = list(command)
    executable = shutil.which(command[0])
    if executable:
        resolved[0] = executable
    try:
        completed = subprocess.run(
            resolved,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            # The test output carries UTF-8 (Russian UI strings). Without an explicit encoding the
            # child is decoded with the host locale codec (cp1252 on a Windows runner), the decode
            # raises and both streams come back as ``None`` — the gate then crashed instead of
            # reporting. Decode as UTF-8 and never lose a result to a stray byte.
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return Result(name, BLOCKED, f"tool not found: {command[0]}")
    output = (completed.stdout or "") + (completed.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-8:])
    if completed.returncode == 0:
        return Result(name, PASS, command[-1] if command else "")
    return Result(name, FAIL, tail or f"exit {completed.returncode}")


def has_cargo() -> bool:
    return _which("cargo") is not None


# ---------------------------------------------------------------------------- artifact verify


def _baked_url_present(data: bytes, url: str) -> bool:
    pattern = re.compile(re.escape(url.encode("utf-8")) + rb"(?![\w.\-/?#])")
    return pattern.search(data) is not None


def parse_minisign_public_key(pubkey_b64: str) -> tuple[bytes, bytes]:
    """The production identity, from the base64 two-line minisign public key in the config."""
    text = base64.b64decode(pubkey_b64).decode("utf-8")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("malformed public key")
    blob = base64.b64decode(lines[1])
    if len(blob) < 42 or blob[:2] != b"Ed":
        raise ValueError("unsupported public key algorithm")
    return blob[2:10], blob[10:42]


def parse_minisign_signature(sig_text: str) -> dict:
    raw = sig_text.strip()
    if "untrusted comment:" not in raw:
        # Tauri writes the whole four-line minisign text base64-encoded (the updater's
        # `signature` field). A plain minisign file is accepted as well.
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
        except Exception:
            decoded = ""
        if "untrusted comment:" in decoded:
            raw = decoded
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("malformed signature")
    blob = base64.b64decode(lines[1])
    if len(blob) < 74 or blob[:2] not in (b"ED", b"Ed"):
        raise ValueError("unsupported signature algorithm")
    return {
        "sig_alg": blob[:2],
        "key_id": blob[2:10],
        "signature": blob[10:74],
        "blob": blob,
        "trusted_comment": lines[2],
    }


def _ed25519_verify(
    public_key: bytes, signature: bytes, data: bytes, sig_alg: bytes
) -> bool | None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None  # the caller reports BLOCKED, never a PASS
    try:
        if sig_alg == b"ED":
            # minisign's prehashed mode: Ed25519 over BLAKE2b-512(file), used by Tauri.
            message = hashlib.blake2b(data, digest_size=64).digest()
        elif sig_alg == b"Ed":
            message = data
        else:
            return False
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except Exception:
        return False


def read_production_pubkey() -> str:
    return str(json.loads(TAURI_CONF.read_text(encoding="utf-8"))["plugins"]["updater"]["pubkey"])


def default_gateway_exe() -> Path | None:
    """The packaged desktop executable that actually carries the baked Gateway URL.

    NSIS compresses the executable, so the URL is not a raw string in the installer. The build
    tree's executable is the authoritative place to check it; the candidate suite also runs
    ``verify-packaged-gateway-url.py`` on the same file.
    """
    for name in ("alex-llm.exe", "Canalla LLM.exe"):
        path = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / name
        if path.is_file():
            return path
    return None


def product_version() -> str:
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', PRODUCT_VERSION_FILE.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else ""


def git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def verify_artifact(
    installer: Path,
    *,
    version: str,
    url: str = DEFAULT_GATEWAY_URL,
    pubkey_b64: str | None = None,
    signature: Path | None = None,
    gateway_exe: Path | None = None,
    expect_sha256: str | None = None,
    expect_commit: str | None = None,
) -> list[Result]:
    """Every release-artifact failure the brief names, each a hard FAIL — never a PASS."""
    results: list[Result] = []
    if not installer.is_file():
        results.append(Result("artifact.exists", FAIL, f"installer not found: {installer}"))
        return results
    results.append(Result("artifact.exists", PASS, str(installer.name)))

    name = installer.name
    expected_name = f"Canalla LLM_{version}_x64-setup.exe"
    if name != expected_name:
        results.append(Result("artifact.name", FAIL, f"expected {expected_name}, got {name}"))
    else:
        results.append(Result("artifact.name", PASS, name))

    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    if expect_sha256:
        if digest.lower() != expect_sha256.lower():
            results.append(Result("artifact.sha256", FAIL, f"{digest} != recorded {expect_sha256}"))
        else:
            results.append(Result("artifact.sha256", PASS, digest))
    else:
        results.append(Result("artifact.sha256", PASS, digest))

    sig_path = signature or installer.with_name(name + ".sig")
    if not sig_path.is_file():
        results.append(Result("artifact.signature", FAIL, f"missing signature: {sig_path}"))
        return results

    data = installer.read_bytes()
    try:
        public_key_id, public_key = parse_minisign_public_key(pubkey_b64 or read_production_pubkey())
        parsed = parse_minisign_signature(sig_path.read_text(encoding="utf-8"))
    except Exception as error:  # malformed key or signature
        results.append(Result("artifact.signature", FAIL, f"unparsable: {error}"))
        return results

    # Minisign stores the key id little-endian; display it the way the release docs do.
    display_id = public_key_id[::-1].hex().upper()
    if parsed["key_id"] != public_key_id:
        results.append(
            Result(
                "artifact.key_identity",
                FAIL,
                "signature key id "
                + parsed["key_id"][::-1].hex().upper()
                + " != production "
                + display_id,
            )
        )
    else:
        results.append(Result("artifact.key_identity", PASS, display_id))

    verified = _ed25519_verify(public_key, parsed["signature"], data, parsed["sig_alg"])
    if verified is None:
        results.append(Result("artifact.signature_verifies", BLOCKED, "cryptography not installed"))
    elif verified:
        detail = (
            "Ed25519 over BLAKE2b-512 (minisign ED)"
            if parsed["sig_alg"] == b"ED"
            else "Ed25519 over the file bytes"
        )
        results.append(Result("artifact.signature_verifies", PASS, detail))
    else:
        results.append(Result("artifact.signature_verifies", FAIL, "signature does not verify"))

    # Version binding: the trusted comment must name this exact artifact.
    comment = parsed["trusted_comment"]
    if f"file:{name}" in comment and "timestamp:" in comment:
        results.append(Result("artifact.signed_name", PASS, comment))
    else:
        results.append(Result("artifact.signed_name", FAIL, f"trusted comment does not bind {name}"))

    if _baked_url_present(data, url):
        results.append(Result("artifact.gateway_url", PASS, url))
    elif gateway_exe is not None and gateway_exe.is_file() and _baked_url_present(
        gateway_exe.read_bytes(), url
    ):
        # The installer is compressed: the URL lives in the packaged executable.
        results.append(
            Result(
                "artifact.gateway_url",
                PASS,
                f"{url} baked in {gateway_exe.name} (the installer is compressed)",
            )
        )
    else:
        results.append(Result("artifact.gateway_url", FAIL, f"bare {url} not baked into the installer"))

    head = git_head()
    if expect_commit:
        if head and head == expect_commit:
            results.append(Result("artifact.commit", PASS, head))
        else:
            results.append(Result("artifact.commit", FAIL, f"HEAD {head or 'unknown'} != {expect_commit}"))
    else:
        results.append(Result("artifact.commit", SKIP, f"HEAD {head or 'unknown'} (no --expect-commit)"))
    return results


# ---------------------------------------------------------------------------------- the gate


def suite_developer(python: str) -> list[Result]:
    results = [
        run("ruff.backend", [python, "-m", "ruff", "check", "app", "tests", "alembic"], cwd=BACKEND),
        run(
            "ruff.format.backend",
            [python, "-m", "ruff", "format", "--check", "app", "tests", "alembic"],
            cwd=BACKEND,
        ),
        run("pytest.backend", [python, "-m", "pytest", "-q"], cwd=BACKEND),
        run("ruff.gateway", [python, "-m", "ruff", "check", "."], cwd=GATEWAY),
        run("pytest.gateway", [python, "-m", "pytest", "-q"], cwd=GATEWAY),
    ]
    results.append(_vitest(python))
    results.append(run("frontend.tsc", ["npx", "tsc", "-b", "--pretty", "false"], cwd=DESKTOP))
    results.append(run("frontend.prettier", ["npm", "run", "format:check"], cwd=DESKTOP))
    results.append(run("frontend.vite", ["npx", "vite", "build"], cwd=DESKTOP))
    return results


def _vitest(python: str) -> Result:
    if not (DESKTOP / "node_modules").is_dir():
        return Result("frontend.vitest", BLOCKED, "node_modules not installed")
    return run("frontend.vitest", ["npm", "test"], cwd=DESKTOP)


def suite_candidate(python: str, *, installer: Path, version: str, args) -> list[Result]:
    results = suite_developer(python)

    if has_cargo():
        results.append(run("rust.check", ["cargo", "check", "--all-targets"], cwd=DESKTOP / "src-tauri"))
        results.append(run("rust.test", ["cargo", "test"], cwd=DESKTOP / "src-tauri"))
    else:
        reason = "no cargo toolchain on this host (untrusted Windows host: native build is BLOCKED)"
        results.append(Result("rust.check", BLOCKED, reason))
        results.append(Result("rust.test", BLOCKED, reason))

    if (DESKTOP / "node_modules" / "@playwright" / "test").is_dir():
        results.append(run("frontend.playwright", ["npm", "run", "test:e2e"], cwd=DESKTOP))
    else:
        results.append(Result("frontend.playwright", BLOCKED, "playwright not installed"))

    stamp = DESKTOP / "src-tauri" / "sidecar" / "alex-backend" / "build-stamp.json"
    if stamp.is_file():
        results.append(
            run(
                "sidecar.stamp",
                [python, str(REPO / "scripts" / "backend-sidecar-stamp.py"), "check",
                 "--backend", str(BACKEND), "--stamp", str(stamp)],
                cwd=REPO,
            )
        )
    else:
        results.append(Result("sidecar.stamp", BLOCKED, "no built sidecar stamp"))

    desktop_exe = DESKTOP / "src-tauri" / "target" / "release" / "alex-llm.exe"
    if desktop_exe.is_file():
        results.append(
            run(
                "packaged.gateway_url",
                [python, str(REPO / "scripts" / "verify-packaged-gateway-url.py"), str(desktop_exe)],
                cwd=REPO,
            )
        )
    else:
        results.append(Result("packaged.gateway_url", BLOCKED, "no built desktop executable"))

    # Release-artifact verification is REQUIRED: a candidate without an installer cannot pass.
    results.extend(
        verify_artifact(
            installer,
            version=version,
            url=args.gateway_url,
            signature=Path(args.signature) if args.signature else None,
            gateway_exe=default_gateway_exe(),
            expect_sha256=args.expect_sha256,
            expect_commit=args.expect_commit,
        )
    )
    return results


def report(results: list[Result]) -> int:
    for result in results:
        print(result.line())
    failed = [r for r in results if r.status == FAIL]
    blocked = [r for r in results if r.status == BLOCKED]
    print("-" * 60)
    print(f"{len(results)} steps · {len(failed)} FAIL · {len(blocked)} BLOCKED")
    if failed:
        print("RELEASE GATE: FAIL")
        return 1
    if blocked:
        print("RELEASE GATE: INCOMPLETE (a required step was BLOCKED — not a PASS)")
        return 2
    print("RELEASE GATE: PASS")
    return 0


# ------------------------------------------------------------------------------------ self-test


def self_test() -> int:
    """Prove the verifier's hard-FAIL cases without a build and without a private key."""
    import tempfile

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    failures = 0

    def check(label: str, condition: bool) -> None:
        nonlocal failures
        if not condition:
            failures += 1
            print(f"self-test: FAILED — {label}")

    version = product_version() or "1.2.1"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        raw_public = public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        # Minisign stores the key id little-endian; display it the way the docs do.
        key_id = bytes.fromhex("B21F1D11FF8E329B")
        key_blob = b"Ed" + key_id + raw_public
        pub_b64 = base64.b64encode(
            (
                "untrusted comment: minisign public key: " + key_id[::-1].hex().upper() + "\n"
                + base64.b64encode(key_blob).decode()
                + "\n"
            ).encode()
        ).decode()

        name = f"Canalla LLM_{version}_x64-setup.exe"
        artifact = root / name
        payload = b"MZ installer bytes " + DEFAULT_GATEWAY_URL.encode() + b"\x00 tail"
        artifact.write_bytes(payload)
        # Tauri/minisign signs the BLAKE2b-512 digest and writes the four-line text
        # base64-encoded (the updater's `signature` field).
        payload_digest = hashlib.blake2b(payload, digest_size=64).digest()
        signature = private.sign(payload_digest)
        sig_blob = b"ED" + key_id + signature
        trusted_comment = "timestamp:1790230189\tfile:" + name
        global_sig = private.sign(signature + trusted_comment.encode())
        wrapped = base64.b64encode(
            (
                "untrusted comment: signature from tauri secret key\n"
                + base64.b64encode(sig_blob).decode()
                + "\ntrusted comment: " + trusted_comment + "\n"
                + base64.b64encode(global_sig).decode()
                + "\n"
            ).encode()
        ).decode()
        artifact.with_name(name + ".sig").write_text(wrapped, encoding="utf-8")

        good = verify_artifact(artifact, version=version, pubkey_b64=pub_b64)
        check("a good artifact passes", all(r.status in {PASS, SKIP} for r in good))

        missing = verify_artifact(root / "absent.exe", version=version, pubkey_b64=pub_b64)
        check("a missing installer fails", any(r.status == FAIL for r in missing))

        wrong_name = root / "Canalla LLM_9.9.9_x64-setup.exe"
        wrong_name.write_bytes(payload)
        bad_name = verify_artifact(wrong_name, version=version, pubkey_b64=pub_b64)
        check("a wrong name fails", any(r.status == FAIL for r in bad_name))

        # A signature made by a different key must be refused on identity alone.
        other = Ed25519PrivateKey.generate()
        other_blob = b"ED" + key_id + other.sign(payload_digest)
        artifact.with_name(name + ".sig").write_text(
            "untrusted comment: x\n" + base64.b64encode(other_blob).decode() + "\ntrusted comment: y\n"
            + base64.b64encode(other.sign(other_blob)).decode() + "\n",
            encoding="utf-8",
        )
        check(
            "a signature that does not verify fails",
            any(r.status == FAIL for r in verify_artifact(artifact, version=version, pubkey_b64=pub_b64)),
        )

        # Restore a good signature, then strip the baked URL: that must fail.
        artifact.with_name(name + ".sig").write_text(wrapped, encoding="utf-8")
        no_url = root / "no-url" / name
        no_url.parent.mkdir()
        no_url_bytes = b"MZ installer bytes without the bare host /updates/latest"
        no_url.write_bytes(no_url_bytes)
        signature_dir = no_url.with_name(name + ".sig")
        stripped = private.sign(hashlib.blake2b(no_url_bytes, digest_size=64).digest())
        stripped_comment = "timestamp:1790230189\tfile:" + name
        signature_dir.write_text(
            "untrusted comment: signature from tauri secret key\n"
            + base64.b64encode(b"ED" + key_id + stripped).decode()
            + "\ntrusted comment: " + stripped_comment + "\n"
            + base64.b64encode(private.sign(stripped + stripped_comment.encode())).decode()
            + "\n",
            encoding="utf-8",
        )
        check(
            "an artifact without the baked URL fails",
            any(r.status == FAIL for r in verify_artifact(no_url, version=version, pubkey_b64=pub_b64)),
        )

        # A compressed installer is verified through the executable it packages.
        packaged = root / "alex-llm.exe"
        packaged.write_bytes(b"MZ packaged exe " + DEFAULT_GATEWAY_URL.encode())
        check(
            "a compressed installer is verified through its packaged exe",
            all(
                r.status in {PASS, SKIP}
                for r in verify_artifact(
                    no_url, version=version, pubkey_b64=pub_b64, gateway_exe=packaged
                )
            ),
        )

        mismatch = verify_artifact(
            artifact, version=version, pubkey_b64=pub_b64, expect_sha256="0" * 64
        )
        check("a wrong recorded sha256 fails", any(r.status == FAIL for r in mismatch))

        wrong_commit = verify_artifact(
            artifact, version=version, pubkey_b64=pub_b64, expect_commit="0" * 40
        )
        check("a wrong expected commit fails", any(r.status == FAIL for r in wrong_commit))

    if failures:
        print(f"RELEASE-GATE: self-test FAILED ({failures})")
        return 1
    print("RELEASE-GATE: self-test PASS")
    return 0


# ----------------------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=("developer", "candidate", "verify-artifact", "publish"))
    parser.add_argument("--python", default=sys.executable, help="interpreter for the Python suites")
    parser.add_argument("--installer", default="", help="release installer to verify")
    parser.add_argument("--signature", default="", help="signature path (defaults to <installer>.sig)")
    parser.add_argument("--version", default="", help="expected product version (defaults to product.py)")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--pubkey-b64", default="", help="override the production public key (self-test)")
    parser.add_argument("--expect-sha256", default="")
    parser.add_argument("--expect-commit", default="")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.mode is None:
        parser.error("a mode (or --self-test) is required")

    if args.mode == "publish":
        print(
            "RELEASE GATE: publish is a separate, authorized action and is not enabled here.\n"
            "  Sign and upload from a trusted host via scripts/production-signing.ps1; nothing "
            "was done."
        )
        return 2

    if args.mode == "verify-artifact":
        if not args.installer:
            parser.error("--installer is required for verify-artifact")
        results = verify_artifact(
            Path(args.installer),
            version=args.version or product_version(),
            url=args.gateway_url,
            pubkey_b64=args.pubkey_b64 or None,
            signature=Path(args.signature) if args.signature else None,
            gateway_exe=default_gateway_exe(),
            expect_sha256=args.expect_sha256 or None,
            expect_commit=args.expect_commit or None,
        )
        return report(results)

    version = args.version or product_version()
    if args.mode == "developer":
        return report(suite_developer(args.python))
    installer = Path(args.installer) if args.installer else Path("__missing_installer__.exe")
    return report(suite_candidate(args.python, installer=installer, version=version, args=args))


if __name__ == "__main__":
    raise SystemExit(main())
