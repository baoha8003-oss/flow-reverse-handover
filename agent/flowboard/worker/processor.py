"""In-process worker that drains queued generation requests.

Scope for Run 3 (Phase 2 bridge): a single handler type `"proxy"` that
forwards `params = {url, method?, headers?, body?}` through the extension
via ``flow_client.api_request``. Further types (gen_image, gen_video,
upload_image, etc.) land in later runs once the full Flow protocol + captcha
round-trip is ported.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import or_
from sqlmodel import select

from flowboard.config import STORAGE_DIR
from flowboard.db import get_session
from flowboard.db.models import Request
from flowboard.services import media as media_service
from flowboard.services.flow_client import flow_client
from flowboard.services.flow_sdk import (
    NO_OPERATIONS_ERROR,
    OMNI_DEFAULT_DURATION_S,
    TRANSIENT_RPC_CODE,
    UNSUPPORTED_PREFIX,
    get_flow_sdk,
)
from flowboard.worker.postprod_handler import handle_postprod

logger = logging.getLogger(__name__)

# Stop dispatching after this many cumulative 403s. A run of 403s means the
# account/token is being rejected — hammering it risks Google flagging the
# very account the whole tool depends on. Reset to 0 on any success.
MAX_CUMULATIVE_403 = 3
# Captcha challenges can be retried more times than a normal transient
# because solving one in the open Flow tab is expected to eventually work.
CAPTCHA_MAX_ATTEMPTS = 10
# `free` failures (extension down, expired token) don't burn the attempt
# budget — but they can't retry forever either. An expired token with the
# extension still connected would otherwise re-dispatch every 15s for the
# rest of the session, hammering Google with an invalid token from the one
# real account this tool depends on.
FREE_MAX_RETRIES = 20
# Once the breaker trips, let a single probe through this often. Without it
# the breaker is one-way: nothing dispatches, so nothing can succeed, so the
# reset-on-success never fires and the queue stalls until a restart.
BREAKER_PROBE_INTERVAL_S = 60.0

# Error codes are matched as standalone tokens: a bare `"403" in err` also
# matched a media id or a byte count that merely contains those digits,
# which either tripped the account breaker on an unrelated failure or
# turned a terminal error into a retry loop. `\b` is wrong here because
# underscore is a word character — it would stop matching "API_403" — so
# the boundary is "not adjacent to another alphanumeric" instead. Patterns
# run against the lowercased message.
_RE_403 = re.compile(r"(?<![0-9a-z])403(?![0-9a-z])")
_RE_401 = re.compile(r"(?<![0-9a-z])401(?![0-9a-z])")

# Errors WE produce when refusing to dispatch. They interpolate caller-supplied
# values, so they are matched by prefix and resolved before any pattern that
# looks for HTTP status codes inside the message.
_SELF_TERMINAL_PREFIXES: tuple[str, ...] = (
    "PUBLIC_ERROR_",
    "missing_",
    "invalid_",
    "unknown_request_type",
    "omni_aspect_unsupported_",
    # A capability this transport has no captured payload for. Retrying cannot
    # make one appear, and the three `no_*_model_for_tier_` prefixes this
    # replaces described a per-tier key table that no longer exists.
    UNSUPPORTED_PREFIX,
)

#: Exact errors that are terminal without being one of our own validations.
#:
#: `NO_FLOW_PROJECT` means the tab is not inside a project, and
#: `create_project_failed` means Flow refused to make one and no fallback id was
#: configured. Both need a person, so retrying twenty times only delays the
#: message that says so.
_SELF_TERMINAL_CODES: frozenset[str] = frozenset()
_TERMINAL_SUBSTRINGS: tuple[str, ...] = (
    "no_flow_project",
    "create_project_failed",
)

#: Flow's and the bridge's names for "that captcha token is no good".
#:
#: Exact codes, because they have to be read BEFORE the `PUBLIC_ERROR_` prefix
#: rule and anything looser would let a caller-supplied value reach the captcha
#: budget. Measured: a replayed token is what Flow calls unusual activity, and
#: every token is single-use.
_CAPTCHA_CODES: tuple[str, ...] = (
    "public_error_unusual_activity",
    "captcha_failed",
)

#: The page could not sign the call: no Flow tab, a discarded one, no `at`
#: token on it, or an injection that returned nothing. None of these spent
#: anything and all of them are fixed in the browser, so they retry without
#: burning the attempt budget -- exactly what the old "extension disconnected"
#: bucket was for, which these four replace now that the token is gone.
_PAGE_UNSIGNED_SUBSTRINGS: tuple[str, ...] = (
    "no_at_token",
    "no_flow_tab",
    "flow_tab_discarded",
    "no_injection_result",
)


def _tier_label(params: dict) -> Optional[str]:
    """The account tier to stamp on a dispatch, or None.

    **This is a label, not a gate.** It used to be one: `_tier_or_reason`
    refused to dispatch without a tier, because the REST payload carried
    `userPaygateTier` and sending the wrong one served an Ultra account at the
    Pro checkpoint. The migrated Flow transport carries no such field, so
    there is nothing left to get wrong — and gating on it would be worse than
    useless now that the value can only come from a dropdown: the tier can
    never resolve on its own, so every generation in the app would fail until
    someone filled that dropdown in.

    Kept on the request row because the UI and `/estimate` read it back to say
    which lane ran and what it plausibly cost.
    """
    return params.get("paygate_tier") or flow_client.paygate_tier


#: Request kinds whose failures come from somewhere other than Google Flow.
#: The retry policy is written around Flow — its 403 feeds an account-wide
#: breaker, its 401 means the extension's token needs refreshing — and none of
#: that is true of another vendor's API.
#:
#: `postprod` belongs here even though most of its ops are local ffmpeg. It is
#: also where OpenAI TTS and whisper transcription run, and `OpenAITTSError`
#: subclasses `RuntimeError`, so the handler hands their failures back as plain
#: error strings. Reading one of those "403"s as a Flow 403 put three of them
#: into `MAX_CUMULATIVE_403` and stopped every Flow dispatch in the app over a
#: different vendor's bill. Nothing under this type dispatches to Flow's
#: generation API, so nothing under it should be able to trip that breaker — and
#: for the local ops the bucket is right anyway: an ffmpeg failure is terminal,
#: and a 429 from either cloud vendor is still transient.
_NON_FLOW_REQUEST_TYPES: frozenset[str] = frozenset(
    {"gen_image_openai", "postprod"}
)


#: The most outputs one node may ask for in a single dispatch.
#:
#: A ceiling rather than a preference: `gen_image` used to accept any positive
#: `variant_count`, so a node whose settings carried 50 drew fifty images and
#: charged for all of them, while the cost dialog — which capped at 16 — had
#: quoted sixteen. One number, three readings, and the largest of them was the
#: one that spent.
MAX_VARIANTS = 16


def clamp_variant_count(raw: object) -> int:
    """How many outputs to produce: 1..MAX_VARIANTS, defaulting to one.

    `bool` is rejected explicitly because it is an `int` in Python and a `True`
    arriving here is data rot, not a request for one variant.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 1
    return max(1, min(MAX_VARIANTS, raw))


