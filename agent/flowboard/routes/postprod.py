"""Local post-production routes.

Everything here runs on this machine — ffmpeg, RealESRGAN, and (for narration)
a single call to Gemini TTS with the user's own key. None of it touches Google
Flow, so it keeps working when the Flow session is gone and it never spends
Flow credits.

The heavy operations are blocking subprocess pipelines. They run in a worker
thread so a four-minute upscale cannot stall the event loop that the extension
bridge and the canvas both depend on.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from flowboard.config import STORAGE_DIR
from flowboard.services import assets, postprod, tts, upscale

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/postprod", tags=["postprod"])
# Finished renders are served from the app root (`/renders/<name>`) rather
# than under /api, matching how cached Flow media is served from `/media`.
bytes_router = APIRouter(tags=["postprod"])

# Renders land under the agent's storage dir so the frontend can serve them
# and so a stray path in a request body cannot write anywhere else.
OUTPUT_DIR = STORAGE_DIR / "renders"

# Request-shape ceilings. These endpoints are unauthenticated and each unit
# of work is a full re-encode or a paid API call, so "the caller decides how
# much work to ask for" is not a safe default.
MAX_CONCAT_CLIPS = 50
MAX_NARRATION_CHARS = 5000
# A subtitle track for a short clip is a few KB; this only stops someone
# parking a large blob in the renders folder through the SRT endpoint.
MAX_SRT_CHARS = 100_000
# The library listing is a convenience index, not a file manager. A cache
# with thousands of clips would otherwise send megabytes of JSON per poll.
MAX_LIBRARY_ITEMS = 300

# Only these are listed and served back. Anything else that lands in the
# folder stays invisible rather than being handed to a browser.
_VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".mkv"})
_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
_AUDIO_EXTS = frozenset({".wav", ".mp3", ".m4a", ".aac"})


def _kind_of(suffix: str) -> Optional[str]:
    """Media kind for a file extension, or None if we don't serve it."""
    s = suffix.lower()
    if s in _VIDEO_EXTS:
        return "video"
    if s in _IMAGE_EXTS:
        return "image"
    if s in _AUDIO_EXTS:
        return "audio"
    return None


def _resolve_output(name: str) -> Path:
    """Confine an output name to OUTPUT_DIR.

    Names arrive over HTTP, so a bare ``Path`` join would accept
    ``../../anything``. Resolve and re-check containment instead of trusting
    the string.
    """
    if not name or not name.strip():
        raise HTTPException(status_code=400, detail="output name is required")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    candidate = (OUTPUT_DIR / name.strip()).resolve()
    root = OUTPUT_DIR.resolve()
    # `candidate == root` would hand ffmpeg the renders directory itself as
    # an output file path.
    if root not in candidate.parents:
        raise HTTPException(status_code=400, detail="output name escapes storage")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


# Where an input path is allowed to point. Without this, every endpoint
# that takes a file path is an arbitrary-file-read primitive: the caller
# names any file on the machine and ffmpeg happily muxes it into a render
# they can then download. Outputs were already confined; inputs were not.
def _input_roots() -> list[Path]:
    roots = [STORAGE_DIR, assets.ASSET_ROOT]
    extra = os.getenv("FLOWBOARD_INPUT_ROOTS", "")
    roots.extend(Path(p) for p in extra.split(os.pathsep) if p.strip())
    resolved = []
    for r in roots:
        try:
            resolved.append(r.resolve())
        except OSError:
            continue
    return resolved


def _within(child: Path, root: Path) -> bool:
    """True if ``child`` is ``root`` or lives under it.

    Compared through ``os.path.normcase`` so a Windows drive-letter or
    path-case difference (``d:\\x`` vs ``D:\\X``) doesn't falsely reject a
    legitimate file. The trailing separator stops ``/rootother`` from
    matching ``/root``.
    """
    c = os.path.normcase(str(child))
    r = os.path.normcase(str(root))
    return c == r or c.startswith(r + os.sep)


def _existing(path: str, *, label: str) -> Path:
    """Resolve an input path and confine it to the allowed roots.

    ``resolve()`` before comparing, so symlinks and ``..`` segments are
    collapsed first — comparing the raw string would let
    ``storage/../../secrets`` through.
    """
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        raise HTTPException(
            status_code=400, detail=f"{label} not found: {path}"
        ) from None

    roots = _input_roots()
    if not any(_within(resolved, r) for r in roots):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{label} is outside the allowed folders; "
                "put the file under the app storage or asset root"
            ),
        )
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail=f"{label} not found: {path}")
    return resolved


