"""Subtitles that light up word by word.

`sub_style_type: "Karaoke ASS"` is one of the packaged tool's subtitle
styles, and this build burned a plain track under that name. Mined from the
binary, the target format is settled: it emits the ASS karaoke tags `\\k`
and `\\kf`.

**Why the word timings are derived rather than asked for.** Measured on
2026-09-02: given a clip whose own transcribe path correctly answered
`NO_SPEECH`, a word-level prompt to Gemini returned five words with
confident, plausible timings (0.449–0.549, 0.659–0.779, …). It invented
them — there was nothing to transcribe. Karaoke's whole point is that the
highlight lands on the word being spoken, so timings that are plausible but
wrong look broken in a way plain subtitles never do. The timings therefore
come from splitting each SRT line's own duration, which cannot drift outside
the line it belongs to.
"""
from __future__ import annotations

import re

import pytest

from flowboard.services import karaoke
from flowboard.services.karaoke import Cue, build_ass, parse_srt, words_from_line

SRT = """1
00:00:00,500 --> 00:00:03,000
Người mẫu đứng nghiêng trước gương

2
00:00:03,200 --> 00:00:06,400
Ánh sáng cửa sổ lướt trên đường cong
"""


# ── the timing split ──────────────────────────────────────────────────


def test_the_word_durations_add_up_to_the_line():
    """The invariant that keeps the highlight inside its own line. Drift
    accumulated across a long clip would push the sweep past the end."""
    words = words_from_line("một hai ba bốn", 4.0)
    assert sum(d for _, d in words) == pytest.approx(4.0, abs=1e-9)


def test_a_longer_word_gets_more_time():
    """Weighted by length because "nghiêng" takes longer to say than "và".
    An even split is audibly wrong on any line mixing the two."""
    words = dict(words_from_line("và nghiêng", 3.0))
    assert words["nghiêng"] > words["và"]


def test_every_word_survives_the_split():
    text = "một hai ba bốn năm sáu bảy"
    assert [w for w, _ in words_from_line(text, 2.0)] == text.split()


def test_a_zero_length_cue_does_not_divide_by_zero():
    words = words_from_line("một hai", 0.0)
    assert [w for w, _ in words] == ["một", "hai"]


def test_an_empty_line_yields_no_words():
    assert words_from_line("   ", 3.0) == []


# ── SRT parsing, which comes from a model and is not trusted ──────────


def test_cues_are_read_with_their_timings():
    cues = parse_srt(SRT)
    assert len(cues) == 2
    assert cues[0].start == pytest.approx(0.5)
    assert cues[0].end == pytest.approx(3.0)


def test_a_malformed_block_costs_one_cue_not_the_track():
    """The SRT is model output. One bad block should not lose the other
    twenty lines of subtitles."""
    broken = SRT + "\n3\nthis line has no timing\nsome text\n"
    assert len(parse_srt(broken)) == 2


def test_a_cue_that_ends_before_it_starts_is_dropped():
    bad = "1\n00:00:05,000 --> 00:00:02,000\nnope\n"
    assert parse_srt(bad) == []


def test_no_srt_at_all_is_empty_not_an_error():
    assert parse_srt("") == []


# ── the ASS itself ────────────────────────────────────────────────────


def _style(**over):
    base = dict(
        font="Arial", size=48,
        primary_color="&H0000FFFF", inactive_color="&H00808080",
    )
    base.update(over)
    return base


def test_each_word_gets_its_own_karaoke_tag():
    ass = build_ass(parse_srt(SRT), **_style())
    line = next(x for x in ass.splitlines() if x.startswith("Dialogue"))
    assert line.count("\\kf") == 6, "six words, six tags"


def test_the_tag_durations_are_centiseconds():
    """ASS karaoke is in CENTISECONDS. Emitting milliseconds is the classic
    10x error: the sweep either finishes in a blink or never completes."""
    cue = Cue(0.0, 2.0, "một hai")
    line = karaoke.karaoke_line(cue)
    total = sum(int(n) for n in re.findall(r"\\kf(\d+)", line))
    assert total == pytest.approx(200, abs=2), f"expected ~200cs, got {total}"


def test_the_tags_land_on_boundaries_without_accumulating_drift():
    """Each tag is the gap to that word's absolute end, not its own rounded
    duration. Seven equal words over one second is 14.2857cs each: rounding
    the durations one at a time gives seven 14s — 98cs, so the sweep finishes
    two centiseconds before the line does, every line, for the whole clip.
    Rounding absolute boundaries keeps each error inside its own word.

    The divisor matters. A length that divides evenly (twelve words over
    three seconds is exactly 25cs each) passes either way and proves
    nothing."""
    cue = Cue(0.0, 1.0, " ".join(["ab"] * 7))
    total = sum(int(n) for n in re.findall(r"\\kf(\d+)", karaoke.karaoke_line(cue)))
    assert total == 100, f"7 words over 1.00s must sum to 100cs, got {total}"


