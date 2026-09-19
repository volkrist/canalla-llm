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
        self.jobs = {}

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        for proc in list(self.jobs.values()):
            if proc and proc.poll() is None:
                proc.kill()
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
            payload = self._execute(name, args, job["id"])
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

    def _execute(self, name, args, run_id=""):
        if name == "list_directory":
            path = Path(args["path"])
            names = "\n".join(sorted(item.name for item in path.iterdir()))
            return {"text": names, "stdout": names, "exit_code": 0, "metadata": {}}
        if name == "hash_file":
            path = Path(args["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "text": digest,
                "stdout": digest,
                "exit_code": 0,
                "metadata": {"digest": digest, "sha256": digest, "path": str(path)},
            }
        if name == "read_file":
            path = Path(args["path"])
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="replace")
            return {
                "text": f"sha256={digest}\n{text}",
                "stdout": text,
                "exit_code": 0,
                "metadata": {"before_sha256": digest, "sha256": digest, "path": str(path)},
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
        if name == "create_directory":
            path = Path(args["path"])
            path.mkdir(parents=True, exist_ok=True)
            return {"text": "ok", "exit_code": 0, "metadata": {"path": str(path), "files_changed": 1}}
        if name == "copy_file":
            source, destination = Path(args["source"]), Path(args["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
            after = hashlib.sha256(destination.read_bytes()).hexdigest()
            return {
                "text": "ok",
                "exit_code": 0,
                "metadata": {"path": str(destination), "after_sha256": after, "files_changed": 1},
            }
        if name == "move_file":
            source, destination = Path(args["source"]), Path(args["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            after = hashlib.sha256(destination.read_bytes()).hexdigest()
            return {
                "text": "ok",
                "exit_code": 0,
                "metadata": {"path": str(destination), "after_sha256": after, "files_changed": 1},
            }
        if name == "search_files":
            root = Path(args["root"])
            query = args.get("query") or ""
            hits = []
            for item in root.rglob("*"):
                if item.is_file() and query.lower() in item.name.lower():
                    hits.append(item.name)
            text = "\n".join(hits)
            return {"text": text, "stdout": text, "exit_code": 0, "metadata": {}}
        if name == "search_code":
            root = Path(args["root"])
            query = args.get("query") or ""
            hits = []
            searched = 0
            for item in root.rglob("*"):
                if item.is_file():
                    searched += 1
                    try:
                        body = item.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    if query in body:
                        for index, line in enumerate(body.splitlines(), 1):
                            if query in line:
                                hits.append(f"{item}:{index}:{line.strip()}")
                                break
            if not hits:
                text = (
                    f"tool=search_code status=no_match scope={root} query={query} "
                    f"searched_files={searched} recommended_next_action=inspect_files_or_reconsider_query"
                )
            else:
                text = "\n".join(hits)
            return {"text": text, "stdout": text, "exit_code": 0, "metadata": {}}
        if name == "get_known_folders":
            desktop = Path.home() / "Desktop"
            documents = Path.home() / "Documents"
            downloads = Path.home() / "Downloads"
            text = f"desktop={desktop}\ndocuments={documents}\ndownloads={downloads}"
            return {
                "text": text,
                "stdout": text,
                "exit_code": 0,
                "metadata": {
                    "desktop": str(desktop),
                    "documents": str(documents),
                    "downloads": str(downloads),
                },
            }
        if name == "get_system_info":
            text = (
                "platform=windows\nos_version=10.0\ncpu_logical_processors=8\n"
                "ram_total_mb=16000\nram_avail_mb=8000\nsystem_disk_free_gb=100"
            )
            return {
                "text": text,
                "stdout": text,
                "exit_code": 0,
                "metadata": {
                    "os_version": "10.0",
                    "cpu_logical_processors": "8",
                    "ram_total_mb": "16000",
                    "system_disk_free_gb": "100",
                },
            }
        if name == "run_python":
            cwd = args.get("cwd") or str(self.root)
            argv = [sys.executable, *list(args.get("argv") or [])]
            if args.get("wait") is False:
                proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.jobs[run_id] = proc
                return {
                    "text": f"started pid={proc.pid}",
                    "exit_code": None,
                    "metadata": {
                        "pid": proc.pid,
                        "status": "running",
                        "started_by_alex": True,
                        "tool_run_id": run_id,
                    },
                }
            result = subprocess.run(
                argv,
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
        if name == "process_status":
            key = args.get("tool_run_id")
            proc = self.jobs.get(key)
            running = bool(proc and proc.poll() is None)
            text = "running" if running else "unknown"
            return {
                "text": text,
                "exit_code": 0,
                "metadata": {"status": text, "pid": getattr(proc, "pid", None)},
            }
        if name == "stop_process":
            key = args.get("tool_run_id")
            proc = self.jobs.pop(key, None)
            pid = getattr(proc, "pid", None)
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            dead = proc is None or proc.poll() is not None
            return {
                "text": "stopped",
                "exit_code": 0,
                "metadata": {"pid": pid, "verified_dead": bool(dead)},
            }
        if name in {"git_status", "git_diff", "git_log", "git_add", "git_commit", "git_push"}:
            cwd = args.get("cwd") or str(self.root)
            if name == "git_add":
                paths = list(args.get("paths") or [])
                if any(item in {"-A", "-a", "--all", ".", "*"} for item in paths):
                    return {
                        "status": "failed",
                        "text": "invalid_arguments",
                        "stderr": "invalid_arguments",
                        "metadata": {"error": "invalid_arguments"},
                    }
                argv = ["git", "add", "--", *paths]
            elif name == "git_commit":
                argv = ["git", "commit", "-m", args.get("message") or "update", "--no-gpg-sign"]
            elif name == "git_push":
                argv = ["git", "push", args.get("remote") or "origin"]
                if args.get("branch"):
                    argv.append(args["branch"])
            else:
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
