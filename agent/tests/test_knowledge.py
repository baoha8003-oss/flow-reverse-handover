"""The packaged tool's craft knowledge, and whether it reaches the model.

`ASSET_ROOT` ships 413 KiB of markdown (423,317 bytes over 36 files) — hook construction, McKee-derived
story structure, four Vietnamese drama formulas, a 146 KiB Veo 3 prompting
guide, a 50-move camera library, a 100-point director's rubric — that this
build had never opened. Every AI step ran on a system prompt written from
scratch instead.

Two properties carry the feature and both are easy to get wrong silently:

* **Everything selected must actually arrive.** A first-come-first-served
  budget let one 16 KB file swallow the lot, and the narrowest, most
  valuable material — four named sections of the Veo guide — was declared
  last and therefore always dropped. Sharing the budget is what fixed it.
* **What does not fit must be named.** A silent cap reads as "the model was
  told everything" when it was told two thirds.
"""
from __future__ import annotations

import pytest

from flowboard.services import knowledge, script_genres

pytestmark = pytest.mark.skipif(
    not knowledge.available(), reason="packaged skill library not installed"
)


# ── selection and budget ──────────────────────────────────────────────


@pytest.mark.parametrize("task", sorted(knowledge.TASKS))
def test_every_task_loads_something(task):
    assert knowledge.sections_for(task).text.strip()


@pytest.mark.parametrize("task", sorted(knowledge.TASKS))
def test_every_declared_source_contributes(task):
    """The regression that motivated budget sharing: sources declared last
    were being dropped whole. Every source must land, even if trimmed."""
    result = knowledge.sections_for(task)
    assert len(result.files) == len(knowledge.TASKS[task]), (
        f"{task}: only {result.files} of "
        f"{[s.path for s in knowledge.TASKS[task]]} arrived"
    )


def _per_file_bytes(result: knowledge.Knowledge) -> list[int]:
    """Bytes each source contributed, in declared order, read back out of
    the assembled text via its ``===== name =====`` markers."""
    chunks = result.text.split("\n\n===== ")
    return [len(c.encode("utf-8")) for c in chunks if c.strip()]


@pytest.mark.parametrize("task", sorted(knowledge.TASKS))
def test_the_budget_is_shared_not_raced_for(task):
    """Presence alone does not prove fairness, and fairness is the point.

    Under a first-come budget every source still *appears* — truncation
    keeps them all in — but the early ones take almost everything and the
    ones declared last get scraps. That matters because the list is written
    most-general first: for `write_prompt` the four named sections of the
    Veo guide come last and are the most specific material in the set.

    So this measures distribution: no source may take more than half the
    budget while others are waiting behind it.
    """
    sources = knowledge.TASKS[task]
    if len(sources) < 2:
        pytest.skip("fairness is meaningless with one source")
    sizes = _per_file_bytes(knowledge.sections_for(task))
    assert sizes, "nothing was assembled"
    # The cap applies only when the budget actually BINDS. The docstring above
    # says "while others are waiting behind it", and the assertion used to drop
    # that clause: with four sources where three are small, the fourth can pass
    # half the budget without anyone being cut, and the whole set still fits.
    # Measured case that exposed it: 13091 + 2390 + 6380 + 2618 = 24479 of
    # 25000 — nothing was rationed, so there was nobody to be unfair to.
    if sum(sizes) < knowledge.DEFAULT_BUDGET_BYTES:
        return
    assert max(sizes) <= knowledge.DEFAULT_BUDGET_BYTES // 2, (
        f"{task}: one source took {max(sizes)}B of "
        f"{knowledge.DEFAULT_BUDGET_BYTES}B while {len(sizes) - 1} others waited"
    )


@pytest.mark.parametrize("task", sorted(knowledge.TASKS))
def test_no_task_exceeds_its_budget(task):
    """`cli_utils.MAX_PROMPT_BYTES` caps a prompt at 100 KB and the Veo guide
    alone is 146 KiB, so an unbudgeted selection fails at dispatch.

    Two tasks carry their own budget (`BUDGETS`) because they draw on a dozen
    sources each; every task still has to stay inside whichever applies, and
    all of them inside the prompt ceiling with room for the brief."""
    budget = knowledge.BUDGETS.get(task, knowledge.DEFAULT_BUDGET_BYTES)
    size = len(knowledge.sections_for(task).text.encode("utf-8"))
    assert size <= budget
    assert budget <= 60_000, "leave half the prompt ceiling for the actual work"


