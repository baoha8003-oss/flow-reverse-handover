"""Rules checked without spending a model call.

The two headline rules point in opposite directions, and that asymmetry is
what these tests exist to hold:

* a character tag belongs in the visual description and **must not** appear
  in spoken text, because it gets read aloud as "at at Mai Anh";
* a real person's name **must not** appear in the visual description, because
  it trips Google's celebrity/trademark filter and the request is refused —
  but it is fine in the voiceover, which a separate voice model speaks and
  that filter never sees.

Implement them in the same direction and you either reject every legitimate
historical narration or let every visual prompt fail at dispatch. Both halves
are asserted here.
"""
from __future__ import annotations

import pytest

from flowboard.services import prompt_checks as pc


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


# ── splitting visual from spoken ──────────────────────────────────────


def test_quoted_text_is_dialogue():
    visual, spoken = pc.split_dialogue('@@Mai đứng trước gương, nói "Con về rồi đây mẹ"')
    assert "Con về rồi đây mẹ" in spoken
    assert "@@Mai" in visual


def test_a_marker_line_is_dialogue():
    visual, spoken = pc.split_dialogue("Cảnh bếp lúc chiều\nLời thoại: Con ăn cơm chưa")
    assert "Con ăn cơm chưa" in spoken
    assert "Cảnh bếp" in visual


def test_text_before_a_marker_stays_visual():
    """A single line often carries both: description, then the line spoken."""
    visual, spoken = pc.split_dialogue("@@Mai bước vào bếp. Lời thoại: Con về rồi")
    assert "@@Mai bước vào bếp" in visual
    assert "Con về rồi" in spoken


def test_an_unrecognised_dialogue_form_defaults_to_visual():
    """The safe default. Treating unknown text as visual makes a tag in it
    noisy; treating it as dialogue would make a celebrity name in it
    silent, and silent is the expensive direction."""
    visual, spoken = pc.split_dialogue("Ngô Quyền chỉ huy trận Bạch Đằng")
    assert "Ngô Quyền" in visual and not spoken.strip()


# ── tags: required in visual, forbidden in dialogue ───────────────────


def test_a_tag_in_dialogue_is_an_error():
    found = pc.check('@@Mai mỉm cười, nói "Chào @@Lan nhé"', cast=["Mai", "Lan"])
    assert "tag_in_dialogue" in _rules(found)


def test_a_tag_in_the_visual_half_is_fine():
    found = pc.check("@@Mai đứng nghiêng trước gương", cast=["Mai"])
    assert "tag_in_dialogue" not in _rules(found)
    assert "unknown_tag" not in _rules(found)


def test_a_tag_naming_nobody_is_an_error():
    """A tag that resolves to no character hands the generator a bare token
    where a reference was meant."""
    found = pc.check("@@Khong_Ton_Tai đứng đó", cast=["Mai"])
    assert "unknown_tag" in _rules(found)


def test_tags_are_not_checked_against_an_empty_cast():
    """No cast supplied means the caller does not know the names, not that
    every name is wrong."""
    assert "unknown_tag" not in _rules(pc.check("@@Mai đứng đó"))


# ── the character ceiling ─────────────────────────────────────────────


def test_four_characters_on_a_lite_lane_is_an_error():
    prompt = "@@A @@B @@C @@D cùng ngồi bàn ăn"
    found = pc.check(prompt, cast=list("ABCD"), lane="lite_relaxed")
    assert "too_many_characters" in _rules(found)


def test_three_characters_on_a_lite_lane_is_fine():
    found = pc.check("@@A @@B @@C ngồi bàn ăn", cast=list("ABC"), lane="lite_relaxed")
    assert "too_many_characters" not in _rules(found)


def test_omni_allows_ten():
    names = [f"N{i}" for i in range(10)]
    prompt = " ".join(f"@@{n}" for n in names) + " cùng trong phòng"
    found = pc.check(prompt, cast=names, lane="omni")
    assert "too_many_characters" not in _rules(found)


def test_single_tags_do_not_count_toward_the_ceiling():
    """`@name` is a reference image, `@@name` is a Character Entity. Only
    the entities occupy the character sockets the ceiling is about."""
    found = pc.check("@a @b @c @d @e cùng cảnh", cast=list("abcde"), lane="lite")
    assert "too_many_characters" not in _rules(found)


# ── duration per lane ─────────────────────────────────────────────────


def test_ten_seconds_on_a_lite_lane_is_an_error():
    """Refused at dispatch, so checking here turns a round trip into a line
    of text."""
    found = pc.check("cảnh", lane="lite_relaxed", seconds=10)
    assert "bad_duration" in _rules(found)


def test_ten_seconds_on_omni_is_fine():
    assert "bad_duration" not in _rules(pc.check("cảnh", lane="omni", seconds=10))


