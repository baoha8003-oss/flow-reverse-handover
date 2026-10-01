"""Generate, score, reword the prompt, generate again.

The policy half of the loop lives in `services.review_loop` precisely so it
can be tested without a board, a worker and a Flow dispatch — this is the
code that decides whether the user's credits get spent, and it should be
possible to prove it behaves without spending any.

The rule the user set when this was planned: **retry automatically only on a
0-credit lane** (`lite_relaxed` / `fast_relaxed`, Ultra's low-priority
queue). On any paid lane the loop reviews once, says what it would have
done, and stops.
"""
from __future__ import annotations

import pytest

from flowboard.services import review_loop
from flowboard.services.review_loop import Settings, decide, is_free_lane
from flowboard.services.video_review import Issue, Review


def _review(score: float = 5.0, **over) -> Review:
    base = dict(
        score=score,
        verdict="poor",
        dimensions={
            "character_consistency": 4.0, "prompt_adherence": 6.0,
            "motion_quality": 5.0, "visual_fidelity": 6.0,
            "temporal_coherence": 5.0, "composition": 6.0,
        },
        issues=[Issue("HIGH", "3s-5s", "máy quay tự zoom")],
        usable_segments=[],
        fix_hint="Khung hình lệch.",
    )
    base.update(over)
    return Review(**base)


# ── the credit rule ───────────────────────────────────────────────────


@pytest.mark.parametrize("lane", ["lite_relaxed", "fast_relaxed"])
def test_the_relaxed_lanes_are_free(lane):
    assert is_free_lane(lane)


@pytest.mark.parametrize("lane", ["lite", "fast", "quality", "omni", "", None])
def test_every_other_lane_costs_credits(lane):
    """`lite` is NOT `lite_relaxed`. Reading one as the other is how a loop
    that promised to be free spends five credits a round."""
    assert not is_free_lane(lane)


def test_a_paid_lane_stops_after_one_review():
    """The user's explicit decision: review, report, do not spend."""
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=5.0, verdict="poor",
        settings=settings, quality="fast",
    )
    assert not got.retried
    assert "tốn credit" in got.reason


def test_a_free_lane_retries_below_the_threshold():
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=5.0, verdict="poor",
        settings=settings, quality="lite_relaxed",
    )
    assert got.retried


def test_a_clip_that_passes_is_never_retried_even_on_a_free_lane():
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=8.5, verdict="good",
        settings=settings, quality="lite_relaxed",
    )
    assert got.accepted and not got.retried


def test_the_round_ceiling_stops_a_free_lane_too():
    """A free lane still costs wall-clock and one vision call per round."""
    settings = Settings(enabled=True, threshold=7.0, max_rounds=2)
    got = decide(
        round_index=2, score=3.0, verdict="unusable",
        settings=settings, quality="lite_relaxed",
    )
    assert not got.retried
    assert "giới hạn" in got.reason


# ── a named CRITICAL fault outranks the score ─────────────────────────


def test_a_critical_fault_is_not_accepted_even_above_the_threshold():
    """The cap in `parse_review` is not enough on its own.

    A CRITICAL finding pins `character_consistency` to 3.0, but the other five
    dimensions still carry 0.75 of the weight, so the ceiling is 8.25 — above
    the 7.0 default. A beautiful clip whose face changes once lands right in
    that band, and it is the failure the rubric puts first.
    """
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=8.25, verdict="good",
        settings=settings, quality="lite_relaxed", has_critical=True,
    )
    assert not got.accepted
    assert got.retried


def test_the_reason_says_the_score_passed_and_a_critical_fault_did_not():
    """Reporting a bare score here would read as the scoring being broken.

    The threshold is a number the user chose; a rule that overrides it has to
    name itself, or "Điểm 8.25 dưới ngưỡng 7.0" sends someone bug-hunting.
    """
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=8.25, verdict="good",
        settings=settings, quality="lite_relaxed", has_critical=True,
    )
    assert "CRITICAL" in got.reason
    assert "dưới ngưỡng" not in got.reason


def test_a_critical_fault_still_cannot_spend_credits():
    """Strictness must not become a second way to bill.

    The lane gate runs after this one, so a paid lane reports and stops.
    """
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=8.25, verdict="good",
        settings=settings, quality="fast", has_critical=True,
        model_key="veo_3_1_i2v_s_fast_ultra",
    )
    assert not got.accepted and not got.retried
    assert "CRITICAL" in got.reason and "tốn credit" in got.reason


def test_a_critical_fault_obeys_the_round_ceiling():
    settings = Settings(enabled=True, threshold=7.0, max_rounds=2)
    got = decide(
        round_index=2, score=8.25, verdict="good",
        settings=settings, quality="lite_relaxed", has_critical=True,
    )
    assert not got.accepted and not got.retried
    assert "CRITICAL" in got.reason and "giới hạn" in got.reason


def test_a_clean_clip_above_the_threshold_is_still_accepted():
    """The guard must not turn every pass into a retry."""
    settings = Settings(enabled=True, threshold=7.0, max_rounds=3)
    got = decide(
        round_index=1, score=8.25, verdict="good",
        settings=settings, quality="lite_relaxed", has_critical=False,
    )
    assert got.accepted and not got.retried