def _client_detail(exc: Exception) -> str:
    """Trim a toolchain failure for the HTTP response.

    This keeps a multi-hundred-line ffmpeg stderr dump out of the response
    body; it does NOT hide local paths, which appear early in most messages
    and are returned by these routes anyway (``RenderResponse.path``). The
    full text goes to the server log.
    """
    text = " ".join(str(exc).split())
    return text[:200] + ("…" if len(text) > 200 else "")


async def _duration_of(path: Path) -> Optional[float]:
    """Probe a render's duration without blocking the event loop.

    ``probe_duration`` shells out to ffprobe (up to PROBE_TIMEOUT_S). Called
    directly from an async handler it stalls every other request — including
    the ones the worker and the UI depend on. A probe failure must also not
    turn a SUCCESSFUL render into a 500: the file exists either way, so the
    duration is reported as unknown instead.
    """
    try:
        return await run_in_threadpool(postprod.probe_duration, path)
    except Exception:
        logger.warning("could not probe duration of %s", path, exc_info=True)
        return None


async def _run(fn, *args, **kwargs) -> Path:
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except (postprod.PostProdError, tts.TTSError, RuntimeError) as exc:
        logger.warning("%s failed: %s", getattr(fn, "__name__", fn), exc)
        raise HTTPException(status_code=500, detail=_client_detail(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, OSError) as exc:
        # Bad input, not a server fault: upscale's validators raise
        # ValueError with the explanation the caller needs ("model X
        # upscales by 4x only…"), which a bare 500 threw away.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class CapabilityStatus(BaseModel):
    assets: dict[str, Any]
    ffmpegAvailable: bool
    upscaleAvailable: bool
    upscaleModels: list[str]
    ttsKeyAvailable: bool
    voices: list[dict[str, Any]]
    fonts: list[str]
    bgm: list[str]


@router.get("/status", response_model=CapabilityStatus)
async def status() -> CapabilityStatus:
    """What the local toolchain can actually do right now.

    The UI uses this to hide features whose binary or key is missing instead
    of offering a button that fails at click time.
    """
    return CapabilityStatus(
        assets=assets.status(),
        ffmpegAvailable=postprod.available(),
        upscaleAvailable=upscale.available(),
        upscaleModels=upscale.models(),
        ttsKeyAvailable=tts.api_key_available(),
        voices=tts.list_voices(),
        fonts=assets.list_fonts(),
        bgm=[p.name for p in assets.list_bgm()],
    )


class LibraryItem(BaseModel):
    name: str
    path: str
    kind: str
    sizeBytes: int
    modified: float
    #: Set for cached Flow media, which the UI previews via ``/media/<id>``.
    mediaId: Optional[str] = None
    #: Set for renders, which the UI previews via ``/renders/<name>``.
    url: Optional[str] = None


class LibraryResponse(BaseModel):
    sources: list[LibraryItem]
    renders: list[LibraryItem]


def _scan_library(
    folder: Path, *, as_media: bool = False, as_render: bool = False
) -> list[LibraryItem]:
    """Index the media files directly inside ``folder``, newest first."""
    if not folder.is_dir():
        return []
    out: list[LibraryItem] = []
    for p in folder.iterdir():
        if not p.is_file():
            continue
        kind = _kind_of(p.suffix)
        if kind is None:
            continue
        try:
            st = p.stat()
        except OSError:
            continue  # vanished between listing and stat
        out.append(
            LibraryItem(
                name=p.name,
                path=str(p),
                kind=kind,
                sizeBytes=st.st_size,
                modified=st.st_mtime,
                mediaId=p.stem if as_media else None,
                url=f"/renders/{quote(p.name)}" if as_render else None,
            )
        )
    out.sort(key=lambda i: i.modified, reverse=True)
    return out[:MAX_LIBRARY_ITEMS]


@router.get("/library", response_model=LibraryResponse)
def library() -> LibraryResponse:
    """Files this machine can feed into post-production, and what it produced.

    Every post-production endpoint takes a filesystem path and confines it to
    the allowed roots, so the UI must not invent one — it picks from here.

    ``sources`` are clips the tool generated (the media cache). ``renders``
    are previous outputs, which are themselves valid inputs, so chains like
    concat → subtitles → logo work without leaving the app.
    """
    from flowboard.services.media import MEDIA_CACHE_DIR

    return LibraryResponse(
        sources=_scan_library(MEDIA_CACHE_DIR, as_media=True),
        renders=_scan_library(OUTPUT_DIR, as_render=True),
    )


@bytes_router.get("/renders/{name:path}")
def get_render(name: str):
    """Stream a finished render so the browser can play or download it.

    Without this the post-production endpoints write files the user has no
    way to reach: they return an absolute path, which a browser cannot open.

    Confined to OUTPUT_DIR by the same resolve-then-check rule the write path
    uses — the name arrives over HTTP, so ``..`` must not escape.
    """
    root = OUTPUT_DIR.resolve()
    try:
        candidate = (OUTPUT_DIR / name).resolve()
    except OSError:
        raise HTTPException(status_code=400, detail="bad render name") from None
    if root not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail="render not found")
    if _kind_of(candidate.suffix) is None:
        # Never hand back something we don't recognise as media, whatever
        # else may have been written into the folder.
        raise HTTPException(status_code=404, detail="render not found")
    mime, _ = mimetypes.guess_type(str(candidate))
    return FileResponse(
        path=str(candidate), media_type=mime or "application/octet-stream"
    )


