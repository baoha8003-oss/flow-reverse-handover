"""Visual style and narrator-persona libraries.

Upstream Flowboard hard-codes one look — fashion / e-commerce editorial — into
its prompt synthesizer. That is too narrow here: the same board needs to
produce stick-figure shorts, Pixar-style animation, Ghibli anime, noir, or
documentary footage.

The packaged VEO3 tool already ships curated libraries for exactly that:
``video_styles.json`` (19 visual styles, each a paragraph of art direction)
and ``voice_styles.json`` (16 Vietnamese narrator personas). Both are read
from disk here so the synthesizer can append a style directive instead of
being rewritten per genre, and so the UI can offer the same named presets the
user already knows.

Missing or malformed files degrade to an empty library — a style is an
enhancement to a prompt, never a precondition for generating one.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

from flowboard.services import assets

logger = logging.getLogger(__name__)

VIDEO_STYLES_FILE = "video_styles.json"
VOICE_STYLES_FILE = "voice_styles.json"

# A style paragraph runs long; truncating keeps the synthesizer's system
# prompt inside a sane token budget without losing the look's essentials,
# which are always stated first.
MAX_STYLE_CHARS = 1200


def _load_json_list(path: Path) -> list[dict]:
    if not path.is_file():
        logger.info("style library not found at %s", path)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read style library %s", path, exc_info=True)
        return []
    if not isinstance(data, list):
        logger.warning("style library %s is not a list", path)
        return []
    return [e for e in data if isinstance(e, dict)]


def _normalize(text: str) -> str:
    """Fold case and Vietnamese diacritics so "Nguoi Que" matches "Người Que"."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.replace("đ", "d").strip()


@lru_cache(maxsize=1)
def video_styles() -> list[dict[str, str]]:
    """Visual styles as ``{name, description}``, in library order."""
    out: list[dict[str, str]] = []
    for entry in _load_json_list(assets.DATA_DIR / VIDEO_STYLES_FILE):
        name = entry.get("name")
        description = entry.get("description")
        if isinstance(name, str) and isinstance(description, str) and name.strip():
            out.append({"name": name.strip(), "description": description.strip()})
    return out


@lru_cache(maxsize=1)
def voice_styles() -> list[dict[str, str]]:
    """Narrator personas as ``{title, description}``, in library order."""
    out: list[dict[str, str]] = []
    for entry in _load_json_list(assets.DATA_DIR / VOICE_STYLES_FILE):
        title = entry.get("title")
        description = entry.get("description")
        if isinstance(title, str) and isinstance(description, str) and title.strip():
            out.append({"title": title.strip(), "description": description.strip()})
    return out


def find_video_style(name: Optional[str]) -> Optional[dict[str, str]]:
    """Exact match, then diacritic-insensitive, then prefix, then substring.

    The substring pass exists because the memorable half of a name is often
    not the first half — users say "Pixar", not "Hoạt Hình 3D Pixar".
    """
    if not isinstance(name, str) or not name.strip():
        return None
    target = name.strip()
    styles = video_styles()
    for style in styles:
        if style["name"] == target:
            return style
    key = _normalize(target)
    if not key:
        return None
    for style in styles:
        if _normalize(style["name"]) == key:
            return style
    for style in styles:
        if _normalize(style["name"]).startswith(key):
            return style
    for style in styles:
        if key in _normalize(style["name"]):
            return style
    return None


def find_voice_style(title: Optional[str]) -> Optional[dict[str, str]]:
    if not isinstance(title, str) or not title.strip():
        return None
    target = title.strip()
    personas = voice_styles()
    for persona in personas:
        if persona["title"] == target:
            return persona
    key = _normalize(target)
    for persona in personas:
        if _normalize(persona["title"]) == key:
            return persona
    return None


def style_directive(name: Optional[str]) -> str:
    """Art-direction block to append to a synthesizer system prompt.

    Empty string when the style is unknown, so callers can concatenate
    unconditionally.
    """
    style = find_video_style(name)
    if style is None:
        return ""
    description = style["description"][:MAX_STYLE_CHARS]
    return (
        "\n\nVISUAL STYLE — this overrides any conflicting look described "
        f"above. Render everything in the style named '{style['name']}':\n"
        f"{description}\n"
    )
