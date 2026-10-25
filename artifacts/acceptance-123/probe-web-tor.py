"""Free verification of Web and Tor through the real installed backend (no model, no GPU)."""

import json

import common
from preflight import execute_tool

TOKEN = common.token()
status, text = common.call("POST", "/chats", token=TOKEN, body={"title": "web/tor probe"})
if status not in (200, 201):
    raise SystemExit(f"chat_create_failed {status} {text[:200]}")
chat = json.loads(text)["id"]
try:
    web = execute_tool(TOKEN, chat, "web_fetch", {"urls": ["https://example.com"], "fresh": True})
    web_blob = json.dumps(web.get("result") or {}, ensure_ascii=False)
    print("web error:", web.get("error"))
    print("web has Example Domain:", "Example Domain" in web_blob)
    print("web sources:", len((web.get("result") or {}).get("sources") or []))

    tor = execute_tool(TOKEN, chat, "tor_fetch", {"urls": ["https://example.com"], "fresh": True})
    tor_blob = json.dumps(tor.get("result") or {}, ensure_ascii=False)
    print("tor error:", tor.get("error"))
    print("tor has Example Domain:", "Example Domain" in tor_blob)
    print("tor sources:", len((tor.get("result") or {}).get("sources") or []))

    mem_status, mem_text = common.call("GET", "/memory", token=TOKEN)
    print("memory:", mem_status, len(mem_text), "bytes")
finally:
    common.call("DELETE", "/chats/" + chat, token=TOKEN)