class SrtBody(BaseModel):
    name: str
    text: str = Field(min_length=1, max_length=MAX_SRT_CHARS)


class SrtResponse(BaseModel):
    path: str


@router.post("/srt", response_model=SrtResponse)
def save_srt(body: SrtBody) -> SrtResponse:
    """Persist pasted subtitle text as an ``.srt`` under the renders folder.

    ``/subtitles`` takes a file path confined to the allowed roots, and a
    browser cannot place a file there. Without this the whole subtitle feature
    is unreachable from the UI — the endpoint exists but nothing can name a
    file it will accept.

    Written UTF-8 with a BOM: ffmpeg's subtitles filter reads a BOM-less UTF-8
    file as the system codepage on Windows, which turns Vietnamese diacritics
    into mojibake burned permanently into the video.
    """
    name = body.name.strip()
    if not name.lower().endswith(".srt"):
        name = f"{name}.srt"
    target = _resolve_output(name)
    try:
        target.write_text(body.text, encoding="utf-8-sig")
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not write subtitle file: {exc}"
        ) from exc
    return SrtResponse(path=str(target))


class ConcatBody(BaseModel):
    # Each clip costs one full re-encode, serialized in a single request —
    # an unbounded list holds a threadpool slot for as long as it likes.
    clips: list[str] = Field(min_length=1, max_length=MAX_CONCAT_CLIPS)
    output: str
    width: int = Field(default=1080, ge=16, le=7680)
    height: int = Field(default=1920, ge=16, le=7680)
    fps: int = Field(default=30, ge=1, le=120)


class RenderResponse(BaseModel):
    path: str
    durationSeconds: Optional[float] = None


@router.post("/concat", response_model=RenderResponse)
async def concat(body: ConcatBody) -> RenderResponse:
    """Normalize every clip to one codec/size/fps, then join them.

    Clips generated in separate Flow calls differ in resolution and frame
    rate often enough that concatenating them raw desyncs audio, so the
    normalize pass is not optional.
    """
    sources = [_existing(c, label="clip") for c in body.clips]
    dst = _resolve_output(body.output)
    # Stage into a private temp directory, not next to the output under a
    # name derived from it: two concurrent concats writing the same output
    # used to overwrite each other's half-written intermediates. The
    # try/finally has to start BEFORE the normalize loop, or a clip that
    # fails mid-way leaves every earlier full-resolution temp behind.
    staging = Path(tempfile.mkdtemp(prefix=".concat-", dir=dst.parent))
    try:
        staged: list[Path] = []
        for i, src in enumerate(sources):
            tmp = staging / f"norm{i:03d}.mp4"
            staged.append(
                await _run(
                    postprod.normalize, src, tmp, body.width, body.height, body.fps
                )
            )
        out = await _run(postprod.concat, staged, dst)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return RenderResponse(path=str(out), durationSeconds=await _duration_of(out))


