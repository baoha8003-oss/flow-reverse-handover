"""An unset video quality no longer lands on the priciest lane.

The module name records what used to be true and is kept so the change stays
visible instead of being tidied away. Measured before the September 2026
transport migration: `resolve_video_model(tier, aspect, "")` returned
`veo_3_1_i2v_s_fast_portrait` — it fell back to `fast`, a pricier lane than the
`lite` the tabs used to hardcode.

Two things changed. `DEFAULT_VIDEO_QUALITY` is now `lite`, chosen because `fast`
maps to the most expensive key this path accepts and a board that never chose a
lane should not be charged the most for it. And only three Veo keys are accepted
at all, so the aspect axis left the key table — aspect became its own payload
slot.

The frontend gate this module documents (`useModelsReady`, `ModelsBanner`) is
still load-bearing and must not be read as removable. What it protects has
narrowed: an empty quality is no longer the expensive answer, but an empty
aspect and an empty duration still are, and both travel through the same window
between mount and the `/api/models` fetch landing.
"""
from __future__ import annotations

from flowboard.services import flow_sdk

PORTRAIT = "VIDEO_ASPECT_RATIO_PORTRAIT"


def test_an_empty_quality_resolves_to_the_cheapest_paid_lane():
    """Not free — `lite` costs credits — but no longer the priciest key."""
    plan = flow_sdk.resolve_video_plan("", PORTRAIT)
    assert plan["error"] is None
    assert plan["model_key"] == flow_sdk.BATCH_VIDEO_LANES["lite"]

    fast = flow_sdk.resolve_video_plan("fast", PORTRAIT)["model_key"]
    assert plan["model_key"] != fast, "the default is the expensive key again"


def test_the_default_is_not_the_most_expensive_key_available():
    """The property, stated without naming which lane is default.

    Asserting `== "lite"` would still pass if `fast` were renamed to `lite`.
    What matters is that not choosing cannot reach anything costlier than the
    cheapest paid lane.
    """
    default_key = flow_sdk.BATCH_VIDEO_LANES[flow_sdk.DEFAULT_VIDEO_QUALITY]
    assert default_key != flow_sdk.BATCH_VIDEO_LANES["fast"]


def test_an_empty_aspect_fails_instead_of_guessing():
    """The other half, unchanged: aspect refuses rather than defaulting.

    A wrong aspect produces a correctly-billed video in the wrong shape, which
    is the failure nobody gets refunded for.
    """
    plan = flow_sdk.resolve_video_plan("lite", "")
    assert plan["model_key"] is None
    assert "invalid_aspect" in plan["error"]


def test_an_unknown_lane_is_refused_and_named():
    """Never folded onto a default. Upstream folds, and because its match is on
    the substring "ultra" the fold sends a 0-credit key to a paid one."""
    plan = flow_sdk.resolve_video_plan("turbo", PORTRAIT)
    assert plan["model_key"] is None
    assert "turbo" in plan["error"]
    assert plan["substitutions"] == []


def test_the_registry_never_offers_an_empty_value(client):
    """The gate protects the window before the fetch lands. After it lands, no
    option may itself be empty — that would put the same fallback one click away
    instead of one race away."""
    body = client.get("/api/models").json()
    groups = [body["imageModels"], body["imageAspects"]]
    for lane in ("t2v", "i2v", "startEnd", "omni"):
        groups.extend([body[lane]["qualities"], body[lane]["aspects"]])
    for group in groups:
        for option in group:
            assert option["value"].strip(), f"empty value in {option}"


def test_every_offered_lane_either_runs_or_says_why_not(client):
    """A lane listed with no note is a promise that it dispatches.

    This is the money-facing half of the registry. Before the migration an
    unavailable lane was offered with a note promising a paid substitution; now
    it is offered with a note promising a refusal. A lane offered with NO note
    has to be genuinely servable, or the dropdown is telling the user something
    the dispatch will contradict.
    """
    body = client.get("/api/models").json()
    servable = set(flow_sdk.BATCH_VIDEO_LANES) | {"omni"}
    for lane in ("t2v", "i2v", "startEnd", "omni"):
        for option in body[lane]["qualities"]:
            if option.get("note"):
                continue
            assert option["value"] in servable, (
                f"{lane}: {option['value']} is offered with no warning"
            )