def test_no_word_gets_a_zero_length_sweep():
    """A `\\kf0` is a word the highlight skips outright. Ten words in half a
    second puts several under one centisecond before the floor applies."""
    cue = Cue(0.0, 0.05, " ".join(["a"] * 10))
    tags = [int(n) for n in re.findall(r"\\kf(\d+)", karaoke.karaoke_line(cue))]
    assert tags and min(tags) >= 1


def test_the_inactive_colour_is_secondary_not_primary():
    """The detail that decides whether the effect runs forwards. In ASS,
    PrimaryColour is what a word turns INTO once sung and SecondaryColour is
    what it starts as — the opposite of every other subtitle setting. Swap
    them and every word starts lit and goes dim."""
    ass = build_ass(
        parse_srt(SRT),
        **_style(primary_color="&H0000FFFF", inactive_color="&H00808080"),
    )
    style_line = next(x for x in ass.splitlines() if x.startswith("Style:"))
    fields = style_line.split(",")
    assert fields[3] == "&H0000FFFF", "PrimaryColour must be the SUNG colour"
    assert fields[4] == "&H00808080", "SecondaryColour must be the UNSUNG colour"


def test_braces_in_the_transcript_cannot_open_an_override_block():
    """The text is model output. A raw `{` would open an ASS override and
    swallow the rest of the line."""
    line = karaoke.karaoke_line(Cue(0.0, 1.0, "hello {\\an8} world"))
    assert "{\\an8}" not in line
    assert "\\{" in line


def test_the_ass_declares_the_sections_libass_needs():
    ass = build_ass(parse_srt(SRT), **_style())
    for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
        assert section in ass


def test_timestamps_use_the_ass_format():
    """`H:MM:SS.cc` — not the SRT `HH:MM:SS,mmm`. libass silently ignores a
    Dialogue line it cannot parse, so the track would simply not appear."""
    ass = build_ass([Cue(3661.5, 3662.0, "x")], **_style())
    assert "1:01:01.50" in ass


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (59.996, "0:01:00.00"),   # carries into the minute
        (3599.999, "1:00:00.00"), # and on into the hour
        (0.0, "0:00:00.00"),
    ],
)
def test_a_rounded_timestamp_carries_instead_of_producing_sixty(seconds, expected):
    """Rounding to centiseconds has to carry all the way up. Formatting the
    fields separately yields `0:00:60.00`, which libass drops silently — the
    cue simply never appears, with nothing in any log to say why."""
    ass = build_ass([Cue(seconds, seconds + 1.0, "x")], **_style())
    dialogue = next(x for x in ass.splitlines() if x.startswith("Dialogue"))
    assert dialogue.split(",")[1] == expected


def test_the_declared_resolution_is_the_clips_own():
    """libass scales every size by the real frame height over PlayResY, so a
    portrait default against a landscape clip renders the captions at 56% of
    the size asked for."""
    ass = build_ass(
        parse_srt(SRT), **_style(), play_res_x=1920, play_res_y=1080
    )
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass


def test_an_empty_srt_writes_no_file(tmp_path):
    """None rather than an empty ASS: an empty file still costs a full
    re-encode, and the caller needs the signal to fall back to a plain
    burn instead of shipping a clip with no subtitles at all."""
    assert karaoke.srt_to_ass("", tmp_path / "out.ass", **_style()) is None
    assert not (tmp_path / "out.ass").exists()


def test_the_file_is_written_with_a_bom(tmp_path):
    """Same reason `transcribe.write_srt` uses one: without the BOM ffmpeg
    reads the file as the system codepage on Windows and burns mojibake."""
    out = karaoke.srt_to_ass(SRT, tmp_path / "out.ass", **_style())
    assert out is not None
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")


# ── the planner picks the right burn ──────────────────────────────────


def _ops(style):
    from types import SimpleNamespace

    import flowboard.services.transcribe as transcribe_mod
    from flowboard.services.postprod_plan import Upstream, ops_for

    original = transcribe_mod.available
    transcribe_mod.available = lambda: True
    try:
        node = SimpleNamespace(
            id=1,
            type="edit_video",
            data={"sourceSettings": {"enable_sub": True, "sub_style_type": style}},
        )
        clip = SimpleNamespace(id=2, type="video", data={"mediaId": "clip"})
        return [op["op"] for op in ops_for(node, [Upstream(clip, "media")])]
    finally:
        transcribe_mod.available = original


@pytest.mark.parametrize("style", ["Karaoke ASS", "🎤 Karaoke ASS", "karaoke ass"])
def test_karaoke_styles_take_the_karaoke_burn(style):
    """Matched on a substring: these workflows write every label with
    decoration, so an equality test against "Karaoke ASS" would miss the
    emoji-prefixed spellings that actually ship."""
    assert "karaoke" in _ops(style)


@pytest.mark.parametrize("style", ["Phụ đề thường", None, ""])
def test_other_styles_keep_the_plain_burn(style):
    ops = _ops(style)
    assert "subtitles" in ops and "karaoke" not in ops
