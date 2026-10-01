"""Local ffmpeg post-production: the finishing pass Flowboard used to send to Flow.

Every step a generated storyboard needs after the clips exist — making clips
concat-safe, joining them, burning subtitles, watermarking, scoring, and fitting
a scene to its narration — runs here against the ffmpeg binary that
``services.assets`` locates in the packaged VEO3 tool. Nothing in this module
touches the network.

Two hard-won constraints shape the code and are the reason it is not a thin
wrapper around a few command strings:

* **Concat needs uniformity, and ffmpeg will not enforce it.** The concat
  demuxer stream-copies, so every clip must already share codec, resolution,
  pixel format, frame rate and *audio layout* — but hand it a mismatch and it
  writes a playable file that is wrong rather than failing. So
  :func:`normalize` grafts a silent track onto a source that has none, and
  :func:`concat` refuses clips whose streams disagree.
* **libass on Windows is fussy twice over.** The ``subtitles`` filter needs the
  drive-letter colon escaped inside a quoted filtergraph token, and
  ``force_style=FontName`` matches the font's *internal* family name — the file
  stem silently falls back to Arial. Both are handled here; see
  :func:`_filter_path` and :func:`font_family`.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence, Union

from flowboard.services import assets

logger = logging.getLogger(__name__)

# One encoding profile for the whole pipeline. Clips only concat by stream copy
# while every one of these matches, so nothing here is per-call configurable.
VIDEO_CODEC = "libx264"
VIDEO_PRESET = "veryfast"
VIDEO_CRF = "20"
PIXEL_FORMAT = "yuv420p"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2

DEFAULT_FPS = 30

# ASS colours are &HBBGGRR or &HAABBGGRR, optionally closed with '&'. Any other
# digit count is not a colour libass understands, so it must not be waved through.
_ASS_COLOR_RE = re.compile(r"^&[Hh](?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})&?$")

_STDERR_TAIL_LINES = 12

# Wall-clock ceiling for a single ffmpeg/ffprobe invocation. These run under
# run_in_threadpool, and Starlette's threadpool is bounded and shared by
# every sync route handler — an ffmpeg wedged on a malformed input holds its
# thread forever, and enough of them stop the whole agent from responding
# with no way to reclaim the slots short of a restart. Generous enough for a
# long render, finite enough that a hang is not permanent.
FFMPEG_TIMEOUT_S = 1800.0
PROBE_TIMEOUT_S = 60.0

PathLike = Union[str, Path]


class PostProdError(RuntimeError):
    """A post-production step failed, or the local toolchain is missing."""


# ── toolchain ─────────────────────────────────────────────────────────────


def _ffmpeg() -> str:
    binary = assets.ffmpeg_bin()
    if not binary:
        raise PostProdError(
            f"ffmpeg not found in {assets.ASSET_ROOT} or on PATH — "
            "post-production is unavailable"
        )
    return binary


def _ffprobe() -> str:
    binary = assets.ffprobe_bin()
    if not binary:
        raise PostProdError(
            f"ffprobe not found in {assets.ASSET_ROOT} or on PATH — "
            "post-production is unavailable"
        )
    return binary


def available() -> bool:
    """True when both binaries resolve, so callers can gate a UI step."""
    return bool(assets.ffmpeg_bin() and assets.ffprobe_bin())


def _run(
    args: Sequence[str], *, what: str, timeout: float = FFMPEG_TIMEOUT_S
) -> subprocess.CompletedProcess:
    logger.debug("%s: %s", what, " ".join(args))
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise PostProdError(
            f"{what} timed out after {timeout:.0f}s and was killed"
        ) from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-_STDERR_TAIL_LINES:])
        raise PostProdError(f"{what} failed (exit {proc.returncode}):\n{tail}")
    return proc


# Overlay positions are interpolated raw into `overlay=x:y`, where ':' and
# ',' would start a new filter option or a new filter. Only the arithmetic
# ffmpeg's overlay expressions actually need is allowed through: the frame
# and logo dimension variables, digits, and basic operators.
_OVERLAY_EXPR_RE = re.compile(r"^[WwHh0-9+\-*/() .]+$")


def _overlay_pos(value: Union[int, str], *, axis: str) -> Union[int, str]:
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise PostProdError(f"invalid overlay {axis}: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or not _OVERLAY_EXPR_RE.match(text):
        raise PostProdError(
            f"invalid overlay {axis} expression {value!r} — expected pixels or "
            "an expression over W/H/w/h"
        )
    return text


def _source(path: PathLike, *, label: str = "input") -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise PostProdError(f"{label} not found: {resolved}")
    return resolved


def _target(path: PathLike) -> Path:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


# ── probing ───────────────────────────────────────────────────────────────


def probe_duration(path: PathLike) -> float:
    """Container duration in seconds."""
    src = _source(path, label="media")
    proc = _run(
        [
            _ffprobe(),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(src),
        ],
        what=f"ffprobe duration of {src.name}",
        timeout=PROBE_TIMEOUT_S,
    )
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (ValueError, TypeError, KeyError) as exc:
        raise PostProdError(
            f"ffprobe reported no usable duration for {src}: {proc.stdout.strip()[:200]}"
        ) from exc


def _streams(src: Path) -> list[dict]:
    """Every stream with the properties concat uniformity is judged on."""
    proc = _run(
        [
            _ffprobe(),
            "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,"
            "sample_rate,channels",
            "-of", "json",
            str(src),
        ],
        what=f"ffprobe streams of {src.name}",
        timeout=PROBE_TIMEOUT_S,
    )
    try:
        return json.loads(proc.stdout).get("streams") or []
    except ValueError:
        return []


def frame_size(path: PathLike) -> tuple[int, int]:
    """The video's pixel dimensions.

    Exists because geometry expressed as a percentage of the frame — which is
    how the packaged tool stores its watermark box — can only become pixels
    once the frame is known. Doing that conversion here keeps `remove_logo`
    integer-only, so its filtergraph still has no caller string in it.
    """
    for stream in _streams(_source(path)):
        if stream.get("codec_type") != "video":
            continue
        width, height = stream.get("width"), stream.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return width, height
    raise PostProdError(f"no video stream with dimensions in {path}")


def has_audio(path: PathLike) -> bool:
    """Whether the file carries at least one audio stream."""
    src = _source(path, label="media")
    return any(s.get("codec_type") == "audio" for s in _streams(src))


# ── fonts ─────────────────────────────────────────────────────────────────


@lru_cache(maxsize=64)
def font_family(font_file: str) -> Optional[str]:
    """Family name libass matches on, read from the OpenType ``name`` table.

    ``force_style=FontName`` is matched against name ID 1 — the legacy family
    name — not the file stem and not name ID 16. Passing the stem
    (``BeVietnamPro-Bold``) resolves to Arial without any warning, which is how
    Vietnamese subtitles end up in the wrong typeface.
    """
    try:
        data = Path(font_file).read_bytes()
    except OSError as exc:
        logger.warning("cannot read font %s: %s", font_file, exc)
        return None

    try:
        base = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
        table_count = struct.unpack_from(">H", data, base + 4)[0]
        name_offset = None
        for index in range(table_count):
            tag, _checksum, offset, _length = struct.unpack_from(
                ">4sIII", data, base + 12 + index * 16
            )
            if tag == b"name":
                name_offset = offset
                break
        if name_offset is None:
            return None

        record_count, strings_offset = struct.unpack_from(">HH", data, name_offset + 2)
        strings = name_offset + strings_offset
        best_score = -1
        best_name: Optional[str] = None
        for index in range(record_count):
            platform, _encoding, language, name_id, length, offset = struct.unpack_from(
                ">HHHHHH", data, name_offset + 6 + index * 12
            )
            if name_id != 1:
                continue
            raw = data[strings + offset: strings + offset + length]
            try:
                text = (
                    raw.decode("utf-16-be") if platform in (0, 3) else raw.decode("mac-roman")
                )
            except UnicodeDecodeError:
                continue
            text = text.replace("\x00", "").strip()
            if not text:
                continue
            # Prefer the Windows/English record; other locales are a fallback.
            score = 1 if (platform == 3 and language == 0x0409) else 0
            if score > best_score:
                best_score, best_name = score, text
        return best_name
    except (struct.error, IndexError) as exc:
        logger.warning("malformed name table in %s: %s", font_file, exc)
        return None


# ── filtergraph and concat-list escaping ──────────────────────────────────


def _filter_path(path: Path) -> str:
    """Absolute path as a filtergraph option value, single quotes included.

    ffmpeg splits filter arguments on ``:``, so ``D:`` has to be escaped, and
    backslashes are escape characters at the same level — forward slashes side-
    step them entirely and Windows accepts them. A literal single quote cannot
    be represented inside a quoted filtergraph token at all, so it is rejected.
    """
    text = str(path)
    if "'" in text:
        raise PostProdError(
            f"path contains a single quote, which ffmpeg filtergraphs cannot escape: {text}"
        )
    return "'" + text.replace("\\", "/").replace(":", "\\:") + "'"


def _concat_entry(path: Path) -> str:
    """One ``file '...'`` line for the concat demuxer list."""
    # Inside the demuxer's single quotes only ``'`` is special: close, emit an
    # escaped quote, reopen.
    return "file '" + path.as_posix().replace("'", "'\\''") + "'"


def _ass_number(value: float, *, field: str, limit: float = 50.0) -> str:
    """A decimal for an ASS style field, bounded.

    libass allocates from these, so an unbounded value is a way to ask
    ffmpeg to exhaust memory. The ceiling is far above any real subtitle.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PostProdError(f"invalid {field}: {value!r}")
    if value < 0 or value > limit:
        raise PostProdError(f"{field} out of range: {value!r}")
    # Trim a trailing ".0" so an integral value reads as one.
    text = f"{float(value):g}"
    return text


