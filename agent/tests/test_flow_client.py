"""FlowClient correlation, callback resolution, and the batch transport.

No real WebSocket here: a FakeWs records the outgoing JSON so the protocol shape
can be asserted, and futures resolve through ``resolve_callback`` — the same code
path the HTTP callback handler uses.

What changed with Google's September 2026 migration: ``api_request`` is gone.
There is no Bearer token to proxy with and the REST host no longer
authenticates, so the agent builds a ``batchexecute`` envelope and the extension
runs it inside a signed-in Flow tab. The correlation machinery below is
unchanged — that part was never the problem.
"""
import asyncio
import json

import pytest

from flowboard.services.flow_client import FlowClient


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _resolve_soon(client: FlowClient, reply: dict) -> None:
    """Answer the next pending request once `_send` has registered it."""

    async def later() -> None:
        await asyncio.sleep(0)
        assert len(client._pending) == 1
        req_id = next(iter(client._pending))
        client.resolve_callback({"id": req_id, **reply})

    asyncio.create_task(later())


# ── correlation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_batch_rpc_round_trips_through_the_callback():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)
    _resolve_soon(client, {"status": 200, "data": ")]}'\n2\n[]"})

    result = await client.batch_rpc("ogiZ0b", "[[[...]]]")

    assert result["status"] == 200
    assert ws.sent[0]["method"] == "batch_rpc"
    assert ws.sent[0]["params"]["rpcid"] == "ogiZ0b"
    assert client._pending == {}
    assert client.ws_stats["request_count"] == 1
    assert client.ws_stats["success_count"] == 1


@pytest.mark.asyncio
async def test_a_batch_rpc_without_an_extension_says_so():
    client = FlowClient()
    assert await client.batch_rpc("ogiZ0b", "[]") == {"error": "extension_disconnected"}


@pytest.mark.asyncio
async def test_clear_extension_fails_pending_futures():
    client = FlowClient()
    client.set_extension(FakeWs())

    task = asyncio.create_task(client.batch_rpc("ogiZ0b", "[]"))
    await asyncio.sleep(0)  # let _send register the future
    assert len(client._pending) == 1

    client.clear_extension()
    assert await task == {"error": "extension_disconnected"}


def test_callback_secret_is_unique_per_instance():
    a = FlowClient()
    b = FlowClient()
    assert a.callback_secret != b.callback_secret
    assert len(a.callback_secret) >= 32


@pytest.mark.asyncio
async def test_a_4xx_counts_as_a_failure():
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"status": 502, "error": "NO_FLOW_TAB"})

    await client.batch_rpc("ogiZ0b", "[]")
    assert client.ws_stats["failed_count"] == 1
    assert client.ws_stats["success_count"] == 0


# ── what travels, and what must not ───────────────────────────────────


@pytest.mark.asyncio
async def test_the_captcha_action_and_match_window_travel():
    """Both are the extension's job: the mint has to happen in the page moments
    before the request leaves, and the 17 MB project listing has to be cut down
    before it crosses the bridge."""
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)
    _resolve_soon(client, {"status": 200, "data": ""})

    await client.batch_rpc(
        "Zzl0ze", "[]", captcha_action="IMAGE_GENERATION", match="op-1"
    )
    params = ws.sent[0]["params"]
    assert params["captchaAction"] == "IMAGE_GENERATION"
    assert params["match"] == "op-1"


@pytest.mark.asyncio
async def test_a_failure_names_the_rpc_and_never_the_envelope():
    """An `f.req` holds the prompt, and a substituted one holds a captcha
    token. A diagnosis needs the rpcid, not the payload."""
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"status": 502, "error": "NO_AT_TOKEN"})

    secret_prompt = "a very private prompt"
    await client.batch_rpc("eb1hJf", f'[[["eb1hJf","{secret_prompt}"]]]')

    assert client.ws_stats["last_failure"]["endpoint"] == "eb1hJf"
    assert secret_prompt not in json.dumps(client.ws_stats)


@pytest.mark.asyncio
async def test_trpc_request_still_serves_the_session_channels():
    """Grok and Shopee answer only to a signed-in cookie session. They are why
    this method outlived the Flow tRPC endpoints it was built for."""
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)
    _resolve_soon(client, {"status": 200, "data": {"ok": True}})

    result = await client.trpc_request(
        url="https://grok.com/rest/app-chat/conversations/new", body={"x": 1}
    )
    assert ws.sent[0]["method"] == "trpc_request"
    assert result["status"] == 200


# ── the page-cannot-sign state ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [
    "NO_FLOW_TAB",
    "FLOW_TAB_DISCARDED",
    "NO_AT_TOKEN",
    "NO_INJECTION_RESULT",
])
async def test_an_unsigned_page_is_recorded_so_health_can_say_it(code):
    """The state that hid the migration for a week: extension connected, queue
    healthy, nothing able to generate. It has to be visible."""
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"status": 502, "error": f"CAPTCHA_FAILED: {code}"})

    await client.batch_rpc("eb1hJf", "[]")
    assert client.page_unsigned is not None
    assert code in client.ws_stats["page_unsigned"]