def classify_error(err: Optional[str], *, request_type: Optional[str] = None) -> str:
    """Bucket a handler error string for the retry policy.

    Returns one of: ``"403"`` (auth/permission — feeds the breaker),
    ``"captcha"``, ``"free"`` (our-side infra: extension down, token
    expired — retry without spending an attempt), ``"counted"`` (rate limit
    / 5xx / transient) or ``"terminal"`` (content filter, bad input — never
    retry). Unknown errors are terminal: retrying a real content problem
    would just re-spend credits.

    ``request_type`` matters because these buckets are about Google Flow. An
    OpenAI 403 says the OpenAI key is out of quota; read as a Flow 403 it
    counted toward the account breaker, and three of them stopped every Flow
    dispatch in the app over a problem on a different vendor's bill. An
    OpenAI 401 is a wrong key, not the extension's expired token, so the
    "free" bucket retried it twenty times against a wall. For a non-Flow
    request only the transient buckets survive; auth and permission failures
    are terminal, because nothing the worker can do will change them.
    """
    e = err or ""
    el = e.lower()
    if request_type in _NON_FLOW_REQUEST_TYPES:
        if e.startswith(_SELF_TERMINAL_PREFIXES) or e in _SELF_TERMINAL_CODES:
            return "terminal"
        if "429" in el or "timeout" in el or "timed out" in el:
            return "counted"
        return "terminal"
    # Our OWN validation errors are classified first, by prefix, and can
    # never reach the auth buckets. They embed the caller's values verbatim
    # ("invalid_duration_403", "..._aspect_403_dur_8"), the request endpoint
    # is unauthenticated, and `params` is unvalidated — so if a number in a
    # rejected request could read as an auth failure, three POSTs would trip
    # the account breaker and stop all generation. A request we refused to
    # dispatch says nothing about the account.
    # A replayed captcha token, asked FIRST and by exact name.
    #
    # Flow says `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, which carries neither the word
    # "captcha" nor a status code. Two rules fight over it: `PUBLIC_ERROR_` is a
    # self-terminal prefix, and a terminal verdict abandons a batch that only
    # needed a fresh single-use token. So the specific codes win over the
    # general prefix, and they are matched exactly rather than by substring —
    # the loose "captcha" test below cannot come first, because our own
    # validation errors embed unvalidated caller values.
    if any(code in el for code in _CAPTCHA_CODES):
        return "captcha"
    if e.startswith(_SELF_TERMINAL_PREFIXES) or e in _SELF_TERMINAL_CODES:
        return "terminal"
    if _RE_403.search(el):
        return "403"
    if any(sub in el for sub in _TERMINAL_SUBSTRINGS):
        return "terminal"
    if "captcha" in el:  # also matches "recaptcha"
        return "captcha"
    if (
        any(sub in el for sub in _PAGE_UNSIGNED_SUBSTRINGS)
        or ("extension" in el and ("connect" in el or "disconnect" in el))
        or _RE_401.search(el)
    ):
        return "free"
    if (
        e.startswith(("API_429", "API_5", "sync_failed", TRANSIENT_RPC_CODE))
        or e == NO_OPERATIONS_ERROR
    ):
        return "counted"
    return "terminal"


# type → coroutine(params) → (result_dict, error_or_None)
Handler = Callable[[dict], Awaitable[tuple[dict, Optional[str]]]]


# What used to sit here: `_handle_proxy`, plus the two URL allowlists it
# guarded. It forwarded a raw url and body to `flow_client.api_request`, and
# that transport is gone -- Flow stopped authenticating it. Nothing in the app
# or the frontend ever enqueued a `proxy` request, so this removes a generic
# "send this url through the browser" primitive rather than a feature.
#
# The cookie-channel paths it listed live on where they are used: Shopee calls
# `flow_client.trpc_request` directly, and the extension holds the allowlist
# that actually enforces them -- by path, never by bare domain.


async def _handle_create_project(params: dict) -> tuple[dict, Optional[str]]:
    name = params.get("name") or params.get("title") or "Untitled"
    if not isinstance(name, str) or not name.strip():
        return {}, "missing_name"
    tool = params.get("tool", "PINHOLE")
    resp = await get_flow_sdk().create_project(name.strip(), tool)
    if resp.get("error"):
        return resp, str(resp["error"])[:200]
    return resp, None


async def _handle_gen_image(params: dict) -> tuple[dict, Optional[str]]:
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    aspect = params.get("aspect_ratio") or "IMAGE_ASPECT_RATIO_LANDSCAPE"
    # A label for the row and the UI — see `_tier_label`. The old hard refusal
    # lived here because the REST body carried `userPaygateTier`; the migrated
    # transport carries no such field.
    tier = _tier_label(params)
    # `ref_media_ids` is the broader name (any upstream image / character /
    # visual_asset feeds in as IMAGE_INPUT_TYPE_REFERENCE). Older callers used
    # `character_media_ids` — accept both.
    raw_ref_ids = params.get("ref_media_ids")
    if not isinstance(raw_ref_ids, list):
        raw_ref_ids = params.get("character_media_ids")
    ref_media_ids: Optional[list[str]] = None
    if isinstance(raw_ref_ids, list):
        cleaned = [m for m in raw_ref_ids if isinstance(m, str) and m]
        ref_media_ids = cleaned or None
    variant_count = clamp_variant_count(params.get("variant_count"))
    # Per-variant prompts (optional). When provided, each variant gets its
    # own text — used by auto-prompt batch mode so variants don't collapse
    # to the same stance.
    raw_prompts = params.get("prompts")
    per_variant_prompts: Optional[list[str]] = None
    if isinstance(raw_prompts, list):
        cleaned = [p for p in raw_prompts if isinstance(p, str) and p.strip()]
        per_variant_prompts = cleaned or None
    image_model = params.get("image_model")
    if not isinstance(image_model, str) or not image_model.strip():
        image_model = None
    resp = await get_flow_sdk().gen_image(
        prompt=prompt.strip(),
        project_id=project_id,
        aspect_ratio=aspect,
        paygate_tier=tier,
        ref_media_ids=ref_media_ids,
        variant_count=variant_count,
        prompts=per_variant_prompts,
        image_model=image_model,
    )
    if resp.get("error"):
        return resp, str(resp["error"])[:200]
    # Flow returns signed fifeUrls directly in the response — persist them
    # immediately so `/media/:id` can serve bytes without any extra round-trip.
    entries_with_urls = [
        e for e in (resp.get("media_entries") or []) if isinstance(e, dict) and e.get("url")
    ]
    if entries_with_urls:
        try:
            media_service.ingest_urls(entries_with_urls)
        except Exception:
            logger.exception("auto-ingest from gen_image response failed")
    return resp, None


#: Where a paid-for OpenAI image waits between the charge and a successful
#: Flow upload. Keyed by request id and variant index, so a re-dispatch of the
#: same request finds the same bytes and a different request never does.
_PAID_IMAGE_DIR = STORAGE_DIR / "openai-images"


def _paid_image_slot(request_id: object, index: int) -> Optional[Path]:
    if not isinstance(request_id, int):
        return None
    return _PAID_IMAGE_DIR / f"req-{request_id}-{index}.bin"