def _ass_color(value: str, *, field: str) -> str:
    if not _ASS_COLOR_RE.match(value or ""):
        raise PostProdError(
            f"{field} must be an ASS colour like '&H0000FFFF' (&HBBGGRR or &HAABBGGRR), "
            f"got {value!r}"
        )
    return value


# ── steps ─────────────────────────────────────────────────────────────────


def normalize(
    src: PathLike,
    dst: PathLike,
    width: int,
    height: int,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Re-encode to the shared profile so clips concat by stream copy.

    Scales to fit inside ``width x height`` and pads the remainder black,
    centred, so nothing is stretched. Fitting can land on an odd width (9:16
    into 16:9 gives 405x720), which ``pad`` then centres to the nearest even
    column — up to two pixels off centre. That is left alone deliberately:
    ``force_divisible_by=2`` fixes the offset by handing back a compensating
    sample aspect ratio, which the ``setsar=1`` this profile needs would throw
    away as a real horizontal stretch. A source without audio gets a silent
    stereo track: the concat demuxer cannot bridge a missing stream, and one
    audio-less clip desynchronises every clip after it.
    """
    if width <= 0 or height <= 0:
        raise PostProdError(f"invalid target size {width}x{height}")
    if fps <= 0:
        raise PostProdError(f"invalid frame rate {fps}")

    source = _source(src)
    target = _target(dst)
    silent = not has_audio(source)

    video_filter = (
        f"fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,format={PIXEL_FORMAT}"
    )

    args = [_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(source)]
    if silent:
        args += [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}",
        ]
    args += [
        "-map", "0:v:0",
        "-map", "1:a:0" if silent else "0:a:0",
        "-vf", video_filter,
        "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CHANNELS),
    ]
    if silent:
        # anullsrc never ends; the video stream decides the length.
        args += ["-shortest"]
    args += ["-movflags", "+faststart", str(target)]

    _run(args, what=f"normalize {source.name}")
    logger.info("normalized %s -> %s (%dx%d @%dfps)", source.name, target.name, width, height, fps)
    return target


_CONCAT_FIELDS = (
    ("video codec", "codec_name"),
    ("width", "width"),
    ("height", "height"),
    ("pixel format", "pix_fmt"),
    ("frame rate", "r_frame_rate"),
    ("audio codec", "audio_codec_name"),
    ("sample rate", "audio_sample_rate"),
    ("channels", "audio_channels"),
)


def _concat_signature(src: Path) -> dict[str, object]:
    """The stream properties every clip in one concat must agree on."""
    streams = _streams(src)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise PostProdError(f"clip has no video stream: {src}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    signature: dict[str, object] = {
        key: video.get(key)
        for key in ("codec_name", "width", "height", "pix_fmt", "r_frame_rate")
    }
    for key in ("codec_name", "sample_rate", "channels"):
        # A missing audio stream reads as None, which is itself a mismatch
        # against a clip that has one — that is the desync case.
        signature[f"audio_{key}"] = audio.get(key)
    return signature


def concat(clips: Sequence[PathLike], dst: PathLike) -> Path:
    """Join clips with the concat demuxer.

    Stream copy only, so pass clips that already went through :func:`normalize`
    with the same geometry. The demuxer does *not* reject a mismatch: given a
    320x180 clip followed by a 640x360 one it writes a file whose container
    still says 320x180 and whose second half decodes at another resolution, and
    given one clip with no audio stream it writes a video track twice as long
    as the audio track. Both play, both are wrong, so uniformity is checked here
    before ffmpeg is asked to copy anything.
    """
    sources = [_source(clip, label="clip") for clip in clips]
    if not sources:
        raise PostProdError("concat needs at least one clip")
    target = _target(dst)

    reference = _concat_signature(sources[0])
    for clip in sources[1:]:
        signature = _concat_signature(clip)
        differences = [
            f"{label} {reference[key]!r} != {signature[key]!r}"
            for label, key in _CONCAT_FIELDS
            if reference[key] != signature[key]
        ]
        if differences:
            raise PostProdError(
                f"clip {clip.name} does not match {sources[0].name} and would "
                f"concat into a broken file — normalize both first "
                f"({'; '.join(differences)})"
            )

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="flowboard-concat-",
        encoding="utf-8", delete=False, newline="\n",
    )
    try:
        with handle as fh:
            fh.write("\n".join(_concat_entry(p) for p in sources) + "\n")
        list_path = Path(handle.name)
        _run(
            [
                _ffmpeg(), "-hide_banner", "-nostdin", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(target),
            ],
            what=f"concat {len(sources)} clip(s)",
        )
    finally:
        Path(handle.name).unlink(missing_ok=True)

    logger.info("concatenated %d clip(s) -> %s", len(sources), target.name)
    return target


def burn_subtitles(
    src: PathLike,
    srt_path: PathLike,
    dst: PathLike,
    *,
    font: str = assets.DEFAULT_SUBTITLE_FONT,
    size: int = 48,
    primary_color: str = "&H00FFFFFF",
    outline_color: str = "&H00000000",
    outline_width: float = 2.0,
    margin_v: int = 60,
    shadow: float = 0.0,
) -> Path:
    """Burn an SRT into the picture with libass, using a CapCut font.

    ``font`` is an asset file name or family stem resolved through
    ``assets.font_path``. Colours are ASS ``&HBBGGRR`` / ``&HAABBGGRR`` — note
    the byte order is blue-green-red, not RGB.
    """
    source = _source(src)
    subtitles = _source(srt_path, label="subtitle file")
    target = _target(dst)

    resolved_font = assets.font_path(font)
    if resolved_font is None:
        raise PostProdError(
            f"font {font!r} not found in {assets.FONT_DIR} "
            f"({len(assets.list_fonts())} font(s) available)"
        )
    family = font_family(str(resolved_font)) or resolved_font.stem

    style = ",".join(
        [
            f"FontName={family}",
            f"FontSize={int(size)}",
            f"PrimaryColour={_ass_color(primary_color, field='primary_color')}",
            f"OutlineColour={_ass_color(outline_color, field='outline_color')}",
            # Decimals on purpose: the packaged tool's own workflows use
            # 0.2, 0.5 and 2.5, and libass takes them. Rounding to an int
            # turned a hairline outline into no outline at all.
            f"Outline={_ass_number(outline_width, field='outline_width')}",
            f"Shadow={_ass_number(shadow, field='shadow')}",
            f"MarginV={int(margin_v)}",
            "BorderStyle=1",
            "Alignment=2",
        ]
    )
    video_filter = (
        f"subtitles={_filter_path(subtitles)}"
        f":fontsdir={_filter_path(assets.FONT_DIR)}"
        f":force_style='{style}'"
    )

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", video_filter,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"burn subtitles into {source.name}",
    )
    logger.info("burned %s into %s using %s", subtitles.name, target.name, family)
    return target


def burn_ass(src: PathLike, ass_path: PathLike, dst: PathLike) -> Path:
    """Burn a prepared ASS subtitle file into the picture.

    Separate from `burn_subtitles` because the two differ in who owns the
    styling. That one takes an SRT and applies `force_style`, since an SRT
    carries no styling of its own. An ASS already contains its styles — and
    for karaoke it must, because the effect lives in per-word `\\kf` tags and
    the Primary/Secondary colour pair. Passing `force_style` here would
    override the very styles that make it karaoke.

    Uses the `ass` filter rather than `subtitles`: `subtitles` also reads ASS
    but re-renders it through its own defaults, which drops the karaoke
    timing.
    """
    source = _source(src)
    subtitles = _source(ass_path, label="ASS subtitle file")
    target = _target(dst)

    video_filter = (
        f"ass={_filter_path(subtitles)}:fontsdir={_filter_path(assets.FONT_DIR)}"
    )
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", video_filter,
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"burn karaoke subtitles into {source.name}",
    )
    logger.info("burned ASS %s into %s", subtitles.name, target.name)
    return target


def overlay_logo(
    src: PathLike,
    logo_path: PathLike,
    dst: PathLike,
    x: Union[int, str],
    y: Union[int, str],
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Path:
    """Composite a logo over the video.

    ``x``/``y`` accept overlay expressions (``"W-w-24"``, ``"H-h-24"``) as well
    as pixels. Expressions are validated against
    :data:`_OVERLAY_EXPR_RE` before reaching the filtergraph — they are
    interpolated raw, so an unchecked string here is a filtergraph-injection
    hole the moment any caller forwards user input. Give one of
    ``width``/``height`` to scale the logo and keep its aspect ratio; give
    both to force a size.
    """
    source = _source(src)
    logo = _source(logo_path, label="logo")
    target = _target(dst)

    x = _overlay_pos(x, axis="x")
    y = _overlay_pos(y, axis="y")

    if width is not None and width <= 0:
        raise PostProdError(f"invalid logo width {width}")
    if height is not None and height <= 0:
        raise PostProdError(f"invalid logo height {height}")

    if width is None and height is None:
        logo_chain = "[1:v]null[logo]"
    else:
        logo_chain = f"[1:v]scale={width or -1}:{height or -1}[logo]"
    filter_complex = f"{logo_chain};[0:v][logo]overlay={x}:{y}[v]"

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-i", str(logo),
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"overlay {logo.name} onto {source.name}",
    )
    logger.info("overlaid %s onto %s", logo.name, target.name)
    return target


# ── aspect conversion ─────────────────────────────────────────────────────

#: The ratios the packaged tool's `aspect_output` dropdown offers, as
#: (width, height). Parsed rather than hard-matched so "4:5" keeps working
#: if the dropdown grows, but kept as a map so a typo fails loudly instead
#: of producing a frame nobody asked for.
_RATIO_RE = re.compile(r"^\s*(\d{1,2})\s*:\s*(\d{1,2})\s*$")

#: Two hex digits per channel, with or without the leading '#'. Interpolated
#: into an ffmpeg `color=` source, so an unchecked value would be filtergraph
#: injection.
_HEX_COLOR_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def parse_ratio(value: str) -> tuple[int, int]:
    """``"9:16"`` -> ``(9, 16)``."""
    match = _RATIO_RE.match(str(value or ""))
    if not match:
        raise PostProdError(f"invalid aspect ratio {value!r} — expected e.g. '9:16'")
    w, h = int(match.group(1)), int(match.group(2))
    if w < 1 or h < 1:
        raise PostProdError(f"invalid aspect ratio {value!r}")
    return w, h


def _even(value: float) -> int:
    """Round to an even pixel — yuv420p subsamples chroma 2x2 and ffmpeg
    refuses an odd dimension outright."""
    return max(2, int(round(value / 2)) * 2)


def frame_for_ratio(src_w: int, src_h: int, ratio: str) -> tuple[int, int]:
    """The output frame for converting a ``src_w x src_h`` clip to ``ratio``.

    The frame's LONG side is the source's long side. That is a deliberate
    choice rather than a canonical 1080x1920: it never invents pixels the
    source does not have. A 1280x720 clip converted to 9:16 becomes a
    720x1280 frame with the clip sitting 1280 * 9/16 = 720 wide inside it —
    every pixel of the result comes from a pixel of the source.

    Wanting a bigger frame than the source is a real want, but it is an
    upscale, and this node already has a separate, explicit upscale pass for
    that. Doing it silently here would hide a resolution change inside a
    geometry setting.
    """
    ratio_w, ratio_h = parse_ratio(ratio)
    long_side = max(src_w, src_h)
    if ratio_w >= ratio_h:
        return _even(long_side), _even(long_side * ratio_h / ratio_w)
    return _even(long_side * ratio_w / ratio_h), _even(long_side)


def _rounded_alpha(radius: int) -> str:
    """A geq alpha expression that rounds the corners of its input.

    Inside the corner's quarter-square the pixel is kept only if it is within
    ``radius`` of the corner circle's centre; everywhere else the pixel is
    opaque. Written against geq's own ``W``/``H`` so it does not need to know
    the size of what it is masking.
    """
    r = int(radius)
    inner_x, inner_y = f"(W/2-{r})", f"(H/2-{r})"
    dx, dy = f"(abs(X-W/2)-{inner_x})", f"(abs(Y-H/2)-{inner_y})"
    in_corner = f"gt(abs(X-W/2),{inner_x})*gt(abs(Y-H/2),{inner_y})"
    inside_circle = f"lte({dx}*{dx}+{dy}*{dy},{r}*{r})"
    return f"if({in_corner},if({inside_circle},255,0),255)"


def convert_aspect(
    src: PathLike,
    dst: PathLike,
    *,
    ratio: str,
    background_image: Optional[PathLike] = None,
    background_color: str = "#000000",
    zoom: float = 100.0,
    border_radius: int = 0,
) -> Path:
    """Re-frame a clip into another aspect ratio over a background.

    Not a letterbox. The packaged tool's ``enable_aspect_convert`` turns a
    16:9 generation into the 9:16 card-on-a-backdrop layout its stick-figure
    workflows ship: the clip is scaled to fit, its corners are rounded, and
    it is centred over a background image (or a flat colour), leaving room
    above and below for the title.

    The filtergraph follows the packaged tool's own, mined from its binary
    rather than invented — ``force_original_aspect_ratio=increase`` plus a
    crop to fill the backdrop, ``format=yuva444p`` and a ``geq`` alpha for
    the corners, then ``overlay=(W-w)/2:(H-h)/2:shortest=1``. ``shortest``
    is what stops a still backdrop from running forever.

    ``zoom`` is a percentage of the frame the clip is fitted into: 100 fits
    it edge to edge on its limiting axis, less leaves a margin.
    """
    source = _source(src)
    target = _target(dst)

    src_w, src_h = frame_size(source)
    out_w, out_h = frame_for_ratio(src_w, src_h, ratio)

    if not 1.0 <= float(zoom) <= 100.0:
        raise PostProdError(f"invalid zoom {zoom} — expected 1..100 (percent)")
    box_w, box_h = _even(out_w * zoom / 100.0), _even(out_h * zoom / 100.0)

    radius = int(border_radius)
    if radius < 0:
        raise PostProdError(f"invalid border radius {border_radius}")
    # A radius past the half-width would make the corner circles overlap and
    # the mask fold in on itself. Clamping is friendlier than refusing: the
    # cap is simply "as round as this box can be", a pill.
    radius = min(radius, box_w // 2, box_h // 2)

    inputs: list[str] = ["-i", str(source)]
    if background_image is not None:
        backdrop = _source(background_image, label="background image")
        # -loop makes the still last as long as it is needed; `shortest=1`
        # on the overlay is what actually ends the output.
        inputs += ["-loop", "1", "-i", str(backdrop)]
        bg_chain = (
            f"[1:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase"
            f":flags=lanczos,crop={out_w}:{out_h},setsar=1[bg]"
        )
    else:
        match = _HEX_COLOR_RE.match(str(background_color or ""))
        if not match:
            raise PostProdError(
                f"invalid background colour {background_color!r} — expected #RRGGBB"
            )
        inputs += [
            "-f", "lavfi",
            "-i", f"color=c=0x{match.group(1)}:s={out_w}x{out_h}",
        ]
        bg_chain = "[1:v]setsar=1[bg]"

    scaled = (
        f"[0:v]scale={box_w}:{box_h}:force_original_aspect_ratio=decrease"
        f":flags=lanczos,setsar=1[vid_scaled]"
    )
    if radius > 0:
        rounding = (
            f"[vid_scaled]format=yuva444p,geq=lum='lum(X,Y)':cb='cb(X,Y)'"
            f":cr='cr(X,Y)':a='{_rounded_alpha(radius)}'[vid_rounded]"
        )
        overlay_in = "vid_rounded"
    else:
        rounding = None
        overlay_in = "vid_scaled"

    chains = [bg_chain, scaled]
    if rounding:
        chains.append(rounding)
    chains.append(f"[bg][{overlay_in}]overlay=(W-w)/2:(H-h)/2:shortest=1[v]")

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            *inputs,
            "-filter_complex", ";".join(chains),
            "-map", "[v]", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"convert {source.name} to {ratio}",
    )
    logger.info(
        "converted %s (%dx%d) to %s (%dx%d), radius %d",
        source.name, src_w, src_h, ratio, out_w, out_h, radius,
    )
    return target


# ── styled title text over the converted frame ────────────────────────────

#: The height the packaged tool's text sizes are quoted against.
#:
#: Its own settings are relative, not pixels — a title of ``size: 14`` on a
#: 1280-tall output is not fourteen pixels, and the binary carries
#: ``_moving_text_render_size_for_height``, a function whose whole job is
#: turning one of these numbers into a pixel size for a given frame. The
#: reference height that function divides by is NOT in the binary's strings,
#: so this constant is the one number here that was chosen rather than read:
#: 360 is what makes the two shipped workflows (`size: 13` and `size: 14`,
#: outlines of 2 and 3) render as the bold caption their layout leaves room
#: for. Change it here if a real render says otherwise — every size, outline
#: and glow width scales through this one place.
TEXT_REFERENCE_HEIGHT = 360

#: Every preset name the packaged tool offers, in its own order, mined from
#: the ordered tuple that sits immediately before `TEXT_ART_PRESETS` in the
#: binary. **Thirty-eight** — this module previously said thirty-seven, which
#: was an estimate rather than a count, and the mined tuple settles it: the
#: names run from `Sunset Glow` to `Cotton Candy` with `None` in front, and
#: there are thirty-eight of them. Corrected rather than propagated; a
#: number that merely looks close is how a wrong one survives.
#:
#: The names matter on their own. Without them an imported workflow asking
#: for `Ice Frost` is indistinguishable from a typo, and the message says
#: "unknown effect" — sending the reader to look for a bug that is not there.
#: With them the app can say the true thing: the preset exists and its
#: gradient is not reproduced here.
TEXT_ART_PRESET_NAMES: tuple[str, ...] = (
    "Sunset Glow", "Neon Pink", "Neon Blue", "Neon Green", "Neon Orange",
    "Neon Red", "Neon Yellow", "Neon Purple", "Gold Luxury", "Cyberpunk",
    "Retro 3D", "Bubble Gum", "Art Orange", "Purple Magic", "Silver Metallic",
    "Sweet Candy", "Ice Frost", "Fire Flame", "Forest Nature",
    "Double Outline", "Comic Book", "Graffiti", "Pink Cyan", "Yellow White",
    "Red Orange", "Luminous Green", "Classic Retro", "Soft Pink Candy",
    "Tiffany Blue", "Ocean Wave", "Luxury Gold Foil", "Cyberpunk Purple",
    "Black Gold", "White Blue Glow", "Lemon Yellow", "Vintage Brown",
    "Pop Art Pink", "Cotton Candy",
)

#: The presets whose colour stops could be recovered with confidence.
#:
#: **Why only two, when all thirty-eight names were recoverable.** The stops
#: live in a marshalled Python code object where colours are interned
#: constants referenced by index, so their order in the string table is a
#: rendering artefact rather than the structure. Two extraction rules were
#: tried against the two presets already known to be correct: a loose
#: adjacency walk reproduced `gold luxury` exactly and got `sunset glow`
#: wrong (it swallowed a neighbouring shadow colour), while keying strictly
#: off `setColorAt` reproduced `sunset glow` exactly and could not reach the
#: others at all. One method right about one preset and wrong about another
#: is a method that cannot be trusted for the thirty-six whose answers are
#: unknown — so they are not guessed.
#:
#: The fallback is the flat `color`, and `overlay_text_art` names the preset
#: it could not draw. Quietly substituting a nearby gradient would look like
#: it worked.
TEXT_ART_GRADIENTS: dict[str, tuple[str, ...]] = {
    "sunset glow": ("#ff4500", "#ff8c00", "#ffdf00"),
    "gold luxury": ("#3d2b00", "#ffe891", "#d4af37", "#8a6f27"),
}

_PRESETS_BY_KEY = {name.lower(): name for name in TEXT_ART_PRESET_NAMES}


def is_known_text_art_preset(effect: object) -> bool:
    """True when the packaged tool has a preset by this name.

    Separate from having its colours: "we know this effect and cannot draw
    it" and "we have never heard of this effect" are different facts, and
    only the second one is a reason to suspect a typo.
    """
    return str(effect or "").strip().lower() in _PRESETS_BY_KEY


@dataclass(frozen=True)
class TextArt:
    """One styled caption laid over the frame.

    Field names and units are the packaged tool's own, straight out of a
    workflow's ``aspect_texts`` list, so the plan layer can hand one over
    without a translation table in between.
    """

    text: str
    font: str = ""
    size: float = 14.0
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: float = 0.0
    glow_color: str = "#000000"
    glow_width: float = 0.0
    line_spacing: float = 1.0
    x_pct: float = 50.0
    y_pct: float = 50.0
    art_effect: str = "None"


def _hex_color(value: str, *, what: str) -> str:
    """``#RRGGBB`` as the ``0xRRGGBB`` an ffmpeg colour option wants."""
    match = _HEX_COLOR_RE.match(str(value or ""))
    if not match:
        raise PostProdError(f"invalid {what} {value!r} — expected #RRGGBB")
    return "0x" + match.group(1)


def _text_art_gradient(effect: object) -> Optional[tuple[str, ...]]:
    key = str(effect or "").strip().lower()
    if not key or key == "none":
        return None
    return TEXT_ART_GRADIENTS.get(key)


def overlay_text_art(
    src: PathLike, dst: PathLike, texts: Sequence[TextArt]
) -> tuple[Path, list[str]]:
    """Draw the workflow's styled captions over the clip.

    Returns the output and a list of notes — one per caption whose art
    effect this build does not carry, so the caller can tell the user which
    styling was dropped instead of shipping a plain caption that looks like
    the setting did nothing.

    Each caption is up to three passes over the frame:

    * the glow, a fat soft stroke under everything, when ``glow_width`` asks
      for one;
    * the outline, a solid stroke in ``outline_color``;
    * the fill — flat ``color``, or, for a preset in
      :data:`TEXT_ART_GRADIENTS`, a vertical gradient masked to the glyphs.

    The gradient is the only interesting one. drawtext cannot fill with a
    gradient, so the glyphs are drawn white onto black to make an alpha mask,
    a ``gradients`` source is generated for the caption's own band of the
    frame, and ``alphamerge`` cuts one out of the other. Generating the
    gradient across the caption's band rather than the whole frame is what
    makes it read as a gradient at all: spread over 1280 rows, the ~180 a
    title occupies all sample nearly the same colour and it looks flat.
    """
    source = _source(src)
    target = _target(dst)
    if not texts:
        raise PostProdError("no text to draw")

    width, height = frame_size(source)
    scale = height / TEXT_REFERENCE_HEIGHT

    chains: list[str] = []
    notes: list[str] = []
    label = "0:v"

    for index, art in enumerate(texts):
        body = str(art.text or "").strip()
        if not body:
            continue

        font_file = assets.font_path(art.font) if art.font else None
        if font_file is None:
            font_file = assets.font_path(assets.DEFAULT_SUBTITLE_FONT)
        if font_file is None:
            raise PostProdError(f"no font file for {art.font!r} and no default")

        size_px = max(8, int(round(float(art.size) * scale)))
        # Lines are drawn by drawtext itself; line_spacing is the multiplier
        # the workflow stores, expressed as the extra pixels drawtext wants.
        spacing = int(round(size_px * (float(art.line_spacing or 1.0) - 1.0)))

        common = (
            f"fontfile={_filter_path(font_file)}"
            f":text='{_drawtext_escape(body)}'"
            f":fontsize={size_px}"
            f":line_spacing={max(0, spacing)}"
            f":x=(w-text_w)*{float(art.x_pct) / 100.0:.4f}"
            f":y=(h-text_h)*{float(art.y_pct) / 100.0:.4f}"
        )

        glow_px = int(round(float(art.glow_width or 0.0) * scale))
        if glow_px > 0:
            out = f"glow{index}"
            chains.append(
                f"[{label}]drawtext={common}"
                f":fontcolor={_hex_color(art.glow_color, what='glow colour')}"
                f":borderw={glow_px}"
                f":bordercolor={_hex_color(art.glow_color, what='glow colour')}"
                f"[{out}]"
            )
            label = out

        outline_px = int(round(float(art.outline_width or 0.0) * scale))
        if outline_px > 0:
            out = f"outline{index}"
            chains.append(
                f"[{label}]drawtext={common}"
                f":fontcolor={_hex_color(art.outline_color, what='outline colour')}"
                f":borderw={outline_px}"
                f":bordercolor={_hex_color(art.outline_color, what='outline colour')}"
                f"[{out}]"
            )
            label = out

        stops = _text_art_gradient(art.art_effect)
        if stops is None:
            asked = str(art.art_effect or "").strip()
            if asked.lower() not in ("", "none"):
                # Two different sentences, because they send the reader to
                # two different places: one is a limitation of this build,
                # the other is probably a misspelling in the workflow.
                notes.append(
                    f"Hiệu ứng chữ “{asked}” có trong tool gốc nhưng bản này "
                    "chưa dựng lại được dải màu — dùng màu phẳng."
                    if is_known_text_art_preset(asked)
                    else f"Không có hiệu ứng chữ tên “{asked}” — dùng màu phẳng."
                )
            out = f"text{index}"
            chains.append(
                f"[{label}]drawtext={common}"
                f":fontcolor={_hex_color(art.color, what='text colour')}[{out}]"
            )
            label = out
            continue

        # The caption's own band, so the gradient is spread across the
        # glyphs rather than across the whole frame.
        top = max(0, int(round((height - size_px) * float(art.y_pct) / 100.0)))
        bottom = min(height, top + size_px)
        colors = ":".join(
            f"c{i}={_hex_color(stop, what='gradient stop')}"
            for i, stop in enumerate(stops)
        )
        mask, grad, filled = f"mask{index}", f"grad{index}", f"art{index}"
        chains.append(
            f"color=c=black:s={width}x{height},format=gray,"
            f"drawtext={common}:fontcolor=white[{mask}]"
        )
        chains.append(
            f"gradients=s={width}x{height}:{colors}:n={len(stops)}"
            f":x0=0:y0={top}:x1=0:y1={bottom},format=rgba[{grad}]"
        )
        chains.append(f"[{grad}][{mask}]alphamerge[{filled}]")
        out = f"text{index}"
        chains.append(f"[{label}][{filled}]overlay=0:0:shortest=1[{out}]")
        label = out

    if not chains:
        raise PostProdError("no text to draw")

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-filter_complex", ";".join(chains),
            "-map", f"[{label}]", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"draw {len(texts)} caption(s) onto {source.name}",
    )
    logger.info("drew %d caption(s) onto %s", len(texts), target.name)
    return target, notes


