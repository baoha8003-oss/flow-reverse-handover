"""Real word timings, from the transcriber to the burned frame.

`karaoke.py`'s own docstring names the condition for replacing its
length-proportional approximation: "a trustworthy word-timing source". P4 built
two of them and `srt_to_ass` grew an `aligned=` parameter to take them. Nothing
connected the two: the transcribe pass never asked for words, and only the SRT
crossed to the burn, so every karaoke track in the product was still the
approximation — with nine tests covering machinery no board could reach.

These tests go through the ops, because the gap was between them.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from flowboard.services import karaoke, stt
from flowboard.worker import postprod_handler as h


# ── the plan asks for words only when it needs them ───────────────────


@pytest.fixture(autouse=True)
def _a_transcriber_exists(monkeypatch):
    """The plan skips subtitles entirely when no source can transcribe, which
    on a machine without a Gemini key would make every test below vacuous."""
    monkeypatch.setattr(stt, "sources", lambda: ["local"])


def _transcribe_op(**settings):
    from types import SimpleNamespace

    from flowboard.services import postprod_plan as pp

    base = {"enable_sub": True}
    base.update(settings)
    node = SimpleNamespace(type="edit_video", data={"sourceSettings": base})
    media = SimpleNamespace(type="video", data={"mediaId": "m-1"})
    ops = pp.ops_for(node, [pp.Upstream(media, "media")], assume_ready=True)
    return next((o for o in ops if o["op"] == "transcribe"), None)


def test_a_karaoke_burn_asks_the_transcriber_for_words():
    """`want_words` is what makes `stt` drop Gemini from the chain — it was
    measured inventing timestamps — and use a source that can actually align."""
    op = _transcribe_op(sub_style_type="Karaoke ASS")
    assert op is not None and op.get("wantWords") is True


def test_a_static_burn_does_not():
    """A plain track has no use for word timings, and asking would give up
    Gemini's price advantage for nothing."""
    op = _transcribe_op(sub_style_type="Chuẩn")
    assert op is not None and "wantWords" not in op


# ── the words survive the gap between the two ops ─────────────────────


@pytest.fixture
def ops_env(monkeypatch, tmp_path):
    """The two ops, with the transcriber and ffmpeg stubbed out."""
    outputs: list = []

    def _new_output(suffix: str):
        path = tmp_path / f"out-{len(outputs)}{suffix}"
        outputs.append(path)
        return path

    monkeypatch.setattr(h, "_new_output", _new_output)
    monkeypatch.setattr(h, "_ingest", lambda out, **kw: {"path": str(out)})
    return {"tmp": tmp_path, "outputs": outputs}


SRT = "1\n00:00:00,000 --> 00:00:02,000\nxin chào các bạn\n"


async def _transcribe(monkeypatch, ops_env, words, *, want_words=True):
    src = ops_env["tmp"] / "clip.mp4"
    src.write_bytes(b"\x00" * 32)
    seen: dict = {}

    async def _to_srt(video, *, language=None, storage_dir=None, timeout=240.0,
                      want_words=False):
        seen["want_words"] = want_words
        return stt.Transcript(srt=SRT, words=words, source="local")

    monkeypatch.setattr(stt, "to_srt", _to_srt)
    monkeypatch.setattr(h, "_resolve_media", lambda p, k: (src, None))
    result, err = await h._op_transcribe(
        {"video": "m-1", **({"wantWords": True} if want_words else {})}
    )
    assert err is None, err
    return seen, result


def test_the_words_land_beside_the_srt(monkeypatch, ops_env):
    words = [stt.Word(text="xin", start=0.0, end=0.4),
             stt.Word(text="chào", start=0.4, end=0.9)]
    seen, result = asyncio.run(_transcribe(monkeypatch, ops_env, words))

    assert seen["want_words"] is True
    sidecar = __import__("pathlib").Path(result["path"] + ".words.json")
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[0]["text"] == "xin"


def test_no_words_no_sidecar(monkeypatch, ops_env):
    _, result = asyncio.run(_transcribe(monkeypatch, ops_env, [], want_words=False))
    assert not __import__("pathlib").Path(result["path"] + ".words.json").exists()


