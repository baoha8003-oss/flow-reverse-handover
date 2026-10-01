"""Shared pool of Gemini API keys, used by both TTS and the LLM layer.

Two things this fixes.

**The pool was a single key.** The packaged tool ships
``data_general/gemini_api_key.txt`` with a whole list of keys and rotates
through them, because Gemini's free tier rate-limits per key. This build read
only the first line, so a user with eight keys got the quota of one.

**The keys were invisible to the LLM layer.** They were wired to narration and
nothing else, while `auto_prompt` / `vision` / `planner` demanded a CLI OAuth
login. A user who cannot complete that login — which is exactly what happened —
has working keys sitting on disk and no way to spend them.

Sources, in precedence order:

1. ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` — a deliberate per-process override.
2. ``~/.flowboard/secrets.json`` — what the user pasted into Settings. Stored
   at mode 0600 and never echoed back.
3. ``data_general/gemini_api_key.txt`` — the packaged tool's own file, read in
   full. A convenience for this machine; anyone handed the app instead pastes
   a key into Settings.

Never log or return a key. Callers report counts and availability.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from flowboard.services import assets

logger = logging.getLogger(__name__)

PROVIDER = "gemini"
KEY_FILE = "gemini_api_key.txt"
ENV_VARS: tuple[str, ...] = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

# A key rides in an HTTP header, so it must be printable ASCII with no spaces.
# Anything else is a mangled file (a BOM, a UTF-16 save, a pasted non-breaking
# space) and has to be rejected here — httpx would otherwise put the raw bytes
# into its own exception text and straight into the log.
KEY_RE = re.compile(r"^[!-~]+$")

# How long a key sits out after the API reports it is over quota. Gemini's
# free tier resets per minute, so a short rest is enough to bring a key back
# without pinning the whole pool on one bad minute.
COOLDOWN_S = 90.0

_lock = threading.Lock()
_cursor = 0
_cooldown_until: dict[str, float] = {}


def _from_env() -> list[str]:
    out = []
    for name in ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            out.append(value)
    return out


def _from_secrets() -> list[str]:
    # Imported lazily: secrets touches the home directory, and this module is
    # pulled in during route import at startup.
    try:
        from flowboard.services.llm import secrets

        value = secrets.get_api_key(PROVIDER)
    except Exception:
        return []
    return [value.strip()] if isinstance(value, str) and value.strip() else []


def _key_file() -> Optional["Path"]:
    """Where the packaged key list lives, or None when it is switched off.

    ``FLOWBOARD_GEMINI_KEY_FILE`` overrides the location; setting it to an
    empty string disables the file source entirely. The test suite does
    exactly that: ``ASSET_ROOT`` deliberately points at the real packaged
    library (postprod tests use its real ffmpeg and fonts), so without this
    switch every test run would pick up the developer's own live keys —
    making the suite spend real quota and turning "is this provider
    available?" into a question about the machine rather than the code.
    """
    override = os.environ.get("FLOWBOARD_GEMINI_KEY_FILE")
    if override is not None:
        return Path(override) if override.strip() else None
    return assets.DATA_DIR / KEY_FILE


def _from_file() -> list[str]:
    """Every non-empty line of the packaged key file, in order."""
    path = _key_file()
    if path is None:
        return []
    try:
        # Typed by hand on Windows, so it arrives BOM-first more often than
        # not; plain utf-8 would leave the BOM glued to the first key.
        raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    except (OSError, UnicodeError):
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def all_keys() -> list[str]:
    """Every usable key, in precedence order, deduplicated.

    Malformed entries are dropped rather than returned: passing one to httpx
    raises with the raw value in the message, which then lands in the log.
    """
    seen: set[str] = set()
    out: list[str] = []
    for candidate in (*_from_env(), *_from_secrets(), *_from_file()):
        if candidate in seen:
            continue
        seen.add(candidate)
        if KEY_RE.match(candidate):
            out.append(candidate)
        else:
            logger.warning(
                "gemini: ignoring a malformed key (non-printable or spaces) "
                "from the key sources; re-save the file as plain UTF-8"
            )
    return out


def count() -> int:
    return len(all_keys())


def available() -> bool:
    return bool(all_keys())


def next_key() -> Optional[str]:
    """The next key to try, skipping any that are cooling off after a 429.

    Round-robin rather than always-first: hammering key #1 until it is
    exhausted and only then moving on wastes the pool's whole point, which is
    spreading load across keys.

    When every key is cooling off, the one closest to waking is returned
    anyway. The caller then gets the real API error, which is far more useful
    than "no key configured" — the user does have keys.
    """
    keys = all_keys()
    if not keys:
        return None
    global _cursor
    now = time.monotonic()
    with _lock:
        for _ in range(len(keys)):
            key = keys[_cursor % len(keys)]
            _cursor += 1
            if _cooldown_until.get(key, 0.0) <= now:
                return key
        # All resting — take the one that wakes first.
        return min(keys, key=lambda k: _cooldown_until.get(k, 0.0))


def mark_exhausted(key: Optional[str]) -> None:
    """Rest a key that just answered 429 / RESOURCE_EXHAUSTED."""
    if not key:
        return
    with _lock:
        _cooldown_until[key] = time.monotonic() + COOLDOWN_S
    logger.info("gemini: a key hit its quota; resting it for %.0fs", COOLDOWN_S)


def reset_cooldowns() -> None:
    """Clear rotation state. For tests and for a key-list change."""
    global _cursor
    with _lock:
        _cursor = 0
        _cooldown_until.clear()


def redact(message: str, *keys: Optional[str]) -> str:
    """Last line of defence: no error text ever carries a key."""
    out = message
    for key in keys:
        if key and len(key) >= 8:
            out = out.replace(key, "***")
    return out
