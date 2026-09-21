"""Installation identity: token exchange, claims, revocation, protocol guard, limits."""

from __future__ import annotations

import jwt
from conftest import auth_header, enroll, make_existing_installation, token_for

from gateway.security import issue_token


def test_token_requires_the_installation_secret(gateway, client):
    installation = enroll(gateway, client)
    response = client.post(
        "/auth/token",
        json={"installation_id": installation["installation_id"], "installation_secret": "wrong-" + "w" * 20},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "gateway_auth_failed"


def test_token_for_an_unknown_installation_is_rejected(client):
    response = client.post(
        "/auth/token",
        json={"installation_id": "00000000-0000-0000-0000-000000000000", "installation_secret": "x" * 32},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "installation_unknown"


def test_access_token_is_short_lived_and_installation_scoped(gateway, client):
    installation = enroll(gateway, client)
    response = client.post(
        "/auth/token",
        json={
            "installation_id": installation["installation_id"],
            "installation_secret": installation["installation_secret"],
        },
    )
    body = response.json()
    assert body["expires_in"] == 15 * 60
    claims = jwt.decode(
        body["access_token"],
        gateway.settings.jwt_secret,
        algorithms=["HS256"],
        audience="alex-installation",
        issuer="alex-gateway",
    )
    assert claims["sub"] == installation["installation_id"]
    assert claims["aud"] == "alex-installation"
    assert claims["iss"] == "alex-gateway"
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert claims["jti"]
    second = client.post(
        "/auth/token",
        json={
            "installation_id": installation["installation_id"],
            "installation_secret": installation["installation_secret"],
        },
    ).json()
    assert second["access_token"] != body["access_token"]
    # The secret is never used as a bearer token.
    assert installation["installation_secret"] not in body["access_token"]


def test_business_endpoints_require_a_bearer_token(client):
    assert client.get("/balance").status_code == 401
    assert client.get("/compute/status").status_code == 401
    assert client.get("/v1/models").status_code == 401
    assert client.get("/balance", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_access_token_is_not_accepted_for_another_issuer(gateway, client):
    installation = enroll(gateway, client)
    forged = issue_token(gateway.settings, installation["installation_id"])
    wrong = jwt.encode(
        {
            "iss": "someone-else",
            "aud": "alex-installation",
            "sub": installation["installation_id"],
            "exp": 9999999999,
            "iat": 1,
        },
        gateway.settings.jwt_secret,
        algorithm="HS256",
    )
    assert (
        client.get("/balance", headers={"Authorization": "Bearer " + forged["access_token"]}).status_code
        == 200
    )
    assert client.get("/balance", headers={"Authorization": "Bearer " + wrong}).status_code == 401


def test_client_protocol_header_mismatch_is_rejected(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    response = client.get("/compute/status", headers={**headers, "X-Alex-Protocol-Version": "99"})
    assert response.status_code == 409
    assert response.json()["code"] == "gateway_protocol_mismatch"
    assert (
        client.get("/compute/status", headers={**headers, "X-Alex-Protocol-Version": "1"}).status_code == 200
    )


def test_revoked_installation_loses_every_surface(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    assert client.get("/balance", headers=headers).status_code == 200
    assert client.post("/auth/revoke", json={}, headers=headers).status_code == 200

    # The already-issued token stops working immediately.
    for path in ("/balance", "/compute/status", "/v1/models"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, path
        assert response.json()["code"] == "installation_revoked"
    # And a fresh token cannot be minted.
    response = client.post(
        "/auth/token",
        json={
            "installation_id": installation["installation_id"],
            "installation_secret": installation["installation_secret"],
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "installation_revoked"
    assert (
        client.post("/compute/ensure", json={"operation_id": "op-12345678"}, headers=headers).status_code
        == 403
    )


def test_revocation_does_not_touch_other_installations(gateway, client):
    first = enroll(gateway, client, label="PC A")
    second = enroll(gateway, client, label="PC B")
    first_headers = auth_header(client, first)
    second_headers = auth_header(client, second)
    client.post("/auth/revoke", json={}, headers=first_headers)
    assert client.get("/balance", headers=first_headers).status_code == 403
    assert client.get("/balance", headers=second_headers).status_code == 200


def test_installation_secret_is_not_a_token_for_business_endpoints(gateway, client):
    installation = enroll(gateway, client)
    response = client.get(
        "/balance", headers={"Authorization": "Bearer " + installation["installation_secret"]}
    )
    assert response.status_code == 401


def test_rate_limit_returns_a_stable_code(gateway, client):
    installation = enroll(gateway, client)
    headers = auth_header(client, installation)
    gateway.app.state.limiter.limits["compute"] = 2
    gateway.app.state.limiter.reset()
    assert client.get("/balance", headers=headers).status_code == 200
    assert client.get("/balance", headers=headers).status_code == 200
    response = client.get("/balance", headers=headers)
    assert response.status_code == 429
    assert response.json()["code"] == "gateway_rate_limited"
    gateway.app.state.limiter.limits["compute"] = 1000


def test_second_installation_is_an_independent_principal(gateway, client):
    second = make_existing_installation(gateway, "PC B")
    headers = {"Authorization": "Bearer " + token_for(client, second)}
    assert client.get("/compute/status", headers=headers).status_code == 200
