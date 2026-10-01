"""The nine placeholders the packaged tool fills and this build was stripping.

``data_general/system_prompts/*.txt`` ship with `__NAME__` slots. `analysis_modes`
fills the ordinary ones (language, scene count, style, voice) and **strips**
everything else, on the sound grounds that a literal `__VOICE_RULE__` left in a
prompt is a token the model tries to obey. Stripping is safe; it is not free:

* `__DIALOGUE_RULE__` is 1.1 KB of lip-sync rules — the ones that keep a
  sentence from being cut in half across two scenes;
* `__OUTPUT_FORMAT_RULE__` is 2.4 KB that names five JSON fields this build
  never receives, `title` and `thumbnail_prompt` among them;
* `__VOICE_RULE__` is one of three blocks, and which one decides whether the
  characters speak at all;
* the five `*_EXAMPLE` slots are the few-shot samples the file refers to by
  name — "CORRECT `image_prompt` value: __IMAGE_EXAMPLE__". Stripped, the rule
  points at an empty string.

**The text lives in the executable, not on disk**, so it is mined at call time
the same way every other packaged asset is read at call time — rather than
copied into this repo, which is the rule the skill library already follows. One
mmap scan of a 353 MB file takes ~0.5 s and the result is cached for the
process, keyed by path + mtime + size.

Nothing is invented. A slot whose text cannot be found is left to the existing
strip, and its name is logged — the packaged tool's own wording or nothing.
"""
from __future__ import annotations

import logging
import mmap
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: Anchor → the slot it fills. The anchor is the first line of the block as the
#: executable stores it, so a match is the block itself rather than a mention
#: of it somewhere else.
_ANCHORS: dict[str, bytes] = {
    "DIALOGUE_RULE": b"DIALOGUE INTEGRITY & LIPSYNC RULES",
    "OUTPUT_FORMAT_RULE": b"OUTPUT FORMAT (MANDATORY JSON STRUCTURE)",
    "VOICE_RULE_DIALOGUE": b"VOICE DIRECTION (MANDATORY)",
    "VOICE_RULE_NARRATION": b"NARRATION MODE - CRITICAL OVERRIDE",
    "VOICE_RULE_SILENT": b"STRICT NO DIALOGUE RULE",
    # The few-shot pairs, one per language. The Vietnamese and English versions
    # are the same scene written twice — Lan, Minh, the kitchen — which is how
    # they are told apart below.
    "IMAGE_EXAMPLE_VI": "Người phụ nữ trung niên tên Lan".encode(),
    "VIDEO_EXAMPLE_VI": "Lan quay đầu về phía Minh".encode(),
    "IMAGE_EXAMPLE_EN": b"A middle-aged woman named Lan",
    "VIDEO_EXAMPLE_EN": b"Lan turns toward Minh",
    "VOICE_EXAMPLE_EN": b"I will come back soon.",
}

#: A constant longer than this is not one of these blocks; the biggest is the
#: 2.4 KB output-format rule. The cap keeps a bad anchor from returning a
#: megabyte of the binary.
_MAX_BLOCK = 8192

_CACHE: dict[tuple, dict[str, str]] = {}


def _exe_path() -> Optional[Path]:
    from flowboard.services import assets

    candidate = assets.ASSET_ROOT / "RUN_VEO_3_ULTRA_PROMAX.exe"
    return candidate if candidate.is_file() else None


def blocks() -> dict[str, str]:
    """Every block this module can find, keyed as in ``_ANCHORS``.

    Empty when the executable is not on this machine — which is the ordinary
    case on a machine that only has the extracted assets, and the reason
    nothing here may be load-bearing.
    """
    path = _exe_path()
    if path is None:
        return {}
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    found: dict[str, str] = {}
    try:
        with open(path, "rb") as handle:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                for name, anchor in _ANCHORS.items():
                    text = _constant_containing(data, anchor)
                    if text:
                        found[name] = text
    except (OSError, ValueError) as exc:  # pragma: no cover — depends on the host
        logger.info("system_prompt_slots: cannot read %s (%s)", path, exc)
        return {}

    missing = sorted(set(_ANCHORS) - set(found))
    if missing:
        logger.info("system_prompt_slots: not found in the binary: %s", ", ".join(missing))
    _CACHE.clear()
    _CACHE[key] = found
    return found


