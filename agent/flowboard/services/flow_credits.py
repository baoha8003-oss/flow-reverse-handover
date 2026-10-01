"""The last credit balance Flow told us, and how long ago it said so.

Five RPCs hand the balance back for free in slot 1 of their reply — `eb1hJf`,
`MZZa6b`, `fZytfe`, `p0UkFb` and, most usefully, `jwpduf`, the poll that runs
every few seconds while anything is rendering. So the number stays fresh while
the user works without a single extra call, and `nzlxg` exists for when nothing
is running.

**Why the age travels with it.** A cached balance shown as if it were current is
the same failure as quoting a guessed price: the user reads "989" and decides to
run a 60-credit board, not knowing the 989 predates three renders. So this never
answers with a bare number — `last_known()` returns the value AND its age, and
the caller decides whether that age is good enough for what it is about to say.
`estimate` quotes it; the over-budget warning only fires on a fresh one.

**Why the allowlist is explicit.** Slot 1 is the balance on those five replies
and something else entirely on others. Reading it from every reply would put a
wrong number on the meter, and a wrong balance is worse than no balance — it is
the one number a user makes spending decisions from.
"""
from __future__ import annotations

import time
from typing import Any, Optional

#: The RPCs whose reply carries the balance in slot 1, from the capture that
#: recorded them (spsocial/PD-Auto-Footage, 2026-09-03). `jwpduf` is the poll,
#: which is why this stays current for free during a render.
BALANCE_BEARING_RPCS: frozenset[str] = frozenset({
    "eb1hJf",   # image-to-video submit
    "MZZa6b",   # reference-to-video submit
    "fZytfe",   # extend submit
    "p0UkFb",   # upscale submit
    "jwpduf",   # operation poll
})

#: Older than this and the number is quoted with its age rather than compared
#: against a price. Ten minutes is long enough to survive a render and short
#: enough that it cannot span a whole working session.
FRESH_FOR_S = 600.0

_credits: Optional[int] = None
_seen_at: Optional[float] = None


def remember(credits: Optional[int]) -> None:
    """Record a balance Flow just reported. `None` is ignored, not stored."""
    global _credits, _seen_at
    if isinstance(credits, bool) or not isinstance(credits, int):
        return
    if credits < 0:
        return
    _credits = credits
    _seen_at = time.time()


def observe(rpcid: str, payload: Any) -> Optional[int]:
    """Pick the balance out of a reply, but only from an RPC known to carry one.

    Returns what it stored, so a caller can log that it happened without having
    to re-derive it. Anything unexpected is dropped silently: this is a
    side-channel observation, and it must never turn a good dispatch into an
    error.
    """
    if rpcid not in BALANCE_BEARING_RPCS:
        return None
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    value = payload[1]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    remember(value)
    return value


def last_known() -> tuple[Optional[int], Optional[float]]:
    """`(credits, age_in_seconds)`, or `(None, None)` when nothing is known."""
    if _credits is None or _seen_at is None:
        return None, None
    return _credits, max(0.0, time.time() - _seen_at)


def fresh() -> Optional[int]:
    """The balance only if it is recent enough to compare against a price."""
    credits, age = last_known()
    if credits is None or age is None or age > FRESH_FOR_S:
        return None
    return credits


def spent_since(before: Optional[int]) -> Optional[int]:
    """Credits spent between a recorded balance and now, or None if unmeasurable.

    This is how a price nobody has published gets measured without guessing:
    read the balance, run the thing once, read again. Both reads are free — the
    poll carries the balance, and `nzlxg` exists for when nothing is running.

    `None` rather than `0` whenever the answer cannot be trusted:

    * either read missing or stale — a delta against a ten-minute-old number is
      not a price, and quoting it as one is worse than admitting ignorance;
    * the balance went UP (a top-up, or a refund landing mid-render). That is a
      real event and it is not this operation's price.

    **The caveat that cannot be coded away:** the delta covers everything the
    account spent in the window, so it is only a price when nothing else was
    rendering. A caller reporting it has to say so.
    """
    after = fresh()
    if before is None or after is None:
        return None
    delta = before - after
    return delta if delta >= 0 else None


def forget() -> None:
    """Drop the cache. For tests, and for a sign-out."""
    global _credits, _seen_at
    _credits = None
    _seen_at = None
