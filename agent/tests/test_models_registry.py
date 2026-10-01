"""`/api/models` — the single source of truth for what this account can run.

Twenty option lists used to be typed out by hand across ten frontend files, and
they drifted apart. These tests pin the drifts that were actually found, so the
registry cannot reintroduce them.

Two of the original four drifts were about the account tier: a duration Pro was
measured to refuse, and a "free" lane that Pro is billed for. Both are gone, and
not because they were fixed — the migrated Flow payload has no
`userPaygateTier` field, so entitlements are Google's answer at dispatch time
and nothing here can filter on them. What replaced them is a sharper version of
drift #3: a lane this transport cannot serve is refused for free, and its note
must say so rather than promising the paid substitution it used to make.
"""
from __future__ import annotations

import pytest

from flowboard.routes import models as mod
from flowboard.services import flow_sdk


@pytest.fixture
def pro(monkeypatch):
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(flow_client, "_paygate_tier", mod.TIER_ONE, raising=False)
    return flow_client


@pytest.fixture
def ultra(monkeypatch):
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(flow_client, "_paygate_tier", mod.TIER_TWO, raising=False)
    return flow_client


def _models(client) -> dict:
    resp = client.get("/api/models")
    assert resp.status_code == 200, resp.text
    return resp.json()


LANES = ("t2v", "i2v", "startEnd", "omni")


# ── drift #1: Square was offered for video ───────────────────────────────

def test_square_is_an_image_aspect_only(client, pro):
    """No video model key is keyed on Square, so a Square video dispatch could
    only ever fail. It was offered anyway in two places."""
    body = _models(client)
    image = {a["value"] for a in body["imageAspects"]}
    assert "IMAGE_ASPECT_RATIO_SQUARE" in image

    for lane in LANES:
        values = {a["value"] for a in body[lane]["aspects"]}
        assert not any("SQUARE" in v for v in values), f"{lane} offers Square"


# ── drift #2: a lane offered with a promise the dispatch will not keep ────

def test_a_lane_this_path_cannot_serve_warns_that_it_will_be_refused(client, pro):
    """The regression that matters most in this file.

    `fast_relaxed` reads as a free queue and there is no key for it here at all.
    The label stays, because the packaged tool shows it and someone will come
    looking — but the note must say the dispatch is REFUSED. It used to promise
    "will run Veo 3.1 - Fast and WILL cost credits", and that promise is no
    longer kept: the run errors instead, for free. A user reading the old note
    cancels a run that was never going to charge them.
    """
    qualities = {q["value"]: q for q in _models(client)["i2v"]["qualities"]}
    assert "fast_relaxed" in qualities
    note = qualities["fast_relaxed"]["note"]
    assert note
    assert "TỪ CHỐI" in note
    assert "sẽ chạy" not in note, "the note still promises a substitution"


def test_no_note_anywhere_promises_a_paid_substitution(client, ultra):
    """Said once for every lane on every screen, because this is the sentence
    that would cost money if it came back."""
    body = _models(client)
    for lane in LANES:
        for option in body[lane]["qualities"]:
            note = option.get("note") or ""
            assert "sẽ chạy Veo" not in note, f"{lane}/{option['value']}: {note}"
    for option in body["settingsOptions"]["veoModel"]:
        assert "sẽ chạy Veo" not in (option.get("note") or "")


def test_the_free_lane_is_marked_free_and_nothing_else_is(client, pro):
    """The one 0-credit key, read from the same table the review loop consults
    before it re-runs a clip without asking. Two answers to "is this free" is
    how the loop came to iterate on a paid lane."""
    qualities = {q["value"]: q for q in _models(client)["i2v"]["qualities"]}
    assert "Miễn phí" in (qualities["lite_relaxed"]["note"] or "")
    for lane_key in ("lite", "fast"):
        assert "Miễn phí" not in (qualities[lane_key]["note"] or "")


def test_a_servable_lane_carries_no_warning(client, pro):
    """Otherwise the warning becomes noise and gets ignored."""
    qualities = {q["value"]: q for q in _models(client)["i2v"]["qualities"]}
    assert qualities["lite"]["note"] is None
    assert qualities["fast"]["note"] is None


# ── the lanes, and which family can serve each ───────────────────────────