def _drawtext_escape(text: str) -> str:
    """Escape a caption for ffmpeg's drawtext filter.

    drawtext parses its own mini-language, so a colon or an apostrophe in the
    title silently truncates the caption or breaks the whole filtergraph. The
    backslash has to go first or it would double-escape everything after it.
    """
    out = text.replace("\\", "\\\\")
    for char in (":", "'", "%", ",", "[", "]", ";"):
        out = out.replace(char, "\\" + char)
    return out.replace("\n", " ")


def overlay_title(
    src: PathLike,
    dst: PathLike,
    text: str,
    *,
    font: str = assets.DEFAULT_SUBTITLE_FONT,
    size: int = 64,
    color: str = "white",
    box_opacity: float = 0.5,
    y_ratio: float = 0.08,
) -> Path:
    """Burn a title line across the top of the video.

    The last piece of the packaged tool's `edit_video` assembly step, which
    takes a `title` alongside the media and voice track. Centred horizontally,
    with a translucent box behind it so the text stays legible over a busy
    frame instead of vanishing into it.
    """
    source = _source(src)
    target = _target(dst)
    caption = (text or "").strip()
    if not caption:
        raise PostProdError("title text is required")
    if size < 1 or size > 400:
        raise PostProdError(f"invalid title size {size}")
    if not 0.0 <= box_opacity <= 1.0:
        raise PostProdError(f"invalid box opacity {box_opacity}")
    if not 0.0 <= y_ratio <= 1.0:
        raise PostProdError(f"invalid y position {y_ratio}")

    resolved_font = assets.font_path(font)
    if resolved_font is None:
        raise PostProdError(
            f"font {font!r} not found in {assets.FONT_DIR} "
            f"({len(assets.list_fonts())} font(s) available)"
        )
    # drawtext takes a path in its own option string; on Windows the drive
    # colon and the backslashes both need escaping there.
    font_arg = str(resolved_font).replace("\\", "/").replace(":", "\\:")

    filtergraph = (
        f"drawtext=fontfile='{font_arg}'"
        f":text='{_drawtext_escape(caption)}'"
        f":fontsize={size}:fontcolor={color}"
        f":x=(w-text_w)/2:y=h*{y_ratio}"
        f":box=1:boxcolor=black@{box_opacity}:boxborderw=18"
    )
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", filtergraph,
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"draw a title on {source.name}",
    )
    logger.info("title drawn on %s", target.name)
    return target