def test_a_tight_budget_reports_what_it_cut():
    result = knowledge.sections_for("write_prompt", budget_bytes=2_000)
    assert result.dropped, "a cut this deep has to be reported"
    assert len(result.text.encode("utf-8")) <= 2_000


def test_truncation_lands_on_a_section_boundary():
    """Cutting mid-sentence hands the model a rule that stops halfway, which
    is worse than not sending the rule at all."""
    result = knowledge.sections_for("review_clip", budget_bytes=3_000)
    body = result.text
    if "##" not in body:
        pytest.skip("budget too small to keep any section")
    # Nothing after the final heading may be a dangling fragment of the next
    # one: the tail must end at a completed line, not mid-word.
    assert not body.rstrip().endswith("#")


def test_an_unknown_task_raises_rather_than_returning_nothing():
    """Silence would read as "this step has no knowledge", which is exactly
    how a typo'd task name would hide."""
    with pytest.raises(KeyError, match="unknown knowledge task"):
        knowledge.sections_for("nonsense")


def test_headings_match_across_accents_and_emoji():
    """The same heading is written three ways across these files — with
    diacritics, without, and with a leading emoji. Matching the literal
    string finds one spelling and misses the rest."""
    text = "## 🎯 BẢNG 7 DẠNG VIDEO CHUẨN\nnội dung\n\n## Khác\nbỏ qua\n"
    got = knowledge._slice_sections(text, ("bang 7 dang video chuan",))
    assert "nội dung" in got and "bỏ qua" not in got


def test_a_missing_root_is_empty_not_an_exception(monkeypatch, tmp_path):
    """Knowledge makes a prompt better. A step that ran without it must not
    start failing because the packaged tool was uninstalled."""
    from flowboard.services import assets

    monkeypatch.setattr(assets, "ASSET_ROOT", tmp_path / "gone")
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path / "gone" / "data_general")
    result = knowledge.sections_for("storyboard")
    assert result.text == ""
    assert all("missing" in d for d in result.dropped)


def test_every_task_names_a_file_that_exists():
    """A renamed file in the asset library shows up here rather than as a
    quietly thinner prompt."""
    for task in knowledge.TASKS:
        result = knowledge.sections_for(task)
        missing = [d for d in result.dropped if "(missing)" in d or "no matching section" in d]
        assert not missing, f"{task}: {missing}"


# ── the eight genres ──────────────────────────────────────────────────


def test_all_eight_genres_are_present():
    """The README names exactly eight. Fewer means one was lost in
    transcription; more means one was invented."""
    assert len(script_genres.GENRES) == 8


@pytest.mark.parametrize("key", sorted(script_genres.GENRES))
def test_a_stated_dialogue_length_is_a_deliverable_one(key):
    """The per-scene word count is load-bearing where it exists: a scene is
    5–8 seconds, so it is what a voice can deliver without a re-cut. Four of
    the eight genres have one in the README; the rest say None rather than
    inherit a plausible default — see
    `test_only_the_genres_the_readme_specifies_carry_a_word_count`."""
    stated = script_genres.GENRES[key].words_per_scene
    if stated is None:
        return
    low, high = stated
    assert 0 < low <= high <= 40


@pytest.mark.parametrize("key", sorted(script_genres.GENRES))
def test_each_genre_builds_a_prompt(key):
    body = script_genres.full_prompt(key)
    assert script_genres.GENRES[key].title in body
    assert len(body) > 200


@pytest.mark.parametrize("key", sorted(script_genres.GENRES))
def test_supplements_add_material(key):
    """`use_hook` / `use_longform` / `use_3act` are the README's three flags,
    and each maps to a real file on disk."""
    plain = script_genres.full_prompt(key)
    with_all = script_genres.full_prompt(
        key, use_hook=True, use_longform=True, use_3act=True
    )
    assert len(with_all) > len(plain) or "=====" in with_all


def test_a_genre_with_on_disk_material_actually_reads_it():
    """`drama_lat_keo` names three real formula files. If the loader stopped
    reading them the preset would silently shrink to the written body."""
    body = script_genres.full_prompt("drama_lat_keo")
    assert "=====" in body, "no file content was pulled in"


def test_an_unknown_genre_raises():
    with pytest.raises(KeyError, match="unknown genre"):
        script_genres.full_prompt("khong_co")


def test_every_supplement_path_resolves():
    for flag, path in script_genres.SUPPLEMENTS.items():
        loaded = knowledge.load_paths([path])
        assert loaded.files, f"{flag} points at a file that does not load: {path}"


