"""MI-GAN as the third chance at removing a watermark.

The bundled GPU tool goes first and is much better at this. But it declines —
measured on 2026-09-02, its still-image sibling answered "No watermark
detected (3%)" on a frame that plainly carried a Veo logo — and "not detected"
is not the same as "clean".

Every number in the module under test was measured against the model rather
than assumed, and the two that shape the design are pinned here:

* **Mask polarity: 0 is the hole.** The one convention that could not be read
  off the file. Wrong, and the module would paint out everything EXCEPT the
  watermark.
* **The model perturbs what it is given, everywhere.** Up to 24 levels across
  a whole frame. That is why the work happens on a tile and only the hole is
  written back — and why "the rest of the frame is untouched" is the property
  worth a test of its own.
"""
from __future__ import annotations

import pytest

from flowboard.services import inpaint

np = pytest.importorskip("numpy", reason="the inpaint extra is not installed")
pytest.importorskip("onnxruntime", reason="the inpaint extra is not installed")


# ── availability is two separate facts ────────────────────────────────


def test_the_model_and_the_runtime_are_reported_apart(monkeypatch):
    """"No model" and "no onnxruntime" have different fixes, and a single
    boolean would send the reader to guess which."""
    monkeypatch.setattr(inpaint, "model_path", lambda: None)
    assert inpaint.available() is False
    assert "migan" in inpaint.unavailable_reason().lower()

    monkeypatch.setattr(inpaint, "model_path", lambda: __import__("pathlib").Path(__file__))
    monkeypatch.setattr(inpaint, "runtime_available", lambda: False)
    assert inpaint.available() is False
    assert "onnxruntime" in inpaint.unavailable_reason()


def test_nothing_missing_means_no_reason(monkeypatch):
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")
    assert inpaint.unavailable_reason() is None


# ── the box ───────────────────────────────────────────────────────────


def test_percentages_become_pixels_against_the_real_frame():
    """The packaged tool stores the box as percentages because it does not
    know the frame size where the setting lives."""
    box = inpaint.Box.from_pct(
        x_pct=78.0, y_pct=88.0, w_pct=16.0, h_pct=6.0, width=1280, height=720
    )
    assert (box.x, box.y) == (998, 634)
    assert (box.w, box.h) == (205, 43)


def test_a_box_running_off_the_frame_is_clamped_not_rejected():
    """A workflow written for another aspect ratio is worth clamping into
    range — refusing would drop the removal over a rounding difference."""
    box = inpaint.Box.from_pct(
        x_pct=95.0, y_pct=95.0, w_pct=20.0, h_pct=20.0, width=100, height=100
    )
    assert box.x + box.w <= 100 and box.y + box.h <= 100
    assert box.w >= 1 and box.h >= 1


def test_a_zero_sized_box_still_has_a_pixel():
    box = inpaint.Box.from_pct(
        x_pct=10.0, y_pct=10.0, w_pct=0.0, h_pct=0.0, width=640, height=480
    )
    assert box.w == 1 and box.h == 1


# ── the tile window ───────────────────────────────────────────────────


def test_the_tile_is_kept_inside_the_frame():
    """Padding a tile that runs off the edge would feed the model black
    pixels it would then treat as real context, and the fill would lean
    towards them."""
    box = inpaint.Box(x=1200, y=690, w=60, h=20)
    x0, y0, w, h = inpaint._tile_bounds(box, 1280, 720)
    assert x0 >= 0 and y0 >= 0
    assert x0 + w <= 1280 and y0 + h <= 720


def test_a_frame_smaller_than_a_tile_uses_the_whole_frame():
    x0, y0, w, h = inpaint._tile_bounds(inpaint.Box(10, 10, 5, 5), 200, 300)
    assert (x0, y0) == (0, 0) and (w, h) == (200, 200)


def test_the_tile_is_centred_on_the_mark():
    box = inpaint.Box(x=600, y=300, w=40, h=40)
    x0, y0, w, h = inpaint._tile_bounds(box, 1280, 720)
    assert x0 < box.x and x0 + w > box.x + box.w
    assert y0 < box.y and y0 + h > box.y + box.h


# ── the piece grid ────────────────────────────────────────────────────


