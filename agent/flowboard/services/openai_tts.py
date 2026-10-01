"""Narration through OpenAI's official speech endpoint. Default OFF.

Six of the nine shipped workflows have a `create_voice` node that says
`engine: "ChatGPT"`, `voice: "Ember"`. Ember is a voice in the ChatGPT *app*,
not a voice the API offers, and the app's speech is not an API — the packaged
tool reaches it through a browser-impersonating HTTP client, which is the same
bridge this build declined to rebuild (see `docs/spec.md`). `/v1/audio/speech`
is the documented endpoint that does the same job, so that is what is built
here, and `voice_catalog` maps the app name onto an API voice.

**Two switches, both off by default**, matching `openai_images`: the key alone
must not turn on a paid path — a key pasted in to write scripts with GPT is not
consent to bill audio against it.

**Cost is per character, not per call**, so it is metered differently from
every other job in this app. `/estimate` therefore counts characters, not
requests: "one narration" says nothing about whether it costs a tenth of a cent
or a dollar.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import httpx

from flowboard.config import STORAGE_DIR
from flowboard.services.llm import secrets

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.openai.com/v1/audio/speech"
_TIMEOUT_SECS = 180.0

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "alloy"

#: OpenAI's documented speech voices. Kept as data so an unknown name is a
#: refusal with a list, not a 400 from the other end.
VOICES: tuple[str, ...] = (
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer", "verse",
)

#: Published price for `gpt-4o-mini-tts`, per million input characters, as of
#: 2026-09-18. Recorded with its date the way `routes/estimate` requires:
#: a number without a date is a number nobody can check later.
PRICE_PER_MILLION_CHARS_USD = 0.60
PRICE_AS_OF = "2026-09-18"


class OpenAITTSError(RuntimeError):
    """Speech could not be produced. Message is always key-free."""


def _setting(key: str):
    # Imported inside the call, as `openai_images` does: `settings_store`
    # touches the DB, and importing it at module scope pulls the DB into every
    # importer of this module including the worker's cold start.
    from flowboard.services import settings_store

    return settings_store.get(key)


def enabled() -> bool:
    """Both the switch and the key — never the key alone.

    `openai_images.api_available` and `stt.openai_available` answer the same
    way for the same reason: a credential lying around is not an instruction
    to spend it.
    """
    if not _setting("OPENAI_TTS_ENABLED"):
        return False
    return bool(secrets.get_api_key("openai"))


def resolve_model() -> str:
    raw = _setting("OPENAI_TTS_MODEL")
    return raw.strip() if isinstance(raw, str) and raw.strip() else DEFAULT_MODEL


def estimate_chars(text: str) -> int:
    return len((text or "").strip())


def _out_dir() -> Path:
    directory = STORAGE_DIR / "narration"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    instructions: Optional[str] = None,
    model: Optional[str] = None,
    out_path: Optional[Path] = None,
) -> Path:
    """Narrate ``text`` through `/v1/audio/speech` and return the written file."""
    script = (text or "").strip()
    if not script:
        raise OpenAITTSError("nothing to narrate: text is empty")
    if voice not in VOICES:
        raise OpenAITTSError(
            f"unknown OpenAI speech voice: {voice!r} — choose one of "
            + ", ".join(VOICES)
        )
    if not _setting("OPENAI_TTS_ENABLED"):
        # Refusing rather than falling back to Gemini: quietly changing engine
        # is how a node ends up producing a voice nobody chose.
        raise OpenAITTSError(
            "giọng OpenAI đang tắt — bật OPENAI_TTS_ENABLED trong Cài đặt trước"
        )
    key = secrets.get_api_key("openai")
    if not key:
        raise OpenAITTSError("chưa có khoá API OpenAI trong Cài đặt")

    payload: dict = {
        "model": model or resolve_model(),
        "input": script,
        "voice": voice,
        "response_format": "mp3",
    }
    if instructions and instructions.strip():
        # `instructions` is how the documented endpoint takes a delivery note,
        # the same role `persona` plays for Gemini.
        payload["instructions"] = instructions.strip()

    try:
        with httpx.Client(timeout=_TIMEOUT_SECS) as client:
            resp = client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        # Never interpolate the response or the key: httpx puts request headers
        # into its own exception text.
        raise OpenAITTSError(f"không gọi được OpenAI TTS: {type(exc).__name__}") from None

    if resp.status_code != 200:
        raise OpenAITTSError(_safe_status(resp.status_code))
    if not resp.content:
        raise OpenAITTSError("OpenAI TTS trả về phản hồi rỗng")

    # Content-addressed, not `hash()`: Python randomises string hashing per
    # process, so a retry would write a second file under a different name and
    # leave the first orphaned.
    digest = hashlib.sha1(script.encode("utf-8")).hexdigest()[:12]
    target = Path(out_path) if out_path else _out_dir() / f"openai-{digest}.mp3"
    target.write_bytes(resp.content)
    logger.info("openai tts: %d chars -> %s", len(script), target.name)
    return target


def _safe_status(status: int) -> str:
    """A sentence per status, carrying no response body.

    The body of a 401 from this endpoint can echo the key prefix back.
    """
    if status == 401:
        return "OpenAI từ chối khoá API (401) — kiểm tra lại khoá trong Cài đặt"
    if status == 429:
        return "OpenAI giới hạn tốc độ (429) — thử lại sau"
    if status == 400:
        return "OpenAI từ chối yêu cầu (400) — thường là tên giọng hoặc model sai"
    return f"OpenAI TTS lỗi HTTP {status}"
