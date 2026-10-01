"""Worker handler for local ffmpeg/RealESRGAN/TTS post-production.

One request type, ``postprod``, carries ``{"op": <name>, ...}`` so every
canvas post-production node shares this single dispatch path instead of
minting a new worker request type per operation (concat, subtitles, bgm,
...). Adding an operation is a dict entry in ``_OPS``, not a new handler
wired into the worker.

Every op takes media ids, never filesystem paths: ``media.cached_path``
resolves each one, and a missing id is a hard failure rather than a
silently-dropped input — a concat that quietly drops a clip produces a
playable file that is simply the wrong one. Every op's output is adopted
back into the media cache via ``media.ingest_local_file`` so downstream
nodes can reference it the same way they reference a Flow-generated clip.

This drives the exact same ``services.postprod`` / ``services.upscale`` /
``services.tts`` functions the HTTP routes in ``routes.postprod`` already
call, through the same conventions — blocking work offloaded via
``run_in_threadpool``, output paths confined by the routes' own
``_resolve_output`` — so behaviour never drifts between the manual UI and
the canvas graph.
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from shutil import rmtree
from typing import Any, Awaitable, Callable, Optional

from fastapi.concurrency import run_in_threadpool

from flowboard.routes.postprod import MAX_CONCAT_CLIPS, _resolve_output
from flowboard.services import assets
from flowboard.services import media as media_service
from flowboard.services import postprod, tts, upscale

logger = logging.getLogger(__name__)

# Exceptions the underlying toolchains raise for bad input or a missing
# binary/key — the same set routes.postprod._run converts to a 4xx/5xx.
# TypeError is added on top: params here arrive as unvalidated JSON (the
# HTTP routes validate shape with pydantic first), so a caller sending a
# string where a number belongs surfaces as a bare TypeError deep in a
# comparison rather than one of the toolchain's own typed errors.
_EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    postprod.PostProdError,
    tts.TTSError,
    RuntimeError,
    ValueError,
    OSError,
    FileNotFoundError,
    TypeError,
)

OpHandler = Callable[[dict], Awaitable[tuple[dict, Optional[str]]]]


async def _run(fn: Callable[..., Path], *args: Any, **kwargs: Any) -> Path:
    """Offload one blocking ffmpeg/RealESRGAN/TTS call.

    These are synchronous, CPU-heavy calls; running one inline would stall
    the event loop the rest of the queue (and the extension bridge) depend
    on, exactly the reason routes.postprod wraps the same functions.
    """
    return await run_in_threadpool(fn, *args, **kwargs)


def _new_output(suffix: str) -> Path:
    """A confined path for one ffmpeg pass to write into.

    The name is generated here, never taken from caller params, and still
    routed through the routes' own ``_resolve_output`` so containment stays
    a single source of truth instead of a rule duplicated by hand.
    """
    return _resolve_output(f"postprod-{uuid.uuid4().hex}{suffix}")


def _resolve_media(params: dict, key: str) -> tuple[Optional[Path], Optional[str]]:
    """One required media-id param -> a cached Path, or a clear error.

    Never silently drops a missing input: returning None here for a caller
    to skip past would make e.g. a concat quietly join fewer clips than
    asked, or a subtitle burn quietly skip the captions — both produce a
    file that plays fine and is simply wrong.
    """
    media_id = params.get(key)
    if not isinstance(media_id, str) or not media_id.strip():
        return None, f"missing_{key}"
    media_id = media_id.strip()
    path = media_service.cached_path(media_id)
    if path is None:
        return None, f"missing_media:{media_id}"
    return path, None


def _ingest(path: Path, *, kind: str, op: str) -> dict:
    """Adopt an op's output into the media cache and shape the handler result.

    Keys match what ``_handle_gen_image`` / ``_handle_gen_video`` already
    return (``media_ids``), so the canvas reads a postprod result with the
    same code path it uses for a Flow generation.
    """
    media_id = media_service.ingest_local_file(path, kind=kind)
    return {"op": op, "media_ids": [media_id], "path": str(path)}


def _error_text(exc: BaseException) -> str:
    """Flatten a (often multi-line ffmpeg stderr) failure into one line.

    Mirrors ``routes.postprod._client_detail``: keeps a stack of stderr
    lines out of ``request.error`` while leaving the real reason legible.
    """
    return " ".join(str(exc).split())[:300]


# ── ops ──────────────────────────────────────────────────────────────────


async def _op_concat(params: dict) -> tuple[dict, Optional[str]]:
    """Normalize every clip to one profile, then join — same as /api/postprod/concat."""
    clips = params.get("clips")
    if not isinstance(clips, list) or not clips:
        return {}, "missing_clips"
    if len(clips) > MAX_CONCAT_CLIPS:
        return {}, f"too_many_clips:{len(clips)}"

    sources: list[Path] = []
    for index, media_id in enumerate(clips):
        if not isinstance(media_id, str) or not media_id.strip():
            return {}, f"missing_clip_at_{index}"
        path = media_service.cached_path(media_id.strip())
        if path is None:
            return {}, f"missing_media:{media_id}"
        sources.append(path)

    width = params.get("width", 1080)
    height = params.get("height", 1920)
    fps = params.get("fps", 30)

    dst = _new_output(".mp4")
    # Private staging dir, not named off the output: two concurrent concats
    # must not overwrite each other's half-written normalize passes.
    staging = Path(tempfile.mkdtemp(prefix=".concat-", dir=dst.parent))
    try:
        staged: list[Path] = []
        for index, src in enumerate(sources):
            tmp = staging / f"norm{index:03d}.mp4"
            staged.append(await _run(postprod.normalize, src, tmp, width, height, fps))
        out = await _run(postprod.concat, staged, dst)
    finally:
        rmtree(staging, ignore_errors=True)
    return _ingest(out, kind="video", op="concat"), None


async def _op_transcribe(params: dict) -> tuple[dict, Optional[str]]:
    """Turn a clip's speech into an SRT and adopt it as media.

    Not ffmpeg — this one calls a speech-to-text provider, so unlike the
    rest of this module it can cost quota or money. The cost estimate counts
    it separately for that reason.

    Routed through `stt` rather than straight at Gemini: Gemini is the only
    provider on this stack that takes audio, so a direct call made subtitles
    rest on one supplier, and when its CLI stopped accepting individual
    accounts every subtitle stopped with it. `stt` tries whatever is
    configured — Gemini, whisper-1, local faster-whisper — and says which
    one answered.

    The SRT comes back as a media id so the `subtitles` op that follows can
    take it the same way it takes any other input, instead of this pass
    needing a private channel to the next one.
    """
    from flowboard.config import STORAGE_DIR
    from flowboard.services import stt, transcribe as transcribe_service

    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    language = params.get("language")
    want_words = bool(params.get("wantWords"))
    try:
        result = await stt.to_srt(
            video,
            language=language if isinstance(language, str) else None,
            storage_dir=STORAGE_DIR,
            want_words=want_words,
        )
    except stt.SttError as exc:
        if exc.kind == "no_speech":
            # A legitimate outcome, and it must not fail the node: failing
            # here throws away every pass that already succeeded — the
            # watermark removal, the aspect conversion, the music — because
            # the clip had nothing to say.
            #
            # An empty SRT lands on the path that already exists for this:
            # `_srt_has_cues` is False, so both burn ops call `_skip_burn`
            # and hand the clip back untouched. One decision, one place.
            logger.info("transcribe: no speech in the clip, subtitles skipped")
            empty = _new_output(".srt")
            empty.write_text("", encoding="utf-8-sig")
            out = _ingest(empty, kind="subtitle", op="transcribe")
            out["note"] = "Clip không có tiếng nói — bỏ qua phụ đề."
            return out, None
        return {}, f"transcribe_{exc.kind}: {_error_text(exc)}"

    logger.info(
        "transcribe: %s answered%s",
        result.source,
        f" with {len(result.words)} word timing(s)" if result.words else "",
    )
    out = _new_output(".srt")
    await run_in_threadpool(transcribe_service.write_srt, result.srt, out)
    if result.words:
        # A sidecar, because ops exchange media ids and a private channel
        # between these two would make them the only pair in the module that
        # talk to each other directly. Best-effort: real timings are an
        # improvement on the approximation, never a requirement for it.
        _write_word_sidecar(out, result.words)
    return _ingest(out, kind="subtitle", op="transcribe"), None


#: Suffix of the file that carries word timings beside an SRT.
_WORDS_SUFFIX = ".words.json"


def _write_word_sidecar(srt: Path, words: list) -> None:
    import json

    payload = [
        {"text": w.text, "start": w.start, "end": w.end}
        for w in words
        if getattr(w, "text", None)
    ]
    try:
        srt.with_suffix(srt.suffix + _WORDS_SUFFIX).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:  # pragma: no cover - a locked disk is not fatal
        logger.info("transcribe: could not write word timings (%s)", exc)


def _read_word_sidecar(srt: Path) -> list:
    """The word timings written beside this SRT, or an empty list."""
    import json

    path = srt.with_suffix(srt.suffix + _WORDS_SUFFIX)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    from flowboard.services.stt import Word

    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                Word(
                    text=str(item["text"]),
                    start=float(item["start"]),
                    end=float(item["end"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _srt_has_cues(srt: Path) -> bool:
    """Whether an SRT holds at least one line ffmpeg could burn.

    Worth checking before either burn, because ffmpeg's own answer is both
    fatal and misleading. Measured on 2026-09-02: given an SRT with no cues,
    the `subtitles` filter exits non-zero saying ``Unable to open <path>``
    for a file that is present and readable. That failure would fail the
    node and discard every pass that already succeeded, over a clip whose
    only problem is having nothing to say.

    Parsing borrows `karaoke.parse_srt` rather than growing a second SRT
    reader; that one is already tolerant of the malformed blocks a model
    produces.
    """
    from flowboard.services import karaoke

    return bool(karaoke.parse_srt(srt.read_text(encoding="utf-8-sig", errors="replace")))


def _skip_burn(params: dict, video: Path, op: str) -> tuple[dict, Optional[str]]:
    """Hand back the untouched clip when there is nothing to burn onto it.

    Returns the caller's own media id, not a re-ingest: the bytes are
    unchanged, and minting a new id for them would copy the whole clip into
    the cache a second time — the same lesson `remove_watermark` learned.
    """
    logger.info("%s: the SRT has no cues, leaving the clip unchanged", op)
    return {
        "op": op,
        "media_ids": [str(params["video"]).strip()],
        "path": str(video),
        "note": "Phụ đề trống — giữ nguyên video gốc.",
    }, None


async def _op_subtitles(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    srt, err = _resolve_media(params, "srt")
    if err:
        return {}, err
    if not await run_in_threadpool(_srt_has_cues, srt):
        return _skip_burn(params, video, "subtitles")
    out = await _run(
        postprod.burn_subtitles,
        video,
        srt,
        _new_output(".mp4"),
        font=params.get("font", assets.DEFAULT_SUBTITLE_FONT),
        size=params.get("size", 48),
        primary_color=params.get("primaryColor", "&H00FFFFFF"),
        outline_color=params.get("outlineColor", "&H00000000"),
        outline_width=params.get("outlineWidth", 2.0),
        shadow=params.get("shadow", 0.0),
        margin_v=params.get("marginV", 60),
    )
    return _ingest(out, kind="video", op="subtitles"), None


async def _op_karaoke(params: dict) -> tuple[dict, Optional[str]]:
    """Burn subtitles that light up word by word.

    Takes the same SRT the plain `subtitles` op does and converts it to ASS
    with a `\\kf` tag per word. The word timings are DERIVED from each
    line's own duration rather than asked of a model — see
    `services.karaoke` for the measurement behind that choice.

    Falls back to `_op_subtitles` when the SRT yields no usable cues, which
    keeps the decision about what to do with a cue-less SRT in exactly one
    place — that op skips the burn and hands the clip back untouched.
    """
    from flowboard.services import karaoke

    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    srt_path, err = _resolve_media(params, "srt")
    if err:
        return {}, err

    font = params.get("font", assets.DEFAULT_SUBTITLE_FONT)
    resolved_font = assets.font_path(font)
    # libass matches on the FAMILY name, not the file stem — the same
    # resolution `burn_subtitles` does before building its force_style.
    family = (
        postprod.font_family(str(resolved_font)) if resolved_font else None
    ) or "Arial"

    # libass scales every size in the file by the real frame height over
    # PlayResY, so the ASS has to be written in the clip's own resolution.
    # Declaring a portrait default against a landscape clip shrinks the
    # captions to 56% of the requested size — wrong, and quietly so.
    width, height = await run_in_threadpool(postprod.frame_size, video)

    # Real timings when the transcribe pass produced them. `srt_to_ass`
    # decides per cue whether they cover it convincingly enough to use, and
    # falls back to the length-proportional split when they do not — half a
    # line aligned and half guessed is worse than a whole line guessed.
    aligned = _read_word_sidecar(srt_path)
    if aligned:
        logger.info("karaoke: using %d real word timing(s)", len(aligned))

    ass_path = _new_output(".ass")
    written = await _run(
        karaoke.srt_to_ass,
        srt_path.read_text(encoding="utf-8-sig", errors="replace"),
        ass_path,
        aligned=aligned or None,
        font=family,
        play_res_x=width,
        play_res_y=height,
        size=int(params.get("size", 48)),
        primary_color=params.get("primaryColor", "&H0000FFFF"),
        inactive_color=params.get("inactiveColor", "&H00FFFFFF"),
        outline_color=params.get("outlineColor", "&H00000000"),
        outline_width=float(params.get("outlineWidth", 2.0)),
        shadow=float(params.get("shadow", 0.0)),
        margin_v=int(params.get("marginV", 60)),
    )
    if written is None:
        logger.info("karaoke: no cues in the SRT, falling back to a plain burn")
        return await _op_subtitles(params)

    out = await _run(postprod.burn_ass, video, written, _new_output(".mp4"))
    return _ingest(out, kind="video", op="karaoke"), None


def _background_image(value: object) -> tuple[Optional[Path], Optional[str]]:
    """Resolve a backdrop that may be a media id, a path, or neither.

    Returns ``(path, note)`` — a note only when a backdrop was asked for and
    could not be found, which is the common case for an imported workflow:
    the shipped ones name the original author's own machine
    (``D:/TOOL/anh nv/Người Que/background_nguoi_que.png``). Falling back to
    the flat colour keeps the node running; saying so keeps the result from
    looking like the backdrop simply did not apply.
    """
    if not isinstance(value, str) or not value.strip():
        return None, None
    text = value.strip()

    cached = media_service.cached_path(text)
    if cached is not None:
        return cached, None

    # A raw path is confined exactly as the HTTP routes confine theirs.
    # Without this the worker reached the same ffmpeg call by a route that
    # asked nobody: a board could name any file on the machine and have
    # ffmpeg read it. `routes/postprod._input_roots` is the single list of
    # places an input may come from, so it is the one consulted here too.
    candidate = Path(text).expanduser()
    if candidate.is_file() and _input_allowed(candidate):
        return candidate, None
    if candidate.is_file():
        logger.warning(
            "aspect: background image %r is outside the allowed input roots", text
        )
        return None, (
            "Ảnh nền nằm ngoài thư mục được phép — dùng màu nền thay thế."
        )

    logger.info("aspect: background image %r not found, using the flat colour", text)
    return None, "Không tìm thấy ảnh nền — dùng màu nền thay thế."


def _input_allowed(path: Path) -> bool:
    """Whether an input path is inside a root this app may read from.

    Borrows the routes' own list rather than growing a second one: two lists
    of "where files may come from" drift, and the drift is a hole.
    """
    from flowboard.routes.postprod import _input_roots, _within

    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(_within(resolved, root) for root in _input_roots())


async def _op_aspect(params: dict) -> tuple[dict, Optional[str]]:
    """Re-frame a clip into another aspect ratio over a background.

    The card-on-a-backdrop pass, not a letterbox — `postprod.convert_aspect`
    carries the geometry and the reason its filtergraph looks the way it
    does.
    """
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err

    ratio = params.get("ratio")
    if not isinstance(ratio, str) or not ratio.strip():
        return {}, "missing_ratio"

    background, note = _background_image(params.get("backgroundImage"))

    out = await _run(
        postprod.convert_aspect,
        video,
        _new_output(".mp4"),
        ratio=ratio.strip(),
        background_image=background,
        background_color=params.get("backgroundColor", "#000000"),
        zoom=float(params.get("zoom", 100.0)),
        border_radius=int(params.get("borderRadius", 0)),
    )
    result = _ingest(out, kind="video", op="aspect")
    if note:
        result["note"] = note
    return result, None


def _text_arts(raw: object) -> list[postprod.TextArt]:
    """The `aspect_texts` payload as TextArt, ignoring fields we do not draw.

    Filtered against the dataclass's own fields rather than popped by name:
    the packaged tool's dialog writes keys this build has no pass for
    (`y_type`, background padding), and an unexpected one must not be a
    TypeError deep inside the constructor.
    """
    if not isinstance(raw, list):
        return []
    fields = set(postprod.TextArt.__dataclass_fields__)
    out: list[postprod.TextArt] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kwargs = {k: v for k, v in entry.items() if k in fields and v is not None}
        if not str(kwargs.get("text", "")).strip():
            continue
        out.append(postprod.TextArt(**kwargs))
    return out


async def _op_text_art(params: dict) -> tuple[dict, Optional[str]]:
    """Draw the workflow's styled captions over the clip."""
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err

    texts = _text_arts(params.get("texts"))
    if not texts:
        return {}, "missing_texts"

    out, notes = await run_in_threadpool(
        postprod.overlay_text_art, video, _new_output(".mp4"), texts
    )
    result = _ingest(out, kind="video", op="text_art")
    if notes:
        result["note"] = " ".join(notes)
    return result, None


