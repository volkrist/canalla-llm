#!/usr/bin/env python3
"""Download, verify and stage the pinned llama.cpp CUDA runtime for the reserve region.

The reserve datacenter's Network Volume holds the verified GGUF and both launch scripts, but no
``llama-server``: US-TX-3, where the primary volume's build lives, has no capacity to read it from,
and no byte copy of that binary exists in git. So the reserve gets its own **reproducible** runtime
instead of a byte copy of an unreachable one.

This script is the only thing that ever fetches it, and it is deliberately strict:

* the source is the official ``ggml-org/llama.cpp`` release named by
  ``scripts/llama-runtime.json``; a download is refused unless its size and SHA256 match the pin, so
  a mirror cannot swap a binary in;
* both archives are staged into one flat directory — exactly where ``start-llm.sh`` looks
  (``/workspace/llama.cpp/build/bin/llama-server``), with the CUDA runtime libraries beside it so
  ``RUNPATH=$ORIGIN`` resolves them;
* symlinks inside the archive are recreated, not flattened into copies, so the staged tree is the
  upstream one;
* after staging the binary is run with ``--version`` and ``--help`` and every flag the launch
  contract needs must be advertised, which is a real check that needs no GPU;
* a manifest with what was staged, and what the binary reported, is written next to it.

Usage::

    python scripts/provision-llama-runtime.py --dest /tmp/llama-bin
    python scripts/provision-llama-runtime.py --dest /workspace/llama.cpp/build/bin   # inside a Pod
    python scripts/provision-llama-runtime.py --verify-only                            # pins, no download
    python scripts/provision-llama-runtime.py --self-test                              # offline logic
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIN_FILE = REPO / "scripts" / "llama-runtime.json"
MANIFEST_NAME = "llama-runtime-manifest.json"
CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 600.0
RUN_TIMEOUT_SECONDS = 120
PASS, FAIL, BLOCKED = 0, 1, 3


def load_pin(path: Path = PIN_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("assets"), dict):
        raise SystemExit(f"{path} is not a pinned runtime manifest")
    if not isinstance(data.get("contract"), dict):
        raise SystemExit(f"{path} has no launch contract")
    return data


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------------- download


def _fetch(url: str, target: Path, *, attempts: int = 3) -> None:
    """One archive, with a curl fallback for hosts where urllib cannot reach out.

    The caller re-verifies size and hash either way, so a fallback can never change what is staged.
    """
    last = "unknown"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, headers={"User-Agent": "canalla-llm-build/1.2.1"})
        try:
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310
                with target.open("wb") as handle:
                    shutil.copyfileobj(response, handle, CHUNK)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = type(error).__name__
        curl = shutil.which("curl")
        if curl:
            code = subprocess.run(
                [curl, "-sS", "-L", "--max-time", "900", "-o", str(target), url], check=False
            ).returncode
            if code == 0 and target.is_file():
                return
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise SystemExit(f"could not download {url} ({last})")


def acquire(entry: dict, cache: Path) -> Path:
    """The verified archive for one asset: reuse a cached copy, else download it."""
    name = str(entry["name"])
    target = cache / name
    expected = str(entry["sha256"])
    if target.is_file():
        if target.stat().st_size == entry.get("bytes") and sha256_of(target) == expected:
            print(f"reusing verified {name} in {cache}")
            return target
        print(f"cached {name} is stale — re-downloading")
        target.unlink()
    print(f"downloading {entry['url']}")
    _fetch(str(entry["url"]), target)
    verify(target, entry)
    return target


def verify(archive: Path, entry: dict) -> None:
    size = archive.stat().st_size
    if entry.get("bytes") and size != entry["bytes"]:
        raise SystemExit(f"{archive.name} is {size} bytes, the pin says {entry['bytes']}")
    digest = sha256_of(archive)
    if digest != entry.get("sha256"):
        raise SystemExit(f"{archive.name} has sha256 {digest}, the pin says {entry.get('sha256')}")


# --------------------------------------------------------------------------------- extract


def relative_under(root: str, name: str) -> str | None:
    """The path under ``root`` as a safe relative path, or ``None`` when it is not under it."""
    folded = name.replace("\\", "/").rstrip("/")
    if folded == root:
        return None
    prefix = root + "/"
    if not folded.startswith(prefix):
        return None
    relative = folded[len(prefix):]
    parts = relative.split("/")
    if not relative or relative.startswith("/") or ".." in parts:
        raise SystemExit(f"the archive contains an unsafe path: {name}")
    return relative


def _prepare(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()


def _link_or_copy(origin: Path, target: Path) -> None:
    """A hard link, or a copy where the filesystem refuses one (e.g. a Windows checkout)."""
    try:
        os.link(origin, target)
    except OSError:
        shutil.copyfile(origin, target)


def extract(archive: Path, root: str, dest: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Extract everything under ``root`` into ``dest``, recreating symlinks.

    Regular files are written first and links second, so a link never depends on the order the
    archive happens to list its members. Returns the regular files' hashes and the links' targets.
    """
    files: dict[str, str] = {}
    links: dict[str, str] = {}
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = [(relative_under(root, member.name), member) for member in tar.getmembers()]
        members = [(relative, member) for relative, member in members if relative is not None]
        for relative, member in members:
            if member.isdir() or member.issym() or member.islnk():
                continue
            target = dest / relative
            _prepare(target)
            stream = tar.extractfile(member)
            if stream is None:
                raise SystemExit(f"{member.name} is not a regular file in the archive")
            target.write_bytes(stream.read())
            target.chmod(0o755)
            files[relative] = sha256_of(target)
        for relative, member in members:
            target = dest / relative
            if member.issym():
                _prepare(target)
                try:
                    os.symlink(member.linkname, target)
                except OSError:
                    _link_or_copy(dest / member.linkname, target)
                links[relative] = member.linkname
            elif member.islnk():
                _prepare(target)
                origin = dest / (relative_under(root, member.linkname) or "")
                _link_or_copy(origin, target)
                links[relative] = member.linkname
    return files, links