# ── reading the settings off a node ───────────────────────────────────


def test_the_loop_is_off_unless_asked_for():
    """A loop that ran by default would spend a vision call per clip on
    every board that never opted in."""
    assert not review_loop.settings_from({}).enabled
    assert not review_loop.settings_from({"review_loop": False}).enabled
    assert not review_loop.settings_from({"review_loop": "false"}).enabled


def test_the_loop_turns_on():
    assert review_loop.settings_from({"review_loop": True}).enabled


def test_the_round_count_is_capped():
    """Bounds the AI spend even when every round is free."""
    got = review_loop.settings_from({"review_loop": True, "review_max_rounds": 99})
    assert got.max_rounds == review_loop.MAX_ROUNDS_CEILING


def test_a_nonsense_threshold_falls_back_rather_than_disabling_the_gate():
    got = review_loop.settings_from({"review_loop": True, "review_threshold": "cao"})
    assert got.threshold == review_loop.DEFAULT_THRESHOLD


def test_a_threshold_is_clamped_to_the_score_scale():
    got = review_loop.settings_from({"review_loop": True, "review_threshold": 47})
    assert got.threshold == 10.0


# ── rewording the prompt ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_critique_reaches_the_rewriter(monkeypatch):
    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["ask"] = prompt
        return "một prompt đã sửa", "claude"

    monkeypatch.setattr(llm_module, "run_llm_chain", _run)
    out = await review_loop.revise_prompt("một cô gái đi bộ", _review())
    assert out == "một prompt đã sửa"
    assert "máy quay tự zoom" in seen["ask"], "the fault has to be in the ask"
    assert "một cô gái đi bộ" in seen["ask"], "so does the prompt being fixed"


@pytest.mark.asyncio
async def test_an_unchanged_rewrite_is_treated_as_no_rewrite(monkeypatch):
    """Retrying with the identical prompt produces the identical clip and
    burns the round for nothing."""
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return "  một cô gái đi bộ  ", "claude"

    monkeypatch.setattr(llm_module, "run_llm_chain", _run)
    assert await review_loop.revise_prompt("một cô gái đi bộ", _review()) is None


@pytest.mark.asyncio
async def test_no_provider_means_stop_not_crash(monkeypatch):
    import flowboard.services.llm as llm_module
    from flowboard.services.llm.base import LLMError

    async def _run(kind, prompt, *, system_prompt="", **kw):
        raise LLMError("no provider")

    monkeypatch.setattr(llm_module, "run_llm_chain", _run)
    assert await review_loop.revise_prompt("một cô gái", _review()) is None


@pytest.mark.asyncio
async def test_a_fenced_rewrite_is_unwrapped(monkeypatch):
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return "```\nprompt mới\n```", "gemini"

    monkeypatch.setattr(llm_module, "run_llm_chain", _run)
    out = await review_loop.revise_prompt("prompt cũ", _review())
    assert out is not None and "```" not in out


@pytest.mark.asyncio
async def test_the_rewrite_carries_the_prompt_rules(monkeypatch):
    """A rewrite has to obey the rules the original prompt did. Fixing
    "camera drift" by naming a real actor trades one fault for a
    safety-filter rejection — and the celebrity-name ban lives in the
    packaged skill library, not in this module's own instructions."""
    from flowboard.services import knowledge

    if not knowledge.available():
        pytest.skip("packaged skill library not installed")

    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return "prompt mới", "claude"

    monkeypatch.setattr(llm_module, "run_llm_chain", _run)
    await review_loop.revise_prompt("một cô gái đi bộ", _review())
    assert "Ràng buộc bắt buộc" in seen["system"]
    assert len(seen["system"]) > 2_000, "the rules did not actually arrive"


@pytest.mark.asyncio
async def test_the_rewriter_asks_for_a_feature_the_registry_can_route(monkeypatch):
    """The bug this test exists for, and why the four above missed it.

    `revise_prompt` used to call ``run_llm("text", …)``. No feature named
    "text" has ever existed, so in production the call raised on every
    invocation, `revise_prompt` returned None, and the review loop stopped
    after one round without ever rewriting anything. Every test above passed
    throughout, because each replaced the dispatch function outright and so
    never asked whether the feature name meant anything.

    This one lets the real chain resolver run and only fakes the providers,
    so a feature name the registry cannot route fails here.
    """
    from flowboard.services.llm import registry

    called: dict = {}

    class _Fake:
        name = "claude"
        supports_vision = True

        async def is_available(self) -> bool:
            return True

        async def run(self, prompt, **kw):
            called["prompt"] = prompt
            return "prompt đã sửa"

    monkeypatch.setattr(registry, "_PROVIDERS", {"claude": _Fake()})
    monkeypatch.setenv("FLOWBOARD_SECRETS_PATH", "/nonexistent/secrets.json")

    out = await review_loop.revise_prompt("một cô gái đi bộ", _review())
    assert out == "prompt đã sửa", "the chain must resolve without a pinned provider"
    assert "máy quay tự zoom" in called["prompt"]
