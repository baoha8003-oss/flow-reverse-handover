"""The nine slots the analysis prompts were losing.

`analysis_modes` strips any placeholder it cannot fill, which is right — a
literal `__VOICE_RULE__` is a token the model tries to obey. It was also
expensive, because the text for nine of them lives in the EXECUTABLE rather
than in the prompt files, and nothing here had ever read it:

* `__OUTPUT_FORMAT_RULE__` is what asks for `title` and `thumbnail_prompt`;
  without it the analysis never returns either, and the node has nowhere to get
  a cover-frame prompt from;
* `__DIALOGUE_RULE__` is what stops a sentence being cut in half across two
  scenes — the failure `narration.split_text` exists to prevent, one layer up;
* `__VOICE_RULE__` decides whether the characters speak AT ALL.

Skipped whole when the executable is not on this machine: it is someone else's
file, and every assertion here is about its content.
"""
from __future__ import annotations

import re

import pytest

from flowboard.services import analysis_modes, system_prompt_slots

pytestmark = pytest.mark.skipif(
    not system_prompt_slots.blocks(),
    reason="the packaged executable is not on this machine",
)


def test_every_anchor_finds_its_block():
    found = system_prompt_slots.blocks()
    assert set(found) == set(system_prompt_slots._ANCHORS), sorted(found)


def test_the_blocks_are_the_size_of_rules_not_of_the_binary():
    """A bad anchor would hand back a slab of the executable. The output format
    rule is the biggest of the nine at ~2.4 KB."""
    for name, text in system_prompt_slots.blocks().items():
        assert 20 <= len(text) <= 4000, (name, len(text))


def test_the_voice_example_is_the_line_not_the_scene():
    """"I will come back soon." appears twice: on its own, and as a line inside
    the English video example. The first match in the file is the long one, so
    taking it would put a whole scene where a spoken line belongs."""
    blocks = system_prompt_slots.blocks()
    assert blocks["VOICE_EXAMPLE_EN"] == "I will come back soon."
    assert "camera" in blocks["VIDEO_EXAMPLE_EN"]


def test_the_three_voice_rules_are_three_different_rules():
    dialogue = system_prompt_slots.voice_rule("dialogue")
    narration = system_prompt_slots.voice_rule("narration")
    silent = system_prompt_slots.voice_rule("no_dialogue")
    assert "MUST SPEAK" in dialogue
    assert "do NOT speak" in narration
    assert "MUST BE EMPTY" in silent
    assert len({dialogue, narration, silent}) == 3


def test_the_examples_follow_the_language():
    """An example in the wrong language teaches the model the wrong language."""
    vi = system_prompt_slots.examples("Vietnamese")
    en = system_prompt_slots.examples("English")
    assert "Người phụ nữ" in vi["IMAGE_EXAMPLE"]
    assert "A middle-aged woman" in en["IMAGE_EXAMPLE"]
    # The OUTPUT skeleton shows the same value, so it takes the same text
    # rather than a second invented one.
    assert vi["OUTPUT_IMAGE_EXAMPLE"] == vi["IMAGE_EXAMPLE"]


def test_the_scan_is_cached():
    first = system_prompt_slots.blocks()
    assert system_prompt_slots.blocks() is first


# ── what the analysis prompt looks like once they are filled ──────────


def _built(label: str, **kw) -> str:
    return analysis_modes.build(label, **kw)[1]


def test_the_standard_prompt_carries_its_rules():
    text = _built("", language="Vietnamese", voice="Nữ miền Bắc")
    assert "DIALOGUE INTEGRITY" in text
    assert "OUTPUT FORMAT (MANDATORY JSON STRUCTURE)" in text
    assert "thumbnail_prompt" in text, "the field the node needs a cover from"
    assert "VOICE DIRECTION (MANDATORY)" in text


def test_nothing_is_left_as_a_placeholder():
    for label in ("", "📹 Video thường TEXT", "🧍 Video Người Que", "💊 Sức khỏe"):
        text = _built(label, language="Vietnamese", style="cinematic")
        assert not re.findall(r"__[A-Z0-9_]+__", text), label


def test_the_text_mode_gets_its_few_shot_examples():
    text = _built("📹 Video thường TEXT", language="Vietnamese")
    assert "Người phụ nữ trung niên tên Lan" in text


def test_the_extra_block_is_where_standard_mode_gets_a_style():
    """`standard_mode.txt` has no `__STYLE_TEXT__` slot of its own — the
    packaged tool puts the style, the voice and the caller's custom instruction
    into `__EXTRA_BLOCK__`, and stripping it dropped all three."""
    text = _built("", style="ánh sáng vàng ấm", custom="giữ nguyên bối cảnh")
    assert "ánh sáng vàng ấm" in text
    assert "giữ nguyên bối cảnh" in text


# ── which voice rule a setting means ──────────────────────────────────


@pytest.mark.parametrize(
    "label,expected",
    [
        ("", "dialogue"),
        (None, "dialogue"),
        ("Nữ miền Bắc", "dialogue"),
        ("🗣️ Không chọn", "no_dialogue"),
        ("no voice", "no_dialogue"),
        ("🗣️ Thuyết minh", "narration"),
        ("Narrator", "narration"),
    ],
)
def test_a_voice_label_picks_a_dialogue_mode(label, expected):
    assert analysis_modes.dialogue_mode(label) == expected


def test_an_unset_voice_does_not_silence_the_characters():
    """The two mistakes are not symmetric. Defaulting to silence gives every
    scene `mouth_locked=true` and no dialogue anywhere — a whole feature
    missing, and hard to notice. Defaulting to speech gives lines the user can
    ignore."""
    text = _built("", voice="")
    assert "STRICT NO DIALOGUE" not in text
    assert "VOICE DIRECTION (MANDATORY)" in text


def test_choosing_no_voice_really_does_silence_them():
    text = _built("", voice="🗣️ Không chọn")
    assert "MUST BE EMPTY" in text
