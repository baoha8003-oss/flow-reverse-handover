"""Speech in a video → SRT subtitle text.

Extracted from the ``/api/postprod/transcribe`` route so the canvas can use
the same path: an ``edit_video`` node with ``enable_sub`` on has to produce
an SRT before it can burn one, and a second implementation of "ask Gemini for
subtitles" is exactly the kind of drift that put the post-production planner
and its handler in different languages.

Tied to Gemini rather than routed through the generic provider dispatch, and
deliberately so: this sends AUDIO, which the other providers in this build
cannot accept. A generic dispatch would fail confusingly depending on which
provider the user happened to have selected.

The packaged tool does the same job with CapCut's speech-to-text
(``capcut_stt_to_srt`` in the binary), which needs a CapCut account. Using
the Gemini key the user already has is the same result without the extra
login.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: A minute of 16 kHz mono at 48 kbps is roughly 360 KB, and the file travels
#: base64-encoded inside the request body. This caps one call at about a
#: half-hour clip rather than letting it carry an unbounded payload.
MAX_AUDIO_BYTES = 12 * 1024 * 1024

#: What the model is told to answer when the clip is silent. Checked for
#: exactly, so a clip with no speech is a clear "nothing to subtitle" rather
#: than an empty file that burns as a blank track.
NO_SPEECH = "NO_SPEECH"


class TranscribeError(RuntimeError):
    """Anything that stops a clip becoming subtitles.

    ``kind`` lets the HTTP layer pick a status code without parsing the
    message: ``no_key`` / ``too_long`` / ``no_speech`` are the user's to fix,
    ``provider`` is upstream's.
    """

    def __init__(self, message: str, *, kind: str = "provider") -> None:
        super().__init__(message)
        self.kind = kind


def available() -> bool:
    """Whether a transcription can be attempted at all.

    Callers use this to avoid scheduling work that is certain to fail — a
    subtitle pass with no key would only ever produce a failed request.
    """
    from flowboard.services import gemini_keys

    return gemini_keys.available()


def _strip_fences(text: str) -> str:
    """Models wrap output in code fences even when told not to."""
    srt = text.strip()
    if not srt.startswith("```"):
        return srt
    srt = srt.lstrip("`")
    if srt.lower().startswith("srt"):
        srt = srt[3:]
    return srt.rsplit("```", 1)[0].strip()


async def video_to_srt(
    video: Path,
    *,
    language: Optional[str] = None,
    storage_dir: Optional[Path] = None,
    timeout: float = 240.0,
) -> str:
    """Transcribe a clip's speech and return SRT text.

    Raises ``TranscribeError``. The audio is extracted to a temp directory
    that is always cleaned up, including on failure.
    """
    from flowboard.services import postprod
    from flowboard.services.llm import registry
    from flowboard.services.llm.base import LLMError

    if not available():
        raise TranscribeError(
            "Automatic subtitles need a Gemini API key. Add one in "
            "Settings → AI Providers.",
            kind="no_key",
        )
    provider = registry.get_provider("gemini")
    if provider is None:  # pragma: no cover — the registry always has it
        raise TranscribeError("gemini provider missing")

    workdir = Path(tempfile.mkdtemp(prefix="transcribe-", dir=storage_dir))
    try:
        audio = postprod.extract_audio(video, workdir / "audio.mp3")
        size = audio.stat().st_size
        if size > MAX_AUDIO_BYTES:
            raise TranscribeError(
                f"That clip's audio is {size // (1024 * 1024)} MB, over the "
                f"{MAX_AUDIO_BYTES // (1024 * 1024)} MB limit. Cut it into "
                "shorter pieces and transcribe those.",
                kind="too_long",
            )
        wanted = (language or "").strip()
        instruction = (
            "Transcribe the speech in this audio into SRT subtitles. "
            "Use real timestamps that match when each line is spoken. "
            + (f"Write the subtitles in {wanted}. " if wanted else "")
            + "Output ONLY the SRT content — no preamble, no markdown fences. "
            f"If there is no speech at all, output exactly: {NO_SPEECH}"
        )
        try:
            text = await provider.run(
                instruction, attachments=[str(audio)], timeout=timeout
            )
        except LLMError as exc:
            raise TranscribeError(str(exc)) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    srt = _strip_fences(text)
    if srt == NO_SPEECH or not srt:
        raise TranscribeError("No speech was found in that clip.", kind="no_speech")
    return srt


def write_srt(srt: str, target: Path) -> Path:
    """Write subtitle text where ffmpeg will read it correctly.

    The BOM is not decoration: without it ffmpeg reads a UTF-8 file as the
    system codepage on Windows and burns mojibake into the picture.
    """
    target.write_text(srt, encoding="utf-8-sig")
    return target
