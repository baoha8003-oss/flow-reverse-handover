"""Locates the local media toolchain and asset library.

Flowboard upstream delegates post-production to Google Flow. This build runs
it locally instead, reusing the binaries and asset library that already ship
with the packaged VEO3 tool in ``d:\\TOOL_VIDEO\\TOOL``: ffmpeg/ffprobe, the
RealESRGAN upscaler, the CapCut font set, the Gemini TTS voice samples, and
the background-music library.

Nothing here is downloaded at runtime — every path resolves against a local
directory so post-production keeps working with no network at all. Point
``FLOWBOARD_ASSET_ROOT`` at a different directory to relocate the library;
each lookup falls back to the system ``PATH`` so a machine without the
packaged tool still works if ffmpeg is installed normally.
"""
from __future__ import annotations

import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# The packaged VEO3 tool directory. Everything below is optional: a missing
# asset degrades one feature, it never breaks import.
ASSET_ROOT = Path(os.getenv("FLOWBOARD_ASSET_ROOT", r"D:\TOOL_VIDEO\TOOL"))

FONT_DIR = ASSET_ROOT / "CapCut_Fonts"
VOICE_SAMPLE_DIR = ASSET_ROOT / "voice"
BGM_DIR = ASSET_ROOT / "nhac_nen"
UPSCALE_DIR = ASSET_ROOT / "UpscaleEngine"
DATA_DIR = ASSET_ROOT / "data_general"

# Subtitle rendering needs a font that covers Vietnamese diacritics. BeVietnamPro
# is built for it; the rest of the CapCut set is display-only.
DEFAULT_SUBTITLE_FONT = "BeVietnamPro-Bold.ttf"

_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _library_file(base: Path, name: str) -> Optional[Path]:
    """Resolve ``name`` inside ``base``, refusing anything that escapes it.

    ``base / name`` is NOT safe on its own: pathlib drops the base entirely
    when the operand is absolute, and ``..`` segments are only collapsed by
    ``resolve()``. Both made a library lookup accept any file on the
    machine — and since a library hit skips the route's own containment
    check, that turned "pick a background track" into "mux any file on disk
    into a downloadable render".

    Compared through ``normcase`` so a Windows drive-letter/case difference
    doesn't falsely reject a legitimate file; the trailing separator stops
    ``/rootother`` from matching ``/root``.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        root = base.resolve()
        candidate = (base / name.strip()).resolve()
    except OSError:
        return None
    c, r = os.path.normcase(str(candidate)), os.path.normcase(str(root))
    if not c.startswith(r + os.sep):
        return None
    return candidate if candidate.is_file() else None


def _resolve_binary(name: str, *candidates: Path) -> Optional[str]:
    """First existing candidate, else the one on PATH, else None."""
    for path in candidates:
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if found:
        return found
    logger.warning("binary %r not found in asset root or PATH", name)
    return None


@lru_cache(maxsize=1)
def ffmpeg_bin() -> Optional[str]:
    return _resolve_binary("ffmpeg", ASSET_ROOT / "ffmpeg.exe", ASSET_ROOT / "ffmpeg")


@lru_cache(maxsize=1)
def ffprobe_bin() -> Optional[str]:
    return _resolve_binary("ffprobe", ASSET_ROOT / "ffprobe.exe", ASSET_ROOT / "ffprobe")


@lru_cache(maxsize=1)
def realesrgan_bin() -> Optional[str]:
    return _resolve_binary(
        "realesrgan-ncnn-vulkan",
        UPSCALE_DIR / "realesrgan-ncnn-vulkan.exe",
        UPSCALE_DIR / "realesrgan-ncnn-vulkan",
    )


def upscayl_bin() -> Optional[str]:
    """The Upscayl fork of the same ncnn engine, shipped beside it.

    Not on PATH as a fallback the way `_resolve_binary` allows for ffmpeg:
    this is a bundled tool, and a stray `upscayl-bin` elsewhere on the
    machine is a different build with different models.
    """
    for candidate in (UPSCALE_DIR / "upscayl-bin.exe", UPSCALE_DIR / "upscayl-bin"):
        if candidate.is_file():
            return str(candidate)
    return None


def upscale_model_dir() -> Optional[Path]:
    models = UPSCALE_DIR / "models"
    return models if models.is_dir() else None


def font_path(name: str = DEFAULT_SUBTITLE_FONT) -> Optional[Path]:
    """Resolve a font by file name, or by family stem (``"Anton"``).

    Only files inside FONT_DIR resolve — see ``_library_file``.
    """
    exact = _library_file(FONT_DIR, name)
    if exact is not None:
        return exact
    if not FONT_DIR.is_dir():
        return None
    stem = name.rsplit(".", 1)[0].lower()
    for candidate in sorted(FONT_DIR.iterdir()):
        if candidate.suffix.lower() not in {".ttf", ".otf"}:
            continue
        if candidate.stem.lower().split("-")[0] == stem or candidate.stem.lower() == stem:
            return candidate
    return None


def list_fonts() -> list[str]:
    if not FONT_DIR.is_dir():
        return []
    return sorted(
        p.name for p in FONT_DIR.iterdir() if p.suffix.lower() in {".ttf", ".otf"}
    )


def list_bgm() -> list[Path]:
    if not BGM_DIR.is_dir():
        return []
    return sorted(p for p in BGM_DIR.iterdir() if p.suffix.lower() in _AUDIO_SUFFIXES)


def bgm_path(name: str) -> Optional[Path]:
    """Resolve a background track by exact file name or case-insensitive stem.

    Library lookups are confined to BGM_DIR: the caller treats a hit as
    pre-approved and skips its own path check, so an escape here is an
    arbitrary-file-read. See ``_library_file``.
    """
    direct = _library_file(BGM_DIR, name)
    if direct is not None:
        return direct
    stem = name.rsplit(".", 1)[0].lower()
    for track in list_bgm():
        if track.stem.lower() == stem:
            return track
    return None


def list_voice_samples() -> list[str]:
    """Gemini TTS voice names that ship with a local preview sample."""
    if not VOICE_SAMPLE_DIR.is_dir():
        return []
    return sorted(p.stem for p in VOICE_SAMPLE_DIR.glob("*.wav"))


def voice_sample_path(voice: str) -> Optional[Path]:
    return _library_file(VOICE_SAMPLE_DIR, f"{voice}.wav")


def status() -> dict[str, object]:
    """Availability snapshot for the setup screen and ``/api/health``."""
    return {
        "assetRoot": str(ASSET_ROOT),
        "assetRootExists": ASSET_ROOT.is_dir(),
        "ffmpeg": ffmpeg_bin(),
        "ffprobe": ffprobe_bin(),
        "realesrgan": realesrgan_bin(),
        "fontCount": len(list_fonts()),
        "bgmCount": len(list_bgm()),
        "voiceSampleCount": len(list_voice_samples()),
    }