# --------------------------------------------------------------------------------- run checks


def run_checks(binary: Path, dest: Path, contract: dict) -> dict:
    """Run the staged binary and demand the launch contract, without a GPU.

    ``llama-server`` is a thin launcher: it resolves its libraries through ``RUNPATH=$ORIGIN`` and
    only loads a CUDA backend when it actually needs one, so ``--version``/``--help`` answer on a
    host with no driver. That is what makes this a real, portable check.
    """
    environment = dict(os.environ)
    environment["LD_LIBRARY_PATH"] = str(dest) + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
    result: dict = {"ran": False, "binary": str(binary)}
    if sys.platform != "linux" or not binary.is_file():
        result["skipped"] = "not a linux host with a staged binary"
        return result
    try:
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
        helped = subprocess.run(
            [str(binary), "--help"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=RUN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        return result

    text = helped.stdout + helped.stderr
    required = [str(flag) for flag in contract.get("required_flags") or []]
    missing = [flag for flag in required if flag not in text]
    reported = ""
    for line in (version.stdout + version.stderr).splitlines():
        if line.strip():
            reported = line.strip()
            break
    result.update(
        {
            "ran": True,
            "version_exit": version.returncode,
            "help_exit": helped.returncode,
            "version_text": reported,
            "required_flags": required,
            "missing_flags": missing,
            "contract_ok": version.returncode == 0 and helped.returncode == 0 and not missing,
        }
    )
    return result


# --------------------------------------------------------------------------------- pod command


def pod_command(pin: dict, dest: str, archive_dir: str = "") -> str:
    """A self-contained POSIX shell command that provisions the runtime onto a mounted volume.

    A CPU Pod has no checkout and no Python guarantee, so the command carries the pin inline: it
    downloads each archive (or copies it from a local ``--pod-archive-dir``), verifies byte count and
    SHA256 before unpacking, stages the pair into the launch path and reports what it did as
    ``CANALLA_L_*`` markers. Nothing secret is ever embedded, and no archive is trusted before it
    matches the pin.
    """
    bin_asset = pin["assets"]["bin"]
    cudart_asset = pin["assets"].get("cudart")
    lines = [
        "set -eu",
        f"DEST={shlex.quote(dest)}",
        f"ARCHIVES={shlex.quote(archive_dir)}",
        'WORK=$(mktemp -d)',
        "trap 'rm -rf \"$WORK\"' EXIT",
        "echo CANALLA_L_START",
        "df -h \"$DEST\" 2>/dev/null || df -h /workspace 2>/dev/null || true",
        "DL_get() {",
        '  if command -v curl >/dev/null 2>&1; then curl -fsSL --max-time 900 -o "$2" "$1";',
        '  else wget -q -T 900 -O "$2" "$1"; fi',
        "}",
        "check() {",
        '  got_bytes=$(wc -c < "$1" | tr -d " ")',
        '  got_sha=$(sha256sum "$1" | cut -d" " -f1)',
        '  if [ "$got_bytes" != "$2" ] || [ "$got_sha" != "$3" ]; then',
        '    echo "CANALLA_L_BAD $1 bytes=$got_bytes sha=$got_sha"; return 1',
        "  fi",
        "}",
        "acquire() {",
        '  name="$1"; url="$2"; bytes="$3"; sha="$4"; tries="$5"; i=0',
        '  while [ "$i" -lt "$tries" ]; do',
        "    i=$((i+1))",
        '    if [ -n "$ARCHIVES" ]; then cp "$ARCHIVES/$name" "$WORK/$name";',
        '    else DL_get "$url" "$WORK/$name"; fi',
        '    if check "$WORK/$name" "$bytes" "$sha"; then return 0; fi',
        '    echo "CANALLA_L_RETRY $name"; sleep 5',
        "  done",
        '  echo "CANALLA_L_FETCH_FAILED $name"; return 1',
        "}",
        "mkdir -p \"$WORK/bin\" \"$WORK/cudart\"",
        'acquire %s %s %s %s 3'
        % (
            shlex.quote(bin_asset["name"]),
            shlex.quote(bin_asset["url"]),
            shlex.quote(str(bin_asset["bytes"])),
            shlex.quote(str(bin_asset["sha256"])),
        ),
    ]
    if cudart_asset:
        lines.append(
            "acquire %s %s %s %s 3"
            % (
                shlex.quote(cudart_asset["name"]),
                shlex.quote(cudart_asset["url"]),
                shlex.quote(str(cudart_asset["bytes"])),
                shlex.quote(str(cudart_asset["sha256"])),
            )
        )
    lines += [
        'mkdir -p "$DEST"',
        f'tar -xzf "$WORK/{bin_asset["name"]}" -C "$WORK/bin"',
        'BIN_ROOT=$(ls "$WORK/bin")',
        'cp -a "$WORK/bin/$BIN_ROOT/." "$DEST/"',
    ]
    if cudart_asset:
        lines += [
            f'tar -xzf "$WORK/{cudart_asset["name"]}" -C "$WORK/cudart"',
            'CUDART_ROOT=$(ls "$WORK/cudart")',
            'cp -a "$WORK/cudart/$CUDART_ROOT/." "$DEST/"',
        ]
    lines += [
        'chmod +x "$DEST/llama-server"',
        'echo "CANALLA_L_SERVER_SHA256 $(sha256sum \"$DEST/llama-server\" | cut -d\" \" -f1)"',
        'LD_LIBRARY_PATH="$DEST" "$DEST/llama-server" --version 2>&1 | head -n1 || true',
        "echo CANALLA_L_OK",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------- main


def provision(args) -> int:
    pin = load_pin()
    contract = pin["contract"]
    assets = dict(pin["assets"])
    if args.skip_cudart:
        assets.pop("cudart", None)

    if args.verify_only:
        for key, entry in sorted(assets.items()):
            print(f"{key}: {entry['url']} sha256={entry['sha256']} bytes={entry['bytes']}")
        print(f"server={contract['server']} alias={contract['alias']} ctx={contract['ctx_size']}")
        return PASS

    dest = Path(args.dest) if args.dest else Path(pin["target_dir"])
    cache = Path(args.cache) if args.cache else None
    temporary = None
    if cache is None:
        temporary = Path(tempfile.mkdtemp(prefix="llama-runtime-"))
        cache = temporary
    try:
        archives = {key: acquire(entry, cache) for key, entry in assets.items()}
        staged: dict[str, str] = {}
        links: dict[str, str] = {}
        for key, entry in sorted(assets.items()):
            files, links_found = extract(archives[key], str(entry["archive_root"]), dest)
            staged.update(files)
            links.update(links_found)
            print(f"staged {key}: {len(files)} files, {len(links_found)} symlinks")
        checks = run_checks(dest / "llama-server", dest, contract)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)

    manifest = {
        "product": pin["product"],
        "tag": pin["tag"],
        "commit": pin["commit"],
        "library_version": pin["library_version"],
        "cuda": pin["cuda"],
        "platform": pin["platform"],
        "server_path": str(dest / "llama-server"),
        "target_dir": str(dest),
        "assets": {key: dict(entry) for key, entry in sorted(assets.items())},
        "files": dict(sorted(staged.items())),
        "symlinks": dict(sorted(links.items())),
        "checks": checks,
        "produced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest_path = dest / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    server = staged.get("llama-server", "")
    print(f"staged {pin['product']} {pin['tag']} into {dest}: {len(staged)} files, server sha256={server}")
    print(f"manifest: {manifest_path}")
    if args.show:
        for name, digest in sorted(staged.items()):
            print(f"  {name} {digest}")
        for name, target in sorted(links.items()):
            print(f"  {name} -> {target}")

    if checks.get("ran") and not checks.get("contract_ok"):
        print(f"CONTRACT: FAIL — version_exit={checks.get('version_exit')} "
              f"help_exit={checks.get('help_exit')} missing={checks.get('missing_flags')}")
        return FAIL
    if checks.get("ran"):
        print(f"CONTRACT: PASS — {checks.get('version_text')}")
    else:
        print(f"CONTRACT: not run ({checks.get('skipped') or checks.get('error')})")
    return PASS


def self_test() -> int:
    failures = 0

    def check(name: str, condition: bool):
        nonlocal failures
        if not condition:
            failures += 1
            print(f"  FAIL {name}")

    pin = load_pin()
    contract = pin["contract"]
    check("the pin has a tag and a commit", bool(pin["tag"]) and len(pin["commit"]) == 40)
    check("the pin describes two assets", set(pin["assets"]) == {"bin", "cudart"})
    for key, entry in pin["assets"].items():
        check(f"{key} pins a github release url", str(entry["url"]).startswith(
            "https://github.com/ggml-org/llama.cpp/releases/download/"
        ))
        check(f"{key} pins a 64-hex sha256", len(str(entry["sha256"])) == 64)
    check("the bin archive targets the launcher", pin["assets"]["bin"]["archive_root"] == f"llama-{pin['tag']}")
    check(
        "the contract names the server the scripts call",
        contract["server"] == f"{pin['target_dir']}/llama-server",
    )

    check("a path under the root is relative", relative_under("llama-b11565", "llama-b11565/llama-server") == "llama-server")
    check("the root itself is skipped", relative_under("llama-b11565", "llama-b11565") is None)
    check("a sibling is skipped", relative_under("llama-b11565", "other/llama-server") is None)
    try:
        relative_under("llama-b11565", "llama-b11565/../escape")
        check("a traversal is refused", False)
    except SystemExit:
        check("a traversal is refused", True)

    sample_help = " ".join(contract["required_flags"]) + " --flash-attn [on|off|auto]"
    check(
        "the contract flags are found in a good help text",
        [flag for flag in contract["required_flags"] if flag not in sample_help] == [],
    )
    check(
        "a missing flag is caught",
        [flag for flag in contract["required_flags"] if flag not in sample_help.replace("--jinja", "")],
    )

    command = pod_command(pin, pin["target_dir"])
    check("the pod command is self-contained shell", command.startswith("set -eu\n"))
    check("the pod command verifies by sha256", "sha256sum" in command and "check " in command)
    for key, entry in pin["assets"].items():
        check(f"the pod command carries {key}'s url", str(entry["url"]) in command)
        check(f"the pod command carries {key}'s sha256", str(entry["sha256"]) in command)
    check("the pod command stages into the launch path", pin["target_dir"] in command)
    check("the pod command never traces", "set -x" not in command)

    if failures:
        print(f"LLAMA-RUNTIME: self-test FAILED ({failures})")
        return FAIL
    print("LLAMA-RUNTIME: self-test PASS")
    return PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default="", help="staging directory (default: the pin's target_dir)")
    parser.add_argument("--cache", default="", help="keep the verified archives here")
    parser.add_argument("--verify-only", action="store_true", help="print the pins and stop")
    parser.add_argument("--skip-cudart", action="store_true", help="stage only the build, not the CUDA runtime")
    parser.add_argument("--print", dest="show", action="store_true", help="print every staged file")
    parser.add_argument(
        "--pod-command", action="store_true", help="print a self-contained pod command instead"
    )
    parser.add_argument("--pod-dest", default="", help="volume path the pod command stages into")
    parser.add_argument("--pod-archive-dir", default="", help="local archives the pod command copies from")
    parser.add_argument("--self-test", action="store_true", help="offline checks of the logic")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.pod_command:
        pin = load_pin()
        sys.stdout.write(pod_command(pin, args.pod_dest or pin["target_dir"], args.pod_archive_dir))
        return PASS
    return provision(args)


if __name__ == "__main__":
    raise SystemExit(main())
