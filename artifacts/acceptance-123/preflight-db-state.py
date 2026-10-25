"""Read-only pre-flight: no stuck tasks, and a persisted final answer exists."""

import os
import sqlite3

ROOT = os.path.join(os.environ["LOCALAPPDATA"], "Alex LLM", "data", "alex.db")
path = ROOT.replace(os.sep, "/")
db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)

terminal = ("COMPLETED", "FAILED", "STOPPED")
rows = list(
    db.execute(
        "select id, status, substr(title, 1, 60) from local_tasks order by started_at desc"
    )
)
active = [row for row in rows if row[1] not in terminal]
print("tasks", len(rows), "active_or_waiting", len(active))
for row in active[:10]:
    print("  ACTIVE", row)

print("latest assistant answers with T1 markers:")
for chat_id, title in db.execute(
    "select id, title from chats where title like 'acceptance T1%' order by updated_at desc limit 3"
):
    message = db.execute(
        "select content from messages where chat_id=? and role='assistant' order by created_at desc limit 1",
        (chat_id,),
    ).fetchone()
    body = (message[0] if message else "") or ""
    found = [marker for marker in ("T1_A", "T1_B", "T1_C") if marker in body]
    print(" ", title, "markers", found, "chars", len(body))

print("latest H1 assistant answers:")
for chat_id, title in db.execute(
    "select id, title from chats where title like 'acceptance H1%' order by updated_at desc limit 2"
):
    message = db.execute(
        "select content from messages where chat_id=? and role='assistant' order by created_at desc limit 1",
        (chat_id,),
    ).fetchone()
    body = (message[0] if message else "") or ""
    print(" ", title, "H1_CLI_OK" in body, "chars", len(body), repr(body[:120]))
