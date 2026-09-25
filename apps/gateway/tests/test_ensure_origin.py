"""Why an ensure happened is part of the record, not something to infer from timing.

The 1.2.0 acceptance could not answer the only question that mattered — "did the chat request
start the compute, or was that the background retry?" — because `POST /compute/ensure` carried no
origin. Three calls thirty seconds apart were read as a background loop when they were just as
likely the chat's own bounded retries, and no log could settle it.

Every ensure now names its caller, the audit row carries it, and the answer echoes it back so the
client can show which event actually started the work.
"""

from __future__ import annotations

import pytest
from conftest import auth_header, enroll

ORIGINS = ("chat", "manual_prewarm", "background_retry", "startup_auto_connect")


def ensure(client, headers, **body):
    from conftest import ensure_body

    response = client.post("/compute/ensure", json=ensure_body(**body), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def audit(gateway, operation):
    from gateway.models import AuditEvent

    with gateway.sessions() as db:
        return [row for row in db.query(AuditEvent).all() if row.operation == operation]


@pytest.mark.parametrize("origin", ORIGINS)
def test_every_origin_is_accepted_and_recorded(gateway, client, origin):
    headers = auth_header(client, enroll(gateway, client))

    payload = ensure(client, headers, operation_id=f"op-{origin[:8]}-000000", origin=origin)

    assert payload["origin"] == origin
    events = audit(gateway, "ensure")
    assert events, "an ensure must leave an audit row"
    assert events[-1].cost.get("origin") == origin


def test_an_unknown_origin_is_refused_rather_than_recorded_as_something_else(gateway, client):
    """The four names are a closed set: a typo must not become an unlabelled ensure."""
    headers = auth_header(client, enroll(gateway, client))

    response = client.post(
        "/compute/ensure",
        json={"operation_id": "op-badorigin0001", "origin": "chat "},
        headers=headers,
    )

    assert response.status_code == 422


def test_an_old_client_without_an_origin_is_still_served(gateway, client):
    """Nothing about the field may break a client that predates it."""
    headers = auth_header(client, enroll(gateway, client))

    payload = ensure(client, headers, operation_id="op-noorigin00001")

    # No origin claimed, and none invented: the key is simply absent.
    assert "origin" not in payload
    events = audit(gateway, "ensure")
    assert events[-1].cost.get("origin") is None


def test_the_audit_separates_a_chat_from_a_background_retry(gateway, client):
    """The distinction the acceptance needed: two rows, two origins, same installation."""
    headers = auth_header(client, enroll(gateway, client))

    ensure(client, headers, operation_id="op-chatorigin001", origin="chat")
    ensure(client, headers, operation_id="op-background001", origin="background_retry")

    origins = [row.cost.get("origin") for row in audit(gateway, "ensure")]
    assert "chat" in origins
    assert "background_retry" in origins
    # And the chat is not the retry: the point of the field is that these are distinguishable.
    assert origins.index("chat") != origins.index("background_retry")
