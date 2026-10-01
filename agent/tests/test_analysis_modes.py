"""The four analysis system prompts the packaged tool ships with.

`data_general/system_prompts/` holds four files, 4KB to 18KB each, and
nothing in this build read any of them until now. Four of the shipped
workflows set `analysis_mode` on their `analyze_video` node, so the setting
was arriving and being dropped: `nguoi_que_new` asks for "🧍 Video Người Que"
and `tap_hoa` for "📹 Video thường TEXT".

The label-to-file rules are mined from the binary, not guessed: the exe
upper-cases the label and tests for TEXT first, then the Vietnamese words
for stick-figure and health, falling through to standard.
"""
from __future__ import annotations

import pytest

from flowboard.services import analysis_modes

pytestmark = pytest.mark.skipif(
    not analysis_modes.available(),
    reason="system_prompts/ not present under the asset root",
)


# ── which file a label picks ──────────────────────────────────────────


@pytest.mark.parametrize(
    "label, expected",
    [
        # Verbatim from the shipped workflows.
        ("🧍 Video Người Que", "stick_figure"),
        ("📹 Video thường TEXT", "text"),
        # The exe tests TEXT first, and this label contains both words.
        ("📹 Video thường", "standard"),
        ("💊 Video sức khỏe", "health"),
        ("stick figure", "stick_figure"),
        ("health", "health"),
    ],
)
def test_a_label_picks_its_mode(label, expected):
    assert analysis_modes.mode_for(label) == expected


@pytest.mark.parametrize("label", ["", None, "   ", "một kiểu lạ"])
def test_anything_unrecognised_falls_through_to_standard(label):
    """The exe's own fallthrough. An imported workflow from a newer build
    may carry a label this one has never seen, and analysing it with the
    standard prompt beats refusing to analyse it."""
    assert analysis_modes.mode_for(label) == "standard"


def test_text_is_tested_before_the_others():
    """Order is load-bearing, not incidental: "📹 Video thường TEXT" would
    match a looser rule for the plain video mode too."""
    assert analysis_modes.mode_for("📹 Video thường TEXT") == "text"


# ── building the prompt ───────────────────────────────────────────────


def test_every_mode_loads_a_real_prompt():
    for label, mode in (
        ("", "standard"), ("TEXT", "text"),
        ("người que", "stick_figure"), ("sức khỏe", "health"),
    ):
        got_mode, text = analysis_modes.build(label)
        assert got_mode == mode
        assert len(text) > 1000, f"{mode} prompt looks truncated"


def test_no_placeholder_survives_into_the_prompt():
    """A literal `__VOICE_TEXT__` reaching the model is a token it has to
    interpret, and it interprets it as an instruction it cannot satisfy.
    Slots this build does not fill are stripped, not passed through."""
    for label in ("", "TEXT", "người que", "sức khỏe"):
        _, text = analysis_modes.build(label)
        assert "__" not in text, f"unfilled placeholder in {label!r}: {text[:200]}"


def test_the_language_reaches_the_prompt():
    _, text = analysis_modes.build("người que", language="English")
    assert "English" in text


@pytest.mark.parametrize("label", ["", "TEXT", "người que", "sức khỏe"])
def test_a_scene_count_reaches_every_mode(label):
    """`lock_scene_count` in the packaged tool — pin the breakdown instead
    of letting the model choose how many scenes there are.

    Only `standard_mode` and `text_mode` carry a slot for it. The other two
    would swallow the setting silently, which is the worst outcome
    available: the caller asked for five scenes, got however many the model
    felt like, and nothing said why."""
    _, text = analysis_modes.build(label, scene_count=5)
    assert "EXACTLY 5 scenes" in text


@pytest.mark.parametrize("label", ["", "TEXT", "người que", "sức khỏe"])
def test_no_scene_count_states_no_number(label):
    _, text = analysis_modes.build(label)
    assert "EXACTLY 5" not in text and "EXACTLY None" not in text


def test_a_custom_style_overrides_the_modes_default():
    _, default = analysis_modes.build("người que")
    assert "Stick figure animation" in default
    _, custom = analysis_modes.build("người que", style="watercolour storybook")
    assert "watercolour storybook" in custom


def test_reference_images_add_the_palette_override_block():
    """The reference's palette has to win over the source video's — the
    whole reason the stick-figure workflow supplies one."""
    _, without = analysis_modes.build("người que")
    _, with_ref = analysis_modes.build("người que", reference_names=["nhân vật A"])
    assert "OVERRIDES" in with_ref and "nhân vật A" in with_ref
    assert "OVERRIDES" not in without


def test_an_unknown_mode_name_loads_nothing_rather_than_a_wrong_file():
    assert analysis_modes.load("not_a_mode") is None
