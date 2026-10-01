"""The Flow account is something the user tells us now, not something we read.

Google's September 2026 migration took away both facts this app used to fetch:
the Bearer token (so no `/v1/credits`) and the `userPaygateTier` field on the
wire (so nothing downstream needs it). What is left is a label — which lanes to
offer, which price to quote — and a fallback project uuid.

The load-bearing test here is `test_an_unset_tier_does_not_block_generation`:
the old behaviour was a hard refusal, and keeping it would have stranded every
generation behind a dropdown that nothing can fill automatically.
"""
from __future__ import annotations

import pytest

from flowboard.services import settings_store
from flowboard.services.flow_client import flow_client
from flowboard.worker import processor as proc


@pytest.fixture(autouse=True)
def _no_live_tier():
    """No pre-migration session in play, so Settings is the only source."""
    flow_client._paygate_tier = None
    yield
    flow_client._paygate_tier = None


# ── the two settings ──────────────────────────────────────────────────


def test_both_keys_are_settable(client):
    resp = client.put("/api/settings", json={"values": {
        "FLOW_PAYGATE_TIER": "PAYGATE_TIER_TWO",
        "FLOW_PROJECT_ID": "11111111-2222-3333-4444-555555555555",
    }})
    assert resp.status_code == 200
    assert sorted(resp.json()["updated"]) == ["FLOW_PAYGATE_TIER", "FLOW_PROJECT_ID"]


@pytest.mark.parametrize("bad", ["ULTRA", "pro", "PAYGATE_TIER_THREE", "<script>", 2])
def test_a_tier_outside_the_closed_set_is_refused(client, bad):
    """A typo'd tier would silently change which lanes are offered and which
    price is quoted, and nothing downstream would contradict it."""
    resp = client.put("/api/settings", json={"values": {"FLOW_PAYGATE_TIER": bad}})
    assert resp.status_code == 400


def test_an_empty_tier_is_a_valid_choice(client):
    """"Chưa chọn" has to be expressible — it is the shipped default."""
    assert client.put(
        "/api/settings", json={"values": {"FLOW_PAYGATE_TIER": ""}}
    ).status_code == 200
    assert flow_client.paygate_tier is None


@pytest.mark.parametrize("bad", ["nope", "11111111-2222-3333-4444", "../etc/passwd"])
def test_a_project_id_that_is_not_a_uuid_is_refused(client, bad):
    """It lands in an RPC payload; a non-uuid there is a request Flow refuses
    after the round trip rather than a typo someone can see."""
    resp = client.put("/api/settings", json={"values": {"FLOW_PROJECT_ID": bad}})
    assert resp.status_code == 400


def test_an_empty_project_id_means_let_flow_mint_one(client):
    assert client.put(
        "/api/settings", json={"values": {"FLOW_PROJECT_ID": ""}}
    ).status_code == 200


# ── what reads them ───────────────────────────────────────────────────


def test_the_tier_reaches_auth_me_and_the_model_registry(client):
    client.put("/api/settings", json={"values": {"FLOW_PAYGATE_TIER": "PAYGATE_TIER_TWO"}})
    assert client.get("/api/auth/me").json()["paygate_tier"] == "PAYGATE_TIER_TWO"
    assert client.get("/api/models").json()["tierLabel"] == "Ultra"


def test_a_live_tier_still_wins_over_the_setting(client):
    """A session that resolved a real tier before the migration keeps
    reporting it — the setting is the fallback, not an override."""
    client.put("/api/settings", json={"values": {"FLOW_PAYGATE_TIER": "PAYGATE_TIER_ONE"}})
    flow_client._paygate_tier = "PAYGATE_TIER_TWO"
    assert flow_client.paygate_tier == "PAYGATE_TIER_TWO"


def test_an_unset_tier_reads_as_none_not_as_pro(client):
    """Not a default. Defaulting to Pro is the bug that poisoned the DB and
    then /api/auth/me for a whole session."""
    assert flow_client.paygate_tier is None
    assert client.get("/api/auth/me").json()["paygate_tier"] is None


# ── the gate that is gone ─────────────────────────────────────────────


def test_an_unset_tier_does_not_block_generation():
    """The whole point of the change. `_tier_label` answers None and the
    handler carries on; the old `_tier_or_reason` refused here."""
    assert proc._tier_label({}) is None
    assert not hasattr(proc, "_tier_or_reason")


