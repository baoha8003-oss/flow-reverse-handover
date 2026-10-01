"""The /api/auth surface after Google stopped minting a token for us.

This used to test three things the agent could learn on its own: the signed-in
profile (Google's userinfo endpoint, called with the captured Bearer), the
account tier and the credit balance (`/v1/credits`, same token). The September
2026 Flow migration removed the token, so none of it is knowable here any more.

What the routes answer now, and what these tests pin:

* ``/me`` — the tier the user picked in Settings, as a LABEL; identity fields
  kept as a stable null shape rather than a promise.
* ``/flow-probe`` — can the browser sign a Flow call? Presence only, never
  values. This replaces ``/refresh-token``, which waited for a Bearer that is
  no longer issued.
* ``/scan`` — one round-trip that distinguishes "bridge down" from "bridge up
  but the page cannot sign", because the second one looked healthy for a week.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import settings_store
from flowboard.services.flow_client import flow_client


@pytest.fixture(autouse=True)
def _reset_state():
    """`flow_client` is a module singleton and bleeds state across tests."""
    flow_client.clear_extension()
    flow_client._paygate_tier = None
    yield
    flow_client.clear_extension()
    flow_client._paygate_tier = None


class _FakeWs:
    """Records what the agent pushes, and answers probes if told to."""

    def __init__(self, probe: dict | None = None) -> None:
        self.sent: list[dict] = []
        self._probe = probe

    async def send(self, payload: str) -> None:
        msg = json.loads(payload)
        self.sent.append(msg)
        if msg.get("method") == "flow_probe" and self._probe is not None:
            flow_client.resolve_callback({"id": msg["id"], "result": self._probe})


# ── /me ───────────────────────────────────────────────────────────────


def test_me_reports_no_identity_because_none_is_knowable(client):
    """The keys stay so the frontend need not branch on their absence; the
    values are null because the endpoint that filled them needed a token."""
    me = client.get("/api/auth/me").json()
    assert me["email"] is None
    assert me["name"] is None
    assert me["picture"] is None
    assert me["verified_email"] is None


def test_me_reports_the_tier_the_user_chose(client):
    client.put(
        "/api/settings", json={"values": {"FLOW_PAYGATE_TIER": "PAYGATE_TIER_TWO"}}
    )
    assert client.get("/api/auth/me").json()["paygate_tier"] == "PAYGATE_TIER_TWO"


def test_an_unset_tier_is_null_not_pro(client):
    """Defaulting to Pro is the bug that poisoned the DB and then this very
    route for a whole session."""
    assert client.get("/api/auth/me").json()["paygate_tier"] is None


def test_the_balance_is_null_rather_than_a_stale_number(client):
    """There is no credits RPC on this path. Null and 0 send a user to opposite
    places, so a guess here would be worse than an absence."""
    me = client.get("/api/auth/me").json()
    assert me["credits"] is None
    assert me["sku"] is None


def test_me_carries_the_last_probe_result(client):
    flow_client._flow_probe = {"flowTabPresent": True, "atTokenPresent": True}
    assert client.get("/api/auth/me").json()["flow_tab"]["atTokenPresent"] is True


# ── /flow-probe ───────────────────────────────────────────────────────


def test_the_probe_needs_the_extension_and_says_which_is_missing(client):
    body = client.post("/api/auth/flow-probe").json()
    assert body["ok"] is False
    assert body["error"] == "extension_disconnected"
    assert "Reload" in body["note"]


def test_a_signed_in_tab_answers_ok(client):
    flow_client.set_extension(_FakeWs({"flowTabPresent": True, "atTokenPresent": True}))
    body = client.post("/api/auth/flow-probe").json()
    assert body["ok"] is True
    assert body["note"] is None


def test_no_flow_tab_says_open_one(client):
    """The actionable half: this is fixed in the browser, not in the app."""
    flow_client.set_extension(
        _FakeWs({"flowTabPresent": False, "atTokenPresent": False})
    )
    body = client.post("/api/auth/flow-probe").json()
    assert body["ok"] is False
    assert "flow.google.com" in body["note"]


def test_a_tab_that_cannot_sign_gets_a_different_instruction(client):
    """"Open a tab" and "sign in on the tab you have" are different fixes, and
    the old single "Open Flow" hint covered both badly."""
    flow_client.set_extension(_FakeWs({
        "flowTabPresent": True, "atTokenPresent": False, "error": "NO_AT_TOKEN",
    }))
    body = client.post("/api/auth/flow-probe").json()
    assert body["ok"] is False
    assert "đăng nhập" in body["note"]


def test_the_probe_never_returns_a_credential(client):
    """It reports whether `at` is on the page, not what it is — and the email as
    a boolean, never an address."""
    flow_client.set_extension(_FakeWs({
        "flowTabPresent": True, "atTokenPresent": True, "emailPresent": True,
        "host": "flow.google.com", "sourcePath": "/project/abc",
    }))
    raw = json.dumps(client.post("/api/auth/flow-probe").json())
    assert "SNlM0e" not in raw
    assert "@" not in raw.replace("flow.google.com", "")


# ── /scan ─────────────────────────────────────────────────────────────


def test_scan_reports_a_missing_bridge(client):
    body = client.post("/api/auth/scan").json()
    assert body["extension_connected"] is False
    assert body["flow_tab_signed"] is False


def test_scan_separates_bridge_up_from_page_signed(client):
    """The distinction the migration made necessary: connected, healthy queue,
    and nothing able to generate."""
    flow_client.set_extension(_FakeWs({
        "flowTabPresent": True, "atTokenPresent": False, "error": "NO_AT_TOKEN",
    }))
    body = client.post("/api/auth/scan").json()
    assert body["extension_connected"] is True
    assert body["flow_tab_present"] is True
    assert body["flow_tab_signed"] is False
    assert body["probe_error"] == "NO_AT_TOKEN"


def test_scan_reports_the_extension_build(client):
    """Which build is loaded is what a stale reload makes unanswerable, and it
    cost an afternoon of blind retries during the migration."""
    flow_client.set_extension(_FakeWs({"flowTabPresent": True, "atTokenPresent": True}))
    flow_client._extension_version = "0.1.0"
    assert client.post("/api/auth/scan").json()["extension_version"] == "0.1.0"


def test_scan_reports_whether_a_tier_was_chosen(client):
    flow_client.set_extension(_FakeWs({"flowTabPresent": True, "atTokenPresent": True}))
    assert client.post("/api/auth/scan").json()["has_paygate_tier"] is False
    client.put(
        "/api/settings", json={"values": {"FLOW_PAYGATE_TIER": "PAYGATE_TIER_ONE"}}
    )
    assert client.post("/api/auth/scan").json()["has_paygate_tier"] is True


# ── /logout ───────────────────────────────────────────────────────────


def test_logout_tells_the_extension_but_holds_no_credential_to_drop(client):
    """Kept because the frontend still offers it, and telling the extension is
    harmless. What it cannot do is sign the user out: the session lives in the
    browser's cookie jar, which this app does not touch."""
    ws = _FakeWs()
    flow_client.set_extension(ws)

    body = client.post("/api/auth/logout").json()
    assert body["ok"] is True
    assert body["extension_notified"] is True
    assert ws.sent == [{"type": "logout"}]


