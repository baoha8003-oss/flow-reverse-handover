"""App settings: shipped config.json defaults layered under user overrides.

The packaged tool's ``data_general/config.json`` is the source of defaults.
We only ever READ it — the desktop exe owns that file. User changes are
stored per-key in the ``appsetting`` table and merged on top.

Two safety properties:

1. **Whitelist, not blocklist.** Only keys in ``SETTABLE`` are ever served
   or accepted. Account/token/cookie keys (``account1``, ``account1_token``,
   ``grok_account``, ``*_token``) are simply not in the set, so they never
   appear in a GET response and are refused on write. A sensitive key added
   to config.json later is denied by default, not leaked by omission.

2. **Case-collapsed.** config.json ships duplicate pairs like
   ``VIDEO_OUTPUT_DIR`` / ``video_output_dir``. We canonicalize to the
   UPPER form and look values up case-insensitively, so nothing is silently
   dropped by reading only one casing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlmodel import select

from flowboard.db.models import AppSetting
from flowboard.db.session import get_session
from flowboard.services import assets

logger = logging.getLogger(__name__)

CONFIG_PATH = assets.ASSET_ROOT / "data_general" / "config.json"

# Minimal defaults so the UI works even without the TOOL asset library
# present. config.json overrides these; user settings override both.
BASELINE: dict[str, Any] = {
    "VIDEO_ASPECT_RATIO": "9:16",
    "VIDEO_DURATION_SECONDS": 8,
    # 720p, not the packaged tool's 480p. Nothing read this key until the batch
    # transport gave Omni a resolution slot, and Flow accepts only 360p or 720p
    # there — so the old default was a value on screen that no dispatch could
    # ever use.
    "VIDEO_RESOLUTION": "720p",
    "VEO_MODEL": "Veo 3.1 - Lite [Lower Priority]",
    "MULTI_VIDEO": 4,
    "OUTPUT_COUNT": 1,
    "AUTO_UPSCALE": True,
    "DOWNLOAD_MODE": "720",
    "SEED_MODE": "Random",
    "SEED_VALUE": 9797,
    "RETRY_WITH_ERROR": 3,
    "CREATE_IMAGE_MODEL": "Nano Banana 2",
    "CREATE_IMAGE_QUALITY": "1k",
}

# The only keys the UI may read or write. Canonical UPPER form.
SETTABLE: frozenset[str] = frozenset(
    {
        # generation / output
        "VIDEO_ASPECT_RATIO", "VIDEO_DURATION_SECONDS", "VIDEO_RESOLUTION",
        "VEO_MODEL", "MULTI_VIDEO", "OUTPUT_COUNT", "AUTO_UPSCALE",
        "DOWNLOAD_MODE", "VIDEO_OUTPUT_DIR", "SEED_MODE", "SEED_VALUE",
        "RETRY_WITH_ERROR",
        # timing / cleanup
        "WAIT_GEN_VIDEO", "WAIT_GEN_IMAGE", "WAIT_RESEND_VIDEO",
        "CLEAR_DATA", "CLEAR_DATA_WAIT", "CLEAR_DATA_IMAGE",
        # image
        "CREATE_IMAGE_MODEL", "CREATE_IMAGE_QUALITY",
        # OpenAI as a second image engine. All three switches ship off: one
        # path bills real money per image and the other burns the user's
        # ChatGPT plan quota, so neither may turn itself on merely because a
        # credential happens to be lying around.
        "OPENAI_IMAGE_ENABLED", "OPENAI_IMAGE_RELAY_ENABLED",
        "OPENAI_IMAGE_PREFER_RELAY", "OPENAI_IMAGE_MODEL",
        "OPENAI_IMAGE_QUALITY",
        # speech to text. Both are opt-in: LOCAL_STT_ENABLED gates a model
        # download of a few hundred megabytes, so having faster-whisper
        # installed is not by itself consent to spend the disk, and
        # OPENAI_STT_ENABLED gates whisper-1, which bills per minute against
        # the same key the text providers use — a key pasted in to write
        # scripts with GPT is not consent to pay for transcription.
        "LOCAL_STT_ENABLED", "LOCAL_STT_MODEL", "OPENAI_STT_ENABLED",
        # text to speech through OpenAI's documented /v1/audio/speech. Off by
        # default for the same reason as the two above, plus one of its own:
        # this endpoint bills per CHARACTER, so a single long narration is not
        # a rounding error the way a single image is.
        "OPENAI_TTS_ENABLED", "OPENAI_TTS_MODEL", "OPENAI_TTS_VOICE",
        # subtitles
        "SUB_FONT_FAMILY", "SUB_FONT_SIZE", "SUB_MARGIN_V", "SUB_OUTLINE_COLOR",
        "SUB_OUTLINE_WIDTH", "SUB_PRIMARY_COLOR", "SUB_SHADOW_VAL",
        "SUB_STYLE_TYPE", "SUB_VIDEO_SOURCE_PATH", "SUB_VIDEO_OUTPUT_PATH",
        # logo / watermark
        "LOGO_PATH", "LOGO_W", "LOGO_H", "LOGO_X_LAND", "LOGO_Y_LAND",
        "LOGO_X_PORT", "LOGO_Y_PORT", "WM_MODE",
        # idea → video
        "IDEA_SCENE_COUNT", "IDEA_STYLE", "IDEA_DIALOGUE_LANGUAGE",
        "IDEA_DIALOGUE_MODE", "IDEA_DIALOGUE_MODE_VAL", "IDEA_NO_DIALOGUE",
        "IDEA_BACKEND", "IDEA_BG_MUSIC", "IDEA_MODEL",
        # voice / misc flags
        "VOICE_SYNC_ENABLED", "VOICE_SYNC_ID",
        "WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES", "SKIP_AUDIO_ERROR",
        "RUN_CAPTCHA", "FIX_403_RECAPTCHA",
        # grok generation params (NOT the account)
        "GROK_MULTI_VIDEO", "GROK_VIDEO_LENGTH_SECONDS", "GROK_VIDEO_RESOLUTION",
        # active project (list is read-only-ish; a projects API supersedes later)
        "CURRENT_PROJECT", "PROJECTS",
        # Flow account + project, both user-set since September 2026. The
        # migrated Flow transport signs every call inside the page and carries
        # no `userPaygateTier`, so the tier can no longer be read from
        # `/v1/credits` — it is a LABEL here (which lanes to offer, which price
        # to quote) and never a dispatch gate. FLOW_PROJECT_ID is the fallback
        # for board→project creation when Flow refuses to mint one.
        "FLOW_PAYGATE_TIER", "FLOW_PROJECT_ID",
    }
)


# Value shapes for the keys where a wrong type is more than cosmetic:
# path-typed keys become write targets and count-typed keys become dispatch
# multipliers, so "any JSON for any whitelisted key" would be an
# arbitrary-write / credit-burn primitive the moment a consumer reads them.
# (min, max) for numbers; None for a plain string bound.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "VIDEO_DURATION_SECONDS": (1, 60),
    "MULTI_VIDEO": (1, 20),
    "OUTPUT_COUNT": (1, 10),
    "SEED_VALUE": (0, 2**31 - 1),
    "RETRY_WITH_ERROR": (0, 20),
    "WAIT_GEN_VIDEO": (0, 3600),
    "WAIT_GEN_IMAGE": (0, 3600),
    "WAIT_RESEND_VIDEO": (0, 3600),
    "CLEAR_DATA_WAIT": (0, 3600),
    "SUB_FONT_SIZE": (1, 400),
    "SUB_MARGIN_V": (0, 2000),
    "SUB_OUTLINE_WIDTH": (0, 50),
    "SUB_SHADOW_VAL": (0, 50),
    "LOGO_W": (1, 7680),
    "LOGO_H": (1, 7680),
    "LOGO_X_LAND": (0, 7680),
    "LOGO_Y_LAND": (0, 7680),
    "LOGO_X_PORT": (0, 7680),
    "LOGO_Y_PORT": (0, 7680),
    "IDEA_SCENE_COUNT": (1, 100),
    "GROK_MULTI_VIDEO": (1, 20),
    "GROK_VIDEO_LENGTH_SECONDS": (1, 60),
}
_BOOL_KEYS: frozenset[str] = frozenset(
    {
        "AUTO_UPSCALE", "CLEAR_DATA", "CLEAR_DATA_IMAGE", "VOICE_SYNC_ENABLED",
        "WORKFLOW_IMAGE_ONLY_NAMED_REFERENCES", "SKIP_AUDIO_ERROR",
        "RUN_CAPTCHA", "FIX_403_RECAPTCHA", "IDEA_NO_DIALOGUE", "IDEA_BG_MUSIC",
        "LOCAL_STT_ENABLED", "OPENAI_STT_ENABLED",
        "OPENAI_IMAGE_ENABLED", "OPENAI_IMAGE_RELAY_ENABLED",
        "OPENAI_IMAGE_PREFER_RELAY", "OPENAI_TTS_ENABLED",
    }
)
# Strings that are pasted into paths or filtergraphs later; a multi-megabyte
# blob is never a legitimate setting. Newlines are allowed — some settings
# are genuinely multi-line — but a null byte is not, since it truncates in
# any C-level consumer downstream.
_MAX_STRING_LEN = 1024
#: A Flow project id as the UI shows it in the URL.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# PROJECTS and friends are lists; bound their serialised size too.
_MAX_CONTAINER_LEN = 8192


def is_settable(key: str) -> bool:
    return key.upper() in SETTABLE


#: Keys whose value must come from a closed set. Spelled out rather than
#: free-text because a typo'd tier would silently change which lanes the UI
#: offers and which price it quotes.
_ENUM_VALUES: dict[str, frozenset[str]] = {
    "FLOW_PAYGATE_TIER": frozenset({"", "PAYGATE_TIER_ONE", "PAYGATE_TIER_TWO"}),
    # Omni's two resolutions. A closed set because the value lands in the model
    # key: a typo would be dropped silently and the render would come back at
    # the other resolution, billed accordingly.
    "VIDEO_RESOLUTION": frozenset({"360p", "720p"}),
}


def validate_value(key: str, value: Any) -> str | None:
    """Reason the value is unacceptable for ``key``, or None if it's fine."""
    ck = key.upper()
    if ck in _ENUM_VALUES:
        allowed = _ENUM_VALUES[ck]
        if not isinstance(value, str) or value not in allowed:
            listed = sorted(v for v in allowed if v)
            # "or empty" only when empty really is allowed. It was said
            # unconditionally, so a key with no empty option told the user to
            # try a value it would also reject.
            suffix = " or empty" if "" in allowed else ""
            return f"{ck} must be one of {listed}{suffix}"
        return None
    if ck == "FLOW_PROJECT_ID":
        # Empty means "let Flow mint one". Anything else has to be a project
        # uuid, because it lands in an RPC payload.
        if not isinstance(value, str):
            return "FLOW_PROJECT_ID must be text"
        if value and not _UUID_RE.fullmatch(value.strip()):
            return "FLOW_PROJECT_ID must be a Flow project uuid (or empty)"
        return None
    if ck in _BOOL_KEYS:
        if not isinstance(value, bool):
            return f"{ck} must be true or false"
        return None
    if ck in _INT_RANGES:
        # bool is an int subclass — a stray `true` must not read as 1 here.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{ck} must be a whole number"
        lo, hi = _INT_RANGES[ck]
        if not lo <= value <= hi:
            return f"{ck} must be between {lo} and {hi}"
        return None
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LEN:
            return f"{ck} is too long (max {_MAX_STRING_LEN} characters)"
        if "\x00" in value:
            return f"{ck} contains a null byte"
        return None
    if isinstance(value, (list, dict)):
        # A container bypassed the length bound entirely, so a megabyte of
        # JSON could be parked in a setting. Bound the serialised form.
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError):
            return f"{ck} is not JSON-serialisable"
        if len(encoded) > _MAX_CONTAINER_LEN:
            return f"{ck} is too large (max {_MAX_CONTAINER_LEN} characters)"
        return None
    if value is not None and not isinstance(value, (int, float, bool)):
        return f"{ck} has an unsupported value type"
    return None


