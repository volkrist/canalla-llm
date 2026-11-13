"""Read-only inspection of the paired-device rows (temporary incident check)."""

import os
import sqlite3

path = os.path.join(os.environ["LOCALAPPDATA"], "Alex LLM", "data", "alex.db")
conn = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)
for row in conn.execute(
    "select id, user_id, display_name, created_at, last_seen, revoked_at from paired_devices "
    "order by created_at desc limit 6"
):
    print(row)
print("---")
for row in conn.execute(
    "select device_id from local_tasks order by started_at desc limit 5"
):
    print("task device", row[0])
conn.close()
