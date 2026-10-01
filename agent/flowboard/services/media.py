"""Media cache + fetch service.

Run 6 wires Google Flow's GCS signed URLs (received through the extension's
TRPC fetch monkey-patch) into a local on-disk cache so the frontend can
render real images via `<img src="/media/:id">`.

GCS signed URLs are self-contained (signature + expiry in the query string)
— we don't need to proxy through the extension; a plain httpx GET works.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlmodel import select

from flowboard.config import STORAGE_DIR
from flowboard.db import get_session
from flowboard.db.models import Asset

logger = logging.getLogger(__name__)

MEDIA_CACHE_DIR = STORAGE_DIR / "media"
MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Media id is a hex-with-dashes UUID in the GCS path.
_MEDIA_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")

# Allowed URL prefixes. Google Flow serves user-generated media from its
# `flow-content.google` CDN (signed with short-TTL query params). The response
# from `batchGenerateImages` includes the signed URL at
# `data.media[].image.generatedImage.fifeUrl`.
_ALLOWED_URL_PREFIXES: tuple[str, ...] = (
    "https://flow-content.google/",
)


def _url_allowed(url: str) -> bool:
    return isinstance(url, str) and any(url.startswith(p) for p in _ALLOWED_URL_PREFIXES)

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def is_valid_media_id(media_id: str) -> bool:
    return bool(_MEDIA_ID_RE.fullmatch(media_id or ""))


def normalize_media_id(raw: str) -> str:
    """Accept either ``media/<uuid>`` or the bare uuid."""
    if raw.startswith("media/"):
        return raw.split("/", 1)[1]
    return raw


def _cache_glob(media_id: str) -> Optional[Path]:
    """Return the cached file for this media_id if one exists (any extension)."""
    if not is_valid_media_id(media_id):
        return None
    for p in MEDIA_CACHE_DIR.glob(f"{media_id}.*"):
        if p.is_file():
            return p
    return None


def cached_path(media_id: str) -> Optional[Path]:
    return _cache_glob(media_id)


def ingest_local_file(path: str | Path, *, kind: str) -> str:
    """Adopt a file local post-production just produced into the media cache.

    ffmpeg/RealESRGAN/TTS write into their own output folders, which the
    media pipeline knows nothing about — the canvas only chains work by
    media id, the same way it does for Flow-generated clips. This mints a
    fresh id, copies the bytes under it (never moves: the source may still
    be a render the user wants to keep browsing in its own folder), and
    upserts an Asset row with no ``url`` — this file never touched Flow, so
    there is no signed link to ever refresh.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"cannot ingest missing file: {src}")
    media_id = str(uuid.uuid4())
    # uuid4 always matches _MEDIA_ID_RE and can never collide with an
    # existing row in practice, but the whole point of a media id is that
    # cached_path can find it afterwards — that invariant is worth a
    # cheap runtime check rather than an assumption.
    if not is_valid_media_id(media_id):  # pragma: no cover
        raise RuntimeError(f"generated media id failed validation: {media_id}")
    dest = MEDIA_CACHE_DIR / f"{media_id}{src.suffix}"
    shutil.copyfile(src, dest)
    with get_session() as s:
        row = s.exec(select(Asset).where(Asset.uuid_media_id == media_id)).first()
        if row is None:
            row = Asset(uuid_media_id=media_id, kind=kind, local_path=str(dest))
        else:  # pragma: no cover — a fresh uuid4 colliding is not realistic
            row.kind = kind
            row.local_path = str(dest)
        s.add(row)
        s.commit()
    return media_id


def ingest_urls(urls: list[dict[str, Any]]) -> int:
    """Upsert Asset rows keyed by uuid_media_id. Returns count touched."""
    touched = 0
    with get_session() as s:
        for entry in urls or []:
            if not isinstance(entry, dict):
                continue
            media_id = entry.get("media_id") or entry.get("mediaId")
            url = entry.get("url")
            kind = entry.get("mediaType") or entry.get("kind") or "image"
            if not isinstance(media_id, str) or not is_valid_media_id(media_id):
                continue
            if not _url_allowed(url):
                logger.warning(
                    "media: skip non-allowed url for %s: %r", media_id, (url or "")[:80]
                )
                continue
            row = s.exec(
                select(Asset).where(Asset.uuid_media_id == media_id)
            ).first()
            if row is None:
                row = Asset(uuid_media_id=media_id, url=url, kind=kind)
            else:
                row.url = url
                if not row.kind:
                    row.kind = kind
            s.add(row)
            touched += 1
        s.commit()
    if touched:
        logger.info("media: ingested %d url(s)", touched)
    return touched