def _load_config() -> dict[str, Any]:
    """config.json as a case-insensitive dict keyed by UPPER. Duplicate
    casings collapse to one entry (last wins, but the pairs hold the same
    value). Missing/broken file → empty, so BASELINE still applies."""
    try:
        # utf-8-sig, not utf-8: the desktop tool owns this file and may
        # rewrite it with a BOM, which makes json.loads raise — and that
        # failure is swallowed below, silently reverting EVERY setting to
        # BASELINE with no visible cause.
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logger.info("no usable config.json at %s — using baseline", CONFIG_PATH)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        out[k.upper()] = v
    return out


def defaults() -> dict[str, Any]:
    """Whitelisted defaults: config.json over BASELINE.

    A config value for a closed-set key is dropped when it is not in the set.
    That file is written by another program, and it really does carry values this
    one cannot use — the packaged tool ships `VIDEO_RESOLUTION: "480p"`, which
    Flow's Omni builder does not accept. Imported unchecked, it put a
    dispatchable-looking value on screen that no dispatch could use, and the
    dropdown had no matching option to show.
    """
    cfg = _load_config()
    merged = dict(BASELINE)
    for key in SETTABLE:
        if key not in cfg:
            continue
        value = cfg[key]
        if key in _ENUM_VALUES and validate_value(key, value) is not None:
            logger.info(
                "settings: ignoring config.json %s=%r (not an accepted value)",
                key, value,
            )
            continue
        merged[key] = value
    return {k: v for k, v in merged.items() if k in SETTABLE}


