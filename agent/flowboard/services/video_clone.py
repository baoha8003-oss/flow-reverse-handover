"""Fetch a video from a URL so it can be worked on locally.

The packaged tool does this with a pile of site-specific scrapers. yt-dlp is
the same job done by a library that thousands of people keep current, which
matters here more than anywhere else in this codebase: sites change their
players constantly and a hand-rolled extractor is broken by next month.

Downloads land in the renders folder rather than the media cache. Cache files
are keyed by Flow media id and served through an Asset row; a file dropped in
there would be listed but not playable. Renders are already served over HTTP
and already accepted as post-production input, so a cloned clip can go
straight into Cut & Merge or Upscale.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# A single clip for local editing. Past this it is a download manager, and the
# request blocks a worker thread for the whole transfer.
MAX_BYTES = 500 * 1024 * 1024
# yt-dlp's own progress hook is the only place the running total is known
# before the file is complete, which is where the ceiling has to be enforced.
_ABORT_MESSAGE = "download exceeded the size limit"


class VideoCloneError(RuntimeError):
    """Fetching failed. The message is safe to show a user."""


def available() -> bool:
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


def _is_public_host(host: str) -> bool:
    """Reject loopback / private / link-local targets.

    This endpoint takes a URL and fetches it from inside the user's network,
    which is the shape of an SSRF primitive. Mirrors the check the image
    upload route already applies.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False
    return True


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise VideoCloneError("A link is required.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise VideoCloneError("The link must start with http:// or https://")
    if not parsed.hostname:
        raise VideoCloneError("That link has no host.")
    if not _is_public_host(parsed.hostname):
        raise VideoCloneError("That host is not reachable from the public internet.")
    return url


def download(url: str, dest_dir: Path, *, max_height: int = 1080) -> Path:
    """Download the video at ``url`` into ``dest_dir`` and return its path.

    Blocking — callers run it in a worker thread. ``max_height`` caps the
    format so a 4K source doesn't arrive when the point is to edit it locally.
    """
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover — guarded by available()
        raise VideoCloneError(
            "yt-dlp is not installed; run `pip install yt-dlp` in the agent env."
        ) from exc

    url = validate_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)

    def _progress(status: dict[str, Any]) -> None:
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        got = status.get("downloaded_bytes") or 0
        if max(total, got) > MAX_BYTES:
            raise VideoCloneError(_ABORT_MESSAGE)

    options: dict[str, Any] = {
        # Cap the resolution and prefer mp4 so the result drops straight into
        # the ffmpeg pipeline without a transcode.
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "merge_output_format": "mp4",
        # Restrict filenames: the title becomes a path, and a title with a
        # slash or a colon in it is a broken (or escaping) path on Windows.
        "restrictfilenames": True,
        "outtmpl": str(dest_dir / "clone-%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_progress],
        "retries": 2,
        "socket_timeout": 30,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except VideoCloneError:
        raise
    except Exception as exc:
        raise VideoCloneError(_clean_error(exc)) from None

    # merge_output_format rewrites the extension after the fact, so the name
    # yt-dlp predicted is not always the name on disk.
    if not path.is_file():
        candidates = sorted(dest_dir.glob(f"{path.stem}.*"))
        if not candidates:
            raise VideoCloneError("The download finished but produced no file.")
        path = candidates[0]

    if path.stat().st_size > MAX_BYTES:
        path.unlink(missing_ok=True)
        raise VideoCloneError(_ABORT_MESSAGE)
    logger.info("cloned %s -> %s", urlparse(url).hostname, path.name)
    return path


def _clean_error(exc: Exception) -> str:
    """yt-dlp errors are long and carry ANSI codes; keep the useful line."""
    text = str(exc).replace("\x1b[0;31m", "").replace("\x1b[0m", "")
    text = text.replace("ERROR: ", "").strip()
    first = text.splitlines()[0] if text else "The download failed."
    return first[:300]


def probe(url: str) -> dict[str, Optional[str]]:
    """Title / uploader / duration without downloading anything."""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise VideoCloneError("yt-dlp is not installed.") from exc
    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise VideoCloneError(_clean_error(exc)) from None
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "durationSeconds": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
    }