def test_logout_without_an_extension_is_not_an_error(client):
    assert client.post("/api/auth/logout").json() == {
        "ok": True, "extension_notified": False,
    }


# ── what is gone ──────────────────────────────────────────────────────


def test_refresh_token_is_gone(client):
    """It opened a background tab and waited for a Bearer to appear. Nothing
    issues one, so the wait could only ever time out."""
    assert client.post("/api/auth/refresh-token").status_code == 404


def test_the_tier_is_never_fetched_from_google():
    """`fetch_paygate_tier` called `/v1/credits` with the captured token. Both
    the endpoint and the token are gone; a leftover caller would be a request
    that cannot succeed."""
    assert not hasattr(flow_client, "fetch_paygate_tier")
    assert settings_store.validate_value("FLOW_PAYGATE_TIER", "PAYGATE_TIER_ONE") is None


# ── /batch-probe ──────────────────────────────────────────────────────


def test_the_probe_only_sends_read_rpcs(client):
    """A closed set, because this is an unauthenticated localhost endpoint.

    Widened by one generation RPC it becomes "spend this user's credits", and
    widened to accept a raw envelope it becomes "issue arbitrary Flow commands
    as the signed-in user".
    """
    body = client.post(
        "/api/auth/batch-probe", json={"rpcid": "ogiZ0b", "project_id": "p"}
    ).json()
    assert body["ok"] is False
    assert "rpcid_not_probeable" in body["error"]


