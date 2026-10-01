"""Gemini text-to-speech narration for the local post-production pipeline.

Every other post step runs offline against the packaged asset library; this
is the one that must reach the network, because nothing local produces
Vietnamese narration at a usable quality. The user brings their own API key
— it is read from an explicit argument, the environment, or the packaged
key file, and it is never written to a log line or an exception message.

Two things about the API shape drive the code here:

- The response is **not** a playable file. Gemini returns base64 raw PCM
  (signed 16-bit little-endian, mono, sample rate announced in the part's
  ``mimeType``), so this module owns the RIFF/WAVE framing. Writing the
  decoded payload straight to disk produces a file no player will open.
- Delivery is prompt-steered, not parameterised. The narrator personas in
  ``data_general/voice_styles.json`` are prepended to the script as an
  instruction, which is the only tone control the model exposes.

The 30 voice names below are the model's own catalogue; the asset library
ships a ``.wav`` preview named after each one, so the local samples double
as the voice picker's audition clips.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

import httpx

from dataclasses import dataclass

from flowboard.config import STORAGE_DIR
from flowboard.services import voice_catalog
from flowboard.services import assets

logger = logging.getLogger(__name__)

# Created on first use, not at import time: an unwritable STORAGE_DIR would
# otherwise raise while the routers import this module at startup, turning
# one unavailable feature into an app that refuses to boot.
NARRATION_DIR = STORAGE_DIR / "narration"

DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TIMEOUT_SECS = 180.0

# The key file may carry more than one line; the first non-empty one wins.
_API_KEY_FILE = "gemini_api_key.txt"
_ENV_KEYS: tuple[str, ...] = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

_PERSONA_FILE = "voice_styles.json"

# Gemini's prebuilt TTS voices, in the order Google documents them.
VOICES: tuple[str, ...] = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)

# audio/L16 is uncompressed signed 16-bit PCM; the mimeType carries the rate
# and (rarely) a channel count, everything else is fixed by the format.
_SAMPLE_WIDTH_BYTES = 2
_FALLBACK_RATE_HZ = 24000
_FALLBACK_CHANNELS = 1

_MIME_PARAM_RE = re.compile(r"(\w+)\s*=\s*(\d+)")
_MIME_DEPTH_RE = re.compile(r"audio/L(\d+)", re.IGNORECASE)

# An API key rides in an HTTP header, so it has to be printable ASCII with no
# spaces. Anything else is a mangled key file (a BOM, a UTF-16 save, a pasted
# non-breaking space) and must be rejected here: httpx would otherwise put the
# raw key bytes into its own exception text, past _redact and into the log.
_KEY_RE = re.compile(r"^[!-~]+$")

Persona = dict[str, str]


class TTSError(RuntimeError):
    """Narration could not be produced. Message is always key-free."""


# ── voices ────────────────────────────────────────────────────────────────


def list_voices() -> list[dict[str, Any]]:
    """Every voice with the local preview sample, when one is installed."""
    voices: list[dict[str, Any]] = []
    for name in VOICES:
        sample = assets.voice_sample_path(name)
        voices.append(
            {
                "name": name,
                "hasSample": sample is not None,
                "sample": str(sample) if sample is not None else None,
            }
        )
    return voices


# ── personas ──────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _personas_cached() -> tuple[Persona, ...]:
    """Parsed persona file. Immutable so the cache can't be mutated by callers."""
    path = assets.DATA_DIR / _PERSONA_FILE
    try:
        # utf-8-sig, not utf-8: an editor that saves the file back with a BOM
        # would otherwise make json.loads fail and silently empty the list.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        logger.info("tts: no persona file at %s", path)
        return ()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("tts: persona file unreadable (%s)", exc)
        return ()
    if not isinstance(raw, list):
        logger.warning("tts: persona file is not a list, ignoring")
        return ()
    return tuple(
        {"title": entry["title"], "description": entry["description"]}
        for entry in raw
        if isinstance(entry, dict)
        and isinstance(entry.get("title"), str)
        and isinstance(entry.get("description"), str)
    )