def test_a_box_that_fits_is_one_piece():
    assert inpaint._pieces(inpaint.Box(100, 100, 200, 80)) == [
        inpaint.Box(100, 100, 200, 80)
    ]


def test_a_box_too_wide_for_a_tile_is_split():
    """The write-back used to be clamped to the tile, so a box wider than 512
    came back PARTLY PAINTED and reported clean. 20% of a 4K width is 768."""
    pieces = inpaint._pieces(inpaint.Box(200, 300, 760, 120))
    assert len(pieces) == 3
    assert all(p.w <= inpaint.MAX_HOLE for p in pieces)


def test_the_pieces_cover_the_box_exactly():
    """No gap (a stripe of mark survives) and no overlap that misses the
    corner: the grid is a partition, and the union has to be the box."""
    box = inpaint.Box(40, 50, 700, 620)
    covered = np.zeros((box.h, box.w), dtype=int)
    for p in inpaint._pieces(box):
        covered[p.y - box.y:p.y - box.y + p.h, p.x - box.x:p.x - box.x + p.w] += 1
    assert covered.min() == 1 and covered.max() == 1


def test_a_tall_box_splits_by_rows_too():
    pieces = inpaint._pieces(inpaint.Box(0, 0, 100, 900))
    assert len(pieces) == 4
    assert all(p.h <= inpaint.MAX_HOLE for p in pieces)


# ── the blend ring ────────────────────────────────────────────────────


def test_the_hole_is_replaced_whole_and_the_ramp_is_outside_it():
    """This is the fix, stated as a property. The ramp used to live INSIDE
    the hole, so the outermost pixels of the box kept most of their original
    value — and those are the pixels the mark's own edge sits on. The mark
    came back as a faint outline of itself."""
    pad = (6, 6, 6, 6)
    alpha = inpaint._blend_alpha(20, 30, pad, np)
    assert alpha.shape == (32, 42, 1)
    # The hole: every pixel of it, fully the model's output.
    hole = alpha[6:26, 6:36, 0]
    assert float(hole.min()) == pytest.approx(1.0)
    # The ring: a ramp down, never reaching the picture beyond it abruptly.
    assert alpha[0, 21, 0] < 0.2
    assert alpha[5, 21, 0] > 0.7


def test_a_hole_against_the_tile_edge_has_no_ring_on_that_side():
    """Nothing to ramp across there, and the model's own output continues
    past the hole anyway."""
    alpha = inpaint._blend_alpha(10, 10, (0, 6, 0, 6), np)
    assert alpha.shape == (16, 16, 1)
    assert float(alpha[0, 0, 0]) == pytest.approx(1.0)


# ── the property the whole design exists for ──────────────────────────


def test_everything_outside_the_hole_and_its_ring_is_bit_identical():
    """Measured: handing the model a whole frame drifts every pixel by up to
    24 levels. Paying that across the picture to clean one corner is the
    trade this module refuses to make.

    The region is the box, grown by GROW because the model leaves the last
    couple of pixels of a hole where they were, plus FEATHER of cross-fade.
    Everything further out is untouched to the bit.
    """
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")

    rng = np.random.default_rng(11)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    box = inpaint.Box.from_pct(
        x_pct=78.0, y_pct=88.0, w_pct=16.0, h_pct=6.0, width=1280, height=720
    )
    out = inpaint.clean_frame(frame, box)

    edge = inpaint.GROW + inpaint.FEATHER
    outside = np.ones((720, 1280), bool)
    outside[box.y - edge:box.y + box.h + edge, box.x - edge:box.x + box.w + edge] = False
    assert np.array_equal(out[outside], frame[outside])


def test_the_mark_does_not_survive_at_the_edge_of_its_own_box():
    """Measured before the fix: 224 of the original 255 left on the rim of
    the box, because the model does not repaint the last pixels of what it is
    given as hole and the ramp then kept them. Growing the hole clears it —
    9 at GROW=3, unchanged at 6 and 8."""
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :, 2] = 200
    box = inpaint.Box(x=1000, y=40, w=200, h=80)
    frame[box.y:box.y + box.h, box.x:box.x + box.w] = (255, 0, 0)

    out = inpaint.clean_frame(frame, box)
    rim = out[box.y:box.y + box.h, box.x:box.x + box.w, 0]
    assert int(rim.max()) < 60, "the mark's own edge came back"