class SubtitleBody(BaseModel):
    # Sizes and offsets reach libass, which allocates from them. Bounds are
    # generous enough for any real subtitle and finite enough that a stray
    # 10^9 cannot ask ffmpeg to allocate its way out of memory.
    video: str
    srt: str
    output: str
    font: str = Field(default=assets.DEFAULT_SUBTITLE_FONT, max_length=200)
    size: int = Field(default=48, ge=1, le=400)
    primaryColor: str = Field(default="&H00FFFFFF", max_length=16)
    outlineColor: str = Field(default="&H00000000", max_length=16)
    outlineWidth: int = Field(default=2, ge=0, le=50)
    marginV: int = Field(default=60, ge=0, le=2000)


@router.post("/subtitles", response_model=RenderResponse)
async def subtitles(body: SubtitleBody) -> RenderResponse:
    out = await _run(
        postprod.burn_subtitles,
        _existing(body.video, label="video"),
        _existing(body.srt, label="srt"),
        _resolve_output(body.output),
        font=body.font,
        size=body.size,
        primary_color=body.primaryColor,
        outline_color=body.outlineColor,
        outline_width=body.outlineWidth,
        margin_v=body.marginV,
    )
    return RenderResponse(path=str(out))


class LogoBody(BaseModel):
    # Position and size go into an ffmpeg scale/overlay chain; 8K is past any
    # real frame, and an unbounded width is an allocation request.
    video: str
    logo: str
    output: str
    x: int = Field(default=40, ge=0, le=7680)
    y: int = Field(default=40, ge=0, le=7680)
    width: Optional[int] = Field(default=None, ge=1, le=7680)
    height: Optional[int] = Field(default=None, ge=1, le=7680)


@router.post("/logo", response_model=RenderResponse)
async def logo(body: LogoBody) -> RenderResponse:
    out = await _run(
        postprod.overlay_logo,
        _existing(body.video, label="video"),
        _existing(body.logo, label="logo"),
        _resolve_output(body.output),
        body.x,
        body.y,
        body.width,
        body.height,
    )
    return RenderResponse(path=str(out))


class BgmBody(BaseModel):
    # These floats are interpolated into an ffmpeg filtergraph, and JSON
    # accepts the bare `Infinity` literal — `volume=inf` is not a filter.
    model_config = ConfigDict(allow_inf_nan=False)

    video: str
    output: str
    # Either a file in the bundled nhac_nen library, or a path inside the
    # allowed input roots (a library miss falls through to _existing).
    track: str
    bgmVolume: float = Field(default=0.3, ge=0.0, le=5.0)
    origVolume: float = Field(default=1.0, ge=0.0, le=5.0)
    fadeIn: float = Field(default=0.0, ge=0.0, le=60.0)
    fadeOut: float = Field(default=0.0, ge=0.0, le=60.0)


@router.post("/bgm", response_model=RenderResponse)
async def bgm(body: BgmBody) -> RenderResponse:
    # A library hit is pre-approved because bgm_path is confined to BGM_DIR;
    # anything else goes through the same containment check as every other
    # input path.
    track = assets.bgm_path(body.track)
    source = track if track is not None else _existing(body.track, label="track")
    out = await _run(
        postprod.mix_bgm,
        _existing(body.video, label="video"),
        source,
        _resolve_output(body.output),
        bgm_volume=body.bgmVolume,
        orig_volume=body.origVolume,
        fade_in=body.fadeIn,
        fade_out=body.fadeOut,
    )
    return RenderResponse(path=str(out))


class NarrateBody(BaseModel):
    # Every call spends the user's own Gemini quota on an unauthenticated,
    # unrated endpoint.
    text: str = Field(min_length=1, max_length=MAX_NARRATION_CHARS)
    voice: str = tts.DEFAULT_VOICE
    # Persona title from voice_styles.json, or free-text delivery notes.
    # Free-text delivery notes are prepended to the prompt, so this is billed
    # Gemini input like `text` is — it needs the same kind of ceiling.
    persona: Optional[str] = Field(default=None, max_length=500)
    output: Optional[str] = None


class NarrateResponse(BaseModel):
    path: str
    voice: str
    durationSeconds: Optional[float] = None


@router.post("/narrate", response_model=NarrateResponse)
async def narrate(body: NarrateBody) -> NarrateResponse:
    if not tts.api_key_available():
        raise HTTPException(
            status_code=400,
            detail="no Gemini API key — set GEMINI_API_KEY or data_general/gemini_api_key.txt",
        )
    out_path = _resolve_output(body.output) if body.output else None
    out = await _run(
        tts.synthesize,
        body.text,
        voice=body.voice,
        persona=body.persona,
        out_path=out_path,
    )
    duration = await _duration_of(out) if postprod.available() else None
    return NarrateResponse(path=str(out), voice=body.voice, durationSeconds=duration)


