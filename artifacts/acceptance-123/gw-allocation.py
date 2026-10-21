"""Read-only: what the Gateway actually saw when the allocation failed (no secrets)."""

import json
import sqlite3

DB = "/var/lib/alex-gateway/gateway.db"
con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def dump_table(name, limit=15, cut=900):
    columns = [row[1] for row in con.execute("pragma table_info(" + name + ")")]
    print("===", name, columns)
    for row in con.execute("select * from " + name + " order by rowid desc limit " + str(limit)):
        data = {
            key: (str(value)[:cut] if value is not None else None)
            for key, value in dict(row).items()
        }
        print(json.dumps(data, ensure_ascii=False))


dump_table("audit_events", 15, 700)
dump_table("gateway_compute", 3, 3000)
dump_table("gateway_operations", 6, 900)
con.close()