def last_frame(src: PathLike, dst: PathLike, *, width: Optional[int] = None) -> Path:
    """The clip's final frame, as an image.

    This is how the packaged tool chains scenes: the last frame of clip N
    becomes the first frame of clip N+1, so the cut between them is seamless
    instead of a jump. Its templates use it four times.

    Seeking near the end is unreliable — the exact duration is often a hair
    off and a seek past it yields nothing — so this decodes forward and keeps
    overwriting, leaving the genuinely last decodable frame.
    """
    source = _source(src)
    target = _target(dst)
    filters = ["select=1"]
    if width is not None:
        if width < 16:
            raise PostProdError(f"invalid width {width}")
        filters.append(f"scale={width}:-2")
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", ",".join(filters),
            # Write every frame to the same file; the last one to land wins.
            "-vsync", "0", "-update", "1",
            "-q:v", "2",
            str(target),
        ],
        what=f"take the last frame of {source.name}",
    )
    if not target.is_file():
        raise PostProdError(f"no frame could be read from {source.name}")
    return target


def grab_thumbnail(
    src: PathLike, dst: PathLike, *, at_seconds: float = 1.0, width: int = 1280
) -> Path:
    """Pull one frame out as a cover image.

    The packaged tool builds a thumbnail for every finished video
    (`_edit_video_thumbnail_command`). Seeking past the end yields no frame at
    all, so a timestamp beyond the clip is pulled back inside it rather than
    failing.
    """
    source = _source(src)
    target = _target(dst)
    if width < 16:
        raise PostProdError(f"invalid thumbnail width {width}")

    duration = probe_duration(source)
    # Keep the seek inside the clip; a hair before the end, never exactly on it.
    when = max(0.0, min(at_seconds, duration - 0.1) if duration > 0 else 0.0)
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            # -ss before -i seeks by keyframe and is far faster on a long clip.
            "-ss", f"{when:.3f}",
            "-i", str(source),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-q:v", "2",
            str(target),
        ],
        what=f"grab a thumbnail from {source.name}",
    )
    if not target.is_file():
        raise PostProdError(f"no frame could be read at {when:.1f}s")
    return target