class FitNarrationBody(BaseModel):
    video: str
    audio: str
    output: str


@router.post("/fit-narration", response_model=RenderResponse)
async def fit_narration(body: FitNarrationBody) -> RenderResponse:
    """Match a scene's length to its narration track."""
    out = await _run(
        postprod.trim_to_audio,
        _existing(body.video, label="video"),
        _existing(body.audio, label="audio"),
        _resolve_output(body.output),
    )
    return RenderResponse(path=str(out), durationSeconds=await _duration_of(out))


class TitleBody(BaseModel):
    video: str
    output: str
    text: str = Field(min_length=1, max_length=300)
    font: str = Field(default=assets.DEFAULT_SUBTITLE_FONT, max_length=200)
    size: int = Field(default=64, ge=1, le=400)
    color: str = Field(default="white", max_length=32)
    boxOpacity: float = Field(default=0.5, ge=0.0, le=1.0)
    yRatio: float = Field(default=0.08, ge=0.0, le=1.0)


@router.post("/title", response_model=RenderResponse)
async def title(body: TitleBody) -> RenderResponse:
    """Burn a title line across the top — the last bit of `edit_video`."""
    out = await _run(
        postprod.overlay_title,
        _existing(body.video, label="video"),
        _resolve_output(body.output),
        body.text,
        font=body.font,
        size=body.size,
        color=body.color,
        box_opacity=body.boxOpacity,
        y_ratio=body.yRatio,
    )
    return RenderResponse(path=str(out), durationSeconds=await _duration_of(out))


class LastFrameBody(BaseModel):
    video: str
    output: str
    width: Optional[int] = Field(default=None, ge=16, le=7680)
    #: Given, the frame is also uploaded to Flow so it can be used as the
    #: first frame of the next clip.
    project_id: Optional[str] = None


class LastFrameResponse(BaseModel):
    path: str
    url: str
    mediaId: Optional[str] = None


@router.post("/last-frame", response_model=LastFrameResponse)
async def last_frame(body: LastFrameBody) -> LastFrameResponse:
    """Take a clip's final frame — optionally straight into Flow.

    This is how scenes are chained: the last frame of one clip becomes the
    first frame of the next, so the join reads as a continuation rather than
    a cut. Uploading it here saves the round trip of downloading the image
    and picking it back up by hand.
    """
    name = body.output.strip()
    if not name.lower().endswith((".jpg", ".jpeg", ".png")):
        name = f"{name}.jpg"
    out = await _run(
        postprod.last_frame,
        _existing(body.video, label="video"),
        _resolve_output(name),
        width=body.width,
    )

    media_id: Optional[str] = None
    if body.project_id:
        from flowboard.services import image_ingest
        from flowboard.services.flow_sdk import is_valid_project_id

        if not is_valid_project_id(body.project_id):
            raise HTTPException(400, "invalid project_id")
        try:
            result = await image_ingest.ingest_bytes(
                out.read_bytes(), "image/jpeg", body.project_id, out.name
            )
        except image_ingest.IngestError as exc:
            raise HTTPException(exc.status, exc.message) from exc
        media_id = result.get("media_id")

    return LastFrameResponse(
        path=str(out), url=f"/renders/{quote(out.name)}", mediaId=media_id
    )


class ThumbnailBody(BaseModel):
    video: str
    output: str
    atSeconds: float = Field(default=1.0, ge=0.0, le=86400.0)
    width: int = Field(default=1280, ge=16, le=7680)


class ThumbnailResponse(BaseModel):
    path: str
    url: str


@router.post("/thumbnail", response_model=ThumbnailResponse)
async def thumbnail(body: ThumbnailBody) -> ThumbnailResponse:
    """One frame as a cover image."""
    name = body.output.strip()
    if not name.lower().endswith((".jpg", ".jpeg", ".png")):
        name = f"{name}.jpg"
    out = await _run(
        postprod.grab_thumbnail,
        _existing(body.video, label="video"),
        _resolve_output(name),
        at_seconds=body.atSeconds,
        width=body.width,
    )
    return ThumbnailResponse(path=str(out), url=f"/renders/{quote(out.name)}")