async def fetch_and_cache(media_id: str) -> Optional[tuple[bytes, str, Path]]:
    """If the Asset has a URL and no cached file, fetch bytes, cache, return.

    Returns ``(bytes, mime, path)`` on success, ``None`` if there is no URL,
    the URL is no longer valid, or the fetch fails.
    """
    if not is_valid_media_id(media_id):
        return None

    with get_session() as s:
        row = s.exec(
            select(Asset).where(Asset.uuid_media_id == media_id)
        ).first()
        if row is None or not row.url:
            return None
        url = row.url

    if not _url_allowed(url):
        logger.warning("refusing to fetch non-allowed URL: %s", url[:60])
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
    except Exception as exc:
        logger.warning("GCS fetch failed for %s: %s", media_id, exc)
        return None

    if resp.status_code != 200:
        logger.warning("GCS fetch %s returned %d", media_id, resp.status_code)
        return None

    mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    # Whitelist served mime — block any injected content-type that isn't image/video
    # from being cached and served back to the browser.
    if not (mime.startswith("image/") or mime.startswith("video/")):
        logger.warning("refusing non-media mime from GCS: %s (media_id=%s)", mime, media_id)
        return None
    ext = _EXT_BY_MIME.get(mime, ".bin")
    path = MEDIA_CACHE_DIR / f"{media_id}{ext}"
    try:
        path.write_bytes(resp.content)
    except OSError as exc:
        logger.error("failed to write cache %s: %s", path, exc)
        return None

    with get_session() as s:
        row = s.exec(
            select(Asset).where(Asset.uuid_media_id == media_id)
        ).first()
        if row is not None:
            row.local_path = str(path)
            row.mime = mime
            s.add(row)
            s.commit()

    _export_to_output_dir(media_id, resp.content, mime)
    return resp.content, mime, path


def _export_to_output_dir(media_id: str, data: bytes, mime: str) -> None:
    """Also drop the file into the user's own output folder.

    The packaged tool writes finished media to
    ``<VIDEO_OUTPUT_DIR>/<project>/{video,image}``, and people expect to find
    their clips there rather than only inside the app. The cache copy is what
    the UI plays; this one is for the filesystem.

    Never raises: a full disk or a bad path must not turn a finished,
    already-paid generation into a failure. It logs and moves on.
    """
    try:
        base = output_dir()
        if base is None:
            return
        out_dir = base / ("video" if mime.startswith("video/") else "image")
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{media_id}{_EXT_BY_MIME.get(mime, '.bin')}"
        if target.exists():
            return
        # Write under a temp name first: a crash mid-write must not leave a
        # half file that looks like a finished render.
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        logger.info("media %s exported to %s", media_id[:8], target)
    except Exception as exc:
        logger.warning("could not export %s to the output folder: %s", media_id[:8], exc)


def output_dir() -> Optional[Path]:
    """Where finished media is written outside the app, or None.

    The same resolution `_export_to_output_dir` uses, factored out so the
    "open results folder" action opens exactly the folder the exporter
    writes to. Deriving it a second time is how the button ends up opening
    somewhere plausible and empty.
    """
    from flowboard.services import settings_store

    settings = settings_store.get_all()
    root = str(settings.get("VIDEO_OUTPUT_DIR") or "").strip()
    if not root:
        return None
    project = Path(str(settings.get("CURRENT_PROJECT") or "")).name or "default_project"
    return Path(root) / project


def status(media_id: str) -> dict:
    """Read-only status for polling from the frontend."""
    if not is_valid_media_id(media_id):
        return {"available": False, "has_url": False, "reason": "invalid_id"}
    cached = _cache_glob(media_id)
    if cached is not None:
        mime = _mime_from_ext(cached.suffix)
        return {"available": True, "has_url": True, "mime": mime}
    with get_session() as s:
        row = s.exec(
            select(Asset).where(Asset.uuid_media_id == media_id)
        ).first()
        if row is None:
            return {"available": False, "has_url": False, "reason": "unknown_media"}
        if row.url:
            return {"available": False, "has_url": True, "reason": "not_cached_yet"}
        return {"available": False, "has_url": False, "reason": "no_url_yet"}


def _mime_from_ext(ext: str) -> str:
    for mime, e in _EXT_BY_MIME.items():
        if e == ext:
            return mime
    return "application/octet-stream"