@pytest.mark.asyncio
async def test_a_successful_rpc_clears_the_unsigned_state():
    """Sticky state outliving the condition it described is its own bug: the
    user reloads the Flow tab, it works, and the panel still says signed out."""
    client = FlowClient()
    client.set_extension(FakeWs())
    client._page_unsigned = "NO_AT_TOKEN"
    _resolve_soon(client, {"status": 200, "data": ""})

    await client.batch_rpc("as29s", "[]")
    assert client.page_unsigned is None


@pytest.mark.asyncio
async def test_an_ordinary_flow_error_is_not_read_as_an_unsigned_page():
    """A content-filter rejection is the request's problem; reloading the tab
    would not help, and saying so would send the user somewhere useless."""
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"status": 200, "error": "PUBLIC_ERROR_UNSAFE_GENERATION"})

    await client.batch_rpc("ogiZ0b", "[]")
    assert client.page_unsigned is None


# ── the probe ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_probe_reports_what_the_page_answered():
    client = FlowClient()
    ws = FakeWs()
    client.set_extension(ws)
    _resolve_soon(client, {"result": {
        "flowTabPresent": True, "atTokenPresent": True, "emailPresent": True,
    }})

    probe = await client.flow_probe()
    assert probe["atTokenPresent"] is True
    assert ws.sent[0]["method"] == "flow_probe"
    assert client.flow_tab == probe
    assert client.ws_stats["at_token_present"] is True
    assert client.ws_stats["flow_probe_age_s"] == 0


@pytest.mark.asyncio
async def test_a_probe_that_finds_an_unsigned_tab_records_why():
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"result": {
        "flowTabPresent": True, "atTokenPresent": False, "error": "NO_AT_TOKEN",
    }})

    await client.flow_probe()
    assert client.page_unsigned == "NO_AT_TOKEN"
    assert client.ws_stats["flow_tab_present"] is True


@pytest.mark.asyncio
async def test_a_probe_the_extension_cannot_answer_is_not_a_signed_tab():
    """An older extension has no `flow_probe` method, so this times out. The
    honest answer is "unknown, and therefore not signed"."""
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"error": "timeout"})

    probe = await client.flow_probe()
    assert probe["atTokenPresent"] is False
    assert probe["error"] == "timeout"


@pytest.mark.asyncio
async def test_disconnecting_forgets_the_probe():
    """It described that connection's tab. Reporting it against the next one
    would be a stale yes."""
    client = FlowClient()
    client.set_extension(FakeWs())
    _resolve_soon(client, {"result": {"flowTabPresent": True, "atTokenPresent": True}})
    await client.flow_probe()

    client.clear_extension()
    assert client.flow_tab is None
    assert client.ws_stats["at_token_present"] is None


# ── the handshake ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extension_ready_records_the_build_and_the_transport():
    """The version is load-bearing: without it, "reload the extension" and "the
    extension was reloaded" are indistinguishable from here."""
    client = FlowClient()
    await client.handle_message({
        "type": "extension_ready", "version": "0.1.0", "transport": "batch",
    })
    assert client.ws_stats["extension_version"] == "0.1.0"
    assert client.ws_stats["transport"] == "batch"


@pytest.mark.asyncio
async def test_a_token_message_from_an_old_extension_is_ignored():
    """An extension that has not been reloaded still sends `token_captured`.
    There is nowhere for it to land, and the state it implies — that this
    process is authorised — is no longer knowable from here."""
    client = FlowClient()
    await client.handle_message({"type": "token_captured", "flowKey": "ya29.LEAK"})
    assert "ya29.LEAK" not in json.dumps(client.ws_stats)
    assert not hasattr(client, "_flow_key")


def test_an_http_error_from_the_page_reaches_the_retry_policy(monkeypatch):
    """The status was kept for the health panel and dropped from the error.

    The extension forwards a non-OK page fetch as `{id, status, data}` with no
    `error` key, so `_payload` found no envelope and raised "no <rpcid> envelope
    in response". `classify_error` buckets that as `terminal`, which meant:
    `MAX_CUMULATIVE_403` could never be reached from the Flow transport — the
    breaker guarding the one Google account could not see a Google 403 — and a
    429 or 502 mid-wave threw away queued work instead of retrying it.
    """
    import asyncio

    from flowboard.services.flow_client import flow_client
    from flowboard.worker.processor import classify_error

    async def run(status: int) -> str:
        async def fake_send(method, params, timeout=None):
            return {"status": status, "data": "<html>nope</html>"}

        monkeypatch.setattr(flow_client, "_send", fake_send)
        result = await flow_client.batch_rpc("eb1hJf", "f.req=x")
        return str(result.get("error") or "")

    assert classify_error(asyncio.run(run(403))) == "403"
    assert classify_error(asyncio.run(run(429))) == "counted"
    assert classify_error(asyncio.run(run(502))) == "counted"


def test_a_page_error_body_is_not_mistaken_for_an_unsigned_page(monkeypatch):
    """`API_403` must not set `page_unsigned`: telling the user to reload the tab
    when their cookie is refused sends them somewhere that cannot help."""
    import asyncio

    from flowboard.services.flow_client import flow_client

    async def fake_send(method, params, timeout=None):
        return {"status": 403, "data": ""}

    monkeypatch.setattr(flow_client, "_send", fake_send)
    flow_client._page_unsigned = None
    asyncio.run(flow_client.batch_rpc("eb1hJf", "f.req=x"))
    assert flow_client._page_unsigned is None
