"""Which of the seven board shapes a brief means.

The packaged tool does not build one generic canvas. It picks a *shape* first
— recurring-character drama, seamless frame chaining, motion transfer,
narrated slideshow, product review, competitor remake, spreadsheet batch —
and every shape is a different chain of nodes. Picking wrong does not produce
a worse board; it produces a board that cannot do the job at all: a slideshow
graph will never keep a face consistent across scenes.

The decision order is the tool's own, and the order is the interesting part:

1. **What the user said, verbatim.** If they asked for motion control, it is
   motion control. Not "motion control unless the brief also mentions a
   product" — an explicit request is not a hint to be weighed against
   keywords, and overriding it is how a tool stops being predictable.
2. **What the input data implies.** A Shopee link, a dance video, a
   mother-in-law script: each points at exactly one shape.
3. **Ask, with two or three options.** A brief that genuinely fits several
   shapes has no right answer, and guessing one builds a board the user then
   has to take apart.

Signals are read from the packaged skill's own recognition notes
(`workflow_ai_skills/workflow-pipeline-selector/SKILL.md`, "Dấu hiệu nhận
biết"), so this file classifies rather than re-invents the taxonomy.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Archetype:
    key: str
    label: str
    #: What the finished chain looks like, for the explanation shown to the
    #: user before the canvas is built.
    chain: str
    #: Phrases that mean "this shape", scored below.
    signals: tuple[str, ...] = ()
    #: Phrases in which the USER named the shape outright. Matching one of
    #: these skips scoring entirely.
    explicit: tuple[str, ...] = ()
    #: Signals that are strong enough on their own — a Shopee link is not a
    #: hint about a product video, it IS one.
    strong: tuple[str, ...] = ()


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        key="character_drama",
        label="Drama nhân vật đồng nhất",
        chain="gen_image → create_character (hub) → gen_video Thành Phần → merge → edit",
        explicit=("nhan vat dong nhat", "dang nhan vat", "component", "thanh phan"),
        signals=(
            "me chong", "nang dau", "gia dinh", "cong so", "tinh cam",
            "loi thoai", "doi thoai", "nhan vat", "drama", "phim ngan",
            "lap lai", "dong nhat", "hoi thoai",
        ),
    ),
    Archetype(
        key="frame_chain",
        label="Chuyển động liền mạch khung hình",
        chain="gen_image → gen_video (Khung Hình) → extract_last_frame → gen_video → merge → edit",
        explicit=("frame to frame", "lien mach", "khung hinh", "seamless"),
        signals=(
            "phong canh", "bien hinh", "du lich", "khong gian 3d", "3d",
            "chuyen canh", "muot", "khong giat", "camera lien tuc",
            "chuyen dong camera", "timelapse",
        ),
    ),
    Archetype(
        key="motion_control",
        label="Hoán đổi cử động / người mẫu",
        chain="upload_media (video mẫu) + ảnh người mẫu → motion_control → edit",
        explicit=("motion control", "hoan doi cu dong", "outfit swap"),
        signals=(
            "nhay", "vu dao", "dance", "tiktok dance", "thoi trang",
            "quan ao", "trang phuc", "nguoi mau", "bat chuoc chuyen dong",
            "thu do",
        ),
        strong=("motion control", "vu dao", "outfit swap"),
    ),
    Archetype(
        key="slideshow",
        label="Trình chiếu ảnh có thuyết minh",
        chain="image_list + text_prompt → create_voice → sync_image_voice → edit",
        explicit=("slideshow", "trinh chieu", "ghep anh"),
        signals=(
            "doc truyen", "tin tuc", "phong su", "thuyet minh", "tai lieu",
            "anh tinh", "zoom pan", "ken burns", "ke chuyen", "narration",
        ),
    ),
    Archetype(
        key="affiliate",
        label="Video bán hàng / affiliate",
        chain="link_list (Shopee) → gemini_prompt → gen_image/gen_video → create_voice → align → edit",
        explicit=("affiliate", "ban hang", "e-commerce", "ecommerce"),
        signals=(
            "shopee", "tiktok shop", "lazada", "san pham", "review san pham",
            "gia ban", "cta", "khuyen mai", "giam gia", "ban hang",
        ),
        strong=("shopee", "tiktok shop", "lazada"),
    ),
    Archetype(
        key="remake",
        label="Tái tạo từ video đối thủ",
        chain="link_list (video) → analyze_video → prompt_list → gen_image → gen_video → voice → edit",
        explicit=("remake", "tai tao", "phan tich video"),
        signals=(
            "douyin", "doi thu", "hot trend", "viral", "bat chuoc video",
            "lam lai video", "clone video", "reup",
        ),
        strong=("douyin", "remake", "video doi thu"),
    ),
    Archetype(
        key="excel_batch",
        label="Sản xuất hàng loạt từ Excel",
        chain="excel_batch → prompt_list → gen_image/gen_video/create_voice → edit",
        explicit=("excel", "hang loat", "batch"),
        signals=(
            "xlsx", "bang tinh", "spreadsheet", "hang chuc kich ban",
            "so luong lon", "tu dong xuat ban", "quy mo lon",
        ),
        strong=("excel", "xlsx", "bang tinh"),
    ),
)

BY_KEY = {a.key: a for a in ARCHETYPES}

#: Below this the brief is genuinely ambiguous and the caller should ask
#: rather than build. Two signals for one shape and none for any other is a
#: decision; one signal each for three shapes is not.
_MIN_CONFIDENT_SCORE = 2


@dataclass(frozen=True)
class Choice:
    """The classification, and how it was reached."""

    archetype: Optional[Archetype]
    #: ``explicit`` · ``inferred`` · ``ask``
    reason: str
    #: What matched, for the explanation the tool owes the user before it
    #: builds a canvas.
    matched: tuple[str, ...] = ()
    #: When ``ask``, the two or three worth offering.
    options: tuple[Archetype, ...] = field(default_factory=tuple)


def fold(text: str) -> str:
    """Lowercase, unaccented, punctuation-free — for matching only.

    Briefs arrive with and without diacritics, in mixed case, with emoji.
    "Mẹ chồng", "me chong" and "MẸ CHỒNG!!" are the same signal, and a
    matcher that treats them as three is a matcher that misses two of them.
    """
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def _hits(folded: str, phrases: tuple[str, ...]) -> list[str]:
    """Phrases present as whole words, not as substrings.

    Substring matching is what makes "bac hoc" contain "bac ho": a signal
    list matched loosely fires on words that merely share letters.
    """
    found = []
    for phrase in phrases:
        needle = fold(phrase)
        if needle and re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", folded):
            found.append(phrase)
    return found


def classify(brief: str, *, requested: Optional[str] = None) -> Choice:
    """Which shape to build, or which two or three to offer.

    ``requested`` is the user naming a shape outright, and it wins whole —
    no scoring, no second-guessing. A tool that overrides what it was
    explicitly told is a tool nobody can predict.
    """
    if requested:
        key = requested.strip()
        if key in BY_KEY:
            return Choice(BY_KEY[key], "explicit", (key,))
        folded_request = fold(key)
        for arch in ARCHETYPES:
            if _hits(folded_request, arch.explicit) or fold(arch.label) in folded_request:
                return Choice(arch, "explicit", (key,))
        logger.info("archetypes: unknown request %r — falling back to the brief", key)

    folded = fold(brief)
    if not folded:
        return Choice(None, "ask", options=ARCHETYPES[:3])

    scored: list[tuple[int, list[str], Archetype]] = []
    for arch in ARCHETYPES:
        matched = _hits(folded, arch.explicit)
        if matched:
            # The user named the shape inside the brief itself.
            return Choice(arch, "explicit", tuple(matched))
        strong = _hits(folded, arch.strong)
        weak = _hits(folded, arch.signals)
        # A strong signal is worth the whole threshold on its own: a Shopee
        # link is not a hint about a product video, it is one.
        score = len(weak) + _MIN_CONFIDENT_SCORE * len(strong)
        if score:
            scored.append((score, sorted(set(strong + weak)), arch))

    if not scored:
        return Choice(None, "ask", options=ARCHETYPES[:3])

    scored.sort(key=lambda row: (-row[0], row[2].key))
    best_score, best_matched, best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0

    # A tie is not a decision. Two shapes matching equally well is exactly
    # the case the tool is told to ask about rather than pick.
    if best_score < _MIN_CONFIDENT_SCORE or best_score == runner_up:
        return Choice(
            None, "ask",
            matched=tuple(best_matched),
            options=_offer(row[2] for row in scored[:3]),
        )
    return Choice(best, "inferred", tuple(best_matched))


def _offer(candidates) -> tuple[Archetype, ...]:
    """Two or three shapes to choose between — never one.

    A weak brief can leave exactly one shape with a single matching word.
    Offering that alone is a question with one answer: it reads as a
    recommendation and gets accepted without thought, which is the guess
    this branch exists to avoid. Topped up from the most generally useful
    shapes so there is a real choice.
    """
    out: list[Archetype] = []
    seen: set[str] = set()
    for arch in list(candidates) + list(ARCHETYPES):
        if arch.key in seen:
            continue
        seen.add(arch.key)
        out.append(arch)
        if len(out) == 3:
            break
    return tuple(out[:3]) if len(out) >= 2 else tuple(out)


def describe(choice: Choice) -> str:
    """Why this shape, in one sentence — owed to the user before building."""
    if choice.reason == "explicit" and choice.archetype:
        return f"Bạn đã chọn dạng “{choice.archetype.label}”. Sơ đồ: {choice.archetype.chain}."
    if choice.reason == "inferred" and choice.archetype:
        signals = ", ".join(choice.matched[:4])
        return (
            f"Nhận diện dạng “{choice.archetype.label}” từ: {signals}. "
            f"Sơ đồ: {choice.archetype.chain}."
        )
    names = " · ".join(a.label for a in choice.options)
    return f"Brief này hợp với nhiều dạng — chọn giúp một: {names}."
