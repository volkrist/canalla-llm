"""Real auth regression: an expired signed token must not look like a lost device credential.

Mints HS256 tokens with the local backend's own secret and calls the real heartbeat endpoint:
* expired token + correct device credential -> 401 whose detail is NOT `device_auth`;
* valid token + wrong device credential      -> 401 whose detail IS `device_auth`.

Only statuses and short details are printed; no token or credential is written anywhere.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "artifacts" / "acceptance-123"))
import common  # noqa: E402

ROOT = Path(os.environ["LOCALAPPDATA"]) / "Alex LLM"
BASE = "http://127.0.0.1:8000"


def secret() -> str:
    return (ROOT / "runtime" / "jwt.secret").read_text(encoding="utf-8").strip()


def user_id() -> str:
    db = sqlite3.connect(ROOT / "data" / "alex.db")
    try:
        return db.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()[0]
    finally:
        db.close()


def token(sub: str, *, minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "iat": now - timedelta(minutes=abs(minutes) + 5),
            "exp": now + timedelta(minutes=minutes),
            "iss": "alex-llm",
            "aud": "alex-desktop",
            "jti": "device-auth-check",
        },
        secret(),
        algorithm="HS256",
    )


def heartbeat(bearer: str, device_id: str, credential: str) -> tuple[int, str]:
    request = urllib.request.Request(
        BASE + "/tools/devices/heartbeat",
        method="POST",
        data=b"",
    )
    request.add_header("Authorization", "Bearer " + bearer)
    request.add_header("X-Alex-Device-Id", device_id)
    request.add_header("X-Alex-Device-Credential", credential)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    record = json.loads((ROOT / "device.json").read_text(encoding="utf-8"))
    device_id = record["device_id"]
    credential = common.read_secret("Alex LLM/device-credential")
    valid = token(user_id(), minutes=60)
    expired = token(user_id(), minutes=-60)

    status_expired, body_expired = heartbeat(expired, device_id, credential)
    status_wrong, body_wrong = heartbeat(valid, device_id, "not-the-device-credential")
    status_ok, body_ok = heartbeat(valid, device_id, credential)

    print(json.dumps(
        {
            "expired_correct_credential": {"status": status_expired, "detail": body_expired[:160]},
            "valid_wrong_credential": {"status": status_wrong, "detail": body_wrong[:160]},
            "valid_correct_credential": {"status": status_ok},
        },
        ensure_ascii=False,
        indent=1,
    ))

    expired_is_not_device_auth = status_expired == 401 and "device_auth" not in body_expired
    wrong_is_device_auth = status_wrong == 401 and "device_auth" in body_wrong
    healthy = status_ok == 200
    print(
        "RESULT",
        "PASS" if (expired_is_not_device_auth and wrong_is_device_auth and healthy) else "FAIL",
    )
    return 0 if (expired_is_not_device_auth and wrong_is_device_auth and healthy) else 1


if __name__ == "__main__":
    raise SystemExit(main())