def split_by_duration(
    src: PathLike, dst_dir: PathLike, seconds: int, *, stem: str = "cut"
) -> list[Path]:
    """Cut a video into fixed-length pieces, newest ffmpeg segment muxer.

    Re-encodes rather than stream-copying. A copy would only be able to cut on
    keyframes, so the pieces would drift from the requested length — sometimes
    by seconds — which is exactly what someone asking for "8 second clips"
    would notice first.
    """
    source = _source(src)
    out_dir = Path(dst_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if seconds < 1:
        raise PostProdError("segment length must be at least 1 second")

    pattern = out_dir / f"{stem}-%03d.mp4"
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            "-f", "segment",
            "-segment_time", str(seconds),
            # Cut exactly on the requested boundary instead of the next
            # keyframe, and restart each piece's clock at zero so players
            # don't show the source's running time.
            "-reset_timestamps", "1",
            "-force_key_frames", f"expr:gte(t,n_forced*{seconds})",
            str(pattern),
        ],
        what=f"cut {source.name} into {seconds}s pieces",
    )
    pieces = sorted(out_dir.glob(f"{stem}-*.mp4"))
    if not pieces:
        raise PostProdError(f"cutting {source.name} produced no pieces")
    logger.info("cut %s into %d pieces", source.name, len(pieces))
    return pieces