def _overrides() -> dict[str, Any]:
    with get_session() as s:
        rows = s.exec(select(AppSetting)).all()
    return {r.key: r.value for r in rows if r.key in SETTABLE}


def get_all() -> dict[str, Any]:
    """Effective settings: user overrides on top of defaults."""
    merged = defaults()
    merged.update(_overrides())
    return merged


def get(key: str) -> Any:
    return get_all().get(key.upper())


def set_many(updates: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Persist whitelisted keys, all-or-nothing.

    Returns ``(updated, rejected)``. When anything is rejected — a
    credential/unknown key, or a value the wrong shape — NOTHING is written:
    the caller gets a 400, and a partial write behind that 400 would leave
    the client's state disagreeing with the server's.
    """
    from datetime import datetime, timezone

    updated: list[str] = []
    rejected: list[str] = []
    to_write: dict[str, Any] = {}
    for k, v in updates.items():
        ck = k.upper()
        if ck not in SETTABLE:
            rejected.append(k)
            continue
        problem = validate_value(ck, v)
        if problem:
            rejected.append(f"{k} ({problem})")
            continue
        to_write[ck] = v

    if rejected:
        return [], rejected

    if to_write:
        with get_session() as s:
            for ck, v in to_write.items():
                row = s.get(AppSetting, ck)
                if row is None:
                    row = AppSetting(key=ck, value=v)
                else:
                    row.value = v
                    row.updated_at = datetime.now(timezone.utc)
                s.add(row)
                updated.append(ck)
            s.commit()
    return updated, rejected


def reset(keys: list[str] | None = None) -> int:
    """Delete overrides so the values fall back to config.json/BASELINE.
    ``None`` resets every override. Returns the number removed."""
    with get_session() as s:
        rows = s.exec(select(AppSetting)).all()
        removed = 0
        wanted = {k.upper() for k in keys} if keys is not None else None
        for r in rows:
            if wanted is None or r.key in wanted:
                s.delete(r)
                removed += 1
        s.commit()
    return removed