def test_text_to_video_offers_the_veo_lanes_again(client, pro):
    """The registry asserted the opposite this morning.

    `YhhmEf` was measured parsing a Veo key and objecting only to the plan
    (19/09/2026), so the lanes are real and are listed without a refusal note.
    Read from the SDK table rather than spelled out here, so a key corrected
    after a live refusal reaches the dropdown without a second edit.
    """
    qualities = {q["value"]: q for q in _models(client)["t2v"]["qualities"]}
    for lane in flow_sdk.VEO_T2V_LANES:
        assert lane in qualities
        note = qualities[lane]["note"] or ""
        assert "TỪ CHỐI" not in note, f"{lane} is servable and must not warn"
    # OMNI stays, and still says it costs money.
    assert qualities["omni"]["note"]
    # `quality` is the one label with no key behind it.
    assert "TỪ CHỐI" in qualities["quality"]["note"]


def test_the_free_text_to_video_queue_is_marked_free(client, pro):
    """`lite_relaxed` is the only unbilled text-to-video lane left.

    `fast_relaxed` was in this list for a few hours. All three of its names
    carry `_relaxed`, and the batch path answers `[5]` NOT_FOUND for every
    `_relaxed` name — measured across four RPCs on 19/09/2026. It is now offered
    with that reason rather than as a free lane that would never run.
    """
    qualities = {q["value"]: q for q in _models(client)["t2v"]["qualities"]}
    assert "Miễn phí" in (qualities["lite_relaxed"]["note"] or "")
    assert "TỪ CHỐI" in (qualities["fast_relaxed"]["note"] or "")


def test_only_omni_offers_the_ten_second_length(client, pro):
    """Veo text-to-video has 4/6/8 and no 10s key. A length that silently does
    nothing on the lane actually running is how 8s clips got ordered as 4."""
    durations = {d["value"]: d for d in _models(client)["t2v"]["durations"]}
    assert set(durations) == {4, 6, 8, 10}
    assert durations[10]["note"] and "OMNI" in durations[10]["note"]
    assert durations[8]["note"] is None


def test_start_end_is_omni_only(client, pro):
    """Omni gained first-to-last (`nprQif`) while Veo lost it: Veo's end-image
    slot was never captured. A Veo lane here would produce a clip that ignores
    the end frame and bills for it."""
    qualities = {q["value"]: q for q in _models(client)["startEnd"]["qualities"]}
    assert qualities["omni"]["note"]
    assert all(
        "TỪ CHỐI" in qualities[k]["note"]
        for k in qualities
        if k != "omni"
    )


def test_image_to_video_offers_every_key_the_sdk_accepts(client, pro):
    """Not a copy of the list: read from the SDK, so a key added there without
    a label here shows up as a failure rather than a missing dropdown entry."""
    qualities = {q["value"] for q in _models(client)["i2v"]["qualities"]}
    assert qualities >= set(flow_sdk.BATCH_VIDEO_LANES)
    assert "omni" in qualities


def test_a_lane_always_gets_at_least_one_duration(client, pro):
    body = _models(client)
    for lane in LANES:
        assert body[lane]["durations"], f"{lane} offers no duration at all"


def test_omni_durations_carry_their_credit_cost(client, pro):
    """OMNI bills by length, so the price belongs next to the choice."""
    for d in _models(client)["omni"]["durations"]:
        assert d["credits"] == flow_sdk.OMNI_FLASH_CREDIT_COST[d["value"]]


def test_veo_image_to_video_is_eight_seconds_and_omni_lengths_say_so(client, pro):
    """A Veo key carries its own length and only 8s exists. The other lengths
    are offered because the same dropdown drives the Omni lane, and each says
    which lane honours it — a duration control that silently does nothing is
    how 8-second clips got ordered as 4."""
    durations = {d["value"]: d for d in _models(client)["i2v"]["durations"]}
    assert durations[8]["note"] is None
    for length in (4, 6, 10):
        assert "OMNI" in durations[length]["note"]


# ── the tables themselves ────────────────────────────────────────────────

def test_image_models_come_from_the_backend_table(client, pro):
    values = {m["value"] for m in _models(client)["imageModels"]}
    assert values == set(flow_sdk.IMAGE_MODELS)


def test_every_image_nickname_maps_to_a_wire_id_flow_accepts(client, pro):
    """The nickname table and the closed wire-id set drifted once: a third
    model Flow would have run had no nickname anyone could pick."""
    from flowboard.services import flow_batch as fb

    assert set(flow_sdk.IMAGE_MODELS.values()) == set(fb.IMAGE_MODEL_WIRE_IDS)


