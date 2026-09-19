"""In-process host that executes allowed workspace jobs for autonomous tests."""

import hashlib
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.database import SessionLocal
from app.tools.models import ToolRun


class FakeHost:
    def __init__(self, client, headers, root: Path):
        self.client = client
        self.headers = headers
        self.root = Path(root)
        self.stop = threading.Event()
        self.handled = []
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _loop(self):
        while not self.stop.wait(0.05):
            jobs = self.client.get("/tools/devices/jobs", headers=self.headers).json()
            for job in jobs:
                self._handle(job)

    def _handle(self, job):
        name = job["tool_name"]
        args = job.get("host_args") or {}
        digest = job["input_digest"]
        self.handled.append(name)
        try:
            payload = self._execute(name, args)
        except Exception as error:
            payload = {
                "status": "failed",
                "text": str(error)[:500],
                "stdout": "",
                "stderr": str(error)[:500],
                "metadata": {"error": str(error)[:80]},
            }
        self.client.post(
            f"/tools/runs/{job['id']}/host-result",
            headers=self.headers,
            json={
                "digest": digest,
                "status": payload.get("status", "completed"),
                "exit_code": payload.get("exit_code"),
                "text": payload.get("text", "")[:20000],
                "stdout": payload.get("stdout", "")[:20000],
                "stderr": payload.get("stderr", "")[:20000],
                "metadata": payload.get("metadata") or {},
            },
        )

    def _execute(self, name, args):
        if name == "list_directory":
            path = Path(args["path"])
            names = "\n".join(sorted(item.name for item in path.iterdir()))
            return {"text": names, "stdout": names, "exit_code": 0, "metadata": {}}
        if name == "read_file":
            path = Path(args["path"])
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="replace")
            return {
                "text": f"sha256={digest}\n{text}",
                "stdout": text,
                "exit_code": 0,
                "metadata": {"before_sha256": digest, "sha256": digest},
            }
        if name == "write_file":
            path = Path(args["path"])
            before = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            expected = args.get("expected_before_sha256")
            if expected and before and expected != before:
                return {
                    "status": "failed",
                    "text": "conflict",
                    "stderr": "conflict",
                    "metadata": {"error": "conflict", "conflict": True, "before_sha256": before},
                }
            path.write_bytes((args.get("content") or "").encode("utf-8"))
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "text": "ok",
                "exit_code": 0,
                "metadata": {
                    "before_sha256": before,
                    "after_sha256": after,
                    "files_changed": 1,
                    "path": str(path),
                },
            }
        if name == "patch_file":
            path = Path(args["path"])
            raw = path.read_bytes()
            before = hashlib.sha256(raw).hexdigest()
            if args.get("expected_before_sha256") != before:
                return {
                    "status": "failed",
                    "text": "conflict",
                    "stderr": "conflict",
                    "metadata": {"error": "conflict", "conflict": True, "before_sha256": before},
                }
            text = raw.decode("utf-8")
            old, new = args.get("old_text") or "", args.get("new_text") or ""
            if old not in text:
                return {
                    "status": "failed",
                    "text": "conflict",
                    "stderr": "conflict",
                    "metadata": {"error": "conflict", "conflict": True, "before_sha256": before},
                }
            path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "text": "patched",
                "exit_code": 0,
                "metadata": {
                    "before_sha256": before,
                    "after_sha256": after,
                    "files_changed": 1,
                    "path": str(path),
                },
            }
        if name == "run_python":
            cwd = args.get("cwd") or str(self.root)
            result = subprocess.run(
                [sys.executable, *list(args.get("argv") or [])],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=int(args.get("timeout_seconds") or 30),
                check=False,
            )
            text = (result.stdout or "") + (result.stderr or "")
            return {
                "text": text,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "exit_code": result.returncode,
                "metadata": {"exit_code": result.returncode, "cwd": cwd},
            }
        if name in {"git_status", "git_diff", "git_log"}:
            cwd = args.get("cwd") or str(self.root)
            argv = ["git", name.split("_", 1)[1]]
            if name == "git_log":
                argv += ["-n", "5"]
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
            return {
                "text": result.stdout or result.stderr,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "exit_code": result.returncode,
                "metadata": {
                    "exit_code": result.returncode,
                    "cwd": cwd,
                    "dirty": bool(result.stdout.strip()),
                },
            }
        return {"text": "ok", "exit_code": 0, "metadata": {}}


def wait_digest(client, run_id, user_id=None):
    for _ in range(40):
        with SessionLocal() as db:
            row = db.get(ToolRun, run_id)
            if row:
                return row.input_digest
        time.sleep(0.05)
    raise AssertionError("digest missing")
