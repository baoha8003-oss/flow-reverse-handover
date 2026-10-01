"""Retry classification, the credit-safety gate, backoff, the captcha
allowance, and the 403 breaker. These protect the user's single real Google
account and their credits, so each rule is pinned explicitly.
"""
from __future__ import annotations

import asyncio

import pytest

from flowboard.db.models import Request
from flowboard.db.session import get_session
from flowboard.worker.processor import (
    BREAKER_PROBE_INTERVAL_S,
    CAPTCHA_MAX_ATTEMPTS,
    FREE_MAX_RETRIES,
    MAX_CUMULATIVE_403,
    WorkerController,
    classify_error,
)


# ── pure classification ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "err,expected",
    [
        ("API_403", "403"),
        ("some captcha challenge", "captcha"),
        ("solve the reCAPTCHA", "captcha"),
        ("extension not connected", "free"),
        ("extension disconnected/reconnected", "free"),
        ("API_401", "free"),
        ("PUBLIC_ERROR_UNSAFE_GENERATION", "terminal"),
        ("PUBLIC_ERROR_AUDIO_FILTERED", "terminal"),
        ("missing_prompt", "terminal"),
        ("invalid_project_id", "terminal"),
        ("url_not_allowed", "terminal"),
        ("paygate_tier_unknown", "terminal"),
        ("unknown_request_type:foo", "terminal"),
        ("API_429", "counted"),
        ("API_503", "counted"),
        ("sync_failed: boom", "counted"),
        # The sentinel the SDK actually emits. The old spelling here
        # ("no_operations_returned") was a phantom assertion — nothing in the
        # system ever produced that string, so the rule it pinned was dead.
        ("no_operations_in_response", "counted"),
        ("some unrecognised thing", "terminal"),  # unknown → terminal, never re-charge
    ],
)
def test_classify_error(err, expected):
    assert classify_error(err) == expected


# ── retry decisions via _apply_failure (no event loop needed) ─────────────
def _req(client, **over):
    row = client.post(
        "/api/requests", json={"type": "proxy", "params": {}}
    ).json()
    with get_session() as s:
        r = s.get(Request, row["id"])
        for k, v in over.items():
            setattr(r, k, v)
        s.add(r)
        s.commit()
        s.refresh(r)
        s.expunge(r)
    return r


def test_counted_error_requeues_and_burns_attempt(client):
    w = WorkerController()
    r = _req(client, attempt=0, max_attempts=3)
    w._apply_failure(r, "API_429", {})
    assert r.status == "queued"
    assert r.attempt == 1
    assert r.next_attempt_at is not None
    assert r.finished_at is None


def test_free_error_requeues_without_burning_attempt(client):
    w = WorkerController()
    r = _req(client, attempt=0)
    w._apply_failure(r, "extension not connected", {})
    assert r.status == "queued"
    assert r.attempt == 0  # infra hiccup — not the request's fault


def test_operation_already_created_is_never_retried(client):
    """The credit-safety gate: an error carrying operation_names means the
    op is rendering (and charged); re-dispatch would charge again."""
    w = WorkerController()
    r = _req(client, attempt=0)
    w._apply_failure(r, "API_503", {"operation_names": ["op/123"]})
    assert r.status == "failed"
    assert r.attempt == 0


def test_timeout_keeps_its_status_and_is_not_retried(client):
    w = WorkerController()
    r = _req(client, attempt=0)
    # timeout results always carry operation_names (the op was created).
    w._apply_failure(r, "timeout_waiting_video", {"operation_names": ["op/1"]})
    assert r.status == "timeout"


def test_terminal_error_fails_immediately(client):
    w = WorkerController()
    r = _req(client, attempt=0)
    w._apply_failure(r, "PUBLIC_ERROR_UNSAFE_GENERATION", {})
    assert r.status == "failed"
    assert r.attempt == 0


def test_counted_gives_up_after_max_attempts(client):
    w = WorkerController()
    r = _req(client, attempt=3, max_attempts=3)
    w._apply_failure(r, "API_429", {})
    assert r.status == "failed"  # attempt not < cap → terminal


def test_captcha_allowed_more_attempts_than_normal(client):
    w = WorkerController()
    # attempt 5 would be terminal for a counted error (cap 3) but retryable
    # for captcha (cap 10).
    r = _req(client, attempt=5, max_attempts=3)
    w._apply_failure(r, "captcha required", {})
    assert r.status == "queued"


