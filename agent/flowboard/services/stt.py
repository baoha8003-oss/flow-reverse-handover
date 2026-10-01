"""Speech to text, with a second and a third source — and real word timings.

`transcribe.py` asks Gemini, and Gemini is the only provider on this stack
that accepts audio, so subtitles have rested on a single supplier: when its
CLI stopped accepting individual accounts, everything that needed speech
stopped with it. This adds two more paths and, more importantly, the one
thing Gemini cannot give.

**Word timings, and why they matter more than a second transcriber.**
`karaoke.py` derives per-word timings by splitting each subtitle line's
duration in proportion to word length. That was not a shortcut: asked for
word timestamps directly, Gemini returned five confidently-timed words for a
clip whose own transcribe path correctly answered ``NO_SPEECH`` — it
invented them, measured 2026-09-02. Independent reports say the same across
Gemini 1.5, 2.0 and 3.

So Gemini stays out of the timing chain entirely, and the module docstring
in `karaoke.py` names the condition for replacing the approximation: "a
trustworthy word-timing source". These are the two that exist:

* **whisper-1** over ``/v1/audio/transcriptions`` with
  ``timestamp_granularities=["word"]`` — the ONLY OpenAI model that returns
  timestamps at all. ``gpt-4o-transcribe`` and its mini variant return
  ``{"text": ...}`` and nothing else, so they cannot drive a subtitle track,
  let alone karaoke. Needs a real platform API key; the ChatGPT OAuth token
  the Codex CLI holds cannot reach this endpoint.
* **faster-whisper** locally with ``word_timestamps=True``, which costs
  nothing per call and works offline once the model is downloaded.

**Both are off until switched on**, which is the project owner's decision:
each needs its own setting (`OPENAI_STT_ENABLED`, `LOCAL_STT_ENABLED`) on
top of its dependency — a platform key for one, the package for the other.
Neither can start spending money or disk on its own, and in particular a
key present for another feature is not consent for this one.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Whisper's own limit on the multipart upload. Roughly twice the cap the
#: Gemini path uses, because that one pays a base64 inflation this does not.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

_API_URL = "https://api.openai.com/v1/audio/transcriptions"

#: The only OpenAI transcription model that returns timestamps. Named as a
#: constant so the reason travels with the value: switching this to
#: `gpt-4o-transcribe` would silently produce a subtitle track with no cues.
WHISPER_MODEL = "whisper-1"

#: Local model size. `small` is the trade the owner picked: ~244 MB, roughly
#: a second of CPU for an 8-second clip. Vietnamese is a known weak spot for
#: Whisper generally, so `medium` is meaningfully better and ~3x slower.
DEFAULT_LOCAL_MODEL = "small"


class SttError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "provider") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    srt: str = ""
    #: Real per-word timings, empty when the source could not supply them.
    #: Empty is meaningful: `karaoke` falls back to its proportional split
    #: rather than pretending it has alignment it does not.
    words: list[Word] = field(default_factory=list)
    #: Which path produced this — shown to the user, because the three
    #: differ in cost and in Vietnamese accuracy.
    source: str = ""


# ── availability ──────────────────────────────────────────────────────


def openai_available() -> bool:
    """A real platform key, the switch on, and in that order.

    Two halves, and the switch is the one that matters. The key half: the
    Codex CLI's token reaches ``chatgpt.com/backend-api/codex`` and nothing
    else, so there is no audio endpoint behind it — users conflate the two,
    hence the check for the platform key specifically.

    The switch half is why this function is not just that check. A key
    pasted into Settings to write scripts with GPT is read by the text
    providers AND by this module, so without `OPENAI_STT_ENABLED` whisper-1
    joins the transcription chain the moment that key exists. The user then
    sees a cost dialog saying "Gemini quota", Gemini answers 429 mid-run,
    and every remaining clip is billed to OpenAI at $0.006/minute — money
    nobody agreed to, arriving through a door they did not know was open.
    The image engine already works this way (`OPENAI_IMAGE_ENABLED`); this
    is the same rule for the same reason.
    """
    from flowboard.services import settings_store
    from flowboard.services.llm import secrets

    if not settings_store.get("OPENAI_STT_ENABLED"):
        return False
    return bool(secrets.get_api_key("openai"))


def local_available() -> bool:
    """Whether faster-whisper is installed AND switched on.

    Both halves matter: the package pulls a model download on first use, so
    having it installed is not consent to spend the disk.
    """
    from flowboard.services import settings_store

    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return bool(settings_store.get("LOCAL_STT_ENABLED"))


def sources() -> list[str]:
    """Every path that could answer right now, best first for word timings.

    Ordered by timing quality rather than cost: local faster-whisper leads
    because it is the only free source of real alignment, and alignment is
    the thing the caller cannot approximate its way out of.
    """
    from flowboard.services import transcribe

    out = []
    if local_available():
        out.append("local")
    if openai_available():
        out.append("whisper-1")
    if transcribe.available():
        out.append("gemini")
    return out


# ── the SRT chain ─────────────────────────────────────────────────────


async def to_srt(
    video: Path,
    *,
    language: Optional[str] = None,
    storage_dir: Optional[Path] = None,
    timeout: float = 240.0,
    want_words: bool = False,
) -> Transcript:
    """Subtitles, from whichever source can answer.

    Gemini first when words are not needed: it is already integrated and
    costs the least to reach. When ``want_words`` is set the order flips to
    the two sources that can actually align, because a transcript without
    timings does not answer the question that was asked.

    Raises ``SttError`` only when every source fails, naming each.
    """
    from flowboard.services import transcribe

    order = ["local", "whisper-1", "gemini"] if want_words else ["gemini", "whisper-1", "local"]
    usable = [s for s in order if s in set(sources())]
    if not usable:
        raise SttError(
            "Chưa có nguồn phiên âm nào: cần khoá Gemini, khoá OpenAI, "
            "hoặc bật faster-whisper trong Cài đặt.",
            kind="no_source",
        )

    reasons: list[str] = []
    for name in usable:
        try:
            if name == "gemini":
                srt = await transcribe.video_to_srt(
                    video, language=language, storage_dir=storage_dir, timeout=timeout
                )
                return Transcript(srt=srt, words=[], source="gemini")
            if name == "whisper-1":
                return await _via_openai(
                    video, language=language, storage_dir=storage_dir,
                    timeout=timeout, want_words=want_words,
                )
            return await _via_local(
                video, language=language, storage_dir=storage_dir, want_words=want_words
            )
        except transcribe.TranscribeError as exc:
            # "no speech" is an answer, not a failure to route around: the
            # next source would spend money to reach the same conclusion.
            if getattr(exc, "kind", "") == "no_speech":
                raise SttError(str(exc), kind="no_speech") from None
            reasons.append(f"{name}: {exc}")
        except SttError as exc:
            if exc.kind == "no_speech":
                raise
            reasons.append(f"{name}: {exc}")
        except Exception as exc:
            logger.warning("stt: %s failed", name, exc_info=True)
            reasons.append(f"{name}: {type(exc).__name__}")

    raise SttError("Không nguồn nào phiên âm được — " + "; ".join(reasons))


# ── OpenAI whisper-1 ──────────────────────────────────────────────────


async def _via_openai(
    video: Path,
    *,
    language: Optional[str],
    storage_dir: Optional[Path],
    timeout: float,
    want_words: bool,
) -> Transcript:
    import httpx

    from flowboard.services import postprod
    from flowboard.services.llm import secrets

    key = secrets.get_api_key("openai")
    if not key:
        raise SttError("no OpenAI API key", kind="no_key")

    workdir = Path(tempfile.mkdtemp(prefix="stt-", dir=storage_dir))
    try:
        audio = postprod.extract_audio(video, workdir / "audio.mp3")
        size = audio.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise SttError(
                f"Âm thanh {size // (1024 * 1024)} MB, vượt giới hạn "
                f"{MAX_AUDIO_BYTES // (1024 * 1024)} MB của whisper-1.",
                kind="too_long",
            )

        data = {
            "model": WHISPER_MODEL,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language
        files = [("file", (audio.name, audio.read_bytes(), "audio/mpeg"))]
        # Repeated field, not a list value: the API takes
        # `timestamp_granularities[]` once per granularity.
        if want_words:
            files.append(("timestamp_granularities[]", (None, "word")))
        files.append(("timestamp_granularities[]", (None, "segment")))

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                _API_URL,
                headers={"Authorization": f"Bearer {key}"},
                data=data,
                files=files,
            )
        if resp.status_code != 200:
            # The body can echo request details; take only the message.
            detail = ""
            try:
                detail = str(resp.json().get("error", {}).get("message", ""))[:200]
            except Exception:
                detail = f"HTTP {resp.status_code}"
            raise SttError(f"whisper-1 từ chối: {detail}")
        payload = resp.json()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    segments = payload.get("segments") or []
    if not segments and not (payload.get("text") or "").strip():
        raise SttError("Không tìm thấy lời nói trong clip.", kind="no_speech")

    words = [
        Word(text=str(w.get("word", "")).strip(),
             start=float(w.get("start", 0.0)), end=float(w.get("end", 0.0)))
        for w in (payload.get("words") or [])
        if str(w.get("word", "")).strip()
    ]
    return Transcript(srt=_srt_from_segments(segments), words=words, source="whisper-1")


# ── faster-whisper, local ─────────────────────────────────────────────


async def _via_local(
    video: Path,
    *,
    language: Optional[str],
    storage_dir: Optional[Path],
    want_words: bool,
) -> Transcript:
    """Runs the model in a worker thread.

    Transcription is CPU-bound and blocking; leaving it on the event loop
    would stall every other request for the duration — the same mistake
    already fixed once in the post-production path.
    """
    import asyncio

    from flowboard.services import postprod

    workdir = Path(tempfile.mkdtemp(prefix="stt-local-", dir=storage_dir))
    try:
        audio = postprod.extract_audio(video, workdir / "audio.mp3")
        segments, words = await asyncio.to_thread(
            _run_local, audio, language, want_words
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not segments:
        raise SttError("Không tìm thấy lời nói trong clip.", kind="no_speech")
    return Transcript(srt=_srt_from_segments(segments), words=words, source="local")


def _run_local(audio: Path, language: Optional[str], want_words: bool):
    from faster_whisper import WhisperModel

    from flowboard.config import STORAGE_DIR
    from flowboard.services import settings_store

    size = settings_store.get("LOCAL_STT_MODEL") or DEFAULT_LOCAL_MODEL
    model = WhisperModel(
        size,
        device="cpu",
        compute_type="int8",
        download_root=str(STORAGE_DIR / "models"),
    )
    raw, _info = model.transcribe(
        str(audio), language=language or None, word_timestamps=want_words
    )

    segments: list[dict] = []
    words: list[Word] = []
    for seg in raw:
        segments.append({
            "start": float(seg.start), "end": float(seg.end),
            "text": (seg.text or "").strip(),
        })
        for w in (getattr(seg, "words", None) or []):
            text = (getattr(w, "word", "") or "").strip()
            if text:
                words.append(Word(text=text, start=float(w.start), end=float(w.end)))
    return segments, words


# ── shared ────────────────────────────────────────────────────────────


def _srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _srt_from_segments(segments: list[dict]) -> str:
    """SRT text from whichever segment shape the source returned.

    Both sources agree on ``{start, end, text}`` in seconds, which is why
    this is shared: two spellings of the same conversion is how the two
    paths drift apart.
    """
    blocks: list[str] = []
    index = 0
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        start, end = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        if end <= start:
            continue
        index += 1
        blocks.append(
            f"{index}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")