def remove_logo(
    src: PathLike,
    dst: PathLike,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
) -> Path:
    """Blur out a watermark using ffmpeg's ``delogo`` filter.

    ``delogo`` interpolates the rectangle from the pixels around its border,
    so it hides a static mark rather than recovering what was behind it:
    expect a soft patch, best over a plain background. Cover the mark snugly —
    an oversized box smears more of the frame than it has to.

    Only geometry is caller-supplied and all four values are integers by the
    time they reach the filtergraph, so there is no string to inject through.
    """
    source = _source(src)
    target = _target(dst)
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PostProdError(f"invalid delogo {name}: {value!r}")
    if width < 1 or height < 1:
        raise PostProdError("delogo width and height must be at least 1 pixel")

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", f"delogo=x={x}:y={y}:w={width}:h={height}",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"remove a logo from {source.name}",
    )
    logger.info("delogo applied to %s", target.name)
    return target


#: ffmpeg's `atempo` accepts a factor in [0.5, 2.0] and refuses anything
#: outside it. Faster or slower than that has to be built by chaining
#: several, whose factors multiply.
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0


def build_atempo_chain(speed: float) -> list[str]:
    """`atempo` filters whose factors multiply out to ``speed``.

    The packaged tool has its own `_build_atempo_filter`; this is that job.
    The chain exists because one `atempo` is capped at 2x — 3x has to be
    ``atempo=2.0,atempo=1.5``, and asking for ``atempo=3.0`` makes ffmpeg
    reject the whole filtergraph.

    Returned as a list so a caller can splice the parts into a larger graph
    without re-parsing a joined string.
    """
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise PostProdError(f"invalid speed: {speed!r}")
    speed = float(speed)
    if not 0.25 <= speed <= 4.0:
        # Past this the audio is unusable whatever the chain does, and a
        # caller asking for it has most likely sent the wrong units.
        raise PostProdError(f"speed out of range (0.25-4.0): {speed}")

    parts: list[str] = []
    remaining = speed
    while remaining > _ATEMPO_MAX:
        parts.append(f"atempo={_ATEMPO_MAX}")
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        parts.append(f"atempo={_ATEMPO_MIN}")
        remaining /= _ATEMPO_MIN
    # `atempo=1.0` is a no-op; emitting it spends a pass for nothing.
    if abs(remaining - 1.0) > 1e-6:
        parts.append(f"atempo={remaining:.6f}".rstrip("0").rstrip("."))
    return parts


def change_speed(
    src: PathLike,
    dst: PathLike,
    *,
    speed: float,
) -> Path:
    """Play a clip faster or slower, picture and sound together.

    Every packaged workflow that sets `video_speed` asks for 1.2 or 1.25,
    and this build ignored the setting completely — so a board run produced
    a clip noticeably longer than the same board through the packaged tool.

    Video is retimed with `setpts`, which takes the RECIPROCAL: 1.25x faster
    is ``setpts=0.8*PTS``. Getting that backwards is the classic error here
    and yields a clip wrong by the square of the factor. Audio goes through
    `build_atempo_chain`, which takes the factor the right way up.

    A clip with no audio track gets the video filter only — telling ffmpeg
    to filter a stream that is not there fails the whole command.
    """
    source = _source(src)
    target = _target(dst)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise PostProdError(f"invalid speed: {speed!r}")
    speed = float(speed)
    if abs(speed - 1.0) < 1e-6:
        raise PostProdError("speed of 1.0 would re-encode for no change")

    # Validated before the command is built so an out-of-range speed fails
    # the same way whether or not the clip happens to have audio.
    audio_filters = build_atempo_chain(speed)

    args = [
        _ffmpeg(), "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-filter:v", f"setpts={1.0 / speed:.6f}*PTS",
    ]
    if has_audio(source):
        args += ["-filter:a", ",".join(audio_filters)]
    else:
        args += ["-an"]
    args += [
        "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-pix_fmt", PIXEL_FORMAT,
        "-movflags", "+faststart",
        str(target),
    ]
    _run(args, what=f"change the speed of {source.name} to {speed}x")
    logger.info("speed %sx applied to %s", speed, target.name)
    return target


def zoom_out_logo(
    src: PathLike,
    dst: PathLike,
    *,
    zoom_percent: float,
    x: int,
    y: int,
    width: int,
    height: int,
) -> Path:
    """Push a watermark off the edge by cropping in, then scaling back.

    This is the packaged tool's own default (`veo_logo_method: "zoom"`, at
    `veo_logo_zoom_percent: 110`). It is a different operation from
    ``remove_logo``: nothing is blurred, the frame is simply tighter, so the
    result has no soft patch — at the cost of losing a border all round.

    The crop window is pushed AWAY from the mark: for a mark in the
    top-right the window sits at the bottom-left, so the mark falls outside
    it. Which corner that is comes from the box the caller passes, not from
    a guess.

    A zoom can be too small to finish the job. Measured on the shipped
    workflow's own numbers — a 1920x1080 frame, a mark at 78%/2% sized
    20%x10% — clearing it needs 114%, and the packaged tool asks for 110%.
    So this logs what the caller would need. It does not silently raise the
    zoom: cropping more than was asked changes the framing of every shot,
    and that is the user's call.

    Geometry is integer pixels, like ``remove_logo``, so nothing the caller
    wrote reaches the filtergraph as a string.
    """
    source = _source(src)
    target = _target(dst)

    if isinstance(zoom_percent, bool) or not isinstance(zoom_percent, (int, float)):
        raise PostProdError(f"invalid zoom_percent: {zoom_percent!r}")
    # Below 100 is not a zoom at all; above 200 the frame is mostly gone.
    if not 100 < float(zoom_percent) <= 200:
        raise PostProdError(
            f"zoom_percent must be above 100 and at most 200, got {zoom_percent!r}"
        )
    for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PostProdError(f"invalid logo {name}: {value!r}")

    frame_w, frame_h = frame_size(source)
    keep_w = max(2, int(frame_w * 100 / float(zoom_percent)))
    keep_h = max(2, int(frame_h * 100 / float(zoom_percent)))
    # ffmpeg's encoders want even dimensions.
    keep_w -= keep_w % 2
    keep_h -= keep_h % 2

    mark_cx = x + width / 2
    mark_cy = y + height / 2
    # Window pushed to the opposite side from the mark's centre.
    off_x = 0 if mark_cx > frame_w / 2 else frame_w - keep_w
    off_y = 0 if mark_cy > frame_h / 2 else frame_h - keep_h
    off_x = max(0, min(off_x, frame_w - keep_w))
    off_y = max(0, min(off_y, frame_h - keep_h))

    cleared_x = (x >= off_x + keep_w) or (x + width <= off_x)
    cleared_y = (y >= off_y + keep_h) or (y + height <= off_y)
    if not (cleared_x or cleared_y):
        # The horizontal and vertical zooms that WOULD clear it, so the
        # message tells the user what to change rather than that it failed.
        needs = []
        if mark_cx > frame_w / 2 and x > 0:
            needs.append(frame_w * 100 / x)
        if mark_cy < frame_h / 2 and (y + height) < frame_h:
            needs.append(frame_h * 100 / (frame_h - (y + height)))
        hint = f"; about {min(needs):.0f}% would" if needs else ""
        logger.warning(
            "zoom_out_logo: %.0f%% does not fully clear the mark%s",
            float(zoom_percent),
            hint,
        )

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf",
            f"crop={keep_w}:{keep_h}:{off_x}:{off_y},"
            f"scale={frame_w}:{frame_h}:flags=lanczos,setsar=1",
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(target),
        ],
        what="zoom_out_logo",
    )
    return target


def extract_audio(src: PathLike, dst: PathLike) -> Path:
    """Pull a small mono speech track out of a video.

    16 kHz mono at a low bitrate: transcription gains nothing from stereo or
    music-grade bandwidth, and this file travels to the API base64-encoded
    inside a JSON body, where size is the binding constraint.
    """
    source = _source(src)
    target = _target(dst)
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
            str(target),
        ],
        what=f"extract audio from {source.name}",
    )
    return target