@pytest.mark.parametrize("seconds", [4, 6, 8])
def test_the_lite_durations_pass(seconds):
    assert "bad_duration" not in _rules(
        pc.check("cảnh", lane="lite_relaxed", seconds=seconds)
    )


# ── real names: forbidden in visual, ALLOWED in dialogue ──────────────


def test_a_celebrity_name_in_the_visual_half_is_an_error():
    found = pc.check("Ngô Quyền đứng trên thuyền chỉ huy hạm đội")
    assert "banned_name" in _rules(found)


def test_the_same_name_inside_dialogue_is_allowed():
    """The rule that is easy to implement backwards. The voiceover is spoken
    by a separate model, and the video model's celebrity filter never sees
    it — so a narration naming a historical figure is correct, not a fault."""
    found = pc.check(
        'Một vị tướng đứng trên thuyền.\n'
        'Lời thoại: Năm 938, Ngô Quyền đã đánh tan quân Nam Hán trên sông Bạch Đằng.'
    )
    assert "banned_name" not in _rules(found), (
        "a real name in the narration is explicitly permitted"
    )


def test_a_name_inside_quotes_is_allowed():
    found = pc.check('Người dẫn nói "Ngô Quyền đã thắng trận Bạch Đằng"')
    assert "banned_name" not in _rules(found)


def test_the_ban_matches_whole_words_only():
    """Substring matching produces a false positive on ordinary Vietnamese.

    "bác học" — a scholar — folds to ``bac hoc``, which contains ``bac ho``,
    the folded form of a banned name. A prompt describing an old scientist
    would be refused for naming someone it never mentions, and a rule that
    cries wolf on ordinary words is a rule the writer learns to ignore.
    """
    found = pc.check("một nhà bác học già đang nhìn kính hiển vi")
    assert "banned_name" not in _rules(found), (
        f"false positive on ordinary text: {[f.span for f in found]}"
    )


def test_the_ban_still_catches_the_real_name():
    """The companion to the test above: tightening the match must not
    loosen it into uselessness."""
    assert "banned_name" in _rules(pc.check("Bác Hồ đứng đọc tuyên ngôn"))


def test_the_ban_ignores_accents_and_case():
    assert "banned_name" in _rules(pc.check("ngo quyen chi huy tran danh"))


def test_the_banned_list_is_never_empty():
    """An empty list silently disables the rule, which reads exactly like
    "no problems found"."""
    assert len(pc.banned_names()) >= 5


# ── length ────────────────────────────────────────────────────────────


def test_an_overlong_prompt_warns_but_does_not_block():
    found = pc.check("x" * (pc.MAX_PROMPT_CHARS + 1))
    assert "too_long" in _rules(found)
    assert "too_long" not in {f.rule for f in pc.errors(found)}


# ── the shape callers depend on ───────────────────────────────────────


def test_a_clean_prompt_produces_nothing():
    found = pc.check(
        '@@Mai đứng nghiêng trước gương, ánh sáng cửa sổ.\n'
        'Lời thoại: Con về rồi đây mẹ.',
        cast=["Mai"], lane="lite_relaxed", seconds=8,
    )
    assert found == [], f"unexpected findings: {found}"


def test_feedback_names_every_finding():
    found = pc.check('nói "chào @@X"', cast=["X"])
    text = pc.as_feedback(found)
    assert "tag_in_dialogue" in text


# ── the dialogue split, which decides which half each rule reads ──────


def test_an_ordinary_description_is_not_turned_into_dialogue():
    """`thoải mái` folds to `thoai mai`, and `thoai` was matched as a bare
    substring — so this line became "dialogue" from the adjective onward, and
    the name after it stopped being checked at all. The rule that costs money
    (a name in a VISUAL prompt is refused by the filter) was the one being
    switched off by a word about sunlight."""
    visual, spoken = pc.split_dialogue(
        "Ánh nắng thoải mái tràn vào phòng, Bác Hồ đứng bên cửa sổ"
    )
    assert spoken == ""
    assert "Bác Hồ" in visual
    assert any(f.rule == "banned_name" for f in pc.check(
        "Ánh nắng thoải mái tràn vào phòng, Bác Hồ đứng bên cửa sổ"
    ))


def test_a_label_still_opens_dialogue():
    visual, spoken = pc.split_dialogue(
        "Cận cảnh người mẹ. Lời thoại: Con về rồi đây mẹ"
    )
    assert spoken.startswith("Lời thoại:")
    assert "Con về rồi đây mẹ" in spoken
    assert "Cận cảnh người mẹ" in visual