def test_the_probe_never_accepts_a_prebuilt_envelope(client):
    """The envelope is built server-side from `flow_batch`. Pinned as a shape
    test because the failure it guards against is not visible in behaviour: a
    route that forwarded `f.req` would look identical until someone posted one."""
    import inspect

    from flowboard.routes import auth

    source = inspect.getsource(auth.batch_probe)
    assert "freq" in source
    assert "body.freq" not in source
    assert not hasattr(auth.BatchProbe, "freq")
    assert "freq" not in auth.BatchProbe.model_fields


def test_the_listing_probe_requires_a_match_window(client):
    """17 MB. A probe without `match` would pull the whole payload through the
    bridge to answer a yes/no question."""
    body = client.post(
        "/api/auth/batch-probe",
        json={"rpcid": "Zzl0ze", "project_id": "abcd1234"},
    ).json()
    assert body["error"] == "missing_match"


def test_the_probe_needs_the_id_its_rpc_reads(client):
    for rpcid, missing in (
        ("Zzl0ze", "project_id"),
        ("jwpduf", "operation_id"),
        ("as29s", "media_id"),
    ):
        body = client.post("/api/auth/batch-probe", json={"rpcid": rpcid}).json()
        assert body["error"] == f"missing_{missing}", rpcid


def test_the_probe_needs_the_extension(client):
    body = client.post(
        "/api/auth/batch-probe",
        json={"rpcid": "as29s", "media_id": "m-1"},
    ).json()
    assert body["error"] == "extension_disconnected"


def test_the_probe_describes_the_answer_without_echoing_it(client, monkeypatch):
    """The project listing window holds media titles and prompts. Echoing it
    would print the user's own prompts into a console and an activity log, which
    is the same leak as logging the envelope — just arriving from the other
    direction."""
    from flowboard.services import flow_batch as fb
    from flowboard.services.flow_client import flow_client as fc

    window = (
        '["op-1",null,null,["Bí mật của tôi",1,2,null,null,'
        '"11111111-1111-4111-8111-111111111111"],"p'
    )

    async def fake_batch_rpc(rpcid, freq, captcha_action=None, match=None, timeout=None):
        assert rpcid == fb.RPC_PROJECT_MEDIA
        assert match == "op-1"
        return {"data": window}

    monkeypatch.setattr(fc, "batch_rpc", fake_batch_rpc)
    monkeypatch.setattr(fc, "_ws", object(), raising=False)
    monkeypatch.setattr(type(fc), "connected", property(lambda self: True))

    raw = client.post(
        "/api/auth/batch-probe",
        json={"rpcid": "Zzl0ze", "project_id": "abcd1234", "match": "op-1"},
    ).text
    assert "Bí mật" not in raw
    assert "11111111-1111" not in raw
    body = json.loads(raw)
    assert body["match_found"] is True
    assert body["response_chars"] == len(window)


def test_a_media_probe_reports_url_presence_not_the_url(client, monkeypatch):
    """Signed urls are short-lived download grants. One in a log is a download
    anybody holding the log can perform."""
    from flowboard.services import flow_batch as fb
    from flowboard.services.flow_client import flow_client as fc
    from tests.flow_fakes import envelope, media_reply

    media_id = "22222222-2222-4222-8222-222222222222"

    async def fake_batch_rpc(rpcid, freq, captcha_action=None, match=None, timeout=None):
        return {"data": envelope(fb.RPC_MEDIA, media_reply(media_id))}

    monkeypatch.setattr(fc, "batch_rpc", fake_batch_rpc)
    monkeypatch.setattr(type(fc), "connected", property(lambda self: True))

    raw = client.post(
        "/api/auth/batch-probe", json={"rpcid": "as29s", "media_id": media_id}
    ).text
    assert "https://" not in raw
    body = json.loads(raw)
    assert body["has_video_url"] is True
    assert body["has_image_url"] is True