def load_personas() -> list[Persona]:
    """Narrator personas from the asset library; empty when not installed.

    The file ships with the tool and never changes at runtime, so parsing is
    cached — call ``clear_persona_cache()`` after relocating the asset root.
    """
    return [dict(persona) for persona in _personas_cached()]


def clear_persona_cache() -> None:
    _personas_cached.cache_clear()


def find_persona(title: str) -> Optional[Persona]:
    """Case-insensitive lookup by title."""
    wanted = (title or "").strip().casefold()
    if not wanted:
        return None
    for persona in _personas_cached():
        if persona["title"].strip().casefold() == wanted:
            return dict(persona)
    return None


def _persona_description(persona: Union[Persona, str, None]) -> Optional[str]:
    if persona is None:
        return None
    if isinstance(persona, str):
        if not persona.strip():
            return None
        found = find_persona(persona)
        # A string that doesn't name a library persona IS the delivery note
        # — that's the documented contract ("persona title, or free-text
        # delivery notes"), and raising on it turned a valid request into a
        # 500 that looked like a server fault.
        return found["description"] if found else persona.strip()
    description = persona.get("description")
    if not isinstance(description, str) or not description.strip():
        raise TTSError("persona has no description to steer delivery with")
    return description.strip()


def style_text(text: str, persona: Union[Persona, str, None] = None) -> str:
    """The prompt actually sent to the model: delivery instruction + script."""
    description = _persona_description(persona)
    if not description:
        return text
    return f"Read in this voice: {description}\n\n{text}"


# ── api key ───────────────────────────────────────────────────────────────


def resolve_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """Explicit argument, then environment, then the packaged key file.

    Never logged and never echoed back into an error — callers that want to
    tell the user something should use ``api_key_available()``.
    """
    if api_key and api_key.strip():
        return api_key.strip()
    for name in _ENV_KEYS:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return _api_key_from_file()


def _api_key_from_file() -> Optional[str]:
    path = assets.DATA_DIR / _API_KEY_FILE
    try:
        # A key file is typed by hand on Windows, so it arrives BOM-first more
        # often than not; utf-8 would leave ﻿ glued to the front of the key.
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    except (OSError, UnicodeError):
        return None
    for line in raw.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def api_key_available() -> bool:
    return resolve_api_key() is not None


def _redact(message: str, api_key: Optional[str]) -> str:
    """Last line of defence: no error text ever carries the key."""
    if api_key and len(api_key) >= 8:
        return message.replace(api_key, "***")
    return message


# ── pcm → wav ─────────────────────────────────────────────────────────────


def _audio_format(mime_type: str) -> tuple[int, int]:
    """``(rate_hz, channels)`` from e.g. ``audio/L16;codec=pcm;rate=24000``.

    The sample width is not negotiable — this module frames 16-bit PCM — so a
    payload announcing any other depth is refused rather than written out at
    the wrong width, which would sound like noise at the wrong duration.
    """
    declared = _MIME_DEPTH_RE.search(mime_type or "")
    if declared and int(declared.group(1)) != _SAMPLE_WIDTH_BYTES * 8:
        raise TTSError(
            f"Gemini TTS returned unsupported audio format: {mime_type!r}"
        )
    params = {k.lower(): int(v) for k, v in _MIME_PARAM_RE.findall(mime_type or "")}
    rate = params.get("rate") or _FALLBACK_RATE_HZ
    channels = params.get("channels") or _FALLBACK_CHANNELS
    return rate, channels


