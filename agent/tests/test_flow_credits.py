"""The credit meter: where the number comes from, and when it must not be shown.

The balance is the one number a user makes spending decisions from, so the two
failure modes here are not symmetric. Showing nothing costs a trip to the Flow
tab. Showing a WRONG number — a stale one presented as current, or slot 1 of a
reply that does not carry a balance — spends money on a decision made from bad
data. Every test below is about the second kind.
"""
from __future__ import annotations

import time

import pytest

from flowboard.services import flow_credits


@pytest.fixture(autouse=True)
def _clean():
    flow_credits.forget()
    yield
    flow_credits.forget()


# ── which replies are allowed to move the meter ───────────────────────


@pytest.mark.parametrize("rpcid", sorted(flow_credits.BALANCE_BEARING_RPCS))
def test_a_balance_bearing_reply_updates_the_meter(rpcid):
    assert flow_credits.observe(rpcid, [None, 741, []]) == 741
    assert flow_credits.last_known()[0] == 741


def test_the_poll_is_one_of_them_which_is_why_this_stays_current():
    """`jwpduf` runs every few seconds while anything renders, so the meter keeps
    up during a render without a single extra call."""
    assert "jwpduf" in flow_credits.BALANCE_BEARING_RPCS


@pytest.mark.parametrize("rpcid", ["ogiZ0b", "YhhmEf", "as29s", "maseQ", "C4BZMd"])
def test_a_reply_that_does_not_carry_a_balance_is_ignored(rpcid):
    """Slot 1 means something else on these. Reading it anyway would put a
    number on the meter that is not a balance at all."""
    assert flow_credits.observe(rpcid, [None, 999, []]) is None
    assert flow_credits.last_known()[0] is None


def test_a_non_integer_in_slot_one_is_ignored():
    assert flow_credits.observe("jwpduf", [None, "many", []]) is None
    assert flow_credits.last_known()[0] is None


def test_a_boolean_is_not_a_balance():
    """`True` is an int in Python. It is not 1 credit."""
    assert flow_credits.observe("jwpduf", [None, True, []]) is None
    assert flow_credits.last_known()[0] is None


def test_a_reply_too_short_to_have_a_slot_one_is_ignored():
    assert flow_credits.observe("jwpduf", [None]) is None
    assert flow_credits.observe("jwpduf", "not a list") is None


def test_observing_never_raises_on_a_hostile_shape():
    """This runs inside the dispatch funnel. A side-channel observation must
    never turn a good generate into an error."""
    for payload in (None, 5, {}, [[]], [None, {}, []]):
        assert flow_credits.observe("jwpduf", payload) is None


# ── remembering directly ──────────────────────────────────────────────


def test_remember_stores_a_plain_number():
    flow_credits.remember(989)
    assert flow_credits.last_known()[0] == 989


def test_remember_ignores_none_rather_than_clearing():
    """`nzlxg` returning None means "could not read", not "you have none". It
    must not wipe a number that was true a moment ago."""
    flow_credits.remember(989)
    flow_credits.remember(None)
    assert flow_credits.last_known()[0] == 989


def test_a_negative_balance_is_refused():
    flow_credits.remember(-5)
    assert flow_credits.last_known()[0] is None


def test_zero_is_a_real_balance_and_is_stored():
    """Nought credits is a fact the user needs. Only None means unknown."""
    flow_credits.remember(0)
    assert flow_credits.last_known()[0] == 0


# ── the age, and what it gates ────────────────────────────────────────


def test_nothing_known_answers_none_for_both():
    assert flow_credits.last_known() == (None, None)


def test_a_fresh_balance_comes_with_a_small_age():
    flow_credits.remember(100)
    credits, age = flow_credits.last_known()
    assert credits == 100
    assert age is not None and age < 5


def test_fresh_returns_the_number_while_it_is_recent():
    flow_credits.remember(100)
    assert flow_credits.fresh() == 100


def test_fresh_withholds_a_stale_number(monkeypatch):
    """`fresh()` is what the over-budget warning reads. A stale balance raising a
    red banner is a false alarm, and false alarms teach people to click through
    the real ones."""
    flow_credits.remember(100)
    later = time.time() + flow_credits.FRESH_FOR_S + 1
    monkeypatch.setattr(flow_credits.time, "time", lambda: later)
    assert flow_credits.fresh() is None
    # But it is still reportable, with its age — the dialog says "read N min ago".
    credits, age = flow_credits.last_known()
    assert credits == 100 and age is not None and age > flow_credits.FRESH_FOR_S


def test_forget_clears_both_the_number_and_the_age():
    flow_credits.remember(50)
    flow_credits.forget()
    assert flow_credits.last_known() == (None, None)

# ── measuring a price nobody published ────────────────────────────────


def test_a_price_is_the_drop_between_two_fresh_reads():
    """How 1080p upscale and the extension lanes get priced without guessing:
    read, run once, read again."""
    flow_credits.remember(989)
    before = flow_credits.fresh()
    flow_credits.remember(974)
    assert flow_credits.spent_since(before) == 15


def test_a_run_that_cost_nothing_measures_as_zero_not_unknown():
    """Zero IS an answer here, and it is the answer that would let a lane join
    `ZERO_CREDIT_MODEL_KEYS` — so it must be distinguishable from "no idea"."""
    flow_credits.remember(989)
    before = flow_credits.fresh()
    flow_credits.remember(989)
    assert flow_credits.spent_since(before) == 0


def test_no_starting_balance_means_no_measurement():
    flow_credits.remember(500)
    assert flow_credits.spent_since(None) is None


def test_no_ending_balance_means_no_measurement():
    assert flow_credits.spent_since(989) is None


def test_a_stale_ending_balance_does_not_become_a_price(monkeypatch):
    """A delta against a ten-minute-old number is not a price. Quoting it as one
    is the "stale shown as current" failure this module exists to prevent."""
    flow_credits.remember(989)
    before = flow_credits.fresh()
    later = time.time() + flow_credits.FRESH_FOR_S + 1
    monkeypatch.setattr(flow_credits.time, "time", lambda: later)
    assert flow_credits.spent_since(before) is None


def test_a_balance_that_went_up_is_not_a_price():
    """A top-up or a refund landing mid-render. Real event, not this
    operation's cost — and a negative price would read as a credit."""
    flow_credits.remember(900)
    before = flow_credits.fresh()
    flow_credits.remember(1000)
    assert flow_credits.spent_since(before) is None