def _keep_paid_image(request_id: object, index: int, image) -> None:
    """Park bytes that have already been charged for.

    Best-effort on purpose: a disk that refuses the write must not turn a
    successful generation into a failure. Losing the cache costs one more
    image on retry, which is exactly the behaviour it replaces.
    """
    slot = _paid_image_slot(request_id, index)
    if slot is None:
        return
    try:
        slot.parent.mkdir(parents=True, exist_ok=True)
        slot.write_bytes(image.data)
        slot.with_suffix(".mime").write_text(
            image.mime + "\n" + image.source, encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("could not park a paid OpenAI image: %s", exc)


def _paid_image_from_disk(request_id: object, index: int):
    """The bytes this request already paid for, if it has been here before."""
    slot = _paid_image_slot(request_id, index)
    if slot is None or not slot.is_file():
        return None
    try:
        data = slot.read_bytes()
        meta = slot.with_suffix(".mime").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not data:
        return None
    from flowboard.services.openai_images import GeneratedImage

    mime = meta[0].strip() if meta else "image/png"
    source = meta[1].strip() if len(meta) > 1 else "cache"
    logger.info(
        "reusing the OpenAI image request %s already paid for (variant %d)",
        request_id, index + 1,
    )
    return GeneratedImage(data=data, mime=mime, source=source)


def _drop_paid_images(request_id: object, count: int) -> None:
    """Forget the parked bytes once their media ids exist in Flow."""
    for index in range(count):
        slot = _paid_image_slot(request_id, index)
        if slot is None:
            return
        for path in (slot, slot.with_suffix(".mime")):
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover - a locked file is harmless
                pass


async def _handle_gen_image_openai(params: dict) -> tuple[dict, Optional[str]]:
    """Draw with OpenAI, then upload the result to Flow like any other image.

    Deliberately the same request kind machinery as `gen_image` rather than a
    separate pipeline: the retry budget, the activity log, the request row
    and the media cache all already exist and all still apply. The only
    difference is who drew the pixels.

    The Flow upload is not optional. A `media_id` is how every downstream
    node addresses an image — start frame, reference, character — so an
    image that never reaches Flow cannot be wired to anything and would be
    a charge with nothing to show for it.

    **Paid bytes are written to disk before that upload.** Inheriting the
    retry budget cuts both ways: a Flow upload that fails returns an error,
    the worker re-dispatches, and this drew — and paid for — a brand new
    image while the one already bought was still in memory. So each variant
    lands in ``storage/openai-images/`` under its request id first, and a
    re-dispatch finds it there and uploads that instead of buying another.
    """
    from flowboard.services import image_ingest, openai_images
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"

    aspect = params.get("aspect_ratio")
    variant_count = clamp_variant_count(params.get("variant_count"))
    node_id = params.get("node_id") if isinstance(params.get("node_id"), int) else None

    media_ids: list[str] = []
    sources: list[str] = []
    first_error: Optional[str] = None
    request_id = params.get("__request_id")
    for index in range(variant_count):
        image = _paid_image_from_disk(request_id, index)
        if image is None:
            try:
                image = await openai_images.generate(
                    prompt.strip(), aspect_ratio=aspect
                )
            except openai_images.ImageGenError as exc:
                first_error = first_error or str(exc)[:200]
                break
            # Before the upload, not after: from here on the bytes are paid
            # for, and a retry must find them rather than buy them again.
            _keep_paid_image(request_id, index, image)
        try:
            out = await image_ingest.ingest_bytes(
                image.data,
                image.mime,
                project_id,
                f"openai-{index + 1}{image_ingest.EXT_BY_MIME.get(image.mime, '.png')}",
                node_id,
            )
        except image_ingest.IngestError as exc:
            # The image was paid for and exists; say that plainly rather
            # than reporting this as a generation failure, because the two
            # have completely different fixes.
            first_error = first_error or f"đã tạo được ảnh nhưng tải lên Flow lỗi: {exc.message}"[:200]
            break
        media_id = out.get("media_id")
        if isinstance(media_id, str):
            media_ids.append(media_id)
            sources.append(image.source)

    if not media_ids:
        return {}, first_error or "openai_image_failed"
    # Every variant that reached Flow has a media id now, so the parked bytes
    # have done their job. Left behind they would be a slow disk leak of
    # multi-megabyte files nothing will ever read again.
    _drop_paid_images(request_id, len(media_ids))
    result: dict = {"media_ids": media_ids, "source": sources[0]}
    if first_error:
        # Partial success. Returning it as done with fewer variants beats
        # failing the node and throwing away images already charged for.
        result["partial_error"] = first_error
        logger.warning(
            "gen_image_openai: %d/%d variants — %s",
            len(media_ids), variant_count, first_error,
        )
    return result, None


# Video polling knobs — module-level so tests can monkeypatch them down to
# milliseconds. 7s x 86 is a ~10-minute hard deadline, matching the packaged
# tool's cadence and ceiling (config.json WAIT_GEN_VIDEO=7, plus the string
# "timeout sau 10 phut polling" lifted from the binary).
#
# These are deliberately NOT read from the WAIT_GEN_VIDEO setting: the poll
# loop reads them per cycle, so a settings lookup here would override the
# monkeypatch in every timing test and make the suite sleep for real.
#
# When the budget runs out without all ops finishing, the handler returns the
# ``timeout_waiting_video`` sentinel and the worker stamps the row as
# ``status='timeout'`` (distinct from ``failed``) so the UI can render it as a
# soft auto-cancel rather than a generation error.
VIDEO_POLL_INTERVAL_S = 7.0
VIDEO_POLL_MAX_CYCLES = 86


def _is_request_canceled(rid: Optional[int]) -> bool:
    """Return True iff the cancel endpoint flipped this row to canceled.

    Long-running handlers call this between polls so a user-initiated
    cancel takes effect mid-flight (we can't abort the Flow HTTP calls
    themselves, but we can stop polling and let _process_one keep the
    canceled status intact).
    """
    if not isinstance(rid, int):
        return False
    with get_session() as s:
        req = s.get(Request, rid)
        if req is None:
            return True
        return req.status == "canceled"


async def _handle_gen_video(params: dict) -> tuple[dict, Optional[str]]:
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    start_media_id = params.get("start_media_id") or params.get("startMediaId")
    raw_starts = params.get("start_media_ids")
    start_media_ids: Optional[list[str]] = None
    if isinstance(raw_starts, list):
        cleaned = [m for m in raw_starts if isinstance(m, str) and m.strip()]
        start_media_ids = [m.strip() for m in cleaned] or None

    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    # Either a single start_media_id OR a non-empty start_media_ids list.
    if start_media_ids is None and (
        not isinstance(start_media_id, str) or not start_media_id.strip()
    ):
        return {}, "missing_start_media_id"
    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"
    tier = _tier_label(params)
    video_quality = params.get("video_quality")
    if not isinstance(video_quality, str) or not video_quality.strip():
        video_quality = None
    end_media_id = params.get("end_media_id") or params.get("endMediaId")
    if not isinstance(end_media_id, str) or not end_media_id.strip():
        end_media_id = None
    else:
        end_media_id = end_media_id.strip()

    # Length and resolution belong to the Omni lane only -- a Veo key carries
    # its own 8 seconds. They are read here regardless so that picking the Omni
    # lane on an image-to-video node honours the duration the node shows;
    # dropping them silently rendered 8s clips for nodes set to 4 and billed
    # the difference.
    duration_s = params.get("duration_s")
    if duration_s is not None and (
        isinstance(duration_s, bool) or not isinstance(duration_s, int)
    ):
        return {}, f"invalid_duration_{duration_s!r}"[:200]
    resolution = params.get("resolution")
    if not isinstance(resolution, str) or resolution.strip().lower() not in (
        "360p", "720p"
    ):
        resolution = "720p"
    else:
        resolution = resolution.strip().lower()

    sdk = get_flow_sdk()
    dispatch = await sdk.gen_video(
        prompt=prompt.strip(),
        project_id=project_id,
        start_media_id=start_media_id.strip()
        if isinstance(start_media_id, str) and start_media_id.strip()
        else None,
        start_media_ids=start_media_ids,
        aspect_ratio=aspect,
        paygate_tier=tier,
        video_quality=video_quality,
        end_media_id=end_media_id,
        duration_s=duration_s,
        resolution=resolution,
    )
    if dispatch.get("error"):
        return dispatch, str(dispatch["error"])[:200]
    return await _poll_video_dispatch(sdk, dispatch, params.get("__request_id"))


async def _poll_video_dispatch(
    sdk, dispatch: dict, rid: Optional[int]
) -> tuple[dict, Optional[str]]:
    """Shared poll+aggregate for every async video dispatch (i2v, t2v, omni).

    Per-op resolution: each operation in the batch resolves independently
    (success, content-filter rejection, or timeout). Breaking the whole loop
    on the first per-op error collapsed a 4-variant gen into a hard failure
    even when 3/4 clips had already rendered, so every op terminates on its
    own and the outcome is aggregated at the end.

    Slot i in ``media_ids`` corresponds to slot i of the dispatch order (the
    original ``start_media_ids`` for i2v), so the frontend keeps
    upstream-image ↔ video-variant alignment even when middle slots fail.
    ``slot_errors`` mirrors that indexing — None for succeeded slots, the
    error code for blocked ones — so the viewer can render the exact filter
    reason on the blocked tile without knowing internal Flow op names."""
    op_names = dispatch.get("operation_names") or []
    if not op_names:
        return dispatch, NO_OPERATIONS_ERROR
    workflows = dispatch.get("workflows") or None

    poll_attempts = 0
    last_poll: dict = {}
    done_by_name: dict[str, bool] = {name: False for name in op_names}
    entry_by_name: dict[str, dict] = {}
    op_errors: dict[str, str] = {}

    while poll_attempts < VIDEO_POLL_MAX_CYCLES and not all(done_by_name.values()):
        await asyncio.sleep(VIDEO_POLL_INTERVAL_S)
        poll_attempts += 1
        if _is_request_canceled(rid):
            return (
                {
                    "raw_dispatch": dispatch,
                    "last_poll": last_poll,
                    "operation_names": op_names,
                    "done": done_by_name,
                    "canceled": True,
                },
                "canceled",
            )
        last_poll = await sdk.check_async(op_names, workflows=workflows)
        if last_poll.get("error"):
            continue
        for op in last_poll.get("operations") or []:
            if not isinstance(op, dict):
                continue
            name = op.get("name")
            if not isinstance(name, str) or done_by_name.get(name, False):
                continue
            err = op.get("error")
            if isinstance(err, str) and err:
                done_by_name[name] = True
                op_errors[name] = err
                continue
            if op.get("done"):
                done_by_name[name] = True
                for e in op.get("media_entries") or []:
                    if isinstance(e, dict) and e.get("media_id"):
                        entry_by_name[name] = e
                        break

    for name in op_names:
        if not done_by_name.get(name) and name not in op_errors:
            op_errors[name] = "timeout_waiting_video"

    positional_ids: list[Optional[str]] = []
    slot_errors: list[Optional[str]] = []
    succeeded_entries: list[dict] = []
    for name in op_names:
        e = entry_by_name.get(name)
        if isinstance(e, dict) and isinstance(e.get("media_id"), str):
            positional_ids.append(e["media_id"])
            succeeded_entries.append(e)
            slot_errors.append(None)
        else:
            positional_ids.append(None)
            slot_errors.append(op_errors.get(name))

    if not any(positional_ids):
        first_err = next(iter(op_errors.values()), "timeout_waiting_video")
        return (
            {
                "raw_dispatch": dispatch,
                "last_poll": last_poll,
                "operation_names": op_names,
                "done": done_by_name,
                "op_errors": op_errors,
            },
            first_err,
        )

    entries_with_urls = [
        e for e in succeeded_entries if isinstance(e, dict) and e.get("url")
    ]
    if entries_with_urls:
        try:
            media_service.ingest_urls(entries_with_urls)
        except Exception:
            logger.exception("auto-ingest from video response failed")
    partial_error: Optional[str] = None
    if op_errors:
        unique_errs = sorted({err for err in op_errors.values()})
        partial_error = (
            f"{len(op_errors)}/{len(op_names)} variants blocked: "
            f"{', '.join(unique_errs)}"
        )

    out: dict[str, Any] = {
        "raw_dispatch": dispatch,
        "last_poll": last_poll,
        "operation_names": op_names,
        "media_ids": positional_ids,
        "media_entries": succeeded_entries,
        "op_errors": op_errors,
        "slot_errors": slot_errors,
        "partial_error": partial_error,
    }
    # The model key that actually dispatched, lifted where the review loop
    # reads it. `gen_video` never lifted it, so on the one lane that IS free the
    # loop saw no key, refused to call it free, and stopped after the first
    # round — the exact lane it was built to iterate on.
    if dispatch.get("model_key"):
        out["model_key"] = dispatch["model_key"]
    # Lift the SDK's lane substitution to the top level: the UI reads the
    # result, not the raw dispatch, and "you asked for Lower Priority but
    # this ran on a paid model" is the one thing it must be able to say.
    #
    # On this transport the list is always empty, because a lane that cannot be
    # served is refused rather than swapped. The lift stays so the UI needs no
    # change if a future capture ever makes a substitution honest.
    if dispatch.get("model_substitutions"):
        out["model_substitutions"] = dispatch["model_substitutions"]
        out["effective_model_key"] = dispatch.get("effective_model_key")
    return out, None


async def _handle_poll_video(params: dict) -> tuple[dict, Optional[str]]:
    """Wait on operations a previous request already paid for.

    No captcha, no submit, no credits: this re-enters the same poll loop with
    operation names that are already running at Google. It exists because the
    batch path can find a finished clip again through the project listing, so a
    render abandoned at the executor's timeout is recoverable instead of lost.

    Returns exactly what a fresh dispatch returns, so `_settle_generation_node`
    cannot tell the difference and the node lands its media the usual way.
    """
    raw_names = params.get("operation_names")
    names = [n for n in (raw_names or []) if isinstance(n, str) and n]
    if not names:
        return {}, "missing_operation_names"
    raw_workflows = params.get("workflows")
    workflows = [
        w for w in (raw_workflows or [])
        if isinstance(w, dict) and w.get("name") and w.get("primary_media_id")
    ] or None

    dispatch = {
        "raw": None,
        "operation_names": names,
        "workflows": workflows,
        "model_key": params.get("model_key"),
        "model_substitutions": [],
        "repolled": True,
    }
    sdk = get_flow_sdk()
    # Tell the SDK which project these operations belong to before asking it to
    # find their media. It caches that at dispatch, in memory only, so on a fresh
    # process it knows nothing about an operation an earlier run created — and
    # without a project the listing is never consulted and every round reports
    # pending until the budget expires.
    sdk.remember_operations(names, params.get("project_id"))
    out, err = await _poll_video_dispatch(
        sdk, dispatch, params.get("__request_id")
    )
    # Put the results back where they came from.
    #
    # This poll ran over the OPEN subset, so its `media_ids` is positional over
    # that subset, not over the node's slots — while `_settle_generation_node`
    # assigns `mediaIds` wholesale. A 3-variant batch whose slot 2 had already
    # landed therefore came back as `[None, "media-B"]`: a clip the user had paid
    # for vanished from the node, and the remaining slots no longer lined up with
    # the source images they were generated from.
    slots = params.get("open_slots")
    landed = params.get("landed_media_ids")
    if isinstance(slots, list) and isinstance(landed, list) and slots:
        merged: list[Optional[str]] = [
            m if isinstance(m, str) else None for m in landed
        ]
        polled = out.get("media_ids") if isinstance(out.get("media_ids"), list) else []
        for position, slot in enumerate(slots):
            if not isinstance(slot, int) or not 0 <= slot < len(merged):
                continue
            value = polled[position] if position < len(polled) else None
            if isinstance(value, str) and value:
                merged[slot] = value
        out = {**out, "media_ids": merged}
    return out, err


async def _handle_gen_video_text(params: dict) -> tuple[dict, Optional[str]]:
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    tier = _tier_label(params)
    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"
    video_quality = params.get("video_quality")
    if not isinstance(video_quality, str) or not video_quality.strip():
        video_quality = None
    duration_s = params.get("duration_s")
    if duration_s is None:
        duration_s = OMNI_DEFAULT_DURATION_S
    elif not isinstance(duration_s, int) or isinstance(duration_s, bool):
        # Don't coerce a malformed duration to the default: duration selects
        # the model key, and a silent 8s substitution bills a longer clip
        # than the caller asked for. The SDK rejects it by name instead.
        return {}, f"invalid_duration_{duration_s!r}"[:200]
    # Through the shared clamp, like `gen_image` and `gen_image_openai`. This
    # was the one dispatch that read `variant_count` with only a floor: a node
    # carrying 50 submitted fifty clips, while the dialog's own ceiling is 16.
    variant_count = clamp_variant_count(params.get("variant_count"))

    sdk = get_flow_sdk()
    # `resolution` arrived in params from the frontend and was read by nothing
    # here, so a 360p choice was sent, ignored, and rendered at 720p while the
    # Settings hint promised it applied to the OMNI lane. Normalised the same way
    # `_handle_gen_video_omni` does; a Veo key carries no resolution and the SDK
    # ignores it on that lane.
    resolution = params.get("resolution")
    if isinstance(resolution, str) and resolution.strip().lower() in ("360p", "720p"):
        resolution = resolution.strip().lower()
    else:
        resolution = None
    dispatch = await sdk.gen_video_text(
        prompt=prompt.strip(),
        project_id=project_id,
        aspect_ratio=aspect,
        paygate_tier=tier,
        video_quality=video_quality,
        duration_s=duration_s,
        variant_count=variant_count,
        resolution=resolution,
    )
    if dispatch.get("error"):
        return dispatch, str(dispatch["error"])[:200]
    return await _poll_video_dispatch(sdk, dispatch, params.get("__request_id"))


async def _price_probe_start(sdk) -> Optional[int]:
    """The balance to measure a price against, refreshed if it has gone stale.

    Neither 1080p upscale nor the extension lanes have a published price, and
    this build has never measured them. The community capture that reported the
    free extension lane at 0 credit measured it on someone else's account, and
    this codebase's rule is that an unmeasured key is never treated as free.

    So the first real run measures. Both reads cost nothing: `nzlxg` is a read,
    and the poll carries the balance for free afterwards. A read that fails is
    not an error here — it only means this run cannot price itself.
    """
    from flowboard.services import flow_credits

    known = flow_credits.fresh()
    if known is not None:
        return known
    try:
        return (await sdk.get_credits()).get("credits")
    except Exception:
        logger.info("price probe: balance unreadable, this run cannot be priced")
        return None


def _price_probe_end(result: dict, before: Optional[int], label: str) -> None:
    """Attach what this run appears to have cost, with its caveat attached.

    Reported as an OBSERVATION, not as a price table: the delta covers whatever
    else the account spent in the same window, so it is only this operation's
    price when nothing else was rendering. Naming that in the field itself is
    what stops the number being copied into a table it has not earned.
    """
    from flowboard.services import flow_credits

    spent = flow_credits.spent_since(before)
    result["credits_before"] = before
    result["credits_spent_observed"] = spent
    if spent is None:
        result["credits_note"] = (
            f"{label}: chưa đo được giá (không đọc được số dư trước/sau)"
        )
        return
    result["credits_note"] = (
        f"{label}: số dư giảm {spent} credit trong lần chạy này — chỉ là giá của "
        "thao tác nếu lúc đó không có render nào khác"
    )
    logger.info("price observation: %s spent=%s (before=%s)", label, spent, before)


async def _handle_upscale_video(params: dict) -> tuple[dict, Optional[str]]:
    """Upscale a clip that already rendered, to 1080p.

    Addresses the clip by BOTH of its ids, because they are different things:
    the operation that produced it and the media it produced. The capture warns
    that swapping them is accepted and then fails NOT_FOUND, so neither is
    derived from the other here - both come from the node, which has kept them
    since `_settle_generation_node` started recording `operationNames`.

    A clip rendered before that is refused by name rather than upscaled with a
    guessed id: a wrong operation id spends a submit to learn it was wrong.

    The result does NOT overwrite the node's media. The original is what the user
    paid for, and a "make this better" button that replaces it is a loss with no
    undo - the frontend lands this under its own key.
    """
    from flowboard.services.flow_sdk import is_valid_project_id

    operation_id = params.get("operation_id") or params.get("operationId")
    media_id = params.get("media_id") or params.get("mediaId")
    project_id = params.get("project_id")
    if not isinstance(operation_id, str) or not operation_id.strip():
        return {}, "missing_upscale_operation_id"
    if not isinstance(media_id, str) or not media_id.strip():
        return {}, "missing_upscale_media_id"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    resolution = params.get("resolution")
    if not isinstance(resolution, str) or not resolution.strip():
        resolution = "1080p"
    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"

    sdk = get_flow_sdk()
    before = await _price_probe_start(sdk)
    dispatch = await sdk.upscale_video(
        operation_id.strip(), media_id.strip(), project_id,
        aspect=aspect, resolution=resolution.strip(),
    )
    if dispatch.get("error"):
        return dispatch, str(dispatch["error"])[:200]
    result, err = await _poll_video_dispatch(sdk, dispatch, params.get("__request_id"))
    if isinstance(result, dict):
        _price_probe_end(result, before, f"upscale {resolution.strip()}")
        # Flag what this result IS, so a consumer that only knows how to land a
        # generation cannot mistake it for one and write over `mediaId`.
        result["op_kind"] = "upscale"
        result["source_media_id"] = media_id.strip()
    return result, err


async def _handle_extend_video(params: dict) -> tuple[dict, Optional[str]]:
    """Add another few seconds to the end of a finished Veo clip.

    Two rules live here rather than in the caller, because both are money rules
    and this is where they can be tested:

    * **The chain source.** The FIRST extension references the CLONE that
      `rqZuUc` makes when it wraps the clip into a scene; every later one
      references the PREVIOUS extension's OPERATION id. The capture is explicit
      that converting that operation id back to a media id produces a request
      Flow accepts and then fails with NOT_FOUND - billed, and no clip.
    * **The lane.** Free unless the caller says `paid_lane`, and nothing infers
      it from anything else.

    The scene is created on demand and handed back so the node can remember it;
    a second scene for the same clip would restart the chain at position 1 and
    orphan whatever had already been extended.
    """
    from flowboard.services import flow_sdk as flow_sdk_module
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    media_id = params.get("media_id") or params.get("mediaId")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    if not isinstance(media_id, str) or not media_id.strip():
        return {}, "missing_media_id"
    media_id = media_id.strip()

    duration_s = params.get("duration_s")
    if duration_s is None:
        duration_s = 8
    if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)):
        return {}, f"invalid_duration_{duration_s!r}"[:200]
    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"
    paid_lane = params.get("paid_lane") is True
    source_model_key = params.get("source_model_key") or ""
    if not isinstance(source_model_key, str):
        source_model_key = ""
    # Refused HERE, before the scene, not at dispatch. The scene call is free but
    # it is a real write on the user's Flow project, and making one for a clip
    # that can never be extended leaves litter behind for a request that was
    # always going to be refused. The SDK refuses again on its own.
    if flow_sdk_module.is_omni_model_key(source_model_key):
        return {}, (
            "unsupported_extend_omni_source: Flow chỉ nối được clip Veo; "
            f"clip này là “{source_model_key}” (Omni) nên nút nối bị mờ"
        )[:200]
    # And refused when nothing SAYS what produced the clip. `params` arrives over
    # an unauthenticated local endpoint that accepts any request type, so the
    # greyed-out button is not the only gate. A blank key does not start with
    # `abra_`, so without this an Omni clip reads as "not Omni" and buys a submit
    # Flow accepts and then fails — the same hole the start-up backfill closes for
    # clips that DO have a record.
    if not source_model_key.strip():
        return {}, (
            "extend_unknown_source: không có bản ghi clip này từ model nào — "
            "chạy lại clip để có bản ghi, hoặc nối trong Flow"
        )[:200]

    scene_id = params.get("scene_id")
    scene_id = scene_id.strip() if isinstance(scene_id, str) else ""
    clone_media_id = params.get("scene_clone_media_id")
    clone_media_id = clone_media_id.strip() if isinstance(clone_media_id, str) else ""
    previous_operation_id = params.get("previous_operation_id")
    previous_operation_id = (
        previous_operation_id.strip()
        if isinstance(previous_operation_id, str) else ""
    )

    sdk = get_flow_sdk()
    # Read the balance BEFORE the scene call, not just before the extension: the
    # scene is meant to be free, and a measurement that starts after it would
    # hide a charge nobody expected.
    before = await _price_probe_start(sdk)
    made_scene = False
    if not scene_id:
        try:
            scene = await sdk.create_scene(project_id, [media_id], aspect=aspect)
        except Exception as exc:
            return {}, f"extend_scene_failed: {str(exc)[:160]}"
        scene_id = str(scene.get("scene_id") or "")
        clone_media_id = str(scene.get("clone_media_id") or "")
        made_scene = True
        if not scene_id:
            return {}, "extend_scene_returned_no_id"

    # Link N of the chain. `previous_operation_id` wins because it is the only
    # correct source once a chain exists; the clone is only correct for link 1.
    source = previous_operation_id or clone_media_id or media_id
    position = params.get("position")
    if isinstance(position, bool) or not isinstance(position, int) or position < 1:
        position = 1

    dispatch = await sdk.extend_video(
        prompt.strip(), project_id, scene_id, source,
        duration_s=float(duration_s), position=position, aspect=aspect,
        source_model_key=source_model_key, paid_lane=paid_lane,
    )
    scene_out = {
        "op_kind": "extend",
        "scene_id": scene_id,
        "scene_clone_media_id": clone_media_id,
        "scene_created": made_scene,
        "position": position,
        "source_media_id": media_id,
        "extend_source": source,
    }
    if dispatch.get("error"):
        # Carry the scene out even on failure: it exists on Flow now, and a
        # retry that made a second one would restart the chain at position 1.
        return {**dispatch, **scene_out}, str(dispatch["error"])[:200]
    result, err = await _poll_video_dispatch(sdk, dispatch, params.get("__request_id"))
    if isinstance(result, dict):
        result.update(scene_out)
        _price_probe_end(
            result, before, "extend " + ("paid" if paid_lane else "free") + " lane"
        )
    return result, err