def test_a_box_wider_than_a_tile_is_cleaned_all_the_way_across():
    """The D.2.18 case end to end: 760 px of mark on a 1280 frame. Painted in
    one tile it came back with 254 of 255 still there past the tile's reach —
    and reported as done."""
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:, :, 1] = 180
    box = inpaint.Box(x=200, y=300, w=760, h=120)
    frame[box.y:box.y + box.h, box.x:box.x + box.w] = (255, 0, 0)

    out = inpaint.clean_frame(frame, box)
    hole = out[box.y:box.y + box.h, box.x:box.x + box.w, 0]
    assert int(hole.max()) < 60, "part of the mark was outside the single tile"


def test_the_hole_actually_changes():
    """The other half. Preserving everything is easy; the point is that the
    watermark is gone."""
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")

    frame = np.zeros((512, 512, 3), dtype=np.uint8)
    frame[:, :, 2] = 200
    box = inpaint.Box(x=200, y=200, w=100, h=100)
    frame[box.y:box.y + box.h, box.x:box.x + box.w] = (255, 0, 0)

    out = inpaint.clean_frame(frame, box)
    red_before = frame[box.y + 50, box.x + 50, 0]
    red_after = out[box.y + 50, box.x + 50, 0]
    assert red_before == 255
    assert red_after < 128, "the mark survived the paint"


def test_the_input_frame_is_not_modified():
    """Callers reuse the buffer ffmpeg wrote into; mutating it would corrupt
    the frame that goes to the encoder."""
    if not inpaint.available():
        pytest.skip("the packaged model is not on this machine")

    frame = np.full((512, 512, 3), 90, dtype=np.uint8)
    keep = frame.copy()
    inpaint.clean_frame(frame, inpaint.Box(100, 100, 40, 40))
    assert np.array_equal(frame, keep)


# ── refusing rather than running for an hour ──────────────────────────


def test_a_wide_box_counts_its_pieces_against_the_ceiling(tmp_path, monkeypatch):
    """500 frames is well under the 1200 ceiling; 500 frames x 3 pieces is
    not. Counted in frames, a grid ran for three times the time it quoted."""
    monkeypatch.setattr(inpaint, "_frame_size", lambda src: (1280, 720))
    monkeypatch.setattr(inpaint, "_frame_count", lambda src: 500)
    monkeypatch.setattr(inpaint, "unavailable_reason", lambda: None)

    src = tmp_path / "wide.mp4"
    src.write_bytes(b"\x00" * 16)
    with pytest.raises(inpaint.InpaintError) as exc:
        inpaint.inpaint_video(
            src, tmp_path / "out.mp4",
            {"xPct": 10.0, "yPct": 10.0, "widthPct": 60.0, "heightPct": 10.0},
        )
    assert "1500" in str(exc.value) and "ô" in str(exc.value)


def test_a_long_clip_is_refused_with_the_time_it_would_take(tmp_path, monkeypatch):
    """At the measured ~0.7s a frame, a long clip stops being a fallback.
    The number belongs in the message — "too long" alone tells nobody what
    to do."""
    monkeypatch.setattr(inpaint, "_frame_size", lambda src: (1280, 720))
    monkeypatch.setattr(inpaint, "_frame_count", lambda src: 5000)
    monkeypatch.setattr(inpaint, "unavailable_reason", lambda: None)

    src = tmp_path / "long.mp4"
    src.write_bytes(b"\x00" * 16)
    with pytest.raises(inpaint.InpaintError) as exc:
        inpaint.inpaint_video(src, tmp_path / "out.mp4", {"xPct": 1.0})
    assert "5000" in str(exc.value) and "phút" in str(exc.value)


def test_running_without_the_extra_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setattr(inpaint, "runtime_available", lambda: False)
    src = tmp_path / "x.mp4"
    src.write_bytes(b"\x00" * 16)
    with pytest.raises(inpaint.InpaintError) as exc:
        inpaint.inpaint_video(src, tmp_path / "out.mp4", {"xPct": 1.0})
    assert "inpaint" in str(exc.value)
