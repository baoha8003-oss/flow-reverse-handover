"""Generation settings for a canvas node, in one vocabulary.

A node carries its settings in one of two shapes and, until this module
existed, only one of them was ever read for generation:

* **Built on the canvas** — canonical camelCase keys directly on ``data``
  (``aspectRatio``, ``durationSeconds``, ``videoQuality``…). The Settings
  UI writes these.
* **Imported from a packaged workflow** — a ``sourceSettings`` dict with the
  packaged tool's own Vietnamese, emoji-decorated labels
  (``ratio: "📱 9:16"``, ``quality: "📉 LITE LOWER priority"``).

``template_import`` writes ``sourceSettings``; the only reader was
``postprod_plan``, for post-production nodes. Nothing read it for ``image``
or ``video``. So ``run_pipeline`` dispatched ``{prompt, project_id,
start_media_id}`` and nothing else, and every imported workflow ran at
Flow's defaults — **landscape**, default duration, default model — no
matter what the template asked for. The same blindness sat in
``estimate._price_of``, which looks for the camelCase keys and therefore
priced every imported node as "unknown".

One module, read by both, so the number the user approves is the number the
run produces. That pairing is the whole point: the previous two rounds each
turned up a planner/executor split, and this is the third.

**Unmapped values return None on purpose.** A silent default here is a
wrong aspect ratio or a wrong model billed to the user; ``None`` lets the
caller fall back explicitly and lets the estimate say "unpriced" honestly.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Runs of whitespace, collapsed so a doubled space cannot break a match.
_SPACING = re.compile(r"\s+")


def _clean(value: Any) -> str:
    """Lowercase, whitespace-collapsed form of a label.

    Deliberately light. The tolerance that matters comes from matching
    SUBSTRINGS below, not from scrubbing here — the packaged workflows carry
    the same setting as ``"⏱️ 8s"``, ``"?? 8s"`` (emoji mangled somewhere
    upstream) and bare ``"8s"``, and a substring search finds the payload
    inside all three however the decoration is spelled.

    An earlier version stripped every non-alphanumeric character and this
    comment claimed that was what made the three spellings work. A mutation
    test disproved it: removing the stripping entirely left all tests green,
    because the substring match was doing the work the whole time. What is
    left is the one normalisation with a reason — collapsing whitespace, so
    ``"LITE  LOWER priority"`` still matches ``"lite lower priority"``.
    """
    if not isinstance(value, str):
        return ""
    return _SPACING.sub(" ", value.lower()).strip()


# ── individual settings ───────────────────────────────────────────────

#: Ratio label → the suffix Flow uses. Prefixed per media kind by the
#: caller, because image and video spell the same shape differently.
_ASPECT_SUFFIX = {
    "9:16": "PORTRAIT",
    "16:9": "LANDSCAPE",
    "1:1": "SQUARE",
}


def aspect_ratio(settings: dict, *, media: str) -> Optional[str]:
    """``VIDEO_ASPECT_RATIO_PORTRAIT`` / ``IMAGE_ASPECT_RATIO_LANDSCAPE`` …

    ``media`` is ``"video"`` or ``"image"``. Every packaged workflow but one
    asks for 9:16, and dropping this is what made board runs come out
    landscape.
    """
    value = settings.get("ratio") or settings.get("aspect_ratio")
    # Already canonical — the Settings UI stores the enum itself
    # (`Option(value="VIDEO_ASPECT_RATIO_PORTRAIT")` in routes/models.py),
    # so a canvas-built node arrives here finished and must pass through
    # rather than be re-derived from a label it does not carry.
    if isinstance(value, str) and "_ASPECT_RATIO_" in value.upper():
        return value.upper()
    raw = _clean(value)
    for label, suffix in _ASPECT_SUFFIX.items():
        if label in raw:
            return f"{media.upper()}_ASPECT_RATIO_{suffix}"
    if raw:
        logger.info("node_settings: unmapped ratio %r", raw)
    return None


def duration_s(settings: dict) -> Optional[int]:
    """Seconds, from ``"⏱️ 8s"`` and friends.

    Not coerced to a default when absent or malformed: duration selects the
    model key, so a silent 8s substitution bills a longer clip than the
    caller asked for — the same reasoning the worker already applies.
    """
    value = settings.get("duration") or settings.get("duration_s")
    # The Settings UI stores `durationSeconds` as a number, not a label.
    # Only strings reached the parser before, so every canvas-built node
    # lost its duration — including the OMNI lane, whose price depends on
    # it, which is how the estimate's own OMNI test caught this.
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 < value <= 60 else None
    raw = _clean(value)
    match = re.search(r"(\d+)\s*s?\b", raw)
    if not match:
        if raw:
            logger.info("node_settings: unmapped duration %r", raw)
        return None
    try:
        seconds = int(match.group(1))
    except ValueError:  # pragma: no cover — the regex only matches digits
        return None
    return seconds if 0 < seconds <= 60 else None


#: Quality label → this codebase's lane name (see `routes/models.py`).
#: Order matters: "lite lower priority" contains "lite", so the relaxed
#: lanes are tested first. Getting that backwards would quietly bill a
#: 0-credit low-priority job at the normal Lite rate.
_QUALITY_LANES: tuple[tuple[str, str], ...] = (
    # OMNI Flash is a lane like the others and the ONLY one with a real
    # price table (`flow_sdk.OMNI_FLASH_CREDIT_COST`). Omitting it here
    # made `_price_of` stop recognising it, so the one lane that CAN be
    # priced started reporting "unknown" — caught by the estimate's own
    # test, which is why that test is worth having.
    ("omni", "omni"),
    ("lite lower priority", "lite_relaxed"),
    ("fast lower priority", "fast_relaxed"),
    ("lite relaxed", "lite_relaxed"),
    ("fast relaxed", "fast_relaxed"),
    ("lite", "lite"),
    ("fast", "fast"),
    ("quality", "quality"),
)


def video_quality(settings: dict) -> Optional[str]:
    """The Veo lane: ``lite`` / ``fast`` / ``quality`` / ``*_relaxed``.

    ``"📉 LITE LOWER priority"`` in the packaged workflows is this
    codebase's ``lite_relaxed`` — *Veo 3.1 - Lite [Lower Priority]* in
    `routes/models.py`, the 0-credit Ultra-only queue. Reading it as plain
    ``lite`` would quote and bill credits for a job that costs none.
    """
    raw = _clean(settings.get("quality") or settings.get("video_quality"))
    if not raw:
        return None
    # Canonical first, and this one is a money bug if skipped. The Settings
    # UI stores the lane name itself — `"lite_relaxed"` — and substring
    # matching reads that as plain `"lite"`, because "lite" is inside it and
    # the relaxed needles are spelled with a space. That silently turns the
    # 0-credit low-priority lane into the charged one.
    known = {lane for _, lane in _QUALITY_LANES}
    if raw.replace(" ", "_") in known:
        return raw.replace(" ", "_")
    for needle, lane in _QUALITY_LANES:
        if needle in raw:
            return lane
    # A resolution ("1080") is an image setting that also lands in this
    # field; it is not a video lane and must not be forced into one.
    if not re.fullmatch(r"\d{3,4}", raw):
        logger.info("node_settings: unmapped video quality %r", raw)
    return None


#: Image model label → key in `flow_sdk.IMAGE_MODELS`. "pro" is checked
#: before the bare families so "Nano Banana pro" does not fall through to
#: whichever generic entry happens to match first.
_IMAGE_MODELS: tuple[tuple[str, str], ...] = (
    ("banana pro", "NANO_BANANA_PRO"),
    ("banana 2", "NANO_BANANA_2"),
)


#: Which service draws the image. Absent means Flow, because Flow is what
#: every existing board and every packaged template means — a node that
#: does not mention an engine has never asked for a different one.
_IMAGE_ENGINES = ("flow", "openai")


def image_engine(settings: dict) -> Optional[str]:
    """``"flow"`` / ``"openai"``, or None when the node does not say.

    None is not the same as ``"flow"`` to the caller: it lets the dispatch
    leave the key out entirely rather than stamping a choice the user never
    made, which matters because an unrecognised value must not silently
    become "openai" and start spending money elsewhere.
    """
    raw = _clean(settings.get("image_engine"))
    if not raw:
        return None
    for name in _IMAGE_ENGINES:
        if name in raw:
            return name
    logger.info("node_settings: unmapped image engine %r", raw)
    return None


def image_model(settings: dict) -> Optional[str]:
    """Key for ``flow_sdk.IMAGE_MODELS``, or None to let the SDK default."""
    raw = _clean(settings.get("model") or settings.get("image_model"))
    if not raw:
        return None
    for needle, key in _IMAGE_MODELS:
        if needle in raw:
            return key
    logger.info("node_settings: unmapped image model %r", raw)
    return None


# ── the shape both callers want ───────────────────────────────────────


def merged_settings(node) -> dict:
    """Both shapes in one dict, canvas keys winning.

    A node edited on the canvas after import carries both; what the user
    last set has to be what runs.
    """
    data = getattr(node, "data", None) or {}
    source = data.get("sourceSettings")
    merged = dict(source) if isinstance(source, dict) else {}
    for key in (
        "ratio",
        "aspect_ratio",
        "aspectRatio",
        "duration",
        "duration_s",
        "durationSeconds",
        "quality",
        "video_quality",
        "videoQuality",
        "model",
        "image_model",
        "imageModel",
        "variant_count",
        "variantCount",
        "image_engine",
        "imageEngine",
        "resolution",
        "video_resolution",
        # The clip-review loop. `review_loop.settings_from` reads these three off
        # whatever this returns, and none of them were in this list — so the loop
        # was off for every node, unconditionally, and no UI could turn it on.
        # `estimate._review_loop_calls` therefore always counted zero, and the
        # money rules written around it (`is_free_dispatch`, the `reviewJobsMax`
        # ceiling) were guarding a path nobody could reach.
        "review_loop",
        "reviewLoop",
        "review_threshold",
        "reviewThreshold",
        "review_max_rounds",
        "reviewMaxRounds",
    ):
        if data.get(key) not in (None, ""):
            merged[key] = data[key]
    # camelCase from the canvas, normalised onto the names read above.
    for camel, snake in (
        ("aspectRatio", "ratio"),
        ("durationSeconds", "duration"),
        ("videoQuality", "quality"),
        ("imageModel", "model"),
        ("variantCount", "variant_count"),
        ("imageEngine", "image_engine"),
        ("videoResolution", "resolution"),
        # `review_loop.settings_from` reads the snake_case names, and the canvas
        # writes camelCase — so a checkbox alone would have set a key nothing read.
        ("reviewLoop", "review_loop"),
        ("reviewThreshold", "review_threshold"),
        ("reviewMaxRounds", "review_max_rounds"),
    ):
        if data.get(camel) not in (None, ""):
            merged[snake] = data[camel]
    return merged


#: Omni's two render resolutions. A closed set rather than a parser: the model
#: key embeds it, so an unrecognised value would have to be dropped or guessed,
#: and guessing 720p on a node that asked for 360p bills the more expensive
#: render.
_RESOLUTIONS = ("360p", "720p")


def resolution(settings: dict) -> Optional[str]:
    """Omni's render resolution, or None when nothing chose one.

    None rather than `"720p"`: the SDK owns the default, and a second copy of it
    here is how the estimate and the dispatch come to disagree about what was
    rendered.
    """
    raw = _clean(settings.get("resolution") or settings.get("video_resolution"))
    if not raw:
        return None
    lowered = raw.lower()
    for known in _RESOLUTIONS:
        if known in lowered:
            return known
    logger.info("node_settings: unmapped resolution %r", raw)
    return None


def for_dispatch(node) -> dict:
    """Generation params for this node, ready to merge into a request.

    Only keys that resolved are present, so a caller can splat this over its
    own defaults without an unresolved setting overwriting anything with
    ``None``. The names match what `worker.processor` already reads.
    """
    media = "video" if getattr(node, "type", "") == "video" else "image"
    settings = merged_settings(node)
    out: dict[str, Any] = {}

    ratio = aspect_ratio(settings, media=media)
    if ratio:
        out["aspect_ratio"] = ratio
    variants = settings.get("variant_count")
    if isinstance(variants, int) and not isinstance(variants, bool) and variants > 0:
        out["variant_count"] = variants

    if media == "video":
        seconds = duration_s(settings)
        if seconds is not None:
            out["duration_s"] = seconds
        lane = video_quality(settings)
        if lane:
            out["video_quality"] = lane
        res = resolution(settings)
        if res:
            out["resolution"] = res
    else:
        model = image_model(settings)
        if model:
            out["image_model"] = model
        engine = image_engine(settings)
        if engine:
            out["image_engine"] = engine
    return out


# ── the packaged tool's remaining template keys ───────────────────────
#
# Everything below was present in the shipped workflow files and read by
# nothing here, so importing a template preserved the value on the node and
# then ignored it. Values and node types were taken from the eleven files on
# disk rather than guessed — `scale` really is spelled `🔍 x1`, and
# `thumbnail_position` really is the string `end`.

#: `🔍 x1` / `x2` / `4` → the multiplier. The emoji and the `x` are the
#: packaged tool's own label; a bare number is what the canvas writes.
_SCALE_RE = re.compile(r"(\d+(?:\.\d+)?)")

#: What the exe calls each TTS engine, and what this build can actually do
#: about it. `gemini` is the only one wired; the others are recognised so an
#: imported template names its intent instead of failing as an unknown value,
#: and so P11 has one place to add them.
TTS_ENGINES = {
    "gemini": "gemini",
    "google": "gemini",
    "chatgpt": "openai",
    "openai": "openai",
    "clone voice": "clone",
    "clone": "clone",
    "capcut": "capcut",
}


def upscale_factor(settings: dict) -> Optional[float]:
    """How much to enlarge the finished media, or None.

    `1` is returned as None rather than 1.0: every shipped template says
    `🔍 x1`, and treating that as "run an upscale pass that changes nothing"
    would add a re-encode — and a quality loss — to twenty-six nodes for no
    reason at all.
    """
    raw = settings.get("scale")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
    else:
        match = _SCALE_RE.search(_clean(raw))
        if match is None:
            if _clean(raw):
                logger.info("node_settings: unmapped scale %r", raw)
            return None
        value = float(match.group(1))
    if value <= 1.0 or value > 8.0:
        return None
    return value


def batch_index(settings: dict) -> Optional[int]:
    """Which batch column this node belongs to.

    Stored, never acted on. The packaged tool uses it to lay nodes out in
    columns; here the coordinates already say that, so acting on it would
    move nodes the user placed. Kept because dropping it would lose the
    grouping when a board is exported back.
    """
    raw = settings.get("batch_idx")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return None


def preview_height(settings: dict) -> Optional[int]:
    """How tall the node's preview was drawn in the packaged tool.

    Stored, never acted on: this canvas sizes nodes itself. Same reasoning
    as `batch_index` — preserved so a round-trip does not quietly flatten
    someone's layout.
    """
    raw = settings.get("preview_height")
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= 4000:
        return raw
    return None


def tts_engine(settings: dict) -> Optional[str]:
    """Which speech engine a `create_voice` node asks for.

    Returns the normalised name, NOT a decision about whether it can run.
    The caller checks availability, because "the template asked for ChatGPT"
    and "ChatGPT is configured" are different facts and collapsing them
    hides which one is missing.
    """
    raw = _clean(settings.get("engine"))
    if not raw:
        return None
    for needle, name in TTS_ENGINES.items():
        if needle in raw:
            return name
    logger.info("node_settings: unmapped tts engine %r", settings.get("engine"))
    return None


def gemini_models(settings: dict) -> tuple[Optional[str], Optional[str]]:
    """``(preferred, fallback)`` model ids for an analyze step.

    The packaged tool writes them with its own label — `⚡ API —
    gemini-3.5-flash` — so the id has to be pulled out of the decoration.
    The fallback exists because Gemini answers 503 under load often enough
    that the templates carry a second choice; losing it turns a retryable
    outage into a failed node.
    """
    return _model_id(settings.get("gemini_model")), _model_id(
        settings.get("fallback_gemini_model")
    )


_MODEL_RE = re.compile(r"(gemini[-\w.]*)", re.IGNORECASE)


def _model_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    match = _MODEL_RE.search(raw)
    return match.group(1).lower() if match else None


def local_path(settings: dict) -> Optional[str]:
    """A file the template points at on the machine that made it.

    `path` (upload_media), `image_path` (video_image_list) and
    `import_excel_path` (link_list) are all this: an absolute path from
    someone else's disk. Returned as a plain string and NOT opened here —
    the caller decides whether it exists and whether it is allowed to read
    it, because a template is untrusted input and this module has no
    business touching the filesystem.
    """
    for key in ("path", "image_path", "import_excel_path"):
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def prompt_template_flags(settings: dict) -> dict[str, Any]:
    """The `prompt_mau` node's three switches, normalised.

    `selected_scene_id` is a string because `random` is one of its values —
    coercing it to an int would turn the tool's own default into a crash.
    """
    out: dict[str, Any] = {}
    for key in ("fashion_random_enabled", "op_lung_enabled"):
        if isinstance(settings.get(key), bool):
            out[key] = settings[key]
    scene = settings.get("selected_scene_id")
    if isinstance(scene, (str, int)) and not isinstance(scene, bool) and str(scene).strip():
        out["selected_scene_id"] = str(scene).strip()
    return out