async def _handle_edit_image(params: dict) -> tuple[dict, Optional[str]]:
    from flowboard.services.flow_sdk import is_valid_project_id

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    source_media_id = params.get("source_media_id") or params.get("sourceMediaId")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    if not isinstance(source_media_id, str) or not source_media_id.strip():
        return {}, "missing_source_media_id"
    aspect = params.get("aspect_ratio") or "IMAGE_ASPECT_RATIO_LANDSCAPE"
    tier = _tier_label(params)
    raw_refs = params.get("ref_media_ids")
    ref_ids: Optional[list[str]] = None
    if isinstance(raw_refs, list):
        cleaned = [m for m in raw_refs if isinstance(m, str) and m]
        ref_ids = cleaned or None
    image_model = params.get("image_model")
    if not isinstance(image_model, str) or not image_model.strip():
        image_model = None

    resp = await get_flow_sdk().edit_image(
        prompt=prompt.strip(),
        project_id=project_id,
        source_media_id=source_media_id.strip(),
        ref_media_ids=ref_ids,
        aspect_ratio=aspect,
        paygate_tier=tier,
        image_model=image_model,
    )
    if resp.get("error"):
        return resp, str(resp["error"])[:200]
    entries_with_urls = [
        e for e in (resp.get("media_entries") or []) if isinstance(e, dict) and e.get("url")
    ]
    if entries_with_urls:
        try:
            media_service.ingest_urls(entries_with_urls)
        except Exception:
            logger.exception("auto-ingest from edit_image response failed")
    return resp, None



