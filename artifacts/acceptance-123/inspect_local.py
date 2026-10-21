"""Read-only inspection of the installed local database (no writes, no compute)."""

import json
import os
import sqlite3

DB = os.path.join(os.environ["LOCALAPPDATA"], "Alex LLM", "data", "alex.db")


def main() -> int:
    connection = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    print("compute_preferences:")
    for row in connection.execute('select user_id, "values" from compute_preferences'):
        print(" ", row[0], row[1])
    print("tables:")
    names = [
        row[0]
        for row in connection.execute("select name from sqlite_master where type='table' order by name")
    ]
    print(" ", ", ".join(names))
    print("recent tool runs:")
    columns = [row[1] for row in connection.execute("pragma table_info(tool_runs)")]
    print(" columns:", columns)
    if "started_at" in columns:
        order = "started_at"
    elif "created_at" in columns:
        order = "created_at"
    else:
        order = None
    if order:
        for row in connection.execute(
            f"select id, tool_name, origin, status, {order} from tool_runs order by {order} desc limit 12"
        ):
            print("  ", row)
    print("local tasks:")
    task_columns = [row[1] for row in connection.execute("pragma table_info(local_tasks)")]
    print("  columns:", task_columns)
    select = [name for name in ("id", "status", "chat_id", "updated_at") if name in task_columns]
    for row in connection.execute(
        "select " + ", ".join(select) + " from local_tasks order by rowid desc limit 10"
    ):
        print("  ", row)
    print("counts:")
    for table in ("chats", "messages", "tool_runs", "local_tasks"):
        try:
            print(" ", table, connection.execute("select count(*) from " + table).fetchone()[0])
        except sqlite3.Error as error:
            print(" ", table, error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