def write_wav(pcm: bytes, out_path: Path, *, rate: int, channels: int = 1) -> Path:
    """Wrap raw signed 16-bit little-endian PCM in a RIFF/WAVE container."""
    if not pcm:
        raise TTSError("no audio data to write")
    block_align = _SAMPLE_WIDTH_BYTES * channels
    if len(pcm) % block_align:
        raise TTSError(
            f"PCM payload of {len(pcm)} bytes is not a whole number of "
            f"{channels}-channel 16-bit frames"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out_path


# ── synthesis ─────────────────────────────────────────────────────────────


def _build_body(styled: str, voice: str) -> dict[str, Any]:
    return {
        "contents": [{"parts": [{"text": styled}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
            },
        },
    }


def _inline_audio(payload: Any, api_key: Optional[str]) -> tuple[bytes, str]:
    """Pull ``(pcm_bytes, mime_type)`` out of a generateContent response."""
    if not isinstance(payload, dict):
        raise TTSError("Gemini TTS returned a non-object response")

    candidates = payload.get("candidates") or []
    if not candidates:
        feedback = payload.get("promptFeedback") or {}
        reason = feedback.get("blockReason") or "no candidates returned"
        raise TTSError(_redact(f"Gemini TTS produced no audio: {reason}", api_key))

    parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
    for part in parts:
        if not isinstance(part, dict):
            continue
        inline = part.get("inlineData") or part.get("inline_data")
        if not isinstance(inline, dict):
            continue
        data = inline.get("data")
        if not isinstance(data, str) or not data:
            continue
        mime_type = inline.get("mimeType") or inline.get("mime_type") or ""
        try:
            return base64.b64decode(data), str(mime_type)
        except (ValueError, TypeError) as exc:
            raise TTSError("Gemini TTS returned undecodable audio data") from exc

    finish = (candidates[0] or {}).get("finishReason")
    detail = f" (finishReason={finish})" if finish else ""
    raise TTSError(_redact(f"Gemini TTS response carried no audio part{detail}", api_key))


@dataclass(frozen=True)
class VoicePlan:
    """Which engine narrates, with which voice, and what to tell the user."""

    engine: str  # "gemini" | "openai"
    voice: Optional[str]  # None = narrate nothing at all
    note: Optional[str]


def plan_voice(engine: Optional[str], voice: Optional[str]) -> VoicePlan:
    """Settle engine and voice together, because neither answers alone.

    Six of the nine shipped workflows ask for `engine: "ChatGPT"` with
    `voice: "Ember"`, and two ask for a cloned voice. All eight name a facility
    this build either has switched off by default or has deliberately not
    built, so every one of them needs a decision here rather than a failure
    three layers down.

    **Asking for OpenAI while the OpenAI path is off falls back to Gemini and
    says so — it does NOT refuse.** That is the opposite of what
    `openai_images` does for a node whose engine is OpenAI, and the difference
    is deliberate: there, falling back would spend the user's *Flow credits* on
    work they did not ask for, so refusing protects money. Here the fallback is
    this app's own default engine, already counted in `/estimate`, while
    refusing would make every imported packaged template fail out of the box —
    which is the exact thing the fallback exists to prevent.

    A switch that is ON but has no key is the other case, and that one DOES
    fail: it is a misconfiguration the user made on purpose, not a default.
    """
    asked = (engine or "").strip().lower() or None

    if asked == "openai":
        from flowboard.services import openai_tts

        if _setting_on("OPENAI_TTS_ENABLED"):
            if not openai_tts.enabled():
                # Switched on, no key: say which of the two is missing rather
                # than narrating with something the user did not choose.
                return VoicePlan(
                    "openai",
                    None,
                    "giọng OpenAI đang bật nhưng chưa có khoá API — dán khoá "
                    "OpenAI vào Cài đặt, hoặc tắt OPENAI_TTS_ENABLED để đọc "
                    "bằng Gemini.",
                )
            choice = voice_catalog.resolve_openai(
                voice, default=openai_default_voice()
            )
            return VoicePlan("openai", choice.voice, choice.warning)

        gemini = voice_catalog.resolve(voice, default=DEFAULT_VOICE)
        if gemini.source == "no_voice":
            return VoicePlan("gemini", None, None)
        note = (
            "node yêu cầu giọng ChatGPT nhưng đường OpenAI đang tắt — đã đọc "
            f"bằng Gemini {gemini.voice!r}. Bật OPENAI_TTS_ENABLED trong Cài "
            "đặt nếu muốn dùng giọng OpenAI."
        )
        return VoicePlan("gemini", gemini.voice, note)

    choice = voice_catalog.resolve(voice, default=DEFAULT_VOICE)
    if asked in ("clone", "capcut") and choice.source != "no_voice":
        # Named for what is missing. "Clone" drives a third-party service
        # through a browser-impersonating client and "CapCut" calls an
        # internal ByteDance API; neither is built here (docs/spec.md), and
        # a user reading "đã đọc bằng Kore" with no reason would file a bug.
        what = "nhân bản giọng" if asked == "clone" else "giọng CapCut"
        return VoicePlan(
            "gemini",
            choice.voice,
            f"node yêu cầu {what} — bản này không có đường đó, đã đọc bằng "
            f"Gemini {choice.voice!r}.",
        )
    return VoicePlan("gemini", choice.voice, choice.warning)


def openai_default_voice() -> str:
    from flowboard.services import openai_tts

    raw = _setting("OPENAI_TTS_VOICE")
    if isinstance(raw, str) and raw.strip().lower() in openai_tts.VOICES:
        return raw.strip().lower()
    return openai_tts.DEFAULT_VOICE


def _setting(key: str):
    from flowboard.services import settings_store

    return settings_store.get(key)


def _setting_on(key: str) -> bool:
    return bool(_setting(key))


def resolve_voice(name: Optional[str]) -> "voice_catalog.VoiceChoice":
    """Turn whatever the node says into a voice this build can actually use.

    `synthesize` below rejects any name outside Google's thirty, and it should
    keep doing that — it is the SDK boundary. This is the layer above it, where
    a name imported from another tool gets a substitute and a sentence saying
    so, instead of a node that fails after the video has been paid for.
    """
    return voice_catalog.resolve(name, default=DEFAULT_VOICE)


def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    persona: Union[Persona, str, None] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    out_path: Optional[Path] = None,
) -> Path:
    """Narrate ``text`` and write a playable WAV, returning its path.

    ``persona`` is either one of ``load_personas()`` or its title; its
    description is prepended to the script as a delivery instruction.
    """
    script = (text or "").strip()
    if not script:
        raise TTSError("nothing to narrate: text is empty")
    if voice not in VOICES:
        raise TTSError(f"unknown Gemini TTS voice: {voice!r}")

    key = resolve_api_key(api_key)
    if not key:
        raise TTSError(
            "no Gemini API key: set GEMINI_API_KEY or place the key in "
            f"{assets.DATA_DIR / _API_KEY_FILE}"
        )
    if not _KEY_RE.match(key):
        raise TTSError(
            "Gemini API key contains characters that cannot be sent in a "
            f"header; re-save {assets.DATA_DIR / _API_KEY_FILE} as plain "
            "UTF-8 with the key on the first line"
        )

    resolved_model = model or os.environ.get("FLOWBOARD_TTS_MODEL") or DEFAULT_MODEL
    styled = style_text(script, persona)
    if out_path:
        target = Path(out_path)
    else:
        NARRATION_DIR.mkdir(parents=True, exist_ok=True)
        target = NARRATION_DIR / f"narration-{uuid.uuid4().hex}.wav"

    # The key travels in a header, never the query string — the URL ends up
    # in logs, proxies and exception text.
    url = _ENDPOINT.format(model=resolved_model)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    try:
        with httpx.Client(timeout=_TIMEOUT_SECS) as client:
            response = client.post(url, headers=headers, json=_build_body(styled, voice))
    except httpx.HTTPError as exc:
        raise TTSError(_redact(f"Gemini TTS request failed: {exc}", key)) from exc

    if response.status_code != 200:
        raise TTSError(_redact(_error_message(response, resolved_model), key))

    try:
        payload = response.json()
    except ValueError as exc:
        raise TTSError("Gemini TTS returned a non-JSON response") from exc

    pcm, mime_type = _inline_audio(payload, key)
    rate, channels = _audio_format(mime_type)
    write_wav(pcm, target, rate=rate, channels=channels)

    logger.info(
        "tts: %d chars -> %s (voice=%s, model=%s, %d Hz)",
        len(styled), target.name, voice, resolved_model, rate,
    )
    return target


def _error_message(response: httpx.Response, model: str) -> str:
    """The API's own message when it sends one, else the raw body."""
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "").strip()
    except ValueError:
        pass
    if not detail:
        detail = (response.text or "").strip()[:300]
    return f"Gemini TTS {model} returned HTTP {response.status_code}: {detail}"
