"""Free static check of the T3/T4 runner before a Pod is paid for.

A live T4 harness referenced a name it never bound (`task_at_start`), so the run that finally
produced real model work would have crashed while building its evidence — the exact "Qwen ran and
we do not know what happened" failure. `pyflakes`-class analysis catches that class of defect in
under a second, so it runs before any paid session rather than after one.

Rules checked: F821 (undefined name), F811 (redefinition), F823 (use before assignment).
Run: python artifacts/acceptance-126/harness-check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BACKEND = REPO / "apps" / "backend"
RUFF = BACKEND / ".venv" / "Scripts" / "python.exe"
FILES = [
    REPO / "artifacts" / "acceptance-123" / "live_restart.py",
    REPO / "artifacts" / "acceptance-123" / "runner.py",
    REPO / "artifacts" / "acceptance-123" / "common.py",
    REPO / "artifacts" / "acceptance-123" / "catalog.py",
    REPO / "artifacts" / "acceptance-126" / "coordinator.py",
    REPO / "artifacts" / "acceptance-126" / "release_coordinator.py",
    REPO / "artifacts" / "acceptance-126" / "release_steps.py",
    REPO / "artifacts" / "acceptance-126" / "free_restart_selftest.py",
]


def main() -> int:
    interpreter = RUFF if RUFF.is_file() else Path(sys.executable)
    command = [str(interpreter), "-m", "ruff", "check", "--select", "F821,F811,F823",
               "--no-cache", *[str(path) for path in FILES]]
    result = subprocess.run(command, capture_output=True, timeout=300)
    output = (result.stdout or b"").decode("utf-8", "replace")
    findings = [line for line in output.splitlines() if line.strip().startswith("F8")]
    verdict = "PASS" if result.returncode == 0 else "FAIL"
    print(json.dumps({"verdict": verdict, "files": len(FILES), "findings": findings[:10],
                      "detail": output.strip()[-600:] if verdict == "FAIL" else ""},
                     ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