def extract_frames(src: PathLike, dst_dir: PathLike, count: int) -> list[Path]:
    """Sample ``count`` frames spread across the clip, in order.

    Used to show a vision model what happens over time. Sampling by rate
    keeps this to a single ffmpeg pass instead of one seek per frame, and the
    frames are scaled down because a vision call pays for pixels it cannot
    use.
    """
    source = _source(src)
    out_dir = Path(dst_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if count < 1:
        raise PostProdError("frame count must be at least 1")

    duration = probe_duration(source)
    fps = max(count / duration, 0.001) if duration > 0 else 1.0
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", f"fps={fps:.6f},scale=768:-2",
            "-frames:v", str(count),
            str(out_dir / "frame-%03d.jpg"),
        ],
        what=f"sample {count} frames from {source.name}",
    )
    return sorted(out_dir.glob("frame-*.jpg"))


def contact_sheets(
    src: PathLike,
    dst_dir: PathLike,
    *,
    fps: float = 3.0,
    max_frames: int = 48,
    cols: int = 6,
    rows: int = 4,
) -> tuple[list[Path], int]:
    """Tile the clip's frames into timestamped grids for a vision model.

    Returns ``(sheets in chronological order, frames actually tiled)``.

    Sheets rather than loose frames, ported from flowkit's reviewer: forty
    frames as forty attachments is forty images to pay for, and the model
    cannot see two moments side by side to judge whether a character drifted
    between them. One grid gives it both. The timestamp is burned into each
    frame so the model can say *when* something went wrong rather than only
    that it did.

    **The column count is computed, not fixed**, and this is the part worth
    keeping from the original: ``tile`` fills a grid it cannot complete with
    a solid colour block, and vision models read that block as a defect in
    the video — confirmed there by a live review call. Taking the largest
    divisor of the chunk size guarantees every cell holds a frame.

    A prime chunk degenerates to a single column, which is correct but tall.
    It is rare where it matters: the clips this scores are 8 seconds, and at
    the default 3fps that is 24 frames — exactly ``6x4``.

    Unlike the original this COPIES frames into the chunk directory rather
    than symlinking them. ``os.symlink`` needs Developer Mode or an elevated
    process on Windows, which is where this build runs; the copies are
    downscaled jpegs in a temporary directory, so the cost is nothing next
    to failing outright.
    """
    import shutil

    source = _source(src)
    out_dir = Path(dst_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if fps <= 0:
        raise PostProdError(f"invalid sampling rate {fps}")
    if cols < 1 or rows < 1:
        raise PostProdError(f"invalid sheet grid {cols}x{rows}")
    if max_frames < 1:
        raise PostProdError(f"invalid frame cap {max_frames}")

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    font = assets.font_path()
    stamp = (
        f",drawtext=fontfile={_filter_path(font)}:text='%{{pts\\:hms}}'"
        ":x=5:y=5:fontsize=14:fontcolor=white:borderw=1:bordercolor=black"
        if font
        else ""
    )
    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-i", str(source),
            "-vf", f"fps={fps:.6f},scale=320:-2{stamp}",
            "-q:v", "2",
            str(frames_dir / "frame-%04d.jpg"),
        ],
        what=f"sample frames from {source.name} at {fps}fps",
    )

    frames = sorted(frames_dir.glob("frame-*.jpg"))
    if not frames:
        raise PostProdError(f"no frames could be read from {source}")
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]

    per_sheet = cols * rows
    sheets: list[Path] = []
    for index, start in enumerate(range(0, len(frames), per_sheet)):
        chunk = frames[start : start + per_sheet]
        chunk_dir = out_dir / f"chunk-{index:02d}"
        chunk_dir.mkdir(exist_ok=True)
        for position, frame in enumerate(chunk, start=1):
            shutil.copyfile(frame, chunk_dir / f"f-{position:04d}.jpg")
        wide = max(c for c in range(1, cols + 1) if len(chunk) % c == 0)
        tall = len(chunk) // wide
        sheet = out_dir / f"sheet-{index:02d}.jpg"
        _run(
            [
                _ffmpeg(), "-hide_banner", "-nostdin", "-y",
                "-i", str(chunk_dir / "f-%04d.jpg"),
                "-vf", f"tile={wide}x{tall}:nb_frames={len(chunk)}",
                "-q:v", "2", str(sheet),
            ],
            what=f"tile {len(chunk)} frames into {sheet.name}",
        )
        sheets.append(sheet)

    logger.info("built %d contact sheet(s) from %d frames", len(sheets), len(frames))
    return sheets, len(frames)


def mix_bgm(
    src: PathLike,
    bgm: PathLike,
    dst: PathLike,
    *,
    bgm_volume: float = 0.3,
    orig_volume: float = 1.0,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    loop: bool = True,
    track_start: float = 0.0,
) -> Path:
    """Duck a background track under the existing audio, sized to the video.

    The music is trimmed to the video's exact duration and, with ``loop``, fed
    through ``-stream_loop -1`` first, so a short track never leaves the tail
    silent and a long one never outruns the picture. The original audio is
    padded to the same length and mixed without amix's automatic gain
    normalisation, which would otherwise halve the narration. Video is stream
    copied.
    """
    source = _source(src)
    music = _source(bgm, label="background track")
    target = _target(dst)

    duration = probe_duration(source)
    if duration <= 0:
        raise PostProdError(f"{source} has no measurable duration")

    # Order matters, and it used to be wrong: the fades came first and were
    # therefore placed on the TRACK's timeline, not the excerpt's. With a
    # non-zero `track_start` the fade-in landed inside the intro that `atrim`
    # then cut away, and the fade-out at `duration - fade_out` fell somewhere
    # in the middle of the music — a bed that opens at full volume and never
    # fades. Every shipped workflow sends 0, which is why nothing noticed.
    #
    # `track_start` skips into the music. The packaged tool exposes it as
    # `start_time`; a long track whose first twenty seconds are an intro is
    # exactly why it exists.
    start = max(0.0, track_start)
    music_chain = [
        f"volume={bgm_volume}",
        f"atrim={start:.3f}:{start + duration:.3f}",
        # Rebases the excerpt to t=0 so the fades below mean what they say.
        "asetpts=N/SR/TB",
    ]
    if fade_in > 0:
        music_chain.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        music_chain.append(
            f"afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out}"
        )
    music_filter = "[1:a]" + ",".join(music_chain)

    if has_audio(source):
        # apad then atrim pins both legs to the video length before mixing, so
        # amix cannot decide the output length for us.
        graph = (
            f"[0:a]volume={orig_volume},apad,atrim=0:{duration:.3f},asetpts=N/SR/TB[orig];"
            f"{music_filter}[music];"
            f"[orig][music]amix=inputs=2:duration=longest:normalize=0[aout]"
        )
    else:
        graph = f"{music_filter}[aout]"

    args = [_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-i", str(source)]
    if loop:
        args += ["-stream_loop", "-1"]
    args += [
        "-i", str(music),
        "-filter_complex", graph,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CHANNELS),
        "-movflags", "+faststart",
        str(target),
    ]
    _run(args, what=f"mix {music.name} under {source.name}")
    logger.info("mixed %s under %s (%.2fs)", music.name, target.name, duration)
    return target


def change_audio_speed(src: PathLike, dst: PathLike, *, speed: float) -> Path:
    """Retime narration without touching a picture, because there isn't one.

    `change_speed` is the clip version and cannot be reused: it applies a
    `setpts` video filter, and asking ffmpeg to filter a video stream that a
    voice file does not have fails the whole command.

    The factor goes through `build_atempo_chain`, which handles the 2x cap
    that makes a bare `atempo=3.0` be rejected outright.
    """
    source = _source(src, label="audio")
    target = _target(dst)
    chain = build_atempo_chain(speed)
    if not chain:
        # Speed 1.0 — a copy rather than a re-encode, so a no-op costs
        # nothing and loses no quality.
        shutil.copyfile(source, target)
        return target
    _run(
        [
            _ffmpeg(), "-y", "-i", str(source),
            "-filter:a", ",".join(chain),
            "-vn", str(target),
        ],
        what=f"retime {source.name} to {speed}x",
    )
    return target


def silence(dst: PathLike, *, seconds: float, rate: int = 44100) -> Path:
    """A silent audio file of a given length.

    Used as the gap between narration segments. Generated rather than
    shipped so the sample rate always matches what it is being joined to —
    a 22 kHz silence spliced between 44 kHz parts makes ffmpeg resample the
    whole concat and the joins become audible.
    """
    target = _target(dst)
    _run(
        [
            _ffmpeg(), "-y", "-f", "lavfi",
            "-i", f"anullsrc=r={rate}:cl=mono",
            "-t", f"{max(0.01, float(seconds)):.3f}",
            str(target),
        ],
        what=f"generate {seconds}s of silence",
    )
    return target