def test_the_two_old_refusal_codes_can_no_longer_be_produced():
    """They were terminal, so any leftover producer would kill a request that
    has nothing wrong with it."""
    import inspect

    source = inspect.getsource(proc)
    assert "paygate_tier_unknown" not in source
    assert "flow_session_expired" not in source
    assert "paygate_tier_unknown" not in proc._SELF_TERMINAL_CODES
    assert "flow_session_expired" not in proc._SELF_TERMINAL_CODES


def test_no_tier_of_any_kind_reaches_the_payload():
    """There is no slot for it, which is stronger than omitting it.

    `_client_context` used to be the chokepoint: omit an unknown tier, never
    coerce it to Pro. The migrated payload has no `userPaygateTier` field at
    all -- `flow_batch._context` is the whole of the context block -- so the
    question is no longer "did we omit it" but "is there anywhere to put it".
    Asked against a real built envelope rather than a helper, because the
    helper is what got deleted.
    """
    from flowboard.services import flow_batch as fb

    envelopes = [
        fb.image_request("a cat", "proj-1"),
        fb.video_request("a cat", "proj-1", "media-1"),
        fb.text_video_request("a cat", "proj-1"),
        fb.omni_reference_video_request("a cat", "proj-1", ["media-1"]),
        fb.create_project_request("board"),
    ]
    for envelope in envelopes:
        assert "aygateTier" not in envelope
        assert "PAYGATE_TIER" not in envelope


def test_the_settings_default_ships_unset():
    """Shipping a guess would put a price on screen nobody chose."""
    assert not settings_store.defaults().get("FLOW_PAYGATE_TIER")
    assert not settings_store.defaults().get("FLOW_PROJECT_ID")


# ── the resolution setting ────────────────────────────────────────────


def test_the_shipped_resolution_default_is_one_flow_accepts():
    """It was 480p, copied from the packaged tool. Nothing read the key until
    the batch transport gave Omni a resolution slot — and Flow accepts only
    360p or 720p there, so the default was a value on screen that no dispatch
    could ever use."""
    assert settings_store.defaults()["VIDEO_RESOLUTION"] == "720p"


def test_a_resolution_outside_the_closed_set_is_refused():
    """The value lands in the model key. A typo would be dropped silently and
    the render would come back at the other resolution, billed accordingly."""
    assert settings_store.validate_value("VIDEO_RESOLUTION", "480p") is not None
    assert settings_store.validate_value("VIDEO_RESOLUTION", "4k") is not None
    assert settings_store.validate_value("VIDEO_RESOLUTION", "720p") is None
    assert settings_store.validate_value("VIDEO_RESOLUTION", "360p") is None


def test_the_refusal_does_not_offer_an_empty_value_that_is_also_refused():
    """The message said "or empty" for every closed-set key. Only the plan
    accepts empty, so on this one it told the user to try a value it would
    reject too."""
    message = settings_store.validate_value("VIDEO_RESOLUTION", "480p")
    assert "or empty" not in message
    assert "or empty" in settings_store.validate_value("FLOW_PAYGATE_TIER", "TIER_X")


def test_a_config_file_value_outside_the_set_is_ignored(monkeypatch):
    """`config.json` is written by another program, and it really does carry
    values this one cannot use. Imported unchecked, 480p reached the Settings
    screen as a selected option the dropdown could not even display."""
    monkeypatch.setattr(
        settings_store, "_load_config",
        lambda: {"VIDEO_RESOLUTION": "480p", "FLOW_PAYGATE_TIER": "TIER_X"},
    )
    defaults = settings_store.defaults()
    # Falls back to BASELINE where there is one...
    assert defaults["VIDEO_RESOLUTION"] == "720p"
    # ...and stays absent where there is not. The plan ships unset on purpose,
    # so "no key" is the correct answer rather than an empty string invented
    # here to make the shapes match.
    assert not defaults.get("FLOW_PAYGATE_TIER")


def test_a_config_file_value_inside_the_set_still_wins(monkeypatch):
    """The check must not turn into "ignore config.json"."""
    monkeypatch.setattr(
        settings_store, "_load_config", lambda: {"VIDEO_RESOLUTION": "360p"}
    )
    assert settings_store.defaults()["VIDEO_RESOLUTION"] == "360p"
