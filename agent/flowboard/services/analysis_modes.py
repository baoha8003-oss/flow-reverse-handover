"""The four analysis system prompts the packaged tool ships with.

``data_general/system_prompts/`` holds four files — ``standard_mode.txt``,
``text_mode.txt``, ``stick_figure_mode.txt``, ``health_mode.txt``, between
4KB and 18KB each — and until now nothing in this build read any of them.
They are what turns "describe this clip" into the packaged tool's actual
analysis: a scene-by-scene breakdown with an image prompt, a Veo prompt and
a dialogue line per scene, plus a Vietnamese script and social copy.

Four of the shipped workflows set ``analysis_mode`` on their `analyze_video`
node, so this is not a hypothetical setting: `nguoi_que_new` asks for
``"🧍 Video Người Que"`` and `tap_hoa` for ``"📹 Video thường TEXT"``.

**How a label picks a file.** Mined from the binary rather than guessed —
the exe upper-cases the label and tests for ``TEXT`` first, then for the
Vietnamese words for stick-figure and health, falling through to the
standard mode. The labels arrive decorated with an emoji, which is why every
test here is a substring test.

**Placeholders.** Each file carries ``__NAME__`` slots the tool fills at
call time. The defaults below are the exe's own, read out of the binary's
string pool next to the file names. Any slot left unfilled is STRIPPED
rather than passed through: a literal ``__VOICE_TEXT__`` in the prompt is a
token the model has to interpret, and it interprets it as an instruction it
cannot satisfy.

Nine of those slots carry text that lives in the executable rather than in the
files — the lip-sync rules, the output-format contract, the three voice modes
and the five few-shot examples. `system_prompt_slots` mines them; stripping
them was safe but expensive, because `__OUTPUT_FORMAT_RULE__` alone is what
asks for `title` and `thumbnail_prompt`, two fields this build never received.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from flowboard.services import assets

logger = logging.getLogger(__name__)

PROMPT_DIR = assets.DATA_DIR / "system_prompts"

#: Internal mode name -> the file that carries its system prompt.
_FILES: dict[str, str] = {
    "text": "text_mode.txt",
    "stick_figure": "stick_figure_mode.txt",
    "health": "health_mode.txt",
    "standard": "standard_mode.txt",
}

#: Substrings that select a mode, in the order the exe tests them. Order is
#: load-bearing: "📹 Video thường TEXT" contains both "text" and "video".
_LABELS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("text",), "text"),
    (("người que", "nguoi que", "stick"), "stick_figure"),
    (("sức khỏe", "suc khoe", "health"), "health"),
)

#: Per-mode defaults for the slots the caller does not fill. Read from the
#: binary's own string pool, which stores them adjacent to the file names.
_DEFAULTS: dict[str, dict[str, str]] = {
    "standard": {
        "STYLE_TEXT": "Cinematic, high quality",
        "VOICE_TEXT": (
            "Analyze and preserve only the audible voice traits from the "
            "source; do not invent an accent or vocal quality that cannot "
            "be heard."
        ),
    },
    "stick_figure": {
        "STYLE_TEXT": "Stick figure animation",
        "VOICE_TEXT": "Natural voice",
    },
    "health": {
        "STYLE_TEXT": (
            "clean modern health explainer video, realistic but safe, bright "
            "clinic/healthy lifestyle visual style"
        ),
        "VOICE_TEXT": "natural clear Vietnamese educational narrator voice",
        "CUSTOM_TEXT": "Analyze the health video faithfully and safely.",
    },
    "text": {
        "STYLE_TEXT": "Cinematic, high quality",
        "VOICE_TEXT": (
            "Analyze and preserve only the audible voice traits from the "
            "source; do not invent an accent or vocal quality that cannot "
            "be heard."
        ),
    },
}

_PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")


#: Labels that mean "nobody speaks" and "a narrator speaks". Inferred from the
#: wording of the packaged workflows' `voice` setting — the executable decides
#: on a `dialogue_mode` value (`no_dialogue` / `narration` / anything else) that
#: this build has no other way to learn. Said plainly because it is the one
#: piece of this module that is not mined.
_SILENT_LABELS = ("không chọn", "khong chon", "no voice", "none", "tắt", "off")
_NARRATION_LABELS = ("thuyết minh", "thuyet minh", "narration", "narrator", "voiceover")


def dialogue_mode(voice_label: object) -> str:
    """``dialogue`` · ``narration`` · ``no_dialogue`` for a voice setting."""
    text = str(voice_label or "").strip().lower()
    # EMPTY means "not set", which is the ordinary state of a node nobody
    # configured — and the two mistakes are not symmetric. Defaulting to
    # silence produces an analysis with no dialogue anywhere and
    # `mouth_locked=true` on every scene, which is a whole feature missing and
    # hard to notice; defaulting to speech produces lines the user can ignore.
    if not text:
        return "dialogue"
    if any(needle in text for needle in _SILENT_LABELS):
        return "no_dialogue"
    if any(needle in text for needle in _NARRATION_LABELS):
        return "narration"
    return "dialogue"


def _style_line(style: str) -> str:
    style = (style or "").strip()
    return f"Visual style: {style}." if style else ""


def _voice_line(voice: str) -> str:
    voice = (voice or "").strip()
    return f"Voice: {voice}." if voice else ""


def mode_for(label: object) -> str:
    """Which of the four modes a workflow's `analysis_mode` label names.

    Unknown, empty, or absent all mean `standard` — the mode the packaged
    tool falls through to. Matched on substrings because the shipped labels
    carry an emoji and their own wording ("📹 Video thường TEXT").
    """
    text = str(label or "").strip().lower()
    if not text:
        return "standard"
    for needles, mode in _LABELS:
        if any(needle in text for needle in needles):
            return mode
    return "standard"


def available() -> bool:
    """Whether the prompt files are where the asset root says they are."""
    return all((PROMPT_DIR / name).is_file() for name in _FILES.values())


def load(mode: str) -> Optional[str]:
    """The raw system prompt for a mode, or None when the file is missing.

    None rather than raising: the asset root is a directory on someone
    else's machine, and a caller that can fall back to its own built-in
    prompt should be allowed to.
    """
    name = _FILES.get(mode)
    if name is None:
        return None
    path = PROMPT_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.info("analysis_modes: cannot read %s (%s)", path, exc)
        return None


def _reference_block(names: list[str]) -> str:
    """The paragraph the exe prepends when reference images are supplied.

    Verbatim from the binary. Its point is that the reference's palette wins
    over the source video's, which is the whole reason the stick-figure
    workflow supplies one.
    """
    if not names:
        return ""
    return (
        "REFERENCE IMAGES provided for: " + ", ".join(names) + ". "
        "Extract color palette, character style, background style, and "
        "background color from the reference image. For example, if the "
        "reference image has a solid black background with white drawings, "
        "the prompts MUST generate a solid black background with white "
        "drawings. Do NOT use white backgrounds if the reference image has "
        "a black background. The style, color palette, and background tone "
        "of the reference image OVERRIDES the style of the original video "
        "and MUST be applied consistently across ALL scenes.\n\n"
    )


def build(
    label: object,
    *,
    language: str = "Vietnamese",
    scene_count: Optional[int] = None,
    duration_text: str = "Original video length",
    style: str = "",
    voice: str = "",
    custom: str = "",
    characters: Optional[list[str]] = None,
    reference_names: Optional[list[str]] = None,
) -> tuple[str, str]:
    """``(mode, system_prompt)`` for one analysis call.

    Returns the resolved mode alongside the text so a caller can report
    which one ran — the label is the user's wording, the mode is what
    actually happened, and when a workflow is imported those two are worth
    being able to compare.
    """
    mode = mode_for(label)
    raw = load(mode)
    if raw is None:
        return mode, ""

    characters = characters or []
    reference_names = reference_names or []
    # The nine slots whose text is inside the executable. Absent (no exe on
    # this machine) they stay stripped, exactly as before.
    from flowboard.services import system_prompt_slots

    mined = dict(system_prompt_slots.rules())
    mined.update(system_prompt_slots.examples(language))
    rule = system_prompt_slots.voice_rule(dialogue_mode(voice))
    if rule:
        mined["VOICE_RULE"] = rule
    # `__EXTRA_BLOCK__` is not mined: the packaged tool fills it with the
    # caller's OWN custom instruction plus the style and voice lines, which is
    # also how `standard_mode` gets a style at all — it has no `__STYLE_TEXT__`
    # slot of its own.
    extra = "\n".join(
        part.strip() for part in (custom, _style_line(style), _voice_line(voice))
        if part and part.strip()
    )
    if extra:
        mined["EXTRA_BLOCK"] = extra
    scene_header = (
        f"Produce EXACTLY {scene_count} scenes.\n\n" if scene_count else ""
    )

    values = {
        "LANG_INSTRUCTION": language,
        "LANG_NAME": language,
        "SCENE_COUNT_HEADER": scene_header,
        "SCENE_COUNT_FOOTER": (
            f"Remember: exactly {scene_count} scenes." if scene_count else ""
        ),
        "DURATION_TEXT": duration_text,
        "DUR_TEXT": duration_text,
        "REF_BLOCK": _reference_block(reference_names),
        "REF_PRE_INSTRUCTION": _reference_block(reference_names),
        "CHAR_NAMES_TEXT": (
            "Characters to focus on: " + ", ".join(characters) if characters else ""
        ),
        "CHAR_IDS": ", ".join(characters),
    }
    values.update(mined)
    for key, value in _DEFAULTS.get(mode, {}).items():
        values.setdefault(key, value)
    for key, value in (
        ("STYLE_TEXT", style), ("VOICE_TEXT", voice), ("CUSTOM_TEXT", custom),
    ):
        if value and value.strip():
            values[key] = value.strip()

    text = raw
    for key, value in values.items():
        text = text.replace(f"__{key}__", value)
    # Anything still in placeholder form is a slot this build does not fill.
    # Leaving it would put a literal `__VOICE_RULE__` in front of the model.
    left = sorted(set(_PLACEHOLDER.findall(text)))
    if left:
        # Named, not silent: a stripped slot is missing instruction, and the
        # next person to wonder why an analysis ignored a rule should find the
        # slot's name in the log rather than have to diff the prompt.
        logger.info("analysis_modes: %s left unfilled in %s", ", ".join(left), mode)
    text = _PLACEHOLDER.sub("", text)

    # Only `standard_mode` and `text_mode` carry a scene-count slot. The
    # other two would swallow the setting silently, which is the worst of
    # the three outcomes: the caller asked for five scenes, got however many
    # the model felt like, and nothing said why. Appended instead — and the
    # end of a system prompt is a strong position for an instruction.
    if scene_count and "__SCENE_COUNT_HEADER__" not in raw:
        text += f"\n\nProduce EXACTLY {scene_count} scenes."
    return mode, text.strip()