# ── the steps that consume it ─────────────────────────────────────────


def test_the_storyboard_prompt_carries_craft_notes():
    from flowboard.services import storyboard

    prompt = storyboard._system_prompt(3, 8, "chain", "")
    assert "Craft notes" in prompt
    assert len(prompt) > 5_000, "craft notes did not arrive"


def test_a_genre_replaces_the_generic_notes():
    from flowboard.services import storyboard

    prompt = storyboard._system_prompt(3, 8, "chain", "", genre="phat_phap_nhan_qua")
    assert "Duyên Khởi" in prompt
    assert "Craft notes" not in prompt, "genre and generic notes both included"


def test_an_unknown_genre_does_not_lose_the_storyboard():
    """A bad genre is the caller's bug. Losing the whole storyboard over it
    would turn a typo into a failed run."""
    from flowboard.services import storyboard

    prompt = storyboard._system_prompt(3, 8, "chain", "", genre="khong_co")
    assert "Return ONLY this JSON" in prompt


def test_the_genre_route_lists_all_eight(client):
    body = client.get("/api/prompt/genres").json()
    assert len(body) == 8
    assert all(g["key"] and g["title"] for g in body)
    # Two numbers or nothing. The four the README leaves unspecified come back
    # null, so the picker can label them without inventing a range.
    assert all(
        g["wordsPerScene"] is None or len(g["wordsPerScene"]) == 2 for g in body
    )
    assert sum(1 for g in body if g["wordsPerScene"]) == 4


# ── the idea tab's own path ───────────────────────────────────────────
#
# `Ý tưởng → Video` calls `/api/prompt/idea`, not `/api/prompt/storyboard`.
# Wiring genres into the storyboard alone would have left the tab users
# actually open running on the generic prompt.


