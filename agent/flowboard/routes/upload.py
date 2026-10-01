"""User image upload to Google Flow.

Multipart upload that base64-encodes the bytes, hands them to
``FlowSDK.upload_image`` (which goes through the extension to
``/v1/flow/uploadImage``), and on success caches the bytes locally keyed by
the Flow-issued media_id.

Design choices:
- Run synchronously rather than through the worker queue. Upload is one
  round-trip and the caller (character node UI) needs the media_id immediately.
- Project-scoped: Flow's uploadImage requires ``clientContext.projectId``.
  Frontend must call ``ensureBoardProject`` first and pass the ``project_id``.
- 10 MB cap and ``image/*`` mime allowlist applied here as defence-in-depth;
  the route never trusts the browser-supplied content-type alone.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from flowboard.services import image_ingest
from flowboard.services.flow_sdk import is_valid_project_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["upload"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Owned by `services.image_ingest` now that the browser is not the only
# source of image bytes. Re-exported under the old names because they are
# part of this module's surface and callers should not have to care where
# the implementation moved to.
ALLOWED_UPLOAD_MIMES = image_ingest.ALLOWED_MIMES
_EXT_BY_MIME = image_ingest.EXT_BY_MIME
_sniff_image_mime = image_ingest.sniff_mime
_classify_aspect = image_ingest.classify_aspect
_sniff_image_dimensions = image_ingest.sniff_dimensions


def _is_public_host(host: str) -> bool:
    """Reject SSRF targets — link uploads must point to a public host, never
    loopback / private / link-local. The agent runs on the user's box; without
    this, a malicious link could pivot to the local network."""
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
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def _ingest_image_bytes(
    raw: bytes,
    mime: str,
    project_id: str,
    file_name: str,
    node_id: Optional[int],
) -> dict:
    """HTTP adapter over `image_ingest.ingest_bytes`.

    The ingest itself is transport-neutral so the worker can call it too;
    this only translates its failure into the response shape the frontend
    has always received.
    """
    try:
        return await image_ingest.ingest_bytes(
            raw, mime, project_id, file_name, node_id
        )
    except image_ingest.IngestError as exc:
        detail: object = (
            {"message": exc.message, "raw": exc.raw}
            if exc.raw is not None
            else exc.message
        )
        raise HTTPException(status_code=exc.status, detail=detail) from exc


@router.post("/upload")
async def upload_image(
    project_id: str = Form(...),
    node_id: Optional[int] = Form(default=None),
    file: UploadFile = File(...),
):
    if not is_valid_project_id(project_id):
        raise HTTPException(status_code=400, detail="invalid project_id")

    mime = (file.content_type or "").lower().split(";")[0].strip()
    if mime not in ALLOWED_UPLOAD_MIMES:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported mime: {mime!r}; allowed: {sorted(ALLOWED_UPLOAD_MIMES)}",
        )

    # Read with a hard cap so a hostile client can't OOM us by streaming
    # forever. Read MAX+1 bytes; if we got more than MAX, reject.
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    size = len(raw)
    if size == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {size} > {MAX_UPLOAD_BYTES}",
        )

    file_name = file.filename or f"upload{_EXT_BY_MIME.get(mime, '')}"
    out = await _ingest_image_bytes(raw, mime, project_id, file_name, node_id)
    logger.info("upload: media_id=%s size=%d mime=%s", out["media_id"], size, mime)
    return out


class UrlUploadBody(BaseModel):
    url: str
    project_id: str
    node_id: Optional[int] = None


@router.post("/upload-url")
async def upload_image_from_url(body: UrlUploadBody):
    """Fetch an image at ``body.url`` server-side, validate, then push it
    through the same Flow upload pipeline as ``/upload``. CORS-free
    alternative to having the browser fetch the URL itself."""
    if not is_valid_project_id(body.project_id):
        raise HTTPException(status_code=400, detail="invalid project_id")

    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="url missing host")
    if not _is_public_host(parsed.hostname or ""):
        raise HTTPException(status_code=400, detail="url host not public")

    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": "Flowboard/0.1"}
        ) as client:
            resp = await client.get(body.url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"fetch returned status {resp.status_code}"
        )

    raw = resp.content
    size = len(raw)
    if size == 0:
        raise HTTPException(status_code=502, detail="empty response body")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"file too large: {size} > {MAX_UPLOAD_BYTES}"
        )

    mime = (resp.headers.get("content-type", "") or "").lower().split(";")[0].strip()
    # Server's Content-Type is the hint; magic-byte sniff is the source of
    # truth (a misconfigured server, or one returning text/html for an HTTP
    # error masquerading as 200, must not slip through).
    sniffed = _sniff_image_mime(raw)
    if mime not in ALLOWED_UPLOAD_MIMES:
        if sniffed is None:
            raise HTTPException(
                status_code=415,
                detail=f"not an image (content-type {mime!r}, no magic bytes match)",
            )
        mime = sniffed
    elif sniffed is not None and sniffed != mime:
        # Trust the magic bytes if they disagree.
        mime = sniffed

    # Derive a filename from the URL path or fall back to a generic one.
    path_name = (parsed.path.rstrip("/").rsplit("/", 1)[-1] or "image").lower()
    if "." not in path_name:
        path_name = path_name + _EXT_BY_MIME.get(mime, "")

    out = await _ingest_image_bytes(raw, mime, body.project_id, path_name, body.node_id)
    logger.info(
        "upload-url: media_id=%s size=%d mime=%s host=%s",
        out["media_id"], size, mime, parsed.netloc,
    )
    return out
