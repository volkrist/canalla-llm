"""One-shot: list RunPod pods. Never prints the API key. Never deletes volumes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paid import VOLUME_ID
from real_session import env_file_map, list_runpod_pods, running_gpu_count, venv_python
import os
import subprocess


def main(argv=None):
    values = env_file_map()
    pods = list_runpod_pods(values["RUNPOD_API_KEY"])
    rows = [{"id": p.get("id"), "status": p.get("status"), "name": p.get("name")} for p in pods]
    print(json.dumps({"running_gpu": running_gpu_count(pods), "volume": VOLUME_ID, "pods": rows}, ensure_ascii=False))
    if argv and argv[:1] == ["terminate"] and len(argv) == 2:
        pod_id = argv[1]
        python = venv_python()
        script = (
            "import os,sys,httpx\n"
            "pod=sys.argv[1]\n"
            "key=os.environ['RUNPOD_API_KEY']\n"
            "r=httpx.post('https://api.runpod.io/v2/pods/'+pod+'/action', headers={'Authorization':'Bearer '+key}, json={'action':'terminate'}, timeout=30.0)\n"
            "print(r.status_code)\n"
        )
        env = os.environ.copy()
        env["RUNPOD_API_KEY"] = values["RUNPOD_API_KEY"]
        proc = subprocess.run([str(python), "-c", script, pod_id], env=env, capture_output=True, text=True)
        print(proc.stdout.strip())
        if proc.returncode:
            print(proc.stderr[-300:], file=sys.stderr)
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