@pytest.mark.asyncio
async def test_the_idea_path_carries_craft_notes(monkeypatch):
    from flowboard.services import prompt_synth

    seen: dict = {}

    async def _run(feature, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return '["cảnh một", "cảnh hai"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    await prompt_synth.idea_to_prompts("một cô gái bán hàng rong", scene_count=2)
    assert "Craft notes" in seen["system"]


@pytest.mark.asyncio
async def test_a_genre_reaches_the_idea_path(monkeypatch):
    from flowboard.services import prompt_synth

    seen: dict = {}

    async def _run(feature, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return '["cảnh một"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    await prompt_synth.idea_to_prompts(
        "nhân quả", scene_count=1, genre="phat_phap_nhan_qua"
    )
    assert "Duyên Khởi" in seen["system"]
    assert "Craft notes" not in seen["system"]


@pytest.mark.asyncio
async def test_an_unknown_genre_falls_back_rather_than_failing(monkeypatch):
    from flowboard.services import prompt_synth

    seen: dict = {}

    async def _run(feature, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return '["cảnh một"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    out = await prompt_synth.idea_to_prompts("x", scene_count=1, genre="khong_co")
    assert out, "a typo'd genre must not cost the shot list"
    assert "Craft notes" in seen["system"]


# ── the budget goes where it is needed ────────────────────────────────


def _delivered(task: str) -> dict:
    """How much of what each source asked for actually arrived, as a fraction.

    Reads the files a second time rather than trusting `dropped`: "truncated"
    does not say whether that meant losing a paragraph or nine tenths of the
    material, and nine tenths is what was happening."""
    import re

    result = knowledge.sections_for(task)
    roots = knowledge._roots()
    got = {}
    parts = re.split(r"=====\s*([^=]+?)\s*=====", result.text)
    for i in range(1, len(parts), 2):
        got[parts[i]] = len(parts[i + 1].encode("utf-8"))

    fractions = {}
    for source in knowledge.TASKS[task]:
        root_key, _, relative = source.path.partition("/")
        raw = knowledge._read(roots[root_key] / relative)
        if raw is None:
            continue
        need = len(knowledge._slice_sections(raw, source.headings).encode("utf-8"))
        name = relative.rsplit("/", 1)[-1]
        fractions[name] = (got.get(name, 0) / need) if need else 1.0
    return fractions


def test_the_storyboard_sources_all_arrive_nearly_whole():
    """An equal share per source read as fair and was not: the modest ones left
    their share unspent while the big file declared FIRST was cut to a third of
    the budget. Measured before the fix, `mx_shell_methodology` arrived at 564
    of its 11 523 bytes and the Veo guide at 3.5% of its four named sections."""
    fractions = _delivered("storyboard")
    if not fractions:
        pytest.skip("the packaged skill tree is not on this machine")
    assert min(fractions.values()) >= 0.8, fractions


def test_only_the_one_reference_book_is_deeply_cut():
    """`veo3_prompting_guide` is 146 KiB and no budget fits it; everything else
    in the prompt-writing set has to survive. One source below half, not six."""
    fractions = _delivered("write_prompt")
    if not fractions:
        pytest.skip("the packaged skill tree is not on this machine")
    thin = [name for name, frac in fractions.items() if frac < 0.5]
    assert thin == ["veo3_prompting_guide.md"], fractions
    # And the first-declared source — most useful, by the table's own order —
    # is delivered whole.
    assert fractions["mx_shell_methodology.md"] >= 0.99, fractions


def test_the_budget_is_actually_used(task="write_prompt"):
    """Fair-share-then-fit left a third of the budget empty: `_fit` lands on a
    whole section, so a truncated source takes less than it was given and the
    slack used to evaporate."""
    size = len(knowledge.sections_for(task).text.encode("utf-8"))
    assert size > knowledge.DEFAULT_BUDGET_BYTES * 0.9


def test_every_topic_asked_for_gets_a_turn():
    """The Veo guide is selected by four headings. In file order its six audio
    subsections filled the allowance and `prompt structure` — the section the
    caller named first — was never delivered at all."""
    result = knowledge.sections_for("write_prompt")
    if not any("veo3_prompting_guide" in f for f in result.files):
        pytest.skip("the packaged guide is not on this machine")
    assert "Professional Prompt Structure" in result.text


def test_a_supplement_adds_material_it_does_not_replace_it():
    """`drama_lat_keo` names three formula files. Adding the three README
    supplements used to SHRINK the result, because every source was given an
    equal share of the same budget."""
    plain = script_genres.full_prompt("drama_lat_keo")
    with_all = script_genres.full_prompt(
        "drama_lat_keo", use_hook=True, use_longform=True, use_3act=True
    )
    if "=====" not in plain:
        pytest.skip("the packaged skill tree is not on this machine")
    assert len(with_all.encode("utf-8")) > len(plain.encode("utf-8"))


# ── a number nobody wrote is not a number ─────────────────────────────


def test_only_the_genres_the_readme_specifies_carry_a_word_count():
    """Read from the README rather than asserted here, because the point is
    that the module must not know more than the file does. Four genres carry
    `Lời thoại: N-M từ/cảnh`; the default gave all eight one, and the picker
    labelled every genre with it."""
    import re

    readme = knowledge._read(knowledge._roots()["video_skills"] / "README.md")
    if readme is None:
        pytest.skip("the packaged skill tree is not on this machine")

    spec = {}
    for match in re.finditer(r"`([a-z_]+)`\s*—\s*[^(]*\(\*([^*]*)\*\)", readme):
        words = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*từ", match.group(2))
        spec[match.group(1)] = (
            (int(words.group(1)), int(words.group(2))) if words else None
        )

    for key, genre in script_genres.GENRES.items():
        if key not in spec:
            continue
        assert genre.words_per_scene == spec[key], key


def test_a_genre_without_a_specified_length_says_so_instead_of_guessing():
    unspecified = [
        k for k, g in script_genres.GENRES.items() if g.words_per_scene is None
    ]
    assert unspecified, "the default is back — all eight would claim a number"
    for key in unspecified:
        body = script_genres.GENRES[key].body
        assert "28–30 từ" not in body, key


def test_a_modest_source_does_not_eat_a_share_it_does_not_need():
    """The allocation itself, away from the files. An equal share per source
    is what left a third of the budget unspent while the big source was cut."""
    assert knowledge._shares([100, 100, 10_000], 3_000) == [100, 100, 2_800]


def test_everyone_wanting_more_than_a_share_splits_it_evenly():
    assert knowledge._shares([5_000, 5_000], 1_000) == [500, 500]


def test_the_named_topics_are_interleaved_rather_than_file_ordered():
    """One section per topic, then the next round. In file order a truncation
    cost whole topics — the Veo guide's audio subsections come first in the
    file and filled the allowance on their own."""
    text = (
        "## Audio one\na\n\n"
        "## Audio two\nb\n\n"
        "## Camera one\nc\n"
    )
    got = knowledge._slice_sections(text, ("audio", "camera"))
    assert got.index("Camera one") < got.index("Audio two")