def test_backoff_grows_with_attempt(client):
    from datetime import datetime, timezone

    w = WorkerController()
    r1 = _req(client, attempt=0, max_attempts=5)
    w._apply_failure(r1, "API_429", {})
    d1 = (r1.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
    r2 = _req(client, attempt=3, max_attempts=5)
    w._apply_failure(r2, "API_429", {})
    d2 = (r2.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
    assert d2 > d1  # exponential


def test_403_breaker_trips_and_stops_dispatching(client):
    w = WorkerController()
    for _ in range(MAX_CUMULATIVE_403 - 1):
        r = _req(client, attempt=0)
        w._apply_failure(r, "API_403", {})
        assert r.status == "queued"
    assert not w._tripped

    # The threshold 403 trips the breaker. The breaker gates DISPATCH — the
    # row itself stays queued, so the work survives until the account
    # recovers instead of being thrown away.
    r = _req(client, attempt=0)
    w._apply_failure(r, "API_403", {})
    assert w._tripped
    assert w._dispatch_blocked()
    assert r.status == "queued"


def test_repeated_403s_still_give_up_on_a_single_request(client):
    """Requeueing while tripped must not become an infinite loop: the
    attempt budget is what ends it."""
    w = WorkerController()
    r = _req(client, attempt=0, max_attempts=3)
    for _ in range(3):
        w._apply_failure(r, "API_403", {})
        assert r.status == "queued"
    w._apply_failure(r, "API_403", {})
    assert r.status == "failed"  # attempt budget exhausted


@pytest.mark.asyncio
async def test_success_resets_the_403_breaker(client):
    """Drive the real success path — the previous version of this test set
    the counter by hand and asserted on its own assignment, so deleting the
    reset from _process_one left it green."""

    async def ok_handler(params):
        return ({"ok": True}, None)

    w = WorkerController(handlers={"proxy": ok_handler}, cooldown_s=0)
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()
    assert w._tripped

    row = client.post("/api/requests", json={"type": "proxy", "params": {}}).json()
    await w._process_one(row["id"])

    assert client.get(f"/api/requests/{row['id']}").json()["status"] == "done"
    assert w._cumulative_403 == 0
    assert not w._tripped  # a success is what re-opens the breaker


def test_tripped_breaker_reopens_for_a_probe_instead_of_stalling_forever(client):
    """Without a half-open probe the breaker is one-way: nothing dispatches,
    so nothing can succeed, so the reset never fires and the queue is dead
    until a restart."""
    import time as _time

    w = WorkerController()
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()
    assert w._dispatch_blocked()  # closed right after tripping

    # Age the trip past the probe interval.
    w._tripped_at = _time.monotonic() - (BREAKER_PROBE_INTERVAL_S + 1)
    assert w._probe_due()
    assert not w._dispatch_blocked()  # one probe goes through…
    assert w._dispatch_blocked()  # …and only one; the clock re-armed


def test_breaker_state_is_reported_for_health(client):
    w = WorkerController()
    assert w.breaker_state["tripped"] is False
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()
    state = w.breaker_state
    assert state["tripped"] is True
    assert state["cumulative_403"] == MAX_CUMULATIVE_403
    assert state["seconds_until_probe"] > 0


def test_free_retries_are_capped_instead_of_looping_forever(client):
    """An expired token with the extension still connected would otherwise
    re-dispatch every 15s for the rest of the session."""
    w = WorkerController()
    r = _req(client, attempt=0, free_retries=FREE_MAX_RETRIES - 1)
    w._apply_failure(r, "API_401", {})
    assert r.status == "queued"
    assert r.free_retries == FREE_MAX_RETRIES
    assert r.attempt == 0  # still never burns the shared budget

    w._apply_failure(r, "API_401", {})
    assert r.status == "failed"
    assert r.error.startswith("auth_retry_exhausted")


def test_captcha_does_not_burn_the_shared_attempt_budget(client):
    """A request that hits captchas first must not arrive at a rate-limit
    error with its counted-retry budget already spent."""
    w = WorkerController()
    r = _req(client, attempt=0, max_attempts=3)
    for i in range(3):
        w._apply_failure(r, "captcha required", {})
        assert r.status == "queued"
        assert r.captcha_retries == i + 1
    assert r.attempt == 0

    # The counted budget is intact, so a later 429 still gets its retries.
    w._apply_failure(r, "API_429", {})
    assert r.status == "queued"
    assert r.attempt == 1


def test_captcha_retries_are_capped(client):
    w = WorkerController()
    r = _req(client, attempt=0, captcha_retries=CAPTCHA_MAX_ATTEMPTS)
    w._apply_failure(r, "captcha required", {})
    assert r.status == "failed"


@pytest.mark.parametrize(
    "err",
    [
        "media id 40312abc not found",
        "uploaded 1403 bytes",
        "PUBLIC_ERROR_X: quota 4030",
    ],
)
def test_digits_inside_other_tokens_do_not_trip_the_breaker(err):
    """Substring matching on '403' let a media id or byte count trip the
    account breaker — a misclassification that stalls the whole queue."""
    assert classify_error(err) != "403"


# ── sweeper ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_sweeper_picks_up_a_due_queued_row(client, monkeypatch):
    """A queued row nobody enqueued (e.g. a retry that came due, or a row
    left over from a restart) gets dispatched by the sweeper."""
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(type(flow_client), "connected", property(lambda self: True))

    ran: list[int] = []

    async def handler(params):
        ran.append(params["__request_id"])
        return ({"ok": True}, None)

    row = client.post("/api/requests", json={"type": "proxy", "params": {}}).json()

    w = WorkerController(
        handlers={"proxy": handler},
        cooldown_s=0,
        enable_sweeper=True,
        sweep_interval_s=0.05,
    )
    task = asyncio.create_task(w.start())
    try:
        # Note: we do NOT call w.enqueue(); the sweeper must find it.
        for _ in range(60):
            await asyncio.sleep(0.05)
            if client.get(f"/api/requests/{row['id']}").json()["status"] == "done":
                break
        assert ran == [row["id"]]
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_sweeper_pauses_while_extension_disconnected(client, monkeypatch):
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(type(flow_client), "connected", property(lambda self: False))

    ran: list[int] = []

    async def handler(params):
        ran.append(1)
        return ({"ok": True}, None)

    client.post("/api/requests", json={"type": "proxy", "params": {}}).json()
    w = WorkerController(
        handlers={"proxy": handler},
        cooldown_s=0,
        enable_sweeper=True,
        sweep_interval_s=0.05,
    )
    task = asyncio.create_task(w.start())
    try:
        await asyncio.sleep(0.4)
        assert ran == []  # nothing dispatched while the bridge is down
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


# ── the breaker must not be reachable from request input ──────────────────
@pytest.mark.parametrize(
    "err",
    [
        # Our own validation errors carry the caller's values verbatim. The
        # endpoint is unauthenticated and `params` is unvalidated, so if these
        # can classify as an auth failure, three POSTs stop all generation.
        # The per-tier key tables these named are gone; a capability with no
        # captured payload is refused under one prefix now, and it carries the
        # caller's lane name verbatim just as the old ones did.
        "unsupported_on_batch_lane_403",
        "unsupported_on_batch_veo_r2v_lane_401_aspect_X",
        "invalid_duration_403",
        "invalid_duration_401",
        "omni_aspect_unsupported_403",
        "invalid_project_id_403",
    ],
)
def test_our_own_validation_errors_never_feed_the_account_breaker(err):
    assert classify_error(err) == "terminal"


def test_a_replayed_captcha_token_is_a_captcha_not_an_unknown_error():
    """Flow answers `PUBLIC_ERROR_UNUSUAL_ACTIVITY` when a captcha token is
    sent twice, and every token is single-use.

    The string carries neither the word "captcha" nor a status code, so the
    generic rules read it as unknown and made it terminal — abandoning a batch
    that only needed a fresh token. It gets the captcha budget instead, which is
    deliberately larger than the ordinary one because solving one in the open
    Flow tab is expected to eventually work.
    """
    assert classify_error("FlowBatchError: ogiZ0b: PUBLIC_ERROR_UNUSUAL_ACTIVITY") == (
        "captcha"
    )
    assert classify_error("RpcError: eb1hJf failed: ['CAPTCHA_FAILED']") == "captcha"
    # And a content refusal stays terminal: retrying it re-spends credits to
    # reach the same answer.
    assert classify_error("PUBLIC_ERROR_UNSAFE_GENERATION") == "terminal"


def test_three_malformed_requests_cannot_trip_the_breaker(client):
    w = WorkerController()
    for _ in range(MAX_CUMULATIVE_403 + 2):
        r = _req(client, attempt=0)
        w._apply_failure(r, "invalid_duration_403", {})
    assert not w._tripped
    assert w._cumulative_403 == 0


# ── an open breaker must pause work, not destroy it ───────────────────────
@pytest.mark.parametrize(
    "err", ["API_429", "captcha required", "extension not connected"]
)
def test_open_breaker_requeues_instead_of_failing_retryable_work(client, err):
    """The half-open probe dispatches one real request per interval. If a
    failure while tripped is terminal, every probe permanently destroys a
    queued request — the breaker would eat the queue one row per minute."""
    w = WorkerController()
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()

    r = _req(client, attempt=0, max_attempts=3)
    w._apply_failure(r, err, {})

    assert r.status == "queued", "work must survive the breaker being open"
    assert r.finished_at is None


def test_open_breaker_still_refuses_to_retry_a_charged_operation(client):
    """The credit gate outranks the requeue rule."""
    w = WorkerController()
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()
    r = _req(client, attempt=0)
    w._apply_failure(r, "API_503", {"operation_names": ["op/charged"]})
    assert r.status == "failed"


def test_probe_is_not_consumed_when_there_is_nothing_to_dispatch(client):
    """The probe is the breaker's only path back to normal. Burning it on an
    idle tick (the main loop polls every 0.5s, so most ticks are idle) pushes
    real recovery from ~60s out to minutes."""
    import time as _time

    w = WorkerController()
    w._cumulative_403 = MAX_CUMULATIVE_403
    w._trip_breaker()
    w._tripped_at = _time.monotonic() - (BREAKER_PROBE_INTERVAL_S + 1)

    # Asking whether dispatch is allowed must not, by itself, spend the probe.
    assert w._probe_due()
    assert w._probe_due()
    assert w._probe_due(), "a read-only check must be repeatable"

    # Only actually taking the probe re-arms the clock.
    w._consume_probe()
    assert not w._probe_due()


def test_a_clean_flow_code_still_classifies_as_captcha():
    """The trap this ordering exists for.

    `PUBLIC_ERROR_` is in `_SELF_TERMINAL_PREFIXES`, and terminal abandons a
    batch. So the moment the error string began leading with Flow's clean code —
    which it does since the live probe showed users were reading a serialised
    protobuf — `PUBLIC_ERROR_UNUSUAL_ACTIVITY` would have become terminal, and a
    batch needing nothing but a fresh single-use token would have been thrown
    away. The specific captcha codes are therefore read first.
    """
    assert classify_error("PUBLIC_ERROR_UNUSUAL_ACTIVITY (ogiZ0b)") == "captcha"
    assert classify_error("CAPTCHA_FAILED") == "captcha"
    # And the prefix rule still governs everything else that starts that way.
    assert classify_error("PUBLIC_ERROR_MODEL_ACCESS_DENIED (eb1hJf)") == "terminal"
    assert classify_error("PUBLIC_ERROR_UNSAFE_GENERATION (ogiZ0b)") == "terminal"


def test_the_captcha_check_is_not_reachable_by_a_caller_supplied_value():
    """It is read before the self-terminal rule, so it must match exact codes.

    A looser test would let an unvalidated param carry a request into the
    captcha budget, which retries far longer than the ordinary one.
    """
    assert classify_error("invalid_duration_'captcha'") == "terminal"
    assert classify_error("missing_prompt_unusual") == "terminal"


# ── the breaker protects the Google account, so only Google may trip it ──


def test_an_openai_failure_under_postprod_does_not_reach_the_flow_breaker():
    """`postprod` runs ffmpeg locally — and OpenAI TTS, and whisper.

    `_NON_FLOW_REQUEST_TYPES` held only `gen_image_openai`, but neither TTS nor
    transcription dispatches under that type: both run inside a `postprod`
    request. `OpenAITTSError` subclasses `RuntimeError`, so the handler returns
    it as a plain error string and the classifier read "403" as a FLOW 403.

    Three of those trip `MAX_CUMULATIVE_403` and stop every Flow dispatch in the
    app — over another vendor's bill. That is verbatim the failure
    `classify_error`'s own docstring says was fixed for images.
    """
    from flowboard.worker.processor import classify_error

    for err in (
        "OpenAI TTS lỗi HTTP 403",
        "transcribe_error: whisper-1 hỏng: HTTP 403",
        "OpenAI từ chối khoá API (401)",
    ):
        assert classify_error(err, request_type="postprod") == "terminal", err


def test_a_rate_limited_openai_call_under_postprod_is_still_retried():
    """The other direction of the same fix: 429 is transient for every vendor,
    so it must stay `counted` rather than become terminal. A rate-limited
    narration that fails hard loses a pass that would have succeeded in a
    minute."""
    from flowboard.worker.processor import classify_error

    assert classify_error(
        "OpenAI giới hạn tốc độ (429)", request_type="postprod"
    ) == "counted"
