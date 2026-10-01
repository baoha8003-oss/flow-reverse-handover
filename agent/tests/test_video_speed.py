"""Playing a clip faster, picture and sound together.

Every packaged workflow that sets `video_speed` asks for 1.2 or 1.25, and
this build ignored the key completely — so a board run produced a clip
noticeably longer than the same board through the packaged tool. It was one
of 48 settings keys the source never referenced.

Two traps live here, and both produce a plausible-looking file rather than
an error:

* `setpts` takes the RECIPROCAL of the speed (1.25x faster is
  `setpts=0.8*PTS`). Backwards, the clip comes out wrong by the square of
  the factor and still plays fine.
* `atempo` is capped at [0.5, 2.0]. Handed 3.0 it rejects the whole
  filtergraph, so anything past 2x has to be a chain whose factors multiply.
"""
from __future__ import annotations

import pytest

from flowboard.services import postprod
from flowboard.services.postprod import PostProdError, build_atempo_chain


def _product(parts: list[str]) -> float:
    total = 1.0
    for p in parts:
        total *= float(p.split("=", 1)[1])
    return total


# ── the atempo chain ──────────────────────────────────────────────────


@pytest.mark.parametrize("speed", [1.25, 1.2, 0.5, 2.0, 3.0, 4.0, 0.25, 0.75])
def test_the_chain_multiplies_out_to_the_speed_asked_for(speed):
    """The invariant that makes a chain correct. Anything else changes the
    clip's length by a factor nobody chose."""
    assert _product(build_atempo_chain(speed)) == pytest.approx(speed, rel=1e-4)


@pytest.mark.parametrize("speed", [1.25, 1.2, 2.0, 0.5])
def test_a_speed_within_range_needs_only_one_filter(speed):
    """Chaining when one filter would do spends an extra audio pass."""
    assert len(build_atempo_chain(speed)) == 1


def test_past_two_times_it_has_to_chain():
    """`atempo=3.0` is rejected by ffmpeg outright — the whole filtergraph
    fails, so this is not a quality question but a works-or-not one."""
    parts = build_atempo_chain(3.0)
    assert len(parts) > 1
    assert all(0.5 <= float(p.split("=")[1]) <= 2.0 for p in parts)


def test_below_half_speed_it_also_has_to_chain():
    parts = build_atempo_chain(0.25)
    assert len(parts) > 1
    assert all(0.5 <= float(p.split("=")[1]) <= 2.0 for p in parts)


def test_no_filter_is_ever_outside_what_ffmpeg_accepts():
    """Swept rather than spot-checked: one factor out of range fails the
    entire graph, so the boundary is the thing that matters."""
    for i in range(25, 401):
        speed = i / 100.0
        for part in build_atempo_chain(speed):
            factor = float(part.split("=", 1)[1])
            assert 0.5 <= factor <= 2.0, f"{speed}x produced {part}"


@pytest.mark.parametrize("bad", [0, -1, 0.1, 5.0, "1.25", True, None])
def test_a_speed_that_cannot_work_is_refused(bad):
    with pytest.raises(PostProdError):
        build_atempo_chain(bad)


# ── the video side, where the reciprocal lives ────────────────────────


def test_setpts_takes_the_reciprocal(monkeypatch, tmp_path):
    """THE trap. `setpts=1.25*PTS` on a 1.25x request makes the clip
    LONGER, and the result plays perfectly — nothing fails, the number is
    just wrong."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    seen: list[str] = []

    monkeypatch.setattr(postprod, "_run", lambda args, **kw: seen.extend(args))
    monkeypatch.setattr(postprod, "_source", lambda p: src)
    monkeypatch.setattr(postprod, "_target", lambda p: tmp_path / "out.mp4")
    monkeypatch.setattr(postprod, "has_audio", lambda p: True)

    postprod.change_speed(src, tmp_path / "out.mp4", speed=1.25)
    vf = seen[seen.index("-filter:v") + 1]
    factor = float(vf.split("=", 1)[1].split("*")[0])
    assert factor == pytest.approx(0.8, rel=1e-3), f"got {vf}, expected 0.8*PTS"


def test_a_silent_clip_is_not_given_an_audio_filter(monkeypatch, tmp_path):
    """Filtering a stream that is not there fails the whole command, so a
    clip with no audio has to take a different path."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    seen: list[str] = []

    monkeypatch.setattr(postprod, "_run", lambda args, **kw: seen.extend(args))
    monkeypatch.setattr(postprod, "_source", lambda p: src)
    monkeypatch.setattr(postprod, "_target", lambda p: tmp_path / "out.mp4")
    monkeypatch.setattr(postprod, "has_audio", lambda p: False)

    postprod.change_speed(src, tmp_path / "out.mp4", speed=1.25)
    assert "-filter:a" not in seen
    assert "-an" in seen