def test_the_burn_reads_them_back(monkeypatch, ops_env):
    """The join that was missing. Without it `srt_to_ass` received
    `aligned=None` on every run and the real timings were thrown away after
    being paid for."""
    words = [stt.Word(text="xin", start=0.0, end=0.4),
             stt.Word(text="chào", start=0.4, end=0.9),
             stt.Word(text="các", start=0.9, end=1.3),
             stt.Word(text="bạn", start=1.3, end=2.0)]
    _, result = asyncio.run(_transcribe(monkeypatch, ops_env, words))
    srt_path = __import__("pathlib").Path(result["path"])
    srt_path.write_text(SRT, encoding="utf-8-sig")

    got: dict = {}
    real_srt_to_ass = karaoke.srt_to_ass

    def _spy(srt, dst, *, aligned=None, **style):
        got["aligned"] = aligned
        return real_srt_to_ass(srt, dst, aligned=aligned, **style)

    monkeypatch.setattr(karaoke, "srt_to_ass", _spy)
    monkeypatch.setattr(h.postprod, "frame_size", lambda v: (1080, 1920))
    monkeypatch.setattr(h.postprod, "burn_ass", lambda *a, **kw: a[1])
    monkeypatch.setattr(
        h, "_resolve_media",
        lambda p, k: (srt_path if k == "srt" else ops_env["tmp"] / "clip.mp4", None),
    )

    asyncio.run(h._op_karaoke({"video": "m-1", "srt": "m-2"}))
    assert got["aligned"] is not None
    assert [w.text for w in got["aligned"]] == ["xin", "chào", "các", "bạn"]


def test_a_missing_sidecar_still_burns(monkeypatch, ops_env):
    """The approximation remains the fallback: a source with no alignment must
    not lose its subtitles."""
    srt_path = ops_env["tmp"] / "only.srt"
    srt_path.write_text(SRT, encoding="utf-8-sig")
    got: dict = {}
    real_srt_to_ass = karaoke.srt_to_ass

    def _spy(srt, dst, *, aligned=None, **style):
        got["aligned"] = aligned
        return real_srt_to_ass(srt, dst, aligned=aligned, **style)

    monkeypatch.setattr(karaoke, "srt_to_ass", _spy)
    monkeypatch.setattr(h.postprod, "frame_size", lambda v: (1080, 1920))
    monkeypatch.setattr(h.postprod, "burn_ass", lambda *a, **kw: a[1])
    monkeypatch.setattr(
        h, "_resolve_media",
        lambda p, k: (srt_path if k == "srt" else ops_env["tmp"] / "clip.mp4", None),
    )

    _, err = asyncio.run(h._op_karaoke({"video": "m-1", "srt": "m-2"}))
    assert err is None
    assert got["aligned"] is None


def test_a_corrupt_sidecar_is_ignored_not_fatal(monkeypatch, ops_env):
    srt_path = ops_env["tmp"] / "bad.srt"
    srt_path.write_text(SRT, encoding="utf-8-sig")
    srt_path.with_suffix(".srt.words.json").write_text("{not json", encoding="utf-8")

    assert h._read_word_sidecar(srt_path) == []


# ── a silent clip loses its subtitles, not the whole edit ─────────────


def test_no_speech_skips_the_subtitles_and_keeps_the_edit(monkeypatch, ops_env):
    """Returned as an error, `no_speech` failed the node — discarding the
    watermark removal, the aspect conversion and the music bed because the clip
    had nothing to say."""
    src = ops_env["tmp"] / "silent.mp4"
    src.write_bytes(bytes(32))

    async def _to_srt(video, **kw):
        raise stt.SttError("NO_SPEECH", kind="no_speech")

    monkeypatch.setattr(stt, "to_srt", _to_srt)
    monkeypatch.setattr(h, "_resolve_media", lambda p, k: (src, None))

    result, err = asyncio.run(h._op_transcribe({"video": "m-1"}))
    assert err is None, "a silent clip must not fail the node"
    assert "không có tiếng nói" in result["note"]
    # And the empty SRT reaches the existing skip path.
    assert h._srt_has_cues(__import__("pathlib").Path(result["path"])) is False


def test_a_real_transcribe_failure_still_fails(monkeypatch, ops_env):
    """The fix must not swallow a broken key or a dead network — those are
    faults the user has to see."""
    src = ops_env["tmp"] / "clip.mp4"
    src.write_bytes(bytes(32))

    async def _to_srt(video, **kw):
        raise stt.SttError("het quota", kind="exhausted")

    monkeypatch.setattr(stt, "to_srt", _to_srt)
    monkeypatch.setattr(h, "_resolve_media", lambda p, k: (src, None))

    _, err = asyncio.run(h._op_transcribe({"video": "m-1"}))
    assert err and "exhausted" in err
