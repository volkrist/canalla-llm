import json
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8", errors="replace").read()
start = text.find("{")
# The script prints progress lines before the final document; take the last complete object.
for candidate in (text[text.rfind("\n{"):], text[start:]):
    try:
        data = json.loads(candidate)
        break
    except ValueError:
        continue
else:
    raise SystemExit("no json document found")
print("verdict:", data.get("verdict") or (data.get("t4") or data.get("t3") or {}).get("verdict"))
scope = data.get("t4") or data.get("t3") or data
print("keys:", sorted(scope))
print("checks:", json.dumps(scope.get("checks"), ensure_ascii=False))
print("final_task:", json.dumps(scope.get("final_task"), ensure_ascii=False)[:300])
print("resume_ui:", json.dumps(scope.get("resume_ui"), ensure_ascii=False))