# ── Omni Flash r2v ────────────────────────────────────────────────────────
# Variable-duration video model with a distinct endpoint + body shape from
# Veo i2v. See agent/flowboard/services/flow_sdk.py::gen_video_omni for the
# request assembly. Single operation per request (no multi-source batching
# like Veo's start_media_ids), so the polling logic collapses to a single
# op + first-error-wins, simpler than _handle_gen_video.

async def _handle_gen_video_omni(params: dict) -> tuple[dict, Optional[str]]:
    from flowboard.services.flow_sdk import (
        is_valid_project_id,
        resolve_character_port_plan,
    )
    from flowboard.services.media_project_sync import (
        MediaSyncError,
        ensure_media_ids_in_project,
    )

    prompt = params.get("prompt")
    project_id = params.get("project_id")
    raw_refs = params.get("ref_media_ids")
    if not isinstance(raw_refs, list):
        # Also accept the legacy single-source field for symmetry with
        # Veo's start_media_id, so the same upstream-walk on the frontend
        # works without a special-case.
        raw_refs = (
            [params.get("start_media_id")]
            if isinstance(params.get("start_media_id"), str)
            else []
        )
    ref_media_ids = [m for m in raw_refs if isinstance(m, str) and m.strip()]
    raw_entities = params.get("ref_entity_ids")
    ref_entity_ids = [
        e.strip() for e in (raw_entities or []) if isinstance(e, str) and e.strip()
    ]
    duration_s = params.get("duration_s")

    if not isinstance(prompt, str) or not prompt.strip():
        return {}, "missing_prompt"
    if not isinstance(project_id, str) or not project_id.strip():
        return {}, "missing_project_id"
    project_id = project_id.strip()
    if not is_valid_project_id(project_id):
        return {}, "invalid_project_id"
    if not ref_media_ids and not ref_entity_ids:
        # Either is enough — a Character Entity needs no uploaded still, and a
        # still needs no entity. Refusing on images alone made the entity path
        # unreachable even once the payload could carry it.
        return {}, "missing_ref_media_ids"
    if not isinstance(duration_s, int) or duration_s not in (4, 6, 8, 10):
        return {}, "invalid_duration_s"
    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_PORTRAIT"
    tier = _tier_label(params)

    # ── Which family, and why the lane still decides ──────────────────
    # One RPC, and on this transport only one family can serve it: Omni's
    # `abra_r2v_<N>s`, which costs 15-30 credits by duration. Veo's own r2v
    # family — `veo_3_1_r2v_*` — was believed to have no payload here. Measured
    # 19/09/2026: `MZZa6b` parses `veo_3_1_r2v_lite_low_priority` and objects
    # only to the plan, so exactly one Veo r2v key is UNDERSTOOD on this
    # transport. The other three carry `portrait` and come back `[5]` NOT_FOUND.
    #
    # "Objects only to the plan" is the important half, and an earlier version of
    # this comment drew the wrong conclusion two lines below its own evidence: it
    # means **this account does not hold that key**. So the key's price is
    # unmeasured here and cannot be measured here. Absent from
    # `ZERO_CREDIT_MODEL_KEYS`, quoted as unpriced by `routes/estimate.py`.
    #
    # That one key is why this branch exists. A board on the free reference lane
    # used to be refused, and its only alternative was Omni at 15-30 credits —
    # billing a board that asked for the free queue is the substitution this
    # build is built to avoid, and now it does not have to.
    #
    # An absent lane still means Omni: that is what the Settings-level "OMNI
    # flash" model choice sends, and it is a choice rather than a default.
    resolution = params.get("resolution")
    if not isinstance(resolution, str) or resolution.strip().lower() not in (
        "360p", "720p"
    ):
        resolution = "720p"
    else:
        resolution = resolution.strip().lower()

    # Which family and key, asked through the one resolver `/estimate` also
    # calls. Two copies of this decision is how a board quoted at 0 credits
    # dispatched 75: the quote read an unset lane as `lite` and refused it, this
    # side read the same unset lane as Omni and billed.
    #
    # Veo's r2v key carries no duration and no resolution: those belong to Omni's
    # key, and sending them beside a Veo one puts numbers in the request row that
    # did nothing.
    lane = params.get("video_quality")
    lane = lane.strip() if isinstance(lane, str) and lane.strip() else None
    plan = resolve_character_port_plan(lane, duration_s, resolution)
    if plan["error"]:
        return {}, plan["error"][:200]
    model_key: Optional[str] = plan["model_key"]

    # ── Cross-project ref sync ────────────────────────────────────────
    # Flow scopes mediaIds to the project they were uploaded in. When
    # the user references media generated under another board's project
    # (the cross-board Reference library case), Flow returns 404 because
    # the asset is unknown in this project. Re-upload bytes from the
    # local cache and substitute the project-local id before dispatch.
    # First sync hits the Flow upload endpoint per ref; subsequent
    # syncs use the MediaProjectMapping cache and are free.
    try:
        synced_refs, sync_failures = await ensure_media_ids_in_project(
            ref_media_ids, project_id
        )
    except MediaSyncError as exc:
        return {}, f"sync_failed: {exc}"[:200]
    if not synced_refs and ref_entity_ids:
        # Entities alone is a valid dispatch, so "no images to sync" is not a
        # failure here.
        synced_refs, sync_failures = [], []
    elif not synced_refs:
        # Every ref failed to sync — surface the first reason.
        first = sync_failures[0][1] if sync_failures else "no_refs_synced"
        return (
            {"sync_failures": sync_failures},
            f"sync_failed: {first}"[:200],
        )
    if sync_failures:
        # Partial sync — log; proceed with the refs that worked.
        logger.warning(
            "gen_video_omni: %d ref(s) failed to sync, proceeding with %d",
            len(sync_failures), len(synced_refs),
        )

    sdk = get_flow_sdk()
    dispatch = await sdk.gen_video_omni(
        prompt=prompt.strip(),
        project_id=project_id,
        ref_media_ids=synced_refs,
        duration_s=duration_s,
        aspect_ratio=aspect,
        paygate_tier=tier,
        model_key=model_key,
        ref_entity_ids=ref_entity_ids,
        resolution=resolution,
    )
    if dispatch.get("error"):
        return dispatch, str(dispatch["error"])[:200]

    result, err = await _poll_video_dispatch(
        sdk, dispatch, params.get("__request_id")
    )
    # Omni's duration is a request-level choice (not encoded in the model
    # key like Veo), so echo it back for the viewer. The model key travels
    # with it because the lane label is not enough to know what was billed:
    # the review loop reads this to decide whether another round is free.
    if isinstance(result, dict):
        result["duration_s"] = duration_s
        result["model_key"] = model_key
        result["resolution"] = resolution
    return result, err


