"""A node's settings must reach the dispatch, and the estimate must read
the same ones.

`template_import` writes `sourceSettings`; the only reader was
`postprod_plan`, for post-production nodes. Nothing read it for `image` or
`video`, so `run_pipeline` dispatched `{prompt, project_id,
start_media_id}` and every imported workflow ran at Flow's defaults —
landscape, default duration, default model — however emphatically the
template asked for 9:16. `estimate._price_of` had the mirror of the same
blindness: it looked for camelCase keys on `data`, which imported nodes do
not carry, so every one of them priced as "unknown".

The values below are verbatim from the packaged workflows in this repo,
including the two spellings that break naive parsing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import node_settings as ns


def node(node_type="video", **data):
    return SimpleNamespace(id=1, type=node_type, data=data, short_id="x")


# ── the decoration is not the setting ─────────────────────────────────


@pytest.mark.parametrize(
    "label",
    [
        "📱 9:16",   # as the packaged workflows write it
        "?? 9:16",   # the same value with the emoji mangled upstream
        "9:16",      # and bare, which some nodes also carry
    ],
)
def test_the_same_ratio_survives_every_spelling_it_ships_with(label):
    """All three of these are present in the real templates. A parser
    matching whole strings would handle one and silently drop the rest —
    and a dropped ratio is a landscape video the user did not ask for."""
    got = ns.aspect_ratio({"ratio": label}, media="video")
    assert got == "VIDEO_ASPECT_RATIO_PORTRAIT"


def test_landscape_is_not_read_as_portrait():
    assert (
        ns.aspect_ratio({"ratio": "🖥️ 16:9"}, media="video")
        == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    )


def test_image_and_video_get_their_own_prefix():
    assert ns.aspect_ratio({"ratio": "9:16"}, media="image").startswith("IMAGE_")
    assert ns.aspect_ratio({"ratio": "9:16"}, media="video").startswith("VIDEO_")


def test_an_unmapped_ratio_is_none_rather_than_a_guess():
    assert ns.aspect_ratio({"ratio": "21:9 cinema"}, media="video") is None


def test_no_ratio_at_all_is_none():
    assert ns.aspect_ratio({}, media="video") is None


# ── duration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["⏱️ 8s", "?? 8s", "8s", "8"])
def test_duration_survives_its_spellings(label):
    assert ns.duration_s({"duration": label}) == 8


def test_a_missing_duration_is_not_defaulted_to_eight():
    """Duration selects the model key, so a silent 8s substitution bills a
    longer clip than was asked for. The worker already refuses to guess;
    this must not undo that by guessing earlier."""
    assert ns.duration_s({}) is None
    assert ns.duration_s({"duration": "some day"}) is None


def test_an_absurd_duration_is_rejected():
    assert ns.duration_s({"duration": "9000s"}) is None


# ── the lane, where a wrong read costs money ──────────────────────────


def test_lower_priority_is_the_relaxed_lane_not_plain_lite():
    """THE case this ordering exists for. Every packaged workflow uses
    "LITE LOWER priority", which is `lite_relaxed` — *Veo 3.1 - Lite
    [Lower Priority]* in routes/models.py. "lite lower priority" contains
    "lite", so testing the bare lanes first would classify a low-priority
    job as a normal Lite one."""
    assert ns.video_quality({"quality": "📉 LITE LOWER priority"}) == "lite_relaxed"
    assert ns.video_quality({"quality": "?? LITE LOWER priority"}) == "lite_relaxed"


def test_plain_lite_is_still_plain_lite():
    assert ns.video_quality({"quality": "Lite"}) == "lite"


def test_fast_lower_priority_is_the_relaxed_fast_lane():
    assert ns.video_quality({"quality": "FAST LOWER priority"}) == "fast_relaxed"


def test_omni_is_a_lane_too():
    """The only lane with a real price table. Leaving it out of the lane
    list made `_price_of` stop recognising OMNI, so the one thing the
    estimate could price started saying "unknown"."""
    assert ns.video_quality({"quality": "omni"}) == "omni"


def test_a_doubled_space_does_not_break_the_lane_match():
    """The one normalisation `_clean` still does, and the test that makes
    it load-bearing. An earlier version stripped every non-alphanumeric
    character too; a mutation showed that stripping was doing nothing —
    substring matching already tolerated the emoji — so it went."""
    assert ns.video_quality({"quality": "LITE  LOWER   priority"}) == "lite_relaxed"


def test_a_resolution_is_not_a_video_lane():
    """`quality: "🖼️ 1080"` is what image nodes carry in the same field.
    Forcing it into a Veo lane would dispatch the wrong model."""
    assert ns.video_quality({"quality": "🖼️ 1080"}) is None


# ── the canvas shape, which is not the template shape ─────────────────
#
# The Settings UI does not write labels. It writes the finished enum
# (`VIDEO_ASPECT_RATIO_PORTRAIT`), a number for duration, and the lane name
# itself. Every one of those was mishandled by a parser written only against
# the imported templates, and the worst of them was silent.


def test_a_canvas_lane_name_is_not_downgraded_to_a_charged_lane():
    """The money bug. `"lite_relaxed"` contains `"lite"`, and the relaxed
    needles are spelled with a space, so substring matching read the
    0-credit low-priority lane as the charged one."""
    assert ns.video_quality({"quality": "lite_relaxed"}) == "lite_relaxed"
    assert ns.video_quality({"quality": "fast_relaxed"}) == "fast_relaxed"


def test_a_canvas_duration_is_a_number_not_a_label():
    """`durationSeconds: 10`. Only strings reached the parser, so every
    canvas-built node lost its duration — and OMNI is priced by duration."""
    assert ns.duration_s({"duration": 10}) == 10


def test_a_canvas_duration_still_rejects_nonsense():
    assert ns.duration_s({"duration": 0}) is None
    assert ns.duration_s({"duration": 9000}) is None
    assert ns.duration_s({"duration": True}) is None


def test_an_already_canonical_ratio_passes_through():
    """The UI stores the enum, so re-deriving it from a label it never
    carries returned None and the run fell back to landscape."""
    got = ns.aspect_ratio({"ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"}, media="video")
    assert got == "VIDEO_ASPECT_RATIO_PORTRAIT"


# ── image model ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label,expected",
    [
        ("🍌 Banana pro", "NANO_BANANA_PRO"),
        ("🍌 Nano Banana pro", "NANO_BANANA_PRO"),
        ("🐒 Banana 2", "NANO_BANANA_2"),
    ],
)
def test_the_image_models_the_templates_actually_name(label, expected):
    assert ns.image_model({"model": label}) == expected


def test_an_unknown_model_falls_back_instead_of_being_forced():
    """"Banana LITE" ships in one template and has no counterpart in
    `flow_sdk.IMAGE_MODELS`. None means "let the SDK default"; mapping it
    to the nearest-looking entry would silently bill a different model."""
    assert ns.image_model({"model": "🍌 Banana LITE"}) is None


# ── what a dispatch receives ──────────────────────────────────────────


def test_an_imported_video_node_carries_its_settings_into_the_dispatch():
    """The whole defect in one assertion: this node is exactly what
    `template_import` writes, and every one of these keys used to be
    dropped."""
    n = node(
        "video",
        sourceSettings={
            "ratio": "📱 9:16",
            "duration": "⏱️ 8s",
            "quality": "📉 LITE LOWER priority",
        },
    )
    assert ns.for_dispatch(n) == {
        "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
        "duration_s": 8,
        "video_quality": "lite_relaxed",
    }


def test_an_imported_image_node_carries_its_model():
    n = node("image", sourceSettings={"ratio": "📱 9:16", "model": "🍌 Banana pro"})
    assert ns.for_dispatch(n) == {
        "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "image_model": "NANO_BANANA_PRO",
    }


def test_a_video_node_is_not_given_an_image_model():
    n = node("video", sourceSettings={"model": "🍌 Banana pro", "ratio": "9:16"})
    assert "image_model" not in ns.for_dispatch(n)


def test_an_unresolved_setting_is_absent_rather_than_none():
    """The result is splatted over the caller's params. A `None` value
    would overwrite a good default with nothing."""
    got = ns.for_dispatch(node("video", sourceSettings={"ratio": "9:16"}))
    assert got == {"aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"}
    assert all(v is not None for v in got.values())


def test_a_node_with_no_settings_dispatches_nothing_extra():
    assert ns.for_dispatch(node("video")) == {}


def test_what_the_user_set_on_the_canvas_beats_the_imported_value():
    """A template imported at 9:16 and then changed to 16:9 on the canvas
    has to run at 16:9."""
    n = node(
        "video",
        sourceSettings={"ratio": "📱 9:16"},
        aspectRatio="16:9",
    )
    assert ns.for_dispatch(n)["aspect_ratio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"


# ── the two readers agree ─────────────────────────────────────────────


def test_the_estimate_reads_the_same_settings_the_run_dispatches():
    """The split that has now appeared three times: planner and executor,
    estimate and executor, and this. Both sides go through one module, so
    the lane the dialog names is the lane the run uses."""
    import inspect

    from flowboard.routes import estimate as estimate_mod
    from flowboard.services import pipeline_executor

    assert "node_settings" in inspect.getsource(estimate_mod._price_of)
    assert "node_settings.for_dispatch" in inspect.getsource(
        pipeline_executor.run_pipeline
    )


def test_the_estimate_names_the_lane_it_cannot_price():
    """"Chưa có bảng giá công bố cho lane này" on every node said nothing
    about which lane. No Veo price table is invented — the relaxed lanes
    are 0-credit only on image-to-video and only on Ultra, and quoting 0
    from a rule with unverified exceptions would under-quote — but the
    lane itself is knowable and worth saying.

    The family is now stated rather than assumed. `lite_relaxed` is free on
    image-to-video and NOT free on text-to-video — the free key lives in
    `BATCH_VIDEO_LANES`, while `resolve_t2v_plan` returns
    `veo_3_1_t2v_lite_*_low_priority`, which `is_zero_credit_model` does not call
    free. Reading one table for every family quoted 0 for a dispatch the retry
    policy treats as paid.
    """
    from flowboard.routes.estimate import _price_of

    n = node("video", sourceSettings={"quality": "📉 LITE LOWER priority"})
    price, note = _price_of(n, has_characters=False, has_start_frame=True)
    # Zero, not unknown. The lane maps to the one model key Google runs free,
    # and it is read from `ZERO_CREDIT_MODEL_KEYS` -- the same table the review
    # loop consults before it re-runs a clip without asking. Two answers to
    # "is this free" is how the loop came to iterate on a paid lane.
    assert price == 0
    assert "0 credit" in note

    # A paid Veo lane still has no published price, and says which lane it was.
    paid = node("video", sourceSettings={"quality": "FAST"})
    paid_price, paid_note = _price_of(
        paid, has_characters=False, has_start_frame=True
    )
    assert paid_price is None
    assert "Fast" in paid_note


def test_the_same_lane_is_not_quoted_free_on_text_to_video():
    """The half the single-table read got wrong.

    `lite_relaxed` on a bare clip dispatches `veo_3_1_t2v_lite_*_low_priority`,
    which is not in `ZERO_CREDIT_MODEL_KEYS` and which `review_loop` will not
    auto-retry. Quoting it at 0 from the image-to-video key meant the dialog said
    free while the retry policy said paid — the two answers `_price_of`'s own
    docstring claims cannot drift apart.
    """
    from flowboard.routes.estimate import _price_of

    n = node("video", sourceSettings={"quality": "📉 LITE LOWER priority",
                                      "duration": "8s"})
    price, _ = _price_of(n, has_characters=False, has_start_frame=False)
    assert price != 0


# ── Omni's resolution ─────────────────────────────────────────────────


def test_the_resolution_reaches_a_video_dispatch():
    """New with the batch transport: Omni takes a resolution per request, and
    the model key embeds it. Before this the key existed in the builder and
    nothing could choose it."""
    n = node("video", resolution="360p")
    assert ns.for_dispatch(n)["resolution"] == "360p"


def test_a_label_around_the_resolution_still_reads():
    """The packaged tool writes decorated strings for every other setting, so
    assume it will here too."""
    from flowboard.services.node_settings import resolution

    assert resolution({"resolution": "HD 720p"}) == "720p"
    assert resolution({"video_resolution": "360P"}) == "360p"


def test_an_unusable_resolution_is_dropped_rather_than_guessed():
    """Flow accepts 360p or 720p and nothing else. The packaged tool's own
    config ships 480p, so this arrives in practice — and coercing it to the
    nearest would bill the more expensive render."""
    from flowboard.services.node_settings import resolution

    assert resolution({"resolution": "480p"}) is None
    assert resolution({}) is None


def test_no_resolution_is_sent_when_nothing_chose_one():
    """None, not "720p": the SDK owns that default, and a second copy here is
    how the estimate and the dispatch come to disagree about what rendered."""
    assert "resolution" not in ns.for_dispatch(node("video"))


def test_an_image_node_never_carries_a_resolution():
    """It is a video concept. A key the handler ignores reads as a setting that
    did something."""
    assert "resolution" not in ns.for_dispatch(node("image", resolution="360p"))


def test_the_clip_review_loop_can_be_switched_on_from_a_node():
    """The whole loop was unreachable, and nothing said so.

    `review_loop.settings_from` reads `review_loop` / `review_threshold` /
    `review_max_rounds` off `merged_settings`, and none of the three were in that
    function's allow-list — so `enabled` was False for every node on every board,
    no matter what anyone set. `estimate._review_loop_calls` therefore always
    counted zero, and the money rules built around the loop (`is_free_dispatch`,
    the `reviewJobsMax` ceiling) were guarding a path nobody could reach.

    Both spellings, because the canvas writes camelCase and the reader wants
    snake_case: a checkbox alone would have set a key nothing read.
    """
    from flowboard.services import node_settings, review_loop

    for data in (
        {"review_loop": True, "review_threshold": 7.5, "review_max_rounds": 3},
        {"reviewLoop": True, "reviewThreshold": 7.5, "reviewMaxRounds": 3},
        {"sourceSettings": {"review_loop": True, "review_threshold": 7.5,
                            "review_max_rounds": 3}},
    ):
        s = review_loop.settings_from(node_settings.merged_settings(node("video", **data)))
        assert s.enabled is True, data
        assert s.threshold == 7.5, data
        assert s.max_rounds == 3, data


def test_the_review_loop_stays_off_when_nobody_asked():
    """Off unless asked for — a loop that ran by default would spend a vision call
    per clip on every board that never opted in."""
    from flowboard.services import node_settings, review_loop

    s = review_loop.settings_from(node_settings.merged_settings(node("video")))
    assert s.enabled is False