async def _op_review(params: dict) -> tuple[dict, Optional[str]]:
    """Score a clip against the prompt that asked for it.

    Produces no media: the result IS the answer, so it carries the clip's
    own media id forward unchanged. A review node in the middle of a chain
    must not cost the chain its video.
    """
    from flowboard.config import STORAGE_DIR
    from flowboard.services import video_review

    video, err = _resolve_media(params, "video")
    if err:
        return {}, err

    prompt = params.get("prompt")
    try:
        review = await video_review.review_clip(
            video,
            prompt=prompt if isinstance(prompt, str) else "",
            storage_dir=STORAGE_DIR,
        )
    except video_review.ReviewError as exc:
        return {}, f"review_failed: {str(exc)[:180]}"

    threshold = params.get("threshold")
    passed = None
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        passed = review.score >= float(threshold)

    return {
        "op": "review",
        "media_ids": [str(params["video"]).strip()],
        "path": str(video),
        "review": {
            "score": review.score,
            "verdict": review.verdict,
            "dimensions": review.dimensions,
            "issues": [
                {
                    "severity": i.severity,
                    "timeRange": i.time_range,
                    "description": i.description,
                }
                for i in review.issues
            ],
            "usableSegments": review.usable_segments,
            "fixHint": review.fix_hint,
            "frames": review.frames,
            "sheets": review.sheets,
            "hasCritical": review.has_critical,
            "passed": passed,
        },
    }, None


