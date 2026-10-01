"""The P8 settings, where they stop being plan entries and become ffmpeg.

Every test here reaches the filter graph or the handler, because that is where
these four faults lived and every one of them failed the whole `edit_video`
node — the plan layer was correct and the tests stopped there:

* `mix_bgm` faded the part of the track it was about to trim away;
* a `🔍 x2` node emitted `scale: 2`, which the engine refuses outright;
* `upscale_resolution` arrives as `'2K'` / `'4K'` and the int-only check
  dropped it, so the handler's default of 4 enlarged 4x instead;
* `voice_speed` was forwarded unbounded, so a template saying 5.0 killed the
  edit rather than being skipped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flowboard.services import postprod, upscale


# ── mix_bgm: trim, then fade ──────────────────────────────────────────


def _graph(monkeypatch, **kw) -> str:
    """The filter graph `mix_bgm` would run, without running it."""
    seen: dict[str, list[str]] = {}

    def _fake_run(args, **_):
        seen["args"] = [str(a) for a in args]
        Path(args[args.index("-y") + 1] if False else kw["dst"]).write_bytes(b"\x00")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(postprod, "probe_duration", lambda p: 30.0)
    monkeypatch.setattr(postprod, "has_audio", lambda p: True)
    monkeypatch.setattr(postprod, "_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(postprod.subprocess, "run", _fake_run)

    postprod.mix_bgm(
        kw["src"], kw["bgm"], kw["dst"],
        fade_in=kw.get("fade_in", 0.0),
        fade_out=kw.get("fade_out", 0.0),
        track_start=kw.get("track_start", 0.0),
    )
    args = seen["args"]
    return args[args.index("-filter_complex") + 1]


@pytest.fixture
def clips(tmp_path):
    src = tmp_path / "in.mp4"
    bgm = tmp_path / "music.mp3"
    for f in (src, bgm):
        f.write_bytes(b"\x00" * 64)
    return {"src": src, "bgm": bgm, "dst": tmp_path / "out.mp4"}


def test_the_trim_comes_before_the_fades(monkeypatch, clips):
    """Faded first, the fade-in landed at second 0 of the TRACK — inside the
    intro `atrim` then cut away — and the music opened at full volume."""
    graph = _graph(monkeypatch, **clips, fade_in=2.0, fade_out=3.0, track_start=20.0)
    music = graph.split("[1:a]")[1]
    assert music.index("atrim") < music.index("afade"), music


def test_the_fade_out_is_placed_on_the_excerpt_not_the_track(monkeypatch, clips):
    """30s clip, 3s fade: the fade belongs at 27s of the excerpt, whatever
    offset the excerpt was taken from."""
    graph = _graph(monkeypatch, **clips, fade_out=3.0, track_start=20.0)
    assert "afade=t=out:st=27.000:d=3.0" in graph, graph


def test_the_offset_still_reaches_the_trim(monkeypatch, clips):
    graph = _graph(monkeypatch, **clips, track_start=20.0)
    assert "atrim=20.000:50.000" in graph, graph


def test_no_offset_no_change(monkeypatch, clips):
    graph = _graph(monkeypatch, **clips, fade_in=1.0)
    assert "atrim=0.000:30.000" in graph
    assert "afade=t=in:st=0:d=1.0" in graph


# ── the multiplier becomes a resample target ──────────────────────────


@pytest.mark.parametrize("height,factor,expected", [
    (1080, 2.0, 2160),
    (720, 2.0, 1440),
    (1080, 3.0, 3240),
    (1081, 2.0, 2162),   # rounded up to even: H.264 needs it
])
def test_a_multiplier_resolves_against_the_source(monkeypatch, tmp_path, height, factor, expected):
    """The bundled models upscale by exactly 4x and `_validate` refuses any
    other scale, so x2 is a resample target — and only the source says what
    height that is."""
    monkeypatch.setattr(upscale, "_probe_height", lambda src: height)
    assert upscale.height_for_scale(tmp_path / "x.mp4", factor) == expected


def test_the_handler_turns_targetScale_into_a_height(monkeypatch, tmp_path):
    """End to end through the op, because the plan layer emitting the right
    key was never the part that was broken."""
    from flowboard.worker import postprod_handler as h

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    seen: dict = {}

    def _fake_upscale_video(source, dst, scale, model, target_height, **kw):
        seen.update(scale=scale, model=model, target_height=target_height)
        Path(dst).write_bytes(b"\x00")
        return Path(dst)

    monkeypatch.setattr(upscale, "upscale_video", _fake_upscale_video)
    monkeypatch.setattr(upscale, "_probe_height", lambda s: 1080)
    monkeypatch.setattr(h, "_resolve_media", lambda p, k: (src, None))
    monkeypatch.setattr(h, "_ingest", lambda *a, **kw: {"ok": True})

    import asyncio

    result, err = asyncio.run(h._op_upscale({"source": "m-1", "targetScale": 2.0}))
    assert err is None and result == {"ok": True}
    # The model still runs at the rate it was trained for.
    assert seen["scale"] == 4
    assert seen["target_height"] == 2160


def test_an_explicit_height_is_used_as_given(monkeypatch, tmp_path):
    from flowboard.worker import postprod_handler as h

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    seen: dict = {}

    def _fake_upscale_video(source, dst, scale, model, target_height, **kw):
        seen["target_height"] = target_height
        Path(dst).write_bytes(b"\x00")
        return Path(dst)

    monkeypatch.setattr(upscale, "upscale_video", _fake_upscale_video)
    monkeypatch.setattr(h, "_resolve_media", lambda p, k: (src, None))
    monkeypatch.setattr(h, "_ingest", lambda *a, **kw: {"ok": True})

    import asyncio

    asyncio.run(h._op_upscale({"source": "m-1", "targetHeight": 1440}))
    assert seen["target_height"] == 1440
