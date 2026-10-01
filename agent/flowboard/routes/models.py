"""What this account can actually generate with.

Every option list in the UI used to be typed out by hand — twenty of them
across ten files — with no way for the frontend to know what the backend
supports. They drifted, and the drift was not cosmetic:

  * a Square aspect was offered for VIDEO, and no video model key exists for
    one, so that dispatch could only fail;
  * Settings offered 4–10s while Text-to-Video hardcoded 8s, so a duration the
    user picked was silently ignored;
  * an unknown quality fell back to `fast` in the backend and `lite` in the
    frontend — the same input, two different bills.

`flow_sdk` already holds the real tables. This serves them, filtered by the
signed-in account's tier, so the UI stops guessing.

It also carries what the tables alone cannot say: which combinations this
build cannot dispatch at all. Since Google's September 2026 move to
`batchexecute`, only three Veo keys are accepted, and there is no captured
payload for Veo text-to-video, Veo first-to-last frame, or Veo
reference-to-video. Those lanes stay in the lists -- the labels are part of the
UI contract and someone who saw them in the packaged tool will look for them --
but each carries the reason it will be refused.

An option that always fails is worse than an option that isn't offered. An
option that silently runs a PAID lane instead is worse than both, and that is
what the notes here used to promise.

Tier no longer filters anything. The migrated payload has no `userPaygateTier`
slot, so which lanes an account really has is Google's answer at dispatch time
and not something this process can know. `tier` is still reported because the
UI shows it as a label.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from flowboard.services import flow_sdk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

TIER_ONE = "PAYGATE_TIER_ONE"
TIER_TWO = "PAYGATE_TIER_TWO"

# Labels are the packaged tool's, byte-exact (docs/ui-spec.md).
QUALITY_LABELS: dict[str, str] = {
    "lite": "Veo 3.1 - Lite",
    "fast": "Veo 3.1 - Fast",
    "quality": "Veo 3.1 - Quality",
    "lite_relaxed": "Veo 3.1 - Lite [Lower Priority]",
    "fast_relaxed": "Veo 3.1 - Fast [Lower Priority]",
    "omni": "OMNI Flash",
}

IMAGE_MODEL_LABELS: dict[str, str] = {
    "NANO_BANANA_PRO": "🍌 Nano Banana pro",
    "NANO_BANANA_2": "🍌 Nano Banana 2",
    "NANO_BANANA_2_LITE": "🍌 Nano Banana 2 Lite",
}

# What `CREATE_IMAGE_MODEL` is spelled as in the settings file. Kept explicit
# rather than derived from the label, because stripping the emoji would make
# the config contract depend on how the label is decorated.
IMAGE_MODEL_CONFIG_NAMES: dict[str, str] = {
    "NANO_BANANA_PRO": "Nano Banana pro",
    "NANO_BANANA_2": "Nano Banana 2",
    "NANO_BANANA_2_LITE": "Nano Banana 2 Lite",
}

VIDEO_ASPECTS: list[dict[str, str]] = [
    {"value": "VIDEO_ASPECT_RATIO_PORTRAIT", "label": "Dọc 9:16"},
    {"value": "VIDEO_ASPECT_RATIO_LANDSCAPE", "label": "Ngang 16:9"},
]
# Square exists for images only. Offering it for video was the bug: no video
# model key is keyed on it, so the dispatch could never succeed.
IMAGE_ASPECTS: list[dict[str, str]] = [
    {"value": "IMAGE_ASPECT_RATIO_PORTRAIT", "label": "Dọc 9:16"},
    {"value": "IMAGE_ASPECT_RATIO_LANDSCAPE", "label": "Ngang 16:9"},
    {"value": "IMAGE_ASPECT_RATIO_SQUARE", "label": "Vuông 1:1"},
]

# Omni's resolution axis. 360p exists in the builder and renders cheaper, but
# its credit cost has never been measured here, so it is offered without a
# price rather than with a guessed one.
VIDEO_RESOLUTIONS: list[dict[str, str]] = [
    {"value": "720p", "label": "720p"},
    {"value": "360p", "label": "360p (chưa đo giá)"},
]

#: Why a lane the UI still offers will be refused.
#:
#: This replaces a set of notes that promised a substitution — "sẽ chạy Veo 3.1
#: - Lite và CÓ tính credit". That promise is no longer kept and must not be
#: shown: a board asking for the free queue now gets an error instead of a
#: bill, and saying otherwise would have someone cancel a run that was never
#: going to charge them.
LANE_REFUSED_NOTE = (
    "Chưa có payload trên đường mới của Flow — chọn làn này sẽ bị TỪ CHỐI "
    "(không tốn credit), KHÔNG bị thay bằng làn trả phí."
)

#: The free queue, said where the lane is chosen. One key, and it is the only
#: reason `review_loop` may re-run a clip without asking first.
#:
#: The second sentence is a live measurement, not a hedge: a Pro account asking
#: for this key gets `PUBLIC_ERROR_MODEL_ACCESS_DENIED` (measured 19/09/2026).
#: Nothing here can know the entitlement before trying — the payload carries no
#: tier and there is no credits RPC — so the honest move is to keep offering the
#: lane at its real price of zero and say it may be refused. Someone who builds
#: a fifteen-node board on it and only then discovers their plan lacks it has
#: lost an afternoon to a note we could have written.
ZERO_CREDIT_NOTE = (
    "Miễn phí (hàng đợi ưu tiên thấp) — chạy chậm hơn. "
    "Gói Pro đo được là KHÔNG có làn này: Flow trả MODEL_ACCESS_DENIED, "
    "dispatch bị từ chối (vẫn không tốn credit)."
)

#: Omni is the only path left for text-to-video, first-to-last and references,
#: and unlike the free Veo lane it always costs credits. Say so at the point of
#: choosing, not only in the cost dialog.
OMNI_PAID_NOTE = "OMNI CÓ tính credit theo độ dài."


class Option(BaseModel):
    value: str
    label: str
    note: Optional[str] = None


class DurationOption(BaseModel):
    value: int
    label: str
    credits: Optional[int] = None
    note: Optional[str] = None


class LaneInfo(BaseModel):
    """One generation path: which qualities, aspects and durations it takes."""

    qualities: list[Option]
    aspects: list[Option]
    durations: list[DurationOption]


class SettingsOptions(BaseModel):
    """The Settings screen's own value space.

    Settings stores human labels ("Veo 3.1 - Lite"), not the model tokens the
    dispatch uses, because that is the contract `settings_store` validates.
    The lists still belong here rather than in the frontend — they are the
    same tables, projected onto the other vocabulary.
    """

    veoModel: list[Option]
    imageModel: list[Option]
    aspect: list[Option]
    duration: list[DurationOption]
    #: Omni's render resolution. New with the batch path: the builder takes it
    #: per request, and 360p had no way to be chosen before.
    resolution: list[Option]


class ModelsResponse(BaseModel):
    tier: Optional[str]
    tierLabel: str
    settingsOptions: SettingsOptions
    imageModels: list[Option]
    imageAspects: list[Option]
    #: text-to-video
    t2v: LaneInfo
    #: image-to-video (Veo)
    i2v: LaneInfo
    #: first→last frame
    startEnd: LaneInfo
    #: OMNI Flash reference-to-video
    omni: LaneInfo


def _veo_lane_options(*, include_omni: bool) -> list[Option]:
    """The image-to-video quality list: what runs, then what gets refused.

    Servable lanes come first so a fresh dropdown's default selection is a lane
    that works.
    """
    out: list[Option] = []
    for key, model_key in flow_sdk.BATCH_VIDEO_LANES.items():
        out.append(Option(
            value=key,
            label=QUALITY_LABELS.get(key, key),
            note=(
                ZERO_CREDIT_NOTE
                if flow_sdk.is_zero_credit_model(model_key)
                else None
            ),
        ))
    if include_omni:
        out.append(
            Option(value="omni", label=QUALITY_LABELS["omni"], note=OMNI_PAID_NOTE)
        )
    for key, why in flow_sdk.REFUSED_VIDEO_LANES.items():
        out.append(Option(
            value=key,
            label=QUALITY_LABELS.get(key, key),
            note=f"{LANE_REFUSED_NOTE} ({why})",
        ))
    return out


def _t2v_lane_options() -> list[Option]:
    """Text-to-video lanes: the Veo table plus Omni.

    Read from `flow_sdk.VEO_T2V_LANES` rather than listed here, so a key table
    corrected after a live refusal reaches the dropdown without a second edit.
    """
    out: list[Option] = []
    for lane in flow_sdk.VEO_T2V_LANES:
        note = ZERO_CREDIT_NOTE if lane.endswith("_relaxed") else None
        out.append(Option(value=lane, label=QUALITY_LABELS.get(lane, lane), note=note))
    out.append(Option(value="omni", label=QUALITY_LABELS["omni"], note=OMNI_PAID_NOTE))
    # Labels with no key behind them, each carrying the reason. Read from the
    # SDK rather than listed here — `fast_relaxed` moved into that table after
    # its three names were measured unknown, and a hardcoded list here would
    # have kept offering it as a free lane that never runs.
    for lane, why in flow_sdk.REFUSED_T2V_LANES.items():
        out.append(Option(
            value=lane,
            label=QUALITY_LABELS.get(lane, lane),
            note=f"{LANE_REFUSED_NOTE} ({why})",
        ))
    return out


#: Veo text-to-video lengths a Pro account was measured NOT to hold.
#:
#: Measured twice, on two different transports, which is why it is worth
#: writing down: the REST era recorded `veo_3_1_t2v_lite_4s` answering MODEL
#: ACCESS DENIED while the 8s key worked, and 19/09/2026 the batch path answered
#: `PUBLIC_ERROR_MODEL_ACCESS_DENIED` for the same key while
#: `veo_3_1_t2v_lite` produced a real 8.00s clip. The entitlement followed the
#: account across the migration.
#:
#: Still offered, not hidden: nothing here can read an entitlement, another plan
#: may hold these, and the dispatch costs nothing when refused. What it must not
#: do is look like a length that will simply work.
T2V_SHORT_DURATIONS_DENIED_ON_PRO = (4, 6)


def _t2v_durations() -> list[DurationOption]:
    """Every length either family can do, each saying which family honours it.

    Veo has 4/6/8 and Omni has 4/6/8/10, so 10s is Omni-only — and a duration
    control that silently does nothing on the lane actually running is how 8s
    clips got ordered as 4.
    """
    veo = set(flow_sdk.VEO_T2V_DURATIONS)
    out: list[DurationOption] = []
    for d in sorted(veo | set(flow_sdk.OMNI_VALID_DURATIONS)):
        if d not in veo:
            note = "Chỉ với làn OMNI."
        elif d in T2V_SHORT_DURATIONS_DENIED_ON_PRO:
            note = (
                "Gói Pro đo được là KHÔNG có độ dài này cho Veo — Flow trả "
                "MODEL_ACCESS_DENIED (không tốn credit). OMNI vẫn chạy."
            )
        else:
            note = None
        out.append(DurationOption(
            value=d,
            label=f"{d}s",
            credits=flow_sdk.OMNI_FLASH_CREDIT_COST.get(d),
            note=note,
        ))
    return out


def _omni_only_options(capability: str) -> list[Option]:
    """A lane only Omni can serve, with every Veo label kept and refused.

    ``capability`` names the missing payload, because "Veo 3.1 - Lite is
    unavailable" reads as a contradiction on a screen where that same label
    works for plain image-to-video.
    """
    out = [Option(value="omni", label=QUALITY_LABELS["omni"], note=OMNI_PAID_NOTE)]
    # Read from the SDK tables, not typed out. This was the one list in the
    # registry still hand-written, and it indexed `QUALITY_LABELS[key]` directly —
    # so a lane added to the SDK without a label here would `KeyError` and turn
    # `GET /api/models` into a 500, taking every dropdown in the app with it.
    # Latent rather than live (every current key has a label), but the registry
    # exists because twenty hand-copied lists drifted, and this was the last one.
    # `.get` with the key as its own fallback so a missing label degrades to a raw
    # token in one dropdown instead of a dead endpoint.
    for key in sorted(
        set(flow_sdk.BATCH_VIDEO_LANES) | set(flow_sdk.REFUSED_VIDEO_LANES)
    ):
        out.append(Option(
            value=key,
            label=QUALITY_LABELS.get(key, key),
            note=(
                f"Veo chưa có payload {capability} trên đường mới — "
                "chọn sẽ bị TỪ CHỐI, không tốn credit."
            ),
        ))
    return out


def _reference_lane_options() -> list[Option]:
    """Lanes for the character port: Omni, plus Veo's one measured reference key.

    Veo's reference family is the only Veo family with a servable lane here, and
    the 20/09 probe closed the last doubt about it: `veo_3_1_r2v_lite_low_priority`
    placed in the model slot `omni_reference_video_request` writes comes back `[7]`
    MODEL_ACCESS_DENIED, while a bogus name in the same slot comes back `[5]`. So
    the RPC reads a Veo name there and the lane is real.

    Its PRICE is unmeasured, and the note says so rather than repeating the
    `_low_priority`-means-free inference: `[7]` means this plan does not hold the
    key, so this account cannot run it to find out. `/estimate` reports it as
    unpriced for the same reason.
    """
    out = [Option(value="omni", label=QUALITY_LABELS["omni"], note=OMNI_PAID_NOTE)]
    for key in sorted(flow_sdk.VEO_R2V_LANES):
        out.append(Option(
            value=key,
            label=QUALITY_LABELS.get(key, key),
            note=(
                "Veo reference — ô model đã xác nhận (đo 20/09). Hậu tố "
                "`_low_priority` thường là hàng đợi miễn phí nhưng GIÁ CHƯA ĐO; "
                "gói hiện tại không có key này nên không đo được ở đây."
            ),
        ))
    for key in sorted(
        (set(flow_sdk.BATCH_VIDEO_LANES) | set(flow_sdk.REFUSED_VIDEO_LANES))
        - set(flow_sdk.VEO_R2V_LANES)
    ):
        out.append(Option(
            value=key,
            label=QUALITY_LABELS.get(key, key),
            note=(
                "Cổng nhân vật: họ r2v của Veo chỉ có "
                f"{sorted(flow_sdk.VEO_R2V_LANES)} — làn này sẽ bị TỪ CHỐI, "
                "không tốn credit."
            ),
        ))
    return out


def _omni_durations() -> list[DurationOption]:
    """Omni's four lengths, with the published credit cost of each.

    The prices come from `flow_sdk.OMNI_FLASH_CREDIT_COST`, which is
    informational: Google can change it, and nothing here can read a balance to
    check. A length with no published price is served with ``credits=None``
    rather than 0, because 0 reads as free.
    """
    return [
        DurationOption(
            value=d,
            label=f"{d}s",
            credits=flow_sdk.OMNI_FLASH_CREDIT_COST.get(d),
        )
        for d in sorted(flow_sdk.OMNI_VALID_DURATIONS)
    ]


@router.get("", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    """Everything the UI needs to build its option lists.

    Nothing here is filtered by tier any more. On the batch path the account's
    entitlements are Google's answer at dispatch time, and a list narrowed by a
    tier this process only half knows would hide a lane the user actually has.
    """
    from flowboard.services.flow_client import flow_client

    tier = flow_client.paygate_tier
    omni_durations = _omni_durations()
    omni_aspects = [Option(**a) for a in VIDEO_ASPECTS]

    settings_options = SettingsOptions(
        # Settings holds the label, so the label is the value here.
        veoModel=[
            Option(value=q.label, label=q.label, note=q.note)
            for q in _veo_lane_options(include_omni=True)
        ],
        imageModel=[
            Option(
                value=IMAGE_MODEL_CONFIG_NAMES.get(k, k),
                label=IMAGE_MODEL_LABELS.get(k, k),
            )
            for k in flow_sdk.IMAGE_MODELS
        ],
        aspect=[
            Option(value="9:16", label="Dọc 9:16"),
            Option(value="16:9", label="Ngang 16:9"),
        ],
        # Every length Omni takes. Veo's own length is fixed by its model key,
        # so a default of 4s applies to Omni lanes only — which the note says,
        # because a duration control that silently does nothing on the lane the
        # user is actually running is how 8-second clips got ordered as 4.
        duration=[
            DurationOption(
                value=d.value,
                label=d.label,
                credits=d.credits,
                note="Chỉ áp dụng cho OMNI; Veo luôn 8s.",
            )
            for d in omni_durations
        ],
        resolution=[Option(**r) for r in VIDEO_RESOLUTIONS],
    )

    return ModelsResponse(
        tier=tier,
        settingsOptions=settings_options,
        tierLabel=(
            "Ultra" if tier == TIER_TWO else ("Pro" if tier == TIER_ONE else "Chưa chọn")
        ),
        imageModels=[
            Option(value=k, label=IMAGE_MODEL_LABELS.get(k, k))
            for k in flow_sdk.IMAGE_MODELS
        ],
        imageAspects=[Option(**a) for a in IMAGE_ASPECTS],
        t2v=LaneInfo(
            # Veo lanes are back. `YhhmEf` was measured parsing a Veo key and
            # objecting only to the plan (19/09/2026), so the lane a board asks
            # for is the lane that is sent — refusing meant a board wanting Veo
            # lite either got nothing or got talked into Omni at 15-30 credits.
            qualities=_t2v_lane_options(),
            aspects=omni_aspects,
            durations=_t2v_durations(),
        ),
        i2v=LaneInfo(
            qualities=_veo_lane_options(include_omni=True),
            aspects=omni_aspects,
            # A Veo lane's length lives in its model key and only 8s exists.
            # Omni's lengths come from the duration list above, which the node
            # editor shows once the Omni lane is picked.
            durations=[DurationOption(value=8, label="8s")] + [
                DurationOption(
                    value=d.value, label=d.label, credits=d.credits,
                    note="Chỉ với làn OMNI.",
                )
                for d in omni_durations
                if d.value != 8
            ],
        ),
        startEnd=LaneInfo(
            # Omni gained this (`nprQif`) while Veo lost it: Veo's end-image
            # slot was never captured, so a first-to-last on a Veo lane is
            # refused rather than run as a plain first-frame clip — which would
            # ignore the end frame the user chose and bill for it.
            qualities=_omni_only_options("khung đầu + khung cuối"),
            aspects=omni_aspects,
            durations=omni_durations,
        ),
        omni=LaneInfo(
            # The character port, which is the one family with a servable VEO lane
            # on this transport. This list offered `omni` alone and never read
            # `VEO_R2V_LANES`, so the lane `/estimate` tells the user is accepted
            # could not be chosen anywhere in the UI — two surfaces, two answers.
            qualities=_reference_lane_options(),
            aspects=omni_aspects,
            durations=omni_durations,
        ),
    )
