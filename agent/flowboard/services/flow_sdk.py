"""Minimal Google Flow SDK wrapper.

Ported from flowkit (`/tmp/flowkit-ref/agent/services/flow_client.py`).
Trimmed to what Run 4 ships: `create_project` (TRPC) + `gen_image` (api_request
with IMAGE_GENERATION captcha). Video / upload / upscale / check_async land in
later runs.

The wrapper intentionally preserves `raw` on every return so callers (and the
request-worker that persists it to the DB) can inspect Flow's error payload
when the user's paygate tier or model name drifts.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

from flowboard.services import flow_batch as fb
from flowboard.services import flow_credits
from flowboard.services.flow_client import FlowClient, flow_client

logger = logging.getLogger(__name__)

# ── the transport ──────────────────────────────────────────────────────────
#
# Every Flow call is a `batchexecute` RPC that the extension runs inside a
# signed-in `flow.google.com` tab. The REST endpoints this module used to hold
# (`aisandbox-pa.googleapis.com/v1/…`) and the tRPC ones on `labs.google` are
# both gone: Google's September 2026 migration stopped minting the Bearer token
# the first needed and unauthenticated the second. `flow_batch` owns the wire
# shapes; this module owns the decisions.

#: Flow's own image composer launches x4 as four separate RPCs at roughly these
#: offsets rather than bursting them. Matched because a burst looks different to
#: Google's abuse detection than a UI does, and the difference costs a
#: `PUBLIC_ERROR_UNUSUAL_ACTIVITY`.
IMAGE_SUBMIT_OFFSETS_S: tuple[float, ...] = (0.0, 0.5, 1.5, 2.5)

#: Flow answers `[8]` when its generation side refuses under load. Measured
#: upstream: an immediate retry is refused again. One cooldown, one retry.
IMAGE_TRANSIENT_RETRY_DELAY_S = 34.0

#: Returned when a capability has no captured payload on this transport. The
#: prefix is load-bearing: `worker/processor.classify_error` reads it as
#: terminal, because no amount of retrying will capture an RPC.
UNSUPPORTED_PREFIX = "unsupported_on_batch_"
UNSUPPORTED_PROJECT_LISTING = (
    UNSUPPORTED_PREFIX + "project_listing: Flow's project list has no captured RPC; "
    "see docs/flow-capture.md"
)


def _pinned_project_id() -> Optional[str]:
    """The Flow project uuid the user pinned in Settings, if any.

    Only a fallback for `create_project`. It is deliberately NOT consulted when
    scoping an ordinary RPC: doing that would quietly run one board's work in
    another board's project, and the media would land where nobody looks for it.
    """
    from flowboard.services import settings_store

    value = settings_store.get("FLOW_PROJECT_ID")
    return value.strip() if isinstance(value, str) and value.strip() else None


# Omni Flash — the variable-duration family. Its duration IS part of the model
# key, so a wrong duration bills a different clip length than was asked for.
#
# The per-duration key table that used to sit here (`OMNI_FLASH_DURATION_KEYS`,
# mapping 4/6/8/10 to `abra_r2v_<N>s`) is gone: `omni_model_key` builds the same
# keys for four modes rather than one, and two tables spelling the same thing is
# how a 6s reference clip would eventually dispatch as 8s.
OMNI_FLASH_VALID_ASPECTS: set[str] = {
    "VIDEO_ASPECT_RATIO_PORTRAIT",
    "VIDEO_ASPECT_RATIO_LANDSCAPE",
}
# Informational — backend doesn't enforce, frontend surfaces to the user
# at dispatch time so the credit cost is visible before submit.
OMNI_FLASH_CREDIT_COST: dict[int, int] = {4: 15, 6: 20, 8: 25, 10: 30}


#: The length an Omni request falls back to when nothing chose one. Named so
#: the SDK default, the worker default and the UI default are one number: they
#: were three, and the cost dialog quoted whichever it happened to read.
OMNI_DEFAULT_DURATION_S = 8


#: Prefix on the one Flow error worth another attempt.
#:
#: Flow answers `[8]` when its own generation side refuses under load; measured
#: upstream, an immediate retry is refused again but a later one succeeds. Every
#: other Flow error is terminal. That difference is only visible on the
#: exception's ``detail``, so it is turned into a stable code HERE rather than
#: recovered downstream by matching an exception's message text — which would
#: make the retry policy depend on how `RpcError.__init__` formats itself.
TRANSIENT_RPC_CODE = "flow_transient_retry"


def _error_text(exc: BaseException) -> str:
    """Error string for a failed RPC: Flow's own code first, when it gave one.

    Three shapes, in the order they are checked:

    * a transient `[8]` gets the retry marker, because that difference is only
      visible on the exception's ``detail``;
    * any other Flow error leads with its `PUBLIC_ERROR_*` name, so the worker's
      classifier and the user's screen both read a code rather than a repr of a
      nested protobuf envelope;
    * anything else falls back to the exception type.
    """
    if isinstance(exc, fb.RpcError):
        if exc.detail == [fb.RPC_RESOURCE_EXHAUSTED]:
            return f"{TRANSIENT_RPC_CODE}: {exc}"
        reason = fb.read_rpc_reason(exc.detail)
        if reason:
            return f"{reason} ({exc.rpcid})"
    return f"{type(exc).__name__}: {exc}"


# Image model keys, indexed by the user-facing nickname used in
# flowkit's models.json. Pro is Flow's premium / higher-quality image
# model; "Banana 2" (NARWHAL) is the lighter / faster option. The
# frontend Settings panel lets the user pick which one drives gen_image
# + edit_image at request time. Update when Google rotates model names.
IMAGE_MODELS: dict[str, str] = {
    "NANO_BANANA_PRO": "GEM_PIX_2",
    "NANO_BANANA_2": "NARWHAL",
    # Third wire id the batch path accepts. It was missing here while
    # `flow_batch.IMAGE_MODEL_WIRE_IDS` already allowed it, which meant a model
    # Flow would have run had no nickname anyone could pick.
    "NANO_BANANA_2_LITE": "HARBOR_SEAL",
}
DEFAULT_IMAGE_MODEL_KEY = "NANO_BANANA_PRO"


def resolve_image_model(key: Optional[str]) -> str:
    """Map a nickname to the Flow wire id for that image model.

    An unknown or missing nickname falls back to the Pro default, so a stale
    frontend cannot break dispatch. That fallback is safe **here and not for
    video**: every image model costs the same, whereas folding an unknown video
    key onto a default is how a 0-credit lane became a paid one. `flow_batch`
    still checks the resulting wire id against a closed set, so this cannot
    invent one.
    """
    if isinstance(key, str) and key in IMAGE_MODELS:
        return IMAGE_MODELS[key]
    return IMAGE_MODELS[DEFAULT_IMAGE_MODEL_KEY]

# ── video lanes ────────────────────────────────────────────────────────────
#
# One flat map, because the batch path accepts exactly three Veo keys and aspect
# is its own payload slot. What used to be here — [tier][quality][aspect] tables
# for i2v, start-end, r2v and t2v, plus four resolvers with fallback chains —
# described a REST API that no longer authenticates. Every suffixed key in those
# tables (`…_portrait`, `…_fl`, `…_relaxed`) is now rejected by Flow.
#
# **A lane this path does not have is REFUSED, never substituted.** Upstream
# folds unknown keys onto a default, and because it matches the substring
# "ultra" its fold sends the 0-credit `veo_3_1_i2v_s_fast_ultra_relaxed` to the
# PAID `veo_3_1_i2v_s_fast_ultra`. A lane that asked to be free being billed is
# the one substitution this build never makes.
BATCH_VIDEO_LANES: dict[str, str] = {
    # The low-priority queue: the only key on this path that costs nothing.
    "lite_relaxed": "veo_3_1_i2v_lite_low_priority",
    "lite": "veo_3_1_i2v_lite",
    # The only "fast" key the batch path accepts. Its name says Ultra; whether a
    # Pro account may send it is unmeasured (probe 4 in the plan's P13.10). If
    # Google refuses it that is an error, not a charge.
    "fast": "veo_3_1_i2v_s_fast_ultra",
}

#: Lanes the UI still offers because the packaged tool does, and which this
#: transport cannot serve. Refused by name so the reason reaches the user
#: instead of a silent downgrade.
REFUSED_VIDEO_LANES: dict[str, str] = {
    "fast_relaxed": "chưa có key nào trên đường mới (làn 0 credit duy nhất là lite_relaxed)",
    "quality": "chưa có key nào trên đường mới",
}

#: Every model key Google runs at no credit cost. One entry now, and the set
#: rather than a lane name is what every "may I spend this automatically?"
#: decision reads — the lane a board asks for and the key that dispatches are
#: not always the same thing.
ZERO_CREDIT_MODEL_KEYS: frozenset[str] = frozenset(
    {"veo_3_1_i2v_lite_low_priority"}
)


def is_zero_credit_model(model_key: Optional[str]) -> bool:
    """Whether a dispatch on this key costs the user nothing.

    Unknown keys are NOT free. A key this table has not heard of is either new
    or misspelled, and guessing "free" on either is how automation starts
    spending without asking.
    """
    return bool(model_key) and str(model_key) in ZERO_CREDIT_MODEL_KEYS


#: The lane an unset choice lands on. `lite`, not `fast`: `fast` maps to the
#: priciest key this path has, and a board that never chose should not be
#: charged the most for it.
DEFAULT_VIDEO_QUALITY = "lite"

#: Omni's own durations, for text-to-video and references. Unlike Veo, the
#: duration IS the model key here.
OMNI_VALID_DURATIONS = (4, 6, 8, 10)
OMNI_VALID_RESOLUTIONS = ("360p", "720p")

# ── Veo text-to-video ──────────────────────────────────────────────────────
#
# Sixteen `veo_3_1_t2v_*` names live in the packaged exe (mmap scan, 19/09/2026).
# This table sends six of them, and the shape of the choice is worth writing
# down because most of it is inference from ONE measurement:
#
#   * measured — `YhhmEf` answered `PUBLIC_ERROR_MODEL_ACCESS_DENIED` for
#     `veo_3_1_t2v_lite_low_priority` on a Pro account. That is a PLAN error, so
#     the RPC parses Veo text-to-video keys. Veo t2v is not missing from this
#     transport; this account simply lacks that key;
#   * inferred — the `_portrait` variants are left out, because aspect became
#     its own payload slot and the accepted image-to-video set dropped its
#     portrait names the same way;
#   * inferred — the duration rides in the key, exactly as the exe spells it
#     (`_4s`, `_6s`, nothing for 8s). No t2v key has a 10s form.
#
# **Nothing here is verified against a live dispatch.** That is safe in the one
# direction that matters: a name Flow does not accept comes back as a refusal
# that costs nothing, so a wrong entry in this table can waste a round trip and
# never a credit. What it cannot do is silently bill — Google answers unknown and
# unheld keys with the same plan error rather than folding them onto a default.
VEO_T2V_LANES: dict[str, dict[int, str]] = {
    # The unbilled queue. Denied on Pro, measured — kept because a different
    # plan may hold it and because refusing it locally would hide that.
    "lite_relaxed": {
        4: "veo_3_1_t2v_lite_4s_low_priority",
        6: "veo_3_1_t2v_lite_6s_low_priority",
        8: "veo_3_1_t2v_lite_low_priority",
    },
    "lite": {
        4: "veo_3_1_t2v_lite_4s",
        6: "veo_3_1_t2v_lite_6s",
        8: "veo_3_1_t2v_lite",
    },
    "fast": {
        4: "veo_3_1_t2v_fast_4s",
        6: "veo_3_1_t2v_fast_6s",
        # The one slot in this table still unmeasured. The exe has BOTH
        # `veo_3_1_t2v_fast` and `veo_3_1_t2v_fast_ultra`, and the only way to
        # tell which this transport knows is to send one on an account that
        # holds it — which generates a clip and bills.
        #
        # Evidence for each: the 4s and 6s names above are base forms, so the
        # family is internally consistent without `_ultra`; but the accepted
        # image-to-video fast key IS `veo_3_1_i2v_s_fast_ultra`. The base form is
        # sent because a wrong guess here costs a free, named refusal — Flow
        # answers `[5]` NOT_FOUND for a name it does not know — and the fix is
        # this single line. If that refusal ever appears, try `_fast_ultra`.
        8: "veo_3_1_t2v_fast",
    },
    # `fast_relaxed` is NOT here, and that is a measurement rather than an
    # omission. All three of its names — `veo_3_1_t2v_fast_4s_relaxed`,
    # `_6s_relaxed`, `_fast_ultra_relaxed` — answered `[5]` NOT_FOUND on
    # 19/09/2026. They shipped in this table for a few hours because the table
    # was built from the exe without re-reading the rule written one section
    # above: the batch path does not know ANY name carrying `_relaxed`,
    # `portrait` or `_fl`. Measured across four RPCs, no exceptions.
}

#: Lengths Veo text-to-video has keys for. Omni's 10s has no Veo counterpart, so
#: asking for it on a Veo lane is refused rather than rounded — the duration is
#: part of the key, and the nearest one bills a different clip.
VEO_T2V_DURATIONS = (4, 6, 8)

#: Lanes with a Veo name in the exe that this transport does NOT know, and why.
#: Offered by the registry with the reason attached, because the labels are part
#: of the UI contract and someone who saw them in the packaged tool will come
#: looking for them.
REFUSED_T2V_LANES: dict[str, str] = {
    "fast_relaxed": (
        "cả ba tên đều có hậu tố `_relaxed`, mà đường batch trả [5] NOT_FOUND "
        "cho mọi tên `_relaxed` (đo 19/09 trên 4 RPC)"
    ),
    "quality": "exe không có key t2v nào cho Quality",
}


# ── Veo reference-to-video ─────────────────────────────────────────────────
#
# The character port was Omni-or-refused, on the belief that Veo's r2v family
# had no payload on this transport. Measured 19/09/2026: `MZZa6b` answers
# `PUBLIC_ERROR_MODEL_ACCESS_DENIED` for `veo_3_1_r2v_lite_low_priority` — a
# PLAN error, so the RPC parses the name.
#
# Exactly one of the exe's four r2v names survives here. The other three carry
# `portrait` and answered `[5]` NOT_FOUND, which is the same rule every other
# family follows: no `portrait`, no `_relaxed`, no `_fl`.
#
# That one key matters out of proportion to its size, because a character-port
# board on the free lane was being refused and its only alternative was Omni at
# 15-30 credits — precisely the substitution this build exists to avoid.
#
# Its PRICE, though, is **not measured**. `_low_priority` is the free-queue
# marker everywhere else on this transport, which is an inference and not a
# reading: the 19/09 probe established only that `MZZa6b` understands the name
# and that THIS plan does not hold it, so this account cannot run it to find
# out. That is why the key is absent from `ZERO_CREDIT_MODEL_KEYS` and why
# `routes/estimate.py` quotes it as unpriced rather than free — a free label is
# what lets the review loop re-run a clip unasked.
VEO_R2V_LANES: dict[str, str] = {
    "lite_relaxed": "veo_3_1_r2v_lite_low_priority",
}


def resolve_r2v_plan(quality: Optional[str]) -> dict[str, Any]:
    """``{model_key, error}`` for a Veo reference-to-video dispatch.

    Only the free lane exists here, so an unrecognised one is refused rather
    than folded — folding would reach Omni, and Omni bills.
    """
    lane = (quality or "").strip().lower()
    key = VEO_R2V_LANES.get(lane)
    if key:
        return {"model_key": key, "error": None}
    return {
        "model_key": None,
        "error": (
            f"{UNSUPPORTED_PREFIX}veo_r2v_lane_{lane or 'unset'}: đường mới chỉ "
            f"nhận {sorted(VEO_R2V_LANES)} cho họ r2v của Veo (các tên khác đều "
            "có `portrait` và bị trả [5]). Dùng làn OMNI nếu muốn chạy — "
            "CÓ tính credit."
        ),
    }


def resolve_character_port_plan(
    quality: Optional[str],
    duration_s: Optional[int],
    resolution: Optional[str] = None,
) -> dict[str, Any]:
    """``{model_key, family, error}`` for a dispatch off the character port.

    One function because the quote and the run answered differently and the gap
    billed. The worker's rule is "an absent lane still means Omni" — that is what
    the Settings-level OMNI choice sends, so it is a choice rather than a
    default. The estimate instead read an absent lane as `DEFAULT_VIDEO_QUALITY`
    ("lite"), found no Veo reference key for it, and quoted "sẽ bị TỪ CHỐI,
    không tốn credit". Measured on the shipped `character_drama` archetype:
    quoted 0 credits, dispatched 3 × 25.

    Every character board the canvas builds landed in that gap, because the
    "Dựng dạng" button sends only `scene_count` and the archetype omits
    `quality`. So the coercion to a default belongs to the families that have
    one, not to this one: here an unset lane is Omni, and only a *named* Veo lane
    goes looking for a Veo key.
    """
    lane = (quality or "").strip().lower() or None
    if lane and lane != "omni":
        plan = resolve_r2v_plan(lane)
        return {
            "model_key": plan["model_key"],
            "family": "veo_r2v",
            "error": plan["error"],
        }
    if not isinstance(duration_s, int) or isinstance(duration_s, bool):
        # The duration is part of Omni's key, so there is no safe default: a
        # substituted length bills a different clip than the board asked for.
        return {"model_key": None, "family": "omni",
                "error": f"invalid_duration_{duration_s!r}"[:200]}
    try:
        key = omni_model_key("references", duration_s, resolution or "720p")
    except ValueError as exc:
        return {"model_key": None, "family": "omni", "error": str(exc)[:200]}
    return {"model_key": key, "family": "omni", "error": None}


def resolve_t2v_plan(quality: Optional[str], duration_s: int) -> dict[str, Any]:
    """``{model_key, error}`` for a Veo text-to-video dispatch.

    Refuses rather than folding, for the same reason the image-to-video resolver
    does: the lanes differ in price, and the cheap ones are the ones a board
    picks on purpose.
    """
    lane = (quality or "").strip().lower()
    out: dict[str, Any] = {"model_key": None, "error": None}
    keys = VEO_T2V_LANES.get(lane)
    if keys is None:
        why = REFUSED_T2V_LANES.get(lane)
        detail = f" — {why}" if why else ""
        out["error"] = (
            f"{UNSUPPORTED_PREFIX}t2v_lane_{lane or 'unset'}{detail}. Veo "
            f"text-to-video có {sorted(VEO_T2V_LANES)}; OMNI là lựa chọn còn "
            "lại (có tính credit)."
        )
        return out
    key = keys.get(duration_s)
    if key is None:
        out["error"] = (
            f"invalid_duration_{duration_s}: làn Veo {lane} chỉ có "
            f"{sorted(keys)} giây."
        )
        return out
    out["model_key"] = key
    return out

# Returned when Flow accepted the dispatch but named no operation. ONE spelling,
# shared with the worker's retry rule: the two used to disagree, so the rule
# that makes an empty dispatch retryable was attached to a string nothing ever
# emitted.
NO_OPERATIONS_ERROR = "no_operations_in_response"


def resolve_video_plan(
    quality: Optional[str],
    aspect_ratio: str,
    *,
    start_end: bool = False,
) -> dict[str, Any]:
    """``{model_key, error, substitutions, effective_quality}`` for a Veo i2v.

    ``model_key`` is None and ``error`` is set whenever this path cannot serve
    the request. ``substitutions`` is always empty — it exists because
    `_poll_video_dispatch` and the review loop read it, and it stays empty
    because this build refuses rather than substitutes. Keeping the key means
    the UI code that reports a swap needs no change if a future capture ever
    makes one honest.
    """
    requested = (quality or DEFAULT_VIDEO_QUALITY).strip().lower()
    plan: dict[str, Any] = {
        "model_key": None,
        "error": None,
        "substitutions": [],
        "effective_quality": requested,
    }
    if start_end:
        plan["error"] = (
            UNSUPPORTED_PREFIX + "veo_start_end: Veo's first→last frame payload was "
            "never captured on the batch path. Dùng OMNI (first+last) thay."
        )
        return plan
    if requested in REFUSED_VIDEO_LANES:
        plan["error"] = (
            f"{UNSUPPORTED_PREFIX}lane_{requested}: "
            f"{REFUSED_VIDEO_LANES[requested]}"
        )
        return plan
    key = BATCH_VIDEO_LANES.get(requested)
    if key is None:
        plan["error"] = (
            f"{UNSUPPORTED_PREFIX}lane_{requested}: làn này không có trên đường mới. "
            f"Có: {sorted(BATCH_VIDEO_LANES)}"
        )
        return plan
    try:
        fb.resolve_video_aspect(aspect_ratio)
    except ValueError as exc:
        # An unrecognised aspect must not be coerced to landscape: a "9:16" from
        # a stale caller became a landscape clip the caller paid for.
        plan["error"] = f"invalid_aspect_{aspect_ratio}: {exc}"[:200]
        return plan
    plan["model_key"] = key
    return plan


def resolve_video_model(
    quality: Optional[str], aspect_ratio: str, *, start_end: bool = False
) -> Optional[str]:
    """Model key only — see ``resolve_video_plan`` for the refusal reason."""
    return resolve_video_plan(quality, aspect_ratio, start_end=start_end)["model_key"]


def omni_model_key(mode: str, duration_s: int, resolution: str = "720p") -> str:
    """Flow's Omni 1.1 Flash key for a mode + duration + resolution.

    Live-captured by flowkit on 2026-09-14. Raises rather than guessing: the
    duration is part of the key, so a wrong one bills a different clip length
    than the caller asked for.
    """
    if duration_s not in OMNI_VALID_DURATIONS:
        raise ValueError(
            f"Omni duration {duration_s}s unsupported (valid: {list(OMNI_VALID_DURATIONS)})"
        )
    res = str(resolution or "720p").strip().lower()
    if res not in OMNI_VALID_RESOLUTIONS:
        raise ValueError(f"Omni resolution must be one of {list(OMNI_VALID_RESOLUTIONS)}")
    suffix = "_360p" if res == "360p" else ""
    if mode == "first_frame":
        return f"abra_i2v_{duration_s}s{suffix}"
    if mode == "first_last":
        return f"omni_flash_i2v_{duration_s}s_first_last{suffix}"
    if mode == "references":
        return f"abra_r2v_{duration_s}s{suffix}"
    if mode == "text":
        return f"abra_t2v_{duration_s}s{suffix}"
    raise ValueError(f"unknown Omni mode {mode!r}")


# project_id must match the shape Google Flow returns (UUID-ish). Validated at
# handler boundaries to prevent path traversal into arbitrary API URLs.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

def is_omni_model_key(model_key: str) -> bool:
    """True when a clip came from Omni, which Flow will not extend.

    One definition, because two callers need it at two different moments: the
    handler refuses BEFORE creating a scene (a scene for a clip that can never be
    extended is a pointless write on the user's Flow project), and the SDK refuses
    again at dispatch because it is a public surface. Two copies of this rule
    would drift, and the half that drifted would spend a submit.
    """
    return (model_key or "").strip().lower().startswith("abra_")


def _dispatch_error(operations: list[str], err: str) -> dict[str, Any]:
    """Error return for a video dispatch, naming the operations Flow DID create.

    Credit safety depends on this. The worker refuses to retry a request whose
    result already names operations, because an operation is a render that is
    running and has been charged for. A batch of four that got three
    operations and then an error must carry those three out, or the retry
    gate has nothing to refuse and the three get billed twice.

    (It used to dig the names out of a REST response body. Now the caller
    holds them, because each one came back from its own RPC.)
    """
    return {"raw": None, "error": err, "operation_names": list(operations)}


def _pending(
    name: str,
    *,
    complaint: Optional[str] = None,
    media_id: Optional[str] = None,
) -> dict[str, Any]:
    """One not-finished operation in the shape the poll loop reads.

    ``complaint`` is the operation poll's own grumble, which is a diagnostic
    and not a failure: Flow has been measured saying "Media not found." about
    an operation that then delivered a finished clip. It rides out under its
    own key so nothing mistakes it for ``error``, which the worker treats as
    terminal.

    ``media_id`` appears once the listing has answered. Its presence says
    "found, still rendering", which is a different thing from "not found yet"
    and the difference is what a stuck poll needs in the log.
    """
    entry: dict[str, Any] = {
        "name": name,
        "done": False,
        "media_entries": [],
        "status": None,
        "error": None,
    }
    if complaint:
        entry["complaint"] = complaint
    if media_id:
        entry["media_id"] = media_id
    return entry


def is_valid_project_id(project_id: str) -> bool:
    return bool(_PROJECT_ID_RE.fullmatch(project_id))

# Image variants per dispatch are capped as defence-in-depth -- the UI clamps
# to 4 too. Any value above this is coerced down.
MAX_VARIANT_COUNT = 4

#: Pause between the video submits of one batch. Each submit mints its own
#: single-use captcha, and a burst of four looks different to Google's abuse
#: detection than a UI does -- `PUBLIC_ERROR_UNUSUAL_ACTIVITY` is what that
#: difference costs. One second is the floor the capture notes give.
VIDEO_SUBMIT_GAP_S = 1.0

#: How many operations the poll caches hold before being dropped as a set. A
#: ceiling, not a policy: the operation poll is a workable fallback when a
#: project id has been forgotten, so losing the cache costs one extra RPC and
#: never a result.
OP_CACHE_LIMIT = 512

#: The project listing is consulted this often even when the operation poll
#: reports nothing. It is the 17 MB call, and the authority -- an operation has
#: been measured reporting no status at all on a job the listing already knew
#: was finished, so never asking would strand that clip until the timeout.
LISTING_EVERY_NTH_ROUND = 3

# What used to sit here: `_client_context`, `_TRPC_HEADERS`, `_API_HEADERS`,
# `_generate_images_url`, `CAPTCHA_*` duplicates of `fb.CAPTCHA_*`, and
# `_MAX_VIDEO_OPS`. The migrated payload has no clientContext block at all --
# `fb._context(project_id)` is the whole of it -- so there is no slot a tier
# could be written into, correctly or otherwise. That is a stronger version of
# the guarantee `_client_context` was carrying: not "omit a tier we do not
# know" but "there is nowhere to put one".


class FlowSDK:
    """High-level helpers on top of ``flow_client``. Stateless."""

    def __init__(self, client: Optional[FlowClient] = None) -> None:
        self._client = client or flow_client
        # Poll state. An operation's media id lives in the PROJECT listing, and
        # the listing needs a project id -- which a decayed operation handle no
        # longer carries. So the project is remembered at dispatch, the media id
        # is remembered once found (the listing is the 17 MB call), and the round
        # counter paces how often the listing gets asked at all.
        self._op_projects: dict[str, str] = {}
        self._op_media: dict[str, str] = {}
        self._op_polls: dict[str, int] = {}

    async def _payload(
        self,
        rpcid: str,
        freq: str,
        captcha_action: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """One RPC, unwrapped to its inner payload. Raises on anything else.

        The two failure modes are deliberately different exceptions:
        `FlowBatchError` for "the bridge could not deliver this" and
        `fb.RpcError` for "Flow answered with an error slot" — the second
        carries Flow's own code, and `[8]` in particular is transient.
        """
        result = await self._client.batch_rpc(
            rpcid, freq, captcha_action, timeout=timeout
        )
        if result.get("error"):
            raise fb.FlowBatchError(f"{rpcid}: {result['error']}")
        payload = fb.first_payload(result.get("data") or "", rpcid)
        # Five replies carry the balance for free in slot 1, the poll among
        # them — so the meter stays current during a render without one extra
        # call. Only those five are read; slot 1 means something else on the
        # rest, and a wrong balance is the one number a user spends against.
        flow_credits.observe(rpcid, payload)
        return payload

    async def get_credits(self) -> dict:
        """The account's live credit balance, or None when unreadable.

        A real read now, not the configured default P13 had to fall back to:
        the ``nzlxg`` RPC (community-captured) answers on the same cookie-auth
        batchexecute path everything else uses. Zero credit, no captcha.
        Returning None on any shape drift is deliberate — a stale or wrong
        number on the meter is worse than an honest "unknown".
        """
        freq = fb.credits_request()
        result = await self._client.batch_rpc(fb.RPC_CREDITS, freq, None)
        if result.get("error"):
            raise fb.FlowBatchError(f"nzlxg: {result['error']}")
        credits = fb.read_credits(result.get("data") or "")
        flow_credits.remember(credits)
        return {"credits": credits, "raw_present": credits is not None}

    async def create_character(self, project_id: str, name: str) -> dict:
        """Create a Character in a Flow project; returns its ``character_id``.

        This closes the gap P7b had to leave open. The store could only ever
        REFERENCE an entity the user had made by hand in Flow — the packaged
        tool does the same, and says so: "Nhân vật CHƯA ACTIVE trong Google
        Flow project hiện tại. Hãy tạo lại nhân vật..." Now the app can make it.

        Zero credit and no captcha: it writes a record, it does not generate.
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("character name is required")
        if not project_id:
            raise ValueError("project_id is required")
        freq = fb.create_character_request(project_id, clean)
        result = await self._client.batch_rpc(fb.RPC_CREATE_CHARACTER, freq, None)
        if result.get("error"):
            raise fb.FlowBatchError(f"C4BZMd: {result['error']}")
        return fb.read_created_character(result.get("data") or "")

    async def upscale_video(
        self,
        operation_id: str,
        media_id: str,
        project_id: str,
        *,
        aspect: Any = None,
        resolution: str = "1080p",
    ) -> dict:
        """Upscale a finished clip to 1080p. Returns the shape the poll reads.

        Two ids, not one, and they are NOT interchangeable: slot 0 addresses the
        OPERATION that produced the clip, slot 4 the MEDIA it produced. The
        capture this came from warns that swapping them gets the request accepted
        and then failed with NOT_FOUND — accepted-and-useless, which from outside
        looks exactly like success.

        No captcha: the payload has no slot for one, so minting a token would
        burn a single-use credential for nothing and edge the account towards
        `PUBLIC_ERROR_UNUSUAL_ACTIVITY`.

        4K is refused inside the builder — two captures disagree about its tier
        slot and 4K costs 50 credits, so guessing spends the user's money to find
        out who was right.

        The price of 1080p is NOT known. This build has never measured it, and
        the first real run is what measures it (read the balance, run, read
        again). Nothing here claims it is free.
        """
        if not operation_id or not isinstance(operation_id, str):
            return _dispatch_error([], "missing_upscale_operation_id")
        if not media_id or not isinstance(media_id, str):
            return _dispatch_error([], "missing_upscale_media_id")
        try:
            freq = fb.upscale_request(
                operation_id, media_id, project_id,
                aspect=aspect if aspect is not None else fb.VIDEO_ASPECT_LANDSCAPE,
                resolution=resolution,
            )
            result = await self._client.batch_rpc(fb.RPC_UPSCALE, freq, None, timeout=120)
            if result.get("error"):
                raise fb.FlowBatchError(f"p0UkFb: {result['error']}")
            upscaled_id = fb.read_upscale_submit(result.get("data") or "")
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            return _dispatch_error([], _error_text(exc)[:200])
        if not upscaled_id:
            # The submit was accepted and we cannot address what it started.
            # Reporting success here would strand a render that is already
            # running — and being billed, at a price nobody has measured yet.
            return _dispatch_error([], "upscale_no_operation_returned")
        self._remember_operation(upscaled_id, project_id)
        return {
            "raw": {"operations": [upscaled_id]},
            "operation_names": [upscaled_id],
            "model_key": fb.UPSCALE_MODEL_1080P,
            "model_substitutions": {},
        }

    async def create_scene(self, project_id: str, media_ids: list[str],
                           aspect: Any = None) -> dict:
        """Wrap finished clips into a scene. Returns scene id + the clone id.

        Zero credit — it groups existing media, it renders nothing. The clone it
        hands back is what `extend_video` must start from.
        """
        if not media_ids:
            raise ValueError("create_scene needs at least one media id")
        freq = fb.create_scene_request(
            project_id, media_ids,
            aspect=aspect if aspect is not None else fb.VIDEO_ASPECT_LANDSCAPE,
        )
        result = await self._client.batch_rpc(fb.RPC_CREATE_SCENE, freq, None)
        if result.get("error"):
            raise fb.FlowBatchError(f"rqZuUc: {result['error']}")
        return fb.read_created_scene(result.get("data") or "")

    async def extend_video(
        self,
        prompt: str,
        project_id: str,
        scene_id: str,
        source_media_id: str,
        *,
        duration_s: float = 8.0,
        position: int = 1,
        aspect: Any = None,
        source_model_key: str = "",
        paid_lane: bool = False,
    ) -> dict:
        """Extend a clip. Refuses an Omni source instead of paying to learn.

        Flow greys the extend button out for Omni (`abra_*`) clips — only Veo can
        be extended. Dispatching anyway costs a round trip at best and a billed
        failure at worst, so the refusal names the reason the way every other
        unsupported lane here does.

        The free lane is the default. `paid_lane=True` is the only way to spend,
        and nothing infers it.
        """
        if is_omni_model_key(source_model_key):
            raise fb.FlowBatchError(
                "unsupported_extend_omni_source: Flow chỉ nối được clip Veo; "
                f"clip này là “{source_model_key}” (Omni) nên nút nối bị mờ"
            )
        freq = fb.extend_video_request(
            prompt, project_id, scene_id, source_media_id,
            duration_s=duration_s, position=position,
            aspect=aspect if aspect is not None else fb.VIDEO_ASPECT_LANDSCAPE,
            paid_lane=paid_lane,
        )
        model_key = fb.EXTEND_MODEL_PAID if paid_lane else fb.EXTEND_MODEL_FREE
        try:
            result = await self._client.batch_rpc(
                fb.RPC_EXTEND_VIDEO, freq, fb.CAPTCHA_VIDEO, timeout=120
            )
            if result.get("error"):
                raise fb.FlowBatchError(f"fZytfe: {result['error']}")
            operation_id = fb.read_extend_submit(result.get("data") or "")
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            return _dispatch_error([], _error_text(exc)[:200])
        if not operation_id:
            return _dispatch_error([], "extend_no_operation_returned")
        self._remember_operation(operation_id, project_id)
        # Same shape `gen_video` returns, so the poll, the review loop and the
        # money guard all read an extension the way they read any other clip.
        return {
            "raw": {"operations": [operation_id]},
            "operation_names": [operation_id],
            "model_key": model_key,
            "model_substitutions": {},
        }

    # ── project listing ───────────────────────────────────────────────────
    #
    # No RPC for this was captured off the new frontend, and the tRPC endpoint
    # these used (`project.searchUserProjects`) went unauthenticated with the
    # migration. Both keep their signatures and answer with a named refusal, so
    # `routes/flow_projects` degrades to "cannot check" rather than reporting an
    # empty project list — which would read as "you have no projects" and invite
    # someone to recreate them all.

    async def search_user_projects(
        self,
        cursor: Optional[str] = None,
        page_size: int = 20,
        tool: str = "PINHOLE",
    ) -> dict[str, Any]:
        return {"raw": None, "projects": [], "error": UNSUPPORTED_PROJECT_LISTING}

    async def list_user_projects_all(
        self, tool: str = "PINHOLE", max_pages: int = 10
    ) -> dict[str, Any]:
        return {
            "projects": [],
            "truncated": False,
            "error": UNSUPPORTED_PROJECT_LISTING,
        }

    async def create_project(
        self, title: str, tool: str = "PINHOLE"
    ) -> dict[str, Any]:
        """Make a Flow project for a board, or fall back to the pinned one.

        Returns ``{raw, project_id}`` or ``{raw, error}`` — the shape
        `routes/projects.ensure_board_project` already reads.

        The RPC (`jHPbke`) comes from a flowkit commit that was reverted there
        together with an unrelated feature, not for failing; its message records
        a live create/delete round trip. If Flow refuses it anyway, the pinned
        ``FLOW_PROJECT_ID`` is used, because the alternative is a board that
        cannot generate at all. A project the user pinned by hand is a
        deliberate choice; silently sharing one board's project with another
        would not be, which is why the fallback says so in the result.

        ``tool`` is honoured only by its absence: the captured payload carries
        no tool slot, so a caller asking for something other than the surface
        default is refused rather than quietly served the default.
        """
        if tool and tool != "PINHOLE":
            return {"raw": None, "error": f"unsupported_on_batch_tool_{tool}"}

        try:
            payload = await self._payload(
                fb.RPC_CREATE_PROJECT, fb.create_project_request(title), timeout=60
            )
            return {"raw": payload, "project_id": fb.read_created_project_id(payload)}
        except (fb.FlowBatchError, fb.RpcError) as exc:
            pinned = _pinned_project_id()
            if pinned:
                logger.warning(
                    "create_project failed (%s) — using the pinned FLOW_PROJECT_ID",
                    exc,
                )
                return {
                    "raw": None,
                    "project_id": pinned,
                    "pinned_fallback": True,
                }
            return {"raw": None, "error": f"create_project_failed: {exc}"[:200]}

    # ── video generation (async via operations) ────────────────────────────
    async def gen_video(
        self,
        prompt: str,
        project_id: str,
        start_media_id: Optional[str] = None,
        aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE",
        paygate_tier: Optional[str] = None,
        scene_id: Optional[str] = None,
        start_media_ids: Optional[list[str]] = None,
        video_quality: Optional[str] = None,
        end_media_id: Optional[str] = None,
        duration_s: Optional[int] = None,
        resolution: str = "720p",
    ) -> dict[str, Any]:
        """Image-to-video. ``{raw, operation_names, model_key, model_substitutions}``.

        Two families ride this one method. A Veo lane (`lite_relaxed` / `lite` /
        `fast`) sends `eb1hJf` with a Veo key. The `omni` lane sends Omni 1.1
        Flash, where the duration is part of the key, and an ``end_media_id``
        turns it into first-to-last frame via `nprQif`.

        **Veo's own first-to-last is refused.** Its payload was never captured,
        and guessing would produce a clip that ignores the end frame the user
        picked and charges for it anyway.

        ``start_media_ids`` dispatches one operation per source image, so a
        4-variant upstream yields 4 clips from one call. Order is preserved: the
        caller pairs slot *i* of ``operation_names`` with source *i*.

        ``paygate_tier`` is accepted and ignored -- the migrated payload has no
        tier slot. It stays because every caller passes it and the request row
        records it as a label.
        """
        lane = (video_quality or "").strip().lower() or DEFAULT_VIDEO_QUALITY
        sources = [m for m in (start_media_ids or []) if isinstance(m, str) and m]
        if not sources and isinstance(start_media_id, str) and start_media_id:
            sources = [start_media_id]
        if not sources:
            return {"raw": None, "error": "missing_start_media_id"}
        if end_media_id and len(sources) > 1:
            # One destination frame shared by four sources is never what the
            # caller means, and the batch would bill four clips to find out.
            return {"raw": None, "error": "end_media_id_requires_single_source"}

        omni = lane == "omni"
        try:
            if omni:
                mode = "first_last" if end_media_id else "first_frame"
                model_key = omni_model_key(mode, duration_s or 8, resolution)
            else:
                plan = resolve_video_plan(
                    lane, aspect_ratio, start_end=bool(end_media_id)
                )
                if plan["error"]:
                    return {"raw": None, "error": plan["error"]}
                model_key = fb.check_video_model(plan["model_key"])
        except ValueError as exc:
            return {"raw": None, "error": str(exc)[:200]}

        operations: list[str] = []
        for index, media_id in enumerate(sources):
            if index:
                await asyncio.sleep(VIDEO_SUBMIT_GAP_S)
            try:
                if omni and end_media_id:
                    rpcid = fb.RPC_GEN_VIDEO_FIRST_LAST
                    freq = fb.omni_first_last_request(
                        prompt, project_id, media_id, end_media_id,
                        duration_s=duration_s or 8, resolution=resolution,
                        aspect=aspect_ratio,
                    )
                elif omni:
                    rpcid = fb.RPC_GEN_VIDEO
                    freq = fb.omni_first_frame_request(
                        prompt, project_id, media_id,
                        duration_s=duration_s or 8, resolution=resolution,
                        aspect=aspect_ratio,
                    )
                else:
                    rpcid = fb.RPC_GEN_VIDEO
                    freq = fb.video_request(
                        prompt, project_id, media_id,
                        aspect=aspect_ratio, model=model_key,
                    )
                operation = fb.read_operation(
                    await self._payload(rpcid, freq, fb.CAPTCHA_VIDEO, timeout=120)
                )
            except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
                return _dispatch_error(operations, _error_text(exc)[:200])
            operations.append(operation.operation_id)
            self._remember_operation(operation.operation_id, project_id)

        if not operations:
            return {"raw": None, "error": NO_OPERATIONS_ERROR}
        return {
            "raw": {"operations": operations},
            "operation_names": operations,
            # The key travels because the lane label does not say what was
            # billed, and the review loop asks this before spending again.
            "model_key": model_key,
            # Always empty: this build refuses a lane it cannot serve rather
            # than substituting one that costs money. The key stays so the UI
            # that reports a swap needs no change if a capture ever makes one
            # honest.
            "model_substitutions": [],
        }

    async def gen_video_text(
        self,
        prompt: str,
        project_id: str,
        aspect_ratio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE",
        paygate_tier: Optional[str] = None,
        video_quality: Optional[str] = None,
        duration_s: int = 8,
        variant_count: int = 1,
        scene_id: Optional[str] = None,
        resolution: Optional[str] = None,
    ) -> dict[str, Any]:
        """Text-to-video, on a Veo lane or on Omni.

        ``{raw, workflows, operation_names, model_key, model_substitutions}``.

        **Veo lanes dispatch now; they used to be refused.** The refusal was
        written believing `YhhmEf` had no Veo payload. Measured live on
        19/09/2026 that is wrong: `veo_3_1_t2v_lite_low_priority` comes back as
        `PUBLIC_ERROR_MODEL_ACCESS_DENIED`, a PLAN error, so the RPC parses Veo
        text-to-video keys and this account merely lacks that one.

        So the lane a board asked for is the lane that is sent. That is the whole
        point: refusing meant a board wanting Veo lite either got nothing or got
        talked into Omni at 15-30 credits, and neither is what it asked for.

        The safety here is Google's, not ours: a key Flow does not accept — a
        stale name, a lane this plan lacks — answers with the same plan error and
        costs nothing. There is no fold onto a default, so a wrong key cannot
        quietly bill for a different model. What a wrong key CAN do is waste a
        round trip, which is why `VEO_T2V_LANES` records how each name was
        arrived at.

        Returns ``workflows`` rather than operations: this submit answers with a
        media/workflow record, and those poll through `as29s` instead of the
        operation RPC. ``check_async`` takes both and the caller cannot tell.
        """
        lane = (video_quality or "").strip().lower()
        if lane == "omni":
            try:
                # `resolution` was sent by the frontend, read by nobody, and this
                # call left the third positional at its `720p` default — so a user
                # who chose 360p in Settings got 720p, while the Settings hint said
                # "chỉ áp dụng cho làn OMNI" as if it applied. `omni_model_key`
                # has built `abra_t2v_<N>s_360p` all along; only this was missing.
                model_key = omni_model_key("text", duration_s, resolution or "720p")
            except ValueError as exc:
                return {"raw": None, "error": str(exc)[:200]}
        else:
            plan = resolve_t2v_plan(lane or DEFAULT_VIDEO_QUALITY, duration_s)
            if plan["error"]:
                return {"raw": None, "error": plan["error"]}
            model_key = plan["model_key"]

        count = (
            variant_count
            if isinstance(variant_count, int) and variant_count > 0
            else 1
        )
        workflows: list[dict[str, Any]] = []
        for index in range(count):
            if index:
                await asyncio.sleep(VIDEO_SUBMIT_GAP_S)
            try:
                submitted = fb.read_text_video_submit(
                    await self._payload(
                        fb.RPC_GEN_VIDEO_TEXT,
                        fb.text_video_request(
                            prompt, project_id,
                            aspect=aspect_ratio, model=model_key,
                        ),
                        fb.CAPTCHA_VIDEO,
                        timeout=120,
                    )
                )
            except (fb.FlowBatchError, fb.RpcError) as exc:
                if workflows:
                    # Keep what was submitted -- those renders are charged for.
                    logger.warning(
                        "text-video variant %d/%d failed after %d accepted: %s",
                        index + 1, count, len(workflows), exc,
                    )
                    break
                return {"raw": None, "error": _error_text(exc)[:200]}
            workflows.append({
                "name": submitted["workflow_id"],
                "primary_media_id": submitted["media_id"],
            })

        if not workflows:
            return {"raw": None, "error": NO_OPERATIONS_ERROR}
        return {
            "raw": {"workflows": workflows},
            "operation_names": [w["name"] for w in workflows],
            "workflows": workflows,
            "model_key": model_key,
            "model_substitutions": [],
        }

    async def gen_video_omni(
        self,
        prompt: str,
        project_id: str,
        ref_media_ids: list[str],
        duration_s: int,
        aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT",
        paygate_tier: Optional[str] = None,
        seed: Optional[int] = None,
        model_key: Optional[str] = None,
        ref_entity_ids: Optional[list[str]] = None,
        resolution: str = "720p",
    ) -> dict[str, Any]:
        """Reference-conditioned video (Ingredients) via `MZZa6b`.

        **Character Entities are refused.** The captured payload has no
        `referenceEntities` slot, and dropping them quietly would render
        somebody else -- a different character, at full price, looking like
        success. The reference-image path still works, so a board built on
        uploaded stills is unaffected.

        **A Veo lane here is refused too.** Veo's own r2v family, which is where
        the packaged tool's 0-credit reference lanes live, has no captured
        payload. Serving Omni instead would bill 15-30 credits to a board that
        asked for a free lane.
        """
        entities = [
            e.strip() for e in (ref_entity_ids or [])
            if isinstance(e, str) and e.strip()
        ]
        if entities:
            return {
                "raw": None,
                "error": (
                    f"{UNSUPPORTED_PREFIX}reference_entities: payload Omni trên "
                    "đường mới không có ô entity. Bỏ entity thì ảnh tham chiếu "
                    "vẫn chạy — nhưng bỏ lặng lẽ là dợng sai nhân vật rồi tính tiền."
                ),
            }
        # A key outside the two known families is refused by name. The prefix
        # guard this replaces refused Veo's reference key too, on the belief that
        # the family had no payload here — which the 19/09 and 20/09 measurements
        # both contradict, while three comments in this file already said so.
        # `VEO_R2V_LANES` is the accepted set, not a prefix, because a prefix
        # would also wave through a name nobody has measured.
        if (
            model_key
            and not model_key.startswith(("abra_", "omni_flash_"))
            and model_key not in set(VEO_R2V_LANES.values())
        ):
            return {
                "raw": None,
                "error": (
                    f"{UNSUPPORTED_PREFIX}veo_r2v_{model_key}: tên này chưa được "
                    f"đo trên `MZZa6b`. Chỉ {sorted(VEO_R2V_LANES.values())} của "
                    "Veo và họ OMNI là đã đo — xem docs/flow-capture.md."
                ),
            }
        refs = [m for m in (ref_media_ids or []) if isinstance(m, str) and m]
        if not refs:
            return {"raw": None, "error": "missing_ref_media_ids"}

        try:
            key = model_key or omni_model_key("references", duration_s, resolution)
            operation = fb.read_operation(
                await self._payload(
                    fb.RPC_GEN_VIDEO_REFERENCES,
                    fb.omni_reference_video_request(
                        prompt, project_id, refs,
                        duration_s=duration_s, resolution=resolution,
                        aspect=aspect_ratio,
                        # The key the caller resolved, on the wire. Without this
                        # the builder rebuilt its own from duration+resolution
                        # while the result reported the caller's — and the review
                        # loop reads the reported one to decide about re-running.
                        model=key,
                    ),
                    fb.CAPTCHA_VIDEO,
                    timeout=120,
                )
            )
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            return {"raw": None, "error": _error_text(exc)[:200]}

        self._remember_operation(operation.operation_id, project_id)
        return {
            "raw": {"operations": [operation.operation_id]},
            "operation_names": [operation.operation_id],
            "model_key": key,
            "model_substitutions": [],
        }

    # ── polling ───────────────────────────────────
    #
    # Three signals have to agree before a clip can be downloaded, and they
    # arrive out of order:
    #
    #   * the OPERATION poll (`jwpduf`) says how the job is going -- but it can
    #     sit at no status at all on a job that finished, and a "Media not
    #     found." complaint on it is survivable rather than fatal;
    #   * the PROJECT LISTING (`Zzl0ze`) is what actually gains a media id, and
    #     it is the 17 MB call, so it is asked sparingly and always with
    #     ``match``;
    #   * the MEDIA record (`as29s`) serves the poster image first and grows the
    #     `/video/` url in later.
    #
    # So an operation reports SUCCESSFUL only once a video url exists. Anything
    # short of that is pending, and the caller's own loop owns the timeout.
    # Algorithm ported from flowkit v1.2.0 `_poll_batch_operation`.

    def _remember_operation(self, operation_id: str, project_id: str) -> None:
        """Note which project to look an operation's media up in.

        Bounded, and cleared as one set: these three caches are keyed by the
        same operation ids, so dropping one without the others would leave a
        media id whose project is unknown.
        """
        if not operation_id:
            return
        if len(self._op_projects) > OP_CACHE_LIMIT:
            self._op_projects.clear()
            self._op_media.clear()
            self._op_polls.clear()
        self._op_projects[operation_id] = project_id

    def remember_operations(
        self, operation_names: list[str], project_id: Optional[str]
    ) -> None:
        """Re-seed the project for operations this process did not dispatch.

        `_op_projects` lives only in memory and is filled at dispatch, so an agent
        restart loses it — and without a project id `_find_operation_media` never
        asks the listing and every poll round reports pending forever. That made
        "the run died, I restarted the agent, press RUN Lỗi" — the headline
        recovery path — a ten-minute no-op on an operation that had already been
        paid for.

        The caller passes the project the operations were CREATED in, read back
        from the request row, rather than the board's project today: those can
        differ, and Flow scopes media ids to the project they were made in.
        """
        if not project_id:
            return
        for name in operation_names or []:
            if isinstance(name, str) and name:
                self._remember_operation(name, project_id)

    async def check_async(
        self,
        operation_names: list[str],
        workflows: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """One poll round. ``{raw, operations: [{name, done, media_entries, ...}]}``.

        Same contract as before the migration, so `_poll_video_dispatch` and the
        review loop are untouched. ``workflows`` entries (text-to-video) poll
        straight through the media RPC -- they never had operation handles.
        """
        workflow_names = {
            w["name"] for w in (workflows or [])
            if isinstance(w, dict) and w.get("name")
        }
        summary: list[dict[str, Any]] = []

        for name in operation_names:
            if name in workflow_names:
                continue
            try:
                summary.append(await self._poll_operation(name))
            # Broad on purpose: one unreadable round must not fail a render
            # that is already paid for and may well finish next round.
            except Exception as exc:
                logger.warning("operation %s poll failed: %s", name[:20], exc)
                summary.append(_pending(name, complaint=str(exc)[:200]))

        if workflows:
            summary.extend(await self._poll_workflows(workflows))

        order = {name: i for i, name in enumerate(operation_names)}
        summary.sort(key=lambda op: order.get(op.get("name"), 1 << 30))
        return {"raw": {"operations": summary}, "operations": summary}

    async def _poll_operation(self, operation_id: str) -> dict[str, Any]:
        """Where one operation stands right now."""
        media_id = self._op_media.get(operation_id)
        complaint = None
        if not media_id:
            media_id, complaint = await self._find_operation_media(operation_id)
            if not media_id:
                return _pending(operation_id, complaint=complaint)
            self._op_media[operation_id] = media_id

        try:
            urls = fb.read_media_urls(
                await self._payload(
                    fb.RPC_MEDIA, fb.media_request(media_id), timeout=60
                ),
                media_id,
            )
        except (fb.FlowBatchError, fb.RpcError) as exc:
            return _pending(operation_id, complaint=str(exc)[:200], media_id=media_id)

        if not urls.video:
            # The id landed but the clip is still being written. Downloading now
            # would save the poster still instead of the video -- a real bug
            # once, not a hypothetical.
            return _pending(operation_id, complaint=complaint, media_id=media_id)
        return {
            "name": operation_id,
            "done": True,
            "media_entries": [
                {"media_id": media_id, "url": urls.video, "mediaType": "video"}
            ],
            "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "error": None,
        }

    async def _find_operation_media(
        self, operation_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Ask the operation how it is going, then the listing where its media is.

        The listing is the authority -- the operation poll has been measured
        reporting nothing about a job the listing already knows finished -- but
        it is also the expensive call. So it is consulted when the poll says
        something happened, when the poll is unreadable, or every third round
        regardless.
        """
        rounds = self._op_polls.get(operation_id, 0) + 1
        self._op_polls[operation_id] = rounds

        project_id = self._op_projects.get(operation_id)
        complaint: Optional[str] = None
        worth_looking = rounds % LISTING_EVERY_NTH_ROUND == 0
        try:
            operation = fb.read_operation(
                await self._payload(
                    fb.RPC_OPERATION, fb.operation_request(operation_id), timeout=60
                )
            )
            complaint = operation.error
            project_id = operation.project_id or project_id
            if project_id:
                self._remember_operation(operation_id, project_id)
            worth_looking = worth_looking or operation.done or operation.complained
        except Exception as exc:
            # An operation that decayed to a bare id still shows up in the
            # listing, so a failed poll is a reason to look there, not to stop.
            logger.debug(
                "operation %s poll unreadable (%s), trying the listing",
                operation_id[:20], exc,
            )
            worth_looking = True

        if not worth_looking:
            return None, complaint
        if not project_id:
            return None, complaint or "no project id for the listing lookup"
        return await self._media_id_for(operation_id, project_id), complaint

    async def _media_id_for(
        self, operation_id: str, project_id: str
    ) -> Optional[str]:
        """Find an operation's media id in the project listing.

        Always with ``match``: that payload is past 17 MB and grows with every
        generation, so anything shipping it whole gets truncated and loses
        roughly half of all lookups.
        """
        result = await self._client.batch_rpc(
            fb.RPC_PROJECT_MEDIA,
            fb.project_media_request(project_id),
            match=operation_id,
            timeout=120,
        )
        if result.get("error"):
            raise fb.FlowBatchError(f"{fb.RPC_PROJECT_MEDIA}: {result['error']}")
        raw = result.get("data") or ""
        media_id = fb.find_media_id_in_text(raw, operation_id)
        if media_id or not raw.lstrip().startswith(")]}"):
            return media_id
        # An extension too old to filter hands back the whole envelope. Parse it
        # rather than report "not ready" forever on a clip that is finished.
        try:
            return fb.find_media_id(
                fb.first_payload(raw, fb.RPC_PROJECT_MEDIA), operation_id
            )
        except (fb.FlowBatchError, fb.RpcError, ValueError, IndexError):
            return None

    async def media_download_url(self, media_id: str) -> Optional[str]:
        """Signed download URL for a finished media item, or None.

        None means "not ready", which is exactly the signal the poll loop wants:
        Flow serves the poster image for a video before the clip itself exists,
        so a record with an image and no `/video/` url is a render still in
        flight. Downloading on the id alone saves a still picture.

        The URL is short-lived, so callers download promptly rather than store
        it. (This used to chase a 302 from Flow's tRPC redirect endpoint; the
        `as29s` RPC answers with the url inline.)
        """
        if not isinstance(media_id, str) or not media_id:
            return None
        try:
            payload = await self._payload(
                fb.RPC_MEDIA, fb.media_request(media_id), timeout=60
            )
        except (fb.FlowBatchError, fb.RpcError) as exc:
            logger.debug("media %s not readable yet (%s)", media_id[:12], exc)
            return None
        urls = fb.read_media_urls(payload, media_id)
        return urls.video or urls.image

    async def _poll_workflows(
        self, workflows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """One poll pass for workflow-mode (text-to-video) submissions.

        A signed video url is the completion signal -- Flow only mints one once
        the render has finished -- so there is no separate status call and no
        percentage to read.
        """
        out: list[dict[str, Any]] = []
        for workflow in workflows:
            if not isinstance(workflow, dict):
                continue
            name = workflow.get("name")
            media_id = workflow.get("primary_media_id")
            if not isinstance(name, str) or not name:
                continue
            if not isinstance(media_id, str) or not media_id:
                continue
            url = await self.media_download_url(media_id)
            if url and "/video/" in url:
                out.append({
                    "name": name,
                    "done": True,
                    "media_entries": [
                        {"media_id": media_id, "url": url, "mediaType": "video"}
                    ],
                    "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                    "error": None,
                })
            else:
                # A poster-only record is a render still in flight.
                out.append(_pending(name, media_id=media_id))
        return out

    # ── images ────────────────────────────────────────────────────────────
    #
    # One RPC per variant, each carrying its own single-use captcha. That is not
    # a choice: Flow's own composer does it that way, and there is no "how many"
    # field in the payload. The launch cadence below is copied from the UI too —
    # a burst of four looks different to Google's abuse detection than a UI
    # does, and `PUBLIC_ERROR_UNUSUAL_ACTIVITY` is what that difference costs.

    async def _image_wave(
        self,
        prompts: list[str],
        project_id: str,
        aspect_ratio: str,
        model_name: str,
        ref_media_ids: Optional[list[str]],
        base_media_id: Optional[str],
        seed: Optional[int],
    ) -> tuple[list[Any], dict[int, BaseException]]:
        """Submit one RPC per prompt, retry transient rejections once.

        Returns ``(images_in_order, failures_by_index)``. A partial wave keeps
        what it got: those images are already paid for, and failing the whole
        batch to report one bad variant throws away three good ones.
        """
        base_seed = seed if seed is not None else int(time.time() * 1000) % 1_000_000

        async def submit(index: int, offset: float) -> Any:
            if offset:
                await asyncio.sleep(offset)
            freq = fb.image_request(
                prompts[index],
                project_id,
                count=1,
                aspect=aspect_ratio,
                seed=base_seed + index * 9973,
                model=model_name,
                ref_media_ids=ref_media_ids,
                base_media_id=base_media_id,
            )
            payload = await self._payload(fb.RPC_GEN_IMAGE, freq, fb.CAPTCHA_IMAGE)
            generated = fb.read_images(payload)
            if not generated:
                raise fb.FlowBatchError("image generation returned no media url")
            return generated[0]

        async def run(indices: list[int]) -> dict[int, Any]:
            tasks = [
                submit(index, IMAGE_SUBMIT_OFFSETS_S[position])
                for position, index in enumerate(indices)
            ]
            done = await asyncio.gather(*tasks, return_exceptions=True)
            return dict(zip(indices, done, strict=True))

        results = await run(list(range(len(prompts))))

        # Flow answers `[8]` when its own generation side refuses under load.
        # Measured upstream: an immediate retry is refused again, so this waits
        # out one cooldown and tries the failed variants ONCE. Not a loop — a
        # retry that keeps going is a retry that keeps paying.
        transient = [
            index
            for index, outcome in results.items()
            if isinstance(outcome, fb.RpcError)
            and outcome.rpcid == fb.RPC_GEN_IMAGE
            and outcome.detail == [8]
        ]
        if transient:
            logger.warning(
                "image wave: transient [8] on variant(s) %s; retrying once after %.0fs",
                ",".join(str(i + 1) for i in transient),
                IMAGE_TRANSIENT_RETRY_DELAY_S,
            )
            await asyncio.sleep(IMAGE_TRANSIENT_RETRY_DELAY_S)
            results.update(await run(transient))

        images = [
            results[i] for i in sorted(results)
            if not isinstance(results[i], BaseException)
        ]
        failures = {
            i: results[i] for i in sorted(results)
            if isinstance(results[i], BaseException)
        }
        return images, failures

    async def gen_image(
        self,
        prompt: str,
        project_id: str,
        aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE",
        paygate_tier: Optional[str] = None,
        ref_media_ids: Optional[list[str]] = None,
        variant_count: int = 1,
        character_media_ids: Optional[list[str]] = None,  # legacy alias
        prompts: Optional[list[str]] = None,
        image_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate 1-4 images. Returns ``{raw, media_ids, media_entries}``.

        ``ref_media_ids`` condition the result on images already in the project —
        this is what keeps a character the same person from beat to beat.
        ``prompts`` gives each variant its own text, so four variants are four
        stances rather than four seeds of one.

        ``paygate_tier`` is accepted and ignored: the migrated payload has no
        tier slot. It stays in the signature because every caller passes it and
        because the request row records it as a label.
        """
        count = max(1, min(int(variant_count), MAX_VARIANT_COUNT))
        merged_refs = ref_media_ids if ref_media_ids is not None else character_media_ids
        refs = [m for m in (merged_refs or []) if isinstance(m, str) and m] or None
        texts = [
            prompts[i] if prompts and i < len(prompts) and prompts[i] else prompt
            for i in range(count)
        ]

        try:
            images, failures = await self._image_wave(
                texts, project_id, aspect_ratio,
                resolve_image_model(image_model), refs, None, None,
            )
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            return {"raw": None, "error": _error_text(exc)[:200]}

        if not images:
            first = failures[min(failures)] if failures else None
            detail = _error_text(first) if first is not None else "no images"
            return {"raw": None, "error": f"image_failed: {detail}"[:200]}

        entries = [_image_entry(i) for i in images]
        out: dict[str, Any] = {
            "raw": {"media": entries},
            "media_ids": [e["media_id"] for e in entries],
            "media_entries": entries,
        }
        if failures:
            # Reported, not raised: the images that exist have been paid for.
            #
            # Through `_error_text`, like the video paths. These three sites built
            # their own strings from `str(exc)`, so a `[7]` arrived as a repr of a
            # nested protobuf and the 80-character cut below landed in the middle
            # of `PUBLIC_ERROR_MODEL_ACCESS_DENIED` — slicing off the one token
            # that says what to do. Leading with the code makes the truncation
            # safe and gives `errorLabel` something it can translate.
            out["partial_error"] = (
                f"{len(failures)}/{count} variants failed: "
                + "; ".join(_error_text(e)[:80] for e in failures.values())
            )[:300]
        return out

    async def edit_image(
        self,
        prompt: str,
        project_id: str,
        source_media_id: str,
        ref_media_ids: Optional[list[str]] = None,
        aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE",
        paygate_tier: Optional[str] = None,
        image_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Refine an existing image. Same return shape as ``gen_image``.

        The source rides in the BASE_IMAGE slot (wire type 2), not as another
        reference (type 1). That distinction is the difference between editing
        the picture and generating a fresh one that merely resembles it — and a
        wrong slot here is accepted by Flow and then ignored, which looks
        exactly like success.
        """
        refs = [
            m for m in (ref_media_ids or [])
            if isinstance(m, str) and m and m != source_media_id
        ] or None
        try:
            images, failures = await self._image_wave(
                [prompt], project_id, aspect_ratio,
                resolve_image_model(image_model), refs, source_media_id, None,
            )
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            return {"raw": None, "error": _error_text(exc)[:200]}
        if not images:
            first = failures[min(failures)] if failures else None
            detail = _error_text(first) if first is not None else "no image"
            return {"raw": None, "error": f"edit_failed: {detail}"[:200]}
        entries = [_image_entry(i) for i in images]
        return {
            "raw": {"media": entries},
            "media_ids": [e["media_id"] for e in entries],
            "media_entries": entries,
        }

    async def upload_image(
        self,
        image_base64: str,
        mime_type: str,
        project_id: str,
        file_name: str = "upload.png",
    ) -> dict[str, Any]:
        """Put a local image into a Flow project. ``{raw, media_id}``.

        The bytes ride inside the RPC as plain base64 — no separate upload
        endpoint — and the call **carries a captcha**, which the REST upload did
        not. That matters for `media_project_sync`: syncing a dozen references
        across projects now mints a dozen captcha tokens, so it is worth doing
        once and caching, which is what the mapping table already does.
        """
        try:
            payload = await self._payload(
                fb.RPC_UPLOAD_IMAGE,
                fb.upload_request(image_base64, project_id, mime_type, file_name),
                fb.CAPTCHA_IMAGE,
                timeout=120,
            )
            media_id = fb.read_uploaded_media_id(payload)
        except (fb.FlowBatchError, fb.RpcError) as exc:
            # Most common real cause of a 200-with-nothing-usable is a silent
            # content-filter rejection (logos, watermarks, product CDN imagery);
            # next most common is a payload shape change. Log enough to tell
            # them apart, without the bytes.
            logger.error(
                "upload_image failed (project=%s file=%s mime=%s): %s",
                project_id, file_name, mime_type, exc,
            )
            return {"raw": None, "error": f"upload_failed: {exc}"[:200]}
        return {"raw": {"media": {"name": media_id}}, "media_id": media_id}


def _image_entry(image: "fb.GeneratedImage") -> dict[str, Any]:
    """One generated image in the `{media_id, url, mediaType}` shape
    `media.ingest_urls` and the worker handlers already read."""
    return {"media_id": image.media_id, "url": image.url, "mediaType": "image"}


# What used to sit here: `_extract_project_id`, `_extract_uploaded_media_id`,
# `extract_operation_names`, `extract_video_workflows`, `extract_video_operations`,
# `_extract_media_ids` and `extract_media_entries` -- roughly 250 lines reading
# a JSON body Flow no longer serves. Their replacements live in `flow_batch` as
# `read_*` functions over the wire arrays, because on this transport there are
# no field names to read: a media id is slot 4 of slot 3, and the only thing
# that makes that safe is a golden test over a captured envelope.


_sdk: Optional[FlowSDK] = None


def get_flow_sdk() -> FlowSDK:
    global _sdk
    if _sdk is None:
        _sdk = FlowSDK()
    return _sdk
