import json
import os
import sqlite3
from pathlib import Path

db = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM" / "data" / "alex.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cutoff = "2026-11-11 17:40"
rows = con.execute(
    "select id, chat_id, status, title, started_at, updated_at, last_error, current_phase,"
    " current_step, tool_calls_used, completion_summary, facts from local_tasks"
    " where started_at >= ? order by started_at desc limit 4",
    (cutoff,),
).fetchall()
for row in rows:
    print("TASK", row["id"], row["status"], row["started_at"], "->", row["updated_at"])
    print("  title:", (row["title"] or "")[:100])
    print("  phase:", row["current_phase"], "step:", row["current_step"], "calls:", row["tool_calls_used"])
    print("  last_error:", row["last_error"], "| summary:", (row["completion_summary"] or "")[:120])
    facts = json.loads(row["facts"] or "{}")
    for key in ("halt", "task_halt", "waiting", "scope", "last_verified"):
        if key in facts:
            print("   facts", key, json.dumps(facts[key], ensure_ascii=False)[:200])
    runs = con.execute(
        "select tool_name, status, origin, started_at, finished_at, error_code, result_metadata,"
        " confirmed_at, claim_id, exec_started_at from tool_runs where chat_id = ? order by started_at",
        (row["chat_id"],),
    ).fetchall()
    print("  tools:", len(runs))
    for run in runs:
        meta = json.loads(run["result_metadata"] or "{}")
        print(
            "   -", run["tool_name"], run["status"], run["origin"],
            (run["started_at"] or "")[11:19], "->", (run["finished_at"] or "")[11:19],
            "err:", run["error_code"], "confirmed:", run["confirmed_at"],
        )
        for key in ("host_args", "host_result"):
            if key in meta:
                print("      ", key, json.dumps(meta[key], ensure_ascii=False)[:240])
    msgs = con.execute(
        "select role, status, substr(content,1,160) as head, created_at from messages"
        " where chat_id = ? order by created_at",
        (row["chat_id"],),
    ).fetchall()
    for msg in msgs:
        print("  msg", msg["role"], msg["status"], (msg["created_at"] or "")[11:19], "|", (msg["head"] or "").replace("\n", " ")[:110])
    events = con.execute(
        "select created_at, kind, payload from task_events where task_id = ? order by created_at desc limit 14",
        (row["id"],),
    ).fetchall()
    for ev in events:
        print("  event", (ev["created_at"] or "")[11:19], ev["kind"], (ev["payload"] or "")[:150])
con.close()
