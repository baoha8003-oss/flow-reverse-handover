"""A second and third source of speech, and real word alignment.

Subtitles used to rest on Gemini alone, because it is the only provider on
this stack that takes audio — and when its CLI stopped accepting individual
accounts, every subtitle stopped with it.

The part that matters more than a spare transcriber is **alignment**.
`karaoke.py` splits each line's duration by word length because Gemini
fabricates word timestamps: asked for them on a clip its own transcribe path
correctly called silent, it returned five confidently-timed words. So the
timing chain excludes Gemini by construction, and the two sources that can
actually align are the reason `words_from_alignment` exists.

The rule these tests hold hardest: **partial alignment is refused**. A sweep
that is right for three words and then lurches reads as broken in a way a
uniform approximation never does, so a cue the alignment does not
convincingly cover falls back rather than being half-timed.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from flowboard.services import karaoke, stt


@dataclass(frozen=True)
class W:
    """Stands in for `stt.Word` — same three fields the aligner reads."""

    text: str
    start: float
    end: float


# ── the SRT conversion both sources share ─────────────────────────────


def test_segments_become_numbered_srt_blocks():
    srt = stt._srt_from_segments([
        {"start": 0.5, "end": 3.0, "text": "xin chào"},
        {"start": 3.2, "end": 6.4, "text": "hôm nay trời đẹp"},
    ])
    assert "1\n00:00:00,500 --> 00:00:03,000\nxin chào" in srt
    assert "2\n00:00:03,200 --> 00:00:06,400" in srt


def test_empty_and_inverted_segments_are_dropped_without_gaps_in_numbering():
    """A dropped cue must not leave a hole in the index: ffmpeg tolerates it,
    but a renumbered track is what every other SRT reader expects."""
    srt = stt._srt_from_segments([
        {"start": 0.0, "end": 1.0, "text": "một"},
        {"start": 1.0, "end": 2.0, "text": "   "},
        {"start": 5.0, "end": 4.0, "text": "ngược"},
        {"start": 2.0, "end": 3.0, "text": "hai"},
    ])
    assert "1\n" in srt and "2\n" in srt and "3\n" not in srt


def test_no_segments_is_an_empty_string_not_a_stray_newline():
    assert stt._srt_from_segments([]) == ""


@pytest.mark.parametrize("seconds,expected", [
    (0.0, "00:00:00,000"),
    (3.5, "00:00:03,500"),
    (3661.25, "01:01:01,250"),
    (-1.0, "00:00:00,000"),
])
def test_srt_timestamps(seconds, expected):
    assert stt._srt_timestamp(seconds) == expected


# ── which sources are on ──────────────────────────────────────────────


def test_no_key_means_no_openai_source(monkeypatch):
    """The ChatGPT OAuth login the Codex CLI holds cannot reach the audio
    endpoint. Only a real platform key counts, and users conflate the two."""
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda name: None)
    assert stt.openai_available() is False


def test_local_needs_the_setting_not_just_the_package(monkeypatch):
    """faster-whisper downloads a few hundred megabytes on first use, so
    having it installed is not by itself consent to spend the disk."""
    from flowboard.services import settings_store

    monkeypatch.setattr(settings_store, "get", lambda key: False)
    assert stt.local_available() is False


@pytest.mark.asyncio
async def test_no_source_at_all_says_what_to_configure(monkeypatch):
    from flowboard.services import transcribe

    monkeypatch.setattr(stt, "openai_available", lambda: False)
    monkeypatch.setattr(stt, "local_available", lambda: False)
    monkeypatch.setattr(transcribe, "available", lambda: False)
    with pytest.raises(stt.SttError) as exc:
        await stt.to_srt(__import__("pathlib").Path("x.mp4"))
    assert exc.value.kind == "no_source"


@pytest.mark.asyncio
async def test_no_speech_stops_instead_of_paying_the_next_source(monkeypatch):
    """A silent clip is an answer. Falling through to whisper-1 would spend
    money to reach the same conclusion."""
    import pathlib

    from flowboard.services import transcribe

    monkeypatch.setattr(stt, "openai_available", lambda: True)
    monkeypatch.setattr(stt, "local_available", lambda: False)
    monkeypatch.setattr(transcribe, "available", lambda: True)

    called: dict = {}

    async def _gemini(*a, **kw):
        raise transcribe.TranscribeError("nothing said", kind="no_speech")

    async def _openai(*a, **kw):
        called["openai"] = True
        return stt.Transcript(srt="x", source="whisper-1")

    monkeypatch.setattr(transcribe, "video_to_srt", _gemini)
    monkeypatch.setattr(stt, "_via_openai", _openai)

    with pytest.raises(stt.SttError) as exc:
        await stt.to_srt(pathlib.Path("x.mp4"))
    assert exc.value.kind == "no_speech"
    assert "openai" not in called, "a silent clip must not be paid for twice"


@pytest.mark.asyncio
async def test_a_dead_source_falls_through_to_the_next(monkeypatch):
    import pathlib

    from flowboard.services import transcribe

    monkeypatch.setattr(stt, "openai_available", lambda: True)
    monkeypatch.setattr(stt, "local_available", lambda: False)
    monkeypatch.setattr(transcribe, "available", lambda: True)

    async def _gemini(*a, **kw):
        raise transcribe.TranscribeError("quota exhausted")

    async def _openai(*a, **kw):
        return stt.Transcript(srt="ok", source="whisper-1")

    monkeypatch.setattr(transcribe, "video_to_srt", _gemini)
    monkeypatch.setattr(stt, "_via_openai", _openai)

    out = await stt.to_srt(pathlib.Path("x.mp4"))
    assert out.source == "whisper-1"


@pytest.mark.asyncio
async def test_wanting_words_reorders_away_from_gemini(monkeypatch):
    """Gemini cannot align, and asking it anyway returns invented timings.
    With `want_words` the aligning sources have to come first."""
    import pathlib

    from flowboard.services import transcribe

    monkeypatch.setattr(stt, "openai_available", lambda: True)
    monkeypatch.setattr(stt, "local_available", lambda: False)
    monkeypatch.setattr(transcribe, "available", lambda: True)

    order: list[str] = []

    async def _gemini(*a, **kw):
        order.append("gemini")
        return "srt"

    async def _openai(*a, **kw):
        order.append("whisper-1")
        return stt.Transcript(srt="ok", words=[W("a", 0, 1)], source="whisper-1")

    monkeypatch.setattr(transcribe, "video_to_srt", _gemini)
    monkeypatch.setattr(stt, "_via_openai", _openai)

    out = await stt.to_srt(pathlib.Path("x.mp4"), want_words=True)
    assert order == ["whisper-1"], "gemini must not be asked for word timings"
    assert out.words


# ── alignment: used when trustworthy, refused when not ────────────────


def test_real_alignment_beats_the_length_split():
    """The whole point. "và" is short and "nghiêng" is long, so the split
    gives the long word more time — but if the speaker actually held "và",
    only alignment knows that."""
    cue = karaoke.Cue(0.0, 2.0, "và nghiêng")
    aligned = [W("và", 0.0, 1.5), W("nghiêng", 1.5, 2.0)]
    timed = karaoke.words_from_alignment(cue, aligned)
    assert timed is not None
    assert dict(timed)["và"] > dict(timed)["nghiêng"], (
        "alignment must override the length heuristic"
    )


def test_a_word_runs_until_the_next_one_starts():
    """The silence between two words belongs to the word being held.
    Leaving it unassigned makes the sweep stutter between words."""
    cue = karaoke.Cue(0.0, 3.0, "một hai")
    aligned = [W("một", 0.0, 0.5), W("hai", 2.0, 3.0)]
    timed = dict(karaoke.words_from_alignment(cue, aligned))
    assert timed["một"] == pytest.approx(2.0), "the gap goes to the held word"


def test_alignment_covering_too_little_of_the_cue_is_refused():
    """Alignment that overlaps a sliver of the line belongs to a different
    line. Using it would put the highlight on the wrong words entirely."""
    cue = karaoke.Cue(0.0, 10.0, "một hai")
    aligned = [W("một", 9.5, 9.7), W("hai", 9.7, 9.9)]
    assert karaoke.words_from_alignment(cue, aligned) is None


def test_fewer_aligned_words_than_the_line_has_is_refused():
    """Half a line timed and half guessed is worse than all of it guessed:
    the sweep is right, then lurches."""
    cue = karaoke.Cue(0.0, 2.0, "một hai ba bốn")
    aligned = [W("một", 0.0, 0.5), W("hai", 0.5, 1.0)]
    assert karaoke.words_from_alignment(cue, aligned) is None


def test_alignment_from_another_cue_entirely_is_refused():
    cue = karaoke.Cue(10.0, 12.0, "một hai")
    aligned = [W("một", 0.0, 0.5), W("hai", 0.5, 1.0)]
    assert karaoke.words_from_alignment(cue, aligned) is None


def test_no_alignment_means_fall_back_not_fail():
    assert karaoke.words_from_alignment(karaoke.Cue(0, 2, "một hai"), []) is None


# ── the line the burner actually writes ───────────────────────────────


def test_karaoke_line_uses_alignment_when_offered():
    cue = karaoke.Cue(0.0, 2.0, "và nghiêng")
    aligned = [W("và", 0.0, 1.5), W("nghiêng", 1.5, 2.0)]
    with_align = karaoke.karaoke_line(cue, aligned)
    without = karaoke.karaoke_line(cue)
    assert with_align != without, "the alignment made no difference"
    # 1.5s held on the first word = 150 centiseconds.
    assert "\\kf150" in with_align


def test_karaoke_line_without_alignment_is_unchanged():
    """The split is the default, not a degraded mode — neither aligning
    source is configured out of the box."""
    cue = karaoke.Cue(0.0, 2.0, "một hai")
    assert karaoke.karaoke_line(cue) == karaoke.karaoke_line(cue, None)


def test_alignment_reaches_the_written_ass(tmp_path):
    srt = "1\n00:00:00,000 --> 00:00:02,000\nvà nghiêng\n"
    style = dict(font="Arial", size=48,
                 primary_color="&H0000FFFF", inactive_color="&H00808080")
    aligned = [W("và", 0.0, 1.5), W("nghiêng", 1.5, 2.0)]
    plain = karaoke.srt_to_ass(srt, tmp_path / "a.ass", **style)
    timed = karaoke.srt_to_ass(srt, tmp_path / "b.ass", aligned=aligned, **style)
    assert plain and timed
    assert plain.read_text(encoding="utf-8-sig") != timed.read_text(encoding="utf-8-sig")


# ── what the estimate says it will cost ───────────────────────────────


def test_the_estimate_names_the_transcribe_source(client, monkeypatch):
    """A transcription is one job whichever source serves it, so a second
    counter would be wrong. WHICH source is what the count cannot say, and
    the three differ: Gemini quota, ~$0.006 a minute, or free."""
    from flowboard.routes import estimate as estimate_mod

    monkeypatch.setattr(estimate_mod, "_transcribe_source", lambda: "whisper-1")

    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "edit_video", "x": 0, "y": 0,
        "data": {"title": "Hậu kỳ", "sourceSettings": {"enable_sub": True}},
    })
    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    if not body["transcribeJobs"]:
        pytest.skip("no transcribe op planned without a Gemini key")
    assert body["transcribeSource"] == "whisper-1"


def test_no_transcription_means_no_source_claim(client):
    """Naming a source for a board that transcribes nothing would read as
    "this will use whisper-1" on a board that will not."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert body["transcribeJobs"] == 0
    assert body["transcribeSource"] == ""