def _constant_containing(data: mmap.mmap, anchor: bytes) -> Optional[str]:
    """The NUL-delimited constant that holds ``anchor``, as text.

    Marshalled constants are NUL-separated in the string pool, so the bytes
    between the surrounding NULs are the whole block. The leading bytes carry
    marshal's own type + length markers, which is why the text is cut at the
    anchor's own line rather than at the constant's first byte.
    """
    # Every occurrence, then the SMALLEST enclosing constant. An anchor can sit
    # inside a longer block as well as on its own — "I will come back soon." is
    # a line of the video example AND the voice example — and the first match is
    # the long one, which hands the caller a sample of the wrong kind.
    raw: Optional[bytes] = None
    index = data.find(anchor)
    while index >= 0:
        start = data.rfind(b"\x00", max(0, index - _MAX_BLOCK), index) + 1
        end = data.find(b"\x00", index, index + _MAX_BLOCK)
        if end >= 0:
            candidate = data[start:end]
            if raw is None or len(candidate) < len(raw):
                raw = candidate
        index = data.find(anchor, index + 1)
    if raw is None:
        return None
    at = raw.find(anchor)
    # Keep whatever precedes the anchor on its own line (some blocks open with
    # a newline or an emoji), and drop marshal's markers before it.
    head = raw[:at]
    line_start = head.rfind(b"\n") + 1
    if line_start <= 0:
        line_start = at
    text = raw[line_start:].decode("utf-8", "replace").strip("\x00")
    return text.strip() or None


def voice_rule(dialogue_mode: str) -> Optional[str]:
    """The block that decides whether the characters speak.

    The executable selects on `dialogue_mode` ∈ {`narration`, `no_dialogue`,
    anything else}; the mapping from a workflow's voice LABEL to that value is
    this build's own inference and lives in `analysis_modes`.
    """
    key = {
        "narration": "VOICE_RULE_NARRATION",
        "no_dialogue": "VOICE_RULE_SILENT",
    }.get(dialogue_mode, "VOICE_RULE_DIALOGUE")
    return blocks().get(key)


def examples(language: str) -> dict[str, str]:
    """The few-shot samples for the language this analysis answers in.

    The packaged tool keeps a Vietnamese pair and an English pair of the same
    scene. Anything that is not Vietnamese takes the English pair, because an
    example in the wrong language teaches the model the wrong language.
    """
    got = blocks()
    vietnamese = "viet" in (language or "").strip().lower()
    image = got.get("IMAGE_EXAMPLE_VI" if vietnamese else "IMAGE_EXAMPLE_EN")
    video = got.get("VIDEO_EXAMPLE_VI" if vietnamese else "VIDEO_EXAMPLE_EN")
    voice = got.get("VOICE_EXAMPLE_EN")

    out: dict[str, str] = {}
    if image:
        out["IMAGE_EXAMPLE"] = image
        # The OUTPUT skeleton shows the same value inside the JSON example, so
        # the two slots take the same text rather than a second invented one.
        out["OUTPUT_IMAGE_EXAMPLE"] = image
    if video:
        out["VIDEO_EXAMPLE"] = video
        out["OUTPUT_VIDEO_EXAMPLE"] = video
    if voice:
        out["VOICE_EXAMPLE"] = voice
    return out


def rules() -> dict[str, str]:
    """The two standard-mode rule blocks, by slot name."""
    got = blocks()
    out: dict[str, str] = {}
    if "DIALOGUE_RULE" in got:
        out["DIALOGUE_RULE"] = got["DIALOGUE_RULE"]
    if "OUTPUT_FORMAT_RULE" in got:
        out["OUTPUT_FORMAT_RULE"] = got["OUTPUT_FORMAT_RULE"]
    return out
