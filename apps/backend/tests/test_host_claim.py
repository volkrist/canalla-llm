"""A05 — durable claim/lease, result delivery and acknowledgement for local jobs.

The failure these tests exist for: the Computer ran a command, the result POST was lost,
and the next poll handed back the *same* job so the side effect ran again. The protocol
must make that impossible without promising exactly-once for an arbitrary shell.

A small host simulator stands in for the Tauri host. It keeps a *durable* journal, exactly
as the native host does, so a "lost POST" and a "host restart" are reproduced honestly:
the side effect is written to a real file, and the journal is what decides whether the
command may run a second time.
"""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from app.database import SessionLocal
from app.models import Message
from app.tools.executor import ExecutionContext, ToolExecutor, reconcile_tools
from app.tools.models import ToolRun
from app.tools.policy import ToolLimits
from tests.db_helpers import require_row


def _host_run(user_id, chat_id, *, status="waiting_host", device_id=None, digest="d" * 64, **extra):
    with SessionLocal() as db:
        row = ToolRun(
            user_id=user_id,
            chat_id=chat_id,
            tool_name="run_python",
            provider="local_device",
            risk_level="NORMAL_CHANGE",
            status=status,
            input_digest=digest,
            input_summary={},
            result_metadata={"host_args": {"argv": ["-c", "print(1)"]}},
            assigned_device_id=device_id,
            **extra,
        )
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture
def account(client, auth):
    headers = auth()
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
    with SessionLocal() as db:
        message = Message(chat_id=chat_id, role="assistant", content="")
        db.add(message)
        db.commit()
        generation_id = message.id
    client.put(
        "/tools/preferences",
        headers=headers,
        json={"computer_mode": "trusted", "workspace_roots": [r"C:\AlexWorkspace"]},
    )
    paired = client.post(
        "/tools/devices/pair", headers=headers, json={"display_name": "A05-PC", "platform": "windows"}
    ).json()
    device_headers = {
        **headers,
        "X-Alex-Device-Id": paired["device_id"],
        "X-Alex-Device-Credential": paired["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=device_headers)
    return {
        "headers": headers,
        "user_id": user_id,
        "chat_id": chat_id,
        "generation_id": generation_id,
        "device_id": paired["device_id"],
        "device_headers": device_headers,
    }


class Journal:
    """The native host's durable record, mirrored for the test."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value
        self.path.write_text(json.dumps(self.data))

    def drop(self, key):
        self.data.pop(key, None)
        self.path.write_text(json.dumps(self.data))


class HostSim:
    def __init__(self, client, device_headers, journal_path: Path, log_path: Path):
        self.client = client
        self.headers = device_headers
        self.journal = Journal(journal_path)
        self.log_path = log_path
        self.fail_next_result = False
        self.fail_next_ack = False
        self.executions = 0
        self.acked: set[str] = set()

    def poll(self):
        return self.client.get("/tools/devices/jobs", headers=self.headers).json()

    def work_once(self) -> int:
        """One poll cycle. Returns the number of jobs handled."""
        handled = 0
        for job in self.poll():
            run_id = job["id"]
            claim_id = job.get("claim_id") or ""
            entry = self.journal.get(run_id)
            if entry and entry["phase"] == "done":
                # Never execute again: only re-deliver the stored result.
                self._deliver(job, entry["result"])
                handled += 1
                continue
            if entry and entry["phase"] == "started":
                # The effect may already have run: decline, do not replay.
                self._report_unknown(job)
                handled += 1
                continue
            self.client.post(
                f"/tools/runs/{run_id}/host-started",
                headers=self.headers,
                json={"claim_id": claim_id},
            )
            self.journal.put(run_id, {"phase": "started", "claim_id": claim_id})
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write("one-line\n")
            self.executions += 1
            result = {
                "digest": job["input_digest"] if "input_digest" in job else _digest_of(job),
                "claim_id": claim_id,
                "status": "completed",
                "exit_code": 0,
                "stdout": "line-appended",
                "stderr": "",
                "text": "line-appended",
                "metadata": {},
            }
            self.journal.put(run_id, {"phase": "done", "claim_id": claim_id, "result": result})
            self._deliver(job, result)
            handled += 1
        return handled

    def _deliver(self, job, result):
        run_id = job["id"]
        if self.fail_next_result:
            self.fail_next_result = False
            return  # the POST is lost; the journal keeps the result
        response = self.client.post(f"/tools/runs/{run_id}/host-result", headers=self.headers, json=result)
        if response.status_code == 200:
            if self.fail_next_ack:
                self.fail_next_ack = False
                return  # the ACK is lost; the journal keeps the entry
            ack = self.client.post(
                f"/tools/runs/{run_id}/host-ack",
                headers=self.headers,
                json={"claim_id": result.get("claim_id", "")},
            )
            if ack.status_code == 200:
                self.acked.add(run_id)
                self.journal.drop(run_id)

    def _report_unknown(self, job):
        run_id = job["id"]
        entry = self.journal.get(run_id) or {}
        self.client.post(
            f"/tools/runs/{run_id}/host-result",
            headers=self.headers,
            json={
                "digest": _digest_of(job),
                "claim_id": entry.get("claim_id", ""),
                "status": "unknown",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "text": "",
                "metadata": {},
            },
        )


def _digest_of(job):
    with SessionLocal() as db:
        return require_row(db, ToolRun, job["id"]).input_digest


def _context(account):
    async def emit(_event, _value):
        return None

    return ExecutionContext(
        account["user_id"],
        account["chat_id"],
        account["generation_id"],
        ToolLimits(),
        emit,
        mode="off",
        computer_mode="trusted",
    )


# --------------------------------------------------------------------------------------
# The mandatory acceptance: a lost result POST must not run the command twice.
# --------------------------------------------------------------------------------------


def test_lost_result_post_never_repeats_the_side_effect(account, client, tmp_path):
    log = tmp_path / "effect.log"
    sim = HostSim(client, account["device_headers"], tmp_path / "journal.json", log)
    sim.fail_next_result = True  # the very first delivery attempt is lost

    stop = threading.Event()

    def host_loop():
        deadline = time.time() + 6
        while time.time() < deadline and not stop.is_set():
            sim.work_once()
            time.sleep(0.02)

    worker = threading.Thread(target=host_loop, daemon=True)
    worker.start()
    try:
        result = asyncio.run(
            ToolExecutor(client.app.state.tools).execute(
                "run_python", {"argv": ["-c", "print(1)"]}, _context(account)
            )
        )
    finally:
        stop.set()
        worker.join(timeout=3)

    assert "line-appended" in result.text
    # Exactly one side effect: the first POST was lost, the poll repeated, nothing re-ran.
    assert log.read_text(encoding="utf-8").count("one-line") == 1
    assert sim.executions == 1
    runs = client.get("/tools/runs", headers=account["headers"]).json()
    assert runs and runs[0]["error_code"] is None


def test_lost_ack_redelivers_without_a_second_execution(account, client, tmp_path):
    log = tmp_path / "effect.log"
    journal_path = tmp_path / "journal.json"
    sim = HostSim(client, account["device_headers"], journal_path, log)
    sim.fail_next_ack = True  # result stored, the ACK response is lost

    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    assert sim.work_once() == 1
    assert log.read_text(encoding="utf-8").count("one-line") == 1
    # The result is stored, the journal is still there: the device that "restarted" polls again.
    restarted = HostSim(client, account["device_headers"], journal_path, log)
    assert restarted.work_once() == 1
    assert log.read_text(encoding="utf-8").count("one-line") == 1  # still exactly one
    assert restarted.executions == 0  # delivered from the journal, never re-run
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "acknowledged"
        assert row.acknowledged_at is not None


def test_host_crash_after_effect_becomes_unknown_not_a_replay(account, client, tmp_path):
    log = tmp_path / "effect.log"
    journal_path = tmp_path / "journal.json"
    sim = HostSim(client, account["device_headers"], journal_path, log)
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])

    # Claim and start, but the process dies before the result is written.
    jobs = sim.poll()
    job = jobs[0]
    client.post(
        f"/tools/runs/{run_id}/host-started",
        headers=account["device_headers"],
        json={"claim_id": job["claim_id"]},
    )
    sim.journal.put(run_id, {"phase": "started", "claim_id": job["claim_id"]})
    with log.open("a", encoding="utf-8") as handle:
        handle.write("one-line\n")

    # A fresh host process sees the started-but-unfinished entry and declines.
    restarted = HostSim(client, account["device_headers"], journal_path, log)
    assert restarted.work_once() == 1
    assert restarted.executions == 0  # never replayed
    assert log.read_text(encoding="utf-8").count("one-line") == 1
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "unknown"
        assert row.error_code == "host_result_unknown"
    # Unknown is preserved across a backend restart and never listed again.
    reconcile_tools()
    with SessionLocal() as db:
        assert require_row(db, ToolRun, run_id).status == "unknown"
    assert sim.poll() == []


# --------------------------------------------------------------------------------------
# Claim, lease and idempotency.
# --------------------------------------------------------------------------------------


def test_a_poll_does_not_hand_out_a_second_claim(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    first = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()
    second = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()
    assert [job["id"] for job in first] == [run_id]
    assert first[0]["claim_id"] == second[0]["claim_id"]
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "claimed"
        assert row.attempt_count == 1
        assert row.claim_owner == account["device_id"]


def test_host_started_freezes_the_lease(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    job = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()[0]
    started = client.post(
        f"/tools/runs/{run_id}/host-started",
        headers=account["device_headers"],
        json={"claim_id": job["claim_id"]},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "started"
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "started"
        assert row.exec_started_at is not None


def test_another_device_never_receives_this_devices_claim(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    client.get("/tools/devices/jobs", headers=account["device_headers"])
    second = client.post(
        "/tools/devices/pair",
        headers=account["headers"],
        json={"display_name": "second", "platform": "windows"},
    ).json()
    other_headers = {
        **account["headers"],
        "X-Alex-Device-Id": second["device_id"],
        "X-Alex-Device-Credential": second["credential"],
    }
    client.post("/tools/devices/heartbeat", headers=other_headers)
    assert client.get("/tools/devices/jobs", headers=other_headers).json() == []
    with SessionLocal() as db:
        assert require_row(db, ToolRun, run_id).claim_owner == account["device_id"]


def test_an_expired_unstarted_claim_is_released_and_reclaimed(account, client):
    from datetime import timedelta, timezone

    from app.models import now

    past = now() - timedelta(seconds=1)
    run_id = _host_run(
        account["user_id"],
        account["chat_id"],
        device_id=account["device_id"],
        status="claimed",
        claim_id="old-claim",
        claim_owner=account["device_id"],
        claimed_at=past - timedelta(seconds=600),
        claim_expires_at=past,
        attempt_count=1,
    )
    jobs = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()
    assert [job["id"] for job in jobs] == [run_id]
    assert jobs[0]["claim_id"] != "old-claim"
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.status == "claimed"
        assert row.attempt_count == 2
        assert row.claim_expires_at.replace(tzinfo=timezone.utc) > now()


def test_a_started_expired_claim_is_never_released(account, client):
    from datetime import timedelta

    from app.models import now

    run_id = _host_run(
        account["user_id"],
        account["chat_id"],
        device_id=account["device_id"],
        status="started",
        claim_id="started-claim",
        claim_owner=account["device_id"],
        claimed_at=now() - timedelta(seconds=900),
        claim_expires_at=now() - timedelta(seconds=600),
        exec_started_at=now() - timedelta(seconds=599),
        attempt_count=1,
    )
    jobs = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()
    assert [job["id"] for job in jobs] == [run_id]
    assert jobs[0]["claim_id"] == "started-claim"
    with SessionLocal() as db:
        assert require_row(db, ToolRun, run_id).attempt_count == 1


def test_result_delivery_is_idempotent_and_a_different_result_conflicts(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    job = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()[0]
    body = {
        "digest": job["input_digest"],
        "claim_id": job["claim_id"],
        "status": "completed",
        "exit_code": 0,
        "stdout": "done",
        "stderr": "",
        "text": "done",
        "metadata": {},
    }
    first = client.post(f"/tools/runs/{run_id}/host-result", headers=account["device_headers"], json=body)
    assert first.status_code == 200 and first.json()["status"] == "result_awaiting_ack"
    # The same result again (a lost response being retried) is accepted, not a conflict.
    again = client.post(f"/tools/runs/{run_id}/host-result", headers=account["device_headers"], json=body)
    assert again.status_code == 200
    with SessionLocal() as db:
        assert require_row(db, ToolRun, run_id).status == "result_awaiting_ack"
    # A *different* result cannot be smuggled in behind the first.
    different = {**body, "text": "tampered", "stdout": "tampered"}
    conflict = client.post(
        f"/tools/runs/{run_id}/host-result", headers=account["device_headers"], json=different
    )
    assert conflict.status_code == 409
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.result_metadata["host_result"]["text"] == "done"


def test_claim_and_digest_mismatches_are_refused(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    job = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()[0]
    base = {
        "digest": job["input_digest"],
        "claim_id": job["claim_id"],
        "status": "completed",
        "stdout": "x",
        "stderr": "",
        "text": "x",
        "metadata": {},
    }
    assert (
        client.post(
            f"/tools/runs/{run_id}/host-result",
            headers=account["device_headers"],
            json={**base, "claim_id": "not-the-claim"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/tools/runs/{run_id}/host-result",
            headers=account["device_headers"],
            json={**base, "digest": "0" * 64},
        ).status_code
        == 409
    )


def test_acknowledged_job_is_no_longer_offered(account, client):
    run_id = _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    job = client.get("/tools/devices/jobs", headers=account["device_headers"]).json()[0]
    client.post(
        f"/tools/runs/{run_id}/host-result",
        headers=account["device_headers"],
        json={
            "digest": job["input_digest"],
            "claim_id": job["claim_id"],
            "status": "completed",
            "stdout": "x",
            "stderr": "",
            "text": "x",
            "metadata": {},
        },
    )
    ack = client.post(
        f"/tools/runs/{run_id}/host-ack",
        headers=account["device_headers"],
        json={"claim_id": job["claim_id"]},
    )
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert client.get("/tools/devices/jobs", headers=account["device_headers"]).json() == []
    with SessionLocal() as db:
        assert require_row(db, ToolRun, run_id).acknowledged_at is not None


def test_cancelled_job_is_not_claimed(account, client):
    run_id = _host_run(
        account["user_id"], account["chat_id"], device_id=account["device_id"], status="stopped"
    )
    assert client.get("/tools/devices/jobs", headers=account["device_headers"]).json() == []
    with SessionLocal() as db:
        row = require_row(db, ToolRun, run_id)
        assert row.claim_id is None  # a cancelled job is never leased


# --------------------------------------------------------------------------------------
# Restart, isolation and no-forgery.
# --------------------------------------------------------------------------------------


def test_backend_restart_preserves_in_flight_host_jobs(account, client):
    from datetime import timedelta

    from app.models import now

    preserved = [
        _host_run(account["user_id"], account["chat_id"], status="claimed", claim_id="c1"),
        _host_run(account["user_id"], account["chat_id"], status="started", claim_id="c2"),
        _host_run(account["user_id"], account["chat_id"], status="result_awaiting_ack", claim_id="c3"),
        _host_run(account["user_id"], account["chat_id"], status="unknown", claim_id="c4"),
    ]
    collateral = _host_run(account["user_id"], account["chat_id"], status="running")
    reconcile_tools()
    with SessionLocal() as db:
        for run_id in preserved:
            assert require_row(db, ToolRun, run_id).status != "failed"
        failed = require_row(db, ToolRun, collateral)
        assert failed.status == "failed"
        assert failed.error_code == "backend_interrupted"
    assert now() is not None and timedelta(seconds=1)


def test_multiple_users_do_not_see_each_others_jobs(client, auth):
    def pairing(email):
        headers = auth(email)
        user_id = client.get("/auth/me", headers=headers).json()["id"]
        chat_id = client.post("/chats", headers=headers, json={}).json()["id"]
        paired = client.post(
            "/tools/devices/pair", headers=headers, json={"display_name": email, "platform": "windows"}
        ).json()
        device_headers = {
            **headers,
            "X-Alex-Device-Id": paired["device_id"],
            "X-Alex-Device-Credential": paired["credential"],
        }
        client.post("/tools/devices/heartbeat", headers=device_headers)
        return headers, user_id, chat_id, device_headers

    _h1, user1, chat1, dev1 = pairing("claim-a@example.com")
    _h2, _user2, _chat2, dev2 = pairing("claim-b@example.com")
    mine = _host_run(user1, chat1, device_id=dev1["X-Alex-Device-Id"])
    assert [job["id"] for job in client.get("/tools/devices/jobs", headers=dev1).json()] == [mine]
    assert client.get("/tools/devices/jobs", headers=dev2).json() == []


def test_a_user_jwt_without_the_device_cannot_deliver_a_result(account, client):
    _host_run(account["user_id"], account["chat_id"], device_id=account["device_id"])
    response = client.post(
        "/tools/runs/00000000-0000-0000-0000-000000000001/host-result",
        headers=account["headers"],
        json={"digest": "0" * 64, "status": "completed", "metadata": {}},
    )
    assert response.status_code == 401
