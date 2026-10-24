"""Free proof that write_file keeps a body byte-for-byte (no model, no GPU)."""

import hashlib
import json
import os

import common
from preflight import execute_tool

BODY = '"""Module docstring."""\nvalue = 1\n'
TOKEN = common.token()
ROOT = str(common.SANDBOX / "PROBE")
os.makedirs(ROOT, exist_ok=True)
TARGET = os.path.join(ROOT, "quoted_module.py")

status, text = common.call("POST", "/chats", token=TOKEN, body={"title": "write quotes probe"})
chat = json.loads(text)["id"]
try:
    write = execute_tool(
        TOKEN,
        chat,
        "write_file",
        {"path": TARGET, "content": BODY, "purpose": "free quote probe"},
    )
    read = execute_tool(TOKEN, chat, "read_file", {"path": TARGET, "purpose": "verify"})
    print("write error:", json.dumps(write.get("error"), ensure_ascii=False))
    print("write result:", json.dumps((write.get("result") or {}).get("metadata"), ensure_ascii=False)[:400])
    on_disk = open(TARGET, "rb").read()
    print("disk:", repr(on_disk))
    print("expected sha:", hashlib.sha256(BODY.encode("utf-8")).hexdigest())
    print("disk sha:    ", hashlib.sha256(on_disk).hexdigest())
    print("MATCH", on_disk == BODY.encode("utf-8"))
    print("read text:", repr(((read.get("result") or {}).get("metadata") or {}).get("text"))[:200])
finally:
    common.call("DELETE", "/chats/" + chat, token=TOKEN)