def test_an_impossible_speed_fails_the_same_way_silent_or_not(monkeypatch, tmp_path):
    """Validated before the command is assembled, so the error does not
    depend on whether the clip happens to carry audio."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(postprod, "_source", lambda p: src)
    monkeypatch.setattr(postprod, "_target", lambda p: tmp_path / "out.mp4")
    monkeypatch.setattr(postprod, "has_audio", lambda p: False)
    with pytest.raises(PostProdError):
        postprod.change_speed(src, tmp_path / "out.mp4", speed=9.0)


def test_speed_of_one_is_refused_rather_than_re_encoded(monkeypatch, tmp_path):
    """A full re-encode that changes nothing costs minutes and loses a
    generation of quality."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(postprod, "_source", lambda p: src)
    monkeypatch.setattr(postprod, "_target", lambda p: tmp_path / "out.mp4")
    with pytest.raises(PostProdError):
        postprod.change_speed(src, tmp_path / "out.mp4", speed=1.0)


# ── the planner puts it in the right place ────────────────────────────


def _chain(**settings):
    """The ops a `merge_video` node plans.

    `merge_video`, not `edit_video`: checked against the imported boards,
    the two nodes carrying `video_speed` are merges (1.2 and 1.25), while
    `edit_video` carries the differently-named `auto_video_speed`. The first
    version of this test asked the wrong node type and would have passed
    against a feature no workflow could trigger.
    """
    from types import SimpleNamespace

    from flowboard.services.postprod_plan import Upstream, ops_for

    node = SimpleNamespace(id=1, type="merge_video", data={"sourceSettings": settings})
    # Two clips: one is not a merge, and the concat would be skipped.
    clip = SimpleNamespace(
        id=2, type="video", data={"mediaIds": ["clip-a", "clip-b"]}
    )
    return [op["op"] for op in ops_for(node, [Upstream(clip, "media")])]


def test_the_workflows_speed_setting_now_reaches_the_chain():
    """1.25 as a float is what the imported boards actually carry."""
    assert "speed" in _chain(video_speed=1.25)


def test_the_retiming_happens_after_the_join():
    """Retiming each clip before concatenating would re-encode every one
    separately and compound the quality loss."""
    chain = _chain(video_speed=1.25)
    assert chain.index("concat") < chain.index("speed")


def test_speed_of_one_adds_no_pass():
    assert "speed" not in _chain(video_speed=1.0)


def test_no_speed_setting_adds_no_pass():
    assert "speed" not in _chain(enable_bgm=False)


def test_an_out_of_range_speed_is_skipped_not_crashed():
    """A workflow with a nonsense value should lose the retiming, not the
    whole edit."""
    assert "speed" not in _chain(video_speed=99.0)


def test_a_single_clip_merge_still_skips_everything():
    """One clip is not a merge; adding a retiming pass to a skipped concat
    would re-encode a clip nobody asked to change."""
    from types import SimpleNamespace

    from flowboard.services.postprod_plan import Upstream, ops_for

    node = SimpleNamespace(
        id=1, type="merge_video", data={"sourceSettings": {"video_speed": 1.25}}
    )
    one = SimpleNamespace(id=2, type="video", data={"mediaId": "only"})
    assert ops_for(node, [Upstream(one, "media")]) == []