def test_a_label_on_its_own_line_carries_to_the_lines_below():
    """How a model actually formats a speech. Read one line at a time, the
    speech itself was description — so every name in it was reported as a
    banned visual name (a false error that now BLOCKS a dispatch) and every
    tag in it went unreported."""
    prompt = (
        "Cận cảnh @@MaiAnh trong sân.\n"
        "Lời thoại:\n"
        "Con về rồi đây mẹ\n"
        "Bác Hồ từng nói thế\n"
        "\n"
        "Cảnh 2: sân nhà lúc chiều"
    )
    visual, spoken = pc.split_dialogue(prompt)
    assert "Con về rồi đây mẹ" in spoken
    assert "Bác Hồ từng nói thế" in spoken, "a name in speech is allowed"
    assert "Cảnh 2" in visual, "a new label ends the block"
    assert pc.check(prompt) == []


def test_two_apostrophes_are_not_a_quoted_passage():
    """`don't ... can't` was read as one quoted span, which made the whole
    sentence dialogue — and the celebrity check skips dialogue."""
    visual, spoken = pc.split_dialogue(
        "She don't know, he can't tell. Bác Hồ walks in."
    )
    assert spoken == ""
    assert any(f.rule == "banned_name" for f in pc.check(
        "She don't know, he can't tell. Bác Hồ walks in."
    ))


def test_a_real_single_quoted_line_is_still_dialogue():
    _, spoken = pc.split_dialogue("The boy shouts 'con muốn về nhà' loudly")
    assert "con muốn về nhà" in spoken


def test_an_unpaired_quote_does_not_swallow_the_rest_of_the_prompt():
    prompt = 'Mẹ nói "con về rồi đây\nCận cảnh Bác Hồ bên cửa sổ'
    visual, _ = pc.split_dialogue(prompt)
    assert "Bác Hồ" in visual


def test_a_vietnamese_tag_is_reported_whole():
    """ASCII-only, the span came back as `@@B` — reported as the offending
    text, and compared against the cast list as the character "b"."""
    findings = pc.check("Cảnh có @@BàTrưng cưỡi voi", cast=["MaiAnh"])
    assert [f.span for f in findings if f.rule == "unknown_tag"] == ["@@BàTrưng"]


def test_a_name_typed_without_its_accents_is_still_the_name():
    """Half of these prompts arrive unaccented. `Đ` was not folded, so
    "Tran Hung Dao" never matched "Trần Hưng Đạo" and the check passed on a
    prompt the filter would refuse."""
    findings = pc.check("Canh quay Tran Hung Dao tren thuyen")
    assert any(f.rule == "banned_name" for f in findings)


def test_decomposed_input_is_not_cut_mid_word():
    """Marker positions are found in the folded text and sliced out of the
    original. Folding NFD input the obvious way changes the length, so every
    index after the first accent pointed one character short."""
    import unicodedata

    prompt = unicodedata.normalize(
        "NFD", "Cận cảnh mẹ ngồi. Lời thoại: Con về rồi đây mẹ"
    )
    visual, spoken = pc.split_dialogue(prompt)
    assert spoken.startswith("Lời thoại:")
    assert visual.strip() == "Cận cảnh mẹ ngồi."


def test_a_new_label_ends_the_block_without_a_blank_line_to_help():
    """A model writes the next heading straight under the speech about as
    often as it leaves a blank line. Without this the rest of the prompt —
    every visual description after the speech — was read as dialogue, and the
    celebrity rule skips dialogue."""
    prompt = (
        "Lời thoại:\n"
        "Con về rồi đây mẹ\n"
        "Cảnh 2: Bác Hồ đứng bên cửa sổ"
    )
    visual, spoken = pc.split_dialogue(prompt)
    assert "Con về rồi đây mẹ" in spoken
    assert "Bác Hồ" in visual
    assert any(f.rule == "banned_name" for f in pc.check(prompt))


def test_a_quote_left_open_does_not_pair_with_one_on_another_line():
    """Two quotes, two lines, and the span between them used to be "dialogue"
    — which is the half no name is checked in. An unpaired opening quote is
    ordinary in these prompts."""
    prompt = (
        'Mẹ nói "con về rồi đây\n'
        'Cận cảnh Bác Hồ bên cửa sổ, ánh sáng "vàng ấm"'
    )
    visual, _ = pc.split_dialogue(prompt)
    assert "Bác Hồ" in visual
    assert any(f.rule == "banned_name" for f in pc.check(prompt))


def test_the_fold_is_the_same_whichever_normalisation_arrives():
    """The rule file is not this repo's and nothing normalises it on the way
    in. Folded per character without normalising first, a decomposed "Bác Hồ"
    became `ba c ho ` — spaces where the marks were — and matched nothing."""
    import unicodedata

    name = "Bác Hồ"
    assert pc._fold(unicodedata.normalize("NFC", name)) == "bac ho"
    assert pc._fold(unicodedata.normalize("NFD", name)) == "bac ho"


def test_the_fold_keeps_one_character_per_character():
    """Marker positions are found in the fold and sliced out of the input."""
    line = "Cận cảnh mẹ ngồi. Lời thoại: Con về"
    assert len(pc._fold(line)) == len(line)