class CutBody(BaseModel):
    video: str
    seconds: int = Field(ge=1, le=3600)
    #: Filename stem for the pieces; they come out as `<stem>-000.mp4`, …
    name: str = Field(default="cut", min_length=1, max_length=80)


class CutResponse(BaseModel):
    pieces: list[LibraryItem]


@router.post("/cut", response_model=CutResponse)
async def cut(body: CutBody) -> CutResponse:
    """Cut one video into fixed-length pieces.

    The counterpart to /concat, and the other half of what the packaged tool's
    Cut & Merge screen does. Pieces land in the renders folder, so each one is
    immediately usable as input anywhere else.
    """
    source = _existing(body.video, label="video")
    stem = Path(body.name.strip()).name or "cut"
    # Route the stem through the same containment check as any other output so
    # a name like `../x` cannot place pieces outside the folder.
    _resolve_output(f"{stem}.mp4")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        pieces = await run_in_threadpool(
            postprod.split_by_duration,
            source,
            OUTPUT_DIR,
            body.seconds,
            stem=stem,
        )
    except postprod.PostProdError as exc:
        raise HTTPException(status_code=400, detail=_client_detail(exc)) from None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_client_detail(exc)) from None

    items: list[LibraryItem] = []
    for piece in pieces:
        stat = piece.stat()
        items.append(
            LibraryItem(
                name=piece.name,
                path=str(piece),
                kind="video",
                sizeBytes=stat.st_size,
                modified=stat.st_mtime,
                url=f"/renders/{quote(piece.name)}",
            )
        )
    return CutResponse(pieces=items)


class DelogoBody(BaseModel):
    video: str
    output: str
    # Bounded like the overlay geometry: these go into a filtergraph and 8K
    # is past any real frame.
    x: int = Field(ge=0, le=7680)
    y: int = Field(ge=0, le=7680)
    width: int = Field(ge=1, le=7680)
    height: int = Field(ge=1, le=7680)


@router.post("/delogo", response_model=RenderResponse)
async def delogo(body: DelogoBody) -> RenderResponse:
    """Blur out a static watermark.

    ffmpeg's ``delogo`` rebuilds the rectangle from its surrounding pixels —
    it hides the mark, it does not recover what was underneath. Good over a
    plain background, visible over detail.
    """
    out = await _run(
        postprod.remove_logo,
        _existing(body.video, label="video"),
        _resolve_output(body.output),
        x=body.x,
        y=body.y,
        width=body.width,
        height=body.height,
    )
    return RenderResponse(path=str(out), durationSeconds=await _duration_of(out))


class RemoveWatermarkBody(BaseModel):
    video: str
    output: str
    #: The tool's own syntax: ``br:auto``, ``x,y,w,h`` or ``br:mx,my,w,h``.
    #: Left unset, the tool detects the mark itself — which is the reason to
    #: use it over `delogo`. Bounded only in length; the tool validates the
    #: syntax and rejects what it cannot parse.
    region: Optional[str] = Field(default=None, max_length=60)


class RemoveWatermarkResponse(BaseModel):
    path: str
    durationSeconds: Optional[float] = None
    #: False when the tool ran fine and found nothing to remove. The caller
    #: has to tell that apart from a successful clean, because the path
    #: returned is then the original file.
    detected: bool
    note: Optional[str] = None


@router.post("/remove-watermark", response_model=RemoveWatermarkResponse)
async def remove_watermark(body: RemoveWatermarkBody) -> RemoveWatermarkResponse:
    """Detect and inpaint the generator's watermark.

    Different from ``/delogo`` in kind, not degree. That blurs a rectangle
    the caller nominates and leaves a smudge; this runs the packaged tool's
    own detector — measured finding ``Veo-text 23x10`` on a real clip — and
    reconstructs what was behind it, keeping the audio track and frame size.

    Use ``/delogo`` when a workflow specifies ``veo_logo_method``; use this
    when the mark should be gone rather than covered.
    """
    from flowboard.services import watermark as watermark_service

    src = _existing(body.video, label="video")
    try:
        result = await watermark_service.remove_from_video(
            src, _resolve_output(body.output), region=body.region
        )
    except watermark_service.WatermarkError as exc:
        # 422, not 500: the request was well-formed and there is nothing
        # wrong with this server — the tool is missing, or refused the file.
        raise HTTPException(status_code=422, detail=str(exc)[:300]) from exc

    return RemoveWatermarkResponse(
        path=str(result.path),
        durationSeconds=await _duration_of(result.path),
        detected=result.detected,
        note=result.note or None,
    )