_DEFAULT_HANDLERS: dict[str, Handler] = {
    "create_project": _handle_create_project,
    "gen_image": _handle_gen_image,
    "gen_image_openai": _handle_gen_image_openai,
    "gen_video": _handle_gen_video,
    "gen_video_text": _handle_gen_video_text,
    "gen_video_omni": _handle_gen_video_omni,
    "poll_video": _handle_poll_video,
    "upscale_video": _handle_upscale_video,
    "extend_video": _handle_extend_video,
    "edit_image": _handle_edit_image,
    "postprod": handle_postprod,
}


def _configured_concurrency() -> int:
    """How many requests may be in flight at once.

    The packaged tool exposes this as ``MULTI_VIDEO`` (ships as 4), so the
    setting has to reach the semaphore instead of sitting in the settings
    table while a separate constant decides the real behaviour. An explicit
    ``FLOWBOARD_MAX_CONCURRENT`` still wins, so a deployment can pin the
    value without touching user settings.

    Raising this does not speed up outbound calls — ``cooldown_s`` spaces
    dispatches apart regardless — it only allows more renders to be waited
    on in parallel. Settings live in the DB, so a lookup that runs before
    ``init_db`` must not stop the worker from being constructed.
    """
    from flowboard.config import MAX_CONCURRENT

    if os.getenv("FLOWBOARD_MAX_CONCURRENT"):
        return MAX_CONCURRENT
    try:
        from flowboard.services import settings_store

        value = settings_store.get("MULTI_VIDEO")
    except Exception:
        return MAX_CONCURRENT
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return MAX_CONCURRENT
    return value


