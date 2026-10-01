"""SRT → ASS with per-word karaoke highlighting.

The packaged tool offers ``sub_style_type: "Karaoke ASS"``, which lights each
word as it is spoken. Mined from its binary: it emits the ASS karaoke tags
``\\k`` and ``\\kf``, so the target format is settled, not guessed.

**Where the word timings come from, and why not from the model.**

The obvious source is to ask Gemini for per-word timestamps, and it will
happily provide them. Measured on 2026-09-02, on a clip whose own transcribe
path correctly answered ``NO_SPEECH``: a word-level prompt returned five
words with confident, plausible-looking timings — 0.449–0.549, 0.659–0.779,
and so on. The model invented them. There was nothing to transcribe.

That is disqualifying for this feature specifically. Karaoke's whole visual
point is that the highlight lands on the word being said; timings that are
plausible but wrong produce a highlight that drifts out of sync, which looks
broken in a way plain subtitles never do. Worse than not offering it.

So the timings are DERIVED from the SRT the pipeline already produced, by
splitting each subtitle line's own duration across its words in proportion to
their length. Three properties follow, and they are the reason this is the
right trade:

* it costs nothing — no second model call, no extra quota;
* it is deterministic — the same SRT always yields the same ASS;
* the highlight can never leave the line's own window, because the window is
  what is being divided. It can be a little early or late WITHIN a line; it
  cannot land on the wrong sentence.

What it is not: real speech-aligned timing. Vietnamese is roughly
syllable-timed, so proportional-by-length is a fair approximation, but a long
pause mid-line will not be reflected.

**The trustworthy source has since arrived**, and this module now takes it
when it is offered. `stt.py` can align words with faster-whisper locally or
with whisper-1 over the API — both real alignment, neither of them Gemini,
which stays out of the timing chain for the reason above. Pass those words
as ``aligned`` and `words_from_alignment` uses them; omit them, or offer
alignment that does not cover a line, and the length-weighted split still
runs. The split is the default, not a degraded mode: neither alignment
source is configured out of the box.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

#: `\kf` sweeps the fill across each word; `\k` snaps it. The packaged binary
#: carries both. Sweep is the one people mean by "karaoke".
KARAOKE_TAG = "kf"

#: ASS karaoke durations are in CENTISECONDS, not milliseconds. Getting this
#: wrong by 10x is the classic ASS bug: the highlight either races through
#: the line in a blink or never finishes it.
_CS_PER_SECOND = 100

_SRT_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class Cue:
    """One subtitle line: when it shows, and what it says."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(srt: str) -> list[Cue]:
    """Cues from SRT text.

    Tolerant on purpose: the SRT comes from a model, and a stray blank line
    or a missing index should cost one cue, not the whole track. Cues whose
    timing does not parse are dropped with a log line rather than raising.
    """
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", (srt or "").replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        match = None
        text_from = 0
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if match:
                text_from = index + 1
                break
        if not match:
            logger.info("karaoke: skipping SRT block with no timing line")
            continue
        start = _seconds(*match.group(1, 2, 3, 4))
        end = _seconds(*match.group(5, 6, 7, 8))
        text = " ".join(lines[text_from:]).strip()
        if text and end > start:
            cues.append(Cue(start, end, text))
    return cues


def words_from_line(text: str, duration: float) -> list[tuple[str, float]]:
    """Split a line into (word, seconds) pairs summing to ``duration``.

    The single point where timing is approximated — see the module docstring.
    Weighted by word length because a longer word takes longer to say, which
    beats an even split on any line mixing "và" with "nghiêng".

    The last word absorbs the rounding remainder so the parts always add up
    to the line's duration exactly; drift accumulated over a long clip would
    otherwise push the highlight off the end.
    """
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return []
    if duration <= 0:
        return [(w, 0.0) for w in words]

    weights = [max(1, len(w)) for w in words]
    total = sum(weights)
    out: list[tuple[str, float]] = []
    spent = 0.0
    for word, weight in zip(words[:-1], weights[:-1], strict=True):
        share = duration * weight / total
        out.append((word, share))
        spent += share
    out.append((words[-1], max(0.0, duration - spent)))
    return out


def words_from_alignment(
    cue: "Cue", aligned: list, *, min_overlap: float = 0.5
) -> Optional[list[tuple[str, float]]]:
    """Real per-word durations for this cue, or None to fall back.

    This is the upgrade the module docstring has been waiting for. Given
    genuine word alignment — `stt.Word` values from faster-whisper or
    whisper-1, never from Gemini — the highlight lands on the word actually
    being spoken instead of on a length-weighted guess.

    Returns None rather than a partial answer whenever the alignment does
    not convincingly cover this cue. A half-aligned line is worse than an
    evenly-split one: the sweep would be right for three words and then
    lurch, which reads as broken in a way a uniform approximation never
    does. Two ways that happens:

    * fewer aligned words overlap the cue than the cue has words, so some
      would have no timing at all;
    * the aligned words cover less than ``min_overlap`` of the cue's span,
      which means the alignment belongs to a different line.
    """
    if not aligned or cue.duration <= 0:
        return None

    expected = [w for w in re.split(r"\s+", cue.text.strip()) if w]
    if not expected:
        return None

    inside = [
        w for w in aligned
        if float(getattr(w, "end", 0.0)) > cue.start
        and float(getattr(w, "start", 0.0)) < cue.end
    ]
    if len(inside) < len(expected):
        return None

    covered = sum(
        min(cue.end, float(w.end)) - max(cue.start, float(w.start)) for w in inside
    )
    if covered < cue.duration * min_overlap:
        return None

    # Take the first N aligned words, one per word of the cue's own text:
    # the transcript that produced the SRT and the alignment come from the
    # same pass, so they agree on word order even when they disagree on
    # punctuation.
    chosen = inside[: len(expected)]
    out: list[tuple[str, float]] = []
    # `strict=False` on purpose: `chosen` is `inside[:len(expected)]`, so it
    # can be SHORTER when the aligner found fewer words than the cue has.
    # The guards above decide whether that shortfall is acceptable; here it
    # simply means the tail falls back to the proportional split.
    for index, (word, timed) in enumerate(zip(expected, chosen, strict=False)):
        start = max(cue.start, float(timed.start))
        # Run each word up to the next one's start rather than to its own
        # end: the gap between words belongs to the word being held, and
        # leaving it unassigned makes the sweep stutter between words.
        if index + 1 < len(chosen):
            end = max(start, float(chosen[index + 1].start))
        else:
            end = min(cue.end, max(start, float(timed.end)))
        out.append((word, max(0.0, end - start)))
    return out


