"""Read-only Gateway evidence for the third Pod's startup_timeout (no secrets)."""

import json
import sqlite3

SID = "70257548-2d6e-4b40-9e0d-fb90fd24ff85"
DB = "/var/lib/alex-gateway/gateway.db"
con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("== session")
for row in con.execute("select * from gateway_sessions where id=?", (SID,)):
    print(json.dumps({k: str(v)[:300] for k, v in dict(row).items() if v is not None}, ensure_ascii=False))

print("== audit events for the session")
for row in con.execute(
    "select created_at, operation, result, error_code, detail, cost from audit_events "
    "where compute_session_id=? or detail like ? order by rowid",
    (SID, "%" + SID + "%"),
):
    print(
        row["created_at"],
        "|",
        row["operation"],
        "|",
        row["result"],
        "|",
        row["error_code"],
        "|",
        (row["detail"] or "")[:300],
        "|",
        (row["cost"] or "")[:300],
    )

print("== gateway_operations for the session")
for row in con.execute("select * from gateway_operations order by rowid desc limit 12"):
    data = dict(row)
    if SID in json.dumps(data):
        print(json.dumps({k: str(v)[:1500] for k, v in data.items()}, ensure_ascii=False))
con.close()
