# GENERATED FILE — do not edit by hand.
# Regenerate with `python tools/voice_catalog/gen_voice_catalog.py`; see the
# README beside it. Hand edits here are lost on the next run without a word,
# which is exactly what happened once.
"""Which voice to narrate with when the name on the node is not a Gemini voice.

Two things arrive carrying a voice name this build cannot use. Templates
exported from the packaged tool carry `voice: "Ember"` — a ChatGPT app voice,
six times across the nine shipped workflows — and CapCut-flavoured projects
carry names like `Giọng Nam Trầm` or `Deadpool`. `tts.synthesize` rejects any
name outside Google's thirty, so every one of those nodes failed at the last
step, after the images and the video had already been paid for.

**Where the numbers come from.** The gender and the Vietnamese description of
each Gemini voice are the packaged tool's own `GOOGLE_VOICE_GROUPS`, mined
verbatim from the exe (3 groups: no-voice, 14 female, 16 male). Frozen here
rather than read at runtime — unlike the knowledge corpus, this is thirty short
lines describing a fixed set, and a dropdown should not cost a 0.5 s binary
scan. `median_f0_hz` is measured on the shipped sample for each voice
(autocorrelation over voiced frames, 16 kHz mono).

**How a foreign name is matched.** The Gemini voice whose measured median
speaking pitch is closest — except when the name states a gender in a gender
WORD *and* the measurement agrees that gender is possible, in which case the
match is taken from inside that gender. Both halves earn their keep: without
the first, a 314 Hz `Cute Boy` maps onto the deepest male voice available, the
worst match by the only thing a listener can hear; without the second, `Giọng
Nam Trầm` — Vietnamese for "deep male voice", measured at 152 Hz in the band
where the two groups overlap — comes back as a woman. 12 of the 67 names take
the exception.

Personal names are never read: "Lisa" measures 113 Hz, and deciding who Lisa is
would be this repo asserting something it cannot check.

**How far that can be trusted, stated rather than implied.** Leave each of the
thirty labelled voices out and match it against the other twenty-nine: the
match lands in the same gender **25 times out of 30**. All five misses sit
between 146 and 169 Hz, where the two groups genuinely overlap. So a match is a
reasonable substitute, not a claim about the speaker — which is why every
substitution warns and names both voices.

Nothing here calls CapCut's or ChatGPT's internal APIs. This is a name table.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str
    description: str
    median_f0_hz: float


@dataclass(frozen=True)
class VoiceChoice:
    """What to narrate with.

    ``voice`` is None when the node asked for no narration at all — that is a
    choice, not a failure, and falling back to a default would put a voice over
    eleven of the nine packaged workflows that deliberately have none.
    """

    voice: str | None
    warning: str | None
    source: str  # exact | alias | no_voice | unknown | refused


#: Google's thirty prebuilt TTS voices, in the order Google documents
#: them. Gender and description are the packaged tool's own
#: GOOGLE_VOICE_GROUPS; the pitch is measured on the shipped sample.
CATALOG: tuple[Voice, ...] = (
    Voice("Zephyr", "female", "Nữ, tươi sáng, nổi bật, âm vực trung-cao.", 225.4),
    Voice("Puck", "male", "Nam, vui vẻ, tích cực, âm vực trung.", 110.3),
    Voice("Charon", "male", "Nam, giàu tính thuyết minh, âm vực thấp.", 168.4),
    Voice("Kore", "female", "Nữ, chắc chắn, dứt khoát, âm vực trung.", 254.0),
    Voice("Fenrir", "male", "Nam, hào hứng, sôi nổi, chất giọng trẻ.", 145.5),
    Voice("Leda", "female", "Nữ, trẻ trung, tươi mới, chất giọng trẻ.", 177.8),
    Voice("Orus", "male", "Nam, mạnh mẽ, chắc giọng, âm vực trung-thấp.", 134.5),
    Voice("Aoede", "female", "Nữ, nhẹ nhàng, phóng khoáng, âm vực trung.", 181.9),
    Voice("Callirrhoe", "female", "Nữ, thoải mái, tự nhiên, âm vực trung.", 152.4),
    Voice("Autonoe", "female", "Nữ, tươi sáng, rõ ràng, âm vực trung.", 179.8),
    Voice("Enceladus", "male", "Nam, nhiều hơi, nhẹ và mềm, âm vực thấp.", 124.5),
    Voice("Iapetus", "male", "Nam, rõ nét, mạch lạc, âm vực trung-thấp.", 167.5),
    Voice("Umbriel", "male", "Nam, mượt mà, nhẹ nhàng, âm vực thấp.", 130.1),
    Voice("Algieba", "male", "Nam, thoải mái, dễ nghe, âm vực trung-thấp.", 99.1),
    Voice("Despina", "female", "Nữ, mượt mà, âm vực trung.", 271.2),
    Voice("Erinome", "female", "Nữ, trong trẻo, rõ chữ, âm vực trung.", 176.8),
    Voice("Algenib", "male", "Nam, khàn và có độ sạn, âm vực thấp.", 95.0),
    Voice("Rasalgethi", "male", "Nam, giàu tính thông tin, âm vực trung.", 148.1),
    Voice("Laomedeia", "female", "Nữ, vui tươi, giàu năng lượng, âm vực trung-cao.", 242.4),
    Voice("Achernar", "female", "Nữ, mềm mại, âm vực cao.", 200.0),
    Voice("Alnilam", "male", "Nam, chắc chắn, dứt khoát, âm vực trung-thấp.", 132.2),
    Voice("Schedar", "male", "Nam, đều đặn, ổn định, âm vực trung-thấp.", 125.0),
    Voice("Gacrux", "female", "Nữ, trưởng thành, chững chạc, âm vực trung.", 207.8),
    Voice("Pulcherrima", "female", "Nữ, tự tin, tiến về phía trước, âm vực trung.", 168.4),
    Voice("Achird", "male", "Nam, thân thiện, âm vực trung.", 162.4),
    Voice("Zubenelgenubi", "male", "Nam, tự nhiên, đời thường, âm vực trung-thấp.", 139.1),
    Voice("Vindemiatrix", "female", "Nữ, dịu dàng, mềm mại, âm vực trung.", 175.8),
    Voice("Sadachbia", "male", "Nam, sinh động, hoạt bát, âm vực thấp.", 146.8),
    Voice("Sadaltager", "male", "Nam, hiểu biết, mang chất chuyên gia, âm vực trung.", 168.4),
    Voice("Sulafat", "female", "Nữ, ấm áp, gần gũi, âm vực trung.", 222.2),
)

#: The packaged tool's own `_is_no_voice` frozenset, mined verbatim (8 literals),
#: folded. The last two are NOT in that frozenset and are added deliberately:
#: `🗣️ Không chọn` is what the shipped workflows actually store in `voice` —
#: eleven times across the nine of them — and reading those aloud with a default
#: voice would put narration over videos built to have none.
NO_VOICE: frozenset[str] = frozenset({
    "no voice",
    "none",
    "khong giong",
    "khong tao giong",
    "tat giong",
    "khong chon",
    "khong chon giong doc",
})

#: Not a voice at all: the packaged tool clones a voice through a third-party
#: service driven by a browser-impersonating HTTP client. That bridge is not
#: built here (see docs/spec.md), so the name gets a substitute and a sentence
#: saying which feature is missing, rather than a silent default.
REFUSED: frozenset[str] = frozenset({"clone voice"})


#: OpenAI's documented speech voices, the set `resolve_openai` may return.
#: `openai_tts.VOICES` is the same list; a test locks the two together, because
#: two copies of a vendor list is exactly the kind of pair that drifts.
OPENAI_VOICES: frozenset[str] = frozenset({
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer", "verse",
})

#: ChatGPT *app* voice name → an OpenAI *API* speech voice.
#:
#: Provenance, stated because it differs from every other number in this file:
#: the nine app names and their character come from OpenAI's published voice
#: line-up, NOT from measurement. There are no local samples of the API voices,
#: so "nearest by measured pitch" — the rule the Gemini table uses — cannot be
#: computed here, and pretending otherwise would be the one thing this file is
#: careful not to do. One basis per table: these picks follow the published
#: descriptions throughout, including where that disagrees with the pitch
#: measured off the app sample (`vale` measures 160 Hz, inside the male band,
#: and is a female voice in the line-up).
#:
#: A pick that sounds wrong is one Settings change away: `OPENAI_TTS_VOICE`
#: overrides the whole table.
OPENAI_BY_APP_VOICE: dict[str, str] = {
    "breeze": "shimmer",
    "cove": "onyx",
    "ember": "echo",
    "fathom": "onyx",
    "glimmer": "nova",
    "juniper": "sage",
    "maple": "coral",
    "orbit": "ash",
    "vale": "sage",
}

#: Foreign voice name → (nearest Gemini voice, its measured pitch,
#: which of the two rules picked it).
#: 9 ChatGPT app voices + 22 Vietnamese and 36 English CapCut voices,
#: every one of them a sample file shipped with the packaged tool.
ALIASES: dict[str, tuple[str, float, str]] = {
    # gpt
    "breeze": ("Pulcherrima", 168.4, "pitch"),
    "cove": ("Algenib", 91.2, "pitch"),
    "ember": ("Puck", 113.5, "pitch"),
    "fathom": ("Puck", 105.6, "pitch"),
    "glimmer": ("Achernar", 192.8, "pitch"),
    "juniper": ("Achernar", 192.8, "pitch"),
    "maple": ("Achernar", 202.5, "pitch"),
    "orbit": ("Algieba", 101.9, "pitch"),
    "vale": ("Achird", 160.0, "pitch"),
    # capcut_vi
    "alex dai de": ("Achird", 164.9, "pitch"),
    "ban mai": ("Gacrux", 210.5, "pitch"),
    "ban tin 1": ("Sulafat", 216.2, "pitch"),
    "ban tin nu": ("Gacrux", 209.2, "name+pitch"),
    "co gai hoat ngon": ("Laomedeia", 242.4, "name+pitch"),
    "giong be": ("Despina", 290.9, "pitch"),
    "giong gai moi lon": ("Despina", 266.7, "name+pitch"),
    "giong nam tram": ("Rasalgethi", 152.4, "name+pitch"),
    "giong nu pho thong": ("Achernar", 200.0, "name+pitch"),
    "kenny dai de": ("Enceladus", 120.3, "pitch"),
    "mai": ("Kore", 256.0, "pitch"),
    "nam ban tin": ("Laomedeia", 238.8, "pitch"),
    "nho ngot ngao": ("Laomedeia", 246.2, "pitch"),
    "quen ten tu test": ("Laomedeia", 248.1, "pitch"),
    "review phim 2": ("Laomedeia", 242.4, "pitch"),
    "review phim 3": ("Gacrux", 213.3, "pitch"),
    "review phim 4": ("Zephyr", 225.4, "pitch"),
    "review phim new": ("Achernar", 201.3, "pitch"),
    "robot vn": ("Achird", 163.3, "pitch"),
    "sunny idol": ("Zephyr", 228.6, "pitch"),
    "thanh nien tu tin": ("Achird", 155.3, "name+pitch"),
    "viet meo": ("Achird", 158.4, "pitch"),
    # capcut_en
    "american female": ("Zephyr", 225.4, "name+pitch"),
    "artist": ("Kore", 254.0, "pitch"),
    "bluetooth": ("Achernar", 192.8, "pitch"),
    "brianjw": ("Vindemiatrix", 175.8, "pitch"),
    "captain": ("Despina", 307.7, "pitch"),
    "chrming male": ("Iapetus", 165.0, "name+pitch"),
    "classical music": ("Despina", 301.9, "pitch"),
    "creepy female": ("Laomedeia", 235.3, "name+pitch"),
    "cute boy": ("Despina", 313.7, "pitch"),
    "cute girl": ("Despina", 275.9, "pitch"),
    "daiana": ("Achernar", 197.5, "pitch"),
    "deadpool": ("Enceladus", 118.5, "pitch"),
    "dolly famle": ("Despina", 280.7, "pitch"),
    "dramaaa": ("Achernar", 195.1, "pitch"),
    "emotional": ("Gacrux", 213.3, "pitch"),
    "en us 2": ("Vindemiatrix", 175.8, "pitch"),
    "en us": ("Despina", 290.9, "pitch"),
    "energetic famale": ("Despina", 271.2, "name+pitch"),
    "english": ("Umbriel", 128.0, "pitch"),
    "excited": ("Kore", 258.1, "pitch"),
    "female teacher": ("Achernar", 195.1, "name+pitch"),
    "grim rock": ("Sulafat", 216.2, "pitch"),
    "janeamber": ("Sulafat", 222.2, "pitch"),
    "jessie": ("Laomedeia", 248.1, "pitch"),
    "kai": ("Fenrir", 146.1, "pitch"),
    "lisa": ("Puck", 113.5, "pitch"),
    "male profess": ("Puck", 105.3, "name+pitch"),
    "mentor": ("Enceladus", 122.1, "pitch"),
    "mischief": ("Sulafat", 219.2, "pitch"),
    "narrator": ("Enceladus", 120.3, "pitch"),
    "oogie": ("Sulafat", 222.2, "pitch"),
    "robaa": ("Achernar", 197.5, "pitch"),
    "sherry": ("Gacrux", 213.3, "pitch"),
    "suaraaa": ("Kore", 262.3, "pitch"),
    "tinn": ("Sulafat", 222.2, "pitch"),
    "trickster": ("Fenrir", 142.9, "pitch"),
}


BY_NAME: dict[str, Voice] = {v.name.casefold(): v for v in CATALOG}

_KEEP = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def fold(name: str) -> str:
    """Lowercase, unaccented, punctuation- and emoji-free.

    The values that arrive are dropdown labels, so they carry whatever the UI
    put in front of them — `🗣️ Không chọn` is stored verbatim in the packaged
    workflows. Comparing those raw would mean listing every decoration.
    """
    text = unicodedata.normalize("NFD", (name or "").strip().lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("\u0111", "d").replace("_", " ")
    return _SPACES.sub(" ", _KEEP.sub(" ", text)).strip()


def resolve_openai(name: str | None, *, default: str) -> VoiceChoice:
    """Pick an OpenAI API speech voice for whatever the node says.

    Separate from `resolve` because the two engines have different catalogues
    and the same node text can be sent to either. Folding them into one
    function would mean one of the two silently accepting a name the other
    owns — a Gemini voice reaching `/v1/audio/speech` is a 400 arriving after
    the request has already been made.
    """
    folded = fold(name or "")
    if not folded:
        return VoiceChoice(default, None, "unknown")
    if folded in NO_VOICE:
        return VoiceChoice(None, None, "no_voice")
    if folded in OPENAI_VOICES:
        return VoiceChoice(folded, None, "exact")

    mapped = OPENAI_BY_APP_VOICE.get(folded)
    if mapped is not None:
        return VoiceChoice(
            mapped,
            f"giọng {name!r} là giọng của ứng dụng ChatGPT, không phải giọng API "
            f"— đã dùng {mapped!r} theo mô tả giọng OpenAI công bố. Đổi "
            f"OPENAI_TTS_VOICE trong Cài đặt nếu muốn giọng khác.",
            "alias",
        )
    return VoiceChoice(
        default,
        f"giọng {name!r} không có trong danh mục giọng OpenAI — đã đọc bằng "
        f"{default!r}.",
        "unknown",
    )


def resolve(name: str | None, *, default: str) -> VoiceChoice:
    """Pick a usable voice for ``name``, and say so when it is a substitute."""
    folded = fold(name or "")
    if not folded:
        # An empty voice is not a request for silence: the packaged workflows
        # say so out loud when they want none. Treat it as "unset" and narrate
        # with the default, the way every other unset setting behaves.
        return VoiceChoice(default, None, "unknown")

    if folded in NO_VOICE:
        return VoiceChoice(None, None, "no_voice")

    exact = BY_NAME.get(folded)
    if exact is not None:
        return VoiceChoice(exact.name, None, "exact")

    if folded in REFUSED:
        return VoiceChoice(
            default,
            f"giọng {name!r} là giọng nhân bản của bản đóng gói — bản này không "
            f"nhân bản giọng, đã đọc bằng {default!r}. Chọn một giọng Gemini "
            f"trên node nếu muốn giọng khác.",
            "refused",
        )

    alias = ALIASES.get(folded)
    if alias is not None:
        target, measured, rule = alias
        how = "giới tính nêu trong tên và cao độ" if rule == "name+pitch" else "cao độ"
        return VoiceChoice(
            target,
            f"giọng {name!r} không có ở Gemini — đã dùng {target!r}, gần nhất "
            f"theo {how} đo được ({measured:.0f} Hz ↔ "
            f"{BY_NAME[target.casefold()].median_f0_hz:.0f} Hz).",
            "alias",
        )

    return VoiceChoice(
        default,
        f"giọng {name!r} không có trong danh mục — đã đọc bằng {default!r}. "
        f"Chọn lại giọng trên node nếu muốn giọng khác.",
        "unknown",
    )