class TranscribeBody(BaseModel):
    video: str
    name: str
    language: Optional[str] = Field(default=None, max_length=60)


class TranscribeResponse(BaseModel):
    path: str
    srt: str


# A minute of 16 kHz mono at 48 kbps is ~360 KB, and the file travels
# base64-encoded inside a JSON body. This caps a request at roughly a
# half-hour clip rather than letting one call carry an unbounded payload.
MAX_AUDIO_BYTES = 12 * 1024 * 1024


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(body: TranscribeBody) -> TranscribeResponse:
    """Speech in a video → a burnable ``.srt``.

    The work lives in ``services/transcribe`` so the canvas can run the same
    path: an ``edit_video`` node with subtitles enabled needs an SRT before
    it can burn one, and two implementations of "ask Gemini for subtitles"
    would drift apart. This layer only maps failures onto status codes.
    """
    from flowboard.services import transcribe as transcribe_service

    source = _existing(body.video, label="video")
    try:
        srt = await transcribe_service.video_to_srt(
            source, language=body.language, storage_dir=STORAGE_DIR
        )
    except transcribe_service.TranscribeError as exc:
        status = {
            "no_key": 400,
            "too_long": 400,
            "no_speech": 422,
        }.get(exc.kind, 502)
        raise HTTPException(status_code=status, detail=str(exc)) from None

    name = body.name.strip()
    if not name.lower().endswith(".srt"):
        name = f"{name}.srt"
    target = _resolve_output(name)
    try:
        transcribe_service.write_srt(srt, target)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"could not write subtitle file: {exc}"
        ) from exc
    return TranscribeResponse(path=str(target), srt=srt)


class CloneBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    max_height: int = Field(default=1080, ge=144, le=2160)


class CloneResponse(BaseModel):
    path: str
    name: str
    url: str
    sizeBytes: int
    durationSeconds: Optional[float] = None


@router.post("/clone", response_model=CloneResponse)
async def clone(body: CloneBody) -> CloneResponse:
    """Fetch a video from a link into the renders folder.

    It lands there, not in the media cache, because renders are already
    served over HTTP and already accepted as post-production input — so a
    cloned clip can go straight into Cut & Merge or Upscale.
    """
    from flowboard.services import video_clone

    if not video_clone.available():
        raise HTTPException(
            status_code=400,
            detail="yt-dlp is not installed in the agent environment.",
        )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path = await run_in_threadpool(
            video_clone.download, body.url, OUTPUT_DIR, max_height=body.max_height
        )
    except video_clone.VideoCloneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return CloneResponse(
        path=str(path),
        name=path.name,
        url=f"/renders/{quote(path.name)}",
        sizeBytes=path.stat().st_size,
        durationSeconds=await _duration_of(path),
    )


class ClonePreviewBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


@router.post("/clone/preview")
async def clone_preview(body: ClonePreviewBody) -> dict:
    """What is at that link, without downloading it."""
    from flowboard.services import video_clone

    if not video_clone.available():
        raise HTTPException(status_code=400, detail="yt-dlp is not installed.")
    try:
        return await run_in_threadpool(video_clone.probe, body.url)
    except video_clone.VideoCloneError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class UpscaleBody(BaseModel):
    source: str
    output: str
    scale: int = 4
    model: str = upscale.DEFAULT_MODEL
    # Downscale after the fixed-factor pass so callers can land exactly on
    # 1080p / 2160p instead of only multiples of the model's scale.
    targetHeight: Optional[int] = None
    kind: str = "video"


@router.post("/upscale", response_model=RenderResponse)
async def upscale_media(body: UpscaleBody) -> RenderResponse:
    if not upscale.available():
        raise HTTPException(
            status_code=400, detail="RealESRGAN engine not found in asset root"
        )
    src = _existing(body.source, label="source")
    dst = _resolve_output(body.output)
    if body.kind == "image":
        out = await _run(upscale.upscale_image, src, dst, body.scale, body.model)
        return RenderResponse(path=str(out))
    out = await _run(
        upscale.upscale_video,
        src,
        dst,
        body.scale,
        body.model,
        body.targetHeight,
    )
    return RenderResponse(path=str(out), durationSeconds=await _duration_of(out))
