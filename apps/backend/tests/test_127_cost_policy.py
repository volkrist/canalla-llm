"""The paid-run arithmetic, checked against the numbers the plan actually uses.

The 1.2.7 planning documents used to state "480 s at $1.09/h = $0.1453" as a worst case while also
claiming a $0.15 cap. That sentence is arithmetically true and operationally false: it ignores the
termination grace and billing rounding, which are exactly the seconds that turn a compliant run
into an over-budget one. These tests pin the conservative derivation.
"""

from __future__ import annotations

import math

from app.compute import cost_policy


def test_the_live_budget_plan_is_conservative_and_feasible():
    plan = cost_policy.plan(0.15, 1.09)
    # 0.15 / (1.09/3600) = 495.4 s, rounded down to the billing minute.
    assert plan.max_billed_seconds == 480
    # The pipeline may use 450 s; the remaining 30 s are the termination grace.
    assert plan.hard_wall_seconds == 450
    assert plan.stop_request_seconds == 440
    assert plan.worst_case_usd <= plan.budget_usd
    assert math.isclose(plan.worst_case_usd, 480 / 3600 * 1.09, rel_tol=1e-9)
    assert plan.feasible is True
    assert plan.capacity_timeout_seconds == 60


def test_the_old_claim_is_not_a_ceiling():
    """480 s of *work* plus a 30 s stop would bill 510 s — more than the budget."""
    naive = 480 / 3600 * 1.09
    assert naive < 0.15  # the old sentence
    over = 510 / 3600 * 1.09
    assert over > 0.15  # what could really have been billed
    plan = cost_policy.plan(0.15, 1.09)
    assert plan.hard_wall_seconds + plan.termination_grace_seconds == plan.max_billed_seconds


def test_a_pricier_pod_is_refused_before_creation():
    plan = cost_policy.plan(0.15, 1.09)
    ok, reason = cost_policy.check_rate(plan, 1.29)
    assert ok is False and "exceed" in reason
    ok, _ = cost_policy.check_rate(plan, 1.09)
    assert ok is True


def test_a_budget_that_buys_nothing_is_infeasible():
    plan = cost_policy.plan(0.01, 1.09)
    assert plan.feasible is False
    plan = cost_policy.plan(0.001, 1.09)
    assert plan.max_billed_seconds == 0 and plan.feasible is False


def test_a_lower_rate_gives_more_room_and_never_less():
    cheap = cost_policy.plan(0.15, 0.52)
    pricey = cost_policy.plan(0.15, 1.09)
    assert cheap.max_billed_seconds >= pricey.max_billed_seconds
    assert cheap.worst_case_usd <= cheap.budget_usd


def test_remaining_seconds_never_goes_negative():
    plan = cost_policy.plan(0.15, 1.09)
    assert cost_policy.remaining_seconds(plan, 0) == 450
    assert cost_policy.remaining_seconds(plan, 10_000) == 0
