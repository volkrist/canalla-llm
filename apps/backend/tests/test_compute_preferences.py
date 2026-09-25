"""Per-user compute preferences: the product's own money policy, owned by each user.

$0.52/hour and $3.00/session are defaults, not caps: any authenticated user may raise or
lower both for themselves, in either provider mode, without an owner or admin role.
"""

from decimal import Decimal

from app.compute.controller import RunPodController
from app.config import get_settings


def read(client, headers):
    response = client.get("/compute/preferences", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def save(client, headers, **values):
    return client.put("/compute/preferences", headers=headers, json=values)


def money(value):
    return Decimal(str(value))


def test_a_new_user_starts_on_the_cheap_automatic_defaults(client, auth):
    body = read(client, auth())
    assert money(body["max_hourly_price"]) == Decimal("0.52")
    assert money(body["session_budget"]) == Decimal("3.00")
    assert body["min_vram_gb"] == get_settings().runpod_min_vram_gb == 48
    assert body["auto_stop_minutes"] == 10
    assert body["auto_search"] is True  # retry search enabled by default
    assert body["selection"] == "automatic"  # cheapest compatible GPU
    assert body["gpu_id"] is None  # nothing is pinned


def test_two_users_never_overwrite_each_other_policy(client, auth):
    a, b = auth(), auth("bob@example.com")
    assert save(client, a, max_hourly_price=0.52, session_budget=3.00).status_code == 200
    assert save(client, b, max_hourly_price=1.50, session_budget=10.00).status_code == 200
    assert money(read(client, a)["max_hourly_price"]) == Decimal("0.52")
    assert money(read(client, a)["session_budget"]) == Decimal("3.00")
    assert money(read(client, b)["max_hourly_price"]) == Decimal("1.50")
    assert money(read(client, b)["session_budget"]) == Decimal("10.00")
    # Saving again for one user still leaves the other one untouched.
    assert save(client, a, max_hourly_price=0.75, session_budget=4.00).status_code == 200
    assert money(read(client, b)["max_hourly_price"]) == Decimal("1.50")


def test_a_regular_user_owns_their_policy_without_an_admin_role(client, auth):
    """No owner/admin requirement for your own money policy (the lifecycle stays gated)."""
    auth()  # the first account is the local owner
    bob = auth("bob@example.com")
    assert save(client, bob, max_hourly_price=2.00, session_budget=10.00).status_code == 200
    assert money(read(client, bob)["max_hourly_price"]) == Decimal("2.00")


def test_values_above_the_old_product_caps_are_allowed_and_below_are_allowed(client, auth):
    headers = auth()
    for hourly, budget in [("2.00", "10.00"), ("5", "50"), ("0.40", "1"), ("0.52", "3")]:
        response = save(client, headers, max_hourly_price=hourly, session_budget=budget)
        assert response.status_code == 200, response.text
        body = read(client, headers)
        assert money(body["max_hourly_price"]) == Decimal(hourly)
        assert money(body["session_budget"]) == Decimal(budget)


def test_technical_bounds_still_reject_nonsense(client, auth):
    headers = auth()
    for body in [
        {"max_hourly_price": -1, "session_budget": 3},
        {"max_hourly_price": 0, "session_budget": 3},
        {"max_hourly_price": 500, "session_budget": 3},
        {"max_hourly_price": 0.52, "session_budget": -3},
        {"max_hourly_price": 0.52, "session_budget": 5000},
    ]:
        assert save(client, headers, **body).status_code == 422, body


def test_policy_is_editable_in_shared_mode_while_the_lifecycle_is_not(client, auth, monkeypatch):
    """Shared mode owns the Pod, not the user's own limits."""
    headers = auth()
    monkeypatch.setattr(get_settings(), "alex_ai_mode", "shared")
    assert save(client, headers, max_hourly_price=1.20, session_budget=6.00).status_code == 200
    assert money(read(client, headers)["session_budget"]) == Decimal("6.00")
    started = client.post("/compute/start", headers=headers, json={})
    assert started.status_code in {409, 422}  # gateway_managed_compute / malformed body


def test_saved_policy_is_read_back_by_a_fresh_controller(client, auth):
    """Preferences live in the database, so a restart (or a restore) keeps them."""
    headers = auth()
    assert save(client, headers, max_hourly_price=1.25, session_budget=7.50).status_code == 200
    restarted = RunPodController(get_settings())
    with restarted.sessions() as db:
        from app.models import User

        user = db.query(User).first()
        if user is None:
            raise AssertionError("the registered user is missing")
        stored = restarted.preferences(user.id)
    assert stored.max_hourly_price == Decimal("1.25")
    assert stored.session_budget == Decimal("7.50")


# ------------------------------------------------------------- the allocation strategy


def test_the_new_user_default_strategy_is_balanced_and_pins_nothing(client, auth):
    """The default has to be the one that prefers real availability over a few cents."""
    body = read(client, auth())
    assert body["strategy"] == "balanced"
    assert body["gpu_id"] is None
    assert body["selection"] == "automatic"


def test_a_user_may_choose_any_of_the_four_strategies(client, auth):
    headers = auth()
    for strategy in ("balanced", "fastest", "cheapest"):
        assert save(client, headers, strategy=strategy).status_code == 200, strategy
        assert read(client, headers)["strategy"] == strategy


def test_a_strategy_outside_the_closed_set_is_refused(client, auth):
    assert save(client, auth(), strategy="turbo").status_code == 422


def test_the_manual_strategy_needs_a_card_but_is_otherwise_accepted(client, auth):
    """Refused with an explanation rather than silently treated as automatic."""
    headers = auth()
    assert save(client, headers, strategy="manual").status_code == 422
    assert read(client, headers)["strategy"] == "balanced"  # the refusal changed nothing

    accepted = save(client, headers, strategy="manual", selection="manual", gpu_id="NVIDIA L40S")
    assert accepted.status_code == 200, accepted.text
    body = read(client, headers)
    assert (body["strategy"], body["selection"], body["gpu_id"]) == ("manual", "manual", "NVIDIA L40S")