def test_labels_are_present_for_every_option(client, pro):
    """A value with no label renders as a raw token in the UI."""
    body = _models(client)
    groups = [body["imageModels"], body["imageAspects"]]
    for lane in LANES:
        groups.extend([body[lane]["qualities"], body[lane]["aspects"]])
    groups.append(body["settingsOptions"]["resolution"])
    for group in groups:
        for option in group:
            assert option["label"], f"{option['value']} has no label"


def test_an_unset_tier_says_so_instead_of_claiming_pro(client, monkeypatch):
    """It used to fall back to "Pro", which put a plan on screen nobody chose.

    That fallback was harmless while the tier selected a checkpoint and the
    tables were per-tier; now it is just a claim. The lists must still be full,
    because a missing tier is an ordinary state and an empty UI is not.
    """
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(flow_client, "_paygate_tier", None, raising=False)
    body = _models(client)
    assert body["tier"] is None
    assert body["tierLabel"] == "Chưa chọn"
    assert body["i2v"]["qualities"]
    assert body["t2v"]["durations"]


def test_the_tier_changes_the_label_and_nothing_else(client, pro, ultra):
    """Pinned because the tier used to filter every list. If filtering ever
    returns, it has to be a deliberate change rather than a quiet one."""
    from flowboard.services.flow_client import flow_client

    as_ultra = _models(client)
    flow_client._paygate_tier = mod.TIER_ONE
    as_pro = _models(client)

    assert as_ultra["tierLabel"] == "Ultra"
    assert as_pro["tierLabel"] == "Pro"
    for lane in LANES:
        assert as_ultra[lane] == as_pro[lane]


def test_omni_resolution_is_offered_and_the_unpriced_one_says_so(client, pro):
    """360p exists in the builder and renders cheaper, but its credit cost has
    never been measured here — so it is offered without a price rather than
    with a guessed one."""
    options = {o["value"]: o for o in _models(client)["settingsOptions"]["resolution"]}
    assert set(options) == {"720p", "360p"}
    assert "chưa đo giá" in options["360p"]["label"]


def test_the_lengths_pro_was_measured_to_lack_say_so(client, pro):
    """Measured twice, on two transports, which is why it is in the UI.

    REST era: `veo_3_1_t2v_lite_4s` answered MODEL ACCESS DENIED while the 8s
    key worked. Batch path, 19/09/2026: the same key answered
    `PUBLIC_ERROR_MODEL_ACCESS_DENIED` while `veo_3_1_t2v_lite` produced a real
    8.00s clip. The entitlement followed the account across the migration.

    Still offered rather than hidden — another plan may hold them, and a refused
    dispatch costs nothing. What it must not do is look like a length that will
    simply work.
    """
    durations = {d["value"]: d for d in _models(client)["t2v"]["durations"]}
    for short in mod.T2V_SHORT_DURATIONS_DENIED_ON_PRO:
        note = durations[short]["note"] or ""
        assert "MODEL_ACCESS_DENIED" in note, short
        # And it names the way out, because OMNI does serve these lengths.
        assert "OMNI" in note
    assert durations[8]["note"] is None, "8s was measured working and must stay clean"


def test_no_option_list_in_the_registry_is_typed_out_by_hand(client, pro):
    """The last hand-written list, and why it mattered.

    `_omni_only_options` typed its lane tuple out and indexed
    `QUALITY_LABELS[key]` directly, so a lane added to the SDK without a label
    here would `KeyError` — turning `GET /api/models` into a 500 and taking every
    dropdown in the app with it. Latent, never fired; but this registry exists
    because twenty hand-copied lists drifted apart, and a list that can crash the
    endpoint is worse than one that merely drifts.
    """
    body = _models(client)
    sdk_lanes = set(flow_sdk.BATCH_VIDEO_LANES) | set(flow_sdk.REFUSED_VIDEO_LANES)
    for lane in ("startEnd", "omni"):
        offered = {q["value"] for q in body[lane]["qualities"]}
        assert sdk_lanes <= offered, (
            f"{lane} omits SDK lanes {sorted(sdk_lanes - offered)}"
        )


def test_a_lane_with_no_label_degrades_instead_of_500ing(client, pro, monkeypatch):
    """A missing label must cost one raw token in one dropdown, not the endpoint.

    Asked by adding a lane the SDK knows and the label table does not — which is
    exactly the drift the hand-typed list could not survive.
    """
    monkeypatch.setitem(flow_sdk.REFUSED_VIDEO_LANES, "brand_new_lane", "chưa đo")
    resp = client.get("/api/models")
    assert resp.status_code == 200, resp.text
    values = {q["value"] for q in resp.json()["startEnd"]["qualities"]}
    assert "brand_new_lane" in values
