"""A generator wired to an empty upload must not spend credits.

Found on 2026-09-02 while setting up the first-ever pipeline run, before
any credit was spent. The packaged "Thời Trang Nữ Cầm Điện thoại" workflow
has an `image` node whose prompt reads "use image **nguoi_mau** as the base
and **san_pham** as the garment reference", fed by two `visual_asset` nodes
that ship with no media. Two things were wrong at once:

* `video` refused to dispatch without its upstream image; `image` dropped
  `ref_media_ids` from the params and dispatched anyway — a paid virtual
  try-on with no model photo and no garment photo.
* The cost preview reported that board as `notReadyJobs: 0`, because
  readiness was decided by prompt presence alone. The one screen whose job
  is to say what a run will cost said "ready" about a run that could only
  waste money.

Both sides are fixed from one helper, and the last test here is the one
that would catch them drifting apart again.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services.pipeline_executor import (
    _start_frame_media_id,
    missing_upload_ports,
    start_frame_unavailable,
)
from flowboard.services.postprod_plan import Upstream


def node(node_type, *, media=None, prompt=None):
    data = {}
    if media is not None:
        data["mediaId"] = media
    if prompt is not None:
        data["prompt"] = prompt
    return SimpleNamespace(id=1, type=node_type, data=data, short_id="x")


def wire(node_obj, port):
    return Upstream(node_obj, port)


# ── the case that would have cost money ───────────────────────────────


def test_an_empty_upload_is_reported():
    missing = missing_upload_ports(
        node("image", prompt="try this on"),
        [wire(node("visual_asset"), "image_1")],
    )
    assert missing == ["image_1"]


def test_every_empty_upload_is_reported_not_just_the_first():
    """The try-on needs BOTH photos. Naming one would send the user back
    to fill it in and hit the same wall again."""
    missing = missing_upload_ports(
        node("image", prompt="p"),
        [wire(node("visual_asset"), "image_1"), wire(node("visual_asset"), "image_2")],
    )
    assert sorted(missing) == ["image_1", "image_2"]


def test_a_filled_upload_is_not_reported():
    missing = missing_upload_ports(
        node("image", prompt="p"),
        [wire(node("visual_asset", media="m-123"), "image_1")],
    )
    assert missing == []


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_a_blank_media_id_counts_as_empty(empty):
    missing = missing_upload_ports(
        node("image", prompt="p"), [wire(node("visual_asset", media=empty), "image_1")]
    )
    assert missing == ["image_1"]


def test_a_character_upload_counts_too():
    """Character reference photos are supplied the same way and fail the
    same way."""
    missing = missing_upload_ports(
        node("image", prompt="p"), [wire(node("character"), "image_1")]
    )
    assert missing == ["image_1"]


# ── what must NOT be reported ─────────────────────────────────────────


def test_a_generator_upstream_is_never_missing():
    """THE constraint that keeps this from breaking every workflow. An
    `image` feeding a `video`'s start_frame has no mediaId before the run
    and a real one after it. Calling that "missing" would refuse to run the
    two-scene chains these templates are built from."""
    missing = missing_upload_ports(
        node("video", prompt="p"), [wire(node("image"), "start_frame")]
    )
    assert missing == []


def test_a_prompt_upstream_is_never_missing():
    missing = missing_upload_ports(
        node("image", prompt="p"), [wire(node("prompt", prompt="text"), "prompt")]
    )
    assert missing == []


def test_a_node_with_no_wires_is_ready():
    """A plain text-to-image node has no uploads to be missing. Reporting
    one would break the simplest case there is."""
    assert missing_upload_ports(node("image", prompt="p"), []) == []


def test_a_produced_media_upstream_is_never_missing():
    missing = missing_upload_ports(
        node("video", prompt="p"), [wire(node("extract_last_frame"), "start_frame")]
    )
    assert missing == []


# ── the start frame: what a video actually chains from ────────────────
#
# Counted on the imported boards: SIX video nodes could never find a start
# frame, because the lookup asked "is this upstream of type `image`?"
# instead of "what landed on the start-frame socket?". Four of them are the
# scene-chaining pattern every packaged workflow is built on.


def test_a_last_frame_node_can_start_the_next_scene():
    """The pattern the templates chain scenes with: video → extract the
    last frame → next video starts there. `extract_last_frame` writes a
    real mediaId when it finishes, but its type is not `image`, so the old
    type-based scan skipped it and the second scene died with
    `missing_upstream_image`."""
    src = node("extract_last_frame", media="frame-9")
    assert _start_frame_media_id([wire(src, "start_frame")], [src]) == "frame-9"


def test_an_uploaded_photo_can_start_a_video():
    """A `visual_asset` wired straight into start_frame — the user's own
    photo as the opening frame. Also invisible to the type-based scan."""
    src = node("visual_asset", media="upload-3")
    assert _start_frame_media_id([wire(src, "start_frame")], [src]) == "upload-3"


def test_a_generated_image_still_starts_a_video():
    """The case that always worked. It has to keep working."""
    src = node("image", media="img-1")
    assert _start_frame_media_id([wire(src, "start_frame")], [src]) == "img-1"


def test_a_hand_drawn_wire_with_no_port_name_still_works():
    """Boards built on the canvas carry no port names. Requiring one would
    break every board that was not imported from a template."""
    src = node("image", media="img-2")
    assert _start_frame_media_id([wire(src, None)], [src]) == "img-2"


def test_an_upstream_that_has_not_produced_yet_is_not_a_start_frame():
    src = node("extract_last_frame")
    assert _start_frame_media_id([wire(src, "start_frame")], [src]) is None


def test_a_prompt_wire_is_never_a_start_frame():
    """Text arrives on its own socket. Reading it as a frame would send a
    prompt id to the generator as an image."""
    src = node("prompt", prompt="hello")
    assert _start_frame_media_id([wire(src, "prompt")], [src]) is None


def test_the_start_frame_socket_wins_over_an_unrelated_image():
    """A board can have an image wired somewhere else entirely. The socket
    decides, not the first `image` in the upstream list."""
    other = node("image", media="not-this-one")
    frame = node("extract_last_frame", media="this-one")
    got = _start_frame_media_id(
        [wire(other, "media"), wire(frame, "start_frame")], [other, frame]
    )
    assert got == "this-one"


def test_no_upstream_at_all_is_no_start_frame():
    assert _start_frame_media_id([], []) is None


# ── wired-but-empty vs never-wired ────────────────────────────────────
#
# The two look identical to `_start_frame_media_id` (both give None) and
# mean opposite things. A socket wired to an empty upload says "start from
# this photo" — dispatching text-to-video there would charge for a clip the
# user did not ask for. No socket at all says "make a video from this
# prompt", which the worker has always supported and the canvas never used.


def test_a_wired_but_empty_start_frame_is_a_blocker():
    src = node("visual_asset")
    assert start_frame_unavailable(node("video", prompt="p"), [wire(src, "start_frame")])


def test_a_video_with_no_start_frame_wire_is_not_blocked():
    """It is a text-to-video. Blocking it made the simplest thing anyone can
    build on the canvas — a prompt and a video node — impossible."""
    assert not start_frame_unavailable(node("video", prompt="p"), [])


def test_a_prompt_only_upstream_still_means_text_to_video():
    src = node("prompt", prompt="hello")
    assert not start_frame_unavailable(node("video", prompt="p"), [wire(src, "prompt")])


def test_a_producer_that_has_not_run_yet_is_not_a_blocker():
    src = node("extract_last_frame")
    assert not start_frame_unavailable(
        node("video", prompt="p"), [wire(src, "start_frame")]
    )


def test_a_filled_upload_is_not_a_blocker():
    src = node("visual_asset", media="m-1")
    assert not start_frame_unavailable(
        node("video", prompt="p"), [wire(src, "start_frame")]
    )


def test_an_image_node_is_not_judged_on_start_frames():
    assert not start_frame_unavailable(node("image", prompt="p"), [])


def test_a_video_is_not_blocked_by_an_empty_character_reference():
    """The board that caught the first version being too broad: a video fed
    by a filled photo AND an empty `character` placeholder. A video never
    reads character refs — only its start frame, which was present."""
    filled = node("visual_asset", media="m-1")
    empty = node("character")
    wires = [wire(empty, None), wire(filled, None)]
    assert missing_upload_ports(node("video", prompt="p"), wires) == []
    assert not start_frame_unavailable(node("video", prompt="p"), wires)


def test_an_image_with_one_reference_present_still_runs():
    """With some references resolved, how many the prompt needs is a
    judgement the graph cannot make. Only "not one of them" is unambiguous."""
    filled = node("visual_asset", media="m-1")
    empty = node("visual_asset")
    got = missing_upload_ports(
        node("image", prompt="p"), [wire(filled, "image_1"), wire(empty, "image_2")]
    )
    assert got == []


# ── how long a node waits ─────────────────────────────────────────────


def test_the_node_wait_outlasts_the_worker_poll_ceiling():
    """Found by the first real pipeline run, which is the only way it could
    have been found: an 8-second clip was still generating at three minutes.

    The executor's wait was a flat 180s while the worker is allowed to poll
    Flow for 7s x 86 (~10 minutes, the packaged tool's own ceiling). So the
    executor stamped the node `timeout` and failed the run while the request
    kept going and Flow kept charging. A wait shorter than the operation it
    waits for saves nothing — it discards a result the user paid for and
    reports a failure that did not happen.
    """
    from flowboard.services.pipeline_executor import _default_request_timeout_s
    from flowboard.worker import processor

    worker_ceiling = processor.VIDEO_POLL_INTERVAL_S * processor.VIDEO_POLL_MAX_CYCLES
    assert _default_request_timeout_s() > worker_ceiling, (
        "the node gives up before the worker does"
    )


def test_the_wait_tracks_the_worker_knobs_rather_than_a_copied_number(
    monkeypatch,
):
    """Derived, not duplicated. A hardcoded 600 here would drift the moment
    someone tuned the worker's cadence — which is exactly how the 180 got
    stranded."""
    from flowboard.services.pipeline_executor import _default_request_timeout_s
    from flowboard.worker import processor

    monkeypatch.setattr(processor, "VIDEO_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(processor, "VIDEO_POLL_MAX_CYCLES", 2)
    assert _default_request_timeout_s() < 180.0


# ── the two sides agree ───────────────────────────────────────────────


def test_the_estimate_and_the_run_use_the_same_rule():
    """The defect was not that either side was wrong on its own — it was
    that they disagreed. The estimate said "ready", the executor dispatched,
    and only the credit balance knew. Both now call this one function, so
    this asserts the import rather than a second copy of the logic.
    """
    import inspect

    from flowboard.routes import estimate as estimate_mod

    source = inspect.getsource(estimate_mod.estimate_board)
    assert "missing_upload_ports" in source, (
        "the estimate stopped consulting the executor's readiness rule"
    )


def test_the_executor_refuses_to_dispatch_on_an_empty_upload():
    """Pins the guard itself. Without it the image branch falls through to
    `_create_request_row` and the credits are gone before anyone looks."""
    import inspect

    from flowboard.services import pipeline_executor

    source = inspect.getsource(pipeline_executor.run_pipeline)
    guard = source.index("missing_upload_ports")
    dispatch = source.index("_create_request_row")
    assert guard < dispatch, "the readiness check must run before the dispatch"