class WorkerController:
    """Bounded-concurrency async queue worker.

    Up to ``max_concurrent`` requests process at once (each may poll Flow
    for minutes), while ``cooldown_s`` spaces successive DISPATCHES apart so
    the outbound API cadence stays human-paced. A slower single request no
    longer blocks the others behind it.
    """

    def __init__(
        self,
        handlers: Optional[dict[str, Handler]] = None,
        max_concurrent: Optional[int] = None,
        cooldown_s: Optional[float] = None,
        enable_sweeper: bool = False,
        sweep_interval_s: float = 5.0,
    ) -> None:
        from flowboard.config import API_COOLDOWN_S

        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._handlers = dict(handlers or _DEFAULT_HANDLERS)
        self._shutdown = asyncio.Event()
        self._active = 0
        self._started_at: Optional[float] = None
        self._max_concurrent = (
            max_concurrent if max_concurrent is not None else _configured_concurrency()
        )
        self._cooldown_s = (
            cooldown_s if cooldown_s is not None else API_COOLDOWN_S
        )
        self._sem = asyncio.Semaphore(max(1, self._max_concurrent))
        self._tasks: set[asyncio.Task] = set()
        self._rate_lock = asyncio.Lock()
        self._last_dispatch = 0.0
        # Ids currently queued in memory or in flight — the sweeper checks
        # this to avoid re-enqueuing a row that's already being handled.
        self._known: set[int] = set()
        self._cumulative_403 = 0
        self._tripped = False  # 403 breaker fired → stop dispatching
        self._tripped_at = 0.0  # monotonic stamp, drives the half-open probe
        self._enable_sweeper = enable_sweeper
        # Paused by the user, as opposed to `_tripped` which is the 403
        # breaker pausing itself. Two flags rather than one because they
        # clear differently: the breaker probes its way back on a timer,
        # while this one waits for the person who set it.
        self._paused = False
        self._sweep_interval_s = sweep_interval_s

    # ── enqueue ────────────────────────────────────────────────────────────
    def enqueue(self, request_id: int) -> None:
        self._known.add(request_id)
        self._queue.put_nowait(request_id)

    # ── 403 breaker ────────────────────────────────────────────────────────
    def _trip_breaker(self) -> None:
        self._tripped = True
        self._tripped_at = time.monotonic()
        logger.error(
            "403 breaker tripped after %d cumulative 403s — pausing dispatch "
            "to protect the account; one probe request every %.0fs will "
            "resume it automatically once the account recovers",
            self._cumulative_403,
            BREAKER_PROBE_INTERVAL_S,
        )

    def _reset_breaker(self) -> None:
        if self._tripped:
            logger.info("403 breaker reset — a request succeeded")
        self._tripped = False
        self._tripped_at = 0.0
        self._cumulative_403 = 0

    def _probe_due(self) -> bool:
        """Read-only: is the tripped breaker due to let a probe through?"""
        return (
            self._tripped
            and time.monotonic() - self._tripped_at >= BREAKER_PROBE_INTERVAL_S
        )

    def _consume_probe(self) -> None:
        """Spend the half-open probe: re-arm the clock so the next one is a
        full interval away. Called only when a request is actually being
        dispatched — spending it on an idle tick would push real recovery
        from one interval out to many, since most ticks have nothing to run.
        """
        self._tripped_at = time.monotonic()
        logger.info("403 breaker half-open — letting one probe request through")

    def _dispatch_blocked(self) -> bool:
        """True while the breaker holds dispatch shut. Consumes the probe.

        Half-open: once tripped, let a single request through every
        BREAKER_PROBE_INTERVAL_S. Without it the breaker is one-way —
        nothing dispatches, so nothing can succeed, so the reset never
        fires and the queue stalls silently until the process restarts.
        """
        if not self._tripped:
            return False
        if self._probe_due():
            self._consume_probe()
            return False
        return True

    # ── user pause ─────────────────────────────────────────────────────────

    def pause(self) -> None:
        """Stop taking new work. In-flight requests are left to finish —
        they are already paid for and abandoning one keeps nothing."""
        self._paused = True
        logger.info("worker paused by user (%d queued)", self._queue.qsize())

    def resume(self) -> None:
        self._paused = False
        logger.info("worker resumed by user")

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def breaker_state(self) -> dict[str, Any]:
        """Surfaced on /api/health: a stalled queue must be visible."""
        return {
            "tripped": self._tripped,
            "cumulative_403": self._cumulative_403,
            "seconds_until_probe": (
                max(
                    0.0,
                    BREAKER_PROBE_INTERVAL_S - (time.monotonic() - self._tripped_at),
                )
                if self._tripped
                else 0.0
            ),
        }

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._started_at = time.time()
        logger.info(
            "worker started (max_concurrent=%d, cooldown=%.1fs, sweeper=%s)",
            self._max_concurrent,
            self._cooldown_s,
            self._enable_sweeper,
        )
        sweeper: Optional[asyncio.Task] = None
        if self._enable_sweeper:
            sweeper = asyncio.create_task(self._sweep_loop())
        while not self._shutdown.is_set():
            # The 403 breaker halts new dispatch to protect the account.
            # Check BEFORE popping so a queued rid isn't consumed-and-dropped
            # while tripped — rows stay 'queued' in the DB and re-dispatch on
            # the next restart (which clears the breaker).
            if self._tripped and not self._probe_due():
                await asyncio.sleep(0.5)
                continue
            # Paused: checked in the same place and for the same reason as
            # the breaker above — popping first would consume a rid and drop
            # it, so the row would sit `queued` in the database with nothing
            # in memory pointing at it until a restart.
            if self._paused:
                await asyncio.sleep(0.5)
                continue
            try:
                rid = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            # Spend the probe only now that there is real work to send. This
            # loop wakes every 0.5s and most wake-ups have an empty queue, so
            # consuming it before the pop threw the probe away on idle ticks
            # and stretched recovery from one interval into many.
            if self._tripped:
                self._consume_probe()
            # Wait for a free slot before spawning so at most
            # max_concurrent are in flight. The row stays 'queued' in the
            # DB until _process_one flips it, so a shutdown here loses
            # nothing — it's re-dispatched on the next boot.
            await self._sem.acquire()
            if self._shutdown.is_set():
                self._sem.release()
                break
            task = asyncio.create_task(self._run_one(rid))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        if sweeper is not None:
            sweeper.cancel()
        # Graceful shutdown: let in-flight requests finish rather than
        # abandoning them mid-poll (which would strand them as 'running').
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run_one(self, rid: int) -> None:
        try:
            await self._rate_gate()
            await self._process_one(rid)
        finally:
            self._sem.release()
            # Whatever the outcome, this rid is no longer being handled. If
            # it was requeued for retry (status='queued' with a future
            # next_attempt_at), the sweeper re-adds it once due.
            self._known.discard(rid)

    async def _sweep_loop(self) -> None:
        """Re-dispatch due queued rows. This is what makes retries fire and
        what recovers queued rows after a restart. Paused while the 403
        breaker is tripped or the extension bridge is down — dispatching
        into a dead bridge would just fail every row in a loop."""
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(self._sweep_interval_s)
                if not flow_client.connected:
                    continue
                if self._paused:
                    continue
                if self._tripped and not self._probe_due():
                    # Keep feeding the queue when a probe is due — the main
                    # loop is what actually consumes the probe, and it can
                    # only do that if there's a row waiting. Rows that don't
                    # get the probe stay queued behind the closed breaker.
                    continue
                self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("sweeper tick failed")

    def _sweep_once(self) -> None:
        now = datetime.now(timezone.utc)
        with get_session() as s:
            rows = s.exec(
                select(Request.id)
                .where(
                    Request.status == "queued",
                    or_(
                        Request.next_attempt_at.is_(None),  # type: ignore[union-attr]
                        Request.next_attempt_at <= now,
                    ),
                )
                .order_by(Request.id)
                .limit(50)
            ).all()
        for rid in rows:
            if rid not in self._known:
                self._known.add(rid)
                self._queue.put_nowait(rid)

    async def _rate_gate(self) -> None:
        """Space successive dispatches at least ``cooldown_s`` apart. Held
        under a lock so the spacing is enforced across concurrent workers;
        processing itself still overlaps once dispatched."""
        if self._cooldown_s <= 0:
            return
        async with self._rate_lock:
            wait = self._last_dispatch + self._cooldown_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_dispatch = time.monotonic()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def drain(self) -> None:
        # Wait for all in-flight tasks to finish.
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def uptime_s(self) -> Optional[float]:
        if self._started_at is None:
            return None
        return time.time() - self._started_at

    def _apply_failure(self, req: Request, err: str, result: Any) -> None:
        """Decide retry vs terminal for a failed request, mutating ``req``.

        Credit safety is the governing rule: once a Flow *operation* exists
        (``result['operation_names']``), the generation is already rendering
        and has been charged — re-dispatching would create a NEW op and
        charge again. So only failures that happened BEFORE an op was
        created are ever auto-retried. ``timeout_waiting_video`` carries
        operation_names, so it is never auto-retried and keeps its
        'timeout' status for the UI.
        """
        cls = classify_error(err, request_type=req.type)
        op_created = isinstance(result, dict) and bool(result.get("operation_names"))

        if cls == "403":
            self._cumulative_403 += 1
            if self._cumulative_403 >= MAX_CUMULATIVE_403:
                self._trip_breaker()

        # The breaker deliberately does NOT make failures terminal. It gates
        # DISPATCH (see _dispatch_blocked), so a tripped breaker should pause
        # work, not destroy it: with the half-open probe sending one real
        # request per interval, treating a failure-while-tripped as terminal
        # would permanently kill one queued request per probe — the breaker
        # would eat the queue a row at a time instead of holding it.
        retryable = cls in ("free", "captcha", "counted", "403") and not op_created
        # 'free' and 'captcha' are not the request's fault — an extension
        # hiccup or a challenge the user still has to solve — so neither
        # burns the shared attempt budget. They get their own counters
        # instead, because "doesn't burn attempts" must not mean "retries
        # forever": an expired token would re-dispatch every 15s all session.
        if cls == "free":
            budget_used, cap = req.free_retries, FREE_MAX_RETRIES
        elif cls == "captcha":
            budget_used, cap = req.captcha_retries, CAPTCHA_MAX_ATTEMPTS
        else:
            budget_used, cap = req.attempt, req.max_attempts

        if retryable and budget_used < cap:
            if cls == "free":
                req.free_retries += 1
                delay = 15.0
            elif cls == "captcha":
                req.captcha_retries += 1
                delay = 30.0
            else:
                req.attempt += 1
                delay = min(2 ** req.attempt * 10, 300)
            req.status = "queued"
            req.error = err  # keep the last reason visible while it waits
            req.finished_at = None
            req.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            return

        if cls == "free" and budget_used >= cap:
            # Name the real problem: the bridge/token never recovered, and
            # this row stopped waiting rather than looping on it.
            err = f"auth_retry_exhausted after {budget_used}: {err}"[:500]

        # Terminal. Preserve the distinct 'timeout' status the UI renders.
        req.status = "timeout" if err == "timeout_waiting_video" else "failed"
        req.error = err
        req.finished_at = datetime.now(timezone.utc)

    # ── execution ──────────────────────────────────────────────────────────
    async def _process_one(self, rid: int) -> None:
        self._active += 1
        try:
            with get_session() as s:
                req = s.get(Request, rid)
                if req is None:
                    logger.warning("worker: request %s not found", rid)
                    return
                # Drift guard — the row might have been canceled (or
                # otherwise transitioned out of queued) between enqueue
                # and pop. The cancel endpoint mutates the DB row only;
                # it can't yank the rid back off the in-memory queue, so
                # we re-check here and bail without flipping status.
                if req.status != "queued":
                    logger.info(
                        "worker: skipping rid=%s (status=%s)", rid, req.status
                    )
                    return
                handler = self._handlers.get(req.type)
                if handler is None:
                    req.status = "failed"
                    req.error = f"unknown_request_type:{req.type}"
                    req.finished_at = datetime.now(timezone.utc)
                    s.add(req)
                    s.commit()
                    return

                req.status = "running"
                s.add(req)
                s.commit()
                params = dict(req.params or {})
                # Enrich with the request's node_id so handlers that need
                # to look up Node.data don't depend on the caller copying
                # it into params explicitly. Underscore prefix avoids
                # colliding with handler-defined fields.
                if req.node_id is not None and "__node_id" not in params:
                    params["__node_id"] = req.node_id
                # Long-running handlers re-check this rid between polls
                # to honor user-initiated cancels.
                params["__request_id"] = rid

            # Release the session during the possibly-long RPC.
            result, err = await handler(params)

            with get_session() as s:
                req = s.get(Request, rid)
                if req is None:
                    return
                # Don't overwrite a canceled row with a late-arriving
                # done/failed stamp. The cancel endpoint already set
                # status='canceled' and finished_at; we only persist the
                # partial result for debugging visibility.
                if req.status == "canceled":
                    if isinstance(result, dict):
                        req.result = result
                        s.add(req)
                        s.commit()
                    return
                req.result = result if isinstance(result, dict) else {"value": result}
                if err:
                    self._apply_failure(req, err, result)
                else:
                    # A clean success clears the 403 breaker — the account
                    # is evidently fine. This is what a half-open probe is
                    # for: it's the only way a tripped breaker recovers
                    # without a restart.
                    self._reset_breaker()
                    req.status = "done"
                    req.error = None
                    req.finished_at = datetime.now(timezone.utc)
                s.add(req)
                s.commit()
        except Exception as exc:
            logger.exception("worker exception on rid=%s", rid)
            try:
                with get_session() as s:
                    req = s.get(Request, rid)
                    if req is not None and req.status != "canceled":
                        req.status = "failed"
                        req.error = str(exc)[:500]
                        req.finished_at = datetime.now(timezone.utc)
                        s.add(req)
                        s.commit()
            except Exception:
                logger.exception("worker: failed to record failure for rid=%s", rid)
        finally:
            self._active -= 1


_worker: Optional[WorkerController] = None


def get_worker() -> WorkerController:
    global _worker
    if _worker is None:
        # Production worker runs the sweeper so retries fire and queued
        # rows recover after a restart. Tests build their own controller
        # (sweeper off) and drive enqueue directly.
        _worker = WorkerController(enable_sweeper=True)
    return _worker