async def _op_bgm(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    track_id = params.get("track")
    if not isinstance(track_id, str) or not track_id.strip():
        return {}, "missing_track"
    track_id = track_id.strip()
    # A bundled library track is pre-approved (assets.bgm_path confines the
    # lookup to BGM_DIR); anything else must already be a cached media id —
    # the same two-step fallback the /bgm route uses for a caller path.
    track = assets.bgm_path(track_id) or media_service.cached_path(track_id)
    if track is None:
        return {}, f"missing_media:{track_id}"
    out = await _run(
        postprod.mix_bgm,
        video,
        track,
        _new_output(".mp4"),
        bgm_volume=params.get("bgmVolume", 0.3),
        orig_volume=params.get("origVolume", 1.0),
        fade_in=params.get("fadeIn", 0.0),
        fade_out=params.get("fadeOut", 0.0),
        track_start=params.get("trackStart", 0.0),
    )
    return _ingest(out, kind="video", op="bgm"), None


async def _op_voice_speed(params: dict) -> tuple[dict, Optional[str]]:
    """Retime the narration before it is mixed in.

    Separate op from `speed`, which retimes a clip. A voice file has no
    video stream, and `change_speed`'s `setpts` filter would fail on it.
    """
    audio, err = _resolve_media(params, "audio")
    if err:
        return {}, err
    speed = params.get("speed")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return {}, f"invalid_speed_{speed!r}"[:80]
    out = await _run(
        postprod.change_audio_speed, audio, _new_output(".m4a"), speed=float(speed)
    )
    return _ingest(out, kind="audio", op="voice_speed"), None


async def _op_thumbnail_insert(params: dict) -> tuple[dict, Optional[str]]:
    """Put a cover frame on the front or back of the finished clip.

    Separate from `_op_thumbnail`, which EXTRACTS a still out of a video.
    Same word, opposite direction — worth two names rather than a flag.
    """
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    image, err = _resolve_media(params, "image")
    if err:
        return {}, err
    out = await _run(
        postprod.append_still,
        video,
        image,
        _new_output(".mp4"),
        seconds=params.get("seconds", 0.2),
        position=params.get("position", "end"),
    )
    return _ingest(out, kind="video", op="thumbnail_insert"), None


async def _op_title(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        return {}, "missing_text"
    out = await _run(
        postprod.overlay_title,
        video,
        _new_output(".mp4"),
        text,
        font=params.get("font", assets.DEFAULT_SUBTITLE_FONT),
        size=params.get("size", 64),
        color=params.get("color", "white"),
        box_opacity=params.get("boxOpacity", 0.5),
        y_ratio=params.get("yRatio", 0.08),
    )
    return _ingest(out, kind="video", op="title"), None


async def _op_logo(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    logo, err = _resolve_media(params, "logo")
    if err:
        return {}, err
    out = await _run(
        postprod.overlay_logo,
        video,
        logo,
        _new_output(".mp4"),
        params.get("x", 40),
        params.get("y", 40),
        params.get("width"),
        params.get("height"),
    )
    return _ingest(out, kind="video", op="logo"), None


async def _op_zoom_logo(params: dict) -> tuple[dict, Optional[str]]:
    """Crop in so the watermark falls outside the frame.

    The packaged tool's default method. Geometry arrives as percentages of
    the frame, resolved to pixels here for the same reason `delogo` does:
    the frame size is not knowable until the file is probed.
    """
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    box, err = await _percent_box(params, video)
    if err:
        return {}, err
    try:
        zoom = float(params.get("zoomPercent", 110))
    except (TypeError, ValueError):
        return {}, "invalid_zoom_percent"
    out = await _run(
        postprod.zoom_out_logo,
        video,
        _new_output(".mp4"),
        zoom_percent=zoom,
        **box,
    )
    return _ingest(out, kind="video", op="zoom_logo"), None


async def _percent_box(params: dict, video) -> tuple[dict, Optional[str]]:
    """Frame-relative geometry → integer pixels.

    Shared by the two watermark methods so they cannot disagree about what
    `xPct` means.
    """
    pct_keys = ("xPct", "yPct", "widthPct", "heightPct")
    missing = [k for k in pct_keys if k not in params]
    if missing:
        return {}, f"missing_{missing[0]}"
    try:
        width_px, height_px = await run_in_threadpool(postprod.frame_size, video)
    except _EXPECTED_ERRORS as exc:
        return {}, _error_text(exc)
    try:
        box = {
            "x": int(float(params["xPct"]) / 100 * width_px),
            "y": int(float(params["yPct"]) / 100 * height_px),
            "width": max(1, int(float(params["widthPct"]) / 100 * width_px)),
            "height": max(1, int(float(params["heightPct"]) / 100 * height_px)),
        }
    except (TypeError, ValueError):
        return {}, "invalid_delogo_percent"
    box["width"] = min(box["width"], max(1, width_px - box["x"]))
    box["height"] = min(box["height"], max(1, height_px - box["y"]))
    return box, None


async def _op_delogo(params: dict) -> tuple[dict, Optional[str]]:
    """Blur out a watermark.

    Geometry arrives either as pixels (`x`/`y`/`width`/`height`) or as
    percentages of the frame (`xPct`/…), which is how the packaged tool
    stores it. Percentages are resolved here rather than in the planner
    because that is the first place the frame size is knowable — and
    `remove_logo` stays integer-only, so nothing the caller wrote reaches
    the filtergraph.
    """
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err

    pct_keys = ("xPct", "yPct", "widthPct", "heightPct")
    if any(k in params for k in pct_keys):
        box, err = await _percent_box(params, video)
        if err:
            return {}, err
    else:
        for field in ("x", "y", "width", "height"):
            if field not in params:
                return {}, f"missing_{field}"
        box = {f: params[f] for f in ("x", "y", "width", "height")}

    out = await _run(
        postprod.remove_logo,
        video,
        _new_output(".mp4"),
        **box,
    )
    return _ingest(out, kind="video", op="delogo"), None


async def _op_speed(params: dict) -> tuple[dict, Optional[str]]:
    """Retime a clip, picture and sound together.

    `postprod.change_speed` owns the two traps: `setpts` takes the
    reciprocal of the factor, and `atempo` is capped at 2x so anything
    faster needs a chain. Both are validated there, which is why this passes
    the number through rather than massaging it.
    """
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    speed = params.get("speed")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        return {}, f"invalid_speed_{speed!r}"[:80]

    out = await _run(postprod.change_speed, video, _new_output(".mp4"), speed=float(speed))
    return _ingest(out, kind="video", op="speed"), None


async def _op_remove_watermark(params: dict) -> tuple[dict, Optional[str]]:
    """Detect and inpaint the generator's watermark.

    The other half of `delogo`, and the better half where it applies.
    `delogo` blurs a rectangle the caller nominates and leaves a smudge; this
    runs the packaged tool's own detector, which found `Veo-text 23x10` on a
    real clip and reconstructed the sky behind it, keeping both the audio
    track and the frame size.

    `region` is optional and usually should be left unset: detection is what
    the tool is good at, and always nominating a box would throw that away
    and reproduce delogo's weakness. It accepts the tool's own syntax
    (`br:auto`, `x,y,w,h`), which is how the packaged workflows' `veo_logo_*`
    percentages can drive it when a caller does want to be explicit.
    """
    from flowboard.services import watermark

    video, err = _resolve_media(params, "video")
    if err:
        return {}, err

    region = params.get("region")
    if region is not None and not isinstance(region, str):
        return {}, f"invalid_region_{region!r}"[:80]

    try:
        result = await watermark.remove_from_video(
            video, _new_output(".mp4"), region=region or None
        )
    except watermark.WatermarkError as exc:
        # The detector could not run at all: no GPU, no executable, a file it
        # refused. The packaged tool answers this case in its own words — its
        # edit pass logs "Xóa logo Gemini: ưu tiên GPU, tự chuyển CPU nếu GPU
        # không khả dụng", and the CPU half of that is the model shipped
        # beside it. So the fallback gets its turn before this is a failure.
        painted = await _inpaint_fallback(
            video, params, reason="Máy dò không chạy được"
        )
        if painted is not None:
            return painted, None
        if params.get("optional"):
            # One pass of an `edit_video` chain. Failing here would throw
            # away the aspect conversion, the music and the subtitles of the
            # same node over a watermark pass that could not run — the clip
            # is intact, so hand it on and say what did not happen.
            logger.warning("remove_watermark: skipped — %s", exc)
            return _unchanged(params, video, f"Không xoá được watermark: {exc}"), None
        return {}, str(exc)[:200]

    if not result.detected:
        # The detector declined. Measured on 2026-09-02, it does that on
        # frames that plainly carry a Veo logo — so "not detected" is not the
        # same as "clean", and the packaged tool ships MI-GAN for exactly
        # this moment (its NOTICE: a CPU fallback for "when the video engine
        # skipped removal").
        #
        # Only with a box, because MI-GAN fills a hole someone points at; it
        # does not detect. No box and there is nothing to point at.
        painted = await _inpaint_fallback(
            video, params, reason="Máy dò không thấy watermark"
        )
        if painted is not None:
            return painted, None

        # Nothing to remove — hand back the id the caller already has.
        # Failing the node would stop a whole chain over a clip that was
        # simply already clean, and re-ingesting (the first version did)
        # copied the full clip into the cache a second time just to mint a
        # new id for identical bytes.
        return _unchanged(params, video, result.note), None
    return _ingest(result.path, kind="video", op="remove_watermark"), None


def _unchanged(params: dict, video, note: Optional[str]) -> dict:
    """The clip as it arrived, under the id the caller already has.

    Re-ingesting identical bytes (the first version did) copied the whole clip
    into the cache a second time just to mint a new id, and failing the node
    would stop a chain over a clip that was simply already clean.
    """
    return {
        "op": "remove_watermark",
        "media_ids": [str(params["video"]).strip()],
        "path": str(video),
        "note": note,
    }


async def _inpaint_fallback(
    video, params: dict, *, reason: str
) -> Optional[dict]:
    """MI-GAN on the CPU, when the detector cannot do it and a box is known.

    Returns None — not an error — whenever it cannot or should not run. This
    is the last of three chances at the same job, and a fallback that turns
    "already clean" into a failed node would be worse than the gap it fills.
    """
    from flowboard.services import inpaint

    box = params.get("fallbackBox")
    if not isinstance(box, dict) or not box:
        return None
    if not inpaint.available():
        logger.info("remove_watermark: no inpaint fallback (%s)",
                    inpaint.unavailable_reason())
        return None

    try:
        out = await _run(inpaint.inpaint_video, video, _new_output(".mp4"), box)
    except inpaint.InpaintError as exc:
        # Say so and fall through to "nothing removed". The clip is intact
        # either way, and stopping the chain here would cost the passes that
        # already succeeded.
        logger.warning("remove_watermark: inpaint fallback failed — %s", exc)
        return None

    logger.info("remove_watermark: %s — MI-GAN painted the box out", reason)
    payload = _ingest(out, kind="video", op="remove_watermark")
    payload["note"] = f"{reason} — đã dùng MI-GAN (CPU) xoá theo vùng đã chỉ."
    return payload


async def _op_upscale(params: dict) -> tuple[dict, Optional[str]]:
    source, err = _resolve_media(params, "source")
    if err:
        return {}, err
    model = params.get("model", upscale.DEFAULT_MODEL)
    # The model runs at the rate it was trained for. `scale` stays for a caller
    # that really means a model scale, but a node asking for "x2" arrives as
    # `targetScale`: both bundled models upscale by exactly 4x and
    # `upscale._validate` refuses anything else, so a multiplier is a resample
    # target — and only the source's own height turns it into one.
    scale = params.get("scale") or upscale.model_scale(model) or 4
    if params.get("kind") == "image":
        out = await _run(upscale.upscale_image, source, _new_output(".png"), scale, model)
        return _ingest(out, kind="image", op="upscale"), None
    height = params.get("targetHeight")
    if height is None and params.get("targetScale"):
        height = await _run(
            upscale.height_for_scale, source, float(params["targetScale"])
        )
    out = await _run(
        upscale.upscale_video,
        source,
        _new_output(".mp4"),
        scale,
        model,
        height,
    )
    return _ingest(out, kind="video", op="upscale"), None


async def _op_last_frame(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    out = await _run(
        postprod.last_frame, video, _new_output(".jpg"), width=params.get("width")
    )
    return _ingest(out, kind="image", op="last_frame"), None


async def _op_thumbnail(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    out = await _run(
        postprod.grab_thumbnail,
        video,
        _new_output(".jpg"),
        at_seconds=params.get("atSeconds", 1.0),
        width=params.get("width", 1280),
    )
    return _ingest(out, kind="image", op="thumbnail"), None


async def _op_extract_audio(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    out = await _run(postprod.extract_audio, video, _new_output(".mp3"))
    return _ingest(out, kind="audio", op="extract_audio"), None


def _with_note(payload: dict, note: Optional[str]) -> dict:
    """Carry a non-fatal notice out with the result.

    A substituted voice is not an error — the audio exists and the node
    succeeds — but it IS a different voice than the one on the node, and that
    has to arrive somewhere the user can read it.
    """
    if note:
        payload["note"] = note
    return payload


def _narrate_one(engine: str, script: str, voice: str, persona):
    """Speak one chunk with whichever engine the plan settled on.

    Segmenting, retrying and the manifest above are engine-agnostic — they are
    about a script being too long for one request, which is true of both
    endpoints — so the engine choice lives in exactly this one call.
    """
    if engine == "openai":
        from flowboard.services import openai_tts

        # `persona` is Gemini's delivery note; the documented OpenAI endpoint
        # takes the same thing under `instructions`.
        return openai_tts.synthesize(script, voice=voice, instructions=persona)
    return tts.synthesize(script, voice=voice, persona=persona)


async def _op_narrate(params: dict) -> tuple[dict, Optional[str]]:
    text = params.get("text")
    if not isinstance(text, str) or not text.strip():
        return {}, "missing_text"
    # Settled here as well as in the plan layer, because `/api/postprod/narrate`
    # reaches this op directly with whatever engine and voice name the caller
    # had. One function for both, so the two cannot drift.
    plan = tts.plan_voice(params.get("engine"), params.get("voice"))
    if plan.voice is None:
        # Only a direct caller can get here — the plan layer drops the pass
        # when nothing is to be narrated. Narrating with a default instead
        # would be the one thing the name asked us not to do.
        return {}, plan.note or "voice_none_requested"
    voice = plan.voice
    note = params.get("voiceNote") or plan.note
    if note:
        logger.info("narrate: %s", note)
    persona = params.get("persona")
    if not isinstance(persona, str) or not persona.strip():
        persona = None
    from flowboard.services import narration

    script = text.strip()

    # A re-read of named segments. `None` is an ordinary full narration; a set is
    # "only these, and reuse the audio already paid for" — which is only possible
    # against a manifest, so the short-script branch below cannot serve it.
    only = _only_segments(params.get("onlySegments"))

    # Short enough for one request: send it as one. Segmenting a caption
    # would add a manifest, a merge and a re-encode to buy nothing.
    if len(script) <= narration.MAX_SEGMENT_CHARS and only is None:
        # No out_path: tts.synthesize picks its own NARRATION_DIR location,
        # and ingest_local_file adopts whatever it produces — no
        # caller-controlled destination path to confine here.
        out = await _run(_narrate_one, plan.engine, script, voice, persona)
        return _with_note(_ingest(out, kind="audio", op="narrate"), note), None

    # Long script: one request per few sentences, each retried on its own.
    # Sent whole, a ten-minute story is one call and one timeout away from
    # losing the lot.
    planned = narration.plan(
        script, pause_s=_pause_seconds(params.get("pauseSeconds"))
    )
    manifest = planned
    manifest_path = _narration_manifest_path(params)

    if only is not None:
        # Re-reading named segments means splicing new audio into audio already
        # paid for. Three things have to still be true, and each one is refused
        # rather than worked around, because every way of "coping" produces a
        # narration that sounds finished and is wrong.
        if manifest_path is None or not manifest_path.exists():
            return {}, "narrate_manifest_missing"
        try:
            recorded = narration.Manifest.read(manifest_path)
        except narration.NarrationError as exc:
            return {}, f"narrate_manifest_unreadable: {str(exc)[:120]}"
        if [s.text for s in recorded.segments] != [s.text for s in planned.segments]:
            # Compared text by text, not by count: an edit that swaps one
            # sentence keeps the count, and a count check would splice the new
            # line's audio into the old line's slot.
            return {}, "narrate_manifest_stale"
        if recorded.voice and recorded.voice != voice:
            # Segment 7 in a different voice, in the middle of a finished
            # narration. A record with no voice at all predates this field and is
            # allowed through — the caller says it cannot tell.
            return {}, f"narrate_voice_changed:{recorded.voice}"
        unknown = sorted(i for i in only if not 0 <= i < len(recorded.segments))
        if unknown:
            return {}, f"narrate_segment_unknown:{','.join(str(i) for i in unknown)}"
        # The recorded manifest, not the fresh plan: it carries the paths of the
        # segments already bought, and `pause_s` from the original read so the
        # join matches.
        manifest = recorded

    logger.info(
        "narrate: %d segment(s) for %d chars%s",
        len(manifest.segments), len(script),
        f" (re-reading {sorted(only)})" if only else "",
    )

    # The manifest is the record of what has been paid for, so it is written as
    # the run goes rather than at the end. Written at the end it was never
    # written on the one run that needed it: `merge` refuses a manifest with
    # holes, so a failed segment returned an error and left the finished
    # segments as temporary files with nothing pointing at them.
    #
    # Named after the request, not after the merged output, because the output
    # does not exist yet when the first segment lands.
    #
    # Recorded so a later partial re-read can refuse to splice a different voice
    # into a finished narration.
    manifest.voice = voice
    manifest.engine = plan.engine

    def _one(chunk: str):
        return _narrate_one(plan.engine, chunk, voice, persona)

    def _after_each(m) -> None:
        if manifest_path is not None:
            try:
                m.write(manifest_path)
            except OSError as exc:  # pragma: no cover - a locked disk
                logger.info("narrate: could not update the manifest (%s)", exc)

    manifest = await _run(
        narration.synthesize_manifest, manifest, _one,
        on_progress=_after_each, only=only,
    )
    if manifest.failed:
        # Named, not summarised: "3 segments failed" leaves the user unable
        # to retry the right ones. The manifest beside it says which files the
        # finished ones are, so a retry pays only for the holes.
        detail = ",".join(str(s.index) for s in manifest.failed[:10])
        if manifest_path is not None:
            logger.info("narrate: %d segment(s) kept in %s",
                        len(manifest.segments) - len(manifest.failed), manifest_path)
        return {}, ("narrate_failed_segments:" + detail)[:200]

    out = await _run(narration.merge, manifest, _new_output(".mp3"))
    if manifest_path is None:
        manifest_path = Path(str(out)).with_suffix(".segments.json")
    manifest.write(manifest_path)
    payload = _ingest(out, kind="audio", op="narrate")
    payload["segments"] = len(manifest.segments)
    payload["manifest"] = str(manifest_path)
    return _with_note(payload, note), None


def _only_segments(raw: object) -> Optional[set[int]]:
    """Which segments a re-read covers, or None for an ordinary full narration.

    An empty list is None, not an empty set: "re-read nothing" is not a request
    anyone makes on purpose, and treating it as one would produce a merge of
    whatever the manifest happened to hold. Non-integers are dropped rather than
    refused — `params` arrives over an unauthenticated local endpoint, and a
    stray value should not be able to turn a re-read into a full one.
    """
    if not isinstance(raw, list):
        return None
    picked = {
        int(v) for v in raw
        if not isinstance(v, bool) and isinstance(v, int) and v >= 0
    }
    return picked or None


def _narration_manifest_path(params: dict) -> Optional[Path]:
    """Where this narration's segment record lives, stable across attempts.

    Keyed by a request id so a re-read finds the same file and can see which
    segments have already been bought. Without an id — a direct call, a test —
    there is nothing stable to key on and the manifest falls back to sitting
    beside the merged output.

    **`manifestRequestId` is what made that first sentence true.** Keyed only on
    `__request_id`, the file could be found by an in-row retry and by nothing
    else — and `postprod` errors classify terminal (`processor._NON_FLOW_
    REQUEST_TYPES`), so that retry never happens. Every re-run mints a new
    request row and therefore a new, empty record. The manifest was effectively
    write-only from the day it shipped: it recorded which segments had been paid
    for, and no code path could ever read it back. A re-read passes the ORIGINAL
    id here, which is the whole mechanism.
    """
    rid = params.get("manifestRequestId")
    if isinstance(rid, bool) or not isinstance(rid, int):
        rid = params.get("__request_id")
    if isinstance(rid, bool) or not isinstance(rid, int):
        return None
    from flowboard.config import STORAGE_DIR

    directory = STORAGE_DIR / "narration"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return directory / f"req-{rid}.segments.json"


def _pause_seconds(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return narration_default_pause()
    return max(0.0, min(5.0, float(raw)))


def narration_default_pause() -> float:
    from flowboard.services import narration

    return narration.DEFAULT_PAUSE_S


async def _op_ken_burns(params: dict) -> tuple[dict, Optional[str]]:
    """A still image zoomed slowly over a voice track — the packaged tool's
    `sync_image_voice`. The clip runs as long as the narration."""
    image, err = _resolve_media(params, "image")
    if err:
        return {}, err
    audio, err = _resolve_media(params, "audio")
    if err:
        return {}, err
    out = await _run(
        postprod.ken_burns,
        image,
        audio,
        _new_output(".mp4"),
        zoom_in=bool(params.get("zoomIn", True)),
        zoom_speed=params.get("zoomSpeed", 0.002),
        width=params.get("width", 1920),
        height=params.get("height", 1080),
    )
    return _ingest(out, kind="video", op="ken_burns"), None


async def _op_fit_narration(params: dict) -> tuple[dict, Optional[str]]:
    video, err = _resolve_media(params, "video")
    if err:
        return {}, err
    audio, err = _resolve_media(params, "audio")
    if err:
        return {}, err
    out = await _run(postprod.trim_to_audio, video, audio, _new_output(".mp4"))
    return _ingest(out, kind="video", op="fit_narration"), None


_OPS: dict[str, OpHandler] = {
    "concat": _op_concat,
    "transcribe": _op_transcribe,
    "subtitles": _op_subtitles,
    "karaoke": _op_karaoke,
    "bgm": _op_bgm,
    "title": _op_title,
    "logo": _op_logo,
    "delogo": _op_delogo,
    "remove_watermark": _op_remove_watermark,
    "aspect": _op_aspect,
    "text_art": _op_text_art,
    "review": _op_review,
    "speed": _op_speed,
    "zoom_logo": _op_zoom_logo,
    "upscale": _op_upscale,
    "last_frame": _op_last_frame,
    "thumbnail": _op_thumbnail,
    "thumbnail_insert": _op_thumbnail_insert,
    "voice_speed": _op_voice_speed,
    "extract_audio": _op_extract_audio,
    "narrate": _op_narrate,
    "fit_narration": _op_fit_narration,
    "ken_burns": _op_ken_burns,
}


async def handle_postprod(params: dict) -> tuple[dict, Optional[str]]:
    """Worker entry point for the single ``postprod`` request type.

    Every canvas post-production node dispatches through here with
    ``{"op": <name>, ...op params...}``. Never raises for an input a caller
    could plausibly send — a bad op name, a missing field, an unresolvable
    media id, or a toolchain rejecting the input all come back as
    ``({}, error_code)``. Only a genuine bug should reach the worker's own
    catch-all in ``_process_one``.
    """
    op = params.get("op")
    if not isinstance(op, str) or not op.strip():
        return {}, "missing_op"
    op_name = op.strip()
    op_fn = _OPS.get(op_name)
    if op_fn is None:
        return {}, f"unknown_postprod_op:{op_name}"
    try:
        return await op_fn(params)
    except _EXPECTED_ERRORS as exc:
        logger.warning("postprod op %s failed: %s", op_name, exc)
        return {}, _error_text(exc)
