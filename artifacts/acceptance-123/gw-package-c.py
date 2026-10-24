"""Read-only Gateway evidence for the Package C sessions (no secrets)."""

import json
import sqlite3

DB = "/var/lib/alex-gateway/gateway.db"
con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row
columns = [row[1] for row in con.execute("pragma table_info(gateway_sessions)")]
print("columns:", columns)
print("== sessions since 2026-10-24 12:30")
for row in con.execute(
    "select * from gateway_sessions where created_at >= '2026-10-24 12:30' order by created_at"
):
    data = dict(row)
    keep = {
        key: data.get(key)
        for key in (
            "id",
            "state",
            "gpu_type",
            "hourly_rate",
            "billable_seconds",
            "estimated_cost",
            "created_at",
            "started_at",
            "ready_at",
            "stopped_at",
            "stop_reason",
            "error_code",
            "datacenter",
            "volume_id",
        )
        if key in data
    }
    print(json.dumps(keep, ensure_ascii=False, default=str))
con.close()