def _ass_time(seconds: float) -> str:
    """ASS timestamps are ``H:MM:SS.cc`` — one digit of hours, centiseconds.

    Divided down from a single centisecond count rather than formatted field
    by field. Rounding to centiseconds has to be able to carry all the way
    up: 59.996s rounds to 6000cs, which is one minute, not ``0:00:60.00``.
    libass drops a Dialogue line it cannot parse without saying so, so that
    cue would simply never appear.
    """
    total = max(0, int(round(seconds * _CS_PER_SECOND)))
    hours, rest = divmod(total, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape(text: str) -> str:
    """Neutralise ASS markup in transcribed text.

    A brace in the transcript would open an override block and swallow the
    rest of the line — the text comes from a model, so it is not trusted to
    be free of them.
    """
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def karaoke_line(cue: Cue, aligned: Optional[list] = None) -> str:
    """One Dialogue line with a ``\\kf`` tag per word.

    Uses real alignment when ``aligned`` covers this cue, and the
    length-weighted split otherwise — the fallback is not a lesser mode, it
    is what runs whenever no trustworthy word-timing source is configured,
    which is the default.

    Each tag is the gap to the word's own end BOUNDARY, not its rounded
    duration. Rounding durations one at a time lets the error accumulate —
    twelve words each half a centisecond short leaves the last word ending
    six centiseconds early — whereas rounding absolute boundaries keeps
    every error inside a single word.

    A word is never given less than one centisecond: a zero-length ``\\kf``
    is a word the sweep skips entirely.
    """
    timings = words_from_alignment(cue, aligned) if aligned else None
    if timings is None:
        timings = words_from_line(cue.text, cue.duration)

    parts: list[str] = []
    elapsed = 0.0
    emitted = 0
    for word, seconds in timings:
        elapsed += seconds
        centis = max(1, int(round(elapsed * _CS_PER_SECOND)) - emitted)
        emitted += centis
        parts.append(f"{{\\{KARAOKE_TAG}{centis}}}{_escape(word)} ")
    return "".join(parts).rstrip()


def build_ass(
    cues: Iterable[Cue],
    *,
    font: str,
    size: int,
    primary_color: str,
    inactive_color: str,
    outline_color: str = "&H00000000",
    outline_width: float = 2.0,
    shadow: float = 0.0,
    margin_v: int = 60,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    aligned: Optional[list] = None,
) -> str:
    """A complete ASS file whose words light up as they are spoken.

    Karaoke colours are the opposite way round from every other subtitle
    setting, and this is the detail that makes or breaks the effect:
    ``PrimaryColour`` is the colour a word turns INTO once sung, while
    ``SecondaryColour`` is what it starts as. So the workflow's
    ``sub_inactive_color`` maps to Secondary, not Primary. Swap them and the
    highlight runs backwards — every word starts lit and goes dim.

    ``play_res_x``/``play_res_y`` are the resolution the sizes below are
    quoted in, and libass rescales everything by the real frame's height
    over ``PlayResY``. They are NOT decoration: leave them at a 1080x1920
    default and a 1920x1080 clip renders every caption at 56% of the
    requested point size. Callers pass the clip's measured frame size.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{primary_color},{inactive_color},"
        f"{outline_color},&H00000000,0,0,0,0,100,100,0,0,1,"
        f"{outline_width},{shadow},2,40,40,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    events = [
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,"
        f"{karaoke_line(c, aligned)}"
        for c in cues
    ]
    return header + "\n".join(events) + "\n"


def srt_to_ass(
    srt: str, dst: Path, *, aligned: Optional[list] = None, **style
) -> Optional[Path]:
    """Write a karaoke ASS beside the SRT, or None when there is nothing to write.

    None rather than an empty file: an empty ASS burns a re-encode that
    changes nothing, and the caller needs to be able to fall back to the
    plain track instead of shipping a clip with no subtitles at all.
    """
    cues = parse_srt(srt)
    if not cues:
        logger.info("karaoke: no usable cues in the SRT, skipping")
        return None
    # utf-8-sig for the same reason `transcribe.write_srt` uses it: without
    # the BOM, ffmpeg reads the file as the system codepage on Windows and
    # burns mojibake into the picture.
    dst.write_text(build_ass(cues, aligned=aligned, **style), encoding="utf-8-sig")
    return dst