def concat_audio(
    parts: Sequence[PathLike], dst: PathLike, *, pause_s: float = 0.0
) -> Path:
    """Join audio files in order, optionally with a gap between them.

    Separate from :func:`concat`, which is for video and enforces a matching
    stream signature. Audio parts from one synthesiser already agree, and the
    filter here re-encodes anyway, so the strictness would only reject valid
    input.

    ``pause_s`` exists because joining sentences with no gap runs them
    together — the synthesiser ends each part on the last syllable, not on a
    breath.
    """
    sources = [_source(p, label="audio part") for p in parts]
    if not sources:
        raise PostProdError("concat_audio needs at least one part")
    target = _target(dst)

    if len(sources) == 1 and pause_s <= 0:
        shutil.copyfile(sources[0], target)
        return target

    gap: Optional[Path] = None
    try:
        ordered: list[Path] = []
        if pause_s > 0:
            gap = target.with_name(f"{target.stem}-gap.wav")
            silence(gap, seconds=pause_s)
            for index, part in enumerate(sources):
                if index:
                    ordered.append(gap)
                ordered.append(part)
        else:
            ordered = list(sources)

        args = [_ffmpeg(), "-y"]
        for part in ordered:
            args += ["-i", str(part)]
        graph = "".join(f"[{i}:a]" for i in range(len(ordered)))
        graph += f"concat=n={len(ordered)}:v=0:a=1[a]"
        args += ["-filter_complex", graph, "-map", "[a]", str(target)]
        _run(args, what=f"join {len(sources)} audio part(s)")
    finally:
        if gap is not None and gap.exists():
            try:
                gap.unlink()
            except OSError:
                pass
    return target


def append_still(
    src: PathLike,
    image: PathLike,
    dst: PathLike,
    *,
    seconds: float = 0.2,
    position: str = "end",
) -> Path:
    """Put a still frame on the front or back of a clip.

    Used for the cover image the packaged workflows attach to a finished
    video (`enable_thumbnail`, 0.2s, at the end).

    Built as one `concat` FILTER rather than through :func:`concat`, which
    uses the demuxer and stream-copies. The demuxer's own docstring explains
    why that would be wrong here: it does not reject a mismatch, it writes a
    file that plays and is wrong. A still has no audio and whatever geometry
    the image happens to be, so it disagrees with the clip on both counts.
    Re-encoding is the price of a file that is actually correct.

    The still is scaled to fit and padded rather than stretched: a 16:9 cover
    squeezed onto a 9:16 clip is a cover nobody recognises.
    """
    source = _source(src)
    still = _source(image, label="thumbnail image")
    target = _target(dst)
    hold = max(0.05, float(seconds))
    at_start = str(position).strip().lower() in ("start", "begin", "front", "dau")

    signature = _concat_signature(source)
    width = int(signature.get("width") or 0)
    height = int(signature.get("height") or 0)
    if width <= 0 or height <= 0:
        raise PostProdError(f"{source} has no usable frame size")

    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps=30,format=yuv420p"
    )
    # Both legs need an audio stream or concat pairs a clip against nothing
    # and the result desyncs — the same failure the demuxer path warns about.
    graph = (
        f"[1:v]{fit}[still];"
        f"[0:v]scale={width}:{height},setsar=1,fps=30,format=yuv420p[main];"
    )
    if at_start:
        graph += "[still][2:a][main][0:a]concat=n=2:v=1:a=1[v][a]"
    else:
        graph += "[main][0:a][still][2:a]concat=n=2:v=1:a=1[v][a]"

    args = [_ffmpeg(), "-y", "-i", str(source), "-loop", "1", "-t", f"{hold:.3f}",
            "-i", str(still)]
    if has_audio(source):
        args += ["-f", "lavfi", "-t", f"{hold:.3f}",
                 "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    else:
        # No audio anywhere: drop the audio leg entirely rather than
        # inventing a silent track the source never had.
        graph = graph.replace("[2:a]", "").replace(
            "concat=n=2:v=1:a=1[v][a]", "concat=n=2:v=1:a=0[v]"
        ).replace("[0:a]", "")
        args += []
    args += ["-filter_complex", graph, "-map", "[v]"]
    if has_audio(source):
        args += ["-map", "[a]", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)]

    _run(args, what=f"append still to {source.name}")
    return target


def ken_burns(
    image: PathLike,
    audio: PathLike,
    dst: PathLike,
    *,
    zoom_in: bool = True,
    zoom_speed: float = 0.002,
    width: int = 1920,
    height: int = 1080,
    fps: int = DEFAULT_FPS,
) -> Path:
    """A still image slowly zoomed over a voice track.

    The packaged tool calls this `sync_image_voice`; its own filter strings
    are `zoompan=z='min(zoom+…` for a zoom in and `zoompan=z='max(1.15-…`
    for a zoom out, which is what the two branches below reproduce.

    The clip runs exactly as long as the audio. `zoompan` counts in FRAMES,
    not seconds, so the duration is probed and multiplied — getting that
    wrong produces a clip that freezes early or cuts the narration off.

    `zoompan` samples the SCALED image, so the input is enlarged first: run
    on the source resolution it produces the shaky, stepped zoom that gives
    cheap slideshows away.
    """
    src = _source(image, label="image")
    track = _source(audio, label="audio")
    target = _target(dst)

    for name, value in (("width", width), ("height", height), ("fps", fps)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise PostProdError(f"invalid {name}: {value!r}")
    if not isinstance(zoom_speed, (int, float)) or isinstance(zoom_speed, bool):
        raise PostProdError(f"invalid zoom_speed: {zoom_speed!r}")
    # Above ~0.02 per frame the zoom crosses the whole image in a second and
    # reads as a glitch rather than a move.
    zoom_speed = max(0.0001, min(0.02, float(zoom_speed)))

    seconds = probe_duration(track)
    if seconds <= 0:
        raise PostProdError("narration has no duration")
    frames = max(1, int(round(seconds * fps)))

    # Sampling resolution for the pan. 4x is enough to keep the motion smooth
    # without making ffmpeg allocate an enormous intermediate.
    sample_w, sample_h = width * 4, height * 4
    zoom = (
        f"min(zoom+{zoom_speed},1.5)"
        if zoom_in
        else f"max(1.15-on*{zoom_speed},1.0)"
    )
    vf = (
        f"scale={sample_w}:{sample_h}:force_original_aspect_ratio=increase,"
        f"crop={sample_w}:{sample_h},"
        f"zoompan=z='{zoom}':d={frames}:s={width}x{height}:fps={fps}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)',"
        f"setsar=1"
    )

    _run(
        [
            _ffmpeg(), "-y",
            "-loop", "1", "-i", str(src),
            "-i", str(track),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            # The looped image is an infinite stream; without -shortest the
            # encode never ends.
            "-shortest",
            "-movflags", "+faststart",
            str(target),
        ],
        what="ken_burns",
    )
    return target


def trim_to_audio(video: PathLike, audio: PathLike, dst: PathLike) -> Path:
    """Fit a scene's picture to its narration track.

    Strategy is loop-and-cut: the video is looped indefinitely and the result
    hard-cut at the narration's duration. A narration longer than the clip
    replays the clip instead of freezing on a held frame or slowing motion —
    generated b-roll loops far less visibly than it time-stretches — and a
    shorter narration simply trims. The narration is never cut: ``-t`` keeps
    every video frame that starts before it ends, so the picture can run up to
    one frame past the audio. The output's only audio track is that narration.
    """
    source = _source(video, label="video")
    narration = _source(audio, label="narration")
    target = _target(dst)

    duration = probe_duration(narration)
    if duration <= 0:
        raise PostProdError(f"{narration} has no measurable duration")

    _run(
        [
            _ffmpeg(), "-hide_banner", "-nostdin", "-y",
            "-stream_loop", "-1", "-i", str(source),
            "-i", str(narration),
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", f"{duration:.3f}",
            "-c:v", VIDEO_CODEC, "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
            "-pix_fmt", PIXEL_FORMAT,
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            "-ar", str(AUDIO_RATE), "-ac", str(AUDIO_CHANNELS),
            "-movflags", "+faststart",
            str(target),
        ],
        what=f"fit {source.name} to {narration.name}",
    )
    logger.info("fitted %s to %.2fs of narration -> %s", source.name, duration, target.name)
    return target
